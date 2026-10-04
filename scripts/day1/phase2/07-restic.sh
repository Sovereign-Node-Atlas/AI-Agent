#!/usr/bin/env bash
# phase2/07-restic.sh — Section 17 Phase 2 step 7: the AEGIS backup repository (Section 9.5; Appendix C; D3; D9; V13).
# Sourced by phase2-services.sh through run_phase_steps; defines step_07 only.
#
# Order (each part idempotent):
#   1. apt restic (0.18.1-3ubuntu1, VERIFIED resolute).
#   2. Passphrase: $ATLAS_ETC/secrets/restic.pass (root 600), generated once and printed ONCE in a framed block on the
#      terminal for the Principal to store off-node beside the LUKS recovery key (D3, R16). NO pause (CONVENTIONS.md §7.6
#      lists no pause for this step): when there is no terminal the block is not printed, the log names the on-node
#      copy, and the phase continues; the end-of-step summary repeats the path.
#   3. $ATLAS_ETC/restic.env (RESTIC_REPOSITORY=/srv/backups/restic on the 4 TB OS drive, RESTIC_PASSWORD_FILE,
#      RESTIC_CACHE_DIR) and the include/exclude sets of Appendix C:
#        include: $ATLAS_SRV/{data,workspace,sandbox}, $ATLAS_SRV/vault/cipher (the vault as-is, ciphertext; phase2/
#                 09b-vault.sh's layout), /srv/cold, $ATLAS_OPT/orchestrator, $ATLAS_ETC/atlas.env and the other
#                 non-secret *.env settings, the Open WebUI data dir, $ATLAS_STATE
#        exclude: $ATLAS_SRV/models, $ATLAS_SRV/engines (weights: manifests only, copied by the unit), $ATLAS_ETC/secrets
#                 entirely (CONVENTIONS.md §2), staging, the automount/FUSE mount points ($ATLAS_SRV/vault/open is the
#                 gocryptfs plaintext view: root gets EACCES there while the vault is open), the throw-away test vault,
#                 caches.
#      Every include path exists before the first backup (fix round 2: the Day 1 snapshot is exit 0, not exit 3):
#      $ATLAS_SRV/vault/cipher is created here with EXACTLY phase2/09b-vault.sh's owner and mode (atlas:atlas 700; an
#      empty directory is what `gocryptfs -init` wants), so step 9b finds its directory instead of this step depending on
#      a later step (§7.5). The Section 11 / Appendix C wording "/srv/atlas/vault" means vault/cipher (+ vault/open as the
#      plaintext view) in the implemented layout: README-contracts.md §3.1 records that the baseline text needs amending.
#   4. `restic init` when the repository does not exist yet; a canary file for V13, written AS ATLAS into the atlas-owned
#      data tree (root never writes by name under an atlas-owned directory: symlink planting).
#   5. /usr/local/sbin/atlas-aegis from the repository file phase2/atlas-aegis.sh (install -m 755; shellcheck covers it):
#      `freeze`, `manifests` (as atlas), `finish` (thaw, ledger row, ntfy on failure), `forget-result`.
#   6. Units: atlas-aegis.service (freeze -> manifests -> restic backup -> finish), atlas-aegis-forget.service (restic
#      forget --keep-within 1d --keep-daily 30 --keep-monthly 12 --prune; Requires/After the backup), atlas-aegis.timer
#      (nightly 02:30 -> the forget unit, so only the nightly timer prunes), atlas-aegis-trigger.path (the manual
#      [EXECUTE AEGIS BACKUP] trigger: /run/atlas/aegis-request, created by the orchestrator as atlas, starts the backup
#      unit; NO sudo), atlas-aegis-missed.service (OnFailure= of the forget unit: a nightly chain that did not run is
#      pushed to the phone, never silent), atlas-restic-check.service/.timer (quarterly restore test). A stale
#      /etc/sudoers.d/atlas-aegis from an earlier revision is removed and a negative sudo test proves the control path is
#      atlas-engines alone (§8). Content greps match DIRECTIVES (^Exec...=), never the units' own header comments (fix
#      round 3, blocker: `grep 'restic forget'` matched atlas-aegis.service's comment and died on every run).
#   6b. The package side of the trigger is ASSERTED, not assumed (fix round 3, major): $ORCH_DIR/src/atlas/tasks/aegis.py
#      must name /run/atlas/aegis-request and must not build a `["sudo", ...]` command; otherwise the step dies with the
#      contract, because a [EXECUTE AEGIS BACKUP] press would otherwise fail with "a password is required" on first use.
#   7. First backup now, through the manual trigger's path (atlas creates the request file; the path unit starts the
#      service; the step waits for Result=success and no raised freeze flag), then run_verify V13 v13-restic.sh (restore
#      the canary to a scratch dir, sha256 compare, restic check). A V13 fail is recorded, not fatal here: the gate
#      blocks on it. After the run `systemctl reset-failed` zeroes the start-rate counter, so the Day 1 run does not
#      count against the 6 h StartLimit window (nightly + two manual = StartLimitBurst=3).
#
# Contracts: `atlas-admin enqueue aegis-freeze|aegis-thaw --wait N` and `celery -A atlas.celery_app control
# add_consumer <queue>` (phase2/02-orchestrator.sh header; the package's freeze cancels the gpu consumer and raises
# /run/atlas/aegis-freeze). orch_write_file/orch_admin/ORCH_ENV/ORCH_DIR/REDIS_ENV come from step 02. $ATLAS_ETC/secrets
# is root:atlas 710 (02's header; the one value with phase2-services.sh and 09b). The package's manual trigger
# (atlas.tasks.aegis_manual_backup) MUST create /run/atlas/aegis-request and confirm with `systemctl is-active
# atlas-aegis.service`, never call sudo (the previous `sudo systemctl start [--no-block] atlas-aegis.service` route and
# its sudoers fragment are gone, fix round 2); _restic_trigger_contract dies when the installed package still builds a
# sudo command, so a trigger that would fail on first use is never declared proven (fix round 3).
# File writes use orch_write_file (rust-coreutils install rejects a pipe source on re-runs; 02's header).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}
if ! declare -F orch_write_file >/dev/null; then
  # shellcheck source=phase2/02-orchestrator.sh
  source "$ATLAS_DAY1_DIR/phase2/02-orchestrator.sh"
fi

RESTIC_PASS_FILE="$ATLAS_ETC/secrets/restic.pass"
RESTIC_PRINTED="$ATLAS_STATE/restic.pass-printed"
RESTIC_ENV_FILE="$ATLAS_ETC/restic.env"
RESTIC_INCLUDE="$ATLAS_ETC/restic-include.txt"
RESTIC_EXCLUDE="$ATLAS_ETC/restic-exclude.txt"
RESTIC_REPO="/srv/backups/restic"
RESTIC_CACHE="/var/cache/restic"
RESTIC_CANARY="$ATLAS_SRV/data/restic-canary.txt"
AEGIS_HELPER=/usr/local/sbin/atlas-aegis
AEGIS_HELPER_SRC="$ATLAS_DAY1_DIR/phase2/atlas-aegis.sh"
AEGIS_REQUEST=/run/atlas/aegis-request
AEGIS_STALE_SUDOERS=/etc/sudoers.d/atlas-aegis   # earlier revision; removed here
AEGIS_UNITS=(atlas-aegis.service atlas-aegis-forget.service atlas-aegis-missed.service atlas-restic-check.service)
AEGIS_VERBATIM=(atlas-aegis.timer atlas-aegis-trigger.path atlas-restic-check.timer)

_restic_passphrase() {
  ensure_dir "$ATLAS_ETC/secrets" root:atlas 710   # the one value (02's header; phase2-services.sh; 09b)
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
    echo "# restic environment (Section 9.5). Written by Phase 2 step 7; read by atlas-aegis.service, atlas-aegis-forget.service, atlas-restic-check.service, verify/v13-restic.sh."
    echo "RESTIC_REPOSITORY=$RESTIC_REPO"
    echo "RESTIC_PASSWORD_FILE=$RESTIC_PASS_FILE"
    echo "RESTIC_CACHE_DIR=$RESTIC_CACHE"
  } | orch_write_file 600 root:root "$RESTIC_ENV_FILE"

  # The vault layout of phase2/09b-vault.sh (_vault_dirs: atlas:atlas 700 for root, cipher, open, test-cipher), created
  # here with the same values so the include set is complete from the first backup (header item 3). gocryptfs -init
  # accepts an empty existing directory; 9b's ensure_dir calls are then no-ops.
  ensure_dir "$ATLAS_SRV/vault" atlas:atlas 700
  ensure_dir "$ATLAS_SRV/vault/cipher" atlas:atlas 700
  ensure_dir "$ATLAS_SRV/vault/open" atlas:atlas 700
  ensure_dir "$ATLAS_SRV/vault/test-cipher" atlas:atlas 700

  # Include set (Appendix C, task list). Every path exists at this point in Section 17 order (steps 1-4 wrote engines/,
  # the Open WebUI dir, memory.env; /srv/cold and the data tree come from step 02; the vault dirs just above), so the
  # Day 1 snapshot is a clean exit 0. The unit still accepts restic's exit 3 for a file that changes mid-read.
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
  local missing=() path
  for path in "${inc[@]}"; do [[ -e "$path" ]] || missing+=("$path"); done
  if (( ${#missing[@]} > 0 )); then
    warn "include paths absent at step time (restic skips them with exit 3 until they exist; steps 1-4 and 9b create them): ${missing[*]}"
  fi
  {
    echo "# restic --files-from: the AEGIS include set (Appendix C). One path per line; secrets are never listed (CONVENTIONS.md §2)."
    printf '%s\n' "${inc[@]}"
  } | orch_write_file 644 root:root "$RESTIC_INCLUDE"
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
  } | orch_write_file 644 root:root "$RESTIC_EXCLUDE"
  grep -qx "$ATLAS_ETC/secrets" "$RESTIC_EXCLUDE" || die "secrets exclusion missing from $RESTIC_EXCLUDE"
  grep -qx "$ATLAS_SRV/vault/open" "$RESTIC_EXCLUDE" || die "vault plaintext mount exclusion missing from $RESTIC_EXCLUDE"
  grep -qx "$ATLAS_SRV/vault/cipher" "$RESTIC_INCLUDE" || die "vault ciphertext dir missing from $RESTIC_INCLUDE (Section 9.5 Contents)"
  log "wrote $RESTIC_ENV_FILE, $RESTIC_INCLUDE (${#inc[@]} paths, ${#missing[@]} absent), $RESTIC_EXCLUDE"
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
  # so the unit never fails at 02:30 for a flag that does not exist. The help text is captured first: under pipefail a
  # `restic --help | grep -q` dies of SIGPIPE when grep exits before restic finished writing (fix round 2).
  local help
  help="$(restic backup --help 2>&1 || true)"
  grep -q -- '--files-from' <<<"$help" || die "this restic ($(restic version 2>&1 | head -n1)) has no --files-from flag; atlas-aegis.service relies on it"
  help="$(restic forget --help 2>&1 || true)"
  grep -q -- '--keep-within' <<<"$help" || die "this restic ($(restic version 2>&1 | head -n1)) has no --keep-within flag; atlas-aegis-forget.service relies on it"
  if restic snapshots >/dev/null 2>&1; then
    log "restic repository $RESTIC_REPO already initialised"
  else
    log "restic init $RESTIC_REPO"
    restic init >/dev/null || die "restic init failed for $RESTIC_REPO (wrong passphrase file or an unreadable repository dir?)"
  fi
  # V13 canary: a known file whose sha256 the restore test compares. Written AS ATLAS (the data tree is atlas-owned;
  # root must not create files by name under it). runuser keeps the environment, so the content travels in a variable.
  ensure_dir "$ATLAS_SRV/data" atlas:atlas 755
  local text
  text="ATLAS restic canary $(date -Is) $(python3 -c 'import secrets; print(secrets.token_hex(16))')"
  # shellcheck disable=SC2016  # the inner sh expands $CANARY_TEXT/$CANARY_PATH from its (inherited) environment
  CANARY_TEXT="$text" CANARY_PATH="$RESTIC_CANARY" svc_user_run /bin/sh -c 'umask 022; printf "%s\n" "$CANARY_TEXT" >"$CANARY_PATH"' \
    || die "could not write the V13 canary $RESTIC_CANARY as atlas ($ATLAS_SRV/data must be atlas-writable)"
  [[ -s "$RESTIC_CANARY" && ! -L "$RESTIC_CANARY" ]] || die "$RESTIC_CANARY is missing or a symlink after the write"
}

# --- the root helper the units run (repository file, shellcheck-clean) ------------------------------------------------
_restic_helper() {
  [[ -f "$AEGIS_HELPER_SRC" ]] || die "$AEGIS_HELPER_SRC is missing (the AEGIS helper source)"
  bash -n "$AEGIS_HELPER_SRC" || die "$AEGIS_HELPER_SRC does not parse"
  install -m 755 -o root -g root "$AEGIS_HELPER_SRC" "$AEGIS_HELPER"
  [[ -x "$ATLAS_OPT/venv/bin/celery" && -x "$ATLAS_OPT/venv/bin/atlas-admin" ]] \
    || die "$ATLAS_OPT/venv lacks celery/atlas-admin (step 02); $AEGIS_HELPER needs both for freeze/thaw"
  "$AEGIS_HELPER" >/dev/null 2>&1 && die "$AEGIS_HELPER without a subcommand must exit non-zero"
  log "installed $AEGIS_HELPER from $AEGIS_HELPER_SRC (freeze | manifests | finish | forget-result | notify)"
}

# --- control path: no second sudoers fragment (CONVENTIONS.md §8) --------------------------------------------------------
_restic_no_sudoers() {
  local sc
  if [[ -e "$AEGIS_STALE_SUDOERS" ]]; then
    rm -f "$AEGIS_STALE_SUDOERS"
    warn "removed $AEGIS_STALE_SUDOERS (earlier revision): the manual AEGIS trigger is atlas-aegis-trigger.path now; /etc/sudoers.d/atlas-engines is the only NOPASSWD grant (CONVENTIONS.md §8)"
  fi
  # Negative test: the atlas account must NOT be able to start or stop the backup unit through sudo (non-interactive).
  sc="$(readlink -f "$(command -v systemctl)")"
  if svc_user_run sudo -n "$sc" start --no-block atlas-aegis.service >/dev/null 2>&1; then
    die "sudo let the atlas account run 'systemctl start --no-block atlas-aegis.service': a sudoers fragment wider than /etc/sudoers.d/atlas-engines is installed (ls /etc/sudoers.d); refusing to continue (CONVENTIONS.md §8)"
  fi
  if svc_user_run sudo -n "$sc" stop atlas-aegis.service >/dev/null 2>&1; then
    die "sudo let the atlas account run 'systemctl stop atlas-aegis.service': a sudoers fragment wider than /etc/sudoers.d/atlas-engines is installed; refusing to continue (CONVENTIONS.md §8)"
  fi
  log "control path: no sudo grant for atlas-aegis.service (start and stop refused as atlas); the trigger is $AEGIS_REQUEST"
}

# --- the package side of the manual trigger (fix round 3, major) -----------------------------------------------------------
# Static assertion against the INSTALLED package ($ORCH_DIR, synced by step 02): the task module must create the request
# file and must not shell out to sudo. A dynamic exercise (`atlas-admin enqueue aegis-manual`) is not in step 02's
# contract, so the file is read; the first backup below then exercises the path unit itself.
_restic_trigger_contract() {
  local mod="$ORCH_DIR/src/atlas/tasks/aegis.py"
  [[ -f "$mod" ]] || die "contract: $mod is missing (atlas.tasks.aegis, the orchestrator writer's module; step 02 synced $ORCH_DIR)"
  grep -q 'aegis-request' "$mod" \
    || die "contract: atlas.tasks.aegis_manual_backup must create $AEGIS_REQUEST (no sudo) and confirm with 'systemctl is-active atlas-aegis.service'; $mod never names aegis-request (see this file's header; atlas-aegis-trigger.path is the trigger)"
  if grep -qE '\[[[:space:]]*"sudo"' "$mod"; then
    die "contract: $mod still builds a sudo command for the manual AEGIS trigger; the sudoers fragment /etc/sudoers.d/atlas-aegis is gone (CONVENTIONS.md §8: atlas-engines is the only grant) so every [EXECUTE AEGIS BACKUP] press would fail with 'a password is required'. The task must create $AEGIS_REQUEST and poll 'systemctl is-active atlas-aegis.service' instead (this file's header)"
  fi
  log "trigger contract: $mod names $AEGIS_REQUEST and builds no sudo command"
}

_restic_units() {
  local u
  export ATLAS_ETC ATLAS_OPT ATLAS_SRV
  for u in "${AEGIS_UNITS[@]}"; do
    [[ -f "$ATLAS_DAY1_DIR/systemd/$u" ]] || die "$ATLAS_DAY1_DIR/systemd/$u is missing"
  done
  for u in "${AEGIS_VERBATIM[@]}"; do
    [[ -f "$ATLAS_DAY1_DIR/systemd/$u" ]] || die "$ATLAS_DAY1_DIR/systemd/$u is missing"
  done
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis.service" /etc/systemd/system/atlas-aegis.service ATLAS_ETC ATLAS_OPT ATLAS_SRV
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis-forget.service" /etc/systemd/system/atlas-aegis-forget.service ATLAS_ETC ATLAS_OPT
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-aegis-missed.service" /etc/systemd/system/atlas-aegis-missed.service ATLAS_ETC ATLAS_OPT
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-restic-check.service" /etc/systemd/system/atlas-restic-check.service ATLAS_ETC ATLAS_OPT
  for u in "${AEGIS_VERBATIM[@]}"; do
    install -m 644 "$ATLAS_DAY1_DIR/systemd/$u" "/etc/systemd/system/$u"
  done
  # Content checks, not mere presence (the lines the Section 9.5 contract hangs on). Every grep is anchored to a DIRECTIVE
  # (`^Key=`): render_template keeps the units' header comments, and an unanchored 'restic forget' matched
  # atlas-aegis.service's own comment on every run (fix round 3, blocker).
  grep -q -- '^ExecStart=/usr/bin/restic forget --keep-within 1d --keep-daily 30 --keep-monthly 12 --prune' /etc/systemd/system/atlas-aegis-forget.service || die "atlas-aegis-forget.service lost its retention line"
  grep -q '^Requires=atlas-aegis.service' /etc/systemd/system/atlas-aegis-forget.service || die "atlas-aegis-forget.service must Require atlas-aegis.service (prune only after a successful backup)"
  grep -q '^OnFailure=atlas-aegis-missed.service' /etc/systemd/system/atlas-aegis-forget.service || die "atlas-aegis-forget.service lost OnFailure=atlas-aegis-missed.service (a nightly chain that did not run must be pushed, §7.4)"
  grep -q '^ExecStart=/usr/local/sbin/atlas-aegis notify ' /etc/systemd/system/atlas-aegis-missed.service || die "atlas-aegis-missed.service does not run '$AEGIS_HELPER notify'"
  grep -q '^Unit=atlas-aegis-forget.service' /etc/systemd/system/atlas-aegis.timer || die "atlas-aegis.timer must start atlas-aegis-forget.service (backup, then the nightly-only prune)"
  grep -qE '^Exec(Start|StartPre|StartPost|Stop|StopPost|Condition)=.*restic forget' /etc/systemd/system/atlas-aegis.service && die "atlas-aegis.service must not prune (the manual trigger starts it; Section 16.3 item 5)"
  grep -q '^SuccessExitStatus=3' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost SuccessExitStatus=3 (restic's partial-read exit code)"
  grep -q '^StartLimitBurst=3' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost StartLimitBurst=3 (nightly plus two manual starts per 6 h)"
  grep -q "^ExecStartPre=/bin/rm -f $AEGIS_REQUEST" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not remove $AEGIS_REQUEST first (the path unit would re-trigger for ever)"
  grep -q "^ExecStartPre=$AEGIS_HELPER freeze" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not run '$AEGIS_HELPER freeze'"
  grep -q "^ExecStopPost=$AEGIS_HELPER finish" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not run '$AEGIS_HELPER finish' (the thaw, and the exit-3 'files not read' push)"
  grep -q '^RuntimeDirectory=atlas-aegis' /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service lost RuntimeDirectory=atlas-aegis (the helper's root-owned intent marker)"
  grep -qxF "EnvironmentFile=$ATLAS_ETC/secrets/redis.env" /etc/systemd/system/atlas-aegis.service || die "atlas-aegis.service does not load secrets/redis.env (the broker URL for freeze/thaw)"
  grep -qE '^Exec[A-Za-z]*=.*\bnotify\b' /etc/systemd/system/atlas-restic-check.service && ! grep -qE "^ExecStart=.*$AEGIS_HELPER notify" /etc/systemd/system/atlas-restic-check.service \
    && die "atlas-restic-check.service pushes through lib/common.sh's notify (token on curl's argv); it must use '$AEGIS_HELPER notify'"
  grep -q "^PathExists=$AEGIS_REQUEST" /etc/systemd/system/atlas-aegis-trigger.path || die "atlas-aegis-trigger.path does not watch $AEGIS_REQUEST"
  grep -q '^Unit=atlas-aegis.service' /etc/systemd/system/atlas-aegis-trigger.path || die "atlas-aegis-trigger.path does not start atlas-aegis.service"
  # The helper itself carries the exit-3 push (restic "some source files could not be read" is Result=success for
  # systemd, so `finish` must speak, fix round 3): assert the installed helper has it.
  grep -q 'status" == 3' "$AEGIS_HELPER" || die "$AEGIS_HELPER lost its restic-exit-3 handling (a partial snapshot must be pushed, §7.4)"
  systemctl daemon-reload
  systemctl reset-failed atlas-aegis.service atlas-aegis-forget.service atlas-aegis-missed.service atlas-aegis-trigger.path 2>/dev/null || true
  systemctl enable --now atlas-aegis.timer >/dev/null
  systemctl enable --now atlas-restic-check.timer >/dev/null
  systemctl enable --now atlas-aegis-trigger.path >/dev/null || die "systemctl enable --now atlas-aegis-trigger.path failed: $(systemctl status atlas-aegis-trigger.path --no-pager 2>&1 | tail -n 5)"
  systemctl is-active --quiet atlas-aegis-trigger.path || die "atlas-aegis-trigger.path is not active after enable (journalctl -u atlas-aegis-trigger.path)"
  log "timers enabled: $(systemctl list-timers --no-legend atlas-aegis.timer atlas-restic-check.timer 2>/dev/null | awk '{print $NF" "$1" "$2" "$3}' | tr '\n' ';'); trigger path active (watching $AEGIS_REQUEST)"
}

# _restic_wait_unit UNIT MAX_S — wait until UNIT is neither activating nor active; prints the final is-active state.
_restic_wait_unit() {
  local unit="$1" max="$2" waited=0 st
  while :; do
    st="$(systemctl is-active "$unit" 2>/dev/null || true)"
    [[ "$st" == activating || "$st" == active || "$st" == deactivating ]] || { printf '%s' "$st"; return 0; }
    (( waited < max )) || { printf '%s' "$st"; return 1; }
    sleep 5; waited=$(( waited + 5 ))
  done
}

_restic_first_backup() {
  log "first AEGIS backup through the manual trigger's path: atlas creates $AEGIS_REQUEST, atlas-aegis-trigger.path starts the unit (freeze -> manifests -> restic -> thaw; the orchestrator is up, so the freeze is mandatory)"
  [[ -d /run/atlas ]] || die "/run/atlas does not exist: atlas-orchestrator.service (RuntimeDirectory=atlas) is not running (step 02)"
  systemctl reset-failed atlas-aegis.service 2>/dev/null || true   # also resets the StartLimit counter for re-runs
  local st
  st="$(systemctl is-active atlas-aegis.service 2>/dev/null || true)"
  [[ "$st" == activating || "$st" == active ]] && die "atlas-aegis.service is already $st (a backup is running); re-run --force 07 when it has finished"
  # shellcheck disable=SC2016  # $1 is the inner sh's positional argument (the request path)
  svc_user_run /bin/sh -c 'umask 077; : >"$1"' _ "$AEGIS_REQUEST" || die "the atlas account could not create $AEGIS_REQUEST (/run/atlas must be atlas:atlas 750, atlas-orchestrator.service's RuntimeDirectory)"
  # The path unit reacts within a second or two; give it 60 s to start the service.
  local waited=0
  while :; do
    st="$(systemctl is-active atlas-aegis.service 2>/dev/null || true)"
    [[ "$st" == activating || "$st" == active || "$st" == deactivating ]] && break
    [[ ! -e "$AEGIS_REQUEST" && "$st" == inactive ]] && break     # already finished (a very small include set)
    (( waited < 60 )) || { journalctl -u atlas-aegis-trigger.path -u atlas-aegis.service --no-pager -n 30 >&2 || true; die "atlas-aegis-trigger.path did not start atlas-aegis.service within 60 s of $AEGIS_REQUEST appearing (unit state $st; the StartLimit may be hit: systemctl reset-failed atlas-aegis.service atlas-aegis-trigger.path)"; }
    sleep 2; waited=$(( waited + 2 ))
  done
  st="$(_restic_wait_unit atlas-aegis.service 7200)" || die "atlas-aegis.service is still $st after 2 h; the first backup did not finish (journalctl -u atlas-aegis.service)"
  local result
  result="$(systemctl show -p Result --value atlas-aegis.service 2>/dev/null || true)"
  if [[ "$result" != success ]]; then
    journalctl -u atlas-aegis.service --no-pager -n 60 >&2 || true
    die "atlas-aegis.service finished with Result=$result (journal above: a freeze failure means the orchestrator's aegis-freeze task did not complete)"
  fi
  [[ -e "$AEGIS_REQUEST" ]] && die "$AEGIS_REQUEST still exists after the run: the unit's first ExecStartPre did not remove it (the path unit would loop)"
  [[ -e /run/atlas/aegis-freeze ]] && die "the freeze flag /run/atlas/aegis-freeze is still raised after the backup: the thaw did not complete (journalctl -u atlas-aegis.service)"
  _restic_env
  local n
  n="$(restic snapshots --json 2>/dev/null | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)"
  (( n >= 1 )) || die "no snapshot in $RESTIC_REPO after the first backup"
  # reset-failed also zeroes the start-rate counter (fix round 3): the Day 1 run must not count against the 6 h window
  # that the nightly and the Principal's first [EXECUTE AEGIS BACKUP] presses share (StartLimitBurst=3).
  systemctl reset-failed atlas-aegis.service atlas-aegis-trigger.path 2>/dev/null || true
  systemctl is-active --quiet atlas-aegis-trigger.path || die "atlas-aegis-trigger.path is no longer active after the first backup (journalctl -u atlas-aegis-trigger.path)"
  log "first backup done through the trigger path: $n snapshot(s) in $RESTIC_REPO ($(du -sh "$RESTIC_REPO" 2>/dev/null | cut -f1)); freeze/thaw completed through the orchestrator; start-rate counter reset, path unit active"
}

step_07() {
  apt_install restic
  _restic_passphrase
  _restic_files
  _restic_init
  _restic_helper
  _restic_units
  _restic_no_sudoers
  _restic_trigger_contract
  _restic_first_backup
  run_verify V13 v13-restic.sh "$RESTIC_CANARY" \
    || warn "V13 recorded as fail: the restore test did not verify by checksum; the Phase 2 gate will block until it passes"
  notify "Phase 2 step 7 done: restic repository $RESTIC_REPO, nightly AEGIS timer, quarterly restore test"
  cat <<MSG

  ==== AEGIS backup ====
  Repository:       $RESTIC_REPO (4 TB OS drive; Section 9.5)
  Passphrase:       on-node copy $RESTIC_PASS_FILE (root only, never backed up). Store a copy on the off-node USB
                    drive with the LUKS recovery key (D3, R16) — printed once above when a terminal was present.
  Nightly:          atlas-aegis.timer 02:30 -> atlas-aegis-forget.service (backup: freeze -> restic -> thaw; then
                    forget --keep-within 1d / 30 daily / 12 monthly --prune; only the nightly run prunes)
  Manual trigger:   [EXECUTE AEGIS BACKUP] -> the orchestrator creates $AEGIS_REQUEST (no sudo; the installed
                    package was checked for exactly that); atlas-aegis-trigger.path starts the backup.
                    Start limit: 3 starts per 6 h (nightly + two manual); a missed nightly is pushed to the phone.
  Restore test:     atlas-restic-check.timer quarterly; V13 recorded now.
  ======================
MSG
  log "step 07 done: nightly atlas-aegis.timer (02:30; backup then keep-within 1d, 30 daily / 12 monthly), manual trigger $AEGIS_REQUEST, quarterly atlas-restic-check.timer; passphrase on-node copy $RESTIC_PASS_FILE"
}
