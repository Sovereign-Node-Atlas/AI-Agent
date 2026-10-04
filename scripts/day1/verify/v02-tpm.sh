#!/usr/bin/env bash
# verify/v02-tpm.sh — V2: fTPM present and enabled, TPM2 enrolment succeeded (Section 3.2, 3.5, 21).
# Usage: v02-tpm.sh LUKS_DEVICE MAPPING_NAME [OS_LUKS_DEVICE|-] [OS_NOTE]
#   Checks: /dev/tpmrm0 exists; systemd-cryptenroll --tpm2-device=list sees it; the LUKS2 header of LUKS_DEVICE
#   carries a systemd-tpm2 token bound to PCR 7 (adjudicated conflict 1); the mapping MAPPING_NAME is active (which,
#   after the Phase 1 reboot, proves the TPM unlocked it without a keyboard). OS_LUKS_DEVICE, when given, must carry
#   a systemd-tpm2 token bound to PCR 7 as well (S9). Exit 0 pass, 1 fail. Must run as root (cryptsetup luksDump).
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

dev="${1:-}"; mapping="${2:-atlas-data}"; osdev="${3:-}"; osnote="${4:-}"
[[ -n "$dev" ]] || { echo "usage: v02-tpm.sh LUKS_DEVICE MAPPING_NAME [OS_LUKS_DEVICE|-] [OS_NOTE]"; exit 1; }
[[ -c /dev/tpmrm0 ]] || { echo "/dev/tpmrm0 absent: fTPM disabled in BIOS or tpm driver missing"; exit 1; }
listing="$(systemd-cryptenroll --tpm2-device=list 2>&1 || true)"
grep -q '/dev/tpmrm0' <<<"$listing" || { echo "systemd-cryptenroll --tpm2-device=list does not show /dev/tpmrm0: $listing"; exit 1; }

token_check() { # token_check DEVICE -> prints "tpm2 pcrs=<list>" or returns 1
  local d="$1" dump
  dump="$(cryptsetup luksDump "$d" 2>&1)" || { echo "luksDump failed on $d"; return 1; }
  grep -q 'systemd-tpm2' <<<"$dump" || { echo "no systemd-tpm2 token on $d"; return 1; }
  local pcrs line
  # luksDump prints the token plugin's fields as "tpm2-hash-pcrs:   7" (older plugins: "tpm2-pcrs:"). A present but
  # EMPTY list is systemd 259's default (no PCR binding at all, adjudicated conflict 1) and is a fail; a missing line
  # (plugin output format unknown) is reported as "unknown" and tolerated.
  line="$(awk '/systemd-tpm2/ {t=1} t && /tpm2-(hash-)?pcrs:/ {print; exit}' <<<"$dump")"
  if [[ -z "$line" ]]; then
    pcrs="unknown"
  else
    pcrs="$(sed -E 's/.*pcrs:[[:space:]]*//; s/[[:space:]]+$//' <<<"$line")"
    [[ -n "$pcrs" ]] || { echo "TPM2 token on $d is bound to NO PCRs (systemd 259 default; enrol with --tpm2-pcrs=7)"; return 1; }
  fi
  printf 'tpm2 pcrs=%s' "$pcrs"
}

data_tok="$(token_check "$dev")" || { echo "V2 fail: $data_tok"; exit 1; }
if [[ "$data_tok" != *"pcrs=7"* && "$data_tok" != *"pcrs=unknown"* ]]; then
  echo "V2 fail: data volume enrolment is not bound to PCR 7 ($data_tok)"; exit 1
fi
if ! cryptsetup status "$mapping" >/dev/null 2>&1; then
  echo "V2 fail: mapping $mapping is not active (TPM2 unlock did not happen)"; exit 1
fi
boot="$(cut -d- -f1 /proc/sys/kernel/random/boot_id)"
os_msg="OS volume: ${osnote:-not checked}"
if [[ -n "$osdev" && "$osdev" != "-" ]]; then
  os_tok="$(token_check "$osdev")" || { echo "V2 fail on OS volume $osdev: $os_tok"; exit 1; }
  # S9 fixes --tpm2-pcrs=7 for every enrolment V2 proves: an OS token from an earlier manual enrolment with another
  # mask (or none) must not pass unnoticed any more than the data volume's would.
  if [[ "$os_tok" != *"pcrs=7"* && "$os_tok" != *"pcrs=unknown"* ]]; then
    echo "V2 fail: OS volume enrolment is not bound to PCR 7 ($os_tok on $osdev; re-enrol: systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 $osdev)"; exit 1
  fi
  os_msg="OS volume $osdev: $os_tok"
elif [[ "$osdev" == "-" && "$osnote" != *ACCEPTED* ]]; then
  # The caller established that "/" is not on LUKS. Section 3.5 requires LUKS2 on the OS volume too, so this is a
  # fail unless the Principal recorded the deviation (ATLAS_ALLOW_UNENCRYPTED_OS=1 in atlas.env; steps 1/2/5 pass
  # "ACCEPTED" in the note). A deviation is a recorded decision, never a footnote in a passing row (rule §7.4).
  echo "V2 fail: OS volume unencrypted and not acknowledged (Section 3.5; ATLAS_ALLOW_UNENCRYPTED_OS=1 in atlas.env records the deviation): ${osnote:-no note}"
  exit 1
fi
echo "fTPM /dev/tpmrm0 seen by systemd; $dev: $data_tok; mapping $mapping active (boot $boot); $os_msg"
exit 0
