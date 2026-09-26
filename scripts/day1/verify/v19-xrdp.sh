#!/usr/bin/env bash
# verify/v19-xrdp.sh — V19: XFCE reachable over xrdp from the Principal's Windows PC, refused from outside LAN and
# WireGuard (Sections 3.6, 17 step 5b, 21; R21).
#   (a) xrdp listens ONLY on the addresses given (ss -ltnp): any other :3389 listener is a fail; and ufw's default
#       deny with per-interface rules (phase1/04-system.sh) is what refuses everything outside LAN/WireGuard.
#   (b) waits up to WAIT_S for the Principal's session: an established TCP connection on 3389 plus a logind session
#       of PRINCIPAL_USER whose service is xrdp-sesman. No session in time -> exit 2 (deferred, the gate does not
#       block; the Principal retries with the hint), never a fail.
# Usage: v19-xrdp.sh PRINCIPAL_USER WAIT_S RERUN_HINT ADDR [ADDR...]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

user="${1:-}"; wait_s="${2:-540}"; hint="${3:-re-run: sudo ./atlas-day1.sh phase1 --force 05b}"; shift 3 || true
addrs=("$@")
[[ -n "$user" && ${#addrs[@]} -gt 0 ]] || { echo "usage: v19-xrdp.sh PRINCIPAL_USER WAIT_S RERUN_HINT ADDR..."; exit 1; }

systemctl is-active --quiet xrdp || { echo "V19 fail: xrdp.service is not active ($(systemctl is-active xrdp))"; exit 1; }
listeners="$(ss -ltnH 'sport = :3389' 2>/dev/null | awk '{print $4}' | sed -E 's/^\[?([^]]*)\]?:3389$/\1/' | sort -u)"
[[ -n "$listeners" ]] || { echo "V19 fail: nothing listens on 3389"; exit 1; }
bad=()
while read -r l; do
  [[ -n "$l" ]] || continue
  ok=0
  for a in "${addrs[@]}"; do [[ "$l" == "$a" ]] && ok=1; done
  (( ok )) || bad+=("$l")
done <<<"$listeners"
if (( ${#bad[@]} > 0 )); then
  echo "V19 fail: xrdp listens on unexpected address(es): ${bad[*]} (allowed: ${addrs[*]})"; exit 1
fi
bound="listening only on $(tr '\n' ' ' <<<"$listeners" | sed 's/ $//')"

deadline=$(( SECONDS + wait_s ))
while (( SECONDS < deadline )); do
  est="$(ss -tnH state established '( sport = :3389 )' 2>/dev/null | awk '{print $4}' | head -n1)"
  if [[ -n "$est" ]]; then
    while read -r sid _ suser _; do
      [[ "$suser" == "$user" ]] || continue
      svc="$(loginctl show-session "$sid" -p Service --value 2>/dev/null || true)"
      if [[ "$svc" == xrdp-sesman ]]; then
        echo "RDP session for $user via xrdp-sesman (logind session $sid) from $est; $bound"
        exit 0
      fi
    done < <(loginctl list-sessions --no-legend 2>/dev/null || true)
  fi
  sleep 5
done
echo "deferred: no RDP session for $user within ${wait_s}s; $bound; $hint"
exit 2
