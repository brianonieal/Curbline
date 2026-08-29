"""
Tear down everything provision.py created.

RDS and ElastiCache bill by the hour whether or not anything is talking to
them. Free tier covers 750 hours a month, but a forgotten instance quietly
consumes that allowance and then starts charging. Run this once the evidence
screenshots are captured.

    python3 infra/teardown.py --region us-east-1 --confirm

Order is the reverse of creation: instances before the subnet groups they sit
in, objects before the bucket that holds them.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import boto3
from botocore.exceptions import ClientError

STACK_FILE = pathlib.Path(__file__).parent / "stack.json"
PREFIX = "curbline"


def log(msg: str) -> None:
    print(f"[teardown] {msg}", flush=True)


def swallow(fn, *codes: str):
    """Run fn, ignoring the given AWS error codes. Teardown must be
    re-runnable after a partial failure."""
    try:
        return fn()
    except ClientError as e:
        if e.response["Error"]["Code"] in codes:
            return None
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=None)
    ap.add_argument("--confirm", action="store_true",
                    help="required; without it this only prints a plan")
    ap.add_argument("--keep-audit", action="store_true",
                    help="keep the S3 audit bucket and its contents")
    args = ap.parse_args()

    if not STACK_FILE.exists():
        # "No record" is not "nothing exists", and conflating them is how a
        # billing stack survives a teardown report. stack.json is gitignored
        # and lives only on the instance that provisioned, so losing the box
        # loses the record while the resources keep running. Give the operator
        # a path rather than an exit.
        region_hint = args.region or "us-east-1"
        sys.exit(
            f"{STACK_FILE} not found, so there is no record of what to delete.\n"
            f"That does NOT mean nothing is running. Check by hand, and pin the\n"
            f"region or an empty result will look like a clean account (E-018):\n\n"
            f"  aws rds describe-db-instances --region {region_hint} "
            f"--query 'DBInstances[].DBInstanceIdentifier'\n"
            f"  aws elasticache describe-cache-clusters --region {region_hint} "
            f"--query 'CacheClusters[].CacheClusterId'\n"
            f"  aws sqs list-queues --region {region_hint} "
            f"--queue-name-prefix {PREFIX}\n"
            f"  aws sns list-topics --region {region_hint}\n"
            f"  aws s3 ls | grep {PREFIX}\n\n"
            f"Delete anything named {PREFIX}-* from the console or the CLI.\n"
            f"RDS and ElastiCache are the two that bill hourly; do those first."
        )
    stack = json.loads(STACK_FILE.read_text())
    region = args.region or stack["region"]

    plan = [
        f"RDS instance      {stack['db']['identifier']} (no final snapshot)",
        f"ElastiCache       {stack['cache']['cluster_id']}",
        f"SQS queues        {len(stack['queues']) * 2} incl. dead-letter",
        f"SNS topic         {stack['sns_topic_arn']}",
        f"S3 bucket         {stack['audit_bucket']}"
        + (" (KEPT)" if args.keep_audit else " and all objects"),
        "Security groups   app, db, cache",
        "Subnet groups     db, cache",
    ]
    log(f"region {region}")
    for line in plan:
        log("  will delete: " + line)

    if not args.confirm:
        log("dry run. re-run with --confirm to actually delete.")
        return 0

    s = boto3.session.Session(region_name=region)
    ec2, sqs, sns, s3 = (s.client(n) for n in ("ec2", "sqs", "sns", "s3"))
    rds, ecache = s.client("rds"), s.client("elasticache")

    # --- The billable resources go FIRST -----------------------------------
    # Ordering is cost control, not tidiness. RDS and ElastiCache are the only
    # two things here that bill hourly; queues, topics and buckets are free or
    # nearly so. swallow() re-raises any code it was not told to expect, so
    # when these ran last a throttle or a permissions error on a free queue
    # deletion aborted the whole run before RDS was touched, and the operator
    # saw a traceback while the expensive half kept billing. Starting the slow
    # deletions first also means the cheap ones happen inside the wait.
    swallow(
        lambda: rds.delete_db_instance(
            DBInstanceIdentifier=stack["db"]["identifier"],
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True,
        ),
        "DBInstanceNotFound",
    )
    log("rds deletion started")

    swallow(
        lambda: ecache.delete_cache_cluster(
            CacheClusterId=stack["cache"]["cluster_id"]
        ),
        "CacheClusterNotFound",
    )
    log("elasticache deletion started")

    # --- queues and topic (instant, and now free of the billing risk) ------
    for base, meta in stack["queues"].items():
        for url in (meta["url"], meta["dlq_url"]):
            swallow(lambda u=url: sqs.delete_queue(QueueUrl=u),
                    "AWS.SimpleQueueService.NonExistentQueue")
        log(f"deleted queue pair {base}")

    swallow(lambda: sns.delete_topic(TopicArn=stack["sns_topic_arn"]),
            "NotFound")
    log("deleted sns topic")

    # --- S3 -----------------------------------------------------------------
    if not args.keep_audit:
        bucket = stack["audit_bucket"]
        try:
            pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
            for page in pages:
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objs:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": objs})
            s3.delete_bucket(Bucket=bucket)
            log(f"deleted bucket {bucket}")
        except ClientError as e:
            log(f"bucket cleanup skipped: {e.response['Error']['Code']}")
    else:
        log(f"keeping bucket {stack['audit_bucket']}")

    log("waiting for both to disappear (several minutes)")
    swallow(
        lambda: rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=stack["db"]["identifier"],
            WaiterConfig={"Delay": 20, "MaxAttempts": 60},
        ),
        "DBInstanceNotFound",
    )

    # Only CacheClusterNotFound means it is gone. This used to break on any
    # ClientError, so a throttle or an expired credential read as "deleted"
    # and the script reported success over a cluster that was still billing.
    # Unknown is not success. Same shape as E-032.
    cache_gone = False
    for _ in range(60):
        try:
            ecache.describe_cache_clusters(
                CacheClusterId=stack["cache"]["cluster_id"])
            time.sleep(20)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("CacheClusterNotFound", "CacheClusterNotFoundFault"):
                cache_gone = True
                break
            log(f"cache status unreadable ({code}), retrying")
            time.sleep(20)
    if not cache_gone:
        log("WARNING: could not confirm the cache is gone. Verify by hand: "
            f"aws elasticache describe-cache-clusters --region {region}")

    # --- subnet groups (only deletable once nothing occupies them) ----------
    swallow(lambda: rds.delete_db_subnet_group(
        DBSubnetGroupName=f"{PREFIX}-db-subnets"), "DBSubnetGroupNotFoundFault")
    swallow(lambda: ecache.delete_cache_subnet_group(
        CacheSubnetGroupName=f"{PREFIX}-cache-subnets"),
        "CacheSubnetGroupNotFoundFault")
    log("deleted subnet groups")

    # --- security groups ----------------------------------------------------
    # The db and cache groups reference the app group, so those rules have to
    # go before the app group itself can be deleted.
    sgs = stack["security_groups"]
    for name in ("db", "cache"):
        swallow(lambda g=sgs[name]: ec2.delete_security_group(GroupId=g),
                "InvalidGroup.NotFound", "DependencyViolation")
    time.sleep(5)
    swallow(lambda: ec2.delete_security_group(GroupId=sgs["app"]),
            "InvalidGroup.NotFound", "DependencyViolation")
    log("deleted security groups")

    # Verify before discarding the record. Unlinking stack.json first produces
    # the worst possible state: resources still billing and nothing left that
    # says what they are called. The v1.0.0 exit criterion is "nothing billable
    # remains", so this asserts it rather than asking the operator to go and
    # look, and the exit code carries the answer.
    still_billing = []
    try:
        alive = rds.describe_db_instances(
            DBInstanceIdentifier=stack["db"]["identifier"])["DBInstances"]
        if alive:
            still_billing.append(f"rds/{stack['db']['identifier']}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "DBInstanceNotFound":
            still_billing.append(f"rds/UNVERIFIED ({e.response['Error']['Code']})")

    try:
        alive = ecache.describe_cache_clusters(
            CacheClusterId=stack["cache"]["cluster_id"])["CacheClusters"]
        if alive:
            still_billing.append(f"elasticache/{stack['cache']['cluster_id']}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in (
                "CacheClusterNotFound", "CacheClusterNotFoundFault"):
            still_billing.append(
                f"elasticache/UNVERIFIED ({e.response['Error']['Code']})")

    if still_billing:
        log("STACK FILE KEPT. These are still present or could not be checked:")
        for item in still_billing:
            log(f"  {item}")
        log("Re-run this script. It is idempotent, and stack.json is the only "
            "record of what to delete.")
        return 1

    STACK_FILE.unlink(missing_ok=True)
    log(f"done. RDS and ElastiCache confirmed absent in {region}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
