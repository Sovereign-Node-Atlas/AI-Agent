#!/usr/bin/env bash
# phase2/07-restic.sh — Section 17 Phase 2 step 7: the AEGIS backup repository (Section 9.5; Appendix C; D3; D9; V13).
# Sourced by phase2-services.sh through run_phase_steps; defines step_07 only.
#
# Order (each part idempotent):
#   1. apt restic (0.18.1-3ubuntu1, VERIFIED resolute).
#   2. Passphrase: $ATLAS_ETC/secrets/restic.pass (root 600), generated once and printed ONCE in a framed block for the
#      Principal to store off-node beside the LUKS recovery key (D3, R16) — the one interactive pause of this step,
#      like Phase 1 step 2: it waits for the words WRITTEN DOWN and never prints the passphrase again.
#   3. $ATLAS_ETC/restic.env (RESTIC_REPOSITORY=/srv/backups/restic on the 4 TB OS drive, RESTIC_PASSWORD_FILE,
#      RESTIC_CACHE_DIR) and the include/exclude sets of Appendix C:
#        include: $ATLAS_SRV/{data,workspace,sandbox,vault (ciphertext as-is)}, /srv/cold, $ATLAS_OPT/orchestrator,
#                 $ATLAS_ETC/atlas.env and the other non-secret *.env settings, the Open WebUI data dir, $ATLAS_STATE
#        exclude: $ATLAS_SRV/models, $ATLAS_SRV/engines (weights: manifests only, copied by the unit), $ATLAS_ETC/secrets
#                 entirely (CONVENTIONS.md §2), staging, the automount/FUSE mount points, caches.
#   4. `restic init` when the repository does not exist yet; a canary file for V13.
#   5. /etc/sudoers.d/atlas-aegis: the orchestrator's manual [EXECUTE AEGIS BACKUP] trigger runs
#      `sudo systemctl start atlas-aegis.service` and nothing else.
#   6. Units: atlas-aegis.service/.timer (nightly 02:30: orchestrator freeze -> restic backup -> forget --keep-daily 30
#      --keep-monthly 12 --prune -> thaw; the freeze/thaw enqueues are tolerated failures so the backup runs even when
#      the orchestrator is down — see the unit header) and atlas-restic-check.service/.timer (quarterly restore test).
#   7. First backup now (systemctl start atlas-aegis.service), then run_verify V13 v13-restic.sh (restore the canary to
#      a scratch dir, sha256 compare, restic check). A V13 fail is recorded, not fatal here: the gate blocks on it.
#
# Contracts: `atlas-admin enqueue aegis-freeze|aegis-thaw --wait N` (phase2/02-orchestrator.sh header) is called by
# the unit with a leading "-": absent or failing, the backup still runs. orch_admin/ORCH_ENV come from step 02.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

RESTIC_PASS_FILE="$ATLAS_ETC/secrets/restic.pass"
RESTIC_CONFIRMED="$ATLAS_STATE/restic.pass-confirmed"
RESTIC_ENV_FILE="$ATLAS_ETC/restic.env"
RESTIC_INCLUDE="$ATLAS_ETC/restic-include.txt"
RESTIC_EXCLUDE="$ATLAS_ETC/restic-exclude.txt"
RESTIC_REPO="/srv/backups/restic"
RESTIC_CACHE="/var/cache/restic"
RESTIC_CANARY="$ATLAS_SRV/data/restic-canary.txt"

_restic_tty_read() {
  local prompt="$1" var="$2"
  [[ -r /dev/tty && -w /dev/tty ]] || die "step 7 needs a terminal for the passphrase pause (D3); run it from the console or an interactive SSH session, or re-run with --force 07 later"
  read -r -p "$prompt" "${var?}" </dev/tty >/dev/tty
}

_restic_passphrase() {
  ensure_dir "$ATLAS_ETC/secrets" root:root 700
  if [[ ! -s "$RESTIC_PASS_FILE" ]]; then
    (umask 077; head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40 >"$RESTIC_PASS_FILE"; echo >>"$RESTIC_PASS_FILE")
    chown root:root "$RESTIC_PASS_FILE"; chmod 600 "$RESTIC_PASS_FILE"
    rm -f "$RESTIC_CONFIRMED"
    log "generated the restic passphrase into $RESTIC_PASS_FILE (root 600)"
  fi
  if [[ -e "$RESTIC_CONFIRMED" ]]; then
    log "restic passphrase already confirmed as written down on $(cat "$RESTIC_CONFIRMED"); not printing it again"
    return 0
  fi
  local answer="" line pass
  pass="$(head -n1 "$RESTIC_PASS_FILE")"
  line="$(printf '#%.0s' $(seq 1 78))"
  {
    echo; echo "$line"; echo "#"
    echo "#   RESTIC (AEGIS BACKUP) PASSPHRASE — repository $RESTIC_REPO"
    echo "#   Printed ONCE. Write it down now and store it on the external USB drive that lives AWAY"
    echo "#   from the node, with the LUKS recovery key (D3, R16). A backup whose passphrase died with"
    echo "#   the machine is useless (Section 9.5)."
    echo "#"
    echo "#       $pass"
    echo "#"
    echo "#   On-node copy (root only, excluded from every backup): $RESTIC_PASS_FILE"
    echo "#"; echo "$line"; echo
  } >/dev/tty 2>/dev/null || die "cannot print the restic passphrase: no terminal (re-run step 7 from a console: --force 07)"
  while [[ "$answer" != "WRITTEN DOWN" ]]; do
    _restic_tty_read 'Type exactly  WRITTEN DOWN  to continue: ' answer
  done
  date -Is >"$RESTIC_CONFIRMED"
  clear 2>/dev/null || true
  log "restic passphrase confirmed as written down (the passphrase itself is never logged)"
}

_restic_files() {
  ensure_dir /srv/backups root:root 700
  ensure_dir "$RESTIC_CACHE" root:root 700
  {
    echo "# restic environment (Section 9.5). Written by Phase 2 step 7; read by atlas-aegis.service, atlas-restic-check.service, verify/v13-restic.sh."
    echo "RESTIC_REPOSITORY=$RESTIC_REPO"
    echo "RESTIC_PASSWORD_FILE=$RESTIC_PASS_FILE"
    echo "RESTIC_CACHE_DIR=$RESTIC_CACHE"
  } | install -m 600 -o root -g root /dev/stdin "$RESTIC_ENV_FILE"

  # Include set (Appendix C, task list). Paths that do not exist yet (memory.env before step 4, the Open WebUI dir
  # before step 3 on a --force re-run) make restic exit 3 ("some files could not be read"); the unit accepts 3.
  local inc=(
    "$ATLAS_SRV/data"
    "$ATLAS_SRV/workspace"
    "$ATLAS_SRV/sandbox"
    "$ATLAS_SRV/vault"
    "/srv/cold"
    "$ATLAS_OPT/orchestrator"
    "$ATLAS_ETC/atlas.env"
    "$ATLAS_ETC/core.env"
    "$ATLAS_ETC/orchestrator.env"
    "$ATLAS_ETC/memory.env"
    "$ATLAS_ETC/restic.env"
    "$ATLAS_ETC/restic-include.txt"
    "$ATLAS_ETC/restic-exclude.txt"
    "$ATLAS_ETC/engines"
    "$ATLAS_SRV/data/open-webui"
    "$ATLAS_STATE"
  )
  {
    echo "# restic --files-from: the AEGIS include set (Appendix C). One path per line; secrets are never listed (CONVENTIONS.md §2)."
    printf '%s\n' "${inc[@]}"
  } | install -m 644 -o root -g root /dev/stdin "$RESTIC_INCLUDE"
  {
    echo "# restic --exclude-file: weights (manifests are copied into data/manifests by the unit), secrets, staging, mounts, caches."
    echo "$ATLAS_SRV/models"
    echo "$ATLAS_SRV/engines"
    echo "$ATLAS_ETC/secrets"
    echo "$ATLAS_SRV/staging"
    echo "$ATLAS_SRV/winpc"          # cifs automount (step 9): never trigger it from a backup
    echo "$ATLAS_SRV/gdrive"         # rclone Drive mounts (Section 13), if the integrations writer places them here
    echo "$ATLAS_SRV/vault-open"     # a gocryptfs plaintext view, if mounted; the vault ciphertext dir is what is backed up (11)
    echo "$ATLAS_STATE/pip-cache"
    echo "$ATLAS_STATE/restore-test.*"
    echo "$ATLAS_OPT/orchestrator/.venv"
    echo "**/__pycache__"
    echo "**/.pytest_cache"
    echo "**/.ruff_cache"
    echo "**/*.pyc"
  } | install -m 644 -o root -g root /dev/stdin "$RESTIC_EXCLUDE"
  grep -qx "$ATLAS_ETC/secrets" "$RESTIC_EXCLUDE" || die "secrets exclusion missing from $RESTIC_EXCLUDE"
  log "wrote $RESTIC_ENV_FILE, $RESTIC_INCLUDE (${#inc[@]} paths), $RESTIC_EXCLUDE"
}

_restic_env() {
  set -a
  # shellcheck disable=SC1090  # KEY=VALUE lines written just above
  source "$RESTIC_ENV_FILE"
  set +a
}

_restic_init() {
  _restic_env
  # UNVERIFIED by the research: `--files-from`; VERIFIED there: init, backup, forget, restore, check. Assert the flag now
  # so the unit never fails at 02:30 for a flag that does not exist.
  restic backup --help 2>&1 | grep -q -- '--files-from' || die "this restic ($(restic version 2>&1 | head -n1)) has no --files-from flag; atlas-aegis.service relies on it"
  if restic snapshots >/dev/null 2>&1; then
    log "restic repository $RESTIC_REPO already initialised"
  else
    log "restic init $RESTIC_REPO"
    restic init >/dev/null || die "restic init failed for $RESTIC_REPO (wrong passphrase file or an unreadable repository dir?)"
  fi
  # V13 canary: a known file whose sha256 the restore test compares.
  ensure_dir "$ATLAS_SRV/data" atlas:atlas 755
  printf 'ATLAS restic canary %s %s\n' "$(date -Is)" "$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" >"$RESTIC_CANARY"
  chown atlas:atlas "$RESTIC_CANARY"; chmod 644 "$RESTIC_CANARY"
}

_restic_sudoers() {
  local frag=/etc/sudoers.d/atlas-aegis sc tmp
  sc="$(readlink -f "$(command -v systemctl)")"
  tmp="$(mktemp)"
  cat >"$tmp" <<SUDO
# atlas-aegis — written by scripts/day1/phase2/07-restic.sh. The orchestrator's manual [EXECUTE AEGIS BACKUP] trigger
# (Section 9.5) runs the same unit the nightly timer runs, and nothing else.
atlas ALL=(root) NOPASSWD: $sc start atlas-aegis.service
SUDO
  if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment failed visudo -c; not installed"; }
  fi
  install -m 440 -o root -g root "$tmp" "$frag"
  rm -f "$tmp"
  log "installed $frag"
}

_restic_units() {
  export ATLAS_ETC ATLAS_OPT ATLAS_SRV
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis.service" /etc/systemd/system/atlas-aegis.service ATLAS_ETC ATLAS_OPT ATLAS_SRV
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-restic-check.service" /etc/systemd/system/atlas-restic-check.service ATLAS_ETC ATLAS_OPT
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis.timer" /etc/systemd/system/atlas-aegis.timer
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-restic-check.timer" /etc/systemd/system/atlas-restic-check.timer
  grep -q -- '--keep-daily 30 --keep-monthly 12 --prune' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost its retention line"
  grep -q '^SuccessExitStatus=3' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost SuccessExitStatus=3 (restic's partial-read exit code)"
  systemctl daemon-reload
  systemctl enable --now atlas-aegis.timer >/dev/null
  systemctl enable --now atlas-restic-check.timer >/dev/null
  log "timers enabled: $(systemctl list-timers --no-legend atlas-aegis.timer atlas-restic-check.timer 2>/dev/null | awk '{print $NF" "$1" "$2" "$3}' | tr '\n' ';')"
}

_restic_first_backup() {
  log "first AEGIS backup (systemctl start atlas-aegis.service; freeze/thaw through the orchestrator are best-effort)"
  systemctl reset-failed atlas-aegis.service 2>/dev/null || true
  if ! systemctl start atlas-aegis.service; then
    journalctl -u atlas-aegis.service --no-pager -n 40 >&2 || true
    die "the first restic backup failed (journal above)"
  fi
  _restic_env
  local n
  n="$(restic snapshots --json 2>/dev/null | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
  (( n >= 1 )) || die "no snapshot in $RESTIC_REPO after the first backup"
  log "first backup done: $n snapshot(s) in $RESTIC_REPO ($(du -sh "$RESTIC_REPO" 2>/dev/null | cut -f1))"
}

step_07() {
  apt_install restic
  _restic_passphrase
  _restic_files
  _restic_init
  _restic_sudoers
  _restic_units
  _restic_first_backup
  run_verify V13 v13-restic.sh "$RESTIC_CANARY" \
    || warn "V13 recorded as fail: the restore test did not verify by checksum; the Phase 2 gate will block until it passes"
  notify "Phase 2 step 7 done: restic repository $RESTIC_REPO, nightly AEGIS timer, quarterly restore test"
  log "step 07 done: nightly atlas-aegis.timer (02:30, keep 30 daily / 12 monthly), quarterly atlas-restic-check.timer; passphrase stored off-node per D3"
}
