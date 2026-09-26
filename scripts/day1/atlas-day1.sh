#!/usr/bin/env bash
# atlas-day1.sh — the Day 1 entry point (CONVENTIONS.md §1).
#
#   sudo ./atlas-day1.sh phase1|phase2|phase3|phase4 [--dry-run] [--force STEP] [--status] [--foreground]
#   sudo ./atlas-day1.sh status | report | help
#
# On every run the scripts tree is mirrored to $ATLAS_OPT/day1 and this script re-executes from there, so a
# `git pull` during a running phase never changes the code that phase is executing. Phases 3 and 4 run detached
# under systemd (detached_phase) unless --foreground is given; --dry-run, --status and --force always run in the
# foreground. Phase N+1 refuses to start until phase N's gate wrote $ATLAS_STATE/done/phaseN.gate.
#
# Contracts relied on from other writers (CONVENTIONS.md §1): phase1-platform.sh, phase2-services.sh,
# phase3-models.sh and phase4-engines.sh live beside this file, accept the common args, and phases 3/4 accept --run.

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"

usage() {
  cat <<EOF
usage: sudo $0 <command> [options]

commands
  phase1      Platform: pre-flight, LUKS2+TPM2, mounts, kernel parameters, firewall, reboot, desktop, Docker, WG-Easy
  phase2      Engines and services: llama.cpp (Vulkan), orchestrator, Open WebUI, memory, voice, tools, restic, sentinel
  phase3      Core LLM pull and load tests (~690 GB; detached under systemd, resumable)
  phase4      Multimodal engines in the ROCm container (detached under systemd, per-engine pass/fail)
  status      Done markers for every phase and the full verification table
  report      Fill sheet "4 Verification" of a copy of docs/ATLAS_BUILD_BASELINE.xlsx from verify.jsonl
  help        This text

options (phases)
  --dry-run       print the steps, run nothing
  --force STEP    clear one step's done marker (e.g. --force 05b) before running
  --status        markers and verify table for that phase only
  --foreground    run phase3/phase4 in this terminal instead of a transient systemd unit

Follow a detached phase with:  journalctl -u atlas-day1-phase3 -f   (or -phase4)
EOF
}

# --- 1. Mirror the tree to $ATLAS_OPT/day1 and re-exec from there -----------------------------------------------------
self="$(readlink -f "$0")"
here="$(dirname "$self")"
opt_dir="$ATLAS_OPT/day1"
cmd="${1:-help}"

if [[ "$cmd" != help && "$cmd" != -h && "$cmd" != --help ]]; then
  require_root
  if [[ "$here" != "$(readlink -f "$opt_dir" 2>/dev/null || echo "$opt_dir")" ]]; then
    command -v rsync >/dev/null || apt_install rsync
    mkdir -p "$opt_dir"
    rsync -a --delete --exclude .git --exclude '__pycache__' --exclude '.venv' --exclude '*.pyc' "$here/" "$opt_dir/"
    chown -R root:root "$opt_dir"
    # Remember where the repo is: gate prints "sudo $ATLAS_ENTRY phaseN" and report finds docs/ next to it.
    export ATLAS_ENTRY="$self"
    ATLAS_REPO_ROOT="$(cd "$here/../.." && pwd -P)"
    export ATLAS_REPO_ROOT
    exec "$opt_dir/atlas-day1.sh" "$@"
  fi
fi
: "${ATLAS_ENTRY:=$self}"
export ATLAS_ENTRY

# --- 2. Helpers -------------------------------------------------------------------------------------------------------
require_gate() {
  local n="$1"
  [[ -e "$ATLAS_DONE_DIR/phase$n.gate" ]] \
    || die "phase $n has not passed its gate ($ATLAS_DONE_DIR/phase$n.gate missing). Run: sudo $ATLAS_ENTRY phase$n"
}

# Split phase options into "foreground-only" markers and the pass-through list.
foreground=0
wants_foreground_by_args=0
pass=()
parse_phase_opts() {
  while (( $# > 0 )); do
    case "$1" in
      --foreground) foreground=1; shift ;;
      --dry-run|--status) wants_foreground_by_args=1; pass+=("$1"); shift ;;
      --force) [[ -n "${2:-}" ]] || die "--force needs a STEP id"; wants_foreground_by_args=1; pass+=("$1" "$2"); shift 2 ;;
      *) die "unknown option '$1' for $cmd (see: $0 help)" ;;
    esac
  done
}

run_foreground_phase() {
  local phase="$1" script="$2"; shift 2
  [[ -x "$script" ]] || die "$script is missing or not executable (written by the phase author; see CONVENTIONS.md §1)"
  export ATLAS_PHASE="$phase"
  log "starting $phase in the foreground: $script $*"
  exec "$script" "$@"
}

run_detached_or_foreground() {
  local phase="$1" script="$2"; shift 2
  [[ -x "$script" ]] || die "$script is missing or not executable (written by the phase author; see CONVENTIONS.md §1)"
  if (( foreground || wants_foreground_by_args )); then
    export ATLAS_PHASE="$phase"
    if (( ${#pass[@]} > 0 )); then
      exec "$script" "${pass[@]}"
    fi
    exec "$script" --run
  fi
  export ATLAS_PHASE="$phase"
  detached_phase "$phase" "$script"
}

do_report() {
  _atlas_state_init
  [[ -s "$ATLAS_VERIFY_FILE" ]] || die "no verify records at $ATLAS_VERIFY_FILE yet"
  local workbook=""
  for cand in "${ATLAS_REPO_ROOT:-}/docs/ATLAS_BUILD_BASELINE.xlsx" "$ATLAS_STATE/ATLAS_BUILD_BASELINE.xlsx"; do
    [[ -n "$cand" && -f "$cand" ]] && { workbook="$cand"; break; }
  done
  [[ -n "$workbook" ]] || die "docs/ATLAS_BUILD_BASELINE.xlsx not found (run from the repo checkout, or copy it to $ATLAS_STATE/)"
  local outdir="$ATLAS_STATE/reports"
  mkdir -p "$outdir"
  local out
  out="$outdir/ATLAS_BUILD_BASELINE-$(date +%Y%m%d-%H%M%S).xlsx"
  cp -f "$workbook" "$out"
  if ! python3 -c 'import openpyxl' 2>/dev/null; then
    apt_install python3-openpyxl
  fi
  python3 "$ATLAS_DAY1_DIR/tools/fill-workbook.py" --verify "$ATLAS_VERIFY_FILE" --workbook "$out" --out "$out"
  echo "Report written to: $out   (source workbook: $workbook, untouched)"
  echo "Table as recorded:"
  python3 "$ATLAS_DAY1_DIR/tools/fill-workbook.py" --verify "$ATLAS_VERIFY_FILE" --print
}

# --- 3. Dispatch ------------------------------------------------------------------------------------------------------
shift || true
case "$cmd" in
  phase1)
    parse_phase_opts "$@"
    run_foreground_phase phase1 "$ATLAS_DAY1_DIR/phase1-platform.sh" "${pass[@]}"
    ;;
  phase2)
    parse_phase_opts "$@"
    (( wants_foreground_by_args )) || require_gate 1
    run_foreground_phase phase2 "$ATLAS_DAY1_DIR/phase2-services.sh" "${pass[@]}"
    ;;
  phase3)
    parse_phase_opts "$@"
    (( wants_foreground_by_args )) || require_gate 2
    run_detached_or_foreground phase3 "$ATLAS_DAY1_DIR/phase3-models.sh"
    ;;
  phase4)
    parse_phase_opts "$@"
    (( wants_foreground_by_args )) || require_gate 3
    run_detached_or_foreground phase4 "$ATLAS_DAY1_DIR/phase4-engines.sh"
    ;;
  status)
    phase_status
    ;;
  report)
    do_report
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    die "unknown command '$cmd'"
    ;;
esac
