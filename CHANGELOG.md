# CHANGELOG

Format: [Keep a Changelog](https://keepachangelog.com/). Versions follow
`VERSION_ROADMAP.md`.

---

## [Unreleased]

Evidence and documentation work between the v0.5.0 run and the v0.7.0 capture
session. No pipeline behaviour changed except the cache stats transport.

### Fixed
- **E-019, the dashboard cache hit rate.** Counters are published to Redis under
  `stats:cache:*` and drained from the worker loop between batches, so the API
  reports the pipeline's cache effectiveness rather than its own always-empty
  copy. `hit_rate` is null for unknown, never 0.0. See D-014.
- `scripts/gate-check.sh` looked only for `.venv/bin/python` and fell through to
  a bare `python3` without the dependencies, reporting 24 test failures on a
  clean tree. It now checks both venv layouts and hard blocks when the
  interpreter cannot import pytest, because a gate must not close on an unrun
  suite.

### Changed
- README's detection parameters table listed FloodNet's 5/10/20 as the default
  while `CURBLINE_SOURCE` defaults to `usgs`. That is E-014 reproduced in the
  documentation after being fixed in the code. Both columns are now shown with
  the default named.
- README corrected on the cache hit rate, test count, decision count, and the
  description of `docs/REPORT.md`, which is written rather than a skeleton.
- `DEMO.md` and Appendix C of the report now share 22 fixed screenshot
  filenames, so captures drop in without renaming and the run book cannot drift
  from the report.
- The report's course header no longer carries a bracketed placeholder.

### Fixed by the boundary audit
E-013, E-017 and E-019 were read as one shape rather than three bugs: code
correct in isolation and wrong across a boundary the suite cannot see. Auditing
each boundary specifically found six more.

- **E-020.** No zone ever receded or closed. `next_state` receded on
  `sensor_count < CLUSTER_MIN_SENSORS`, which cannot hold, because
  `current_clusters()` is called with `p_minpoints := CLUSTER_MIN_SENSORS` and
  discards noise. Recession is now swept for on a timer, because a zone stops
  flooding by disappearing and no queue message can carry an absence.
- **E-021.** Each zone issued at most one advisory ever. The duplicate guard
  compared state and corroboration but not level, under a comment saying it
  compared level. Rising water crossing a threshold never notified.
- **E-022.** `web/app.js` hardcoded FloodNet's thresholds into the map
  expressions, so on the `usgs` default the map and the depth rail disagreed.
- **E-023.** `/api/health` returned `ok` with all three graded components dead.
  Workers now heartbeat through Redis.
- **E-024.** Dead-letter queue depth was computed and discarded, under a
  docstring saying it was reported. `provision.py` never wrote the DLQ URLs.
- **E-025.** A hand-run `current_clusters()` answers at FloodNet calibration
  whatever the source. Documented loudly; the committed evidence query now
  passes its parameters explicitly.

E-014, E-022 and E-025 are the same defect in three layers. D-005 said
thresholds move with the source; the dispatcher, the frontend and the SQL
defaults each did not implement it, and each fix stopped one layer short.

### Added
- 27 tests. 31 to 58. `should_notify`, `sweep_state` and `health_status`
  extracted as pure functions so the decisions they encode are testable with
  reachable inputs, which is the direct lesson of E-020: the suite asserted
  `next_state("active", 1) == "receding"` and passed, on an argument the
  pipeline cannot produce.
- Limitations 10 through 12 in the report: the USGS baseline lost on restart,
  unvalidated reading timestamps, and an audit record attesting the wrong
  process's thresholds. All found by the audit, none fixed.
- 5 tests in `TestCacheStatsTransport`. 31 to 36.
- D-014, the cache counter transport decision, with its flip condition.
- `docs/evidence/api-state.json` and `docs/evidence/cli/current-clusters-query.sql`
  from the 2026-08-28 capture session.

---

## [0.5.0] — 2026-08-28

Infrastructure live. First execution of the pipeline against real managed
services: RDS PostgreSQL 18 with PostGIS 3.6.3, ElastiCache, SQS, SNS, S3 and
EC2. Nine defects found and fixed. None was reachable by the existing test
suite, because the suite is moto throughout and had never run SQL against
PostGIS or exercised the dispatcher's database types.

### Added
- `infra/account-setup.sh`. The one-time account bootstrap `SETUP.md` described
  as unscriptable. Runs in CloudShell so no long-lived access key is created,
  and launches the probe instance before the IAM work so a new-account
  verification hold or a zero vCPU quota surfaces in the first minute.
- `config.thresholds_for(source)`. D-005 was logged on 2026-08-27 and never
  implemented.
- `TestSourceCalibration`, `TestZoneLookupTypes`, `TestSensorCacheDivergence`.
  26 tests to 31.
- v0.5.0 recall questions in `RETENTION.md`.

### Fixed
- **E-009** Ubuntu 24.04 dropped `awscli` from its archive; `bootstrap.sh` now
  installs CLI v2 from AWS directly.
- **E-010** The policy granted `s3:PutPublicAccessBlock`, an operation name that
  is not an IAM action. IAM validates neither, so it applied and authorized
  nothing.
- **E-011** A fresh account has no RDS or ElastiCache service-linked role. RDS
  reports that as "Missing necessary credentials", which points at the caller.
- **E-012** `psql` paged the PostGIS version row and blocked the script on a
  keypress, immediately after printing the line the operator was told to expect.
- **E-013** `double precision` to `numeric` is an assignment cast, not an
  implicit one, so `current_clusters(...)` was reported as nonexistent when it
  existed.
- **E-014** `SOURCE` defaulted to `usgs` while thresholds defaulted to FloodNet's
  5/10/20, contradicting D-005. The first live dashboard read 17 of 28 sensors
  wet at 151.8 cm. `api/server.py` was sending three of the four thresholds as
  literals and needed the same fix.
- **E-015** `systemctl enable --now` does not restart a running unit, so after a
  reboot the workers held an RDS endpoint that teardown had deleted.
- **E-016** The sensor upsert was gated on a Redis hit, so a cache that outlived
  its rows made every insert violate a foreign key, forever, under redelivery.
- **E-017** The dispatcher keyed its previous-state lookup on `uuid.UUID` values
  from the database and looked them up with the string from the queue. No zone
  ever left `forming`, so no advisory had ever been built, audited to S3 or
  published to SNS in any run of this system.

### Changed
- `scripts/gate-check.sh` enumerates with `git ls-files` instead of walking the
  filesystem. It was reporting three hard blocks, all of them `moto` and
  `botocore` inside `.venv`.
- D-001's flip condition. It fired on "EC2 cannot reach RDS by end of build day
  one," which priced a data-layer rewrite as a swap. It now fires only if a
  second RDS in the default VPC security group still refuses an EC2 host in that
  same group.
- v0.5.0's abort condition is now a five-rung connectivity ladder. Rung 3, adding
  the default VPC security group to RDS and ElastiCache, removes the likeliest
  failure cause in about two minutes.
- Shell scripts are mode 755. They were committed 644, so a fresh clone failed
  on `./infra/bootstrap.sh`.

### Security
- `*.pem` and `files.zip` added to `.gitignore`. The EC2 private key was sitting
  untracked in the working tree with nothing preventing `git add -A`.
- AWS account id removed from `COSTS.md` before the repo was published.

### Note
Repo initialized under git at v0.4.1. Published at
https://github.com/brianonieal/Curbline on 2026-08-28.

---

## [0.4.1] — 2026-08-27

Documentation and process only. No source changes; the pipeline is byte
identical to 0.4.0.

### Added
- `COSTS.md`. Three services bill hourly against three separate 750-hour free
  tier clocks, and a forgotten teardown consumes an entire month's allowance.
  The two-day build is not the risk; forgetting is.
- `scripts/gate-check.sh`. Mechanizes the gate-close checklist. Hard blocks on
  failing tests, tests that reach a real AWS account, staged or tracked secret
  files, hardcoded credentials, banned technologies (Lambda, containers,
  orchestrators), and any decision missing a `Flips if:` line.

### Note
`MEMORY_CORRECTIONS.md` is referenced by `VERSION_ROADMAP.md` and `TIMELOG.md`
and is deliberately NOT in this repo. It is global across projects and lives at
the Syntaris root. Estimates in this repo remain uncalibrated until it exists.

---

## [0.4.0] — 2026-08-27

Code complete. Nothing in this release has executed against a real AWS account.

### Fixed
- USGS source reported raw gage height as flood depth, putting 10 of 12
  sampled gauges into permanent WARNING with no rain. Now reports rise above a
  per-site 10th-percentile baseline. See E-001.
- `provision.py` never opened tcp/8000 or tcp/22, leaving the dashboard
  unreachable from a browser. See E-004.
- Security group ordering deadlock: the provisioning host was not a member of
  the group its own rules referenced. `attach_sg_to_self()` resolves it via
  IMDSv2. See E-005.
- `bootstrap.sh` did not install `redis-tools`, `awscli` or `jq`, all of which
  `DEMO.md` instructs you to use.

### Added
- `infra/SETUP.md` and `infra/iam-policy.json`. The IAM role and EC2 instance
  creation were previously undocumented, which would have produced AccessDenied
  on the first API call.
- `api/mock_server.py`. Runs the full console with no AWS, breaking the
  dependency between frontend work and provisioning.
- `web/` console: MapLibre map, staff-gauge depth rail, advisory queue,
  component status bar.
- `api/server.py` read-only presentation layer with WebSocket fan-out.
- `infra/teardown.py`, previously referenced in the README but absent.
- `.gitignore` covering `.env` and `infra/stack.json`. The assignment requires
  a public repo and `provision.py` writes the RDS password to `.env`.
- 26 unit tests using moto.
- `DEMO.md` run book with a rubric-mapped screenshot checklist.
- `docs/REPORT.md` skeleton.
- Source-specific threshold guidance in `config.py`. See D-005.

### Verified at packaging
All Python compiles; 26/26 tests pass; `app.js` passes `node --check`;
`schema.sql` and the fixture load clean on PostgreSQL 16 / PostGIS 3.4 and
return the expected 2 zones; `bootstrap.sh` passes `bash -n`.

---

## [0.3.0] — 2026-08-27
Initial pipeline: three workers, PostGIS schema with `current_clusters()`,
read-through cache, SQS worker loop with idempotency and SIGTERM drain.
