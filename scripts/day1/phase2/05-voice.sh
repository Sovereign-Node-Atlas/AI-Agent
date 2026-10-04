#!/usr/bin/env bash
# phase2/05-voice.sh — Section 17 Phase 2 step 5: Kokoro, Chatterbox, Whisper Large-v3-Turbo, PyAnnote 3.1 (Sections
# 14.1, 14.3, 21 V6/V7). Sourced by phase2-services.sh through run_phase_steps; defines step_05 only.
#
# Order inside the step (each part idempotent; a re-run after a failure resumes cheaply):
#   1. Kokoro-FastAPI (CPU image), speaches (faster-whisper large-v3-turbo, CPU int8) and docling-serve from
#      docker/core/compose.voice.yml merged into the core compose project; Kokoro voice list checked against the
#      Section 14.3 shortlist; one timed sentence; the turbo model pulled once, then speaches restarted with
#      PRELOAD_MODELS + HF_HUB_OFFLINE=1; a Kokoro->Whisper round trip proves STT.
#      Docling ownership (resolved, fix round 3): Section 17 places the "Docling ingestion service" in step 4, and
#      phase2/04-memory.sh now starts the docling container from compose.voice.yml (its header gives the command), so
#      Open WebUI has its document service from step 4 on; this step's `up -d docling` is a no-op for it and only
#      re-proves /docs so a `--force 05` re-run still checks the whole overlay. Still open: CONVENTIONS §1's layout row
#      lists docling-serve inside docker/core/compose.yml; the overlay file needs its own row (compose.voice.yml header).
#   2. Chatterbox 0.1.7 into its own venv $ATLAS_OPT/venv-voice (CPU torch 2.6.0 from download.pytorch.org/whl/cpu),
#      one sentence rendered by the atlas account, then re-rendered with HF_HUB_OFFLINE=1 (proof that the weights are
#      cached and nothing needs the network afterwards); the seconds, the peak RSS and the GTT delta are recorded.
#   3. PyAnnote 4.0.7 into $ATLAS_OPT/venv-pyannote (see WHY TWO VENVS below), torch 2.9.1 + torchcodec 0.8.1 pinned
#      as a pair (torchcodec wheels declare no torch dependency; see the PYANNOTE_TORCHCODEC_PIN comment for why 0.7.0,
#      the earlier pin, cannot load on resolute at all).
#   4. The V7 listening test is RENDERED here, once, as atlas (phase2/voice_render.py render -> $LISTENING_TEST_DIR and
#      its summary $LISTENING_TEST_DIR/v7-listening-test.json); verify/v07-voice-listen.sh only reads that summary, so
#      V7 stays under the 10-minute verify contract at the gate.
#   5. POST /arbiter/remeasure on the step-2 orchestrator (loopback admin path) so the Section 4.1 resident set includes
#      the voice stack; V6 (verify/v06-pyannote.sh, runs the diarisation as atlas), V7 (the fast reader);
#      $ATLAS_ETC/voice.env written.
#
# WHY TWO VENVS (the task asked for PyAnnote "into that venv"): chatterbox-tts 0.1.7 pins torch==2.6.0 for Python
# < 3.14 (voice-stt.md §3.1 VERIFIED pyproject) while pyannote.audio 4.0.7 requires torch>=2.8.0 (§5.1 VERIFIED).
# One venv cannot satisfy both; pip would fail at resolution. So: venv-voice (Chatterbox) and venv-pyannote.
# Both use a uv-managed CPython 3.11: the host python3 is 3.14 (§1), Chatterbox switches to an untested
# torch>=2.9 pin on 3.14 and its README says "developed/tested on 3.11".
#
# WHO RUNS WHAT (fix round 2): both venvs are ROOT-OWNED, u=rwX,go=rX, asserted after every install and before root
# ever executes an interpreter from them (Section 16.3 item 6: the account that runs model-driven code never owns the
# code it runs; the same boundary step 4 puts on /opt/atlas/venv). Everything that LOADS a model (the import checks, the
# Chatterbox renders, the V7 render, the V6 diarisation) runs as the atlas service account (svc_user_run) against the
# atlas-owned HF cache $VOICE_HF_HOME, which is the only state the voice stack writes at run time; nothing under
# $VOICE_HF_HOME may belong to another user at the end of the step (asserted), and nothing under the venvs may belong to
# anyone but root (asserted).
# ENGINE ARBITER (Section 4.2, hard requirement: "every load ... of any weight-bearing process ... Chatterbox when
# invoked" passes through it; fix round 3). This step loads Chatterbox three times (the online measure, the offline
# re-render, the V7 clone batch inside voice_render.py), and the step-2 orchestrator with its Arbiter is already running.
# What is done here, every run:
#   * before EACH of those loads, GET /arbiter/status: the step STOPS (die) when the ledger shows any resident or
#     generating engine (rule 3) or when the orchestrator does not answer (step 2 is a prerequisite, §7.5; a silent skip
#     would bypass the hard requirement);
#   * after the measurement, POST /arbiter/register {engine: "chatterbox", total_bytes: CHATTERBOX_FOOTPRINT_BYTES} so
#     the ledger holds the measured footprint (rule 1). The orchestrator accepts a unit-style key that is in neither
#     engines.json nor phase4-engines.json as class `phase4` with a logged WARNING (atlas.arbiter.register_measured:
#     "the driver measured a real process and the ledger must reflect it"); re-registered on every run because the
#     ledger is in-memory while $ATLAS_STATE/voice-chatterbox.json survives restarts;
#   * the figures the run-time path must declare go into voice.env (CHATTERBOX_FOOTPRINT_BYTES = peak RSS of the CPU
#     render, CHATTERBOX_GTT_DELTA_MB = GTT in use after minus before, expected ~0 on CPU, CHATTERBOX_ARBITER_KEY), and
#     the Arbiter's resident set is re-measured once the voice services are up.
# What is NOT possible from this file and is a BLOCKING cross-writer item (orchestrator writer; recorded for
# phase2/README-contracts.md, not only here): POST /arbiter/load and /arbiter/unload answer 404 for a key that is not in
# config/engines.json / atlas.engines, so the loads themselves cannot yet be granted/queued/refused by the Arbiter. Once
# the `chatterbox` key exists (class `resident` or a CPU class, footprint from CHATTERBOX_FOOTPRINT_BYTES), this step and
# voice_render.py must wrap each load in POST /arbiter/load ... /arbiter/unload; until then the status check above is
# the rule-3 guard and the register call is the rule-1 ledger entry.
#
# UNPINNED / PINNED HERE (rule §7.9; scripts/day1/README.md does not exist yet, so the disclosure lives here and is
# repeated for phase2/README-contracts.md): uv is pinned (UV_PIN, the PyPI release current on 2026-10-04), the managed
# CPython is pinned to the patch (VOICE_PYTHON, the newest 3.11 build uv 0.12.23 lists); the Kokoro/speaches/docling
# image tags are pinned; torch/torchaudio/torchcodec/chatterbox/pyannote are pinned. Nothing in this file is unpinned.
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * docker/core/compose.yml (core services writer): project `atlas-core` (its `name:`), network `atlas`, interpolation
#     from $ATLAS_ETC/core.env (phase2/02-orchestrator.sh; a superset of Phase 1's docker.env). This step merges
#     docker/core/compose.voice.yml into that project with `-f compose.yml -f compose.voice.yml --env-file core.env
#     --env-file voice.env` (docker.env when core.env is absent).
#   * $ATLAS_ETC/docker.env (Phase 1 step 6): LAN_IP, ATLAS_UID, ATLAS_GID used by compose interpolation.
#   * $ATLAS_ETC/secrets/hf-token.env (phase2-services.sh prompt, atlas:atlas 600): HF_TOKEN for the gated PyAnnote
#     repos; verify/v06-pyannote.sh parses it (never sources it) and hands it to the atlas run through the environment.
#   * config/voice-casting.json (content writer): personas[].key/kokoro_primary/kokoro_alternate, reference_recordings;
#     an optional kokoro_fallback per persona is honoured by voice_render.py (Section 22 fallback).
#   * config/allowlist.txt: ghcr.io, github.com (+ release/objects hosts: uv's python-build-standalone download ONLY;
#     fix round 3: chatterbox-tts 0.1.7's PUBLISHED metadata declares `resemble-perth>=1.0.0`, resolved from
#     pypi.org/files.pythonhosted.org like every other dependency, VERIFIED pypi.org/pypi/chatterbox-tts/0.1.7/json
#     2026-10-04; the `git+https://github.com/resemble-ai/Perth.git` line voice-stt.md §3.1 reads is the repository's
#     pyproject, not what pip/uv resolve from the wheel), huggingface.co/.hf.co, pypi.org, files.pythonhosted.org,
#     download.pytorch.org.
#   * /var/cache/atlas (phase2/04-memory.sh's convention): build caches never under $ATLAS_STATE, which restic backs up.
#   * $ATLAS_OPT/venv-uv (phase2/02-orchestrator.sh creates it first, unpinned): this step pins it to UV_PIN in place.
#   * The orchestrator of step 2 on http://127.0.0.1:$ORCH_PORT: POST /arbiter/remeasure is an admin route, loopback-only
#     until ORCH_ADMIN_TOKEN_FILE is set in orchestrator.env; when it is, the token is read from that file and sent as
#     X-Atlas-Token through a curl config on stdin (never argv).
# Contract this file defines for others:
#   * $ATLAS_ETC/voice.env (root:atlas 640, also a compose --env-file): SPEACHES_PRELOAD_MODELS, SPEACHES_HF_HUB_OFFLINE,
#     KOKORO_URL=http://127.0.0.1:8880/v1, SPEACHES_URL=http://127.0.0.1:8881/v1, STT_MODEL=<registry id>,
#     DOCLING_URL=http://127.0.0.1:5001, VOICE_VENV=/opt/atlas/venv-voice, PYANNOTE_VENV=/opt/atlas/venv-pyannote,
#     PYANNOTE_PIPELINE, HF_HOME=$ATLAS_SRV/engines/hf, HF_HUB_OFFLINE=1, HF_HUB_DISABLE_TELEMETRY=1,
#     PYANNOTE_METRICS_ENABLED=0, DO_NOT_TRACK=1, LISTENING_TEST_DIR=$ATLAS_SRV/staging/listening-test,
#     V7_SUMMARY=$LISTENING_TEST_DIR/v7-listening-test.json, CHATTERBOX_FOOTPRINT_BYTES, CHATTERBOX_DEVICE=cpu,
#     CHATTERBOX_GTT_DELTA_MB, CHATTERBOX_ARBITER_KEY=chatterbox (the ledger key registered above), UV_VERSION,
#     VOICE_PYTHON. The orchestrator sources it for every Kokoro/Chatterbox/
#     PyAnnote call; the four offline/telemetry switches are what keep the voice stack silent after this step (§7.1).
#   * Open WebUI (step 3's writer, docker/core/compose.yml: network_mode host) reaches the overlay through the loopback
#     publishes: AUDIO_TTS_OPENAI_API_BASE_URL=http://127.0.0.1:8880/v1 (model "kokoro"),
#     AUDIO_STT_OPENAI_API_BASE_URL=http://127.0.0.1:8881/v1 with AUDIO_STT_MODEL=whisper-1 (aliased below),
#     DOCLING_SERVER_URL=http://127.0.0.1:5001. Container names are atlas-kokoro, atlas-speaches, atlas-docling.
#   * Layout additions for CONVENTIONS.md §1/§2 (recorded for phase2/README-contracts.md): docker/core/compose.voice.yml
#     (kokoro, speaches, docling-serve overlay merged into compose.yml by this step); on the node /opt/atlas/venv-voice,
#     /opt/atlas/venv-pyannote (root, u=rwX,go=rX), /opt/atlas/venv-uv and /opt/atlas/python (root, a+rX; shared with
#     steps 2 and 6), /opt/atlas/tools and /opt/atlas/playwright (step 6).

[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

KOKORO_IMAGE_TAG="v0.9.0"                      # voice-stt.md §2.1 VERIFIED (2026-09-10); pinned in compose.voice.yml
SPEACHES_IMAGE_TAG="0.8.3-cpu"                 # voice-stt.md §4.1 VERIFIED; pinned in compose.voice.yml
SPEACHES_TURBO_PREFERRED="mobiuslabsgmbh/faster-whisper-large-v3-turbo"   # faster-whisper's own mapping (§4.1 VERIFIED)
CHATTERBOX_PIN="chatterbox-tts==0.1.7"         # voice-stt.md §3.1 VERIFIED (PyPI 2026-03-26, MIT)
CHATTERBOX_TORCH_PIN="2.6.0"                   # chatterbox pyproject for python < 3.14 (§3.1 VERIFIED)
PYANNOTE_PIN="pyannote.audio==4.0.7"           # voice-stt.md §5.1 VERIFIED (PyPI 2026-06-30); requires torch>=2.8.0
# torch/torchaudio 2.9.1 (PyPI 2025-11-12, cp311 manylinux wheels VERIFIED on pypi.org 2026-10-04) paired with
# torchcodec 0.8.1 (PyPI 2025-10-28). WHY NOT the 2.8.0 + 0.7.0 pair of the first revision: the torchcodec 0.7.0 wheel
# bundles libtorchcodec_core{4,5,6,7}.so only (FFmpeg 4-7), and resolute ships ffmpeg 7:8.0.1 (voice-stt.md §1 VERIFIED)
# = libavcodec.so.62, so `import torchcodec` fails there with "Could not load libtorchcodec ... FFmpeg versions tried:
# [7, 6, 5, 4]" and pyannote's audio I/O never works. torchcodec 0.8.1 is the first series whose wheel carries
# libtorchcodec_core8.so (VERIFIED 2026-10-04 by listing the cp311 manylinux_2_28 wheel from files.pythonhosted.org).
# torchcodec declares NO torch dependency, so an unpinned install resolves to the newest torchcodec, whose libtorchcodec
# refuses to load against the pinned torch; the pair is pinned together and asserted at import time below.
# UNVERIFIED: the exact torchcodec 0.8.x <-> torch 2.9.x row of torchcodec's compatibility table (github.com README not
# fetched this round); the import assertion in _voice_venv_pyannote is the check and dies with the pair named.
PYANNOTE_TORCH_PIN="2.9.1"
PYANNOTE_TORCHCODEC_PIN="0.8.1"
PYANNOTE_PIPELINE="pyannote/speaker-diarization-3.1"   # Section 14.1; voice-stt.md §5.2
# uv and its managed CPython are PINNED (rule §7.9): uv 0.12.23 is the PyPI release current on 2026-10-04 (VERIFIED);
# cpython 3.11.17 is the newest 3.11 build that uv 0.12.23 lists for linux-x86_64-gnu (VERIFIED with
# `uv python list --all-versions --only-downloads` on that uv). Raising either is a deliberate edit here.
UV_PIN="0.12.23"
VOICE_PYTHON="3.11.17"                         # voice-stt.md §1 recommendation (3.11/3.12), Chatterbox README 3.11
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
KOKORO_URL="http://127.0.0.1:8880"
SPEACHES_URL="http://127.0.0.1:8881"
DOCLING_URL="http://127.0.0.1:5001"
VOICE_CACHE_DIR="/var/cache/atlas"             # same root as phase2/04-memory.sh: never under $ATLAS_STATE (restic)
CHATTERBOX_ARBITER_KEY="chatterbox"            # the Arbiter ledger key (header: ENGINE ARBITER); §8 should list it

VOICE_VENV=""
PYANNOTE_VENV=""
VOICE_HF_HOME=""
VOICE_ENV_FILE=""
LISTENING_DIR=""
UV_BIN=""
SPEACHES_MODEL_ID=""

_voice_paths() {
  VOICE_VENV="$ATLAS_OPT/venv-voice"
  PYANNOTE_VENV="$ATLAS_OPT/venv-pyannote"
  VOICE_HF_HOME="$ATLAS_SRV/engines/hf"        # same cache 04-memory.sh uses (HF_HOME contract in memory.env)
  VOICE_ENV_FILE="$ATLAS_ETC/voice.env"
  LISTENING_DIR="$ATLAS_SRV/staging/listening-test"
}

_voice_apt() {
  # ffmpeg 7:8.0.1 and sox VERIFIED in resolute (voice-stt.md §1); ffmpeg is torchcodec's decoder for PyAnnote.
  apt_install python3-venv python3-pip ffmpeg sox curl jq unzip
}

_voice_dirs() {
  id -u atlas >/dev/null 2>&1 || die "service account atlas does not exist (Phase 1 step 3)"
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/engines/voice" atlas:atlas 755
  # speaches' Whisper weights live on the 8 TB volume like every other engine cache (Appendix C / Section 3.5), bind-
  # mounted into the container, which runs as ATLAS_UID:ATLAS_GID (compose.voice.yml).
  ensure_dir "$ATLAS_SRV/engines/voice/speaches-hub" atlas:atlas 755
  ensure_dir "$VOICE_HF_HOME" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/staging" atlas:atlas 755
  # The Principal listens from the XFCE desktop: owner is the login user, group atlas (the renderer writes through the
  # setgid group), nobody else: the clones are synthesised from recordings of real people (2770, like the inbox).
  ensure_dir "$LISTENING_DIR" "$PRINCIPAL_USER:atlas" 2770
  ensure_dir "$ATLAS_SRV/staging/inbox" "$PRINCIPAL_USER:atlas" 2770
  ensure_dir "$ATLAS_SRV/staging/inbox/voice-references" "$PRINCIPAL_USER:atlas" 2770
  ensure_dir "$VOICE_CACHE_DIR" root:root 755
  ensure_dir "$VOICE_CACHE_DIR/uv" root:root 755
  # speaches model_aliases.json (voice-stt.md §4.1 VERIFIED mount path): whisper-1 -> the turbo id, so Open WebUI's
  # AUDIO_STT_MODEL=whisper-1 stays stable. Rewritten once the registry id is known (see _voice_speaches_model).
  local aliases="$ATLAS_SRV/engines/voice/model_aliases.json"
  if [[ ! -s "$aliases" ]]; then
    printf '{ "whisper-1": "%s", "tts-1": "speaches-ai/Kokoro-82M-v1.0-ONNX", "tts-1-hd": "speaches-ai/Kokoro-82M-v1.0-ONNX" }\n' \
      "$SPEACHES_TURBO_PREFERRED" >"$aliases"
    chown atlas:atlas "$aliases"; chmod 644 "$aliases"
  fi
  # Step 4 (root) may have left root-owned entries in the shared cache; from here on everything runs as atlas, and the
  # end of the step asserts the tree is atlas-owned (CONVENTIONS.md §2).
  chown -R atlas:atlas "$VOICE_HF_HOME"
}

_voice_env_init() {
  # Created with its final mode (root:atlas 640): a die between here and _voice_write_env never leaves it world-readable.
  [[ -e "$VOICE_ENV_FILE" ]] || install -m 640 -o root -g atlas /dev/null "$VOICE_ENV_FILE"
  if [[ ! -s "$VOICE_ENV_FILE" ]]; then
    { echo "# /etc/atlas/voice.env — written by phase2/05-voice.sh (contract in that file's header). Not a secret."; } >"$VOICE_ENV_FILE"
  fi
  # First start online: the registry lookup and the model download need huggingface.co once. compose.voice.yml
  # defaults SPEACHES_HF_HUB_OFFLINE to 1, so the online window exists only while this key says 0.
  grep -q '^SPEACHES_PRELOAD_MODELS=' "$VOICE_ENV_FILE" || ensure_kv "$VOICE_ENV_FILE" SPEACHES_PRELOAD_MODELS '[]'
  grep -q '^SPEACHES_HF_HUB_OFFLINE=' "$VOICE_ENV_FILE" || ensure_kv "$VOICE_ENV_FILE" SPEACHES_HF_HUB_OFFLINE 0
  chown root:atlas "$VOICE_ENV_FILE"; chmod 640 "$VOICE_ENV_FILE"
}

# _voice_compose ARGS... — the merged core + voice project (contract in docker/core/compose.voice.yml).
_voice_compose() {
  local core="$ATLAS_DAY1_DIR/docker/core/compose.yml" voice="$ATLAS_DAY1_DIR/docker/core/compose.voice.yml"
  [[ -f "$core" ]] || die "$core is missing (the core services writer's file; compose.voice.yml is merged into it)"
  [[ -f "$voice" ]] || die "$voice is missing"
  local envf=()
  if [[ -s "$ATLAS_ETC/core.env" ]]; then
    envf=(--env-file "$ATLAS_ETC/core.env")
  elif [[ -s "$ATLAS_ETC/docker.env" ]]; then
    envf=(--env-file "$ATLAS_ETC/docker.env")
  else
    die "neither $ATLAS_ETC/core.env (step 02) nor $ATLAS_ETC/docker.env (Phase 1 step 6) exists"
  fi
  docker compose -f "$core" -f "$voice" "${envf[@]}" --env-file "$VOICE_ENV_FILE" "$@"
}

_voice_up() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  proxy_env
  log "docker compose up -d kokoro speaches docling (images kokoro-fastapi-cpu:$KOKORO_IMAGE_TAG, speaches:$SPEACHES_IMAGE_TAG, docling-serve-cpu:v1.34.0; pulls go through the proxy)"
  retry 3 _voice_compose up -d --quiet-pull kokoro speaches docling \
    || die "docker compose up for the voice services failed: $(_voice_compose logs --tail 20 kokoro speaches docling 2>&1 | tail -n 30)"
}

_voice_kokoro_check() {
  wait_http "$KOKORO_URL/health" 180 || die "Kokoro did not answer 200 on $KOKORO_URL/health within 180 s: docker logs atlas-kokoro"
  # 14.3 shortlist must exist in the image's voice list (GET /v1/audio/voices -> {"voices":[{"id",...}]}, VERIFIED).
  # API answers are fed to python on stdin, never as an argv element (Linux caps one argument at 128 KiB).
  local voices
  voices="$(curl -fsS --noproxy '*' --max-time 30 "$KOKORO_URL/v1/audio/voices")" || die "GET $KOKORO_URL/v1/audio/voices failed"
  python3 -c '
import json, re, sys
voices = json.load(sys.stdin)["voices"]
ids = {v["id"] if isinstance(v, dict) else str(v) for v in voices}
canon = sorted(i for i in ids if re.fullmatch(r"[a-z]{2}_[a-z]+", i) and "_v0" not in i and not i.endswith("_inno"))
need = set()
for p in json.load(open(sys.argv[1], encoding="utf-8"))["personas"]:
    for k in ("kokoro_primary", "kokoro_alternate", "kokoro_fallback"):
        if p.get(k):
            need.add(p[k])
need.add("am_onyx")   # voice_render.py Section 22 fallback preset for a persona without candidates (Alaric)
missing = sorted(need - ids)
print(f"kokoro: {len(canon)} canonical presets; shortlist {len(need)} voices, missing {missing}")
sys.exit(1 if missing else 0)' "$ATLAS_DAY1_DIR/config/voice-casting.json" <<<"$voices" \
    || die "Kokoro's voice list lacks Section 14.3 presets (see above); the image tag is wrong or the voice packs changed"
  # One timed sentence (voice-stt.md §8.2): the number is recorded, not asserted (§2.4 latency is UNVERIFIED).
  local tmp t0 t1 secs audio
  tmp="$(mktemp --suffix=.wav)"
  t0="$(date +%s.%N)"
  curl -fsS --noproxy '*' --max-time 300 "$KOKORO_URL/v1/audio/speech" -H 'Content-Type: application/json' \
    -d '{"model":"kokoro","input":"Good morning. The overnight audit finished without exceptions.","voice":"bm_george","response_format":"wav","stream":false}' \
    -o "$tmp" || { rm -f "$tmp"; die "Kokoro POST /v1/audio/speech failed"; }
  t1="$(date +%s.%N)"
  secs="$(python3 -c 'import sys; print(round(float(sys.argv[2])-float(sys.argv[1]),2))' "$t0" "$t1")"
  audio="$(python3 -c 'import sys,wave; w=wave.open(sys.argv[1]); print(round(w.getnframes()/w.getframerate(),2))' "$tmp" 2>/dev/null || echo '?')"
  rm -f "$tmp"
  log "kokoro: one sentence (bm_george) rendered in ${secs}s wall-clock, ${audio}s of audio"
}

# _voice_pick_turbo PREFERRED <JSON on stdin> — the preferred id when listed, else the first id containing large-v3-turbo.
_voice_pick_turbo() {
  python3 -c '
import json, sys
ids = [m["id"] for m in json.load(sys.stdin).get("data", [])]
pref = sys.argv[1]
print(pref if pref in ids else next((i for i in ids if "large-v3-turbo" in i), ""))' "$1"
}

_voice_speaches_model() {
  wait_http "$SPEACHES_URL/health" 180 || die "speaches did not answer 200 on $SPEACHES_URL/health within 180 s: docker logs atlas-speaches"
  local installed
  installed="$(curl -fsS --noproxy '*' --max-time 30 "$SPEACHES_URL/v1/models" 2>/dev/null || echo '{}')"
  SPEACHES_MODEL_ID="$(_voice_pick_turbo "$SPEACHES_TURBO_PREFERRED" <<<"$installed")"
  if [[ -z "$SPEACHES_MODEL_ID" ]]; then
    # UNVERIFIED (voice-stt.md §4.1): which turbo id speaches' registry offers (it is built from HF tags). The preferred
    # id is faster-whisper's own mapping; any other id containing large-v3-turbo is accepted; nothing else is (the
    # baseline names Large-v3-Turbo; large-v3 would be a silent substitution). The registry answer is large (every
    # ctranslate2 ASR model on the hub): stdin, never argv.
    local registry
    registry="$(curl -fsS --noproxy '*' --max-time 120 "$SPEACHES_URL/v1/registry?task=automatic-speech-recognition")" \
      || die "GET $SPEACHES_URL/v1/registry failed (the registry needs huggingface.co through the container proxy on the first start; is SPEACHES_HF_HUB_OFFLINE=0 in $VOICE_ENV_FILE?)"
    SPEACHES_MODEL_ID="$(_voice_pick_turbo "$SPEACHES_TURBO_PREFERRED" <<<"$registry")"
    [[ -n "$SPEACHES_MODEL_ID" ]] || die "speaches' registry lists no large-v3-turbo model (looked for $SPEACHES_TURBO_PREFERRED); the whisper.cpp path (voice-stt.md §4.2) is the alternative, not automated here"
    log "speaches: downloading $SPEACHES_MODEL_ID (POST /v1/models/<id>; ~1.6 GB through the proxy into $ATLAS_SRV/engines/voice/speaches-hub, several minutes)"
    retry 3 curl -fsS --noproxy '*' --max-time 3600 -X POST "$SPEACHES_URL/v1/models/$SPEACHES_MODEL_ID" -o /dev/null \
      || die "speaches could not download $SPEACHES_MODEL_ID (huggingface.co via the container proxy; docker logs atlas-speaches)"
  fi
  installed="$(curl -fsS --noproxy '*' --max-time 30 "$SPEACHES_URL/v1/models")"
  grep -qF "\"$SPEACHES_MODEL_ID\"" <<<"$installed" || die "speaches does not list $SPEACHES_MODEL_ID after the download: ${installed:0:300}"
  # Alias whisper-1 -> the real id (mounted read-only; picked up on the restart below).
  printf '{ "whisper-1": "%s", "tts-1": "speaches-ai/Kokoro-82M-v1.0-ONNX", "tts-1-hd": "speaches-ai/Kokoro-82M-v1.0-ONNX" }\n' \
    "$SPEACHES_MODEL_ID" >"$ATLAS_SRV/engines/voice/model_aliases.json"
  # From now on: preload the model at start and never touch the network (Section 12.5; PRELOAD_MODELS VERIFIED).
  ensure_kv "$VOICE_ENV_FILE" SPEACHES_PRELOAD_MODELS "[\"$SPEACHES_MODEL_ID\"]"
  ensure_kv "$VOICE_ENV_FILE" SPEACHES_HF_HUB_OFFLINE 1
  ensure_kv "$VOICE_ENV_FILE" STT_MODEL "$SPEACHES_MODEL_ID"
  log "speaches: restarting with PRELOAD_MODELS=[\"$SPEACHES_MODEL_ID\"] and HF_HUB_OFFLINE=1"
  _voice_compose up -d --no-deps speaches || die "docker compose up speaches (offline restart) failed"
  wait_http "$SPEACHES_URL/health" 300 || die "speaches did not come back after the offline restart (PRELOAD_MODELS makes it exit if the model is missing): docker logs atlas-speaches"
  # Round trip: Kokoro speaks, Whisper listens (voice-stt.md §8.4).
  local wav resp text t0 t1 secs
  wav="$(mktemp --suffix=.wav)"
  curl -fsS --noproxy '*' --max-time 300 "$KOKORO_URL/v1/audio/speech" -H 'Content-Type: application/json' \
    -d '{"model":"kokoro","input":"The quick brown fox jumps over the lazy dog.","voice":"af_heart","response_format":"wav","stream":false}' \
    -o "$wav" || { rm -f "$wav"; die "Kokoro render for the STT round trip failed"; }
  t0="$(date +%s.%N)"
  resp="$(curl -fsS --noproxy '*' --max-time 600 "$SPEACHES_URL/v1/audio/transcriptions" -F "file=@$wav" -F "model=$SPEACHES_MODEL_ID" -F "language=en")" \
    || { rm -f "$wav"; die "POST $SPEACHES_URL/v1/audio/transcriptions failed (docker logs atlas-speaches)"; }
  t1="$(date +%s.%N)"
  rm -f "$wav"
  secs="$(python3 -c 'import sys; print(round(float(sys.argv[2])-float(sys.argv[1]),2))' "$t0" "$t1")"
  text="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("text",""))' <<<"$resp" 2>/dev/null || true)"
  grep -qi 'quick brown fox' <<<"$text" || die "Whisper did not transcribe the round-trip sentence (got: '${text:-${resp:0:200}}')"
  log "speaches: $SPEACHES_MODEL_ID transcribed the round trip in ${secs}s: '$text'"
}

_voice_docling_check() {
  # /docs is the documented path (services-tools.md §2.3); the compose healthcheck uses the same UNVERIFIED-free path.
  wait_http "$DOCLING_URL/docs" 300 || die "docling-serve did not answer 200 on $DOCLING_URL/docs within 300 s: docker logs atlas-docling"
  log "docling-serve up at $DOCLING_URL (Open WebUI: DOCLING_SERVER_URL=http://127.0.0.1:5001; Section 17 step 4's Docling service, startable from step 4 with the command in the header)"
}

# --- uv-managed CPython 3.11 venvs ------------------------------------------------------------------------------------
# _voice_root_only DIR — the venv boundary: root:root, u=rwX,go=rX, nothing writable by group/other, asserted.
_voice_root_only() {
  local dir="$1" stray
  chown -R root:root "$dir"
  chmod -R u=rwX,go=rX "$dir"
  # Symlinks are excluded from the mode test: their own mode is always 0777 (bin/python -> the managed interpreter).
  stray="$(find "$dir" ! -type l \( ! -user root -o -perm /o+w \) 2>/dev/null | head -n 3 || true)"
  [[ -z "$stray" ]] || die "$dir still has non-root or world-writable entries after the fix (Section 16.3 item 6): $(tr '\n' ' ' <<<"$stray")"
}

_voice_uv() {
  local bv="$ATLAS_OPT/venv-uv"
  if [[ ! -x "$bv/bin/uv" ]]; then
    mkdir -p "$ATLAS_OPT"
    python3 -m venv "$bv" || die "python3 -m venv $bv failed"
  fi
  # Pinned (rule §7.9): step 2 may have created this venv with an unpinned uv; it is brought to UV_PIN here, in place.
  if [[ "$("$bv/bin/uv" --version 2>/dev/null | awk '{print $2}')" != "$UV_PIN" ]]; then
    proxy_env
    log "venv-uv: installing uv==$UV_PIN into $bv (was: $("$bv/bin/uv" --version 2>/dev/null || echo none))"
    retry 3 "$bv/bin/python" -m pip install --quiet --disable-pip-version-check "uv==$UV_PIN" || die "pip install uv==$UV_PIN into $bv failed (pypi.org through the proxy?)"
    [[ "$("$bv/bin/uv" --version 2>/dev/null | awk '{print $2}')" == "$UV_PIN" ]] || die "uv in $bv is not $UV_PIN after the install: $("$bv/bin/uv" --version 2>&1)"
  fi
  chmod -R a+rX "$bv"
  UV_BIN="$bv/bin/uv"
  # Caches under /var/cache/atlas (OS drive, transient), never $ATLAS_STATE (restic's include set, CONVENTIONS §2).
  export UV_PYTHON_INSTALL_DIR="$ATLAS_OPT/python" UV_CACHE_DIR="$VOICE_CACHE_DIR/uv" UV_HTTP_TIMEOUT=600
  mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"
  proxy_env
  # uv fetches python-build-standalone from github.com release assets (allowlisted). A failure stops here. The patch
  # level is pinned (VOICE_PYTHON); `uv python find 3.11.17` matches only that build.
  if ! "$UV_BIN" python find "$VOICE_PYTHON" >/dev/null 2>&1; then
    log "uv: installing the managed CPython $VOICE_PYTHON under $UV_PYTHON_INSTALL_DIR"
    retry 3 "$UV_BIN" python install "$VOICE_PYTHON" || die "uv python install $VOICE_PYTHON failed (github.com release assets through the proxy? does uv $UV_PIN list $VOICE_PYTHON: uv python list --all-versions)"
  fi
  chmod -R a+rX "$UV_PYTHON_INSTALL_DIR"     # the atlas account runs the venvs built on this interpreter
  log "uv: $("$UV_BIN" --version 2>&1) with CPython $VOICE_PYTHON"
}

# _voice_venv_make DIR — create DIR with the managed CPython when absent; root-owned BEFORE root runs anything in it.
_voice_venv_make() {
  local dir="$1"
  if [[ ! -x "$dir/bin/python" ]]; then
    "$UV_BIN" venv --python "$VOICE_PYTHON" "$dir" || die "uv venv $dir failed"
  fi
  # An earlier revision chowned the venvs to atlas: re-own before executing the interpreter (root never runs code the
  # atlas account could have written; Section 16.3 items 5/6).
  _voice_root_only "$dir"
  "$dir/bin/python" -c 'import sys; assert sys.version_info[:3] == tuple(int(x) for x in sys.argv[1].split(".")), sys.version' "$VOICE_PYTHON" \
    || die "$dir is not a Python $VOICE_PYTHON venv (delete it and re-run this step)"
}

# _voice_pip DIR ARGS... — uv pip install into DIR through the proxy (as root, into the root-owned venv).
_voice_pip() {
  local dir="$1"; shift
  proxy_env
  retry 3 "$UV_BIN" pip install --quiet --python "$dir/bin/python" "$@"
}

# _voice_as_atlas [ENV=VAL...] CMD... — run CMD as the atlas service account with the proxy and the HF cache set; every
# telemetry switch off (rule §7.1). Extra KEY=VALUE pairs precede the command (env(1) syntax).
_voice_as_atlas() {
  svc_user_run env HOME="$(getent passwd atlas | cut -d: -f6)" HF_HOME="$VOICE_HF_HOME" HF_HUB_ENABLE_HF_TRANSFER=0 \
    HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 PYANNOTE_METRICS_ENABLED=0 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" NO_PROXY="${NO_PROXY:-}" \
    OMP_NUM_THREADS="$(nproc)" "$@"
}

_voice_venv_chatterbox() {
  _voice_venv_make "$VOICE_VENV"
  if ! "$VOICE_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("chatterbox-tts") == "0.1.7"' 2>/dev/null; then
    # UNVERIFIED (voice-stt.md §3.1): the CPU wheel for torch==2.6.0 cp311 on download.pytorch.org (index was blocked
    # during research; it is the standard CPU index). Installed FIRST so chatterbox's pin resolves to the CPU build
    # instead of pulling ~3 GB of CUDA libraries.
    log "venv-voice: torch==$CHATTERBOX_TORCH_PIN (CPU index), then $CHATTERBOX_PIN (every dependency, resemble-perth included, from pypi.org)"
    _voice_pip "$VOICE_VENV" --index-url "$TORCH_CPU_INDEX" "torch==$CHATTERBOX_TORCH_PIN" "torchaudio==$CHATTERBOX_TORCH_PIN" \
      || die "CPU torch $CHATTERBOX_TORCH_PIN could not be installed into $VOICE_VENV from $TORCH_CPU_INDEX"
    # No `apt_install git` (fix round 3): the 0.1.7 wheel's metadata resolves resemble-perth from PyPI (header), and uv
    # fetches its CPython over HTTPS; nothing in this venv's build needs git.
    _voice_pip "$VOICE_VENV" "$CHATTERBOX_PIN" \
      || die "pip install $CHATTERBOX_PIN failed in $VOICE_VENV (pypi.org / files.pythonhosted.org through the proxy? the resolver output above names the package)"
    _voice_root_only "$VOICE_VENV"
  fi
  _voice_as_atlas "$VOICE_VENV/bin/python" -c 'import chatterbox, torch; assert not torch.cuda.is_available(); print("chatterbox ok, torch", torch.__version__)' \
    || die "chatterbox does not import from $VOICE_VENV as atlas (read-only venv; the HF cache is $VOICE_HF_HOME)"
}

# _voice_gtt_used_mb — gpu_gtt_used_mb, or "" when the counter is unreadable (recorded, never fatal here: V3a owns it).
_voice_gtt_used_mb() { gpu_gtt_used_mb 2>/dev/null || true; }

_voice_chatterbox_measure() {
  # One sentence with the English base model (built-in conditioning, no reference clip); ResembleAI/chatterbox is
  # pulled from Hugging Face on the first call (voice-stt.md §3.2 VERIFIED file list), by the atlas account into the
  # atlas-owned cache. The seconds are RECORDED, not gated: cloning is an offline job. Then the same render is repeated
  # with HF_HUB_OFFLINE=1, which must succeed from the cache alone (Section 12.5; the run-time environment in voice.env
  # sets HF_HUB_OFFLINE=1 for good). The FOOTPRINT is measured too (header, Section 4.2): peak RSS of the render process
  # (ru_maxrss) and the GTT counter before/after (CPU device: expected ~0). Each load is preceded by the Arbiter idle
  # check and the result is registered with the Arbiter afterwards (header: ENGINE ARBITER).
  local out="$ATLAS_STATE/voice-chatterbox.json" sample="$ATLAS_SRV/engines/voice/chatterbox-sample.wav"
  if [[ -s "$out" ]] && grep -q '"offline_ok": true' "$out" && grep -q '"footprint_bytes"' "$out"; then
    log "chatterbox: measurement already recorded in $out: $(tr -d '\n' <"$out")"
    return 0
  fi
  proxy_env
  local script rec_online rec_offline gtt0 gtt1
  # shellcheck disable=SC2016  # the script is python, not shell; nothing here is meant to expand
  script='
import json, resource, sys, time, wave
import numpy as np
from chatterbox.tts import ChatterboxTTS
sample, text = sys.argv[1], sys.argv[2]
t0 = time.monotonic()
model = ChatterboxTTS.from_pretrained(device="cpu")
load_s = round(time.monotonic() - t0, 1)
t1 = time.monotonic()
wav = model.generate(text)
gen_s = round(time.monotonic() - t1, 1)
pcm = np.clip(wav.detach().cpu()[0].numpy(), -1.0, 1.0)
pcm16 = (pcm * 32767.0).astype("<i2")
if sample != "-":
    with wave.open(sample, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(model.sr)); w.writeframes(pcm16.tobytes())
audio_s = round(len(pcm16) / float(model.sr), 2)
peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024   # Linux: KiB -> bytes
print(json.dumps({"load_s": load_s, "generate_s": gen_s, "audio_s": audio_s,
                  "rtf": round(gen_s / audio_s, 2) if audio_s else None, "peak_rss_bytes": int(peak_rss)}))
'
  local text="Good morning. The overnight audit finished without exceptions, and nothing needs your attention today."
  log "chatterbox: rendering one sentence on CPU as atlas (first run downloads ResembleAI/chatterbox into $VOICE_HF_HOME)"
  _voice_arbiter_require_idle "the Chatterbox online measure (CPU load)"
  gtt0="$(_voice_gtt_used_mb)"
  rec_online="$(_voice_as_atlas HF_HUB_OFFLINE=0 "$VOICE_VENV/bin/python" -c "$script" "$sample" "$text" | tail -n1)" \
    || die "the Chatterbox one-sentence render failed (see above; huggingface.co through the proxy?)"
  gtt1="$(_voice_gtt_used_mb)"
  log "chatterbox: online render $rec_online (GTT used before ${gtt0:-?} MiB, after ${gtt1:-?} MiB)"
  log "chatterbox: repeating a short render with HF_HUB_OFFLINE=1 (the cache must be complete; nothing may leave the node)"
  _voice_arbiter_require_idle "the Chatterbox offline re-render (CPU load)"
  rec_offline="$(_voice_as_atlas HF_HUB_OFFLINE=1 "$VOICE_VENV/bin/python" -c "$script" - "Offline check." | tail -n1)" \
    || die "Chatterbox could not render with HF_HUB_OFFLINE=1 from $VOICE_HF_HOME: the weight cache is incomplete or the loader still needs the network (see above)"
  python3 - "$out" "$rec_online" "$rec_offline" "$text" "${gtt0:-}" "${gtt1:-}" <<'PY' || die "could not write $out"
import json, sys, time
out, on, off, text, g0, g1 = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6]
gtt_delta = (int(g1) - int(g0)) if (g0 and g1) else None
rec = {"model": "ResembleAI/chatterbox", "device": "cpu", "ran_as": "atlas", "sentence": text,
       "load_s": on["load_s"], "generate_s": on["generate_s"], "audio_s": on["audio_s"], "rtf": on["rtf"],
       "footprint_bytes": int(on["peak_rss_bytes"]), "footprint_kind": "peak_rss (CPU render; Section 4.2 figure for /arbiter/load)",
       "gtt_delta_mb": gtt_delta,
       "offline_ok": True, "offline_load_s": off["load_s"],
       "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(rec, fh); fh.write("\n")
print(f"chatterbox: load {rec['load_s']}s, one sentence {rec['generate_s']}s for {rec['audio_s']}s of audio (RTF {rec['rtf']}); "
      f"peak RSS {rec['footprint_bytes'] / 2**30:.2f} GiB, GTT delta {gtt_delta} MiB; offline reload {rec['offline_load_s']}s")
PY
  log "chatterbox: measurement recorded in $out (cloning speed is not a gate; the footprint goes into voice.env)"
}

_voice_venv_pyannote() {
  _voice_venv_make "$PYANNOTE_VENV"
  if ! "$PYANNOTE_VENV/bin/python" -c "import importlib.metadata as m; assert m.version('pyannote.audio') == '4.0.7' and m.version('torch').startswith('$PYANNOTE_TORCH_PIN') and m.version('torchcodec') == '$PYANNOTE_TORCHCODEC_PIN'" 2>/dev/null; then
    # UNVERIFIED: CPU wheels for torch/torchaudio 2.9.1 on the CPU index (the index was blocked in research and in this
    # round; 2.9.1 cp311 manylinux wheels exist on PyPI, VERIFIED). torchcodec 0.8.1's cp311 manylinux wheel is on PyPI
    # (VERIFIED, carries libtorchcodec_core8.so for the ffmpeg 8 of resolute); uv's default first-index strategy takes
    # torch/torchaudio from the CPU index (the +cpu builds) and falls back to PyPI only for a package the CPU index does
    # not carry at all (torchcodec, if absent there). A pair that does not import together dies below, naming both pins.
    log "venv-pyannote: torch==$PYANNOTE_TORCH_PIN torchaudio==$PYANNOTE_TORCH_PIN torchcodec==$PYANNOTE_TORCHCODEC_PIN (CPU index), then $PYANNOTE_PIN"
    _voice_pip "$PYANNOTE_VENV" --index-url "$TORCH_CPU_INDEX" --extra-index-url https://pypi.org/simple \
        "torch==$PYANNOTE_TORCH_PIN" "torchaudio==$PYANNOTE_TORCH_PIN" "torchcodec==$PYANNOTE_TORCHCODEC_PIN" \
      || die "CPU torch $PYANNOTE_TORCH_PIN/torchaudio/torchcodec $PYANNOTE_TORCHCODEC_PIN could not be installed into $PYANNOTE_VENV from $TORCH_CPU_INDEX"
    _voice_pip "$PYANNOTE_VENV" "$PYANNOTE_PIN" || die "pip install $PYANNOTE_PIN failed in $PYANNOTE_VENV"
    _voice_root_only "$PYANNOTE_VENV"
  fi
  # The import of torchcodec is the pair check (and the FFmpeg-8 check: core8 must load against the host's libavcodec
  # 62): a mismatch dies here, in the step, not later at V6.
  _voice_as_atlas "$PYANNOTE_VENV/bin/python" -c 'import pyannote.audio, torch, torchcodec; print("pyannote.audio", pyannote.audio.__version__, "torch", torch.__version__, "torchcodec", torchcodec.__version__)' \
    || die "pyannote.audio/torch/torchcodec do not import together from $PYANNOTE_VENV (torchcodec $PYANNOTE_TORCHCODEC_PIN must match torch $PYANNOTE_TORCH_PIN and the host ffmpeg: ffmpeg -version | head -n1; a 'Could not load libtorchcodec' error names the FFmpeg majors the wheel carries)"
}

# _voice_listening_modes — owner $PRINCIPAL_USER (listens from the XFCE desktop), group atlas with WRITE (the renderer
# rewrites the summary and replaces WAVs on a re-run), nobody else (clones of real voices). Applied BEFORE and after
# every render (fix round 3: an earlier verify/v07 stripped g+w at the gate, so the documented `--force 05` re-run
# failed with PermissionError and the STALE summary was recorded).
_voice_listening_modes() {
  chown -R "$PRINCIPAL_USER:atlas" "$LISTENING_DIR"
  chmod -R o-rwx,g+rwX "$LISTENING_DIR"
}

_voice_render_v7() {
  # The listening test is rendered here, once, as atlas; verify/v07-voice-listen.sh reads the summary (header, item 4).
  local renderer="$ATLAS_DAY1_DIR/phase2/voice_render.py" summary="$LISTENING_DIR/v7-listening-test.json" rc=0
  [[ -f "$renderer" ]] || die "$renderer is missing"
  svc_user_run test -r "$renderer" -a -r "$ATLAS_DAY1_DIR/config/voice-casting.json" \
    || die "atlas cannot read $renderer or config/voice-casting.json under $ATLAS_DAY1_DIR (the /opt copy is root 755 by CONVENTIONS §2)"
  _voice_listening_modes
  # A stale summary can never satisfy the check below: it is removed before the render, so whatever exists afterwards
  # was written by THIS render (voice_render.py writes it atomically, temp file + rename).
  rm -f "$summary" "$summary.part"
  # The clone batch loads Chatterbox (CPU, ~GB) inside the renderer: Section 4.2 rule 3 guard first (header).
  _voice_arbiter_require_idle "the V7 render (Chatterbox clone batch inside voice_render.py)"
  log "v7: rendering the listening test into $LISTENING_DIR as atlas (Kokoro paragraphs; Chatterbox clones for every reference recording present; existing files are kept)"
  _voice_as_atlas HF_HUB_OFFLINE=1 python3 "$renderer" render --casting "$ATLAS_DAY1_DIR/config/voice-casting.json" \
      --out "$LISTENING_DIR" --kokoro "$KOKORO_URL" --venv-python "$VOICE_VENV/bin/python" --hf-home "$VOICE_HF_HOME" \
      --json-out "$summary" >/dev/null || rc=$?
  case "$rc" in
    0) log "v7: rendered (every reference recording present)" ;;
    2) log "v7: rendered; reference recordings absent -> V7 will be recorded deferred (Section 22)" ;;
    *) # The renderer records its own failures in the summary (status fail) and exits 1; V7 is then recorded fail from
       # it. Any other outcome without a fresh summary (PermissionError before the write, a crash, a kill) is fatal here.
       [[ -s "$summary" ]] || die "voice_render.py exited $rc and wrote no summary at $summary: see its stderr above (PermissionError on $LISTENING_DIR? the renderer runs as atlas, the directory is $(stat -c '%A %U:%G' "$LISTENING_DIR"))"
       warn "v7: the renderer exited $rc with a summary; V7 will be recorded as fail from $summary" ;;
  esac
  [[ -s "$summary" ]] || die "voice_render.py wrote no summary at $summary"
  cp -f "$summary" "$ATLAS_STATE/v7-listening-test.json"
  _voice_listening_modes
}

# --- Engine Arbiter (Section 4.2; header: ENGINE ARBITER) ---------------------------------------------------------------
# _voice_arbiter_call METHOD PATH [JSON_BODY] -> VOICE_ARB_HTTP (three digits, 000 when unreachable), VOICE_ARB_BODY.
# Admin route on the step-2 orchestrator: loopback-only until ORCH_ADMIN_TOKEN_FILE is set in orchestrator.env; when it
# is, the token is read from that file and travels in a curl config on stdin (never argv). Results travel through
# globals: a `$(...)` caller would run this in a subshell and lose the HTTP code.
VOICE_ARB_HTTP=""
VOICE_ARB_BODY=""
_voice_arbiter_call() {
  local method="$1" path="$2" body="${3:-}" cfg="" tokf="" tok="" ans url
  url="http://127.0.0.1:${ORCH_PORT:-8800}$path"
  tokf="$(awk -F= '$1=="ORCH_ADMIN_TOKEN_FILE" {print $2; exit}' "$ATLAS_ETC/orchestrator.env" 2>/dev/null || true)"
  if [[ -n "$tokf" && -r "$tokf" ]]; then
    tok="$(awk -F= '$1=="ORCH_ADMIN_TOKEN" {print $2; exit} NR==1 && $0 !~ /=/ {print $0; exit}' "$tokf")"
    [[ -n "$tok" ]] && cfg="header = \"X-Atlas-Token: $tok\""
  fi
  local extra=()
  [[ -z "$body" ]] || extra=(-H 'Content-Type: application/json' -d "$body")
  ans="$(mktemp)"
  VOICE_ARB_HTTP="$(curl -sS --noproxy '*' --max-time 60 -X "$method" -K - "${extra[@]}" -o "$ans" -w '%{http_code}' "$url" 2>/dev/null <<<"$cfg" || true)"
  [[ "$VOICE_ARB_HTTP" =~ ^[0-9]{3}$ ]] || VOICE_ARB_HTTP=000
  VOICE_ARB_BODY="$(tr -d '\n' <"$ans")"
  rm -f "$ans"
}

# _voice_arbiter_require_idle WHAT — Section 4.2 rule 3 for a Chatterbox load the Arbiter cannot grant itself yet
# (header): the ledger must show no resident and no generating engine, or the step stops. The orchestrator must answer:
# step 2 is a prerequisite (§7.5), and a skipped check would bypass a hard requirement silently (§7.4).
_voice_arbiter_require_idle() {
  local what="$1" verdict state resident generating
  _voice_arbiter_call GET /arbiter/status
  [[ "$VOICE_ARB_HTTP" == 200 ]] || die "GET /arbiter/status answered HTTP $VOICE_ARB_HTTP before $what (Section 4.2: every weight-bearing load passes through the Arbiter): is the step-2 orchestrator up? systemctl status atlas-orchestrator; curl -sS http://127.0.0.1:${ORCH_PORT:-8800}/health"
  # Engine keys are [A-Za-z0-9._-]+ (CONVENTIONS §8), so a comma-joined list reads back as one word.
  verdict="$(python3 -c '
import json, sys
d = json.load(sys.stdin)
res = ",".join(str(x.get("engine", "?")) for x in d.get("resident", []))
g = d.get("generating")
gen = str(g.get("engine", "?")) if isinstance(g, dict) else ""
print("halted" if d.get("halted") else "ok", res or "-", gen or "-")' <<<"$VOICE_ARB_BODY" 2>/dev/null)" \
    || die "GET /arbiter/status did not answer with the ledger JSON before $what: ${VOICE_ARB_BODY:0:200}"
  read -r state resident generating <<<"$verdict"
  [[ "$resident" == "-" && "$generating" == "-" ]] \
    || die "refusing $what: the Arbiter ledger shows resident engine(s) [$resident] and generating [$generating] (Section 4.2 rule 3: two resident, one generating; this ~GB CPU load is not granted beside them). Wait for the engine to unload (or POST /arbiter/unload) and re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 05"
  [[ "$state" == ok ]] || warn "arbiter: the ledger reports halted=true (a GPU release check failed earlier); the CPU load proceeds, Phase 3 will not until it is cleared"
  log "arbiter: ledger idle (no resident, no generating engine) before $what"
}

# _voice_arbiter_register_chatterbox — Section 4.2 rule 1: the measured footprint goes into the ledger on EVERY run
# (the ledger is in-memory; $ATLAS_STATE/voice-chatterbox.json survives restarts, the registration does not). The
# orchestrator records a unit-style key that is in neither engines.json nor phase4-engines.json as class `phase4` with a
# logged WARNING (atlas.arbiter.register_measured); that is the documented path until the `chatterbox` key exists.
_voice_arbiter_register_chatterbox() {
  local rec="$ATLAS_STATE/voice-chatterbox.json" fp
  fp="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["footprint_bytes"]))' "$rec" 2>/dev/null || true)"
  [[ -n "$fp" ]] || die "no footprint_bytes in $rec: nothing to register with the Arbiter (the Chatterbox measurement did not record it)"
  _voice_arbiter_call POST /arbiter/register "{\"engine\": \"$CHATTERBOX_ARBITER_KEY\", \"total_bytes\": $fp, \"task_id\": \"day1-phase2-05\"}"
  [[ "$VOICE_ARB_HTTP" == 200 ]] || die "POST /arbiter/register {engine: $CHATTERBOX_ARBITER_KEY, total_bytes: $fp} answered HTTP $VOICE_ARB_HTTP: ${VOICE_ARB_BODY:0:200} (Section 4.2 rule 1: the ledger must hold the Chatterbox footprint; orchestrator down, or a package that refuses keys outside engines.json?)"
  log "arbiter: registered $CHATTERBOX_ARBITER_KEY with total_bytes=$fp (peak RSS of the CPU render): ${VOICE_ARB_BODY:0:200}"
}

# _voice_arbiter_remeasure — Section 4.1: the resident set is re-read once the voice services are up (the orchestrator
# measured at its start in step 2, before Kokoro/Whisper/Docling existed). A 409 (an engine is resident) or an
# unreachable orchestrator is a WARN, not a stop: the API re-measures itself on the next load while nothing is resident
# (_maybe_remeasure), so the figure is never stale when it is used.
_voice_arbiter_remeasure() {
  _voice_arbiter_call POST /arbiter/remeasure
  case "$VOICE_ARB_HTTP" in
    200) log "arbiter: resident set re-measured with the voice stack up (POST /arbiter/remeasure on :${ORCH_PORT:-8800}): ${VOICE_ARB_BODY:0:200}" ;;
    409) warn "arbiter: POST /arbiter/remeasure refused (an engine is resident): ${VOICE_ARB_BODY:0:200}; the API re-measures on the next load with nothing resident" ;;
    *)   warn "arbiter: POST /arbiter/remeasure answered HTTP $VOICE_ARB_HTTP (orchestrator down? systemctl status atlas-orchestrator); the API re-measures on the next load with nothing resident" ;;
  esac
}

_voice_write_env() {
  ensure_kv "$VOICE_ENV_FILE" KOKORO_URL "$KOKORO_URL/v1"
  ensure_kv "$VOICE_ENV_FILE" SPEACHES_URL "$SPEACHES_URL/v1"
  ensure_kv "$VOICE_ENV_FILE" STT_MODEL "$SPEACHES_MODEL_ID"
  ensure_kv "$VOICE_ENV_FILE" DOCLING_URL "$DOCLING_URL"
  ensure_kv "$VOICE_ENV_FILE" VOICE_VENV "$VOICE_VENV"
  ensure_kv "$VOICE_ENV_FILE" PYANNOTE_VENV "$PYANNOTE_VENV"
  ensure_kv "$VOICE_ENV_FILE" PYANNOTE_PIPELINE "$PYANNOTE_PIPELINE"
  ensure_kv "$VOICE_ENV_FILE" HF_HOME "$VOICE_HF_HOME"
  # Offline and silent for good (rule §7.1, Section 12.5): no revision HEADs, no telemetry user-agent, no pyannote metrics.
  ensure_kv "$VOICE_ENV_FILE" HF_HUB_OFFLINE 1
  ensure_kv "$VOICE_ENV_FILE" HF_HUB_DISABLE_TELEMETRY 1
  ensure_kv "$VOICE_ENV_FILE" PYANNOTE_METRICS_ENABLED 0
  ensure_kv "$VOICE_ENV_FILE" DO_NOT_TRACK 1
  ensure_kv "$VOICE_ENV_FILE" LISTENING_TEST_DIR "$LISTENING_DIR"
  ensure_kv "$VOICE_ENV_FILE" V7_SUMMARY "$LISTENING_DIR/v7-listening-test.json"
  ensure_kv "$VOICE_ENV_FILE" UV_VERSION "$UV_PIN"
  ensure_kv "$VOICE_ENV_FILE" VOICE_PYTHON "$VOICE_PYTHON"
  # Section 4.2 figure for the orchestrator's Chatterbox path (header): the measured footprint of the CPU render.
  local rec="$ATLAS_STATE/voice-chatterbox.json" fp gtt
  fp="$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["footprint_bytes"]))' "$rec" 2>/dev/null || true)"
  gtt="$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1])).get("gtt_delta_mb"); print("" if v is None else int(v))' "$rec" 2>/dev/null || true)"
  [[ -n "$fp" ]] || die "no footprint_bytes in $rec (the Chatterbox measurement did not record it)"
  ensure_kv "$VOICE_ENV_FILE" CHATTERBOX_FOOTPRINT_BYTES "$fp"
  ensure_kv "$VOICE_ENV_FILE" CHATTERBOX_DEVICE cpu
  ensure_kv "$VOICE_ENV_FILE" CHATTERBOX_GTT_DELTA_MB "${gtt:-unknown}"
  ensure_kv "$VOICE_ENV_FILE" CHATTERBOX_ARBITER_KEY "$CHATTERBOX_ARBITER_KEY"
  chown root:atlas "$VOICE_ENV_FILE"; chmod 640 "$VOICE_ENV_FILE"
  log "wrote $VOICE_ENV_FILE (CHATTERBOX_FOOTPRINT_BYTES=$fp, CHATTERBOX_GTT_DELTA_MB=${gtt:-unknown})"
}

_voice_assert_owners() {
  local stray
  stray="$(find "$VOICE_HF_HOME" ! -user atlas 2>/dev/null | head -n 5 || true)"
  [[ -z "$stray" ]] || die "entries under $VOICE_HF_HOME are not owned by atlas (CONVENTIONS.md §2; every voice download runs as atlas): $(tr '\n' ' ' <<<"$stray")"
  log "hf cache $VOICE_HF_HOME: every entry owned by atlas"
  stray="$(find "$VOICE_VENV" "$PYANNOTE_VENV" ! -type l \( ! -user root -o -perm /022 \) 2>/dev/null | head -n 5 || true)"
  [[ -z "$stray" ]] || die "entries under $VOICE_VENV / $PYANNOTE_VENV are not root-owned read-only (Section 16.3 item 6): $(tr '\n' ' ' <<<"$stray")"
  log "venvs $VOICE_VENV, $PYANNOTE_VENV: root-owned, read-only for atlas"
}

step_05() {
  _voice_paths
  _voice_apt
  _voice_dirs
  _voice_env_init
  _voice_up
  _voice_kokoro_check
  _voice_speaches_model
  _voice_docling_check
  _voice_uv
  _voice_venv_chatterbox
  _voice_chatterbox_measure
  _voice_venv_pyannote
  _voice_write_env
  _voice_arbiter_register_chatterbox
  _voice_render_v7
  _voice_arbiter_remeasure
  # V6: recorded pass/fail, never fatal here (the Phase 2 gate blocks on a fail; the message names the licence URLs).
  run_verify V6 v06-pyannote.sh "$PYANNOTE_VENV" "$VOICE_HF_HOME" "$PYANNOTE_PIPELINE" \
    || warn "V6 recorded as fail: accept the PyAnnote licences named in the verify table with the HF_TOKEN account, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 05"
  # V7: pass ("rendered N files; Principal to listen") or deferred (reference recordings absent), per Section 17/22.
  run_verify V7 v07-voice-listen.sh "$LISTENING_DIR/v7-listening-test.json" \
    || warn "V7 recorded as fail: Kokoro or the Chatterbox clone did not render (see the verify table)"
  _voice_assert_owners
  notify "Phase 2 step 5 done: Kokoro, Whisper turbo, Docling, Chatterbox, PyAnnote (V6/V7 recorded)"
  log "step 05 done: listening test in $LISTENING_DIR (open it from the XFCE desktop)"
}
