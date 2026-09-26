#!/usr/bin/env bash
# verify/v06-pyannote.sh — V6: PyAnnote 3.1 gated model accepted and loading (Sections 14.1, 21; Phase 2 step 5).
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; exactly one line on stdout; never prompts;
# safe to re-run; well under 10 minutes (two ~30 MB model files, a 10-second CPU diarisation, twice).
#
#   1. The HF token (secrets/hf-token.env) must get HTTP 200 on a file HEAD of BOTH gated repos the 3.1 pipeline
#      needs (voice-stt.md §5.2): pyannote/speaker-diarization-3.1 (config.yaml) and pyannote/segmentation-3.0
#      (pytorch_model.bin). 401/403 = licence not accepted -> the two URLs the Principal must visit; exit 1.
#   2. A 10-second 16 kHz mono WAV is generated (two synthetic tones with a gap) and diarised once ONLINE (downloads
#      into HF_HOME) and once with HF_HUB_OFFLINE=1 (proves the cache loads without network, Section 12.5).
#   3. If pyannote.audio 4.0.7 cannot load the legacy 3.1 pipeline (UNVERIFIED, voice-stt.md §6 conflict 1) and the
#      token has access to pyannote/speaker-diarization-community-1, that pipeline is loaded instead and the message
#      says so loudly; otherwise fail.
# Usage: v06-pyannote.sh [VENV=/opt/atlas/venv-pyannote] [HF_HOME=/srv/atlas/engines/hf] [PIPELINE=pyannote/speaker-diarization-3.1]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

venv="${1:-$ATLAS_OPT/venv-pyannote}"
hf_home="${2:-$ATLAS_SRV/engines/hf}"
pipeline="${3:-pyannote/speaker-diarization-3.1}"
tokf="$ATLAS_ETC/secrets/hf-token.env"

[[ -x "$venv/bin/python" ]] || { echo "V6 fail: $venv/bin/python does not exist (phase2/05-voice.sh builds it)"; exit 1; }
HF_TOKEN=""
if [[ -r "$tokf" ]]; then
  # shellcheck disable=SC1090  # secret file, HF_TOKEN=... (CONVENTIONS.md §2)
  source "$tokf"
fi
[[ -n "$HF_TOKEN" ]] || { echo "V6 fail: HF_TOKEN empty or $tokf unreadable (phase2-services.sh prompts for it)"; exit 1; }
proxy_env
base="${HF_ENDPOINT:-https://huggingface.co}"

# 1. File HEAD on both gated repos. LFS files answer with a redirect to the CDN, so -L is followed; X-Error-Code
#    (GatedRepo) is reported when present (huggingface_hub semantics, voice-stt.md §5.3 VERIFIED).
head_code() {
  local url="$1" hdr code err
  hdr="$(mktemp)"
  code="$(curl -sS -I -L --max-time 60 -o /dev/null -D "$hdr" -w '%{http_code}' -H "Authorization: Bearer $HF_TOKEN" "$url" 2>/dev/null || echo 000)"
  err="$(grep -i '^x-error-code:' "$hdr" | tail -n1 | tr -d '\r' | awk '{print $2}')"
  rm -f "$hdr"
  printf '%s%s\n' "$code" "${err:+/$err}"
}
r31="$(head_code "$base/pyannote/speaker-diarization-3.1/resolve/main/config.yaml")"
rseg="$(head_code "$base/pyannote/segmentation-3.0/resolve/main/pytorch_model.bin")"
blocked=()
[[ "$r31" == 200* ]] || blocked+=("https://huggingface.co/pyannote/speaker-diarization-3.1 (HTTP $r31)")
[[ "$rseg" == 200* ]] || blocked+=("https://huggingface.co/pyannote/segmentation-3.0 (HTTP $rseg)")
if (( ${#blocked[@]} > 0 )); then
  case "$r31$rseg" in
    *401*|*403*) echo "V6 fail: licence not accepted for ${blocked[*]}: visit each URL with the account that owns HF_TOKEN in $tokf, click 'Agree and access repository', then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 05"; exit 1 ;;
    *) echo "V6 fail: huggingface.co unreachable or the token is invalid: ${blocked[*]} (proxy up? huggingface.co and .hf.co allowlisted?)"; exit 1 ;;
  esac
fi

# 2. Generated 10-second two-tone WAV (16 kHz mono; the 3.1 card wants mono 16 kHz).
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
python3 - "$work/test16k.wav" <<'PY' || { echo "V6 fail: could not generate the test WAV"; exit 1; }
import math, struct, sys, wave
sr, secs = 16000, 10
frames = bytearray()
for i in range(sr * secs):
    t = i / sr
    if t < 4.5:
        f, am = 180.0, 4.0        # "speaker A": low fundamental, 4 Hz amplitude modulation
    elif t < 5.0:
        f, am = 0.0, 0.0          # gap
    else:
        f, am = 320.0, 6.0        # "speaker B": higher fundamental
    if f:
        v = sum(math.sin(2 * math.pi * f * k * t) / k for k in (1, 2, 3, 4)) * (0.6 + 0.4 * math.sin(2 * math.pi * am * t))
        v *= 0.25
    else:
        v = 0.0
    frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(bytes(frames))
PY

diarise() {   # diarise PIPELINE OFFLINE(0|1) -> JSON on stdout; exit 3 = pipeline failed to load, 1 = run failed
  local pipe="$1" offline="$2"
  HF_HOME="$hf_home" HF_HUB_OFFLINE="$offline" HF_HUB_ENABLE_HF_TRANSFER=0 PYANNOTE_METRICS_ENABLED=0 \
  OMP_NUM_THREADS="$(nproc)" HF_TOKEN="$HF_TOKEN" \
    "$venv/bin/python" - "$pipe" "$work/test16k.wav" <<'PY'
import json, os, sys, time
import torch
from pyannote.audio import Pipeline
name, wav = sys.argv[1], sys.argv[2]
tok = os.environ.get("HF_TOKEN") or None
t0 = time.monotonic()
try:
    pipe = Pipeline.from_pretrained(name, token=tok)   # 4.x keyword is `token` (voice-stt.md §5.1 VERIFIED)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": f"load: {type(exc).__name__}: {exc}"}))
    sys.exit(3)
if pipe is None:
    print(json.dumps({"error": "load: from_pretrained returned None (gated repo not accepted?)"}))
    sys.exit(3)
pipe.to(torch.device("cpu"))
load_s = round(time.monotonic() - t0, 1)
t1 = time.monotonic()
try:
    out = pipe(wav)
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": f"run: {type(exc).__name__}: {exc}", "load_s": load_s}))
    sys.exit(1)
diar = getattr(out, "speaker_diarization", out)   # community-1 vs 3.1 output shapes
speakers = sorted({spk for _, _, spk in diar.itertracks(yield_label=True)})
print(json.dumps({"pipeline": name, "load_s": load_s, "run_s": round(time.monotonic() - t1, 1),
                  "speakers": len(speakers), "offline": os.environ.get("HF_HUB_OFFLINE") == "1"}))
PY
}

used="$pipeline"
online="$(diarise "$pipeline" 0)" && rc=0 || rc=$?
if (( rc == 3 )); then
  # 3. Loud fallback: the community-1 pipeline, only when the token already has access to it.
  alt="pyannote/speaker-diarization-community-1"
  ralt="$(head_code "$base/$alt/resolve/main/config.yaml")"
  if [[ "$ralt" == 200* ]]; then
    warn "V6: $pipeline failed to load under pyannote.audio 4.0.7 ($online); trying $alt"
    used="$alt"
    online="$(diarise "$alt" 0)" && rc=0 || rc=$?
  else
    echo "V6 fail: $pipeline did not load: $(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("error","?"))' "$online" 2>/dev/null || echo "$online"); the community-1 fallback is not accepted either (HTTP $ralt at https://huggingface.co/$alt)"
    exit 1
  fi
fi
if (( rc != 0 )); then
  echo "V6 fail: diarisation with $used failed: $(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("error","?"))' "$online" 2>/dev/null || echo "$online")"
  exit 1
fi
offline="$(diarise "$used" 1)" || { echo "V6 fail: $used loaded online but not with HF_HUB_OFFLINE=1 from $hf_home: $offline"; exit 1; }

summary="$(python3 - "$online" "$offline" "$used" "$pipeline" "$r31" "$rseg" <<'PY'
import json, sys
on, off = json.loads(sys.argv[1]), json.loads(sys.argv[2])
used, wanted, r31, rseg = sys.argv[3:7]
note = "" if used == wanted else f" (FALLBACK: {wanted} failed to load under pyannote.audio 4.0.7, {used} used instead)"
print(f"{used} accepted (HEAD 3.1 config.yaml {r31}, segmentation-3.0 pytorch_model.bin {rseg}); loaded in {on['load_s']}s, "
      f"diarised 10 s of synthetic audio in {on['run_s']}s ({on['speakers']} speaker(s) found); offline reload {off['load_s']}s{note}")
PY
)"
echo "$summary"
exit 0
