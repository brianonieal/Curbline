# CURBLINE — VERSION ROADMAP

**Current:** v0.4.1 (code complete, nothing executed against AWS)
**Authority:** this file governs from v0.5.0 forward. Gates close in order.

---

## Estimate honesty

These are raw estimates. Build Rules v10 calls for calibration against
`MEMORY_CORRECTIONS.md`, and that file was not available when this roadmap was
written, so **no correction factor has been applied.** If prior reflexion
entries show systematic bias on infrastructure gates, apply it before trusting
Phase A timings. Treat every number below as uncalibrated.

The one estimate to distrust most is v0.5.0. First-time AWS provisioning is the
gate that historically runs long, and the range reflects that.

---

## Phase structure

**Phase A (v0.5.0 → v1.0.0)** is the graded submission. Hard deadline, no
scope negotiation. Everything else is optional.

**Phase B (v1.1.0 → v1.5.0)** hardens the detector. Every gate here corresponds
to a limitation that must be written into the report anyway, so documenting
them costs nothing extra and gives the report a credible future-work section.

**Phase C (v1.6.0 → v2.0.0)** changes the product's category: from telling you
a street *is* flooding to telling you it is *about to*.

### The strongest case this roadmap is wrong

Phases B and C will probably never happen. Coursework gets submitted and
abandoned, and nine gates past the deadline is planning theater that makes the
document feel more serious than the project. If Curbline is a class assignment
and nothing more, everything after v1.0.0 is wasted writing.

### The strongest case it is right

The Phase B items are not invented future work. They are the exact limitations
that already exist in the code and already have to appear in the report. Naming
them as gates converts a list of weaknesses into a plan, which is the
difference between a report that reads as unfinished and one that reads as
scoped. Phase C only matters if Curbline becomes a portfolio piece, and that is
a decision to make after submission, not before.

**Decide at v1.0.0 close, not now.** Do not start Phase B work before the
submission is in.

---

# PHASE A — SUBMISSION

## v0.5.0 — Infrastructure live

**Goal:** every managed service exists and the EC2 host can reach the ones
inside the VPC.

**Entry:** `infra/SETUP.md` complete. `aws sts get-caller-identity` on the
instance returns an assumed-role ARN.

**Scope**
- Run `infra/bootstrap.sh`
- `provision.py` creates security groups, SQS, SNS, S3, RDS, ElastiCache
- `schema.sql` loads, PostGIS extension enabled
- Security group self-attach confirmed

**Not in scope:** any application logic, any UI work.

**Exit criteria**
- [x] `psql -c "SELECT PostGIS_Full_Version();"` succeeds from EC2
- [x] `redis-cli -h $CURBLINE_CACHE_HOST ping` returns PONG from EC2
- [x] All four SQS queues exist and are listable
- [ ] SNS topic exists with a confirmed email subscription
- [x] S3 audit bucket exists with public access blocked
- [x] `http://<instance-ip>:8000` is reachable from Brian's browser
- [ ] Console screenshot captured for each of the six services

**Estimate:** 2 to 5 hours. The spread is the point.

**Connectivity ladder.** Replaces the original single abort condition, which
fired far too early. See D-001 for the repricing.

1. `bootstrap.sh` provisions and the exit criteria pass. Expected path.
2. Connectivity fails. Confirm `attach_sg_to_self` actually ran (E-005), then
   check subnet routing and that the instance has a public IP.
3. Put RDS and ElastiCache into the **default** VPC security group alongside
   their own groups. The default group permits all traffic between its own
   members, `infra/SETUP.md` launches the instance into it, and
   `attach_sg_to_self` adds `curbline-app` without removing it, so the host is
   still a member. Two minutes, and it removes custom security group
   misconfiguration, the likeliest cause, from the problem entirely.
   Both modify calls REPLACE the group list rather than append to it. Pass the
   existing group and the default group together or you silently drop
   `curbline-db`.
4. Re-provision RDS from scratch rather than keep debugging it. Roughly twenty
   minutes, mostly waiting.
5. Only if a second RDS in the default group still refuses an EC2 host in that
   same group is this a real VPC or account problem. That is the D-001 flip, and
   only there does DynamoDB get considered, with the fixture and the report
   section priced in.

If rung 3 is still in place when evidence is captured, say so in the report. It
widens the data tier from "only the app tier on 5432" to "any member of the
default group," which is not public but is not what a least-privilege claim
describes.

---

## v0.6.0 — Pipeline flowing

**Goal:** a reading enters the collector and an advisory leaves the dispatcher,
with an audit record in S3.

**Entry:** v0.5.0 closed.

**Scope**
- All three workers running under systemd
- Readings persisting to `readings`
- Clustering producing rows from `current_clusters()`
- Dispatcher issuing at least one advisory end to end
- Thresholds set correctly for whichever source is live (see CLAUDE.md trap 2)

**Not in scope:** the console, the report.

**Exit criteria**
- [ ] `systemctl status 'curbline-*'` shows four active units
- [ ] `SELECT count(*) FROM readings` is non-zero and growing
- [ ] `SELECT * FROM current_clusters()` returns at least one zone
- [ ] At least one row in `zones` and one in `advisories`
- [ ] SNS email received
- [ ] S3 object present under `advisories/` with thresholds recorded inside
- [ ] Duplicate-delivery path verified: replay a message, confirm the skip
- [ ] Prior gate still holds

**Estimate:** 3 to 4 hours.

**Note:** to force a zone without waiting for weather, drop the threshold
temporarily. Restore it before capturing any evidence. A zone produced from
noise is fine for proving wiring and dishonest in a screenshot.

---

## v0.7.0 — Console live

**Goal:** the dashboard renders real pipeline state over WebSocket.

**Entry:** v0.6.0 closed.

**Scope**
- `api/server.py` serving under systemd
- Map drawing sensors, zones, and NWS polygons
- Depth rail populated from live readings
- Advisory queue populated
- Status bar showing queue depth, cache hit rate, database reachability

**Not in scope:** new visual design. The design is done; this gate wires it.

**Exit criteria**
- [ ] Console loads over the public IP
- [ ] Map shows at least one zone hull drawn as a polygon
- [ ] Depth rail shows ticks at correct heights and the water fill rises
- [ ] Advisory card appears and clicking it flies the map to that zone
- [ ] Status bar shows a non-zero cache hit rate
- [ ] **Degradation proven:** stop ElastiCache, reload, confirm the console
      still works with the cache pip amber. Screenshot both states.
- [ ] Prior gates still hold

**Estimate:** 2 to 3 hours, assuming the frontend was already exercised against
`api/mock_server.py`.

---

## v0.8.0 — Evidence and report

**Goal:** the submission package is complete except for the push.

**Entry:** v0.7.0 closed.

**Scope**
- Full screenshot set per `DEMO.md`
- `docs/REPORT.md` written out from the skeleton
- README accurate against what was actually built
- Limitations section written honestly

**Exit criteria**
- [ ] Every checkbox in the `DEMO.md` screenshot checklist ticked
- [ ] REPORT.md complete, every bracketed placeholder removed
- [ ] Which data source produced each screenshot is stated
- [ ] If any demo used replayed data, that is disclosed with its capture date
- [ ] Limitations section names all five known limitations

**Estimate:** 3 to 4 hours. This is consistently underestimated.

---

## v1.0.0 — Submitted

**Goal:** delivered and nothing is billing.

**Entry:** v0.8.0 closed.

**Exit criteria**
- [ ] `git status` confirms `.env` and `infra/stack.json` are not staged
- [ ] Pushed to GitHub, repo accessible to instructors
- [ ] Link submitted in the Canvas text entry box
- [ ] Instructors emailed
- [ ] `python3 infra/teardown.py --confirm` run
- [ ] AWS console confirms nothing billable remains
- [ ] `CHANGELOG.md` and `TIMELOG.md` current
- [ ] REFLEXION written for Phase A into `MEMORY_CORRECTIONS.md`

**Estimate:** 1 hour.

**Phase A total: 11 to 17 hours.** At a two-day budget that fits with room for
one bad surprise, not two.

---

# PHASE B — HARDENING

Do not start any of this before v1.0.0 closes.

## v1.1.0 — FloodNet integration
Implement `FloodNetSource.fetch()` against the real API docs. Switch
`CURBLINE_SOURCE` and restore the 5/10/20 cm thresholds, which were calibrated
for exactly this measurement.
**Exit:** live FloodNet readings producing zones; USGS still works as fallback.
**Blocked on:** FloodNet approval. **Estimate:** 2 hrs once docs are in hand.

## v1.2.0 — Zone identity by spatial overlap
Replace the membership hash in `stable_zone_id` with overlap matching against
the previous cycle's hulls, so a zone that gains or loses one sensor stays the
same zone.
**Exit:** a zone survives a membership change with its `zone_id` and
`opened_at` intact; test added asserting it. **Estimate:** 4 hrs.

## v1.3.0 — Threshold calibration
Validate detection against ground truth. Join historical zones against NYC 311
street-flooding complaints in the same location and hour, and report precision
and recall rather than asserting the thresholds are reasonable.
**Exit:** a calibration table in the report with real numbers.
**Estimate:** 6 hrs. This is the gate that turns claims into evidence.

## v1.4.0 — Zone-based alert resolution
Resolve NWS `affectedZones` UGC references to polygons so zone-based products
stop being invisible to correlation.
**Exit:** an alert with `geometry: null` correlates correctly.
**Estimate:** 3 hrs.

## v1.5.0 — Event archive and review
Persist closed zones and let the console scrub back through a past event.
**Exit:** a completed flood event replayable in the UI from stored state.
**Estimate:** 5 hrs.

---

# PHASE C — ANTICIPATION

The category change. v1.x tells an operator a street is flooding. v2.0 tells
them it is about to, with enough lead time to act.

## v1.6.0 — Rainfall ingestion
Add precipitation as a fourth input alongside sensors and alerts. Fourth
process, or extend the collector.
**Exit:** rainfall persisted and time-aligned with sensor readings.
**Estimate:** 4 hrs.

## v1.7.0 — Per-zone response curves
For each historically flooding location, fit the relationship between rainfall
over a preceding window and observed depth. Store the curve per zone.
**Exit:** a fitted curve per zone with its fit quality recorded, and honest
reporting of zones with too little history to fit.
**Estimate:** 8 hrs.

**Flip condition for the whole of Phase C:** if v1.3.0 shows the detector's
precision is poor, do not build forecasting on top of it. Fix detection first.
A forecast built on a miscalibrated detector is confidently wrong, which is
worse than being late.

## v1.8.0 — Lead-time forecast
Project each zone forward from current rainfall and issue a predicted formation
time with an explicit confidence interval.
**Exit:** forecast issued before observed formation on at least one real event,
with the lead time measured and reported.
**Estimate:** 8 hrs.

## v2.0.0 — Forecast console

**Goal:** the console shows predicted zones alongside observed ones, clearly
distinguished, with lead-time countdowns.

**Scope**
- Predicted zones rendered distinctly from observed (never the same treatment)
- Lead-time countdown per predicted zone
- Forecast accuracy tracked and displayed: how often predictions verified
- Advisory ladder extended with a predictive tier below `monitor`

**Exit criteria**
- [ ] Predicted and observed zones are visually unmistakable from each other
- [ ] Every forecast carries a confidence interval, never a bare number
- [ ] A verification scoreboard shows hit rate and false alarm rate from real
      history, not a claim
- [ ] A forecast that did not verify is displayed as such, not quietly dropped
- [ ] Documentation states plainly that this is unvalidated research software
      and not a public safety system

**Estimate:** 10 hrs.

**The load-bearing assumption for all of Phase C:** that rainfall over a
preceding window predicts street flooding well enough to be useful at the block
level. If v1.7.0 produces curves with poor fit across most zones, that
assumption is false and Phase C should be abandoned rather than shipped. Say so
if it happens.

---

## Gate close checklist

Applies to every gate above.

```
GATE CLOSE: v[X.Y.Z]

Quality
- [ ] Tests passing: [N]/[N]
- [ ] No test touches a real AWS account
- [ ] Prior gate exit criteria still hold (regression check)
- [ ] No secrets staged for commit

Records
- [ ] CHANGELOG.md updated
- [ ] TIMELOG.md updated
- [ ] DECISIONS.md: new decisions logged with Flips if: lines
- [ ] ERRORS.md: anything that cost more than 30 minutes written up
- [ ] RETENTION.md: one recall question added for this gate

Cost
- [ ] Nothing billable left running that this gate does not need

REFLEXION
  Predicted: [X] hrs | Actual: [Y] hrs | Variance: [+/-Z]%
  Why off: [one specific sentence, not generic]
  Correction: [what changes for future gates of this type]

Gate criteria: MET / NOT MET
```
