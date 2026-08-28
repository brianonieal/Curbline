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

---

#### Run this FIRST, before any other capture

Every other item in this checklist records state that already exists. This one
can still find a defect, so it runs while there is time to act on the result.
It is fully reversible, which is a convenience and not the reason for the
ordering.

```bash
CACHE_SG=$(jq -r .security_groups.cache infra/stack.json)
APP_SG=$(jq -r .security_groups.app infra/stack.json)

aws ec2 revoke-security-group-ingress   --group-id "$CACHE_SG" --protocol tcp --port 6379 --source-group "$APP_SG"
sudo systemctl restart curbline-api        # force a fresh connect attempt
# screenshot: console still working, cache pip amber, /api/health degraded

aws ec2 authorize-security-group-ingress   --group-id "$CACHE_SG" --protocol tcp --port 6379 --source-group "$APP_SG"
sudo systemctl restart curbline-api
# confirm pip returns green before continuing
```

**The restart is not optional.** Security groups are stateful. An already
established Redis connection can survive the revoke through connection tracking,
so without a restart the API keeps using a socket that the new rule would never
have permitted. The test would pass and demonstrate nothing. Restarting forces a
fresh connect that has to clear the revoked rule.

**Expect the console to feel slower while degraded.** Every broadcast tick now
absorbs a `socket_connect_timeout` before falling through to Postgres. That
latency is the degradation path working, not a second fault, and it is worth
capturing rather than hiding.

**Confirm green before continuing.** The remaining captures assume a healthy
cache, and a status bar still showing amber will contradict the cache hit rate
screenshot two items later.

---

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
- [ ] Cache unreachable: console still serving, pip amber, `/api/health`
      degraded. Procedure is "Run this FIRST" above; do not leave it until last.

### Report

Fill in `docs/REPORT.md`. It is 15 points and takes longer than you think.

### Push and tear down

### Pre-teardown checklist

**Teardown is irreversible, and re-provisioning costs about twenty minutes plus
RDS creation time.** Anything missing after this point is unrecoverable inside
the deadline. Walk this against files on disk, not against a memory of having
taken them.

**Captures that require live AWS. Open each file and confirm it is legible.**

- [ ] Six console screenshots: RDS, ElastiCache, SQS, SNS, S3, EC2
- [ ] Cache-unreachable pair: degraded with amber pip, and healthy again after
      re-authorising
- [ ] `systemctl status 'curbline-*'` with four units active
- [ ] Three `journalctl` views: collector, correlator, dispatcher
- [ ] SQS queue depth non-zero
- [ ] `redis-cli INFO keyspace`
- [ ] `curl /api/health`
- [ ] `SELECT PostGIS_Full_Version();`
- [ ] `SELECT * FROM current_clusters();`
- [ ] `aws s3 ls` of `advisories/`, plus one audit object opened and readable
- [ ] `advisories` rows showing `sns_message_id` and `audit_key`
- [ ] Duplicate-delivery replay: same `ingest_id`, one row before and after
- [ ] Console: zero zones, active zone, before and after depth change
- [ ] Console: status bar showing cache hit rate
- [ ] SNS email, or the written reason it could not be captured

**Anything unobtainable is recorded, not silently dropped.** Put the reason in
the Appendix C row. A reader who sees "not captured, no NWS flood alert was
active in the study area during the window" learns something real about the
limits of the demonstration. A row that simply vanishes reads as an oversight.

**Repository state.**

- [ ] `git status` clean; `.env` and `infra/stack.json` not listed
- [ ] Screenshots committed, or deliberately excluded and stored elsewhere
- [ ] Appendix C File and Source columns filled for every captured row
- [ ] `docs/REPORT.md` has no bracketed placeholder remaining

**Only when every line above is ticked:**

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
