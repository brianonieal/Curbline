"""
Curbline infrastructure provisioning.

Creates every managed AWS service the pipeline depends on, in dependency order,
using the account's DEFAULT VPC. Using the default VPC is deliberate: it already
has subnets in multiple AZs with a route to an internet gateway, which removes
the single most common source of lost hours on a short build.

Service list is deliberately minimal. The assignment permits messaging, queuing,
caching, databases, plus storage and VMs, and states that nothing else is
allowed. So there is no Secrets Manager, no Parameter Store, no CloudWatch
dashboard here. The database password is generated once and written to a
restricted local env file on the EC2 host.

Run this FROM the EC2 instance (it uses the instance role for credentials).

    python3 infra/provision.py --region us-east-1

Writes infra/stack.json with every identifier the workers need.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import secrets
import string
import sys
import time

import boto3
from botocore.exceptions import ClientError

STACK_FILE = pathlib.Path(__file__).parent / "stack.json"
PREFIX = "curbline"

# Queues: each stage gets an input queue and a dead-letter queue behind it.
QUEUES = ["ingest", "zones"]

DB_INSTANCE_CLASS = "db.t3.micro"     # free tier eligible node class
DB_ENGINE_VERSION = None               # None = let RDS pick the current default
CACHE_NODE_TYPE = "cache.t3.micro"     # free tier eligible node class
DB_NAME = "curbline"
DB_USER = "curbline"


def log(msg: str) -> None:
    print(f"[provision] {msg}", flush=True)


def make_password(length: int = 24) -> str:
    # RDS rejects '/', '@', '"' and space in master passwords.
    alphabet = string.ascii_letters + string.digits + "-_.!*#"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# --------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------

def default_vpc(ec2) -> tuple[str, list[str]]:
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("No default VPC in this region. Pick a region that has one.")
    vpc_id = vpcs[0]["VpcId"]

    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    # RDS subnet groups need at least two AZs.
    by_az: dict[str, str] = {}
    for s in subnets:
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    subnet_ids = list(by_az.values())
    if len(subnet_ids) < 2:
        sys.exit(f"Default VPC {vpc_id} has subnets in fewer than 2 AZs.")

    log(f"vpc={vpc_id} subnets={subnet_ids}")
    return vpc_id, subnet_ids


def ensure_sg(ec2, vpc_id: str, name: str, description: str) -> str:
    try:
        resp = ec2.create_security_group(
            GroupName=name, Description=description, VpcId=vpc_id
        )
        sg_id = resp["GroupId"]
        log(f"created security group {name} = {sg_id}")
        return sg_id
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidGroup.Duplicate":
            raise
        found = ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )["SecurityGroups"]
        log(f"reusing security group {name} = {found[0]['GroupId']}")
        return found[0]["GroupId"]


def allow_from_cidr(ec2, sg_id: str, port: int, cidr: str, note: str) -> None:
    """Open a port to a specific CIDR. Never 0.0.0.0/0 by default."""
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[{
                "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                "IpRanges": [{"CidrIp": cidr, "Description": note}],
            }],
        )
        log(f"opened tcp/{port} on {sg_id} to {cidr} ({note})")
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        log(f"tcp/{port} already open on {sg_id} to {cidr}")


def my_public_cidr() -> str | None:
    """The caller's own address, so the dashboard is not exposed to the world."""
    import urllib.request
    for url in ("https://checkip.amazonaws.com",
                "https://api.ipify.org"):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.read().decode().strip() + "/32"
        except Exception:
            continue
    return None


def this_instance_id() -> str | None:
    """Instance id via IMDSv2, so provisioning can attach its own SG."""
    import urllib.request
    try:
        tok_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        with urllib.request.urlopen(tok_req, timeout=2) as r:
            token = r.read().decode()
        req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.read().decode().strip()
    except Exception:
        return None


def attach_sg_to_self(ec2, sg_id: str) -> None:
    """
    Attach the app security group to the instance we are running on.

    This closes a bootstrapping gap: the app group cannot exist before the
    instance (provisioning runs on it), but the instance must be a member of
    that group before RDS and ElastiCache will accept its connections.
    """
    iid = this_instance_id()
    if not iid:
        log("not running on EC2, or IMDS unavailable. "
            f"Attach {sg_id} to your instance manually.")
        return
    try:
        inst = ec2.describe_instances(InstanceIds=[iid])[
            "Reservations"][0]["Instances"][0]
        current = [g["GroupId"] for g in inst["SecurityGroups"]]
        if sg_id in current:
            log(f"instance {iid} already in {sg_id}")
            return
        ec2.modify_instance_attribute(
            InstanceId=iid, Groups=current + [sg_id])
        log(f"attached {sg_id} to instance {iid}")
    except ClientError as e:
        log(f"could not attach SG automatically ({e.response['Error']['Code']}). "
            f"Attach {sg_id} to {iid} in the console.")


def allow_from_sg(ec2, target_sg: str, source_sg: str, port: int) -> None:
    """Open a port on target_sg to members of source_sg only. No CIDR rules."""
    try:
        ec2.authorize_security_group_ingress(
            GroupId=target_sg,
            IpPermissions=[{
                "IpProtocol": "tcp",
                "FromPort": port,
                "ToPort": port,
                "UserIdGroupPairs": [{"GroupId": source_sg}],
            }],
        )
        log(f"opened tcp/{port} on {target_sg} from {source_sg}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
        log(f"tcp/{port} on {target_sg} already open from {source_sg}")


# --------------------------------------------------------------------------
# Queuing (SQS) and messaging (SNS)
# --------------------------------------------------------------------------

def create_queues(sqs) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for base in QUEUES:
        dlq_name = f"{PREFIX}-{base}-dlq"
        q_name = f"{PREFIX}-{base}"

        dlq_url = sqs.create_queue(QueueName=dlq_name)["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(
            QueueUrl=dlq_url, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]

        q_url = sqs.create_queue(
            QueueName=q_name,
            Attributes={
                # Long polling. Cuts empty receives and cost dramatically.
                "ReceiveMessageWaitTimeSeconds": "20",
                # Must exceed the slowest expected unit of work.
                "VisibilityTimeout": "60",
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": 5}
                ),
            },
        )["QueueUrl"]

        out[base] = {"url": q_url, "dlq_url": dlq_url, "dlq_arn": dlq_arn}
        log(f"queue {q_name} ready")
    return out


def create_topic(sns) -> str:
    arn = sns.create_topic(Name=f"{PREFIX}-advisories")["TopicArn"]
    log(f"sns topic {arn}")
    return arn


# --------------------------------------------------------------------------
# Storage (S3) for the write-once audit record
# --------------------------------------------------------------------------

def create_bucket(s3, region: str, account_id: str) -> str:
    name = f"{PREFIX}-audit-{account_id}"
    try:
        if region == "us-east-1":
            s3.create_bucket(Bucket=name)
        else:
            s3.create_bucket(
                Bucket=name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        log(f"created bucket {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in (
            "BucketAlreadyOwnedByYou",
            "BucketAlreadyExists",
        ):
            raise
        log(f"reusing bucket {name}")

    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    return name


# --------------------------------------------------------------------------
# Database (RDS PostgreSQL + PostGIS)
# --------------------------------------------------------------------------

def create_rds(rds, subnet_ids: list[str], sg_id: str, password: str) -> dict:
    group_name = f"{PREFIX}-db-subnets"
    try:
        rds.create_db_subnet_group(
            DBSubnetGroupName=group_name,
            DBSubnetGroupDescription="Curbline database subnets",
            SubnetIds=subnet_ids,
        )
        log(f"created db subnet group {group_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "DBSubnetGroupAlreadyExists":
            raise
        log(f"reusing db subnet group {group_name}")

    identifier = f"{PREFIX}-db"
    kwargs = dict(
        DBInstanceIdentifier=identifier,
        DBName=DB_NAME,
        Engine="postgres",
        DBInstanceClass=DB_INSTANCE_CLASS,
        MasterUsername=DB_USER,
        MasterUserPassword=password,
        AllocatedStorage=20,
        StorageType="gp3",
        VpcSecurityGroupIds=[sg_id],
        DBSubnetGroupName=group_name,
        # Free tier is Single-AZ only.
        MultiAZ=False,
        # Never reachable from the internet. Workers sit inside the VPC.
        PubliclyAccessible=False,
        BackupRetentionPeriod=0,   # short-lived coursework instance
        DeletionProtection=False,
    )
    if DB_ENGINE_VERSION:
        kwargs["EngineVersion"] = DB_ENGINE_VERSION

    try:
        rds.create_db_instance(**kwargs)
        log(f"creating rds instance {identifier} (this takes several minutes)")
    except ClientError as e:
        if e.response["Error"]["Code"] != "DBInstanceAlreadyExists":
            raise
        log(f"rds instance {identifier} already exists")

    waiter = rds.get_waiter("db_instance_available")
    waiter.wait(DBInstanceIdentifier=identifier,
                WaiterConfig={"Delay": 20, "MaxAttempts": 60})

    inst = rds.describe_db_instances(DBInstanceIdentifier=identifier)["DBInstances"][0]
    endpoint = inst["Endpoint"]
    log(f"rds available at {endpoint['Address']}:{endpoint['Port']}")
    return {
        "identifier": identifier,
        "host": endpoint["Address"],
        "port": endpoint["Port"],
        "dbname": DB_NAME,
        "user": DB_USER,
    }


# --------------------------------------------------------------------------
# Caching (ElastiCache Redis)
# --------------------------------------------------------------------------

def create_cache(ec, subnet_ids: list[str], sg_id: str) -> dict:
    group_name = f"{PREFIX}-cache-subnets"
    try:
        ec.create_cache_subnet_group(
            CacheSubnetGroupName=group_name,
            CacheSubnetGroupDescription="Curbline cache subnets",
            SubnetIds=subnet_ids,
        )
        log(f"created cache subnet group {group_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "CacheSubnetGroupAlreadyExists":
            raise
        log(f"reusing cache subnet group {group_name}")

    cluster_id = f"{PREFIX}-cache"
    try:
        ec.create_cache_cluster(
            CacheClusterId=cluster_id,
            Engine="redis",
            CacheNodeType=CACHE_NODE_TYPE,
            NumCacheNodes=1,
            CacheSubnetGroupName=group_name,
            SecurityGroupIds=[sg_id],
        )
        log(f"creating elasticache cluster {cluster_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "CacheClusterAlreadyExists":
            raise
        log(f"elasticache cluster {cluster_id} already exists")

    # No public waiter for cache clusters, so poll.
    for _ in range(90):
        c = ec.describe_cache_clusters(
            CacheClusterId=cluster_id, ShowCacheNodeInfo=True
        )["CacheClusters"][0]
        if c["CacheClusterStatus"] == "available":
            node = c["CacheNodes"][0]["Endpoint"]
            log(f"elasticache available at {node['Address']}:{node['Port']}")
            return {"cluster_id": cluster_id,
                    "host": node["Address"], "port": node["Port"]}
        time.sleep(20)
    sys.exit("ElastiCache cluster did not become available in time.")


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--admin-cidr", default=None,
                    help="CIDR allowed to reach SSH and the dashboard. "
                         "Defaults to this machine's public IP as a /32.")
    args = ap.parse_args()

    session = boto3.session.Session(region_name=args.region)
    ec2 = session.client("ec2")
    sqs = session.client("sqs")
    sns = session.client("sns")
    s3 = session.client("s3")
    rds = session.client("rds")
    ecache = session.client("elasticache")
    account_id = session.client("sts").get_caller_identity()["Account"]

    vpc_id, subnet_ids = default_vpc(ec2)

    app_sg = ensure_sg(ec2, vpc_id, f"{PREFIX}-app", "Curbline EC2 workers")
    db_sg = ensure_sg(ec2, vpc_id, f"{PREFIX}-db", "Curbline RDS")
    cache_sg = ensure_sg(ec2, vpc_id, f"{PREFIX}-cache", "Curbline ElastiCache")

    # Only the app tier may reach the data tier. No 0.0.0.0/0 anywhere.
    allow_from_sg(ec2, db_sg, app_sg, 5432)
    allow_from_sg(ec2, cache_sg, app_sg, 6379)

    # Without this the console is unreachable from a browser, which is the
    # kind of thing you discover after everything else already works.
    # my_public_cidr() resolves whichever host runs this. Under bootstrap.sh
    # that is the EC2 instance, not the operator. See E-008.
    admin = args.admin_cidr
    if admin is None:
        if this_instance_id() is not None:
            log("WARNING: no --admin-cidr and this is an EC2 instance. The "
                "autodetected address is this instance's own, so tcp/8000 "
                "would open to nobody with a browser. Re-run with "
                "--admin-cidr <your-public-ip>/32.")
        admin = my_public_cidr()
    if admin:
        allow_from_cidr(ec2, app_sg, 22, admin, "ssh")
        allow_from_cidr(ec2, app_sg, 8000, admin, "curbline console")
    else:
        log("WARNING: could not determine your public IP. Open tcp/22 and "
            "tcp/8000 on the app group manually, scoped to your address.")

    attach_sg_to_self(ec2, app_sg)

    queues = create_queues(sqs)
    topic_arn = create_topic(sns)
    bucket = create_bucket(s3, args.region, account_id)

    password = make_password()
    db = create_rds(rds, subnet_ids, db_sg, password)
    cache = create_cache(ecache, subnet_ids, cache_sg)

    stack = {
        "region": args.region,
        "vpc_id": vpc_id,
        "subnet_ids": subnet_ids,
        "security_groups": {"app": app_sg, "db": db_sg, "cache": cache_sg},
        "queues": queues,
        "sns_topic_arn": topic_arn,
        "audit_bucket": bucket,
        "db": db,
        "cache": cache,
    }
    STACK_FILE.write_text(json.dumps(stack, indent=2))
    log(f"wrote {STACK_FILE}")

    env = STACK_FILE.parent.parent / ".env"
    env.write_text(
        f"CURBLINE_REGION={args.region}\n"
        f"CURBLINE_DB_HOST={db['host']}\n"
        f"CURBLINE_DB_PORT={db['port']}\n"
        f"CURBLINE_DB_NAME={db['dbname']}\n"
        f"CURBLINE_DB_USER={db['user']}\n"
        f"CURBLINE_DB_PASSWORD={password}\n"
        f"CURBLINE_CACHE_HOST={cache['host']}\n"
        f"CURBLINE_CACHE_PORT={cache['port']}\n"
        f"CURBLINE_QUEUE_INGEST={queues['ingest']['url']}\n"
        f"CURBLINE_QUEUE_ZONES={queues['zones']['url']}\n"
        f"CURBLINE_SNS_TOPIC={topic_arn}\n"
        f"CURBLINE_AUDIT_BUCKET={bucket}\n"
    )
    env.chmod(0o600)
    log(f"wrote {env} with mode 600")

    print("\nDashboard will be at: http://<this instance public IP>:8000")
    print("\nNext:")
    print(f"  psql -h {db['host']} -U {DB_USER} -d {DB_NAME} -f sql/schema.sql")
    print("  (password is in .env; screenshot the PostGIS version check)")


if __name__ == "__main__":
    main()
