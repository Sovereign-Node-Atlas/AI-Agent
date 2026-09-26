#!/usr/bin/env bash
# phase1/01-preflight.sh — Phase 1 step 1 (Section 17): pre-flight using only what a bare host has.
# Confirms Ubuntu Server 26.04 (resolute), kernel 7.x, both NVMe drives, fTPM (for V2 in step 2), the AMD GPU from
# lspci and /sys/class/drm, and records V1 (network) as info. Never touches rocminfo: the gfx1151 confirmation is the
# Phase 4 container self-test (V11, Section 3.4). Adjudicated conflict 6: no hard fail on the PCI device id.
# Sourced by phase1-platform.sh through run_phase_steps; defines step_01 only.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

step_01() {
  local problems=()

  # --- OS and kernel (platform research item 12; VERIFIED codename/version) -------------------------------------
  local VERSION_ID="" VERSION_CODENAME="" VERSION="" PRETTY_NAME=""
  # shellcheck disable=SC1091  # /etc/os-release is a fixed-format KEY=VALUE file
  source /etc/os-release
  log "os: ${PRETTY_NAME:-?} (VERSION_ID=$VERSION_ID codename=$VERSION_CODENAME)"
  [[ "$VERSION_ID" == "26.04" && "$VERSION_CODENAME" == "resolute" ]] \
    || die "pre-flight: this is not Ubuntu 26.04 (resolute): ${PRETTY_NAME:-unknown}. Section 3.1 fixes 26.04.1 LTS."
  [[ "$VERSION" == *"26.04.1"* ]] || warn "pre-flight: expected the 26.04.1 point release, found '$VERSION' (the full update in step 4 will bring it current)"
  local kver; kver="$(uname -r)"
  log "kernel: $kver"
  [[ "$kver" == 7.* ]] || die "pre-flight: kernel $kver, but Section 3.1 requires the 7.x kernel that ships with 26.04 (amdgpu for gfx1151)"
  [[ "$kver" == 7.0.* ]] || warn "pre-flight: kernel $kver is not 7.0.x; the GRUB parameters were validated for 7.0 (V3a will tell)"
  if ! dpkg-query -W dracut >/dev/null 2>&1; then
    warn "pre-flight: dracut is not installed yet (26.04's initramfs tool); step 2 installs it"
  fi
  command -v sudo >/dev/null && log "sudo provider: $(sudo --version 2>/dev/null | head -n1 || echo unknown) (26.04 ships sudo-rs; the scripts never use sudo -E)"

  # --- Memory (Section 4.1 expects ~192 GB) --------------------------------------------------------------------
  local mem_gib; mem_gib="$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)"
  log "memory: ${mem_gib} GiB"
  (( mem_gib >= 180 )) || problems+=("MemTotal ${mem_gib} GiB, expected ~192 GiB (Section 2)")

  # --- GPU: vendor 0x1002, display class, driver amdgpu; device id logged and warned, never failed -------------
  command -v lspci >/dev/null || apt_install pciutils
  local slot; slot="$(lspci -Dn -d 1002: 2>/dev/null | awk '$2 ~ /^03/ {print $1; exit}')"
  [[ -n "$slot" ]] || die "pre-flight: no AMD (0x1002) display-class PCI device found (lspci -Dn -d 1002:)"
  log "gpu: $(lspci -nn -s "$slot")"
  local drv devid
  drv="$(basename "$(readlink -f "/sys/bus/pci/devices/$slot/driver" 2>/dev/null || echo none)")"
  devid="$(cat "/sys/bus/pci/devices/$slot/device" 2>/dev/null || echo unknown)"
  [[ "$drv" == amdgpu ]] || die "pre-flight: GPU $slot is bound to driver '$drv', not amdgpu"
  if [[ "$devid" != "0x1586" ]]; then
    warn "pre-flight: GPU device id $devid (0x1586 = Strix Halo 8050S/8060S in pci.ids; the 8065S id is unpublished, so this is informational)"
  fi
  local cdev; cdev="$(gpu_card_device_dir)" || die "pre-flight: no /sys/class/drm/card*/device with vendor 0x1002"
  log "drm: $(dirname "$cdev") gtt_total=$(gpu_gtt_total_mb) MiB (pre-reboot: TTM's default is ~50 % of RAM; step 4 raises it), vram_total=$(( $(cat "$cdev/mem_info_vram_total") / 1048576 )) MiB"
  [[ -c /dev/kfd ]] || warn "pre-flight: /dev/kfd is absent (amdkfd not loaded?); Phase 4 containers need it, Phase 1 does not"

  # --- fTPM (V2 is recorded in step 2, after enrolment) ---------------------------------------------------------
  [[ -c /dev/tpmrm0 ]] || die "pre-flight: /dev/tpmrm0 absent: enable fTPM in the BIOS (Section 3.2) and reboot"
  local tpmlist; tpmlist="$(systemd-cryptenroll --tpm2-device=list 2>&1 || true)"
  grep -q '/dev/tpmrm0' <<<"$tpmlist" || die "pre-flight: systemd-cryptenroll --tpm2-device=list does not list /dev/tpmrm0: $tpmlist"
  log "tpm2: $(grep '/dev/tpmrm0' <<<"$tpmlist" | head -n1)"
  [[ ! -e /etc/systemd/tpm2-pcr-public-key.pem ]] \
    || die "pre-flight: /etc/systemd/tpm2-pcr-public-key.pem exists; systemd-cryptenroll would also bind to a PCR 11 signature that GRUB boots cannot satisfy. Remove it (it belongs to UKI/systemd-boot setups) and re-run."

  # --- NVMe: two drives; DATA_DISK is a blank whole disk, not the root disk ------------------------------------
  log "disks:"; lsblk -d -o NAME,SIZE,MODEL,SERIAL,TRAN,TYPE | sed 's/^/    /'
  local n; n="$(lsblk -dn -o NAME,TRAN | awk '$2=="nvme"' | wc -l)"
  (( n >= 2 )) || problems+=("expected 2 NVMe drives, found $n")
  local data_dev root_src root_disk
  data_dev="$(readlink -f "$DATA_DISK")"
  root_src="$(findmnt -n -o SOURCE / )"
  root_disk="$(lsblk -sno NAME "$root_src" 2>/dev/null | tail -n1)"
  log "data disk: $DATA_DISK -> $data_dev; root on /dev/${root_disk:-?} ($root_src)"
  [[ -n "$root_disk" && "$data_dev" != "/dev/$root_disk" ]] || die "pre-flight: DATA_DISK $DATA_DISK is the root disk; refusing"
  [[ "$(lsblk -dn -o TYPE "$data_dev")" == disk ]] || die "pre-flight: DATA_DISK $data_dev is not a whole disk"
  local size_tb; size_tb="$(lsblk -dnb -o SIZE "$data_dev" | awk '{printf "%.1f", $1/1e12}')"
  awk -v s="$size_tb" 'BEGIN {exit !(s >= 7.0)}' || warn "pre-flight: DATA_DISK is ${size_tb} TB, the baseline says 8 TB (Section 2); continuing because it is blank"
  local sig; sig="$(blkid -p -o value -s TYPE "$data_dev" 2>/dev/null || true)"
  local label=""; [[ "$sig" == crypto_LUKS ]] && label="$(cryptsetup luksDump "$data_dev" 2>/dev/null | awk -F: '/^Label:/ {gsub(/^[ \t]+/,"",$2); print $2; exit}')"
  if [[ -z "$sig" ]] && (( $(lsblk -n -o NAME "$data_dev" | wc -l) == 1 )); then
    log "data disk: blank (no signature, no partitions): step 2 will format it"
  elif [[ "$sig" == crypto_LUKS && "$label" == atlas-data ]]; then
    log "data disk: already LUKS2 with label atlas-data (this script's own format from an earlier run): step 2 will reuse it"
  else
    die "pre-flight: DATA_DISK $data_dev carries signature '${sig:-partitions}' that is not this script's atlas-data LUKS volume; refusing to touch it. Wipe it deliberately (wipefs -a) only if you are certain, then re-run."
  fi
  if lsblk -rno TYPE | grep -qx crypt; then log "OS volume: LUKS present (installer choice)"; else warn "OS volume: NOT encrypted (installer choice; Section 3.5 assumed LUKS on both drives; step 2 records this)"; fi

  # --- Things later steps need from the Principal; warn now so they are fixed before the LUKS pause ------------
  local ak="/home/$PRINCIPAL_USER/.ssh/authorized_keys"
  [[ -s "$ak" ]] || warn "pre-flight: $ak is missing or empty. Step 4 makes SSH key-only and STOPS if no key is present: add your public key first (or work from the console for Phase 1)."
  [[ -s "$CLOUDFLARE_TXT" ]] || warn "pre-flight: $CLOUDFLARE_TXT is missing or empty; step 7 needs the Cloudflare token there (it is relocated and shredded in Phase 2 step 6b)"

  # --- V1: network, informational (Section 21, R9) -------------------------------------------------------------
  run_verify V1 v01-network.sh "$LAN_IFACE" || true

  if (( ${#problems[@]} > 0 )); then
    die "pre-flight problems: ${problems[*]}"
  fi
  log "pre-flight passed"
}
