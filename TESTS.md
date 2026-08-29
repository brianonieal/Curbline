# TESTS

Registry per the testing skill. No test in this suite touches a real AWS
account or incurs spend: moto stands in for SQS, SNS and S3, and the database
layer is stubbed.

**Current: 76 passing, 0 failing.**

```bash
.venv/bin/python -m pytest tests/ -q
```

Use the venv interpreter, not bare `python3`. A bare interpreter lacks
boto3, moto and psycopg and reports 18 failed with 4 errors, which looks
like a broken suite and is not one. Same trap as the one `infra/SETUP.md`
documents for `scripts/gate-check.sh`.

---

## Unit suite — `tests/test_pipeline.py`

| Class | Tests | Covers |
|---|---|---|
| `TestZoneIdentity` | 4 | Hash stability, order independence, the D-003 membership tradeoff asserted as a property, UUID validity |
| `TestAdvisoryLadder` | 8 | Threshold boundaries, and that NWS corroboration never lowers a level |
| `TestLifecycle` | 4 | forming → active → receding → active transitions |
| `TestCacheDegradation` | 4 | Cold cache, unreachable cache, corrupt entry, hit path skips the loader |
| `TestWorkerLoop` | 2 | Delete only after success; failed handler leaves the message for retry |
| `TestAuditOrdering` | 2 | S3 write precedes SNS publish; failed audit blocks the notification |
| `TestAlertFiltering` | 2 | Null-geometry alerts survive ingest; non-flood events dropped |
| `TestSourceCalibration` | 3 | D-005 threshold mapping per source, and that an unrecognised source falls back to the sensitive calibration |
| `TestZoneLookupTypes` | 1 | E-017: a UUID-typed zone_id from the database still matches the string in the queue body, so a forming zone promotes |
| `TestSensorCacheDivergence` | 1 | E-016: a foreign key violation repairs the sensor row and retries the insert once |
| `TestReadingTimestamps` | 8 | E-028: observed_at must carry a UTC offset, enforced on the Reading dataclass so no source can bypass it; a bad gauge skips its reading, not the poll |
| `TestAuditProvenance` | 3 | E-027: the audit record names the parameters the correlator actually clustered with, and discloses a fallback rather than substituting silently |
| `TestUSGSBaselinePersistence` | 7 | E-026: the baseline survives a restart via Redis, a failed lookup backs off, and a site with no datum is withheld rather than published as a zero rise |
| `TestCacheStatsTransport` | 5 | E-019: counters publish to Redis and clear, a failed flush retains the deltas rather than under-reporting the incident that caused it, and an unknown hit rate reads null rather than 0.0 |

## Integration fixture — `tests/fixture_clusters.sql`

Real NYC coordinates against live PostgreSQL 16 / PostGIS 3.4.

| Case | Expected | Verified |
|---|---|---|
| 4 wet sensors, SE Queens, within ~700 m | one zone | 0.437 km², `ST_Polygon` |
| 2 wet sensors, Red Hook | polygon, not linestring | 0.125 km², `ST_Polygon` |
| 1 wet sensor, isolated in the Bronx | rejected as DBSCAN noise | excluded |
| Dry sensor inside a wet zone footprint | not absorbed | excluded |
| NWS polygon over Queens only | Queens correlates, Red Hook does not | correct |

```bash
createdb curbline_test
psql -d curbline_test -f sql/schema.sql
psql -d curbline_test -f tests/fixture_clusters.sql
psql -d curbline_test -c "SELECT * FROM current_clusters();"
```

---

## Coverage gaps, stated rather than hidden

Not covered by automated tests:

- `infra/provision.py` and `infra/teardown.py`. Executed against a real account
  for the first time on 2026-08-28 and still carry no automated test. They are
  the riskiest code in the repo and five of the nine v0.5.0 defects were in them
  or in the policy they depend on.
- `api/server.py` WebSocket broadcast fan-out.
- The frontend. Structurally validated (`node --check`, HTML parse, id contract,
  CSS variable coverage) but never rendered in a browser.
- `USGSSource` baseline resolution. Verified manually against the live API on
  2026-08-27; no regression test, because a test would either hit the network
  or assert against a fixture that could drift from the real schema.

## Adding tests

Write the failing test before the code, per the testing skill. Every new worker
path needs four cases: happy path, duplicate delivery, downstream failure, and
cold cache.
