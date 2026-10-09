#!/usr/bin/env bash
# phase1/05-postboot.sh — Phase 1 step 5 (Sections 3.3, 17, 21): after the reboot, prove that the kernel accepted the
# GRUB parameters (all three of Section 3.3, lockup_timeout included) and that the GTT pool matches them (V3a:
# /proc/cmdline, the live module parameters, 196608 MiB or MemTotal once the kernel caps the pool, S47), that
# vulkaninfo shows the GPU as RADV GFX1151, that /tmp is tmpfs and swap is off, and that the TPM unlocked the data
# volume without a keyboard (V2 re-recorded post-reboot). The llama-cli half of V3 belongs to the Phase 2 gate.
# Two read-only records follow (v0.3.5): the kernel line (Section 3.3, S45; closes or raises the to-do kernel-line) and
# what the unused XDNA 2 NPU shows (3.7 W-NPU).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# _npu_record — Section 2, 3.7 W-NPU (v0.3.5): what the XDNA 2 NPU shows on this kernel; read-only, never a stop.
# ATLAS does not use the NPU and no container is given /dev/accel. The line settles the one fact the 2026-10-09 review
# could not take from a primary source: the NPU's PCI revision (the in-tree amdxdna driver of 7.0 binds 1022:17f0 at
# revisions 0x10, 0x11 and 0x20 only, so another revision simply stays unbound, which is harmless here).
_npu_record() {
  local slot dev rev drv accel errs
  slot="$({ lspci -Dn -d 1022:17f0 2>/dev/null || true; } | awk '{print $1; exit}')"
  if [[ -z "$slot" ]]; then
    log "npu: no 1022:17f0 device on the PCI bus (switched off in the BIOS, or another id); ATLAS does not use it (3.7 W-NPU)"
    return 0
  fi
  dev="/sys/bus/pci/devices/$slot"
  rev="$(cat "$dev/revision" 2>/dev/null || echo unknown)"
  drv=none; [[ -L "$dev/driver" ]] && drv="$(basename "$(readlink "$dev/driver")")"
  accel="$({ find /dev/accel -mindepth 1 -maxdepth 1 -name 'accel*' -printf '%f\n' 2>/dev/null || true; } | sort | paste -sd' ')"
  errs="$({ dmesg 2>/dev/null || true; } | { grep -i amdxdna || true; } | { grep -iE 'error|fail|unknown' || true; } | head -n3 | tr '\n' ' ')"
  log "npu: $slot 1022:17f0 revision $rev, driver $drv, /dev/accel: ${accel:-none} (not used by ATLAS, given to no container; 3.7 W-NPU)"
  [[ -z "$errs" ]] || warn "npu: amdxdna reported: ${errs}(no ATLAS action: the NPU is unused and kernel updates carry the fixes; 3.7 W-NPU)"
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
  local osdev="" os_note initrd_note=""
  if declare -F _luks_os_device >/dev/null; then
    osdev="$(_luks_os_device)" || die "cannot resolve the LUKS device under / (see the message above)"
  fi
  [[ -s "$ATLAS_STATE/initrd-tpm2.note" ]] && initrd_note="$(head -n1 "$ATLAS_STATE/initrd-tpm2.note")"
  # Step 4's initrd check travels across the reboot in initrd-tpm2.note. Whether the console asked for the OS
  # passphrase this boot is not observable from here, so the row states what was checked, not "auto-unlocked".
  if [[ -n "$osdev" ]]; then os_note="OS volume up after the reboot; ${initrd_note:-initrd TPM2 support not checked}"
  else os_note="OS volume UNENCRYPTED (Section 3.5)"; fi   # v02 exits 2 (deferred, to-do os-volume-encryption) for "-"; run_verify returns 0
  run_verify V2 v02-tpm.sh "$dev" "$mapping" "${osdev:--}" "$os_note" \
    || die "V2 failed post-reboot"

  # V3a: kernel parameters and GTT pool; vulkaninfo needs the Vulkan loader, RADV and the tools (llama-cpp research §1).
  apt_install vulkan-tools mesa-vulkan-drivers libvulkan1
  [[ -f /usr/share/vulkan/icd.d/radeon_icd.x86_64.json || -f /usr/share/vulkan/icd.d/radeon_icd.json ]] \
    || warn "no RADV ICD file under /usr/share/vulkan/icd.d (mesa-vulkan-drivers installed?)"
  if dmesg 2>/dev/null | grep -q 'gttsize via module parameter is deprecated'; then
    log "dmesg: amdgpu.gttsize deprecation warning present (expected on kernel 7.x; ttm.pages_limit is the parameter of record)"
  fi
  run_verify V3a v03a-gtt.sh 196608 || die "V3a failed: the kernel parameters (gttsize, ttm.pages_limit, lockup_timeout), the GTT pool or the RADV device string do not match (see the verify table; cat /proc/cmdline; cat /sys/module/amdgpu/parameters/lockup_timeout; dmesg | grep -i gtt)"
  # Kernel line after the reboot (Section 3.3, S45): read-only. Step 4's to-do closes here once the line is GA 7.0.
  declare -F phase1_kernel_line_report >/dev/null || die "phase1_kernel_line_report is not defined: step 5 must be run by phase1-platform.sh"
  local kl
  if kl="$(phase1_kernel_line_report)"; then
    log "kernel line: GA 7.0 (Section 3.3): $kl"
    if todo_is_open kernel-line; then todo_done kernel-line; fi
  else
    warn "kernel line: not the GA 7.0 line of Section 3.3: $kl"
    todo_is_open kernel-line || phase1_kernel_line_todo "$kl"
  fi
  _npu_record
  log "step 5 complete: GTT pool $(gpu_gtt_total_mb) MiB, vulkaninfo sees RADV GFX1151"
}
