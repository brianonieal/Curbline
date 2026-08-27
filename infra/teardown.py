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
        sys.exit(f"{STACK_FILE} not found. Nothing recorded to tear down.")
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

    # --- queues and topic (instant) ---------------------------------------
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

    # --- RDS and ElastiCache (slow; start both, then wait) ------------------
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

    log("waiting for both to disappear (several minutes)")
    swallow(
        lambda: rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=stack["db"]["identifier"],
            WaiterConfig={"Delay": 20, "MaxAttempts": 60},
        ),
        "DBInstanceNotFound",
    )

    for _ in range(60):
        try:
            ecache.describe_cache_clusters(
                CacheClusterId=stack["cache"]["cluster_id"])
            time.sleep(20)
        except ClientError:
            break

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

    STACK_FILE.unlink(missing_ok=True)
    log("done. verify in the console that nothing remains billable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
