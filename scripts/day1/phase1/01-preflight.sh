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

# phase1_secure_boot_state — "enabled", "disabled" or "unknown". Source of record: the SecureBoot EFI variable (efivarfs
# files carry a 4-byte attribute header, byte 4 is the value); mokutil --sb-state when present; a legacy-BIOS boot has
# no Secure Boot at all. verify/v02-tpm.sh carries the same reading (it is standalone and must not depend on this file).
phase1_secure_boot_state() {
  local f v
  for f in /sys/firmware/efi/efivars/SecureBoot-*; do
    [[ -r "$f" ]] || continue
    v="$(od -An -tu1 -j4 -N1 "$f" 2>/dev/null | tr -d '[:space:]')"
    case "$v" in 1) echo enabled; return 0 ;; 0) echo disabled; return 0 ;; esac
  done
  if command -v mokutil >/dev/null 2>&1; then
    case "$(mokutil --sb-state 2>/dev/null || true)" in
      *enabled*) echo enabled; return 0 ;;
      *disabled*) echo disabled; return 0 ;;
    esac
  fi
  [[ -d /sys/firmware/efi ]] || { echo disabled; return 0; }
  echo unknown
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
  # pciutils is on every Ubuntu Server image; nothing is installed before the proxy exists (rule §7.1, step 4).
  command -v lspci >/dev/null || die "pre-flight: lspci (pciutils) is missing; install it from the console and re-run (no package is fetched before the allowlist proxy exists)"
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
  # Assigned on their own lines (fix round 3): a `die` inside a command substitution that is an argument to `log` exits
  # only the subshell and set -e ignores it, so an unreadable mem_info_* would print "gtt_total= MiB" and continue.
  local gtt vram_bytes vram
  gtt="$(gpu_gtt_total_mb)"
  vram_bytes="$(cat "$cdev/mem_info_vram_total")" || die "pre-flight: cannot read $cdev/mem_info_vram_total (amdgpu bound to $slot but the sysfs layout differs)"
  vram=$(( vram_bytes / 1048576 ))
  log "drm: $(dirname "$cdev") gtt_total=$gtt MiB (pre-reboot: TTM's default is ~50 % of RAM; step 4 raises it), vram_total=$vram MiB"
  [[ -c /dev/kfd ]] || warn "pre-flight: /dev/kfd is absent (amdkfd not loaded?); Phase 4 containers need it, Phase 1 does not"

  # --- fTPM (V2 is recorded in step 2, after enrolment) ---------------------------------------------------------
  [[ -c /dev/tpmrm0 ]] || die "pre-flight: /dev/tpmrm0 absent: enable fTPM in the BIOS (Section 3.2) and reboot"
  local tpmlist; tpmlist="$(systemd-cryptenroll --tpm2-device=list 2>&1 || true)"
  grep -q '/dev/tpmrm0' <<<"$tpmlist" || die "pre-flight: systemd-cryptenroll --tpm2-device=list does not list /dev/tpmrm0: $tpmlist"
  log "tpm2: $(grep '/dev/tpmrm0' <<<"$tpmlist" | head -n1)"
  [[ ! -e /etc/systemd/tpm2-pcr-public-key.pem ]] \
    || die "pre-flight: /etc/systemd/tpm2-pcr-public-key.pem exists; systemd-cryptenroll would also bind to a PCR 11 signature that GRUB boots cannot satisfy. Remove it (it belongs to UKI/systemd-boot setups) and re-run."
  # --- Secure Boot vs the PCR 7 binding (fix round 3, major). D2 closes Secure Boot as DISABLED and S9 fixes the TPM2
  # enrolment to PCR 7. With Secure Boot off, PCR 7 measures only the SecureBoot=0/PK/KEK/db/dbx variables and no
  # image-authority event, so its value is the same for the installed GRUB and for any live USB booted on this
  # hardware: the TPM would unseal both volumes' keys to anyone who boots their own OS on the node, and Section 3.5's
  # encryption at rest protects against disk removal only, not theft of the whole node. Two closed decisions meet
  # here with a consequence the baseline does not record; the Principal settles it, and the scripts never report
  # V2 green over it unnoticed. The acknowledgement is ATLAS_ACCEPT_PCR7_NO_SB=1 in atlas.env (also enforced by
  # verify/v02-tpm.sh, which puts the Secure Boot state in every V2 row). CONVENTIONS §3 / config/atlas.env.example
  # list the key (blank by default); Section 20 still needs an R-item for the decision (README "Known limits").
  local sb; sb="$(phase1_secure_boot_state)"
  log "secure boot: $sb (D2 closes it as disabled; step 2 binds the TPM2 tokens to PCR 7, S9)"
  if [[ "$sb" != enabled ]]; then
    if [[ "${ATLAS_ACCEPT_PCR7_NO_SB:-0}" == "1" ]]; then
      warn "pre-flight: Secure Boot is $sb, so the PCR 7 binding of step 2 does not tie the TPM unlock to this OS image (any OS booted on this hardware can unseal); ACKNOWLEDGED by ATLAS_ACCEPT_PCR7_NO_SB=1 in $ATLAS_ETC/atlas.env and recorded in every V2 row"
    else
      die "pre-flight: Secure Boot is $sb (D2) and step 2 binds the TPM2 enrolment to PCR 7 only (S9). With Secure Boot off, PCR 7 carries no image-authority measurement, so the TPM unseals both volumes' keys to ANY OS booted on this hardware (a rescue USB): the encryption at rest then protects against disk removal only, not theft of the whole node. Decide before anything is enrolled: (a) enable Secure Boot in the BIOS (reopens D2; PCR 7 then carries the image authority; the shim/GRUB path of Ubuntu boots signed) or (b) accept the weaker binding knowingly with ATLAS_ACCEPT_PCR7_NO_SB=1 in $ATLAS_ETC/atlas.env (every V2 row records it). Not offered here: --tpm2-pcrs=0+4+7, because PCR 4 changes on every GRUB/shim update and the headless node would stop at a console passphrase prompt after unattended-upgrades; it needs a Section 23 amendment of S9 first. Then re-run: sudo $ATLAS_ENTRY phase1"
    fi
  fi

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
  # Section 3.5 requires LUKS2 on the OS volume too ("nothing transient touches disk unencrypted") and Section 22's
  # precondition is an install with the encrypted-LVM option. No step offers a waiver (fix round 3): only a Section 23
  # amendment by the Principal could change 3.5 or D3, and the scripts follow the document. Checked on "/" itself,
  # not on any crypt mapping (an already-open atlas-data mapping must not count).
  if lsblk -sno TYPE "$root_src" 2>/dev/null | grep -qx crypt; then
    log "OS volume: LUKS present (installer choice, Section 3.5)"
  else
    die "pre-flight: the OS volume is NOT encrypted, but Section 3.5 requires LUKS2 on both volumes ('nothing transient touches disk unencrypted'; Section 22 presumes the encrypted-LVM install). Reinstall Ubuntu Server with the encrypted-LVM option and re-run: sudo $ATLAS_ENTRY phase1. (No setting waives this: a change to 3.5/D3 is a Section 23 amendment, not a script option.)"
  fi
  # SSH, Cockpit and xrdp bind to LAN_IP itself (Section 3.6). A DHCP lease that later changes would leave them on
  # the stale address (console-only recovery), so a dynamic address is flagged for a router reservation.
  if ip -o -4 addr show dev "$LAN_IFACE" scope global 2>/dev/null | grep -q ' dynamic '; then
    warn "pre-flight: $LAN_IP on $LAN_IFACE is a DHCP lease (not static). SSH, Cockpit and xrdp are bound to this address from step 4: reserve it for this node's MAC on the router (or make it static in netplan) so it never changes."
  else
    log "network: $LAN_IP on $LAN_IFACE is a static (non-dynamic) address"
  fi

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
