#!/usr/bin/env bash
# phase1-platform.sh — Phase 1 driver (Section 17 Phase 1; CONVENTIONS.md §1, §4). Sources phase1/NN-*.sh in order
# through run_phase_steps and runs step_<id> under run_step, so a re-run skips completed steps.
#
#   sudo ./atlas-day1.sh phase1 [--dry-run] [--force STEP] [--status]
#   sudo /opt/atlas/day1/phase1-platform.sh [--no-reboot] [--dry-run] [--force STEP] [--status]
#   sudo /opt/atlas/day1/phase1-platform.sh --reload-allowlist [FILE]
#
# --reload-allowlist is the lightweight path after an allowlist edit (config/allowlist.txt says so): it copies FILE
# (when given) over this copy's config/allowlist.txt, re-renders /etc/squid/allowlist.txt and reloads squid. Nothing
# else runs: no ufw reset, no dist-upgrade, no reboot, which is what `--force 04` would do.
#
# ATLAS_ENTRY (the command the steps print as "re-run with ...") is exported by atlas-day1.sh; when this driver is run
# directly it is defaulted to the atlas-day1.sh beside this file, so the step files can expand it under `set -u`.
#
# The reboot in the middle (Section 17 step 4 -> 5): step 4 ends by calling phase1_request_reboot (defined here),
# which writes step 4's done marker, writes $ATLAS_STATE/reboot-pending with the current boot id, and reboots. On
# the next run, phase1_check_reboot sees a different boot id, removes the marker and lets run_phase_steps skip 01-04
# and continue from 05. With --no-reboot (or ATLAS_NO_REBOOT=1) the reboot is left to the Principal and the driver
# refuses to go past step 4 until it has happened. --no-reboot is accepted here only: atlas-day1.sh's option parser
# does not pass it through, so use the direct path above for that case.
#
# Interactive moments (rule §7.6 names the recovery-key pause; the two further inputs below are declared here, in
# the step headers and in the README):
#   * step 2: the recovery key is printed once and the step waits for "WRITTEN DOWN"; in the SAME pause, when the
#     installer encrypted the OS volume and it has no TPM2 token yet, the OS LUKS passphrase is asked ONCE so the TPM
#     can unlock the OS at boot (Section 3.5; used for the enrolment only, never stored);
#   * step 7: when the Cloudflare token cannot list zones and no zone id is found (beside the token in CLOUDFLARE.txt,
#     or in an existing secrets/cloudflare.env), the zone id is asked ONCE from the terminal with a 5-minute timeout
#     (Section 22, S11); no terminal or no answer leaves it blank with a warning.
#   * step 7's non-interactive path for the zone id: a "Zone ID: <32 hex>" line beside the token in CLOUDFLARE.txt, or
#     CF_ZONE_ID= pre-seeded in /etc/atlas/secrets/cloudflare.env (no atlas.env key: CONVENTIONS §3 does not list one).
# Waits that are not prompts: steps 5b and 7 WAIT up to 10 minutes each for the Principal's RDP session and phone
# handshake. A timeout records V19/V5 as FAIL (CONVENTIONS §6 lists both as required with no deferral), the step
# still completes, and the Phase 1 gate blocks Phase 2 until `--force 05b` / `--force 07` re-runs the wait.
# No waiver of Section 3.5 exists (fix round 3): an unencrypted OS volume stops step 1; the on-node recovery copy of
# D3 is always written. One recorded acknowledgement exists, ATLAS_ACCEPT_PCR7_NO_SB=1 in /etc/atlas/atlas.env: D2
# (Secure Boot disabled) makes the S9 PCR 7 binding unseal to any OS booted on the hardware, and step 1 / V2 stop
# until the Principal either enables Secure Boot or records that acknowledgement (see phase1/01-preflight.sh). The
# key is listed in CONVENTIONS §3 and config/atlas.env.example (blank by default).
# Phase-1-owned settings file: /etc/atlas/network.env (LAN_DNS_SERVERS, written by step 4, read by step 7,
# docker-egress-rules.sh and --reload-allowlist); nothing Phase 1 derives is written into atlas.env (§3 key set).

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"

export ATLAS_PHASE=phase1
: "${ATLAS_ENTRY:=$(readlink -f "$(dirname "$(readlink -f "$0")")/atlas-day1.sh")}"
export ATLAS_ENTRY
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

# --- arguments: --no-reboot and --reload-allowlist are ours, the rest is parse_common_args ---------------------------
args=()
reload_allowlist=0
reload_file=""
while (( $# > 0 )); do
  case "$1" in
    --no-reboot) ATLAS_NO_REBOOT=1; export ATLAS_NO_REBOOT; shift ;;
    --reload-allowlist)
      reload_allowlist=1
      if [[ -n "${2:-}" && "${2:-}" != --* ]]; then reload_file="$2"; shift 2; else shift; fi ;;
    *) args+=("$1"); shift ;;
  esac
done
require_root
if (( reload_allowlist )); then
  (( ${#args[@]} == 0 )) || die "--reload-allowlist takes an optional FILE and no other option"
  # No load_env: the render needs only the allowlist, sentinel-feeds.json and the squid template; the dnsmasq
  # re-render reads DOMAIN/WINDOWS_SHARE/LAN_IFACE from atlas.env and LAN_DNS_SERVERS from network.env inside
  # phase1_reload_allowlist.
  # shellcheck source=phase1/04-system.sh
  source "$ATLAS_DAY1_DIR/phase1/04-system.sh"
  phase1_reload_allowlist "$reload_file"
  exit 0
fi
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
