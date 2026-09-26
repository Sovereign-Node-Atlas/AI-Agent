#!/usr/bin/env bash
# verify/v07-voice-listen.sh — V7: voice casting listening test, Alaric and Gideon clones sourced (Sections 14.3, 17
# Phase 2 gate, 21, 22). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred; one stdout line; no prompt.
#
# Runs phase2/voice_render.py: one fixed paragraph for every persona x Kokoro candidate in config/voice-casting.json
# into $LISTENING_TEST_DIR/<persona>-<voice>.wav, plus a Chatterbox clone for each persona whose reference recording
# exists under reference_recordings (the inbox paths). The listening itself is the Principal's, so a full render is
# recorded as PASS with the message "rendered N files; Principal to listen"; absent recordings are DEFERRED naming
# the paths (never a fail: Section 22); a render error is a fail.
# Usage: v07-voice-listen.sh [--skip-clone]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

skip=()
[[ "${1:-}" == "--skip-clone" ]] && skip=(--skip-clone)

# Defaults, overridable by /etc/atlas/voice.env (phase2/05-voice.sh contract).
VOICE_VENV="$ATLAS_OPT/venv-voice"; HF_HOME="$ATLAS_SRV/engines/hf"; LISTENING_TEST_DIR="$ATLAS_SRV/staging/listening-test"
KOKORO_URL="http://127.0.0.1:8880/v1"
if [[ -r "$ATLAS_ETC/voice.env" ]]; then
  # shellcheck disable=SC1091  # KEY=VALUE lines only (05-voice.sh contract)
  source "$ATLAS_ETC/voice.env"
fi
kokoro_base="${KOKORO_URL%/v1}"

renderer="$ATLAS_DAY1_DIR/phase2/voice_render.py"
[[ -f "$renderer" ]] || { echo "V7 fail: $renderer is missing"; exit 1; }
casting="$ATLAS_DAY1_DIR/config/voice-casting.json"
[[ -f "$casting" ]] || { echo "V7 fail: $casting is missing"; exit 1; }
mkdir -p "$LISTENING_TEST_DIR"

out="$(python3 "$renderer" render --casting "$casting" --out "$LISTENING_TEST_DIR" --kokoro "$kokoro_base" \
        --venv-python "$VOICE_VENV/bin/python" --hf-home "$HF_HOME" --json-out "$ATLAS_STATE/v7-listening-test.json" "${skip[@]}")" \
  && rc=0 || rc=$?
# Files must be readable from the Principal's desktop session.
principal="$(awk -F= '$1=="PRINCIPAL_USER" {print $2; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)"
if [[ -n "$principal" ]] && id -u "$principal" >/dev/null 2>&1; then
  chown -R "$principal:atlas" "$LISTENING_TEST_DIR" 2>/dev/null || true
fi
chmod -R g+rX "$LISTENING_TEST_DIR" 2>/dev/null || true

line="$(python3 - "$out" "$rc" "$LISTENING_TEST_DIR" <<'PY'
import json, sys
raw, rc, out_dir = sys.argv[1], int(sys.argv[2]), sys.argv[3]
try:
    s = json.loads(raw.strip().splitlines()[-1]) if raw.strip() else {}
except json.JSONDecodeError:
    s = {}
n, k, c = s.get("files", 0), s.get("kokoro_files", 0), s.get("chatterbox_files", 0)
missing, errors = s.get("missing_references", []), s.get("errors", [])
if rc == 0:
    print(f"rendered {n} files; Principal to listen ({k} Kokoro, {c} Chatterbox clone(s) in {out_dir}, open index from the XFCE desktop)")
elif rc == 2:
    print(f"deferred: reference recordings absent: {', '.join(missing)}; rendered {n} Kokoro files into {out_dir}; "
          f"Principal to listen, then copy the recordings and re-run: sudo /opt/atlas/day1/atlas-day1.sh phase2 --force 05")
else:
    err = s.get("error") or "; ".join(errors[:3]) or raw.strip()[-300:] or "renderer produced no output"
    print(f"fail: {err} ({n} files rendered into {out_dir})")
PY
)"
echo "$line"
case "$rc" in 0) exit 0 ;; 2) exit 2 ;; *) exit 1 ;; esac
