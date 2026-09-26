#!/usr/bin/env bash
# phase1/08-gate.sh — Phase 1 step 8 (Section 17, CONVENTIONS.md §6): the gate table. Required V2, V3a, V5, V19
# (deferred never blocks); V1 recorded only. Writes $ATLAS_STATE/done/phase1.gate on PASS so Phase 2 may start.
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
