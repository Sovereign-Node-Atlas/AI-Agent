#!/usr/bin/env bash
# verify/v20-google.sh — V20: Google OAuth completed for both accounts; Gmail, Calendar and Drive reachable
# (Sections 13, 17 Phase 2 step 6c, 21). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; one stdout line;
# never prompts (phase2/google_oauth.py verify refreshes a stored token but never opens the browser flow).
# Pass only when every account in GOOGLE_ACCOUNTS answered all three APIs with its own address.
# Usage: v20-google.sh [TOKEN_DIR=/etc/atlas/secrets/google] [VENV=/opt/atlas/venv]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

token_dir="${1:-$ATLAS_ETC/secrets/google}"
venv="${2:-$ATLAS_OPT/venv}"
helper="$ATLAS_DAY1_DIR/phase2/google_oauth.py"
accounts="$(awk -F= '$1=="GOOGLE_ACCOUNTS" {sub(/^[^=]*=/, ""); gsub(/"/, ""); print; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)"
[[ -n "$accounts" ]] || { echo "V20 fail: GOOGLE_ACCOUNTS not set in $ATLAS_ETC/atlas.env"; exit 1; }
[[ -x "$venv/bin/python" ]] || { echo "V20 fail: $venv/bin/python missing"; exit 1; }
[[ -f "$helper" ]] || { echo "V20 fail: $helper missing"; exit 1; }
proxy_env

parts=(); failed=()
for acct in $accounts; do
  email="${acct%%:*}"; tag="${acct##*:}"
  tokf="$token_dir/$email.json"
  if [[ ! -f "$tokf" ]]; then
    failed+=("$tag $email: token $tokf absent"); continue
  fi
  perm="$(stat -c '%a %U' "$tokf")"
  [[ "$perm" == "600 atlas" ]] || failed+=("$tag $email: token is $perm, want 600 atlas")
  out="$(timeout 240 "$venv/bin/python" "$helper" verify --token "$tokf" --email "$email" --owner atlas 2>/dev/null || true)"
  json="$(printf '%s\n' "$out" | grep -E '^\{' | tail -n1)"
  line="$(python3 - "$json" "$tag" "$email" <<'PY' 2>/dev/null
import json, sys
raw, tag, email = sys.argv[1:4]
d = json.loads(raw) if raw else {}
if d.get("ok"):
    print(f"OK {tag} {email}: gmail {d['gmail_labels']} labels, {d['calendars']} calendar(s), drive {d['drive_user']}")
else:
    print(f"FAIL {tag} {email}: {d.get('error', 'no answer from google_oauth.py verify')}")
PY
)" || line="FAIL $tag $email: could not parse the helper's answer"
  case "$line" in
    OK\ *) parts+=("${line#OK }") ;;
    *) failed+=("${line#FAIL }") ;;
  esac
done

if (( ${#failed[@]} > 0 )); then
  echo "V20 fail: $(IFS='; '; echo "${failed[*]}")${parts[*]:+; ok: $(IFS='; '; echo "${parts[*]}")}; re-run: sudo /opt/atlas/day1/atlas-day1.sh phase2 --force 06c"
  exit 1
fi
echo "$(IFS='; '; echo "${parts[*]}")"
exit 0
