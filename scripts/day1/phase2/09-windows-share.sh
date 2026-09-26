#!/usr/bin/env bash
# phase2/09-windows-share.sh — Section 17 Phase 2 step 9: the Windows PC share as an on-demand mount (Section 12.4:
# "a shared folder the node mounts when a task needs it and releases afterwards"; no software on the PC). Sourced by
# phase2-services.sh through run_phase_steps; defines step_09 only.
#
# Order (each part idempotent):
#   1. WINDOWS_SHARE from /etc/atlas/atlas.env (//host/share). Blank, "none", "skip" or "-" -> log a line and return
#      (load_env normally refuses a blank value, so this is the documented opt-out spelling).
#   2. Credentials: $ATLAS_ETC/secrets/smb.cred (root 600; mount.cifs `credentials=` file with username=, password=,
#      domain= lines, VERIFIED format) written from ONE prompt on the terminal when the file is absent; never echoed.
#   3. systemd/srv-atlas-winpc.mount + .automount rendered and installed under the systemd-escaped name of
#      $ATLAS_SRV/winpc; ONLY the .automount is enabled (research 4.12): the share mounts on first access and is
#      released after TimeoutIdleSec=600 (the unit-file form of x-systemd.idle-timeout).
#   4. Test: a directory listing through the automount, then findmnt must show a cifs mount. A failure is fatal with
#      the mount unit's journal and the exact re-run command: a share that cannot be listed must not be pretended.
#
# Facts: cifs-utils 2:7.4-1ubuntu0.26.04.3 (VERIFIED); vers=3.1.1 / noserverino spellings UNVERIFIED on the page read
# (the listing test is the proof). Contract this file defines: the share is at $ATLAS_SRV/winpc for the orchestrator's
# tools (WINPC_MOUNT in /etc/atlas/orchestrator.env).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

WINPC_CRED="$ATLAS_ETC/secrets/smb.cred"
WINPC_MOUNT="$ATLAS_SRV/winpc"

_winpc_tty_read() {
  local prompt="$1" var="$2" silent="${3:-}"
  [[ -r /dev/tty && -w /dev/tty ]] || die "step 9 needs a terminal once for the Windows share credentials; run from a console/SSH session, or create $WINPC_CRED by hand (username=, password=, domain= lines; root 600) and re-run with --force 09"
  if [[ -n "$silent" ]]; then
    read -r -s -p "$prompt" "${var?}" </dev/tty >/dev/tty; echo >/dev/tty
  else
    read -r -p "$prompt" "${var?}" </dev/tty >/dev/tty
  fi
}

_winpc_credentials() {
  ensure_dir "$ATLAS_ETC/secrets" root:root 700
  if [[ -s "$WINPC_CRED" ]] && grep -q '^username=' "$WINPC_CRED" && grep -q '^password=' "$WINPC_CRED"; then
    log "credentials file $WINPC_CRED present"
    return 0
  fi
  local user="" pass="" domain=""
  echo
  echo "  Windows PC share $WINDOWS_SHARE (Section 12.4). Enter the Windows account that can read and write it;"
  echo "  the credentials are written once to $WINPC_CRED (root, mode 600) and never printed or logged."
  _winpc_tty_read "  Windows username: " user
  [[ -n "$user" ]] || die "empty username; nothing written"
  _winpc_tty_read "  Windows password (input hidden): " pass silent
  [[ -n "$pass" ]] || die "empty password; nothing written"
  _winpc_tty_read "  Domain or workgroup [WORKGROUP]: " domain
  [[ -n "$domain" ]] || domain=WORKGROUP
  (umask 077; printf 'username=%s\npassword=%s\ndomain=%s\n' "$user" "$pass" "$domain" >"$WINPC_CRED")
  chown root:root "$WINPC_CRED"; chmod 600 "$WINPC_CRED"
  unset pass
  log "wrote $WINPC_CRED (root 600) for user $user@$domain"
}

_winpc_units() {
  local mount_unit automount_unit uid gid
  mount_unit="$(systemd-escape -p --suffix=mount "$WINPC_MOUNT")"
  automount_unit="$(systemd-escape -p --suffix=automount "$WINPC_MOUNT")"
  uid="$(id -u atlas)"; gid="$(id -g atlas)"
  ensure_dir "$WINPC_MOUNT" atlas:atlas 750
  export WINDOWS_SHARE ATLAS_SRV ATLAS_ETC ATLAS_UID="$uid" ATLAS_GID="$gid"
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/srv-atlas-winpc.mount" "/etc/systemd/system/$mount_unit" WINDOWS_SHARE ATLAS_SRV ATLAS_ETC ATLAS_UID ATLAS_GID
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/srv-atlas-winpc.automount" "/etc/systemd/system/$automount_unit" ATLAS_SRV
  grep -qF "What=$WINDOWS_SHARE" "/etc/systemd/system/$mount_unit" || die "rendered $mount_unit lost What=$WINDOWS_SHARE"
  systemctl daemon-reload
  # Never enable the .mount (it would mount at boot and hold the PC); the automount owns it (research 4.12).
  systemctl disable "$mount_unit" >/dev/null 2>&1 || true
  # Re-arm cleanly on re-runs: stop an existing mount so the new options apply on the next access.
  systemctl stop "$mount_unit" >/dev/null 2>&1 || true
  systemctl enable --now "$automount_unit" >/dev/null || die "systemctl enable --now $automount_unit failed: $(systemctl status "$automount_unit" --no-pager 2>&1 | tail -n 5)"
  systemctl is-active --quiet "$automount_unit" || die "$automount_unit is not active after enable"
  log "installed $mount_unit (not enabled) and $automount_unit (enabled, idle timeout 600 s)"
  WINPC_MOUNT_UNIT="$mount_unit"
}
WINPC_MOUNT_UNIT=""

_winpc_test() {
  local listing n fstype
  log "listing $WINPC_MOUNT through the automount (mounts $WINDOWS_SHARE on demand; up to 60 s)"
  if ! listing="$(timeout 60 ls -A "$WINPC_MOUNT" 2>&1)"; then
    journalctl -u "$WINPC_MOUNT_UNIT" --no-pager -n 20 >&2 || true
    die "listing $WINPC_MOUNT failed: $(tr '\n' ' ' <<<"$listing" | cut -c1-200). Check that the PC is on and $WINDOWS_SHARE is shared to the account in $WINPC_CRED (dmesg | grep -i cifs), then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09"
  fi
  fstype="$(findmnt -n -o FSTYPE "$WINPC_MOUNT" 2>/dev/null || true)"
  [[ "$fstype" == cifs ]] || die "$WINPC_MOUNT is not a cifs mount after the listing (fstype '${fstype:-none}'); the automount did not trigger or the mount failed (journalctl -u $WINPC_MOUNT_UNIT)"
  n="$(wc -l <<<"$listing")"; [[ -n "$listing" ]] || n=0
  # Write capability (Section 12.4 "with write capability"): a touch/rm in a scratch name as the atlas user.
  local probe="$WINPC_MOUNT/.atlas-write-test-$$"
  if svc_user_run /bin/sh -c "touch '$probe' && rm -f '$probe'" 2>/dev/null; then
    log "$WINDOWS_SHARE mounted on $WINPC_MOUNT (cifs), $n entries listed, write test ok as atlas"
  else
    warn "$WINDOWS_SHARE mounted on $WINPC_MOUNT (cifs, $n entries) but the atlas user could not create a file: the share is read-only for this account (Section 12.4 asks for write capability)"
  fi
  ensure_kv "$ATLAS_ETC/orchestrator.env" WINPC_MOUNT "$WINPC_MOUNT"
  log "the share releases after 600 s idle (TimeoutIdleSec in $(systemd-escape -p --suffix=automount "$WINPC_MOUNT"))"
}

step_09() {
  case "${WINDOWS_SHARE:-}" in
    ""|none|skip|-)
      log "WINDOWS_SHARE is not set (value '${WINDOWS_SHARE:-}'): skipping the Windows PC share mount (Section 12.4); set it in $ATLAS_ETC/atlas.env and re-run with --force 09 when the PC is available"
      return 0 ;;
  esac
  [[ "$WINDOWS_SHARE" =~ ^//[^/]+/.+$ ]] || die "WINDOWS_SHARE='$WINDOWS_SHARE' must look like //host/share"
  apt_install cifs-utils
  _winpc_credentials
  _winpc_units
  _winpc_test
  log "step 09 done: $WINDOWS_SHARE on demand at $WINPC_MOUNT"
}
