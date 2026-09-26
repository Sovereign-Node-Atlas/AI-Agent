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
  [[ "$id" =~ ^V[0-9]+[a-z]?$ ]] || die "record_v: ID must look like V7 or V3a, got '$id'"
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
    die "$unit is already running; follow it with: journalctl -u $unit -f"
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
  for v in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy HF_HUB_ENABLE_HF_TRANSFER; do
    [[ -n "${!v:-}" ]] && setenv+=("--setenv=$v=${!v}")
  done
  systemd-run --unit="$unit" --collect \
    --property=StandardOutput=journal --property=StandardError=journal \
    --property="WorkingDirectory=$(dirname "$script")" \
    "${setenv[@]}" "$script" --run \
    || die "systemd-run failed to start $unit"
  log "$unit started; log file $logf"
  echo "Follow it with: journalctl -u $unit -f"
  echo "Or tail the log:  tail -f $logf"
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

# load_env — install config/atlas.env.example on first run, source it, auto-detect blank detectable keys (persisting
# them so DATA_DISK survives the LUKS format that makes it "mounted"), and die naming any required key still blank.
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

  # Keys the Principal must confirm; checked first because the message is deterministic and the fix is a text edit.
  local missing=() k
  for k in GOOGLE_ACCOUNTS WINDOWS_SHARE FAMILY_NAMES; do
    [[ -n "${!k:-}" ]] || missing+=("$k")
  done
  if (( ${#missing[@]} > 0 )); then
    die "load_env: the Principal must set ${missing[*]} in $envf (see the comments there), then re-run"
  fi
  local acct
  for acct in $GOOGLE_ACCOUNTS; do
    [[ "$acct" =~ ^[^:[:space:]]+@[^:[:space:]]+:(corporate|estate)$ ]] \
      || die "load_env: GOOGLE_ACCOUNTS entry '$acct' must be email:corporate or email:estate"
  done
  [[ "$WINDOWS_SHARE" =~ ^//[^/]+/.+$ ]] || die "load_env: WINDOWS_SHARE must look like //host/share, got '$WINDOWS_SHARE'"

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
  [[ -n "${HF_ENDPOINT:-}" ]] && export HF_ENDPOINT
  (( changed )) && log "load_env: auto-detected values written to $envf"
  log "load_env: user=$PRINCIPAL_USER tz=$TZ lan=$LAN_IFACE/$LAN_CIDR ip=${LAN_IP:-?} data=$DATA_DISK"
}

# ---------------------------------------------------------------------------------------------------------------------
# Driver argument handling
# ---------------------------------------------------------------------------------------------------------------------
# parse_common_args ARGS... — --dry-run, --force STEP, --status, --run (CONVENTIONS.md §4). Drivers set ATLAS_PHASE
# before calling. --status prints the done markers and the latest verify table, then exits 0. --force clears
# done/$ATLAS_PHASE.STEP (also accepts PHASE.STEP). --run marks the in-unit entry (ATLAS_IN_UNIT=1).
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
}
