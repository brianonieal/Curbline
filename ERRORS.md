# ERRORS

Pre-seeded with problems already found and fixed, so they are not rediscovered.
Anything that costs more than 30 minutes gets an entry.

Format: symptom first, because that is what you will be searching for.

---

## E-001 — Every gauge reads WARNING on a dry day
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** FIXED | **Cost:** would have been a ruined demo

**Symptom:** the map shows the entire region in maximum alarm with no rain
anywhere. Depth values in the hundreds of centimetres. One sensor negative.

**Root cause:** USGS gage height is water surface elevation above an arbitrary
local datum, not flood depth. `sources.py` multiplied it by 30.48 and called
the result depth. Live sampling of 12 NYC-area gauges returned -0.26 to
19.58 ft under normal conditions; 10 of 12 converted past the 20 cm warning
threshold.

**Fix:** `USGSSource` now reports rise above each site's own 10th-percentile
recent history, fetched from the `continuous` collection over a 14-day window.
Verified: max depth dropped from 597 cm to 192 cm, and a gauge reading 19.58 ft
correctly resolved a 13.27 ft baseline.

**Prevention:** never convert a sensor unit without confirming what physical
quantity it measures. Print the raw distribution before trusting a conversion.

---

## E-002 — Zone hulls invisible on the map
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** FIXED | **Cost:** ~1 hr

**Symptom:** clustering returns rows but nothing renders, or the insert fails
with a geometry type violation.

**Root cause:** `ST_ConvexHull` of two points returns a LINESTRING and of one
point returns a POINT. Neither satisfies `geometry(Polygon, 4326)`, and neither
draws as a filled area.

**Fix:** `current_clusters()` buffers the hull by 492 ft in EPSG:2263 before
transforming back. Verified against the fixture: a two-sensor Red Hook cluster
returns `ST_Polygon` at 0.125 km².

**Prevention:** any geometry derived from a variable-size point set needs the
degenerate cases tested explicitly. `tests/fixture_clusters.sql` covers 1, 2,
and 4 point clusters.

---

## E-003 — Clustering distances wrong at different latitudes
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** FIXED by design | **Cost:** avoided

**Symptom:** would have manifested as clusters forming at inconsistent physical
distances across the city.

**Root cause:** `ST_ClusterDBSCAN` in raw EPSG:4326 measures epsilon in degrees.
A degree of longitude is not a fixed distance.

**Fix:** transform to EPSG:2263 (NAD83 New York Long Island, US survey feet)
before clustering. Epsilon is then 1640 ft, a real distance.

**Prevention:** any distance operation in PostGIS either uses `geography` or
projects to a local CRS first. Never `geometry` in 4326 for distance.

---

## E-004 — Dashboard unreachable after everything else works
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** FIXED | **Cost:** would have been ~1 hr at the worst time

**Symptom:** all four services running, pipeline healthy, browser times out on
port 8000.

**Root cause:** `provision.py` opened 5432 and 6379 between security groups but
never opened 8000 or 22 to the operator.

**Fix:** `allow_from_cidr` opens 22 and 8000 to the caller's public IP as a /32,
resolved from `checkip.amazonaws.com`. Never 0.0.0.0/0.

**Prevention:** if a service is meant to be reached by a human, an explicit
ingress rule for that human is part of provisioning, not an afterthought.

---

## E-005 — RDS refuses connections from its own provisioning host
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** FIXED | **Cost:** would have been a confusing hour

**Symptom:** `psql` from EC2 hangs, then times out, despite the RDS security
group explicitly allowing 5432 from the app group.

**Root cause:** an ordering deadlock. `provision.py` creates the `curbline-app`
group, but it runs on an instance launched before that group existed, so the
instance is not a member of it. The rule allows a group the host is not in.

**Fix:** `attach_sg_to_self()` reads the instance id via IMDSv2 and calls
`modify_instance_attribute` to add the group to the running instance.

**Prevention:** when a security group rule references a group by id, verify the
source host is actually a member of it. The rule looking correct in the console
is not the same as it applying.

---

## E-006 — NWS alerts correlate against nothing
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** KNOWN LIMITATION, not a bug | **Cost:** n/a

**Symptom:** alerts arrive and persist, but `alert_for_hull()` returns NULL for
zones that visibly sit inside a warned area.

**Root cause:** many NWS products are zone-based rather than storm-based and
arrive with `geometry: null`, referencing UGC zones in `affectedZones` instead.
A NULL polygon cannot intersect anything.

**Status:** handled, not fixed. Stored with NULL geometry, excluded from
spatial correlation, counted and logged by the collector. Resolution scheduled
for v1.4.0.

**Do not:** fabricate a bounding polygon for these. A wrong polygon is worse
than a missing one.

---

## E-007 — `round()` fails on a PostGIS area expression
**Found:** 2026-08-27, pre-v0.5.0 | **Status:** FIXED | **Cost:** 2 min

**Symptom:** `function round(double precision, integer) does not exist`

**Root cause:** `ST_Area` returns double precision; two-argument `round` in
Postgres takes numeric.

**Fix:** cast first: `round(ST_Area(...)::numeric, 3)`.

---

## Template

```
## E-008 — Console still unreachable after E-004 was "fixed"
**Found:** 2026-08-28, pre-v0.5.0 | **Status:** FIXED | **Cost:** would have been the whole v0.5.0 exit criteria

**Symptom:** `provision.py` reports `opened tcp/8000 on sg-... to x.x.x.x/32`,
the rule is visibly present in the console, and the browser still times out.

**Root cause:** `my_public_cidr()` resolves the public address of whichever host
executes it, via `checkip.amazonaws.com`. `bootstrap.sh` runs `provision.py`
**on the EC2 instance**, so the address it resolves is the instance's own. The
rule opens tcp/22 and tcp/8000 from the instance to itself. E-004 opened the
ports correctly and pointed them at the wrong address, which is why the log line
looks right.

**Fix:** `bootstrap.sh` now requires `CURBLINE_ADMIN_CIDR` and forwards it as
`--admin-cidr`. `provision.py` warns when it is on an EC2 instance with no
`--admin-cidr` given. Every documented invocation now passes a literal address.

**Prevention:** an address-detection helper answers "who am I," never "who is the
operator." The two are the same only when the code runs on the operator's
machine. `provision.py` is designed to run on the instance, so they never are.
Note that this makes the v0.5.0 exit criterion "reachable from Brian's browser"
untestable from the instance: verify it from the browser, not with curl on EC2.

---

## E-009 — `bootstrap.sh` dies on apt before provisioning anything
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** one failed bootstrap run, nothing billable created

**Symptom:** `./infra/bootstrap.sh` on a fresh Ubuntu 24.04 instance stops at
`E: Package 'awscli' has no installation candidate`. Nothing after the apt line
runs.

**Root cause:** Ubuntu 24.04 (noble) dropped `awscli` from its archive. The v1
CLI it used to ship was stale, and AWS distributes v2 itself rather than through
distribution packages. The apt line was written against an earlier Ubuntu where
the package still existed.

**Fix:** `bootstrap.sh` drops `awscli` from the apt list and installs CLI v2
from `awscliv2.zip`, guarded by `command -v aws` so a re-run is cheap. The
archive name is built from `uname -m`, which matches AWS naming for x86_64 and
aarch64 both.

**Prevention:** `set -euo pipefail` did its job. The script stopped before
`provision.py` ran, so no RDS or ElastiCache instance existed and there was no
half-built stack to tear down. A bootstrap that fails for free is designed
behavior, not luck. Also worth recording: nothing in the pipeline needs the CLI.
provision.py, teardown.py and the workers are boto3 throughout, and the CLI is
present only for DEMO.md and COSTS.md evidence capture.

---

## E-010 — Policy grants an action name that does not exist
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** one provision run, stopped after the bucket was created

**Symptom:** `provision.py` logs `created bucket curbline-audit-...`, then dies
with `AccessDenied` on `PutPublicAccessBlock`, reporting that no identity-based
policy allows `s3:PutBucketPublicAccessBlock`.

**Root cause:** the IAM action is `s3:PutBucketPublicAccessBlock`. The boto3
method and the wire operation are both `PutPublicAccessBlock`, without `Bucket`.
`iam-policy.json` granted the operation name, which is not an action. IAM does
not validate action names in a policy document, so `put-role-policy` succeeded
and the grant authorized nothing.

**Fix:** `iam-policy.json` grants `s3:PutBucketPublicAccessBlock`. Re-applied
with `aws iam put-role-policy`, verified by re-running `provision.py` past the
bucket step.

**Prevention:** a policy that applied cleanly proves nothing about whether its
actions exist. Read the action name from the service authorization reference,
not from the SDK method. Every other call in `provision.py` and `teardown.py`
was audited against this policy at the same time and the rest are correct; no
tags are set anywhere, so no tagging actions are needed.

---

## E-011 — RDS reports missing credentials when the account is the problem
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** one provision run, stopped before RDS was created

**Symptom:** `provision.py` clears every network, queue, topic and bucket step,
then dies on `CreateDBSubnetGroup` with `InvalidParameterValue: Missing
necessary credentials`, linking to the RDS service-linked roles page.

**Root cause:** RDS needs the account-level `AWSServiceRoleForRDS` to exist
before its first create call, and an account that has never provisioned RDS does
not have one. The instance role's credentials were fine. The message names
credentials because RDS cannot assume a role that was never created, and the
account, not the caller, is what is missing something. ElastiCache has the same
requirement and fails the same way a minute later.

**Fix:** `account-setup.sh` now creates both service-linked roles, for
`rds.amazonaws.com` and `elasticache.amazonaws.com`, before the instance role
work. Creating them requires `iam:CreateServiceLinkedRole`, so this belongs to
the console identity in CloudShell and deliberately not to the instance policy,
which grants no `iam:` actions at all.

**Prevention:** read an AWS error for who is missing what before assuming it is
the caller. "Missing necessary credentials" on a first-ever call to a service
usually means an account-level prerequisite, not a bad policy. A first run in a
fresh account exercises setup paths that never appear again, which is the
argument for provisioning in a clean account at least once before the demo.

---

## E-012 — bootstrap.sh stops silently after the schema loads
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** minutes, and it would have been unbounded in an unattended run

**Symptom:** the schema load prints the PostGIS version row and the script
appears to finish, except the systemd units never install and the prompt never
returns. The terminal shows `(END)`.

**Root cause:** `psql` pages output through `less` when stdout is a tty, and the
`postgis_full_version` row is wide enough to trigger it. The script was not
hung, it was blocked on a keypress. Run from `bootstrap.sh` this is easy to read
as completion, because the line it prints is the line you were told to look for.

**Fix:** `psql -P pager=off` in `bootstrap.sh`. The row still prints, it just
does not page.

**Prevention:** any interactive-by-default tool inside a provisioning script
needs its interactivity turned off explicitly, not by luck of the terminal.
`psql` is the obvious one here. The general test is whether the script would
complete with no human watching it, which is also the condition under which a
block like this is invisible rather than merely annoying.

---

## E-013 — Correlator crashes on every message, dashboard never ticks
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** the pipeline produced nothing while every other component looked healthy

**Symptom:** all four services report `active`, the dashboard serves, and it sits
on "Waiting for the first pipeline tick." `journalctl -u curbline-correlator`
shows `psycopg.errors.UndefinedFunction: function current_clusters(double
precision, smallint, double precision, smallint) does not exist`, with a hint
about explicit type casts.

**Root cause:** the function is declared `current_clusters(NUMERIC, INT,
NUMERIC, INT)`. `config.DEPTH_THRESHOLD_CM` and `config.CLUSTER_EPS_FT` are
Python floats, which psycopg sends as `double precision`. PostgreSQL casts
`smallint` to `integer` implicitly, so the two INT parameters resolved fine, but
`double precision` to `numeric` is an assignment cast, not an implicit one, and
function resolution does not apply assignment casts. The function existed the
whole time. Only the call was unresolvable, which is why the error says "does
not exist" rather than naming a type problem.

**Fix:** `db.py` calls `current_clusters(%s::numeric, %s::int, %s::numeric,
%s::int)`. Cast at the call site rather than widening the signature, because
NUMERIC is the right declaration for a depth in centimetres and a distance in
feet.

**Prevention:** "function does not exist" from PostgreSQL means no candidate
matched the argument types, not that the name is absent. Read the parenthesised
types in the message before assuming the schema failed to load. This class of
bug is invisible to the current test suite, which uses moto throughout and never
executes SQL against a real PostGIS instance. That is a real coverage gap, not
an oversight to hide: the first time this query ran against Postgres was in
production on the graded instance.

---

## E-014 — Shipped defaults contradicted a decision that was already logged
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** one miscalibrated evidence screenshot

**Symptom:** the first live dashboard reported 17 of 28 sensors wet, one open
zone, and a deepest reading of 151.8 cm.

**Root cause:** `config.SOURCE` defaulted to `usgs` while `DEPTH_THRESHOLD_CM`
defaulted to 5.0, and `workers/dispatcher.py` hardcoded the advisory and warning
tiers at 10 and 20. Those are FloodNet street-depth numbers being applied to
USGS stage rise, which D-005 explicitly decided against. The comment block above
the defaults described the problem accurately and then did nothing about it. The
decision was logged and never implemented.

**Fix:** `thresholds_for(source)` in `config.py` maps floodnet and replay to
5/10/20 and usgs to 60/90/120, with an unknown source falling back to FloodNet
because over-reporting is the safe direction. `decide_level` reads config rather
than literals. `TestSourceCalibration` asserts the mapping, and the ladder tests
now pin `CURBLINE_SOURCE=floodnet` instead of passing by coincidence.

**Prevention:** a decision in DECISIONS.md is a claim about the code, and
nothing was checking it. The tell here was a comment that explained the correct
behaviour in the imperative ("Raise these substantially when running on USGS")
next to a default that did not do it. Prose instructing a future reader to do
something the code could do itself is a defect, not documentation.

---

## E-015 — Services survive a reboot holding a database that no longer exists
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** one confusing pool timeout on an otherwise clean bootstrap

**Symptom:** `bootstrap.sh` completes, `provision.py` reports every service
created, and `curbline-api` is `active (running)` while logging
`psycopg_pool.PoolTimeout: couldn't get a connection`. Nothing looks broken; the
unit has been up longer than the database has existed.

**Root cause:** the four units are `enabled`, so systemd starts them at boot
against whatever `.env` is on disk. After a stop/start cycle following a
teardown, that file names an RDS endpoint that was deleted. `bootstrap.sh` then
finished with `systemctl enable --now`, and `--now` starts a unit that is not
running but is a no-op on one that is. `provision.py` rewrote `.env` correctly;
no process re-read it.

**Fix:** `bootstrap.sh` now runs `systemctl enable` and `systemctl restart` as
separate commands. Restart is unconditional, so a re-run always reloads
configuration.

**Prevention:** `--now` is not "apply this". Any provisioning step that rewrites
configuration has to restart the readers explicitly, because a long-running
process holds the values it read at startup and no amount of correctness in the
file reaches it. The tell is a unit whose uptime predates the resource it
depends on.

---

## E-016 — Cache used as proof a database row exists
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** a stalled pipeline that looked like a replay-source problem

**Symptom:** the collector logs `published 5 readings from source=replay` every
minute, the queue shows messages in flight, and `sensors`, `readings` and
`zones` all hold zero rows. The correlator logs
`ForeignKeyViolation: readings_sensor_id_fkey`, `Key (sensor_id)=(demo:q4) is
not present in table "sensors"`.

**Root cause:** `handle_reading` upserted the sensor only when the Redis lookup
missed. Clearing the `sensors` table without clearing Redis left the cache
reporting sensors that no longer existed, so the upsert was skipped and every
insert violated the foreign key. Because SQS redelivers, the same messages
failed continuously rather than once.

The cache is a copy of a row. It was being read as evidence the row exists.
Those diverge for ordinary reasons: a restored database, a manual delete, a
cache outliving the table it describes.

**Fix:** `handle_reading` catches `ForeignKeyViolation`, invalidates the cache
entry, rewrites the sensor and retries the insert once. The cache still saves a
round trip on the common path; it no longer decides whether a write can succeed.

**Prevention:** a cache answers "what did this row look like," never "does this
row exist." Any write guarded by a cache hit needs a path that survives the
guard being wrong, because at-least-once delivery turns a single wrong answer
into an unbounded retry loop rather than one failure.

**Note:** the operator error that exposed this was mine. Clearing `sensors` to
separate replay data from USGS data was correct in intent; doing it without
flushing Redis was not. The bug it uncovered predates that command.

---

## E-017 — No advisory has ever fired, in any run of this system
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** the entire notification path was dead and looked healthy

**Symptom:** zones form, appear on the map and persist in the database, and the
dispatcher logs `zone <id> forming, no advisory yet` on every cycle forever. The
same zone_id recurs minutes apart and is still reported as forming. The advisory
queue stays empty, nothing is written to S3 under `advisories/`, and SNS
publishes nothing.

**Root cause:** `handle` built its previous-state lookup as
`{z["zone_id"]: z for z in db.open_zones()}`. `zones.zone_id` is a `UUID`
column, so psycopg returns `uuid.UUID` objects as the keys, while `zone_id` from
the SQS body is a string parsed from JSON. `UUID(x) == str(x)` is False, so the
lookup never matched, `previous_state` was always `None`, `next_state` always
returned `forming`, and the guard `if state == "forming": return` took the early
exit every time.

Nothing was closing zones and nothing was wrong with the lifecycle logic. The
dictionary simply never found anything.

**Fix:** the three queries that select `zone_id` from `zones` now cast it with
`zone_id::text`, and the dispatcher keys and looks up with `str(...)` so a
future query that forgets the cast cannot resurrect this.

**Prevention:** the lifecycle was unit tested through `next_state`, which is a
pure function over the state string and passed. The defect lived in how the
argument was obtained, one line above the call. A pure function tested in
isolation says nothing about whether its inputs are ever computed correctly, and
this is the second time in two days that a correct decision was defeated by the
plumbing feeding it (see E-014).

Worth stating plainly in the report: the S3 audit write and the SNS publish, two
of the three graded components' outputs, had never executed against a real
account before this fix. Every prior demonstration showed zone detection only.

---

## E-018 - An empty regional API result read as proof of deletion
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED | **Cost:** a false all-clear on a live stack, and roughly four hours of unnoticed RDS and ElastiCache billing

**Symptom:** a status probe reported the entire stack gone and teardown complete.
EC2, RDS, ElastiCache, SQS, SNS and the security groups all returned empty. Only
the S3 bucket and the IAM role appeared to survive, which was read as teardown
having errored on the bucket.

**Root cause:** the workstation AWS CLI had no region set in the environment and
`us-east-2` in its config file. Every regional call went to Ohio, where nothing
had ever been built. S3 `ListBuckets` and IAM are not regional, which is exactly
why those two answered correctly and made the result look coherent rather than
broken. The us-east-1 stack was running the whole time.

**Fix:** `aws configure set region us-east-1` on the workstation. CLAUDE.md now
carries the rule in its traps section.

**Prevention:** an empty result from a regional API is not evidence of absence.
It is evidence of absence **in the region you asked**. Before concluding that
anything is gone, print the region and confirm it. The tell here was available
and missed: a probe claiming everything was deleted, while two non-regional
services reported healthy resources that teardown should also have removed.

Second-order lesson: verify a teardown by looking at the console in a known
region, or at a screenshot, not by a CLI query whose region you have not
checked. This is the second false read on stack state in one day.

---

## E-019 - Dashboard cache hit rate is structurally always zero
**Found:** 2026-08-28, v0.5.0 | **Status:** FIXED 2026-08-28 | **Cost:** one v0.7.0 exit criterion, unblocked by the fix

**Symptom:** `/api/state` reports `"cache": {"hits": 0, "misses": 0,
"hit_rate": null, "reachable": true}` on a system that has processed over a
thousand readings through a working cache.

**Root cause:** `cache.STATS` is a module-level dictionary, so it counts only
what the process holding it has done. `cache.sensor()` is called exclusively by
`workers/correlator.py`. `api/server.py` reads `cache.STATS` and serves it to
the dashboard without ever calling the cache itself, so it is reporting its own
counters, which are always empty. The correlator has the real numbers and no way
to publish them.

**Fix:** the counters are published through Redis under `stats:cache:*`.
`cache.STATS` keeps its role as an unflushed per-process delta and is
incremented on the hot path as before. `cache.flush_stats()` drains those
deltas with `INCRBY` and is called from the `consume()` loop between batches,
so no network roundtrip is added to a cache read to measure that read.
`cache.read_stats()` returns the aggregate, and `api/server.py` now calls it
instead of reading the local dict.

Three details carry the design:

- **Flush between batches, not inside `_get`.** Paying a roundtrip to measure a
  roundtrip is self-defeating, and the report makes a latency claim about this
  cache. The dashboard trails activity by up to one long-poll interval, which
  for a status bar is not a cost.
- **A failed flush retains the deltas.** The flush target is the cache itself,
  so a failure usually means the cache is down, which is exactly when the error
  count is worth keeping. Asserted by
  `test_failed_flush_retains_the_deltas`.
- **`hit_rate` is `None`, never `0.0`, when nothing has been recorded or Redis
  is unreachable.** A status bar showing 0% for an unknown is the same quiet
  wrongness as the original defect. `web/app.js` already renders null as `n/a`.

Verified by `TestCacheStatsTransport`, five tests covering publish-and-clear,
retention on failure, aggregate read, unreachable cache, and the
never-published case.

**Prevention:** a metric displayed by one process about work performed by
another needs a transport. This one silently reported a plausible-looking zero
instead of failing, which is the harder version of the bug. The general rule:
a module-level counter is per-process by definition, and any dashboard reading
one is reporting on the web server, not on the system.

**Consequence for v0.7.0:** the exit criterion "Status bar shows a non-zero
cache hit rate" is satisfiable again, provided the correlator has processed at
least one reading before the capture.

---

## E-0NN — [symptom in the words you would search for]
**Found:** [date], [gate] | **Status:** [FIXED / OPEN / KNOWN LIMITATION] | **Cost:** [time lost]

**Symptom:** [what you actually observed]
**Root cause:** [why, not what]
**Fix:** [what changed, with verification]
**Prevention:** [the general rule this instance teaches]
```
