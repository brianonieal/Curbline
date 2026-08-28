# CHANGELOG

Format: [Keep a Changelog](https://keepachangelog.com/). Versions follow
`VERSION_ROADMAP.md`.

---

## [Unreleased]

Nothing yet. v0.6.0 opens on pipeline behaviour.

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
