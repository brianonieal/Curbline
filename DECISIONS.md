# DECISIONS

Every entry carries a `Flips if:` line naming a specific observable trigger,
not a risk. "Flips if the sensor count exceeds ~5,000" is a flip condition.
"Risk: may not scale" is not.

Check the flip condition before relitigating any decision below.

---

## D-001 — PostGIS on RDS rather than DynamoDB
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

Curbline's output is produced by two spatial operations: DBSCAN clustering of
inundated sensors, and polygon intersection against NWS warnings. Neither is a
key-value lookup. DynamoDB would mean implementing DBSCAN and point-in-polygon
in the worker.

**Rejected:** DynamoDB plus shapely. Zero VPC surface, instant provisioning, no
free-tier clock. Loses the spatial queries.
**Rejected:** Postgres without PostGIS. Pointless; the extension is the reason.

**Cost accepted:** three VPC-bound services instead of one, and RDS taking
minutes to provision rather than seconds.

**Flips if:** a second RDS instance, launched into the default VPC security
group, still refuses connections from an EC2 host in that same group. Not
before.

**Revised 2026-08-27.** The original flip fired on "EC2 cannot reach RDS by the
end of build day one," which mispriced the switch. DynamoDB plus shapely reads
as a swap and is a data-layer rewrite: 16 functions in `curbline/db.py`, both
stored functions in `sql/schema.sql` reimplemented in Python, plus scikit-learn
for DBSCAN and pyproj for the 4326 to 2263 transform. Shapely provides neither
and neither is in `requirements.txt`. It also deletes
`tests/fixture_clusters.sql`, the only verification artifact in this repo backed
by real execution rather than assertion, and it makes the README argue against
its own "Why PostGIS rather than a key-value store" section. An escape hatch
that costs more than the failure it escapes is not an escape hatch. The
connectivity ladder in `VERSION_ROADMAP.md` under v0.5.0 replaces it; every rung
above this one costs minutes.

---

## D-002 — Keep ElastiCache, using four components instead of three
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

The rubric awards full marks for at least three of four components. Four earns
the same points as three. ElastiCache was kept anyway because sensor metadata
is read on every message and changes almost never, which is the textbook
read-through case, and because the course's stated learning objective asks for
analysis of component interaction on performance. A cache gives that analysis
something concrete to measure.

**Rejected:** SQS + RDS + SNS only. One less VPC-bound service on the day VPC
config is the top risk. Same rubric score.

**Flips if:** ElastiCache provisioning fails or is unreachable and costs more
than one hour. Drop it; three components is full marks.

---

## D-003 — Zone identity from a hash of the member sensor set
**Date:** 2026-08-27 | **Status:** ACTIVE, superseded at v1.2.0 | **Gate:** pre-v0.5.0

A zone has no natural key. A UUID4 per detection would create a new zone every
cycle and make lifecycle tracking impossible. `stable_zone_id` hashes the
sorted member set so the same flooded block keeps its identity across cycles.

**Known cost, asserted in a test:** a zone that gains or loses one sensor
becomes a different zone, losing its `opened_at` and its state.

**Rejected:** spatial overlap matching against the previous cycle's hulls. The
correct answer, and roughly four hours of work not available before submission.

**Flips if:** any demo or evidence run shows a zone fragmenting visibly as
sensors join or leave. Then v1.2.0 moves ahead of submission.

---

## D-004 — USGS reports rise above baseline, not absolute stage
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

Verified empirically against the live API. Gage height is water surface above
an arbitrary local datum, not flood depth. Twelve gauges sampled in the NYC
bounding box on a dry day returned -0.26 to 19.58 ft; converted directly, ten
of twelve read past the 20 cm warning threshold with no rain. `USGSSource` now
reports the positive excursion above each site's own 10th-percentile recent
history.

**Rejected:** raw gage height. Produces permanent false alarm.
**Rejected:** NWS flood-stage thresholds per site. More correct, more API work,
not available in the same endpoint.

**Cost accepted:** the baseline comes from a bounded history query, so a site
with no history falls back to its first reading, which yields a rise of zero.
Failing toward no-alarm is the safe direction for a detector.

**Flips if:** FloodNet access is granted. Then USGS becomes a fallback only and
this stops being the primary measurement path.

---

## D-005 — Thresholds are source-specific, not global
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

5/10/20 cm is calibrated for FloodNet street depth: above sensor noise, below
curb height. USGS stage rise is a different physical quantity on a different
scale. A 20 cm rise on the Passaic is routine. After the D-004 fix, 9 of 29
gauges still cleared 20 cm under normal conditions.

**Decision:** thresholds move with the source. Roughly 60/90/120 for USGS as a
starting point, and every screenshot states which source produced it.

**Flips if:** v1.3.0 calibration against 311 complaints produces better numbers.
Replace these with the measured ones.

---

## D-006 — Buffer cluster hulls by 492 ft
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

A two-sensor cluster convex-hulls to a LINESTRING and a one-sensor cluster to a
POINT. Both violate `geometry(Polygon)` and render invisibly on a map. The
buffer forces a polygon and gives a two-sensor zone a plausible footprint.

**Cost accepted:** the hull overstates the flooded area. It is a zone marker,
not a flood extent measurement, and the report must not present it as one.

**Flips if:** sensor density ever gets high enough that concave hulls are
meaningful. Then `ST_ConcaveHull` replaces buffer-of-convex-hull.

---

## D-007 — Minimum two sensors to form a zone
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

One wet sensor is a reading. Two adjacent wet sensors is a flooding street.
DBSCAN `minpoints=2` encodes exactly that, and isolated wet sensors are
correctly rejected as noise.

**Cost accepted:** a genuine single-point flood, such as one underpass, is
never detected.

**Flips if:** v1.3.0 calibration shows the misses are dominated by real
single-sensor events. Then `minpoints=1` with a higher depth threshold.

---

## D-008 — New zones enter `forming` and do not notify
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

A zone must survive a second cycle before it issues an advisory. Suppresses
single-cycle sensor noise.

**Cost accepted:** roughly one poll interval of added latency on a real event.

**Flips if:** measured latency from first wet reading to advisory exceeds the
useful decision window for a real flood, which is minutes not hours.

---

## D-009 — Audit to S3 before publishing to SNS
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

If S3 fails, the handler raises, the message is not deleted, and the unit
retries. No advisory is ever issued without a durable record of the evidence
and the thresholds that produced it.

**Cost accepted:** an S3 outage stops advisories entirely rather than sending
unaudited ones.

**Flips if:** never, for a system that recommends closing streets. If this
inverts, the system has stopped being defensible.

---

## D-010 — Default VPC rather than a purpose-built one
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

The default VPC already has subnets across AZs with an internet gateway route.
Building one removes the single largest source of lost hours on a short build.

**Cost accepted:** less isolation than a production design would use. Named as
a limitation in the report rather than presented as a choice for security.

**Flips if:** this project ever handles data that is not public.

---

## D-011 — No Secrets Manager
**Date:** 2026-08-27 | **Status:** ACTIVE | **Gate:** pre-v0.5.0

The assignment permits messaging, queuing, caching, databases, storage and VMs,
and states nothing else is allowed. Secrets Manager is outside that list. The
RDS password lives in a mode-600 env file written by `provision.py` and
gitignored.

**Cost accepted:** weaker credential handling than production would use.
Stated in the report as constraint-driven, not as good practice.

**Flips if:** the instructor confirms supporting services are permitted.

---

## D-012 — Entered Build Rules at Phase 6
**Date:** 2026-08-27 | **Status:** ACTIVE, deviation recorded | **Gate:** pre-v0.5.0

Code was written before Phases 1 through 5 ran. No intake, no interrogation, no
MOCKUPS.md approval, no FRONTEND_SPEC.md gate.

**Reason:** a two-day deadline with an already-chosen concept. The five-phase
cycle is designed for client work with negotiable scope.

**Cost accepted:** no mockup approval trail, no component registry, and design
decisions recorded in CSS comments rather than DESIGN_SYSTEM.md.

**Flips if:** this project continues past v1.0.0 into Phase B. At that point
run the missing gates properly rather than compounding the deviation.

---

## D-013 — Prove cache degradation by revoking the security group rule, not by deleting the cluster
**Date:** 2026-08-28 | **Status:** ACTIVE | **Gate:** v0.5.0

`DEMO.md` originally said "stop ElastiCache". An ElastiCache cluster cannot be
stopped, only deleted, so in practice that instruction resolved to deleting it.
The two are not the same test.

Deleting the cluster removes its DNS record, so the next connect fails on name
resolution. Revoking tcp/6379 from the app security group leaves the cluster and
its DNS intact and makes it unreachable over the network, so the connect fails
on timeout. `cache.py` claims to survive an unreachable cache, and the timeout
path is the one it actually documents. Test the failure the code claims to
handle, not a neighbouring one that happens to be easier to cause.

**Rejected:** delete and recreate. Exercises a different failure, costs a
recreation, and cannot be undone inside a demo window. Reversibility is a real
benefit of the revoke but it is the secondary reason, not the reason.

**Cost accepted:** the revoke must be paired with `systemctl restart
curbline-api`. Security groups are stateful, so an already established Redis
connection can survive the revoke through connection tracking and the capture
would pass while demonstrating nothing. The restart forces a fresh connect that
has to clear the revoked rule. This is a procedural step that is easy to omit
and silently invalidates the evidence when omitted.

**Flips if:** `cache.py` ever resolves the cache endpoint per call rather than
holding a pooled connection. DNS failure then becomes a reachable path and both
tests are worth running.
