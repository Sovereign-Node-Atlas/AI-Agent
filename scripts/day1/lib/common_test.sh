#!/usr/bin/env bash
# lib/common_test.sh — self-test for lib/common.sh. Needs no root and no network: every root is redirected to a
# temp directory. Run: bash scripts/day1/lib/common_test.sh   (exit 0 = every case passed).

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export ATLAS_ETC="$TMP/etc" ATLAS_STATE="$TMP/state" ATLAS_OPT="$TMP/opt" ATLAS_SRV="$TMP/srv"
export ATLAS_PHASE=phasetest ATLAS_ENTRY=./atlas-day1.sh

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"
# The library turns on errexit; the test harness must survive failing cases, so switch it off here and run every
# case that is expected to fail inside a subshell.
set +e
trap - ERR

pass_n=0 fail_n=0
ok()   { pass_n=$((pass_n + 1)); echo "PASS  $*"; }
bad()  { fail_n=$((fail_n + 1)); echo "FAIL  $*"; }
check() { # check DESCRIPTION CONDITION...
  local desc="$1"; shift
  if "$@"; then ok "$desc"; else bad "$desc"; fi
}
quiet() { "$@" >/dev/null 2>&1; }

# --- run_step --------------------------------------------------------------------------------------------------------
calls=0
step_good() { calls=$((calls + 1)); true; }
step_bad()  { false; }

quiet run_step phasetest 01 step_good
check "run_step: marker created on success" test -e "$ATLAS_STATE/done/phasetest.01"
check "run_step: ran the function once" test "$calls" -eq 1
quiet run_step phasetest 01 step_good
check "run_step: second run skips (function not re-run)" test "$calls" -eq 1
( run_step phasetest 02 step_bad ) >/dev/null 2>&1; rc=$?
check "run_step: failing step returns non-zero" test "$rc" -ne 0
check "run_step: failing step leaves no marker" test ! -e "$ATLAS_STATE/done/phasetest.02"
( ATLAS_DRY_RUN=1 run_step phasetest 03 step_good ) >/dev/null 2>&1
check "run_step: dry-run writes no marker" test ! -e "$ATLAS_STATE/done/phasetest.03"

# --- run_phase_steps ordering (05 before 05b before 06) ---------------------------------------------------------------
sd="$TMP/steps"; mkdir -p "$sd"
printf 'step_05() { echo 05 >>"%s/order"; }\n' "$TMP" >"$sd/05-postboot.sh"
printf 'step_05b() { echo 05b >>"%s/order"; }\n' "$TMP" >"$sd/05b-desktop.sh"
printf 'step_06() { echo 06 >>"%s/order"; }\n' "$TMP" >"$sd/06-docker.sh"
printf 'step_04() { echo 04 >>"%s/order"; }\n' "$TMP" >"$sd/04-system.sh"
quiet run_phase_steps phaseorder "$sd"
check "run_phase_steps: byte order 04 05 05b 06" test "$(tr '\n' ' ' <"$TMP/order")" = "04 05 05b 06 "

# --- record_v ---------------------------------------------------------------------------------------------------------
msg='he said "hi" \ back\slash and a	tab'
quiet record_v V2 pass "$msg"
line="$(tail -n1 "$ATLAS_STATE/verify.jsonl")"
check "record_v: line is strict JSON with the message intact" \
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert d["msg"]==sys.argv[2], d["msg"]; assert d["id"]=="V2" and d["result"]=="pass" and d["phase"]=="phasetest" and "ts" in d' "$line" "$msg"
( record_v V2 bogus x ) >/dev/null 2>&1; rc=$?
check "record_v: rejects an invalid RESULT" test "$rc" -ne 0

# --- run_verify ------------------------------------------------------------------------------------------------------
# The verify dir belongs to another writer; use a private ATLAS_DAY1_DIR for the stubs instead of touching it.
fake_day1="$TMP/day1"; mkdir -p "$fake_day1/verify"
for code in 0 1 2 3 7; do
  printf '#!/usr/bin/env bash\necho "stub says %s"\nexit %s\n' "$code" "$code" >"$fake_day1/verify/stub$code.sh"
  chmod +x "$fake_day1/verify/stub$code.sh"
done
verify_rc() { ( ATLAS_DAY1_DIR="$fake_day1" run_verify "$1" "$2" >/dev/null 2>&1 ); }
latest() { python3 -c '
import json,sys
recs=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
r=[x for x in recs if x["id"]==sys.argv[2]][-1]
print(r["result"]+"|"+r["msg"])' "$ATLAS_STATE/verify.jsonl" "$1"; }

verify_rc V5 stub0.sh; check "run_verify: exit 0 -> pass" test "$(latest V5)" = "pass|stub says 0"
verify_rc V6 stub1.sh; check "run_verify: exit 1 -> fail" test "$(latest V6)" = "fail|stub says 1"
verify_rc V7 stub2.sh; check "run_verify: exit 2 -> deferred" test "$(latest V7)" = "deferred|stub says 2"
verify_rc V1 stub3.sh; check "run_verify: exit 3 -> info" test "$(latest V1)" = "info|stub says 3"
verify_rc V8 stub7.sh; check "run_verify: exit 7 -> fail with 'exit 7'" test "$(latest V8)" = "fail|exit 7: stub says 7"
( ATLAS_DAY1_DIR="$fake_day1" run_verify V6 stub1.sh >/dev/null 2>&1 ); rc=$?
check "run_verify: returns 1 on fail" test "$rc" -eq 1
( ATLAS_DAY1_DIR="$fake_day1" run_verify V5 stub0.sh >/dev/null 2>&1 ); rc=$?
check "run_verify: returns 0 on pass" test "$rc" -eq 0

# --- gate -------------------------------------------------------------------------------------------------------------
( gate 1 V2 V5 -- V1 ) >/dev/null 2>&1; rc=$?
check "gate: all required pass -> return 0" test "$rc" -eq 0
check "gate: marker written on PASS" test -e "$ATLAS_STATE/done/phase1.gate"
out="$( gate phase1 V2 V5 -- V1 2>/dev/null )"
check "gate: prints PHASE 1 GATE: PASS" grep -q '^PHASE 1 GATE: PASS' <<<"$out"
check "gate: prints the next command" grep -q '^Next: sudo ./atlas-day1.sh phase2' <<<"$out"
check "gate: optional info row printed" grep -q '^V1 *info' <<<"$out"
( gate 1 V2 V6 ) >/dev/null 2>&1; rc=$?
check "gate: required fail -> return 1" test "$rc" -eq 1
check "gate: marker removed on FAIL" test ! -e "$ATLAS_STATE/done/phase1.gate"
( gate 2 V2 V7 ) >/dev/null 2>&1; rc=$?
check "gate: deferred does not block" test "$rc" -eq 0
( gate 3 V2 V19 ) >/dev/null 2>&1; rc=$?
check "gate: missing required id -> return 1" test "$rc" -eq 1
out="$( gate 3 V2 V19 2>/dev/null )"
check "gate: missing id shown as missing" grep -q '^V19 *missing' <<<"$out"
check "gate: FAIL line names the blocker" grep -q '^PHASE 3 GATE: FAIL (V19=missing)' <<<"$out"
# V4 per engine: latest per engine prefix, any fail blocks
quiet record_v V4 pass "gpt-oss-120b: K q4_0 V q4_0"
quiet record_v V4 fail "meditron-70b: kv fallback"
( gate 3 V4 ) >/dev/null 2>&1; rc=$?
check "gate: V4 per-engine fail blocks" test "$rc" -eq 1
quiet record_v V4 pass "meditron-70b: K q8_0 V q8_0"
( gate 3 V4 ) >/dev/null 2>&1; rc=$?
check "gate: V4 latest per engine pass -> return 0" test "$rc" -eq 0

# --- ensure_line / ensure_kv ------------------------------------------------------------------------------------------
f="$TMP/lines.txt"
ensure_line "$f" "alpha=1"; ensure_line "$f" "alpha=1"; ensure_line "$f" "beta 2"
check "ensure_line: idempotent" test "$(wc -l <"$f")" -eq 2
kv="$TMP/kv.env"
printf '# comment\nKEY=old\nOTHER=x\n' >"$kv"
ensure_kv "$kv" KEY 'new "value" \with\backslash'
ensure_kv "$kv" KEY 'new "value" \with\backslash'
ensure_kv "$kv" NEWKEY 42
check "ensure_kv: replaces the existing key in place" test "$(grep -c '^KEY=' "$kv")" -eq 1
check "ensure_kv: value written verbatim" test "$(grep '^KEY=' "$kv")" = 'KEY=new "value" \with\backslash'
check "ensure_kv: appends a new key" test "$(grep '^NEWKEY=' "$kv")" = "NEWKEY=42"
check "ensure_kv: keeps comments and other keys" test "$(head -n1 "$kv")" = "# comment" -a "$(grep -c '^OTHER=x' "$kv")" -eq 1
check "ensure_kv: idempotent line count" test "$(wc -l <"$kv")" -eq 4

# --- render_template --------------------------------------------------------------------------------------------------
tpl="$TMP/unit.tpl"
# shellcheck disable=SC2016  # the template must contain literal $VAR references for render_template to expand
printf 'User=${SVC}\nPort=$PORT\nKeep=$UNTOUCHED\n' >"$tpl"
export SVC=atlas PORT=8800 UNTOUCHED=literal
render_template -m 600 "$tpl" "$TMP/out/unit.txt" SVC PORT
check "render_template: named variables substituted" test "$(sed -n '1p;2p' "$TMP/out/unit.txt" | tr '\n' ' ')" = "User=atlas Port=8800 "
# shellcheck disable=SC2016  # the expected output is the literal, unexpanded reference
check "render_template: unnamed variable left alone" test "$(sed -n '3p' "$TMP/out/unit.txt")" = 'Keep=$UNTOUCHED'
check "render_template: mode applied" test "$(stat -c %a "$TMP/out/unit.txt")" = "600"
( render_template "$tpl" "$TMP/out/x" SVC NOPE_UNSET ) >/dev/null 2>&1; rc=$?
check "render_template: unset variable dies" test "$rc" -ne 0

# --- retry -------------------------------------------------------------------------------------------------------------
cnt="$TMP/retry.count"; : >"$cnt"
flaky() { echo x >>"$cnt"; [ "$(wc -l <"$cnt")" -ge 3 ]; }
start=$SECONDS
retry 5 flaky >/dev/null 2>&1; rc=$?
elapsed=$(( SECONDS - start ))
check "retry: succeeds after two failures" test "$rc" -eq 0 -a "$(wc -l <"$cnt")" -eq 3
check "retry: backed off 2s+4s (>=6s)" test "$elapsed" -ge 6
( retry 2 false ) >/dev/null 2>&1; rc=$?
check "retry: exhausts and returns the failure" test "$rc" -ne 0

# --- parse_common_args -----------------------------------------------------------------------------------------------
( parse_common_args --dry-run; [ "$ATLAS_DRY_RUN" = 1 ] ); rc=$?
check "parse_common_args: --dry-run sets ATLAS_DRY_RUN" test "$rc" -eq 0
( parse_common_args --run; [ "$ATLAS_IN_UNIT" = 1 ] ); rc=$?
check "parse_common_args: --run sets ATLAS_IN_UNIT" test "$rc" -eq 0
( parse_common_args --force 01 ) >/dev/null 2>&1
check "parse_common_args: --force clears the marker" test ! -e "$ATLAS_STATE/done/phasetest.01"
out="$( parse_common_args --status 2>/dev/null )"; rc=$?
check "parse_common_args: --status exits 0" test "$rc" -eq 0
check "parse_common_args: --status prints the verify table" grep -q '^V2 *pass' <<<"$out"
( parse_common_args --bogus ) >/dev/null 2>&1; rc=$?
check "parse_common_args: unknown argument dies" test "$rc" -ne 0

# --- load_env (deterministic parts only: install on first run, die naming blank CONFIRM keys) -------------------------
( load_env ) >"$TMP/load_env.out" 2>&1; rc=$?
check "load_env: installs the example on first run" test -f "$ATLAS_ETC/atlas.env"
check "load_env: dies naming the blank CONFIRM keys" test "$rc" -ne 0
check "load_env: message names GOOGLE_ACCOUNTS WINDOWS_SHARE FAMILY_NAMES" grep -q 'GOOGLE_ACCOUNTS WINDOWS_SHARE FAMILY_NAMES' "$TMP/load_env.out"
ensure_kv "$ATLAS_ETC/atlas.env" GOOGLE_ACCOUNTS '"a@b.com:corporate c@d.com:estate"'
ensure_kv "$ATLAS_ETC/atlas.env" WINDOWS_SHARE 'not-a-share'
ensure_kv "$ATLAS_ETC/atlas.env" FAMILY_NAMES 'Rida'
( load_env ) >"$TMP/load_env.out" 2>&1; rc=$?
check "load_env: rejects a malformed WINDOWS_SHARE" test "$rc" -ne 0
check "load_env: WINDOWS_SHARE message" grep -q 'WINDOWS_SHARE must look like //host/share' "$TMP/load_env.out"

# --- notify / proxy_env without configuration --------------------------------------------------------------------------
out="$(notify "hello" 2>&1)"; rc=$?
check "notify: not configured -> logs and returns 0" test "$rc" -eq 0
check "notify: not configured message" grep -q 'notify: not configured' <<<"$out"
# The host running this test may itself sit behind a proxy; clear that so the cases see only proxy_env's work.
unset HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy HF_HUB_ENABLE_HF_TRANSFER
( proxy_env; [ -z "${https_proxy:-}" ] && [ -z "${HTTPS_PROXY:-}" ] ); rc=$?
check "proxy_env: no proxy.env -> nothing exported" test "$rc" -eq 0
printf 'HTTPS_PROXY=http://127.0.0.1:3128\n' >"$ATLAS_ETC/proxy.env"
( proxy_env; [ "${https_proxy:-}" = http://127.0.0.1:3128 ] && [ "${HTTP_PROXY:-}" = http://127.0.0.1:3128 ] \
    && [ "${HF_HUB_ENABLE_HF_TRANSFER:-}" = 0 ] && [ -n "${NO_PROXY:-}" ] && env | grep -q '^https_proxy=http://127.0.0.1:3128$' ); rc=$?
check "proxy_env: proxy.env exported (both cases, in the environment)" test "$rc" -eq 0

# --- summary ------------------------------------------------------------------------------------------------------------
echo
echo "common_test: $pass_n passed, $fail_n failed"
test "$fail_n" -eq 0
