#!/usr/bin/env python3
"""V7 listening-test renderer (Section 14.3, 21 V7, 22; CONVENTIONS.md §5).

Renders ONE fixed paragraph for every persona x Kokoro candidate in config/voice-casting.json into
<out>/<persona>-<voice>.wav through Kokoro-FastAPI's OpenAI-compatible API, and, for the personas that have a
reference recording listed under "reference_recordings" (Alaric, Gideon), a Chatterbox clone sample
<out>/<persona>-chatterbox-clone.wav rendered inside /opt/atlas/venv-voice (the venv phase2/05-voice.sh builds).

A persona whose reference recording is absent AND that has no Kokoro candidate at all (Alaric: 14.3 says "no preset
delivers gravel") still gets a sample: Section 22 promises "both fall back to the nearest Kokoro preset", so
<out>/<persona>-<voice>-fallback.wav is rendered with the persona's "kokoro_fallback" key when config/voice-casting.json
carries one, else FALLBACK_VOICE (am_onyx, the preset 14.3 names as "reassigned" for the gravel register), counted in
the summary as "fallback"; the status stays deferred.

Where it runs (fix round): phase2/05-voice.sh calls `render` ONCE, as the atlas service account, with no time cap;
verify/v07-voice-listen.sh only READS the summary this writes to --json-out and the files under --out, so V7 stays
well under the 10-minute verify contract and never re-renders. Every render is idempotent: an output WAV that already
exists (and, for clones, is newer than its reference recording) is kept and counted, not rendered again.

Exit codes of `render` (the contract verify/v07-voice-listen.sh maps onto V7):
    0  every render succeeded and every reference recording was present   -> pass ("Principal to listen")
    2  a reference recording is absent (Kokoro renders still happen)       -> deferred, naming the paths
    1  a Kokoro render failed, Kokoro is unreachable, or a present clone failed (or hit --deadline) -> fail

stdout: exactly one JSON line (the summary); progress goes to stderr. The summary is also written to --json-out.

Usage:
    voice_render.py render [--casting FILE] [--out DIR] [--kokoro URL] [--venv-python FILE] [--hf-home DIR]
                           [--json-out FILE] [--skip-clone] [--deadline SECONDS]
    voice_render.py clone-batch --jobs FILE      (internal: re-executed under the voice venv; FILE is a JSON list of
                                                  {"ref","text","out"}; ChatterboxTTS is loaded ONCE for all entries)

Facts typed literally from voice-stt.md: POST /v1/audio/speech with {"model":"kokoro","input","voice",
"response_format":"wav","stream":false} (§2.2 VERIFIED); ChatterboxTTS.from_pretrained(device) and
generate(text, audio_prompt_path=...) returning a waveform tensor with model.sr (§3.2 VERIFIED); chunks of at most
~300 characters and never fewer than a few words (§3.3 continuation quirk); the 500M model on CPU is "roughly
real-time or slower" (§3.4 UNVERIFIED), which is why the clones are rendered in the step, not in the verify script.
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

# Section 14.3: Alaric has no Kokoro candidate ("no preset delivers gravel"); the only preset the table associates with
# that register is am_onyx ("else am_onyx reassigned" in Gideon's row). Used only when the reference recording is absent
# and config/voice-casting.json carries no "kokoro_fallback" for the persona.
FALLBACK_VOICE = "am_onyx"

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


def wav_ok(path: Path) -> bool:
    """True when `path` is a readable, non-empty WAV (an interrupted earlier run leaves a partial file)."""
    try:
        return path.is_file() and wav_seconds(path) > 0.0
    except (OSError, wave.Error, EOFError):
        return False


# ---------------------------------------------------------------------------------------------------------------
# Chatterbox (runs under the voice venv's interpreter: `voice_render.py clone-batch --jobs FILE`)
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


def write_pcm16(out: Path, audio: Any, sr: int) -> float:
    """Write a mono 16-bit PCM WAV with the stdlib (no torchaudio backend); return the seconds of audio."""
    import numpy as np  # type: ignore[import-not-found]

    pcm = np.clip(audio[0].numpy(), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16.tobytes())
    tmp.replace(out)
    return round(len(pcm16) / float(sr), 2)


def cmd_clone_batch(args: argparse.Namespace) -> int:
    """Render every job {"ref","text","out"} as a Chatterbox clone on CPU; the model is loaded once (fix round).

    stdout: one JSON line {"load_s": .., "results": [{"out","generate_s","audio_s","sr"} | {"out","error"}]}.
    Exit 0 when every job rendered, 1 otherwise (the per-job errors are in the JSON either way).
    """
    try:
        import torch  # type: ignore[import-not-found]
        from chatterbox.tts import ChatterboxTTS  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - only reachable outside the voice venv
        eprint(f"clone-batch: chatterbox-tts is not importable from {sys.executable}: {exc}")
        return 1
    jobs: list[dict[str, str]] = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    t0 = time.monotonic()
    model = ChatterboxTTS.from_pretrained(device="cpu")
    load_s = round(time.monotonic() - t0, 1)
    eprint(f"clone-batch: ResembleAI/chatterbox loaded on CPU in {load_s}s for {len(jobs)} job(s)")
    results: list[dict[str, Any]] = []
    failed = 0
    for job in jobs:
        ref, out = Path(job["ref"]), Path(job["out"])
        if not ref.is_file():
            results.append({"out": str(out), "error": f"reference recording {ref} does not exist"})
            failed += 1
            continue
        try:
            t1 = time.monotonic()
            pieces = []
            for chunk in split_chunks(job["text"]):
                wav = model.generate(chunk, audio_prompt_path=str(ref))
                pieces.append(wav.detach().cpu())
            audio = torch.cat(pieces, dim=1) if len(pieces) > 1 else pieces[0]
            gen_s = round(time.monotonic() - t1, 1)
            audio_s = write_pcm16(out, audio, int(model.sr))
            results.append({"out": str(out), "generate_s": gen_s, "audio_s": audio_s, "sr": int(model.sr)})
            eprint(f"clone-batch: {out.name} gen {gen_s}s for {audio_s}s of audio")
        except Exception as exc:  # broad on purpose: one failed clone must not lose the others' results
            results.append({"out": str(out), "error": f"{type(exc).__name__}: {exc}"})
            failed += 1
            eprint(f"clone-batch: {out.name} FAILED: {exc}")
    print(json.dumps({"load_s": load_s, "results": results}), flush=True)
    return 1 if failed else 0


def chatterbox_clone_batch(
    venv_python: Path, hf_home: Path, jobs: list[dict[str, str]], budget_s: float | None
) -> dict[str, Any]:
    """Re-execute this file under the voice venv ONCE for all clones (its torch/chatterbox pins differ from the host).

    `budget_s` is the wall-clock budget left (None = unlimited). On timeout every job that has no result is reported
    with the error "timed out", never silently skipped.
    """
    import subprocess
    import tempfile

    if not venv_python.is_file():
        raise RuntimeError(f"{venv_python} does not exist (phase2/05-voice.sh builds /opt/atlas/venv-voice)")
    env = dict(os.environ)
    env.update(
        {
            "HF_HOME": str(hf_home),
            "HF_HUB_ENABLE_HF_TRANSFER": "0",
            # Offline after step 5's one-time pull (Section 12.5, rule §7.1: no telemetry, nothing outbound).
            "HF_HUB_OFFLINE": env.get("HF_HUB_OFFLINE", "1"),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "OMP_NUM_THREADS": str(os.cpu_count() or 8),
        }
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="v7-clone-jobs-", delete=False) as fh:
        json.dump(jobs, fh)
        jobs_file = fh.name
    try:
        proc = subprocess.run(
            [str(venv_python), str(Path(__file__).resolve()), "clone-batch", "--jobs", jobs_file],
            env=env,
            capture_output=True,
            text=True,
            timeout=budget_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        tail = ((exc.stderr or "") if isinstance(exc.stderr, str) else "").strip().splitlines()[-3:]
        return {
            "load_s": None,
            "results": [
                {"out": j["out"], "error": f"timed out after {budget_s:.0f}s budget ({' | '.join(tail)})"}
                for j in jobs
                if not wav_ok(Path(j["out"]))
            ],
        }
    finally:
        try:
            os.unlink(jobs_file)
        except OSError:
            pass
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if not last.startswith("{"):
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f"chatterbox clone-batch failed (exit {proc.returncode}): {' | '.join(tail)}")
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


def _kokoro_entry(key: str, voice: str, target: Path, secs: float | None, engine: str) -> dict[str, Any]:
    return {
        "persona": key,
        "engine": engine,
        "voice": voice,
        "file": target.name,
        "render_s": secs,
        "audio_s": wav_seconds(target),
        "reused": secs is None,
    }


def cmd_render(args: argparse.Namespace) -> int:
    started = time.monotonic()
    deadline: float | None = float(args.deadline) if args.deadline and float(args.deadline) > 0 else None
    casting = load_casting(Path(args.casting))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    refs: dict[str, str] = casting["reference_recordings"]

    rendered: list[dict[str, Any]] = []
    errors: list[str] = []
    missing_refs: list[str] = []
    clone_jobs: list[dict[str, str]] = []
    clone_meta: dict[str, tuple[str, Path]] = {}

    # Kokoro must be up before anything else is attempted.
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{args.kokoro}/health", timeout=15) as resp:
            resp.read()
    except (urllib.error.URLError, OSError) as exc:
        summary = {
            "check": "V7",
            "status": "fail",
            "error": f"Kokoro unreachable at {args.kokoro}/health: {exc}",
            "out_dir": str(out_dir),
        }
        _write_summary(args, summary)
        return 1

    def render_kokoro(key: str, voice: str, target: Path, engine: str) -> None:
        try:
            if wav_ok(target):
                rendered.append(_kokoro_entry(key, voice, target, None, engine))
                eprint(f"{engine:8s} {key:8s} {voice:12s} kept    -> {target.name}")
                return
            secs = kokoro_render(args.kokoro, PARAGRAPH, voice, target)
            rendered.append(_kokoro_entry(key, voice, target, secs, engine))
            eprint(f"{engine:8s} {key:8s} {voice:12s} {secs:6.2f}s -> {target.name}")
        except Exception as exc:  # broad on purpose: every failure must land in the summary, not abort the loop
            errors.append(f"{key}/{voice}: {exc}")
            eprint(f"{engine:8s} {key:8s} {voice:12s} FAILED: {exc}")

    for persona in casting["personas"]:
        key = persona["key"]
        candidates = [v for v in (persona.get("kokoro_primary"), persona.get("kokoro_alternate")) if v]
        for voice in candidates:
            render_kokoro(key, voice, out_dir / f"{key}-{voice}.wav", "kokoro")
        ref_path = refs.get(key)
        if not ref_path:
            continue
        ref = Path(ref_path)
        if not ref.is_file():
            missing_refs.append(str(ref))
            eprint(f"clone    {key:8s} reference absent: {ref}")
            if not candidates:
                # Section 22: "both fall back to the nearest Kokoro preset and V7 is recorded as deferred".
                fb = persona.get("kokoro_fallback") or FALLBACK_VOICE
                render_kokoro(key, fb, out_dir / f"{key}-{fb}-fallback.wav", "fallback")
            continue
        if args.skip_clone:
            eprint(f"clone    {key:8s} reference present, clone skipped (--skip-clone)")
            continue
        target = out_dir / f"{key}-chatterbox-clone.wav"
        if wav_ok(target) and target.stat().st_mtime >= ref.stat().st_mtime:
            rendered.append(
                {
                    "persona": key,
                    "engine": "chatterbox",
                    "voice": ref.name,
                    "file": target.name,
                    "render_s": None,
                    "audio_s": wav_seconds(target),
                    "reused": True,
                }
            )
            eprint(f"clone    {key:8s} {ref.name:12s} kept (newer than the reference) -> {target.name}")
            continue
        clone_jobs.append({"ref": str(ref), "text": PARAGRAPH, "out": str(target)})
        clone_meta[str(target)] = (key, ref)

    if clone_jobs:
        budget: float | None = None
        if deadline is not None:
            budget = max(60.0, deadline - (time.monotonic() - started))
        try:
            batch = chatterbox_clone_batch(Path(args.venv_python), Path(args.hf_home), clone_jobs, budget)
            seen: set[str] = set()
            for res in batch.get("results", []):
                key, ref = clone_meta.get(res.get("out", ""), ("?", Path("?")))
                seen.add(res.get("out", ""))
                if "error" in res:
                    errors.append(f"{key}/clone: {res['error']}")
                    eprint(f"clone    {key:8s} FAILED: {res['error']}")
                    continue
                rendered.append(
                    {
                        "persona": key,
                        "engine": "chatterbox",
                        "voice": ref.name,
                        "file": Path(res["out"]).name,
                        "render_s": res.get("generate_s"),
                        "load_s": batch.get("load_s"),
                        "audio_s": res.get("audio_s"),
                        "reused": False,
                    }
                )
                eprint(f"clone    {key:8s} {ref.name:12s} gen {res.get('generate_s')}s -> {Path(res['out']).name}")
            for job in clone_jobs:
                if job["out"] not in seen:
                    key = clone_meta[job["out"]][0]
                    errors.append(f"{key}/clone: no result reported by clone-batch")
        except Exception as exc:  # broad on purpose: recorded, the summary decides the exit code
            for job in clone_jobs:
                errors.append(f"{clone_meta[job['out']][0]}/clone: {exc}")
            eprint(f"clone    batch FAILED: {exc}")

    kokoro_n = sum(1 for r in rendered if r["engine"] == "kokoro")
    fallback_n = sum(1 for r in rendered if r["engine"] == "fallback")
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
        "fallback_files": fallback_n,
        "chatterbox_files": clone_n,
        "missing_references": missing_refs,
        "errors": errors,
        "rendered": rendered,
        "elapsed_s": round(time.monotonic() - started, 1),
        "rendered_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _write_summary(args, summary)
    return {"pass": 0, "deferred": 2, "fail": 1}[status]


def _write_summary(args: argparse.Namespace, summary: dict[str, Any]) -> None:
    """Write --json-out atomically (temp file + rename): a partial write can never leave a half summary that
    verify/v07-voice-listen.sh or phase2/05-voice.sh would read as the verdict (fix round 3)."""
    if args.json_out:
        jp = Path(args.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        tmp = jp.with_name(jp.name + ".part")
        tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        tmp.replace(jp)
    print(json.dumps(summary), flush=True)


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
    r.add_argument(
        "--deadline",
        default="0",
        help="total wall-clock budget in seconds for the whole render (0 = none); clones past it are recorded as fail",
    )
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("clone-batch", help="internal: Chatterbox clones under the voice venv, model loaded once")
    c.add_argument("--jobs", required=True, help='JSON file: [{"ref": FILE, "text": TEXT, "out": FILE}, ...]')
    c.set_defaults(func=cmd_clone_batch)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
