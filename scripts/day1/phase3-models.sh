#!/usr/bin/env bash
# phase3-models.sh — Phase 3 driver: core LLM pull and load tests (ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 3,
# Section 21 V4, V10, V14b, V21, V22; R19). Long, detached under systemd, resumable at the file level.
#
#   sudo ./atlas-day1.sh phase3 [--dry-run] [--force STEP] [--status] [--foreground]   (the entry point re-execs this)
#   phase3-models.sh --run          the in-unit entry started by detached_phase (atlas-day1-phase3)
#   phase3-models.sh                without --run and not --dry-run: detaches itself (same unit) and returns
#
# Steps (CONVENTIONS.md §1, §6; each wrapped in run_step, so a re-run skips what is complete):
#   01  pull: every engine whose arbiter_class is core/apex/vision/crosscheck (the seven GGUF engines of Section 5.1),
#       hashes and sizes read from the Hugging Face tree API at pull time (research conflict a), resumable per file,
#       Meditron raw splits cat-joined (conflict d), enumerated shard names for the abliterated twin and Nemotron
#       (conflicts e, f); then $ATLAS_SRV/models/MANIFEST.json, the file AEGIS backs up instead of weights (Section 9.5).
#   02  load tests, one engine at a time, by phase3/loadtest.py: load through the orchestrator's Arbiter API (systemctl
#       fallback, said so in the log), /health, V4 proof from the unit's journal (conflict h), decode/prefill tok/s at
#       512 and 8k prompt tokens, swap time (unload previous + load this), unload, GTT release (tolerance 2 GiB, 120 s).
#       DeepSeek V4 Flash ladders f16 -> q8_0 -> q4_0 with a coherence prompt and writes the winner to overrides.json
#       (conflict b); any DeepSeek failure records V22 deferred and continues (R19).
#   03  two-residency: gpt-oss-120b + qwen2.5-vl-72b (vision at parallel_coresident), ~142 GB of weights plus caches on
#       the GTT counter, two generation requests through the orchestrator, the second proven to start after the first
#       finished (V21, V14b).
#   04  gate: the Section 17 table (engine, load ok, KV type, decode 512/8k, prefill, swap s, released) and
#       `gate phase3 V4 V10 V14b V21 -- V22`.
#
# Contracts relied on from other writers (CONVENTIONS.md §1; each is checked and fails loudly when absent):
#   * config/engines.json — schema per its _meta block (key, hf_repo, subdir, files[{name,sha256,bytes}], enumerate_pattern,
#     join_into, mmproj, quant, footprint_gb, ctx_size, parallel, parallel_coresident, ctx_size_coresident, kv_class,
#     kv_ladder, ctx_size_f16_cap, n_keep, extra_args, expected_decode_tok_s, kv_proof_lines, arbiter_class, known_issue).
#   * phase2/04-memory.sh — its header offers `ej`, `hf_tree_lfs REPO [SUBDIR]` and `pull_engine_files KEY` to this
#     driver (the reference implementation of the manifest-at-pull-time rule; it only defines functions). Sourced here.
#   * phase2/engine-env.py — renders $ATLAS_ETC/engines/<key>.env (ATLAS_KV_TYPE, ATLAS_CTX_SIZE, ATLAS_PARALLEL,
#     ATLAS_MODEL_PRESENT, ATLAS_KV_PROOF_LINES, LLAMA_ARG_PORT, ...) and owns $ATLAS_ETC/engines/overrides.json
#     (--set-override KEY kv_type|ctx_size|parallel|coresident VALUE, --clear-override KEY FIELD, --key KEY).
#   * systemd/llama-server@.service (phase2/01-llama.sh): `systemctl start` returns only when /health is 200; the
#     journal of llama-server@<key> carries the "llama_kv_cache: size = ... K (q8_0): ... V (q8_0): ..." proof line.
#   * the orchestrator (phase2/02-orchestrator.sh, $ATLAS_ETC/orchestrator.env ORCH_URL/ORCH_PORT): the Arbiter API
#     and chat contract are stated in phase3/loadtest.py's header; when the orchestrator is down loadtest.py falls back
#     to `systemctl start|stop llama-server@<key>` and says so, and the queueing proof (V14b) is then recorded as fail
#     with that reason, never as pass.
# Contract this file defines for others: $ATLAS_STATE/phase3/results/<key>.json (one per engine, schema in loadtest.py)
# and $ATLAS_SRV/models/MANIFEST.json (the AEGIS manifest: key, repo, quant, files[{name,bytes,sha256}], pulled_at).

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"

export ATLAS_PHASE=phase3
require_root
parse_common_args "$@"

P3_STATE="$ATLAS_STATE/phase3"
P3_RESULTS="$P3_STATE/results"
P3_LOADTEST="$ATLAS_DAY1_DIR/phase3/loadtest.py"
P3_ENGINE_ENV_PY="$ATLAS_DAY1_DIR/phase2/engine-env.py"
P3_MEMORY_STEP="$ATLAS_DAY1_DIR/phase2/04-memory.sh"
P3_ENGINES_JSON="$ATLAS_DAY1_DIR/config/engines.json"
P3_MODELS_DIR="$ATLAS_SRV/models"
P3_CLASSES="core apex vision crosscheck"      # CONVENTIONS.md §8: the seven GGUF engines; resident models are Phase 2's
P3_TEXT_KEY="gpt-oss-120b"                     # Section 17 step 3 / Section 4.1: the everyday pairing
P3_VISION_KEY="qwen2.5-vl-72b"

# --- Pre-flight ------------------------------------------------------------------------------------------------------
if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
  load_env
  # CONVENTIONS.md §6: Phase 3 refuses to start without the Phase 2 gate marker (atlas-day1.sh checks too; direct runs).
  [[ -e "$ATLAS_DONE_DIR/phase2.gate" ]] \
    || die "Phase 2 has not passed its gate ($ATLAS_DONE_DIR/phase2.gate missing). Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2"
fi

# Without --run (the in-unit entry) this driver detaches itself so a dropped SSH session cannot kill a 15-hour pull
# (Section 17: "detached under systemd"). --dry-run always runs here, in the foreground, and touches nothing.
if [[ "${ATLAS_IN_UNIT:-0}" != "1" && "$ATLAS_DRY_RUN" != "1" ]]; then
  detached_phase phase3 "$(readlink -f "$0")"
  exit 0
fi

for f in "$P3_LOADTEST" "$P3_ENGINE_ENV_PY" "$P3_MEMORY_STEP" "$P3_ENGINES_JSON"; do
  [[ -f "$f" ]] || die "$f is missing (CONVENTIONS.md §1 layout; written by its own author)"
done
# shellcheck source=phase2/04-memory.sh
source "$P3_MEMORY_STEP"
for fn in ej hf_tree_lfs pull_engine_files; do
  declare -F "$fn" >/dev/null || die "phase2/04-memory.sh does not define $fn (its header promises it to the Phase 3 driver)"
done

# The seven engine keys in engines.json order (CONVENTIONS.md §8 order is the port order and the test order).
P3_KEYS=()
mapfile -t P3_KEYS < <(python3 - "$P3_ENGINES_JSON" "$P3_CLASSES" <<'PY'
import json, sys
classes = set(sys.argv[2].split())
for e in json.load(open(sys.argv[1], encoding="utf-8"))["engines"]:
    if e.get("arbiter_class") in classes:
        print(e["key"])
PY
)
(( ${#P3_KEYS[@]} > 0 )) || die "engines.json lists no engine with arbiter_class in {$P3_CLASSES}"

# --- Helpers ---------------------------------------------------------------------------------------------------------
# _p3_loadtest SUBCMD ARGS... — run phase3/loadtest.py with every path it needs; its stdout carries only
# "RECORD<TAB>ID<TAB>RESULT<TAB>MSG" lines (recorded here with record_v), its stderr the log. A non-zero exit is an
# infrastructure error (not a failed measurement, which loadtest.py records) and stops the phase.
_p3_loadtest() {
  local sub="$1"; shift
  local orch_url="" out rc=0
  if [[ -r "$ATLAS_ETC/orchestrator.env" ]]; then
    orch_url="$(sed -nE "s/^ORCH_URL=['\"]?([^'\"]+)['\"]?$/\1/p" "$ATLAS_ETC/orchestrator.env" | head -n1)"
  fi
  [[ -n "$orch_url" ]] || orch_url="http://127.0.0.1:${ORCH_PORT:-8800}"
  mkdir -p "$P3_RESULTS"
  out="$(mktemp)"
  # Local engines and the orchestrator are loopback: the allowlist proxy must not see them (loadtest.py also bypasses it).
  python3 "$P3_LOADTEST" \
    --engines "$P3_ENGINES_JSON" --env-dir "$ATLAS_ETC/engines" --results-dir "$P3_RESULTS" \
    --orch-url "$orch_url" --engine-env "$P3_ENGINE_ENV_PY" --models-dir "$P3_MODELS_DIR" \
    --slots-dir "$ATLAS_SRV/data/slots" --port-base "${LLAMA_PORT_BASE:-8100}" \
    --overrides "$ATLAS_ETC/engines/overrides.json" \
    "$sub" "$@" >"$out" || rc=$?
  local tag id result msg
  while IFS=$'\t' read -r tag id result msg; do
    [[ "$tag" == "RECORD" ]] || continue
    record_v "$id" "$result" "$msg"
  done <"$out"
  rm -f "$out"
  (( rc == 0 )) || die "loadtest.py $sub failed with exit $rc (infrastructure error, see the log above); re-run to resume"
}

# _p3_render_envs — re-render every engine env file so ATLAS_MODEL_PRESENT reflects the files on disk.
_p3_render_envs() {
  python3 "$P3_ENGINE_ENV_PY" \
    --engines "$P3_ENGINES_JSON" --out "$ATLAS_ETC/engines" --models-dir "$P3_MODELS_DIR" \
    --slots-dir "$ATLAS_SRV/data/slots" --port-base "${LLAMA_PORT_BASE:-8100}" \
    --overrides "$ATLAS_ETC/engines/overrides.json" \
    || die "engine-env.py failed to render $ATLAS_ETC/engines/*.env"
  chown root:atlas "$ATLAS_ETC/engines"/*.env 2>/dev/null || true
  chmod 640 "$ATLAS_ETC/engines"/*.env 2>/dev/null || true
}

# _p3_remaining_bytes KEY — bytes still to download for one engine: tree-API sizes of the wanted files minus what is
# already on disk (complete file, .part, or the joined file). Prints one integer. Dies when the tree cannot be read.
_p3_remaining_bytes() {
  local key="$1" repo subdir treef rc=0
  repo="$(ej "$key" hf_repo)"; subdir="$(ej "$key" subdir)"
  [[ -n "$repo" ]] || die "engines.json: $key has no hf_repo"
  treef="$(mktemp)"
  hf_tree_lfs "$repo" "$subdir" >"$treef"
  [[ -s "$treef" ]] || { rm -f "$treef"; die "$repo${subdir:+/$subdir} lists no files on the Hugging Face tree API"; }
  python3 - "$P3_ENGINES_JSON" "$key" "$P3_MODELS_DIR/$key" "$treef" <<'PY' || rc=$?
import fnmatch, json, os, sys
path, key, dest_dir, treef = sys.argv[1:5]
eng = next(e for e in json.load(open(path, encoding="utf-8"))["engines"] if e["key"] == key)
tree = {}
for line in open(treef, encoding="utf-8").read().splitlines():
    p, size, _oid = line.split("\t")
    tree[p] = int(size) if size.isdigit() else 0
subdir = eng.get("subdir") or ""
wanted = []
if eng.get("enumerate_pattern"):
    pat = eng["enumerate_pattern"].lower()
    wanted = [p for p in tree if fnmatch.fnmatch(p.rsplit("/", 1)[-1].lower(), pat)]
else:
    for f in eng.get("files", []):
        base = f["name"].rsplit("/", 1)[-1]
        for cand in (f["name"], f"{subdir}/{base}" if subdir else base, base):
            if cand in tree:
                wanted.append(cand)
                break
        else:
            sys.exit(f"{key}: {f['name']} is not in the tree listing of {eng['hf_repo']}")
if not wanted:
    sys.exit(f"{key}: nothing in the tree matches the research file list")
join_into = eng.get("join_into")
if join_into and os.path.isfile(os.path.join(dest_dir, join_into)):
    print(0)
    sys.exit(0)
remaining = 0
for p in wanted:
    dest = os.path.join(dest_dir, p.rsplit("/", 1)[-1])
    have = os.path.getsize(dest) if os.path.isfile(dest) else (os.path.getsize(dest + ".part") if os.path.isfile(dest + ".part") else 0)
    remaining += max(tree[p] - have, 0)
print(remaining)
PY
  rm -f "$treef"
  return "$rc"
}

_p3_human_gb() { python3 -c 'import sys; print(f"{int(sys.argv[1])/1e9:.1f} GB")' "$1"; }

# _p3_eta BYTES — expected time at DOWNLOAD_MBPS (a planning figure, atlas.env §3), as "H h MM min".
_p3_eta() {
  python3 -c '
import sys
b, mbps = int(sys.argv[1]), float(sys.argv[2])
s = b * 8 / (mbps * 1e6) if mbps > 0 else 0
print(f"{int(s // 3600)} h {int((s % 3600) // 60):02d} min at {mbps:g} Mbit/s")' "$1" "${DOWNLOAD_MBPS:-100}"
}

# _p3_write_manifest — $ATLAS_SRV/models/MANIFEST.json from every per-engine MANIFEST.json (Section 9.5: AEGIS backs
# up this file instead of the weights). Includes the Phase 2 resident models when their manifests exist.
_p3_write_manifest() {
  python3 - "$P3_ENGINES_JSON" "$P3_MODELS_DIR" <<'PY'
import datetime, json, os, sys
engines_path, models_dir = sys.argv[1:3]
engines = json.load(open(engines_path, encoding="utf-8"))["engines"]
out = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
       "models_dir": models_dir, "purpose": "AEGIS manifest (Section 9.5): exact model files and checksums; weights are re-downloadable",
       "engines": []}
missing = []
for e in engines:
    p = os.path.join(models_dir, e["key"], "MANIFEST.json")
    if not os.path.isfile(p):
        if e.get("arbiter_class") in {"core", "apex", "vision", "crosscheck"}:
            missing.append(e["key"])
        continue
    m = json.load(open(p, encoding="utf-8"))
    out["engines"].append({
        "key": e["key"], "repo": m.get("repo", e.get("hf_repo")), "subdir": m.get("subdir"), "quant": e.get("quant"),
        "licence": e.get("licence"), "arbiter_class": e.get("arbiter_class"), "join_into": m.get("join_into"),
        "files": [{"name": f["name"], "bytes": f.get("bytes"), "sha256": f.get("sha256")} for f in m.get("files", [])],
        "pulled_at": m.get("verified_at"),
    })
if missing:
    sys.exit(f"no per-engine MANIFEST.json for: {', '.join(missing)}")
path = os.path.join(models_dir, "MANIFEST.json")
with open(path + ".tmp", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
    fh.write("\n")
os.replace(path + ".tmp", path)
print(f"{path}: {len(out['engines'])} engines")
PY
  local rc=$?
  (( rc == 0 )) || die "could not write $P3_MODELS_DIR/MANIFEST.json (a per-engine manifest is missing: see above)"
  chown atlas:atlas "$P3_MODELS_DIR/MANIFEST.json" 2>/dev/null || true
}

# --- Step 01: pull ---------------------------------------------------------------------------------------------------
step_01() {
  [[ -d "$P3_MODELS_DIR" ]] || die "$P3_MODELS_DIR missing: the 8 TB data volume is not mounted (Phase 1 step 3)"
  ensure_dir "$P3_MODELS_DIR" atlas:atlas 755
  [[ -f "$ATLAS_ETC/proxy.env" ]] || die "$ATLAS_ETC/proxy.env missing: every download must go through the allowlist proxy (rule §7.1)"
  if [[ ! -s "$ATLAS_ETC/secrets/hf-token.env" ]]; then
    warn "no Hugging Face token in $ATLAS_ETC/secrets/hf-token.env; none of the seven repos is known to be gated (gguf-models.md, UNVERIFIED), a 401/403 will stop the pull with the licence URL"
  fi
  notify "Phase 3 step 1: pulling ${#P3_KEYS[@]} engines into $P3_MODELS_DIR (~690 GB)"

  # Plan first: remaining bytes per engine from the tree API (also proves huggingface.co is reachable through the proxy).
  local key total=0 b
  local -A remaining=()
  for key in "${P3_KEYS[@]}"; do
    b="$(_p3_remaining_bytes "$key")" || die "could not plan the pull for $key"
    remaining["$key"]="$b"
    total=$(( total + b ))
    log "plan: $key needs $(_p3_human_gb "$b") more from $(ej "$key" hf_repo)"
  done
  local free
  free="$(df -B1 --output=avail "$P3_MODELS_DIR" | tail -n1 | tr -d ' ')"
  # Meditron is joined from a copy of its parts (+~74 GB transiently); keep 5 % headroom on top.
  local need=$(( total + total / 20 + 80000000000 ))
  if (( total > 0 && free < need )); then
    die "only $(_p3_human_gb "$free") free on $P3_MODELS_DIR, need about $(_p3_human_gb "$need") ($(_p3_human_gb "$total") to download plus join and headroom)"
  fi
  log "plan: $(_p3_human_gb "$total") to download in total, expected $(_p3_eta "$total") (DOWNLOAD_MBPS=${DOWNLOAD_MBPS:-100}); $(_p3_human_gb "$free") free"

  local done_bytes=0 t0 t1
  for key in "${P3_KEYS[@]}"; do
    b="${remaining[$key]}"
    log "pull $key: $(_p3_human_gb "$b") to fetch; remaining after it $(_p3_human_gb $(( total - done_bytes - b ))), about $(_p3_eta $(( total - done_bytes )))"
    notify "Phase 3 pull: $key ($(_p3_human_gb "$b"), remaining $(_p3_eta $(( total - done_bytes ))))"
    t0=$SECONDS
    # pull_engine_files (phase2/04-memory.sh): tree-API oid/size per file, hf_download (resumable, sha256-verified,
    # complete files skipped), size check, cat-join for join_into, per-engine MANIFEST.json. Dies loudly on any mismatch.
    pull_engine_files "$key"
    t1=$SECONDS
    done_bytes=$(( done_bytes + b ))
    if (( b > 0 && t1 > t0 )); then
      log "pull $key: done in $(( (t1 - t0) / 60 )) min ($(python3 -c 'import sys; print(f"{int(sys.argv[1])*8/int(sys.argv[2])/1e6:.1f}")' "$b" "$(( t1 - t0 ))") Mbit/s effective)"
    else
      log "pull $key: complete (nothing to fetch)"
    fi
  done

  _p3_write_manifest
  # The seven env files were rendered in Phase 2 with ATLAS_MODEL_PRESENT=0; re-render now that the files exist.
  _p3_render_envs
  local absent=()
  for key in "${P3_KEYS[@]}"; do
    grep -qx "ATLAS_MODEL_PRESENT=1" "$ATLAS_ETC/engines/$key.env" 2>/dev/null || absent+=("$key")
  done
  (( ${#absent[@]} == 0 )) || die "after the pull, engine-env.py still finds no model file for: ${absent[*]} (model_file_pattern in engines.json does not match what was downloaded)"
  notify "Phase 3 step 1 done: ${#P3_KEYS[@]} engines pulled, MANIFEST.json written"
  log "step 01 done: $P3_MODELS_DIR/MANIFEST.json"
}

# --- Step 02: load tests ---------------------------------------------------------------------------------------------
step_02() {
  [[ -x /usr/local/bin/llama-server ]] || die "/usr/local/bin/llama-server missing (Phase 2 step 1)"
  [[ -f /etc/systemd/system/llama-server@.service ]] || die "llama-server@.service not installed (Phase 2 step 1)"
  mkdir -p "$P3_RESULTS"
  notify "Phase 3 step 2: load tests for ${#P3_KEYS[@]} engines (one at a time)"
  # Stops anything weight-bearing that is still resident, waits for the GTT counter to settle, records the baseline.
  _p3_loadtest prepare
  local key prev="" resfile
  for key in "${P3_KEYS[@]}"; do
    resfile="$P3_RESULTS/$key.json"
    if [[ -f "$resfile" ]] && python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("ok") else 1)' "$resfile" 2>/dev/null; then
      log "load test $key: already passed ($resfile); skipping (delete the file to re-test)"
      continue
    fi
    notify "Phase 3 load test: $key"
    log "load test $key (previous resident: ${prev:-none})"
    _p3_loadtest engine "$key" ${prev:+--previous "$prev"}
    prev="$key"
  done
  # The last engine is still resident on purpose (its unload + release is the measurement): finish it now.
  _p3_loadtest finish ${prev:+--previous "$prev"}
  # V10 per engine, the V10 summary the gate reads, and V22 (DeepSeek, R19) from the result files.
  _p3_loadtest summarize
  notify "Phase 3 step 2 done: load tests recorded (see verify.jsonl V4/V10/V22)"
  log "step 02 done"
}

# --- Step 03: two-residency ------------------------------------------------------------------------------------------
step_03() {
  notify "Phase 3 step 3: two-residency test ($P3_TEXT_KEY + $P3_VISION_KEY)"
  _p3_loadtest coresident --text "$P3_TEXT_KEY" --vision "$P3_VISION_KEY"
  log "step 03 done"
}

# --- Step 04: gate ---------------------------------------------------------------------------------------------------
step_04() {
  echo
  echo "Phase 3 load tests (Section 17 step 4 table):"
  _p3_loadtest table
  echo
  # CONVENTIONS.md §6: V4 per engine, V10, V14b, V21 required; V22 recorded only (a DeepSeek failure defers it, R19).
  if gate phase3 V4 V10 V14b V21 -- V22; then
    notify "Phase 3 gate: PASS. Next: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase4"
    return 0
  fi
  notify "Phase 3 gate: FAIL (see the table in journalctl -u atlas-day1-phase3)"
  return 1
}

# --- Run -------------------------------------------------------------------------------------------------------------
log "Phase 3 (core LLM pull and load tests) starting; engines: ${P3_KEYS[*]}; log $(_atlas_log_file)"
[[ "$ATLAS_DRY_RUN" == "1" ]] || notify "Phase 3 starting (detached; follow with: journalctl -u atlas-day1-phase3 -f)"
run_step phase3 01 step_01
run_step phase3 02 step_02
run_step phase3 03 step_03
run_step phase3 04 step_04
if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
  echo
  phase_status phase3
fi
log "Phase 3 driver finished"
