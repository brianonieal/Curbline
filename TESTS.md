# TESTS

Registry per the testing skill. No test in this suite touches a real AWS
account or incurs spend. What actually stands in for what, stated precisely
because the previous wording claimed more coverage than exists:

- **moto covers SQS, in two tests.** `mock_aws()` appears exactly once in the
  suite, in the module-level `sqs_queue` fixture that `TestWorkerLoop` consumes.
- **SNS and S3 are not simulated at all.** They are patched out with
  `unittest.mock.patch.object` on `aws.publish` and `aws.write_audit`. So
  `TestAuditOrdering` proves the dispatcher calls them in the right order and
  proves nothing about whether a valid S3 object or SNS message is produced.
- **Zero tests execute against live PostgreSQL.** Every database call is
  patched, so **all SQL in this project is unexecuted by this suite**: both
  stored functions, the `open_zones()` lateral, and every write path. The
  fixture below is the only thing that runs real SQL and it is run by hand.

That gap is not incidental. E-013 and E-017 both passed every test and both
broke the system in production. v1.2.5 exists to close it.

**Current: 95 Python passing, 0 failing. Plus 18 console checks in
`tests/console_smoke.js`, run by `gate-check.sh`.**

The console had no automated coverage at all until 2026-08-28, and three of the
eleven captures in the reduced evidence set are the console. `console_smoke.js` loads
`web/app.js` into a stubbed DOM and MapLibre and drives `apply()` through the
payload shapes the API actually produces, including the degraded ones: an
unreachable cache, a database down, a queue probe returning -1, a null sensor
depth, a forming zone. A thrown exception fails the gate.

It was verified to fail: no-opping `applyThresholds()` breaks four of its
checks. A harness that cannot catch the regression it was written for is the
E-020 mistake in a different language.

```bash
# A fresh clone has no .venv, so on a new host this line is not optional.
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
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
| `TestSourceCalibration` | 6 | D-005 threshold mapping per source, that every buildable source has its own thresholds, and that an unrecognised source falls back to the sensitive calibration and is rejected rather than guessed |
| `TestZoneLookupTypes` | 1 | E-017: a UUID-typed zone_id from the database still matches the string in the queue body, so a forming zone promotes |
| `TestSensorCacheDivergence` | 1 | E-016: a foreign key violation repairs the sensor row and retries the insert once |
| `TestEscalationFixture` | 5 | E-030: replay.example.json cannot produce an advisory ladder because membership changes per tier; these assert replay.escalation.json can |
| `TestAlertIngestGuards` | 6 | E-029: an alert with no id is skipped at the collector instead of dead-lettering, and a missing expiry is published as NULL rather than fabricated |
| `TestReadingTimestamps` | 8 | E-028: observed_at must carry a UTC offset, enforced on the Reading dataclass so no source can bypass it; a bad gauge skips its reading, not the poll |
| `TestAuditProvenance` | 3 | E-027: the audit record names the parameters the correlator actually clustered with, and discloses a fallback rather than substituting silently |
| `TestUSGSBaselinePersistence` | 9 | E-026: the baseline survives a restart via Redis, a failed lookup backs off, and a site with no datum is withheld rather than published as a zero rise |
| `TestCacheStatsTransport` | 5 | E-019: counters publish to Redis and clear, a failed flush retains the deltas rather than under-reporting the incident that caused it, and an unknown hit rate reads null rather than 0.0 |
| `TestRecessionIsDrivenByAbsence` | 7 | E-020: a zone the pipeline can actually produce can never recede on sensor count, so recession is swept on a timer instead. A stale active zone recedes, a stale receding zone closes, a closed zone is left alone, and the staleness window exceeds the reading window |
| `TestAdvisorySuppression` | 6 | E-021: an escalation notifies even when state and corroboration are unchanged, an unchanged level stays suppressed, new corroboration notifies, and a forming zone never notifies |
| `TestHealthVerdict` | 5 | E-023: one stopped worker degrades the whole system, every worker dead is not `ok`, unknown liveness does not degrade, and a database outage outranks everything |
| `TestWorkerHeartbeat` | 4 | E-023: a heartbeat key expires, a silent worker reads as not live, an unreachable cache reports unknown rather than dead, and `beat()` never raises into the pipeline |
| `TestEnvFileContract` | 3 | The `.env` contract `bootstrap.sh` depends on: the generated password's alphabet is safe for an unquoted shell assignment, excludes what RDS rejects, and round-trips through dotenv parsing |
| **Total** | **95** | Reconciles to the headline count above. If these disagree, the table is stale, not the suite |

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
