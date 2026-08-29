# CHANGELOG

Format: [Keep a Changelog](https://keepachangelog.com/). Versions follow
`VERSION_ROADMAP.md`.

---

## [Unreleased]

Evidence and documentation work between the v0.5.0 run and the v0.7.0 capture
session. No pipeline behaviour changed except the cache stats transport.

### Changed, 2026-08-29, pre-window corrections

Documentation only. No source file changed except `curbline/__init__.py`'s
version string. Made ahead of a hard-bounded 2-hour capture window, so every
item here is something that would have cost time or credibility inside it.

- **Version drift closed.** `CLAUDE.md` and `curbline/__init__.py` both read
  `0.4.1` while `VERSION_ROADMAP.md` had v0.7.0 open. `__init__.py` is the one
  that mattered: it is a runtime string that can surface in a console capture or
  a report header, not only a document a reader misinterprets. Historical
  references to 0.4.1 in `CHANGELOG.md`, `MANIFEST.txt`, `CONSOLIDATION.md`,
  `RETENTION.md`, `TIMELOG.md` and `.gitignore` are records of a past event and
  were deliberately left alone.
- **`TESTS.md` class table reconciles to 95 again.** It documented 16 classes
  totalling 67. Five classes were never registered
  (`TestRecessionIsDrivenByAbsence`, `TestAdvisorySuppression`,
  `TestHealthVerdict`, `TestWorkerHeartbeat`, `TestEnvFileContract`) and
  `TestSourceCalibration` was recorded at 3 against an actual 6. Four of the
  five cover E-020, E-021 and E-023, so the registry was missing most of the
  boundary audit; the fifth, `TestEnvFileContract`, covers the `.env` contract
  `bootstrap.sh` depends on and carries no E-number. A total row now makes the
  reconciliation checkable at a glance.
- **The moto claim was an overstatement and is corrected in all seven live
  assertions of it.** `mock_aws()` appears once in the suite, in the
  module-level `sqs_queue` fixture, and covers SQS in two tests; SNS and S3 are
  patched with `unittest.mock`, so `TestAuditOrdering` proves call ordering and
  nothing about the objects produced. The claim was corrected in `TESTS.md`,
  `CLAUDE.md`, `ERRORS.md` (the E-013 prevention note), `docs/REPORT.md`
  sections 6.2 and 7, in the header comment of `scripts/preflight.py`, and in
  the module docstring of `tests/test_pipeline.py`,
  which is the file a reader opens to check the registry and which carried the
  original sentence verbatim. The sealed historical entry at `CHANGELOG.md`
  under v0.5.0 is left alone, because it records what was believed then.
  Every corrected location now also states that no test executes against live
  PostgreSQL, so every line of SQL in the project is unexercised by the suite.
- **The venv trap is closed in the run book.** `TESTS.md`, `CLAUDE.md` and
  `infra/SETUP.md` each told the reader to run `.venv/bin/python`, and a fresh
  clone has no `.venv`, so the documented command failed on a new capture host.
  Each now carries the create-and-install line first. `CLAUDE.md` also stopped
  recommending bare `python3` for pytest, which contradicted `TESTS.md`'s own
  warning. `infra/SETUP.md`'s claim that gate-check defaults to bare `python3`
  was two commits stale, and its "26 passed" was two gates stale.
- **`DEMO.md`'s capture session is rewritten for a 2-hour window.** The teardown
  rehearsal is dropped, which also removes the hazard that rehearsing teardown
  destroys a confirmed SNS subscription along with its topic. Captures cut from
  22 to 11, chosen by rubric points with no committed substitute.
- **`docs/REPORT.md` Appendix C marks the 11 skipped captures rather than
  dropping them**, each with a reason and the committed substitute where one
  exists. Three have no substitute and say so. Screenshot paths now match where
  the run book tells you to save them.
- **Two arithmetic errors corrected in section 6.3.** It claimed twenty-five
  logged defects against an actual thirty-four, and described E-020 through
  E-034 as "six" when the range is fifteen.
- **E-035 added**, recording the dropped teardown rehearsal as an accepted risk
  with its mitigation, rather than leaving it as an undocumented schedule cut.
  Its line count was corrected before commit: commit `3c8c83f` added 98 lines
  and deleted 24, and the 122 in the diffstat bar is insertions plus deletions,
  not surviving lines.

### Caught by the pre-commit verification pass, listed because they were nearly shipped

The edits above were reviewed adversarially before commit. Seven defects in the
edits themselves were found and fixed. They are recorded rather than quietly
folded in, because the pattern in them is the same one E-013, E-017 and E-019
share: a correction applied in the place you were looking and not in the places
that repeat the claim.

- **The moto correction was applied in two files and asserted as complete.** It
  appeared in seven. The worst survivor was the module docstring of
  `tests/test_pipeline.py`, the file a reader opens to verify the registry.
- **`DEMO.md`'s rewritten run book invoked `.venv`-only scripts with bare
  `python3`**, in the same change whose stated purpose was closing that exact
  trap. `preflight.py` imports psycopg and `teardown.py` imports boto3, and
  `bootstrap.sh` installs neither outside the venv. Both would have died with
  `ModuleNotFoundError`, one of them at 0:25 and the other at teardown, which is
  the step whose failure keeps RDS billing.
- **Two Appendix C substitute claims were false.**
  `docs/evidence/cli/ec2-instance.txt` was cited as recording the attached IAM
  role and contains no role, profile or ARN. `sqs-ingest.json` and
  `sqs-zones.json` were cited as carrying queue depths that prove decoupling and
  both read `ApproximateNumberOfMessages: 0`. Both rows now say what the file
  actually contains.
- **Appendix C claimed eleven captures in the past tense** with no screenshot on
  disk in any branch. Rewritten to state scope rather than accomplishment, with
  an instruction to check the files before submitting.
- **The pre-teardown checklist still gated teardown on captures the same change
  had dropped**, including six AWS console shots and the status bar.
- **`CLAUDE.md` kept a test count of 26** while the same commit corrected that
  identical number to 95 in `infra/SETUP.md`.
- **`TESTS.md` named the wrong location for `mock_aws()`**, placing it inside
  `TestWorkerLoop` when it is in the module-level `sqs_queue` fixture, in a
  passage whose whole purpose was precision.

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
- **E-034.** `provision.py` wrote `stack.json` once at the very end, after a
  ten minute blocking wait on RDS. An interruption in that window left an
  instance billing hourly with nothing naming it, and the master password,
  generated in memory and written only in that same final block, gone entirely.
  The record is now checkpointed after every resource and the password is
  persisted before the instance that uses it exists.
- **E-033.** Teardown deleted the free resources first and the hourly-billing
  ones last, so an error on a queue deletion aborted the run with RDS and
  ElastiCache still running. It also treated any ElastiCache error as "gone",
  and unlinked `stack.json` before verifying anything, which is the worst
  state: live resources and no record of their names. Billable deletions now go
  first, verification gates the unlink, and the exit code carries the answer.
- **E-032.** The teardown gate could pass on a live billing stack. Its two
  `aws` calls carried no `--region`, so from the wrong region both return zero
  and v1.0.0 closes with RDS and ElastiCache still running. In the other
  direction, a failed call was compared `!= "0"` and reported as "requires
  teardown" against an already-empty account. Region pinned, and unknown is now
  its own state with an honest message.
- **E-031.** The fixture that fixes E-030 was never committed. `.gitignore`
  denies `data/*.json` with an allowlist, so a new fixture is ignored in
  silence and the capture host would have failed on a missing file. Now
  excepted, committed, and asserted by a test that checks git tracking rather
  than disk presence.
- **E-030.** `data/replay.example.json` cannot demonstrate that E-021 is fixed.
  Zone identity hashes the member sensor set, the wet set grows as the storm
  deepens, so every depth tier is a new zone and a correct system yields one
  advisory per zone: indistinguishable from the defect. The capture guidance had
  already been written to check for a ladder that fixture cannot produce.
  `data/replay.escalation.json` holds membership constant and climbs through
  every tier.
- **E-029.** An NWS alert with no `expires` field was stored and then never
  correlated, drawn or counted, because three queries filtered on
  `expires > now()`, which is NULL rather than true for a NULL column. The
  collector polls `/alerts/active`, so it was fetching active alerts and then
  discarding some as expired on the basis of a field the feed had not supplied.
  NULL now means "active while the feed still lists it", bounded by
  `updated_at`, with no fabricated expiry. An alert with no id is also skipped
  at the collector rather than dead-lettering after five receives.
- **E-028.** `observed_at` was inserted into a `TIMESTAMPTZ` column unparsed.
  A naive timestamp is read in the database server's timezone, and a shift
  larger than the reading window empties every cluster query silently. Latent
  only because both current sources happen to emit offsets. An offset is now
  required and normalised to UTC, enforced on the `Reading` dataclass so no
  source can bypass it.
- **E-027.** The S3 audit record named the four detection parameters by
  reading the dispatcher's own config, while clustering ran in the correlator
  with its copy. The correlator now carries them in the message and the record
  is split into `detection` and `advisory` blocks, each naming its provenance.
  The advisory thresholds were not recorded at all before, so the record could
  not explain the level it assigned.
- **E-026.** A collector restart mid-storm silently stopped detection at a
  site. The USGS baseline lived only in process memory and fell back to the
  current reading when the history fetch failed, making the rise exactly zero.
  Baselines now persist to Redis, and a site whose datum cannot be established
  has its readings withheld rather than published as a confident zero.

E-014, E-022 and E-025 are the same defect in three layers. D-005 said
thresholds move with the source; the dispatcher, the frontend and the SQL
defaults each did not implement it, and each fix stopped one layer short.

### Added
- 64 tests. 31 to 95. `should_notify`, `sweep_state` and `health_status`
  extracted as pure functions so the decisions they encode are testable with
  reachable inputs, which is the direct lesson of E-020: the suite asserted
  `next_state("active", 1) == "receding"` and passed, on an argument the
  pipeline cannot produce.
- Limitations 10 through 12 in the report. 11 and 12 are unfixed: unvalidated
  reading timestamps, and an audit record attesting the wrong process's
  thresholds. 10 is now the residue of E-026 rather than the defect itself, and
  names the trade taken: a site with no resolvable datum is absent from the map
  instead of appearing dry, because appearing dry is a claim the system cannot
  support and absence is not.
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
