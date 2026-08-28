# Run book and evidence checklist

Two days, four blocks. The order is chosen so the riskiest work happens while
there is still time to recover from it.

---

## Block 1: provision (do this first, before any application work)

The single most likely way to lose a day is a security group that reaches
nothing. Find that out now, not tomorrow.

```bash
# On a fresh Ubuntu EC2 instance with an IAM instance role attached
git clone <repo> ~/curbline && cd ~/curbline
# CURBLINE_ADMIN_CIDR is YOUR address, read from your own machine, not
# from this instance. curl on the instance returns the instance. See E-008.
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=203.0.113.7/32 ./infra/bootstrap.sh
```

**Block 1 is not finished until this succeeds:**

```bash
set -a; source .env; set +a
PGPASSWORD="$CURBLINE_DB_PASSWORD" psql -h "$CURBLINE_DB_HOST" \
  -U "$CURBLINE_DB_USER" -d "$CURBLINE_DB_NAME" -c "SELECT PostGIS_Full_Version();"
redis-cli -h "$CURBLINE_CACHE_HOST" ping
```

If either fails, stop and fix networking. Do not start writing features.

**Abort condition.** If EC2 still cannot reach RDS by end of day one, switch to
DynamoDB with shapely in the worker. Same three-process shape, same rubric
coverage, weaker queries. A working demo beats an elegant one that does not run.

---

## Block 2: frontend, in parallel with block 1

The mock server needs no AWS at all, so this can be built on a laptop while
provisioning runs.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python api/mock_server.py
# http://localhost:8000
```

The mock runs a three-minute storm cycle that rises and recedes, so every
visual state appears without waiting for rain: forming and active zones,
monitor, advisory and warning levels, and one zone corroborated by a National
Weather Service polygon while the other is not.

**The mock is a development tool. Never screenshot it for evidence.**

---

## Block 3: pipeline verification

```bash
systemctl status 'curbline-*'
journalctl -u curbline-correlator -f
```

Force a zone without waiting for weather, by lowering the threshold:

```bash
sudo systemctl set-environment CURBLINE_DEPTH_THRESHOLD_CM=0.5
sudo systemctl restart curbline-correlator curbline-dispatcher
```

Put it back before capturing evidence. A threshold of 0.5 cm produces zones
from noise, which is fine for proving the wiring and dishonest for a screenshot.

Confirm each stage independently:

```bash
aws sqs get-queue-attributes --queue-url "$CURBLINE_QUEUE_INGEST" \
  --attribute-names ApproximateNumberOfMessages
psql ... -c "SELECT count(*) FROM readings;"
psql ... -c "SELECT zone_id, sensor_count, max_depth_cm, state FROM zones;"
aws s3 ls "s3://$CURBLINE_AUDIT_BUCKET/advisories/" --recursive | tail
```

Subscribe an email address to the SNS topic before this block, or you will have
no delivery to screenshot:

```bash
aws sns subscribe --topic-arn "$CURBLINE_SNS_TOPIC" \
  --protocol email --notification-endpoint you@example.com
```

---

## Block 4: evidence, report, push, tear down

### Screenshot checklist

The rubric awards 30 points for demonstrating end-to-end function and says it is
on you to provide enough screenshots to prove it. Capture all of these.

**Cloud integration (10 pts)**
- [ ] RDS console: instance `curbline-db`, status Available, engine PostgreSQL
- [ ] ElastiCache console: cluster `curbline-cache`, status Available
- [ ] SQS console: all four queues, showing messages available
- [ ] SNS console: topic with a confirmed subscription
- [ ] S3 console: audit bucket with objects under `advisories/`
- [ ] EC2 console: the instance, with its IAM role attached

**Distributed application (10 pts)**
- [ ] `systemctl status 'curbline-*'` with all four units active
- [ ] `journalctl -u curbline-collector` showing readings published
- [ ] `journalctl -u curbline-correlator` showing clusters published
- [ ] `journalctl -u curbline-dispatcher` showing an advisory with its audit key
- [ ] SQS queue depth non-zero, which is the visible proof the stages are decoupled

**Technology components (15 pts)**
- [ ] `SELECT PostGIS_Full_Version();` output
- [ ] `SELECT * FROM current_clusters();` returning real zones
- [ ] `redis-cli ... INFO keyspace` showing cached sensor keys
- [ ] The received SNS email
- [ ] One S3 audit object opened, showing the thresholds recorded alongside the decision

**End-to-end (30 pts)**
- [ ] Console with zero zones (baseline)
- [ ] Console with an active zone drawn, rail filled, advisory queued
- [ ] Same zone before and after, showing depth change on the rail
- [ ] A zone with `NWS confirmed` on the card, next to one without
- [ ] Status bar showing queue depth, cache hit rate, PostGIS up
- [ ] `curl /api/health` output

**Deliberate failure, which is worth more than it costs**
- [ ] Stop ElastiCache, reload the console, show it still working with the cache
      pip amber. This demonstrates the degradation path rather than claiming it.

### Report

Fill in `docs/REPORT.md`. It is 15 points and takes longer than you think.

### Push and tear down

```bash
git status                       # confirm .env is NOT listed
git add -A && git commit -m "Curbline v1.0" && git push
python3 infra/teardown.py --confirm
```

**Check `git status` before pushing.** `.env` holds the RDS master password and
is gitignored, but verify rather than trust. If it ever lands in a public repo,
rotate the password immediately; deleting the commit is not enough.

---

## Known limitations to state in the report rather than hide

1. Many National Weather Service products are zone-based and arrive with
   `geometry: null`, so they cannot participate in spatial correlation.
2. Zone identity is derived from the member sensor set, so a zone that gains or
   loses one sensor becomes a new zone. Production would match on spatial
   overlap against the prior cycle.
3. Detection thresholds are reasoned choices, not calibrated against ground
   truth. Nothing here has been validated against observed flooding.
4. A `forming` zone delays notification by one cycle, trading latency for noise
   suppression.
5. If the demo uses replayed data, say so plainly and say when it was captured.
