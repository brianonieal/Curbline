# Curbline

Street-flood zone detection over live sensor and weather feeds for New York City.

A distributed pipeline that turns individual flood-sensor depth readings into
**zones**, which are several adjacent streets inundated at once, correlates each
zone against active National Weather Service warning polygons, and issues
graded advisories with an immutable audit trail.

---

## The problem

The remnants of Hurricane Ida caused fourteen injury deaths in New York City on
1 and 2 September 2021, thirteen of them directly. Drowning in an unregulated
basement apartment was the most common circumstance, at 71%, and 79% of the
decedents lived in Queens.<sup>[1]</sup> The gap was not rainfall data. It was
street-level water depth, aggregated into something a dispatcher could act on,
in time to act.

<sup>[1]</sup> Yuan, Spira-Cohen, Olson and Lane, NYC Department of Health and
Mental Hygiene, *Disaster Medicine and Public Health Preparedness* 18 (2024),
article e55, DOI 10.1017/dmp.2024.49. See `docs/REPORT.md` section 1.

A single sensor reporting eight centimetres of water is a reading. Four adjacent
sensors reporting water simultaneously is a flooding street, and those are
different objects. Existing public tools show the first. Curbline produces the
second, and attaches a recommended action to it.

## What this is not

This is coursework. It is not an operational warning system, it has not been
validated against ground truth, and no one should make a safety decision with
it. Detection thresholds are documented choices, not calibrated values.

---

## Architecture

Three long-running processes passing work through managed queues. No functions,
no containers, no orchestrator, per the assignment constraints.

```
  NWS api.weather.gov          Sensor source
  (alert polygons)             (FloodNet / USGS)
          |                          |
          +------------+-------------+
                       |
              [ A. collector ]  normalize, publish
                       |
                  SQS curbline-ingest
                       |
              [ B. correlator ]  persist, cluster, correlate
                   |        |
        ElastiCache |        | RDS PostgreSQL + PostGIS
       (read-through)        (source of truth, spatial engine)
                       |
                  SQS curbline-zones
                       |
              [ C. dispatcher ]  lifecycle, decide, notify
                   |        |
                  SNS      S3 (audit record)
                       |
              [ D. api ]  GeoJSON + WebSocket  -->  browser
```

### Component mapping

| Assignment component | Service used | Role |
|---|---|---|
| Queuing | Amazon SQS | Stage-to-stage handoff, two queues plus dead-letter queues |
| Database | Amazon RDS for PostgreSQL with PostGIS | Source of truth **and** the spatial clustering engine |
| Caching | Amazon ElastiCache for Redis | Read-through cache over sensor reference data |
| Messaging | Amazon SNS | Advisory fan-out |
| Storage (permitted extra) | Amazon S3 | Write-once advisory audit record |
| Compute (permitted extra) | Amazon EC2 | Hosts the four processes |

Four of the four eligible components are used. Three were required.

**Process D (`api`) is the presentation layer, not one of the three required
components.** The three components are the collector, correlator, and
dispatcher. The API exists so the pipeline's output can be seen; it performs no
pipeline work.

Every managed service is an AWS service. Nothing is self-installed on the
instance. No Lambda, no containers, no Kubernetes, no service mesh. The service
list is deliberately minimal because the assignment states that beyond
messaging, queuing, caching, databases, storage and VMs, nothing else is
allowed; that is why the database password lives in a mode-600 env file rather
than in Secrets Manager.

---

## Why PostGIS rather than a key-value store

The application's output is produced by two spatial operations, and neither is
a lookup:

**Clustering.** `ST_ClusterDBSCAN` over currently-inundated sensors, projected
to EPSG:2263 (NAD83 / New York Long Island, US survey feet) so the epsilon
parameter is a real distance. Clustering in raw WGS84 degrees would make the
same epsilon mean different distances at different latitudes.

**Correlation.** `ST_Intersects` between a zone hull and active NWS warning
polygons, backed by GiST indexes on both.

Implementing DBSCAN and point-in-polygon in the worker would be more code,
slower, and harder to defend. The database does the work rather than storing
the result.

### The degenerate-geometry trap

A two-sensor cluster convex-hulls to a `LINESTRING` and a one-sensor cluster to
a `POINT`. Both violate a `geometry(Polygon)` column and render as nothing on a
map. `current_clusters()` buffers the hull by 492 feet (about 150 m) in the
projected CRS, which forces a polygon and gives the zone a sensible footprint.
Verified: see below.

---

## Verification

The clustering function was validated against a fixture of real NYC coordinates
(`tests/fixture_clusters.sql`) on PostgreSQL 16 with PostGIS 3.4.

| Case | Expected | Result |
|---|---|---|
| 4 wet sensors, SE Queens, within ~700 m | one zone | `{q1,q2,q3,q4}`, 0.437 km², `ST_Polygon` |
| 2 wet sensors, Red Hook (degenerate hull) | one **polygon**, not a linestring | `{b1,b2}`, 0.125 km², `ST_Polygon` |
| 1 wet sensor alone in the Bronx | excluded as DBSCAN noise | excluded |
| Dry sensor inside a wet zone's footprint | not absorbed into the zone | excluded |
| NWS polygon over Queens only | Queens zone correlates, Red Hook does not | correct |

---

## Detection parameters

Every one of these is a decision, not a constant. All live in `curbline/config.py`
and are recorded in each S3 audit object alongside the decision they produced.

**The depth thresholds are source-specific and derived from `CURBLINE_SOURCE`,
not fixed** (D-005). FloodNet reports standing water on a road surface; USGS
reports gage height, a stage rise above an arbitrary local datum, which is a
different physical quantity on a different scale. Applying one source's numbers
to the other is wrong by roughly an order of magnitude, and it shipped that way
until 2026-08-28 (E-014). `SOURCE` defaults to `usgs`, so the right-hand column
is what an unconfigured run actually uses.

| Parameter | FloodNet | USGS (default) | Rationale |
|---|---|---|---|
| `DEPTH_THRESHOLD_CM` | 5.0 | 60 | Detection floor: above sensor noise, below the point a car is affected |
| `ADVISORY_THRESHOLD_CM` | 10 | 90 | Roughly half a curb. A driver notices, a pedestrian avoids it |
| `WARNING_THRESHOLD_CM` | 20 | 120 | Most of a curb. A passenger vehicle is genuinely at risk |
| `CLUSTER_EPS_FT` | 1640 (~500 m) | same | Roughly a long NYC block, measured in EPSG:2263 so it is constant across the city |
| `CLUSTER_MIN_SENSORS` | 2 | same | One wet sensor is a reading; two adjacent is a street |
| `READING_WINDOW_MINS` | 15 | same | A reading older than this is not "current" |

A newly detected zone enters state `forming` and does **not** notify. It must
survive a second cycle to reach `active`. This suppresses single-cycle sensor
noise at a cost of roughly one poll interval of latency on a real event.

---

## Reliability properties

**Idempotency.** SQS delivers at least once. Every unit carries an `ingest_id`,
and the correlator claims it with `INSERT ... ON CONFLICT DO NOTHING`. A
duplicate delivery is a skip, not an error.

**Ordering of side effects.** The dispatcher writes the S3 audit record
**before** publishing to SNS. If S3 fails, the handler raises, the message is
not deleted, and the unit retries. No advisory is issued without a durable
record of the evidence behind it.

**Cache degradation.** Every Redis read is wrapped. A cache miss, a timeout, or
a fully unreachable cluster all fall through to a direct Postgres read. The
system is correct with the cache cold or down; it is only slower.

**Failure isolation.** A message is deleted only after its handler returns
cleanly. After five failed receives the redrive policy moves it to a
dead-letter queue rather than looping forever.

**Graceful shutdown.** Each worker traps SIGTERM and finishes the message it is
holding before exiting, so `systemctl restart` does not lose in-flight work.

---

## Data sources

| Source | Access | Cadence |
|---|---|---|
| FloodNet NYC | Data request form, non-commercial license | 1 minute |
| USGS Water Data API | No key | ~15 minutes |
| NWS `api.weather.gov` | No key, User-Agent required | On issuance |

`CURBLINE_SOURCE` selects the sensor source at runtime. All three implement one
interface, so switching is a config change and a service restart.

**Known limitation.** Many NWS products are zone-based rather than storm-based
and arrive with `geometry: null`, referencing UGC zones instead. Those are
stored with a NULL polygon and cannot participate in spatial correlation. This
is a real gap in coverage, not a defect in the pipeline.

---

## The console

The dashboard is a staff gauge, a map, and an advisory queue.

A staff gauge is the graduated ruler used to read water depth off a wall or a
piling, and it is the instrument this entire domain is read through. The left
rail is that gauge: every wet sensor in the network is a tick at its true depth,
so the city's whole water distribution reads as one scale, and the rail fills as
the city floods. Reference lines are subject-true rather than arbitrary, which
is why 15 cm is marked as curb height.

Colour encodes depth on a floodwater ramp from pale silt to deep murk, shared
by the rail, the map circles and the zone fills so the three never disagree.
Alarm colour is sodium vapour, which is what NYC streetlight actually looks
like on wet pavement, escalating to road-flare orange at warning.

The status bar carries queue depth, cache hit rate and database reachability,
because the distributed system is the point and queue depth is the clearest
visible proof the stages are decoupled.

The hit rate needed a transport to be true (E-019). `cache.STATS` is a
module-level counter, so it counts only what the process holding it did.
`cache.sensor()` is called by the correlator, while the API served its own
always-empty copy and rendered it as a 0% hit rate on a healthy cache. The
counters are now published to Redis under `stats:cache:*`, drained from the
worker loop between batches so no roundtrip is added to a cache read to measure
that read, and the API reads the aggregate. An unknown hit rate reads `n/a`,
never `0%`, because those are different facts.

### Building the console without AWS

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python api/mock_server.py     # http://localhost:8000
```

The mock server runs a scripted three-minute storm through the identical
payload contract, so the UI can be built and reviewed while provisioning runs
in parallel. It is a development tool and is never used for evidence.

## Running it

On a fresh Ubuntu EC2 instance with an IAM instance role attached:

```bash
git clone <repo> ~/curbline
cd ~/curbline
# CURBLINE_ADMIN_CIDR is YOUR address, read from your own machine, not
# from this instance. curl on the instance returns the instance. See E-008.
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=203.0.113.7/32 ./infra/bootstrap.sh
```

That provisions every managed service, loads the schema, installs the systemd
units, and starts all four processes.

Credentials come from the EC2 instance role. There are no AWS keys in this
repository, in `.env`, or anywhere on disk.

### Teardown

Run `infra/teardown.py` when the evidence screenshots are captured. RDS and
ElastiCache bill by the hour whether or not anything is using them.

---

## Repository layout

```
CLAUDE.md               Project instructions for Claude Code (read first)
VERSION_ROADMAP.md      Gated roadmap, v0.5.0 through v2.0.0
DECISIONS.md            14 decisions, each with a flip condition
ERRORS.md               30 logged defects, mostly fixed. Do not rediscover.
TESTS.md                Test registry and stated coverage gaps
RETENTION.md            Cold-defense drill log and flip watch
CHANGELOG.md            Version history
TIMELOG.md              Hours against gates
infra/SETUP.md          IAM role + EC2 setup. Do this FIRST.
infra/iam-policy.json   Scoped instance-role policy
infra/provision.py      Creates all managed services, in dependency order
infra/teardown.py       Deletes everything. Cost control, not optional.
infra/bootstrap.sh      One-shot host setup
sql/schema.sql          Tables, GiST indexes, current_clusters(), alert_for_hull()
curbline/config.py      All tunable parameters, one place
curbline/db.py          Postgres access, spatial queries, read models
curbline/cache.py       Read-through cache with graceful degradation
curbline/aws.py         Clients, the SQS worker loop, SIGTERM handling
curbline/sources.py     Pluggable reading sources
workers/collector.py    Component A
workers/correlator.py   Component B
workers/dispatcher.py   Component C
api/server.py           Presentation layer (not a required component)
api/mock_server.py      Runs the console with no AWS, for frontend work
web/                    Console: index.html, style.css, app.js
data/capture_replay.py  Records a live storm for demo replay
systemd/                Service units
tests/                  Clustering fixture and 91 unit tests
docs/REPORT.md          The report, mapped to the rubric
docs/evidence/          CLI captures and S3 audit records from the live run
DEMO.md                 Run book and evidence checklist
```
