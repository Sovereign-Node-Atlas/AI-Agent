#!/usr/bin/env bash
# verify/v23-cloudflare-token.sh — V23: Cloudflare token relocated to a mode-600 environment file and CLOUDFLARE.txt
# deleted (Sections 12.3, 17 Phase 2 step 6b, 21; R7). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; one
# stdout line; never prompts; safe to re-run (read-only API calls only).
#
# Checks: $ATLAS_ETC/secrets/cloudflare.env exists, mode 600, owner atlas-ddns:atlas-ddns, resolves outside
# $ATLAS_SRV, outside any git worktree, outside restic's include set ($ATLAS_ETC/restic.include when present; absent
# before step 7 has run, which is reported but not a fail), CF_API_TOKEN is a scoped token (never the Global API Key)
# and GET /user/tokens/verify says active, CF_ZONE_ID is set, and `find` under the Principal's home reports zero
# CLOUDFLARE.txt files (the count found is printed).
# Usage: v23-cloudflare-token.sh [PRINCIPAL_USER] [CLOUDFLARE_TXT]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

principal="${1:-$(awk -F= '$1=="PRINCIPAL_USER" {print $2; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)}"
cf_txt="${2:-$(awk -F= '$1=="CLOUDFLARE_TXT" {print $2; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)}"
[[ -n "$principal" ]] || { echo "V23 fail: PRINCIPAL_USER unknown (argument or $ATLAS_ETC/atlas.env)"; exit 1; }
home="$(getent passwd "$principal" | cut -d: -f6)"
[[ -d "$home" ]] || { echo "V23 fail: home directory of $principal not found"; exit 1; }
[[ -n "$cf_txt" ]] || cf_txt="$home/CLOUDFLARE.txt"

envf="$ATLAS_ETC/secrets/cloudflare.env"
[[ -f "$envf" ]] || { echo "V23 fail: $envf does not exist"; exit 1; }
problems=()
mode="$(stat -c '%a' "$envf")"; owner="$(stat -c '%U' "$envf")"; group="$(stat -c '%G' "$envf")"
[[ "$mode" == 600 ]] || problems+=("mode $mode (want 600)")
[[ "$owner" == atlas-ddns && "$group" == atlas-ddns ]] || problems+=("owner $owner:$group (want atlas-ddns:atlas-ddns)")
real="$(readlink -f "$envf")"
[[ "$real" != "$ATLAS_SRV"/* ]] || problems+=("inside $ATLAS_SRV")
if command -v git >/dev/null 2>&1 && git -C "$(dirname "$real")" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  problems+=("inside a git worktree")
fi
inc="$ATLAS_ETC/restic.include"
restic_note="restic.include not written yet (step 7)"
if [[ -s "$inc" ]]; then
  restic_note="outside restic's include set"
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(tr -d '[:space:]' <<<"$line")"
    [[ -n "$line" ]] || continue
    case "$real" in
      "$line"|"$line"/*) problems+=("covered by restic include '$line'"); restic_note="INSIDE restic's include set" ;;
    esac
  done <"$inc"
fi
token="$(awk -F= '$1=="CF_API_TOKEN" {print $2; exit}' "$envf")"
zone_id="$(awk -F= '$1=="CF_ZONE_ID" {print $2; exit}' "$envf")"
[[ -n "$token" ]] || problems+=("CF_API_TOKEN empty")
[[ "$token" =~ ^[0-9a-f]{37}$ ]] && problems+=("CF_API_TOKEN looks like the Global API Key (37 hex)")
[[ -n "$token" && ! "$token" =~ ^[0-9a-f]{37}$ && ! "$token" =~ ^[A-Za-z0-9_-]{40}$ ]] && problems+=("CF_API_TOKEN is not a 40-char API token")
[[ "$zone_id" =~ ^[0-9a-f]{32}$ ]] || problems+=("CF_ZONE_ID not set to a 32-hex zone id")

# Read-only API proof (UNVERIFIED endpoint name, platform.md item 10; a non-JSON answer is a fail, not ignored).
token_state="token unverified"
if [[ -n "$token" ]] && command -v jq >/dev/null 2>&1; then
  proxy_env
  ans="$(curl -sS --max-time 30 -H "Authorization: Bearer $token" https://api.cloudflare.com/client/v4/user/tokens/verify 2>/dev/null || true)"
  if jq -e '.success == true and .result.status == "active"' <<<"$ans" >/dev/null 2>&1; then
    token_state="scoped token active (/user/tokens/verify)"
  else
    problems+=("/user/tokens/verify did not report active: ${ans:0:80}")
  fi
fi

# CLOUDFLARE.txt must be gone: the named path and any file of that name under the Principal's home.
count="$(find "$home" -xdev -type f -name 'CLOUDFLARE.txt' 2>/dev/null | wc -l | tr -d ' ')"
[[ -e "$cf_txt" ]] && { problems+=("$cf_txt still exists"); }
(( count == 0 )) || problems+=("$count CLOUDFLARE.txt file(s) found under $home")

if (( ${#problems[@]} > 0 )); then
  echo "V23 fail: $(IFS='; '; echo "${problems[*]}") (env file $envf $mode $owner:$group; CLOUDFLARE.txt count under $home: $count)"
  exit 1
fi
echo "$envf 600 atlas-ddns:atlas-ddns outside $ATLAS_SRV and git, $restic_note; $token_state; zone id set; CLOUDFLARE.txt found under $home: $count"
exit 0
