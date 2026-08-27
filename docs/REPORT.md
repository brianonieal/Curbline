# Curbline: architecture and implementation report

> Skeleton. Every heading below maps to something the rubric scores. Bracketed
> notes say what belongs there and are deleted as sections are written.

**Author:** Brian Onieal
**Course:** [course number and title]
**Submitted:** [date]
**Repository:** [github url]

---

## 1. Problem and business value
*Rubric: Real-World Problem Relevance, 10 pts*

[Ida, September 2021, eleven deaths in NYC basement apartments. The gap was not
rainfall data but street-level depth aggregated into something actionable. State
who the user is (a borough emergency coordinator), what decision they make
(close a street, stage barricades, notify residents), and what they use today.]

### 1.1 Why a zone rather than a reading
[One sensor reporting 8 cm is a reading. Four adjacent sensors reporting water
simultaneously is a flooding street. This distinction is the product.]

### 1.2 What exists already
[FloodNet's dashboard shows sensors. NWS shows warning polygons. Neither
produces zones, and neither attaches a recommended action. Be honest that this
is a gap between two existing tools, not a new category.]

---

## 2. Architecture
*Rubric: Report Completeness and Architecture Detail, 15 pts*

### 2.1 Component diagram
[Reuse the README diagram. Label the three required components explicitly and
state that the API process is the presentation layer and is not one of them.]

### 2.2 The three components
[One subsection each: collector, correlator, dispatcher. For each: input,
output, what it owns, what it does not.]

### 2.3 Technology component mapping
| Assignment component | Service | Why this one |
|---|---|---|
| Queuing | Amazon SQS | |
| Database | Amazon RDS PostgreSQL + PostGIS | |
| Caching | Amazon ElastiCache for Redis | |
| Messaging | Amazon SNS | |
| Storage (extra) | Amazon S3 | |
| Compute (extra) | Amazon EC2 | |

[Four of four used; three required. Note the service list is deliberately
minimal because the assignment permits nothing beyond these categories, which
is why the database credential is in a mode-600 env file rather than Secrets
Manager.]

### 2.4 Constraint compliance
[No Lambda. No containers, Kubernetes or service mesh. Every data service is
managed by AWS; nothing is self-installed on the instance. Say this plainly.]

---

## 3. Component interaction analysis
*Rubric learning objective: evaluate impact on scalability, performance, efficiency*

### 3.1 Why queues rather than direct calls
[Backpressure, independent failure, independent restart. Cite observed queue
depth from your own screenshots.]

### 3.2 What the cache actually buys
[Sensor metadata is read on every message and changes almost never; that ratio
is the justification. Report your measured hit rate. Explain the degradation
path: every read falls through to Postgres, so a dead cache is slower, not
wrong. Reference the deliberate-failure screenshot.]

### 3.3 Why the spatial work is in the database
[ST_ClusterDBSCAN and ST_Intersects with GiST indexes, versus implementing
DBSCAN and point-in-polygon in the worker. Include the EPSG:2263 projection
reasoning: clustering in raw degrees makes epsilon mean different distances at
different latitudes.]

### 3.4 Where this breaks at scale
[Be specific and honest. The correlator re-runs clustering over all current
readings, which is fine at hundreds of sensors and wrong at hundreds of
thousands. Name what you would change: spatial partitioning by borough,
incremental clustering, or a windowed materialized view.]

---

## 4. Data
### 4.1 Sources
[FloodNet: cadence, license, access process. USGS: the modernized Water Data
API and why not the legacy host. NWS: no key, User-Agent required.]

### 4.2 The null-geometry problem
[Zone-based NWS products carry no polygon. Quantify how many you observed.]

### 4.3 Detection parameters
| Parameter | Value | Reasoning |
|---|---|---|
| Depth threshold | 5.0 cm | |
| Cluster radius | 1640 ft (~500 m) | |
| Minimum sensors | 2 | |
| Reading window | 15 min | |

[These are decisions, not constants. Defend each one.]

---

## 5. Reliability
### 5.1 Idempotency
[At-least-once delivery, the ingest_id conditional insert, duplicate as skip.]

### 5.2 Ordering of side effects
[Audit before notify. Why that order and what it guarantees.]

### 5.3 Failure isolation
[Delete-after-success, visibility timeout, dead-letter after five receives.]

### 5.4 Graceful shutdown
[SIGTERM drain so restarts do not lose in-flight work.]

---

## 6. Verification
### 6.1 Clustering validation
[The fixture results table: four-sensor Queens cluster, the two-sensor
degenerate-hull case, noise rejection, dry-sensor exclusion, alert correlation.]

### 6.2 Unit tests
[26 tests, moto for AWS, no test touches a real account. Name the four paths
covered per worker.]

### 6.3 Evidence
[Screenshot index with a one-line caption each.]

---

## 7. Limitations
[Copy from DEMO.md and expand. This section costs nothing and is the difference
between a report that reads as finished and one that reads as sold.]

---

## 8. What I would do differently
[Zone identity by spatial overlap rather than membership hash. Calibration
against observed flooding. Handling zone-based alerts via UGC lookup.]

---

## Appendix A: Repository layout
## Appendix B: Provisioning and teardown
## Appendix C: Full screenshot set
