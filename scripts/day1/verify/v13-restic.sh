#!/usr/bin/env bash
# verify/v13-restic.sh — V13: "restic backup completes and a restore verifies by checksum" (Sections 9.5, 21; Phase 2
# step 7; re-run quarterly by atlas-restic-check.service). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; one
# stdout line; never prompts; under 10 minutes; safe to re-run. Must run as root (the passphrase file is root 600).
# Usage: v13-restic.sh [CANARY=/srv/atlas/data/restic-canary.txt]
#   1. the repository answers (restic snapshots --json, 60 s) and holds at least one snapshot;
#   2. the newest snapshot is restored into a scratch directory, restricted to CANARY (--include, VERIFIED flag; 240 s);
#   3. sha256 of the restored file equals sha256 of the live file (the canary is written by step 7 and never changes
#      between the backup and this test; a changed file is reported as such, not as corruption);
#   4. `restic check` (structural, no data read; 240 s). Skipped when ATLAS_PHASE=quarterly: atlas-restic-check.service
#      runs `restic check --read-data-subset=1/10` right after this script, which includes the structural check.
# Time budget (fix round): 60 + 240 + 240 = 540 s < the 600 s of §5 and run_verify's 660 s kill; a stage that times
# out is named in the fail line instead of surfacing as a bare "exit 124".
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

canary="${1:-$ATLAS_SRV/data/restic-canary.txt}"
envf="$ATLAS_ETC/restic.env"
[[ -r "$envf" ]] || { echo "V13 fail: $envf missing (Phase 2 step 7 has not run)"; exit 1; }
command -v restic >/dev/null || { echo "V13 fail: restic not installed"; exit 1; }
[[ -f "$canary" ]] || { echo "V13 fail: canary $canary does not exist"; exit 1; }
set -a
# shellcheck disable=SC1090  # KEY=VALUE lines written by phase2/07-restic.sh
source "$envf"
set +a
[[ -r "${RESTIC_PASSWORD_FILE:-}" ]] || { echo "V13 fail: RESTIC_PASSWORD_FILE unreadable (run as root)"; exit 1; }

T_SNAPSHOTS=60; T_RESTORE=240; T_CHECK=240

snaps=""; rc=0
snaps="$(timeout "$T_SNAPSHOTS" restic snapshots --json 2>/dev/null)" || rc=$?
(( rc == 124 )) && { echo "V13 fail: 'restic snapshots' timed out after ${T_SNAPSHOTS}s (repository ${RESTIC_REPOSITORY:-?} not answering)"; exit 1; }
read -r sid stime count < <(printf '%s' "$snaps" | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except Exception:
    s = []
if not s:
    print("- - 0")
else:
    s.sort(key=lambda x: x.get("time", ""))
    print(s[-1].get("short_id") or s[-1].get("id", "")[:8], s[-1].get("time", "?")[:19], len(s))
')
[[ "$sid" != "-" && -n "$sid" ]] || { echo "V13 fail: no snapshots in ${RESTIC_REPOSITORY:-?} (or the repository does not answer; restic exit $rc)"; exit 1; }

scratch="$(mktemp -d "${ATLAS_STATE}/restore-test.XXXXXX" 2>/dev/null || mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
rc=0
timeout "$T_RESTORE" restic restore "$sid" --target "$scratch" --include "$canary" >/dev/null 2>"$scratch/.err" || rc=$?
if (( rc == 124 )); then
  echo "V13 fail: 'restic restore $sid --include $canary' timed out after ${T_RESTORE}s"; exit 1
elif (( rc != 0 )); then
  echo "V13 fail: restic restore $sid --include $canary failed (exit $rc): $(tr '\n' ' ' <"$scratch/.err" | cut -c1-200)"; exit 1
fi
restored="$scratch$canary"
[[ -f "$restored" ]] || { echo "V13 fail: $canary was not in snapshot $sid (restore produced nothing at $restored)"; exit 1; }
want="$(sha256sum "$canary" | cut -d' ' -f1)"
have="$(sha256sum "$restored" | cut -d' ' -f1)"
if [[ "$want" != "$have" ]]; then
  echo "V13 fail: sha256 mismatch for $canary: live $want, restored $have (snapshot $sid $stime)"; exit 1
fi
check_note="restic check ok"
if [[ "${ATLAS_PHASE:-}" == quarterly ]]; then
  check_note="structural check left to the unit's --read-data-subset run"
else
  rc=0
  timeout "$T_CHECK" restic check >/dev/null 2>"$scratch/.chk" || rc=$?
  if (( rc == 124 )); then
    echo "V13 fail: 'restic check' timed out after ${T_CHECK}s (restore and checksum were fine; snapshot $sid)"; exit 1
  elif (( rc != 0 )); then
    echo "V13 fail: restic check reported errors after a good restore (exit $rc): $(tr '\n' ' ' <"$scratch/.chk" | cut -c1-200)"; exit 1
  fi
fi
echo "restored $canary from snapshot $sid ($stime) with matching sha256 ${want:0:12}…; $check_note; $count snapshot(s) in ${RESTIC_REPOSITORY}"
exit 0
