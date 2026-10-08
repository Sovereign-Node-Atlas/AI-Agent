#!/usr/bin/env bash
# lib/common.sh — the shared library every Day 1 script sources first (CONVENTIONS.md §4).
#
# Usage from a driver or verify script:
#   # shellcheck source=lib/common.sh
#   source "$(dirname "$(readlink -f "$0")")/lib/common.sh"     # or ../lib/common.sh from a step file
#
# Roots (all overridable for tests, CONVENTIONS.md §4):
#   ATLAS_ETC    /etc/atlas           settings and secrets
#   ATLAS_STATE  /var/lib/atlas/day1  done markers, verify.jsonl, logs
#   ATLAS_OPT    /opt/atlas           the running copy of scripts/day1 and llama.cpp
#   ATLAS_SRV    /srv/atlas           the 8 TB data volume
#
# Other environment the library honours (set by callers, never required):
#   ATLAS_PHASE          phase name used in log file names, verify records and markers ("phase1".."phase4")
#   ATLAS_LOG_FILE       explicit log path (detached_phase exports it so the unit and the caller share one file)
#   ATLAS_LOG_TO_STDERR  "1" sends the console copy of log lines to stderr (verify scripts must keep stdout to one line)
#   ATLAS_DRY_RUN        "1" makes run_step print what it would do and run nothing (set by parse_common_args --dry-run)
#   ATLAS_ENTRY          the repository path of atlas-day1.sh, used when printing "the exact next command"
#   ATLAS_SVC_USER       service account for svc_user_run (default: atlas)
#   ATLAS_FORCED_STEPS   exported BY parse_common_args: the bare step ids given to --force, space-separated ("09b", "05b 07");
#                        step files may read it (phase2/09b-vault.sh does); detached_phase passes it into the unit
#   ATLAS_TEST_SECURE_BOOT  TEST ONLY (lib/common_test.sh): "on" or "off" replaces the firmware reading in
#                        _atlas_secure_boot_enabled so both load_env branches run on any CI host. Never set on the node:
#                        phase1/01-preflight.sh and verify/v02-tpm.sh read the firmware themselves and ignore it.
#
# Contracts this file defines that CONVENTIONS.md does not state (other writers code against these):
#   * $ATLAS_ETC/proxy.env  — written by Phase 1 step 4 (squid). Sourceable KEY=VALUE lines:
#                             HTTP_PROXY=http://127.0.0.1:3128  HTTPS_PROXY=...  NO_PROXY=...
#                             proxy_env exports them (both cases) plus HF_HUB_ENABLE_HF_TRANSFER=0. Absent file = no proxy yet.
#   * $ATLAS_STATE/done/phaseN.gate — written by gate on PASS, removed on FAIL; atlas-day1.sh refuses phase N+1 without it.
#   * run_phase_steps sources DIR/NN-*.sh in C-locale byte order and calls run_step PHASE <id> step_<id>, <id> = the
#     two-digit(+letter) prefix before the first dash, so 05 runs before 05b before 06. (Not sort -V: it puts 05b first.)
#   * gate accepts the phase as "1" or "phase1"; V4 rows are one per engine (latest record per "engine:" message prefix).

set -Eeuo pipefail

# ---------------------------------------------------------------------------------------------------------------------
# Roots and derived paths
# ---------------------------------------------------------------------------------------------------------------------
: "${ATLAS_ETC:=/etc/atlas}"
: "${ATLAS_STATE:=/var/lib/atlas/day1}"
: "${ATLAS_OPT:=/opt/atlas}"
: "${ATLAS_SRV:=/srv/atlas}"
: "${ATLAS_PHASE:=common}"
: "${ATLAS_DRY_RUN:=0}"
: "${ATLAS_SVC_USER:=atlas}"
export ATLAS_ETC ATLAS_STATE ATLAS_OPT ATLAS_SRV ATLAS_PHASE ATLAS_DRY_RUN ATLAS_SVC_USER

# The scripts/day1 directory that holds this lib/ (repo checkout or the /opt copy).
ATLAS_DAY1_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export ATLAS_DAY1_DIR

ATLAS_VERIFY_FILE="$ATLAS_STATE/verify.jsonl"
ATLAS_TODO_FILE="$ATLAS_STATE/todo.jsonl"      # the live ATLAS to-do list (inputs the Principal did not have on Day 1)
ATLAS_DONE_DIR="$ATLAS_STATE/done"
ATLAS_LOG_DIR="$ATLAS_STATE/logs"
export ATLAS_VERIFY_FILE ATLAS_DONE_DIR ATLAS_LOG_DIR

# Set when run_step is executing a step, so the ERR trap can name it.
ATLAS_CURRENT_STEP=""

# ---------------------------------------------------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------------------------------------------------
_atlas_ts() { date '+%Y-%m-%d %H:%M:%S'; }

# Resolve the log file lazily so a caller that sets ATLAS_PHASE after sourcing still gets <phase>-<date>.log.
_atlas_log_file() {
  if [[ -n "${ATLAS_LOG_FILE:-}" ]]; then
    printf '%s\n' "$ATLAS_LOG_FILE"
  else
    printf '%s/%s-%s.log\n' "$ATLAS_LOG_DIR" "$ATLAS_PHASE" "$(date '+%Y%m%d')"
  fi
}

_atlas_emit() {
  local level="$1"; shift
  local line
  line="$(_atlas_ts) [$ATLAS_PHASE] $level $*"
  if [[ "${ATLAS_LOG_TO_STDERR:-0}" == "1" || "$level" != "INFO" ]]; then
    printf '%s\n' "$line" >&2
  else
    printf '%s\n' "$line"
  fi
  local f
  f="$(_atlas_log_file)"
  # Non-root callers (tests, verify scripts run by hand) may not be able to write the state dir; never fail on that.
  if mkdir -p "$(dirname "$f")" 2>/dev/null; then
    printf '%s\n' "$line" >>"$f" 2>/dev/null || true
  fi
}

log()  { _atlas_emit INFO "$*"; }
warn() { _atlas_emit WARN "$*"; }
die()  { _atlas_emit FATAL "$*"; exit 1; }

# ERR trap: names the script, line and command that failed, and the step if run_step is active.
# $1 exit code, $2 source file, $3 line, $4 command (evaluated in the failing frame by the trap string below).
_atlas_on_err() {
  local rc="$1" src="$2" line="$3" cmd="$4"
  local where="${src}:${line}"
  [[ -n "$ATLAS_CURRENT_STEP" ]] && where="step $ATLAS_CURRENT_STEP, $where"
  _atlas_emit ERROR "command failed (exit $rc) at $where: $cmd"
}
# BASH_SOURCE[0] is unset at the top level of `bash -c` (no file), so fall back to $0 under `set -u`.
trap '_atlas_on_err "$?" "${BASH_SOURCE[0]:-$0}" "$LINENO" "$BASH_COMMAND"' ERR

require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "must run as root (Ubuntu 26.04 ships sudo-rs: use 'sudo $0 ...', never 'sudo -E')"
}

# Create the state layout; safe for non-root (silently skipped when not writable).
_atlas_state_init() {
  mkdir -p "$ATLAS_DONE_DIR" "$ATLAS_LOG_DIR" 2>/dev/null || true
}

# ---------------------------------------------------------------------------------------------------------------------
# Steps and markers
# ---------------------------------------------------------------------------------------------------------------------
# run_step PHASE STEP FUNC — idempotent step wrapper (CONVENTIONS.md §4).
run_step() {
  local phase="$1" step="$2" func="$3"
  _atlas_state_init
  local marker="$ATLAS_DONE_DIR/$phase.$step"
  if [[ -e "$marker" ]]; then
    log "skip $phase.$step ($func): done at $(cat "$marker" 2>/dev/null || echo '?')"
    return 0
  fi
  if [[ "$ATLAS_DRY_RUN" == "1" ]]; then
    log "DRY-RUN would run $phase.$step ($func)"
    return 0
  fi
  declare -F "$func" >/dev/null || die "step $phase.$step: function $func is not defined"
  log "start $phase.$step ($func)"
  ATLAS_CURRENT_STEP="$phase.$step"
  local rc=0
  # The step is called plainly, not inside `if`/`||`, so `set -e` stays active inside it and the first failing
  # command aborts the phase with the ERR trap naming the line. The explicit status check below is for callers
  # that run with errexit disabled (the self-test): a non-zero return must still leave no marker.
  "$func"
  # shellcheck disable=SC2181  # a plain call is required to keep errexit active inside the step; see above
  rc=$?
  ATLAS_CURRENT_STEP=""
  if (( rc != 0 )); then
    die "step $phase.$step ($func) failed with exit $rc; no marker written"
  fi
  date -Is >"$marker"
  log "done $phase.$step"
}

# run_phase_steps PHASE DIR — source DIR/NN-*.sh in byte (C locale) order and run each step_<id>.
# Section 17 ids are two digits plus an optional letter (01, 05, 05b, 06c), so a byte sort gives 04 < 05 < 05b < 06.
# `sort -V` was tested for this and is NOT used: GNU version sort orders 05b-desktop.sh BEFORE 05-postboot.sh.
run_phase_steps() {
  local phase="$1" dir="$2"
  [[ -d "$dir" ]] || die "run_phase_steps: no such directory $dir"
  local files=()
  mapfile -t files < <(find "$dir" -maxdepth 1 -type f -name '[0-9][0-9]*-*.sh' -printf '%f\n' | LC_ALL=C sort)
  (( ${#files[@]} > 0 )) || die "run_phase_steps: no NN-*.sh step files in $dir"
  local f id
  for f in "${files[@]}"; do
    id="${f%%-*}"
    # shellcheck disable=SC1090  # step files are discovered at run time; each defines only step_<id>()
    source "$dir/$f"
    run_step "$phase" "$id" "step_$id"
  done
}

# ---------------------------------------------------------------------------------------------------------------------
# Verification records
# ---------------------------------------------------------------------------------------------------------------------
# record_v ID RESULT MSG — append one strict-JSON line to verify.jsonl. JSON is produced by python3's json module so
# quotes, backslashes and control characters in MSG are always escaped correctly.
record_v() {
  local id="$1" result="$2" msg="${3:-}"
  case "$result" in pass|fail|deferred|info) ;; *) die "record_v $id: RESULT must be pass|fail|deferred|info, got '$result'" ;; esac
  # V items (V7, V3a) plus the two recorded-only row families Section 21 (v0.3.2 scope note) names:
  # T-<tool> for Phase 2 step 6 soft installs and P4-wheels for the Phase 4 wheel-index pre-flight.
  [[ "$id" =~ ^(V[0-9]+[a-z]?|T-[a-z0-9-]+|P4-wheels)$ ]] || die "record_v: ID must look like V7, V3a, T-<tool> or P4-wheels, got '$id'"
  _atlas_state_init
  command -v python3 >/dev/null || die "record_v needs python3"
  local line
  line="$(python3 -c '
import json, sys
print(json.dumps({"ts": sys.argv[1], "phase": sys.argv[2], "id": sys.argv[3], "result": sys.argv[4], "msg": sys.argv[5]}))
' "$(date -Is)" "$ATLAS_PHASE" "$id" "$result" "$msg")"
  printf '%s\n' "$line" >>"$ATLAS_VERIFY_FILE"
  log "verify $id=$result: $msg"
}

# ask VAR PROMPT [secret] — one plain question on the terminal, 300 s. No terminal (systemd-run, nohup) or no answer
# leaves VAR empty, and the caller records a to-do instead of stopping: the Principal's rule (v0.3.3) is that a missing
# input never stops a phase. ATLAS_ASK_ANSWER, when set, is the answer (self-test hook; also handy for scripted runs).
ask() {
  local __var="$1" prompt="$2" secret="${3:-}" __ans=""
  if [[ -n "${ATLAS_ASK_ANSWER+x}" ]]; then
    __ans="$ATLAS_ASK_ANSWER"
  elif ( : </dev/tty ) 2>/dev/null; then
    if [[ "$secret" == secret ]]; then
      read -r -s -t 300 -p "$prompt" __ans </dev/tty >/dev/tty || __ans=""
      echo >/dev/tty
    else
      read -r -t 300 -p "$prompt" __ans </dev/tty >/dev/tty || __ans=""
    fi
  fi
  printf -v "$__var" '%s' "$__ans"
}

# todo_add ID TITLE [DETAIL] — append an item to the live ATLAS to-do list ($ATLAS_TODO_FILE, one JSON object per line;
# the latest line per ID wins, todo_done closes one). Logged as a warning so it is visible in the phase log too.
todo_add() {
  local id="$1" title="$2" detail="${3:-}"
  [[ "$id" =~ ^[a-z0-9][a-z0-9.-]*$ ]] || die "todo_add: ID must be lowercase [a-z0-9.-], got '$id'"
  _atlas_state_init
  command -v python3 >/dev/null || die "todo_add needs python3"
  python3 -c '
import json, sys
print(json.dumps({"ts": sys.argv[1], "phase": sys.argv[2], "id": sys.argv[3], "title": sys.argv[4], "detail": sys.argv[5], "done": False}))
' "$(date -Is)" "$ATLAS_PHASE" "$id" "$title" "$detail" >>"$ATLAS_TODO_FILE"
  warn "TO-DO [$id]: $title — recorded for the live ATLAS; the phase continues"
}

# todo_done ID — close a to-do item (the input arrived later, or a re-run with --force picked it up).
todo_done() {
  local id="$1"
  _atlas_state_init
  python3 -c '
import json, sys
print(json.dumps({"ts": sys.argv[1], "phase": sys.argv[2], "id": sys.argv[3], "title": "", "detail": "", "done": True}))
' "$(date -Is)" "$ATLAS_PHASE" "$id" >>"$ATLAS_TODO_FILE"
  log "to-do $id closed"
}

# atlas_tpm_check — returns 0 when systemd sees exactly one TPM (what tpm2-device=auto needs: two tpmrm devices make it
# refuse with ENOTUNIQ); otherwise prints the reason, with systemd-cryptenroll's own words, and returns 1. "TPM2 support
# is not installed" means a libtss2 library is missing (systemd 259 dlopen()s libtss2-esys, -rc and -mu; doc S42).
atlas_tpm_check() {
  local l n
  l="$(systemd-cryptenroll --tpm2-device=list 2>&1 || true)"
  if grep -qi 'support is not installed' <<<"$l"; then
    echo "systemd's TPM2 support is not installed: it needs libtss2-esys, libtss2-rc and libtss2-mu (sudo apt-get install libtss2-rc0t64). systemd-cryptenroll says: $(tr '\n' ' ' <<<"$l")"; return 1
  fi
  # ATLAS_TEST_TPM_PRESENT=1 stands in for the device node in lib/common_test.sh only (like ATLAS_TEST_SECURE_BOOT).
  [[ -c /dev/tpmrm0 || "${ATLAS_TEST_TPM_PRESENT:-}" == 1 ]] || { echo "/dev/tpmrm0 absent: enable the fTPM in the BIOS (Section 3.2) and reboot"; return 1; }
  n="$(grep -c '^/dev/tpmrm' <<<"$l" || true)"
  if (( n != 1 )); then
    echo "$n TPM device(s) listed by systemd-cryptenroll --tpm2-device=list; tpm2-device=auto needs exactly one (fTPM enabled? fTPM and a discrete TPM both active?): $(tr '\n' ' ' <<<"$l")"; return 1
  fi
}

# todo_is_open ID — true when the latest record for ID is an open to-do (so a caller closes it once, not every run).
todo_is_open() {
  [[ -s "${ATLAS_TODO_FILE:-}" ]] || return 1
  python3 - "$ATLAS_TODO_FILE" "$1" <<'PY'
import json, sys
state = None
for line in open(sys.argv[1], encoding="utf-8"):
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    if r.get("id") == sys.argv[2]:
        state = not r.get("done")
sys.exit(0 if state else 1)
PY
}

# _atlas_rdp_narrowed — true when ufw is active and no 3389 rule on LAN_IFACE admits the whole LAN subnet any more.
_atlas_rdp_narrowed() {
  command -v ufw >/dev/null 2>&1 || return 1
  local st net
  st="$(ufw status 2>/dev/null)" || return 1
  [[ "$st" == *"Status: active"* ]] || return 1
  net="$(python3 -c 'import ipaddress, sys; print(ipaddress.ip_network(sys.argv[1], strict=False))' "$LAN_CIDR" 2>/dev/null)" || return 1
  ! grep -qE "^3389/tcp on ${LAN_IFACE}[[:space:]]+ALLOW( IN)?[[:space:]]+${net//./\\.}([[:space:]]|\$)" <<<"$st"
}

# atlas_rdp_sources_check LAN_CIDR "ENTRY..." — every entry must be a canonical dotted quad (octets 0..255, no leading
# zeros) with an optional DECIMAL prefix 0..32 written without leading zeros (ufw passes the prefix unchanged to
# iptables, which reads a leading zero as octal, and a dotted suffix as a netmask that Python would read as a hostmask),
# must have no host bits set (ufw would silently widen 192.168.1.20/24 to the whole /24), must lie inside the LAN
# without covering all of it, and must not touch the WireGuard bridge network (VPN sessions arrive masqueraded as its
# address on another interface, so a LAN rule for it never matches). Prints the offending entries and returns 1.
atlas_rdp_sources_check() {
  python3 - "$1" "$2" "${ATLAS_WG_BRIDGE_NET:-10.42.42.0/24}" <<'PY'
import ipaddress, re, sys
try:
    lan = ipaddress.ip_network(sys.argv[1], strict=False)
except ValueError as e:
    print(f"LAN_CIDR {sys.argv[1]!r} is not a network ({e})"); sys.exit(1)
bridge = ipaddress.ip_network(sys.argv[3], strict=False)
bad = []
for tok in sys.argv[2].split():
    if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,3}){3}(/(0|[1-9][0-9]?))?", tok):
        bad.append(f"{tok} (write an address or address/prefix with a decimal prefix, e.g. 192.168.1.20 or 192.168.1.16/28)"); continue
    try:
        net = ipaddress.IPv4Network(tok, strict=True)
    except ValueError as e:
        hint = f"; write {tok.split('/')[0]} for one PC" if "host bits" in str(e) else ""
        bad.append(f"{tok} ({e}{hint})"); continue
    if lan.version != 4 or not net.subnet_of(lan):
        bad.append(f"{tok} (not inside the LAN {lan})")
    elif net.overlaps(bridge):
        bad.append(f"{tok} (the WireGuard bridge {bridge}: VPN sessions are allowed separately)")
if not bad:
    nets = [ipaddress.IPv4Network(t, strict=True) for t in sys.argv[2].split()]
    if any(lan.subnet_of(n) for n in ipaddress.collapse_addresses(nets)):
        bad.append("together these admit the whole LAN, which narrows nothing (leave RDP_ALLOW_FROM blank for that)")
if bad:
    print("; ".join(bad)); sys.exit(1)
PY
}

# todo_list — the open items, latest record per ID, as a table (atlas-day1.sh status; the Phase gates print it too).
todo_list() {
  [[ -s "${ATLAS_TODO_FILE:-}" ]] || { echo "  (no to-do items)"; return 0; }
  python3 - "$ATLAS_TODO_FILE" <<'PY'
import json, sys
latest = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except json.JSONDecodeError:
        continue
    latest[r["id"]] = r
rows = [r for r in latest.values() if not r.get("done")]
if not rows:
    print("  (no open to-do items)")
for r in rows:
    print(f"  {r['id']:<28} {r['title']}")
    if r.get("detail"):
        print(f"  {'':<28}   {r['detail']}")
PY
}

# run_verify ID SCRIPT [ARGS] — run verify/SCRIPT (§5 contract: exit 0/1/2/3, one stdout line) and record it.
run_verify() {
  local id="$1" script="$2"; shift 2
  local path="$ATLAS_DAY1_DIR/verify/$script"
  [[ -f "$path" ]] || die "run_verify $id: $path does not exist"
  local out rc=0
  # §5: a verify script never takes longer than 10 minutes; kill it a little after that rather than hang a phase.
  if [[ -x "$path" ]]; then
    out="$(timeout --foreground 660 "$path" "$@")" || rc=$?
  else
    out="$(timeout --foreground 660 bash "$path" "$@")" || rc=$?
  fi
  # Keep every byte of evidence but on one line.
  out="$(printf '%s' "$out" | tr '\n' ' ' | sed -e 's/[[:space:]]\+/ /g' -e 's/^ //' -e 's/ $//')"
  [[ -n "$out" ]] || out="(no output)"
  local result
  case "$rc" in
    0) result=pass ;;
    1) result=fail ;;
    2) result=deferred ;;
    3) result=info ;;
    124) result=fail; out="exit 124 (timed out after 660 s): $out" ;;
    *) result=fail; out="exit $rc: $out" ;;
  esac
  record_v "$id" "$result" "$out"
  [[ "$result" == fail ]] && return 1
  return 0
}

# _atlas_verify_rows [ID...] — latest record per id as TSV "id<TAB>result<TAB>ts<TAB>msg", in natural id order or in
# the order of the ids given. V4 yields one row per engine (latest per "engine:" prefix, like tools/fill-workbook.py).
_atlas_verify_rows() {
  [[ -f "$ATLAS_VERIFY_FILE" ]] || return 0
  python3 - "$ATLAS_VERIFY_FILE" "$@" <<'PY'
import json, re, sys
path, wanted = sys.argv[1], sys.argv[2:]
latest = {}
v4 = {}
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not {"id", "result", "msg", "ts"} <= rec.keys():
            continue
        if rec["id"] == "V4":
            key = rec["msg"].split(":", 1)[0].strip() or rec["msg"]
            v4[key] = rec
        else:
            latest[rec["id"]] = rec

def natural(i):
    m = re.match(r"V(\d+)([a-z]?)$", i)
    return (int(m.group(1)), m.group(2)) if m else (9999, i)

ids = wanted or sorted(set(latest) | ({"V4"} if v4 else set()), key=natural)
def clean(s):
    return str(s).replace("\t", " ").replace("\n", " ")
for i in ids:
    if i == "V4":
        for rec in v4.values():
            print("\t".join(["V4", rec["result"], clean(rec["ts"]), clean(rec["msg"])]))
        continue
    rec = latest.get(i)
    if rec:
        print("\t".join([i, rec["result"], clean(rec["ts"]), clean(rec["msg"])]))
PY
}

# verify_table [ID...] — print an aligned table (ID, result, message, timestamp) of the latest record per id.
# Ids given but never recorded print as "missing". Prints "(no verify records yet)" when the file is empty.
verify_table() {
  local rows=() r
  mapfile -t rows < <(_atlas_verify_rows "$@")
  local -A seen=()
  for r in "${rows[@]}"; do seen["${r%%$'\t'*}"]=1; done
  local id
  for id in "$@"; do
    [[ -n "${seen[$id]:-}" ]] || rows+=("$id"$'\t'"missing"$'\t'"-"$'\t'"-")
  done
  if (( ${#rows[@]} == 0 )); then
    echo "(no verify records yet)"
    return 0
  fi
  local w=7 msg
  for r in "${rows[@]}"; do
    IFS=$'\t' read -r _ _ _ msg <<<"$r"
    (( ${#msg} > w )) && w=${#msg}
  done
  (( w > 100 )) && w=100
  local rid res ts
  printf '%-6s %-9s %-*s %s\n' "ID" "RESULT" "$w" "MESSAGE" "TIMESTAMP"
  for r in "${rows[@]}"; do
    IFS=$'\t' read -r rid res ts msg <<<"$r"
    printf '%-6s %-9s %-*s %s\n' "$rid" "$res" "$w" "$msg" "$ts"
  done
}

# gate PHASE REQUIRED_IDS... [-- OPTIONAL_IDS...] — CONVENTIONS.md §4/§6.
# PHASE is "1" or "phase1". Latest record per id; required ids that are fail or missing block; deferred never blocks;
# optional ids are printed only. Writes $ATLAS_STATE/done/phaseN.gate on PASS (removes it on FAIL), prints the verdict
# and the exact next command.
gate() {
  local phase="${1#phase}"; shift
  [[ "$phase" =~ ^[1-4]$ ]] || die "gate: PHASE must be 1..4 or phase1..phase4, got '$phase'"
  local required=() optional=() in_opt=0 a
  for a in "$@"; do
    if [[ "$a" == "--" ]]; then in_opt=1; continue; fi
    if (( in_opt )); then optional+=("$a"); else required+=("$a"); fi
  done
  _atlas_state_init
  echo
  echo "PHASE $phase GATE — required: ${required[*]:-none}; recorded only: ${optional[*]:-none}"
  verify_table "${required[@]}" "${optional[@]}"
  echo
  local rows=() r rid res blockers=()
  mapfile -t rows < <(_atlas_verify_rows "${required[@]}")
  local -A status=()
  for r in "${rows[@]}"; do
    IFS=$'\t' read -r rid res _ _ <<<"$r"
    # Any fail among a multi-row id (V4 per engine) fails the id.
    if [[ "${status[$rid]:-}" != fail ]]; then status["$rid"]="$res"; fi
  done
  for rid in "${required[@]}"; do
    case "${status[$rid]:-missing}" in
      pass|deferred|info) ;;
      fail) blockers+=("$rid=fail") ;;
      *) blockers+=("$rid=missing") ;;
    esac
  done
  local marker="$ATLAS_DONE_DIR/phase$phase.gate"
  local entry="${ATLAS_ENTRY:-./atlas-day1.sh}"
  local next
  case "$phase" in
    1) next="sudo $entry phase2" ;;
    2) next="sudo $entry phase3" ;;
    3) next="sudo $entry phase4" ;;
    4) next="sudo $entry report" ;;
  esac
  if (( ${#blockers[@]} == 0 )); then
    date -Is >"$marker"
    echo "PHASE $phase GATE: PASS"
    echo "Next: $next"
    log "phase $phase gate PASS (${required[*]:-no required ids})"
    return 0
  fi
  rm -f "$marker"
  echo "PHASE $phase GATE: FAIL (${blockers[*]})"
  echo "Fix the red rows, then re-run: sudo $entry phase$phase   (the phase skips completed steps; use --force STEP to redo one)"
  log "phase $phase gate FAIL: ${blockers[*]}"
  return 1
}

# ---------------------------------------------------------------------------------------------------------------------
# Packages, retries, files
# ---------------------------------------------------------------------------------------------------------------------
# retry N CMD... — exponential backoff 2s 4s 8s ...; returns the last exit code.
retry() {
  local n="$1"; shift
  local attempt=1 delay=2 rc=0
  while :; do
    rc=0
    "$@" || rc=$?
    (( rc == 0 )) && return 0
    if (( attempt >= n )); then
      warn "retry: '$*' failed $attempt time(s), last exit $rc"
      return "$rc"
    fi
    warn "retry: '$*' failed (exit $rc), attempt $attempt/$n; sleeping ${delay}s"
    sleep "$delay"
    delay=$(( delay * 2 ))
    attempt=$(( attempt + 1 ))
  done
}

_ATLAS_APT_UPDATED="${_ATLAS_APT_UPDATED:-0}"

# apt_install PKG... — non-interactive, idempotent (dpkg-query), apt index refreshed once per process, 3 retries.
apt_install() {
  local pkg missing=()
  for pkg in "$@"; do
    if [[ "$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)" == "install ok installed" ]]; then
      continue
    fi
    missing+=("$pkg")
  done
  if (( ${#missing[@]} == 0 )); then
    log "apt_install: already installed: $*"
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  proxy_env
  if [[ "$_ATLAS_APT_UPDATED" != "1" ]]; then
    retry 3 apt-get -q update || die "apt-get update failed (is the allowlist proxy up and .ubuntu.com allowlisted?)"
    _ATLAS_APT_UPDATED=1
  fi
  log "apt_install: installing ${missing[*]}"
  retry 3 apt-get install -y -q -o Dpkg::Options::=--force-confold "${missing[@]}" \
    || die "apt_install: failed to install ${missing[*]}"
}

# ensure_dir PATH OWNER MODE — OWNER is "user" or "user:group"; empty OWNER leaves ownership alone.
ensure_dir() {
  local path="$1" owner="${2:-}" mode="${3:-755}"
  mkdir -p "$path"
  chmod "$mode" "$path"
  [[ -n "$owner" ]] && chown "$owner" "$path"
  return 0
}

# ensure_line FILE LINE — append LINE unless an identical line exists.
ensure_line() {
  local file="$1" line="$2"
  [[ -e "$file" ]] || { mkdir -p "$(dirname "$file")"; : >"$file"; }
  grep -qxF -- "$line" "$file" || printf '%s\n' "$line" >>"$file"
}

# ensure_kv FILE KEY VALUE — set KEY=VALUE, replacing the first existing "KEY=..." line or appending. Keeps the file's
# inode, mode and owner (content is rewritten in place). VALUE is written verbatim: quote it yourself if it has spaces.
ensure_kv() {
  local file="$1" key="$2" value="$3"
  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "ensure_kv: bad key '$key'"
  [[ -e "$file" ]] || { mkdir -p "$(dirname "$file")"; : >"$file"; }
  local tmp
  tmp="$(mktemp)"
  local replaced=0 line
  while IFS= read -r line || [[ -n "$line" ]]; do
    if (( ! replaced )) && [[ "$line" =~ ^[[:space:]]*${key}= ]]; then
      printf '%s=%s\n' "$key" "$value" >>"$tmp"
      replaced=1
    else
      printf '%s\n' "$line" >>"$tmp"
    fi
  done <"$file"
  (( replaced )) || printf '%s=%s\n' "$key" "$value" >>"$tmp"
  cat "$tmp" >"$file"
  rm -f "$tmp"
}

# render_template [-m MODE] [-o OWNER] SRC DST [VAR...] — substitute only the named variables (envsubst SHELL-FORMAT),
# every named variable must be set, then install DST with MODE (default 644). With no VAR given, every $VAR in SRC is
# substituted. Uses envsubst (gettext-base) when present and an equivalent python3 substitution otherwise, so the
# self-test runs on hosts without gettext-base.
render_template() {
  local mode=644 owner=""
  while [[ "${1:-}" == -* ]]; do
    case "$1" in
      -m) mode="$2"; shift 2 ;;
      -o) owner="$2"; shift 2 ;;
      *) die "render_template: unknown option $1" ;;
    esac
  done
  local src="$1" dst="$2"; shift 2
  [[ -f "$src" ]] || die "render_template: template $src does not exist"
  local v fmt=""
  for v in "$@"; do
    [[ -n "${!v+x}" ]] || die "render_template: variable $v is unset (template $src)"
    export "${v?}"
    fmt+="\${$v} "
  done
  local tmp
  tmp="$(mktemp)"
  if command -v envsubst >/dev/null; then
    if (( $# > 0 )); then envsubst "$fmt" <"$src" >"$tmp"; else envsubst <"$src" >"$tmp"; fi
  else
    python3 - "$src" "$@" <<'PY' >"$tmp"
import os, re, sys
src, names = sys.argv[1], sys.argv[2:]
text = open(src, encoding="utf-8").read()
def sub(m):
    name = m.group(1) or m.group(2)
    if names and name not in names:
        return m.group(0)
    return os.environ.get(name, "")
sys.stdout.write(re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", sub, text))
PY
  fi
  mkdir -p "$(dirname "$dst")"
  if [[ -n "$owner" ]]; then
    install -m "$mode" -o "${owner%%:*}" -g "${owner#*:}" "$tmp" "$dst"
  else
    install -m "$mode" "$tmp" "$dst"
  fi
  rm -f "$tmp"
}

# wait_http URL TIMEOUT_S — poll every 2 s until HTTP 200; returns 1 on timeout.
wait_http() {
  local url="$1" timeout_s="${2:-60}"
  local deadline=$(( SECONDS + timeout_s )) code
  while (( SECONDS < deadline )); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || true)"
    [[ "$code" == "200" ]] && return 0
    sleep 2
  done
  warn "wait_http: $url did not return 200 within ${timeout_s}s (last: ${code:-none})"
  return 1
}

# ---------------------------------------------------------------------------------------------------------------------
# Proxy, notifications, downloads
# ---------------------------------------------------------------------------------------------------------------------
# proxy_env — export the squid allowlist proxy when Phase 1 has configured it ($ATLAS_ETC/proxy.env). Rule §7.1: every
# outbound request goes through the allowlist proxy; before the proxy exists (Phase 1 pre-flight) nothing is exported.
proxy_env() {
  local f="$ATLAS_ETC/proxy.env"
  [[ -f "$f" ]] || return 0
  # Deliberately not `local`: an exported local dies with the function and the caller would see nothing.
  HTTP_PROXY="" HTTPS_PROXY="" NO_PROXY=""
  # shellcheck disable=SC1090  # written by Phase 1 step 4; KEY=VALUE lines only (contract in the header)
  source "$f"
  [[ -n "$HTTPS_PROXY" ]] || die "proxy_env: $f exists but HTTPS_PROXY is empty"
  [[ -n "$HTTP_PROXY" ]] || HTTP_PROXY="$HTTPS_PROXY"
  [[ -n "$NO_PROXY" ]] || NO_PROXY="localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,.local"
  export HTTP_PROXY HTTPS_PROXY NO_PROXY
  export http_proxy="$HTTP_PROXY" https_proxy="$HTTPS_PROXY" no_proxy="$NO_PROXY"
  # hf_transfer ignores proxies (platform research §6, UNVERIFIED but widely reported): keep it off.
  export HF_HUB_ENABLE_HF_TRANSFER=0
}

# notify MSG — ntfy push (http://127.0.0.1:8090/$NTFY_TOPIC, bearer token from secrets/ntfy.env); never fails the caller.
notify() {
  local msg="$*"
  local tokf="$ATLAS_ETC/secrets/ntfy.env"
  local topic="${NTFY_TOPIC:-atlas}"
  if [[ ! -f "$tokf" ]]; then
    log "notify: not configured ($tokf absent): $msg"
    return 0
  fi
  local NTFY_TOKEN=""
  # shellcheck disable=SC1090  # secret file, NTFY_TOKEN=... (CONVENTIONS.md §2)
  source "$tokf" 2>/dev/null || true
  if [[ -z "$NTFY_TOKEN" ]]; then
    log "notify: not configured (NTFY_TOKEN empty): $msg"
    return 0
  fi
  if ! curl -sS --noproxy '*' --max-time 10 -o /dev/null \
       -H "Authorization: Bearer $NTFY_TOKEN" -H "Title: ATLAS $ATLAS_PHASE" \
       -d "$msg" "http://127.0.0.1:8090/$topic" 2>/dev/null; then
    warn "notify: push failed (ntfy down?): $msg"
  fi
  return 0
}

_sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

# hf_download REPO FILE DEST SHA256 [REVISION] — resumable Hugging Face download through the proxy, sha256-verified,
# skipped when DEST already matches. HF_TOKEN comes from the environment or $ATLAS_ETC/secrets/hf-token.env.
# SHA256 "none" is tolerated for files whose hash the research could not verify: the computed hash is then written
# to DEST.sha256 and a loud warning is logged (rule §7.3 wants checksums; this keeps the gap visible, not silent).
hf_download() {
  local repo="$1" file="$2" dest="$3" sha="${4:-}" rev="${5:-main}"
  [[ -n "$sha" ]] || die "hf_download $repo/$file: SHA256 argument is required (use 'none' only when unverifiable)"
  sha="${sha,,}"
  if [[ -f "$dest" ]]; then
    local have
    have="$(_sha256_of "$dest")"
    if [[ "$sha" == "none" ]]; then
      log "hf_download: $dest exists, sha256 $have (UNVERIFIED, no reference hash); skipping"
      printf '%s  %s\n' "$have" "$(basename "$dest")" >"$dest.sha256"
      return 0
    fi
    if [[ "$have" == "$sha" ]]; then
      log "hf_download: $dest already present with the expected sha256; skipping"
      return 0
    fi
    warn "hf_download: $dest exists with sha256 $have, expected $sha; re-downloading"
    rm -f "$dest"
  fi
  proxy_env
  local hdr=()
  if [[ -z "${HF_TOKEN:-}" && -f "$ATLAS_ETC/secrets/hf-token.env" ]]; then
    local HF_TOKEN=""
    # shellcheck disable=SC1091  # secret file, HF_TOKEN=... (CONVENTIONS.md §2)
    source "$ATLAS_ETC/secrets/hf-token.env"
  fi
  [[ -n "${HF_TOKEN:-}" ]] && hdr=(-H "Authorization: Bearer $HF_TOKEN")
  local base="${HF_ENDPOINT:-https://huggingface.co}"
  local url="$base/$repo/resolve/$rev/$file"
  local part="$dest.part"
  mkdir -p "$(dirname "$dest")"
  log "hf_download: $url -> $dest (resume from $(stat -c %s "$part" 2>/dev/null || echo 0) bytes)"
  local code rc=0
  code="$(curl -L -C - --fail -sS --retry 5 --retry-delay 10 --connect-timeout 30 \
          -w '%{http_code}' -o "$part" "${hdr[@]}" "$url")" || rc=$?
  if (( rc != 0 )); then
    # A fully downloaded .part answered with 416 on resume makes curl fail on some versions; the hash decides.
    if [[ -f "$part" && "$sha" != "none" && "$(_sha256_of "$part")" == "$sha" ]]; then
      log "hf_download: $part was already complete (curl exit $rc, HTTP $code)"
    else
      case "$code" in
        401|403) die "hf_download: HTTP $code for $url — gated or private: accept the licence at https://huggingface.co/$repo with the account that owns HF_TOKEN in $ATLAS_ETC/secrets/hf-token.env" ;;
        404) die "hf_download: HTTP 404 for $url — the repo, revision or file name is wrong" ;;
        *) die "hf_download: curl exit $rc (HTTP $code) for $url; re-run to resume" ;;
      esac
    fi
  fi
  local have
  have="$(_sha256_of "$part")"
  if [[ "$sha" == "none" ]]; then
    warn "hf_download: no reference sha256 for $repo/$file; recording computed $have in $dest.sha256 (UNVERIFIED)"
    printf '%s  %s\n' "$have" "$(basename "$dest")" >"$dest.sha256"
  elif [[ "$have" != "$sha" ]]; then
    rm -f "$part"
    die "hf_download: sha256 mismatch for $repo/$file: got $have, expected $sha (partial removed; re-run)"
  fi
  mv -f "$part" "$dest"
  log "hf_download: verified $dest"
}

# ---------------------------------------------------------------------------------------------------------------------
# GPU memory (sysfs, bytes -> MiB; llama-cpp-vulkan research §5.2, amdgpu_gtt_mgr.c)
# ---------------------------------------------------------------------------------------------------------------------
# Print the /sys/class/drm/cardN/device directory of the AMD GPU (vendor 0x1002). Connector nodes (card0-DP-1) are
# excluded by the name check.
gpu_card_device_dir() {
  local c
  for c in /sys/class/drm/card*; do
    [[ "$(basename "$c")" =~ ^card[0-9]+$ ]] || continue
    [[ -r "$c/device/vendor" ]] || continue
    if [[ "$(cat "$c/device/vendor")" == "0x1002" ]]; then
      printf '%s\n' "$c/device"
      return 0
    fi
  done
  return 1
}

_gpu_mem_info_mb() {
  local attr="$1" dir
  dir="$(gpu_card_device_dir)" || die "no AMD GPU (vendor 0x1002) under /sys/class/drm"
  [[ -r "$dir/$attr" ]] || die "$dir/$attr is not readable (amdgpu not bound?)"
  echo $(( $(cat "$dir/$attr") / 1048576 ))
}

gpu_gtt_used_mb()  { _gpu_mem_info_mb mem_info_gtt_used; }
gpu_gtt_total_mb() { _gpu_mem_info_mb mem_info_gtt_total; }

# ---------------------------------------------------------------------------------------------------------------------
# Service user, detached phases
# ---------------------------------------------------------------------------------------------------------------------
# svc_user_run CMD... — run as the atlas service account (runuser: no sudo-rs involvement).
svc_user_run() {
  require_root
  id -u "$ATLAS_SVC_USER" >/dev/null 2>&1 || die "svc_user_run: user $ATLAS_SVC_USER does not exist yet"
  runuser -u "$ATLAS_SVC_USER" -- "$@"
}

# detached_phase NAME SCRIPT — start "SCRIPT --run" as transient unit atlas-day1-NAME, journal-logged, surviving SSH
# loss. Refuses to start if the unit is already active. Prints the follow command.
detached_phase() {
  local name="$1" script="$2"
  require_root
  local unit="atlas-day1-$name"
  [[ -x "$script" ]] || die "detached_phase: $script is not executable"
  if systemctl is-active --quiet "$unit"; then
    die "$unit is already running; follow it with: journalctl -fu $unit"
  fi
  _atlas_state_init
  local logf
  logf="$(ATLAS_PHASE="$name" _atlas_log_file)"
  proxy_env
  local setenv=(
    "--setenv=ATLAS_ETC=$ATLAS_ETC" "--setenv=ATLAS_STATE=$ATLAS_STATE"
    "--setenv=ATLAS_OPT=$ATLAS_OPT" "--setenv=ATLAS_SRV=$ATLAS_SRV"
    "--setenv=ATLAS_PHASE=$name" "--setenv=ATLAS_LOG_FILE=$logf"
    "--setenv=ATLAS_ENTRY=${ATLAS_ENTRY:-./atlas-day1.sh}"
    "--setenv=ATLAS_REPO_ROOT=${ATLAS_REPO_ROOT:-}"
  )
  local v
  for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy HF_HUB_ENABLE_HF_TRANSFER ATLAS_FORCED_STEPS; do
    [[ -n "${!v:-}" ]] && setenv+=("--setenv=$v=${!v}")
  done
  systemd-run --unit="$unit" --collect \
    --property=StandardOutput=journal --property=StandardError=journal \
    --property="WorkingDirectory=$(dirname "$script")" \
    "${setenv[@]}" "$script" --run \
    || die "systemd-run failed to start $unit"
  log "$unit started; log file $logf"
  # Rule §7.10: the exact follow command, and the spelling that keeps the phase in this terminal instead (a --force
  # detaches like the plain command; only --foreground keeps it here, atlas-day1.sh / README §5).
  local forced=""
  [[ -n "${ATLAS_FORCED_STEPS:-}" ]] && forced=" --force ${ATLAS_FORCED_STEPS// / --force }"
  echo "Follow it with:  journalctl -fu $unit"
  echo "Or tail the log: tail -f $logf"
  echo "To run it in this terminal instead (survives no SSH drop): sudo ${ATLAS_ENTRY:-./atlas-day1.sh} $name --foreground$forced"
}

# ---------------------------------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------------------------------
_atlas_detect_lan_iface() { ip -o route show default 2>/dev/null | awk '{print $5; exit}'; }

_atlas_detect_lan_cidr() {
  local iface="$1" addr
  addr="$(ip -o -4 addr show dev "$iface" scope global 2>/dev/null | awk '{print $4; exit}')"
  [[ -n "$addr" ]] || return 1
  python3 -c 'import ipaddress, sys; print(ipaddress.ip_interface(sys.argv[1]).network)' "$addr"
}

_atlas_detect_lan_ip() {
  ip -o -4 addr show dev "$1" scope global 2>/dev/null | awk '{print $4; exit}' | cut -d/ -f1
}

# Largest NVMe disk that holds no mounted filesystem (itself or any child: partitions, LUKS mappings), as /dev/disk/by-id.
_atlas_detect_data_disk() {
  local best="" best_size=0 name size tran type
  while read -r name size type tran; do
    [[ "$type" == "disk" && "$tran" == "nvme" ]] || continue
    # any mountpoint on the disk or its descendants disqualifies it
    if lsblk -no MOUNTPOINTS "/dev/$name" 2>/dev/null | grep -q .; then continue; fi
    if (( size > best_size )); then best="$name"; best_size="$size"; fi
  done < <(lsblk -dnb -o NAME,SIZE,TYPE,TRAN 2>/dev/null)
  [[ -n "$best" ]] || return 1
  local byid="" l target
  for l in /dev/disk/by-id/nvme-*; do
    [[ -e "$l" ]] || continue
    target="$(readlink -f "$l")"
    [[ "$target" == "/dev/$best" ]] || continue
    # prefer the model_serial name over the eui.* one; take the first of either
    if [[ "$(basename "$l")" != nvme-eui.* ]]; then byid="$l"; break; fi
    [[ -n "$byid" ]] || byid="$l"
  done
  [[ -n "$byid" ]] || return 1
  printf '%s\n' "$byid"
}

_atlas_detect_principal_user() {
  getent passwd | awk -F: '$3 >= 1000 && $3 < 65534 && $6 ~ /^\/home\// {print $3, $1, $6}' | sort -n \
    | while read -r _ user home; do
        [[ -d "$home" ]] && { printf '%s\n' "$user"; break; }
      done
}

_atlas_detect_tz() {
  local tz=""
  [[ -s /etc/timezone ]] && tz="$(head -n1 /etc/timezone)"
  [[ -n "$tz" ]] || tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  [[ -n "$tz" ]] || tz="Australia/Sydney"
  printf '%s\n' "$tz"
}

# _atlas_secure_boot_enabled — 0 only when the firmware reports Secure Boot ON (the SecureBoot EFI variable: 4-byte
# attribute header, byte 4 is the value; then mokutil). A legacy-BIOS boot or an unreadable state counts as OFF, like
# phase1/01-preflight.sh's phase1_secure_boot_state (the authoritative reading, kept there and in verify/v02-tpm.sh);
# this copy is kept for callers that only need a yes/no (the pre-flight logs the state; D15: the Principal enables it).
_atlas_secure_boot_enabled() {
  local f v
  case "${ATLAS_TEST_SECURE_BOOT:-}" in on) return 0 ;; off) return 1 ;; esac   # test-only override (header)
  for f in /sys/firmware/efi/efivars/SecureBoot-*; do
    [[ -r "$f" ]] || continue
    v="$(od -An -tu1 -j4 -N1 "$f" 2>/dev/null | tr -d '[:space:]')"
    case "$v" in 1) return 0 ;; 0) return 1 ;; esac
  done
  if command -v mokutil >/dev/null 2>&1; then
    case "$(mokutil --sb-state 2>/dev/null || true)" in *enabled*) return 0 ;; esac
  fi
  return 1
}

# load_env — install config/atlas.env.example on first run, source it, auto-detect blank detectable keys (persisting
# them so DATA_DISK survives the LUKS format that makes it "mounted"), ask once for the Principal's keys and record a
# to-do for any left blank (never a stop: v0.3.3).
load_env() {
  local envf="$ATLAS_ETC/atlas.env"
  local example="$ATLAS_DAY1_DIR/config/atlas.env.example"
  if [[ ! -f "$envf" ]]; then
    [[ -f "$example" ]] || die "load_env: neither $envf nor $example exists"
    mkdir -p "$ATLAS_ETC"
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
      chmod 755 "$ATLAS_ETC"
      local grp=root
      getent group atlas >/dev/null 2>&1 && grp=atlas   # the atlas group exists only after Phase 1 step 6
      install -m 640 -o root -g "$grp" "$example" "$envf"
    else
      install -m 640 "$example" "$envf"
    fi
    log "load_env: installed $example -> $envf (first run)"
  fi
  set -a
  # shellcheck disable=SC1090  # the installed copy of config/atlas.env.example
  source "$envf"
  set +a

  # Keys the Principal provides. Policy (v0.3.3, the Principal's instruction): a blank key NEVER stops a phase.
  # Each blank key is asked ONCE on the terminal in plain words; an answer is written to atlas.env; no answer (or no
  # terminal) records a to-do for the live ATLAS and the step that needs the key defers itself instead of failing.
  # WINDOWS_SHARE is not asked at all: the Principal moved the Windows share to the live to-do list (Section 12.4).
  # Secure Boot is the Principal's BIOS action (D15: enabled); there is no acknowledgement key any more.
  local ans=""
  _atlas_confirm_key() { # KEY QUESTION EXAMPLE TODO_ID TODO_TITLE
    local key="$1" q="$2" ex="$3" tid="$4" title="$5"
    local asked="$ATLAS_STATE/asked.$key"
    if [[ -n "${!key:-}" ]]; then
      # Provided (now or later): close the to-do the earlier blank left open.
      [[ -e "$asked" ]] && { rm -f "$asked"; todo_done "$tid"; }
      return 0
    fi
    [[ -e "$asked" ]] && return 0   # asked once and recorded once; the to-do stays open until the key is set
    if [[ ! -e "$asked" ]]; then
      ask ans "$q (example: $ex; press Enter to skip and leave it for later): "
      if [[ -n "$ans" ]]; then
        ensure_kv "$envf" "$key" "\"$ans\""
        printf -v "$key" '%s' "$ans"; export "${key?}"
        log "load_env: $key set in $envf"
        return 0
      fi
      _atlas_state_init; : >"$asked"
    fi
    todo_add "$tid" "$title" "Set $key in $envf (example: $key=\"$ex\"), then re-run the step that needs it with --force"
  }
  _atlas_confirm_key GOOGLE_ACCOUNTS "Your two Google account addresses, each tagged corporate or estate" \
    "you@company.com:corporate you@gmail.com:estate" input-google-accounts \
    "Google accounts not given: Gmail, Calendar and Drive (Phase 2 step 6c, V20) are deferred"
  _atlas_confirm_key FAMILY_NAMES "Family members' names for the privacy routing rule, space separated" \
    "Surname Givenname" input-family-names \
    "Family names not given: the router's family-name hard rule (Section 7.2) is inactive"
  if [[ "${BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE:-}" != yes ]]; then
    BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=""
    _atlas_confirm_key BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE \
      "Type yes if you accept Google's Android SDK terms (https://developer.android.com/studio/terms) so the Android build container can be built" \
      "yes" input-android-sdk-licence \
      "Android SDK terms not accepted: the Android/Windows build container (Phase 2 step 6) is deferred"
    [[ "${BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE:-}" == yes ]] || BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=""
  fi
  export BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE
  if [[ -n "${WINDOWS_SHARE:-}" && -e "$ATLAS_STATE/asked.WINDOWS_SHARE" ]]; then
    rm -f "$ATLAS_STATE/asked.WINDOWS_SHARE"; todo_done input-windows-share
  fi
  if [[ -z "${WINDOWS_SHARE:-}" && ! -e "$ATLAS_STATE/asked.WINDOWS_SHARE" ]]; then
    _atlas_state_init; : >"$ATLAS_STATE/asked.WINDOWS_SHARE"
    todo_add input-windows-share "Windows PC share not configured (the Principal's choice for Day 1): Phase 2 step 9 is skipped" \
      "Set WINDOWS_SHARE=\"//host/share\" in $envf and create $ATLAS_ETC/secrets/smb.cred, then: atlas-day1.sh phase2 --force 09"
  fi
  local acct
  for acct in ${GOOGLE_ACCOUNTS:-}; do
    [[ "$acct" =~ ^[^:[:space:]]+@[^:[:space:]]+:(corporate|estate)$ ]] \
      || die "load_env: GOOGLE_ACCOUNTS entry '$acct' must be email:corporate or email:estate (edit $envf)"
  done
  [[ -z "${WINDOWS_SHARE:-}" || "$WINDOWS_SHARE" =~ ^//[^/]+/.+$ ]] || die "load_env: WINDOWS_SHARE must look like //host/share, got '$WINDOWS_SHARE' (edit $envf)"

  # Auto-detected keys: detect when blank, persist, die when detection fails.
  local changed=0 v
  if [[ -z "${PRINCIPAL_USER:-}" ]]; then
    v="$(_atlas_detect_principal_user)"
    [[ -n "$v" ]] || die "load_env: PRINCIPAL_USER blank and no non-system user with a /home directory found; set it in $envf"
    PRINCIPAL_USER="$v"; ensure_kv "$envf" PRINCIPAL_USER "$v"; changed=1
  fi
  if [[ -z "${TZ:-}" ]]; then
    TZ="$(_atlas_detect_tz)"; ensure_kv "$envf" TZ "$TZ"; changed=1
  fi
  if [[ -z "${LAN_IFACE:-}" ]]; then
    v="$(_atlas_detect_lan_iface)"
    [[ -n "$v" ]] || die "load_env: LAN_IFACE blank and no default route found; set it in $envf"
    LAN_IFACE="$v"; ensure_kv "$envf" LAN_IFACE "$v"; changed=1
  fi
  if [[ -z "${LAN_CIDR:-}" ]]; then
    v="$(_atlas_detect_lan_cidr "$LAN_IFACE" || true)"
    [[ -n "$v" ]] || die "load_env: LAN_CIDR blank and $LAN_IFACE has no IPv4 address; set it in $envf"
    LAN_CIDR="$v"; ensure_kv "$envf" LAN_CIDR "$v"; changed=1
  fi
  if [[ -z "${DATA_DISK:-}" ]]; then
    v="$(_atlas_detect_data_disk || true)"
    [[ -n "$v" ]] || die "load_env: DATA_DISK blank and no NVMe disk without a mounted filesystem found; set it (a /dev/disk/by-id path) in $envf"
    DATA_DISK="$v"; ensure_kv "$envf" DATA_DISK "$v"; changed=1
  fi
  [[ -e "$DATA_DISK" ]] || die "load_env: DATA_DISK=$DATA_DISK does not exist"
  # RDP_ALLOW_FROM (optional): checked here, after LAN_CIDR is known, with the same rules ufw applies plus "inside the
  # LAN", because step 4 resets ufw before adding rules and a value ufw rejects there would leave the node unfiltered.
  if [[ -n "${RDP_ALLOW_FROM:-}" ]]; then
    local __bad
    __bad="$(atlas_rdp_sources_check "$LAN_CIDR" "$RDP_ALLOW_FROM")" \
      || die "load_env: RDP_ALLOW_FROM must be IPv4 addresses or CIDRs inside the LAN $LAN_CIDR, separated by spaces (e.g. \"192.168.1.20\"): $__bad. Edit $envf, or leave it blank for the whole LAN"
    # Close the to-do only once the firewall really is narrowed (the key alone changes nothing until the to-do's ufw
    # commands or a --force 04 apply it).
    if todo_is_open rdp-restrict && _atlas_rdp_narrowed; then todo_done rdp-restrict; fi
  fi
  if [[ -z "${CLOUDFLARE_TXT:-}" ]]; then
    CLOUDFLARE_TXT="/home/$PRINCIPAL_USER/CLOUDFLARE.txt"; ensure_kv "$envf" CLOUDFLARE_TXT "$CLOUDFLARE_TXT"; changed=1
  fi
  : "${WG_IFACE:=wg0}" "${WG_CIDR:=10.8.0.0/24}" "${WG_PORT:=51820}"
  : "${DOMAIN:=sovereign-node.link}" "${VPN_HOST:=vpn.sovereign-node.link}"
  : "${NTFY_TOPIC:=atlas}" "${OPENWEBUI_PORT:=3000}" "${ORCH_PORT:=8800}" "${LLAMA_PORT_BASE:=8100}" "${DOWNLOAD_MBPS:=100}"
  # Derived, not stored: the LAN address every published Docker port is pinned to (conflict 4).
  LAN_IP="$(_atlas_detect_lan_ip "$LAN_IFACE" || true)"
  export PRINCIPAL_USER TZ LAN_IFACE LAN_CIDR LAN_IP DATA_DISK CLOUDFLARE_TXT WG_IFACE WG_CIDR WG_PORT DOMAIN VPN_HOST
  export GOOGLE_ACCOUNTS WINDOWS_SHARE FAMILY_NAMES NTFY_TOPIC OPENWEBUI_PORT ORCH_PORT LLAMA_PORT_BASE DOWNLOAD_MBPS
  export RDP_ALLOW_FROM="${RDP_ALLOW_FROM:-}"
  export BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE
  [[ -n "${HF_ENDPOINT:-}" ]] && export HF_ENDPOINT
  (( changed )) && log "load_env: auto-detected values written to $envf"
  log "load_env: user=$PRINCIPAL_USER tz=$TZ lan=$LAN_IFACE/$LAN_CIDR ip=${LAN_IP:-?} data=$DATA_DISK"
}

# ---------------------------------------------------------------------------------------------------------------------
# Driver argument handling
# ---------------------------------------------------------------------------------------------------------------------
# parse_common_args ARGS... — --dry-run, --force STEP, --status, --run (CONVENTIONS.md §4). Drivers set ATLAS_PHASE
# before calling. --status prints the done markers and the latest verify table, then exits 0. --force clears
# done/$ATLAS_PHASE.STEP (also accepts PHASE.STEP) and appends the bare step id to the exported ATLAS_FORCED_STEPS
# (space-separated; phase2/09b-vault.sh treats a forced 09b as the Principal's request to initialise the real vault,
# README-contracts.md §3). --run marks the in-unit entry (ATLAS_IN_UNIT=1).
parse_common_args() {
  ATLAS_IN_UNIT="${ATLAS_IN_UNIT:-0}"
  while (( $# > 0 )); do
    case "$1" in
      --dry-run) ATLAS_DRY_RUN=1; export ATLAS_DRY_RUN; shift ;;
      --force)
        [[ -n "${2:-}" ]] || die "--force needs a STEP id (e.g. --force 05b)"
        local step="$2" marker
        [[ "$step" == *.* ]] && marker="$ATLAS_DONE_DIR/$step" || marker="$ATLAS_DONE_DIR/$ATLAS_PHASE.$step"
        if [[ -e "$marker" ]]; then rm -f "$marker"; log "cleared marker $marker"; else warn "no marker $marker to clear"; fi
        ATLAS_FORCED_STEPS="${ATLAS_FORCED_STEPS:+$ATLAS_FORCED_STEPS }${step#*.}"
        export ATLAS_FORCED_STEPS
        shift 2 ;;
      --status) phase_status "$ATLAS_PHASE"; exit 0 ;;
      --run) ATLAS_IN_UNIT=1; export ATLAS_IN_UNIT; shift ;;
      -h|--help)
        echo "usage: $0 [--dry-run] [--force STEP] [--status] [--run]"; exit 0 ;;
      *) die "unknown argument '$1' (accepted: --dry-run, --force STEP, --status, --run)" ;;
    esac
  done
}

# phase_status [PHASE...] — done markers (all phases when none given) and the full verify table.
phase_status() {
  local phases=("$@") p m
  (( ${#phases[@]} > 0 )) || phases=(phase1 phase2 phase3 phase4)
  for p in "${phases[@]}"; do
    echo "== $p"
    local found=0
    for m in "$ATLAS_DONE_DIR/$p".*; do
      [[ -e "$m" ]] || continue
      found=1
      printf '  %-22s %s\n' "$(basename "$m")" "$(head -n1 "$m" 2>/dev/null || true)"
    done
    (( found )) || echo "  (no steps done)"
    if [[ -e "$ATLAS_DONE_DIR/$p.gate" ]]; then echo "  gate: PASS"; else echo "  gate: not passed"; fi
  done
  echo "== verify records ($ATLAS_VERIFY_FILE)"
  verify_table
  echo "== to-do list for the live ATLAS ($ATLAS_TODO_FILE)"
  todo_list
}
