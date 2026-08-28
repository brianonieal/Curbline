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

## E-0NN — [symptom in the words you would search for]
**Found:** [date], [gate] | **Status:** [FIXED / OPEN / KNOWN LIMITATION] | **Cost:** [time lost]

**Symptom:** [what you actually observed]
**Root cause:** [why, not what]
**Fix:** [what changed, with verification]
**Prevention:** [the general rule this instance teaches]
```
