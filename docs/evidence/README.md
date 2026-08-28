# Evidence

## audit/

Six advisory audit records pulled directly from
`s3://curbline-audit-<account-id>/advisories/` on 2026-08-28, before the bucket
was removed. These are the objects the dispatcher wrote, unmodified.

Each one carries the zone id, lifecycle state, advisory level, member sensor
ids, maximum depth, the NWS alert id or null, the full hull geometry, **the
threshold set in force at the moment the decision was made**, and the dispatched
message text.

They are committed rather than screenshotted deliberately. A screenshot of a
JSON file proves that a JSON file was displayed. The file itself is the artifact
the system produced, it is checkable against `sql/schema.sql` and
`workers/dispatcher.py`, and it satisfies the `DEMO.md` item calling for an
audit object opened to show the recorded thresholds.

Provenance: the sensor readings that produced these were synthetic, replayed
from `data/replay.example.json`. Everything downstream of that injection point,
including these records, was produced by the live stack. See report limitation 9.

Verified free of credentials before commit: no password, key, token or
connection string appears in any of the six.
