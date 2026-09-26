#!/usr/bin/env bash
# verify/v13-restic.sh — V13: "restic backup completes and a restore verifies by checksum" (Sections 9.5, 21; Phase 2
# step 7; re-run quarterly by atlas-restic-check.service). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; one
# stdout line; never prompts; under 10 minutes; safe to re-run. Must run as root (the passphrase file is root 600).
# Usage: v13-restic.sh [CANARY=/srv/atlas/data/restic-canary.txt]
#   1. the repository answers (restic snapshots --json) and holds at least one snapshot;
#   2. the newest snapshot is restored into a scratch directory, restricted to CANARY (--include, VERIFIED flag);
#   3. sha256 of the restored file equals sha256 of the live file (the canary is written by step 7 and never changes
#      between the backup and this test; a changed file is reported as such, not as corruption);
#   4. `restic check` (structural, no data read; the quarterly unit adds --read-data-subset itself).
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

snaps="$(timeout 120 restic snapshots --json 2>/dev/null || true)"
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
[[ "$sid" != "-" && -n "$sid" ]] || { echo "V13 fail: no snapshots in ${RESTIC_REPOSITORY:-?} (or the repository does not answer)"; exit 1; }

scratch="$(mktemp -d "${ATLAS_STATE}/restore-test.XXXXXX" 2>/dev/null || mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
if ! timeout 300 restic restore "$sid" --target "$scratch" --include "$canary" >/dev/null 2>"$scratch/.err"; then
  echo "V13 fail: restic restore $sid --include $canary failed: $(tr '\n' ' ' <"$scratch/.err" | cut -c1-200)"; exit 1
fi
restored="$scratch$canary"
[[ -f "$restored" ]] || { echo "V13 fail: $canary was not in snapshot $sid (restore produced nothing at $restored)"; exit 1; }
want="$(sha256sum "$canary" | cut -d' ' -f1)"
have="$(sha256sum "$restored" | cut -d' ' -f1)"
if [[ "$want" != "$have" ]]; then
  echo "V13 fail: sha256 mismatch for $canary: live $want, restored $have (snapshot $sid $stime)"; exit 1
fi
if ! timeout 480 restic check >/dev/null 2>"$scratch/.chk"; then
  echo "V13 fail: restic check reported errors after a good restore: $(tr '\n' ' ' <"$scratch/.chk" | cut -c1-200)"; exit 1
fi
echo "restored $canary from snapshot $sid ($stime) with matching sha256 ${want:0:12}…; restic check ok; $count snapshot(s) in ${RESTIC_REPOSITORY}"
exit 0
