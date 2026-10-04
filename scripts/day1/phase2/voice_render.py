#!/usr/bin/env python3
"""V7 listening-test renderer (Section 14.3, 21 V7, 22; CONVENTIONS.md §5).

Renders ONE fixed paragraph for every persona x Kokoro candidate in config/voice-casting.json into
<out>/<persona>-<voice>.wav through Kokoro-FastAPI's OpenAI-compatible API, and, for the personas that have a
reference recording listed under "reference_recordings" (Alaric, Gideon), a Chatterbox clone sample
<out>/<persona>-chatterbox-clone.wav rendered inside /opt/atlas/venv-voice (the venv phase2/05-voice.sh builds).

Reference recordings (fix round 5): the Principal may drop alaric.* / gideon.* as WAV, MP3 or M4A into the inbox;
phase2/05-voice.sh transcodes whatever is there to 16 kHz mono WAV under $ATLAS_SRV/staging/voice-references-normalised/
and passes that directory as --references-dir, so the clone always consumes <references-dir>/<persona>.wav. Without
--references-dir the casting file's paths are used as they are (the earlier behaviour).

ENGINE ARBITER (Section 4.2, hard requirement: "every load ... of any weight-bearing process ... Chatterbox when
invoked" passes through it; fix round 5). Before the clone batch loads ChatterboxTTS this renderer POSTs
/arbiter/load {"engine": "chatterbox", "task_id": ...} on the step-2 orchestrator (--arbiter, loopback; the admin
token from --arbiter-token-file when orchestrator.env configures one) and POSTs /arbiter/unload after the batch WITH
THE CLONE-BATCH CHILD'S PID (fix round 6: Section 4.2 rule 5's substance for a CPU process, the Arbiter confirms the
release against /proc/<pid> instead of taking this program's word; the child has exited by then, so it is gone),
whatever happened inside it. The key is config/engines.json's `external` entry (class external: budgeted, counted as
resident and ledgered by the Arbiter, run by this process). A 404 from either route means "the Arbiter does not know
the key" (an orchestrator running a package or an engines.json without the entry) and is a FAILURE of every clone in
the batch, named as such in the summary: the load is never skipped around the Arbiter. A decision other than
granted (queued, refused), an unreachable orchestrator or a non-200 answer fail the clones the same way; the Kokoro
renders still happen, and the summary's status is "fail" so V7 is recorded fail (rule §7.4), never a silent pass.

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
    1  a Kokoro render failed, Kokoro is unreachable, a present clone failed (or hit --deadline), or the Arbiter
       did not grant / does not know the chatterbox key (ENGINE ARBITER above)                   -> fail

stdout: exactly one JSON line (the summary); progress goes to stderr. The summary is also written to --json-out.

Usage:
    voice_render.py render [--casting FILE] [--out DIR] [--kokoro URL] [--venv-python FILE] [--hf-home DIR]
                           [--json-out FILE] [--skip-clone] [--deadline SECONDS] [--references-dir DIR]
                           [--arbiter URL] [--arbiter-token-file FILE] [--arbiter-task-id ID]
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
# The step-2 orchestrator (CONVENTIONS.md §8: ORCH_PORT 8800) and the engines.json `external` key (Section 4.2).
DEFAULT_ARBITER = f"http://127.0.0.1:{os.environ.get('ORCH_PORT') or 8800}"
ARBITER_KEY = os.environ.get("CHATTERBOX_ARBITER_KEY") or "chatterbox"
REFERENCE_EXTS = (".wav", ".mp3", ".m4a")  # what phase2/05-voice.sh accepts in the inbox and normalises
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
# Engine Arbiter (Section 4.2; module docstring ENGINE ARBITER)
# ---------------------------------------------------------------------------------------------------------------
class ArbiterRefusal(RuntimeError):
    """The Arbiter did not grant the Chatterbox load (or does not know the key): the clones must not render."""


def _arbiter_token(token_file: str) -> str:
    """ORCH_ADMIN_TOKEN=... or the bare token, as phase2/02-orchestrator.sh writes the file; '' when no file."""
    if not token_file:
        return ""
    text = Path(token_file).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ORCH_ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return text.strip()


def arbiter_call(base: str, token: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST <base><path> on the loopback orchestrator (never through the proxy); (HTTP status, JSON body or {})."""
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}", method="POST", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **({"X-Atlas-Token": token} if token else {})},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=120) as resp:
            code, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        code, raw = exc.code, exc.read()
    try:
        data = json.loads(raw.decode("utf-8", "replace")) if raw else {}
    except ValueError:
        data = {"detail": raw.decode("utf-8", "replace")[:300]}
    return code, data if isinstance(data, dict) else {"detail": data}


def arbiter_load(base: str, token: str, task_id: str) -> dict[str, Any]:
    """POST /arbiter/load for the chatterbox key; returns the granted decision or raises ArbiterRefusal (loudly)."""
    entry = os.environ.get("ATLAS_ENTRY") or "./atlas-day1.sh"
    try:
        code, data = arbiter_call(base, token, "/arbiter/load", {"engine": ARBITER_KEY, "task_id": task_id})
    except (urllib.error.URLError, OSError) as exc:
        raise ArbiterRefusal(
            f"POST {base}/arbiter/load unreachable ({exc}); Section 4.2: the Chatterbox load passes through the "
            "Arbiter or does not happen (is the step-2 orchestrator up? systemctl status atlas-orchestrator)"
        ) from exc
    if code == 404:
        raise ArbiterRefusal(
            f"the Arbiter does not know engine key {ARBITER_KEY!r} (HTTP 404 from {base}/arbiter/load: "
            f"{data.get('detail', '')}). The running orchestrator does not read config/engines.json's `external` "
            "list (Section 4.2 class external): deploy the package and config that carry it and restart it "
            f"(sudo {entry} phase2 --force 02), then re-run step 5. The clone render is NOT skipped around the Arbiter"
        )
    if code != 200:
        raise ArbiterRefusal(f"POST {base}/arbiter/load answered HTTP {code}: {data.get('detail', data)}")
    if data.get("decision") != "granted":
        raise ArbiterRefusal(
            f"the Arbiter did not grant the {ARBITER_KEY} load: {data.get('decision')} ({data.get('reason', '?')}); "
            "Section 4.2 rule 3: wait for the resident engine(s) to unload and re-run step 5"
        )
    return data


def arbiter_unload(base: str, token: str, task_id: str, pid: int | None = None) -> dict[str, Any]:
    """POST /arbiter/unload; `pid` (the clone-batch child) lets the Arbiter confirm the release in /proc (rule 5)."""
    body: dict[str, Any] = {"engine": ARBITER_KEY, "task_id": task_id}
    if pid is not None:
        body["pid"] = int(pid)
    try:
        code, data = arbiter_call(base, token, "/arbiter/unload", body)
    except (urllib.error.URLError, OSError) as exc:
        raise ArbiterRefusal(f"POST {base}/arbiter/unload unreachable ({exc}); the ledger still shows "
                             f"{ARBITER_KEY} resident (Section 4.2 rule 1)") from exc
    if code == 404:
        raise ArbiterRefusal(f"the Arbiter does not know engine key {ARBITER_KEY!r} on unload (HTTP 404)")
    if code != 200 or data.get("decision") != "granted":
        raise ArbiterRefusal(f"POST {base}/arbiter/unload: HTTP {code} {data.get('decision')} "
                             f"({data.get('reason', data.get('detail', '?'))})")
    return data


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


class CloneBatchError(RuntimeError):
    """The clone-batch child failed; `pid` is still reported to /arbiter/unload so the release is measured."""

    def __init__(self, msg: str, pid: int | None) -> None:
        super().__init__(msg)
        self.pid = pid


def chatterbox_clone_batch(
    venv_python: Path, hf_home: Path, jobs: list[dict[str, str]], budget_s: float | None
) -> dict[str, Any]:
    """Re-execute this file under the voice venv ONCE for all clones (its torch/chatterbox pins differ from the host).

    `budget_s` is the wall-clock budget left (None = unlimited). On timeout every job that has no result is reported
    with the error "timed out", never silently skipped. The result carries the child's `pid` for POST /arbiter/unload
    (module docstring: the Arbiter confirms the release against /proc/<pid>).
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
    pid: int | None = None
    try:
        proc = subprocess.Popen(
            [str(venv_python), str(Path(__file__).resolve()), "clone-batch", "--jobs", jobs_file],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        pid = proc.pid
        try:
            out, err = proc.communicate(timeout=budget_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
            tail = (err or "").strip().splitlines()[-3:]
            return {
                "load_s": None,
                "pid": pid,
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
    out = out or ""
    last = out.strip().splitlines()[-1] if out.strip() else ""
    if not last.startswith("{"):
        tail = ((err or "") or out).strip().splitlines()[-5:]
        raise CloneBatchError(f"chatterbox clone-batch failed (exit {proc.returncode}): {' | '.join(tail)}", pid)
    result: dict[str, Any] = json.loads(last)
    result["pid"] = pid
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
        if args.references_dir:
            # phase2/05-voice.sh normalised whatever the inbox held (WAV/MP3/M4A) to <references-dir>/<key>.wav.
            ref = Path(args.references_dir) / f"{key}.wav"
        if not ref.is_file():
            if args.references_dir:
                stem = Path(ref_path).with_suffix("")
                missing_refs.append(f"{stem}{{{','.join(REFERENCE_EXTS)}}} (no normalised 16 kHz WAV at {ref})")
            else:
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

    arbiter: dict[str, Any] = {}
    if clone_jobs:
        budget: float | None = None
        if deadline is not None:
            budget = max(60.0, deadline - (time.monotonic() - started))
        # Section 4.2 (module docstring ENGINE ARBITER): the load is granted by the Arbiter or does not happen.
        task_id = args.arbiter_task_id or f"day1-phase2-05-v7-{int(time.time())}"
        token = ""
        try:
            token = _arbiter_token(args.arbiter_token_file)
            granted = arbiter_load(args.arbiter, token, task_id)
            arbiter["load"] = granted.get("reason")
            eprint(f"arbiter  {ARBITER_KEY:8s} load granted: {granted.get('reason')}")
        except (ArbiterRefusal, OSError) as exc:
            arbiter["load_error"] = str(exc)
            for job in clone_jobs:
                errors.append(f"{clone_meta[job['out']][0]}/clone: not rendered, Arbiter: {exc}")
            eprint(f"arbiter  {ARBITER_KEY:8s} FAILED: {exc}")
            clone_jobs = []
    if clone_jobs:
        batch_pid: int | None = None  # the clone-batch child, reported to /arbiter/unload (module docstring)
        try:
            batch = chatterbox_clone_batch(Path(args.venv_python), Path(args.hf_home), clone_jobs, budget)
            batch_pid = batch.get("pid")
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
            batch_pid = getattr(exc, "pid", None)
            for job in clone_jobs:
                errors.append(f"{clone_meta[job['out']][0]}/clone: {exc}")
            eprint(f"clone    batch FAILED: {exc}")
        finally:
            # Rule 1: the ledger must stop showing the process resident once it has exited, whatever happened above;
            # rule 5: the child's pid lets the Arbiter confirm that it has (the child has returned or been killed here).
            try:
                released = arbiter_unload(args.arbiter, token, task_id, batch_pid)
                arbiter["unload"] = released.get("reason")
                eprint(f"arbiter  {ARBITER_KEY:8s} unloaded: {released.get('reason')}")
            except (ArbiterRefusal, OSError) as exc:
                arbiter["unload_error"] = str(exc)
                errors.append(f"arbiter/unload: {exc}")
                eprint(f"arbiter  {ARBITER_KEY:8s} unload FAILED: {exc}")

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
        "arbiter": arbiter,
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
    r.add_argument(
        "--references-dir",
        default="",
        help="directory of normalised 16 kHz mono <persona>.wav references (phase2/05-voice.sh); "
        "default: the casting file's paths as they are",
    )
    r.add_argument("--arbiter", default=DEFAULT_ARBITER, help="the step-2 orchestrator (POST /arbiter/load|unload)")
    r.add_argument("--arbiter-token-file", default="", help="ORCH_ADMIN_TOKEN_FILE when orchestrator.env sets one")
    r.add_argument("--arbiter-task-id", default="", help="task id written to the Arbiter ledger (Section 4.2 rule 9)")
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("clone-batch", help="internal: Chatterbox clones under the voice venv, model loaded once")
    c.add_argument("--jobs", required=True, help='JSON file: [{"ref": FILE, "text": TEXT, "out": FILE}, ...]')
    c.set_defaults(func=cmd_clone_batch)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
