#!/usr/bin/env bash
# Run once on a fresh Ubuntu EC2 instance, from /home/ubuntu.
set -euo pipefail

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip postgresql-client \
                        redis-tools awscli git jq

cd /home/ubuntu/curbline
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Provision every managed service. Takes several minutes; RDS is the slow part.
.venv/bin/python infra/provision.py --region "${AWS_REGION:-us-east-1}"

# Load the schema. Screenshot the PostGIS version line that this prints.
set -a; source .env; set +a
PGPASSWORD="$CURBLINE_DB_PASSWORD" psql \
  -h "$CURBLINE_DB_HOST" -U "$CURBLINE_DB_USER" -d "$CURBLINE_DB_NAME" \
  -f sql/schema.sql

sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now curbline-collector curbline-correlator \
                            curbline-dispatcher curbline-api

systemctl --no-pager status 'curbline-*'
