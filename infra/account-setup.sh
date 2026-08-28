#!/usr/bin/env bash
# One-time account setup: key pair, probe instance, instance role, SSH ingress.
#
# Run this from AWS CloudShell. CloudShell is already authenticated as your
# console identity, so no long-lived access keys are created and nothing
# sensitive lands on a laptop. Do not create root access keys to run this
# somewhere else; AWS advises against them and they are the worst credential to
# leave on disk.
#
#   ./infra/account-setup.sh 203.0.113.7/32
#
# The argument is YOUR browser's public address as a /32, read from
# https://checkip.amazonaws.com in a normal browser tab. Not from CloudShell:
# CloudShell runs inside AWS and reports an AWS address, which is E-008 all over
# again. Never commit the real value.
#
# Ordering is deliberate. The instance launches before the IAM work so that a
# new-account verification hold or a zero vCPU quota surfaces in the first
# minute, not after twenty minutes of setup. That failure is the reason this
# script exists.
set -euo pipefail

ADMIN_CIDR="${1:?usage: $0 <your-ip>/32   read from https://checkip.amazonaws.com in your browser}"
REGION="${AWS_REGION:-us-east-1}"
KEY="curbline"
ROLE="curbline-ec2"

export AWS_DEFAULT_REGION="$REGION"
say() { printf '\n=== %s\n' "$*"; }

say "region $REGION, account $(aws sts get-caller-identity --query Account --output text)"

# --- key pair -------------------------------------------------------------
say "key pair"
if aws ec2 describe-key-pairs --key-names "$KEY" >/dev/null 2>&1; then
  echo "$KEY exists, leaving it alone. The private key is returned only at"
  echo "creation, so if you no longer have the .pem, delete the pair and re-run."
else
  aws ec2 create-key-pair --key-name "$KEY" --query KeyMaterial --output text > ~/"$KEY".pem
  chmod 400 ~/"$KEY".pem
  echo "wrote ~/$KEY.pem   DOWNLOAD IT: CloudShell Actions > Download file"
fi

# --- probe instance -------------------------------------------------------
# Launched with no instance profile. Attaching it later is one API call and
# does not need a relaunch, and this way IAM is not on the critical path to
# finding out whether this account can launch anything at all.
say "probe instance"
IID=$(aws ec2 describe-instances \
        --filters Name=tag:Name,Values=curbline \
                  Name=instance-state-name,Values=pending,running \
        --query 'Reservations[].Instances[].InstanceId' --output text)

if [[ -n "$IID" ]]; then
  echo "reusing existing instance $IID"
else
  IID=$(aws ec2 run-instances \
    --image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
    --instance-type t3.micro \
    --key-name "$KEY" \
    --associate-public-ip-address \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=curbline}]' \
    --query 'Instances[0].InstanceId' --output text)
  echo "launched $IID"
fi

echo "waiting for running state (up to ~10 min; a new-account hold fails here)"
aws ec2 wait instance-running --instance-ids "$IID"
IP=$(aws ec2 describe-instances --instance-ids "$IID" \
       --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
echo "running, public ip $IP"

# --- service-linked roles -------------------------------------------------
# RDS and ElastiCache each need an account-level service-linked role to exist
# before their first create call. A brand new account has neither. The failure
# is reported as InvalidParameterValue "Missing necessary credentials" on
# CreateDBSubnetGroup, which reads like a credentials problem and sends you
# looking at the instance role instead of at the account. Creating these needs
# iam:CreateServiceLinkedRole, which is exactly why it happens here under your
# console identity rather than from the instance.
say "service-linked roles"
for svc in rds elasticache; do
  if aws iam create-service-linked-role --aws-service-name "$svc.amazonaws.com" >/dev/null 2>&1; then
    echo "created the $svc service-linked role"
  else
    echo "$svc service-linked role already present"
  fi
done


# --- instance role --------------------------------------------------------
say "iam role"
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  echo "role $ROLE exists"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    >/dev/null
  echo "created role $ROLE"
fi

POLICY="$(dirname "$0")/iam-policy.json"
[[ -f "$POLICY" ]] || { echo "missing $POLICY. Run this from a clone of the repo."; exit 1; }
aws iam put-role-policy --role-name "$ROLE" --policy-name curbline \
  --policy-document "file://$POLICY"
echo "attached the scoped inline policy (not AdministratorAccess, which is the"
echo "claim the report makes)"

aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1 || true
aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" \
  --role-name "$ROLE" >/dev/null 2>&1 || true

# --- associate ------------------------------------------------------------
# IAM is eventually consistent. A freshly created instance profile is routinely
# not visible to EC2 for a minute, and the error looks like a permissions
# problem rather than a timing one.
say "associating instance profile"
if aws ec2 describe-iam-instance-profile-associations \
     --filters "Name=instance-id,Values=$IID" \
     --query 'IamInstanceProfileAssociations[?State!=`disassociated`]' \
     --output text | grep -q .; then
  echo "already associated"
else
  for i in $(seq 1 12); do
    if aws ec2 associate-iam-instance-profile --instance-id "$IID" \
         --iam-instance-profile Name="$ROLE" >/dev/null 2>&1; then
      echo "associated on attempt $i"; break
    fi
    [[ $i -eq 12 ]] && { echo "could not associate after 12 tries"; exit 1; }
    sleep 10
  done
fi

# --- ssh ingress ----------------------------------------------------------
# The default security group permits all traffic between its own members and
# nothing from outside, so without this you cannot reach the box at all.
# provision.py opens 22 and 8000 on curbline-app later, which is too late to
# verify the role.
say "ssh ingress for $ADMIN_CIDR"
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
        --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups \
       --filters Name=group-name,Values=default Name=vpc-id,Values="$VPC" \
       --query 'SecurityGroups[0].GroupId' --output text)
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp \
  --port 22 --cidr "$ADMIN_CIDR" >/dev/null 2>&1 \
  && echo "opened tcp/22 on $SG to $ADMIN_CIDR" \
  || echo "tcp/22 already open to $ADMIN_CIDR on $SG"

# --- summary --------------------------------------------------------------
cat <<SUMMARY

=== done
  instance   $IID
  public ip  $IP
  key        ~/$KEY.pem   (download via Actions > Download file if you have not)
  role       $ROLE
  vpc / sg   $VPC / $SG

Next, from your laptop:
  ssh -i $KEY.pem ubuntu@$IP
  aws sts get-caller-identity

An arn ending in assumed-role/$ROLE/... means v0.5.0's entry criteria are met.
Then stop. Do not run bootstrap.sh tonight.
SUMMARY
