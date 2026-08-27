# Consolidation note

2026-08-27. Four separate archives were merged into this single repository.

## What was merged

| Archive | Contents | Outcome |
|---|---|---|
| `All_files.zip` | nested `curbline-v0.4.0.zip`, 37 files | superseded, discarded |
| `Frontend_files.zip` | 14 loose files, no directory structure | 11 identical, 3 stale, discarded |
| `Roadmap_files.zip` | nested `curbline-v0.4.1.zip`, 47 files | **kept in full** |
| `Structural_files.zip` | 14 loose files, no directory structure | 8 identical, 6 stale, discarded |

Every file across all four archives was hashed and compared against the v0.4.1
tree. **Nothing was unique to the discarded archives.** Each file was either
byte-identical to its v0.4.1 counterpart or an older revision of it.

## Why the loose files could not simply be merged in

`Frontend_files.zip` and `Structural_files.zip` were flat. They had lost their
directory structure, so `db.py` in one and `db.py` in the other were different
revisions of `curbline/db.py` with no way to tell them apart by path. Both also
carried a `README.md`, from different points in the build.

More importantly, six of those files were stale in ways that reintroduce fixed
bugs:

- `sources.py` computed `float(value) * FT_TO_CM` with no baseline. That is the
  version that reports raw USGS gage height as flood depth and puts 10 of 12
  gauges into permanent WARNING on a dry day. See ERRORS.md E-001.
- `provision.py` had neither `allow_from_cidr` nor `attach_sg_to_self`, so the
  dashboard port stays closed and RDS refuses the provisioning host. See E-004
  and E-005.
- `config.py` lacked the source-specific threshold guidance. See D-005.
- `bootstrap.sh` did not install `redis-tools`, `awscli` or `jq`.
- `db.py` predated the presentation-layer read models, so `api/server.py`
  would fail on import.
- `.env.example` and `README.md` were earlier revisions.

## What this tree is

`curbline-v0.4.1.zip` unchanged, verified identical to the authoritative build.
47 files. Source code is byte-identical to v0.4.0; v0.4.1 added the governance
layer (VERSION_ROADMAP.md, CLAUDE.md, DECISIONS.md, ERRORS.md, TESTS.md,
RETENTION.md, TIMELOG.md, CHANGELOG.md, COSTS.md, scripts/gate-check.sh).

## Delete the four source archives

They contain no information that is not in this tree, and two of them contain
code that would undo fixes if copied over by hand.

Read order: `CLAUDE.md` -> `VERSION_ROADMAP.md` -> `infra/SETUP.md`.
