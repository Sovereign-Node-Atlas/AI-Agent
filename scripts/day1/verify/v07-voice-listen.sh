#!/usr/bin/env bash
# verify/v07-voice-listen.sh — V7: voice casting listening test, Alaric and Gideon clones sourced (Sections 14.3, 17
# Phase 2 gate, 21, 22). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred; one stdout line; no prompt;
# well under a minute, safe to re-run.
#
# This script renders NOTHING (fix round: a full render — 19 Kokoro paragraphs plus two CPU Chatterbox clones — does
# not fit the 10-minute verify contract and was run twice, in step 5 and at the gate). phase2/05-voice.sh runs
# phase2/voice_render.py render ONCE as the atlas account and writes the summary to
# $LISTENING_TEST_DIR/v7-listening-test.json (contract: 05-voice.sh header); this script reads that summary, checks
# that every file it lists still exists as a non-empty WAV under $LISTENING_TEST_DIR, and maps the recorded status:
#   pass      "rendered N files; Principal to listen"        (every reference recording present, everything rendered)
#   deferred  reference recordings absent, naming the paths (never a fail: Section 22)
#   fail      the renderer recorded an error, the summary is missing, or a listed file vanished
# Usage: v07-voice-listen.sh [SUMMARY_JSON]
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

# Defaults, overridable by /etc/atlas/voice.env (phase2/05-voice.sh contract).
LISTENING_TEST_DIR="$ATLAS_SRV/staging/listening-test"
if [[ -r "$ATLAS_ETC/voice.env" ]]; then
  # shellcheck disable=SC1091  # KEY=VALUE lines only (05-voice.sh contract)
  source "$ATLAS_ETC/voice.env"
fi
summary="${1:-$LISTENING_TEST_DIR/v7-listening-test.json}"
entry="${ATLAS_ENTRY:-./atlas-day1.sh}"

[[ -d "$LISTENING_TEST_DIR" ]] || { echo "V7 fail: $LISTENING_TEST_DIR does not exist (phase2/05-voice.sh creates it); re-run: sudo $entry phase2 --force 05"; exit 1; }
[[ -s "$summary" ]] || { echo "V7 fail: no render summary at $summary (phase2/05-voice.sh runs voice_render.py once); re-run: sudo $entry phase2 --force 05"; exit 1; }

# This script changes NOTHING under $LISTENING_TEST_DIR (fix round 3, major): an earlier revision re-applied
# `chmod -R o-rwx,g+rX` here, which stripped the group-write bit phase2/05-voice.sh sets (`g+rwX`: the atlas renderer
# rewrites v7-listening-test.json and replaces WAVs on a `--force 05` re-run), so after one gate run the documented
# remediation of a deferred V7 failed with PermissionError and recorded the STALE summary. Ownership
# ($PRINCIPAL_USER:atlas) and modes (dir 2770, files o-rwx,g+rwX) are 05-voice.sh's job, set before and after every
# render; a verify script reads what it judges.

line="$(python3 - "$summary" "$LISTENING_TEST_DIR" "$entry" <<'PY'
import json, sys, wave
from pathlib import Path
summary, out_dir, entry = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
try:
    s = json.load(open(summary, encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    print(f"fail: {summary} unreadable: {exc}")
    sys.exit(1)
status = s.get("status", "fail")
n, k, c, f = s.get("files", 0), s.get("kokoro_files", 0), s.get("chatterbox_files", 0), s.get("fallback_files", 0)
missing, errors = s.get("missing_references", []), s.get("errors", [])
gone = []
for r in s.get("rendered", []):
    p = out_dir / r.get("file", "")
    try:
        with wave.open(str(p), "rb") as w:
            ok = w.getnframes() > 0
    except Exception:  # noqa: BLE001 - any unreadable file counts as gone
        ok = False
    if not ok:
        gone.append(p.name)
if gone:
    print(f"fail: {len(gone)} rendered file(s) missing or empty under {out_dir}: {', '.join(gone[:5])}; "
          f"re-run: sudo {entry} phase2 --force 05")
    sys.exit(1)
if status == "pass":
    print(f"rendered {n} files; Principal to listen ({k} Kokoro, {c} Chatterbox clone(s) in {out_dir}, "
          f"rendered {s.get('rendered_at', '?')}; open the folder from the XFCE desktop)")
    sys.exit(0)
if status == "deferred":
    print(f"deferred: reference recordings absent: {', '.join(missing)}; rendered {n} files into {out_dir} "
          f"({k} Kokoro, {f} fallback preset(s) per Section 22); Principal to listen, then copy the recordings and "
          f"re-run: sudo {entry} phase2 --force 05")
    sys.exit(2)
err = s.get("error") or "; ".join(errors[:3]) or "renderer recorded status fail without detail"
print(f"fail: {err} ({n} files rendered into {out_dir}); re-run: sudo {entry} phase2 --force 05")
sys.exit(1)
PY
)" && rc=0 || rc=$?
echo "$line"
case "$rc" in 0) exit 0 ;; 2) exit 2 ;; *) exit 1 ;; esac
