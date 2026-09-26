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
#       torch 2.13.0; conflict 12) built through the allowlist proxy, its freeze/constraints copied to
#       $ATLAS_SRV/engines/manifests/, then V11 (verify/v11-rocm-selftest.sh -> phase4/selftest.py: rocminfo gfx1151,
#       torch sees the device, matmul, 4 GB alloc, two-step diffusion). V11 fail STOPS the phase (Section 17 step 1:
#       the first and only place ROCm runs); before stopping, the same self-test runs once in the community image
#       kyuz0/amd-strix-halo-comfyui as a diagnostic (research §1.1) and conflict 19's kernel note is logged.
#   02  green engines in Section 17's order, each by phase4/engines/<key>.sh under its json timeout; per-engine JSON at
#       $ATLAS_STATE/phase4/<key>.json (built, loaded, sample_output_path, footprint_mb from the GTT delta, seconds);
#       a failure is recorded fail and the phase CONTINUES (per-engine pass/fail, never block).
#   03  yellow engines (trellis, blender-cycles with the CPU fallback): deferred on failure.
#   04  pointllm -> V8, clay-prithvi -> V9: pass or deferred, recorded with record_v.
#   05  every passing engine registered with the Arbiter: POST $ORCH_URL/arbiter/register; unreachable -> log, continue.
#   06  the per-engine table, $ATLAS_STATE/phase4/summary.json (+ copy at $ATLAS_SRV/engines/phase4-results.json),
#       `gate phase4 V11 -- V8 V9`.
#
# Contracts relied on from other writers (CONVENTIONS.md §1; each is checked and fails loudly when absent):
#   * /etc/atlas/docker.env (phase1/06-docker.sh): ATLAS_UID ATLAS_GID RENDER_GID VIDEO_GID CONTAINER_HTTP_PROXY
#     CONTAINER_HTTPS_PROXY CONTAINER_NO_PROXY (containers reach squid at the docker0 gateway; DOCKER-USER blocks the rest).
#   * /etc/atlas/secrets/hf-token.env (phase2-services.sh): HF_TOKEN=... for the gated repos (FLUX.1-dev, Stable Audio
#     Open; conflict 16). Absent -> those two engines fail with the licence URL, the phase continues.
#   * /etc/atlas/orchestrator.env (phase2/02-orchestrator.sh): ORCH_URL. The orchestrator's POST /arbiter/register takes
#     {engine, total_bytes, task_id} (atlas/api.py); this driver also sends {key, footprint_mb, class: "phase4"} as the
#     brief states them (extra fields are ignored by the pydantic model). CONTRACT GAP, reported to the orchestrator
#     writer: Arbiter.register_measured raises UnknownEngine (HTTP 404) for keys not in config/engines.json, and the
#     Phase 4 keys are not there; a 404 is logged and the registration is persisted in $ATLAS_STATE/phase4/registry.json
#     (and $ATLAS_SRV/engines/phase4-results.json) for the Arbiter to load, never a failure of this phase.
# Contracts this file defines for others: $ATLAS_STATE/phase4/<key>.json (schema in phase4/lib-engine.sh),
# $ATLAS_STATE/phase4/summary.json and $ATLAS_SRV/engines/phase4-results.json (the Section 17 step 6 table as JSON),
# $ATLAS_STATE/phase4/registry.json ([{key, footprint_mb, total_bytes, class, registered, http}]).
# Per-engine logs: $ATLAS_STATE/logs/phase4-<key>.log. To redo one engine: delete $ATLAS_STATE/phase4/<key>.json and
# re-run with --force 02 (or 03/04); completed builds, venvs and pulls are skipped by the engine scripts themselves.

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
P4_LAST_RESULT=""

[[ -f "$P4_JSON" ]] || die "$P4_JSON is missing"
_p4_cfg() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["base_image"][sys.argv[2]])' "$P4_JSON" "$1"; }
P4_IMAGE="$(_p4_cfg tag)"; P4_LABEL="$(_p4_cfg label)"; P4_IMAGE_VER="$(_p4_cfg version)"
P4_DIAG_IMAGE="$(_p4_cfg diagnostic_fallback_image)"
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
  # -k 60: after TERM the script's EXIT trap kills its container and writes the result; KILL only if that stalls.
  P4_IMAGE="$P4_IMAGE" P4_TIMEOUT_S="$timeout_s" P4_LOAD_TIMEOUT_S="$P4_LOAD_TIMEOUT_S" \
    timeout --foreground -k 60 "$timeout_s" "$script" </dev/null || rc=$?
  local res
  res="$(_p4_result "$key")"
  if [[ "$res" == missing || -z "$res" ]]; then
    # The script died before its trap could write (or was SIGKILLed): record it here so the table has a row.
    res=fail; [[ "$tier" == green ]] || res=deferred
    python3 - "$P4_STATE/$key.json" "$key" "$(_p4_field "$key" name)" "$tier" "$res" "$rc" "$(( SECONDS - t0 ))" \
              "$(tail -n 20 "$ATLAS_LOG_DIR/phase4-$key.log" 2>/dev/null || true)" <<'PY'
import json, sys, time
path, key, name, tier, res, rc, secs, tail = sys.argv[1:9]
rec = {"key": key, "name": name, "tier": tier, "result": res, "built": False, "loaded": False,
       "sample_output_path": None, "footprint_mb": None, "seconds": int(secs), "exit_code": int(rc),
       "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
       "notes": f"engine script exited {rc} without writing a result (timeout={'yes' if rc in (124, 137) else 'no'})",
       "log_tail": tail.splitlines()[-20:]}
json.dump(rec, open(path, "w", encoding="utf-8"), indent=2)
PY
  fi
  P4_LAST_RESULT="$res"
  log "$key: $res (exit $rc, $(( (SECONDS - t0) / 60 )) min, footprint_mb=$(_p4_result "$key" footprint_mb), sample=$(_p4_result "$key" sample_output_path))"
  notify "Phase 4: $key -> $res"
  return 0
}

# --- Step 01: base image + V11 -----------------------------------------------------------------------------------------
step_01() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  [[ -r "$P4_DOCKER_ENV" ]] || die "$P4_DOCKER_ENV missing (Phase 1 step 6 writes the container proxy and gids)"
  [[ -f "$ATLAS_ETC/proxy.env" ]] || die "$ATLAS_ETC/proxy.env missing: every download must go through the allowlist proxy (rule §7.1)"
  [[ -d "$P4_ENGINES_DIR" ]] || die "$P4_ENGINES_DIR missing: the 8 TB data volume is not mounted (Phase 1 step 3)"
  [[ -f "$P4_DOCKERFILE_DIR/Dockerfile" ]] || die "$P4_DOCKERFILE_DIR/Dockerfile is missing"
  local hp sp np uid gid
  hp="$(awk -F= '$1=="CONTAINER_HTTP_PROXY" {print $2; exit}' "$P4_DOCKER_ENV")"
  sp="$(awk -F= '$1=="CONTAINER_HTTPS_PROXY" {print $2; exit}' "$P4_DOCKER_ENV")"
  np="$(awk -F= '$1=="CONTAINER_NO_PROXY" {sub(/^[^=]*=/, ""); print; exit}' "$P4_DOCKER_ENV")"
  uid="$(awk -F= '$1=="ATLAS_UID" {print $2; exit}' "$P4_DOCKER_ENV")"
  gid="$(awk -F= '$1=="ATLAS_GID" {print $2; exit}' "$P4_DOCKER_ENV")"
  [[ -n "$sp" && -n "$uid" && -n "$gid" ]] || die "$P4_DOCKER_ENV lacks CONTAINER_HTTPS_PROXY / ATLAS_UID / ATLAS_GID"
  [[ -n "$hp" ]] || hp="$sp"
  [[ -n "$np" ]] || np="localhost,127.0.0.1"

  ensure_dir "$P4_ENGINES_DIR/manifests" "$uid:$gid" 755
  ensure_dir "$P4_ENGINES_DIR/home" "$uid:$gid" 755
  ensure_dir "$P4_ENGINES_DIR/miopen" "$uid:$gid" 755
  ensure_dir "$P4_ENGINES_DIR/hf" "$uid:$gid" 755
  ensure_dir "$ATLAS_SRV/workspace/phase4-samples" "$uid:$gid" 755
  mkdir -p "$P4_STATE"

  if [[ "$(docker image inspect -f "{{index .Config.Labels \"$P4_LABEL\"}}" "$P4_IMAGE" 2>/dev/null)" == "$P4_IMAGE_VER" ]]; then
    log "$P4_IMAGE already built (label $P4_LABEL=$P4_IMAGE_VER); skipping the build"
  else
    notify "Phase 4 step 1: building $P4_IMAGE (ROCm 10.0.0 gfx1151 wheels, ~5 GB through the proxy)"
    log "docker build $P4_IMAGE from $P4_DOCKERFILE_DIR (index $(_p4_cfg rocm_index); stable.repo.amd.com, pypi.org, files.pythonhosted.org, .ubuntu.com must be allowlisted)"
    docker build --pull -t "$P4_IMAGE" \
      --build-arg "http_proxy=$hp" --build-arg "https_proxy=$sp" --build-arg "HTTP_PROXY=$hp" --build-arg "HTTPS_PROXY=$sp" \
      --build-arg "no_proxy=$np" --build-arg "NO_PROXY=$np" \
      --build-arg "ATLAS_UID=$uid" --build-arg "ATLAS_GID=$gid" \
      --build-arg "ROCM_INDEX=$(_p4_cfg rocm_index)" --build-arg "ROCM_VER=$(_p4_cfg rocm_version)" \
      "$P4_DOCKERFILE_DIR" </dev/null \
      || die "docker build of $P4_IMAGE failed. If pip could not resolve torch==$(_p4_cfg torch) for cp312 (UNVERIFIED cp-tag list, research §1.1), drop the == pins in the Dockerfile and let the [device-gfx1151] extras resolve; otherwise check /var/log/squid/access.log for TCP_DENIED"
  fi
  # The resolved wheel set is the manifest of this image (rule §7.9; Appendix C: engines/ is backed up as manifests).
  docker run --rm --network none "$P4_IMAGE" cat /opt/atlas/base-freeze.txt >"$P4_ENGINES_DIR/manifests/rocm-base-freeze.txt" \
    || die "could not read /opt/atlas/base-freeze.txt from $P4_IMAGE"
  docker run --rm --network none "$P4_IMAGE" cat /opt/atlas/constraints-rocm.txt >"$P4_ENGINES_DIR/manifests/constraints-rocm.txt" \
    || die "could not read /opt/atlas/constraints-rocm.txt from $P4_IMAGE"
  chown "$uid:$gid" "$P4_ENGINES_DIR/manifests/"*.txt
  log "image manifest: $(grep -E '^(torch|amd-torch-device-gfx1151|rocm-sdk-core)==' "$P4_ENGINES_DIR/manifests/constraints-rocm.txt" | paste -sd ' ' -)"

  # V11: the first and only place ROCm runs. A fail stops the phase (CONVENTIONS.md §6: V11 required).
  notify "Phase 4 step 1: V11 self-test in $P4_IMAGE"
  if run_verify V11 v11-rocm-selftest.sh "$P4_IMAGE"; then
    log "V11 pass; the base image is proven on gfx1151"
    return 0
  fi
  # Diagnostic only (research §1.1): the same self-test in the community image separates "our image is wrong" from
  # "this kernel/BIOS combination is broken". Best effort; never changes the V11 record.
  warn "V11 FAILED in $P4_IMAGE. Running the same self-test once in $P4_DIAG_IMAGE as a diagnostic (~5.4 GB pull, best effort)"
  local diag="not run"
  if retry 2 docker pull -q "$P4_DIAG_IMAGE" >/dev/null 2>&1; then
    local rc=0 out
    out="$(ATLAS_PHASE=phase4 timeout --foreground -k 15 540 docker run --rm --device /dev/kfd --device /dev/dri \
             --group-add "$(awk -F= '$1=="VIDEO_GID" {print $2; exit}' "$P4_DOCKER_ENV")" \
             --group-add "$(awk -F= '$1=="RENDER_GID" {print $2; exit}' "$P4_DOCKER_ENV")" \
             --security-opt seccomp=unconfined --ipc=host --network none \
             -e V11_OUT=/tmp/v11-diag.json -e HF_HUB_OFFLINE=1 \
             -v "$ATLAS_DAY1_DIR/phase4:/opt/atlas/phase4:ro" \
             "$P4_DIAG_IMAGE" /opt/venv/bin/python /opt/atlas/phase4/selftest.py 2>&1 </dev/null | tail -n 12)" || rc=$?
    diag="exit $rc: $(tr '\n' ' ' <<<"$out" | cut -c1-600)"
    record_v V11 info "diagnostic in $P4_DIAG_IMAGE: $diag"
  else
    diag="could not pull $P4_DIAG_IMAGE (registry-1.docker.io allowlisted?)"
  fi
  warn "diagnostic ($P4_DIAG_IMAGE /opt/venv/bin/python, VERIFIED venv path): $diag"
  warn "If the community image also fails (hang, 'Memory critical error', or no device): kernel 7.0 + gfx1151 has open hang reports (ROCm/legacy-rocm-build #6530, ROCm/ROCm #6182); the host kernel, not the container, may be the cause. Adjudicated conflict 19: no Day 1 action, flagged for the Principal in the README."
  notify "Phase 4 STOPPED: V11 failed in $P4_IMAGE (see journalctl -u atlas-day1-phase4)"
  die "V11 failed: the ROCm base image did not pass the gfx1151 self-test; nothing else in Phase 4 can run (Section 17 step 1)"
}

# --- Step 02: green engines ------------------------------------------------------------------------------------------
step_02() {
  local keys=() key
  mapfile -t keys < <(_p4_keys green)
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
    _p4_run_engine "$key"
    # Section 15.2: yellow never blocks; a fail from the script is recorded as deferred.
    if [[ "$P4_LAST_RESULT" == fail ]]; then
      python3 - "$P4_STATE/$key.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p, encoding="utf-8")); d["result"] = "deferred"
d["notes"] = (d.get("notes") or "") + "; yellow tier: recorded deferred"
json.dump(d, open(p, "w", encoding="utf-8"), indent=2)
PY
    fi
  done
  log "step 03 done: $(for key in "${keys[@]}"; do printf '%s=%s ' "$key" "$(_p4_result "$key")"; done)"
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
_p4_orch_url() {
  local url=""
  [[ -r "$P4_ORCH_ENV" ]] && url="$(sed -nE "s/^ORCH_URL=['\"]?([^'\"]+)['\"]?$/\1/p" "$P4_ORCH_ENV" | head -n1)"
  [[ -n "$url" ]] || url="http://127.0.0.1:${ORCH_PORT:-8800}"
  printf '%s\n' "$url"
}

step_05() {
  local url keys=() key mb bytes code body registered=0 rows=()
  url="$(_p4_orch_url)"
  mapfile -t keys < <(_p4_keys green yellow verify)
  if ! curl -sS --noproxy '*' --max-time 5 -o /dev/null "$url/health" 2>/dev/null; then
    warn "orchestrator unreachable at $url/health: registrations are persisted in $P4_STATE/registry.json only (log and continue)"
  fi
  for key in "${keys[@]}"; do
    [[ "$(_p4_result "$key")" == pass ]] || continue
    mb="$(_p4_result "$key" footprint_mb)"
    [[ "$mb" =~ ^[0-9]+$ ]] || mb=0
    bytes=$(( mb * 1048576 ))
    body="$(python3 -c 'import json,sys; print(json.dumps({"engine": sys.argv[1], "total_bytes": int(sys.argv[2]), "task_id": "phase4-register", "key": sys.argv[1], "footprint_mb": int(sys.argv[3]), "class": "phase4"}))' "$key" "$bytes" "$mb")"
    code="$(curl -sS --noproxy '*' --max-time 15 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d "$body" "$url/arbiter/register" 2>/dev/null || true)"
    [[ "$code" =~ ^[0-9]{3}$ ]] || code=000
    case "$code" in
      200) log "registered $key with the Arbiter: footprint_mb=$mb"; registered=$(( registered + 1 )) ;;
      404) warn "Arbiter answered 404 for $key: the Arbiter only knows config/engines.json keys (contract gap, header); persisted for the orchestrator writer" ;;
      000) warn "orchestrator unreachable for $key ($url); persisted, continuing" ;;
      *)   warn "Arbiter answered HTTP $code for $key; persisted, continuing" ;;
    esac
    rows+=("$key"$'\t'"$mb"$'\t'"$bytes"$'\t'"$code")
  done
  python3 - "$P4_STATE/registry.json" "$url" "${rows[@]}" <<'PY'
import json, sys, time
path, url, *rows = sys.argv[1:]
out = {"orchestrator_url": url, "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engines": []}
for r in rows:
    key, mb, b, code = r.split("\t")
    out["engines"].append({"key": key, "footprint_mb": int(mb), "total_bytes": int(b), "class": "phase4",
                           "registered": code == "200", "http": code})
json.dump(out, open(path, "w", encoding="utf-8"), indent=2)
print(f"{path}: {len(out['engines'])} passing engines, {sum(1 for e in out['engines'] if e['registered'])} registered")
PY
  log "step 05 done: $registered registered with the Arbiter at $url; registry $P4_STATE/registry.json"
}

# --- Step 06: table and gate -------------------------------------------------------------------------------------------
_p4_table() {
  python3 - "$P4_JSON" "$P4_STATE" "$P4_ENGINES_DIR/phase4-results.json" <<'PY'
import json, os, sys, time
cfg, state, copy_to = sys.argv[1:4]
doc = json.load(open(cfg, encoding="utf-8"))
rows = []
for e in doc["engines"]:
    p = os.path.join(state, e["key"] + ".json")
    if os.path.isfile(p):
        r = json.load(open(p, encoding="utf-8"))
    else:
        r = {"result": "deferred" if e["tier"] == "deferred" else "missing", "built": False, "loaded": False,
             "sample_output_path": None, "footprint_mb": None, "seconds": None,
             "notes": e.get("research_note", "") if e["tier"] == "deferred" else "not run"}
    rows.append({"key": e["key"], "name": e["name"], "tier": e["tier"], "verify_id": e.get("verify_id"),
                 "owner": e.get("owner"), "result": r.get("result"), "built": r.get("built"), "loaded": r.get("loaded"),
                 "sample_output_path": r.get("sample_output_path"), "footprint_mb": r.get("footprint_mb"),
                 "footprint_gb_expected": e.get("footprint_gb_expected"), "seconds": r.get("seconds"),
                 "licence_note": e.get("licence_note"), "notes": (r.get("notes") or "")})
fmt = "{:<18} {:<8} {:<9} {:<5} {:<6} {:>9} {:>7}  {}"
print(fmt.format("ENGINE", "TIER", "RESULT", "BUILT", "LOADED", "FOOTPRINT", "SECS", "SAMPLE / NOTE"))
for r in rows:
    fp = f"{r['footprint_mb']} MiB" if r["footprint_mb"] is not None else "-"
    tail = r["sample_output_path"] or (r["notes"][:70] if r["notes"] else "")
    print(fmt.format(r["key"], r["tier"], r["result"] or "?", "yes" if r["built"] else "no",
                     "yes" if r["loaded"] else "no", fp, r["seconds"] if r["seconds"] is not None else "-", tail))
summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "engines": rows}
for path in (os.path.join(state, "summary.json"), copy_to):
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
            fh.write("\n")
        os.replace(path + ".tmp", path)
    except OSError as exc:
        print(f"(could not write {path}: {exc})", file=sys.stderr)
PY
}

step_06() {
  echo
  echo "Phase 4 engines (Section 17 step 6 table: built, loaded, sample output, footprint, pass/fail/deferred):"
  _p4_table
  echo "Per-engine detail: $P4_STATE/<key>.json; logs: $ATLAS_LOG_DIR/phase4-<key>.log; samples: $ATLAS_SRV/workspace/phase4-samples/<key>/"
  echo "Licence notes for the Principal: FLUX.1-dev (non-commercial, accepted 2026-09-21), PointLLM (cc-by-nc-4.0, unverified), Rad-DINO (research use per its card)."
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
