# CHANGELOG

Format: [Keep a Changelog](https://keepachangelog.com/). Versions follow
`VERSION_ROADMAP.md`.

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
