# Instance and role setup

Everything else in `infra/` assumes this is already done. It is the one part
that is not scripted, because creating the role that lets you script things is
the bootstrapping step.

Do this from your laptop, in the AWS console or CLI, before you touch anything
else. Fifteen minutes.

## 1. IAM role

```bash
cat > trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON

aws iam create-role --role-name curbline-ec2 \
  --assume-role-policy-document file://trust.json

aws iam put-role-policy --role-name curbline-ec2 \
  --policy-name curbline --policy-document file://infra/iam-policy.json

aws iam create-instance-profile --instance-profile-name curbline-ec2
aws iam add-role-to-instance-profile \
  --instance-profile-name curbline-ec2 --role-name curbline-ec2
```

The policy is deliberately scoped to what `provision.py`, the three workers,
and `teardown.py` actually call. It is not `AdministratorAccess`, and being
able to say that in the report is worth the ten extra minutes.

## 2. EC2 instance

Ubuntu 24.04, `t3.micro` (free tier), default VPC, public subnet, auto-assign
public IP enabled. Attach the `curbline-ec2` instance profile at launch.

Launch it with the **default** security group. `provision.py` creates the
`curbline-app` group and attaches it to the running instance automatically,
which resolves the ordering problem: the group cannot exist before the instance
that provisions it, but the instance has to be in that group before RDS and
ElastiCache will accept connections from it.

```bash
aws ec2 run-instances \
  --image-id <ubuntu-24.04-ami-for-your-region> \
  --instance-type t3.micro \
  --iam-instance-profile Name=curbline-ec2 \
  --associate-public-ip-address \
  --key-name <your-keypair> \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=curbline}]'
```

Look up the AMI id for your region rather than copying one; they are
region-specific and they change.

### Probe-first order, revised 2026-08-27

The command above assumes the role already exists. On a **new AWS account**, run
the launch first, without `--iam-instance-profile`, and attach the profile
afterwards with `aws ec2 associate-iam-instance-profile`. A new account can sit
behind identity or payment verification, and can start with a low or zero vCPU
quota in a region. Neither is a bug, both can take hours to clear, and neither
can be compressed by working harder. Getting one instance to `running` is the
probe that surfaces them; do it before spending time on IAM.

Two things the default security group does not give you:

- **No inbound SSH.** The default group permits all traffic between its own
  members and nothing from outside, so you cannot reach the box until you add
  tcp/22 from your own address. `provision.py` opens 22 and 8000 later on
  `curbline-app`, which is too late to verify step 3.
- **No key pair by default.** `--key-name` must reference one that exists.

## 2b. Running gate-check

`scripts/gate-check.sh` defaults to bare `python3`, which will not have this
project's dependencies and reports a **false hard block on the test suite**.
Point it at the venv:

```bash
PYTHON=.venv/bin/python ./scripts/gate-check.sh v0.5.0
```

Verified: bare interpreter reports 18 failed / 4 errors, venv interpreter
reports 26 passed. The tests are fine; the interpreter was wrong.

## 3. Verify the role before going further

SSH in and confirm the instance can act as itself:

```bash
aws sts get-caller-identity
```

An `arn:aws:sts::...:assumed-role/curbline-ec2/...` means the role is working.
An error here means `bootstrap.sh` will fail on its first API call, so fix it
now rather than halfway through provisioning.

## 4. Then run bootstrap

```bash
git clone <repo> ~/curbline && cd ~/curbline
AWS_REGION=us-east-1 ./infra/bootstrap.sh
```

`provision.py` opens tcp/22 and tcp/8000 to your current public address only.
If your address changes, re-run with `--admin-cidr`.
