# Run book and evidence checklist

**Blocks 1 through 3 below are history.** They describe the original build and
are kept because they record why the order was chosen. If you are picking this
up to finish the submission, use the capture session immediately below and
ignore them unless something breaks.

---

## THE CAPTURE SESSION

**Budget: one 2-hour window.** The timetable below is the plan of record. It is
tighter than the earlier version in two deliberate ways, both of which cost
evidence and buy time:

- **The teardown rehearsal is dropped.** An earlier plan provisioned, tore down
  the empty stack to prove `teardown.py` works, then re-provisioned. That cost
  about thirty minutes and a second RDS creation. It also created a hazard it
  was not worth accepting: `teardown.py` deletes the SNS topic, and a deleted
  topic takes its confirmed subscription with it, so a confirmation clicked
  against the first stack would have to be redone. Dropping it accepts an
  untested `teardown.py`. See **E-035** for the accepted risk and its
  mitigation.
- **Eleven captures, not twenty-two.** Chosen by rubric points that have no
  committed substitute. The eleven dropped rows stay in Appendix C marked
  **skipped** with a reason each, because a row deleted is a row nobody can
  audit. Appendix C reserves **unmet** for the one row that failed for a reason
  outside this project's control, which is a different claim from dropped for
  time.

**Before you start, have ready:** your own public IP read from *your* machine,
not the instance (E-008), and the mailbox for the SNS subscription open.

| Clock | Step | Budget |
|---|---|---|
| 0:00 | Pin us-east-1. Verify nothing is **already** running and billing | 5m |
| 0:05 | `bootstrap.sh` | 20m |
| 0:25 | `preflight.py`, and read it. Do not start units if it fails | 2m |
| 0:27 | Confirm the SNS subscription from the inbox | 3m |
| 0:30 | `preflight.py` again. Must pass | 2m |
| 0:32 | Point at `replay.escalation.json`, verify with `show-environment` | 5m |
| 0:37 | Let the pipeline run. Capture the 3 AWS console tabs in this window | 10m |
| 0:47 | Cache degradation: revoke, restart api, capture amber, restore | 15m |
| 1:02 | Four terminal captures | 15m |
| 1:17 | Three console captures | 20m |
| 1:37 | `capture-evidence.sh`, eyeball the two proofs, commit | 10m |
| 1:47 | Teardown, verify by console | 10m |

Ends at 1:57. There is three minutes of slack in a two-hour window, so a defect
in any of the sixteen fixes that have never run against real managed services
comes out of the capture budget, not out of thin air. If something breaks, drop
console captures before you drop teardown.

```bash
# ---- 0:00  Pin the region, then prove the account is empty.          5m
#      An empty result from a regional API means check the region FIRST.
#      It never means the resource is gone. This step exists because a
#      wrong-region probe once reported a live billing stack as torn down.
#      See E-018.
aws configure set region us-east-1
aws rds describe-db-instances --region us-east-1 \
  --query 'DBInstances[].DBInstanceIdentifier'
aws elasticache describe-cache-clusters --region us-east-1 \
  --query 'CacheClusters[].CacheClusterId'
#      Both must return []. If either does not, tear that down before
#      provisioning or you will bill two stacks for the whole window.

# ---- 0:05  Provision. ~20 min, mostly waiting on RDS.               20m
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=<your-ip>/32 ./infra/bootstrap.sh

# ---- 0:25  Preflight. Expect it to FAIL on the SNS subscription.     2m
#      It runs every query the workers run, with the same argument types,
#      including the open_zones() lateral and the NULL-expiry predicate,
#      neither of which has ever executed against real PostgreSQL.
set -a; source .env; set +a
.venv/bin/python scripts/preflight.py
```

**0:27  Confirm the SNS subscription from the inbox. 3m.** AWS sends the
confirmation link the moment the subscription is created. An unconfirmed
subscription silently drops every publication, so any advisory issued before
you click it is gone and cannot be replayed into your inbox. This is the item
that has slipped past two gates, and it slips because it feels like it can
wait.

```bash
# ---- 0:30  Preflight again. This time it must pass clean.            2m
.venv/bin/python scripts/preflight.py

# ---- 0:32  The escalation fixture, and verify it took.               5m
#      replay.example.json CANNOT produce a ladder: it grows its wet set as
#      the storm deepens, zone identity is a membership hash (D-003), so every
#      depth tier becomes a NEW zone. A correct system running it produces one
#      advisory per zone, which looks exactly like the E-021 defect. See E-030.
REPLAY=/home/ubuntu/curbline/data/replay.escalation.json
sudo systemctl set-environment CURBLINE_REPLAY_FILE=$REPLAY
sudo systemctl restart 'curbline-*'
systemctl show-environment | grep CURBLINE_REPLAY_FILE   # must end in escalation

# ---- 0:37  Let it run. Do NOT capture the console yet.              10m
#      The cache hit rate is published to Redis from the worker loop between
#      batches, and only the correlator calls cache.sensor(). Until it has
#      clustered a batch and flushed, read_stats() returns null and the
#      console correctly shows `n/a`. Shooting now captures a legitimate
#      `n/a` that reads as a failed criterion. See E-019.
#
#      Use this window for the three AWS console tabs: RDS, ElastiCache, SQS.
watch -n10 "PGPASSWORD=\$CURBLINE_DB_PASSWORD psql -tA \
  -h \$CURBLINE_DB_HOST -U \$CURBLINE_DB_USER -d \$CURBLINE_DB_NAME \
  -c \"SELECT zone_id, count(*), string_agg(level, ' then ' ORDER BY issued_at) \
      FROM advisories GROUP BY zone_id\""
```

**0:47  Cache degradation. 15m.** Full procedure under "Run this FIRST" below,
which is where the revoke and restore commands live. Revoke ingress, restart
the api unit, capture `e2e-cache-degraded.png` with the pip amber, restore, and
confirm green before continuing. The remaining captures assume a healthy cache.

**1:02  Four terminal captures. 15m.** Distributed application, 10 pts, and
nothing committed substitutes for any of them.

- `dist-systemctl.png` all four units active
- `dist-collector-journal.png` readings published
- `dist-correlator-journal.png` clusters published, **and the cache hit rate**
- `dist-dispatcher-journal.png` an advisory **with its audit key**

**1:17  Three console captures. 20m.** End-to-end, 30 pts, no substitute.

- `e2e-console-active-zone.png` a zone drawn, rail filled, advisory queued
- `e2e-console-depth-change.png` the same zone escalating, the advisory ladder
- `e2e-api-health.png` `/api/health`, including the degraded verdict

```bash
# ---- 1:37  Every textual artifact, then the two eyeball proofs.     10m
./scripts/capture-evidence.sh
cat docs/evidence/cli/MANIFEST.md      # has never existed before this run

git add docs/evidence && git commit -m "Evidence from the capture session"

# ---- 1:47  Teardown, then verify by console.                        10m
.venv/bin/python infra/teardown.py --confirm; echo "exit=$?"
aws rds describe-db-instances --region us-east-1
aws elasticache describe-cache-clusters --region us-east-1
```

**The two things to verify by eye before teardown,** because they are the only
proof that the two severe fixes work in production rather than only in unit
tests:

- `advisories-per-zone.txt` shows a zone with **more than one** advisory and a
  rising ladder, `monitor -> advisory -> warning`. One row per zone means either
  E-021 is still suppressing escalation, or you are running the wrong fixture.
  Check `CURBLINE_REPLAY_FILE` before concluding the fix is broken.
- `zone-states.txt` shows a zone that is not `forming` or `active`. All zones
  open forever means E-020's sweep thread is not running. It is not silent:
  `poll_loop` logs `poll iteration failed, continuing`, so grep the dispatcher
  journal. What stays silent is the health surface, which keeps reporting live.

**Because the rehearsal was dropped, teardown gets a second check the next
morning.** Verify by console immediately, then look at the billing dashboard
before you do anything else the following day, then delete by hand if anything
survived. See E-035.

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

**Abort condition, superseded.** This said "if EC2 cannot reach RDS by end of
day one, switch to DynamoDB". That priced a rewrite of the data layer, the
fixture and a report section as though it were a swap, and it fired far too
early. D-001 was repriced and `VERSION_ROADMAP.md` replaced it with a five-rung
connectivity ladder: check the security group self-attach (E-005), then subnet
routing, then put RDS and ElastiCache in the default VPC group alongside their
own, then re-provision RDS from scratch. Only if a second RDS in the default
group still refuses an EC2 host in that same group is this a real account
problem, and only there does DynamoDB get considered. Use the ladder, not this
paragraph.

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

Force a zone without waiting for weather, by lowering the threshold. Restart all
four units, not two:

```bash
sudo systemctl set-environment CURBLINE_DEPTH_THRESHOLD_CM=0.5
sudo systemctl restart curbline-collector curbline-correlator \
                       curbline-dispatcher curbline-api
```

**Why all four.** Config is read once at import, per process, so a restarted
subset leaves the others holding the old value. This used to be written as
correlator and dispatcher only, which skews two things at once: the console
keeps drawing its gauge and empty-state copy from the API's stale thresholds
(E-014's shape), and the dispatcher's audit record would attest parameters the
correlator was not using (E-027). The second is now carried in the message and
cannot skew, but the console still can, and a screenshot of a dashboard
describing a threshold the pipeline is not applying is worse than no screenshot.

Put it back before capturing evidence, and restart all four again. A threshold
of 0.5 cm produces zones from noise, which is fine for proving the wiring and
dishonest for a screenshot.

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

#### Run this FIRST, before any capture that depends on the pipeline

Every other item in this checklist records state that already exists. This one
can still find a defect, so it runs while there is time to act on the result.
It is fully reversible, which is a convenience and not the reason for the
ordering.

The three AWS console tabs at 0:37 are the one thing that may precede it. They
read state that provisioning already settled, so nothing this test finds can
invalidate them, and they fill a window otherwise spent waiting for the
correlator to flush.

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
cache, and an amber pip left in place will contradict the cache hit rate in
`dist-correlator-journal.png`, which is now the capture that carries it.

**The hit rate needs the correlator to have processed readings since the flush,
not merely to be running.** Counters are published to Redis from the worker loop
between batches (E-019), so a status bar captured seconds after a restart shows
`n/a` correctly rather than a number. Let the pipeline run a minute before
capturing.

---

Filenames are fixed here and in Appendix C of the report. Save to
`docs/evidence/screenshots/` under exactly these names so the report's table
resolves without renaming anything afterwards.

**Eleven captures, not twenty-two.** The set was cut to fit a 2-hour window,
chosen by rubric points that have no committed substitute. Skipped rows are
marked `[~]` with the reason and the substitute where one exists. They are kept
rather than deleted, here and in Appendix C, because a deleted row is one
nobody can audit and one the report cannot honestly account for.

**Cloud integration (10 pts)**, 3 of 6
- [ ] `cloud-rds.png` RDS console: instance `curbline-db`, status Available, engine PostgreSQL
- [ ] `cloud-elasticache.png` ElastiCache console: cluster `curbline-cache`, status Available
- [ ] `cloud-sqs.png` SQS console: all four queues, showing messages available
- [~] `cloud-sns.png` SKIPPED. The confirmed subscription is established by
      `preflight.py` passing at 0:30 and by the email arriving. The console tab
      adds nothing the run does not already prove.
- [~] `cloud-s3.png` SKIPPED. Substitute: `docs/evidence/cli/s3-advisories.txt`
      already lists the objects under `advisories/`.
- [~] `cloud-ec2.png` SKIPPED. Partial substitute only:
      `docs/evidence/cli/ec2-instance.txt` records the instance id, type, launch
      time and state, but **not** the IAM role. See its Appendix C row.

**Distributed application (10 pts)**, 4 of 5, no substitute exists for any kept row
- [ ] `dist-systemctl.png` `systemctl status 'curbline-*'` with all four units active
- [ ] `dist-collector-journal.png` `journalctl -u curbline-collector` showing readings published
- [ ] `dist-correlator-journal.png` `journalctl -u curbline-correlator` showing clusters
      published **and the cache hit rate**. This shot now carries the hit rate,
      which is why `e2e-console-status-bar.png` is skipped.
- [ ] `dist-dispatcher-journal.png` `journalctl -u curbline-dispatcher` showing an advisory **with its audit key**
- [~] `dist-queue-depth.png` SKIPPED. Partial substitute only: both committed
      json captures read `ApproximateNumberOfMessages: 0`, taken while the
      pipeline was idle. They prove the queues exist, not that traffic flowed.
      Decoupling is carried by the three journal captures instead.

**Technology components (15 pts)**, 0 of 5
- [~] `tech-postgis-version.png` SKIPPED. No committed substitute for the version
      string. `preflight.py` asserts the extension resolves, which is the load-bearing
      half.
- [~] `tech-current-clusters.png` SKIPPED. Substitute:
      `docs/evidence/cli/current-clusters-query.sql`, the committed query, run
      against live PostGIS on 2026-08-28.
- [~] `tech-redis-keyspace.png` SKIPPED. No committed substitute. Cache liveness
      is carried by the hit rate in `dist-correlator-journal.png`.
- [~] `tech-sns-email.png` SKIPPED. No committed substitute. The subscription is
      still confirmed and the email still received at 0:27; only the screenshot
      is dropped. Say so in the report rather than implying the email never came.
- [~] `tech-s3-audit-object.png` SKIPPED. Substitute: the six records committed
      under `docs/evidence/audit/`. Per `docs/evidence/README.md` those files
      **are** the artifact; a screenshot would only prove a JSON file was displayed.

**End-to-end (30 pts)**, 4 of 6, no substitute exists for any kept row
- [ ] `e2e-console-active-zone.png` Console with an active zone drawn, rail filled, advisory queued
- [ ] `e2e-console-depth-change.png` The same zone escalating: **the advisory
      ladder**, `monitor -> advisory -> warning`. This is the E-021 proof in the UI.
- [ ] `e2e-api-health.png` `/api/health`, including the `degraded` verdict
- [ ] `e2e-cache-degraded.png` Cache unreachable: console still serving, pip amber,
      `/api/health` degraded. Captured at 0:47, not last.
- [~] `e2e-console-baseline.png` SKIPPED. The zero-zone state is the least
      informative console capture and `e2e-console-active-zone.png` shows the
      same chrome with data in it.
- [~] `e2e-console-status-bar.png` SKIPPED. The cache hit rate it existed to
      show moves to `dist-correlator-journal.png`. Shooting it early reads `n/a`
      legitimately (E-019) and looks like a failed criterion.
- [ ] **Expected unmet:** a zone with `NWS confirmed` next to one without. This needs
      an active NWS flood alert intersecting a zone footprint during the capture
      window. There were zero in the last run. Capture it if one exists, record the
      reason in the Appendix C row if not. Do not fabricate a polygon to force it.

### Report

Fill in `docs/REPORT.md`. It is 15 points and takes longer than you think.

### Push and tear down

### Pre-teardown checklist

**Teardown is irreversible, and re-provisioning costs about twenty minutes plus
RDS creation time.** Anything missing after this point is unrecoverable inside
the deadline. Walk this against files on disk, not against a memory of having
taken them.

**Everything textual is one command. Run it on the EC2 host:**

```bash
./scripts/capture-evidence.sh
```

It writes twenty-odd artifacts to `docs/evidence/cli/` and a `MANIFEST.md`
listing what it got and what it could not get, each failure with its reason. It
never aborts on a failed capture, because a partial evidence set beats one that
stopped silently at item four. Every `aws` call is pinned to `us-east-1` per
E-018, and the clustering query passes its parameters explicitly rather than
inheriting the function's FloodNet defaults per E-025.

- [ ] `./scripts/capture-evidence.sh` run, exit 0
- [ ] `docs/evidence/cli/MANIFEST.md` read, and **every** line under MISSING
      either resolved by re-running or written into an Appendix C row with its
      reason. Do not tear down with an unexplained entry.

**Two things the script proves that nothing else does.** Check these by eye in
the output, because they are the evidence that two severe defects are actually
dead rather than merely fixed in a unit test:

- [ ] `advisories-per-zone.txt` shows at least one zone with **more than one**
      advisory, and a ladder like `monitor -> advisory -> warning`. One row per
      zone means E-021 is still live and escalation is being suppressed.
- [ ] `zone-states.txt` shows at least one zone not in `forming`/`active`. If
      every zone is open forever, E-020's sweep is not running.

**Still manual, because they need a browser or a mailbox:**

- [ ] Three AWS console screenshots: RDS, ElastiCache, SQS. SNS, S3 and EC2
      are dropped from the reduced set; see Appendix C for the reason on each
- [ ] Cache-unreachable pair: degraded with amber pip, and healthy again after
      re-authorising
- [ ] Duplicate-delivery replay: same `ingest_id`, one row before and after
- [ ] Console: active zone, and the same zone escalating through the ladder.
      The zero-zone baseline is dropped from the reduced set
- [ ] SNS email **received** and the subscription confirmed. The screenshot of
      it is dropped, the email is not: an unconfirmed subscription silently
      drops every publication
- [~] One S3 audit object opened: dropped. The six records under
      `docs/evidence/audit/` are the artifact itself
- [~] Console status bar with a non-zero cache hit rate: dropped. The hit rate
      is carried by `dist-correlator-journal.png` instead

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
.venv/bin/python infra/teardown.py --confirm
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
