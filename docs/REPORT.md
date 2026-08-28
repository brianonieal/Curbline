# Curbline: architecture and implementation report

**Author:** Brian Onieal
**Course:** Johns Hopkins University, Cloud Computing [course number]
**Submitted:** 2026-08-28
**Repository:** https://github.com/brianonieal/Curbline

---

## 1. Problem and business value
*Rubric: Real-World Problem Relevance, 10 pts*

On 1 and 2 September 2021 the remnants of Hurricane Ida dropped more rain on New
York City in an hour than the sewer system was built to carry. A review of death
records by the New York City Department of Health and Mental Hygiene identified
fourteen Ida-related injury deaths in the city, thirteen of them caused directly
by the storm.[^ida] The most common circumstance of death was drowning in an
unregulated basement apartment, at 71% of the total, and 79% of the decedents
lived in Queens. The review named rapid nighttime flooding, inadequate exits and
impeded emergency access as the recurring conditions. The failure was not a
shortage of rainfall data. Radar, gauges and forecasts all showed the storm. What nobody had
was street-level water depth aggregated into a statement an operations person
could act on: *this block is flooding now*.

The user for this system is a borough emergency coordinator during an active
rain event. Their decisions are concrete and time-bounded: close a roadway
segment, stage barricades, or notify residents in a specific area. Today they
work from two inputs. National Weather Service flash flood warnings cover a
county or a forecast zone, which is the right shape for a warning and the wrong
shape for closing a street. 311 complaints are street-level but arrive after
someone has already driven into the water.

Curbline sits between those two. It ingests street-level depth readings,
clusters spatially adjacent sensors that are simultaneously wet, and emits a
zone with a recommended action. The value is not the measurement. It is the
aggregation and the fact that something is attached to it.

### 1.1 Why a zone rather than a reading

One sensor reporting 8 cm is a reading. It might be a clogged drain, a bad
sensor, or a puddle. Four adjacent sensors reporting water at the same time is a
flooding street, and that is a different claim entirely: it survives a single
faulty device, it has a footprint you can draw on a map, and it corresponds to
the unit a coordinator actually acts on. A roadway is closed, not a point.

This distinction is the entire product. Everything downstream, the clustering
radius, the minimum sensor count, the one-cycle delay before notifying, exists
to make the difference between those two claims reliable.

### 1.2 What exists already

FloodNet NYC operates the sensor network and publishes a public dashboard
showing individual sensors and their current depth. The National Weather Service
publishes flood warnings as polygons and zone-based products through its API.
Both are mature, both are authoritative, and Curbline consumes rather than
replaces them.

Neither produces zones. FloodNet shows you sensors and leaves the aggregation to
the reader. NWS shows you a warning area far coarser than a street. Neither
attaches a recommended action to what it displays.

This is a configuration of existing practice, not a new method. DBSCAN is from
1996, PostGIS has clustered points for two decades, and correlating a sensor
network against an authoritative warning feed is standard practice in
environmental monitoring. The contribution here is that the gap between those
two specific tools is filled, and that the output is shaped like a decision.

[^ida]: Yuan, A., Spira-Cohen, A., Olson, C., and Lane, K. "Immediate Injury
Deaths Related to the Remnants From Hurricane Ida in New York City, September
1-2, 2021." *Disaster Medicine and Public Health Preparedness*, vol. 18, 2024,
article e55. DOI 10.1017/dmp.2024.49. Authors are with the Bureau of
Environmental Surveillance and Policy, New York City Department of Health and
Mental Hygiene.
Contemporaneous September 2021 reporting gave thirteen deaths with eleven in
basement apartments. Those figures predate the medical examiner review and are
superseded by it, which is normal for disaster tolls. The reviewed figures are
used here throughout.

---

## 2. Architecture
*Rubric: Report Completeness and Architecture Detail, 15 pts*

### 2.1 Component diagram

```
   FloodNet / USGS                     NWS alerts
        |                                   |
        +----------------+------------------+
                         |
                  [1] COLLECTOR                    EC2, systemd unit
                         |
                    SQS: curbline-ingest  ---> curbline-ingest-dlq
                         |
                  [2] CORRELATOR                   EC2, systemd unit
                         |          \
                         |           ElastiCache (sensor metadata, read-through)
                         |          /
              RDS PostgreSQL + PostGIS
                    ST_ClusterDBSCAN
                         |
                    SQS: curbline-zones   ---> curbline-zones-dlq
                         |
                  [3] DISPATCHER                   EC2, systemd unit
                         |
              +----------+----------+
              |                     |
        S3 audit record        SNS advisory
        (written first)        (published second)

                  api/server.py                    presentation layer only
                  FastAPI + WebSocket              NOT one of the three
                         |
                    browser console
```

The three graded components are the collector, the correlator and the
dispatcher. Each is a separate long-running process under its own systemd unit,
communicating only through SQS. `api/server.py` is the presentation layer. It
reads the same database and serves the console, and it is deliberately not
counted as one of the three because it performs no pipeline work.

### 2.2 The three components

**Collector.** Input: the configured reading source (FloodNet, USGS, or a
recorded replay) plus the NWS alerts endpoint. Output: `reading` and `alert`
messages on `curbline-ingest`. It owns source adaptation and nothing else: it
normalises whatever the upstream API returns into a single `Reading` shape and
publishes it. It does not write to the database, does not cluster, and holds no
state beyond its poll cursor.

**Correlator.** Input: `curbline-ingest`. Output: `zone` candidate messages on
`curbline-zones`. It owns persistence of readings and the spatial question. It
writes each reading through the idempotency gate, then asks PostGIS which
currently inundated sensors form clusters. It does not decide whether a cluster
warrants an advisory and never notifies anyone.

**Dispatcher.** Input: `curbline-zones`. Output: an S3 audit object and an SNS
publication. It owns the zone lifecycle and the decision to notify: what level
this zone rates, whether it has changed since the last cycle, and whether it has
survived long enough to be believed. It performs no spatial work.

The split is deliberate. Each stage can fail, restart, or fall behind without
corrupting the others, and each can be reasoned about independently.

### 2.3 Technology component mapping

| Assignment component | Service | Why this one |
|---|---|---|
| Queuing | Amazon SQS | Decouples three stages with different failure and latency profiles. At-least-once delivery with a visibility timeout and a dead-letter queue after five receives is exactly the semantic this pipeline needs, and it forced the idempotency design in section 5.1. |
| Database | Amazon RDS PostgreSQL + PostGIS | The output is produced by two spatial operations, DBSCAN clustering and polygon intersection. Neither is a key-value lookup. See D-001. |
| Caching | Amazon ElastiCache for Redis | Sensor metadata is read on every single message and changes almost never. That read-to-write ratio is the textbook read-through case. See D-002. |
| Messaging | Amazon SNS | Fan-out to human subscribers without the pipeline knowing who they are. Adding a recipient is a subscription, not a deployment. |
| Storage (extra) | Amazon S3 | Immutable audit record written before any advisory is sent, carrying the evidence and the thresholds that produced the decision. See D-009. |
| Compute (extra) | Amazon EC2 | The only permitted compute. Three worker processes plus the API under systemd on a single t3.micro. |

Four of the four assignment components are used where three earn full marks.
ElastiCache was kept anyway, because the course's stated learning objective asks
for analysis of component interaction on performance, and a cache gives that
analysis something measurable.

The service list is deliberately minimal. The assignment permits messaging,
queuing, caching, databases, storage and VMs, and states nothing else is
allowed. That constraint is why the RDS password lives in a mode-600 env file
written by `provision.py` rather than in Secrets Manager, which is outside the
permitted list. This is recorded as D-011 and named again in the limitations
section. It is a constraint-driven choice, not a recommendation.

### 2.4 Constraint compliance

- **No serverless functions.** No Lambda, no Step Functions. All three
  components are long-running processes.
- **No containers.** No Docker, ECS, Fargate, EKS, Kubernetes or App Runner.
  The workers run as systemd units directly on the instance.
- **No service mesh.**
- **No self-installed data services.** Every database, queue, cache and topic is
  an AWS managed service. Nothing in `bootstrap.sh` installs a data service on
  the host; the apt line installs client tools only, `postgresql-client` and
  `redis-tools`.
- **Permitted services only.** Six services, all inside the permitted
  categories.

`scripts/gate-check.sh` mechanically enforces the first four of these on every
gate close, scanning the tracked file set for Lambda references, container
artifacts, and orchestrator manifests.

---

## 3. Component interaction analysis
*Rubric learning objective: evaluate impact on scalability, performance, efficiency*

### 3.1 Why queues rather than direct calls

If the collector called the correlator directly, three things would be true that
are not true now. A slow database write would apply backpressure all the way to
the upstream API poll, so a database hiccup would become dropped readings.
Restarting the correlator would drop whatever the collector was mid-call on. And
the two stages would have to scale together even though their costs are nothing
alike: the collector is network-bound and cheap, the correlator runs DBSCAN over
every currently inundated sensor.

With SQS between them, the collector's only failure mode is "SQS is
unreachable". Observed queue depth is the visible proof: during the 2026-08-28
run the ingest queue held messages in flight while the correlator worked through
them, and when the correlator was restarted for a code deploy the queue absorbed
the gap and the messages were processed on the next poll rather than lost.

The dead-letter queues are the other half. A message that fails five times is
removed from the working queue rather than blocking it. Section 5.3 covers what
that protects against, and E-016 is a concrete case where it would have
mattered: a poison message failing on every redelivery.

### 3.2 What the cache actually buys

Sensor metadata, the name and coordinates of a device, is consulted on every
reading and changes essentially never. At the observed replay cadence of five
readings per minute that is five cache reads per minute against a table that
changed twice all day. That ratio, not the raw latency saving, is the
justification.

The degradation path matters more than the speedup. `cache.sensor()` is a
read-through: a miss falls back to Postgres and repopulates. A dead cache makes
the system slower, not wrong. The deliberate-failure screenshot in Appendix C
shows the console still serving with the cache unavailable and the cache
indicator amber.

E-016 is the interesting finding here, and it is a correction to how the cache
was originally used. The correlator upserted a sensor row only when the cache
missed, which quietly treated a cache hit as proof the row existed in Postgres.
Those two facts diverge for ordinary reasons: a restored database, a manual
delete, a cache outliving the table it describes. When they diverged, every
insert violated a foreign key, and because SQS redelivers, the same messages
failed continuously instead of once. The fix was to catch the violation, repair
the row and retry once. **A cache answers what a row looked like. It is never
evidence that the row exists.**

### 3.3 Why the spatial work is in the database

Clustering runs as `ST_ClusterDBSCAN` inside a PostGIS function, and NWS
correlation as `ST_Intersects` against a GiST-indexed geometry column. The
alternative is pulling every current reading into the worker and implementing
DBSCAN and point-in-polygon in Python.

That alternative costs a clustering library, a projection library, and the
transfer of the full working set on every cycle. It also loses the index: GiST
makes `ST_Intersects` against a few hundred alert polygons a bounded operation
rather than a scan.

The projection is the part worth stating explicitly. Coordinates arrive in
EPSG:4326, which is degrees. A DBSCAN epsilon expressed in degrees means a
different physical distance at different latitudes, and a different distance
east-west than north-south. The clustering function transforms to EPSG:2263, the
New York State Plane Long Island foot, before measuring. Epsilon is 1640 feet
because in that projection 1640 feet is 1640 feet everywhere in the study area.

### 3.4 Where this breaks at scale

The correlator re-runs clustering over all currently inundated sensors on every
cycle, debounced by a minimum interval. At the scale this system was built for,
tens to hundreds of sensors, that is the correct simple choice. At tens of
thousands it is wrong: the work per cycle grows with the total wet sensor count
regardless of how few of them changed.

Three specific changes, in the order I would make them:

1. **Spatial partitioning by borough.** Clustering is local; a sensor in the
   Bronx can never join a cluster in Staten Island. Partitioning turns one large
   DBSCAN into five independent smaller ones that parallelise trivially.
2. **Incremental clustering.** Re-cluster only partitions containing a sensor
   whose state changed this cycle.
3. **A windowed materialised view** over `latest_readings`, refreshed on write,
   rather than recomputing the window in every query.

A second limit arrives before that one. Zone identity is a hash of the member
sensor set (D-003), so a zone whose membership changes by one sensor becomes a
different zone. At coursework density that is acceptable and was directly
observed during the 2026-08-28 replay: as the storm frames advanced and the wet
set grew, the same physical flood presented as successive distinct zone ids. At
production density it would produce constant churn. The fix is spatial overlap
matching against the previous cycle's hulls, which is section 8.

---

## 4. Data

### 4.1 Sources

**FloodNet NYC.** Street-level depth sensors, the intended primary source.
Reports standing water depth on a road surface, which is directly the quantity
this system reasons about. Access requires a data agreement; the data is under a
noncommercial license and is not redistributed in this repository, which is why
`.gitignore` excludes `data/*.json`.

**USGS Water Services.** The fallback, and the source actually used for live
runs. Queried through the modernised Water Data API rather than the legacy
waterservices host. Critically, USGS reports **gage height**, the water surface
above an arbitrary local datum, not flood depth. Section 4.3 covers what that
forced.

**National Weather Service.** Active flood alerts. No API key required, but
requests without a `User-Agent` identifying the caller are rejected. Used only
for corroboration; an NWS polygon intersecting a zone footprint raises that
zone's advisory level but never creates a zone on its own.

### 4.2 The null-geometry problem

A large share of NWS alerts carry `geometry: null`. These are zone-based
products that reference UGC forecast zones by code instead of shipping a
polygon. Dropping them loses real warnings; fabricating a polygon for them
invents evidence the system does not have.

Curbline stores them with a NULL geometry. They are ingested, visible and
counted, but they cannot correlate against a zone footprint, because there is no
footprint to intersect. `TestAlertFiltering` asserts both halves of this: that
null-geometry alerts survive ingest, and that non-flood event types are dropped.

Honest quantification: during the 2026-08-28 evidence run the collector reported
**0 active flood alerts** in the NYC bounding box, so the null-geometry path was
not exercised in the final run. The handling exists and is unit tested; the
count from a live storm is not something this build observed. Resolving these
properly needs a UGC zone lookup, which is section 8.

### 4.3 Detection parameters

**These are decisions, not constants, and they are source-specific.**

| Parameter | Value (FloodNet) | Value (USGS) | Reasoning |
|---|---|---|---|
| Depth threshold | 5.0 cm | 60 cm | On FloodNet, 5 cm is above sensor noise and below the point where a car is affected. On USGS it is a rise above baseline, a different physical quantity on a different scale. |
| Advisory level | 10 cm | 90 cm | Roughly half a curb. Water a driver will notice and a pedestrian will avoid. |
| Warning level | 20 cm | 120 cm | Most of a curb height. The depth at which a passenger vehicle is genuinely at risk. |
| Cluster radius | 1640 ft (~500 m) | same | Roughly two to three Queens blocks. Wide enough to join sensors on the same flooded street, narrow enough not to bridge unrelated streets. Measured in EPSG:2263 so the distance is constant across the study area. |
| Minimum sensors | 2 | same | One wet sensor is a reading; two adjacent is a street. DBSCAN `minpoints=2` encodes exactly the product claim in section 1.1. |
| Reading window | 15 min | same | Long enough to tolerate a missed poll, short enough that a zone reflects current conditions rather than an hour-old storm. |

Two of these deserve their cost stated plainly.

**The threshold pair is the single largest correctness risk in the system, and
it was wrong in the shipped defaults until 2026-08-28.** D-005 recorded on
2026-08-27 that thresholds must move with the source. The code did not implement
it: `SOURCE` defaulted to `usgs` while the thresholds defaulted to FloodNet's
5/10/20, and the dispatcher hardcoded the advisory and warning tiers. The first
live dashboard consequently reported 17 of 28 sensors wet with a deepest reading
of 151.8 cm, which is 151.8 cm of river stage rise above a local datum and not
151.8 cm of water in a street. This is logged as E-014. The correction derives
all thresholds from the configured source, and `TestSourceCalibration` now
asserts the mapping.

**Minimum sensors of 2 means a genuine single-point flood is never detected.**
One flooded underpass with one sensor in it is rejected as DBSCAN noise. That is
a deliberate trade of recall for precision, and it is a real miss, not a
theoretical one.

---

## 5. Reliability

### 5.1 Idempotency

SQS delivers at least once. Every consuming stage must therefore be able to see
the same message twice without acting twice.

The gate is `db.claim_reading`, an `INSERT ... ON CONFLICT (ingest_id) DO
NOTHING` that returns whether this call won the race. A duplicate returns false
and is logged as a skip, not an error. The `ingest_id` is generated once by the
collector when the reading is first observed, so it identifies the observation
rather than the delivery.

This was verified directly rather than asserted. On 2026-08-28 a message that
had already been processed was rebuilt from its database row and republished to
`curbline-ingest`. The row count for that `ingest_id` was 1 before and 1 after.
Counting by `ingest_id` rather than by table total matters, because the collector
inserts new readings continuously and a table total would have moved for
unrelated reasons.

### 5.2 Ordering of side effects

The dispatcher writes the S3 audit record **before** publishing to SNS. If the
S3 write raises, the handler raises, the SQS message is not deleted, and the
unit is retried. No advisory is ever sent without a durable record of the
evidence and the thresholds that produced it.

The inverse ordering would allow a recommendation to close a street to exist
with no record of why. For a system in this class that is not an acceptable
failure mode, which is why D-009's flip condition is "never".

Verified with artifacts rather than a log line: each row in `advisories` carries
both an `sns_message_id` returned by SNS and an `audit_key` naming the S3 object
written first, and the objects exist at those keys.

### 5.3 Failure isolation

Each worker deletes an SQS message only after its handler returns successfully.
A handler that raises leaves the message in flight; when the visibility timeout
expires it becomes visible again and is retried. After five receives it moves to
the corresponding dead-letter queue rather than blocking the working queue
forever.

The three workers share nothing but the queues and the database. The dispatcher
being down does not stop readings from being persisted; it stops advisories from
being issued, and the backlog waits in `curbline-zones`. This was observed
during development: the correlator was restarted repeatedly while the collector
kept publishing, and no readings were lost.

### 5.4 Graceful shutdown

Each worker installs a SIGTERM handler that stops accepting new messages,
finishes the in-flight unit, and exits. `systemctl restart` therefore does not
lose work mid-handler. The journal lines `shutdown requested (signal 15),
draining` followed by `<worker> stopped` are visible in Appendix C and are the
evidence that the drain actually runs rather than being a claim.

---

## 6. Verification

### 6.1 Clustering validation

`tests/fixture_clusters.sql` exercises the spatial core against real NYC
coordinates in a live PostgreSQL and PostGIS instance.

| Case | Expected | Verified |
|---|---|---|
| 4 wet sensors, SE Queens, within ~700 m | one zone | 0.437 km², `ST_Polygon` |
| 2 wet sensors, Red Hook | polygon, not linestring | 0.125 km², `ST_Polygon` |
| 1 wet sensor, isolated in the Bronx | rejected as DBSCAN noise | excluded |
| Dry sensor inside a wet zone footprint | not absorbed | excluded |
| NWS polygon over Queens only | Queens correlates, Red Hook does not | correct |

The second row is the degenerate-hull case from E-002 and D-006. The convex hull
of two points is a LINESTRING and of one point is a POINT; both violate
`geometry(Polygon, 4326)` and render invisibly. The function buffers by 492 ft to
force a polygon. The cost is that the hull overstates the flooded area. It is a
zone marker, not a flood extent measurement, and must not be read as one.

### 6.2 Unit tests

31 tests, all passing. moto stands in for SQS, SNS and S3; the database layer is
stubbed. **No test touches a real AWS account or incurs spend**, which is
enforced mechanically by `scripts/gate-check.sh` at every gate close.

Coverage targets the four paths that actually break in a queue pipeline: the
happy path, duplicate delivery, a failing downstream, and a cold cache.

| Class | Tests | Covers |
|---|---|---|
| `TestZoneIdentity` | 4 | Hash stability, order independence, the D-003 membership tradeoff asserted as a property, UUID validity |
| `TestAdvisoryLadder` | 8 | Threshold boundaries, and that NWS corroboration never lowers a level |
| `TestLifecycle` | 4 | forming to active to receding transitions |
| `TestCacheDegradation` | 4 | Cold cache, unreachable cache, corrupt entry, hit path skips the loader |
| `TestWorkerLoop` | 2 | Delete only after success; failed handler leaves the message for retry |
| `TestAuditOrdering` | 2 | S3 write precedes SNS publish; failed audit blocks the notification |
| `TestAlertFiltering` | 2 | Null-geometry alerts survive ingest; non-flood events dropped |
| `TestSourceCalibration` | 3 | D-005 threshold mapping per source, and the fallback for an unknown source |
| `TestZoneLookupTypes` | 1 | E-017: a UUID-typed zone id still matches the string in the queue body |
| `TestSensorCacheDivergence` | 1 | E-016: a foreign key violation repairs the sensor row and retries once |

**What this suite could not catch, stated rather than hidden.** The suite is
moto throughout and never executed SQL against a real PostGIS instance or
exercised psycopg's type mapping. Two defects lived in exactly that blind spot
until the system first ran against real managed services:

- **E-013.** `current_clusters(NUMERIC, INT, NUMERIC, INT)` was called with
  Python floats, which psycopg sends as `double precision`. PostgreSQL casts
  `smallint` to `integer` implicitly but will not cast `double precision` to
  `numeric` during function resolution, so the call was unresolvable and
  reported as "function does not exist". The function existed the whole time.
- **E-017.** The dispatcher's previous-state lookup keyed a dictionary on
  `uuid.UUID` values returned from a UUID column and looked them up with the
  string from the SQS body. `UUID(x)` never equals `str(x)`, so the lookup never
  matched, every zone stayed `forming`, and the early return meant **no advisory
  had ever been built, audited, or published in any run of this system.**
  `next_state` was unit tested and correct; the defect was one line above the
  call, in how its argument was obtained.

The last two rows of the table above are regression tests added for those two.
The general lesson is worth more than either fix: a pure function tested in
isolation says nothing about whether its inputs are ever computed correctly.

### 6.3 Evidence

Full screenshot set in Appendix C. Nine defects were found and fixed during the
first end-to-end run against real infrastructure, logged as E-009 through E-017
in `ERRORS.md`. That file is part of the deliverable rather than an internal
artifact, because the failure modes it records, an IAM action name that does not
exist, a service-linked role a fresh account has never created, and a cache used
as an existence oracle, are the substance of what building this taught.

---

## 7. Limitations

Nine, stated plainly.

1. **The hull overstates the flooded area.** Cluster footprints are convex hulls
   buffered by 492 ft to guarantee a valid polygon. A zone marker, not a flood
   extent measurement.
2. **Single-sensor floods are never detected.** `minpoints=2` deliberately
   rejects isolated wet sensors as noise. One flooded underpass with one sensor
   in it is a miss.
3. **Zone identity churns with membership.** A zone is identified by a hash of
   its member sensors, so one sensor joining or leaving produces a new zone id
   for the same physical flood. Directly observed during the replay run.
4. **USGS is a proxy, not a measurement of the thing.** Stage rise above a
   per-site baseline is not street depth. When the live source is USGS, the
   system is detecting rivers rising, not streets flooding, and the two
   correlate imperfectly at best.
5. **Thresholds are uncalibrated.** 5/10/20 and 60/90/120 are reasoned defaults,
   not numbers fitted to observed flooding. Nothing in this project validates
   them against ground truth such as 311 complaints or documented closures.
6. **No validation of detection quality at all.** There is no hit rate, no false
   alarm rate, and no baseline to compare against. The system has been shown to
   function end to end. It has not been shown to be *correct* about flooding.
7. **The default VPC is used.** Less network isolation than a production design
   would have. A deliberate trade to protect a short build schedule (D-010), not
   a security choice.
8. **The database credential is in a mode-600 env file.** The assignment's
   permitted service list excludes Secrets Manager (D-011). This is
   constraint-driven and is not good practice.
9. **The sensor input to the end-to-end demonstration was synthetic. Everything
   downstream of it was real.** No flooding occurred anywhere in the capture
   window, so no genuine reading would cross a detection threshold. The pipeline
   was therefore exercised with synthetic readings from
   `data/replay.example.json`, a four-frame progression written for interface
   testing, replayed through the live production stack.

   That needs stating precisely, because "replay data" covers three different
   situations and they are not equivalent. This was **not** a recording of a
   real storm, and it was **not** live gage data with the detection threshold
   lowered until something tripped, which `DEMO.md` explicitly warns against
   screenshotting. It was fabricated depth values on five fabricated sensor ids
   in the `demo:` namespace, injected at the very top of the pipeline.

   From that injection point onward nothing was simulated. SQS carried every
   message under at-least-once delivery. The correlator wrote through the real
   idempotency gate into RDS. PostGIS executed the actual `ST_ClusterDBSCAN`
   over real geometry and returned real hulls. The dispatcher applied the real
   advisory ladder, wrote real immutable audit objects to S3, and published to
   SNS, which returned message ids now recorded in the `advisories` table.

   The distributed system is real, and the distributed system is what this
   assignment grades. The weather is synthetic, and the weather is not something
   I control. A live USGS run on the same afternoon correctly produced zero
   advisories, which is the honest result for a dry day and demonstrates nothing
   whatsoever about the notification path.

---

## 8. What I would do differently

**Zone identity by spatial overlap rather than membership hash.** Match this
cycle's hulls against the previous cycle's by `ST_Intersects` and carry the id
forward when they overlap above a threshold. This removes limitation 3 and is
the single change that most improves the system's behaviour over time.

**Calibrate against observed flooding.** Join historical 311 flooding complaints
and documented street closures against what the detector would have produced,
and fit the thresholds to that. This turns limitations 5 and 6 from open
questions into measured numbers, and it is the difference between a system that
runs and a system anyone should trust.

**Resolve zone-based NWS alerts through UGC lookup.** Fetch the UGC zone
geometry and attach it, so the products currently stored with a NULL geometry can
correlate like any other.

**Test against a real PostgreSQL in CI.** E-013 and E-017 were both invisible to
a moto-only suite and both were found by a human running the system. A
containerised Postgres is not available under this assignment's constraints, but
a hosted instance in a test account would have caught both in minutes.

---

## Appendix A: Repository layout

```
curbline/        importable package: config, aws, cache, db, sources
workers/         the three graded components
api/             FastAPI presentation layer, not a graded component
infra/           account-setup.sh, bootstrap.sh, provision.py, teardown.py, iam-policy.json
sql/             schema.sql, including current_clusters() and alert_for_hull()
web/             single-page console: index.html, app.js, style.css
tests/           31 unit tests plus fixture_clusters.sql
systemd/         four unit files
data/            capture_replay.py and the disclosed replay fixture
docs/            this report
```

Governance files at the root: `DECISIONS.md` (12 decisions, each with a flip
condition), `ERRORS.md` (17 logged defects), `TESTS.md`, `CHANGELOG.md`,
`VERSION_ROADMAP.md`, `TIMELOG.md`, `COSTS.md`, `RETENTION.md`.

## Appendix B: Provisioning and teardown

One-time account setup is `infra/account-setup.sh`, run from AWS CloudShell so
no long-lived access key is ever created. It creates the key pair, launches the
instance, creates the `curbline-ec2` role with the scoped policy in
`infra/iam-policy.json`, creates the RDS and ElastiCache service-linked roles a
fresh account lacks, and opens SSH to the operator's address only.

Provisioning is `infra/bootstrap.sh`, run on the instance:

```bash
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=<operator address>/32 ./infra/bootstrap.sh
```

The operator CIDR must be supplied explicitly. `provision.py` falls back to
`checkip.amazonaws.com`, which from inside AWS returns the instance's own
address and opens the console port to nobody. That is E-008 and it presents as a
security group misconfiguration rather than as the address-resolution bug it is.

Teardown is `python3 infra/teardown.py --confirm`, which deletes RDS,
ElastiCache, both queue pairs, the topic, the bucket and its contents, the
subnet groups and the security groups. It deliberately does not terminate the
EC2 instance, since that is the host executing it.

Cost control is part of the definition of done for every gate. RDS and
ElastiCache bill hourly against separate free-tier clocks, and a forgotten
teardown consumes a month's allowance.

## Appendix C: Full screenshot set

Every screenshot states which data source produced it. Captures marked
**replay** used `data/replay.example.json`, a four-frame recorded storm
progression, and are disclosed as replays here and in `curbline/sources.py`.
Captures marked **usgs** are live gage data.


### Cloud integration (10 pts)

| File | Source | Caption |
|---|---|---|
| | | RDS console: instance `curbline-db`, status Available, engine PostgreSQL |
| | | ElastiCache console: cluster `curbline-cache`, status Available |
| | | SQS console: all four queues, showing messages available |
| | | SNS console: topic with a confirmed subscription |
| | | S3 console: audit bucket with objects under `advisories/` |
| | | EC2 console: the instance, with its IAM role attached |

### Distributed application (10 pts)

| File | Source | Caption |
|---|---|---|
| | | `systemctl status 'curbline-*'` with all four units active |
| | | `journalctl -u curbline-collector` showing readings published |
| | | `journalctl -u curbline-correlator` showing clusters published |
| | | `journalctl -u curbline-dispatcher` showing an advisory with its audit key |
| | | SQS queue depth non-zero, which is the visible proof the stages are decoupled |

### Technology components (15 pts)

| File | Source | Caption |
|---|---|---|
| | | `SELECT PostGIS_Full_Version();` output |
| | | `SELECT * FROM current_clusters();` returning real zones |
| | | `redis-cli ... INFO keyspace` showing cached sensor keys |
| | | The received SNS email |
| | | One S3 audit object opened, showing the thresholds recorded alongside the decision |

### End-to-end (30 pts)

| File | Source | Caption |
|---|---|---|
| | | Console with zero zones (baseline) |
| | | Console with an active zone drawn, rail filled, advisory queued |
| | | Same zone before and after, showing depth change on the rail |
| | | A zone with `NWS confirmed` on the card, next to one without |
| | | Status bar showing queue depth, cache hit rate, PostGIS up |
| | | `curl /api/health` output |
| | | Stop ElastiCache, reload the console, show it still working with the cache |

*Unmet captures are listed with the reason rather than removed. See section 7.*
