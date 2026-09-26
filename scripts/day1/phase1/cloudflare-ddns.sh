#!/usr/bin/env bash
# phase1/cloudflare-ddns.sh — installed as /usr/local/sbin/atlas-ddns by phase1/07-remote.sh and run by
# atlas-ddns.timer as the atlas-ddns account (Section 12.2: "checked every few minutes, updated only on change").
#
# Environment (from /etc/atlas/secrets/cloudflare.env via EnvironmentFile=; CONVENTIONS.md §2):
#   CF_API_TOKEN     scoped token, Zone:DNS:Edit on the zone (Section 12.3)
#   CF_ZONE_NAME     e.g. sovereign-node.link
#   CF_ZONE_ID       the zone id; REQUIRED in practice because a Zone:DNS:Edit-only token cannot list zones
#                    (adjudicated conflict 3; GET /zones needs Zone:Zone:Read). When blank, GET /zones is attempted once.
#   CF_RECORD_NAME   e.g. vpn.sovereign-node.link (must be a DNS-only A record: WireGuard is UDP, never proxied)
#   HTTPS_PROXY      from /etc/atlas/proxy.env: every request goes through the allowlist proxy (§7.1)
#   DDNS_FORCE=1     ignore the cached last-ip and talk to the API even when the public IP is unchanged
# State: $STATE_DIRECTORY/last-ip (systemd StateDirectory=atlas-ddns -> /var/lib/atlas-ddns), read by verify/v05-wireguard.sh.
# Exit: 0 on success or no-op, 1 on any failure (the timer retries in 5 minutes; journalctl -u atlas-ddns shows why).
# This script is standalone on purpose: it runs sandboxed as a service account and must not depend on lib/common.sh.
set -euo pipefail

API="https://api.cloudflare.com/client/v4"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/atlas-ddns}"
STATE="$STATE_DIR/last-ip"

fail() { echo "atlas-ddns: $*" >&2; exit 1; }

for v in CF_API_TOKEN CF_ZONE_NAME CF_RECORD_NAME; do
  [[ -n "${!v:-}" ]] || fail "$v is not set (expected in /etc/atlas/secrets/cloudflare.env)"
done
command -v curl >/dev/null || fail "curl is not installed"
command -v jq >/dev/null || fail "jq is not installed"

auth=(-H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json")

# 1. Public IPv4. Three echo services, all on the allowlist; the first that answers with a plausible IPv4 wins.
public_ip() {
  local ip
  ip="$(curl -fsS -m 10 https://www.cloudflare.com/cdn-cgi/trace 2>/dev/null | awk -F= '$1=="ip"{print $2}' || true)"
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || ip="$(curl -fsS -m 10 https://api.ipify.org 2>/dev/null || true)"
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || ip="$(curl -fsS -m 10 -4 https://icanhazip.com 2>/dev/null | tr -d '[:space:]' || true)"
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  printf '%s\n' "$ip"
}
ip="$(public_ip)" || fail "could not determine the public IPv4 (proxy down, or www.cloudflare.com/api.ipify.org/icanhazip.com not allowlisted)"
case "$ip" in
  100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*)
    # RFC 6598 shared address space seen from the outside means CGNAT (R11): the port-forward cannot work.
    echo "atlas-ddns: WARNING public address $ip is in 100.64.0.0/10 (CGNAT): inbound WireGuard will not work (R11)" >&2 ;;
esac

if [[ "${DDNS_FORCE:-0}" != "1" && -f "$STATE" && "$(cat "$STATE")" == "$ip" ]]; then
  exit 0   # unchanged since the last successful update: no API call ("updated only on change")
fi

# 2. Zone id: from the env file, else one attempt at GET /zones (works only with Zone:Zone:Read on the token).
zone_id="${CF_ZONE_ID:-}"
if [[ -z "$zone_id" ]]; then
  zone_id="$(curl -fsS -m 20 "${auth[@]}" "$API/zones?name=$CF_ZONE_NAME&status=active" | jq -r '.result[0].id // empty' || true)"
  [[ -n "$zone_id" ]] || fail "CF_ZONE_ID is blank and GET /zones failed: the token has no Zone:Zone:Read. Put CF_ZONE_ID=<zone id from the Cloudflare dashboard Overview page> in /etc/atlas/secrets/cloudflare.env"
fi

# 3. The A record, then PATCH only the content (partial update; PUT would replace the whole record).
rec="$(curl -fsS -m 20 "${auth[@]}" "$API/zones/$zone_id/dns_records?type=A&name=$CF_RECORD_NAME")" \
  || fail "GET dns_records failed for $CF_RECORD_NAME (token scope, zone id, or proxy)"
rid="$(jq -r '.result[0].id // empty' <<<"$rec")"
cur="$(jq -r '.result[0].content // empty' <<<"$rec")"
proxied="$(jq -r '.result[0].proxied // empty' <<<"$rec")"
[[ -n "$rid" ]] || fail "A record $CF_RECORD_NAME does not exist in zone $CF_ZONE_NAME: create it once in the dashboard (DNS only, grey cloud)"
if [[ "$cur" != "$ip" || "$proxied" == "true" ]]; then
  curl -fsS -m 20 -X PATCH "${auth[@]}" "$API/zones/$zone_id/dns_records/$rid" \
       --data "{\"content\":\"$ip\",\"proxied\":false}" | jq -e '.success == true' >/dev/null \
    || fail "PATCH dns_records/$rid failed (token needs Zone:DNS:Edit on $CF_ZONE_NAME)"
  logger -t atlas-ddns "updated $CF_RECORD_NAME: ${cur:-none} -> $ip (proxied=false)" 2>/dev/null || true
  echo "atlas-ddns: updated $CF_RECORD_NAME: ${cur:-none} -> $ip"
fi
mkdir -p "$STATE_DIR"
printf '%s\n' "$ip" >"$STATE"
