#!/usr/bin/env bash
# phase2/09b-vault.sh — Section 17 Phase 2 (between steps 9 and 10): the gocryptfs vault (Section 11, D13, 10.5, V18).
# Sourced by phase2-services.sh through run_phase_steps; defines step_09b only.
#
# STEP ID (recorded, fix round): Section 17 names no vault step (V18 sits at the Phase 2 gate) and CONVENTIONS.md §1
# lists no 09b file. The vault needs apt, a helper, a unit, a sudoers fragment and an initialised cipher dir before
# the gate can prove V18, and Section 17 step 10 is the judging step, so this gap-fill step carries the setup under the
# marker phase2.09b. BASELINE AMENDMENT NEEDED (not a documentation nicety, fix round 4): CONVENTIONS.md §1 must list
# `09b-vault` in the phase2 list and Section 17 Phase 2 needs a line between steps 9 and 10, "9b. gocryptfs vault:
# helper, unit, sudoers, cipher dir (Section 11, D13; V18 at the gate)", or the Principal's `--force 09b` names a step
# the documents do not know (README-contracts.md §3 item 2).
#
# What it does, in order (each part idempotent):
#   0. If the vault is open (a --force re-run), lock it first: chmod/chown on a live FUSE mount root is refused.
#   1. apt gocryptfs 2.6.1-1 (= upstream v2.6.1, services-tools.md §4.11 VERIFIED; the installed upstream version is
#      compared against that pin and the step dies on a mismatch, rule §7.9, with ATLAS_GOCRYPTFS_VERSION_OK=1 as the
#      documented override once the man-page facts below are re-verified) and fuse3 (fusermount3 is the setuid helper
#      an unprivileged mount needs).
#   2. Layout under $ATLAS_SRV/vault (atlas:atlas 700): cipher/ (the real vault: the gocryptfs container Appendix C
#      backs up "as ciphertext"; phase2/07-restic.sh includes exactly this path) and open/ (the plaintext mount point,
#      excluded from restic by 07 and, belt and braces, by this step). The throw-away TEST vault (a second cipher dir
#      with a random passphrase in $ATLAS_ETC/secrets/vault-test.pass, so verify/v18-vault.sh can prove the mechanics
#      unattended at the gate without ever storing the Principal's passphrase) lives OUTSIDE the Appendix C vault tree,
#      at $ATLAS_SRV/staging/vault-test-cipher (fix round 4: staging is excluded from restic entirely by 07, so the tree
#      Appendix C says is "backed up as ciphertext" holds nothing that is not). The earlier location
#      $ATLAS_SRV/vault/test-cipher is removed when it still holds an initialised test vault (07 re-creates it empty and
#      still excludes it; both are harmless and 07 may drop them).
#   3. The vault control contract for the orchestrator (README-contracts.md "Vault"):
#        /etc/atlas/vault.env (root:atlas 640)  VAULT_CIPHER_DIR, VAULT_MOUNT_DIR, VAULT_IDLE=15m, ... (also copied into
#                                             orchestrator.env so the service sees them; restarted once when changed)
#        /usr/local/bin/atlas-vault           open|lock|status (+ init, override, wait-mounted, cleanup): the passphrase
#                                             arrives on STDIN, never as an argument, never in a log
#        /run/atlas-vault/ (root:root 755)    the ONLY transient home of the passphrase (pass, root:root 600, tmpfs,
#                                             shredded the moment the mount is up) and of override.env (root:root 644,
#                                             written by root verification runs only). Root-owned so the sudoers-granted
#                                             helper never operates by name inside a directory atlas can write
#                                             (symlink planting = root escalation; fix round). Never /run/atlas.
#                                             Fix round 2: the passfile is root:root 600, NOT group-readable, so no
#                                             other process running as or with group atlas (celery, llama-server) can
#                                             read the Principal's passphrase in the window before the mount. gocryptfs
#                                             (user atlas) gets it on the INHERITED fd 0: systemd opens the file as root
#                                             (StandardInput=file:) before dropping to User=atlas, and gocryptfs reads
#                                             ONE LINE FROM STDIN when stdin is not a terminal (gocryptfs v2.6.1
#                                             internal/readpassword/read.go lines 34-35 and 49-50: Once/Twice fall back
#                                             to readPasswordStdin, for mount and for -init alike; VERIFIED 2026-10-04 by
#                                             this writer against the v2.6.1 tag, and independently by the review).
#                                             Fix round 4 BLOCKER corrected: the earlier
#                                             `-passfile /dev/stdin` could never work for user atlas, because gocryptfs
#                                             opens the -passfile PATH by name (passfile.go:27 os.Open) and
#                                             /dev/stdin -> /proc/self/fd/0 is a
#                                             magic link the kernel re-opens with the caller's uid against the target
#                                             inode's mode (root:root 600 -> EACCES; reproduced with
#                                             `runuser -u nobody -- cat /dev/stdin < root600file`). No -passfile
#                                             anywhere now; init hands the same file to fd 0 through a root shell
#                                             redirection. The mechanics proof in part 5 exercises exactly this path,
#                                             so a wrong assumption dies HERE, not at the gate.
#                                             This tmpfs file is the declared exception to CONVENTIONS §7.2 "secrets
#                                             only under /etc/atlas/secrets/" (README-contracts.md §1, §3 item 11): §7.2
#                                             must admit "the transient tmpfs passfile /run/atlas-vault/pass, root:root
#                                             600, shredded the moment the mount is up". A file-less alternative
#                                             (`systemd-run --pipe`) was evaluated and rejected: the transient unit
#                                             would inherit the orchestrator's stdout/stderr pipes for the whole mount
#                                             lifetime (POST /vault/open would block until the idle lock) and
#                                             SetCredential=/StandardInputData= put the passphrase in unit properties.
#        /etc/systemd/system/atlas-vault.service   gocryptfs -fg -idle ${VAULT_IDLE} as user atlas, in the HOST mount
#                                             namespace (a mount made inside the orchestrator's own sandboxed unit
#                                             would be invisible to every other process); its hooks run with full
#                                             privileges (`+`) because only root may touch /run/atlas-vault.
#        /etc/sudoers.d/atlas-vault           atlas may run exactly: atlas-vault open | lock | status. `status` needs no
#                                             root (it reads /proc/self/mountinfo) but stays in the fragment because
#                                             the package's VaultController._argv() runs every verb through `sudo -n`
#                                             (orchestrator/src/atlas/vault.py); removing it would make the package's
#                                             status() log a helper error on every locked check.
#                                             BASELINE CONTRADICTION (fix round 4; recorded, not resolved here):
#                                             CONVENTIONS §8 says /etc/sudoers.d/atlas-engines "allows exactly those
#                                             commands and nothing else" and Section 23 S21 says the control path is "a
#                                             NOPASSWD sudoers fragment with exactly three systemctl verbs"; this second
#                                             fragment contradicts both literally. Why it exists anyway: the button is
#                                             pressed by the orchestrator (user atlas, ProtectSystem=full), the mount
#                                             must land in the HOST namespace so Celery and the session reader see it,
#                                             the passfile must stay root-only, and atlas-engines' explicit lines name
#                                             only llama-server@<key>; without this grant the orchestrator has no root
#                                             path to start the unit at all. The resolution is the baseline writer's:
#                                             (a) amend §8 and S21 to "atlas-engines (systemctl start|stop|restart
#                                             llama-server@<key>) and atlas-vault (atlas-vault open|lock|status)", with
#                                             02-orchestrator.sh:57 and 07-restic.sh:230 citing both, or (b) keep the
#                                             literal and give the vault another root path (an atlas-vault.service line
#                                             in atlas-engines plus a credential delivery that is not an atlas-written
#                                             file). (a) is the implemented state; 07-restic.sh's negative sudo test
#                                             probes atlas-aegis verbs only, so the two fragments do not collide.
#      The orchestrator's POST /vault/open therefore pipes the passphrase into `sudo -n /usr/local/bin/atlas-vault open`.
#   4. REAL-VAULT INITIALISATION IS OPT-IN (fix round 2). CONVENTIONS.md §7.6 lists exactly three interactive pauses and
#      says everything else runs unattended, so a plain `sudo ./atlas-day1.sh phase2` never stops here: by default the
#      real vault is left uninitialised (flag $ATLAS_STATE/vault-init-pending, warned, pushed by ntfy), the test vault is
#      initialised and proven, and the gate runs V18 on it and prints the exact command. The Principal initialises the
#      real vault from a console with
#          sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b
#      (`env` because sudo-rs's handling of `sudo VAR=value cmd` is UNVERIFIED; the entry script re-execs with `exec`,
#      so the variable reaches this step). Only then does the step read the passphrase (read -s from /dev/tty, twice on
#      first initialisation, once on a re-run) and, on first initialisation, wait for the gocryptfs master key to be
#      confirmed written down (the only recovery for a forgotten passphrase, D3). ATLAS_VAULT_INIT=1 without a terminal
#      dies with that message (an opt-in that cannot be honoured must not be skipped silently). The passphrase lives in
#      this process's memory only, is piped to the helper, never appears in argv, in a file that survives, or in any log.
#      A driver that exports ATLAS_FORCED_STEPS (contract requested in README-contracts.md §3; common.sh does not yet)
#      containing `09b` is honoured like ATLAS_VAULT_INIT=1 when a terminal is present.
#   5. Mechanics proof from the shell through the REAL button path (user atlas -> sudo-rs -> atlas-vault open ->
#      systemd unit): mount, write a file as atlas under $VAULT_MOUNT_DIR/.atlas-selftest/ (the ONLY path Day 1
#      automation ever touches inside a vault; v18 uses it too), read it back, confirm the cipher dir holds neither the
#      plaintext name nor the plaintext content, delete it, lock, confirm the mount is gone. On the real vault when the
#      passphrase was typed (the prompt says so in as many words: Section 16.3 rule 5 reserves vault modifications for
#      the Principal, so the consent is explicit, fix round 4), on the test vault otherwise (an override file,
#      root-written, points the unit there). Any failure after the open removes the file and locks the vault before
#      dying (nothing stays mounted or littered).
#   6. run_verify V18 with the Principal's passphrase piped in and V18_REAL_VAULT=1 exported (fix round 4: real mode is
#      explicit, never inferred from "something arrived on stdin"; the test itself opens through POST /vault/open and
#      uses a 20 s idle). A fail is recorded, not fatal here: the Phase 2 gate blocks on it. The gate keeps a real-mode
#      pass; otherwise it runs V18 unattended on the test cipher dir, where V18 records `deferred` (never `pass`) while
#      the real vault is uninitialised (the flag below exists), exactly as V7 is deferred without its recordings.
#
# Secrets directory (fix rounds 2-4, ONE value): $ATLAS_ETC/secrets is root:atlas 710 (traverse-only for atlas), the
# value phase2-services.sh asserts at the start of every Phase 2 run and phase2/02 (ORCH_SECRETS_MODE=710), 03, 06c, 07,
# 08 and 09 now write; this step's earlier `root:root 700` removed atlas's traversal and broke V20 and the hf/ntfy/redis
# env files. The fix-round-4 review asked for 750 "like every other writer"; that premise is stale (the Phase 2 writers
# moved to 710 in round 3; only phase1/02,03,07 still write 750, and both modes let atlas traverse). CONVENTIONS §2's row
# (root:root 700) must read `root:atlas 710 (traverse only; every file inside is 600 owned by its one reader)` (README §3
# item 10). What 710 must never become is listable or readable by drift: this step warns about, and the gate records as
# NOT HEALTHY, any file under $ATLAS_ETC/secrets that is group- or world-readable (`find -type f -perm /077`).
#
# Facts typed from services-tools.md §4.11 (VERIFIED man page): `gocryptfs -init [OPTIONS] CIPHERDIR`, mount
# `gocryptfs [OPTIONS] CIPHERDIR MOUNTPOINT`, `-idle duration` ("500s or 2h45m"; a process with open files or its
# cwd in the mount keeps it not idle), `-passfile FILE` (first line), `-fg`, `-q`, `-nosyslog`, `fusermount -u`,
# exit 12 = password incorrect, 10 = mount point not empty, 6 = cipher dir not empty on -init. No -allow_other: the
# kernel then denies every other user, root included, which is what keeps restic (root) out of the plaintext view.
# UNVERIFIED: that a systemd unit with ProtectSystem= (the orchestrator) sees a FUSE mount made later on the host —
# slave propagation is the documented default, and v18 proves it through the orchestrator's GET /vault/status.
#
# Contracts relied on from other writers: $ATLAS_ETC/orchestrator.env is written by phase2/02-orchestrator.sh with
# ensure_kv (keys other steps add are kept); $ATLAS_ETC/restic-exclude.txt by phase2/07-restic.sh (this step only
# appends lines with ensure_line); POST /vault/open, GET /vault/status and `atlas-admin vault-session-test --file PATH`
# are the orchestrator's (README-contracts.md). sudo-rs (conflict 7) resets the environment for the button path and the
# fragment carries no SETENV, so VAULT_*_OVERRIDE can only ever come from a root caller.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

VAULT_ROOT="$ATLAS_SRV/vault"
VAULT_CIPHER_DIR="$VAULT_ROOT/cipher"
VAULT_MOUNT_DIR="$VAULT_ROOT/open"
# The throw-away test vault lives under staging (excluded from restic entirely by phase2/07-restic.sh), never inside the
# Appendix C vault tree (header part 2, fix round 4). verify/v18-vault.sh reads this path from vault.env.
VAULT_TEST_CIPHER_DIR="$ATLAS_SRV/staging/vault-test-cipher"
VAULT_LEGACY_TEST_CIPHER_DIR="$VAULT_ROOT/test-cipher"   # earlier revision; removed below when it holds a test vault
VAULT_TEST_PASS_FILE="$ATLAS_ETC/secrets/vault-test.pass"
VAULT_ENV_FILE="$ATLAS_ETC/vault.env"
VAULT_HELPER=/usr/local/bin/atlas-vault
VAULT_UNIT=atlas-vault.service
VAULT_SUDOERS=/etc/sudoers.d/atlas-vault
VAULT_IDLE=15m                      # D13: auto-lock after 15 minutes idle
VAULT_RUN_DIR=/run/atlas-vault      # root:root 755 on tmpfs; never the orchestrator's /run/atlas (atlas-writable)
VAULT_PASS_FILE="$VAULT_RUN_DIR/pass"
VAULT_OVERRIDE_FILE="$VAULT_RUN_DIR/override.env"
VAULT_PENDING_FLAG="$ATLAS_STATE/vault-init-pending"
VAULT_RESTIC_EXCLUDE="$ATLAS_ETC/restic-exclude.txt"

# vault_is_mounted DIR — true when DIR is a mount point, read from /proc/self/mountinfo (field 5). `mountpoint -q`
# stats the directory, which the FUSE kernel driver denies to every user but the mount owner (root included).
vault_is_mounted() {
  awk -v m="$1" '$5 == m { f = 1 } END { exit !f }' /proc/self/mountinfo
}

_vault_lock_if_open() {
  if vault_is_mounted "$VAULT_MOUNT_DIR" || systemctl is-active --quiet "$VAULT_UNIT" 2>/dev/null; then
    log "vault is open at $VAULT_MOUNT_DIR; locking it first (a re-run must never chmod a live FUSE mount)"
    if [[ -x "$VAULT_HELPER" ]]; then
      "$VAULT_HELPER" lock >/dev/null || die "could not lock the open vault at $VAULT_MOUNT_DIR ($VAULT_HELPER lock)"
    else
      systemctl stop "$VAULT_UNIT" 2>/dev/null || true
      fusermount3 -u "$VAULT_MOUNT_DIR" 2>/dev/null || umount -l "$VAULT_MOUNT_DIR" 2>/dev/null || true
    fi
    vault_is_mounted "$VAULT_MOUNT_DIR" && die "$VAULT_MOUNT_DIR is still mounted (a process holds files open in it: lsof +f -- $VAULT_MOUNT_DIR)"
  fi
  return 0
}

VAULT_GOCRYPTFS_PIN="2.6.1-1"        # services-tools.md §4.11 VERIFIED: the Ubuntu 26.04 archive package (upstream v2.6.1)

_vault_apt() {
  apt_install gocryptfs fuse3
  local ver pkgver upstream
  ver="$(gocryptfs -version 2>/dev/null | head -n1 || true)"
  [[ -n "$ver" ]] || die "gocryptfs -version printed nothing after apt_install (package gocryptfs $VAULT_GOCRYPTFS_PIN expected)"
  command -v fusermount3 >/dev/null || die "fusermount3 missing (package fuse3): unprivileged FUSE mounts cannot work"
  # Rule §7.9: the research gives a pin, so the installed version is held to it. The archive cannot be told to install an
  # older version, so this is a check, not a selection: a different upstream version means the man-page facts this step
  # types (exit codes 12/10/6, -idle, -passfile, -fg, -q, -nosyslog) must be re-verified before the override is used.
  pkgver="$(dpkg-query -W -f='${Version}' gocryptfs 2>/dev/null || true)"
  upstream="${pkgver%%-*}"; upstream="${upstream#*:}"
  if [[ "$upstream" != "${VAULT_GOCRYPTFS_PIN%%-*}" ]]; then
    if [[ "${ATLAS_GOCRYPTFS_VERSION_OK:-0}" == 1 ]]; then
      warn "gocryptfs $pkgver installed; the VERIFIED pin is $VAULT_GOCRYPTFS_PIN (services-tools.md §4.11); continuing because ATLAS_GOCRYPTFS_VERSION_OK=1"
    else
      die "gocryptfs $pkgver installed but the VERIFIED pin is $VAULT_GOCRYPTFS_PIN (services-tools.md §4.11; rule §7.9). Re-verify the man-page facts in this file's header against the installed version, then re-run with: sudo env ATLAS_GOCRYPTFS_VERSION_OK=1 ${ATLAS_ENTRY:-./atlas-day1.sh} phase2"
    fi
  fi
  log "gocryptfs: $ver (package $pkgver; pin $VAULT_GOCRYPTFS_PIN)"
}

_vault_dirs() {
  ensure_dir "$VAULT_ROOT" atlas:atlas 700
  ensure_dir "$VAULT_CIPHER_DIR" atlas:atlas 700
  ensure_dir "$VAULT_MOUNT_DIR" atlas:atlas 700
  # The test vault sits under staging (Phase 1 step 3 creates $ATLAS_SRV/staging on the data volume; restic excludes it).
  [[ -d "$ATLAS_SRV/staging" ]] || die "$ATLAS_SRV/staging does not exist (Phase 1 step 3 creates it); the test vault needs a restic-excluded home"
  ensure_dir "$VAULT_TEST_CIPHER_DIR" atlas:atlas 700
  if [[ -f "$VAULT_LEGACY_TEST_CIPHER_DIR/gocryptfs.conf" ]]; then
    # Only ever held the gate's throw-away vault (random passphrase, nothing of the Principal's): remove it so the
    # Appendix C tree holds the real container alone. 07-restic.sh may re-create it empty; harmless.
    rm -rf -- "$VAULT_LEGACY_TEST_CIPHER_DIR"
    log "removed the earlier test vault at $VAULT_LEGACY_TEST_CIPHER_DIR (now $VAULT_TEST_CIPHER_DIR, outside the Appendix C tree)"
  fi
  # root:atlas 710 (header: SECRETS DIRECTORY): atlas traverses to its own files, lists nothing; the one Phase 2 value.
  ensure_dir "$ATLAS_ETC/secrets" root:atlas 710
  # 710 is only safe while every file inside stays unreadable to the group: warn here, the gate records NOT HEALTHY.
  local loose
  loose="$(find "$ATLAS_ETC/secrets" -type f -perm /077 2>/dev/null | tr '\n' ' ' || true)"
  [[ -z "$loose" ]] || warn "group- or world-readable file(s) under $ATLAS_ETC/secrets (every secret must be 600, CONVENTIONS §7.2): $loose— the gate records this as NOT HEALTHY"
  # The runtime dir is root-owned on tmpfs (/run): the helper re-creates it on every open; fail now if /run is not tmpfs.
  local fst
  fst="$(findmnt -n -o FSTYPE --target /run 2>/dev/null || true)"
  [[ "$fst" == tmpfs ]] || die "/run is '$fst', not tmpfs: the vault passphrase file $VAULT_PASS_FILE would touch disk (CONVENTIONS.md §7.2)"
  install -d -m 755 -o root -g root "$VAULT_RUN_DIR"
  # restic (Section 10.5, CONVENTIONS.md §7.2): the plaintext view and the throw-away test vault are never in the include
  # set. 07-restic.sh excludes the mount point and all of staging already; ensure_line keeps both whatever 07's revision
  # says (the explicit test-cipher line is belt and braces under the staging exclusion).
  if [[ -f "$VAULT_RESTIC_EXCLUDE" ]]; then
    ensure_line "$VAULT_RESTIC_EXCLUDE" "$VAULT_MOUNT_DIR"
    ensure_line "$VAULT_RESTIC_EXCLUDE" "$VAULT_TEST_CIPHER_DIR"
    log "restic exclusions present in $VAULT_RESTIC_EXCLUDE: $VAULT_MOUNT_DIR, $VAULT_TEST_CIPHER_DIR"
  else
    warn "$VAULT_RESTIC_EXCLUDE does not exist (Phase 2 step 7 writes it); the gate checks the exclusion before V13"
  fi
  log "vault layout: cipher $VAULT_CIPHER_DIR, mount $VAULT_MOUNT_DIR, test cipher $VAULT_TEST_CIPHER_DIR (atlas:atlas 700); runtime $VAULT_RUN_DIR (root:root 755, tmpfs)"
}

_file_hash() { [[ -e "$1" ]] && sha256sum "$1" | cut -c1-64 || echo none; }

# Sets VAULT_ORCH_ENV_CHANGED=1 when orchestrator.env really changed (ensure_kv rewrites the file on every call).
_vault_env() {
  [[ -e "$VAULT_ENV_FILE" ]] || : >"$VAULT_ENV_FILE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_CIPHER_DIR "$VAULT_CIPHER_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_MOUNT_DIR "$VAULT_MOUNT_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_TEST_CIPHER_DIR "$VAULT_TEST_CIPHER_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_IDLE "$VAULT_IDLE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_UNIT "$VAULT_UNIT"
  ensure_kv "$VAULT_ENV_FILE" VAULT_HELPER "$VAULT_HELPER"
  ensure_kv "$VAULT_ENV_FILE" VAULT_RUN_DIR "$VAULT_RUN_DIR"
  ensure_kv "$VAULT_ENV_FILE" VAULT_PASS_FILE "$VAULT_PASS_FILE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_OVERRIDE_FILE "$VAULT_OVERRIDE_FILE"
  ensure_kv "$VAULT_ENV_FILE" VAULT_USER atlas
  chown root:atlas "$VAULT_ENV_FILE"
  chmod 640 "$VAULT_ENV_FILE"          # CONVENTIONS.md §2: non-secret settings are root:atlas 640; the unit (atlas) reads it
  # The orchestrator service loads orchestrator.env, not vault.env: mirror the keys the package needs (step 02 wrote
  # that file with ensure_kv, which keeps foreign keys). Restarted once below if anything changed.
  local orch="$ATLAS_ETC/orchestrator.env" before
  [[ -e "$orch" ]] || die "$orch missing: Phase 2 step 2 has not run (it writes orchestrator.env)"
  before="$(_file_hash "$orch")"
  ensure_kv "$orch" VAULT_CIPHER_DIR "$VAULT_CIPHER_DIR"
  ensure_kv "$orch" VAULT_MOUNT_DIR "$VAULT_MOUNT_DIR"
  ensure_kv "$orch" VAULT_IDLE "$VAULT_IDLE"
  ensure_kv "$orch" VAULT_UNIT "$VAULT_UNIT"
  ensure_kv "$orch" VAULT_HELPER "$VAULT_HELPER"
  VAULT_ORCH_ENV_CHANGED=0
  [[ "$(_file_hash "$orch")" == "$before" ]] || VAULT_ORCH_ENV_CHANGED=1
  log "wrote $VAULT_ENV_FILE (root:atlas 640) and the VAULT_* keys of $orch (changed=$VAULT_ORCH_ENV_CHANGED)"
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
#   atlas-vault override root only (verification runs): writes VAULT_OVERRIDE_FILE from VAULT_IDLE_OVERRIDE /
#                        VAULT_CIPHER_OVERRIDE for the NEXT open, whoever performs it (the button included), so
#                        verify/v18-vault.sh can open the TEST vault with a 20 s idle through POST /vault/open.
#   atlas-vault wait-mounted | cleanup   used by atlas-vault.service only (ExecStartPost=+ / ExecStopPost=-+, root).
#
# Overrides (root only; sudo-rs resets the environment and /etc/sudoers.d/atlas-vault has no SETENV, so the button
# path can never carry them, and the override file lives in a root-only directory, so atlas can never plant one):
#   VAULT_CIPHER_OVERRIDE=<dir>   mount/init this cipher dir instead of VAULT_CIPHER_DIR (the test vault)
#   VAULT_IDLE_OVERRIDE=<dur>     e.g. 20s; may only SHORTEN VAULT_IDLE (15m), never lengthen or disable it
# A written override applies to ONE open (wait-mounted removes it) and expires after 600 s unused, so a crashed
# verification run can never redirect the Principal's next button press. The passphrase is never an argument,
# never logged, and lives on tmpfs (VAULT_PASS_FILE, root:root 600: no atlas process can read it) only between the
# button press and the mount; wait-mounted and cleanup shred it. gocryptfs (user atlas) receives it on the INHERITED
# fd 0: systemd opens the file as root (StandardInput=file: in atlas-vault.service) before dropping to User=atlas, and
# gocryptfs reads one line from stdin whenever stdin is not a terminal (v2.6.1 readpassword: Once/Twice ->
# readPasswordStdin, mount and -init alike). No -passfile: `-passfile /dev/stdin` would make gocryptfs re-open
# /proc/self/fd/0 by name as atlas against a root:root 600 inode (EACCES). init hands the same file to fd 0 through a
# root shell redirection. Everything under VAULT_RUN_DIR is created with mktemp + rename, symlinks refused: root never
# follows a name into a directory another account could write.
set -Eeuo pipefail
ENV_FILE=/etc/atlas/vault.env
[[ -r "$ENV_FILE" ]] || { echo "atlas-vault: $ENV_FILE missing (scripts/day1/phase2/09b-vault.sh writes it)" >&2; exit 2; }
set -a
# shellcheck disable=SC1090  # KEY=VALUE lines written by 09b-vault.sh
source "$ENV_FILE"
set +a
: "${VAULT_CIPHER_DIR:?}" "${VAULT_MOUNT_DIR:?}"
: "${VAULT_IDLE:=15m}" "${VAULT_UNIT:=atlas-vault.service}" "${VAULT_USER:=atlas}"
: "${VAULT_RUN_DIR:=/run/atlas-vault}" "${VAULT_PASS_FILE:=/run/atlas-vault/pass}" "${VAULT_OVERRIDE_FILE:=/run/atlas-vault/override.env}"
OVERRIDE_MAX_AGE_S=600

is_mounted() { awk -v m="$VAULT_MOUNT_DIR" '$5 == m { f = 1 } END { exit !f }' /proc/self/mountinfo; }
need_root() { [[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "atlas-vault $1: needs root (the orchestrator runs: sudo -n /usr/local/bin/atlas-vault $1)" >&2; exit 2; }; }

# run_dir — the root-only tmpfs directory; refuses anything that is not a root-owned directory on tmpfs.
run_dir() {
  [[ ! -L "$VAULT_RUN_DIR" ]] || { echo "atlas-vault: $VAULT_RUN_DIR is a symlink; refusing" >&2; exit 2; }
  install -d -m 755 -o root -g root "$VAULT_RUN_DIR"
  local fst own
  fst="$(findmnt -n -o FSTYPE --target "$VAULT_RUN_DIR" 2>/dev/null || true)"
  [[ "$fst" == tmpfs ]] || { echo "atlas-vault: $VAULT_RUN_DIR is on '$fst', not tmpfs; the passphrase must never touch disk" >&2; exit 2; }
  own="$(stat -c '%u:%g:%a' "$VAULT_RUN_DIR")"
  [[ "$own" == 0:0:755 ]] || { echo "atlas-vault: $VAULT_RUN_DIR is $own, expected root:root 755" >&2; exit 2; }
  local f
  for f in "$VAULT_PASS_FILE" "$VAULT_OVERRIDE_FILE"; do
    [[ ! -L "$f" ]] || { echo "atlas-vault: $f is a symlink; refusing" >&2; exit 2; }
  done
}

# write_run_file PATH OWNER MODE — content on stdin; mktemp in the run dir, chown/chmod the temp file, rename over PATH.
write_run_file() {
  local path="$1" owner="$2" mode="$3" t
  t="$(mktemp -p "$VAULT_RUN_DIR" .new.XXXXXX)"
  cat >"$t"
  chown "$owner" "$t"
  chmod "$mode" "$t"
  mv -T -f "$t" "$path"
}

shred_pass() {
  [[ -e "$VAULT_PASS_FILE" || -L "$VAULT_PASS_FILE" ]] || return 0
  if [[ -L "$VAULT_PASS_FILE" || ! -f "$VAULT_PASS_FILE" ]]; then rm -f "$VAULT_PASS_FILE"; return 0; fi
  shred -u "$VAULT_PASS_FILE" 2>/dev/null || rm -f "$VAULT_PASS_FILE"
}

# read_pass — one line from stdin into the tmpfs passfile (root:root 600: nothing running as atlas can read, unlink or
# replace it; gocryptfs gets the content on fd 0 from root). -s keeps a terminal from echoing; -t bounds a caller that
# forgot to pipe.
read_pass() {
  local p=""
  IFS= read -r -s -t 120 p || true
  # shellcheck disable=SC2016  # the "$pass" in the hint is a literal example for the caller
  [[ -n "$p" ]] || { printf 'atlas-vault: no passphrase arrived on stdin within 120 s (pipe it: printf "%%s\\n" "$pass" | atlas-vault %s)\n' "$1" >&2; exit 2; }
  run_dir
  shred_pass
  printf '%s\n' "$p" | write_run_file "$VAULT_PASS_FILE" root:root 600
  p=""
}

# dur_s DURATION — gocryptfs/Go duration ("20s", "15m", "2h45m", "500s") to seconds; empty output when unparseable.
dur_s() {
  local d="$1" total=0 n u rest
  [[ "$d" =~ ^([0-9]+[hms])+$ ]] || { echo ""; return 0; }
  rest="$d"
  while [[ -n "$rest" ]]; do
    [[ "$rest" =~ ^([0-9]+)([hms])(.*)$ ]] || { echo ""; return 0; }
    n="${BASH_REMATCH[1]}"; u="${BASH_REMATCH[2]}"; rest="${BASH_REMATCH[3]}"
    case "$u" in h) total=$(( total + n * 3600 )) ;; m) total=$(( total + n * 60 )) ;; s) total=$(( total + n )) ;; esac
  done
  echo "$total"
}

# override_write — from VAULT_IDLE_OVERRIDE / VAULT_CIPHER_OVERRIDE (root only). Validates: the idle may only shorten
# VAULT_IDLE and never be 0 (D13 stays authoritative); the cipher dir must be an initialised vault.
override_write() {
  local idle="${VAULT_IDLE_OVERRIDE:-}" cipher="${VAULT_CIPHER_OVERRIDE:-}" base_s new_s
  [[ -n "$idle" || -n "$cipher" ]] || return 0
  run_dir
  if [[ -n "$idle" ]]; then
    base_s="$(dur_s "$VAULT_IDLE")"; new_s="$(dur_s "$idle")"
    [[ -n "$base_s" && -n "$new_s" ]] || { echo "atlas-vault override: unparseable idle '$idle' or VAULT_IDLE '$VAULT_IDLE' (forms: 20s, 15m, 2h45m)" >&2; exit 2; }
    (( new_s > 0 )) || { echo "atlas-vault override: VAULT_IDLE_OVERRIDE=0 would disable the D13 auto-lock; refused" >&2; exit 2; }
    (( new_s <= base_s )) || { echo "atlas-vault override: VAULT_IDLE_OVERRIDE=$idle is longer than VAULT_IDLE=$VAULT_IDLE; an override may only shorten it" >&2; exit 2; }
  fi
  if [[ -n "$cipher" ]]; then
    [[ "$cipher" == /* && ! -L "$cipher" && -d "$cipher" && -f "$cipher/gocryptfs.conf" ]] \
      || { echo "atlas-vault override: VAULT_CIPHER_OVERRIDE=$cipher is not an initialised vault directory" >&2; exit 2; }
  fi
  {
    printf '# written %s by atlas-vault override (uid %s); applies to the next open only, expires after %ss\n' "$(date +%s)" "${SUDO_UID:-$EUID}" "$OVERRIDE_MAX_AGE_S"
    [[ -z "$idle" ]] || printf 'VAULT_IDLE=%s\n' "$idle"
    [[ -z "$cipher" ]] || printf 'VAULT_CIPHER_DIR=%s\n' "$cipher"
  } | write_run_file "$VAULT_OVERRIDE_FILE" root:root 644
}

# override_value KEY — KEY from a valid (root-owned regular file, not expired) override file; removes a stale one.
override_value() {
  local f="$VAULT_OVERRIDE_FILE" own ts now
  [[ -e "$f" ]] || return 0
  if [[ -L "$f" || ! -f "$f" ]]; then rm -f "$f"; echo "atlas-vault: $f was not a regular file; removed" >&2; return 0; fi
  own="$(stat -c '%u' "$f")"
  [[ "$own" == 0 ]] || { rm -f "$f"; echo "atlas-vault: $f was not root-owned; removed" >&2; return 0; }
  ts="$(sed -nE '1s/^# written ([0-9]+) .*/\1/p' "$f")"
  now="$(date +%s)"
  if [[ ! "$ts" =~ ^[0-9]+$ ]] || (( now - ts > OVERRIDE_MAX_AGE_S )); then
    rm -f "$f"; echo "atlas-vault: stale override file removed (older than ${OVERRIDE_MAX_AGE_S}s); the defaults apply" >&2; return 0
  fi
  sed -nE "s/^$1=(.*)$/\1/p" "$f" | head -n1
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
  run_dir
  # A root caller with override variables writes the file now; the button path (sudo-rs, env reset) never has them and
  # uses whatever a root verification run left for this one open (or nothing).
  override_write
  local cipher idle
  cipher="$(override_value VAULT_CIPHER_DIR)"; cipher="${cipher:-$VAULT_CIPHER_DIR}"
  idle="$(override_value VAULT_IDLE)"; idle="${idle:-$VAULT_IDLE}"
  if [[ ! -f "$cipher/gocryptfs.conf" ]]; then
    rm -f "$VAULT_OVERRIDE_FILE"
    echo "atlas-vault open: $cipher/gocryptfs.conf missing: the vault is not initialised (run from a console: sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b)" >&2
    exit 2
  fi
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
  [[ "$cipher" == "$VAULT_CIPHER_DIR" && "$idle" == "$VAULT_IDLE" ]] || echo "atlas-vault open: override in effect for this open: cipher $cipher, idle $idle" >&2
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

do_override() {
  need_root override
  # Not in /etc/sudoers.d/atlas-vault: atlas cannot reach this verb; the whole Day 1 run is itself under sudo.
  [[ -n "${VAULT_IDLE_OVERRIDE:-}" || -n "${VAULT_CIPHER_OVERRIDE:-}" ]] \
    || { echo "atlas-vault override: set VAULT_IDLE_OVERRIDE and/or VAULT_CIPHER_OVERRIDE" >&2; exit 2; }
  override_write
  [[ -s "$VAULT_OVERRIDE_FILE" ]] || { echo "atlas-vault override: $VAULT_OVERRIDE_FILE was not written" >&2; exit 2; }
  echo "override written: idle ${VAULT_IDLE_OVERRIDE:-$VAULT_IDLE}, cipher ${VAULT_CIPHER_OVERRIDE:-$VAULT_CIPHER_DIR} (next open only)"
}

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
  # No -q: gocryptfs prints the master key on init and the caller must be able to show it once (D3). The root:root 600
  # passfile is opened by THIS root shell and handed to gocryptfs (user atlas) as fd 0; stdin is not a terminal, so
  # gocryptfs -init reads the passphrase from it, once, with no confirmation prompt (no -passfile: see the header).
  out="$(runuser -u "$VAULT_USER" -- gocryptfs -init "$cipher" <"$VAULT_PASS_FILE" 2>&1)" || rc=$?
  shred_pass
  if (( rc != 0 )) || [[ ! -f "$cipher/gocryptfs.conf" ]]; then
    echo "atlas-vault init: gocryptfs -init failed (exit $rc; 6 = cipher dir not empty, 22 = empty passphrase): $out" >&2
    exit 1
  fi
  printf '%s\n' "$out"
}

# --- unit-internal (ExecStartPost=+ / ExecStopPost=-+: full privileges, so the root-only run dir can be cleaned) -------
do_wait_mounted() {
  need_root wait-mounted
  # The unit is "started" only once the plaintext view exists; then the passfile and the one-shot override go.
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
  override) do_override ;;
  wait-mounted) do_wait_mounted ;;
  cleanup) need_root cleanup; cleanup ;;
  *) echo "usage: atlas-vault open|lock|status|init|override   (passphrase on stdin for open and init)" >&2; exit 2 ;;
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
  # ${VAULT_*} in Exec lines below are systemd substitutions from the EnvironmentFiles and stay literal (escaped in this
  # heredoc); Description= gets no substitution from systemd, so the idle is rendered now (fix round 2).
  cat >"$tmp" <<UNIT
# /etc/systemd/system/atlas-vault.service — the open vault (Section 11, D13). Written by scripts/day1/phase2/09b-vault.sh.
# Started ONLY by /usr/local/bin/atlas-vault open (the interface button path), never at boot: gocryptfs runs in the
# foreground as user atlas in the host mount namespace, auto-unmounts after $VAULT_IDLE idle (VAULT_IDLE, or a shorter
# one-shot override) and then exits, so "systemctl is-active atlas-vault" mirrors the vault state. The passfile is tmpfs
# (root:root 600, in the root-only $VAULT_RUN_DIR): systemd opens it as root and hands it to gocryptfs as STDIN
# (StandardInput=file:), gocryptfs reads one line from its non-terminal stdin (no -passfile: a path re-open of
# /dev/stdin as atlas would be refused on the root-only inode), and wait-mounted shreds it the moment the mount is up
# (full privileges, the '+' prefix, because atlas may not touch that directory). No process running as atlas can ever
# read the file. The override file (root-written, verification runs only) may shorten the idle or point at the test
# vault for ONE open; vault.env is loaded first so its VAULT_IDLE is the default the override shortens.
# NoNewPrivileges must stay off: an unprivileged FUSE mount goes through the setuid fusermount3.
[Unit]
Description=A.T.L.A.S. vault (gocryptfs plaintext view, auto-locks after $VAULT_IDLE idle)
Documentation=file:$VAULT_ENV_FILE
ConditionPathExists=$VAULT_PASS_FILE

[Service]
Type=simple
User=atlas
Group=atlas
EnvironmentFile=$VAULT_ENV_FILE
EnvironmentFile=-$VAULT_OVERRIDE_FILE
StandardInput=file:$VAULT_PASS_FILE
ExecStart=/usr/bin/gocryptfs -fg -q -nosyslog -idle \${VAULT_IDLE} \${VAULT_CIPHER_DIR} \${VAULT_MOUNT_DIR}
ExecStartPost=+$VAULT_HELPER wait-mounted
ExecStopPost=-+$VAULT_HELPER cleanup
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
  # Same pattern as atlas-engines (plain sudoers syntax only, sudo-rs on 26.04, conflict 7). This second fragment
  # (exactly open|lock|status) contradicts CONVENTIONS §8 / Section 23 S21 literally; the header states why it exists and
  # the two ways the baseline writer can resolve it (README-contracts.md §3 item 6). visudo, when present and able to
  # check a file, is the first parse; the proof that counts is sudo's own below, exactly as phase2/01-llama.sh does (fix
  # round 4: whether Ubuntu 26.04's sudo-rs ships a visudo that accepts `-c -f FILE` is UNVERIFIED, so its absence or a
  # usage error is a warning, never a hard stop; any other visudo failure is a real parse error and stops the step).
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<SUDO
# atlas-vault — written by scripts/day1/phase2/09b-vault.sh (Section 11). The orchestrator's /vault/open, /vault/lock
# and /vault/status run exactly these (atlas.vault.VaultController runs every verb through sudo -n), with the
# passphrase on stdin. 'status' needs no privilege but is listed so the package's status() never logs a sudo error.
# Nothing else is permitted.
atlas ALL=(root) NOPASSWD: $VAULT_HELPER open
atlas ALL=(root) NOPASSWD: $VAULT_HELPER lock
atlas ALL=(root) NOPASSWD: $VAULT_HELPER status
SUDO
  if command -v visudo >/dev/null 2>&1; then
    local vout
    if ! vout="$(visudo -c -f "$tmp" 2>&1)"; then
      if grep -qiE 'usage:|unknown option|invalid option|unrecognized|unexpected argument' <<<"$vout"; then
        warn "visudo cannot check a file here (${vout//$'\n'/ }); relying on sudo's own parse below"
      else
        rm -f "$tmp"
        die "sudoers fragment failed visudo -c: ${vout//$'\n'/ }; not installed"
      fi
    fi
  else
    warn "visudo not found (sudo-rs without it? UNVERIFIED); installing $VAULT_SUDOERS and proving it with sudo -n below"
  fi
  # Keep the previous fragment so a failed proof can restore it (an empty file is a valid no-op policy).
  local prev
  prev="$(mktemp)"
  [[ -f "$VAULT_SUDOERS" ]] && cp -p "$VAULT_SUDOERS" "$prev"
  install -m 440 -o root -g root "$tmp" "$VAULT_SUDOERS"
  rm -f "$tmp"
  # Proof that sudo-rs accepts the fragment for the atlas user (status needs no passphrase). On failure the fragment is
  # restored (or removed) again so the node keeps a working sudo.
  local st
  if ! st="$(runuser -u atlas -- sudo -n "$VAULT_HELPER" status 2>&1)"; then
    if [[ -s "$prev" ]]; then install -m 440 -o root -g root "$prev" "$VAULT_SUDOERS"; else rm -f "$VAULT_SUDOERS"; fi
    rm -f "$prev"
    die "'sudo -n $VAULT_HELPER status' as atlas failed under sudo-rs: $st (fragment restored/removed again; check: runuser -u atlas -- sudo -n -l)"
  fi
  rm -f "$prev"
  log "installed $VAULT_SUDOERS (atlas -> sudo -n atlas-vault status: $st)"
}

_vault_has_tty() { [[ -r /dev/tty && -w /dev/tty ]] && { : </dev/tty; } 2>/dev/null; }

# _vault_init_requested — the Principal asked for the real vault's initialisation in this run (header part 4):
# ATLAS_VAULT_INIT=1 in the environment, or a driver that exports ATLAS_FORCED_STEPS naming 09b (contract requested).
_vault_init_requested() {
  [[ "${ATLAS_VAULT_INIT:-0}" == 1 ]] && return 0
  [[ " ${ATLAS_FORCED_STEPS:-} " == *" 09b "* ]] && return 0
  return 1
}

# _vault_tty_pass VAR PROMPT — read a hidden line from the terminal into VAR (never echoed, never logged).
_vault_tty_pass() {
  local prompt="$2"
  read -r -s -p "$prompt" "${1?}" </dev/tty >/dev/tty
  echo >/dev/tty
}

# Sets VAULT_PASS in the caller's scope (the only place the passphrase ever lives). Caller checked _vault_has_tty.
_vault_prompt() {
  echo >/dev/tty
  echo "The vault (Section 11) is an encrypted folder at $VAULT_CIPHER_DIR, opened by the interface button and locked" >/dev/tty
  echo "after $VAULT_IDLE idle. Type its passphrase now: it is used for this step's checks and then forgotten. It is never" >/dev/tty
  echo "stored, never logged, and there is no reset: a forgotten passphrase means the master key shown below or nothing (D3)." >/dev/tty
  # Section 16.3 rule 5 reserves vault modifications for the Principal: say exactly what the checks will write.
  echo "This step will write one self-test file under $VAULT_SELFTEST_SUBDIR/ inside the vault, read it back, and delete it" >/dev/tty
  echo "(V18 does the same once more through the interface button); nothing else in the vault is touched." >/dev/tty
  local p1 p2
  if [[ -f "$VAULT_CIPHER_DIR/gocryptfs.conf" ]]; then
    _vault_tty_pass p1 "Vault passphrase (existing vault, input hidden): "
  else
    _vault_tty_pass p1 "New vault passphrase (input hidden): "
    _vault_tty_pass p2 "Repeat it: "
    [[ "$p1" == "$p2" ]] || die "the two passphrases differ; nothing written, re-run: sudo env ATLAS_VAULT_INIT=1 ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
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

VAULT_SELFTEST_SUBDIR=".atlas-selftest"   # the ONLY path Day 1 automation touches inside a vault (here and verify/v18-vault.sh)

# _vault_mech_abort MSG — a failure after the open: remove the self-test file as atlas, lock, then die. `die` exits
# through no RETURN trap, so every check after the open goes through this instead (nothing stays mounted or littered).
_vault_mech_abort() {
  local msg="$1"
  if [[ -n "${VAULT_MECH_FILE:-}" ]] && vault_is_mounted "$VAULT_MOUNT_DIR"; then
    runuser -u atlas -- rm -f "$VAULT_MECH_FILE" 2>/dev/null || true
    runuser -u atlas -- rmdir "$VAULT_MOUNT_DIR/$VAULT_SELFTEST_SUBDIR" 2>/dev/null || true
  fi
  if vault_is_mounted "$VAULT_MOUNT_DIR" || systemctl is-active --quiet "$VAULT_UNIT" 2>/dev/null; then
    "$VAULT_HELPER" lock >/dev/null 2>&1 || true
  fi
  die "$msg"
}

# _vault_mechanics CIPHER_DIR PASS_SOURCE — the mechanics through the real button path: atlas -> sudo-rs -> atlas-vault
# open -> unit. PASS_SOURCE is "real" (VAULT_PASS in memory) or a file (the test passphrase). For the test vault a
# root-written override file points the ONE next open at it (the same mechanism verify/v18-vault.sh uses).
_vault_mechanics() {
  local cipher="$1" src="$2" name content st
  name="$VAULT_SELFTEST_SUBDIR/mechanics-$(date +%s)"
  content="ATLAS vault mechanics check $(date -Is) $(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  VAULT_MECH_FILE=""
  _vault_lock_if_open
  if [[ "$cipher" != "$VAULT_CIPHER_DIR" ]]; then
    VAULT_CIPHER_OVERRIDE="$cipher" "$VAULT_HELPER" override >/dev/null || die "atlas-vault override for $cipher failed"
    [[ -s "$VAULT_OVERRIDE_FILE" ]] || die "$VAULT_OVERRIDE_FILE did not appear after 'atlas-vault override'"
  fi
  if [[ "$src" == real ]]; then
    # The captured output never reaches the log (§7.2): sudo-rs's pty relaying of a piped stdin on a tty session is
    # UNVERIFIED, and an echoed passphrase would otherwise land in $st on a wrong-passphrase attempt. Journal instead.
    st="$(printf '%s\n' "$VAULT_PASS" | runuser -u atlas -- sudo -n "$VAULT_HELPER" open 2>&1)" \
      || die "the button path failed for the real vault ('sudo -n $VAULT_HELPER open' as atlas, passphrase on stdin; wrong passphrase = gocryptfs exit 12); see: journalctl -u $VAULT_UNIT -n 8"
    st="${st//"$VAULT_PASS"/[redacted]}"
  else
    st="$(runuser -u atlas -- sudo -n "$VAULT_HELPER" open <"$src" 2>&1)" \
      || die "the button path failed on the test vault: 'sudo -n $VAULT_HELPER open' as atlas -> $st"
  fi
  vault_is_mounted "$VAULT_MOUNT_DIR" || _vault_mech_abort "atlas-vault open printed '$st' but $VAULT_MOUNT_DIR is not mounted"
  systemctl is-active --quiet "$VAULT_UNIT" || _vault_mech_abort "$VAULT_MOUNT_DIR is mounted but $VAULT_UNIT is not active (the mount did not come from the unit)"
  [[ ! -e "$VAULT_PASS_FILE" ]] || _vault_mech_abort "$VAULT_PASS_FILE still exists after the mount (wait-mounted must shred it)"
  [[ ! -e "$VAULT_OVERRIDE_FILE" ]] || _vault_mech_abort "$VAULT_OVERRIDE_FILE survived the mount (wait-mounted must remove the one-shot override)"
  # The unit must have mounted the cipher dir we meant (the override was honoured, or the default applied).
  local pid cmd
  pid="$(systemctl show -p MainPID --value "$VAULT_UNIT" 2>/dev/null || true)"
  cmd="$(tr '\0' ' ' <"/proc/${pid:-0}/cmdline" 2>/dev/null || true)"
  [[ "$cmd" == *" $cipher $VAULT_MOUNT_DIR"* ]] || _vault_mech_abort "$VAULT_UNIT mounted a different cipher dir than intended ($cipher): gocryptfs cmdline '$cmd'"
  # Write and read back as atlas (the mount owner; root is denied by the FUSE kernel driver, which is the design), under
  # the self-test directory only (Section 16.3 rule 5: the Principal's vault is otherwise never written by automation).
  VAULT_MECH_FILE="$VAULT_MOUNT_DIR/$name"
  runuser -u atlas -- mkdir -p "$VAULT_MOUNT_DIR/$VAULT_SELFTEST_SUBDIR" || _vault_mech_abort "could not create $VAULT_MOUNT_DIR/$VAULT_SELFTEST_SUBDIR as atlas"
  # shellcheck disable=SC2016  # $1/$2 are expanded by the inner bash, on purpose (content never touches argv parsing here)
  runuser -u atlas -- bash -c 'printf "%s\n" "$1" >"$2"' _ "$content" "$VAULT_MECH_FILE" \
    || _vault_mech_abort "could not write $VAULT_MECH_FILE as atlas"
  local back
  back="$(runuser -u atlas -- cat "$VAULT_MECH_FILE" 2>/dev/null || true)"
  [[ "$back" == "$content" ]] || _vault_mech_abort "read-back mismatch in the open vault"
  # Ciphertext only on disk: neither the plaintext name nor the plaintext content may appear in the cipher dir.
  [[ ! -e "$cipher/$name" && ! -e "$cipher/$VAULT_SELFTEST_SUBDIR" ]] || _vault_mech_abort "the plaintext file name appears in the cipher dir (names are not encrypted?)"
  if grep -rqF -- "$content" "$cipher"; then
    _vault_mech_abort "the plaintext content appears in $cipher (content not encrypted?)"
  fi
  local nfiles
  nfiles="$(find "$cipher" -type f | wc -l)"
  (( nfiles >= 3 )) || _vault_mech_abort "expected gocryptfs.conf, gocryptfs.diriv and one encrypted file in $cipher, found $nfiles files"
  runuser -u atlas -- rm -f "$VAULT_MECH_FILE" || _vault_mech_abort "could not remove $VAULT_MECH_FILE as atlas"
  runuser -u atlas -- rmdir "$VAULT_MOUNT_DIR/$VAULT_SELFTEST_SUBDIR" 2>/dev/null || true   # kept only if v18 litter is inside
  VAULT_MECH_FILE=""
  st="$(runuser -u atlas -- sudo -n "$VAULT_HELPER" lock 2>&1)" || _vault_mech_abort "'sudo -n $VAULT_HELPER lock' as atlas failed: $st"
  if vault_is_mounted "$VAULT_MOUNT_DIR"; then _vault_mech_abort "vault still mounted after lock"; fi
  if systemctl is-active --quiet "$VAULT_UNIT"; then _vault_mech_abort "$VAULT_UNIT still active after lock"; fi
  [[ -z "$(ls -A "$VAULT_MOUNT_DIR")" ]] || die "$VAULT_MOUNT_DIR is not empty while locked (something wrote into the mount point)"
  log "mechanics on $cipher: open (atlas -> sudo-rs -> atlas-vault -> $VAULT_UNIT), write+read as atlas under $VAULT_SELFTEST_SUBDIR/, ciphertext-only on disk ($nfiles files), lock: all confirmed"
}

_vault_restart_orchestrator() {
  # The service reads VAULT_* from orchestrator.env at start; a running orchestrator needs one restart to see a change.
  systemctl is-active --quiet atlas-orchestrator || { log "atlas-orchestrator not running; nothing to restart"; return 0; }
  if [[ "${VAULT_ORCH_ENV_CHANGED:-0}" != 1 ]]; then
    log "orchestrator.env unchanged; atlas-orchestrator not restarted"
    return 0
  fi
  local port="${ORCH_PORT:-8800}"
  systemctl restart atlas-orchestrator || die "systemctl restart atlas-orchestrator failed"
  wait_http "http://127.0.0.1:$port/health" 180 || die "the orchestrator did not answer 200 on /health within 180 s after the restart (journalctl -u atlas-orchestrator)"
  log "atlas-orchestrator restarted with the VAULT_* settings; /health 200"
}

step_09b() {
  _vault_lock_if_open
  _vault_apt
  _vault_dirs
  _vault_env
  _vault_helper
  _vault_unit
  _vault_sudoers
  _vault_restart_orchestrator
  _vault_init_test
  VAULT_PASS=""
  local init_cmd="sudo env ATLAS_VAULT_INIT=1 ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  if _vault_init_requested; then
    # The opt-in (header part 4): the Principal asked for the real vault now. An opt-in that cannot be honoured dies.
    _vault_has_tty || die "ATLAS_VAULT_INIT=1 but no terminal is available to type the vault passphrase; run from a console: $init_cmd"
    _vault_prompt
    _vault_init_real
    rm -f "$VAULT_PENDING_FLAG"
    _vault_mechanics "$VAULT_CIPHER_DIR" real
    # V18 on the real vault with the real passphrase (the test itself opens through POST /vault/open with a 20 s idle).
    # Real mode is EXPLICIT (V18_REAL_VAULT=1; v18 ignores stdin without it, fix round 4). Recorded, not fatal: the gate
    # blocks on a fail. The gate keeps this pass; otherwise it re-runs V18 on the test vault.
    if ! printf '%s\n' "$VAULT_PASS" | V18_REAL_VAULT=1 run_verify V18 v18-vault.sh "$VAULT_CIPHER_DIR"; then
      warn "V18 recorded as fail (see verify.jsonl); the Phase 2 gate will block until it passes"
    fi
  elif [[ -f "$VAULT_CIPHER_DIR/gocryptfs.conf" ]]; then
    log "unattended (default): the real vault is already initialised; its passphrase was not asked for. Mechanics proven on the test vault; the gate runs V18 on it. To re-prove the real vault: $init_cmd"
    rm -f "$VAULT_PENDING_FLAG"
    _vault_mechanics "$VAULT_TEST_CIPHER_DIR" "$VAULT_TEST_PASS_FILE"
  else
    # CONVENTIONS §7.6: this phase has exactly three interactive pauses and this step is not one of them, so the default
    # run never prompts. Deferred loudly, never silently, never fatal: the button answers "not initialised" (exit 2)
    # until the Principal runs the opt-in command from a console.
    date -Is >"$VAULT_PENDING_FLAG"
    warn "UNATTENDED (default): the real vault at $VAULT_CIPHER_DIR is NOT initialised (its passphrase must be typed on a console). Deferred; flag $VAULT_PENDING_FLAG. From a console: $init_cmd"
    notify "Phase 2 step 9b: real vault initialisation deferred (opt-in). From a console: $init_cmd"
    _vault_mechanics "$VAULT_TEST_CIPHER_DIR" "$VAULT_TEST_PASS_FILE"
  fi
  VAULT_PASS=""
  unset VAULT_PASS
  log "step 09b done: vault at $VAULT_CIPHER_DIR (mount $VAULT_MOUNT_DIR, idle $VAULT_IDLE), helper $VAULT_HELPER, unit $VAULT_UNIT, sudoers $VAULT_SUDOERS, runtime $VAULT_RUN_DIR"
  if [[ -f "$VAULT_PENDING_FLAG" ]]; then
    notify "Phase 2 step 9b done on the test vault only; real vault initialisation pending (console needed)"
  else
    notify "Phase 2 step 9b done: vault initialised and proven (V18 recorded when the passphrase was typed)"
  fi
}
