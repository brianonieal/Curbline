# TIMELOG

Hours against `VERSION_ROADMAP.md` gates. Coursework, so no billing rate
applies; the column exists because the freelance-billing skill expects it and
because knowing the real cost of a gate is what makes the next estimate honest.

| Date | Gate | Est | Actual | Var | Notes |
|---|---|---|---|---|---|
| 2026-08-27 | pre-v0.5.0 (design + code) | — | — | — | Entered at Build Rules Phase 6; see D-012 |
| 2026-08-27 | v0.4.1 (docs: COSTS, gate-check) | — | — | — | No source change |
| 2026-08-28 | v0.5.0 Infrastructure live | 2–5 | 25h elapsed | not computable | Nine defects, E-009 to E-017. Started 2026-08-27 12:00, exit criteria met 2026-08-28 13:20 EDT. Hands-on hours were not tracked, so elapsed is all that can be reported and it includes an overnight break. See REFLEXION. |
| | v0.6.0 Pipeline flowing | 3–4 | | | |
| 2026-08-28 | v0.7.0 Console live **(open)** | 2–3 | 2h32m so far | not comparable | Bounded by commit timestamps 17:18 to 19:50 EDT, a single unbroken session. The console wiring the estimate was written for has not started. The time went to a boundary audit and eight defect fixes, E-020 to E-027. See REFLEXION. |
| | v0.8.0 Evidence and report | 3–4 | | | Consistently underestimated |
| | v1.0.0 Submitted | 1 | | | |

**Phase A estimate: 11–17 hrs.** Uncalibrated. No correction factor from
`MEMORY_CORRECTIONS.md` was applied, because that file was not available when
the roadmap was written. Treat these as raw.

At gate close, fill Actual and Var, then write the REFLEXION into
`MEMORY_CORRECTIONS.md`. That is what makes the next project's estimates better.

---

## REFLEXION v0.5.0

**Predicted:** 2 to 5 hrs | **Actual:** not measurable | **Variance:** not computable

**Why off:** the estimate priced provisioning, and provisioning was the part
that worked. `provision.py` built every service correctly on its first
successful run. What consumed the gate was nine defects, eight of them in paths
no test could reach: an IAM action name that does not exist, a service-linked
role a fresh account has never created, a `double precision` to `numeric` cast
PostgreSQL will not perform implicitly, a Redis hit used as proof a row existed,
and a `uuid.UUID` compared against a string. The gate was not slow because AWS
was slow. It was slow because the first run against real managed services was
also the first execution of most of this code.

**Correction:** for any gate whose exit criteria require a real cloud account,
estimate the first run separately from the steady-state run. The steady-state
estimate of 2 to 5 hours was accurate; the second provisioning run took about
twenty minutes end to end. Budget the first run at roughly one defect per
fifteen minutes of elapsed time and treat that as expected work rather than as
overrun.

**Second correction:** track hands-on hours. This gate cannot report a variance
because only calendar time was recorded, which leaves the 2 to 5 hour estimate
unimprovable. An estimate that cannot be scored does not calibrate anything.

**Carry to `MEMORY_CORRECTIONS.md` at the Syntaris root, not this repo.**

---

## REFLEXION v0.7.0 (partial, gate still open)

**Predicted:** 2 to 3 hrs | **Actual so far:** 2h32m | **Variance:** not
comparable, and the reason is the finding.

**Why it is not comparable.** The v0.7.0 estimate was written for "Console
live": wiring `api/server.py` and the frontend to real pipeline state and
capturing six screenshots. None of that has happened. It cannot happen without
a provisioned stack, and the stack was torn down after the v0.5.0 capture.

What the 2h32m actually bought was different work that did not exist as a line
item anywhere: reading E-013, E-017 and E-019 as one defect shape rather than
three bugs, auditing each boundary they crossed, and fixing the eight defects
that turned up (E-020 through E-027). Two of those were severe. E-020 meant no
zone ever closed. E-021 meant every zone issued at most one advisory, ever.

So the number 2h32m is accurate and scoring it against 2 to 3 hours would be
meaningless. It is time spent on a different task that happens to sit inside the
same gate's calendar window.

**Correction, and it supersedes nothing from v0.5.0.** v0.5.0's correction was
"track hands-on hours." That worked: this entry reports 2h32m from commit
timestamps rather than 31h50m of elapsed calendar, and the bound is real because
the session was unbroken. Keep doing that. Commit timestamps are sufficient
instrumentation and no stopwatch is needed.

The new correction is about scope rather than measurement. **When work displaces
a gate's planned scope rather than adding to it, open a row for the work that
actually happened instead of logging hours against the estimate it did not
touch.** A gate row that silently absorbs unrelated work destroys the estimate's
value in both directions: v0.7.0's 2 to 3 hour figure now looks met when the
console has not been started, and the audit looks free when it cost two and a
half hours. Neither is true.

The practical form: if the first hour of a gate is not spent on that gate's
stated scope, stop and add a row.

**Still outstanding for v0.7.0:** all six exit criteria. Every one needs a live
stack.

**Hands-on across the project so far**, bounded by commit clustering rather than
claimed: roughly 6h30m on 2026-08-27 before the first commit (building the
consolidated tree), then about 10h on 2026-08-28 across three sessions
(06:51 to 13:30, 13:40 to 16:30, 17:18 to 19:50) with three gaps over
forty-five minutes that may have been breaks or may have been long debugging
runs. Call it 16 to 17 hours hands-on against a Phase A estimate of 11 to 17.
That is at the ceiling of the estimate with the last three gates not started,
so Phase A will overrun, and the overrun is concentrated in defect work rather
than in build work.

**Carry to `MEMORY_CORRECTIONS.md` at the Syntaris root, not this repo.**
