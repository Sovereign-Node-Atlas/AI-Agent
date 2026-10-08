#!/usr/bin/env bash
# verify/v02-tpm.sh — V2: fTPM present and enabled, TPM2 enrolment succeeded (Section 3.2, 3.5, 21).
# Usage: v02-tpm.sh LUKS_DEVICE MAPPING_NAME [OS_LUKS_DEVICE|-] [OS_NOTE]
#   Checks: /dev/tpmrm0 exists; systemd-cryptenroll --tpm2-device=list sees it; the LUKS2 header of LUKS_DEVICE
#   carries a systemd-tpm2 token whose PCR list (read from the token JSON, `cryptsetup token export`, never from the
#   human-readable luksDump text) contains PCR 7 (adjudicated conflict 1, S9: an empty list or an unreadable binding
#   is a FAIL, never "unknown"); the mapping MAPPING_NAME is active (which, after the Phase 1 reboot, proves the TPM
#   unlocked it without a keyboard). OS_LUKS_DEVICE must carry a systemd-tpm2 token bound to PCR 7 as well (S9);
#   "-" (no LUKS under "/") is DEFERRED, exit 2, once everything about the data volume passed: Section 3.5 wants
#   LUKS2 on the OS volume, which only a reinstall gives, so it is the Principal's to-do os-volume-encryption (policy
#   v0.3.3), never reported as a pass and never a red row. Secure Boot (D15, decided 2026-10-05: the Principal enables it in the BIOS): while it is
#   still off, a binding to PCR 7 alone ties the unlock to nothing an attacker's own boot medium would change, so the
#   state is a NOTE in every V2 message and a to-do (secure-boot), never a failure (policy v0.3.3). Exit 0 pass,
#   1 fail, 2 deferred (OS volume unencrypted). Must run as root.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

dev="${1:-}"; mapping="${2:-atlas-data}"; osdev="${3:-}"; osnote="${4:-}"
[[ -n "$dev" ]] || { echo "usage: v02-tpm.sh LUKS_DEVICE MAPPING_NAME [OS_LUKS_DEVICE|-] [OS_NOTE]"; exit 1; }
[[ -c /dev/tpmrm0 ]] || { echo "/dev/tpmrm0 absent: fTPM disabled in BIOS or tpm driver missing"; exit 1; }
listing="$(systemd-cryptenroll --tpm2-device=list 2>&1 || true)"
grep -q '/dev/tpmrm0' <<<"$listing" || { echo "systemd-cryptenroll --tpm2-device=list does not show /dev/tpmrm0: $listing"; exit 1; }

# Secure Boot state: the SecureBoot EFI variable (4-byte attribute header, byte 4 is the value), then mokutil; a
# legacy-BIOS boot has none. Same reading as phase1_secure_boot_state in phase1/01-preflight.sh (this script is
# standalone, §5, so it does not source that file).
sb_state() {
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
sb="$(sb_state)"
sb_data_note=""; sb_os_note=""

# token_pcrs DEVICE -> prints the PCR list of the first systemd-tpm2 token ("7" or "0,4,7"), or an error message with
# return 1. Token ids come from luksDump's "Tokens:" section; the binding from the token JSON ("tpm2-pcrs": [7], an
# array of integers, VERIFIED systemd-cryptenroll(1) / the systemd-tpm2 token format), parsed without jq because this
# runs in step 2 before the proxy exists (jq is installed in step 4).
token_pcrs() {
  local d="$1" dump ids id json list found=0
  dump="$(cryptsetup luksDump "$d" 2>&1)" || { echo "luksDump failed on $d"; return 1; }
  ids="$(awk '/^Tokens:/ {t=1; next} /^[A-Za-z]/ {t=0} t && /^[[:space:]]+[0-9]+:[[:space:]]*systemd-tpm2/ {gsub(/:/,"",$1); print $1}' <<<"$dump")"
  [[ -n "$ids" ]] || { echo "no systemd-tpm2 token on $d"; return 1; }
  for id in $ids; do
    json="$(cryptsetup token export --token-id "$id" "$d" 2>/dev/null)" || { echo "cryptsetup token export --token-id $id $d failed: cannot read the PCR binding"; return 1; }
    found=1
    list="$(grep -oE '"tpm2-pcrs"[[:space:]]*:[[:space:]]*\[[^]]*\]' <<<"$json" | head -n1 | sed -E 's/.*\[//; s/\]//' | tr -d '[:space:]')"
    if ! grep -qE '"tpm2-pcrs"' <<<"$json"; then
      echo "token $id on $d has no tpm2-pcrs field: cannot read the PCR binding (token format changed?)"; return 1
    fi
    [[ -n "$list" ]] || { echo "TPM2 token $id on $d is bound to NO PCRs (systemd 259 default; enrol with --tpm2-pcrs=7)"; return 1; }
    printf '%s\n' "$list"
    return 0
  done
  (( found )) || { echo "no readable systemd-tpm2 token on $d"; return 1; }
}

# pcr_ok LIST — the comma-separated list contains 7 (S9).
pcr_ok() { [[ ",$1," == *",7,"* ]]; }

# sb_check LIST WHAT -> returns 1 (and prints why) when Secure Boot is off and the binding is PCR 7 alone, unless
# acknowledged. A list with more PCRs (0, 4) binds to firmware/boot-manager code and does not need the acknowledgement.
# Policy v0.3.3 / D15: Secure Boot off is recorded in the V2 message (the Principal enables it, phase1/01-preflight.sh
# records the to-do); it never fails V2, which proves the enrolment itself.
sb_check() {
  local list="$1" what="$2"
  [[ "$sb" == enabled ]] && return 0
  [[ "$list" == "7" ]] || return 0
  echo "$what bound to PCR 7 while Secure Boot is $sb: binding not yet tied to this OS image (to-do secure-boot, D15)"
  return 0
}

data_pcrs="$(token_pcrs "$dev")" || { echo "V2 fail: $data_pcrs"; exit 1; }
pcr_ok "$data_pcrs" || { echo "V2 fail: data volume enrolment is not bound to PCR 7 (tpm2 pcrs=$data_pcrs on $dev; re-enrol: systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 $dev)"; exit 1; }
sb_data_note="$(sb_check "$data_pcrs" "data volume $dev")"
if ! cryptsetup status "$mapping" >/dev/null 2>&1; then
  echo "V2 fail: mapping $mapping is not active (TPM2 unlock did not happen)"; exit 1
fi
boot="$(cut -d- -f1 /proc/sys/kernel/random/boot_id)"
os_msg="OS volume: ${osnote:-not checked}"
if [[ -n "$osdev" && "$osdev" != "-" ]]; then
  os_pcrs="$(token_pcrs "$osdev")" || { echo "V2 fail on OS volume $osdev: $os_pcrs"; exit 1; }
  # S9 fixes --tpm2-pcrs=7 for every enrolment V2 proves: an OS token from an earlier manual enrolment with another
  # mask (or none) must not pass unnoticed any more than the data volume's would.
  pcr_ok "$os_pcrs" || { echo "V2 fail: OS volume enrolment is not bound to PCR 7 (tpm2 pcrs=$os_pcrs on $osdev; re-enrol: systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 $osdev)"; exit 1; }
  sb_os_note="$(sb_check "$os_pcrs" "OS volume $osdev")"
  os_msg="OS volume $osdev: tpm2 pcrs=$os_pcrs${osnote:+ ($osnote)}${sb_os_note:+; $sb_os_note}"
else
  # The caller established that "/" is not on LUKS. Policy v0.3.3: a to-do (os-volume-encryption, recorded by the
  # pre-flight), noted here; V2 proves the data-volume enrolment, which stands.
  os_msg="OS volume UNENCRYPTED (to-do os-volume-encryption, Section 3.5): ${osnote:-no note}"
fi
sb_note="secure_boot=$sb${sb_data_note:+; $sb_data_note}"
echo "fTPM /dev/tpmrm0 seen by systemd; $dev: tpm2 pcrs=$data_pcrs; mapping $mapping active (boot $boot); $os_msg; $sb_note"
[[ -n "$osdev" && "$osdev" != "-" ]] || exit 2
exit 0
