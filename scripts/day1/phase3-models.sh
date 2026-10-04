#!/usr/bin/env bash
# phase3-models.sh — Phase 3 driver: core LLM pull and load tests (ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 3,
# Section 21 V4, V10, V14b, V21, V22; R19). Long, detached under systemd, resumable at the file level.
#
#   sudo ./atlas-day1.sh phase3 [--dry-run] [--force STEP] [--status] [--foreground]   (the entry point re-execs this)
#   phase3-models.sh --run          the in-unit entry started by detached_phase (atlas-day1-phase3)
#   phase3-models.sh --foreground   accepted here too (mapped to --run): run in this terminal, not in the unit
#   phase3-models.sh                without --run/--foreground and not --dry-run: detaches itself (same unit) and
#                                   returns; with --force STEP it clears the marker and still detaches (said so)
#
# Steps (CONVENTIONS.md §1, §6; each wrapped in run_step, so a re-run skips what is complete):
#   01  pull: every engine whose arbiter_class is core/apex/vision/crosscheck (the seven GGUF engines of Section 5.1),
#       hashes and sizes read from the Hugging Face tree API at pull time (research conflict a), a research-pinned
#       sha256 that disagrees with the tree is FATAL before any byte moves (rule §7.9), the repo's commit sha resolved
#       before and after each pull (a repo that moved mid-pull is fatal), resumable per file, Meditron raw splits
#       cat-joined (conflict d), enumerated shard names for the abliterated twin and Nemotron (conflicts e, f); then the
#       aggregate manifest (Section 9.5) written INTO THE BACKUP SET at $ATLAS_SRV/data/manifests/models-MANIFEST.json
#       with a convenience copy at $ATLAS_SRV/models/MANIFEST.json (see _p3_write_manifest).
#   02  load tests, one engine at a time, by phase3/loadtest.py: load through the orchestrator's Arbiter API ONLY
#       (POST /arbiter/load|unload|register; Section 4.2 is a hard requirement and Section 17 step 2 says "load through
#       the Engine Arbiter": an orchestrator that is not serving is the missing Phase 2 step 2 prerequisite and stops
#       the phase, there is no systemctl fallback), /health, V4 proof from the unit's journal (conflict h),
#       decode/prefill tok/s at 512 and 8k prompt tokens with the Section 5.1 band as part of the V10 verdict, swap time
#       (unload previous + load this), the measured footprint registered with the Arbiter (Section 4.2 rule 1), unload,
#       GTT release polled after EVERY unload and after every unit that died on its own (rule 5: never the process exit;
#       measurement window 2 GiB / 120 s; the Arbiter's own rule-5 check is the authority; a leak stops the phase).
#       Nemotron's death at 8k is a V10 WARNING with the issue number (Section 23 S6), any other engine's a fail.
#       DeepSeek V4 Flash ladders engines.json kv_ladder DOWN (f16 -> q8_0 -> q4_0, Section 4.3 / Section 23 S2) with a
#       coherence prompt at each rung and keeps the lowest coherent rung reached (conflict b); an f16-only result records
#       V4 deferred and V22 pass with the note; any DeepSeek failure records V22 deferred and continues (R19).
#       Background generation (atlas-sentinel.timer, atlas-prune.timer, atlas-celery-gpu.service) is stopped around
#       steps 02 and 03 and restarted afterwards (Section 4.2 rule 3: one generation at a time, background work too).
#   03  two-residency: gpt-oss-120b + qwen2.5-vl-72b (vision at parallel_coresident), ~142 GB of weights plus caches on
#       the GTT counter, two generation requests through the orchestrator (/internal/v1/chat/completions), the second
#       proven to start after the first finished (V21); then, with the pair resident, the Arbiter's refusal of an
#       over-budget load and its Deep Think downgrade against the real engine set (V14b, Section 21 V14).
#   04  gate: the Section 17 table (engine, load ok, KV type, decode 512/8k, prefill, swap s, footprint, released,
#       control mode, notes; every engines.json baseline_deviation printed under it) and
#       `gate phase3 V4 V10 V14b V21 -- V22`.
#
# Contracts relied on from other writers (CONVENTIONS.md §1; each is checked and fails loudly when absent):
#   * config/engines.json — schema per its _meta block (key, hf_repo, subdir, files[{name,sha256,bytes,verify}],
#     enumerate_pattern, join_into, mmproj, quant, footprint_gb, ctx_size, parallel, parallel_coresident,
#     ctx_size_coresident, kv_class, kv_ladder, ctx_size_f16_cap, n_keep, extra_args, expected_decode_tok_s,
#     kv_proof_lines, arbiter_class, known_issue, baseline_deviation; kv_ladder_rule and baseline_deviation_rule).
#   * phase2/04-memory.sh — its header offers `ej`, `hf_tree_lfs REPO [SUBDIR]`, `hf_download_public` and
#     `pull_engine_files KEY` to this driver (the reference implementation of the manifest-at-pull-time rule; it only
#     defines functions). Sourced here. Contract gap, stated for its writer: pull_engine_files pins revision `main`
#     (hf_download's rev argument is not threaded through) and downgrades a research-snippet sha256 mismatch to a
#     warning; this driver therefore checks the snippet hashes against the tree itself BEFORE the pull (fatal), records
#     the repo commit sha before and after every pull in the aggregate manifest (revision_sha, revision_sha_after) and,
#     when a repo moved during a pull, re-lists the tree and dies only if a WANTED file's oid or size differs from what
#     was pulled (every byte was sha256-verified against the plan; an unrelated commit is a warning, not a 15-hour redo).
#   * SECRETS ON ARGV (CONVENTIONS.md §2 "never echoed"; cross-writer gaps, stated here): lib/common.sh hf_download puts
#     `-H "Authorization: Bearer $HF_TOKEN"` on curl's argv (/proc/<pid>/cmdline is world-readable for the hours a
#     multi-GB file takes) and lib/common.sh notify does the same with NTFY_TOKEN for up to 10 s per push. The pull path
#     of step 01 never reaches the first one: pull_engine_files calls hf_download_public (04-memory.sh), which runs
#     hf_download with HF_TOKEN="" and ATLAS_ETC pointed at an empty directory, so no token exists in that call (none of
#     the seven repos is gated). Step 01 checks that this is still so (declare -f pull_engine_files names
#     hf_download_public) and dies otherwise rather than leak the token for hours; this driver's own _p3_repo_sha sends
#     the header on curl's stdin (-H @-). The notify gap stays until common.sh passes the header through stdin or -K.
#   * phase2/engine-env.py — renders $ATLAS_ETC/engines/<key>.env (ATLAS_KV_TYPE, ATLAS_CTX_SIZE, ATLAS_PARALLEL,
#     ATLAS_MODEL_PRESENT, ATLAS_KV_PROOF_LINES, LLAMA_ARG_PORT, ...) and owns $ATLAS_ETC/engines/overrides.json
#     (--set-override KEY kv_type|ctx_size|parallel|coresident VALUE, --clear-override KEY FIELD, --key KEY).
#   * systemd/llama-server@.service (phase2/01-llama.sh): `systemctl start` returns only when /health is 200; the
#     journal of llama-server@<key> carries the "llama_kv_cache: size = ... K (q8_0): ... V (q8_0): ..." proof line.
#   * the orchestrator (phase2/02-orchestrator.sh; $ATLAS_ETC/orchestrator.env ORCH_URL, optional
#     ORCH_ADMIN_TOKEN_FILE): the Arbiter API and chat contract are stated in phase3/loadtest.py's header against
#     orchestrator/src/atlas/api.py. ORCH_URL must be loopback (CONVENTIONS.md §8); it is checked here and there.
#   * systemd/atlas-aegis.service: `atlas-aegis manifests` copies <models|engines>/<key>/MANIFEST.json into
#     $ATLAS_SRV/data/manifests, and phase2/07-restic.sh includes $ATLAS_SRV/data (Appendix C) and excludes
#     $ATLAS_SRV/models: the per-engine manifests are what AEGIS backs up; the aggregate written here lands in
#     data/manifests so it is backed up too.
# Contract this file defines for others: $ATLAS_STATE/phase3/results/<key>.json (one per engine, schema in loadtest.py;
# the driver skips an engine on a re-run only when ok AND released are true), $ATLAS_STATE/phase3/results/
# pending-overrides.json (loadtest.py's journal of temporary overrides, restored on the next invocation),
# $ATLAS_STATE/phase3/revisions/<key> and <key>.after (the HF commit sha before and after the pull) and the aggregate
# manifest $ATLAS_SRV/data/manifests/models-MANIFEST.json (key, repo, revision_sha, revision_sha_after, source_endpoint,
# quant, files[{name,bytes,sha256}], pulled_at).

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"

export ATLAS_PHASE=phase3
require_root
# --foreground is atlas-day1.sh's spelling of --run (it strips it before exec); accept it directly too, and remember a
# --force so the detach below can say what it is doing (atlas-day1.sh --force execs this driver without --run).
P3_ARGS=()
P3_FORCED=0
for a in "$@"; do
  case "$a" in
    --foreground) a=--run ;;
    --force) P3_FORCED=1 ;;
  esac
  P3_ARGS+=("$a")
done
parse_common_args "${P3_ARGS[@]}"

P3_STATE="$ATLAS_STATE/phase3"
P3_RESULTS="$P3_STATE/results"
P3_REVISIONS="$P3_STATE/revisions"
P3_LOADTEST="$ATLAS_DAY1_DIR/phase3/loadtest.py"
P3_ENGINE_ENV_PY="$ATLAS_DAY1_DIR/phase2/engine-env.py"
P3_MEMORY_STEP="$ATLAS_DAY1_DIR/phase2/04-memory.sh"
P3_ENGINES_JSON="$ATLAS_DAY1_DIR/config/engines.json"
P3_MODELS_DIR="$ATLAS_SRV/models"
P3_MANIFEST_DIR="$ATLAS_SRV/data/manifests"          # Appendix C: $ATLAS_SRV/data is in restic's include set
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
  if (( P3_FORCED )); then
    # atlas-day1.sh strips --foreground and execs this driver with only `--force STEP` (its run_detached_or_foreground),
    # so the spelling that really stays in the terminal is this driver's own (cross-file note for atlas-day1.sh).
    log "marker cleared; detaching the phase (follow with: journalctl -u atlas-day1-phase3 -f; to stay in this terminal instead run: $(readlink -f "$0") --foreground --force STEP)"
  fi
  detached_phase phase3 "$(readlink -f "$0")"
  exit 0
fi

for f in "$P3_LOADTEST" "$P3_ENGINE_ENV_PY" "$P3_MEMORY_STEP" "$P3_ENGINES_JSON"; do
  [[ -f "$f" ]] || die "$f is missing (CONVENTIONS.md §1 layout; written by its own author)"
done
# shellcheck source=phase2/04-memory.sh
source "$P3_MEMORY_STEP"
for fn in ej hf_tree_lfs hf_download_public pull_engine_files; do
  declare -F "$fn" >/dev/null || die "phase2/04-memory.sh does not define $fn (its header promises it to the Phase 3 driver)"
done

# Background generation stopped around steps 02/03 (Section 4.2 rule 3; see _p3_quiesce_background); restarted on every
# exit path, including die and a signal, so a failed step never leaves the Sentinel or the prune timer off.
P3_QUIESCED=()
_p3_resume_background() {
  local u
  for u in "${P3_QUIESCED[@]}"; do
    if systemctl start "$u" 2>/dev/null; then log "restarted $u"; else warn "could not restart $u; start it by hand: systemctl start $u"; fi
  done
  P3_QUIESCED=()
}
_p3_install_signal_traps() {
  trap '_p3_resume_background' EXIT
  trap 'exit 143' TERM
  trap 'exit 130' INT
}
_p3_install_signal_traps

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
# _p3_orch_setting KEY — one value from $ATLAS_ETC/orchestrator.env (KEY=value, quotes optional); empty when absent.
_p3_orch_setting() {
  local key="$1"
  [[ -r "$ATLAS_ETC/orchestrator.env" ]] || return 0
  sed -nE "s/^${key}=['\"]?([^'\"]+)['\"]?$/\1/p" "$ATLAS_ETC/orchestrator.env" | head -n1
}

# _p3_loadtest SUBCMD ARGS... — run phase3/loadtest.py with every path it needs; its stdout carries only
# "RECORD<TAB>ID<TAB>RESULT<TAB>MSG" lines (recorded here with record_v), its stderr the log. A non-zero exit is an
# infrastructure error (not a failed measurement, which loadtest.py records) and stops the phase.
_p3_loadtest() {
  local sub="$1"; shift
  local orch_url out rc=0 token_file
  orch_url="$(_p3_orch_setting ORCH_URL)"
  [[ -n "$orch_url" ]] || orch_url="http://127.0.0.1:${ORCH_PORT:-8800}"
  # CONVENTIONS.md §8: the orchestrator binds 127.0.0.1. Engine control and generation never leave the host, whatever a
  # mis-edited orchestrator.env says (rule §7.1 belt; loadtest.py checks again).
  [[ "$orch_url" =~ ^http://(127\.0\.0\.1|localhost|\[::1\]):[0-9]+/?$ ]] \
    || die "ORCH_URL in $ATLAS_ETC/orchestrator.env must be loopback (CONVENTIONS.md §8), got '$orch_url'"
  local extra=()
  token_file="$(_p3_orch_setting ORCH_ADMIN_TOKEN_FILE)"
  if [[ -n "$token_file" ]]; then
    [[ -r "$token_file" ]] || die "ORCH_ADMIN_TOKEN_FILE=$token_file (orchestrator.env) is not readable; /arbiter/* needs it"
    extra+=(--admin-token-file "$token_file")
  fi
  mkdir -p "$P3_RESULTS"
  out="$(mktemp)"
  local logf pid
  logf="$(_atlas_log_file)"
  # Local engines and the orchestrator are loopback: the allowlist proxy must not see them (loadtest.py also bypasses it).
  # ATLAS_DRM_ROOT is a test-only hook for the GTT counter: this driver only ever runs on the node, so it is unset
  # unconditionally (loadtest.py refuses it on the node as well, comparing canonical paths).
  # loadtest.py's stderr (every measurement, the step 4 table, the baseline deviations) is tee'd into the phase log,
  # which detached_phase advertises with `tail -f`; the journal keeps it too. The child runs in the background so a
  # TERM/INT to this shell is forwarded to it (loadtest.py turns SIGTERM into an exception and restores its temporary
  # overrides); systemd's stop sends TERM to the whole cgroup anyway.
  env -u ATLAS_DRM_ROOT python3 "$P3_LOADTEST" \
    --engines "$P3_ENGINES_JSON" --env-dir "$ATLAS_ETC/engines" --results-dir "$P3_RESULTS" \
    --orch-url "$orch_url" --engine-env "$P3_ENGINE_ENV_PY" --models-dir "$P3_MODELS_DIR" \
    --slots-dir "$ATLAS_SRV/data/slots" --port-base "${LLAMA_PORT_BASE:-8100}" \
    --overrides "$ATLAS_ETC/engines/overrides.json" "${extra[@]}" \
    "$sub" "$@" >"$out" 2> >(tee -a "$logf" >&2) &
  pid=$!
  # shellcheck disable=SC2064  # $pid is meant to expand now: the trap must name this child
  trap "kill -TERM $pid 2>/dev/null" TERM INT
  while :; do
    if wait "$pid"; then rc=0; else rc=$?; fi
    # wait returns >128 when a trapped signal arrived while the child still runs: wait for it again.
    if (( rc > 128 )) && kill -0 "$pid" 2>/dev/null; then continue; fi
    break
  done
  _p3_install_signal_traps
  local tag id result msg
  while IFS=$'\t' read -r tag id result msg; do
    [[ "$tag" == "RECORD" ]] || continue
    record_v "$id" "$result" "$msg"
  done <"$out"
  rm -f "$out"
  (( rc != 143 && rc != 130 )) || die "loadtest.py $sub was terminated (exit $rc); its temporary overrides were restored; re-run to resume"
  (( rc == 0 )) || die "loadtest.py $sub failed with exit $rc (infrastructure error, see the log above); re-run to resume"
}

# _p3_quiesce_background — Section 4.2 rule 3 (C26): exactly one generation at a time, background work included. The
# Sentinel and prune timers enqueue Celery gpu-queue tasks (CONVENTIONS.md §8) and atlas-celery-gpu runs them; a task
# landing mid-measurement would generate beside the timing request (corrupting tok/s) or ask the Arbiter to load or
# evict the engine under test. Units that are active are stopped for the step and restarted by _p3_resume_background
# (also on the EXIT trap). atlas-celery-gpu has TimeoutStopSec=900: a running task finishes first, by design.
_p3_quiesce_background() {
  local u
  for u in atlas-sentinel.timer atlas-prune.timer atlas-celery-gpu.service; do
    systemctl is-active --quiet "$u" || continue
    systemctl stop "$u" || die "could not stop $u; it must not generate during the load tests (Section 4.2 rule 3)"
    P3_QUIESCED+=("$u")
    log "quiesced $u for this step (restarted when the step ends)"
  done
}

# _p3_control_mode — say, before any engine is touched, how the load tests will be controlled (rule §7.10).
_p3_control_mode() {
  local orch_url
  orch_url="$(_p3_orch_setting ORCH_URL)"
  [[ -n "$orch_url" ]] || orch_url="http://127.0.0.1:${ORCH_PORT:-8800}"
  local state
  state="$(systemctl show -p ActiveState --value atlas-orchestrator 2>/dev/null || echo unknown)"
  case "$state" in
    active|activating|reloading)
      log "control: atlas-orchestrator is $state; every load goes through its Engine Arbiter at $orch_url (POST /arbiter/load|unload; loadtest.py waits for /health 200 and stops the phase if the Arbiter API is missing)" ;;
    *)
      die "control: atlas-orchestrator is $state. Every load and unload passes through the Engine Arbiter (Section 4.2, Section 17 Phase 3 step 2; CONVENTIONS.md §7.5: Phase 2 step 2 is the prerequisite); there is no systemctl fallback. Start it with: systemctl start atlas-orchestrator, then re-run" ;;
  esac
}

# _p3_render_envs — re-render every engine env file so ATLAS_MODEL_PRESENT reflects the files on disk. engine-env.py
# sets root:atlas 640 itself; the belt below fails hard like phase2/04-memory.sh _mem_render_envs (rule §7.4).
_p3_render_envs() {
  python3 "$P3_ENGINE_ENV_PY" \
    --engines "$P3_ENGINES_JSON" --out "$ATLAS_ETC/engines" --models-dir "$P3_MODELS_DIR" \
    --slots-dir "$ATLAS_SRV/data/slots" --port-base "${LLAMA_PORT_BASE:-8100}" \
    --overrides "$ATLAS_ETC/engines/overrides.json" \
    || die "engine-env.py failed to render $ATLAS_ETC/engines/*.env"
  chown root:atlas "$ATLAS_ETC/engines"/*.env || die "could not set root:atlas on $ATLAS_ETC/engines/*.env"
  chmod 640 "$ATLAS_ETC/engines"/*.env || die "could not set mode 640 on $ATLAS_ETC/engines/*.env"
}

# _p3_repo_sha REPO — the commit sha huggingface.co serves for the repo's default branch (GET /api/models/<repo>,
# field "sha"), through the allowlist proxy, the token in a 600 header file as hf_tree_lfs does. Prints the sha; dies
# when it cannot be resolved (rule §7.9: the manifest records what was actually pulled).
_p3_repo_sha() {
  local repo="$1"
  proxy_env
  local HF_TOKEN="${HF_TOKEN:-}"
  if [[ -z "$HF_TOKEN" && -f "$ATLAS_ETC/secrets/hf-token.env" ]]; then
    # shellcheck disable=SC1091  # secret file, HF_TOKEN=... (CONVENTIONS.md §2)
    source "$ATLAS_ETC/secrets/hf-token.env"
  fi
  local url="${HF_ENDPOINT:-https://huggingface.co}/api/models/$repo"
  local body code
  body="$(mktemp)"
  # The bearer header travels on curl's stdin (-H @-, curl >= 7.55; the hf_tree_lfs pattern): never on argv, never in a
  # temp file a die between mktemp and rm could leave behind (CONVENTIONS.md §2).
  if [[ -n "$HF_TOKEN" ]]; then
    code="$(printf 'Authorization: Bearer %s\n' "$HF_TOKEN" \
            | curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" -H @- "$url" || true)"
  else
    code="$(curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" "$url" || true)"
  fi
  [[ "$code" == 200 ]] || { rm -f "$body"; die "_p3_repo_sha: HTTP ${code:-none} for $url (gated? proxy? allowlist?)"; }
  local sha
  sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("sha") or "")' "$body" || true)"
  rm -f "$body"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "_p3_repo_sha: $url returned no 40-hex commit sha (got '${sha:0:40}')"
  printf '%s\n' "$sha"
}

# _p3_remaining_bytes KEY — bytes still to download for one engine: tree-API sizes of the wanted files minus what is
# already on disk (complete file, .part, or the joined file). Prints one integer. Dies when the tree cannot be read and
# when a research-pinned sha256 (verify=research-snippet) disagrees with the tree API (rule §7.9; before any byte moves).
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
tree, oids = {}, {}
for line in open(treef, encoding="utf-8").read().splitlines():
    p, size, oid = line.split("\t")
    tree[p] = int(size) if size.isdigit() else 0
    oids[p] = oid
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
        pinned = (f.get("sha256") or "").lower()
        if pinned and oids.get(cand, "none") != "none" and oids[cand].lower() != pinned:
            # A pinned hash that the tree contradicts means the file changed (or the research hash was wrong): stop
            # before the pull, name both, and let a human decide (engines.json files_rule; CONVENTIONS §7.9).
            sys.exit(f"{key}: sha256 of {cand} pinned in engines.json ({pinned}) differs from the Hugging Face tree API "
                     f"({oids[cand]}); verify on https://huggingface.co/{eng['hf_repo']}/blob/main/{cand} and correct "
                     "engines.json before pulling")
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

# _p3_write_manifest — the aggregate of every per-engine MANIFEST.json plus the resolved commit sha per engine.
# Section 9.5: "a manifest of exact model versions and checksums is backed up instead" of the weights. The unit
# atlas-aegis copies the per-engine files into $ATLAS_SRV/data/manifests (the include set of Appendix C) and restic
# excludes $ATLAS_SRV/models, so this aggregate is written INTO data/manifests to be backed up as well; the copy under
# models/ is a convenience index for humans, not what AEGIS relies on. Includes the Phase 2 resident models when their
# manifests exist.
_p3_write_manifest() {
  local rc=0
  ensure_dir "$P3_MANIFEST_DIR" atlas:atlas 755
  python3 - "$P3_ENGINES_JSON" "$P3_MODELS_DIR" "$P3_REVISIONS" "$P3_MANIFEST_DIR/models-MANIFEST.json" \
    "${HF_ENDPOINT:-https://huggingface.co}" <<'PY' || rc=$?
import datetime, json, os, sys
engines_path, models_dir, rev_dir, out_path, endpoint = sys.argv[1:6]
engines = json.load(open(engines_path, encoding="utf-8"))["engines"]
out = {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
       "models_dir": models_dir,
       "source_endpoint": endpoint,  # Section 12.5 / 16.3 item 8: huggingface.co only (step 01 refuses anything else)
       "purpose": "AEGIS manifest (Section 9.5): exact model files, checksums and repo commit shas; weights are re-downloadable",
       "engines": []}
missing = []
for e in engines:
    p = os.path.join(models_dir, e["key"], "MANIFEST.json")
    if not os.path.isfile(p):
        if e.get("arbiter_class") in {"core", "apex", "vision", "crosscheck"}:
            missing.append(e["key"])
        continue
    m = json.load(open(p, encoding="utf-8"))
    rev_file = os.path.join(rev_dir, e["key"])
    revision_sha = open(rev_file, encoding="utf-8").read().strip() if os.path.isfile(rev_file) else None
    after_file = rev_file + ".after"
    revision_sha_after = open(after_file, encoding="utf-8").read().strip() if os.path.isfile(after_file) else None
    out["engines"].append({
        "key": e["key"], "repo": m.get("repo", e.get("hf_repo")), "subdir": m.get("subdir"), "quant": e.get("quant"),
        "revision": m.get("revision"), "revision_sha": revision_sha, "revision_sha_after": revision_sha_after,
        "licence": e.get("licence"), "arbiter_class": e.get("arbiter_class"), "join_into": m.get("join_into"),
        "files": [{"name": f["name"], "bytes": f.get("bytes"), "sha256": f.get("sha256")} for f in m.get("files", [])],
        "pulled_at": m.get("verified_at"),
    })
if missing:
    sys.exit(f"no per-engine MANIFEST.json for: {', '.join(missing)}")
with open(out_path + ".tmp", "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2)
    fh.write("\n")
os.replace(out_path + ".tmp", out_path)
print(f"{out_path}: {len(out['engines'])} engines")
PY
  (( rc == 0 )) || die "could not write $P3_MANIFEST_DIR/models-MANIFEST.json (a per-engine manifest is missing: see above)"
  chown atlas:atlas "$P3_MANIFEST_DIR/models-MANIFEST.json" || die "could not chown $P3_MANIFEST_DIR/models-MANIFEST.json"
  # Convenience copy beside the weights (not backed up: restic excludes $ATLAS_SRV/models).
  install -o atlas -g atlas -m 644 "$P3_MANIFEST_DIR/models-MANIFEST.json" "$P3_MODELS_DIR/MANIFEST.json" \
    || die "could not copy the manifest to $P3_MODELS_DIR/MANIFEST.json"
}

# _p3_tree_matches_manifest KEY — after a pull during which the repo's default branch moved: re-list the tree and
# compare the oid (sha256) and size of every file the per-engine MANIFEST.json records (what was actually pulled and
# verified) with what the tree serves now. Prints nothing; returns 0 when every wanted file is unchanged, dies with the
# differing file when one is not (the files may then mix two revisions). An unrelated commit (a README edit, another
# quant folder) is therefore not a reason to redo a 15-hour pull.
_p3_tree_matches_manifest() {
  local key="$1" repo subdir treef rc=0
  repo="$(ej "$key" hf_repo)"; subdir="$(ej "$key" subdir)"
  treef="$(mktemp)"
  hf_tree_lfs "$repo" "$subdir" >"$treef"
  python3 - "$P3_MODELS_DIR/$key/MANIFEST.json" "$treef" "$key" <<'PY' || rc=$?
import json, sys
manifest, treef, key = sys.argv[1:4]
tree = {}
for line in open(treef, encoding="utf-8").read().splitlines():
    p, size, oid = line.split("\t")
    tree[p.rsplit("/", 1)[-1]] = (oid.lower(), int(size) if size.isdigit() else None)
bad = []
for f in json.load(open(manifest, encoding="utf-8"))["files"]:
    now = tree.get(f["name"])
    if now is None:
        bad.append(f"{f['name']}: no longer in the tree")
    elif now[0] != "none" and now[0] != str(f.get("sha256", "")).lower():
        bad.append(f"{f['name']}: sha256 {f.get('sha256')} pulled, tree now {now[0]}")
    elif now[1] is not None and f.get("bytes") is not None and now[1] != int(f["bytes"]):
        bad.append(f"{f['name']}: {f['bytes']} bytes pulled, tree now {now[1]}")
if bad:
    sys.exit(f"{key}: the repo moved during the pull AND the wanted files changed: " + "; ".join(bad))
PY
  rm -f "$treef"
  return "$rc"
}

# --- Step 01: pull ---------------------------------------------------------------------------------------------------
step_01() {
  [[ -d "$P3_MODELS_DIR" ]] || die "$P3_MODELS_DIR missing: the 8 TB data volume is not mounted (Phase 1 step 3)"
  ensure_dir "$P3_MODELS_DIR" atlas:atlas 755
  ensure_dir "$P3_REVISIONS" root:root 755
  [[ -f "$ATLAS_ETC/proxy.env" ]] || die "$ATLAS_ETC/proxy.env missing: every download must go through the allowlist proxy (rule §7.1)"
  # Section 12.5 / 16.3 item 8: model pulls come from Hugging Face only. HF_ENDPOINT (atlas.env, non-secret) is honoured
  # by hf_tree_lfs, hf_download and _p3_repo_sha; the squid allowlist fails other hosts closed, but any allowlisted host
  # would otherwise be accepted and the manifest would misstate the source.
  case "${HF_ENDPOINT:-https://huggingface.co}" in
    https://huggingface.co|https://huggingface.co/) ;;
    *) die "HF_ENDPOINT=${HF_ENDPOINT:-} (atlas.env): model pulls come from https://huggingface.co only (Section 12.5, 16.3 item 8); unset it and re-run" ;;
  esac
  # CONVENTIONS.md §2 belt (header, SECRETS ON ARGV): the pull must go through hf_download_public, which never sources
  # the token; a pull_engine_files that calls hf_download directly would put HF_TOKEN on curl's argv for hours.
  if ! declare -f pull_engine_files | grep -q 'hf_download_public '; then
    die "phase2/04-memory.sh pull_engine_files no longer downloads through hf_download_public: with $ATLAS_ETC/secrets/hf-token.env present, lib/common.sh hf_download would expose HF_TOKEN on curl's command line (/proc/<pid>/cmdline) for the whole pull; fix that first (header: SECRETS ON ARGV)"
  fi
  if [[ ! -s "$ATLAS_ETC/secrets/hf-token.env" ]]; then
    warn "no Hugging Face token in $ATLAS_ETC/secrets/hf-token.env; none of the seven repos is known to be gated (gguf-models.md, UNVERIFIED), a 401/403 will stop the pull with the licence URL"
  fi
  notify "Phase 3 step 1: pulling ${#P3_KEYS[@]} engines into $P3_MODELS_DIR (~690 GB)"

  # Plan first: remaining bytes per engine from the tree API (also proves huggingface.co is reachable through the proxy),
  # pinned hashes checked against the tree, and the repo commit sha the plan is made against.
  local key total=0 b sha prev_sha
  local -A remaining=() planned_sha=()
  for key in "${P3_KEYS[@]}"; do
    b="$(_p3_remaining_bytes "$key")" || die "could not plan the pull for $key"
    remaining["$key"]="$b"
    total=$(( total + b ))
    sha="$(_p3_repo_sha "$(ej "$key" hf_repo)")" || die "could not resolve the commit sha of $(ej "$key" hf_repo)"
    planned_sha["$key"]="$sha"
    if [[ -s "$P3_REVISIONS/$key" ]]; then
      prev_sha="$(cat "$P3_REVISIONS/$key")"
      if [[ "$prev_sha" != "$sha" && "$b" -gt 0 ]]; then
        die "$key: $(ej "$key" hf_repo) moved from commit $prev_sha (an earlier run) to $sha with $(_p3_human_gb "$b") still to fetch; partial files may not match the new tree. Verify the repo, remove $P3_MODELS_DIR/$key/*.part and $P3_REVISIONS/$key, then re-run"
      fi
    fi
    log "plan: $key needs $(_p3_human_gb "$b") more from $(ej "$key" hf_repo) @ ${sha:0:12}"
  done
  local free
  free="$(df -B1 --output=avail "$P3_MODELS_DIR" | tail -n1 | tr -d ' ')"
  # Meditron is joined from a copy of its parts (+~74 GB transiently); keep 5 % headroom on top.
  local need=$(( total + total / 20 + 80000000000 ))
  if (( total > 0 && free < need )); then
    die "only $(_p3_human_gb "$free") free on $P3_MODELS_DIR, need about $(_p3_human_gb "$need") ($(_p3_human_gb "$total") to download plus join and headroom)"
  fi
  log "plan: $(_p3_human_gb "$total") to download in total, expected $(_p3_eta "$total") (DOWNLOAD_MBPS=${DOWNLOAD_MBPS:-100}); $(_p3_human_gb "$free") free"

  local done_bytes=0 t0 t1 after_sha
  for key in "${P3_KEYS[@]}"; do
    b="${remaining[$key]}"
    printf '%s\n' "${planned_sha[$key]}" >"$P3_REVISIONS/$key"
    log "pull $key: $(_p3_human_gb "$b") to fetch; remaining after it $(_p3_human_gb $(( total - done_bytes - b ))), about $(_p3_eta $(( total - done_bytes )))"
    notify "Phase 3 pull: $key ($(_p3_human_gb "$b"), remaining $(_p3_eta $(( total - done_bytes ))))"
    t0=$SECONDS
    # pull_engine_files (phase2/04-memory.sh): tree-API oid/size per file, hf_download (resumable, sha256-verified,
    # complete files skipped), size check, cat-join for join_into, per-engine MANIFEST.json. Dies loudly on any mismatch.
    pull_engine_files "$key"
    t1=$SECONDS
    # The tree was read at `main`: a repo that moved during the pull MAY have served files from two commits. Every byte
    # was sha256-verified against the plan, so what matters is whether the WANTED files changed: compare them with the
    # tree as it is now and die only then; an unrelated commit is recorded (revision_sha_after) and warned about.
    after_sha="$(_p3_repo_sha "$(ej "$key" hf_repo)")" || die "could not re-resolve the commit sha of $(ej "$key" hf_repo)"
    printf '%s\n' "$after_sha" >"$P3_REVISIONS/$key.after"
    if [[ "$after_sha" != "${planned_sha[$key]}" ]]; then
      _p3_tree_matches_manifest "$key" \
        || die "$key: $(ej "$key" hf_repo) moved from commit ${planned_sha[$key]} to $after_sha DURING the pull and the wanted files differ (above). Remove $P3_MODELS_DIR/$key/MANIFEST.json, $P3_REVISIONS/$key and $P3_REVISIONS/$key.after, then re-run (hf_download skips files whose sha256 still matches the tree)"
      warn "$key: $(ej "$key" hf_repo) moved from commit ${planned_sha[$key]} to $after_sha during the pull; every wanted file is unchanged (oid and size re-checked), both shas recorded in the manifest"
    fi
    done_bytes=$(( done_bytes + b ))
    if (( b > 0 && t1 > t0 )); then
      log "pull $key: done in $(( (t1 - t0) / 60 )) min ($(python3 -c 'import sys; print(f"{int(sys.argv[1])*8/int(sys.argv[2])/1e6:.1f}")' "$b" "$(( t1 - t0 ))") Mbit/s effective) at commit ${after_sha:0:12}"
    else
      log "pull $key: complete (nothing to fetch) at commit ${after_sha:0:12}"
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
  notify "Phase 3 step 1 done: ${#P3_KEYS[@]} engines pulled, manifest written to $P3_MANIFEST_DIR/models-MANIFEST.json"
  log "step 01 done: $P3_MANIFEST_DIR/models-MANIFEST.json (copy: $P3_MODELS_DIR/MANIFEST.json)"
}

# --- Step 02: load tests ---------------------------------------------------------------------------------------------
step_02() {
  [[ -x /usr/local/bin/llama-server ]] || die "/usr/local/bin/llama-server missing (Phase 2 step 1)"
  [[ -f /etc/systemd/system/llama-server@.service ]] || die "llama-server@.service not installed (Phase 2 step 1)"
  mkdir -p "$P3_RESULTS"
  _p3_control_mode
  # Every load below is charged to the Arbiter's budget; a chat through Open WebUI meanwhile would load a second engine
  # beside the one under measurement and break the release checks (Section 4.2 rules 3, 5). The Principal is told; the
  # timers and the gpu worker, which the notice cannot bind, are stopped for the step.
  log "do not use the assistant (Open WebUI, Deep Think, Celery jobs) while Phase 3 runs: the load tests own the engine budget"
  _p3_quiesce_background
  notify "Phase 3 step 2: load tests for ${#P3_KEYS[@]} engines (one at a time). Do not use the assistant until Phase 3 finishes."
  # Unloads anything weight-bearing that is still resident (through the Arbiter), credits a release left pending by an
  # interrupted run, restores journalled temporary overrides, waits for the GTT counter, records the baseline.
  _p3_loadtest prepare
  local key prev="" resfile
  for key in "${P3_KEYS[@]}"; do
    resfile="$P3_RESULTS/$key.json"
    # File-level resumability (CONVENTIONS.md §7.3): skip only an engine whose release was measured too; one saved
    # while still resident (released null) is re-tested rather than left with a permanent 'released ?' V10 fail.
    if [[ -f "$resfile" ]] && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get("ok") and d.get("released") is True else 1)' "$resfile" 2>/dev/null; then
      log "load test $key: already passed and released ($resfile); skipping (delete the file to re-test)"
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
  _p3_resume_background
  notify "Phase 3 step 2 done: load tests recorded (see verify.jsonl V4/V10/V22)"
  log "step 02 done"
}

# --- Step 03: two-residency ------------------------------------------------------------------------------------------
step_03() {
  _p3_control_mode
  log "do not use the assistant while the two-residency test runs (it owns the engine budget and the generation lock)"
  _p3_quiesce_background
  notify "Phase 3 step 3: two-residency test ($P3_TEXT_KEY + $P3_VISION_KEY). Do not use the assistant until it finishes."
  _p3_loadtest coresident --text "$P3_TEXT_KEY" --vision "$P3_VISION_KEY"
  _p3_resume_background
  log "step 03 done"
}

# --- Step 04: gate ---------------------------------------------------------------------------------------------------
step_04() {
  log "Phase 3 load tests (Section 17 step 4 table; loadtest.py prints it to stderr, tee'd into $(_atlas_log_file)):"
  _p3_loadtest table
  # CONVENTIONS.md §6: V4 per engine, V10, V14b, V21 required; V22 recorded only (a DeepSeek failure defers it, R19).
  # The gate table is printed to stdout (the journal) and into the phase log as well.
  local rc=0
  gate phase3 V4 V10 V14b V21 -- V22 > >(tee -a "$(_atlas_log_file)") || rc=$?
  if (( rc == 0 )); then
    notify "Phase 3 gate: PASS. Next: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase4"
    return 0
  fi
  notify "Phase 3 gate: FAIL (see the table in journalctl -u atlas-day1-phase3 or $(_atlas_log_file))"
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
