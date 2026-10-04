#!/usr/bin/env bash
# phase4/engines/chronos.sh — Chronos-Bolt (amazon/chronos-bolt-base), green (Section 15.2 row "Chronos (Chronos-Bolt /
# Chronos-2)": TimesFM was dropped in v0.3.1; Section 17 step 2 still says "TimesFM or Chronos"; adjudicated conflict
# 14: TimesFM 3.0 is non-commercial, Chronos is the forecasting engine). The key is `chronos` (fix round 3: the Arbiter
# ledger, result files, sample directory and registry carry the engine built, not the one the baseline removed).
# Research: rocm-containers.md §3.4 (pip package and model ids VERIFIED from the README). Test: chronos_test.py.
P4_KEY="chronos"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
