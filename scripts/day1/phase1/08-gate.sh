#!/usr/bin/env bash
# phase1/08-gate.sh — Phase 1 step 8 (Section 17, CONVENTIONS.md §6): the gate table. Required V2, V3a, V5, V19; V1
# recorded only. V3a must pass; V2 must pass except that an unencrypted OS volume alone records it deferred (to-do).
# V5/V19 need the Principal's phone/PC: a wait that timed out is recorded as deferred (policy v0.3.3, to-do list; the
# gate never blocks on deferred). Any other V5/V19 failure is a red row and the Principal re-runs `--force 07` /
# `--force 05b`. Writes $ATLAS_STATE/done/phase1.gate on PASS so Phase 2 may start.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

step_08() {
  if gate phase1 V2 V3a V5 V19 -- V1; then
    notify "Phase 1 gate PASS. Next: sudo $ATLAS_ENTRY phase2"
    return 0
  fi
  notify "Phase 1 gate FAIL; see the table in the phase log"
  return 1
}
