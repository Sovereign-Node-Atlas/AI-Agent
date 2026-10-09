#!/usr/bin/env bash
# verify/v03a_gtt_test.sh — regression test (v0.3.5, doc S47) for verify/v03a-gtt.sh's GTT pool check. Kernel 7.0.0-38
# reports the requested 196608 MiB whatever MemTotal is; 7.0.0-39 and later cap the pool at MemTotal and log "Capping
# GTT to <N>M". Both must pass; the ~50 % TTM default and anything in between must fail. Runs a copy of the script with
# /proc, /sys/module and the GTT reader redirected to a fresh temp dir; touches nothing else.
# Run: bash verify/v03a_gtt_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin" "$T/mod/ttm/parameters" "$T/mod/amdgpu/parameters"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

echo "BOOT_IMAGE=/vmlinuz root=x amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000" >"$T/cmdline"
echo 50331648 >"$T/mod/ttm/parameters/pages_limit"
echo 196608 >"$T/mod/amdgpu/parameters/gttsize"
echo 10000,60000,10000,10000 >"$T/mod/amdgpu/parameters/lockup_timeout"
printf 'MemTotal:       195035136 kB\n' >"$T/meminfo"   # 190464 MiB: 192 GiB less carve-out and reservations
printf '#!/bin/sh\ncat "%s/dmesg" 2>/dev/null\nexit 0\n' "$T" >"$T/bin/dmesg"
printf '#!/bin/sh\necho "  deviceName = AMD Radeon Graphics (RADV GFX1151)"\n' >"$T/bin/vulkaninfo"
chmod +x "$T/bin/"*
sed -e "s#/proc/cmdline#$T/cmdline#" -e "s#/sys/module/#$T/mod/#g" -e "s#/proc/meminfo#$T/meminfo#" \
    -e "s#^source \"\$(dirname \"\$(readlink -f \"\$0\")\")/../lib/common.sh\"#source \"$REPO/lib/common.sh\"; gpu_gtt_total_mb() { cat \"$T/gtt\"; }#" \
    "$REPO/verify/v03a-gtt.sh" >"$T/v3a.sh"
grep -q 'gpu_gtt_total_mb() { cat' "$T/v3a.sh" || { echo "FAIL could not redirect the GTT reader (source line changed?)"; exit 1; }

v3a() {  # v3a GTT_MIB DMESG -> "rc:first 60 chars"
  echo "$1" >"$T/gtt"; printf '%s\n' "$2" >"$T/dmesg"
  local out rc
  out="$(PATH="$T/bin:$PATH" bash "$T/v3a.sh" 2>/dev/null)"; rc=$?
  printf '%s' "$rc:$out"
}
r="$(v3a 196608 'amdgpu 0000:c5:00.0: 196608M of GTT memory ready.')"
ok "uncapped (7.0.0-38): pass" "${r%%:*}" "0"
ok "uncapped: says so" "$(grep -c 'uncapped' <<<"$r")" "1"
r="$(v3a 190464 'amdgpu 0000:c5:00.0: Capping GTT to 190464M to not exceed available system memory')"
ok "capped at MemTotal (7.0.0-39): pass" "${r%%:*}" "0"
ok "capped: quotes the dmesg line" "$(grep -c 'Capping GTT to 190464M' <<<"$r")" "1"
r="$(v3a 189000 '')"
ok "capped, within 1 % below MemTotal: pass" "${r%%:*}" "0"
r="$(v3a 95232 '')"
ok "TTM default (~50 %): fail" "${r%%:*}" "1"
r="$(v3a 193000 '')"
ok "between MemTotal and the request: fail" "${r%%:*}" "1"
r="$(v3a 196610 '')"
ok "above the request: fail" "${r%%:*}" "1"
sed -i 's/ amdgpu.lockup_timeout=10000,60000,10000,10000//' "$T/cmdline"
r="$(v3a 196608 '')"
ok "parameter missing from the cmdline still fails" "${r%%:*}:$(grep -c 'cmdline lacks amdgpu.lockup_timeout' <<<"$r")" "1:1"

echo "v03a_gtt_test: $pass passed, $fail failed"
(( fail == 0 ))
