#!/usr/bin/env bash
# phase4-engines.sh — Phase 4 driver: multimodal engines in the ROCm container (ATLAS_FRAMEWORK_REVIEW.md Section 17
# Phase 4; Section 15.2 tiers; Section 21 V8, V9, V11; Section 3.4). Long, detached under systemd, per-engine pass/fail.
#
#   sudo ./atlas-day1.sh phase4 [--dry-run] [--force STEP] [--status] [--foreground]   (the entry point re-execs this)
#   phase4-engines.sh --run          the in-unit entry started by detached_phase (atlas-day1-phase4)
#   phase4-engines.sh                without --run and not --dry-run: detaches itself (same unit) and returns
#
# Steps (CONVENTIONS.md §1, §6; each wrapped in run_step, so a re-run skips what is complete):
#   01  the ONE base image docker/rocm-base/Dockerfile -> atlas/rocm-base:10.0.0 (AMD's gfx1151 wheels, ROCm 10.0.0,
#       torch 2.13.0; conflict 12) built through the allowlist proxy, its freeze/constraints kept root-held under
#       $ATLAS_STATE/phase4/ and published to $ATLAS_SRV/engines/manifests/ by the image itself (as atlas), then V11
#       (verify/v11-rocm-selftest.sh -> phase4/selftest.py: rocminfo gfx1151, torch sees the device, matmul, 4 GB
#       alloc, two-step diffusion). V11 also records which container flags the GPU needs
#       ($ATLAS_STATE/phase4/v11-runflags.txt, root-held: the default seccomp profile first, seccomp=unconfined/ipc=host
#       only when the default demonstrably fails). V11 fail STOPS the phase (Section 17 step 1: the first and only
#       place ROCm runs): the same self-test runs once in the community image kyuz0/amd-strix-halo-comfyui as a
#       diagnostic (research §1.1; digest-pinned in the json; hardened flags first, the relaxed pair only on a
#       non-timeout failure, like V11), the diagnostic is folded into the V11 fail record (never a second record with
#       another status), conflict 19's kernel note is logged, the gate table is printed, then the driver stops.
#   02  green engines in Section 17's order, each by phase4/engines/<key>.sh under its json timeout; per-engine JSON at
#       $ATLAS_STATE/phase4/<key>.json (built, loaded, sample_output_path, footprint_mb from the GTT delta, seconds);
#       a failure is recorded fail and the phase CONTINUES (per-engine pass/fail, never block). Before the first engine
#       the Arbiter's ledger is checked (Section 4.2 rules 3 and 5): a core/apex/vision/crosscheck engine still
#       resident from Phase 3 is unloaded through /arbiter/unload, or the step stops.
#   03  yellow engines (trellis, blender-cycles with the CPU fallback): result deferred on failure (CONVENTIONS §7.4),
#       the raw verdict kept in `outcome` so the table still says the attempt failed (Section 17 step 3).
#   04  pointllm -> V8, clay-prithvi -> V9: pass or deferred, recorded with record_v.
#   05  every passing engine registered with the Arbiter: POST $ORCH_URL/arbiter/register. Every passing engine must
#       register (Section 17 step 5; 4.2 rule 1): fewer registrations than passing engines stops the step unmarked,
#       after the per-engine table and the gate table (rule §7.10). A passing engine without a measured footprint_mb is
#       never registered as 0 bytes (rule §7.4: the ledger holds measurements, never fabricated ones).
#   06  the per-engine table, $ATLAS_STATE/phase4/summary.json (+ a copy published as atlas to
#       $ATLAS_SRV/engines/phase4-results.json), `gate phase4 V11 -- V8 V9`.
#
# Contracts relied on from other writers (CONVENTIONS.md §1; each is checked and fails loudly when absent):
#   * /etc/atlas/docker.env (phase1/06-docker.sh): ATLAS_UID ATLAS_GID RENDER_GID VIDEO_GID CONTAINER_HTTP_PROXY
#     CONTAINER_HTTPS_PROXY CONTAINER_NO_PROXY (containers reach squid at the docker0 gateway; DOCKER-USER blocks the rest).
#   * /etc/atlas/secrets/hf-token.env (phase2-services.sh): HF_TOKEN=... for the gated repos (FLUX.1-dev, Stable Audio
#     Open; conflict 16). Root reads it; its owner and mode are the Phase 2 writer's business (currently atlas:atlas 600
#     per CONVENTIONS §2; an earlier revision wrote root:root 600) because lib-engine.sh never mounts the file itself:
#     for a gated pull it stages a copy owned by the container uid (mode 400) in a private root-only tmpfs directory,
#     bind-mounts THAT read-only at /run/secrets/hf-token.env, and removes it after the run. Absent -> those two
#     engines fail before any container starts, naming the token prompt and the licence URL; the phase continues.
#   * /etc/atlas/orchestrator.env (phase2/02-orchestrator.sh): ORCH_URL, and ORCH_ADMIN_TOKEN_FILE when the admin
#     routes carry a token (atlas/api.py: without it the admin routes accept loopback clients, which this driver is).
#     The token header is handed to curl on STDIN (-H @-), never on argv (/proc/<pid>/cmdline is world-readable;
#     rule §7.2). POST /arbiter/register takes {engine, total_bytes, task_id}; this driver also sends {key,
#     footprint_mb, class: "phase4"} as the brief states them (extra fields are ignored by the pydantic model).
#     Orchestrator side (landed, fix round 2): atlas/arbiter.py build_arbiter merges config/phase4-engines.json
#     (class `phase4`, CONVENTIONS §8) and register_measured accepts a bare unit-style key it has never seen as a
#     phase4 engine with the measured footprint (logged WARNING), so a 404 here now means a regression in the
#     orchestrator, not a missing feature; step 05 still stops on it (rule §7.4) and says so.
# Contracts this file defines for others: $ATLAS_STATE/phase4/<key>.json (schema in phase4/lib-engine.sh),
# $ATLAS_STATE/phase4/summary.json and $ATLAS_SRV/engines/phase4-results.json (the Section 17 step 6 table as JSON),
# $ATLAS_STATE/phase4/registry.json ([{key, footprint_mb, total_bytes, class, registered, http}]),
# $ATLAS_STATE/phase4/diag-image.txt (the digest of the diagnostic image once pulled, when the json pins none),
# $ATLAS_STATE/phase4/hf-manifests/ and git-pins.json (lib-engine.sh: pull listings, revision pins, clone pins).
# Per-engine logs: $ATLAS_STATE/logs/phase4-<key>.log. To redo one engine: delete $ATLAS_STATE/phase4/<key>.json and
# re-run with --force 02 (or 03/04); completed builds, venvs and pulls are skipped by the engine scripts themselves.
# Root never opens a path under $ATLAS_SRV for writing (fix round 2): the tree is atlas-owned and bind-mounted RW into
# every Phase 4 container, so a symlink planted there could redirect a root write. Everything root records lives
# under $ATLAS_STATE/phase4 (root:root); copies meant for the data volume are written by a container running as atlas
# (_p4_publish), which cannot gain anything from its own symlinks.
#
# Privilege note for the Principal (fix round): `atlas` is in group `docker` (Phase 1 step 6) = root-equivalent on the
# host through the rootful docker socket. The sudoers fragment (CONVENTIONS §8) and the approval gate bound the
# orchestrator's code paths, not its privilege; the Phase 1 writer has been asked to gate the socket (rootless docker or
# a socket proxy). This phase keeps its own exposure minimal: no secret in any container's Config.Env, GPU and network
# never in the same container, resource caps on every run (lib-engine.sh header).

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/common.sh"

export ATLAS_PHASE=phase4
require_root
parse_common_args "$@"

P4_JSON="$ATLAS_DAY1_DIR/config/phase4-engines.json"
P4_STATE="$ATLAS_STATE/phase4"
P4_ENGINES_DIR="$ATLAS_SRV/engines"
P4_DOCKERFILE_DIR="$ATLAS_DAY1_DIR/docker/rocm-base"
P4_DOCKER_ENV="$ATLAS_ETC/docker.env"
P4_ORCH_ENV="$ATLAS_ETC/orchestrator.env"
P4_DIAG_DIGEST_FILE="$P4_STATE/diag-image.txt"
P4_RUNFLAGS_FILE="$P4_STATE/v11-runflags.txt"
P4_LAST_RESULT=""

[[ -f "$P4_JSON" ]] || die "$P4_JSON is missing"
_p4_cfg() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["base_image"].get(sys.argv[2]); print("" if v is None else v)' "$P4_JSON" "$1"; }
P4_IMAGE="$(_p4_cfg tag)"; P4_LABEL="$(_p4_cfg label)"; P4_IMAGE_VER="$(_p4_cfg version)"
P4_DIAG_IMAGE="$(_p4_cfg diagnostic_fallback_image)"
P4_DIAG_DIGEST="$(_p4_cfg diagnostic_fallback_digest)"
P4_LOAD_TIMEOUT_S="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["defaults"]["load_timeout_s"])' "$P4_JSON")"
export P4_IMAGE P4_LOAD_TIMEOUT_S

# --- Pre-flight ------------------------------------------------------------------------------------------------------
if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
  load_env
  # CONVENTIONS.md §6: Phase 4 refuses to start without the Phase 3 gate marker (atlas-day1.sh checks too; direct runs).
  [[ -e "$ATLAS_DONE_DIR/phase3.gate" ]] \
    || die "Phase 3 has not passed its gate ($ATLAS_DONE_DIR/phase3.gate missing). Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
fi

# Without --run this driver detaches itself (Section 17: "long, detached"); --dry-run runs here and touches nothing.
if [[ "${ATLAS_IN_UNIT:-0}" != "1" && "$ATLAS_DRY_RUN" != "1" ]]; then
  detached_phase phase4 "$(readlink -f "$0")"
  exit 0
fi

# --- Engine list helpers ---------------------------------------------------------------------------------------------
# _p4_keys TIER... — keys of the given tiers in json (= Section 17) order.
_p4_keys() {
  python3 - "$P4_JSON" "$@" <<'PY'
import json, sys
tiers = set(sys.argv[2:])
for e in json.load(open(sys.argv[1], encoding="utf-8"))["engines"]:
    if e["tier"] in tiers:
        print(e["key"])
PY
}
# _p4_field KEY FIELD — a scalar field of one engine (defaults applied for timeout_s / load_timeout_s).
_p4_field() {
  python3 - "$P4_JSON" "$1" "$2" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
eng = next(e for e in doc["engines"] if e["key"] == sys.argv[2])
v = eng.get(sys.argv[3])
if v is None:
    v = doc.get("defaults", {}).get(sys.argv[3], "")
print("" if v is None else v)
PY
}
# _p4_result KEY [FIELD] — the recorded result word (or one field) of an engine, "missing" when there is no file.
_p4_result() {
  local f="$P4_STATE/$1.json" field="${2:-result}"
  [[ -f "$f" ]] || { echo missing; return 0; }
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get(sys.argv[2]); print("" if v is None else v)' "$f" "$field" 2>/dev/null || echo missing
}
# _p4_denv KEY — one value of /etc/atlas/docker.env.
_p4_denv() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$P4_DOCKER_ENV"; }

# Every non-deferred engine must have its script and test (rule §7.4: fail before spending hours, not after).
P4_ALL_KEYS=()
mapfile -t P4_ALL_KEYS < <(_p4_keys green yellow verify)
for key in "${P4_ALL_KEYS[@]}"; do
  s="$(_p4_field "$key" build_script)"; t="$(_p4_field "$key" test_script)"
  [[ -n "$s" && -x "$ATLAS_DAY1_DIR/$s" ]] || die "$key: build_script '$s' is missing or not executable"
  [[ -n "$t" && -f "$ATLAS_DAY1_DIR/$t" ]] || die "$key: test_script '$t' is missing"
done
[[ -f "$ATLAS_DAY1_DIR/phase4/lib-engine.sh" && -f "$ATLAS_DAY1_DIR/phase4/engines/p4common.py" ]] \
  || die "phase4/lib-engine.sh or phase4/engines/p4common.py is missing"

# --- Publishing into the atlas-owned tree (header: root never writes under $ATLAS_SRV) -------------------------------
# _p4_publish SRC DEST — copy the root-held file SRC to DEST (a container path under /srv/atlas/engines) with a short,
# hardened container running as atlas: no network, no capabilities, 64 pids, 1 GiB, 120 s.
_p4_publish() {
  local src="$1" dest="$2" uid gid
  uid="$(_p4_denv ATLAS_UID)"; gid="$(_p4_denv ATLAS_GID)"
  [[ -f "$src" && -n "$uid" && -n "$gid" ]] || return 1
  # shellcheck disable=SC2016  # "$1" is the container shell's own positional parameter (the dest path), on purpose
  timeout -k 10 120 docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
    --user "$uid:$gid" --pids-limit 64 --memory 1g --memory-swap 1g \
    -v "$src:/tmp/publish.src:ro" -v "$P4_ENGINES_DIR:/srv/atlas/engines" \
    --entrypoint sh "$P4_IMAGE" -c 'cp -f /tmp/publish.src "$1"' sh "$dest" </dev/null
}

# --- Orchestrator access ---------------------------------------------------------------------------------------------
_p4_orch_url() {
  local url=""
  [[ -r "$P4_ORCH_ENV" ]] && url="$(sed -nE "s/^ORCH_URL=['\"]?([^'\"]+)['\"]?$/\1/p" "$P4_ORCH_ENV" | head -n1)"
  [[ -n "$url" ]] || url="http://127.0.0.1:${ORCH_PORT:-8800}"
  printf '%s\n' "$url"
}
# _p4_orch_curl METHOD PATH [JSON_BODY] — prints "<http_code> <body>" (code 000 when unreachable). Adds X-Atlas-Token
# when ORCH_ADMIN_TOKEN_FILE is configured in orchestrator.env (api.py); loopback needs none. The header reaches curl
# through STDIN (-H @-, curl >= 7.55), so the token is never on argv (fix round 2, rule §7.2). Never fails the caller.
_p4_orch_curl() {
  local method="$1" path="$2" body="${3:-}" url tokf tok="" hdr=()
  url="$(_p4_orch_url)"
  tokf="$( [[ -r "$P4_ORCH_ENV" ]] && sed -nE "s/^ORCH_ADMIN_TOKEN_FILE=['\"]?([^'\"]+)['\"]?$/\1/p" "$P4_ORCH_ENV" | head -n1 || true)"
  if [[ -n "$tokf" && -r "$tokf" ]]; then
    tok="$(sed -nE 's/^ORCH_ADMIN_TOKEN=//p' "$tokf" | head -n1)"
    [[ -n "$tok" ]] || tok="$(head -n1 "$tokf")"
    [[ -n "$tok" ]] && hdr=(-H @-)
  fi
  local data=()
  [[ -n "$body" ]] && data=(-H 'Content-Type: application/json' -d "$body")
  local out code
  out="$( { [[ -n "$tok" ]] && printf 'X-Atlas-Token: %s\n' "$tok"; } \
          | curl -sS --noproxy '*' --max-time 20 -w $'\n%{http_code}' "${hdr[@]}" "${data[@]}" -X "$method" "$url$path" 2>/dev/null)" \
    || out=$'\n000'
  code="${out##*$'\n'}"; body="${out%$'\n'*}"
  [[ "$code" =~ ^[0-9]{3}$ ]] || code=000
  printf '%s %s\n' "$code" "$(tr '\n' ' ' <<<"$body")"
}

# --- Engine runner ---------------------------------------------------------------------------------------------------
# _p4_run_engine KEY — run the engine script under its timeout; make sure a result file exists; set P4_LAST_RESULT.
_p4_run_engine() {
  local key="$1"
  local tier script timeout_s rc=0 t0
  tier="$(_p4_field "$key" tier)"
  script="$ATLAS_DAY1_DIR/$(_p4_field "$key" build_script)"
  timeout_s="$(_p4_field "$key" timeout_s)"
  [[ "$timeout_s" =~ ^[0-9]+$ ]] || timeout_s=14400
  if [[ "$(_p4_result "$key")" == pass ]]; then
    log "$key: already passed ($P4_STATE/$key.json); skipping (delete the file to redo)"
    P4_LAST_RESULT=pass
    return 0
  fi
  notify "Phase 4: building $key ($tier, timeout $(( timeout_s / 60 )) min)"
  log "$key: running $script (tier $tier, timeout ${timeout_s}s, log $ATLAS_LOG_DIR/phase4-$key.log)"
  t0=$SECONDS
  # No --foreground: timeout puts the script in its own process group and TERMs the whole group on expiry, so the
  # docker clients (which forward TERM to their containers) and the script's sampler loop stop too; the script's EXIT
  # trap then removes any container left and writes the result. -k 60: KILL only if that stalls.
  P4_IMAGE="$P4_IMAGE" P4_TIMEOUT_S="$timeout_s" P4_LOAD_TIMEOUT_S="$P4_LOAD_TIMEOUT_S" \
    timeout -k 60 "$timeout_s" "$script" </dev/null || rc=$?
  local res
  res="$(_p4_result "$key")"
  if [[ "$res" == missing || -z "$res" ]]; then
    # The script died before its trap could write (or was SIGKILLed): record it here so the table has a row.
    res=fail; [[ "$tier" == green ]] || res=deferred
    python3 - "$P4_STATE/$key.json" "$key" "$(_p4_field "$key" name)" "$tier" "$res" "$rc" "$(( SECONDS - t0 ))" \
              "$(tail -n 20 "$ATLAS_LOG_DIR/phase4-$key.log" 2>/dev/null || true)" <<'PY'
import json, sys, time
path, key, name, tier, res, rc, secs, tail = sys.argv[1:9]
rec = {"key": key, "name": name, "tier": tier, "result": res, "outcome": "fail", "blocking": tier == "green",
       "built": False, "loaded": False, "sample_output_path": None, "footprint_mb": None, "seconds": int(secs),
       "exit_code": int(rc), "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
       "notes": f"engine script exited {rc} without writing a result (timeout={'yes' if rc in (124, 137) else 'no'})",
       "log_tail": tail.splitlines()[-20:]}
json.dump(rec, open(path, "w", encoding="utf-8"), indent=2)
PY
  fi
  P4_LAST_RESULT="$res"
  log "$key: $res (outcome $(_p4_result "$key" outcome), exit $rc, $(( (SECONDS - t0) / 60 )) min, footprint_mb=$(_p4_result "$key" footprint_mb), sample=$(_p4_result "$key" sample_output_path))"
  notify "Phase 4: $key -> $res"
  return 0
}

# --- Step 01: base image + V11 -----------------------------------------------------------------------------------------
# _p4_v11_last_msg — the msg of the latest V11 record (the same reader gate/verify_table use).
_p4_v11_last_msg() {
  _atlas_verify_rows V11 | tail -n1 | cut -f4
}

# _p4_diag_run — the V11 self-test once in the community image (research §1.1), as a diagnostic only. Hardened run:
# atlas uid, no capabilities, no new privileges, pids/memory caps, read-only root with a tmpfs /tmp; devices and gids
# as any GPU run; --entrypoint so the self-test is what runs whatever ENTRYPOINT the image declares (fix round 2).
# Flag sets mirror V11: the hardened profile first, and only a NON-timeout failure is repeated once with
# --security-opt seccomp=unconfined --ipc=host (the community image's documented run line, research §6.2; a hang is a
# kernel symptom, never a seccomp one). Pinned by digest: config diagnostic_fallback_digest (VERIFIED from the Docker
# Hub registry API on 2026-10-04 for :latest), else the digest recorded in $P4_STATE/diag-image.txt after the first
# pull (rule §7.9). Prints one line on STDOUT (the caller folds it into the V11 record); logs go to stderr so they
# never land in that record (fix round 2). Never fails the caller.
_p4_diag_run() {
  local ref="$P4_DIAG_IMAGE" digest="$P4_DIAG_DIGEST" pinned="config"
  if [[ -z "$digest" && -s "$P4_DIAG_DIGEST_FILE" ]]; then digest="$(head -n1 "$P4_DIAG_DIGEST_FILE")"; pinned="recorded"; fi
  if [[ -n "$digest" ]]; then ref="${P4_DIAG_IMAGE%%@*}"; ref="${ref%%:*}@$digest"; else pinned="UNPINNED (first pull)"; fi
  if ! retry 2 docker pull -q "$ref" >/dev/null 2>&1; then
    echo "could not pull $ref (registry-1.docker.io allowlisted? digest still published?)"
    return 0
  fi
  local got
  got="$(docker inspect --format '{{index .RepoDigests 0}}' "$ref" 2>/dev/null || true)"
  got="${got##*@}"
  if [[ -n "$got" && -z "$digest" ]]; then
    mkdir -p "$P4_STATE"; printf '%s\n' "$got" >"$P4_DIAG_DIGEST_FILE"
    log "diagnostic image digest recorded: $P4_DIAG_IMAGE@$got -> $P4_DIAG_DIGEST_FILE (used on every later run)" >&2
  fi
  local uid gid
  uid="$(_p4_denv ATLAS_UID)"; gid="$(_p4_denv ATLAS_GID)"
  local rc=0 out used="hardened"
  _diag_once() {   # _diag_once EXTRA_FLAG... ; sets rc and out
    rc=0
    out="$(ATLAS_PHASE=phase4 timeout -k 15 300 docker run --rm --name "v11-diag-$$" \
             --device /dev/kfd --device /dev/dri \
             --group-add "$(_p4_denv VIDEO_GID)" --group-add "$(_p4_denv RENDER_GID)" \
             --user "$uid:$gid" --cap-drop ALL --security-opt no-new-privileges \
             --pids-limit 1024 --memory 32g --memory-swap 32g \
             --read-only --tmpfs /tmp:mode=1777 --network none "$@" \
             -e HOME=/tmp -e MIOPEN_USER_DB_PATH=/tmp/miopen -e MIOPEN_CUSTOM_CACHE_DIR=/tmp/miopen \
             -e V11_OUT=/tmp/v11-diag.json -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 \
             -e PYTHONNOUSERSITE=1 \
             -v "$ATLAS_DAY1_DIR/phase4:/opt/atlas/phase4:ro" \
             --entrypoint /opt/venv/bin/python "$ref" /opt/atlas/phase4/selftest.py 2>&1 </dev/null | tail -n 12)" || rc=$?
    docker rm -f "v11-diag-$$" >/dev/null 2>&1 || true
  }
  _diag_once
  if (( rc != 0 && rc != 124 && rc != 137 )); then
    local first="$out"
    _diag_once --security-opt seccomp=unconfined --ipc=host
    used="hardened failed (exit; $(tr '\n' ' ' <<<"$first" | cut -c1-160)), then relaxed seccomp=unconfined ipc=host"
  elif (( rc == 124 || rc == 137 )); then
    used="hardened run hung and was killed (not retried: a hang is a kernel symptom, #6530 #6182)"
  fi
  echo "$ref ($pinned; $used) exit $rc: $(tr '\n' ' ' <<<"$out" | cut -c1-600)"
}

step_01() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  [[ -r "$P4_DOCKER_ENV" ]] || die "$P4_DOCKER_ENV missing (Phase 1 step 6 writes the container proxy and gids)"
  [[ -f "$ATLAS_ETC/proxy.env" ]] || die "$ATLAS_ETC/proxy.env missing: every download must go through the allowlist proxy (rule §7.1)"
  [[ -d "$P4_ENGINES_DIR" ]] || die "$P4_ENGINES_DIR missing: the 8 TB data volume is not mounted (Phase 1 step 3)"
  [[ -f "$P4_DOCKERFILE_DIR/Dockerfile" ]] || die "$P4_DOCKERFILE_DIR/Dockerfile is missing"
  local hp sp np uid gid
  hp="$(_p4_denv CONTAINER_HTTP_PROXY)"
  sp="$(_p4_denv CONTAINER_HTTPS_PROXY)"
  np="$(_p4_denv CONTAINER_NO_PROXY)"
  uid="$(_p4_denv ATLAS_UID)"
  gid="$(_p4_denv ATLAS_GID)"
  [[ -n "$sp" && -n "$uid" && -n "$gid" ]] || die "$P4_DOCKER_ENV lacks CONTAINER_HTTPS_PROXY / ATLAS_UID / ATLAS_GID"
  [[ -n "$hp" ]] || hp="$sp"
  [[ -n "$np" ]] || np="localhost,127.0.0.1"

  ensure_dir "$P4_ENGINES_DIR/manifests" "$uid:$gid" 755
  # The container HOME and the MIOpen cache hold whatever tools drop there: not world-readable (CONVENTIONS §2).
  ensure_dir "$P4_ENGINES_DIR/home" "$uid:$gid" 700
  ensure_dir "$P4_ENGINES_DIR/miopen" "$uid:$gid" 700
  ensure_dir "$P4_ENGINES_DIR/hf" "$uid:$gid" 755
  ensure_dir "$ATLAS_SRV/workspace/phase4-samples" "$uid:$gid" 755
  mkdir -p "$P4_STATE"

  if [[ "$(docker image inspect -f "{{index .Config.Labels \"$P4_LABEL\"}}" "$P4_IMAGE" 2>/dev/null)" == "$P4_IMAGE_VER" ]]; then
    log "$P4_IMAGE already built (label $P4_LABEL=$P4_IMAGE_VER); skipping the build"
  else
    notify "Phase 4 step 1: building $P4_IMAGE (ROCm 10.0.0 gfx1151 wheels, ~5 GB through the proxy)"
    log "docker build $P4_IMAGE from $P4_DOCKERFILE_DIR (index $(_p4_cfg rocm_index); archive.ubuntu.com, security.ubuntu.com, pypi.org, files.pythonhosted.org and stable.repo.amd.com must be in config/allowlist.txt — never the .ubuntu.com wildcard)"
    docker build --pull -t "$P4_IMAGE" \
      --build-arg "http_proxy=$hp" --build-arg "https_proxy=$sp" --build-arg "HTTP_PROXY=$hp" --build-arg "HTTPS_PROXY=$sp" \
      --build-arg "no_proxy=$np" --build-arg "NO_PROXY=$np" \
      --build-arg "ATLAS_UID=$uid" --build-arg "ATLAS_GID=$gid" \
      --build-arg "ROCM_INDEX=$(_p4_cfg rocm_index)" --build-arg "ROCM_VER=$(_p4_cfg rocm_version)" \
      "$P4_DOCKERFILE_DIR" </dev/null \
      || die "docker build of $P4_IMAGE failed. If pip could not resolve torch==$(_p4_cfg torch) for cp312 (UNVERIFIED cp-tag list, research §1.1), drop the == pins in the Dockerfile and let the [device-gfx1151] extras resolve; if the build stopped at 'rocminfo not found', the rocm[...] wheel layout changed (research §1.2); otherwise check /var/log/squid/access.log for TCP_DENIED"
  fi
  # The resolved wheel set is the manifest of this image (rule §7.9; Appendix C: engines/ is backed up as manifests).
  # Root keeps its copy under $P4_STATE; the data-volume copies are written by the image itself as atlas (header).
  local f
  for f in base-freeze.txt constraints-rocm.txt; do
    timeout -k 10 120 docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
        --user "$uid:$gid" --pids-limit 64 --memory 1g --memory-swap 1g \
        --entrypoint cat "$P4_IMAGE" "/opt/atlas/$f" </dev/null >"$P4_STATE/rocm-$f" \
      || die "could not read /opt/atlas/$f from $P4_IMAGE"
    [[ -s "$P4_STATE/rocm-$f" ]] || die "/opt/atlas/$f in $P4_IMAGE is empty"
  done
  timeout -k 10 120 docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
      --user "$uid:$gid" --pids-limit 64 --memory 1g --memory-swap 1g \
      -v "$P4_ENGINES_DIR:/srv/atlas/engines" \
      --entrypoint sh "$P4_IMAGE" -c 'cp -f /opt/atlas/base-freeze.txt /srv/atlas/engines/manifests/rocm-base-freeze.txt && cp -f /opt/atlas/constraints-rocm.txt /srv/atlas/engines/manifests/constraints-rocm.txt' </dev/null \
    || die "could not publish the image manifests into $P4_ENGINES_DIR/manifests (as atlas)"
  log "image manifest: $(grep -E '^(torch|amd-torch-device-gfx1151|rocm-sdk-core)==' "$P4_STATE/rocm-constraints-rocm.txt" | paste -sd ' ' -) (root copy $P4_STATE/rocm-base-freeze.txt; published to $P4_ENGINES_DIR/manifests/)"

  # V11: the first and only place ROCm runs. A fail stops the phase (CONVENTIONS.md §6: V11 required).
  notify "Phase 4 step 1: V11 self-test in $P4_IMAGE"
  if run_verify V11 v11-rocm-selftest.sh "$P4_IMAGE"; then
    log "V11 pass; the base image is proven on gfx1151; GPU run flags ($P4_RUNFLAGS_FILE): $(grep -v '^#' "$P4_RUNFLAGS_FILE" 2>/dev/null | paste -sd ' ' - || echo '?')"
    return 0
  fi
  # Diagnostic only (research §1.1): the same self-test in the community image separates "our image is wrong" from
  # "this kernel/BIOS combination is broken". Folded into the V11 fail record: gate, --status, report and
  # tools/fill-workbook.py read the LATEST record per id, so a second record with another status would mask the fail.
  local v11_msg diag
  v11_msg="$(_p4_v11_last_msg)"
  warn "V11 FAILED in $P4_IMAGE. Running the same self-test once in $P4_DIAG_IMAGE as a diagnostic (~5.4 GB pull, best effort)"
  diag="$(_p4_diag_run)"
  warn "diagnostic ($P4_DIAG_IMAGE --entrypoint /opt/venv/bin/python, VERIFIED venv path): $diag"
  record_v V11 fail "${v11_msg:-V11 fail (no message recorded)}; diagnostic in $P4_DIAG_IMAGE: $diag"
  warn "If the community image also fails (hang, 'Memory critical error', or no device): kernel 7.0 + gfx1151 has open hang reports (ROCm/legacy-rocm-build #6530, ROCm/ROCm #6182); the host kernel, not the container, may be the cause. Adjudicated conflict 19: no Day 1 action, flagged for the Principal in the README."
  notify "Phase 4 STOPPED: V11 failed in $P4_IMAGE (see journalctl -u atlas-day1-phase4)"
  # Rule §7.10: the gate table and the exact next command even when the phase stops here.
  gate phase4 V11 -- V8 V9 || true
  echo "V11 is the Phase 4 gate: fix the image or the kernel question above, then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase4 --force 01"
  die "V11 failed: the ROCm base image did not pass the gfx1151 self-test; nothing else in Phase 4 can run (Section 17 step 1)"
}

# --- Step 02: green engines ------------------------------------------------------------------------------------------
# _p4_arbiter_clear — Section 4.2 rules 3 and 5: nothing weight-bearing from Phase 3 may still be resident when the
# first Phase 4 engine loads. GET /arbiter/status; unload every core/apex/vision/crosscheck engine through the
# Arbiter; refuse if one stays. Unreachable orchestrator: the GTT counter decides (a quiet GPU is under 16 GiB used).
_p4_arbiter_clear() {
  local line code body
  line="$(_p4_orch_curl GET /arbiter/status)"
  code="${line%% *}"; body="${line#* }"
  if [[ "$code" != 200 ]]; then
    local used
    used="$(gpu_gtt_used_mb 2>/dev/null || echo 0)"
    warn "GET $(_p4_orch_url)/arbiter/status answered $code: cannot ask the Arbiter what is resident; GTT used ${used} MiB"
    (( used < 16384 )) || die "GTT used ${used} MiB with the Arbiter unreachable: something is still resident (Section 4.2 rule 5); stop it (systemctl stop 'llama-server@*') or start the orchestrator, then re-run"
    return 0
  fi
  local resident=()
  mapfile -t resident < <(python3 -c '
import json, sys
d = json.loads(sys.argv[1])
for r in d.get("resident") or []:
    if r.get("class") in ("core", "apex", "vision", "crosscheck"):
        print(r.get("engine"))
' "$body" 2>/dev/null || true)
  (( ${#resident[@]} == 0 )) && { log "Arbiter ledger: no core/apex/vision/crosscheck engine resident"; return 0; }
  local e
  for e in "${resident[@]}"; do
    warn "Arbiter reports $e resident from Phase 3: unloading through /arbiter/unload (Section 4.2 rule 5)"
    line="$(_p4_orch_curl POST /arbiter/unload "$(python3 -c 'import json,sys; print(json.dumps({"engine": sys.argv[1], "task_id": "phase4-clear"}))' "$e")")"
    log "unload $e: HTTP ${line%% *}"
  done
  line="$(_p4_orch_curl GET /arbiter/status)"
  body="${line#* }"
  mapfile -t resident < <(python3 -c '
import json, sys
d = json.loads(sys.argv[1])
for r in d.get("resident") or []:
    if r.get("class") in ("core", "apex", "vision", "crosscheck"):
        print(r.get("engine"))
' "$body" 2>/dev/null || true)
  (( ${#resident[@]} == 0 )) || die "still resident after /arbiter/unload: ${resident[*]}; Phase 4 engines need the GPU memory (Section 4.2 rule 3)"
}

step_02() {
  local keys=() key
  mapfile -t keys < <(_p4_keys green)
  _p4_arbiter_clear
  notify "Phase 4 step 2: ${#keys[@]} green engines: ${keys[*]}"
  for key in "${keys[@]}"; do
    _p4_run_engine "$key"    # a green failure is recorded fail; the phase continues (Section 17 step 2)
  done
  log "step 02 done: $(for key in "${keys[@]}"; do printf '%s=%s ' "$key" "$(_p4_result "$key")"; done)"
}

# --- Step 03: yellow engines ------------------------------------------------------------------------------------------
step_03() {
  local keys=() key
  mapfile -t keys < <(_p4_keys yellow)
  notify "Phase 4 step 3: yellow engines: ${keys[*]}"
  for key in "${keys[@]}"; do
    # Section 15.2 / 17 step 3: yellow never blocks. lib-engine.sh already writes result=deferred with outcome=fail
    # (CONVENTIONS §7.4) and the driver's own fallback record does the same; nothing is rewritten here.
    _p4_run_engine "$key"
  done
  log "step 03 done: $(for key in "${keys[@]}"; do printf '%s=%s(outcome %s) ' "$key" "$(_p4_result "$key")" "$(_p4_result "$key" outcome)"; done)"
}

# --- Step 04: V8 PointLLM, V9 Clay/Prithvi ---------------------------------------------------------------------------
step_04() {
  local keys=() key vid res msg
  mapfile -t keys < <(_p4_keys verify)
  notify "Phase 4 step 4: verify engines: ${keys[*]} (V8, V9)"
  for key in "${keys[@]}"; do
    vid="$(_p4_field "$key" verify_id)"
    _p4_run_engine "$key"
    res="$P4_LAST_RESULT"
    [[ "$res" == pass ]] || res=deferred     # Section 17 step 4: mark deferred if they fail
    msg="$key: $(_p4_field "$key" name); built=$(_p4_result "$key" built) loaded=$(_p4_result "$key" loaded) footprint_mb=$(_p4_result "$key" footprint_mb) sample=$(_p4_result "$key" sample_output_path); $(_p4_result "$key" notes | cut -c1-300)"
    [[ -n "$vid" ]] && record_v "$vid" "$res" "$msg"
  done
  log "step 04 done"
}

# --- Step 05: register with the Arbiter --------------------------------------------------------------------------------
step_05() {
  local url keys=() passing=() unmeasured=() key mb bytes code line body registered=0 rows=()
  url="$(_p4_orch_url)"
  mapfile -t keys < <(_p4_keys green yellow verify)
  for key in "${keys[@]}"; do
    [[ "$(_p4_result "$key")" == pass ]] || continue
    mb="$(_p4_result "$key" footprint_mb)"
    if [[ "$mb" =~ ^[0-9]+$ ]]; then
      passing+=("$key")
    else
      # Section 4.2 rule 1: the ledger holds MEASURED footprints. A pass without one is never registered as 0 bytes
      # (rule §7.4); it is recorded and stops the step below.
      warn "$key passed but its result file carries no numeric footprint_mb ('$mb'): not registered (a 0-byte entry would let the Arbiter grant an over-budget load)"
      unmeasured+=("$key")
      rows+=("$key"$'\x1f'"0"$'\x1f'"0"$'\x1f'"no-footprint")
    fi
  done
  if ! curl -sS --noproxy '*' --max-time 5 -o /dev/null "$url/health" 2>/dev/null; then
    warn "orchestrator unreachable at $url/health: nothing can be registered (the step will stop below unless no engine passed)"
  fi
  for key in "${passing[@]}"; do
    mb="$(_p4_result "$key" footprint_mb)"
    bytes=$(( mb * 1048576 ))
    body="$(python3 -c 'import json,sys; print(json.dumps({"engine": sys.argv[1], "total_bytes": int(sys.argv[2]), "task_id": "phase4-register", "key": sys.argv[1], "footprint_mb": int(sys.argv[3]), "class": "phase4"}))' "$key" "$bytes" "$mb")"
    line="$(_p4_orch_curl POST /arbiter/register "$body")"
    code="${line%% *}"
    case "$code" in
      200) log "registered $key with the Arbiter: footprint_mb=$mb"; registered=$(( registered + 1 )) ;;
      404) warn "Arbiter answered 404 for $key: the orchestrator rejected a class-phase4 key (atlas/arbiter.py register_measured accepts them since fix round 2: this is a regression there, or an older orchestrator is running)" ;;
      000) warn "orchestrator unreachable for $key ($url)" ;;
      *)   warn "Arbiter answered HTTP $code for $key: ${line#* }" ;;
    esac
    rows+=("$key"$'\x1f'"$mb"$'\x1f'"$bytes"$'\x1f'"$code")
  done
  python3 - "$P4_STATE/registry.json" "$url" "${rows[@]}" <<'PY'
import json, sys, time
path, url, *rows = sys.argv[1:]
out = {"orchestrator_url": url, "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engines": []}
for r in rows:
    key, mb, b, code = r.split("\x1f")
    out["engines"].append({"key": key, "footprint_mb": int(mb) if code != "no-footprint" else None,
                           "total_bytes": int(b) if code != "no-footprint" else None, "class": "phase4",
                           "registered": code == "200", "http": code})
json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
print(f"{path}: {len(out['engines'])} passing engines, {sum(1 for e in out['engines'] if e['registered'])} registered")
PY
  if (( registered != ${#passing[@]} || ${#unmeasured[@]} > 0 )); then
    # Rule §7.10: the per-engine table AND the gate table still print before the stop; the gate marker is not written
    # (step 06 does that) while step 05 is undone.
    echo; _p4_table; echo
    gate phase4 V11 -- V8 V9 || true
    (( ${#unmeasured[@]} == 0 )) || warn "step 05: passing engine(s) without a measured footprint: ${unmeasured[*]} (delete $P4_STATE/<key>.json and re-run the engine with --force 02/03/04 so the GTT delta is measured)"
    die "step 05: $registered of ${#passing[@]} measured passing engines registered with the Arbiter at $url${unmeasured:+; ${#unmeasured[@]} passing engine(s) unmeasured} (404 = the running orchestrator rejects class-phase4 keys: restart it on the current atlas package, or fix atlas/arbiter.py; 000 = orchestrator down), then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase4 (the step is not marked done)"
  fi
  (( ${#passing[@]} > 0 )) || warn "step 05: no engine passed, nothing to register"
  log "step 05 done: $registered registered with the Arbiter at $url; registry $P4_STATE/registry.json"
}

# --- Step 06: table and gate -------------------------------------------------------------------------------------------
# _p4_table — print the Section 17 step 6 table, write $P4_STATE/summary.json (root-held) and publish a copy to
# $ATLAS_SRV/engines/phase4-results.json as atlas (fix round 2: root never opens a path under /srv for writing; the
# summary carries test-influenced `notes`).
_p4_table() {
  python3 - "$P4_JSON" "$P4_STATE" <<'PY'
import json, os, sys, time
cfg, state = sys.argv[1:3]
doc = json.load(open(cfg, encoding="utf-8"))
rows = []
for e in doc["engines"]:
    p = os.path.join(state, e["key"] + ".json")
    if os.path.isfile(p):
        r = json.load(open(p, encoding="utf-8"))
    else:
        r = {"result": "deferred" if e["tier"] == "deferred" else "missing", "outcome": None, "built": False,
             "loaded": False, "sample_output_path": None, "footprint_mb": None, "seconds": None,
             "notes": e.get("research_note", "") if e["tier"] == "deferred" else "not run"}
    rows.append({"key": e["key"], "name": e["name"], "tier": e["tier"], "verify_id": e.get("verify_id"),
                 "owner": e.get("owner"), "result": r.get("result"), "outcome": r.get("outcome"),
                 "blocking": e["tier"] == "green", "built": r.get("built"), "loaded": r.get("loaded"),
                 "sample_output_path": r.get("sample_output_path"), "footprint_mb": r.get("footprint_mb"),
                 "footprint_gb_expected": e.get("footprint_gb_expected"), "seconds": r.get("seconds"),
                 "licence_note": e.get("licence_note"), "notes": (r.get("notes") or "")})
fmt = "{:<18} {:<8} {:<15} {:<5} {:<6} {:>9} {:>7}  {}"
print(fmt.format("ENGINE", "TIER", "RESULT", "BUILT", "LOADED", "FOOTPRINT", "SECS", "SAMPLE / NOTE"))
for r in rows:
    fp = f"{r['footprint_mb']} MiB" if r["footprint_mb"] is not None else "-"
    res = r["result"] or "?"
    if r["outcome"] and r["outcome"] != r["result"]:
        res = f"{res}({r['outcome']})"          # e.g. deferred(fail): a yellow/verify attempt that failed
    tail = r["sample_output_path"] or (r["notes"][:70] if r["notes"] else "")
    print(fmt.format(r["key"], r["tier"], res, "yes" if r["built"] else "no",
                     "yes" if r["loaded"] else "no", fp, r["seconds"] if r["seconds"] is not None else "-", tail))
notes = [(r["key"], r["licence_note"]) for r in rows if r["licence_note"]]
if notes:
    print("Licence notes for the Principal (config/phase4-engines.json licence_note):")
    for key, note in notes:
        print(f"  {key}: {note}")
summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engines": rows}
path = os.path.join(state, "summary.json")
with open(path + ".tmp", "w", encoding="utf-8") as fh:
    json.dump(summary, fh, indent=2)
    fh.write("\n")
os.replace(path + ".tmp", path)
PY
  if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
    _p4_publish "$P4_STATE/summary.json" /srv/atlas/engines/phase4-results.json \
      || warn "could not publish $P4_STATE/summary.json to $P4_ENGINES_DIR/phase4-results.json (the root-held copy stands)"
  fi
}

step_06() {
  echo
  echo "Phase 4 engines (Section 17 step 6 table: built, loaded, sample output, footprint, pass/fail/deferred; RESULT shows deferred(fail) when a non-blocking attempt failed):"
  _p4_table
  echo "Per-engine detail: $P4_STATE/<key>.json; logs: $ATLAS_LOG_DIR/phase4-<key>.log; samples: $ATLAS_SRV/workspace/phase4-samples/<key>/; venv freezes: $P4_ENGINES_DIR/manifests/<key>-freeze.txt; pull records and pins: $P4_STATE/hf-manifests/, $P4_STATE/git-pins.json"
  echo
  # CONVENTIONS.md §6: V11 required; V8, V9 recorded, deferred never blocks.
  if gate phase4 V11 -- V8 V9; then
    notify "Phase 4 gate: PASS. Next: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} report"
    return 0
  fi
  notify "Phase 4 gate: FAIL (see journalctl -u atlas-day1-phase4)"
  return 1
}

# --- Run -------------------------------------------------------------------------------------------------------------
log "Phase 4 (multimodal engines in the ROCm container) starting; image $P4_IMAGE; engines: ${P4_ALL_KEYS[*]}; log $(_atlas_log_file)"
[[ "$ATLAS_DRY_RUN" == "1" ]] || notify "Phase 4 starting (detached; follow with: journalctl -u atlas-day1-phase4 -f)"
run_step phase4 01 step_01
run_step phase4 02 step_02
run_step phase4 03 step_03
run_step phase4 04 step_04
run_step phase4 05 step_05
run_step phase4 06 step_06
if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
  echo
  phase_status phase4
fi
log "Phase 4 driver finished"
