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
#   Optional, set inside p4_build: P4_TEST_SETTINGS=(key=value ...) for the test's argv; P4_TEST_ENV=(K=V ...) for its
#   environment.
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
#     CONTAINER_HTTPS_PROXY CONTAINER_NO_PROXY.   /etc/atlas/secrets/hf-token.env (phase2-services.sh): HF_TOKEN=...,
#     owned by the atlas uid with mode 600 (CONVENTIONS §2; checked before a gated pull, see "HF_TOKEN" below).
# Container layout (fixed; the image's ENV points here): /srv/atlas/engines/{hf,venv/<key>,src/<dir>,dl/<key>,<key>,
# miopen,manifests/<key>} = the same paths under $ATLAS_SRV/engines, /srv/atlas/workspace/phase4-samples/<key> =
# $ATLAS_SRV/workspace/phase4-samples/<key>, /opt/atlas/phase4 = $ATLAS_DAY1_DIR/phase4 read-only (tests, p4common.py).
#
# Container run kinds (p4_docker_run; fix round 3: three kinds, each mounting only what it needs, nothing root-held on
# a mount, and NO run gets the shared hf/ or src/ trees read-write except the one that fills them):
#   pull runs (--pull, p4_pull_repo only): the allowlist proxy, NO GPU, hf/ READ-WRITE (the one writer of the shared
#     cache), the token mount for gated repos (below), and nothing else: no venv, src, dl, private or manifests mount.
#     The only code that runs is the image's own huggingface_hub/diffusers and phase4/engines/p4common.py (`-I -P`).
#   build runs (no flag, or --net for the proxy): NO GPU, the image's default seccomp profile, --cap-drop ALL,
#     no-new-privileges, no --ipc. Mounts: hf/ READ-ONLY, venv/<key> RW, src/<dir> RW for THIS engine's json git[]
#     dirs only (never the src/ tree), dl/<key> RW, <key>/ (the private dir) RW, manifests/<key>/ RW (this engine's pip
#     freezes). These execute third-party install code (setup.py / build backends of the clones, unpinned PyPI
#     packages, torch.hub and rembg download code), so they can alter nothing of another engine: not its weights, its
#     checkout, its venv or its freeze (Section 16.4: an OS-level cap is the guard, not a review layer).
#   GPU test runs (--gpu, p4_run_test only): --network none, HF_HUB_OFFLINE=1, /dev/kfd + /dev/dri, the numeric
#     video/render gids, --cap-drop ALL, no-new-privileges, and ONLY the extra flags V11 proved necessary
#     ($ATLAS_STATE/phase4/v11-runflags.txt, root-held 644, written by verify/v11-rocm-selftest.sh: empty when the
#     default seccomp profile passed, else `--security-opt=seccomp=unconfined` and/or `--ipc=host`, each probed
#     separately there; refused unless owned by uid 0). SYS_PTRACE is never added (research §6.2 "only for
#     debuggers/profilers"). Mounts: hf/, venv/<key>, src/<dir>, dl/<key>, <key>/ READ-ONLY (remote code run here
#     cannot alter any engine's weights, venv or checkout); phase4-samples/<key> and miopen/ RW.
#   Before the GPU run, p4_main re-runs the host-side sha256 check of every repo this engine pulled against the
#     root-held manifests (p4_verify_pulls): an engine whose weights changed after the pull is never tested, never
#     registered with the Arbiter.
#   HOME (fix round 3): every run gets a throw-away HOME=/tmp on a private tmpfs. The persisted directory
#     $ATLAS_SRV/engines/home is mounted ONLY at the three cache paths that must survive between runs, under explicit
#     variables: TORCH_HOME=/srv/atlas/engines/home/.cache/torch (torch.hub code + weights: TRELLIS's DINOv2),
#     U2NET_HOME=/srv/atlas/engines/home/.u2net (rembg), HF_MODULES_CACHE=/srv/atlas/engines/home/.cache/hf-modules
#     (transformers trust_remote_code modules: OpenVLA). No dotfile (.curlrc, .netrc, .cargo/config.toml,
#     .pydistutils.cfg, pip.conf, .gitconfig) can travel from a GPU test into a later network-enabled build run; in
#     addition PYTHONNOUSERSITE=1, PIP_CONFIG_FILE=/dev/null, GIT_CONFIG_GLOBAL=/dev/null, GIT_CONFIG_NOSYSTEM=1 and
#     CARGO_HOME=/tmp/cargo are set. The cache sub-directories are created inside home/ by a container running as atlas.
#   Every run: --pids-limit 4096, --memory/--memory-swap = json mem_limit_gb (default max(16, 2 x
#     footprint_gb_expected); engines that load through the no-mmap path hold the file bytes AND the decoded tensors at
#     once, so their json sets a larger explicit value), --cpus = nproc - 2 (the orchestrator and Redis keep two cores).
#     amdgpu GTT allocations are NOT charged to the container cgroup (TTM pages are not memcg-accounted), so --memory
#     bounds the CPU-side allocations only; the host GTT delta remains the footprint measure (json footprint_rule).
#   HF_TOKEN (gated repos only, --token, pull runs only): /etc/atlas/secrets/hf-token.env (CONVENTIONS §2: atlas:atlas
#     600, written by phase2-services.sh) is bind-mounted READ-ONLY at /run/secrets/hf-token.env. A bind mount keeps
#     the host inode's owner and mode, so the container uid (= the host atlas uid) reads it directly: no copy anywhere
#     (fix round 3: the earlier staged copy under /run could outlive a SIGKILLed run; rule §7.2 "secrets live only
#     under /etc/atlas/secrets"). p4_pull_repo dies (rule §7.4) when the file is not owned by ATLAS_UID with mode 600.
#     The token is never an --env-file (not in Config.Env, not inherited by every subprocess); the pull runs
#     `python3.12 -I -P` (no user site, no PYTHONPATH, no cwd on sys.path) with the throw-away HOME while HF_HOME
#     stays on the hf/ mount. HF_HUB_DISABLE_XET=1 on every network run (and in the image): huggingface_hub >= 1.0
#     downloads LFS/Xet files through the hf_xet Rust client by default, whose proxy handling is UNVERIFIED, so the
#     plain HTTP client that honours https_proxy is used (remove it only after one pull is observed in
#     /var/log/squid/access.log going through cas-bridge.xethub.hf.co).
#   Records (fix round 2): the hub listing, the revision pin and the resolved repo id of every pull are printed by
#     p4common.py as tagged lines and stored ROOT-HELD under $ATLAS_STATE/phase4/hf-manifests/<org>__<repo>.{json,
#     pinned,resolved}; git checkouts are pinned in $ATLAS_STATE/phase4/git-pins.json. Root never opens a path under
#     $ATLAS_SRV for writing (a symlink planted by container code could redirect it): it creates a missing directory
#     ONCE (_p4_dir_once: never chmod/chown of an existing path, never through a symlink) and reads there only through
#     p4_safe_read / the realpath-checked sha256 re-verification. Appendix C backs the engines volume up "manifest
#     only" and phase2/atlas-aegis.sh collects <engines>/<key>/MANIFEST.json: after every pull, clone pin and freeze
#     this library composes that file from the root-held records (p4_manifest_publish) and a container running as
#     atlas copies it into the engine's private dir, so the pull records reach the backup set (fix round 3).
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
P4_HF_MANIFESTS="$P4_STATE/hf-manifests"                      # root-held pull records (fix round 2)
P4_GIT_PINS="$P4_STATE/git-pins.json"                         # root-held clone pins (fix round 2)
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
P4_DL="/srv/atlas/engines/dl/$P4_KEY"                        # container path
P4_HOST_DL="$P4_ENGINES_DIR/dl/$P4_KEY"
P4_PRIVATE="/srv/atlas/engines/$P4_KEY"                      # container path: the engine's own tree (blender unpack ...)
P4_HOST_PRIVATE="$P4_ENGINES_DIR/$P4_KEY"
P4_HOME="/srv/atlas/engines/home"                            # container path of the PERSISTED cache base (not $HOME)
P4_HOST_HOME="$P4_ENGINES_DIR/home"
P4_CACHE_SUBDIRS=(.cache/torch .cache/hf-modules .u2net)      # the only parts of home/ any run mounts (header "HOME")
P4_TOKEN_FILE="$ATLAS_ETC/secrets/hf-token.env"
P4_TOKEN_MOUNT="/run/secrets/hf-token.env"                   # container path (p4common.py reads it)
P4_DOCKER_ENV="$ATLAS_ETC/docker.env"
P4_RUNFLAGS_FILE="$P4_STATE/v11-runflags.txt"                # written by verify/v11-rocm-selftest.sh (root-held)
P4_MANIFESTS="/srv/atlas/engines/manifests/$P4_KEY"          # container path: this engine's freezes (RW in build runs)
P4_HOST_MANIFESTS="$P4_ENGINES_DIR/manifests/$P4_KEY"
P4_FREEZE="$P4_MANIFESTS/freeze.txt"                         # container path
P4_MANIFEST_JSON="$P4_STATE/manifests/$P4_KEY.json"          # root-held MANIFEST.json, published as atlas (header)
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

# HF_ENDPOINT (atlas.env, CONVENTIONS §3, normally unset) is honoured for every pull like lib/common.sh hf_download
# does (fix round 2: a private mirror must not fail twelve public engines). Only the TOKEN is bound to the host: a
# gated repo behind a non-Hugging-Face endpoint is refused in p4_pull_repo (rule §7.2), nothing dies at source time.
P4_HF_HOST_OK=1
if [[ -n "${HF_ENDPOINT:-}" && ! "$HF_ENDPOINT" =~ ^https://(huggingface\.co|[a-z0-9.-]+\.hf\.co)/?$ ]]; then
  P4_HF_HOST_OK=0
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
mkdir -p "$P4_STATE" "$P4_MARKERS" "$P4_HF_MANIFESTS" "$P4_STATE/manifests"
chmod 700 "$P4_MARKERS" "$P4_HF_MANIFESTS"
chmod 755 "$P4_STATE/manifests"      # the composed MANIFEST.json is handed to a container running as atlas (bind mount)
# Leftovers of the fix-round-2 staged token copies (/run/atlas-p4-<pid>/hf-token.env) from a run of an earlier
# revision that was SIGKILLed before its trap ran: no current revision stages anything, so every such directory is
# stale; shred the copy and remove it (rule §7.2: no secret copy outside /etc/atlas/secrets).
for _stale in /run/atlas-p4-*; do
  [[ -d "$_stale" && ! -L "$_stale" ]] || continue
  [[ -f "$_stale/hf-token.env" ]] && { shred -u "$_stale/hf-token.env" 2>/dev/null || rm -f "$_stale/hf-token.env"; }
  rm -rf "$_stale"
  warn "$P4_KEY: removed a stale staged-token directory $_stale left by an earlier revision"
done
unset _stale

# _p4_dir_once PATH MODE — create a directory on the atlas-writable tree ONCE, owned by the container uid. Never chmod or
# chown an existing path (both follow a symlink that container code could plant there) and never create through a
# symlink (fix round 3: the last root write-through under $ATLAS_SRV). Mode is set only at creation.
_p4_dir_once() {
  local d="$1" mode="$2"
  [[ ! -L "$d" ]] || die "$P4_KEY: $d is a symlink on the atlas-writable tree (planted by container code?); refusing to use it"
  if [[ ! -d "$d" ]]; then
    install -d -m "$mode" -o "$P4_UID" -g "$P4_GID" -- "$d" || die "$P4_KEY: could not create $d"
  fi
}
for d in hf venv src dl manifests; do
  _p4_dir_once "$P4_ENGINES_DIR/$d" 755
done
# The per-engine mount points must exist before docker mounts them (docker would create a root-owned directory that
# the container's atlas uid cannot write). src/<dir> is mounted per json git[] entry (header: never the src/ tree).
_p4_dir_once "$P4_HOST_VENV" 755
_p4_dir_once "$P4_HOST_DL" 755
_p4_dir_once "$P4_HOST_PRIVATE" 755
_p4_dir_once "$P4_HOST_MANIFESTS" 755
P4_SRC_DIRS=()
mapfile -t P4_SRC_DIRS < <(python3 - "$P4_JSON" "$P4_KEY" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
eng = next(e for e in doc["engines"] if e["key"] == sys.argv[2])
for g in eng.get("git", []):
    print(g["dir"])
PY
)
for d in "${P4_SRC_DIRS[@]}"; do
  [[ -n "$d" && "$d" != */* && "$d" != . && "$d" != .. ]] || die "$P4_KEY: json git[].dir '$d' is not a plain directory name"
  _p4_dir_once "$P4_HOST_SRC/$d" 755
done
# The persisted cache base and the MIOpen cache hold what tools drop there (kernel DBs, hub checkouts): 700.
_p4_dir_once "$P4_HOST_HOME" 700
_p4_dir_once "$P4_ENGINES_DIR/miopen" 700
_p4_dir_once "$P4_SAMPLES_DIR" 755
_p4_dir_once "$P4_SAMPLES" 755
# Everything this script prints goes to the journal (the driver's stdout) and to the per-engine log (rule: "logs to
# $ATLAS_STATE/logs/phase4-<key>.log").
exec > >(tee -a "$P4_LOG") 2>&1
# The driver's `timeout` TERMs the whole process group, the tee above included; a later log line would then hit a pipe
# with no reader and SIGPIPE would kill bash INSIDE the EXIT trap (exit 141, the 0/1/2 contract skipped) in a
# --foreground run. Ignoring the signal turns that into a harmless EPIPE write error (fix round 2).
trap '' PIPE

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
# braces: a name registered in a subshell never reaches this array). No secret copy exists to remove (header "HF_TOKEN").
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

# --- the token file (header "HF_TOKEN") ---------------------------------------------------------------------------------
# _p4_token_check — the secrets file must be a regular file owned by the container uid with mode 600 (CONVENTIONS §2),
# or the read-only bind mount would hand the container nothing it can read. Prints the reason on failure, returns 1.
_p4_token_check() {
  local f="$P4_TOKEN_FILE" own mode
  [[ ! -L "$f" && -s "$f" ]] || { echo "$f is absent, empty or a symlink"; return 1; }
  own="$(stat -c %u "$f")"; mode="$(stat -c %a "$f")"
  [[ "$own" == "$P4_UID" ]] || { echo "$f is owned by uid $own, not the atlas uid $P4_UID (CONVENTIONS §2: atlas:atlas 600): chown atlas:atlas $f"; return 1; }
  [[ "$mode" == 600 ]] || { echo "$f has mode $mode, not 600 (CONVENTIONS §2): chmod 600 $f"; return 1; }
  return 0
}

# --- container runs --------------------------------------------------------------------------------------------------
# _p4_gpu_flags — fills P4_GPU_FLAGS with the extra flags V11 proved necessary (header "GPU test runs"); dies when V11
# has not recorded them or the file is not root's. Called by p4_run_test in the PARENT shell before the backgrounded
# run (fix round 2: a die inside the `&` subshell would only end that subshell); p4_docker_run reuses the result.
P4_GPU_FLAGS=()
P4_GPU_FLAGS_READY=0
_p4_gpu_flags() {
  P4_GPU_FLAGS=()
  [[ -f "$P4_RUNFLAGS_FILE" && ! -L "$P4_RUNFLAGS_FILE" ]] \
    || die "$P4_RUNFLAGS_FILE missing: V11 (phase4-engines.sh step 01) has not recorded which container flags gfx1151 needs; run the driver, not this script, first"
  [[ "$(stat -c %u "$P4_RUNFLAGS_FILE")" == 0 ]] \
    || die "$P4_RUNFLAGS_FILE is not owned by root (uid $(stat -c %u "$P4_RUNFLAGS_FILE")): refusing to take GPU container flags from it"
  local line
  while IFS= read -r line; do
    case "$line" in
      ''|'#'*) ;;
      '--security-opt=seccomp=unconfined'|'--ipc=host') P4_GPU_FLAGS+=("$line") ;;
      *) die "$P4_RUNFLAGS_FILE holds an unexpected flag '$line' (only --security-opt=seccomp=unconfined and --ipc=host are accepted)" ;;
    esac
  done <"$P4_RUNFLAGS_FILE"
  P4_GPU_FLAGS_READY=1
}

# _p4_home_init — create the persisted cache sub-directories inside home/ with a container running as atlas (root never
# creates paths below an atlas-writable directory: home/.cache could itself be a planted symlink). Then refuse any of
# them that is a symlink or not a directory on the host. Cheap and idempotent; run once at source time.
_p4_home_init() {
  local name; name="$(_p4_unique)"
  P4_CONTAINERS+=("$name")
  # shellcheck disable=SC2016  # "$@" are the container shell's positional parameters (the sub-directories)
  timeout -k 10 120 docker run --rm --name "$name" --network none --cap-drop ALL --security-opt no-new-privileges \
      --user "$P4_UID:$P4_GID" --pids-limit 64 --memory 1g --memory-swap 1g \
      -v "$P4_HOST_HOME:$P4_HOME" --entrypoint sh "$P4_IMAGE" -c 'cd "$0" && for d in "$@"; do mkdir -p -- "$d" && chmod 700 -- "$d"; done' \
      "$P4_HOME" "${P4_CACHE_SUBDIRS[@]}" </dev/null \
    || die "$P4_KEY: could not create the cache directories ${P4_CACHE_SUBDIRS[*]} under $P4_HOST_HOME as atlas"
  local d
  for d in "${P4_CACHE_SUBDIRS[@]}"; do
    [[ ! -L "$P4_HOST_HOME/$d" && -d "$P4_HOST_HOME/$d" ]] \
      || die "$P4_KEY: $P4_HOST_HOME/$d is not a plain directory after the container created it (a planted symlink?)"
  done
}

# p4_docker_run [--pull|--net|--gpu] [--token] [--name NAME] [DOCKER_ARGS...] -- CMD...
#   See the header "Container run kinds". Exactly one kind: --pull (hf/ RW, network, nothing else mounted), --net (a
#   build run with the proxy), --gpu (the test run, no network), or none (a build run without network). --token (pull
#   runs only) bind-mounts the secrets file read-only (gated repos; the caller has checked owner and mode with
#   _p4_token_check in the parent shell: this function returns 1 with a WARN, it never dies, because the pull runs it
#   inside a pipeline where a die would be lost). Never -it: nothing here may wait for input.
#   Engine scripts may add mounts through P4_BUILD_MOUNTS / P4_TEST_MOUNTS (arrays of docker -v specs).
p4_docker_run() {
  local net=0 gpu=0 pull=0 token=0 name="" args=()
  while (( $# > 0 )); do
    case "$1" in
      --net) net=1; shift ;;
      --gpu) gpu=1; shift ;;
      --pull) pull=1; net=1; shift ;;
      --token) token=1; shift ;;
      --name) name="$2"; shift 2 ;;
      --) shift; break ;;
      *) args+=("$1"); shift ;;
    esac
  done
  if (( net && gpu )); then
    warn "p4_docker_run: --net/--pull and --gpu together is not allowed (network runs never get the GPU)"
    return 1
  fi
  if (( token && ! pull )); then
    warn "p4_docker_run: --token is only allowed on a --pull run (the token reaches no build or test container)"
    return 1
  fi
  [[ -n "$name" ]] || name="$(_p4_unique)"
  P4_CONTAINERS+=("$name")
  # Throw-away HOME on a private tmpfs for EVERY run (header "HOME"); the persisted caches are mounted at explicit paths.
  local run=(docker run --rm --name "$name"
    --user "$P4_UID:$P4_GID"
    --cap-drop ALL --security-opt no-new-privileges
    --pids-limit 4096 --memory "${P4_MEM_GB}g" --memory-swap "${P4_MEM_GB}g" --cpus "$P4_CPUS"
    --tmpfs "/tmp:mode=700,uid=$P4_UID,gid=$P4_GID" -e HOME=/tmp -e XDG_CACHE_HOME=/tmp/.cache
    -e "P4_LOAD_TIMEOUT_S=$P4_LOAD_TIMEOUT_S"
    -e HF_HUB_DISABLE_TELEMETRY=1 -e DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 -e HF_HUB_DISABLE_IMPLICIT_TOKEN=1
    -e HF_HUB_DISABLE_PROGRESS_BARS=1 -e HF_HUB_DISABLE_XET=1
    -e PYTHONNOUSERSITE=1 -e PIP_CONFIG_FILE=/dev/null -e GIT_CONFIG_GLOBAL=/dev/null -e GIT_CONFIG_NOSYSTEM=1
    -e CARGO_HOME=/tmp/cargo -e CURL_HOME=/tmp
    -e "HF_ASSETS_CACHE=/tmp/.cache/hf-assets"
    -v "$P4_DAY1/phase4:/opt/atlas/phase4:ro")
  if (( ! pull )); then
    # Build and test runs: the three persisted caches (header "HOME"), each at its own path, nothing else of home/.
    run+=(-e "TORCH_HOME=$P4_HOME/.cache/torch" -v "$P4_HOST_HOME/.cache/torch:$P4_HOME/.cache/torch"
          -e "HF_MODULES_CACHE=$P4_HOME/.cache/hf-modules" -v "$P4_HOST_HOME/.cache/hf-modules:$P4_HOME/.cache/hf-modules"
          -e "U2NET_HOME=$P4_HOME/.u2net" -v "$P4_HOST_HOME/.u2net:$P4_HOME/.u2net")
  fi
  if (( gpu )); then
    (( P4_GPU_FLAGS_READY )) || _p4_gpu_flags
    run+=(--device /dev/kfd --device /dev/dri --group-add "$P4_VIDEO_GID" --group-add "$P4_RENDER_GID"
          "${P4_GPU_FLAGS[@]}"
          -v "$P4_ENGINES_DIR/hf:/srv/atlas/engines/hf:ro"
          -v "$P4_HOST_VENV:$P4_VENV:ro"
          -v "$P4_HOST_DL:$P4_DL:ro"
          -v "$P4_HOST_PRIVATE:$P4_PRIVATE:ro"
          -v "$P4_ENGINES_DIR/miopen:/srv/atlas/engines/miopen"
          -v "$P4_SAMPLES:/srv/atlas/workspace/phase4-samples/$P4_KEY")
    local d; for d in "${P4_SRC_DIRS[@]}"; do run+=(-v "$P4_HOST_SRC/$d:$P4_SRC/$d:ro"); done
    if declare -p P4_TEST_MOUNTS >/dev/null 2>&1 && (( ${#P4_TEST_MOUNTS[@]} > 0 )); then
      local m; for m in "${P4_TEST_MOUNTS[@]}"; do run+=(-v "$m"); done
    fi
  elif (( pull )); then
    run+=(-v "$P4_ENGINES_DIR/hf:/srv/atlas/engines/hf")
  else
    run+=(-v "$P4_ENGINES_DIR/hf:/srv/atlas/engines/hf:ro"
          -v "$P4_HOST_VENV:$P4_VENV"
          -v "$P4_HOST_DL:$P4_DL"
          -v "$P4_HOST_PRIVATE:$P4_PRIVATE"
          -v "$P4_HOST_MANIFESTS:$P4_MANIFESTS")
    local d; for d in "${P4_SRC_DIRS[@]}"; do run+=(-v "$P4_HOST_SRC/$d:$P4_SRC/$d"); done
    if declare -p P4_BUILD_MOUNTS >/dev/null 2>&1 && (( ${#P4_BUILD_MOUNTS[@]} > 0 )); then
      local m; for m in "${P4_BUILD_MOUNTS[@]}"; do run+=(-v "$m"); done
    fi
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
    local why
    if ! why="$(_p4_token_check)"; then
      warn "p4_docker_run --token: $why"
      return 1
    fi
    # The secrets file itself, read-only; the bind mount keeps the host inode's atlas:atlas 600 (header "HF_TOKEN").
    run+=(-v "$P4_TOKEN_FILE:$P4_TOKEN_MOUNT:ro" -e "HF_TOKEN_FILE=$P4_TOKEN_MOUNT")
  fi
  run+=("${args[@]}" "$P4_IMAGE" "$@")
  "${run[@]}" </dev/null
}

# The persisted cache sub-directories must exist (as atlas) before the first p4_docker_run bind-mounts them: docker
# would otherwise create root-owned directories the container cannot write (header "HOME").
_p4_home_init

# --- pulls and clones ------------------------------------------------------------------------------------------------
_p4_repo_slug() { printf '%s' "${1//\//__}"; }

# _p4_host_verify REPO SHA MANIFEST — re-run the sha256 check on the host as root, read-only, against the root-held
# manifest (fix round 2: the in-container check runs as atlas in the same sandbox it protects). Snapshot entries are the
# hub's symlinks into blobs/: each is resolved and must stay inside the repo's storage folder under hf/hub.
_p4_host_verify() {
  python3 - "$P4_ENGINES_DIR/hf/hub" "$1" "$2" "$3" <<'PY'
import hashlib, json, os, sys
hub, repo, sha, manifest = sys.argv[1:5]
storage = os.path.realpath(os.path.join(hub, "models--" + repo.replace("/", "--")))
snap = os.path.join(storage, "snapshots", sha)
files = json.load(open(manifest, encoding="utf-8"))["files"]
bad = []
checked = 0
for f in files:
    want = f.get("sha256")
    if not want:
        continue
    p = os.path.join(snap, f["name"])
    real = os.path.realpath(p)
    if not real.startswith(storage + os.sep) or not os.path.isfile(real):
        bad.append(f"{f['name']}: resolves outside the repo storage or is not a regular file ({real})")
        continue
    fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    h = hashlib.sha256()
    with os.fdopen(fd, "rb") as fh:
        for chunk in iter(lambda: fh.read(16 * 2**20), b""):
            h.update(chunk)
    if h.hexdigest() != want:
        bad.append(f"{f['name']}: sha256 {h.hexdigest()} != {want}")
    checked += 1
if bad:
    print("\n".join(bad))
    sys.exit(1)
print(f"{checked} LFS files re-verified on the host")
PY
}

# p4_pull_repo REPO [GATED 0|1] [FALLBACK] [ALLOW_JSON] [REVISION] — one snapshot through the proxy (p4common.py pull).
# Preconditions are checked HERE, in the parent shell, before the pipeline (fix round 2: a die inside the pipeline
# would be lost): a gated repo needs the secrets file and a Hugging Face endpoint. Exit 3 from the helper = licence not
# accepted: die with the exact URL (conflict 16); 4 = repo not found; 5 = sha256 mismatch (deleted; re-run); 6 =
# pulled but not resolvable offline. The token file is mounted for gated repos ONLY (rule §7.2). On success the tagged
# records go root-held under $P4_HF_MANIFESTS, the sha256s are re-verified on the host and MANIFEST.json is published.
p4_pull_repo() {
  local repo="$1" gated="${2:-0}" fallback="${3:-}" allow_json="${4:-}" revision="${5:-}"
  local slug; slug="$(_p4_repo_slug "$repo")"
  local tok=()
  if [[ "$gated" == "1" || "$gated" == "true" ]]; then
    [[ -s "$P4_TOKEN_FILE" ]] \
      || die "$P4_KEY: $repo is gated and $P4_TOKEN_FILE is absent: create the token (Phase 2 start prompt, or by hand: (umask 077; printf 'HF_TOKEN=hf_...\\n' > $P4_TOKEN_FILE; chown atlas:atlas $P4_TOKEN_FILE)) and accept the licence at https://huggingface.co/$repo with that account, then re-run (delete $P4_RESULT first)"
    local why
    why="$(_p4_token_check)" || die "$P4_KEY: $repo is gated but the token file cannot be handed to the pull container: $why (rule §7.4; CONVENTIONS §2 fixes atlas:atlas 600, phase2-services.sh writes it so)"
    (( P4_HF_HOST_OK )) \
      || die "$P4_KEY: $repo is gated and HF_ENDPOINT='${HF_ENDPOINT:-}' is not https://huggingface.co or a *.hf.co host: refusing to send HF_TOKEN there (rule §7.2); unset HF_ENDPOINT in $ATLAS_ETC/atlas.env or point it at a *.hf.co mirror"
    tok=(--token)
  fi
  # The pin: json revision, else the root-held pin of an earlier pull (delete $P4_HF_MANIFESTS/<slug>.pinned to re-pin).
  if [[ -z "$revision" && -s "$P4_HF_MANIFESTS/$slug.pinned" ]]; then
    revision="$(head -n1 "$P4_HF_MANIFESTS/$slug.pinned")"
    [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die "$P4_KEY: $P4_HF_MANIFESTS/$slug.pinned does not hold a 40-hex sha ('$revision'); delete it to re-pin"
  fi
  local args=(python3.12 -I -P /opt/atlas/phase4/engines/p4common.py pull "$repo")
  [[ -n "$fallback" ]] && args+=(--fallback "$fallback")
  [[ -n "$revision" ]] && args+=(--revision "$revision")
  if [[ -n "$allow_json" && "$allow_json" != "null" ]]; then
    local pat
    while IFS= read -r pat; do
      [[ -n "$pat" ]] && args+=(--allow "$pat")
    done < <(python3 -c 'import json,sys; [print(p) for p in json.loads(sys.argv[1])]' "$allow_json")
  fi
  log "$P4_KEY: pulling $repo${fallback:+ (fallback $fallback)}${revision:+ @$revision} through the proxy into $P4_ENGINES_DIR/hf${tok:+ (gated: token file bind-mounted read-only)}"
  local errf rc=0
  errf="$(mktemp)"
  # stdout+stderr through one synchronous tee: live in the log, complete in $errf when the pipeline returns (no race).
  if ! p4_docker_run --pull "${tok[@]}" -- "${args[@]}" 2>&1 | tee "$errf"; then
    rc="${PIPESTATUS[0]}"
  fi
  local url
  url="$(grep -o 'LICENCE_URL .*' "$errf" | tail -n1 | awk '{print $2}' || true)"
  case "$rc" in
    0) ;;
    3) rm -f "$errf"; die "$P4_KEY: $repo is gated and the token is not accepted for it. Accept the licence at ${url:-https://huggingface.co/$repo} with the account that owns HF_TOKEN in $P4_TOKEN_FILE (and check that file is current), then re-run (delete $P4_RESULT first)" ;;
    4) rm -f "$errf"; die "$P4_KEY: $repo${fallback:+ (and $fallback)} does not exist on the hub: the research repo id (UNVERIFIED-by-snippet) is wrong; fix config/phase4-engines.json" ;;
    5) rm -f "$errf"; die "$P4_KEY: a file of $repo failed its sha256 check against the hub listing and was deleted; re-run (see $P4_LOG)" ;;
    6) rm -f "$errf"; die "$P4_KEY: $repo was pulled and verified but does not resolve OFFLINE the way the GPU test will load it (see the [p4] lines above in $P4_LOG: refs/main, the hub's tree listing, or allow_patterns that skip a file the loader expects); fix config/phase4-engines.json allow_patterns or re-pull" ;;
    *) rm -f "$errf"; die "$P4_KEY: pull of $repo failed with exit $rc (proxy denial? allowlist? token file unreadable by the container uid? see $P4_LOG and /var/log/squid/access.log)" ;;
  esac
  # The tagged records (container output = data: each is validated before root writes it to the root-held dir).
  local manifest sha chosen
  manifest="$(grep -a '^P4MANIFEST ' "$errf" | tail -n1 | sed 's/^P4MANIFEST //' || true)"
  sha="$(grep -a '^P4PIN ' "$errf" | tail -n1 | awk '{print $2}' || true)"
  chosen="$(grep -a '^P4RESOLVED ' "$errf" | tail -n1 | awk '{print $2}' || true)"
  rm -f "$errf"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "$P4_KEY: the pull of $repo printed no valid P4PIN line (p4common.py contract)"
  [[ "$chosen" == "$repo" || "$chosen" == "$fallback" ]] || die "$P4_KEY: the pull of $repo printed P4RESOLVED '$chosen', which is neither the repo nor its fallback"
  [[ -n "$revision" && "$revision" != "$sha" ]] && die "$P4_KEY: the pull of $repo reports sha $sha but revision $revision was requested (p4common.py contract)"
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); assert isinstance(d.get("files"), list) and d.get("sha")==sys.argv[2], "manifest/sha mismatch"' "$manifest" "$sha" \
    || die "$P4_KEY: the pull of $repo printed no valid P4MANIFEST line"
  local mf="$P4_HF_MANIFESTS/$slug.json"
  printf '%s\n' "$manifest" | python3 -c 'import json,sys; json.dump(json.load(sys.stdin), open(sys.argv[1], "w", encoding="utf-8"), indent=2)' "$mf.tmp" \
    && mv -f "$mf.tmp" "$mf"
  if [[ ! -s "$P4_HF_MANIFESTS/$slug.pinned" ]]; then
    printf '%s\n' "$sha" >"$P4_HF_MANIFESTS/$slug.pinned"
    p4_note "$repo pinned to hub revision $sha ($P4_HF_MANIFESTS/$slug.pinned; delete to re-pin)"
  fi
  printf '%s\n' "$chosen" >"$P4_HF_MANIFESTS/$slug.resolved"
  local v
  v="$(_p4_host_verify "$chosen" "$sha" "$mf")" || die "$P4_KEY: host-side sha256 re-verification of $chosen@$sha FAILED: $v"
  log "$P4_KEY: $chosen@$sha: $v; records in $P4_HF_MANIFESTS/$slug.{json,pinned,resolved}"
  p4_manifest_publish
}

# p4_verify_pulls — before the GPU test (p4_main), re-run the host-side sha256 check of EVERY repo this engine's json
# pulls, against the root-held records of the pull (fix round 3: build runs execute third-party code with hf/ mounted
# read-only now, but the check costs minutes and makes the Arbiter registration rest on weights proven unchanged since
# the pull, not on a check that ran hours earlier). A missing record means the pull never completed: die.
p4_verify_pulls() {
  local repo slug chosen sha mf v n=0
  while IFS=$'\x1f' read -r repo _ _ _ _; do
    [[ -n "$repo" ]] || continue
    slug="$(_p4_repo_slug "$repo")"
    mf="$P4_HF_MANIFESTS/$slug.json"
    [[ -s "$mf" && -s "$P4_HF_MANIFESTS/$slug.pinned" ]] \
      || die "$P4_KEY: no root-held pull record for $repo ($mf / .pinned missing): the pull did not complete; re-run"
    chosen="$(p4_resolved_repo "$repo")"
    sha="$(head -n1 "$P4_HF_MANIFESTS/$slug.pinned")"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "$P4_KEY: $P4_HF_MANIFESTS/$slug.pinned does not hold a 40-hex sha"
    v="$(_p4_host_verify "$chosen" "$sha" "$mf")" \
      || die "$P4_KEY: $chosen@$sha no longer matches the pull record ($v): the weights changed after the pull (a build run or a planted file?); refusing to test or register this engine. Delete $P4_ENGINES_DIR/hf/hub/models--${chosen//\//--} and $P4_RESULT, then re-run to pull again"
    log "$P4_KEY: pre-test re-verification of $chosen@$sha: $v"
    n=$(( n + 1 ))
  done < <(python3 - "$P4_JSON" "$P4_KEY" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
eng = next(e for e in doc["engines"] if e["key"] == sys.argv[2])
for r in eng.get("hf_repos", []):
    print("\x1f".join([r["repo"], "", "", "", ""]))
PY
)
  (( n == 0 )) || P4_NOTES+=("$n hub repo(s) re-verified against the root-held manifests before the test")
}

# p4_manifest_publish — compose this engine's MANIFEST.json (the hub listings with per-file sha256, the revision pins,
# the resolved ids, the clone pins, the freeze path) from the ROOT-HELD records into $P4_MANIFEST_JSON, then have a
# container running as atlas copy it to <engines>/<key>/MANIFEST.json, which phase2/atlas-aegis.sh collects into the
# restic set (Appendix C "manifest only"; header "Records"). Never fails the caller: the root-held copy is authoritative.
p4_manifest_publish() {
  python3 - "$P4_MANIFEST_JSON" "$P4_KEY" "$P4_NAME" "$P4_HF_MANIFESTS" "$P4_GIT_PINS" "$P4_MANIFESTS/freeze.txt" <<'PY' || { warn "$P4_KEY: could not compose $P4_MANIFEST_JSON"; return 0; }
import json, os, sys, time
path, key, name, hfdir, pins, freeze = sys.argv[1:7]
doc = {"key": key, "name": name, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "hf": {}, "git": {},
       "freeze": freeze, "source": "phase4/lib-engine.sh p4_manifest_publish (root-held records under $ATLAS_STATE/phase4)"}
if os.path.isdir(hfdir):
    for fn in sorted(os.listdir(hfdir)):
        if not fn.endswith(".json"):
            continue
        slug = fn[:-5]
        try:
            m = json.load(open(os.path.join(hfdir, fn), encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ent = {"manifest": m}
        for ext in ("pinned", "resolved"):
            p = os.path.join(hfdir, slug + "." + ext)
            if os.path.isfile(p):
                ent[ext] = open(p, encoding="utf-8").read().strip()
        doc["hf"][slug] = ent
if os.path.isfile(pins):
    try:
        allpins = json.load(open(pins, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        allpins = {}
    doc["git"] = {d: v for d, v in allpins.items() if isinstance(v, dict) and v.get("engine") == key}
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
  # shellcheck disable=SC2016  # "$1" is the container shell's positional parameter (the destination path)
  if ! timeout -k 10 120 docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
        --user "$P4_UID:$P4_GID" --pids-limit 64 --memory 1g --memory-swap 1g \
        -v "$P4_MANIFEST_JSON:/tmp/publish.src:ro" -v "$P4_HOST_PRIVATE:$P4_PRIVATE" \
        --entrypoint sh "$P4_IMAGE" -c 'cp -f /tmp/publish.src "$1"' sh "$P4_PRIVATE/MANIFEST.json" </dev/null >/dev/null 2>&1; then
    warn "$P4_KEY: could not publish $P4_MANIFEST_JSON to $P4_HOST_PRIVATE/MANIFEST.json (as atlas); the root-held copy stands"
  fi
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

# p4_resolved_repo REPO — the repo id the pull actually used (fallback aware), from the root-held record.
p4_resolved_repo() {
  local f v
  f="$P4_HF_MANIFESTS/$(_p4_repo_slug "$1").resolved"
  if [[ -s "$f" ]] && v="$(head -n1 "$f")" && [[ -n "$v" ]]; then printf '%s\n' "$v"; else printf '%s\n' "$1"; fi
}

# _p4_git_pin_get DIR / _p4_git_pin_set DIR URL SHA — the root-held clone pins (fix round 2, rule §7.9 for the code
# that actually runs; rule 7.9's audit trail existed for HF pulls only).
_p4_git_pin_get() {
  [[ -s "$P4_GIT_PINS" ]] || return 0
  python3 -c 'import json,sys; print((json.load(open(sys.argv[1])).get(sys.argv[2]) or {}).get("sha", ""))' "$P4_GIT_PINS" "$1" 2>/dev/null || true
}
_p4_git_pin_set() {
  python3 - "$P4_GIT_PINS" "$1" "$2" "$3" "$P4_KEY" <<'PY'
import json, os, sys, time
path, d, url, sha, key = sys.argv[1:6]
doc = {}
if os.path.isfile(path):
    doc = json.load(open(path, encoding="utf-8"))
doc[d] = {"url": url, "sha": sha, "engine": key, "pinned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
json.dump(doc, open(path + ".tmp", "w", encoding="utf-8"), indent=2)
os.replace(path + ".tmp", path)
PY
  p4_manifest_publish
}

# p4_git_clone URL DIR [--recursive] [REF] — into $P4_ENGINES_DIR/src/DIR (a json git[].dir of this engine: the only
# src/ paths this engine's containers mount) through the proxy; skipped when DIR/.git exists. REF (json git[].ref) or the recorded pin is fetched exactly (`git fetch --depth 1 origin REF` works for a
# commit sha on GitHub); without either the default branch HEAD is cloned and its sha recorded, so every later re-clone
# builds the same code until the Principal deletes the pin from $P4_GIT_PINS.
p4_git_clone() {
  local url="$1" dir="$2" rec="${3:-}" ref="${4:-}"
  [[ -n "$dir" && "$dir" != */* && "$dir" != . ]] || die "$P4_KEY: p4_git_clone: bad checkout dir name '$dir'"
  local pinned; pinned="$(_p4_git_pin_get "$dir")"
  if [[ -d "$P4_HOST_SRC/$dir/.git" ]]; then
    log "$P4_KEY: $P4_HOST_SRC/$dir already cloned${pinned:+ (pinned $pinned)}; skipping"
    [[ -n "$pinned" ]] && P4_NOTES+=("git $dir @ $pinned")
    return 0
  fi
  [[ -n "$ref" ]] || ref="$pinned"
  local sub=""
  [[ "$rec" == "--recursive" ]] && sub=1
  local found=0 d
  for d in "${P4_SRC_DIRS[@]}"; do [[ "$d" == "$dir" ]] && found=1; done
  (( found )) || die "$P4_KEY: p4_git_clone '$dir' is not a json git[].dir of this engine (only those are mounted; add it to config/phase4-engines.json)"
  # A half-finished clone from an interrupted run is emptied INSIDE the container as atlas (the mount point itself is
  # the per-dir bind mount, so the directory stays; root deletes nothing under $ATLAS_SRV, header "Records").
  # shellcheck disable=SC2016  # "$1" is the container shell's positional parameter (the checkout dir)
  p4_docker_run -- sh -c 'find "$1" -mindepth 1 -delete' sh "$P4_SRC/$dir" \
    || die "$P4_KEY: could not empty $P4_HOST_SRC/$dir before cloning"
  if [[ -n "$ref" ]]; then
    log "$P4_KEY: git fetch $url @ $ref -> $P4_HOST_SRC/$dir (pinned)"
    # shellcheck disable=SC2016  # $1..$4 are the container shell's positional parameters (dir, url, ref, submodules)
    p4_docker_run --net -- sh -c 'set -e; git init -q "$1"; cd "$1"; git remote add origin "$2"; git fetch --depth 1 origin "$3"; git checkout -q --detach FETCH_HEAD; if [ -n "$4" ]; then git submodule update --init --recursive --depth 1; fi' \
        sh "$P4_SRC/$dir" "$url" "$ref" "$sub" \
      || die "$P4_KEY: git fetch $url @ $ref failed (github.com allowlisted? pin still reachable? see $P4_LOG; delete the $dir entry in $P4_GIT_PINS to re-pin to HEAD)"
  else
    local flags=(--depth 1)
    [[ -n "$sub" ]] && flags+=(--recurse-submodules --shallow-submodules)
    log "$P4_KEY: git clone $url -> $P4_HOST_SRC/$dir (default branch HEAD, no pin yet; git clone accepts the empty mount point)"
    p4_docker_run --net -- git clone "${flags[@]}" "$url" "$P4_SRC/$dir" \
      || die "$P4_KEY: git clone $url failed (github.com allowlisted? see $P4_LOG)"
  fi
  local sha
  sha="$(p4_docker_run -- git -C "$P4_SRC/$dir" rev-parse HEAD 2>/dev/null | tr -d '[:space:]')" || true
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "$P4_KEY: git rev-parse HEAD in $dir returned '$sha' (container output is data; refusing to record it)"
  [[ -z "$ref" || "$sha" == "$ref" || ! "$ref" =~ ^[0-9a-f]{40}$ ]] || die "$P4_KEY: $dir checked out $sha but $ref was requested"
  _p4_git_pin_set "$dir" "$url" "$sha"
  if [[ -n "$pinned" ]]; then
    p4_note "git $dir @ $sha (re-cloned at the recorded pin)"
  elif [[ -n "$ref" ]]; then
    p4_note "git $dir @ $sha (json ref $ref)"
  else
    p4_note "git $dir @ $sha (default branch HEAD, now pinned in $P4_GIT_PINS)"
  fi
}

# p4_git_from_json — every git[] entry of this engine's json (US-separated, see p4_pull).
p4_git_from_json() {
  local url dir rec ref
  while IFS=$'\x1f' read -r url dir rec ref; do
    [[ -n "$url" ]] || continue
    p4_git_clone "$url" "$dir" "$rec" "$ref"
  done < <(python3 - "$P4_JSON" "$P4_KEY" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
eng = next(e for e in doc["engines"] if e["key"] == sys.argv[2])
for g in eng.get("git", []):
    print("\x1f".join([g["url"], g["dir"], "--recursive" if g.get("recursive") else "", g.get("ref") or ""]))
PY
)
}

# --- venvs -----------------------------------------------------------------------------------------------------------
# p4_venv_create — /srv/atlas/engines/venv/<key> as a --system-site-packages venv over the image's ROCm torch. When the
# venv is (re)created, every pip/install marker of this engine is dropped (fix round 2: a marker must never say "done"
# for a venv that no longer holds the packages).
p4_venv_create() {
  if [[ -x "$P4_HOST_VENV/bin/python" ]]; then
    return 0
  fi
  log "$P4_KEY: creating venv $P4_HOST_VENV (system site-packages: inherits the gfx1151 torch)"
  rm -f "$P4_MARKERS/$P4_KEY"-pip-* "$P4_MARKERS/$P4_KEY"-*-installed
  p4_docker_run -- python3.12 -m venv --system-site-packages "$P4_VENV" \
    || die "$P4_KEY: python3.12 -m venv failed"
  # The venv lives on the bind-mounted data volume: it must now be visible on the host, or the mount is wrong.
  [[ -x "$P4_HOST_VENV/bin/python" ]] \
    || die "$P4_KEY: the container reported success but $P4_HOST_VENV/bin/python does not exist on the host (bind mount $P4_HOST_VENV -> $P4_VENV broken?)"
}

# p4_marker NAME — the root-only idempotence marker path for this engine (never inside the bind-mounted venv).
p4_marker() { printf '%s/%s-%s\n' "$P4_MARKERS" "$P4_KEY" "$1"; }

# p4_venv_freeze — record what the venv resolved (rule §7.9 audit artefact); written by the container as atlas, the
# path goes into the result notes so the Section 17 step 6 table points at it.
p4_venv_freeze() {
  p4_docker_run -- sh -c "\"$P4_VENV/bin/pip\" freeze --all > \"$P4_FREEZE\"" \
    || die "$P4_KEY: pip freeze into $P4_FREEZE failed"
  local n
  n="$(p4_safe_read "$P4_HOST_MANIFESTS/freeze.txt" | wc -l)" || n="?"
  log "$P4_KEY: venv freeze ($n lines) -> $P4_HOST_MANIFESTS/freeze.txt"
  p4_manifest_publish
  local note="freeze: $P4_HOST_MANIFESTS/freeze.txt"
  local x
  for x in "${P4_NOTES[@]}"; do [[ "$x" == "$note" ]] && return 0; done
  P4_NOTES+=("$note")
}

# p4_venv_pip ARGS... — pip in the engine venv, under the image's constraints file, through the proxy. Idempotent by a
# marker keyed on the argument list (a changed list re-installs; pip itself skips satisfied requirements); the markers
# are dropped whenever p4_venv_create rebuilds the venv.
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
  # The V11 flags are read HERE, in the parent shell: a die inside the backgrounded run below would end only that
  # subshell and the engine would be recorded as "no P4RESULT line" instead of the real reason (fix round 2).
  _p4_gpu_flags
  local base peak now
  base="$(_p4_gtt_settle)"
  P4_GTT_BASE="$base"; peak="$base"
  local outf name
  outf="$(mktemp)"
  name="$(_p4_unique)"
  # Registered in the parent BEFORE the backgrounded run: an array append inside the `&` subshell never reaches the
  # EXIT trap's cleanup loop (fix round: the hung-load case would leave the container holding GTT).
  P4_CONTAINERS+=("$name")
  log "$P4_KEY: test run ($py $P4_TEST_PY --out /srv/atlas/workspace/phase4-samples/$P4_KEY ${settings[*]:-}); GTT baseline ${base} MiB (settled); mem cap ${P4_MEM_GB}g cpus $P4_CPUS; GPU flags: ${P4_GPU_FLAGS[*]:-(default seccomp)}"
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
# p4_main — the skeleton every engine script ends with. Reads p4_build (required), P4_TEST_SETTINGS (optional array of
# key=value settings for the test, e.g. repo ids resolved at build time) and P4_TEST_ENV (optional array of K=V for
# the test container's environment).
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
  # The weights the GPU test will load must still be the ones the pull verified (header; fix round 3).
  p4_verify_pulls
  local settings=() envs=() kv
  if declare -p P4_TEST_SETTINGS >/dev/null 2>&1; then settings=("${P4_TEST_SETTINGS[@]}"); fi
  if declare -p P4_TEST_ENV >/dev/null 2>&1; then for kv in "${P4_TEST_ENV[@]}"; do envs+=(-e "$kv"); done; fi
  if p4_run_test "${envs[@]}" "${settings[@]}"; then
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
