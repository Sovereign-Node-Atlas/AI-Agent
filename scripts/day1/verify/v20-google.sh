#!/usr/bin/env bash
# verify/v20-google.sh — V20: Google OAuth completed for both accounts; Gmail, Calendar and Drive reachable
# (Sections 13, 17 Phase 2 step 6c, 21). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; one stdout line;
# never prompts (phase2/google_oauth.py verify refreshes a stored token but never opens the browser flow).
# Pass only when every account in GOOGLE_ACCOUNTS answered all three APIs with its own address, AND the proof ran as
# the account that consumes the tokens at run time: when this script runs as root the helper is executed as atlas
# (runuser), so a token atlas cannot open (a root-only /etc/atlas/secrets, CONVENTIONS §2 vs the atlas-owned google/
# subdirectory; the agreed mode is root:atlas 750, see phase2/06c-google-oauth.sh "SECRETS DIRECTORY") fails here, at
# the gate, not in Phase 3; the failure text names the directory's mode, owner and last change time so the step that
# flipped it can be found. The coreutils `timeout` sits INSIDE the runuser/env chain (fix round 2: `timeout` execs its
# argument and cannot run a shell function; placed outside it printed "failed to run command 'as_consumer'" into
# /dev/null and every account was recorded as "no answer").
# The inbox copy of the OAuth client JSON must be gone (step 6c shreds it after V20 first passes; §7.2: no secret
# inside /srv/atlas).
# Usage: v20-google.sh [TOKEN_DIR=/etc/atlas/secrets/google] [VENV=/opt/atlas/venv]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

token_dir="${1:-$ATLAS_ETC/secrets/google}"
venv="${2:-$ATLAS_OPT/venv}"
helper="$ATLAS_DAY1_DIR/phase2/google_oauth.py"
entry="${ATLAS_ENTRY:-./atlas-day1.sh}"
accounts="$(awk -F= '$1=="GOOGLE_ACCOUNTS" {sub(/^[^=]*=/, ""); gsub(/"/, ""); print; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)"
[[ -n "$accounts" ]] || { echo "V20 fail: GOOGLE_ACCOUNTS not set in $ATLAS_ETC/atlas.env"; exit 1; }
[[ -x "$venv/bin/python" ]] || { echo "V20 fail: $venv/bin/python missing"; exit 1; }
[[ -f "$helper" ]] || { echo "V20 fail: $helper missing"; exit 1; }
proxy_env

as_consumer() {   # the atlas account when root (the orchestrator's identity), the caller otherwise
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    runuser -u "$ATLAS_SVC_USER" -- env HOME="$(getent passwd "$ATLAS_SVC_USER" | cut -d: -f6)" \
      HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" NO_PROXY="${NO_PROXY:-}" "$@"
  else
    "$@"
  fi
}
who="$(id -un)"
[[ "${EUID:-$(id -u)}" -eq 0 ]] && who="$ATLAS_SVC_USER"

parts=(); failed=()
for acct in $accounts; do
  email="${acct%%:*}"; tag="${acct##*:}"
  tokf="$token_dir/$email.json"
  if [[ ! -f "$tokf" ]]; then
    failed+=("$tag $email: token $tokf absent"); continue
  fi
  perm="$(stat -c '%a %U' "$tokf")"
  [[ "$perm" == "600 atlas" ]] || failed+=("$tag $email: token is $perm, want 600 atlas")
  if ! as_consumer test -r "$tokf"; then
    secrets_dir="$(dirname "$(dirname "$tokf")")"
    failed+=("$tag $email: $who cannot read $tokf ($(stat -c '%A %U:%G, changed %y' "$secrets_dir") on $secrets_dir blocks traversal; every writer must set it root:atlas 750 (phase2/09b-vault.sh:119 still writes 700), see phase2/06c-google-oauth.sh)")
    continue
  fi
  # runuser -> env -> timeout -> python: every link execs an external program (a shell function cannot follow timeout).
  out="$(as_consumer timeout 240 "$venv/bin/python" "$helper" verify --token "$tokf" --email "$email" --owner atlas 2>/dev/null || true)"
  json="$(printf '%s\n' "$out" | grep -E '^\{' | tail -n1)"
  line="$(python3 -c '
import json, sys
tag, email = sys.argv[1:3]
raw = sys.stdin.read().strip()
d = json.loads(raw) if raw else {}
if d.get("ok"):
    print(f"OK {tag} {email}: gmail {d[\"gmail_labels\"]} labels, {d[\"calendars\"]} calendar(s), drive {d[\"drive_user\"]}")
else:
    print(f"FAIL {tag} {email}: {d.get(\"error\", \"no answer from google_oauth.py verify\")}")' "$tag" "$email" <<<"$json" 2>/dev/null)" \
    || line="FAIL $tag $email: could not parse the helper's answer"
  case "$line" in
    OK\ *) parts+=("${line#OK }") ;;
    *) failed+=("${line#FAIL }") ;;
  esac
done

inbox="$ATLAS_SRV/staging/inbox/google-oauth-client.json"
[[ -e "$inbox" ]] && failed+=("plain-text OAuth client still at $inbox (step 6c shreds it after V20 passes; §7.2)")

join() { local out="" x; for x in "$@"; do out+="${out:+; }$x"; done; printf '%s' "$out"; }
if (( ${#failed[@]} > 0 )); then
  echo "V20 fail: $(join "${failed[@]}")${parts[*]:+; ok: $(join "${parts[@]}")}; re-run: sudo $entry phase2 --force 06c"
  exit 1
fi
echo "$(join "${parts[@]}") (proof ran as $who)"
exit 0
