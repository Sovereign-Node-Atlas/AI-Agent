#!/usr/bin/env bash
# verify/v15-approval-gate.sh — V15: "Approval gate holds a standard-tier email until approved, routine-tier
# auto-sends and logs" (Sections 16.1 rule 3, 16.2, 21; Phase 2 gate). Contract (CONVENTIONS.md §5): exit 0 pass /
# 1 fail; exactly one stdout line (the pytest summary); never prompts; safe to re-run; no live service is touched.
#
# Contract with the orchestrator package (CONVENTIONS.md §7.7/§7.8, README-contracts.md "Unit tests"): the tests live
# at orchestrator/tests/test_approval.py and run with `/opt/atlas/venv/bin/python -m pytest tests/test_approval.py`
# from the package directory, with the outbound channels stubbed (nothing is sent). Pass = pytest exit 0 (exit 5 "no
# tests collected" is a fail). The installed copy ($ATLAS_OPT/orchestrator) is preferred; the mirrored tree
# ($ATLAS_DAY1_DIR/orchestrator) is the fallback.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

ID=V15
TEST=test_approval.py
CLAIM="approval gate holds standard-tier, auto-sends and logs routine-tier"

py="$ATLAS_OPT/venv/bin/python"
[[ -x "$py" ]] || { echo "$ID fail: $py missing (Phase 2 step 2 builds the orchestrator venv)"; exit 1; }
pkg="$ATLAS_OPT/orchestrator"
[[ -f "$pkg/tests/$TEST" ]] || pkg="$ATLAS_DAY1_DIR/orchestrator"
[[ -f "$pkg/tests/$TEST" ]] || { echo "$ID fail: tests/$TEST not found under $ATLAS_OPT/orchestrator or $ATLAS_DAY1_DIR/orchestrator (contract: orchestrator/tests/$TEST)"; exit 1; }
"$py" -m pytest --version >/dev/null 2>&1 || { echo "$ID fail: pytest is not installed in $ATLAS_OPT/venv (pyproject.toml declares pytest>=8 as a runtime dependency; the gate installs nothing): re-run Phase 2 step 2: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02"; exit 1; }

if [[ -r "$ATLAS_ETC/orchestrator.env" ]]; then
  set -a
  # shellcheck disable=SC1091  # KEY=VALUE lines written by phase2/02-orchestrator.sh
  source "$ATLAS_ETC/orchestrator.env"
  set +a
fi
# Rule §7.1, unconditionally and AFTER the optional source (the env file must not be able to re-enable them): the package
# imports chromadb (posthog) and huggingface_hub at import time; without these the mirrored-tree fallback would attempt
# telemetry beacons that only the egress firewall then stops. HF_HUB_OFFLINE: the tests never download anything.
export HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_OFFLINE=1 DO_NOT_TRACK=1 ANONYMIZED_TELEMETRY=False CHROMA_TELEMETRY_ENABLED=false PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONDONTWRITEBYTECODE=1
# No -q here: pyproject.toml already sets addopts = "-q"; a second -q makes pytest -qq, which prints no "N passed" line.
cmd=("$py" -m pytest -p no:cacheprovider "tests/$TEST")
if [[ "${EUID:-$(id -u)}" -eq 0 ]] && [[ "$(stat -c %U "$pkg")" == atlas ]]; then
  cmd=(runuser -u atlas -- "${cmd[@]}")
fi
rc=0
out="$(cd "$pkg" && timeout 540 "${cmd[@]}" 2>&1)" || rc=$?
# `|| true`: under set -Eeuo pipefail a grep with no match would abort the script before the evidence line (fix round).
summary="$(grep -E '[0-9]+ (passed|failed|error|errors|skipped|xfailed|xpassed|warning|warnings)' <<<"$out" | tail -n1 | sed -e 's/=//g' -e 's/^ *//' -e 's/ *$//' || true)"
if (( rc == 0 )); then
  echo "$CLAIM: tests/$TEST -> ${summary:-pytest exit 0}"
  exit 0
fi
tail_lines="$(tail -n 6 <<<"$out" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g' | cut -c1-300)"
echo "$ID fail: tests/$TEST -> ${summary:-no summary line} (pytest exit $rc): $tail_lines"
exit 1
