#!/usr/bin/env bash
# phase4/lib-engine.sh — the library every phase4/engines/<key>.sh sources (Section 17 Phase 4 steps 2-4; CONVENTIONS.md
# §1 "phase4/engines/<name>.sh builds and tests one engine"). Not a step file: it is sourced, never run.
#
# Engine script shape (the contract this file defines; the driver phase4-engines.sh runs the script, never sources it):
#   P4_KEY="flux1-dev"
#   # shellcheck source=phase4/lib-engine.sh
#   source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"
#   p4_build() { p4_venv_from_json; p4_pull; }        # everything before the test; idempotent (skips what exists)
#   p4_main "$@"                                       # skip-if-passed, build, test, result JSON, exit 0/1/2
#
# What p4_main does: logs to $ATLAS_STATE/logs/phase4-<key>.log (and the journal), skips when the result file already
# says pass (delete $ATLAS_STATE/phase4/<key>.json to redo an engine), runs p4_build, then p4_run_test (the engine's
# <key>_test.py inside the container, network off, HF_HUB_OFFLINE=1, the GTT counter sampled on the host every 2 s),
# and writes $ATLAS_STATE/phase4/<key>.json:
#   {key, name, tier, result: pass|fail|deferred, outcome: pass|fail (the raw test verdict before the tier rule),
#    blocking: bool (green only), built, loaded, sample_output_path, footprint_mb, gtt_baseline_mb, gtt_peak_mb,
#    peak_alloc_mb, seconds, load_seconds, started_at, finished_at, exit_code, notes, log_tail[20]}
# Exit code: 0 pass, 1 fail, 2 deferred (yellow and verify tiers on any failure; Section 15.2 "never blocks";
# CONVENTIONS.md §7.4 "yellow engines record deferred" — `outcome` keeps the raw fail so the Section 17 step 6 table
# still says the attempt failed). Any unexpected exit (die, errexit, the driver's timeout) is caught by the EXIT trap
# and still yields a result file.
#
# Environment from the driver (defaults make a script runnable by hand as root for one engine):
#   P4_IMAGE      the base image tag (config/phase4-engines.json base_image.tag)
#   P4_TIMEOUT_S  informational here; the driver wraps the script in `timeout` (json timeout_s)
#   P4_LOAD_TIMEOUT_S  in-test load watchdog (json defaults.load_timeout_s)
# Files relied on from other writers (each checked, fails loudly when absent):
#   /etc/atlas/docker.env (Phase 1 step 6): ATLAS_UID ATLAS_GID RENDER_GID VIDEO_GID CONTAINER_HTTP_PROXY
#     CONTAINER_HTTPS_PROXY CONTAINER_NO_PROXY.   /etc/atlas/secrets/hf-token.env (phase2-services.sh): HF_TOKEN=...
# Container layout (fixed; the image's ENV points here): /srv/atlas/engines = $ATLAS_SRV/engines (hf/ venv/ src/ dl/
# home/ miopen/ manifests/), /srv/atlas/workspace/phase4-samples = $ATLAS_SRV/workspace/phase4-samples,
# /opt/atlas/phase4 = $ATLAS_DAY1_DIR/phase4 read-only (the tests and p4common.py).
#
# Container run kinds (p4_docker_run; fix round). Two kinds, never mixed:
#   build/pull/clone runs (--net): the allowlist proxy, NO GPU devices, the image's default seccomp profile,
#     --cap-drop ALL, --security-opt no-new-privileges, no --ipc. These execute third-party install code (setup.py /
#     build backends of the git clones, unpinned PyPI packages), so they get nothing beyond the network they need
#     (Section 16.4: an OS-level cap is the guard, not a review layer).
#   GPU test runs (--gpu, p4_run_test only): --network none, HF_HUB_OFFLINE=1, /dev/kfd + /dev/dri, the numeric
#     video/render gids, --cap-drop ALL, no-new-privileges, and ONLY the extra flags V11 proved necessary
#     ($ATLAS_SRV/engines/v11-runflags.txt written by verify/v11-rocm-selftest.sh: empty when the default seccomp
#     profile passed the 4 GB GTT allocation, else `--security-opt seccomp=unconfined` / `--ipc=host`). SYS_PTRACE is
#     never added (research §6.2 marks it "only for debuggers/profilers").
#   Every run: --pids-limit 4096, --memory/--memory-swap = json mem_limit_gb (default max(16, 2 x
#     footprint_gb_expected)), --cpus = nproc - 2 (the orchestrator and Redis keep two cores). amdgpu GTT allocations
#     are NOT charged to the container cgroup (TTM pages are not memcg-accounted), so --memory bounds the CPU-side
#     allocations only; the host GTT delta remains the footprint measure (json footprint_rule).
#   HF_TOKEN (gated repos only, --token): the secrets file is bind-mounted read-only at /run/secrets/hf-token.env on a
#     private tmpfs; p4common.py reads it from there. It is never an --env-file, so it is not in the container's
#     Config.Env (docker inspect) and not inherited by every subprocess of the pull.
# Privilege note for the Principal (fix round): the `atlas` account is in group `docker` (Phase 1 step 6, CONVENTIONS
# §2). The docker socket of a rootful daemon is root-equivalent on the host, so the sudoers fragment and the approval
# gate bound the orchestrator's code paths, not its privilege. Reported to the Phase 1 writer (socket gating: rootless
# docker or a socket proxy) — nothing in this file can close it; what it can do is keep secrets out of container
# metadata (above) and give every run the least it needs.

# ATLAS_PHASE must be fixed BEFORE common.sh is sourced (it defaults the name to "common" otherwise): the library's
# log lines and verify records belong to phase4 whether the driver or a hand run started this script.
: "${ATLAS_PHASE:=phase4}"
export ATLAS_PHASE
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

[[ -n "${P4_KEY:-}" ]] || die "lib-engine.sh: P4_KEY must be set before sourcing"
require_root

# die override (fix round): common.sh's die logs through an asynchronous tee (see `exec` below), so the EXIT trap could
# miss the FATAL line in the log file. Keep the message in a variable the trap reads directly.
P4_LAST_FATAL=""
die() { P4_LAST_FATAL="$*"; _atlas_emit FATAL "$*"; exit 1; }

P4_DAY1="$ATLAS_DAY1_DIR"
P4_JSON="$P4_DAY1/config/phase4-engines.json"
P4_STATE="$ATLAS_STATE/phase4"
P4_MARKERS="$P4_STATE/markers"                                # root-only idempotence markers (never on the bind mount)
P4_RESULT="$P4_STATE/$P4_KEY.json"
P4_LOG="$ATLAS_LOG_DIR/phase4-$P4_KEY.log"
P4_ENGINES_DIR="$ATLAS_SRV/engines"
P4_SAMPLES_DIR="$ATLAS_SRV/workspace/phase4-samples"
P4_SAMPLES="$P4_SAMPLES_DIR/$P4_KEY"
P4_TEST_PY="/opt/atlas/phase4/engines/${P4_KEY}_test.py"      # container path
P4_VENV="/srv/atlas/engines/venv/$P4_KEY"                    # container path
P4_HOST_VENV="$P4_ENGINES_DIR/venv/$P4_KEY"
P4_SRC="/srv/atlas/engines/src"                              # container path
P4_HOST_SRC="$P4_ENGINES_DIR/src"
P4_HOST_DL="$P4_ENGINES_DIR/dl/$P4_KEY"
P4_TOKEN_FILE="$ATLAS_ETC/secrets/hf-token.env"
P4_TOKEN_MOUNT="/run/secrets/hf-token.env"                   # container path (p4common.py reads it)
P4_DOCKER_ENV="$ATLAS_ETC/docker.env"
P4_RUNFLAGS_FILE="$P4_ENGINES_DIR/v11-runflags.txt"          # written by verify/v11-rocm-selftest.sh
P4_FREEZE="/srv/atlas/engines/manifests/${P4_KEY}-freeze.txt" # container path
: "${P4_LOAD_TIMEOUT_S:=900}"
export P4_KEY P4_STATE P4_RESULT P4_LOG P4_SAMPLES P4_LOAD_TIMEOUT_S

[[ -f "$P4_JSON" ]] || die "$P4_JSON is missing"
command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
[[ -r "$P4_DOCKER_ENV" ]] || die "$P4_DOCKER_ENV missing (Phase 1 step 6 writes the uids, gids and container proxy)"
[[ -d "$P4_ENGINES_DIR" ]] || die "$P4_ENGINES_DIR missing: the 8 TB data volume is not mounted (Phase 1 step 3)"

# p4_field FIELD — one field of this engine's json entry (scalars printed plainly, lists/objects as JSON, null as "").
p4_field() {
  python3 - "$P4_JSON" "$P4_KEY" "$1" <<'PY'
import json, sys
path, key, field = sys.argv[1:4]
doc = json.load(open(path, encoding="utf-8"))
eng = next((e for e in doc["engines"] if e["key"] == key), None)
if eng is None:
    sys.exit(f"{key}: not in {path}")
v = eng.get(field, doc.get("defaults", {}).get(field))
if v is None:
    print("")
elif isinstance(v, (list, dict)):
    print(json.dumps(v))
else:
    print(v)
PY
}

P4_NAME="$(p4_field name)"
P4_TIER="$(p4_field tier)"
: "${P4_IMAGE:=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_image"]["tag"])' "$P4_JSON")}"
export P4_IMAGE

# docker.env values (Phase 1 step 6 contract).
_p4_denv() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$P4_DOCKER_ENV"; }
P4_UID="$(_p4_denv ATLAS_UID)"; P4_GID="$(_p4_denv ATLAS_GID)"
P4_RENDER_GID="$(_p4_denv RENDER_GID)"; P4_VIDEO_GID="$(_p4_denv VIDEO_GID)"
P4_CPROXY="$(_p4_denv CONTAINER_HTTPS_PROXY)"; P4_CPROXY_HTTP="$(_p4_denv CONTAINER_HTTP_PROXY)"
P4_CNOPROXY="$(_p4_denv CONTAINER_NO_PROXY)"
for v in P4_UID P4_GID P4_RENDER_GID P4_VIDEO_GID P4_CPROXY; do
  [[ -n "${!v}" ]] || die "$P4_DOCKER_ENV lacks the value for $v (Phase 1 step 6 contract)"
done
[[ -n "$P4_CPROXY_HTTP" ]] || P4_CPROXY_HTTP="$P4_CPROXY"
[[ -n "$P4_CNOPROXY" ]] || P4_CNOPROXY="localhost,127.0.0.1"

# HF_ENDPOINT (atlas.env, CONVENTIONS §3, normally unset): the pull sends the HF token as a Bearer header to this host,
# so it is forwarded to the container only when it is huggingface.co or a *.hf.co mirror; anything else stops here.
if [[ -n "${HF_ENDPOINT:-}" && ! "$HF_ENDPOINT" =~ ^https://(huggingface\.co|[a-z0-9.-]+\.hf\.co)/?$ ]]; then
  die "HF_ENDPOINT='$HF_ENDPOINT' is not https://huggingface.co or a *.hf.co host: refusing to send HF_TOKEN there (rule §7.2)"
fi

# Resource caps (header): memory from the json, cpus = nproc - 2.
P4_MEM_GB="$(p4_field mem_limit_gb)"
if [[ ! "$P4_MEM_GB" =~ ^[0-9]+$ ]]; then
  _fp="$(p4_field footprint_gb_expected)"
  [[ "$_fp" =~ ^[0-9]+$ ]] || _fp=8
  P4_MEM_GB=$(( _fp * 2 )); (( P4_MEM_GB < 16 )) && P4_MEM_GB=16
  unset _fp
fi
P4_CPUS=$(( $(nproc) - 2 )); (( P4_CPUS < 1 )) && P4_CPUS=1

# --- state, logging, cleanup ------------------------------------------------------------------------------------------
_atlas_state_init
mkdir -p "$P4_STATE" "$P4_MARKERS" "$P4_HOST_SRC" "$P4_HOST_DL" "$P4_SAMPLES"
chmod 700 "$P4_MARKERS"
for d in hf hf/manifests venv src dl manifests; do
  ensure_dir "$P4_ENGINES_DIR/$d" "$P4_UID:$P4_GID" 755
done
# The container HOME (and the MIOpen cache) receive whatever tools drop there (credential caches, ~/.config): 700.
ensure_dir "$P4_ENGINES_DIR/home" "$P4_UID:$P4_GID" 700
ensure_dir "$P4_ENGINES_DIR/miopen" "$P4_UID:$P4_GID" 700
ensure_dir "$P4_SAMPLES_DIR" "$P4_UID:$P4_GID" 755
ensure_dir "$P4_SAMPLES" "$P4_UID:$P4_GID" 755
ensure_dir "$P4_HOST_DL" "$P4_UID:$P4_GID" 755
# Everything this script prints goes to the journal (the driver's stdout) and to the per-engine log (rule: "logs to
# $ATLAS_STATE/logs/phase4-<key>.log").
exec > >(tee -a "$P4_LOG") 2>&1

P4_STARTED_AT="$(date -Is)"
P4_T0=$SECONDS
P4_BUILT=0
P4_RESULT_WRITTEN=0
P4_TEST_JSON=""
P4_FOOTPRINT_MB=""
P4_GTT_BASE=""
P4_GTT_PEAK=""
P4_OUTCOME=""
P4_GOT_TERM=0
P4_CONTAINERS=()
P4_NOTES=()

p4_note() { log "$P4_KEY: $*"; P4_NOTES+=("$*"); }

# p4_safe_read FILE — cat a file under the atlas-writable tree only when it is a regular file, never a symlink (a link
# planted by container code must not make root read or write an arbitrary host path). Returns 1 otherwise.
p4_safe_read() {
  local f="$1"
  [[ ! -L "$f" && -f "$f" ]] || return 1
  cat -- "$f"
}

# p4_write_result RESULT NOTE — merge the shell-side facts with the test JSON into $P4_RESULT (atomic).
p4_write_result() {
  local result="$1" note="${2:-}"
  local tail20
  tail20="$(tail -n 20 "$P4_LOG" 2>/dev/null || true)"
  local notes
  notes="$(printf '%s\n' "${P4_NOTES[@]}" "$note" | sed '/^$/d' | paste -sd ';' -)"
  local outcome="${P4_OUTCOME:-$result}"
  python3 - "$P4_RESULT" "$P4_KEY" "$P4_NAME" "$P4_TIER" "$result" "$P4_BUILT" "$P4_STARTED_AT" "$(( SECONDS - P4_T0 ))" \
            "${P4_TEST_JSON:-{\}}" "${P4_FOOTPRINT_MB:-}" "${P4_GTT_BASE:-}" "${P4_GTT_PEAK:-}" "$notes" "$tail20" \
            "${P4_EXIT_CODE:-}" "$outcome" <<'PY'
import json, os, sys, time
(path, key, name, tier, result, built, started, seconds, test_json, fp, base, peak, notes, tail, rc,
 outcome) = sys.argv[1:17]
try:
    t = json.loads(test_json) if test_json else {}
except json.JSONDecodeError:
    t = {}
rec = {
    "key": key, "name": name, "tier": tier, "result": result,
    "outcome": outcome,
    "blocking": tier == "green",
    "built": built == "1",
    "loaded": bool(t.get("loaded", False)),
    "sample_output_path": t.get("output"),
    "footprint_mb": int(fp) if fp else None,
    "gtt_baseline_mb": int(base) if base else None,
    "gtt_peak_mb": int(peak) if peak else None,
    "peak_alloc_mb": t.get("peak_alloc_mb"),
    "device": t.get("device"),
    "seconds": int(seconds),
    "test_seconds": t.get("seconds"),
    "load_seconds": t.get("load_seconds"),
    "started_at": started,
    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "exit_code": int(rc) if rc else None,
    "notes": "; ".join(x for x in [notes, t.get("notes") or ""] if x),
    "log_tail": tail.splitlines()[-20:],
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(rec, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
PY
  P4_RESULT_WRITTEN=1
  log "$P4_KEY: result=$result outcome=$outcome footprint_mb=${P4_FOOTPRINT_MB:-?} -> $P4_RESULT"
}

# The driver's `timeout` (no --foreground: the whole process group gets TERM, the docker clients included) lands here
# first; exit 143 so the EXIT trap knows it was the timeout and not a clean end.
_p4_on_term() { P4_GOT_TERM=1; exit 143; }
trap _p4_on_term TERM INT

# The EXIT trap: kill any container still running and make sure a result file exists whatever happened (rule §7.4:
# never silent). Containers are removed by the names this script handed out AND by the p4-<key>- prefix (belt and
# braces: a name registered in a subshell never reaches this array).
_p4_on_exit() {
  local rc=$?
  local c
  for c in "${P4_CONTAINERS[@]}"; do
    docker rm -f "$c" >/dev/null 2>&1 || true
  done
  docker ps -q --filter "name=^p4-${P4_KEY}-" 2>/dev/null | xargs -r docker rm -f >/dev/null 2>&1 || true
  if (( P4_RESULT_WRITTEN == 0 )); then
    # A TERM from the driver's timeout can leave $? at 0 (bash was waiting on a child): treat "ended without a result
    # and exit 0" as the timeout it is, never as a clean run.
    if (( P4_GOT_TERM == 1 )) || (( rc == 0 )); then rc=143; fi
    P4_EXIT_CODE="$rc"
    local res=fail
    [[ "$P4_TIER" == green ]] || res=deferred
    P4_OUTCOME=fail
    # The reason: the last die() message (kept in a variable, the log tee is asynchronous), else the log's FATAL line.
    local why="$P4_LAST_FATAL"
    [[ -n "$why" ]] || why="$(grep -a ' FATAL ' "$P4_LOG" 2>/dev/null | tail -n1 | sed 's/^.* FATAL //' || true)"
    [[ -n "$why" ]] || why="engine script ended with exit $rc before a result was written"
    (( rc == 124 || rc == 143 )) && why="killed by the driver's timeout (SIGTERM, json timeout_s; exit $rc): $why"
    p4_write_result "$res" "$why" || true
    # Exit code contract (header): 1 fail (green), 2 deferred (yellow/verify); a timeout keeps its own code.
    if (( rc != 124 && rc != 137 && rc != 143 )); then
      [[ "$res" == fail ]] && exit 1
      exit 2
    fi
  fi
}
trap _p4_on_exit EXIT

_p4_unique() { printf 'p4-%s-%s-%s' "$P4_KEY" "$$" "$RANDOM"; }

# --- container runs --------------------------------------------------------------------------------------------------
# _p4_gpu_flags — fills P4_GPU_FLAGS with the extra flags V11 proved necessary (header "GPU test runs"); dies when V11
# has not recorded them. Runs in the parent shell (never a subshell) so the die really stops the script.
P4_GPU_FLAGS=()
_p4_gpu_flags() {
  P4_GPU_FLAGS=()
  [[ -f "$P4_RUNFLAGS_FILE" && ! -L "$P4_RUNFLAGS_FILE" ]] \
    || die "$P4_RUNFLAGS_FILE missing: V11 (phase4-engines.sh step 01) has not recorded which container flags gfx1151 needs; run the driver, not this script, first"
  local line
  while IFS= read -r line; do
    case "$line" in
      ''|'#'*) ;;
      '--security-opt=seccomp=unconfined'|'--ipc=host') P4_GPU_FLAGS+=("$line") ;;
      *) die "$P4_RUNFLAGS_FILE holds an unexpected flag '$line' (only --security-opt=seccomp=unconfined and --ipc=host are accepted)" ;;
    esac
  done <"$P4_RUNFLAGS_FILE"
}

# p4_docker_run [--net] [--gpu] [--token] [--name NAME] [DOCKER_ARGS...] -- CMD...
#   See the header "Container run kinds". --net and --gpu are mutually exclusive (a GPU test never has the network;
#   install code never has the GPU). --token bind-mounts the secrets file read-only (gated repos only). Never -it:
#   nothing here may wait for input.
p4_docker_run() {
  local net=0 gpu=0 token=0 name="" args=()
  while (( $# > 0 )); do
    case "$1" in
      --net) net=1; shift ;;
      --gpu) gpu=1; shift ;;
      --token) token=1; shift ;;
      --name) name="$2"; shift 2 ;;
      --) shift; break ;;
      *) args+=("$1"); shift ;;
    esac
  done
  (( net && gpu )) && die "p4_docker_run: --net and --gpu together is not allowed (network runs never get the GPU)"
  [[ -n "$name" ]] || name="$(_p4_unique)"
  P4_CONTAINERS+=("$name")
  local run=(docker run --rm --name "$name"
    --user "$P4_UID:$P4_GID"
    --cap-drop ALL --security-opt no-new-privileges
    --pids-limit 4096 --memory "${P4_MEM_GB}g" --memory-swap "${P4_MEM_GB}g" --cpus "$P4_CPUS"
    -e HOME=/srv/atlas/engines/home
    -e "P4_LOAD_TIMEOUT_S=$P4_LOAD_TIMEOUT_S"
    -e HF_HUB_DISABLE_TELEMETRY=1 -e DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 -e HF_HUB_DISABLE_IMPLICIT_TOKEN=1
    -e HF_HUB_DISABLE_PROGRESS_BARS=1
    -v "$P4_ENGINES_DIR:/srv/atlas/engines"
    -v "$P4_SAMPLES_DIR:/srv/atlas/workspace/phase4-samples"
    -v "$P4_DAY1/phase4:/opt/atlas/phase4:ro")
  if (( gpu )); then
    _p4_gpu_flags
    run+=(--device /dev/kfd --device /dev/dri --group-add "$P4_VIDEO_GID" --group-add "$P4_RENDER_GID"
          "${P4_GPU_FLAGS[@]}")
  fi
  if (( net )); then
    run+=(-e "http_proxy=$P4_CPROXY_HTTP" -e "https_proxy=$P4_CPROXY" -e "no_proxy=$P4_CNOPROXY"
          -e "HTTP_PROXY=$P4_CPROXY_HTTP" -e "HTTPS_PROXY=$P4_CPROXY" -e "NO_PROXY=$P4_CNOPROXY"
          -e HF_HUB_ENABLE_HF_TRANSFER=0)
    [[ -n "${HF_ENDPOINT:-}" ]] && run+=(-e "HF_ENDPOINT=$HF_ENDPOINT")
  else
    run+=(--network none -e HF_HUB_OFFLINE=1)
  fi
  if (( token )); then
    [[ -s "$P4_TOKEN_FILE" ]] || die "$P4_TOKEN_FILE is absent or empty: a gated repo needs HF_TOKEN (phase2-services.sh writes it)"
    run+=(--tmpfs "/run/secrets:mode=700,uid=$P4_UID,gid=$P4_GID" -v "$P4_TOKEN_FILE:$P4_TOKEN_MOUNT:ro"
          -e "HF_TOKEN_FILE=$P4_TOKEN_MOUNT")
  fi
  run+=("${args[@]}" "$P4_IMAGE" "$@")
  "${run[@]}" </dev/null
}

# --- pulls and clones ------------------------------------------------------------------------------------------------
# p4_pull_repo REPO [GATED 0|1] [FALLBACK] [ALLOW_JSON] [REVISION] — one snapshot through the proxy (p4common.py pull).
# Exit 3 from the helper = licence not accepted: die with the exact URL (conflict 16); 4 = repo not found: die naming
# it; 5 = a file failed its sha256 check (deleted; re-run). The token is mounted for gated repos ONLY (rule §7.2).
p4_pull_repo() {
  local repo="$1" gated="${2:-0}" fallback="${3:-}" allow_json="${4:-}" revision="${5:-}"
  local args=(python3.12 /opt/atlas/phase4/engines/p4common.py pull "$repo")
  [[ -n "$fallback" ]] && args+=(--fallback "$fallback")
  [[ -n "$revision" ]] && args+=(--revision "$revision")
  if [[ -n "$allow_json" && "$allow_json" != "null" ]]; then
    local pat
    while IFS= read -r pat; do
      [[ -n "$pat" ]] && args+=(--allow "$pat")
    done < <(python3 -c 'import json,sys; [print(p) for p in json.loads(sys.argv[1])]' "$allow_json")
  fi
  local tok=()
  [[ "$gated" == "1" || "$gated" == "true" ]] && tok=(--token)
  log "$P4_KEY: pulling $repo${fallback:+ (fallback $fallback)}${revision:+ @$revision} through the proxy into $P4_ENGINES_DIR/hf${tok:+ (gated: token mounted)}"
  local errf rc=0
  errf="$(mktemp)"
  # stdout+stderr through one synchronous tee: live in the log, complete in $errf when the pipeline returns (no race).
  if ! p4_docker_run --net "${tok[@]}" -- "${args[@]}" 2>&1 | tee "$errf"; then
    rc="${PIPESTATUS[0]}"
  fi
  local url
  url="$(grep -o 'LICENCE_URL .*' "$errf" | tail -n1 | awk '{print $2}' || true)"
  rm -f "$errf"
  case "$rc" in
    0) return 0 ;;
    3) die "$P4_KEY: $repo is gated and the token is not accepted for it. Accept the licence at ${url:-https://huggingface.co/$repo} with the account that owns HF_TOKEN in $P4_TOKEN_FILE (and check that file is current), then re-run (delete $P4_RESULT first)" ;;
    4) die "$P4_KEY: $repo${fallback:+ (and $fallback)} does not exist on the hub: the research repo id (UNVERIFIED-by-snippet) is wrong; fix config/phase4-engines.json" ;;
    5) die "$P4_KEY: a file of $repo failed its sha256 check against the hub manifest and was deleted; re-run (see $P4_LOG)" ;;
    *) die "$P4_KEY: pull of $repo failed with exit $rc (proxy denial? allowlist? see $P4_LOG and /var/log/squid/access.log)" ;;
  esac
}

# p4_pull — every hf_repos[] entry of this engine's json. Fields are joined with US (0x1f), never TAB: TAB is IFS
# whitespace and an empty field would shift the columns (fix round).
p4_pull() {
  local repo gated fallback allow revision
  while IFS=$'\x1f' read -r repo gated fallback allow revision; do
    [[ -n "$repo" ]] || continue
    p4_pull_repo "$repo" "$gated" "$fallback" "$allow" "$revision"
  done < <(python3 - "$P4_JSON" "$P4_KEY" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
eng = next(e for e in doc["engines"] if e["key"] == sys.argv[2])
for r in eng.get("hf_repos", []):
    allow = r.get("allow_patterns")
    print("\x1f".join([r["repo"], "1" if r.get("gated") else "0", r.get("fallback_repo") or "",
                       json.dumps(allow) if allow else "", r.get("revision") or ""]))
PY
)
}

# p4_resolved_repo REPO — the repo id the pull actually used (fallback aware), from the manifest marker.
p4_resolved_repo() {
  local f="$P4_ENGINES_DIR/hf/manifests/${1//\//__}.resolved"
  local v
  if v="$(p4_safe_read "$f" 2>/dev/null)" && [[ -n "$v" ]]; then head -n1 <<<"$v"; else printf '%s\n' "$1"; fi
}

# p4_git_clone URL DIR [--recursive] — into $P4_ENGINES_DIR/src/DIR through the proxy; skipped when DIR/.git exists.
p4_git_clone() {
  local url="$1" dir="$2" rec="${3:-}"
  [[ -n "$dir" && "$dir" != */* && "$dir" != . ]] || die "$P4_KEY: p4_git_clone: bad checkout dir name '$dir'"
  if [[ -d "$P4_HOST_SRC/$dir/.git" ]]; then
    log "$P4_KEY: $P4_HOST_SRC/$dir already cloned; skipping"
    return 0
  fi
  local flags=(--depth 1)
  [[ "$rec" == "--recursive" ]] && flags+=(--recurse-submodules --shallow-submodules)
  log "$P4_KEY: git clone $url -> $P4_HOST_SRC/$dir"
  # A half-finished clone from an interrupted run is removed first (both parts are guarded against being empty).
  rm -rf "${P4_HOST_SRC:?}/${dir:?}"
  p4_docker_run --net -- git clone "${flags[@]}" "$url" "$P4_SRC/$dir" \
    || die "$P4_KEY: git clone $url failed (github.com allowlisted? see $P4_LOG)"
}

# p4_git_from_json — every git[] entry of this engine's json (US-separated, see p4_pull).
p4_git_from_json() {
  local url dir rec
  while IFS=$'\x1f' read -r url dir rec; do
    [[ -n "$url" ]] || continue
    p4_git_clone "$url" "$dir" "$rec"
  done < <(python3 - "$P4_JSON" "$P4_KEY" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
eng = next(e for e in doc["engines"] if e["key"] == sys.argv[2])
for g in eng.get("git", []):
    print("\x1f".join([g["url"], g["dir"], "--recursive" if g.get("recursive") else ""]))
PY
)
}

# --- venvs -----------------------------------------------------------------------------------------------------------
# p4_venv_create — /srv/atlas/engines/venv/<key> as a --system-site-packages venv over the image's ROCm torch.
p4_venv_create() {
  if [[ -x "$P4_HOST_VENV/bin/python" ]]; then
    return 0
  fi
  log "$P4_KEY: creating venv $P4_HOST_VENV (system site-packages: inherits the gfx1151 torch)"
  p4_docker_run -- python3.12 -m venv --system-site-packages "$P4_VENV" \
    || die "$P4_KEY: python3.12 -m venv failed"
  # The venv lives on the bind-mounted data volume: it must now be visible on the host, or the mount is wrong.
  [[ -x "$P4_HOST_VENV/bin/python" ]] \
    || die "$P4_KEY: the container reported success but $P4_HOST_VENV/bin/python does not exist on the host (bind mount $P4_ENGINES_DIR -> /srv/atlas/engines broken?)"
}

# p4_marker NAME — the root-only idempotence marker path for this engine (never inside the bind-mounted venv).
p4_marker() { printf '%s/%s-%s\n' "$P4_MARKERS" "$P4_KEY" "$1"; }

# p4_venv_freeze — record what the venv resolved (rule §7.9 audit artefact); written by the container as atlas, the
# path goes into the result notes so the Section 17 step 6 table points at it.
p4_venv_freeze() {
  p4_docker_run -- sh -c "\"$P4_VENV/bin/pip\" freeze --all > \"$P4_FREEZE\"" \
    || die "$P4_KEY: pip freeze into $P4_FREEZE failed"
  local n
  n="$(p4_safe_read "$P4_ENGINES_DIR/manifests/${P4_KEY}-freeze.txt" | wc -l)" || n="?"
  log "$P4_KEY: venv freeze ($n lines) -> $P4_ENGINES_DIR/manifests/${P4_KEY}-freeze.txt"
  local note="freeze: $P4_ENGINES_DIR/manifests/${P4_KEY}-freeze.txt"
  local x
  for x in "${P4_NOTES[@]}"; do [[ "$x" == "$note" ]] && return 0; done
  P4_NOTES+=("$note")
}

# p4_venv_pip ARGS... — pip in the engine venv, under the image's constraints file, through the proxy. Idempotent by a
# marker keyed on the argument list (a changed list re-installs; pip itself skips satisfied requirements).
p4_venv_pip() {
  p4_venv_create
  local hash marker
  hash="$(printf '%s\n' "$@" | sha256sum | cut -c1-16)"
  marker="$(p4_marker "pip-$hash")"
  if [[ -e "$marker" ]]; then
    log "$P4_KEY: pip step $hash already done; skipping"
    return 0
  fi
  log "$P4_KEY: pip install (constraints /opt/atlas/constraints-rocm.txt): $*"
  p4_docker_run --net -- "$P4_VENV/bin/pip" install -c /opt/atlas/constraints-rocm.txt "$@" \
    || die "$P4_KEY: pip install failed: $* (pypi.org / files.pythonhosted.org allowlisted? a pin that fights the ROCm torch? see $P4_LOG)"
  p4_venv_freeze
  date -Is >"$marker"
}

# p4_venv_from_json — pip[] of this engine's json (empty list = nothing to do).
p4_venv_from_json() {
  local pkgs=()
  mapfile -t pkgs < <(python3 -c 'import json,sys; [print(p) for p in json.loads(sys.argv[1] or "[]")]' "$(p4_field pip)")
  p4_venv_create
  (( ${#pkgs[@]} == 0 )) || p4_venv_pip "${pkgs[@]}"
}

# p4_in_venv [--net] [-e K=V ...] [-w DIR] -- CMD... — a command in the container with the venv first on PATH (no GPU).
p4_in_venv() {
  local pre=() net=()
  while (( $# > 0 )); do
    case "$1" in
      --net) net=(--net); shift ;;
      -e) pre+=(-e "$2"); shift 2 ;;
      -w) pre+=(-w "$2"); shift 2 ;;
      --) shift; break ;;
      *) pre+=("$1"); shift ;;
    esac
  done
  p4_docker_run "${net[@]}" -e "PATH=$P4_VENV/bin:/usr/local/bin:/usr/bin:/bin" -e "VIRTUAL_ENV=$P4_VENV" "${pre[@]}" -- "$@"
}

# p4_derive_image TAG < Dockerfile-on-stdin — a per-engine image FROM the base (research §4 item 3), built through the
# proxy, skipped when its label is present. Sets P4_IMAGE to TAG for the rest of the script.
p4_derive_image() {
  local tag="$1" label="org.atlas.phase4.$P4_KEY"
  if [[ "$(docker image inspect -f "{{index .Config.Labels \"$label\"}}" "$tag" 2>/dev/null)" == "1" ]]; then
    log "$P4_KEY: derived image $tag already built"
  else
    local ctx
    ctx="$(mktemp -d)"
    { echo "FROM $P4_IMAGE"; echo "LABEL $label=\"1\""; cat; } >"$ctx/Dockerfile"
    log "$P4_KEY: docker build $tag FROM $P4_IMAGE"
    if ! docker build -t "$tag" \
      --build-arg "http_proxy=$P4_CPROXY_HTTP" --build-arg "https_proxy=$P4_CPROXY" \
      --build-arg "HTTP_PROXY=$P4_CPROXY_HTTP" --build-arg "HTTPS_PROXY=$P4_CPROXY" \
      --build-arg "no_proxy=$P4_CNOPROXY" --build-arg "NO_PROXY=$P4_CNOPROXY" \
      "$ctx" </dev/null; then
      rm -rf "${ctx:?}"
      die "$P4_KEY: docker build of $tag failed (see $P4_LOG)"
    fi
    rm -rf "${ctx:?}"
  fi
  P4_IMAGE="$tag"
  export P4_IMAGE
}

# --- the test run ----------------------------------------------------------------------------------------------------
# _p4_gtt_used_mb — the host GTT counter (lib/common.sh). P4_GTT_FAKE_MB is honoured ONLY outside the systemd unit and
# ONLY on a host without an AMD GPU (the library's own self-test); on the node it is refused by name, so a footprint
# registered with the Arbiter is never a fabricated number without the result JSON saying so. Checked once here, at
# load time (the sampler runs in command substitutions, where a die would not reach the parent).
if [[ -n "${P4_GTT_FAKE_MB:-}" ]]; then
  if [[ "${ATLAS_IN_UNIT:-0}" == "1" ]] || gpu_card_device_dir >/dev/null 2>&1; then
    die "P4_GTT_FAKE_MB is set but this is the node (AMD GPU present or running in the unit): refusing a fabricated footprint; unset P4_GTT_FAKE_MB"
  fi
  warn "$P4_KEY: P4_GTT_FAKE_MB=$P4_GTT_FAKE_MB in use (no AMD GPU on this host): the footprint is FAKED"
  P4_NOTES+=("footprint FAKED (P4_GTT_FAKE_MB)")
fi
_p4_gtt_used_mb() {
  if [[ -n "${P4_GTT_FAKE_MB:-}" ]]; then printf '%s\n' "$P4_GTT_FAKE_MB"; else gpu_gtt_used_mb; fi
}

# _p4_gtt_settle — Section 4.2 rule 5: before loading, confirm the previous engine's memory is really released by
# polling the counter, not by trusting a process exit. Two consecutive 2 s samples within 64 MiB = settled; 60 s cap.
_p4_gtt_settle() {
  local prev now i
  prev="$(_p4_gtt_used_mb)"
  for (( i = 0; i < 30; i++ )); do
    sleep 2
    now="$(_p4_gtt_used_mb)"
    if (( now - prev < 64 && prev - now < 64 )); then
      printf '%s\n' "$now"
      return 0
    fi
    prev="$now"
  done
  warn "$P4_KEY: GTT counter still moving after 60 s (last ${now} MiB): a previous container may still be draining; baseline taken anyway"
  P4_NOTES+=("GTT baseline taken while the counter was still moving (${now} MiB)")
  printf '%s\n' "$now"
}

# p4_run_test [--python PATH] [-e K=V ...] [SETTING=VALUE ...] — <key>_test.py in the venv, network off, HF offline,
# the host GTT counter sampled every 2 s; footprint_mb = peak - baseline. Sets P4_TEST_JSON, P4_FOOTPRINT_MB,
# P4_GTT_BASE, P4_GTT_PEAK. Returns 0 when the test reported ok, 1 otherwise (never dies: the caller decides).
p4_run_test() {
  local py="$P4_VENV/bin/python" pre=() settings=()
  while (( $# > 0 )); do
    case "$1" in
      --python) py="$2"; shift 2 ;;
      -e) pre+=(-e "$2"); shift 2 ;;
      *) settings+=("$1"); shift ;;
    esac
  done
  [[ -f "$P4_DAY1/phase4/engines/${P4_KEY}_test.py" ]] || die "$P4_DAY1/phase4/engines/${P4_KEY}_test.py is missing"
  local base peak now
  base="$(_p4_gtt_settle)"
  P4_GTT_BASE="$base"; peak="$base"
  local outf name
  outf="$(mktemp)"
  name="$(_p4_unique)"
  # Registered in the parent BEFORE the backgrounded run: an array append inside the `&` subshell never reaches the
  # EXIT trap's cleanup loop (fix round: the hung-load case would leave the container holding GTT).
  P4_CONTAINERS+=("$name")
  log "$P4_KEY: test run ($py $P4_TEST_PY --out /srv/atlas/workspace/phase4-samples/$P4_KEY ${settings[*]:-}); GTT baseline ${base} MiB (settled); mem cap ${P4_MEM_GB}g cpus $P4_CPUS"
  p4_docker_run --gpu --name "$name" "${pre[@]}" -- \
    "$py" "$P4_TEST_PY" --out "/srv/atlas/workspace/phase4-samples/$P4_KEY" --load-timeout "$P4_LOAD_TIMEOUT_S" \
    "${settings[@]}" >"$outf" &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 2
    now="$(_p4_gtt_used_mb 2>/dev/null || echo "$peak")"
    (( now > peak )) && peak="$now"
  done
  local rc=0
  wait "$pid" || rc=$?
  P4_GTT_PEAK="$peak"
  P4_FOOTPRINT_MB=$(( peak - base ))
  (( P4_FOOTPRINT_MB < 0 )) && P4_FOOTPRINT_MB=0
  P4_TEST_JSON="$(grep -a '^P4RESULT ' "$outf" | tail -n1 | sed 's/^P4RESULT //' || true)"
  # Anything else the test printed on stdout belongs in the log.
  grep -av '^P4RESULT ' "$outf" || true
  rm -f "$outf"
  log "$P4_KEY: test exit $rc; GTT peak ${peak} MiB, delta ${P4_FOOTPRINT_MB} MiB"
  if [[ -z "$P4_TEST_JSON" ]]; then
    p4_note "test produced no P4RESULT line (exit $rc)"
    return 1
  fi
  python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("ok") else 1)' "$P4_TEST_JSON" 2>/dev/null
}

# --- main ------------------------------------------------------------------------------------------------------------
# p4_main — the skeleton every engine script ends with. Reads p4_build (required) and P4_TEST_SETTINGS (optional array
# of key=value settings for the test, e.g. repo ids resolved at build time).
p4_main() {
  log "== $P4_KEY ($P4_NAME, tier $P4_TIER) image $P4_IMAGE log $P4_LOG"
  if [[ -f "$P4_RESULT" ]] && python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("result")=="pass" else 1)' "$P4_RESULT" 2>/dev/null; then
    log "$P4_KEY: already passed ($P4_RESULT); delete that file to rebuild"
    P4_RESULT_WRITTEN=1
    exit 0
  fi
  declare -F p4_build >/dev/null || die "$P4_KEY: the engine script defines no p4_build()"
  # A build failure is a `die` inside p4_build -> EXIT trap -> fail (green) or deferred (yellow/verify) with log tail.
  p4_build
  P4_BUILT=1
  local settings=()
  if declare -p P4_TEST_SETTINGS >/dev/null 2>&1; then settings=("${P4_TEST_SETTINGS[@]}"); fi
  if p4_run_test "${settings[@]}"; then
    P4_EXIT_CODE=0
    P4_OUTCOME=pass
    p4_write_result pass ""
    exit 0
  fi
  P4_OUTCOME=fail
  if [[ "$P4_TIER" == green ]]; then
    P4_EXIT_CODE=1
    p4_write_result fail "test did not pass"
    exit 1
  fi
  # Section 17 step 3 "log pass or fail, never block": outcome=fail is kept; result=deferred per CONVENTIONS §7.4.
  P4_EXIT_CODE=2
  p4_write_result deferred "test did not pass (tier $P4_TIER never blocks; outcome=fail)"
  exit 2
}
