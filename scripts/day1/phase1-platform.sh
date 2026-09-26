#!/usr/bin/env bash
# phase1-platform.sh — Phase 1 driver (Section 17 Phase 1; CONVENTIONS.md §1, §4). Sources phase1/NN-*.sh in order
# through run_phase_steps and runs step_<id> under run_step, so a re-run skips completed steps.
#
#   sudo ./atlas-day1.sh phase1 [--dry-run] [--force STEP] [--status]
#   sudo /opt/atlas/day1/phase1-platform.sh [--no-reboot] [--dry-run] [--force STEP] [--status]
#
# The reboot in the middle (Section 17 step 4 -> 5): step 4 ends by calling phase1_request_reboot (defined here),
# which writes step 4's done marker, writes $ATLAS_STATE/reboot-pending with the current boot id, and reboots. On
# the next run, phase1_check_reboot sees a different boot id, removes the marker and lets run_phase_steps skip 01-04
# and continue from 05. With --no-reboot (or ATLAS_NO_REBOOT=1) the reboot is left to the Principal and the driver
# refuses to go past step 4 until it has happened. --no-reboot is accepted here only: atlas-day1.sh's option parser
# does not pass it through, so use the direct path above for that case.
#
# Interactive moments (rule §7.6): the recovery-key pause in step 2 (and, only if the installer encrypted the OS
# volume, its passphrase once, just before). Steps 5b and 7 WAIT up to 10 minutes each for the Principal's RDP
# session and phone handshake but never block: a timeout records deferred and the gate treats deferred as passed.

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"

export ATLAS_PHASE=phase1
ATLAS_REBOOT_MARKER="$ATLAS_STATE/reboot-pending"
: "${ATLAS_NO_REBOOT:=0}"
export ATLAS_NO_REBOOT

_boot_id() { cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown; }

_reboot_now() {
  sync
  echo
  echo "  Rebooting in 5 seconds so the kernel parameters take effect. After the node is back:"
  echo "      sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1"
  echo "  continues from step 5 (steps 1-4 are skipped by their done markers)."
  echo
  sleep 5
  systemctl reboot
  sleep 120
  die "systemctl reboot returned but the node did not reboot; reboot it by hand, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1"
}

# phase1_request_reboot — called at the END of step 4 (the step's own done marker is written here because the
# reboot ends the process before run_step could write it).
phase1_request_reboot() {
  _atlas_state_init
  date -Is >"$ATLAS_DONE_DIR/phase1.04"
  printf 'boot_id=%s\nstep=phase1.04\nts=%s\n' "$(_boot_id)" "$(date -Is)" >"$ATLAS_REBOOT_MARKER"
  log "step 4 done; reboot marker written ($ATLAS_REBOOT_MARKER)"
  notify "Phase 1 step 4 done; rebooting for the kernel parameters"
  if [[ "$ATLAS_NO_REBOOT" == "1" ]]; then
    echo
    echo "  --no-reboot: NOT rebooting. Reboot the node yourself, then re-run:  sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1"
    echo "  (the driver continues from step 5 once it sees a new boot id)"
    echo
    exit 0
  fi
  _reboot_now
}

# phase1_check_reboot — called before the steps run and again at the start of step 5.
phase1_check_reboot() {
  [[ -e "$ATLAS_REBOOT_MARKER" ]] || return 0
  local recorded; recorded="$(awk -F= '$1=="boot_id" {print $2}' "$ATLAS_REBOOT_MARKER")"
  if [[ "$recorded" == "$(_boot_id)" ]]; then
    if [[ "$ATLAS_DRY_RUN" == "1" ]]; then
      log "DRY-RUN: a reboot is pending after step 4 (marker $ATLAS_REBOOT_MARKER); a real run would reboot here"
      return 0
    fi
    if [[ "$ATLAS_NO_REBOOT" == "1" ]]; then
      die "step 4 staged the kernel parameters but the node has not rebooted yet. Reboot it, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1"
    fi
    log "reboot still pending from step 4 (same boot id): rebooting now"
    _reboot_now
  fi
  rm -f "$ATLAS_REBOOT_MARKER"
  log "resumed after the step-4 reboot (boot id $(_boot_id)); continuing from step 5"
}

# --- arguments: --no-reboot is ours, the rest is parse_common_args ----------------------------------------------------
args=()
for a in "$@"; do
  case "$a" in
    --no-reboot) ATLAS_NO_REBOOT=1; export ATLAS_NO_REBOOT ;;
    *) args+=("$a") ;;
  esac
done
require_root
parse_common_args "${args[@]}"

if [[ "$ATLAS_DRY_RUN" == "1" ]]; then
  log "DRY-RUN: listing Phase 1 steps; nothing is executed and /etc/atlas/atlas.env is not touched"
else
  load_env
fi
phase1_check_reboot

log "Phase 1 (platform) starting; log $(_atlas_log_file)"
run_phase_steps phase1 "$ATLAS_DAY1_DIR/phase1"

if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
  echo
  echo "Phase 1 finished. Status of every step:"
  phase_status phase1
fi
