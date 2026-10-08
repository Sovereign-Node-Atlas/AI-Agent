#!/usr/bin/env bash
# phase1/01-preflight.sh — Phase 1 step 1 (Section 17): pre-flight using what a bare host has, plus systemd's TPM2
# libraries from the archive when the image lacks them (doc S42; the one pre-proxy install of this step).
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
  if ! command -v dracut >/dev/null 2>&1; then     # the same test step 2 makes (02-luks.sh), so the two always agree
    warn "pre-flight: dracut is not installed (26.04's initramfs tool, seeded on the Server ISO). Step 2 needs it and installs nothing before the proxy exists: install it from the console now (sudo apt-get install dracut), or step 2 will stop"
  fi
  command -v sudo >/dev/null && log "sudo provider: $(sudo --version 2>/dev/null | head -n1 || echo unknown) (26.04 ships sudo-rs; the scripts never use sudo -E)"

  # --- Memory (Section 4.1 expects ~192 GB) --------------------------------------------------------------------
  local mem_gib; mem_gib="$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)"
  log "memory: ${mem_gib} GiB"
  (( mem_gib >= 180 )) || problems+=("MemTotal ${mem_gib} GiB, expected ~192 GiB (Section 2)")

  # --- GPU: vendor 0x1002, display class, driver amdgpu; device id logged and warned, never failed -------------
  # pciutils is on every Ubuntu Server image; the only package step 1 installs before the proxy exists is
  # libtss2-rc0t64, below (rule §7.1's declared exceptions: rsync, libtss2-rc0t64, then squid/dnsmasq in step 4).
  command -v lspci >/dev/null || die "pre-flight: lspci (pciutils) is missing; install it from the console and re-run (step 1 fetches only systemd's TPM2 libraries before the allowlist proxy exists)"
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
  # systemd 259 dlopen()s libtss2-esys, libtss2-rc and libtss2-mu for every TPM2 operation (dlopen_tpm2, VERIFIED in
  # src/shared/tpm2-util.c v259). The 26.04.1 server image carries esys and mu but NOT libtss2-rc0t64 (VERIFIED from its
  # manifest; main, about 20 KB; nothing on the image depends on it), so systemd-cryptenroll answers "TPM2 support is
  # not installed" until it is there (doc S42). Step 2's enrolment also dlopen()s libtss2-tcti-device (tpm2_context_new). It is installed here from the Ubuntu archive: a declared pre-proxy
  # install like rsync in atlas-day1.sh and squid/dnsmasq in step 4 (the firewall does not exist yet).
  # On the default server install, libtss2-esys and -mu (and the device TCTI step 2's enrolment loads) arrive only
  # through fwupd, a Recommends of ubuntu-server; a minimized install may lack them too (review v0.3.4). So every
  # library systemd needs is checked, and whatever is missing is installed in one go (apt_install skips the rest).
  local ldc so missing_tss=0; ldc="$(ldconfig -p 2>/dev/null || true)"
  for so in libtss2-esys.so.0 libtss2-rc.so.0 libtss2-mu.so.0 libtss2-tcti-device.so.0; do
    grep -qF "$so" <<<"$ldc" || missing_tss=1
  done
  if (( missing_tss )); then
    log "installing systemd's TPM2 libraries (libtss2-rc0t64 is not on the 26.04.1 server image; esys/mu/tcti-device come only with fwupd)"
    apt_install libtss2-esys-3.0.2-0t64 libtss2-mu-4.0.1-0t64 libtss2-rc0t64 libtss2-tcti-device0t64
  fi
  local tpm_why; tpm_why="$(atlas_tpm_check)" || die "pre-flight: $tpm_why"
  log "tpm2: $(grep '^/dev/tpmrm' <<<"$(systemd-cryptenroll --tpm2-device=list 2>&1 || true)" | head -n1)"
  [[ ! -e /etc/systemd/tpm2-pcr-public-key.pem ]] \
    || die "pre-flight: /etc/systemd/tpm2-pcr-public-key.pem exists; systemd-cryptenroll would also bind to a PCR 11 signature that GRUB boots cannot satisfy. Remove it (it belongs to UKI/systemd-boot setups) and re-run."
  # --- Secure Boot vs the PCR 7 binding. S9 fixes the TPM2 enrolment to PCR 7. With Secure Boot off, PCR 7 measures
  # only the SecureBoot=0/PK/KEK/db/dbx variables and no image-authority event, so the TPM would unseal both volumes'
  # keys to anyone who boots their own OS on the node. D15 (2026-10-05) closes it: the Principal enables Secure Boot
  # in the BIOS (a precondition in README §1); until then the state is a warning, a note in every V2 row and a to-do.
  local sb; sb="$(phase1_secure_boot_state)"
  log "secure boot: $sb (D15: to be enabled in the BIOS; step 2 binds the TPM2 tokens to PCR 7, S9)"
  if [[ "$sb" != enabled ]]; then
    # D15 (decided 2026-10-05): the Principal enables Secure Boot in the BIOS. Until then the PCR 7 binding of step 2
    # does not tie the TPM unlock to this OS image (any OS booted on this hardware could unseal). Policy v0.3.3: this is
    # a to-do, never a stop; V2 records the Secure Boot state in its message.
    warn "pre-flight: Secure Boot is $sb. Enable it in the BIOS (D15); until then the TPM binding protects against disk removal only, not theft of the whole node. Enrolment proceeds."
    todo_add secure-boot "Enable Secure Boot in the BIOS (D15), then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1 --force 02 so the TPM binding is re-enrolled with Secure Boot measured" \
      "Section 3.2, D15, R23. Reboot into the BIOS, switch Secure Boot on, boot Ubuntu (it is signed), run the command."
  fi

  # --- NVMe: two drives; DATA_DISK is a blank whole disk, not the root disk ------------------------------------
  log "disks:"; lsblk -d -o NAME,SIZE,MODEL,SERIAL,TRAN,TYPE | sed 's/^/    /'
  local n; n="$(lsblk -dn -o NAME,TRAN | awk '$2=="nvme"' | wc -l)"
  (( n >= 2 )) || problems+=("expected 2 NVMe drives, found $n")
  local data_dev root_src root_disk
  data_dev="$(readlink -f "$DATA_DISK")"
  root_src="$(findmnt -n -o SOURCE / )"
  # List mode (-l): with NAME shown, lsblk draws tree prefixes into pipes, which made this "    └─nvme0n1" before.
  root_disk="$(lsblk -lnso NAME,TYPE "$root_src" 2>/dev/null | awk '$2=="disk" {print $1; exit}')"
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
  if grep -qx crypt <<<"$(lsblk -lnso TYPE "$root_src" 2>/dev/null || true)"; then
    log "OS volume: LUKS present (installer choice, Section 3.5)"
  else
    # Policy v0.3.3: a to-do, not a stop. The 8 TB data volume (everything ATLAS stores) is encrypted by step 2
    # regardless; the OS volume holds the system and logs. Section 3.5 still wants both, so it stays on the list.
    warn "pre-flight: the OS volume is NOT encrypted (the installer's 'Encrypt the LVM group with LUKS' option was not taken). The data volume will be encrypted by step 2; the OS volume stays as it is. Recorded as a to-do (Section 3.5)."
    todo_add os-volume-encryption "OS volume is unencrypted: reinstall Ubuntu Server with 'Set up this disk as an LVM group' + 'Encrypt the LVM group with LUKS' when convenient, then run Day 1 again (the data volume and its contents survive)" \
      "Section 3.5 wants LUKS2 on both volumes. Logs and the OS live on this one; all ATLAS data is on the encrypted 8 TB volume."
  fi
  # SSH, Cockpit and xrdp bind to LAN_IP itself (Section 3.6). A DHCP lease that later changes would leave them on
  # the stale address (console-only recovery), so a dynamic address is flagged for a router reservation.
  if ip -o -4 addr show dev "$LAN_IFACE" scope global 2>/dev/null | grep -q ' dynamic '; then
    warn "pre-flight: $LAN_IP on $LAN_IFACE is a DHCP lease (not static). SSH, Cockpit and xrdp are bound to this address from step 4: reserve it for this node's MAC on the router (or make it static in netplan) so it never changes."
  else
    log "network: $LAN_IP on $LAN_IFACE is a static (non-dynamic) address"
  fi

  # --- Things later steps need from the Principal; settled now so they are fixed before the LUKS pause ---------
  # The SSH key is a STOP, not a warning: step 4 makes SSH key-only (Section 3.6) and dies without a key, but by then
  # LUKS is enrolled and the node is about to reboot into the hardened configuration; a missing key found at step 1
  # costs one ssh command, found after the reboot it costs a console session. Section 22 (build-side preconditions)
  # is where the key belongs; the README §1 node list names it.
  local ak="/home/$PRINCIPAL_USER/.ssh/authorized_keys" keyline=""
  if [[ ! -s "$ak" ]]; then
    # Policy v0.3.3: ask once, plainly; no key means step 4 keeps password login on (ATLAS_SSH_PASSWORD_AUTH=keep) and
    # the hardening is a to-do, instead of locking the Principal out or stopping here.
    echo
    echo "  SSH key (optional now). Step 4 normally switches SSH to key-only login. On your Windows PC, PowerShell:"
    echo "    ssh-keygen -t ed25519            (Enter three times)"
    echo "    type \$env:USERPROFILE\\.ssh\\id_ed25519.pub"
    echo "  and paste the single line it prints (starts with ssh-ed25519) here."
    ask keyline "  Public key line (Enter to skip; password login then stays on and this becomes a to-do): "
    if [[ "$keyline" =~ ^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256|sk-ssh-ed25519@openssh.com)\ [A-Za-z0-9+/=]+ ]]; then
      install -d -m 700 -o "$PRINCIPAL_USER" -g "$PRINCIPAL_USER" "/home/$PRINCIPAL_USER/.ssh"
      printf '%s\n' "$keyline" >>"$ak"
      chown "$PRINCIPAL_USER:$PRINCIPAL_USER" "$ak"; chmod 600 "$ak"
      log "SSH public key added to $ak"
    else
      [[ -z "$keyline" ]] || warn "that did not look like an OpenSSH public key line; skipping"
      ensure_kv "$ATLAS_ETC/atlas.env" ATLAS_SSH_PASSWORD_AUTH keep
      export ATLAS_SSH_PASSWORD_AUTH=keep
      todo_add ssh-key "Add your SSH public key to $ak and switch SSH to key-only: set ATLAS_SSH_PASSWORD_AUTH= (blank) in $ATLAS_ETC/atlas.env, then sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1 --force 04" \
        "Section 3.6. Password login stays enabled until then (LAN and WireGuard only; never internet-facing)."
    fi
  fi
  if [[ ! -s "$CLOUDFLARE_TXT" && ! -s "$ATLAS_ETC/secrets/cloudflare.env" ]]; then
    warn "pre-flight: no Cloudflare token at $CLOUDFLARE_TXT; step 7 installs WireGuard and ntfy and leaves the dynamic DNS updater for later (to-do)"
  fi

  # --- V1: network, informational (Section 21, R9) -------------------------------------------------------------
  run_verify V1 v01-network.sh "$LAN_IFACE" || true

  if (( ${#problems[@]} > 0 )); then
    die "pre-flight problems: ${problems[*]}"
  fi
  log "pre-flight passed"
}
