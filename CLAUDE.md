# CLAUDE.md

Project instructions for Claude Code. Read this before touching anything.

**Project:** Curbline, street-flood zone detection for New York City
**Owner:** Brian Onieal
**Current version:** v0.4.1
**Deadline:** JHU cloud computing Individual Project, end of Module 7
**Governing skill:** build-rules (Blueprint v10), with the deviations recorded below

---

## Deviation from Build Rules v10, recorded not hidden

This project entered at Phase 6. It did not pass Phases 1 through 5. There was
no intake brain dump, no interrogation, no MOCKUPS.md approval cycle, and no
FRONTEND_SPEC.md gate before UI code was written. The code exists first.

That is a real violation of "no code until all five approved." It is recorded
here so the next reader does not conclude the gates passed silently.

Consequence for how the remaining work runs: `VERSION_ROADMAP.md` is the
authority from v0.5.0 forward, and gate discipline applies normally from here.
Do not retroactively manufacture the missing phase approvals.

**Foundation files present (10 of 22):** CLAUDE.md, VERSION_ROADMAP.md,
DECISIONS.md, ERRORS.md, TESTS.md, CHANGELOG.md, TIMELOG.md, RETENTION.md,
COSTS.md, plus `scripts/gate-check.sh` which mechanizes the gate-close
checklist. Run it before claiming any gate closed.

**Deliberately absent (14):** CONTRACT.md and COMMS.md (no client, this is
coursework), MOCKUPS.md and FRONTEND_SPEC.md and COMPONENT_REGISTRY.md and
DESIGN_SYSTEM.md (UI is one page of three files; the design tokens live in
`web/style.css` with their reasoning in comments), COUNCIL_AGENTS.md and
HARNESS.md and PLANS.md and PATTERNS.md and SPEC.md (gate specs are inline in
VERSION_ROADMAP.md), INFRASTRUCTURE.md (`infra/` is the infrastructure),
TECH_STACK.md (in README), and the three MEMORY_*.md files (global, not
per-project: MEMORY_CORRECTIONS.md is referenced by VERSION_ROADMAP.md and
TIMELOG.md and must live at your Syntaris root, never in this repo). Do not create these unless the work actually calls for one.

---

## Hard constraints from the assignment. Violating any of these costs points.

- **No serverless functions.** No Lambda, no Step Functions.
- **No containers.** No Docker, ECS, Fargate, EKS, Kubernetes, App Runner.
- **No service mesh.**
- **No self-installed data services.** Every database, queue, cache, and topic
  is an AWS managed service. Never `apt-get install postgresql` on the host.
- **Permitted services only:** messaging, queuing, caching, databases, plus
  storage and VMs. The assignment says nothing else is allowed. That is why the
  database password lives in a mode-600 env file and not in Secrets Manager.
  Do not add a service to solve a problem; solve it inside this list.
- **Three processes minimum.** The collector, correlator, and dispatcher are
  the three graded components. `api/server.py` is the presentation layer and is
  explicitly not one of them. Never describe it as a component.

---

## Traps already discovered. Do not rediscover them.

Full detail in `ERRORS.md`. The short version:

1. **USGS gage height is not flood depth.** It is stage above an arbitrary
   local datum. Raw values in the NYC bbox run -0.26 to 19.58 ft on a dry day.
   `USGSSource` reports rise above a per-site p10 baseline. Never revert this.
2. **Thresholds are source-specific.** 5/10/20 cm is calibrated for FloodNet
   street depth. On USGS stage rise those numbers are wrong by roughly an order
   of magnitude. Start near 60/90/120 for USGS and say which source produced
   each screenshot.
3. **Two-point clusters degenerate.** Convex hull of 2 points is a LINESTRING
   and of 1 point is a POINT. Both violate `geometry(Polygon)` and render
   invisibly. `current_clusters()` buffers by 492 ft to force a polygon.
4. **Many NWS alerts have `geometry: null`.** Zone-based products reference UGC
   zones instead. Store NULL, do not drop, do not fabricate a polygon.
5. **Security group ordering.** `provision.py` self-attaches `curbline-app` to
   its own instance via IMDSv2. Without it, RDS refuses the connection.
6. **Port 8000 must be open** or the dashboard is unreachable from a browser.

---

## Working rules

**Read before writing.** `VERSION_ROADMAP.md` for the current gate, then
`DECISIONS.md` for what is already settled and why. Do not relitigate a logged
decision without checking its `Flips if:` condition first.

**Sequential builds.** One file completed and validated before the next begins.
Non-negotiable, per Build Rules.

**Never regress a passing gate.** Before closing any gate, the prior gate's
exit criteria must still hold. If a change breaks an earlier gate, that is a
stop, not a note.

**Tests before build.** Per the testing skill: write the failing test, then
build. `TESTS.md` is the registry. 26 tests currently pass and none touch a
real AWS account. Keep it that way; moto for everything.

**Log decisions with flip conditions.** Every entry in `DECISIONS.md` carries a
`Flips if:` line naming a specific observable trigger, not a risk. "Flips if
sensor count exceeds ~5,000" is a flip condition. "Risk: may not scale" is not.

**Cost control is part of done.** RDS and ElastiCache bill hourly against a
750-hour free tier. `infra/teardown.py --confirm` after evidence capture.
A gate is not closed with billable resources left running unnecessarily.

**Do not commit secrets.** `.env` and `infra/stack.json` are gitignored.
Verify with `git status` before every push. If the RDS password ever reaches a
public repo, rotate it; deleting the commit is not sufficient.

---

## Commands

```bash
# Frontend work, no AWS required
.venv/bin/python api/mock_server.py          # http://localhost:8000

# Tests
python3 -m pytest tests/ -q

# Gate close check. Hard-blocks on failing tests, staged secrets,
# banned technologies, and decisions missing a Flips if: line.
./scripts/gate-check.sh v0.5.0

# Provision (on EC2, with the instance role attached)
# CURBLINE_ADMIN_CIDR is YOUR address, read from your own machine, not
# from this instance. curl on the instance returns the instance. See E-008.
AWS_REGION=us-east-1 CURBLINE_ADMIN_CIDR=203.0.113.7/32 ./infra/bootstrap.sh

# Service control
sudo systemctl status 'curbline-*'
journalctl -u curbline-correlator -f

# Teardown. Not optional.
python3 infra/teardown.py --confirm
```

---

## Voice for anything written into this repo

Brian's global rules apply to code comments, commit messages, and docs.

- No em dashes. Use commas, colons, parentheses, semicolons, or periods.
- No hedging filler. No "I hope this helps," no "feel free to."
- Comments explain **why**, never what. A comment restating the line above it
  gets deleted.
- Name limitations directly. The report's limitations section is worth more
  than a paragraph claiming the system is finished.
- Never claim novelty without naming the prior art. Curbline sits in a gap
  between FloodNet's dashboard and the NWS warning feed. It is a configuration
  of existing practice, not a new method.
