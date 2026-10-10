#!/usr/bin/env bash
# phase1/05-postboot.sh — Phase 1 step 5 (Sections 3.3, 17, 21): after the reboot, prove that the kernel accepted the
# GRUB parameters (all three of Section 3.3, lockup_timeout included) and that the GTT pool matches them (V3a:
# /proc/cmdline, the live module parameters, 196608 MiB or MemTotal once the kernel caps the pool, S47), that no
# crash-kernel memory is reserved and a panic restarts the node after 10 s (V3a too, option (c) of 2026-10-10, S48: no
# crashkernel= on the cmdline, kexec_crash_size 0, exactly one panic=10, kernel.panic 10, panic_on_oops 0), that
# vulkaninfo shows the GPU as RADV GFX1151, that /tmp is tmpfs and swap is off, and that the TPM unlocked the data
# volume without a keyboard (V2 re-recorded post-reboot). The llama-cli half of V3 belongs to the Phase 2 gate.
# Three read-only records follow: the kernel line (v0.3.5, Section 3.3, S45; closes or raises the to-do kernel-line),
# what the unused XDNA 2 NPU shows (v0.3.5, 3.7 W-NPU), and whether a panic's log can be kept across the restart
# (S48: pstore backend, systemd-pstore, kdump state).
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

# _panic_log_record — option (c), S48: whether this node keeps a kernel panic's log across the panic=10 restart, and that
# kdump is still off; read-only, never a stop. With no crash kernel loaded, panic() writes the tail of the kernel log
# (kmsg_bytes, 10240 by default, compressed) to the pstore backend before the restart, an oops writes one too, and
# systemd-pstore.service copies the records to /var/lib/systemd/pstore at the next boot and erases them from the
# firmware (Storage=external, Unlink=yes; tmpfiles ages the copies out after 14 days). The backend is efi_pstore (EFI
# variables; a module udev loads by its platform:efivars alias) or, if the firmware has an ACPI ERST table, the built-in
# erst driver, which registers first. Which one this board offers is UNKNOWN until this line is read; "(null)" or nothing
# means a panic leaves no log here, which is a finding for the Principal, not a failure. Nothing under /sys/fs/pstore is
# removed (unlinking a record erases it from NVRAM), systemd-pstore is not run by hand, and no panic is triggered.
_panic_log_record() {
  local backend kmsg efi="" fs n_live n_arch pst kd use=""
  backend="$(cat /sys/module/pstore/parameters/backend 2>/dev/null || true)"
  kmsg="$(cat /sys/module/pstore/parameters/kmsg_bytes 2>/dev/null || echo unknown)"
  if [[ -d /sys/module/efi_pstore ]]; then
    efi=", efi_pstore loaded (pstore_disable=$(cat /sys/module/efi_pstore/parameters/pstore_disable 2>/dev/null || echo unknown))"
  fi
  fs="$(findmnt -n -o FSTYPE /sys/fs/pstore 2>/dev/null || true)"
  n_live="$({ ls -A /sys/fs/pstore 2>/dev/null || true; } | wc -l)"
  n_arch="$({ ls -A /var/lib/systemd/pstore 2>/dev/null || true; } | wc -l)"
  pst="$(systemctl is-enabled systemd-pstore.service 2>/dev/null || true)"
  kd="$(systemctl is-enabled kdump-tools.service 2>/dev/null || true)"
  if [[ -r /etc/default/kdump-tools ]]; then
    use="$(awk -F= '$1 ~ /^[[:space:]]*USE_KDUMP$/ {v = $2} END {print v}' /etc/default/kdump-tools)"
  fi
  log "panic log: pstore backend ${backend:-none}, kmsg_bytes $kmsg$efi; /sys/fs/pstore ${fs:-not mounted} with $n_live record(s); systemd-pstore.service ${pst:-unknown}; $n_arch archived under /var/lib/systemd/pstore; kdump-tools.service ${kd:-absent}, USE_KDUMP=${use:-unset} (option (c), S48)"
  case "$backend" in
    ""|"(null)") warn "panic log: no pstore backend is registered, so a kernel panic leaves no log across the panic=10 restart on this board (record only; journalctl -k -b | grep -E '^pstore: |^ERST: ')" ;;
  esac
  case "$kd" in
    enabled*) warn "panic log: kdump-tools.service is '$kd' again (step 4 disabled and masked it; V3a still fails on any crash-kernel reservation)" ;;
  esac
  if (( n_arch > 0 )); then
    warn "panic log: $n_arch crash record(s) from an earlier boot are archived under /var/lib/systemd/pstore (read them there; nothing is deleted)"
  fi
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
  run_verify V3a v03a-gtt.sh 196608 || die "V3a failed: the kernel parameters (gttsize, ttm.pages_limit, lockup_timeout), the crash-kernel and panic settings (no crashkernel=, nothing reserved, exactly one panic=10, kernel.panic 10, panic_on_oops 0; S48), the GTT pool or the RADV device string do not match (see the verify table; cat /proc/cmdline; cat /sys/module/amdgpu/parameters/lockup_timeout; cat /sys/kernel/kexec_crash_size; ls /etc/default/grub.d; dmesg | grep -i gtt)"
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
  _panic_log_record
  log "step 5 complete: GTT pool $(gpu_gtt_total_mb) MiB, vulkaninfo sees RADV GFX1151"
}
