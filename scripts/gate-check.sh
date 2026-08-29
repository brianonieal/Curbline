#!/usr/bin/env bash
#
# Mechanizes the gate-close checklist at the bottom of VERSION_ROADMAP.md.
#
# The checklist is only useful if it actually runs. A checklist you tick by
# hand at 1am is a checklist you tick without reading.
#
#   ./scripts/gate-check.sh v0.5.0
#
# Exits non-zero if any hard block fails. Hard blocks are the ones the testing
# skill and the secrets rule make non-negotiable; everything else warns.

set -uo pipefail

GATE="${1:-unspecified}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PASS=0; WARN=0; FAIL=0

ok()   { echo "  [ok]   $*"; PASS=$((PASS+1)); }
warn() { echo "  [warn] $*"; WARN=$((WARN+1)); }
fail() { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }

echo
echo "GATE CLOSE CHECK: $GATE"
echo "$(date -u '+%Y-%m-%d %H:%M UTC')"
echo

# ---------------------------------------------------------------------------
echo "Quality"
# ---------------------------------------------------------------------------

# Both venv layouts, because the gate is run from the Windows dev machine as
# well as from the EC2 host. Falling through to a bare python3 that lacks the
# dependencies reported 24 test failures on a clean tree, which is a false hard
# block and erodes trust in the check exactly as much as a false pass would.
PY="${PYTHON:-python3}"
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
fi

if ! $PY -c "import pytest" >/dev/null 2>&1; then
  fail "interpreter $PY cannot import pytest. HARD BLOCK: a gate cannot close on an unrun suite."
fi

if $PY -m pytest tests/ -q >/tmp/gate-tests.txt 2>&1; then
  ok "tests: $(tail -1 /tmp/gate-tests.txt | tr -d '\n')"
else
  fail "tests failing. HARD BLOCK. See /tmp/gate-tests.txt"
fi

# No test may reach a real account. The testing skill is explicit about this.
if grep -rlE 'boto3\.(client|resource)\(' tests/ 2>/dev/null \
     | xargs -r grep -L 'mock_aws\|moto' | grep -q .; then
  fail "a test constructs a boto3 client outside moto. HARD BLOCK."
else
  ok "no test touches a real AWS account"
fi

if $PY -m py_compile infra/*.py curbline/*.py workers/*.py api/*.py data/*.py 2>/dev/null; then
  ok "all python compiles"
else
  fail "python compile error. HARD BLOCK."
fi

# Scoped to attribute existence on typed libraries. See mypy.ini for why every
# other error code is off. This catches the E-013 family without executing
# anything: a method that does not exist on a psycopg, redis or boto3 object.
if $PY -c "import mypy" >/dev/null 2>&1; then
  if $PY -m mypy curbline/ workers/ api/ infra/ scripts/ \
       >/tmp/gate-mypy.txt 2>&1; then
    ok "mypy: no undefined attributes on typed libraries"
  else
    fail "mypy found undefined attributes. See /tmp/gate-mypy.txt"
  fi
else
  warn "mypy not installed, skipped the library-attribute check"
fi

if command -v node >/dev/null 2>&1; then
  node --check web/app.js 2>/dev/null && ok "app.js parses" || fail "app.js syntax error"
  # Parsing only proves it is JavaScript. Seven of the evidence screenshots are
  # the console, and an exception during a capture session costs the session,
  # so the smoke test drives apply() through the real payload shapes including
  # the degraded ones.
  if node tests/console_smoke.js >/tmp/gate-console.txt 2>&1; then
    ok "console smoke: $(grep -c '^  ok' /tmp/gate-console.txt) checks"
  else
    fail "console smoke failed. HARD BLOCK. See /tmp/gate-console.txt"
  fi
else
  warn "node not installed, skipped app.js checks"
fi

bash -n infra/bootstrap.sh 2>/dev/null && ok "bootstrap.sh syntax valid" \
  || fail "bootstrap.sh syntax error"

# ---------------------------------------------------------------------------
echo
echo "Secrets"
# ---------------------------------------------------------------------------

SECRET_LEAK=0
for f in .env infra/stack.json; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    fail "$f is TRACKED BY GIT. HARD BLOCK. Rotate the RDS password."
    SECRET_LEAK=1
  fi
done
if git diff --cached --name-only 2>/dev/null | grep -qE '^\.env$|stack\.json$'; then
  fail "a secret file is STAGED. HARD BLOCK."
  SECRET_LEAK=1
fi
[ "$SECRET_LEAK" -eq 0 ] && ok "no secret files tracked or staged"

# The inverse of a secret leak, and it bit once. .gitignore denies data/*.json
# by default with an allowlist, which is the right direction for licensed data
# and means a NEW fixture is ignored in silence: git add says nothing, the
# commit succeeds, and the file exists on one laptop. replay.escalation.json
# was written, tested against and referenced by the run book while absent from
# every clone. See E-031. Warn rather than block, because a genuinely cached
# FloodNet extract sitting here is expected and must NOT be committed.
# Bytecode is excluded: a warning that always fires teaches people to skip it.
HIDDEN_DATA=$(git ls-files --others --ignored --exclude-standard data/ 2>/dev/null \
              | grep -vE '__pycache__|\.pyc$' || true)
if [ -n "$HIDDEN_DATA" ]; then
  warn "ignored file(s) under data/. If any is a fixture the code needs, it will not exist on the capture host:"
  printf '%s\n' "$HIDDEN_DATA" | sed 's/^/           /'
else
  ok "no ignored fixtures hiding under data/"
fi

# Scan what git tracks, not what happens to sit in the working directory. A
# .venv under the repo trips every pattern below: botocore assigns
# aws_secret_access_key, and moto ships import docker plus AWS::Lambda model
# names. The gate is a claim about this repository, not about its dependencies.
CRED_RE='(AKIA[0-9A-Z]{16}|aws_secret_access_key[[:space:]]*=)'
if git ls-files -z '*.py' '*.sh' '*.js' '*.md' 2>/dev/null | xargs -0 -r grep -InE "$CRED_RE" 2>/dev/null | grep -v '^scripts/gate-check[.]sh:' | grep -q .; then
  fail "possible hardcoded AWS credential. HARD BLOCK."
else
  ok "no hardcoded AWS credentials found"
fi

# ---------------------------------------------------------------------------
echo
echo "Constraint compliance (assignment bans)"
# ---------------------------------------------------------------------------

BANNED=0
for term in 'import docker' 'FROM python:' 'apiVersion: apps/v1' 'AWS::Lambda' 'boto3.client("lambda")' "boto3.client('lambda')"; do
  if git ls-files -z 2>/dev/null | xargs -0 -r grep -Il -F "$term" 2>/dev/null | grep -v '^scripts/gate-check[.]sh$' | grep -q .; then
    fail "banned technology reference found: $term"
    BANNED=1
  fi
done
for f in Dockerfile docker-compose.yml docker-compose.yaml serverless.yml; do
  [ -e "$f" ] && { fail "banned artifact present: $f"; BANNED=1; }
done
[ "$BANNED" -eq 0 ] && ok "no Lambda, container, or orchestrator artifacts"

# ---------------------------------------------------------------------------
echo
echo "Records"
# ---------------------------------------------------------------------------

grep -q "$GATE" CHANGELOG.md 2>/dev/null \
  && ok "CHANGELOG.md mentions $GATE" \
  || warn "CHANGELOG.md has no entry for $GATE"

grep -q "$GATE" TIMELOG.md 2>/dev/null \
  && ok "TIMELOG.md mentions $GATE" \
  || warn "TIMELOG.md has no row for $GATE"

DEC=$(grep -c '^## D-' DECISIONS.md 2>/dev/null || echo 0)
FLIP=$(grep -c 'Flips if:' DECISIONS.md 2>/dev/null || echo 0)
if [ "$FLIP" -ge "$DEC" ]; then
  ok "DECISIONS.md: $DEC decisions, $FLIP flip conditions"
else
  fail "DECISIONS.md: $DEC decisions but only $FLIP flip conditions. Every decision needs one."
fi

grep -q "$GATE" RETENTION.md 2>/dev/null \
  && ok "RETENTION.md has a question for $GATE" \
  || warn "RETENTION.md has no recall question for $GATE. Add one."

# ---------------------------------------------------------------------------
echo
echo "Cost"
# ---------------------------------------------------------------------------

if command -v aws >/dev/null 2>&1; then
  # --region is not optional. RDS and ElastiCache are regional, and an
  # unpinned call answers for whatever region the caller happens to default
  # to. That returns zero from the wrong region and reads as a clean teardown
  # on a stack that is still billing, which is E-018 exactly, and it already
  # produced one false teardown report on this project.
  RDS=$(aws rds describe-db-instances --region us-east-1 \
          --query 'length(DBInstances[?DBInstanceStatus==`available`])' \
          --output text 2>/dev/null || echo "?")
  CACHE=$(aws elasticache describe-cache-clusters --region us-east-1 \
          --query 'length(CacheClusters[?CacheClusterStatus==`available`])' \
          --output text 2>/dev/null || echo "?")
  echo "  billable now (us-east-1): RDS=$RDS  ElastiCache=$CACHE"

  if [ "$RDS" = "?" ] || [ "$CACHE" = "?" ]; then
    # Unknown is a third state and must not be reported as either answer. The
    # old code compared != "0", so a failed call became "requires teardown",
    # which sends someone to re-run teardown on an already-empty account when
    # the real problem is credentials or region.
    if [ "$GATE" = "v1.0.0" ]; then
      fail "cannot verify nothing is billable: the AWS calls did not answer. Check credentials and region. Unverified is not torn down."
    else
      warn "cost check could not reach AWS (credentials or region), not a teardown claim"
    fi
  elif [ "$GATE" = "v1.0.0" ] && { [ "$RDS" != "0" ] || [ "$CACHE" != "0" ]; }; then
    fail "v1.0.0 requires teardown. Run: python3 infra/teardown.py --confirm"
  else
    ok "cost check recorded"
  fi
else
  warn "aws cli not available, cost check skipped"
fi

# ---------------------------------------------------------------------------
echo
echo "---------------------------------------------------------------"
printf "  %d passed, %d warnings, %d failures\n" "$PASS" "$WARN" "$FAIL"
echo "---------------------------------------------------------------"

if [ "$FAIL" -gt 0 ]; then
  echo
  echo "GATE NOT MET. Fix the failures above; they are hard blocks."
  exit 1
fi

cat <<REFLEXION

GATE CRITERIA MET (mechanical checks only).

The checks above cannot verify that the gate's own exit criteria in
VERSION_ROADMAP.md are satisfied. Read them and confirm by hand.

Then paste this into TIMELOG.md and write the REFLEXION:

  | $(date -u +%Y-%m-%d) | $GATE | <est> | <actual> | <var> | <note> |

  REFLEXION $GATE
    Predicted: __ hrs | Actual: __ hrs | Variance: __%
    Why off: <one specific sentence, not generic>
    Correction: <what changes for the next gate of this type>

Carry the correction into your global MEMORY_CORRECTIONS.md, not this repo.
REFLEXION
