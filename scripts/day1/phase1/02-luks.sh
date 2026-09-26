#!/usr/bin/env bash
# phase1/02-luks.sh — Phase 1 step 2 (Sections 3.5, 17, 21 V2; D3, R16): LUKS2 on the data volume, TPM2 enrolment
# bound to PCR 7 (adjudicated conflict 1: systemd 259's default PCR mask is empty), recovery key printed ONCE, the
# one interactive pause ("WRITTEN DOWN"), crypttab, initramfs (dracut on 26.04). Also enrols TPM2 on the
# installer-made OS volume when lsblk shows it is LUKS; when it is not, that is logged and recorded, not failed.
# Facts typed literally from the platform research item 1 (VERIFIED unless marked). Defines step_02 only.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# Names shared with steps 3 and 5 (contract inside Phase 1; nothing in Phase 2 depends on them).
ATLAS_LUKS_MAPPING="atlas-data"
ATLAS_LUKS_KEYFILE="$ATLAS_ETC/secrets/luks-data.key"
ATLAS_LUKS_RECOVERY="$ATLAS_ETC/secrets/luks-data.recovery"
ATLAS_LUKS_CONFIRMED="$ATLAS_STATE/luks-data.recovery-confirmed"

# _luks_has_token DEVICE TOKEN_NAME — does the LUKS2 header carry a systemd-<name> token?
_luks_has_token() { cryptsetup luksDump "$1" 2>/dev/null | grep -q "systemd-$2"; }

# _luks_os_device — print the LUKS device backing "/" (empty when the OS volume is not encrypted).
_luks_os_device() {
  local src crypt_name
  src="$(findmnt -n -o SOURCE /)"
  crypt_name="$(lsblk -sno NAME,TYPE "$src" 2>/dev/null | awk '$2=="crypt" {print $1; exit}')"
  [[ -n "$crypt_name" ]] || return 0
  cryptsetup status "$crypt_name" 2>/dev/null | awk '/device:/ {print $2; exit}'
}
_luks_os_mapping() {
  local src; src="$(findmnt -n -o SOURCE /)"
  lsblk -sno NAME,TYPE "$src" 2>/dev/null | awk '$2=="crypt" {print $1; exit}'
}

# _luks_tty_read PROMPT VAR [silent] — read from the controlling terminal; die if there is none (rule §7.6: the pause
# is explicit; an unattended run must stop here with a precise message rather than hang or skip).
_luks_tty_read() {
  local prompt="$1" var="$2" silent="${3:-}"
  [[ -r /dev/tty && -w /dev/tty ]] || die "step 2 needs a terminal for the recovery-key pause; run it from the console or an interactive SSH session"
  if [[ -n "$silent" ]]; then
    read -r -s -p "$prompt" "${var?}" </dev/tty >/dev/tty; echo >/dev/tty
  else
    read -r -p "$prompt" "${var?}" </dev/tty >/dev/tty
  fi
}

step_02() {
  apt_install cryptsetup systemd-cryptsetup tpm2-tools tpm-udev dracut
  ensure_dir "$ATLAS_ETC/secrets" root:root 700

  local dev; dev="$(readlink -f "$DATA_DISK")"
  [[ -b "$dev" ]] || die "DATA_DISK $DATA_DISK does not resolve to a block device"
  [[ ! -e /etc/systemd/tpm2-pcr-public-key.pem ]] || die "/etc/systemd/tpm2-pcr-public-key.pem exists (see step 1)"

  # --- Keyfile (task: generated keyfile in $ATLAS_ETC/secrets). It authorises enrolments now and re-enrolment after
  # a firmware change later; crypttab never references it (the TPM unlocks at boot). ---------------------------
  if [[ ! -s "$ATLAS_LUKS_KEYFILE" ]]; then
    ( umask 077; head -c 64 /dev/urandom >"$ATLAS_LUKS_KEYFILE" )
    log "generated LUKS keyfile $ATLAS_LUKS_KEYFILE (root, 600)"
  fi
  chmod 600 "$ATLAS_LUKS_KEYFILE"; chown root:root "$ATLAS_LUKS_KEYFILE"

  # --- Format: refuse anything that is not blank or our own earlier format (label check) -----------------------
  local sig label=""
  sig="$(blkid -p -o value -s TYPE "$dev" 2>/dev/null || true)"
  [[ "$sig" == crypto_LUKS ]] && label="$(cryptsetup luksDump "$dev" 2>/dev/null | awk -F: '/^Label:/ {gsub(/^[ \t]+/,"",$2); print $2; exit}')"
  if [[ -z "$sig" ]] && (( $(lsblk -n -o NAME "$dev" | wc -l) == 1 )); then
    log "luksFormat (LUKS2, sector 4096, label atlas-data) on $dev"
    cryptsetup luksFormat --type luks2 --batch-mode --sector-size 4096 --label atlas-data --key-file "$ATLAS_LUKS_KEYFILE" "$dev"
  elif [[ "$sig" == crypto_LUKS && "$label" == atlas-data ]]; then
    log "$dev is already our atlas-data LUKS2 volume; not formatting"
    cryptsetup open --test-passphrase --key-file "$ATLAS_LUKS_KEYFILE" "$dev" \
      || die "$dev is labelled atlas-data but $ATLAS_LUKS_KEYFILE does not unlock it (keyfile changed?). Restore the keyfile or wipe the volume deliberately, then re-run."
  else
    die "refusing to format $dev: signature '${sig:-partitions present}' is not blank and not our atlas-data volume"
  fi
  local uuid; uuid="$(cryptsetup luksUUID "$dev")"

  # --- TPM2 enrolment (PCR 7) and recovery key --------------------------------------------------------------------
  if _luks_has_token "$dev" tpm2; then
    log "TPM2 token already enrolled on $dev"
  else
    log "enrolling TPM2 (PCR 7) on $dev"
    systemd-cryptenroll --unlock-key-file="$ATLAS_LUKS_KEYFILE" --tpm2-device=auto --tpm2-pcrs=7 "$dev" \
      || die "systemd-cryptenroll --tpm2-device=auto failed on $dev (V2). Check: fTPM enabled, exactly one TPM (systemd-cryptenroll --tpm2-device=list)"
  fi
  local recovery=""
  if _luks_has_token "$dev" recovery && [[ -s "$ATLAS_LUKS_RECOVERY" ]]; then
    recovery="$(cat "$ATLAS_LUKS_RECOVERY")"
    log "recovery key already enrolled (on-node copy at $ATLAS_LUKS_RECOVERY, D3)"
  else
    if _luks_has_token "$dev" recovery; then
      warn "a recovery token exists but the on-node copy is missing: wiping it and enrolling a fresh one"
      systemd-cryptenroll --wipe-slot=recovery --unlock-key-file="$ATLAS_LUKS_KEYFILE" "$dev"
    fi
    recovery="$(systemd-cryptenroll --unlock-key-file="$ATLAS_LUKS_KEYFILE" --recovery-key "$dev" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$recovery" ]] || die "systemd-cryptenroll --recovery-key printed nothing"
    ( umask 077; printf '%s\n' "$recovery" >"$ATLAS_LUKS_RECOVERY" )
    rm -f "$ATLAS_LUKS_CONFIRMED"
    log "recovery key enrolled; on-node copy at $ATLAS_LUKS_RECOVERY (root, 600; D3 convenience copy)"
  fi

  # --- OS volume: enrol TPM2 if the installer encrypted it; otherwise log and record, do not fail --------------
  local osdev osmap os_note
  osdev="$(_luks_os_device)"; osmap="$(_luks_os_mapping)"
  if [[ -z "$osdev" ]]; then
    os_note="UNENCRYPTED (installer choice; Section 3.5 assumed LUKS; the LUKS keyfile and recovery copy therefore sit in clear on the OS drive: keep the USB copy authoritative, R16)"
    warn "OS volume: $os_note"
  elif _luks_has_token "$osdev" tpm2; then
    os_note="LUKS $osmap on $osdev, TPM2 already enrolled"
    log "OS volume: $os_note"
  else
    log "OS volume: LUKS $osmap on $osdev without a TPM2 token: enrolling (needs the installer passphrase once)"
    echo
    echo "  The OS volume ($osmap) is encrypted with the passphrase you typed in the Ubuntu installer."
    echo "  Type it once so the TPM can unlock the OS at boot without a keyboard (it is not stored)."
    local pw="" pwfile=/dev/shm/atlas-os.pw
    _luks_tty_read "  LUKS passphrase for $osmap: " pw silent
    [[ -n "$pw" ]] || die "empty passphrase; re-run step 2 (sudo $ATLAS_ENTRY phase1 --force 02)"
    ( umask 077; printf '%s' "$pw" >"$pwfile" ); pw=""
    if ! systemd-cryptenroll --unlock-key-file="$pwfile" --tpm2-device=auto --tpm2-pcrs=7 "$osdev"; then
      shred -u "$pwfile"
      die "TPM2 enrolment on the OS volume failed (wrong passphrase?). Re-run: sudo $ATLAS_ENTRY phase1 --force 02"
    fi
    shred -u "$pwfile"
    # crypttab: key column -> none, add tpm2-device=auto (+ x-initrd.attach for the root device). UNVERIFIED: the
    # installer's exact line for 26.04.1 (curtin names the mapping after the storage id, e.g. dm_crypt-0); parsed,
    # never hard-coded. The passphrase slot stays as the OS recovery path.
    if grep -qE "^[[:space:]]*${osmap}[[:space:]]" /etc/crypttab; then
      awk -v m="$osmap" 'BEGIN{OFS=" "} $1==m && $0 !~ /^#/ {
          opts=(NF>=4)?$4:"luks"; if (opts !~ /tpm2-device=/) opts=opts",tpm2-device=auto"; if (opts !~ /x-initrd.attach/) opts=opts",x-initrd.attach";
          print $1,$2,"none",opts; next } {print}' /etc/crypttab >/etc/crypttab.atlas.tmp
      cat /etc/crypttab.atlas.tmp >/etc/crypttab && rm -f /etc/crypttab.atlas.tmp
      log "crypttab: $osmap now unlocks via tpm2-device=auto: $(grep -E "^[[:space:]]*${osmap}[[:space:]]" /etc/crypttab)"
    else
      die "no /etc/crypttab line for $osmap; cannot make the OS volume TPM-unlock at boot (add it by hand and re-run)"
    fi
    os_note="LUKS $osmap on $osdev, TPM2 enrolled now (passphrase slot kept as recovery)"
  fi

  # --- The one interactive pause: recovery key printed ONCE, framed, wait for WRITTEN DOWN (D3, R16) ----------
  if [[ -e "$ATLAS_LUKS_CONFIRMED" ]]; then
    log "recovery key already confirmed as written down on $(cat "$ATLAS_LUKS_CONFIRMED"); not printing it again"
  else
    local answer="" line
    line="$(printf '#%.0s' $(seq 1 78))"
    {
      echo; echo "$line"; echo "#"
      echo "#   LUKS RECOVERY KEY for the 8 TB data volume (label atlas-data, UUID $uuid)"
      echo "#   Printed ONCE. Write it down now and copy it to the external USB drive that lives"
      echo "#   AWAY from the node (D3, R16). It unlocks the volume if the TPM ever refuses."
      echo "#"
      echo "#       $recovery"
      echo "#"
      echo "#   On-node convenience copy (root only): $ATLAS_LUKS_RECOVERY"
      echo "#"; echo "$line"; echo
    } >/dev/tty 2>/dev/null || die "cannot print the recovery key: no terminal"
    while [[ "$answer" != "WRITTEN DOWN" ]]; do
      _luks_tty_read 'Type exactly  WRITTEN DOWN  to continue: ' answer
    done
    date -Is >"$ATLAS_LUKS_CONFIRMED"
    clear 2>/dev/null || true
    log "recovery key confirmed as written down (the key itself is never logged)"
  fi

  # --- crypttab for the data volume (unlocked in the main system, nofail so boot never waits on it) ------------
  local ct_line="$ATLAS_LUKS_MAPPING UUID=$uuid none tpm2-device=auto,nofail,headless=true,discard"
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

  # --- initramfs: dracut on 26.04 (VERIFIED); the TPM modules are added explicitly (hostonly default UNVERIFIED) --
  install -d -m 755 /etc/dracut.conf.d
  printf 'add_dracutmodules+=" crypt systemd-cryptsetup tpm2-tss "\n' >/etc/dracut.conf.d/90-atlas-tpm2.conf
  if command -v dracut >/dev/null; then
    log "regenerating the initramfs with dracut (this takes a minute)"
    dracut --force --regenerate-all --quiet || die "dracut --force --regenerate-all failed"
    if [[ -n "$osdev" ]] && command -v lsinitrd >/dev/null; then
      if ! lsinitrd 2>/dev/null | grep -qE 'systemd-cryptsetup|libcryptsetup-token-systemd-tpm2'; then
        warn "the initrd does not list systemd-cryptsetup/tpm2 token support; the OS volume may ask for its passphrase at the console after the step-4 reboot (keyboard is at hand for Phase 1). Check /etc/dracut.conf.d/90-atlas-tpm2.conf and 'lsinitrd | grep -i tpm2'."
        os_note+="; initrd tpm2 support UNVERIFIED (see log)"
      fi
    fi
  elif command -v update-initramfs >/dev/null; then
    warn "dracut absent, falling back to update-initramfs -u -k all (initramfs-tools)"
    update-initramfs -u -k all
  else
    die "neither dracut nor update-initramfs exists; cannot regenerate the initramfs"
  fi
  systemctl daemon-reload

  run_verify V2 v02-tpm.sh "$dev" "$ATLAS_LUKS_MAPPING" "${osdev:--}" "$os_note" \
    || die "V2 failed after enrolment; see the verify table"
  log "step 2 complete: data volume $dev is LUKS2 (uuid $uuid), TPM2-unlocked as /dev/mapper/$ATLAS_LUKS_MAPPING"
}
