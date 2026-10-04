#!/usr/bin/env bash
# phase1/03-mounts.sh — Phase 1 step 3 (Sections 3.5, 17; CONVENTIONS.md §2): ext4 on the unlocked data volume,
# /srv/atlas in fstab with nofail and a device timeout, the directory tree with its owners (creates the atlas and
# atlas-ddns accounts here), /srv/cold and /srv/backups, swap off permanently, /tmp tmpfs verified (adjudicated
# conflict 2: it is already tmpfs on 26.04; never "enable" a unit that has no [Install] section). Defines step_03.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

step_03() {
  local mapping="${ATLAS_LUKS_MAPPING:-atlas-data}" mdev="/dev/mapper/${ATLAS_LUKS_MAPPING:-atlas-data}"
  local dev; dev="$(readlink -f "$DATA_DISK")"

  # --- Make sure the volume is open (TPM first; the on-node recovery copy (D3) or a still-present keyfile as the
  # explicit fallback; step 2 wipes the keyfile once the tokens are enrolled) --------------------------------------
  if ! cryptsetup status "$mapping" >/dev/null 2>&1; then
    local uuid; uuid="$(cryptsetup luksUUID "$dev")"
    if ! systemd-cryptsetup attach "$mapping" "/dev/disk/by-uuid/$uuid" none tpm2-device=auto 2>/dev/null; then
      local kf="${ATLAS_LUKS_KEYFILE:-$ATLAS_ETC/secrets/luks-data.key}" rf="${ATLAS_LUKS_RECOVERY:-$ATLAS_ETC/secrets/luks-data.recovery}"
      warn "TPM2 attach failed; opening $dev with the recovery copy / keyfile instead (V2 is re-checked after the reboot in step 5)"
      if [[ -s "$rf" ]]; then
        tr -d '[:space:]' <"$rf" | cryptsetup open --key-file=- "$dev" "$mapping" \
          || die "cannot open $dev with the TPM or the on-node recovery copy $rf"
      elif [[ -s "$kf" ]]; then
        cryptsetup open --key-file "$kf" "$dev" "$mapping" || die "cannot open $dev with the TPM or the keyfile $kf"
      else
        die "cannot open $dev: the TPM refused and there is no on-node recovery copy (accepted unencrypted OS). Open it with the USB recovery key: cryptsetup open $dev $mapping, then re-run: sudo $ATLAS_ENTRY phase1"
      fi
    fi
  fi
  [[ -b "$mdev" ]] || die "$mdev is not a block device"

  # --- Filesystem: only on a blank mapping, or reuse our own labelled ext4 ----------------------------------------
  local fstype flabel
  fstype="$(blkid -p -o value -s TYPE "$mdev" 2>/dev/null || true)"
  flabel="$(blkid -p -o value -s LABEL "$mdev" 2>/dev/null || true)"
  if [[ -z "$fstype" ]]; then
    log "mkfs.ext4 -L atlas-data on $mdev"
    mkfs.ext4 -q -L atlas-data -m 0 -E lazy_itable_init=0,lazy_journal_init=0 "$mdev"
    udevadm settle --timeout=30 || true       # the filesystem UUID link and blkid cache follow the change event asynchronously
  elif [[ "$fstype" == ext4 && "$flabel" == atlas-data ]]; then
    log "$mdev already holds our ext4 (label atlas-data); not formatting"
  else
    die "refusing mkfs: $mdev carries $fstype (label '${flabel}') that is not this script's atlas-data filesystem"
  fi

  # --- fstab + mount (systemd.mount options VERIFIED: nofail, x-systemd.device-timeout) -------------------------
  local fs_uuid; fs_uuid="$(blkid -o value -s UUID "$mdev")"
  # nosuid,nodev (fix round): untrusted output lands here (workspace, sandbox, staging/inbox, model pulls), so a dropped
  # setuid binary or device node must be inert; not noexec, engines and venvs under engines/ must run. Same pair /tmp has.
  local fstab_line="UUID=$fs_uuid $ATLAS_SRV ext4 defaults,noatime,nosuid,nodev,nofail,x-systemd.device-timeout=30s 0 2"
  if grep -qE "^[^#]*[[:space:]]${ATLAS_SRV}[[:space:]]" /etc/fstab; then
    sed -i -E "s|^[^#]*[[:space:]]${ATLAS_SRV}[[:space:]].*\$|$fstab_line|" /etc/fstab
  else
    ensure_line /etc/fstab "$fstab_line"
  fi
  install -d -m 755 "$ATLAS_SRV"
  systemctl daemon-reload
  if ! findmnt -n "$ATLAS_SRV" >/dev/null; then
    mount "$ATLAS_SRV" || die "mount $ATLAS_SRV failed (fstab line: $fstab_line)"
  else
    mount -o remount "$ATLAS_SRV" || die "mount -o remount $ATLAS_SRV failed (fstab line: $fstab_line)"   # picks up nosuid,nodev on a re-run
  fi
  [[ "$(findmnt -n -o SOURCE "$ATLAS_SRV")" == "$mdev" ]] || die "$ATLAS_SRV is mounted from $(findmnt -n -o SOURCE "$ATLAS_SRV"), not $mdev"
  local mopts; mopts="$(findmnt -n -o OPTIONS "$ATLAS_SRV")"
  [[ "$mopts" == *nosuid* && "$mopts" == *nodev* ]] || die "$ATLAS_SRV is mounted without nosuid,nodev ($mopts)"
  log "mounted $ATLAS_SRV from $mdev ($(findmnt -n -o SIZE,AVAIL "$ATLAS_SRV"); $mopts)"

  # --- Service accounts (CONVENTIONS.md §2): atlas (render, video; docker is added in step 6), atlas-ddns ---------
  # The account's home is /var/lib/atlas/home, NOT /var/lib/atlas: the parent of $ATLAS_STATE (/var/lib/atlas/day1:
  # done markers, verify.jsonl, the phase1.gate marker that atlas-day1.sh trusts) stays root:root 755, so the
  # service account can never rename or replace the Day 1 state and audit trail (Section 16.3 item 6).
  local atlas_home=/var/lib/atlas/home
  ensure_dir /var/lib/atlas root:root 755
  if ! id -u atlas >/dev/null 2>&1; then
    useradd -r -m -d "$atlas_home" -s /bin/bash -U atlas
    log "created system user atlas (home $atlas_home)"
  elif [[ "$(getent passwd atlas | cut -d: -f6)" != "$atlas_home" ]]; then
    usermod -d "$atlas_home" atlas          # earlier layout had the home on /var/lib/atlas itself; no -m: day1/ must not move
    log "moved the atlas account's home to $atlas_home"
  fi
  ensure_dir "$atlas_home" atlas:atlas 750
  chown root:root /var/lib/atlas; chmod 755 /var/lib/atlas
  getent group render >/dev/null || die "group 'render' does not exist (udev creates it for /dev/dri/renderD*; is amdgpu loaded?)"
  usermod -aG render,video atlas
  if ! id -u atlas-ddns >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin -d /nonexistent -U atlas-ddns
    log "created system user atlas-ddns"
  fi
  id -u "$PRINCIPAL_USER" >/dev/null 2>&1 || die "PRINCIPAL_USER=$PRINCIPAL_USER does not exist"
  # load_env installed atlas.env as root:root because the atlas group did not exist yet (§2 says root:atlas 640).
  chown root:atlas "$ATLAS_ETC/atlas.env"; chmod 640 "$ATLAS_ETC/atlas.env"
  # root:atlas 710 (traverse-only for atlas; phase1/07-remote.sh header, phase2/06c): atlas-owned files live inside.
  ensure_dir "$ATLAS_ETC/secrets" root:atlas 710
  ensure_dir "$ATLAS_ETC/secrets/google" atlas:atlas 700

  # --- Directory tree (Section 3.5, Appendix C, CONVENTIONS.md §2) ---------------------------------------------
  chown atlas:atlas "$ATLAS_SRV"; chmod 755 "$ATLAS_SRV"
  local d
  for d in models engines data data/slots workspace sandbox vault staging; do
    ensure_dir "$ATLAS_SRV/$d" atlas:atlas 750
  done
  ensure_dir "$ATLAS_SRV/staging/inbox" "$PRINCIPAL_USER:atlas" 2770
  ensure_dir "$ATLAS_SRV/staging/inbox/voice-references" "$PRINCIPAL_USER:atlas" 2770
  ensure_dir /srv/cold atlas:atlas 750
  ensure_dir /srv/backups root:root 700
  ensure_dir "$ATLAS_STATE" root:root 755
  # Phase logs carry the Principal's username, addresses, the zone id and quoted compose output: root and adm only.
  # (lib/common.sh creates log files with the default umask; the 750 directory is what keeps them from other users.)
  ensure_dir "$ATLAS_STATE/logs" root:adm 750
  ensure_dir "$ATLAS_OPT" root:root 755
  log "tree: $ATLAS_SRV/{models,engines,data,workspace,sandbox,vault,staging} atlas:atlas; staging/inbox $PRINCIPAL_USER:atlas 2770; /srv/cold atlas; /srv/backups root"

  # --- Swap off permanently (curtin's /swap.img or a swap partition/LV; platform research item 3) ---------------
  swapoff -a || true
  sed -i -E 's/^([^#].*[[:space:]]swap[[:space:]].*)$/# \1  # disabled by ATLAS Phase 1 step 3 (Section 3.5)/' /etc/fstab
  systemctl daemon-reload
  local u
  while read -r u _; do
    [[ -n "$u" ]] || continue
    systemctl mask "$u" >/dev/null 2>&1 || true
  done < <(systemctl list-units --type=swap --all --no-legend --plain 2>/dev/null | awk '{print $1}')
  [[ -f /swap.img ]] && rm -f /swap.img
  (( $(wc -l </proc/swaps) == 1 )) || die "swap is still active: $(tail -n +2 /proc/swaps)"
  printf 'vm.swappiness=0\n' >/etc/sysctl.d/90-atlas-noswap.conf
  sysctl -q -p /etc/sysctl.d/90-atlas-noswap.conf || true
  log "swap disabled (fstab commented, units masked, /swap.img removed)"

  # --- /tmp tmpfs: already the 26.04 default (tmp.mount statically wanted by local-fs.target); verify + cap -----
  systemctl unmask tmp.mount >/dev/null 2>&1 || true
  install -d -m 755 /etc/systemd/system/tmp.mount.d
  cat >/etc/systemd/system/tmp.mount.d/atlas.conf <<'CONF'
# ATLAS Phase 1 step 3: cap /tmp so tmpfs pages never compete with the 192 GB GTT pool (default size=50% of RAM).
[Mount]
Options=mode=1777,strictatime,nosuid,nodev,size=16G,nr_inodes=1m
CONF
  systemctl daemon-reload
  if findmnt -n -o FSTYPE /tmp | grep -qx tmpfs; then
    log "/tmp is tmpfs ($(findmnt -n -o OPTIONS /tmp)); the 16G cap applies from the next boot"
  else
    local state; state="$(systemctl show tmp.mount -p UnitFileState --value 2>/dev/null || echo unknown)"
    [[ "$state" != masked ]] || die "tmp.mount is masked; unmask failed"
    warn "/tmp is not tmpfs yet (tmp.mount unit state: $state); it is wanted by local-fs.target and step 5 verifies it after the reboot"
  fi
}
