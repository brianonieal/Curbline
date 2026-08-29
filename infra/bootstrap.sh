#!/usr/bin/env bash
# Run once on a fresh Ubuntu EC2 instance, from /home/ubuntu.
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip postgresql-client \
                        redis-tools git jq unzip curl

# Ubuntu 24.04 removed awscli from its archive, so apt cannot install it here.
# provision.py, teardown.py and the workers all use boto3 and never need the
# CLI, but DEMO.md and COSTS.md shell out to it for evidence capture, so install
# v2 from AWS directly. uname -m matches AWS archive naming exactly, which keeps
# this correct if the instance type ever moves to Graviton.
if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscliv2.zip
  unzip -q -o /tmp/awscliv2.zip -d /tmp
  sudo /tmp/aws/install --update
  rm -rf /tmp/aws /tmp/awscliv2.zip
fi

cd /home/ubuntu/curbline

# The instance survives teardown, so this box is usually a returning one with a
# clone from the last session. Provisioning a stale checkout is how you end up
# debugging a defect that was fixed hours ago, or hitting a missing fixture that
# only reached the repo after the last pull. See E-031.
if [ -d .git ]; then
  echo "[bootstrap] repo at $(git rev-parse --short HEAD), fetching"
  git pull --ff-only || {
    echo "[bootstrap] git pull failed. Resolve it before provisioning:" >&2
    echo "[bootstrap]   the code about to create billable resources is stale." >&2
    exit 1
  }
  echo "[bootstrap] now at $(git rev-parse --short HEAD)"
fi

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Provision every managed service. Takes several minutes; RDS is the slow part.
#
# CURBLINE_ADMIN_CIDR must be the address of the browser you will open the
# console from. provision.py falls back to checkip.amazonaws.com, and from this
# host that resolves to the instance's own public IP, which opens tcp/8000 to
# nobody. See E-008.
: "${CURBLINE_ADMIN_CIDR:?set to your own public IP as a /32, e.g. 203.0.113.7/32. See E-008}"
.venv/bin/python infra/provision.py \
  --region "${AWS_REGION:-us-east-1}" \
  --admin-cidr "$CURBLINE_ADMIN_CIDR"

# Load the schema. Screenshot the PostGIS version line that this prints.
set -a; source .env; set +a
# -P pager=off because psql pages a wide result set when stdout is a tty, and
# the PostGIS version row is very wide. Without it this script blocks on less
# waiting for a keypress, which is invisible in an unattended run. See E-012.
PGPASSWORD="$CURBLINE_DB_PASSWORD" psql -P pager=off \
  -h "$CURBLINE_DB_HOST" -U "$CURBLINE_DB_USER" -d "$CURBLINE_DB_NAME" \
  -f sql/schema.sql

sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
# enable, then restart explicitly. "enable --now" only starts a unit that is
# not already running, and these units are enabled, so after a reboot systemd
# has already started them against whatever .env was on disk at boot time.
# provision.py rewrites .env with new endpoints on every run, so without the
# restart the workers keep a database host that no longer exists. See E-015.
sudo systemctl enable curbline-collector curbline-correlator curbline-dispatcher curbline-api
sudo systemctl restart curbline-collector curbline-correlator curbline-dispatcher curbline-api

systemctl --no-pager status 'curbline-*'
