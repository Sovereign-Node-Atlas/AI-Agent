#!/usr/bin/env bash
# phase2/05-voice.sh — Section 17 Phase 2 step 5: Kokoro, Chatterbox, Whisper Large-v3-Turbo, PyAnnote 3.1 (Sections
# 14.1, 14.3, 21 V6/V7). Sourced by phase2-services.sh through run_phase_steps; defines step_05 only.
#
# Order inside the step (each part idempotent; a re-run after a failure resumes cheaply):
#   1. Kokoro-FastAPI (CPU image), speaches (faster-whisper large-v3-turbo, CPU int8) and docling-serve from
#      docker/core/compose.voice.yml merged into the core compose project; Kokoro voice list checked against the
#      Section 14.3 shortlist; one timed sentence; the turbo model pulled once, then speaches restarted with
#      PRELOAD_MODELS + HF_HUB_OFFLINE=1; a Kokoro->Whisper round trip proves STT.
#   2. Chatterbox 0.1.7 into its own venv $ATLAS_OPT/venv-voice (CPU torch 2.6.0 from download.pytorch.org/whl/cpu),
#      one sentence rendered and its wall-clock seconds recorded (cloning is an offline job; speed is not a gate).
#   3. PyAnnote 4.0.7 into $ATLAS_OPT/venv-pyannote (see WHY TWO VENVS below).
#   4. V6 (verify/v06-pyannote.sh): token reaches both gated repos, one diarisation on a generated 10-s WAV, online
#      then offline. V7 (verify/v07-voice-listen.sh -> phase2/voice_render.py): the listening test.
#   5. $ATLAS_ETC/voice.env written for the orchestrator and for compose (contract below).
#
# WHY TWO VENVS (the task asked for PyAnnote "into that venv"): chatterbox-tts 0.1.7 pins torch==2.6.0 for Python
# < 3.14 (voice-stt.md §3.1 VERIFIED pyproject) while pyannote.audio 4.0.7 requires torch>=2.8.0 (§5.1 VERIFIED).
# One venv cannot satisfy both; pip would fail at resolution. So: venv-voice (Chatterbox) and venv-pyannote.
# Both use a uv-managed CPython 3.11: the host python3 is 3.14 (§1), Chatterbox switches to an untested
# torch>=2.9 pin on 3.14 and its README says "developed/tested on 3.11".
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * docker/core/compose.yml (core services writer) defines the compose network `atlas` and runs without a
#     --project-name; this step merges docker/core/compose.voice.yml into that project (header of that file).
#   * $ATLAS_ETC/docker.env (Phase 1 step 6): LAN_IP, ATLAS_UID, ATLAS_GID used by compose interpolation.
#   * $ATLAS_ETC/secrets/hf-token.env (phase2-services.sh prompt): HF_TOKEN for the gated PyAnnote repos.
#   * config/voice-casting.json (content writer): personas[].key/kokoro_primary/kokoro_alternate, reference_recordings.
#   * config/allowlist.txt: ghcr.io, github.com (+ release/objects hosts: Chatterbox's resemble-perth git dependency,
#     uv's CPython download), huggingface.co/.hf.co, pypi.org, files.pythonhosted.org, download.pytorch.org.
# Contract this file defines for others:
#   * $ATLAS_ETC/voice.env (root:atlas 640, also a compose --env-file): SPEACHES_PRELOAD_MODELS, SPEACHES_HF_HUB_OFFLINE,
#     KOKORO_URL=http://127.0.0.1:8880/v1, SPEACHES_URL=http://127.0.0.1:8881/v1, STT_MODEL=<registry id>,
#     DOCLING_URL=http://127.0.0.1:5001, VOICE_VENV=/opt/atlas/venv-voice, PYANNOTE_VENV=/opt/atlas/venv-pyannote,
#     PYANNOTE_PIPELINE, HF_HOME=$ATLAS_SRV/engines/hf, LISTENING_TEST_DIR=$ATLAS_SRV/staging/listening-test.
#   * Open WebUI (step 3's writer) points AUDIO_TTS_OPENAI_API_BASE_URL at http://kokoro:8880/v1 (model "kokoro") and
#     AUDIO_STT_OPENAI_API_BASE_URL at http://speaches:8000/v1 with AUDIO_STT_MODEL=whisper-1 (aliased below).

KOKORO_IMAGE_TAG="v0.9.0"                      # voice-stt.md §2.1 VERIFIED (2026-09-10); pinned in compose.voice.yml
SPEACHES_IMAGE_TAG="0.8.3-cpu"                 # voice-stt.md §4.1 VERIFIED; pinned in compose.voice.yml
SPEACHES_TURBO_PREFERRED="mobiuslabsgmbh/faster-whisper-large-v3-turbo"   # faster-whisper's own mapping (§4.1 VERIFIED)
CHATTERBOX_PIN="chatterbox-tts==0.1.7"         # voice-stt.md §3.1 VERIFIED (PyPI 2026-03-26, MIT)
CHATTERBOX_TORCH_PIN="2.6.0"                   # chatterbox pyproject for python < 3.14 (§3.1 VERIFIED)
PYANNOTE_PIN="pyannote.audio==4.0.7"           # voice-stt.md §5.1 VERIFIED (PyPI 2026-06-30)
PYANNOTE_TORCH_PIN="2.8.0"                     # pyannote needs torch>=2.8.0 (§5.1 VERIFIED); 2.8.0 is the floor
PYANNOTE_PIPELINE="pyannote/speaker-diarization-3.1"   # Section 14.1; voice-stt.md §5.2
VOICE_PYTHON="3.11"                            # voice-stt.md §1 recommendation (3.11/3.12), Chatterbox README 3.11
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
KOKORO_URL="http://127.0.0.1:8880"
SPEACHES_URL="http://127.0.0.1:8881"
DOCLING_URL="http://127.0.0.1:5001"

VOICE_VENV=""
PYANNOTE_VENV=""
VOICE_HF_HOME=""
VOICE_ENV_FILE=""
UV_BIN=""
SPEACHES_MODEL_ID=""

_voice_paths() {
  VOICE_VENV="$ATLAS_OPT/venv-voice"
  PYANNOTE_VENV="$ATLAS_OPT/venv-pyannote"
  VOICE_HF_HOME="$ATLAS_SRV/engines/hf"        # same cache 04-memory.sh uses (HF_HOME contract in memory.env)
  VOICE_ENV_FILE="$ATLAS_ETC/voice.env"
}

_voice_apt() {
  # ffmpeg 7:8.0.1 and sox VERIFIED in resolute (voice-stt.md §1); ffmpeg is torchcodec's decoder for PyAnnote.
  apt_install python3-venv python3-pip ffmpeg sox curl jq unzip
}

_voice_dirs() {
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/engines/voice" atlas:atlas 755
  ensure_dir "$VOICE_HF_HOME" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/staging" atlas:atlas 755
  # The Principal listens from the XFCE desktop: owner is the login user, group atlas, setgid.
  ensure_dir "$ATLAS_SRV/staging/listening-test" "$PRINCIPAL_USER:atlas" 2775
  ensure_dir "$ATLAS_SRV/staging/inbox" "$PRINCIPAL_USER:atlas" 2770
  ensure_dir "$ATLAS_SRV/staging/inbox/voice-references" "$PRINCIPAL_USER:atlas" 2770
  # speaches model_aliases.json (voice-stt.md §4.1 VERIFIED mount path): whisper-1 -> the turbo id, so Open WebUI's
  # AUDIO_STT_MODEL=whisper-1 stays stable. Rewritten once the registry id is known (see _voice_speaches_model).
  local aliases="$ATLAS_SRV/engines/voice/model_aliases.json"
  if [[ ! -s "$aliases" ]]; then
    printf '{ "whisper-1": "%s", "tts-1": "speaches-ai/Kokoro-82M-v1.0-ONNX", "tts-1-hd": "speaches-ai/Kokoro-82M-v1.0-ONNX" }\n' \
      "$SPEACHES_TURBO_PREFERRED" >"$aliases"
    chown atlas:atlas "$aliases"; chmod 644 "$aliases"
  fi
}

_voice_env_init() {
  if [[ ! -e "$VOICE_ENV_FILE" ]]; then
    { echo "# /etc/atlas/voice.env — written by phase2/05-voice.sh (contract in that file's header). Not a secret."; } >"$VOICE_ENV_FILE"
  fi
  # First start online: the registry lookup and the model download need huggingface.co once.
  grep -q '^SPEACHES_PRELOAD_MODELS=' "$VOICE_ENV_FILE" || ensure_kv "$VOICE_ENV_FILE" SPEACHES_PRELOAD_MODELS '[]'
  grep -q '^SPEACHES_HF_HUB_OFFLINE=' "$VOICE_ENV_FILE" || ensure_kv "$VOICE_ENV_FILE" SPEACHES_HF_HUB_OFFLINE 0
  chown root:atlas "$VOICE_ENV_FILE"; chmod 640 "$VOICE_ENV_FILE"
}

# _voice_compose ARGS... — the merged core + voice project (contract in docker/core/compose.voice.yml).
_voice_compose() {
  local core="$ATLAS_DAY1_DIR/docker/core/compose.yml" voice="$ATLAS_DAY1_DIR/docker/core/compose.voice.yml"
  [[ -f "$core" ]] || die "$core is missing (the core services writer's file; compose.voice.yml is merged into it)"
  [[ -f "$voice" ]] || die "$voice is missing"
  [[ -s "$ATLAS_ETC/docker.env" ]] || die "$ATLAS_ETC/docker.env missing (Phase 1 step 6)"
  docker compose -f "$core" -f "$voice" --env-file "$ATLAS_ETC/docker.env" --env-file "$VOICE_ENV_FILE" "$@"
}

_voice_up() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  proxy_env
  log "docker compose up -d kokoro speaches docling (images kokoro-fastapi-cpu:$KOKORO_IMAGE_TAG, speaches:$SPEACHES_IMAGE_TAG, docling-serve-cpu:v1.34.0; pulls go through the proxy)"
  retry 3 _voice_compose up -d --quiet-pull kokoro speaches docling \
    || die "docker compose up for the voice services failed: $(_voice_compose logs --tail 20 kokoro speaches docling 2>&1 | tail -n 30)"
}

_voice_kokoro_check() {
  wait_http "$KOKORO_URL/health" 180 || die "Kokoro did not answer 200 on $KOKORO_URL/health within 180 s: docker logs kokoro"
  # 14.3 shortlist must exist in the image's voice list (GET /v1/audio/voices -> {"voices":[{"id",...}]}, VERIFIED).
  local voices
  voices="$(curl -fsS --noproxy '*' --max-time 30 "$KOKORO_URL/v1/audio/voices")" || die "GET $KOKORO_URL/v1/audio/voices failed"
  python3 - "$voices" "$ATLAS_DAY1_DIR/config/voice-casting.json" <<'PY' || die "Kokoro's voice list lacks Section 14.3 presets (see above); the image tag is wrong or the voice packs changed"
import json, re, sys
voices = json.loads(sys.argv[1])["voices"]
ids = {v["id"] if isinstance(v, dict) else str(v) for v in voices}
canon = sorted(i for i in ids if re.fullmatch(r"[a-z]{2}_[a-z]+", i) and "_v0" not in i and not i.endswith("_inno"))
need = set()
for p in json.load(open(sys.argv[2], encoding="utf-8"))["personas"]:
    for k in ("kokoro_primary", "kokoro_alternate"):
        if p.get(k):
            need.add(p[k])
missing = sorted(need - ids)
print(f"kokoro: {len(canon)} canonical presets; shortlist {len(need)} voices, missing {missing}")
sys.exit(1 if missing else 0)
PY
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

_voice_speaches_model() {
  wait_http "$SPEACHES_URL/health" 180 || die "speaches did not answer 200 on $SPEACHES_URL/health within 180 s: docker logs speaches"
  local installed
  installed="$(curl -fsS --noproxy '*' --max-time 30 "$SPEACHES_URL/v1/models" 2>/dev/null || echo '{}')"
  SPEACHES_MODEL_ID="$(python3 -c '
import json, sys
ids = [m["id"] for m in json.loads(sys.argv[1]).get("data", [])]
pick = [i for i in ids if "large-v3-turbo" in i]
print(pick[0] if pick else "")' "$installed")"
  if [[ -z "$SPEACHES_MODEL_ID" ]]; then
    # UNVERIFIED (voice-stt.md §4.1): which turbo id speaches' registry offers (it is built from HF tags). The preferred
    # id is faster-whisper's own mapping; any other id containing large-v3-turbo is accepted; nothing else is (the
    # baseline names Large-v3-Turbo; large-v3 would be a silent substitution).
    local registry
    registry="$(curl -fsS --noproxy '*' --max-time 120 "$SPEACHES_URL/v1/registry?task=automatic-speech-recognition")" \
      || die "GET $SPEACHES_URL/v1/registry failed (the registry needs huggingface.co through the container proxy on the first start)"
    SPEACHES_MODEL_ID="$(python3 -c '
import json, sys
ids = [m["id"] for m in json.loads(sys.argv[1]).get("data", [])]
pref = sys.argv[2]
if pref in ids:
    print(pref)
else:
    pick = [i for i in ids if "large-v3-turbo" in i]
    print(pick[0] if pick else "")' "$registry" "$SPEACHES_TURBO_PREFERRED")"
    [[ -n "$SPEACHES_MODEL_ID" ]] || die "speaches' registry lists no large-v3-turbo model (looked for $SPEACHES_TURBO_PREFERRED); the whisper.cpp path (voice-stt.md §4.2) is the alternative, not automated here"
    log "speaches: downloading $SPEACHES_MODEL_ID (POST /v1/models/<id>; ~1.6 GB through the proxy, several minutes)"
    retry 3 curl -fsS --noproxy '*' --max-time 3600 -X POST "$SPEACHES_URL/v1/models/$SPEACHES_MODEL_ID" -o /dev/null \
      || die "speaches could not download $SPEACHES_MODEL_ID (huggingface.co via the container proxy; docker logs speaches)"
  fi
  installed="$(curl -fsS --noproxy '*' --max-time 30 "$SPEACHES_URL/v1/models")"
  grep -qF "\"$SPEACHES_MODEL_ID\"" <<<"$installed" || die "speaches does not list $SPEACHES_MODEL_ID after the download: $installed"
  # Alias whisper-1 -> the real id (mounted read-only; picked up on the restart below).
  printf '{ "whisper-1": "%s", "tts-1": "speaches-ai/Kokoro-82M-v1.0-ONNX", "tts-1-hd": "speaches-ai/Kokoro-82M-v1.0-ONNX" }\n' \
    "$SPEACHES_MODEL_ID" >"$ATLAS_SRV/engines/voice/model_aliases.json"
  # From now on: preload the model at start and never touch the network (Section 12.5; PRELOAD_MODELS VERIFIED).
  ensure_kv "$VOICE_ENV_FILE" SPEACHES_PRELOAD_MODELS "[\"$SPEACHES_MODEL_ID\"]"
  ensure_kv "$VOICE_ENV_FILE" SPEACHES_HF_HUB_OFFLINE 1
  ensure_kv "$VOICE_ENV_FILE" STT_MODEL "$SPEACHES_MODEL_ID"
  log "speaches: restarting with PRELOAD_MODELS=[\"$SPEACHES_MODEL_ID\"] and HF_HUB_OFFLINE=1"
  _voice_compose up -d --no-deps speaches || die "docker compose up speaches (offline restart) failed"
  wait_http "$SPEACHES_URL/health" 300 || die "speaches did not come back after the offline restart (PRELOAD_MODELS makes it exit if the model is missing): docker logs speaches"
  # Round trip: Kokoro speaks, Whisper listens (voice-stt.md §8.4).
  local wav resp text t0 t1 secs
  wav="$(mktemp --suffix=.wav)"
  curl -fsS --noproxy '*' --max-time 300 "$KOKORO_URL/v1/audio/speech" -H 'Content-Type: application/json' \
    -d '{"model":"kokoro","input":"The quick brown fox jumps over the lazy dog.","voice":"af_heart","response_format":"wav","stream":false}' \
    -o "$wav" || { rm -f "$wav"; die "Kokoro render for the STT round trip failed"; }
  t0="$(date +%s.%N)"
  resp="$(curl -fsS --noproxy '*' --max-time 600 "$SPEACHES_URL/v1/audio/transcriptions" -F "file=@$wav" -F "model=$SPEACHES_MODEL_ID" -F "language=en")" \
    || { rm -f "$wav"; die "POST $SPEACHES_URL/v1/audio/transcriptions failed (docker logs speaches)"; }
  t1="$(date +%s.%N)"
  rm -f "$wav"
  secs="$(python3 -c 'import sys; print(round(float(sys.argv[2])-float(sys.argv[1]),2))' "$t0" "$t1")"
  text="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("text",""))' "$resp" 2>/dev/null || true)"
  grep -qi 'quick brown fox' <<<"$text" || die "Whisper did not transcribe the round-trip sentence (got: '${text:-$resp}')"
  log "speaches: $SPEACHES_MODEL_ID transcribed the round trip in ${secs}s: '$text'"
}

_voice_docling_check() {
  # /docs is the documented path (services-tools.md §2.3); the compose healthcheck uses the same UNVERIFIED-free path.
  wait_http "$DOCLING_URL/docs" 300 || die "docling-serve did not answer 200 on $DOCLING_URL/docs within 300 s: docker logs docling"
  log "docling-serve up at $DOCLING_URL (Open WebUI: DOCLING_SERVER_URL=http://docling:5001)"
}

# --- uv-managed CPython 3.11 venvs ------------------------------------------------------------------------------------
_voice_uv() {
  local bv="$ATLAS_OPT/venv-uv"
  if [[ ! -x "$bv/bin/uv" ]]; then
    mkdir -p "$ATLAS_OPT"
    python3 -m venv "$bv" || die "python3 -m venv $bv failed"
    proxy_env
    # UNVERIFIED: uv version — the research gives no pin for uv itself; installed unpinned from PyPI (README notes it).
    retry 3 "$bv/bin/python" -m pip install --quiet --disable-pip-version-check uv || die "pip install uv into $bv failed"
  fi
  UV_BIN="$bv/bin/uv"
  export UV_PYTHON_INSTALL_DIR="$ATLAS_OPT/python" UV_CACHE_DIR="$ATLAS_STATE/uv-cache" UV_HTTP_TIMEOUT=600
  mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"
  proxy_env
  # UNVERIFIED: uv fetches python-build-standalone from github.com release assets (allowlisted). A failure stops here.
  if ! "$UV_BIN" python find "$VOICE_PYTHON" >/dev/null 2>&1; then
    log "uv: installing a managed CPython $VOICE_PYTHON under $UV_PYTHON_INSTALL_DIR"
    retry 3 "$UV_BIN" python install "$VOICE_PYTHON" || die "uv python install $VOICE_PYTHON failed (github.com release assets through the proxy?)"
  fi
  log "uv: $("$UV_BIN" --version 2>&1) with CPython $VOICE_PYTHON"
}

# _voice_venv_make DIR — create DIR with the managed CPython when absent.
_voice_venv_make() {
  local dir="$1"
  if [[ ! -x "$dir/bin/python" ]]; then
    "$UV_BIN" venv --python "$VOICE_PYTHON" "$dir" || die "uv venv $dir failed"
  fi
  "$dir/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version' \
    || die "$dir is not a Python 3.11 venv (delete it and re-run this step)"
}

# _voice_pip DIR ARGS... — uv pip install into DIR through the proxy.
_voice_pip() {
  local dir="$1"; shift
  proxy_env
  retry 3 "$UV_BIN" pip install --quiet --python "$dir/bin/python" "$@"
}

_voice_venv_chatterbox() {
  _voice_venv_make "$VOICE_VENV"
  if ! "$VOICE_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("chatterbox-tts") == "0.1.7"' 2>/dev/null; then
    # UNVERIFIED (voice-stt.md §3.1): the CPU wheel for torch==2.6.0 cp311 on download.pytorch.org (index was blocked
    # during research; it is the standard CPU index). Installed FIRST so chatterbox's pin resolves to the CPU build
    # instead of pulling ~3 GB of CUDA libraries.
    log "venv-voice: torch==$CHATTERBOX_TORCH_PIN (CPU index), then $CHATTERBOX_PIN (needs github.com: resemble-perth git dependency)"
    _voice_pip "$VOICE_VENV" --index-url "$TORCH_CPU_INDEX" "torch==$CHATTERBOX_TORCH_PIN" "torchaudio==$CHATTERBOX_TORCH_PIN" \
      || die "CPU torch $CHATTERBOX_TORCH_PIN could not be installed into $VOICE_VENV from $TORCH_CPU_INDEX"
    apt_install git
    _voice_pip "$VOICE_VENV" "$CHATTERBOX_PIN" \
      || die "pip install $CHATTERBOX_PIN failed in $VOICE_VENV (resemble-perth is a git+https dependency on github.com)"
  fi
  "$VOICE_VENV/bin/python" -c 'import chatterbox, torch; assert not torch.cuda.is_available(); print("chatterbox ok, torch", torch.__version__)' \
    || die "chatterbox does not import from $VOICE_VENV"
  chown -R atlas:atlas "$VOICE_VENV"
}

_voice_chatterbox_measure() {
  # One sentence with the English base model (built-in conditioning, no reference clip); ResembleAI/chatterbox is
  # pulled from Hugging Face on the first call (voice-stt.md §3.2 VERIFIED file list). The seconds are RECORDED, not
  # gated: cloning is an offline job (Section 4.2 lists Chatterbox under the Arbiter, on demand).
  local out="$ATLAS_STATE/voice-chatterbox.json"
  if [[ -s "$out" ]]; then
    log "chatterbox: measurement already recorded in $out: $(tr -d '\n' <"$out")"
    return 0
  fi
  proxy_env
  log "chatterbox: rendering one sentence on CPU (first run downloads ResembleAI/chatterbox into $VOICE_HF_HOME)"
  HF_HOME="$VOICE_HF_HOME" HF_HUB_ENABLE_HF_TRANSFER=0 OMP_NUM_THREADS="$(nproc)" \
    "$VOICE_VENV/bin/python" - "$out" "$ATLAS_SRV/engines/voice/chatterbox-sample.wav" <<'PY' || die "the Chatterbox one-sentence render failed (see above; huggingface.co through the proxy?)"
import json, sys, time, wave
import numpy as np
from chatterbox.tts import ChatterboxTTS
out, sample = sys.argv[1], sys.argv[2]
t0 = time.monotonic()
model = ChatterboxTTS.from_pretrained(device="cpu")
load_s = round(time.monotonic() - t0, 1)
text = "Good morning. The overnight audit finished without exceptions, and nothing needs your attention today."
t1 = time.monotonic()
wav = model.generate(text)
gen_s = round(time.monotonic() - t1, 1)
pcm = np.clip(wav.detach().cpu()[0].numpy(), -1.0, 1.0)
pcm16 = (pcm * 32767.0).astype("<i2")
with wave.open(sample, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(model.sr)); w.writeframes(pcm16.tobytes())
audio_s = round(len(pcm16) / float(model.sr), 2)
rec = {"model": "ResembleAI/chatterbox", "device": "cpu", "load_s": load_s, "generate_s": gen_s,
       "audio_s": audio_s, "rtf": round(gen_s / audio_s, 2) if audio_s else None, "sentence": text,
       "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(rec, fh)
    fh.write("\n")
print(f"chatterbox: load {load_s}s, one sentence {gen_s}s for {audio_s}s of audio (RTF {rec['rtf']})")
PY
  chown atlas:atlas "$ATLAS_SRV/engines/voice/chatterbox-sample.wav" 2>/dev/null || true
  log "chatterbox: measurement recorded in $out (cloning speed is not a gate)"
}

_voice_venv_pyannote() {
  _voice_venv_make "$PYANNOTE_VENV"
  if ! "$PYANNOTE_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("pyannote.audio") == "4.0.7"' 2>/dev/null; then
    # UNVERIFIED: CPU wheels for torch/torchaudio 2.8.0 and torchcodec (>=0.7.0 per pyannote's pyproject) on the CPU
    # index; the research's own container recipe installs them from that index unpinned (voice-stt.md §8.7).
    log "venv-pyannote: torch==$PYANNOTE_TORCH_PIN torchaudio torchcodec (CPU index), then $PYANNOTE_PIN"
    _voice_pip "$PYANNOTE_VENV" --index-url "$TORCH_CPU_INDEX" "torch==$PYANNOTE_TORCH_PIN" "torchaudio==$PYANNOTE_TORCH_PIN" "torchcodec>=0.7.0" \
      || die "CPU torch $PYANNOTE_TORCH_PIN/torchaudio/torchcodec could not be installed into $PYANNOTE_VENV from $TORCH_CPU_INDEX"
    _voice_pip "$PYANNOTE_VENV" "$PYANNOTE_PIN" || die "pip install $PYANNOTE_PIN failed in $PYANNOTE_VENV"
  fi
  "$PYANNOTE_VENV/bin/python" -c 'import pyannote.audio, torch; print("pyannote.audio", pyannote.audio.__version__, "torch", torch.__version__)' \
    || die "pyannote.audio does not import from $PYANNOTE_VENV"
  chown -R atlas:atlas "$PYANNOTE_VENV"
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
  ensure_kv "$VOICE_ENV_FILE" LISTENING_TEST_DIR "$ATLAS_SRV/staging/listening-test"
  chown root:atlas "$VOICE_ENV_FILE"; chmod 640 "$VOICE_ENV_FILE"
  log "wrote $VOICE_ENV_FILE"
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
  # V6: recorded pass/fail, never fatal here (the Phase 2 gate blocks on a fail; the message names the licence URLs).
  run_verify V6 v06-pyannote.sh "$PYANNOTE_VENV" "$VOICE_HF_HOME" "$PYANNOTE_PIPELINE" \
    || warn "V6 recorded as fail: accept the PyAnnote licences named in the verify table with the HF_TOKEN account, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 05"
  # V7: pass ("rendered N files; Principal to listen") or deferred (reference recordings absent), per Section 17/22.
  run_verify V7 v07-voice-listen.sh \
    || warn "V7 recorded as fail: Kokoro or the Chatterbox clone did not render (see the verify table)"
  chown -R "$PRINCIPAL_USER:atlas" "$ATLAS_SRV/staging/listening-test" 2>/dev/null || true
  notify "Phase 2 step 5 done: Kokoro, Whisper turbo, Docling, Chatterbox, PyAnnote (V6/V7 recorded)"
  log "step 05 done: listening test in $ATLAS_SRV/staging/listening-test (open it from the XFCE desktop)"
}
