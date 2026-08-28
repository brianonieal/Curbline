#!/usr/bin/env bash
#
# Capture every CLI evidence artifact in one pass, on the EC2 host.
#
# This exists because teardown is irreversible and re-provisioning costs about
# twenty minutes plus RDS creation time. Running fifteen captures by hand at the
# end of a long session is how one gets missed, and a missed capture is only
# discovered after the stack is gone. One command, one manifest, one place to
# check what is absent.
#
#   ./scripts/capture-evidence.sh
#
# Writes to docs/evidence/cli/. Safe to re-run: every file is overwritten.
# Never prints the database password. Every aws call pins --region us-east-1,
# because an empty result from a regional API means wrong region far more often
# than it means missing resource, and that already produced one false teardown
# report. See E-018.
#
# Screenshots are NOT covered here. Those are in DEMO.md and need a browser and
# the AWS console. This handles everything that is text.

set -uo pipefail   # deliberately not -e: one failed capture must not abandon
                   # the rest. A partial evidence set beats an aborted one.

cd "$(dirname "$0")/.."
OUT="docs/evidence/cli"
mkdir -p "$OUT"

REGION="us-east-1"
export AWS_REGION="$REGION"
export AWS_PAGER=""

if [ -f .env ]; then
  set -a; . ./.env; set +a
else
  echo "FATAL: no .env. Run infra/bootstrap.sh first." >&2
  exit 1
fi

: "${CURBLINE_DB_USER:=curbline}"
: "${CURBLINE_DB_NAME:=curbline}"
: "${CURBLINE_CACHE_PORT:=6379}"

CAPTURED=(); MISSING=()

# Every variable this script reads, defaulted to empty rather than left unset.
# Without this, `set -u` aborts the entire run on the first missing one, which
# is the exact failure the script exists to prevent: a partial capture that
# stops silently partway through the window you cannot get back. A blank value
# produces one failed capture and a manifest line instead.
for v in CURBLINE_DB_HOST CURBLINE_DB_PASSWORD CURBLINE_CACHE_HOST \
         CURBLINE_QUEUE_INGEST CURBLINE_QUEUE_ZONES \
         CURBLINE_QUEUE_INGEST_DLQ CURBLINE_QUEUE_ZONES_DLQ \
         CURBLINE_SNS_TOPIC CURBLINE_AUDIT_BUCKET CURBLINE_SOURCE \
         CURBLINE_DEPTH_THRESHOLD_CM CURBLINE_READING_WINDOW_MINS \
         CURBLINE_CLUSTER_EPS_FT CURBLINE_CLUSTER_MIN_SENSORS; do
  eval ": \"\${$v:=}\""
done

for v in CURBLINE_DB_HOST CURBLINE_CACHE_HOST CURBLINE_QUEUE_INGEST \
         CURBLINE_SNS_TOPIC CURBLINE_AUDIT_BUCKET; do
  eval "[ -n \"\$$v\" ]" || MISSING+=("\$$v is unset in .env")
done

# $1 output file, rest: command. Records whether it produced anything, because
# an empty file that exists is the failure mode this script is guarding against.
cap() {
  local name="$1"; shift
  local err; err="$(mktemp)"
  local ok=1
  if "$@" > "$OUT/$name" 2>"$err"; then
    [ -s "$OUT/$name" ] && ok=0
  fi
  if [ "$ok" -eq 0 ]; then
    CAPTURED+=("$name")
  else
    # Reason is kept in the manifest, not on disk. A stray .err file next to the
    # evidence would get committed and read as an artifact.
    MISSING+=("$name  ($(head -c 160 "$err" | tr '\n' ' '))")
    rm -f "$OUT/$name"
  fi
  rm -f "$err"
  return "$ok"
}

psql_q() {
  PGPASSWORD="$CURBLINE_DB_PASSWORD" psql -P pager=off -X \
    -h "$CURBLINE_DB_HOST" -U "$CURBLINE_DB_USER" -d "$CURBLINE_DB_NAME" \
    -c "$1"
}

echo "Capturing to $OUT (region $REGION)"
echo

# --- Managed services -------------------------------------------------------
cap rds.json aws rds describe-db-instances \
    --db-instance-identifier curbline-db --region "$REGION"
cap elasticache.json aws elasticache describe-cache-clusters \
    --cache-cluster-id curbline-cache --region "$REGION"
cap sqs-ingest.json aws sqs get-queue-attributes \
    --queue-url "$CURBLINE_QUEUE_INGEST" --attribute-names All --region "$REGION"
cap sqs-zones.json aws sqs get-queue-attributes \
    --queue-url "$CURBLINE_QUEUE_ZONES" --attribute-names All --region "$REGION"
cap sns-subscriptions.json aws sns list-subscriptions-by-topic \
    --topic-arn "$CURBLINE_SNS_TOPIC" --region "$REGION"
cap s3-advisories.txt aws s3 ls "s3://$CURBLINE_AUDIT_BUCKET/advisories/" \
    --recursive --region "$REGION"
cap s3-public-access-block.json aws s3api get-public-access-block \
    --bucket "$CURBLINE_AUDIT_BUCKET" --region "$REGION"

# Dead-letter queues. Depth here should be zero; capturing it is what makes
# that a stated result rather than an assumption. See E-024.
[ -n "${CURBLINE_QUEUE_INGEST_DLQ:-}" ] && cap sqs-ingest-dlq.json \
  aws sqs get-queue-attributes --queue-url "$CURBLINE_QUEUE_INGEST_DLQ" \
  --attribute-names All --region "$REGION"
[ -n "${CURBLINE_QUEUE_ZONES_DLQ:-}" ] && cap sqs-zones-dlq.json \
  aws sqs get-queue-attributes --queue-url "$CURBLINE_QUEUE_ZONES_DLQ" \
  --attribute-names All --region "$REGION"

# --- The host and its role --------------------------------------------------
TOKEN="$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
IID="$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || true)"
if [ -n "$IID" ]; then
  cap ec2-instance.txt aws ec2 describe-instances --instance-ids "$IID" \
      --region "$REGION"
else
  MISSING+=("ec2-instance.txt  (IMDSv2 returned no instance id)")
fi
cap caller-identity.json aws sts get-caller-identity --region "$REGION"

# --- The three graded components --------------------------------------------
cap systemctl-status.txt systemctl --no-pager --full status \
    curbline-collector curbline-correlator curbline-dispatcher curbline-api
for unit in collector correlator dispatcher; do
  cap "journal-$unit.txt" journalctl -u "curbline-$unit" -n 200 --no-pager
done

# --- Database ---------------------------------------------------------------
cap postgis-version.txt psql_q "SELECT PostGIS_Full_Version();"
cap table-counts.txt psql_q \
  "SELECT 'readings' t, count(*) FROM readings
   UNION ALL SELECT 'sensors', count(*) FROM sensors
   UNION ALL SELECT 'zones', count(*) FROM zones
   UNION ALL SELECT 'advisories', count(*) FROM advisories
   UNION ALL SELECT 'alerts', count(*) FROM alerts;"

# Explicit parameters, not the function's FloodNet defaults. A no-argument call
# answers at 5.0 cm whatever CURBLINE_SOURCE is, which would make this artifact
# describe a calibration the running system was not using. See E-025.
cap current-clusters.txt psql_q \
  "SELECT cluster_id, sensor_ids, sensor_count, max_depth_cm,
          ST_GeometryType(hull)
     FROM current_clusters(
       p_threshold_cm := ${CURBLINE_DEPTH_THRESHOLD_CM:-5.0},
       p_window_mins  := ${CURBLINE_READING_WINDOW_MINS:-15},
       p_eps_ft       := ${CURBLINE_CLUSTER_EPS_FT:-1640},
       p_minpoints    := ${CURBLINE_CLUSTER_MIN_SENSORS:-2});"

# The evidence for D-009: every advisory carries both an SNS message id and the
# S3 key that was written before it.
cap advisories-with-audit.txt psql_q \
  "SELECT advisory_id::text, zone_id::text, level, issued_at,
          sns_message_id, audit_key
     FROM advisories ORDER BY issued_at DESC LIMIT 20;"

# E-020 and E-021 are only proven dead by real rows: more than one advisory for
# some zone, and at least one zone that is not stuck open.
cap advisories-per-zone.txt psql_q \
  "SELECT zone_id::text, count(*) AS advisories,
          string_agg(level, ' -> ' ORDER BY issued_at) AS ladder
     FROM advisories GROUP BY zone_id ORDER BY count(*) DESC;"
cap zone-states.txt psql_q \
  "SELECT state, count(*), max(updated_at) AS latest
     FROM zones GROUP BY state ORDER BY state;"

# --- Cache ------------------------------------------------------------------
cap redis-keyspace.txt redis-cli -h "$CURBLINE_CACHE_HOST" \
    -p "$CURBLINE_CACHE_PORT" INFO keyspace
cap redis-stats.txt redis-cli -h "$CURBLINE_CACHE_HOST" \
    -p "$CURBLINE_CACHE_PORT" MGET \
    stats:cache:hits stats:cache:misses stats:cache:errors
cap redis-heartbeats.txt redis-cli -h "$CURBLINE_CACHE_HOST" \
    -p "$CURBLINE_CACHE_PORT" MGET \
    heartbeat:collector heartbeat:correlator heartbeat:dispatcher

# --- API --------------------------------------------------------------------
cap api-health.json curl -sS --max-time 10 http://localhost:8000/api/health
cap api-state.json curl -sS --max-time 10 http://localhost:8000/api/state

# --- Manifest ---------------------------------------------------------------
{
  echo "# CLI evidence manifest"
  echo
  echo "Captured $(date -u +%Y-%m-%dT%H:%M:%SZ) by scripts/capture-evidence.sh"
  echo "Region $REGION. Source ${CURBLINE_SOURCE:-usgs}."
  echo
  echo "## Present (${#CAPTURED[@]})"
  [ ${#CAPTURED[@]} -gt 0 ] && printf -- '- %s\n' "${CAPTURED[@]}"
  if [ ${#MISSING[@]} -gt 0 ]; then
    echo
    echo "## MISSING (${#MISSING[@]})"
    echo
    echo "Each line is a capture that produced nothing, with the reason."
    echo "Resolve or record why in the Appendix C row. Do NOT tear down with"
    echo "an unexplained entry here."
    printf -- '- %s\n' "${MISSING[@]}"
  fi
} > "$OUT/MANIFEST.md"

echo
echo "captured ${#CAPTURED[@]}, missing ${#MISSING[@]}"
[ ${#MISSING[@]} -gt 0 ] && printf -- '  MISSING: %s\n' "${MISSING[@]}"
echo "manifest: $OUT/MANIFEST.md"
echo
echo "Screenshots are still manual. See DEMO.md."
exit 0
