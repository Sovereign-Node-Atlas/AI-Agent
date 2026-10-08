#!/usr/bin/env bash
# phase1/02-luks.sh — Phase 1 step 2 (Sections 3.5, 17, 21 V2; D3, R16): LUKS2 on the data volume, TPM2 enrolment
# bound to PCR 7 (adjudicated conflict 1: systemd 259's default PCR mask is empty), recovery key printed ONCE, the
# one interactive pause ("WRITTEN DOWN"), crypttab, initramfs (dracut on 26.04). Also enrols TPM2 on the
# installer-made OS volume when it is LUKS.
#
# THE CONSOLE PAUSE (rule §7.6): one console session, at most two framed blocks. Block 1 asks for the inputs, each
# checked on the spot (three tries) before it is used and before anything irreversible: the OS LUKS passphrase ONCE when the installer encrypted
# the OS volume and it has no TPM2 token yet, or when this step finds that its token no longer unseals (e.g. Secure
# Boot switched on; the TPM is tried first); and the data volume's recovery key ONCE when nothing on the node can
# authorise a header change (keyfile gone, TPM refuses, no usable on-node copy). A missing or disabled TPM stops the
# step before anything is asked. Block 2 prints a NEW recovery key once and waits for WRITTEN DOWN; only after that
# are the old recovery slots revoked, an exposed copy shredded and the confirmation written, so an interrupted pause
# never leaves a revoked key as the only one the Principal holds (review v0.3.4). If the only problem was a missing
# on-node copy on an encrypted OS with exactly one recovery slot, the typed key is restored as the copy instead of
# being replaced. Every run of this step re-seals existing TPM2 tokens to the current PCR 7 (a no-op when it is
# unchanged), so --force 02 after switching Secure Boot on is the remedy, and so is a plain re-run of an unfinished
# step. Section 3.5 wants the OS volume TPM-unlocked too; typed secrets are used for this step only and never stored,
# except a key that restores the missing on-node copy (D3). The screen AND the terminal scrollback are cleared after
# each block, also when the pause is interrupted (an EXIT/INT trap around block 2). Everything else in this step runs
# unattended.
#
# WHAT STAYS ON THE NODE (D3, R16): the recovery key's on-node convenience copy ($ATLAS_LUKS_RECOVERY, root 600) when
# the OS volume is LUKS2 (D3: "on the node and on an external USB drive"; R16 "the on-node copy is convenience only";
# Section 3.5). Policy v0.3.3: an unencrypted OS volume (the installer's encrypted-LVM box not ticked) is the
# Principal's install choice, not broken machinery, so step 1 records the to-do os-volume-encryption and this step
# still encrypts the data volume and continues. Then NO on-node copy is written: a plaintext key beside the volume it
# opens would void its encryption, so the written and USB copies are the only ones, which the pause and the to-do say
# (doc S33); a copy found from an earlier run is not just shredded but its key is rotated (shredding a file does not
# revoke the key), and V2 is recorded deferred, not pass (verify/v02-tpm.sh). The generated keyfile is NOT kept: it
# authorises the enrolments and is then wiped from the LUKS header and shredded, because a second full-strength unlock
# secret on the OS drive is nothing the TPM path needs (re-enrolment after a firmware change authorises with
# --unlock-tpm2-device=auto, UNVERIFIED flag from systemd 256, or with the recovery key; step 3's fallback open uses
# the on-node copy where one exists). So the ways into the 8 TB volume are: the TPM at boot, the recovery key (USB and
# written copies, plus the on-node copy when the OS volume is encrypted).
#
# PCR 7 AND SECURE BOOT: with Secure Boot off, PCR 7 carries no image authority and the TPM unseals to any OS booted
# on this hardware. D15 (2026-10-05, supersedes D2): the Principal enables Secure Boot in the BIOS; step 1 records a
# to-do while it is off and never stops (policy v0.3.3); verify/v02-tpm.sh names the Secure Boot state in every V2
# row. The mask stays --tpm2-pcrs=7 (S9, adjudicated conflict 1); a stronger mask needs a Section 23 amendment.
#
# No package is installed here: cryptsetup, systemd-cryptsetup and dracut are seeded on the 26.04 Server ISO
# (VERIFIED: the installer itself uses them for the encrypted OS volume). Their absence stops the step (step 4 brings
# the proxy; rule §7.1). systemd-cryptenroll needs the libtss2 libraries at runtime (step 1 installs the one the image
# lacks, libtss2-rc0t64, doc S42), and the INITRAMFS needs tpm2-tools as well: dracut's
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

# _luks_is_blockdev PATH — [[ -b ]], as a function so phase1/luks_helpers_test.sh can stub it without a real device.
_luks_is_blockdev() { [[ -b "$1" ]]; }

# _luks_os_device — the LUKS device backing "/": prints nothing when "/" has no crypt layer; returns 1 with a message
# on stderr when a crypt layer exists but its device cannot be resolved, so a caller never takes "encrypted" for
# "unencrypted" (the unencrypted branch records a reinstall to-do and skips the OS enrolment).
_luks_os_device() {
  local map dev
  map="$(_luks_os_mapping)"
  [[ -n "$map" ]] || return 0
  dev="$(cryptsetup status "$map" 2>/dev/null | awk '$1=="device:" {print $2; exit}')" || true
  _luks_is_blockdev "$dev" || { echo "\"/\" is on the crypt mapping '$map' but 'cryptsetup status $map' names no backing device" >&2; return 1; }
  printf '%s\n' "$dev"
}

# _luks_enroll DEVICE ARGS... — run systemd-cryptenroll ARGS DEVICE with whatever authorises a header change: the
# keyfile while it exists; else the TPM (--unlock-tpm2-device=auto, UNVERIFIED: added in systemd 256 per the release
# notes and expected on 259); else the recovery key ($PASSWORD is systemd-cryptenroll's documented passphrase source)
# from the in-memory copy of this run or the on-node copy (D3). Dies with the exact way out when none works.
# _luks_recovery_slots DEVICE — the keyslot numbers held by systemd-recovery tokens, one per line (LUKS2 JSON
# metadata; empty when none). Read in full before parsing (no early-exit pipe under pipefail).
_luks_recovery_slots() {
  local j
  j="$(cryptsetup luksDump --dump-json-metadata "$1" 2>/dev/null)" || return 0
  python3 -c 'import json,sys
d = json.loads(sys.argv[1])
s = sorted({int(k) for t in d.get("tokens", {}).values() if t.get("type") == "systemd-recovery" for k in t.get("keyslots", [])})
sys.stdout.write("".join(f"{k}\n" for k in s))' "$j" 2>/dev/null || true
}

# _luks_key_ok DEVICE KEY — does KEY (a passphrase or recovery key) open DEVICE? The key goes through a pipe from the
# printf builtin, never argv; --key-file=- takes the bytes as they are (no newline handling), the same bytes
# systemd-cryptenroll later gets from $PASSWORD; --disable-external-tokens keeps a TPM token from answering instead.
_luks_key_ok() {
  printf '%s' "$2" | cryptsetup open --test-passphrase --disable-external-tokens --key-file=- "$1" >/dev/null 2>&1
}

# _luks_node_key — the in-memory or on-node recovery copy, whitespace stripped (empty when none).
_luks_node_key() {
  local rec="${LUKS_RECOVERY_INMEM:-}"
  [[ -n "$rec" || ! -s "$ATLAS_LUKS_RECOVERY" ]] || rec="$(tr -d '[:space:]' <"$ATLAS_LUKS_RECOVERY")"
  printf '%s' "$rec"
}

# _luks_enroll_try DEVICE ARGS... — the same authorisation order as _luks_enroll (keyfile, TPM, in-memory or on-node
# recovery copy) but returns 1 instead of stopping when none works, so the --force 02 re-seal can ask for the USB
# recovery key in the pause instead (review v0.3.4: on an unencrypted OS volume there is no on-node copy).
_luks_enroll_try() {
  local dev="$1"; shift
  if [[ -s "$ATLAS_LUKS_KEYFILE" ]]; then
    systemd-cryptenroll "--unlock-key-file=$ATLAS_LUKS_KEYFILE" "$@" "$dev"; return
  fi
  systemd-cryptenroll --unlock-tpm2-device=auto "$@" "$dev" 2>/dev/null && return 0
  local rec="${LUKS_RECOVERY_INMEM:-}"
  [[ -n "$rec" || ! -s "$ATLAS_LUKS_RECOVERY" ]] || rec="$(tr -d '[:space:]' <"$ATLAS_LUKS_RECOVERY")"
  [[ -n "$rec" ]] || return 1
  PASSWORD="$rec" systemd-cryptenroll "$@" "$dev"
}

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
    IFS= read -r -s -p "$prompt" "${var?}" </dev/tty >/dev/tty; echo >/dev/tty   # IFS=: a passphrase keeps its edge blanks
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
    local img out checked=0 missing=()
    for img in /boot/initrd.img-*; do
      [[ -f "$img" ]] || continue
      checked=$((checked + 1))
      out="$(lsinitrd "$img" 2>/dev/null || true)"
      grep -qE 'libcryptsetup-token-systemd-tpm2|libtss2-esys' <<<"$out" || missing+=("${img##*/}")
    done
    if (( checked == 0 )); then
      warn "no /boot/initrd.img-* found after dracut --regenerate-all; initrd TPM2 support could not be checked (ls /boot)"
      PHASE1_INITRD_NOTE="initrd TPM2 support UNVERIFIED (no /boot/initrd.img-* found)"
    elif (( ${#missing[@]} > 0 )); then
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
  local osdev osmap os_note="" need_os_enrol=0 os_reseal=0
  # Every run of this step re-seals existing TPM2 tokens to the current PCR 7 (review v0.3.4, fourth round): whether a
  # token still unseals decides, not --force. Unchanged PCR 7: systemd 259 keeps the token as it is (no-op). Changed
  # (Secure Boot switched on, firmware update): the new token is enrolled first and the old tpm2 slots wiped with the
  # new one excluded (VERIFIED in cryptenroll.c: wipe_slots(..., except_slot=slot)). A usable TPM is checked first so
  # a missing or disabled fTPM never turns into a request for a key that cannot help.
  local tpm_why; tpm_why="$(atlas_tpm_check)" || die "no usable TPM: $tpm_why. Nothing was changed."
  osdev="$(_luks_os_device)" || die "cannot resolve the LUKS device under / (see the message above). This is not an unencrypted install; check: findmnt /; lsblk -s \"\$(findmnt -n -o SOURCE /)\""
  osmap="$(_luks_os_mapping)"
  if [[ -z "$osdev" ]]; then
    os_note="OS volume NOT encrypted (to-do os-volume-encryption: reinstall with the encrypted-LVM option)"
    warn "$os_note; Section 3.5 wants LUKS2 on it. The 8 TB data volume is encrypted now regardless, and the phase continues"
    todo_add os-volume-encryption "The OS volume is not encrypted: reinstall Ubuntu with the encrypted-LVM option (Section 3.5), then run Phase 1 again" \
      "Until then NO copy of the data volume's recovery key is kept on the node (it would sit on an unencrypted disk beside the volume it opens): the USB copy is the only one, keep it safe (D3, R16). V2 stays deferred."
  elif _luks_has_token "$osdev" tpm2; then
    # Re-seal with the TPM's own authorisation first (PCR 7 unchanged: a no-op, no passphrase); only when the TPM
    # refuses (Secure Boot just switched on) is the installer passphrase asked, once, in console block 1.
    if systemd-cryptenroll --unlock-tpm2-device=auto --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 "$osdev" >/dev/null 2>&1; then
      os_note="LUKS $osmap on $osdev, TPM2 token current (checked and re-sealed with the TPM's own authorisation)"
      log "OS volume: $os_note"
    else
      need_os_enrol=1; os_reseal=1
      log "OS volume: LUKS $osmap on $osdev: the TPM no longer unseals its token (PCR 7 changed, e.g. Secure Boot switched on); it is re-sealed with the installer passphrase, asked once in console block 1"
    fi
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

  # --- TPM2 token and recovery key (review v0.3.4, third round). RULE: nothing irreversible before the Principal has
  # typed WRITTEN DOWN. New tokens and a new recovery key may be ADDED before that (the volume stays openable by the
  # old ways too); old recovery slots are revoked, an exposed copy is shredded and the confirmation is written only
  # after it. When nothing on the node can authorise a header change (keyfile gone, TPM refuses, no copy), the data
  # volume's recovery key is asked in the console block below instead of stopping. --------------------------------
  local keep_copy=1 exposed=0 need_new_key=0 need_data_rec=0 data_tpm_pending=0
  [[ -n "$osdev" ]] || keep_copy=0
  # D3's on-node copy presumes an encrypted OS volume. A copy found on an unencrypted one may have been read, and
  # shredding a file does not revoke its key (flash may keep the old pages), so that key is rotated, not just deleted.
  (( ! keep_copy )) && [[ -e "$ATLAS_LUKS_RECOVERY" ]] && exposed=1
  if (( exposed )) || ! _luks_has_token "$dev" recovery || [[ ! -e "$ATLAS_LUKS_CONFIRMED" ]] \
     || { (( keep_copy )) && [[ ! -s "$ATLAS_LUKS_RECOVERY" ]]; }; then
    need_new_key=1
  fi
  (( need_new_key || need_os_enrol )) && _luks_need_tty     # fail fast: these need the console, nothing is changed yet

  log "$( _luks_has_token "$dev" tpm2 && echo "checking the TPM2 token on $dev against the current PCR 7 (re-sealed if it changed)" || echo "enrolling TPM2 (PCR 7) on $dev" )"
  if ! _luks_enroll_try "$dev" --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 >/dev/null 2>&1; then
    # Authorisation known good (keyfile, or a node copy that opens the volume) means the TPM enrolment itself failed:
    # broken machinery, and no typed key can help.
    if [[ -s "$ATLAS_LUKS_KEYFILE" ]] || { [[ -n "$(_luks_node_key)" ]] && _luks_key_ok "$dev" "$(_luks_node_key)"; }; then
      die "systemd-cryptenroll --tpm2-device=auto failed on $dev although it was authorised (V2). Check: fTPM enabled, exactly one TPM (systemd-cryptenroll --tpm2-device=list). Nothing was revoked."
    fi
    need_data_rec=1; data_tpm_pending=1
  fi
  # Recovery slots that exist now; revoked after WRITTEN DOWN when a new key replaces them (by number: LUKS2 fills the
  # first free slot, so "the newest is the highest" would be wrong once the keyfile slot is free).
  local old_slots=() sl recovery="" restore_copy=0
  mapfile -t old_slots < <(_luks_recovery_slots "$dev")
  if (( need_new_key && ! need_data_rec )); then
    # stderr goes to a root-only file, shown only on failure: on success it carries only a banner (the key itself is
    # on stdout, and no QR code is drawn when stderr is not a terminal).
    local errf; errf="$(umask 077; mktemp "$ATLAS_ETC/secrets/.enroll-err.XXXXXX")"
    recovery="$(_luks_enroll_try "$dev" --recovery-key 2>"$errf" | tr -d '[:space:]')" || recovery=""
    if [[ -z "$recovery" ]]; then
      local enr_err; enr_err="$(tail -n 5 "$errf" | tr '\n' ' ')"; rm -f "$errf"
      if [[ -s "$ATLAS_LUKS_KEYFILE" ]] || { [[ -n "$(_luks_node_key)" ]] && _luks_key_ok "$dev" "$(_luks_node_key)"; }; then
        die "systemd-cryptenroll --recovery-key failed on $dev although it was authorised: $enr_err Nothing was revoked."
      fi
      _luks_has_token "$dev" recovery \
        || die "systemd-cryptenroll --recovery-key failed on $dev and no recovery key has ever been issued, so there is nothing to type: $enr_err"
      need_data_rec=1       # nothing on the node authorises it: the recovery key the Principal holds will
    else
      rm -f "$errf"
    fi
  elif (( ! need_new_key )); then
    log "recovery key already enrolled and confirmed as written down on $(cat "$ATLAS_LUKS_CONFIRMED")"
  fi
  (( need_data_rec )) && log "nothing on the node can authorise a change to $dev (keyfile gone, TPM refuses, no copy): its recovery key is asked once, in the console block below"
  (( need_data_rec )) && _luks_need_tty

  # --- Console block 1: the inputs (data volume recovery key, OS passphrase). A wrong value stops the step here, before
  # anything irreversible. ------------------------------------------------------------------------------------------
  local pw="" rk="" line answer=""
  line="$(printf '#%.0s' $(seq 1 78))"
  if (( need_data_rec || need_os_enrol )); then
    {
      echo; echo "$line"; echo "#"
      if (( need_data_rec )); then
        echo "#   The 8 TB data volume needs its RECOVERY KEY (from your USB drive or your written copy): the TPM no"
        echo "#   longer unseals it (PCR 7 changed, e.g. Secure Boot switched on) and no usable copy is on this node."
        if (( need_new_key && keep_copy && ! exposed && ${#old_slots[@]} == 1 )); then
          echo "#   It is never logged; it is kept as the node's root-only copy (D3), which was missing."
        else
          echo "#   It is used for this step only and never stored or logged."
        fi
        echo "#"
      fi
      if (( need_os_enrol )); then
        echo "#   The OS volume ($osmap) is encrypted with the passphrase you typed in the Ubuntu installer."
        (( os_reseal )) && echo "#   (Its TPM2 token no longer unseals, e.g. Secure Boot switched on; it is re-sealed to the current state.)"
        echo "#   It is asked ONCE so the TPM can unlock the OS at boot without a keyboard (never stored)."
        echo "#"
      fi
      echo "$line"; echo
    } >/dev/tty 2>/dev/null || die "cannot use the console: no terminal"
    # Each value is checked here, before anything is changed, with up to three tries.
    local tries
    if (( need_data_rec )); then
      tries=0
      while :; do
        rk=""; while [[ -z "$rk" ]]; do _luks_tty_read "  Recovery key of the 8 TB data volume: " rk silent; done
        rk="$(tr -d '[:space:]' <<<"$rk")"
        _luks_key_ok "$dev" "$rk" && break
        tries=$((tries + 1))
        (( tries < 3 )) || { rk=""; die "the recovery key for $dev was not accepted three times. Nothing was revoked and no new key was shown; a re-run continues from here: sudo $ATLAS_ENTRY phase1"; }
        echo "  Not accepted, try again." >/dev/tty
      done
    fi
    if (( need_os_enrol )); then
      tries=0
      while :; do
        pw=""; while [[ -z "$pw" ]]; do _luks_tty_read "  LUKS passphrase for $osmap: " pw silent; done
        _luks_key_ok "$osdev" "$pw" && break
        tries=$((tries + 1))
        (( tries < 3 )) || { pw=""; die "the passphrase for $osmap was not accepted three times. Nothing was revoked and no new key was shown; a re-run continues from here: sudo $ATLAS_ENTRY phase1"; }
        echo "  Not accepted, try again." >/dev/tty
      done
    fi
    printf '\033[2J\033[3J\033[H' >/dev/tty 2>/dev/null || true
  fi
  if (( need_data_rec )); then
    # Proves the key and re-seals the TPM2 token in one call (new token enrolled first, old tpm2 slots wiped).
    if ! PASSWORD="$rk" systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 "$dev" >/dev/null; then
      rk=""
      die "the recovery key opens $dev but the TPM2 enrolment with it failed: the TPM is the problem (systemd-cryptenroll --tpm2-device=list). Nothing was revoked."
    fi
    data_tpm_pending=0
    LUKS_RECOVERY_INMEM="$rk"          # authorises the remaining header changes of this run; cleared at the end
    log "TPM2 token on $dev $( _luks_has_token "$dev" tpm2 && echo re-sealed ) to the current PCR 7 with the typed recovery key"
    if (( need_new_key && keep_copy && ! exposed && ${#old_slots[@]} == 1 )); then
      # Encrypted OS, only the copy or the confirmation was missing: the typed key is the Principal's own written/USB
      # key, now proven, so it is restored as the on-node copy rather than replaced (their copies stay valid). Only with
      # exactly ONE recovery slot: a second one is the trace of an interrupted pause (a key shown but never
      # confirmed), so that case rotates and revokes both after WRITTEN DOWN.
      restore_copy=1; need_new_key=0
    elif (( need_new_key )); then
      recovery="$(_luks_enroll_try "$dev" --recovery-key 2>/dev/null | tr -d '[:space:]')" || recovery=""
      [[ -n "$recovery" ]] || die "systemd-cryptenroll --recovery-key failed on $dev after the typed key was accepted"
    fi
    rk=""
  fi
  (( data_tpm_pending )) && die "the TPM2 token on $dev could not be enrolled (V2). Check: fTPM enabled, exactly one TPM (systemd-cryptenroll --tpm2-device=list)"
  if (( restore_copy )); then
    ( umask 077; printf '%s\n' "$LUKS_RECOVERY_INMEM" >"$ATLAS_LUKS_RECOVERY" )
    date -Is >"$ATLAS_LUKS_CONFIRMED"
    log "on-node recovery copy restored from the typed key ($ATLAS_LUKS_RECOVERY, root 600; D3); your written and USB copies stay valid"
  fi

  # --- Console block 2: the NEW recovery key, printed ONCE, then WRITTEN DOWN (D3, R16). Only after that are the old
  # recovery slots revoked, an exposed copy shredded and the on-node copy (encrypted OS only) written. ---------------
  if [[ -n "$recovery" ]]; then
    LUKS_RECOVERY_INMEM="$recovery"     # authorises the revocations below and the keyfile wipe later in this step
    rm -f "$ATLAS_LUKS_CONFIRMED"
    # An interrupted pause (Ctrl-C, end of input, an error) must not leave the key on screen or in the scrollback.
    trap 'printf "\033[2J\033[3J\033[H" >/dev/tty 2>/dev/null || true; echo "  recovery key NOT confirmed: nothing was revoked; re-run sudo $ATLAS_ENTRY phase1 to issue and confirm a new one" >/dev/tty 2>/dev/null || true' EXIT
    trap 'exit 130' INT TERM
    {
      echo; echo "$line"; echo "#"
      echo "#   LUKS RECOVERY KEY for the 8 TB data volume (label atlas-data, UUID $uuid)"
      echo "#   Printed ONCE. Write it down now and copy it to the external USB drive that lives"
      echo "#   AWAY from the node (D3, R16). It unlocks the volume if the TPM ever refuses."
      (( ${#old_slots[@]} > 0 )) && echo "#   It REPLACES your previous recovery key, which stops working once you confirm below."
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
      echo "$line"; echo
    } >/dev/tty 2>/dev/null || die "cannot print the recovery key: no terminal (nothing was revoked; the new key is unused)"
    while [[ "$answer" != "WRITTEN DOWN" ]]; do
      _luks_tty_read 'Type exactly  WRITTEN DOWN  to continue: ' answer
    done
    # ESC[2J clears the screen, ESC[3J the scrollback (xterm/VTE/Windows Terminal honour it; `clear` alone does not).
    printf '\033[2J\033[3J\033[H' >/dev/tty 2>/dev/null || true
    trap - EXIT INT TERM
    for sl in "${old_slots[@]}"; do
      [[ "$sl" =~ ^[0-9]+$ ]] || die "unexpected recovery slot '$sl' on $dev"
      PASSWORD="$recovery" systemd-cryptenroll "--wipe-slot=$sl" "$dev" >/dev/null \
        || die "could not revoke the old recovery key slot $sl on $dev (the new key works; re-run --force 02 to retry)"
    done
    (( ${#old_slots[@]} > 0 )) && warn "the previous recovery key (slot ${old_slots[*]}) is revoked; only the key just written down opens the volume"
    if (( exposed )); then
      shred -u "$ATLAS_LUKS_RECOVERY"
      warn "shredded the old on-node recovery copy (OS volume unencrypted); its key was revoked above"
    fi
    if (( keep_copy )); then
      ( umask 077; printf '%s\n' "$recovery" >"$ATLAS_LUKS_RECOVERY" )
    fi
    date -Is >"$ATLAS_LUKS_CONFIRMED"
    log "recovery key confirmed as written down$( (( keep_copy )) && echo "; on-node copy at $ATLAS_LUKS_RECOVERY (root, 600; D3)" || echo "; no on-node copy (OS volume unencrypted)") (the key itself is never logged; screen and scrollback cleared)"
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
    # headless=true: no password-prompt fallback, so only the TPM can make this pass.
    systemd-cryptsetup attach "$ATLAS_LUKS_MAPPING" "/dev/disk/by-uuid/$uuid" none tpm2-device=auto,headless=true \
      || { record_v V2 fail "TPM2 unlock test failed on $dev (systemd-cryptsetup attach, tpm2-device=auto, headless)"; die "TPM2 unlock test failed (V2). If PCR 7 changed during this run (Secure Boot or firmware), re-run: sudo $ATLAS_ENTRY phase1 --force 02"; }
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
    log "keyfile slot wiped and $ATLAS_LUKS_KEYFILE shredded; the TPM and the recovery key ($( (( keep_copy )) && echo 'USB + on-node copy, D3' || echo 'written + USB copies only; the OS volume is unencrypted' )) are the only ways in"
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

  # Every run of this step (re-)seals the tokens to the current PCR 7, so with Secure Boot on they are now bound to it.
  if declare -F phase1_secure_boot_state >/dev/null && [[ "$(phase1_secure_boot_state)" == enabled ]] \
     && todo_is_open secure-boot; then
    todo_done secure-boot
  fi
  # v02 exits 2 (deferred, run_verify returns 0) when the ONLY gap is the unencrypted OS volume; a real TPM2 failure
  # on either volume is exit 1 and stays fatal here.
  run_verify V2 v02-tpm.sh "$dev" "$ATLAS_LUKS_MAPPING" "${osdev:--}" "$os_note" \
    || die "V2 failed after enrolment; see the verify table"
  log "step 2 complete: data volume $dev is LUKS2 (uuid $uuid), TPM2-unlocked as /dev/mapper/$ATLAS_LUKS_MAPPING"
}
