# RETENTION

The per-project retention substrate for Tool for Thought, sitting alongside
`DECISIONS.md`. Chat memory does not reach Claude Code, so this has to be a
file.

**The test this exists to pass:** six weeks from now, cold, no repo open, under
follow-up questions from an interviewer or an advisor, can Brian reconstruct
what he chose, what he rejected, why, and what would flip it.

---

## Rules for whoever runs the drill

**Free recall only.** Ask in prose. Make him type the answer. Never present the
answer as options to pick from. Recognition is not the skill being built, and
choosing from four is not the same act as producing it cold.

**One question, at most one follow-up.** Then stop, regardless of outcome.

**Wrong answers get the answer immediately.** Do not send him to the repo. The
re-test comes from the weighting below, not from withholding.

**Nothing retires.** Items stay in the pool permanently. Weight selection toward
least-recently-asked and toward anything previously wrong. This weighting is the
only re-testing mechanism in the system, so it is load-bearing. If it is
removed, immediate answer-giving becomes pointless and the wrong-answer rule has
to change with it.

**Skips are free.** Counted within the session only. At three consecutive skips,
say once that the retention half is not running, then drop it.

**Session start:** fires only if a decision was logged since the last session.
One question. Never fires on a session with nothing behind it.

**Session end:** after any session that produced a build decision, output what
was decided, why, what was rejected, the flip condition, and one question that
could not be answered without opening the repo. Three to six lines, no preamble.

---

## Flip watch

Check these against observed reality at the start of any session. If a
condition has been met and Brian has not noticed, say so, naming the decision
and when it was made.

| Decision | Flip condition | Met? |
|---|---|---|
| D-001 PostGIS over DynamoDB | EC2 cannot reach RDS by end of build day one | not yet |
| D-002 Keep ElastiCache | Provisioning fails or costs more than an hour | not yet |
| D-003 Zone id from member hash | A zone visibly fragments in a demo run | not yet |
| D-004 USGS rise above baseline | FloodNet access granted | pending approval |
| D-005 Source-specific thresholds | v1.3.0 calibration produces measured numbers | not yet |
| D-006 Buffer hulls 492 ft | Sensor density makes concave hulls meaningful | not yet |
| D-007 minpoints=2 | Calibration shows misses dominated by single-sensor events | not yet |
| D-008 forming state delays notify | Measured detect-to-advisory latency exceeds the decision window | not measured |
| D-010 Default VPC | Project handles non-public data | no |
| D-011 No Secrets Manager | Instructor confirms supporting services permitted | not asked |
| D-012 Entered at Phase 6 | Project continues past v1.0.0 into Phase B | not yet |
| Phase C viability | v1.7.0 curves fit poorly across most zones | not reached |

---

## Question pool

Rotate the audience. Each shape tests something different.

### Technical interview
- Why PostGIS rather than DynamoDB here, and what did that choice cost you?
- What breaks in this architecture at ten times the sensor count?
- Why does the correlator debounce clustering instead of running it per message?
- Your dispatcher writes to S3 before publishing to SNS. Why that order, and
  what happens if you swap them?
- SQS delivers at least once. Where exactly does this system absorb a duplicate,
  and what would go wrong without that?
- Why does a new zone not notify on its first cycle?
- Why is the API process not one of your three components?

### Advisor review (McCulloh / Liew shape)
- What is your baseline, and how would you know if this detector is any good?
- Your thresholds are 5, 10 and 20 centimetres. Defend each one.
- What is the failure mode of your evaluation itself, not of the system?
- You cluster with DBSCAN at 1640 feet. Why that number, and what changes at
  half or double it?
- You report rise above a 10th-percentile baseline. What assumption does that
  encode, and when is it false?
- What would falsify the claim that this detects flooding?

### Client / operational
- What does this cost to run per month at current scale?
- What happens when ElastiCache goes down at 3am?
- A dispatcher acts on one of your advisories and the street is dry. Walk me
  through how you would find out why.
- Why should an emergency coordinator trust this over what they use now?
- What is the one thing most likely to make this wrong?

### v0.5.0 infrastructure and pipeline

> Added 2026-08-28 after the first end-to-end run. Every one of these is a
> mechanism question with a wrong answer that sounds right, which is the whole
> reason they belong in the pool.

- PostgreSQL told you `function current_clusters(...) does not exist` while the
  function was sitting in the schema. What actually happened, and why did the
  message say that?
- You applied an IAM policy successfully and it authorized nothing. Explain how
  a policy can be valid, applied, and useless at the same time.
- Before this gate, no advisory had ever fired in any run of the system. The
  lifecycle function that decides advisories was unit tested and correct. Where
  was the bug, and why did no test catch it?
- A Redis cache hit caused a Postgres write to fail. Walk through the mechanism,
  and say what a cache is allowed to be evidence of.
- Your detection threshold was 5 cm while the collector was reading USGS stage
  rise. What did the dashboard show, and why is that number not a flood depth?
- RDS reported "Missing necessary credentials" when your credentials were fine.
  What was actually missing, and who was it missing for?

### Derivation and mechanism
> Known hole in the system: the drill covers build decisions, not derivations.
> A bypassed derivation is not caught by anything downstream. These are here
> anyway, because they are the ones that will be asked out loud.

- Explain DBSCAN's epsilon and minpoints in your own words, and what noise
  points mean in your specific case.
- Why does clustering in EPSG:4326 give wrong distances, and what does
  EPSG:2263 fix?
- What is the convex hull of two points, and why did that matter here?

---

## Drill history

| Date | Item | Audience | Right / Wrong | Note |
|---|---|---|---|---|
| | | | | |

Weight selection toward least-recently-asked and toward previous wrongs.
Empty table means the retention half has not run yet.

---

## Backfill status

Per the Tool for Thought plan, PrePayGuard is the first backfill target, not
Curbline. Curbline is forward-only: its decisions were logged as they were made,
with rationale recorded at the time rather than reconstructed afterward.

That distinction matters. A reconstructed rationale is a story invented after
the fact, and it collapses on the first follow-up question. Everything in
`DECISIONS.md` was written the day the decision was taken.

**Do not backfill rationale into this file.** If a decision here ever turns out
to lack recoverable reasoning, log it as **no recoverable rationale**. That is a
finding, not a gap to fill.

---

## v0.4.1 — cost and gate discipline

**Q (technical interview).** Curbline runs entirely inside the AWS free tier.
Explain why the two-day build is not the cost risk, and what actually is.

*Answer:* Three services bill hourly (EC2, RDS, ElastiCache), each against its
own separate 750-hour monthly allowance. 750 hours is one instance running
continuously for 31.25 days, so it is not a generous buffer. Two days across
three services is 144 hours, about 6.4% of each allowance, effectively zero.
The real risk is forgetting teardown: one month of all three running consumes
99% of every allowance, and the month after that bills at full rate (~$32/mo
compute) because you assume you are still covered. That is why teardown is an
exit criterion at v1.0.0 and not a courtesy.

**Q (McCulloh / Liew).** Your gate-close script hard-blocks on a decision that
has no `Flips if:` line. Why is that a hard block rather than a warning?

*Answer:* A decision without a flip condition cannot be revisited on evidence,
only on memory or mood. The flip condition is what converts a choice into a
falsifiable commitment: it names the specific observable that would make the
choice wrong. Without one, six weeks later the reasoning is gone and the
decision gets either defended reflexively or relitigated from scratch. Both are
worse than checking a stated trigger.

Asked: never | Result: —

---

## v0.7.0 — the unreachable-input test

**Q (technical interview).** Your test suite asserted
`next_state("active", 1) == "receding"` and it passed for the life of the
project. The behaviour it described never once occurred in production. Explain
how both of those are true at the same time, and what it means for how you read
a green suite.

*Answer:* The assertion was correct about the function and irrelevant to the
system. `next_state` really did return `receding` for a sensor count of 1. The
pipeline could never hand it a 1: `current_clusters()` is called with
`p_minpoints := CLUSTER_MIN_SENSORS` and filters `WHERE cid IS NOT NULL`, so
every row it emits is a DBSCAN cluster with at least that many members. The test
supplied an argument the producer makes impossible, so it proved a branch was
correct without proving the branch was reachable. The consequence was that no
zone ever receded or closed and `open_zones()` grew without bound.

This is E-017 seen from the other side. There, a correct pure function was fed a
wrongly obtained argument in production. Here, a correct pure function was fed
an argument production cannot produce. Both pass every test. The general rule:
a unit test fixes the relationship between an input and an output, and says
nothing about whether that input occurs. When the producer of an argument
constrains its range, the test has to be written inside that range, or it is
measuring a function nobody calls. **A passing test on an unreachable input is
worse than no test, because it reads as coverage.**

**Q (McCulloh / Liew).** You found six defects by auditing rather than by
running the system. What made that possible, and why is it worth more than the
six fixes?

*Answer:* Three defects had already occurred and had been written up. Read as a
list they were unrelated: a psycopg type mapping, a UUID compared to a string, a
counter in the wrong process. Read as a shape they were one thing: code correct
in isolation and wrong across a boundary the test suite cannot observe. Naming
the boundary turns each entry into a search: every Python value entering
PostgreSQL, every value crossing SQS and then compared to one that did not,
every piece of module-level state read by a process that did not write it. Those
searches found E-020 through E-025, including one that meant every zone issued
at most one advisory ever.

Worth more than the fixes because it changes when a defect entry is closed. An
entry is closed when the instance is fixed; it should be closed when the
codebase has been searched for the class. Applying that at E-013 rather than at
E-019 would have caught the threshold literals in the frontend and in the SQL
defaults immediately, instead of after they had been "fixed" twice in other
layers.

Asked: never | Result: —
