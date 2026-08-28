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
| | v0.7.0 Console live | 2–3 | | | |
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
