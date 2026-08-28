# Account setup

Everything in `infra/` assumes this is done. It is the one part that is not
fully scripted, because creating the role that lets you script things is the
bootstrapping step.

**Run all of this from AWS CloudShell.** CloudShell is already authenticated as
your console identity, so no long-lived access keys are created and nothing
sensitive lands on a laptop. Do not create root access keys to run it elsewhere.

Every step below is idempotent. If a run fails partway, fix the cause and start
again from step 1.

---

## 0. Before you start

**Region.** Nine defaults in this repo point at `us-east-1`, including
`curbline/config.py`, which the three workers read at runtime. Set the console
region to **N. Virginia (us-east-1)** and keep it there. Provisioning in one
region with an instance in another produces a failure that presents exactly like
a security group problem, which sends you down the wrong ladder.

**CloudShell is per-region.** Changing the console dropdown does not move an
already-open shell. Close CloudShell and reopen it, and confirm the tab reads
`us-east-1`.

**Your address.** Open `https://checkip.amazonaws.com` in a normal browser tab
and keep the number. Not from CloudShell: CloudShell runs inside AWS and reports
an AWS address, which is E-008. Never commit the real value.

---

## 1. Confirm where you are

```bash
echo "account: $(aws sts get-caller-identity --query Account --output text)"
echo "region:  ${AWS_REGION:-unset}"
```

Region must read `us-east-1`. If it does not, reopen CloudShell.

---

## 2. Put the two files in CloudShell

`account-setup.sh` needs `iam-policy.json` beside it. Either clone this repo:

```bash
git clone <your-repo-url> ~/curbline && cd ~/curbline
```

Or, if the repo is not published yet, use CloudShell **Actions, Upload file**
once for each of `infra/account-setup.sh` and `infra/iam-policy.json`. They land
in your home directory, which is where the script looks.

The repo has to be reachable by URL before `bootstrap.sh` can run at all, since
its documented first step on the instance is `git clone`. Publishing it now
removes this step permanently.

---

## 3. Run the setup script

Substitute your own address from step 0.

```bash
bash ~/account-setup.sh 203.0.113.7/32
```

It creates the key pair, launches a `t3.micro` probe instance, waits for it to
reach `running`, creates the `curbline-ec2` role with the scoped policy, builds
and associates the instance profile, and opens tcp/22 to your address only.

The instance launches **before** the IAM work on purpose. A new-account identity
or payment verification hold, or a zero vCPU quota in the region, both surface at
that moment rather than after twenty minutes of setup. Neither is a bug and
neither can be compressed by working harder, so finding out early is the point.

If the launch fails with `VcpuLimitExceeded`, `PendingVerification`,
`OptInRequired` or `Blocked`, stop. That is a quota increase or a support case,
not something to debug.

The instance-profile association retries for two minutes. IAM is eventually
consistent and a fresh profile is routinely invisible to EC2 for a minute, with
an error that reads like a permissions failure rather than a timing one.

---

## 4. Download the private key

The private half of a key pair is returned **only at creation** and cannot be
retrieved again. If you lose it you must delete the pair, create a new one, and
relaunch the instance, because the public key is baked in at launch.

CloudShell **Actions, Download file**, exact path:

```
/home/cloudshell-user/curbline.pem
```

Save it somewhere you will find again. Do this before closing the tab.

---

## 5. Get the connect command

This prints a complete line with the real address already in it, so there is
nothing to substitute:

```bash
echo "ssh -i curbline.pem ubuntu@$(aws ec2 describe-instances --filters Name=tag:Name,Values=curbline Name=instance-state-name,Values=running --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)"
```

Run the printed line from the directory holding `curbline.pem`. On Windows, if
OpenSSH rejects the key as too permissive:

```
icacls curbline.pem /inheritance:r /grant:r "${env:USERNAME}:R"
```

---

## 6. Verify the role before going further

On the instance:

```bash
aws sts get-caller-identity
```

An Arn ending `assumed-role/curbline-ec2/...` means the role is working. An error
here means `bootstrap.sh` fails on its first API call, so fix it now rather than
halfway through provisioning.

**This is the entry criterion for v0.5.0. Stop here.** Do not run `bootstrap.sh`
in the same sitting.

---

## 7. Then run bootstrap

```bash
git clone <your-repo-url> ~/curbline && cd ~/curbline
```

```bash
# CURBLINE_ADMIN_CIDR is YOUR address, read from your own machine in step 0, not
# from this instance. curl here returns the instance. See E-008.
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=203.0.113.7/32 ./infra/bootstrap.sh
```

`provision.py` creates the `curbline-app` group and attaches it to the running
instance automatically, which resolves the ordering problem: the group cannot
exist before the instance that provisions it, but the instance has to be in that
group before RDS and ElastiCache accept connections from it. `attach_sg_to_self`
appends rather than replaces, so the instance stays in the default group too,
which is what makes rung 3 of the v0.5.0 connectivity ladder work.

If your address changes, re-run with a corrected `--admin-cidr`.

---

## Running gate-check

`scripts/gate-check.sh` defaults to bare `python3`, which will not have this
project's dependencies and reports a **false hard block on the test suite**.
Point it at the venv:

```bash
PYTHON=.venv/bin/python ./scripts/gate-check.sh v0.5.0
```

Verified: bare interpreter reports 18 failed and 4 errors, venv interpreter
reports 26 passed. The tests are fine; the interpreter was wrong.

---

## If something is already half-built

Everything above is safe to re-run. To see current state:

```bash
aws ec2 describe-instances --filters Name=tag:Name,Values=curbline Name=instance-state-name,Values=pending,running --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]' --output table
```

To start genuinely clean, terminate the instance and delete the key pair, then
go back to step 1. The IAM role and the security group rule are harmless to
leave in place and the script skips them if they exist.
