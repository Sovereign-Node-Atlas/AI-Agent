#!/usr/bin/env bash
# phase4/engines/ui-tars.sh — UI-TARS-1.5-7B, green (Section 15.2 says "UI-TARS 2.0"; adjudicated conflict 13: 2.0 has
# no open weights, so 1.5-7B is built and UI-TARS-2 goes on the Section 15.5 watch-list; this script says so).
# Research: rocm-containers.md §3.5 (repo id VERIFIED from the README; loading follows the Qwen2.5-VL convention,
# UNVERIFIED). Test: ui-tars_test.py (a synthetic GUI screenshot, one grounding turn -> ui_tars.txt).
P4_KEY="ui-tars"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_note "UI-TARS 2.0 has no open weights (bytedance/UI-TARS README, VERIFIED): building ByteDance-Seed/UI-TARS-1.5-7B; UI-TARS-2 recorded on the watch-list (Section 15.5)"
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
