#!/usr/bin/env bash
# phase1/02-luks.sh — Phase 1 step 2 (Sections 3.5, 17, 21 V2; D3, R16): LUKS2 on the data volume, TPM2 enrolment
# bound to PCR 7 (adjudicated conflict 1: systemd 259's default PCR mask is empty), recovery key printed ONCE, the
# one interactive pause ("WRITTEN DOWN"), crypttab, initramfs (dracut on 26.04). Also enrols TPM2 on the
# installer-made OS volume when it is LUKS.
#
# THE ONE PAUSE (rule §7.6): a single framed console block prints the recovery key, asks (only when the installer
# encrypted the OS volume and it has no TPM2 token yet) for the OS LUKS passphrase ONCE (Section 3.5 wants the OS
# volume TPM-unlocked too; the passphrase is used for the enrolment only and never stored; this second input inside
# the same pause is declared in the phase1-platform.sh header), and waits for WRITTEN DOWN. The screen AND the
# terminal scrollback are cleared afterwards (ESC[3J), so the key does not linger in the SSH client's history. The
# Principal is at the console exactly once. Everything else in this step runs unattended.
#
# WHAT STAYS ON THE NODE (D3, R16): the recovery key's on-node convenience copy ($ATLAS_LUKS_RECOVERY, root 600),
# always, because D3 is a closed decision ("on the node and on an external USB drive"; R16 "the on-node copy is
# convenience only") and the OS volume it sits on is normally LUKS2 itself (Section 3.5). Policy v0.3.3: an
# unencrypted OS volume (the installer's encrypted-LVM box not ticked) is the Principal's install choice, not broken
# machinery, so step 1 records the to-do os-volume-encryption and this step still encrypts the data volume and
# continues; the on-node recovery copy then sits on an unencrypted disk until the reinstall, which the to-do says, and
# V2 is recorded deferred, not pass (verify/v02-tpm.sh). The generated keyfile is NOT kept: it authorises the
# enrolments and is then wiped from the LUKS header and shredded, because a second full-strength unlock secret on the
# OS drive is nothing the TPM path needs (re-enrolment after a firmware change authorises with
# --unlock-tpm2-device=auto, UNVERIFIED flag from systemd 256, or with the recovery key; step 3's fallback open uses
# the recovery copy). So the ways into the 8 TB volume are: the TPM at boot, the recovery key (USB + on-node copy).
#
# PCR 7 AND SECURE BOOT: with Secure Boot off, PCR 7 carries no image authority and the TPM unseals to any OS booted
# on this hardware. D15 (2026-10-05, supersedes D2): the Principal enables Secure Boot in the BIOS; step 1 records a
# to-do while it is off and never stops (policy v0.3.3); verify/v02-tpm.sh names the Secure Boot state in every V2
# row. The mask stays --tpm2-pcrs=7 (S9, adjudicated conflict 1); a stronger mask needs a Section 23 amendment.
#
# No package is installed here: cryptsetup, systemd-cryptsetup and dracut are seeded on the 26.04 Server ISO
# (VERIFIED: the installer itself uses them for the encrypted OS volume). Their absence stops the step (step 4 brings
# the proxy; rule §7.1). systemd-cryptenroll speaks to the TPM directly, but the INITRAMFS needs tpm2-tools: dracut's
# tpm2-tss module (dracut 110 on 26.04.1, modules.d/73tpm2-tss) has `check() { require_binaries tpm2 || return 1; }`
# (VERIFIED from dracut-ng main), tpm2-tools (universe, ships /usr/bin/tpm2) is NOT on the 26.04.1 server image
# (VERIFIED from its manifest), and a module named in add_dracutmodules that fails its check stops dracut ("Module
# 'X' cannot be installed." then exit 1 in for_each_module_dir, VERIFIED from dracut-ng dracut.sh). So the
# drop-in names tpm2-tss, and crypttab carries tpm2-device=, only once `tpm2` exists (in dracut's default hostonly mode
# a tpm2-device= in crypttab alone pulls tpm2-tss in, review v0.3.4): phase1/04-system.sh installs tpm2-tools through
# the proxy BEFORE its dist-upgrade, adds the crypttab option, writes the drop-in (a new kernel's initramfs then
# already carries TPM support) and rebuilds every initramfs before the reboot, with the helpers below. On the first
# run this step therefore leaves the initramfs to step 4.
# Facts typed literally from the platform research item 1 (VERIFIED unless marked). Defines step_02 and the two
# phase1_initramfs_* helpers step 4 calls (step files are sourced in order before each step runs).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# Names shared with steps 3 and 5 (contract inside Phase 1; nothing in Phase 2 depends on them).
ATLAS_LUKS_MAPPING="atlas-data"
ATLAS_LUKS_KEYFILE="$ATLAS_ETC/secrets/luks-data.key"
ATLAS_LUKS_RECOVERY="$ATLAS_ETC/secrets/luks-data.recovery"
ATLAS_LUKS_CONFIRMED="$ATLAS_STATE/luks-data.recovery-confirmed"

# _luks_has_token DEVICE TOKEN_NAME — does the LUKS2 header carry a systemd-<name> token? The dump is read in full
# first (fix round 3): `luksDump | grep -q` under pipefail can report failure through grep's early exit (SIGPIPE on the
# writer), and a false negative here would re-enrol a second TPM2 token or wipe and reprint the recovery key.
_luks_has_token() {
  local d
  d="$(cryptsetup luksDump "$1" 2>/dev/null)" || return 1
  [[ "$d" == *"systemd-$2"* ]]
}

# _luks_os_mapping — the crypt mapping under "/" (empty = "/" has no crypt layer). List mode (-l) is essential:
# lsblk draws tree prefixes ("└─", "`-") into pipes whenever NAME is shown, and the encrypted-LVM install puts "/" on
# an LV above the crypt mapping, so tree mode yields "└─dm_crypt-0" (review v0.3.4, reproduced). -s lists the
# dependencies of SOURCE in order, so the first crypt line is the mapping that carries "/".
_luks_os_mapping() {
  local src; src="$(findmnt -n -o SOURCE /)"
  lsblk -lnso NAME,TYPE "$src" 2>/dev/null | awk '$2=="crypt" {print $1; exit}' || true
}

# _luks_os_device — the LUKS device backing "/": prints nothing when "/" has no crypt layer; returns 1 with a message
# on stderr when a crypt layer exists but its device cannot be resolved, so a caller never takes "encrypted" for
# "unencrypted" (the unencrypted branch records a reinstall to-do and skips the OS enrolment).
_luks_os_device() {
  local map dev
  map="$(_luks_os_mapping)"
  [[ -n "$map" ]] || return 0
  dev="$(cryptsetup status "$map" 2>/dev/null | awk '$1=="device:" {print $2; exit}')" || true
  [[ -b "$dev" ]] || { echo "\"/\" is on the crypt mapping '$map' but 'cryptsetup status $map' names no backing device" >&2; return 1; }
  printf '%s\n' "$dev"
}

# _luks_enroll DEVICE ARGS... — run systemd-cryptenroll ARGS DEVICE with whatever authorises a header change: the
# keyfile while it exists; else the TPM (--unlock-tpm2-device=auto, UNVERIFIED: added in systemd 256 per the release
# notes and expected on 259); else the recovery key ($PASSWORD is systemd-cryptenroll's documented passphrase source)
# from the in-memory copy of this run or the on-node copy (D3). Dies with the exact way out when none works.
_luks_enroll() {
  local dev="$1"; shift
  if [[ -s "$ATLAS_LUKS_KEYFILE" ]]; then
    systemd-cryptenroll "--unlock-key-file=$ATLAS_LUKS_KEYFILE" "$@" "$dev"; return
  fi
  if systemd-cryptenroll --unlock-tpm2-device=auto "$@" "$dev" 2>/dev/null; then return 0; fi
  local rec="${LUKS_RECOVERY_INMEM:-}"
  [[ -n "$rec" || ! -s "$ATLAS_LUKS_RECOVERY" ]] || rec="$(tr -d '[:space:]' <"$ATLAS_LUKS_RECOVERY")"
  # The by-hand instruction keeps the key out of the shell history (rule §7.2): a leading `PASSWORD=... cmd` would be
  # written to ~/.bash_history in clear; `read -rs` into an exported variable is not.
  [[ -n "$rec" ]] || die "systemd-cryptenroll $* $dev: the keyfile is gone, --unlock-tpm2-device=auto was refused (systemd $(systemctl --version | head -n1)) and no recovery copy is on the node. Run it by hand with the USB recovery key, NEVER on the command line (the shell history would keep it):  read -rs PASSWORD; export PASSWORD; systemd-cryptenroll $* $dev; unset PASSWORD   then re-run: sudo $ATLAS_ENTRY phase1 --force 02"
  PASSWORD="$rec" systemd-cryptenroll "$@" "$dev"
}

# _luks_need_tty — the pause needs a real terminal. access(2) on /dev/tty is true even without a controlling
# terminal (only the 0666 mode bits are checked), so the device is opened for real: under systemd-run/nohup that
# fails and the step stops with this message instead of a generic ERR-trap line (rule §7.6: stop, never hang or skip).
_luks_need_tty() {
  ( : </dev/tty ) 2>/dev/null || die "step 2 needs a terminal for its one console pause (recovery key / OS passphrase); run it from the console or an interactive SSH session, never detached"
}

# _luks_tty_read PROMPT VAR [silent] — read from the controlling terminal.
_luks_tty_read() {
  local prompt="$1" var="$2" silent="${3:-}"
  _luks_need_tty
  if [[ -n "$silent" ]]; then
    read -r -s -p "$prompt" "${var?}" </dev/tty >/dev/tty; echo >/dev/tty
  else
    read -r -p "$prompt" "${var?}" </dev/tty >/dev/tty
  fi
}

# phase1_initramfs_conf — write the dracut drop-in. tpm2-tss is named only when the `tpm2` binary exists (header): a
# drop-in naming a module whose check fails would break every later initramfs build, kernel updates included.
phase1_initramfs_conf() {
  local mods="crypt systemd-cryptsetup"
  command -v tpm2 >/dev/null 2>&1 && mods+=" tpm2-tss"
  install -d -m 755 /etc/dracut.conf.d
  printf '# ATLAS Phase 1 (steps 2 and 4): TPM2 unlock of the OS volume in the initrd (Section 3.5).\nadd_dracutmodules+=" %s "\n' \
    "$mods" >/etc/dracut.conf.d/90-atlas-tpm2.conf
}

# phase1_crypttab_tpm2 — add tpm2-device=auto to the crypttab lines of the data volume and (when it carries a TPM2
# token) the OS volume. Until tpm2-tools exists the lines are written WITHOUT it: in dracut's default hostonly mode the
# systemd-cryptsetup module pulls in tpm2-tss as soon as /etc/crypttab mentions tpm2-device= (71systemd-cryptsetup
# depends()), tpm2-tss's check fails without the tpm2 binary, and every initramfs build stops, including the kernel
# postinst of an unattended upgrade between step 2 and step 4 (review v0.3.4). Step 4 calls this right after
# installing tpm2-tools; step 2 writes the option directly when tpm2-tools is already there. Idempotent.
phase1_crypttab_tpm2() {
  [[ -f /etc/crypttab ]] || return 0
  local names=("$ATLAS_LUKS_MAPPING") osdev osmap
  osdev="$(_luks_os_device)" || die "cannot resolve the LUKS device under / (see the message above); not touching crypttab"
  osmap="$(_luks_os_mapping)"
  if [[ -n "$osdev" && -n "$osmap" ]] && _luks_has_token "$osdev" tpm2; then names+=("$osmap"); fi
  awk -v list="${names[*]}" 'BEGIN { n = split(list, a, " "); for (i = 1; i <= n; i++) want[a[i]] = 1 }
    $0 !~ /^[[:space:]]*#/ && ($1 in want) && NF >= 3 {
      opts = (NF >= 4) ? $4 : "luks"
      if (opts !~ /(^|,)tpm2-device=/) opts = opts ",tpm2-device=auto"
      print $1, $2, $3, opts; next }
    { print }' /etc/crypttab >/etc/crypttab.atlas.tmp
  cat /etc/crypttab.atlas.tmp >/etc/crypttab && rm -f /etc/crypttab.atlas.tmp
  log "crypttab: tpm2-device=auto on ${names[*]}"
}

# phase1_initramfs_rebuild — drop-in, then regenerate every initramfs, then check each image for the TPM pieces.
# Called by step 2 when tpm2-tools is already present (a --force 02 after step 4) and by step 4 before its reboot.
# Sets PHASE1_INITRD_NOTE and persists it in $ATLAS_STATE/initrd-tpm2.note, which step 5 puts into the post-reboot
# V2 row (the reboot comes before anyone reads this step's log).
PHASE1_INITRD_NOTE=""
phase1_initramfs_rebuild() {
  PHASE1_INITRD_NOTE=""
  command -v dracut >/dev/null || die "dracut is not installed; the 26.04 Server ISO seeds it (apt-get install dracut, then re-run)"
  command -v tpm2 >/dev/null 2>&1 || die "tpm2-tools is not installed; dracut's tpm2-tss module needs its tpm2 binary (phase1/04-system.sh installs it through the proxy)"
  phase1_initramfs_conf
  log "regenerating every initramfs with dracut (crypt, systemd-cryptsetup, tpm2-tss; this takes a minute or two)"
  dracut --force --regenerate-all --quiet || die "dracut --force --regenerate-all failed; see the output above and /etc/dracut.conf.d/90-atlas-tpm2.conf"
  local osd; osd="$(_luks_os_device)" || die "cannot resolve the LUKS device under / (see the message above)"
  if [[ -n "$osd" ]] && command -v lsinitrd >/dev/null; then
    # Capture first, match second: `lsinitrd | grep -q` under pipefail fails exactly when grep matches early
    # (lsinitrd gets SIGPIPE). Every image is checked, not only the running kernel's: GRUB boots the newest.
    local img out missing=()
    for img in /boot/initrd.img-*; do
      [[ -f "$img" ]] || continue
      out="$(lsinitrd "$img" 2>/dev/null || true)"
      grep -qE 'libcryptsetup-token-systemd-tpm2|libtss2-esys' <<<"$out" || missing+=("${img##*/}")
    done
    if (( ${#missing[@]} > 0 )); then
      warn "initrd without the systemd TPM2 token or libtss2: ${missing[*]}; the OS volume may ask for its passphrase at the console after the reboot (keyboard at hand for Phase 1). Check: lsinitrd /boot/<image> | grep -iE 'tpm2|tss2'"
      PHASE1_INITRD_NOTE="initrd TPM2 support UNVERIFIED (${missing[*]})"
    else
      PHASE1_INITRD_NOTE="initrd TPM2 support checked in every image"
    fi
  fi
  _atlas_state_init
  printf '%s\n' "$PHASE1_INITRD_NOTE" >"$ATLAS_STATE/initrd-tpm2.note"
}

step_02() {
  local t missing=()
  for t in cryptsetup systemd-cryptenroll systemd-cryptsetup dracut; do command -v "$t" >/dev/null || missing+=("$t"); done
  (( ${#missing[@]} == 0 )) || die "missing on this host: ${missing[*]} (expected on the 26.04 Server ISO: cryptsetup, systemd-cryptsetup, dracut). Install them from the console (apt-get install cryptsetup systemd-cryptsetup dracut) and re-run; step 2 installs nothing because the allowlist proxy does not exist before step 4 (rule §7.1)"
  # root:atlas 750 once the atlas group exists (step 3 creates it; root:root 700 until then). ONE value across every
  # writer (fix round 3): phase2-services.sh, phase2/02,03,06c,07,08,09 assert root:atlas 750 and asked phase1/02,03,07
  # to match (phase2/02-orchestrator.sh header). CONVENTIONS §2's row still says root:root 700, which cannot hold
  # together with its atlas:atlas 600 files inside; that row needs the amendment to root:atlas 750 (fix-round notes).
  if getent group atlas >/dev/null 2>&1; then ensure_dir "$ATLAS_ETC/secrets" root:atlas 710
  else ensure_dir "$ATLAS_ETC/secrets" root:root 700; fi

  local dev; dev="$(readlink -f "$DATA_DISK")"
  [[ -b "$dev" ]] || die "DATA_DISK $DATA_DISK does not resolve to a block device"
  [[ ! -e /etc/systemd/tpm2-pcr-public-key.pem ]] || die "/etc/systemd/tpm2-pcr-public-key.pem exists (see step 1)"

  # --- OS volume state, decided once. Unencrypted: the data volume is still encrypted and the phase continues; the
  # to-do os-volume-encryption (step 1) stands and V2 is recorded deferred (policy v0.3.3, header). ---------------
  local osdev osmap os_note="" need_os_enrol=0 os_reseal=0 reseal=0
  # --force 02 re-seals both TPM2 tokens to the current PCR 7 (the secure-boot to-do's remedy: switching Secure Boot
  # on changes PCR 7, so the old tokens no longer unseal). systemd-cryptenroll enrols the new token first and wipes the
  # old tpm2 slots with the new one excluded (VERIFIED in cryptenroll.c: wipe_slots(..., except_slot=slot)).
  [[ " ${ATLAS_FORCED_STEPS:-} " == *" 02 "* ]] && reseal=1
  osdev="$(_luks_os_device)" || die "cannot resolve the LUKS device under / (see the message above). This is not an unencrypted install; check: findmnt /; lsblk -s \"\$(findmnt -n -o SOURCE /)\""
  osmap="$(_luks_os_mapping)"
  if [[ -z "$osdev" ]]; then
    os_note="OS volume NOT encrypted (to-do os-volume-encryption: reinstall with the encrypted-LVM option)"
    warn "$os_note; Section 3.5 wants LUKS2 on it. The 8 TB data volume is encrypted now regardless, and the phase continues"
    todo_add os-volume-encryption "The OS volume is not encrypted: reinstall Ubuntu with the encrypted-LVM option (Section 3.5), then run Phase 1 again" \
      "Until then NO copy of the data volume's recovery key is kept on the node (it would sit on an unencrypted disk beside the volume it opens): the USB copy is the only one, keep it safe (D3, R16). V2 stays deferred."
  elif _luks_has_token "$osdev" tpm2 && (( ! reseal )); then
    os_note="LUKS $osmap on $osdev, TPM2 already enrolled"
    log "OS volume: $os_note"
  elif _luks_has_token "$osdev" tpm2; then
    need_os_enrol=1; os_reseal=1
    log "OS volume: LUKS $osmap on $osdev: --force 02 re-seals its TPM2 token to the current PCR 7 (the installer passphrase is asked once, in the pause below)"
  else
    need_os_enrol=1
    log "OS volume: LUKS $osmap on $osdev without a TPM2 token: will enrol (the installer passphrase is asked once, in the pause below)"
  fi

  # --- Keyfile (task: generated keyfile in $ATLAS_ETC/secrets). It authorises the enrolments of this run and exists
  # only until they are done (header): crypttab never references it (the TPM unlocks at boot). -------------------
  local sig label=""
  sig="$(blkid -p -o value -s TYPE "$dev" 2>/dev/null || true)"
  [[ "$sig" == crypto_LUKS ]] && label="$(cryptsetup luksDump "$dev" 2>/dev/null | awk -F: '/^Label:/ {gsub(/^[ \t]+/,"",$2); print $2; exit}')"
  local keyfile_wiped=0
  if [[ "$sig" == crypto_LUKS && "$label" == atlas-data && ! -s "$ATLAS_LUKS_KEYFILE" ]] && _luks_has_token "$dev" tpm2; then
    keyfile_wiped=1          # earlier run of this step already wiped the keyfile slot; the TPM (or recovery key) authorises changes
    log "keyfile absent by design (wiped after enrolment); TPM2 or the recovery key authorises any re-enrolment"
  elif [[ ! -s "$ATLAS_LUKS_KEYFILE" ]]; then
    ( umask 077; head -c 64 /dev/urandom >"$ATLAS_LUKS_KEYFILE" )
    log "generated LUKS keyfile $ATLAS_LUKS_KEYFILE (root, 600)"
  fi
  if [[ -s "$ATLAS_LUKS_KEYFILE" ]]; then chmod 600 "$ATLAS_LUKS_KEYFILE"; chown root:root "$ATLAS_LUKS_KEYFILE"; fi

  # --- Format: refuse anything that is not blank or our own earlier format (label check) -----------------------
  if [[ -z "$sig" ]] && (( $(lsblk -n -o NAME "$dev" | wc -l) == 1 )); then
    log "luksFormat (LUKS2, sector 4096, label atlas-data) on $dev"
    cryptsetup luksFormat --type luks2 --batch-mode --sector-size 4096 --label atlas-data --key-file "$ATLAS_LUKS_KEYFILE" "$dev"
    udevadm settle --timeout=30 || true       # /dev/disk/by-uuid/<uuid> is created asynchronously from the change event
  elif [[ "$sig" == crypto_LUKS && "$label" == atlas-data ]]; then
    log "$dev is already our atlas-data LUKS2 volume; not formatting"
    if (( ! keyfile_wiped )); then
      cryptsetup open --test-passphrase --key-file "$ATLAS_LUKS_KEYFILE" "$dev" \
        || die "$dev is labelled atlas-data but $ATLAS_LUKS_KEYFILE does not unlock it (keyfile changed?). Restore the keyfile or wipe the volume deliberately, then re-run."
    fi
  else
    die "refusing to format $dev: signature '${sig:-partitions present}' is not blank and not our atlas-data volume"
  fi
  local uuid; uuid="$(cryptsetup luksUUID "$dev")"
  [[ -e "/dev/disk/by-uuid/$uuid" ]] || { udevadm settle --timeout=30 || true; }
  [[ -e "/dev/disk/by-uuid/$uuid" ]] || die "/dev/disk/by-uuid/$uuid did not appear (udevadm settle; udevadm trigger --subsystem-match=block)"

  # --- TPM2 enrolment (PCR 7) and recovery key --------------------------------------------------------------------
  if _luks_has_token "$dev" tpm2 && (( reseal )); then
    log "re-sealing the TPM2 token on $dev to the current PCR 7 (--force 02)"
    _luks_enroll "$dev" --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 \
      || die "re-sealing the TPM2 token on $dev failed (V2). The old token is still there; authorise with the USB recovery key as the message above says, then re-run --force 02"
  elif _luks_has_token "$dev" tpm2; then
    log "TPM2 token already enrolled on $dev"
  else
    log "enrolling TPM2 (PCR 7) on $dev"
    _luks_enroll "$dev" --tpm2-device=auto --tpm2-pcrs=7 \
      || die "systemd-cryptenroll --tpm2-device=auto failed on $dev (V2). Check: fTPM enabled, exactly one TPM (systemd-cryptenroll --tpm2-device=list)"
  fi
  # D3's on-node convenience copy presumes an encrypted OS volume. On an unencrypted one it is NOT written (and an
  # earlier copy is shredded): a plaintext key beside the volume it opens would void the data volume's encryption
  # against theft (review v0.3.4); the USB copy is then the only one, which the pause and the to-do say.
  local keep_copy=1
  [[ -n "$osdev" ]] || keep_copy=0
  if (( ! keep_copy )) && [[ -e "$ATLAS_LUKS_RECOVERY" ]]; then
    shred -u "$ATLAS_LUKS_RECOVERY"
    warn "shredded the on-node recovery copy: the OS volume is not encrypted (to-do os-volume-encryption); the USB copy is the only one"
  fi
  local recovery="" show_key=0
  if _luks_has_token "$dev" recovery && [[ -e "$ATLAS_LUKS_CONFIRMED" ]] && { (( ! keep_copy )) || [[ -s "$ATLAS_LUKS_RECOVERY" ]]; }; then
    log "recovery key already enrolled and confirmed as written down on $(cat "$ATLAS_LUKS_CONFIRMED")"
  else
    if _luks_has_token "$dev" recovery; then
      warn "a recovery token exists but its confirmation (or the on-node copy) is missing: wiping it and enrolling a fresh one"
      _luks_enroll "$dev" --wipe-slot=recovery
    fi
    recovery="$(_luks_enroll "$dev" --recovery-key 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$recovery" ]] || die "systemd-cryptenroll --recovery-key printed nothing"
    LUKS_RECOVERY_INMEM="$recovery"       # authorises the keyfile wipe below on this run; cleared at the end of the step
    # D3: the on-node convenience copy (root 600) on the LUKS2 OS volume only; the USB copy is the one that matters (R16).
    if (( keep_copy )); then
      ( umask 077; printf '%s\n' "$recovery" >"$ATLAS_LUKS_RECOVERY" )
      log "recovery key enrolled; on-node copy at $ATLAS_LUKS_RECOVERY (root, 600; D3 convenience copy)"
    else
      log "recovery key enrolled; no on-node copy (the OS volume is not encrypted): the USB copy is the only one"
    fi
    rm -f "$ATLAS_LUKS_CONFIRMED"
    show_key=1
  fi

  # --- THE ONE PAUSE: recovery key printed ONCE (framed), OS passphrase asked in the same block if needed, then
  # WRITTEN DOWN (D3, R16). Skipped entirely when nothing is left to show or ask. ---------------------------------
  local pw=""
  if (( show_key || need_os_enrol )); then
    _luks_need_tty
    local answer="" line
    line="$(printf '#%.0s' $(seq 1 78))"
    {
      echo; echo "$line"; echo "#"
      if (( show_key )); then
        echo "#   LUKS RECOVERY KEY for the 8 TB data volume (label atlas-data, UUID $uuid)"
        echo "#   Printed ONCE. Write it down now and copy it to the external USB drive that lives"
        echo "#   AWAY from the node (D3, R16). It unlocks the volume if the TPM ever refuses."
        echo "#"
        echo "#       $recovery"
        echo "#"
        if (( keep_copy )); then
          echo "#   On-node convenience copy (root only, D3): $ATLAS_LUKS_RECOVERY"
          echo "#   The USB copy is the one that matters (R16): a fire or burglary that takes the node takes this copy too."
        else
          echo "#   NO copy is kept on the node: its OS disk is not encrypted (to-do os-volume-encryption)."
          echo "#   Your written copy and the USB drive are the ONLY copies (R16). Without them a TPM change locks the data."
        fi
        echo "#   The screen and this terminal's scrollback are erased once you confirm; the key is never logged."
        echo "#"
      fi
      if (( need_os_enrol )); then
        echo "#   The OS volume ($osmap) is encrypted with the passphrase you typed in the Ubuntu installer."
        (( os_reseal )) && echo "#   (--force 02: its TPM2 token is re-sealed to the current firmware state, e.g. Secure Boot now on.)"
        echo "#   You will be asked for it ONCE below so the TPM can unlock the OS at boot without a keyboard"
        echo "#   (it is used for the enrolment only and never stored)."
        echo "#"
      fi
      echo "$line"; echo
    } >/dev/tty 2>/dev/null || die "cannot print the recovery key: no terminal"
    if (( need_os_enrol )); then
      while [[ -z "$pw" ]]; do _luks_tty_read "  LUKS passphrase for $osmap: " pw silent; done
    fi
    if (( show_key )); then
      while [[ "$answer" != "WRITTEN DOWN" ]]; do
        _luks_tty_read 'Type exactly  WRITTEN DOWN  to continue: ' answer
      done
      date -Is >"$ATLAS_LUKS_CONFIRMED"
    fi
    # ESC[2J clears the screen, ESC[3J the scrollback (xterm/VTE/Windows Terminal honour it; `clear` alone does not).
    printf '\033[2J\033[3J\033[H' >/dev/tty 2>/dev/null || true
    (( show_key )) && log "recovery key confirmed as written down (the key itself is never logged; screen and scrollback cleared)"
    recovery=""
  fi

  # --- OS volume enrolment with the passphrase from the pause ($PASSWORD is systemd-cryptenroll's documented
  # passphrase source; nothing touches disk or /dev/shm) ---------------------------------------------------------
  if (( need_os_enrol )); then
    local wipe=()
    (( os_reseal )) && wipe=(--wipe-slot=tpm2)
    if ! PASSWORD="$pw" systemd-cryptenroll "${wipe[@]}" --tpm2-device=auto --tpm2-pcrs=7 "$osdev"; then
      pw=""
      die "TPM2 enrolment on the OS volume failed (wrong passphrase?). Re-run: sudo $ATLAS_ENTRY phase1 --force 02 (the recovery key is not printed again)"
    fi
    pw=""
    # crypttab: key column -> none, add tpm2-device=auto (+ x-initrd.attach for the root device). UNVERIFIED: the
    # installer's exact line for 26.04.1 (curtin names the mapping after the storage id, e.g. dm_crypt-0); parsed,
    # never hard-coded. The passphrase slot stays as the OS recovery path.
    if grep -qE "^[[:space:]]*${osmap}[[:space:]]" /etc/crypttab; then
      local tpm=0; command -v tpm2 >/dev/null 2>&1 && tpm=1   # phase1_crypttab_tpm2 header: tpm2-device= waits for tpm2-tools
      awk -v m="$osmap" -v tpm="$tpm" 'BEGIN{OFS=" "} $1==m && $0 !~ /^#/ {
          opts=(NF>=4)?$4:"luks"; if (tpm && opts !~ /tpm2-device=/) opts=opts",tpm2-device=auto"; if (opts !~ /x-initrd.attach/) opts=opts",x-initrd.attach";
          print $1,$2,"none",opts; next } {print}' /etc/crypttab >/etc/crypttab.atlas.tmp
      cat /etc/crypttab.atlas.tmp >/etc/crypttab && rm -f /etc/crypttab.atlas.tmp
      log "crypttab: $osmap: $(grep -E "^[[:space:]]*${osmap}[[:space:]]" /etc/crypttab)$( (( tpm )) || echo ' (tpm2-device=auto is added by step 4 once tpm2-tools is installed)')"
    else
      die "no /etc/crypttab line for $osmap; cannot make the OS volume TPM-unlock at boot (add it by hand and re-run)"
    fi
    os_note="LUKS $osmap on $osdev, TPM2 $( (( os_reseal )) && echo re-sealed || echo enrolled ) now (passphrase slot kept as recovery)"
  fi

  # --- crypttab for the data volume (unlocked in the main system, nofail so boot never waits on it) ------------
  local ct_tpm=""; command -v tpm2 >/dev/null 2>&1 && ct_tpm="tpm2-device=auto,"   # phase1_crypttab_tpm2 header
  local ct_line="$ATLAS_LUKS_MAPPING UUID=$uuid none ${ct_tpm}nofail,headless=true,discard"
  if grep -qE "^[[:space:]]*${ATLAS_LUKS_MAPPING}[[:space:]]" /etc/crypttab 2>/dev/null; then
    sed -i -E "s|^[[:space:]]*${ATLAS_LUKS_MAPPING}[[:space:]].*\$|$ct_line|" /etc/crypttab
  else
    ensure_line /etc/crypttab "$ct_line"
  fi
  log "crypttab: $ct_line"

  # --- Prove the TPM unlocks it (V2 evidence): attach through the TPM, leave it attached for step 3 -------------
  if ! cryptsetup status "$ATLAS_LUKS_MAPPING" >/dev/null 2>&1; then
    systemd-cryptsetup attach "$ATLAS_LUKS_MAPPING" "/dev/disk/by-uuid/$uuid" none tpm2-device=auto \
      || { record_v V2 fail "TPM2 unlock test failed on $dev (systemd-cryptsetup attach with tpm2-device=auto)"; die "TPM2 unlock test failed (V2)"; }
    log "TPM2 unlock test passed: /dev/mapper/$ATLAS_LUKS_MAPPING is open"
  fi

  # --- The keyfile has done its job: wipe its slot and shred it (header). Authorisation for the wipe comes from the
  # TPM or the recovery key, never from the keyfile being wiped. ---------------------------------------------------
  if [[ -s "$ATLAS_LUKS_KEYFILE" ]]; then
    if ! { _luks_has_token "$dev" tpm2 && _luks_has_token "$dev" recovery; }; then
      die "refusing to wipe the keyfile slot: TPM2 and recovery tokens must both be enrolled first"
    fi
    local kf="$ATLAS_LUKS_KEYFILE"
    ATLAS_LUKS_KEYFILE=""                 # so _luks_enroll authorises with the TPM / recovery key, not the slot being wiped
    _luks_enroll "$dev" --wipe-slot=password \
      || { ATLAS_LUKS_KEYFILE="$kf"; die "systemd-cryptenroll --wipe-slot=password failed on $dev; the keyfile slot is still present"; }
    ATLAS_LUKS_KEYFILE="$kf"
    shred -u "$ATLAS_LUKS_KEYFILE"
    log "keyfile slot wiped and $ATLAS_LUKS_KEYFILE shredded; the TPM and the recovery key (USB + on-node copy, D3) are the only ways in"
  fi
  LUKS_RECOVERY_INMEM=""; recovery=""

  # --- initramfs (header): rebuilt here only when tpm2-tools is already installed (a --force 02 after step 4);
  # otherwise step 4 installs it through the proxy, adds tpm2-device= to crypttab and rebuilds before the reboot.
  # Until then neither the drop-in nor crypttab mentions TPM2 (phase1_crypttab_tpm2 header), so an initramfs build in
  # between (an unattended kernel update) still succeeds. ----------------------------------------------------------
  if command -v tpm2 >/dev/null 2>&1; then
    phase1_initramfs_rebuild
    [[ -z "$PHASE1_INITRD_NOTE" ]] || os_note+="${os_note:+; }$PHASE1_INITRD_NOTE"
  else
    phase1_initramfs_conf
    log "initramfs: left to step 4, which installs tpm2-tools through the proxy (dracut's tpm2-tss module needs it) and rebuilds before the reboot"
  fi
  systemctl daemon-reload

  # v02 exits 2 (deferred, run_verify returns 0) when the ONLY gap is the unencrypted OS volume; a real TPM2 failure
  # on either volume is exit 1 and stays fatal here.
  run_verify V2 v02-tpm.sh "$dev" "$ATLAS_LUKS_MAPPING" "${osdev:--}" "$os_note" \
    || die "V2 failed after enrolment; see the verify table"
  log "step 2 complete: data volume $dev is LUKS2 (uuid $uuid), TPM2-unlocked as /dev/mapper/$ATLAS_LUKS_MAPPING"
}
