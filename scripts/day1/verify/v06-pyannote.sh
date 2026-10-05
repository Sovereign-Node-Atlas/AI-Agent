#!/usr/bin/env bash
# verify/v06-pyannote.sh — V6: PyAnnote 3.1 gated model accepted and loading (Sections 14.1, 21; Phase 2 step 5).
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; exactly one line on stdout; never prompts;
# safe to re-run; well under 10 minutes (two ~30 MB model files, a 10-second CPU diarisation, twice).
#
#   1. The HF token (secrets/hf-token.env) must get HTTP 200 on a file HEAD of BOTH gated repos the 3.1 pipeline
#      needs (voice-stt.md §5.2): pyannote/speaker-diarization-3.1 (config.yaml) and pyannote/segmentation-3.0
#      (pytorch_model.bin). 401/403 = licence not accepted -> the two URLs the Principal must visit; exit 2 (deferred,
#      policy v0.3.3: a Principal input never fails a gate). No token at all -> exit 2 as well.
#      The bearer header is read by curl from a mode-600 file (-H @file), never placed on argv where /proc/<pid>/cmdline
#      shows it to every local account (fix round). The token file is PARSED (awk), never sourced: CONVENTIONS §2 makes
#      it atlas:atlas, and this script runs as root (fix round 2: no root execution of an atlas-writable file).
#   2. A 10-second 16 kHz mono WAV is generated (two synthetic tones with a gap) and diarised once ONLINE (downloads
#      into HF_HOME) and once with HF_HUB_OFFLINE=1 (proves the cache loads without network, Section 12.5). Both runs
#      execute as the atlas service account (runuser) when this script runs as root, so the HF cache entries and lock
#      files are owned by the account that uses them at run time (CONVENTIONS.md §2: /srv/atlas is atlas:atlas).
#   3. Section 21 defines V6 as "PyAnnote 3.1 ... loading" and Section 14.1 fixes 3.1. The pin is pyannote.audio 4.0.7
#      (voice-stt.md §5.1 VERIFIED: the release current in 2026, the only line receiving fixes; the research names no 3.x
#      release and calls 3.1 the "legacy" pipeline under 4.x, UNVERIFIED whether 4.0.7 loads it, §6 conflict 1). V6
#      PASSES ONLY WHEN 3.1 LOADS (fix round 6 restores the round-4 verdict; CONVENTIONS.md line 4: the document wins over
#      the research, and no Section 23 row amends 14.1/21). When the installed pyannote.audio REFUSES the 3.1 id (exit 3
#      below: a load error, not a run error and not a licence refusal) V6 is a FAIL whose message carries a DIAGNOSTIC:
#      whether the research's 4.x fallback (pyannote/speaker-diarization-community-1, gated too) loads under the same
#      install, so the Principal can decide between pinning a 3.1-loading release and amending 14.1/21 (a Section 23 row)
#      to name the community pipeline, after which phase2/05-voice.sh may switch PYANNOTE_PIPELINE; the scripts never
#      switch it themselves (Section 16.3 item 6). Neither loads -> FAIL too (never deferred; CONVENTIONS.md §7.4);
#      3.1's licence not accepted -> FAIL naming the URLs (Section 14.1 fixes 3.1, the fallback is not a way around it).
# Usage: v06-pyannote.sh [VENV=/opt/atlas/venv-pyannote] [HF_HOME=/srv/atlas/engines/hf] [PIPELINE=pyannote/speaker-diarization-3.1]
#                        [FALLBACK=pyannote/speaker-diarization-community-1   (diagnostic only, header item 3)]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

venv="${1:-$ATLAS_OPT/venv-pyannote}"
hf_home="${2:-$ATLAS_SRV/engines/hf}"
pipeline="${3:-pyannote/speaker-diarization-3.1}"
fallback="${4:-pyannote/speaker-diarization-community-1}"
tokf="$ATLAS_ETC/secrets/hf-token.env"
entry="${ATLAS_ENTRY:-./atlas-day1.sh}"

[[ -x "$venv/bin/python" ]] || { echo "V6 fail: $venv/bin/python does not exist (phase2/05-voice.sh builds it)"; exit 1; }
HF_TOKEN=""
if [[ -r "$tokf" ]]; then
  # Parsed, not sourced (header item 1): the file is atlas-owned per §2 and this runs as root.
  HF_TOKEN="$(awk -F= '$1=="HF_TOKEN" {sub(/^[^=]*=/, ""); gsub(/["'"'"' \t\r]/, ""); print; exit}' "$tokf")"
fi
[[ -n "$HF_TOKEN" ]] || { echo "V6 deferred: no Hugging Face token yet (to-do input-hf-token); PyAnnote diarisation waits for it"; exit 2; }
[[ "$HF_TOKEN" =~ ^hf_[A-Za-z0-9_]{20,}$ ]] || { echo "V6 fail: HF_TOKEN in $tokf does not look like a Hugging Face token (hf_...)"; exit 1; }
# The child processes (runuser -> env -> python) inherit it from the environment: never an argv element.
export HF_TOKEN
proxy_env
base="${HF_ENDPOINT:-https://huggingface.co}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# The header file is root-only (umask 077); the work dir itself is readable so the atlas runs below can open the WAV.
hdrf="$work/.auth"
(umask 077; printf 'Authorization: Bearer %s\n' "$HF_TOKEN" >"$hdrf")
chmod 755 "$work"

# 1. File HEAD on both gated repos. LFS files answer with a redirect to the CDN, so -L is followed; X-Error-Code
#    (GatedRepo) is reported when present (huggingface_hub semantics, voice-stt.md §5.3 VERIFIED).
head_code() {
  local url="$1" hdr code err
  hdr="$(mktemp)"
  code="$(curl -sS -I -L --max-time 60 -o /dev/null -D "$hdr" -w '%{http_code}' -H "@$hdrf" "$url" 2>/dev/null)" || code="${code:-000}"
  [[ "$code" =~ ^[0-9]{3}$ ]] || code=000
  # `|| true`: a normal 200 answer carries no X-Error-Code header, grep exits 1 and, under set -E, the ERR trap would
  # otherwise log a spurious "command failed" line twice per passing V6 (fix round 3; the function itself continued).
  err="$(grep -i '^x-error-code:' "$hdr" | tail -n1 | tr -d '\r' | awk '{print $2}' || true)"
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
    *401*|*403*) echo "V6 deferred: PyAnnote licence not accepted yet for ${blocked[*]} (to-do: visit each URL with the account that owns HF_TOKEN in $tokf, click 'Agree and access repository', then re-run: sudo $entry phase2 --force 05)"; exit 2 ;;
    *) echo "V6 fail: huggingface.co unreachable or the token is invalid: ${blocked[*]} (proxy up? huggingface.co and .hf.co allowlisted?)"; exit 1 ;;
  esac
fi

# 2. Generated 10-second two-tone WAV (16 kHz mono; the 3.1 card wants mono 16 kHz).
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
chmod 644 "$work/test16k.wav"

# as_runtime_user CMD... — the atlas service account when root (the account that owns $hf_home and runs PyAnnote later).
as_runtime_user() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    id -u "$ATLAS_SVC_USER" >/dev/null 2>&1 || { echo "V6 fail: service account $ATLAS_SVC_USER does not exist (Phase 1 step 3)"; exit 1; }
    runuser -u "$ATLAS_SVC_USER" -- "$@"
  else
    "$@"
  fi
}

diarise() {   # diarise PIPELINE OFFLINE(0|1) -> JSON on stdout; exit 3 = pipeline failed to load, 1 = run failed
  local pipe="$1" offline="$2"
  # HF_TOKEN is EXPORTED above and inherited by runuser (which keeps the caller's environment except HOME/SHELL/USER/
  # LOGNAME without -l/-m), env and python: it is never an argument of env(1), so no /proc/<pid>/cmdline ever shows it.
  as_runtime_user env HF_HOME="$hf_home" HF_HUB_OFFLINE="$offline" HF_HUB_ENABLE_HF_TRANSFER=0 \
    HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 PYANNOTE_METRICS_ENABLED=0 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" NO_PROXY="${NO_PROXY:-}" \
    OMP_NUM_THREADS="$(nproc)" \
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

err_of() { python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("error","?"))' "$1" 2>/dev/null || echo "$1"; }
pa_version="$(as_runtime_user "$venv/bin/python" -c 'import importlib.metadata as m; print(m.version("pyannote.audio"))' 2>/dev/null || echo '?')"

used="$pipeline"
note=""
online="$(diarise "$pipeline" 0)" && rc=0 || rc=$?
if (( rc == 3 )); then
  # 3. The installed pyannote.audio refused the 3.1 pipeline id: a FAIL (Sections 14.1 and 21 name 3.1; header item 3).
  #    The research's fallback is probed as a DIAGNOSTIC for the Principal's decision, never as the pass path.
  refusal="$(err_of "$online")"
  rfb="$(head_code "$base/$fallback/resolve/main/config.yaml")"
  if [[ "$rfb" != 200* ]]; then
    diag="the research's fallback $fallback is not reachable either (HTTP $rfb at https://huggingface.co/$fallback: its user conditions are not accepted with the HF_TOKEN account, or the host is unreachable)"
  elif fbout="$(diarise "$fallback" 0)"; then
    diag="DIAGNOSTIC: the research's fallback $fallback DOES load and diarise under the same install ($(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(f"load {d[\"load_s\"]}s, run {d[\"run_s\"]}s, {d[\"speakers\"]} speaker(s)")' "$fbout" 2>/dev/null || echo ok)); switching to it needs a Section 23 row amending 14.1/21 first (research conflict 1), then PYANNOTE_PIPELINE in voice.env"
  else
    diag="the research's fallback $fallback does not load either ($(err_of "$fbout"))"
  fi
  echo "V6 fail: $pipeline did not load under pyannote.audio $pa_version ($refusal); Section 14.1 names PyAnnote 3.1, so this is a fail, not a fallback. $diag. Remedy: pin pyannote.audio (phase2/05-voice.sh PYANNOTE_PIN) to a release that loads 3.1, or amend 14.1/21; then re-run: sudo $entry phase2 --force 05"
  exit 1
elif (( rc != 0 )); then
  echo "V6 fail: diarisation with $pipeline failed: $(err_of "$online")"
  exit 1
fi
offline="$(diarise "$used" 1)" || { echo "V6 fail: $used loaded online but not with HF_HUB_OFFLINE=1 from $hf_home: $(err_of "$offline")"; exit 1; }
# `used` is always the 3.1 id here (header item 3); kept as a variable so the message names what was proven.

summary="$(python3 - "$online" "$offline" "$used" "$r31" "$rseg" "$pa_version" "$note" <<'PY'
import json, sys
on, off = json.loads(sys.argv[1]), json.loads(sys.argv[2])
used, r31, rseg, pav, note = sys.argv[3:8]
print(f"pipeline used: {used} (pyannote.audio {pav}); 3.1 accepted (HEAD 3.1 config.yaml {r31}, segmentation-3.0 "
      f"pytorch_model.bin {rseg}); loaded in {on['load_s']}s, diarised 10 s of synthetic audio in {on['run_s']}s "
      f"({on['speakers']} speaker(s) found); offline reload {off['load_s']}s{note}")
PY
)"
echo "$summary"
exit 0
