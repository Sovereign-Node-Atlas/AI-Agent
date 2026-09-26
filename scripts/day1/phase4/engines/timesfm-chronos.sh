#!/usr/bin/env bash
# phase4/engines/timesfm-chronos.sh — Chronos-Bolt (amazon/chronos-bolt-base), green (Section 15.2 "TimesFM or
# Chronos"; adjudicated conflict 14: TimesFM 3.0 is non-commercial, Chronos is the forecasting engine).
# Research: rocm-containers.md §3.4 (pip package and model ids VERIFIED from the README). Test: timesfm-chronos_test.py.
P4_KEY="timesfm-chronos"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_venv_from_json
  p4_pull
}

p4_main "$@"
