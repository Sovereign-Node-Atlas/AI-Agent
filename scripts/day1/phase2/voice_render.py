#!/usr/bin/env python3
"""V7 listening-test harness (Section 14.3, 21 V7; CONVENTIONS.md §5).

Renders ONE fixed paragraph for every persona x Kokoro candidate in config/voice-casting.json into
<out>/<persona>-<voice>.wav through Kokoro-FastAPI's OpenAI-compatible API, and, for the personas that have a
reference recording listed under "reference_recordings" (Alaric, Gideon), a Chatterbox clone sample
<out>/<persona>-chatterbox-clone.wav rendered inside /opt/atlas/venv-voice (the venv phase2/05-voice.sh builds).

Exit codes (the same contract verify/v07-voice-listen.sh maps onto V7):
    0  every render succeeded and every reference recording was present   -> pass ("Principal to listen")
    2  a reference recording is absent (Kokoro renders still happen)       -> deferred, naming the paths
    1  a Kokoro render failed, Kokoro is unreachable, or a present clone failed -> fail

stdout: exactly one JSON line (the summary); progress goes to stderr. The summary is also written to --json-out.

Usage:
    voice_render.py render [--casting FILE] [--out DIR] [--kokoro URL] [--venv-python FILE] [--hf-home DIR]
                           [--json-out FILE] [--skip-clone]
    voice_render.py clone --ref FILE --text TEXT --out FILE        (internal: re-executed under the voice venv)

Facts typed literally from voice-stt.md: POST /v1/audio/speech with {"model":"kokoro","input","voice",
"response_format":"wav","stream":false} (§2.2 VERIFIED); ChatterboxTTS.from_pretrained(device) and
generate(text, audio_prompt_path=...) returning a waveform tensor with model.sr (§3.2 VERIFIED); chunks of at most
~300 characters and never fewer than a few words (§3.3 continuation quirk).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

# The one fixed paragraph, in the 100-200 token "goldilocks range" VOICES.md recommends (voice-stt.md §2.3).
PARAGRAPH = (
    "Good morning. Before we begin, here is where things stand. The overnight review finished without "
    "exceptions, two items are waiting for your decision, and the estate report is ready whenever you want it. "
    "I have kept the summary short on purpose; the detail is one question away. If you would rather take the "
    "difficult item first, say so, and we will start there."
)

DEFAULT_CASTING = Path(__file__).resolve().parent.parent / "config" / "voice-casting.json"
DEFAULT_OUT = Path(os.environ.get("ATLAS_SRV", "/srv/atlas")) / "staging" / "listening-test"
DEFAULT_KOKORO = "http://127.0.0.1:8880"
DEFAULT_VENV_PY = Path(os.environ.get("ATLAS_OPT", "/opt/atlas")) / "venv-voice" / "bin" / "python"
DEFAULT_HF_HOME = Path(os.environ.get("ATLAS_SRV", "/srv/atlas")) / "engines" / "hf"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------------------------
# Kokoro
# ---------------------------------------------------------------------------------------------------------------
def kokoro_render(base: str, text: str, voice: str, out: Path) -> float:
    """POST /v1/audio/speech (VERIFIED schema), write the WAV, return the wall-clock seconds."""
    payload = {"model": "kokoro", "input": text, "voice": voice, "response_format": "wav", "stream": False}
    req = urllib.request.Request(
        f"{base}/v1/audio/speech",
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.monotonic()
    # Loopback service: never send it through the allowlist proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=600) as resp:
        data = resp.read()
    if not data.startswith(b"RIFF"):
        raise RuntimeError(f"Kokoro did not return a WAV for voice {voice!r} ({len(data)} bytes)")
    out.write_bytes(data)
    return round(time.monotonic() - t0, 2)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        return round(w.getnframes() / rate, 2) if rate else 0.0


# ---------------------------------------------------------------------------------------------------------------
# Chatterbox (runs under the voice venv's interpreter: `voice_render.py clone ...`)
# ---------------------------------------------------------------------------------------------------------------
def split_chunks(text: str, limit: int = 300) -> list[str]:
    """Sentence-bounded chunks of at most `limit` characters (voice-stt.md §3.3: hallucination past long inputs)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def cmd_clone(args: argparse.Namespace) -> int:
    """Render TEXT as a clone of REF with Chatterbox on CPU; write a 16-bit PCM WAV with the stdlib (no torchaudio backend)."""
    try:
        import numpy as np  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]
        from chatterbox.tts import ChatterboxTTS  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - only reachable outside the voice venv
        eprint(f"clone: chatterbox-tts is not importable from {sys.executable}: {exc}")
        return 1
    ref = Path(args.ref)
    if not ref.is_file():
        eprint(f"clone: reference recording {ref} does not exist")
        return 1
    t0 = time.monotonic()
    model = ChatterboxTTS.from_pretrained(device="cpu")
    load_s = round(time.monotonic() - t0, 1)
    pieces = []
    t1 = time.monotonic()
    for chunk in split_chunks(args.text):
        wav = model.generate(chunk, audio_prompt_path=str(ref))
        pieces.append(wav.detach().cpu())
    audio = torch.cat(pieces, dim=1) if len(pieces) > 1 else pieces[0]
    gen_s = round(time.monotonic() - t1, 1)
    pcm = np.clip(audio[0].numpy(), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(model.sr))
        w.writeframes(pcm16.tobytes())
    print(
        json.dumps(
            {
                "out": str(out),
                "load_s": load_s,
                "generate_s": gen_s,
                "sr": int(model.sr),
                "audio_s": round(len(pcm16) / float(model.sr), 2),
            }
        )
    )
    return 0


def chatterbox_clone(venv_python: Path, hf_home: Path, ref: Path, text: str, out: Path) -> dict[str, Any]:
    """Re-execute this file under the voice venv (its torch/chatterbox pins differ from the host Python)."""
    import subprocess

    if not venv_python.is_file():
        raise RuntimeError(f"{venv_python} does not exist (phase2/05-voice.sh builds /opt/atlas/venv-voice)")
    env = dict(os.environ)
    env.update({"HF_HOME": str(hf_home), "HF_HUB_ENABLE_HF_TRANSFER": "0", "OMP_NUM_THREADS": str(os.cpu_count() or 8)})
    proc = subprocess.run(
        [
            str(venv_python),
            str(Path(__file__).resolve()),
            "clone",
            "--ref",
            str(ref),
            "--text",
            text,
            "--out",
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=3000,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f"chatterbox clone failed (exit {proc.returncode}): {' | '.join(tail)}")
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    result: dict[str, Any] = json.loads(last)
    return result


# ---------------------------------------------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------------------------------------------
def load_casting(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    if "personas" not in data or "reference_recordings" not in data:
        raise ValueError(f"{path}: expected keys 'personas' and 'reference_recordings' (config/README.md)")
    return data


def cmd_render(args: argparse.Namespace) -> int:
    casting = load_casting(Path(args.casting))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = casting["reference_recordings"]

    rendered: list[dict[str, Any]] = []
    errors: list[str] = []
    missing_refs: list[str] = []

    # Kokoro must be up before anything else is attempted.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{args.kokoro}/health", timeout=15) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        summary = {
            "status": "fail",
            "error": f"Kokoro unreachable at {args.kokoro}/health: {exc}",
            "out_dir": str(out_dir),
        }
        print(json.dumps(summary))
        return 1

    for persona in casting["personas"]:
        key = persona["key"]
        candidates = [v for v in (persona.get("kokoro_primary"), persona.get("kokoro_alternate")) if v]
        for voice in candidates:
            target = out_dir / f"{key}-{voice}.wav"
            try:
                secs = kokoro_render(args.kokoro, PARAGRAPH, voice, target)
                rendered.append(
                    {
                        "persona": key,
                        "engine": "kokoro",
                        "voice": voice,
                        "file": target.name,
                        "render_s": secs,
                        "audio_s": wav_seconds(target),
                    }
                )
                eprint(f"kokoro  {key:8s} {voice:12s} {secs:6.2f}s -> {target.name}")
            except Exception as exc:  # broad on purpose: every failure must land in the summary, not abort the loop
                errors.append(f"{key}/{voice}: {exc}")
                eprint(f"kokoro  {key:8s} {voice:12s} FAILED: {exc}")
        ref_path = refs.get(key)
        if not ref_path:
            continue
        ref = Path(ref_path)
        if not ref.is_file():
            missing_refs.append(str(ref))
            eprint(f"clone   {key:8s} reference absent: {ref}")
            continue
        if args.skip_clone:
            eprint(f"clone   {key:8s} reference present, clone skipped (--skip-clone)")
            continue
        target = out_dir / f"{key}-chatterbox-clone.wav"
        try:
            info = chatterbox_clone(Path(args.venv_python), Path(args.hf_home), ref, PARAGRAPH, target)
            rendered.append(
                {
                    "persona": key,
                    "engine": "chatterbox",
                    "voice": ref.name,
                    "file": target.name,
                    "render_s": info.get("generate_s"),
                    "load_s": info.get("load_s"),
                    "audio_s": info.get("audio_s"),
                }
            )
            eprint(
                f"clone   {key:8s} {ref.name:12s} load {info.get('load_s')}s gen {info.get('generate_s')}s -> {target.name}"
            )
        except Exception as exc:  # broad on purpose: recorded, the summary decides the exit code
            errors.append(f"{key}/clone: {exc}")
            eprint(f"clone   {key:8s} FAILED: {exc}")

    kokoro_n = sum(1 for r in rendered if r["engine"] == "kokoro")
    clone_n = sum(1 for r in rendered if r["engine"] == "chatterbox")
    if errors:
        status = "fail"
    elif missing_refs:
        status = "deferred"
    else:
        status = "pass"
    summary: dict[str, Any] = {
        "check": "V7",
        "status": status,
        "out_dir": str(out_dir),
        "paragraph_chars": len(PARAGRAPH),
        "files": len(rendered),
        "kokoro_files": kokoro_n,
        "chatterbox_files": clone_n,
        "missing_references": missing_refs,
        "errors": errors,
        "rendered": rendered,
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if args.json_out:
        jp = Path(args.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return {"pass": 0, "deferred": 2, "fail": 1}[status]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render the listening test")
    r.add_argument("--casting", default=str(DEFAULT_CASTING))
    r.add_argument("--out", default=str(DEFAULT_OUT))
    r.add_argument("--kokoro", default=DEFAULT_KOKORO)
    r.add_argument("--venv-python", default=str(DEFAULT_VENV_PY))
    r.add_argument("--hf-home", default=str(DEFAULT_HF_HOME))
    r.add_argument("--json-out", default="")
    r.add_argument(
        "--skip-clone", action="store_true", help="do not render Chatterbox clones even when references exist"
    )
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("clone", help="internal: Chatterbox clone under the voice venv")
    c.add_argument("--ref", required=True)
    c.add_argument("--text", required=True)
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_clone)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
