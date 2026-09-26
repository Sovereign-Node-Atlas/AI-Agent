#!/usr/bin/env bash
# verify/v16-router-hard-rule.sh — V16: "Router hard rule routes a 'medical' message to Arthur even when the
# classifier disagrees, decision logged" (Sections 7.1, 7.2 rules 1 and 5, 21; Phase 2 gate). Contract (CONVENTIONS.md
# §5): exit 0 pass / 1 fail; exactly one stdout line (the pytest summary); never prompts; safe to re-run; no live
# service is touched (the resident classifier is stubbed to disagree on purpose).
#
# Contract with the orchestrator package (CONVENTIONS.md §7.7/§7.8, README-contracts.md "Unit tests"): the tests live
# at orchestrator/tests/test_router.py and run with `/opt/atlas/venv/bin/python -m pytest -q tests/test_router.py`
# from the package directory. FAMILY_NAMES from orchestrator.env is exported so the family-name hard rule (7.2 rule 1)
# is exercised with the Principal's names. Pass = pytest exit 0 (exit 5 "no tests collected" is a fail). The
# installed copy ($ATLAS_OPT/orchestrator) is preferred; the mirrored tree ($ATLAS_DAY1_DIR/orchestrator) is the
# fallback.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

ID=V16
TEST=test_router.py
CLAIM="router hard rule sends 'medical' to Arthur over the classifier, decision logged"

py="$ATLAS_OPT/venv/bin/python"
[[ -x "$py" ]] || { echo "$ID fail: $py missing (Phase 2 step 2 builds the orchestrator venv)"; exit 1; }
pkg="$ATLAS_OPT/orchestrator"
[[ -f "$pkg/tests/$TEST" ]] || pkg="$ATLAS_DAY1_DIR/orchestrator"
[[ -f "$pkg/tests/$TEST" ]] || { echo "$ID fail: tests/$TEST not found under $ATLAS_OPT/orchestrator or $ATLAS_DAY1_DIR/orchestrator (contract: orchestrator/tests/$TEST)"; exit 1; }
"$py" -m pytest --version >/dev/null 2>&1 || { echo "$ID fail: pytest is not installed in $ATLAS_OPT/venv (phase2/10-gate.sh installs it; by hand: $ATLAS_OPT/venv/bin/pip install pytest)"; exit 1; }

if [[ -r "$ATLAS_ETC/orchestrator.env" ]]; then
  set -a
  # shellcheck disable=SC1091  # KEY=VALUE lines written by phase2/02-orchestrator.sh
  source "$ATLAS_ETC/orchestrator.env"
  set +a
fi
export PYTHONDONTWRITEBYTECODE=1
cmd=("$py" -m pytest -q -p no:cacheprovider "tests/$TEST")
if [[ "${EUID:-$(id -u)}" -eq 0 ]] && [[ "$(stat -c %U "$pkg")" == atlas ]]; then
  cmd=(runuser -u atlas -- "${cmd[@]}")
fi
rc=0
out="$(cd "$pkg" && timeout 540 "${cmd[@]}" 2>&1)" || rc=$?
summary="$(grep -E '[0-9]+ (passed|failed|error|errors|skipped|xfailed|xpassed|warning|warnings)' <<<"$out" | tail -n1 | sed -e 's/=//g' -e 's/^ *//' -e 's/ *$//')"
if (( rc == 0 )); then
  echo "$CLAIM: tests/$TEST -> ${summary:-pytest exit 0}"
  exit 0
fi
tail_lines="$(tail -n 6 <<<"$out" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | cut -c1-300)"
echo "$ID fail: tests/$TEST -> ${summary:-no summary line} (pytest exit $rc): $tail_lines"
exit 1
