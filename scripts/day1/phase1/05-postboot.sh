#!/usr/bin/env bash
# phase1/05-postboot.sh — Phase 1 step 5 (Sections 3.3, 17, 21): after the reboot, prove that the kernel accepted the
# GRUB parameters and that the GTT pool matches them (V3a: /proc/cmdline, sysfs, 196608 MiB with tolerance), that
# vulkaninfo shows the GPU as RADV GFX1151, that /tmp is tmpfs and swap is off, and that the TPM unlocked the data
# volume without a keyboard (V2 re-recorded post-reboot). The llama-cli half of V3 belongs to the Phase 2 gate.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

step_05() {
  # The driver refuses to reach this step while the reboot marker still names the current boot.
  declare -F phase1_check_reboot >/dev/null && phase1_check_reboot

  findmnt -n -o FSTYPE /tmp | grep -qx tmpfs || die "/tmp is not tmpfs after the reboot (systemctl status tmp.mount); Section 3.5 requires it"
  (( $(wc -l </proc/swaps) == 1 )) || die "swap is active after the reboot: $(tail -n +2 /proc/swaps)"
  log "/tmp is tmpfs ($(findmnt -n -o OPTIONS /tmp)); swap off"

  # TPM auto-unlock across the reboot is the real V2 proof.
  local dev mapping="${ATLAS_LUKS_MAPPING:-atlas-data}"; dev="$(readlink -f "$DATA_DISK")"
  if ! findmnt -n "$ATLAS_SRV" >/dev/null; then
    warn "$ATLAS_SRV is not mounted after the reboot; trying the cryptsetup target and mount once"
    systemctl start cryptsetup.target || true
    systemctl start "$(systemd-escape --template=systemd-cryptsetup@.service "$mapping")" || true
    mount "$ATLAS_SRV" 2>/dev/null || true
  fi
  if ! findmnt -n "$ATLAS_SRV" >/dev/null; then
    record_v V2 fail "after reboot: /dev/mapper/$mapping not unlocked by the TPM and $ATLAS_SRV not mounted (journalctl -u systemd-cryptsetup@*)"
    die "the data volume did not auto-unlock after the reboot (V2 fail). Unlock it with the recovery key to investigate: cryptsetup open $dev $mapping"
  fi
  local osdev=""; declare -F _luks_os_device >/dev/null && osdev="$(_luks_os_device)"
  run_verify V2 v02-tpm.sh "$dev" "$mapping" "${osdev:--}" "$( [[ -n "$osdev" ]] && echo "auto-unlocked at boot" || echo "unencrypted (installer choice)")" \
    || die "V2 failed post-reboot"

  # V3a: kernel parameters and GTT pool; vulkaninfo needs the Vulkan loader, RADV and the tools (llama-cpp research §1).
  apt_install vulkan-tools mesa-vulkan-drivers libvulkan1
  [[ -f /usr/share/vulkan/icd.d/radeon_icd.x86_64.json || -f /usr/share/vulkan/icd.d/radeon_icd.json ]] \
    || warn "no RADV ICD file under /usr/share/vulkan/icd.d (mesa-vulkan-drivers installed?)"
  if dmesg 2>/dev/null | grep -q 'gttsize via module parameter is deprecated'; then
    log "dmesg: amdgpu.gttsize deprecation warning present (expected on kernel 7.x; ttm.pages_limit is the parameter of record)"
  fi
  run_verify V3a v03a-gtt.sh 196608 || die "V3a failed: the kernel parameters or the GTT pool do not match (see the verify table; cat /proc/cmdline; dmesg | grep -i gtt)"
  log "step 5 complete: GTT pool $(gpu_gtt_total_mb) MiB, vulkaninfo sees RADV GFX1151"
}
