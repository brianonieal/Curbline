# Run book and evidence checklist

**Blocks 1 through 3 below are history.** They describe the original build and
are kept because they record why the order was chosen. If you are picking this
up to finish the submission, use the capture session immediately below and
ignore them unless something breaks.

---

## THE CAPTURE SESSION

One sitting. The stack bills hourly from the first command to teardown, so the
sequence is ordered to make that window short and to put anything with a human
in the loop at the front.

**Before you start, have ready:** your own public IP (from *your* machine, not
the instance, see E-008), and access to the mailbox for the SNS subscription.

```bash
# 1. Provision. ~20 min, mostly waiting on RDS.
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=<your-ip>/32 ./infra/bootstrap.sh
```

**2. Confirm the SNS subscription NOW, before anything publishes.** AWS emails a
confirmation link the moment the subscription is created. An unconfirmed
subscription silently drops every publication, so advisories issued before you
click it are gone and cannot be replayed into your inbox. This is the item that
has slipped past two gates; it slips because it feels like it can wait, and it
cannot.

```bash
# 3. Prove connectivity before doing anything else.
set -a; source .env; set +a
PGPASSWORD="$CURBLINE_DB_PASSWORD" psql -P pager=off -h "$CURBLINE_DB_HOST" \
  -U "$CURBLINE_DB_USER" -d "$CURBLINE_DB_NAME" -c "SELECT PostGIS_Full_Version();"
redis-cli -h "$CURBLINE_CACHE_HOST" ping

# 4. Cache degradation test, FIRST, not last. Full procedure below under
#    "Run this FIRST". Capture the amber pair, restore, confirm green.

# 5. Let the pipeline run long enough to escalate. The replay storm has to
#    climb through monitor -> advisory -> warning for the E-021 evidence to
#    exist at all. Watch until a second advisory appears for one zone:
watch -n10 'PGPASSWORD=$CURBLINE_DB_PASSWORD psql -tA -h $CURBLINE_DB_HOST \
  -U $CURBLINE_DB_USER -d $CURBLINE_DB_NAME \
  -c "SELECT zone_id, count(*) FROM advisories GROUP BY zone_id"'

# 6. One command for every textual artifact.
./scripts/capture-evidence.sh
cat docs/evidence/cli/MANIFEST.md
```

**7. Screenshots.** Six AWS console shots plus the console UI set. Filenames are
fixed in the checklist below and match Appendix C exactly, so they drop in
without renaming.

**8. Read the manifest before tearing anything down.** Every MISSING line either
gets resolved by re-running or written into its Appendix C row with the reason.
Teardown is irreversible and re-provisioning is another twenty minutes.

```bash
# 9. Commit while the stack still exists, in case something needs re-capturing.
git add docs/evidence && git commit -m "Evidence from the capture session"

# 10. Tear down. Not optional; RDS and ElastiCache bill hourly.
python3 infra/teardown.py --confirm

# 11. Confirm nothing billable survived. An empty result from a regional API
#     means check the region first, not that the resource is gone. See E-018.
aws rds describe-db-instances --region us-east-1
aws elasticache describe-cache-clusters --region us-east-1
```

**The two things to verify by eye before teardown,** because they are the only
proof that this session's two severe fixes work in production rather than only
in unit tests:

- `advisories-per-zone.txt` shows a zone with **more than one** advisory and a
  rising ladder. One row per zone means E-021 is still suppressing escalation.
- `zone-states.txt` shows a zone that is not `forming` or `active`. All zones
  open forever means E-020's sweep thread is not running.

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
cache, and a status bar still showing amber will contradict the status bar
screenshot later in the set.

**The hit rate needs the correlator to have processed readings since the flush,
not merely to be running.** Counters are published to Redis from the worker loop
between batches (E-019), so a status bar captured seconds after a restart shows
`n/a` correctly rather than a number. Let the pipeline run a minute before
capturing.

---

Filenames are fixed here and in Appendix C of the report. Save to
`docs/evidence/screenshots/` under exactly these names so the report's table
resolves without renaming anything afterwards.

**Cloud integration (10 pts)**
- [ ] `cloud-rds.png` RDS console: instance `curbline-db`, status Available, engine PostgreSQL
- [ ] `cloud-elasticache.png` ElastiCache console: cluster `curbline-cache`, status Available
- [ ] `cloud-sqs.png` SQS console: all four queues, showing messages available
- [ ] `cloud-sns.png` SNS console: topic with a confirmed subscription
- [ ] `cloud-s3.png` S3 console: audit bucket with objects under `advisories/`
- [ ] `cloud-ec2.png` EC2 console: the instance, with its IAM role attached

**Distributed application (10 pts)**
- [ ] `dist-systemctl.png` `systemctl status 'curbline-*'` with all four units active
- [ ] `dist-collector-journal.png` `journalctl -u curbline-collector` showing readings published
- [ ] `dist-correlator-journal.png` `journalctl -u curbline-correlator` showing clusters published
- [ ] `dist-dispatcher-journal.png` `journalctl -u curbline-dispatcher` showing an advisory with its audit key
- [ ] `dist-queue-depth.png` SQS queue depth non-zero, the visible proof the stages are decoupled

**Technology components (15 pts)**
- [ ] `tech-postgis-version.png` `SELECT PostGIS_Full_Version();` output
- [ ] `tech-current-clusters.png` `SELECT * FROM current_clusters();` returning real zones
- [ ] `tech-redis-keyspace.png` `redis-cli INFO keyspace` showing cached sensor keys
- [ ] `tech-sns-email.png` The received SNS email
- [ ] `tech-s3-audit-object.png` One S3 audit object opened, showing the thresholds recorded alongside the decision

**End-to-end (30 pts)**
- [ ] `e2e-console-baseline.png` Console with zero zones (baseline)
- [ ] `e2e-console-active-zone.png` Console with an active zone drawn, rail filled, advisory queued
- [ ] `e2e-console-depth-change.png` Same zone before and after, showing depth change on the rail
- [ ] `e2e-console-status-bar.png` Status bar showing queue depth, a non-zero cache
      hit rate, and PostGIS up. If it reads `n/a`, the correlator has not flushed
      counters yet; wait a poll interval rather than capturing the empty state.
- [ ] `e2e-api-health.png` `curl /api/health` output
- [ ] **Expected unmet:** a zone with `NWS confirmed` next to one without. This needs
      an active NWS flood alert intersecting a zone footprint during the capture
      window. There were zero in the last run. Capture it if one exists, record the
      reason in the Appendix C row if not. Do not fabricate a polygon to force it.

**Deliberate failure, which is worth more than it costs**
- [ ] `e2e-cache-degraded.png` Cache unreachable: console still serving, pip amber,
      `/api/health` degraded. Procedure is "Run this FIRST" above; do not leave it
      until last.

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

- [ ] Six AWS console screenshots: RDS, ElastiCache, SQS, SNS, S3, EC2
- [ ] Cache-unreachable pair: degraded with amber pip, and healthy again after
      re-authorising
- [ ] One S3 audit object opened and readable
- [ ] Duplicate-delivery replay: same `ingest_id`, one row before and after
- [ ] Console: zero zones, active zone, before and after depth change
- [ ] Console: status bar showing a non-zero cache hit rate
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
