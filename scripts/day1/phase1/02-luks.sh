#!/usr/bin/env bash
# phase1/02-luks.sh — Phase 1 step 2 (Sections 3.5, 17, 21 V2; D3, R16): LUKS2 on the data volume, TPM2 enrolment
# bound to PCR 7 (adjudicated conflict 1: systemd 259's default PCR mask is empty), recovery key printed ONCE, the
# one interactive pause ("WRITTEN DOWN"), crypttab, initramfs (dracut on 26.04). Also enrols TPM2 on the
# installer-made OS volume when it is LUKS.
#
# THE ONE PAUSE (rule §7.6): a single framed console block prints the recovery key, asks (only when the installer
# encrypted the OS volume and it has no TPM2 token yet) for the OS LUKS passphrase once, and waits for WRITTEN DOWN.
# The Principal is at the console exactly once. Everything else in this step runs unattended.
#
# OS VOLUME NOT ENCRYPTED: Section 3.5 requires LUKS2 on the OS volume too ("nothing transient touches disk
# unencrypted"). Step 1 already dies on an unencrypted OS volume unless ATLAS_ALLOW_UNENCRYPTED_OS=1 is set in
# /etc/atlas/atlas.env (a recorded decision of the Principal, not a footnote). With that acknowledgement this step
# (a) still enrols TPM2 and the recovery key, (b) then WIPES the keyfile slot and shreds the keyfile, and (c) writes NO
# on-node recovery copy, so the unencrypted OS drive holds nothing that opens the 8 TB volume; the USB copy is the
# only copy (D3/R16). V2 records the deviation and the acknowledgement in its message.
#
# No package is installed here: cryptsetup, systemd-cryptsetup and dracut are seeded on the 26.04 Server ISO
# (VERIFIED: the installer itself uses them for the encrypted OS volume); tpm2-tools is not needed because
# systemd-cryptenroll speaks to the TPM directly. Their absence stops the step (step 4 brings the proxy; rule §7.1).
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

# _luks_os_accepted — the Principal acknowledged an unencrypted OS volume (atlas.env, see the header).
_luks_os_accepted() { [[ "${ATLAS_ALLOW_UNENCRYPTED_OS:-0}" == "1" ]]; }

# _luks_unlock_arg — how systemd-cryptenroll authorises a change: the keyfile while it exists, else the TPM.
# UNVERIFIED: --unlock-tpm2-device= was added in systemd 256 (release notes) and is expected on 259; it is used only
# after the keyfile slot was wiped in the accepted-unencrypted-OS case, and the call dies loudly if it is refused.
_luks_unlock_arg() {
  if [[ -s "$ATLAS_LUKS_KEYFILE" ]]; then printf -- '--unlock-key-file=%s\n' "$ATLAS_LUKS_KEYFILE"
  else printf -- '--unlock-tpm2-device=auto\n'; fi
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

step_02() {
  local t missing=()
  for t in cryptsetup systemd-cryptenroll systemd-cryptsetup dracut; do command -v "$t" >/dev/null || missing+=("$t"); done
  (( ${#missing[@]} == 0 )) || die "missing on this host: ${missing[*]} (expected on the 26.04 Server ISO: cryptsetup, systemd-cryptsetup, dracut). Install them from the console (apt-get install cryptsetup systemd-cryptsetup dracut) and re-run; step 2 installs nothing because the allowlist proxy does not exist before step 4 (rule §7.1)"
  ensure_dir "$ATLAS_ETC/secrets" root:root 700

  local dev; dev="$(readlink -f "$DATA_DISK")"
  [[ -b "$dev" ]] || die "DATA_DISK $DATA_DISK does not resolve to a block device"
  [[ ! -e /etc/systemd/tpm2-pcr-public-key.pem ]] || die "/etc/systemd/tpm2-pcr-public-key.pem exists (see step 1)"

  # --- OS volume state, decided once (step 1 already died on an unacknowledged unencrypted OS volume) ------------
  local osdev osmap os_note="" need_os_enrol=0
  osdev="$(_luks_os_device)"; osmap="$(_luks_os_mapping)"
  if [[ -z "$osdev" ]]; then
    _luks_os_accepted || die "the OS volume is not encrypted (Section 3.5). Reinstall with LUKS, or set ATLAS_ALLOW_UNENCRYPTED_OS=1 in $ATLAS_ETC/atlas.env to accept the deviation knowingly, then re-run"
    os_note="OS volume UNENCRYPTED, ACCEPTED by ATLAS_ALLOW_UNENCRYPTED_OS=1 (Section 3.5 deviation): keyfile slot wiped, no on-node recovery copy, USB copy is the only copy (D3, R16)"
    warn "$os_note"
  elif _luks_has_token "$osdev" tpm2; then
    os_note="LUKS $osmap on $osdev, TPM2 already enrolled"
    log "OS volume: $os_note"
  else
    need_os_enrol=1
    log "OS volume: LUKS $osmap on $osdev without a TPM2 token: will enrol (the installer passphrase is asked once, in the pause below)"
  fi

  # --- Keyfile (task: generated keyfile in $ATLAS_ETC/secrets). It authorises enrolments now and re-enrolment after
  # a firmware change later; crypttab never references it (the TPM unlocks at boot). In the accepted-unencrypted-OS
  # case it exists only until the enrolments are done (header). ----------------------------------------------------
  local sig label=""
  sig="$(blkid -p -o value -s TYPE "$dev" 2>/dev/null || true)"
  [[ "$sig" == crypto_LUKS ]] && label="$(cryptsetup luksDump "$dev" 2>/dev/null | awk -F: '/^Label:/ {gsub(/^[ \t]+/,"",$2); print $2; exit}')"
  local keyfile_wiped=0
  if [[ "$sig" == crypto_LUKS && "$label" == atlas-data && ! -s "$ATLAS_LUKS_KEYFILE" ]] && _luks_os_accepted \
     && _luks_has_token "$dev" tpm2; then
    keyfile_wiped=1          # earlier run of this step already wiped the keyfile slot; the TPM authorises changes
    log "keyfile absent by design (accepted unencrypted OS); TPM2 authorises any re-enrolment"
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
  local unlock; unlock="$(_luks_unlock_arg)"
  if _luks_has_token "$dev" tpm2; then
    log "TPM2 token already enrolled on $dev"
  else
    log "enrolling TPM2 (PCR 7) on $dev"
    systemd-cryptenroll "$unlock" --tpm2-device=auto --tpm2-pcrs=7 "$dev" \
      || die "systemd-cryptenroll --tpm2-device=auto failed on $dev (V2). Check: fTPM enabled, exactly one TPM (systemd-cryptenroll --tpm2-device=list)"
  fi
  local recovery="" show_key=0
  if _luks_has_token "$dev" recovery && [[ -e "$ATLAS_LUKS_CONFIRMED" ]] && { _luks_os_accepted || [[ -s "$ATLAS_LUKS_RECOVERY" ]]; }; then
    log "recovery key already enrolled and confirmed as written down on $(cat "$ATLAS_LUKS_CONFIRMED")"
  else
    if _luks_has_token "$dev" recovery; then
      warn "a recovery token exists but its confirmation (or the on-node copy) is missing: wiping it and enrolling a fresh one"
      systemd-cryptenroll --wipe-slot=recovery "$unlock" "$dev"
    fi
    recovery="$(systemd-cryptenroll "$unlock" --recovery-key "$dev" 2>/dev/null | tr -d '[:space:]')"
    [[ -n "$recovery" ]] || die "systemd-cryptenroll --recovery-key printed nothing"
    if _luks_os_accepted; then
      rm -f "$ATLAS_LUKS_RECOVERY"
      log "recovery key enrolled; NO on-node copy (accepted unencrypted OS): the USB copy is the only copy"
    else
      ( umask 077; printf '%s\n' "$recovery" >"$ATLAS_LUKS_RECOVERY" )
      log "recovery key enrolled; on-node copy at $ATLAS_LUKS_RECOVERY (root, 600; D3 convenience copy)"
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
        if _luks_os_accepted; then echo "#   There is NO copy on the node (unencrypted OS volume, accepted): the USB copy is the only copy."
        else echo "#   On-node convenience copy (root only): $ATLAS_LUKS_RECOVERY"; fi
        echo "#"
      fi
      if (( need_os_enrol )); then
        echo "#   The OS volume ($osmap) is encrypted with the passphrase you typed in the Ubuntu installer."
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
    clear 2>/dev/null || true
    (( show_key )) && log "recovery key confirmed as written down (the key itself is never logged)"
    recovery=""
  fi

  # --- OS volume enrolment with the passphrase from the pause ($PASSWORD is systemd-cryptenroll's documented
  # passphrase source; nothing touches disk or /dev/shm) ---------------------------------------------------------
  if (( need_os_enrol )); then
    if ! PASSWORD="$pw" systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 "$osdev"; then
      pw=""
      die "TPM2 enrolment on the OS volume failed (wrong passphrase?). Re-run: sudo $ATLAS_ENTRY phase1 --force 02 (the recovery key is not printed again)"
    fi
    pw=""
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

  # --- Accepted unencrypted OS: nothing on the OS drive may open the data volume from now on (header) -----------
  if _luks_os_accepted && [[ -s "$ATLAS_LUKS_KEYFILE" ]]; then
    _luks_has_token "$dev" tpm2 && _luks_has_token "$dev" recovery \
      || die "refusing to wipe the keyfile slot: TPM2 and recovery tokens must both be enrolled first"
    systemd-cryptenroll --wipe-slot=password --unlock-tpm2-device=auto "$dev" \
      || die "systemd-cryptenroll --wipe-slot=password failed on $dev; the keyfile slot is still present"
    shred -u "$ATLAS_LUKS_KEYFILE"
    rm -f "$ATLAS_LUKS_RECOVERY"
    log "keyfile slot wiped and $ATLAS_LUKS_KEYFILE shredded; the TPM and the USB recovery key are the only ways in"
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
