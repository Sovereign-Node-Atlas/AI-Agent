#!/usr/bin/env bash
# phase2/09b-vault.sh — Section 17 Phase 2 (between steps 9 and 10): the gocryptfs vault (Section 11, D13, 10.5, V18).
# Sourced by phase2-services.sh through run_phase_steps; defines step_09b only.
#
# What it does, in order (each part idempotent):
#   1. apt gocryptfs 2.6.1-1 (= upstream v2.6.1, services-tools.md §4.11 VERIFIED) and fuse3 (fusermount3 is the
#      setuid helper an unprivileged mount needs).
#   2. Layout under $ATLAS_SRV/vault (atlas:atlas 700): cipher/ (the real vault, what restic backs up as ciphertext),
#      open/ (the mount point the task fixes: /srv/atlas/vault/open), test-cipher/ (a second, throw-away cipher dir
#      with a random passphrase in $ATLAS_ETC/secrets/vault-test.pass, so verify/v18-vault.sh can prove the mechanics
#      unattended at the gate without ever storing the Principal's passphrase).
#   3. The vault control contract for the orchestrator (README-contracts.md "Vault"):
#        /etc/atlas/vault.env                 VAULT_CIPHER_DIR, VAULT_MOUNT_DIR, VAULT_IDLE=15m, ... (also copied into
#                                             orchestrator.env so the service sees them; the service is restarted once)
#        /usr/local/bin/atlas-vault           open|lock|status (+ init, wait-mounted, cleanup): the passphrase arrives on
#                                             STDIN, never as an argument, never in a log
#        /etc/systemd/system/atlas-vault.service   gocryptfs -fg -idle ${VAULT_IDLE} as user atlas, in the HOST mount
#                                             namespace (a mount made inside the orchestrator's own sandboxed unit
#                                             would be invisible to every other process), passfile on tmpfs shredded
#                                             the moment the mount is up (services research S5 pattern)
#        /etc/sudoers.d/atlas-vault           atlas may run exactly: atlas-vault open | lock | status
#      The orchestrator's POST /vault/open therefore pipes the passphrase into `sudo -n /usr/local/bin/atlas-vault open`.
#   4. THE ONE INTERACTIVE PAUSE OF THIS STEP: the Principal types the passphrase (read -s from /dev/tty, twice on first
#      initialisation, once on a re-run). It lives in this process's memory only, is piped to the helper, never appears
#      in argv, in a file that survives, or in any log. On first initialisation gocryptfs prints the master key
#      (the only recovery for a forgotten passphrase, D3): it is shown on the terminal only and never logged.
#   5. Mechanics proof from the shell, on the REAL vault, through the REAL button path (user atlas -> sudo-rs ->
#      atlas-vault open -> systemd unit): mount, write a file as atlas, read it back, confirm the cipher dir holds
#      neither the plaintext name nor the plaintext content, delete it, lock, confirm the mount is gone.
#   6. run_verify V18 with the Principal's passphrase piped in (the real cipher dir, a 20 s idle for the test).
#      A fail is recorded, not fatal here: the Phase 2 gate blocks on it. The gate re-runs V18 unattended on the test
#      cipher dir (verify/v18-vault.sh reads secrets/vault-test.pass when nothing arrives on stdin).
#
# Facts typed from services-tools.md §4.11 (VERIFIED man page): `gocryptfs -init [OPTIONS] CIPHERDIR`, mount
# `gocryptfs [OPTIONS] CIPHERDIR MOUNTPOINT`, `-idle duration` ("500s or 2h45m"; a process with open files or its
# cwd in the mount keeps it not idle), `-passfile FILE` (first line), `-fg`, `-q`, `-nosyslog`, `fusermount -u`,
# exit 12 = password incorrect, 10 = mount point not empty, 6 = cipher dir not empty on -init. No -allow_other: the
# kernel then denies every other user, root included, which is what keeps restic (root) out of the plaintext view.
# UNVERIFIED: that a systemd unit with ProtectSystem= (the orchestrator) sees a FUSE mount made later on the host —
# slave propagation is the documented default, and v18 proves it through the orchestrator's GET /vault/status.
#
# Contracts relied on from other writers: /run/atlas is the orchestrator's RuntimeDirectory (atlas-orchestrator.service,
# RuntimeDirectoryPreserve=yes) — created here when absent so the helper works before the service ever ran;
# $ATLAS_ETC/orchestrator.env is written by phase2/02-orchestrator.sh with ensure_kv (keys other steps add are kept);
# `atlas-admin vault-session-test --file PATH` and GET /vault/status are the orchestrator's (README-contracts.md).
# phase2/07-restic.sh runs restic with --one-file-system (the FUSE mount is never descended) but its exclude list
# names $ATLAS_SRV/vault-open, not this mount point: recorded in README-contracts.md.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

VAULT_ROOT="$ATLAS_SRV/vault"
VAULT_CIPHER_DIR="$VAULT_ROOT/cipher"
VAULT_MOUNT_DIR="$VAULT_ROOT/open"
VAULT_TEST_CIPHER_DIR="$VAULT_ROOT/test-cipher"
VAULT_TEST_PASS_FILE="$ATLAS_ETC/secrets/vault-test.pass"
VAULT_ENV_FILE="$ATLAS_ETC/vault.env"
VAULT_HELPER=/usr/local/bin/atlas-vault
VAULT_UNIT=atlas-vault.service
VAULT_SUDOERS=/etc/sudoers.d/atlas-vault
VAULT_IDLE=15m                      # D13: auto-lock after 15 minutes idle
VAULT_RUN_DIR=/run/atlas            # the orchestrator's RuntimeDirectory (tmpfs)
VAULT_PASS_FILE="$VAULT_RUN_DIR/vault-pass"
VAULT_OVERRIDE_FILE="$VAULT_RUN_DIR/vault-override.env"

# vault_is_mounted DIR — true when DIR is a mount point, read from /proc/self/mountinfo (field 5). `mountpoint -q`
# stats the directory, which the FUSE kernel driver denies to every user but the mount owner (root included).
vault_is_mounted() {
  awk -v m="$1" '$5 == m { f = 1 } END { exit !f }' /proc/self/mountinfo
}

_vault_apt() {
  apt_install gocryptfs fuse3
  local ver
  ver="$(gocryptfs -version 2>/dev/null | head -n1 || true)"
  [[ -n "$ver" ]] || die "gocryptfs -version printed nothing after apt_install (package gocryptfs 2.6.1-1 expected)"
  command -v fusermount3 >/dev/null || die "fusermount3 missing (package fuse3): unprivileged FUSE mounts cannot work"
  log "gocryptfs: $ver"
}

_vault_dirs() {
  ensure_dir "$VAULT_ROOT" atlas:atlas 700
  ensure_dir "$VAULT_CIPHER_DIR" atlas:atlas 700
  ensure_dir "$VAULT_MOUNT_DIR" atlas:atlas 700
  ensure_dir "$VAULT_TEST_CIPHER_DIR" atlas:atlas 700
  ensure_dir "$ATLAS_ETC/secrets" root:root 700
  install -d -m 750 -o atlas -g atlas "$VAULT_RUN_DIR"
  log "vault layout: cipher $VAULT_CIPHER_DIR, mount $VAULT_MOUNT_DIR, test cipher $VAULT_TEST_CIPHER_DIR (atlas:atlas 700)"
}

_vault_env() {
  [[ -e "$VAULT_ENV_FILE" ]] || : >"$VAULT_ENV_FILE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_CIPHER_DIR "$VAULT_CIPHER_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_MOUNT_DIR "$VAULT_MOUNT_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_TEST_CIPHER_DIR "$VAULT_TEST_CIPHER_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_IDLE "$VAULT_IDLE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_UNIT "$VAULT_UNIT"
  ensure_kv "$VAULT_ENV_FILE" VAULT_HELPER "$VAULT_HELPER"
  ensure_kv "$VAULT_ENV_FILE" VAULT_PASS_FILE "$VAULT_PASS_FILE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_OVERRIDE_FILE "$VAULT_OVERRIDE_FILE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_USER atlas
  chown root:root "$VAULT_ENV_FILE"
  chmod 644 "$VAULT_ENV_FILE"          # no secret in it; the unit and the helper (as atlas) read it
  # The orchestrator service loads orchestrator.env, not vault.env: mirror the keys the package needs (step 02 wrote
  # that file with ensure_kv, which keeps foreign keys). Restarted once below if anything changed.
  local orch="$ATLAS_ETC/orchestrator.env"
  [[ -e "$orch" ]] || die "$orch missing: Phase 2 step 2 has not run (it writes orchestrator.env)"
  ensure_kv "$orch" VAULT_CIPHER_DIR "$VAULT_CIPHER_DIR"
  ensure_kv "$orch" VAULT_MOUNT_DIR "$VAULT_MOUNT_DIR"
  ensure_kv "$orch" VAULT_IDLE "$VAULT_IDLE"
  ensure_kv "$orch" VAULT_UNIT "$VAULT_UNIT"
  ensure_kv "$orch" VAULT_HELPER "$VAULT_HELPER"
  log "wrote $VAULT_ENV_FILE and the VAULT_* keys of $orch"
}

_vault_helper() {
  local tmp
  tmp="$(mktemp)"
  # The helper is a plain bash script; every path comes from /etc/atlas/vault.env at run time.
  cat >"$tmp" <<'HELPER'
#!/usr/bin/env bash
# atlas-vault — the A.T.L.A.S. vault control contract (Section 11, D13). Written by scripts/day1/phase2/09b-vault.sh.
#
#   atlas-vault open     passphrase on STDIN (one line, newline optional); starts atlas-vault.service; prints "open";
#                        exit 0 when the plaintext view is mounted, 1 when the passphrase was refused or the mount
#                        did not appear, 2 on a usage/contract error. Root, or user atlas through sudo -n.
#   atlas-vault lock     stops the unit (fusermount3 fallback); prints "locked"; exit 0 when unmounted.
#   atlas-vault status   prints "open" or "locked"; exit 0.
#   atlas-vault init     passphrase on STDIN; gocryptfs -init of the cipher dir; prints gocryptfs's output (the master
#                        key line) on stdout for the caller to show ONCE; root only; never used by the orchestrator.
#   atlas-vault wait-mounted | cleanup   used by atlas-vault.service only (ExecStartPost / ExecStopPost).
#
# Overrides, honoured only when the caller is root and NOT sudo (verification runs, never the button):
#   VAULT_CIPHER_OVERRIDE=<dir>   mount/init this cipher dir instead of VAULT_CIPHER_DIR (the test vault)
#   VAULT_IDLE_OVERRIDE=<dur>     e.g. 20s instead of VAULT_IDLE (15m)
# The passphrase is never an argument, never logged, and lives on tmpfs (VAULT_PASS_FILE, mode 600, owner atlas) only
# between the button press and the mount; wait-mounted and cleanup shred it.
set -Eeuo pipefail
ENV_FILE=/etc/atlas/vault.env
[[ -r "$ENV_FILE" ]] || { echo "atlas-vault: $ENV_FILE missing (scripts/day1/phase2/09b-vault.sh writes it)" >&2; exit 2; }
set -a
# shellcheck disable=SC1090  # KEY=VALUE lines written by 09b-vault.sh
source "$ENV_FILE"
set +a
: "${VAULT_CIPHER_DIR:?}" "${VAULT_MOUNT_DIR:?}"
: "${VAULT_IDLE:=15m}" "${VAULT_UNIT:=atlas-vault.service}" "${VAULT_USER:=atlas}"
: "${VAULT_PASS_FILE:=/run/atlas/vault-pass}" "${VAULT_OVERRIDE_FILE:=/run/atlas/vault-override.env}"

is_mounted() { awk -v m="$VAULT_MOUNT_DIR" '$5 == m { f = 1 } END { exit !f }' /proc/self/mountinfo; }
need_root() { [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "atlas-vault $1: needs root (the orchestrator runs: sudo -n /usr/local/bin/atlas-vault $1)" >&2; exit 2; }; }
shred_pass() {
  [[ -e "$VAULT_PASS_FILE" ]] || return 0
  shred -u "$VAULT_PASS_FILE" 2>/dev/null || rm -f "$VAULT_PASS_FILE"
}

# read_pass — one line from stdin into the tmpfs passfile (owner atlas, 600). -s keeps a terminal from echoing;
# -t bounds a caller that forgot to pipe anything.
read_pass() {
  local p=""
  IFS= read -r -s -t 120 p || true
  # shellcheck disable=SC2016  # the "$pass" in the hint is a literal example for the caller
  [[ -n "$p" ]] || { printf 'atlas-vault: no passphrase arrived on stdin within 120 s (pipe it: printf "%%s\\n" "$pass" | atlas-vault %s)\n' "$1" >&2; exit 2; }
  install -d -m 750 -o "$VAULT_USER" -g "$VAULT_USER" "$(dirname "$VAULT_PASS_FILE")"
  shred_pass
  (umask 077; printf '%s\n' "$p" >"$VAULT_PASS_FILE")
  chown "$VAULT_USER:$VAULT_USER" "$VAULT_PASS_FILE"
  chmod 600 "$VAULT_PASS_FILE"
  p=""
}

write_overrides() {
  rm -f "$VAULT_OVERRIDE_FILE"
  # sudo-rs sets SUDO_USER for the command it runs; the button path never gets overrides.
  [[ -z "${SUDO_USER:-}" ]] || return 0
  [[ -n "${VAULT_IDLE_OVERRIDE:-}" || -n "${VAULT_CIPHER_OVERRIDE:-}" ]] || return 0
  {
    [[ -z "${VAULT_IDLE_OVERRIDE:-}" ]] || printf 'VAULT_IDLE=%s\n' "$VAULT_IDLE_OVERRIDE"
    [[ -z "${VAULT_CIPHER_OVERRIDE:-}" ]] || printf 'VAULT_CIPHER_DIR=%s\n' "$VAULT_CIPHER_OVERRIDE"
  } >"$VAULT_OVERRIDE_FILE"
  chown "root:$VAULT_USER" "$VAULT_OVERRIDE_FILE"
  chmod 640 "$VAULT_OVERRIDE_FILE"
}

cleanup() {
  shred_pass
  rm -f "$VAULT_OVERRIDE_FILE"
  if is_mounted; then
    fusermount3 -u -z "$VAULT_MOUNT_DIR" 2>/dev/null || umount -l "$VAULT_MOUNT_DIR" 2>/dev/null || true
  fi
}

do_open() {
  need_root open
  if is_mounted; then echo "open"; return 0; fi
  [[ -f "${VAULT_CIPHER_OVERRIDE:-$VAULT_CIPHER_DIR}/gocryptfs.conf" ]] \
    || { echo "atlas-vault open: ${VAULT_CIPHER_OVERRIDE:-$VAULT_CIPHER_DIR}/gocryptfs.conf missing: the vault is not initialised (phase2/09b-vault.sh)" >&2; exit 2; }
  write_overrides
  read_pass open
  systemctl reset-failed "$VAULT_UNIT" 2>/dev/null || true
  if ! systemctl start "$VAULT_UNIT"; then
    cleanup
    journalctl -u "$VAULT_UNIT" --no-pager -n 8 >&2 || true
    echo "atlas-vault open: $VAULT_UNIT did not start (gocryptfs exit 12 = passphrase incorrect; journal above)" >&2
    exit 1
  fi
  if ! is_mounted; then
    cleanup
    systemctl stop "$VAULT_UNIT" 2>/dev/null || true
    echo "atlas-vault open: $VAULT_UNIT is active but $VAULT_MOUNT_DIR is not a mount point" >&2
    exit 1
  fi
  echo "open"
}

do_lock() {
  need_root lock
  systemctl stop "$VAULT_UNIT" 2>/dev/null || true
  local _i
  for _i in $(seq 1 15); do
    is_mounted || break
    sleep 1
  done
  if is_mounted; then
    fusermount3 -u "$VAULT_MOUNT_DIR" 2>/dev/null || umount "$VAULT_MOUNT_DIR" 2>/dev/null || umount -l "$VAULT_MOUNT_DIR" 2>/dev/null || true
    sleep 1
  fi
  shred_pass
  rm -f "$VAULT_OVERRIDE_FILE"
  if is_mounted; then
    echo "atlas-vault lock: $VAULT_MOUNT_DIR is still mounted (a process holds files open in it: lsof +f -- $VAULT_MOUNT_DIR)" >&2
    exit 1
  fi
  echo "locked"
}

do_status() { if is_mounted; then echo "open"; else echo "locked"; fi; }

do_init() {
  need_root init
  local cipher="${VAULT_CIPHER_OVERRIDE:-$VAULT_CIPHER_DIR}"
  if [[ -f "$cipher/gocryptfs.conf" ]]; then
    echo "atlas-vault init: $cipher is already initialised (gocryptfs.conf present); nothing done" >&2
    return 0
  fi
  [[ -d "$cipher" ]] || { echo "atlas-vault init: $cipher does not exist" >&2; exit 2; }
  read_pass init
  local out rc=0
  # No -q: gocryptfs prints the master key on init and the caller must be able to show it once (D3).
  out="$(runuser -u "$VAULT_USER" -- gocryptfs -init -passfile "$VAULT_PASS_FILE" "$cipher" 2>&1)" || rc=$?
  shred_pass
  if (( rc != 0 )) || [[ ! -f "$cipher/gocryptfs.conf" ]]; then
    echo "atlas-vault init: gocryptfs -init failed (exit $rc; 6 = cipher dir not empty, 22 = empty passphrase): $out" >&2
    exit 1
  fi
  printf '%s\n' "$out"
}

# --- unit-internal ----------------------------------------------------------------------------------------------------
do_wait_mounted() {
  # ExecStartPost (runs as atlas): the unit is "started" only once the plaintext view exists; then the passfile goes.
  local _i
  for _i in $(seq 1 30); do
    if is_mounted; then shred_pass; rm -f "$VAULT_OVERRIDE_FILE"; return 0; fi
    sleep 1
  done
  shred_pass
  rm -f "$VAULT_OVERRIDE_FILE"
  echo "atlas-vault wait-mounted: $VAULT_MOUNT_DIR did not become a mount point within 30 s" >&2
  exit 1
}

case "${1:-}" in
  open) do_open ;;
  lock) do_lock ;;
  status) do_status ;;
  init) do_init ;;
  wait-mounted) do_wait_mounted ;;
  cleanup) cleanup ;;
  *) echo "usage: atlas-vault open|lock|status|init   (passphrase on stdin for open and init)" >&2; exit 2 ;;
esac
HELPER
  bash -n "$tmp" || { rm -f "$tmp"; die "the embedded atlas-vault helper does not parse (bash -n)"; }
  install -m 755 -o root -g root "$tmp" "$VAULT_HELPER"
  rm -f "$tmp"
  log "installed $VAULT_HELPER"
}

_vault_unit() {
  local tmp
  tmp="$(mktemp)"
  # ${VAULT_*} below are systemd substitutions from the EnvironmentFiles and stay literal (escaped in this heredoc).
  cat >"$tmp" <<UNIT
# /etc/systemd/system/atlas-vault.service — the open vault (Section 11, D13). Written by scripts/day1/phase2/09b-vault.sh.
# Started ONLY by /usr/local/bin/atlas-vault open (the interface button path), never at boot: gocryptfs runs in the
# foreground as user atlas in the host mount namespace, auto-unmounts after \${VAULT_IDLE} idle and then exits, so
# "systemctl is-active atlas-vault" mirrors the vault state. The passfile is tmpfs and is shredded by wait-mounted.
# NoNewPrivileges must stay off: an unprivileged FUSE mount goes through the setuid fusermount3.
[Unit]
Description=A.T.L.A.S. vault (gocryptfs plaintext view, auto-locks after \${VAULT_IDLE} idle)
Documentation=file:$VAULT_ENV_FILE
ConditionPathExists=$VAULT_PASS_FILE

[Service]
Type=simple
User=atlas
Group=atlas
EnvironmentFile=$VAULT_ENV_FILE
EnvironmentFile=-$VAULT_OVERRIDE_FILE
ExecStart=/usr/bin/gocryptfs -fg -q -nosyslog -idle \${VAULT_IDLE} -passfile $VAULT_PASS_FILE \${VAULT_CIPHER_DIR} \${VAULT_MOUNT_DIR}
ExecStartPost=$VAULT_HELPER wait-mounted
ExecStopPost=-$VAULT_HELPER cleanup
Restart=no
TimeoutStartSec=60
TimeoutStopSec=30
KillMode=control-group
NoNewPrivileges=false
UNIT
  install -m 644 -o root -g root "$tmp" "/etc/systemd/system/$VAULT_UNIT"
  rm -f "$tmp"
  systemctl daemon-reload
  systemctl disable "$VAULT_UNIT" >/dev/null 2>&1 || true   # never at boot; the button starts it
  log "installed /etc/systemd/system/$VAULT_UNIT"
}

_vault_sudoers() {
  # CONVENTIONS.md §8 pattern (like atlas-engines): plain sudoers syntax only, sudo-rs on 26.04 (conflict 7).
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<SUDO
# atlas-vault — written by scripts/day1/phase2/09b-vault.sh (Section 11). The orchestrator's /vault/open and
# /vault/lock endpoints run exactly these, with the passphrase on stdin. Nothing else is permitted.
atlas ALL=(root) NOPASSWD: $VAULT_HELPER open
atlas ALL=(root) NOPASSWD: $VAULT_HELPER lock
atlas ALL=(root) NOPASSWD: $VAULT_HELPER status
SUDO
  if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment failed visudo -c; not installed"; }
  else
    warn "visudo not found (sudo-rs without it?); installing $VAULT_SUDOERS unchecked"
  fi
  install -m 440 -o root -g root "$tmp" "$VAULT_SUDOERS"
  rm -f "$tmp"
  # Proof that sudo-rs accepts the fragment for the atlas user (status needs no passphrase).
  local st
  st="$(runuser -u atlas -- sudo -n "$VAULT_HELPER" status 2>&1)" \
    || die "'sudo -n $VAULT_HELPER status' as atlas failed under sudo-rs: $st (check: runuser -u atlas -- sudo -n -l)"
  log "installed $VAULT_SUDOERS (atlas -> sudo -n atlas-vault status: $st)"
}

# _vault_tty_pass VAR PROMPT — read a hidden line from the terminal into VAR (never echoed, never logged).
_vault_tty_pass() {
  local prompt="$2"
  [[ -r /dev/tty && -w /dev/tty ]] || die "step 9b needs a terminal for the vault passphrase (Section 11); run it from the console or an interactive SSH session, or later: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  read -r -s -p "$prompt" "${1?}" </dev/tty >/dev/tty
  echo >/dev/tty
}

# Sets VAULT_PASS in the caller's scope (the only place the passphrase ever lives).
_vault_prompt() {
  echo >/dev/tty
  echo "The vault (Section 11) is an encrypted folder at $VAULT_CIPHER_DIR, opened by the interface button and locked" >/dev/tty
  echo "after $VAULT_IDLE idle. Type its passphrase now: it is used for this step's checks and then forgotten. It is never" >/dev/tty
  echo "stored, never logged, and there is no reset: a forgotten passphrase means the master key shown below or nothing (D3)." >/dev/tty
  local p1 p2
  if [[ -f "$VAULT_CIPHER_DIR/gocryptfs.conf" ]]; then
    _vault_tty_pass p1 "Vault passphrase (existing vault, input hidden): "
  else
    _vault_tty_pass p1 "New vault passphrase (input hidden): "
    _vault_tty_pass p2 "Repeat it: "
    [[ "$p1" == "$p2" ]] || die "the two passphrases differ; nothing written, re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2"
  fi
  (( ${#p1} >= 8 )) || die "the vault passphrase must be at least 8 characters; nothing written, re-run"
  VAULT_PASS="$p1"
  p1=""; p2=""
}

_vault_init_real() {
  if [[ -f "$VAULT_CIPHER_DIR/gocryptfs.conf" ]]; then
    log "vault already initialised ($VAULT_CIPHER_DIR/gocryptfs.conf present)"
    return 0
  fi
  local out
  out="$(printf '%s\n' "$VAULT_PASS" | "$VAULT_HELPER" init)" || die "vault initialisation failed (see the message above)"
  [[ -f "$VAULT_CIPHER_DIR/gocryptfs.conf" ]] || die "gocryptfs -init returned 0 but $VAULT_CIPHER_DIR/gocryptfs.conf is missing"
  # The master key: terminal only, never the log (D3: the Principal's off-node responsibility, beside the LUKS key).
  {
    echo
    echo "=================================================================================================="
    echo " VAULT MASTER KEY — write it down beside the LUKS recovery key and the restic passphrase (D3)."
    echo " It is the ONLY way back in if the passphrase is forgotten (gocryptfs -masterkey). It is not logged"
    echo " and will not be shown again."
    echo "--------------------------------------------------------------------------------------------------"
    printf '%s\n' "$out" | grep -E -A3 -i 'master key' || printf '%s\n' "$out"
    echo "=================================================================================================="
  } >/dev/tty
  local answer=""
  while [[ "$answer" != "WRITTEN DOWN" ]]; do
    read -r -p "Type WRITTEN DOWN to continue: " answer </dev/tty >/dev/tty
  done
  log "vault initialised at $VAULT_CIPHER_DIR (master key shown once on the terminal, confirmed written down)"
}

_vault_init_test() {
  if [[ ! -s "$VAULT_TEST_PASS_FILE" ]]; then
    (umask 077; head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40 >"$VAULT_TEST_PASS_FILE"; echo >>"$VAULT_TEST_PASS_FILE")
    chown root:root "$VAULT_TEST_PASS_FILE"; chmod 600 "$VAULT_TEST_PASS_FILE"
    log "generated the test-vault passphrase into $VAULT_TEST_PASS_FILE (root 600; it protects nothing but the gate's V18 run)"
  fi
  if [[ -f "$VAULT_TEST_CIPHER_DIR/gocryptfs.conf" ]]; then
    log "test vault already initialised ($VAULT_TEST_CIPHER_DIR)"
    return 0
  fi
  VAULT_CIPHER_OVERRIDE="$VAULT_TEST_CIPHER_DIR" "$VAULT_HELPER" init <"$VAULT_TEST_PASS_FILE" >/dev/null \
    || die "test vault initialisation failed at $VAULT_TEST_CIPHER_DIR"
  [[ -f "$VAULT_TEST_CIPHER_DIR/gocryptfs.conf" ]] || die "test vault: gocryptfs.conf missing after init"
  log "test vault initialised at $VAULT_TEST_CIPHER_DIR"
}

# The mechanics, on the real vault, through the real button path: atlas -> sudo-rs -> atlas-vault open -> unit.
_vault_mechanics() {
  local name content st
  name=".atlas-vault-mechanics-$(date +%s)"
  content="ATLAS vault mechanics check $(date -Is) $(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  if vault_is_mounted "$VAULT_MOUNT_DIR"; then
    log "vault is open already; locking it first"
    "$VAULT_HELPER" lock >/dev/null || die "could not lock the open vault at $VAULT_MOUNT_DIR"
  fi
  st="$(printf '%s\n' "$VAULT_PASS" | runuser -u atlas -- sudo -n "$VAULT_HELPER" open 2>&1)" \
    || die "the button path failed: 'sudo -n $VAULT_HELPER open' as atlas with the passphrase on stdin -> $st"
  vault_is_mounted "$VAULT_MOUNT_DIR" || die "atlas-vault open printed '$st' but $VAULT_MOUNT_DIR is not mounted"
  systemctl is-active --quiet "$VAULT_UNIT" || die "$VAULT_MOUNT_DIR is mounted but $VAULT_UNIT is not active (the mount did not come from the unit)"
  [[ ! -e "$VAULT_PASS_FILE" ]] || die "$VAULT_PASS_FILE still exists after the mount (wait-mounted must shred it)"
  # Write and read back as atlas (the mount owner; root is denied by the FUSE kernel driver, which is the design).
  # shellcheck disable=SC2016  # $1/$2 are expanded by the inner bash, on purpose (content never touches argv parsing here)
  runuser -u atlas -- bash -c 'printf "%s\n" "$1" >"$2"' _ "$content" "$VAULT_MOUNT_DIR/$name" \
    || die "could not write $VAULT_MOUNT_DIR/$name as atlas"
  local back
  back="$(runuser -u atlas -- cat "$VAULT_MOUNT_DIR/$name")"
  [[ "$back" == "$content" ]] || die "read-back mismatch in the open vault"
  # Ciphertext only on disk: neither the plaintext name nor the plaintext content may appear in the cipher dir.
  [[ ! -e "$VAULT_CIPHER_DIR/$name" ]] || die "the plaintext file name appears in the cipher dir (names are not encrypted?)"
  if grep -rqF -- "$content" "$VAULT_CIPHER_DIR"; then
    die "the plaintext content appears in $VAULT_CIPHER_DIR (content not encrypted?)"
  fi
  local nfiles
  nfiles="$(find "$VAULT_CIPHER_DIR" -type f | wc -l)"
  (( nfiles >= 3 )) || die "expected gocryptfs.conf, gocryptfs.diriv and one encrypted file in $VAULT_CIPHER_DIR, found $nfiles files"
  runuser -u atlas -- rm -f "$VAULT_MOUNT_DIR/$name"
  st="$(runuser -u atlas -- sudo -n "$VAULT_HELPER" lock 2>&1)" || die "'sudo -n $VAULT_HELPER lock' as atlas failed: $st"
  if vault_is_mounted "$VAULT_MOUNT_DIR"; then die "vault still mounted after lock"; fi
  if systemctl is-active --quiet "$VAULT_UNIT"; then die "$VAULT_UNIT still active after lock"; fi
  [[ -z "$(ls -A "$VAULT_MOUNT_DIR")" ]] || die "$VAULT_MOUNT_DIR is not empty while locked (something wrote into the mount point)"
  log "mechanics: open (atlas -> sudo-rs -> atlas-vault -> $VAULT_UNIT), write+read as atlas, ciphertext-only on disk ($nfiles files), lock: all confirmed"
}

_vault_restart_orchestrator() {
  # The service reads VAULT_* from orchestrator.env at start; a running orchestrator needs one restart to see them.
  systemctl is-active --quiet atlas-orchestrator || { log "atlas-orchestrator not running; nothing to restart"; return 0; }
  local port="${ORCH_PORT:-8800}"
  systemctl restart atlas-orchestrator || die "systemctl restart atlas-orchestrator failed"
  wait_http "http://127.0.0.1:$port/health" 180 || die "the orchestrator did not answer 200 on /health within 180 s after the restart (journalctl -u atlas-orchestrator)"
  log "atlas-orchestrator restarted with the VAULT_* settings; /health 200"
}

step_09b() {
  _vault_apt
  _vault_dirs
  _vault_env
  _vault_helper
  _vault_unit
  _vault_sudoers
  _vault_restart_orchestrator
  VAULT_PASS=""
  _vault_prompt
  _vault_init_real
  _vault_init_test
  _vault_mechanics
  # V18 on the real vault with the real passphrase (the test itself uses a 20 s idle). Recorded, not fatal: the gate
  # blocks on a fail. The gate re-runs it on the test vault without any passphrase from the Principal.
  if ! printf '%s\n' "$VAULT_PASS" | run_verify V18 v18-vault.sh "$VAULT_CIPHER_DIR"; then
    warn "V18 recorded as fail (see verify.jsonl); the Phase 2 gate will block until it passes"
  fi
  VAULT_PASS=""
  unset VAULT_PASS
  log "step 09b done: vault at $VAULT_CIPHER_DIR (mount $VAULT_MOUNT_DIR, idle $VAULT_IDLE), helper $VAULT_HELPER, unit $VAULT_UNIT, sudoers $VAULT_SUDOERS"
  notify "Phase 2 step 9b done: vault initialised and proven (V18 recorded)"
}
