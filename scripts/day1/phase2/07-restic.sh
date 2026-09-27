#!/usr/bin/env bash
# phase2/07-restic.sh — Section 17 Phase 2 step 7: the AEGIS backup repository (Section 9.5; Appendix C; D3; D9; V13).
# Sourced by phase2-services.sh through run_phase_steps; defines step_07 only.
#
# Order (each part idempotent):
#   1. apt restic (0.18.1-3ubuntu1, VERIFIED resolute).
#   2. Passphrase: $ATLAS_ETC/secrets/restic.pass (root 600), generated once and printed ONCE in a framed block on the
#      terminal for the Principal to store off-node beside the LUKS recovery key (D3, R16). NO pause (fix round:
#      CONVENTIONS.md §7.6 lists no pause for this step): when there is no terminal the block is not printed, the log
#      names the on-node copy, and the phase continues; the end-of-step summary repeats the path.
#   3. $ATLAS_ETC/restic.env (RESTIC_REPOSITORY=/srv/backups/restic on the 4 TB OS drive, RESTIC_PASSWORD_FILE,
#      RESTIC_CACHE_DIR) and the include/exclude sets of Appendix C:
#        include: $ATLAS_SRV/{data,workspace,sandbox}, $ATLAS_SRV/vault/cipher (the vault as-is, ciphertext; phase2/
#                 09b-vault.sh's layout), /srv/cold, $ATLAS_OPT/orchestrator, $ATLAS_ETC/atlas.env and the other
#                 non-secret *.env settings, the Open WebUI data dir, $ATLAS_STATE
#        exclude: $ATLAS_SRV/models, $ATLAS_SRV/engines (weights: manifests only, copied by the unit), $ATLAS_ETC/secrets
#                 entirely (CONVENTIONS.md §2), staging, the automount/FUSE mount points ($ATLAS_SRV/vault/open is the
#                 gocryptfs plaintext view: root gets EACCES there while the vault is open), the throw-away test vault,
#                 caches.
#   4. `restic init` when the repository does not exist yet; a canary file for V13.
#   5. /usr/local/sbin/atlas-aegis, the root helper the unit runs: `freeze` (the orchestrator's aegis-freeze task,
#      mandatory while the orchestrator is up, skipped explicitly when it is down), `manifests`, `finish` (thaw through
#      celery's broadcast channel, ledger row, ntfy on failure). Written here because CONVENTIONS.md §1 fixes the
#      systemd/ list; the unit header (systemd/atlas-aegis.service) explains the sequence.
#   6. /etc/sudoers.d/atlas-aegis: the orchestrator's manual [EXECUTE AEGIS BACKUP] trigger runs `sudo systemctl start
#      atlas-aegis.service` (blocking) or `... start --no-block atlas-aegis.service`, and nothing else; proven with a
#      negative test (a `stop` must be refused) and, below, by running the first backup through exactly that path.
#   7. Units: atlas-aegis.service/.timer (nightly 02:30; freeze -> restic backup -> forget --keep-within 1d --keep-daily
#      30 --keep-monthly 12 --prune -> thaw) and atlas-restic-check.service/.timer (quarterly restore test).
#   8. First backup now, as the atlas account through sudo (the manual trigger's path), then run_verify V13
#      v13-restic.sh (restore the canary to a scratch dir, sha256 compare, restic check). A V13 fail is recorded, not
#      fatal here: the gate blocks on it.
#
# Contracts: `atlas-admin enqueue aegis-freeze|aegis-thaw --wait N` and `celery -A atlas.celery_app control
# add_consumer <queue>` (phase2/02-orchestrator.sh header; the package's freeze cancels the cpu and gpu consumers and
# raises /run/atlas/aegis-freeze). orch_admin/ORCH_ENV/REDIS_ENV come from step 02. $ATLAS_ETC/secrets is root:atlas
# 750 (02's header). The package's manual trigger (atlas.tasks.aegis_manual_backup) may use either sudoers line.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

RESTIC_PASS_FILE="$ATLAS_ETC/secrets/restic.pass"
RESTIC_PRINTED="$ATLAS_STATE/restic.pass-printed"
RESTIC_ENV_FILE="$ATLAS_ETC/restic.env"
RESTIC_INCLUDE="$ATLAS_ETC/restic-include.txt"
RESTIC_EXCLUDE="$ATLAS_ETC/restic-exclude.txt"
RESTIC_REPO="/srv/backups/restic"
RESTIC_CACHE="/var/cache/restic"
RESTIC_CANARY="$ATLAS_SRV/data/restic-canary.txt"
AEGIS_HELPER=/usr/local/sbin/atlas-aegis
AEGIS_SUDOERS=/etc/sudoers.d/atlas-aegis

_restic_passphrase() {
  ensure_dir "$ATLAS_ETC/secrets" root:atlas 750
  if [[ ! -s "$RESTIC_PASS_FILE" ]]; then
    (umask 077; head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40 >"$RESTIC_PASS_FILE"; echo >>"$RESTIC_PASS_FILE")
    chown root:root "$RESTIC_PASS_FILE"; chmod 600 "$RESTIC_PASS_FILE"
    rm -f "$RESTIC_PRINTED"
    log "generated the restic passphrase into $RESTIC_PASS_FILE (root 600)"
  fi
  if [[ -e "$RESTIC_PRINTED" ]]; then
    log "restic passphrase already printed on $(cat "$RESTIC_PRINTED"); not printing it again (on-node copy: $RESTIC_PASS_FILE)"
    return 0
  fi
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    # No pause and no failure (CONVENTIONS.md §7.6): the on-node copy is the source for the USB copy (D3).
    warn "no terminal: the restic passphrase was NOT displayed. Copy it to the off-node USB drive from the on-node copy: sudo cat $RESTIC_PASS_FILE (root only, excluded from every backup). A later terminal run of --force 07 prints it once."
    return 0
  fi
  local line pass
  pass="$(head -n1 "$RESTIC_PASS_FILE")"
  line="$(printf '#%.0s' $(seq 1 78))"
  {
    echo; echo "$line"; echo "#"
    echo "#   RESTIC (AEGIS BACKUP) PASSPHRASE — repository $RESTIC_REPO"
    echo "#   Printed ONCE. Write it down and store it on the external USB drive that lives AWAY from"
    echo "#   the node, with the LUKS recovery key (D3, R16). A backup whose passphrase died with the"
    echo "#   machine is useless (Section 9.5). The phase continues; nothing waits for you."
    echo "#"
    echo "#       $pass"
    echo "#"
    echo "#   On-node copy (root only, excluded from every backup): $RESTIC_PASS_FILE"
    echo "#"; echo "$line"; echo
  } >/dev/tty 2>/dev/null || { warn "could not write to /dev/tty; the passphrase stays in $RESTIC_PASS_FILE"; return 0; }
  date -Is >"$RESTIC_PRINTED"
  log "restic passphrase printed once to the terminal (the passphrase itself is never logged); on-node copy $RESTIC_PASS_FILE"
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

  # Include set (Appendix C, task list). Paths that do not exist yet (memory.env before step 4, vault/cipher before
  # step 9b, the Open WebUI dir before step 3 on a --force re-run) make restic exit 3 ("some files could not be
  # read"); the unit accepts 3.
  local inc=(
    "$ATLAS_SRV/data"
    "$ATLAS_SRV/workspace"
    "$ATLAS_SRV/sandbox"
    "$ATLAS_SRV/vault/cipher"
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
    echo "$ATLAS_SRV/winpc"            # cifs automount (step 9): never trigger it from a backup
    echo "$ATLAS_SRV/gdrive"           # rclone Drive mounts (Section 13), if the integrations writer places them here
    echo "$ATLAS_SRV/vault/open"       # gocryptfs plaintext view (phase2/09b-vault.sh, no -allow_other): root gets EACCES; ciphertext is what is backed up (Section 11)
    echo "$ATLAS_SRV/vault/test-cipher" # the throw-away test vault of step 9b
    echo "$ATLAS_STATE/pip-cache"
    echo "$ATLAS_STATE/restore-test.*"
    echo "$ATLAS_OPT/orchestrator/.venv"
    echo "**/__pycache__"
    echo "**/.pytest_cache"
    echo "**/.ruff_cache"
    echo "**/*.pyc"
  } | install -m 644 -o root -g root /dev/stdin "$RESTIC_EXCLUDE"
  grep -qx "$ATLAS_ETC/secrets" "$RESTIC_EXCLUDE" || die "secrets exclusion missing from $RESTIC_EXCLUDE"
  grep -qx "$ATLAS_SRV/vault/open" "$RESTIC_EXCLUDE" || die "vault plaintext mount exclusion missing from $RESTIC_EXCLUDE"
  grep -qx "$ATLAS_SRV/vault/cipher" "$RESTIC_INCLUDE" || die "vault ciphertext dir missing from $RESTIC_INCLUDE (Section 9.5 Contents)"
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
  # UNVERIFIED by the research: `--files-from`; VERIFIED there: init, backup, forget, restore, check. Assert the flags now
  # so the unit never fails at 02:30 for a flag that does not exist.
  restic backup --help 2>&1 | grep -q -- '--files-from' || die "this restic ($(restic version 2>&1 | head -n1)) has no --files-from flag; atlas-aegis.service relies on it"
  restic forget --help 2>&1 | grep -q -- '--keep-within' || die "this restic ($(restic version 2>&1 | head -n1)) has no --keep-within flag; atlas-aegis.service relies on it"
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

# --- the root helper the unit runs -----------------------------------------------------------------------------------------
_restic_helper() {
  cat >"$AEGIS_HELPER.tmp" <<'HELPER'
#!/usr/bin/env bash
# /usr/local/sbin/atlas-aegis — written by scripts/day1/phase2/07-restic.sh (generated: CONVENTIONS.md §1 fixes the
# systemd/ list). Run as ROOT by /etc/systemd/system/atlas-aegis.service (its header explains the sequence):
#   freeze     with atlas-orchestrator AND atlas-celery-cpu active: `atlas-admin enqueue aegis-freeze --wait 900` as
#              atlas MUST succeed (the queues pause, /run/atlas/aegis-freeze goes up), otherwise exit 1 and the unit
#              fails before restic runs (Section 9.5 Freeze row; an unfrozen backup is never called a success).
#              With either unit inactive the freeze is skipped EXPLICITLY (crash-consistent backup; the journal says so).
#              The intent marker is written BEFORE the enqueue, so `finish` thaws even when the wait timed out after the
#              task had already paused the queues.
#   manifests  copy every <models|engines>/<key>/MANIFEST.json into $ATLAS_SRV/data/manifests (Appendix C).
#   finish     ExecStopPost, always runs. Thaw goes through celery's broadcast channel — `celery control add_consumer
#              cpu|gpu`, which a worker answers while its task queues are cancelled — never through the paused queue;
#              then the flag comes down and the package's aegis-thaw task is enqueued (now deliverable) for its ledger
#              row. No reply from a worker: both workers are restarted and the unit is reported FAILED. Then a ntfy
#              push (lib/common.sh notify) whenever systemd's $SERVICE_RESULT is not "success" or the thaw fell back.
# Environment from the unit: ATLAS_OPT, ATLAS_SRV, ATLAS_ETC, restic.env, orchestrator.env, secrets/redis.env and
# memory.env keys; runuser keeps them, so atlas-admin and celery see the broker URL and the settings. Never exits 3
# (the unit's SuccessExitStatus for restic).
set -Eeuo pipefail
: "${ATLAS_OPT:=/opt/atlas}"; : "${ATLAS_SRV:=/srv/atlas}"; : "${ATLAS_ETC:=/etc/atlas}"
VENV="$ATLAS_OPT/venv"
RUN_DIR=/run/atlas
INTENT="$RUN_DIR/aegis-unit-froze"
FLAG="${AEGIS_FREEZE_FLAG:-$RUN_DIR/aegis-freeze}"

as_atlas() { runuser -u atlas -- "$@"; }
say() { printf 'atlas-aegis %s\n' "$*"; }
err() { printf 'atlas-aegis %s\n' "$*" >&2; }

orchestrator_up() {
  systemctl -q is-active atlas-orchestrator.service && systemctl -q is-active atlas-celery-cpu.service
}

cmd_freeze() {
  if ! orchestrator_up; then
    rm -f "$INTENT"
    say "freeze skipped: atlas-orchestrator or atlas-celery-cpu is inactive; taking a crash-consistent backup (Section 9.5 Freeze row needs a running orchestrator)"
    return 0
  fi
  [[ -d "$RUN_DIR" ]] || install -d -m 750 -o atlas -g atlas "$RUN_DIR"
  date -Is >"$INTENT"
  if ! as_atlas "$VENV/bin/atlas-admin" enqueue aegis-freeze --wait 900; then
    err "freeze FAILED with the orchestrator up: the unit stops here (no backup of an unfrozen store is reported as success); finish thaws"
    return 1
  fi
  say "freeze done: Celery cpu/gpu consumers paused, flag $FLAG raised"
}

cmd_manifests() {
  local d="$ATLAS_SRV/data/manifests" f rel
  mkdir -p "$d"
  find "$ATLAS_SRV/models" "$ATLAS_SRV/engines" -mindepth 2 -maxdepth 2 -name MANIFEST.json 2>/dev/null | while read -r f; do
    rel="${f#"$ATLAS_SRV"/}"
    mkdir -p "$d/$(dirname "$rel")"
    cp -f "$f" "$d/$rel"
  done
  say "manifests copied into $d: $(find "$d" -name MANIFEST.json 2>/dev/null | wc -l)"
}

thaw() {
  local ok=1 q
  for q in cpu gpu; do
    if ! as_atlas "$VENV/bin/celery" -A atlas.celery_app control --timeout 20 add_consumer "$q" >/dev/null 2>&1; then
      ok=0; err "thaw: 'celery control add_consumer $q' got no reply"
    fi
  done
  rm -f "$FLAG"
  if (( ok )); then
    if as_atlas "$VENV/bin/atlas-admin" enqueue aegis-thaw --wait 120 >/dev/null; then
      rm -f "$INTENT"
      say "thaw done: consumers resumed on cpu and gpu, flag $FLAG removed, aegis-thaw ledger row written"
      return 0
    fi
    err "thaw: consumers answered add_consumer but 'atlas-admin enqueue aegis-thaw --wait 120' failed"
  fi
  if systemctl restart atlas-celery-cpu.service atlas-celery-gpu.service; then
    rm -f "$INTENT"
    err "thaw FALLBACK: restarted atlas-celery-cpu and atlas-celery-gpu (fresh consumers, queues live); the unit is reported failed so this is seen"
  else
    err "thaw FAILED: workers did not resume and could not be restarted; the Celery queues may still be paused (flag $FLAG removed)"
  fi
  return 1
}

notify_push() {
  local lib="$ATLAS_OPT/day1/lib/common.sh"
  if [[ -r "$lib" ]]; then
    # shellcheck disable=SC1090  # lib/common.sh: notify() never fails the caller; its log copy goes nowhere (/dev/null)
    ( set +e; export ATLAS_LOG_TO_STDERR=1 ATLAS_PHASE=aegis ATLAS_LOG_FILE=/dev/null; source "$lib"; notify "$1" ) || true
  else
    err "notify: $lib missing; message was: $1"
  fi
}

cmd_finish() {
  local rc=0 result="${SERVICE_RESULT:-unknown}" status="${EXIT_STATUS:-?}"
  if [[ -e "$INTENT" ]]; then
    thaw || rc=1
  else
    say "finish: no freeze was requested by this run; nothing to thaw"
  fi
  [[ "$status" == 3 ]] && say "restic exit 3: some source files could not be read (names above); the snapshot exists"
  if [[ "$result" != success || $rc -ne 0 ]]; then
    notify_push "AEGIS backup on $(hostname) FAILED: systemd result=$result exit=$status, thaw=$([[ $rc -eq 0 ]] && echo ok || echo fallback/failed). See: journalctl -u atlas-aegis.service"
    err "finish: result=$result exit=$status thaw_rc=$rc (ntfy pushed)"
  else
    say "finish: backup result=$result exit=$status, thaw ok"
  fi
  return "$rc"
}

case "${1:-}" in
  freeze)    cmd_freeze ;;
  manifests) cmd_manifests ;;
  finish)    cmd_finish ;;
  *) err "usage: atlas-aegis freeze|manifests|finish"; exit 2 ;;
esac
HELPER
  bash -n "$AEGIS_HELPER.tmp" || { rm -f "$AEGIS_HELPER.tmp"; die "the generated $AEGIS_HELPER does not parse"; }
  install -m 755 -o root -g root "$AEGIS_HELPER.tmp" "$AEGIS_HELPER"
  rm -f "$AEGIS_HELPER.tmp"
  [[ -x "$ATLAS_OPT/venv/bin/celery" && -x "$ATLAS_OPT/venv/bin/atlas-admin" ]] \
    || die "$ATLAS_OPT/venv lacks celery/atlas-admin (step 02); $AEGIS_HELPER needs both for freeze/thaw"
  log "installed $AEGIS_HELPER (freeze | manifests | finish)"
}

_restic_sudoers() {
  local sc tmp
  sc="$(readlink -f "$(command -v systemctl)")"
  tmp="$(mktemp)"
  cat >"$tmp" <<SUDO
# atlas-aegis — written by scripts/day1/phase2/07-restic.sh. The orchestrator's manual [EXECUTE AEGIS BACKUP] trigger
# (Section 9.5) starts the same unit the nightly timer runs, blocking or with --no-block, and nothing else.
atlas ALL=(root) NOPASSWD: $sc start atlas-aegis.service
atlas ALL=(root) NOPASSWD: $sc start --no-block atlas-aegis.service
SUDO
  if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment failed visudo -c; not installed"; }
  else
    warn "visudo not found (sudo-rs without it?); installing $AEGIS_SUDOERS unchecked (the tests below prove the grant)"
  fi
  install -m 440 -o root -g root "$tmp" "$AEGIS_SUDOERS"
  rm -f "$tmp"
  # Negative test: the fragment must not widen the control path (a `stop` is refused, non-interactively).
  if svc_user_run sudo -n "$sc" stop atlas-aegis.service >/dev/null 2>&1; then
    die "sudo let the atlas account run 'systemctl stop atlas-aegis.service': $AEGIS_SUDOERS (or another fragment) is wider than the two start lines; refusing to continue"
  fi
  log "installed $AEGIS_SUDOERS (start, start --no-block; a stop is refused as atlas)"
}

_restic_units() {
  export ATLAS_ETC ATLAS_OPT ATLAS_SRV
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis.service" /etc/systemd/system/atlas-aegis.service ATLAS_ETC ATLAS_OPT ATLAS_SRV
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-restic-check.service" /etc/systemd/system/atlas-restic-check.service ATLAS_ETC ATLAS_OPT
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis.timer" /etc/systemd/system/atlas-aegis.timer
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-restic-check.timer" /etc/systemd/system/atlas-restic-check.timer
  grep -q -- '--keep-within 1d --keep-daily 30 --keep-monthly 12 --prune' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost its retention line"
  grep -q '^SuccessExitStatus=3' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost SuccessExitStatus=3 (restic's partial-read exit code)"
  grep -q "^ExecStartPre=$AEGIS_HELPER freeze" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not run '$AEGIS_HELPER freeze'"
  grep -q "^ExecStopPost=$AEGIS_HELPER finish" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not run '$AEGIS_HELPER finish' (the thaw)"
  grep -qF "EnvironmentFile=$ATLAS_ETC/secrets/redis.env" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not load secrets/redis.env (the broker URL for freeze/thaw)"
  systemctl daemon-reload
  systemctl enable --now atlas-aegis.timer >/dev/null
  systemctl enable --now atlas-restic-check.timer >/dev/null
  log "timers enabled: $(systemctl list-timers --no-legend atlas-aegis.timer atlas-restic-check.timer 2>/dev/null | awk '{print $NF" "$1" "$2" "$3}' | tr '\n' ';')"
}

_restic_first_backup() {
  local sc
  sc="$(readlink -f "$(command -v systemctl)")"
  log "first AEGIS backup through the manual trigger's path: atlas runs 'sudo systemctl start atlas-aegis.service' (freeze -> restic -> thaw; the orchestrator is up, so the freeze is mandatory)"
  systemctl reset-failed atlas-aegis.service 2>/dev/null || true   # also resets the StartLimit counter for re-runs
  if ! svc_user_run sudo -n "$sc" start atlas-aegis.service; then
    journalctl -u atlas-aegis.service --no-pager -n 60 >&2 || true
    die "the first restic backup failed (journal above: a freeze failure means the orchestrator's aegis-freeze task did not complete; a sudo refusal means $AEGIS_SUDOERS is not in effect)"
  fi
  local result
  result="$(systemctl show -p Result --value atlas-aegis.service 2>/dev/null || true)"
  [[ "$result" == success ]] || die "atlas-aegis.service finished with Result=$result (journalctl -u atlas-aegis.service)"
  [[ -e /run/atlas/aegis-freeze ]] && die "the freeze flag /run/atlas/aegis-freeze is still raised after the backup: the thaw did not complete (journalctl -u atlas-aegis.service)"
  _restic_env
  local n
  n="$(restic snapshots --json 2>/dev/null | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
  (( n >= 1 )) || die "no snapshot in $RESTIC_REPO after the first backup"
  log "first backup done: $n snapshot(s) in $RESTIC_REPO ($(du -sh "$RESTIC_REPO" 2>/dev/null | cut -f1)); freeze/thaw completed through the orchestrator"
}

step_07() {
  apt_install restic
  _restic_passphrase
  _restic_files
  _restic_init
  _restic_helper
  _restic_sudoers
  _restic_units
  _restic_first_backup
  run_verify V13 v13-restic.sh "$RESTIC_CANARY" \
    || warn "V13 recorded as fail: the restore test did not verify by checksum; the Phase 2 gate will block until it passes"
  notify "Phase 2 step 7 done: restic repository $RESTIC_REPO, nightly AEGIS timer, quarterly restore test"
  cat <<MSG

  ==== AEGIS backup ====
  Repository:       $RESTIC_REPO (4 TB OS drive; Section 9.5)
  Passphrase:       on-node copy $RESTIC_PASS_FILE (root only, never backed up). Store a copy on the off-node USB
                    drive with the LUKS recovery key (D3, R16) — printed once above when a terminal was present.
  Nightly:          atlas-aegis.timer 02:30 (freeze -> restic -> keep-within 1d / 30 daily / 12 monthly -> thaw)
  Manual trigger:   [EXECUTE AEGIS BACKUP] -> sudo systemctl start atlas-aegis.service (two starts per 6 h)
  Restore test:     atlas-restic-check.timer quarterly; V13 recorded now.
  ======================
MSG
  log "step 07 done: nightly atlas-aegis.timer (02:30, keep-within 1d, 30 daily / 12 monthly), quarterly atlas-restic-check.timer; passphrase on-node copy $RESTIC_PASS_FILE"
}
