#!/usr/bin/env python3
"""CosyVoice2-0.5B zero-shot / cross-lingual synthesis (rocm-containers.md §6.5, UNVERIFIED-by-snippet: the
CosyVoice2 class and its inference_* generators). The model directory is the offline HF snapshot; the prompt is the
repo's asset/zero_shot_prompt.wav when present, else a synthetic 3-second voiced tone (then cross-lingual mode, which
needs no prompt transcript). Writes cosyvoice2_0.wav."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p4common import Test

REPO = "FunAudioLLM/CosyVoice2-0.5B"


def _synthetic_prompt(path: Path) -> Path:
    import math
    import struct
    import wave

    sr, secs = 16000, 3
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for i in range(sr * secs):
            t = i / sr
            v = 0.3 * math.sin(2 * math.pi * 140 * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t))
            frames += struct.pack("<h", int(v * 32767))
        w.writeframes(bytes(frames))
    return path


def main(t: Test) -> None:
    import torchaudio
    from huggingface_hub import snapshot_download

    src = Path(t.args.src) / "CosyVoice"
    sys.path.insert(0, str(src))
    sys.path.insert(0, str(src / "third_party" / "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from cosyvoice.utils.file_utils import load_wav

    model_dir = snapshot_download(REPO)      # offline: resolves the cached snapshot
    cv = CosyVoice2(model_dir, load_jit=False, load_trt=False, load_vllm=False, fp16=False)
    t.loaded()
    prompt_wav = src / "asset" / "zero_shot_prompt.wav"
    text = "Good evening. Systems are nominal."
    if prompt_wav.is_file():
        prompt = load_wav(str(prompt_wav), 16000)
        gen = cv.inference_zero_shot(text, "希望你以后能够做的比我还好呦。", prompt, stream=False)
        mode = "zero_shot with the repo asset"
    else:
        prompt = load_wav(str(_synthetic_prompt(t.out / "prompt_synthetic.wav")), 16000)
        gen = cv.inference_cross_lingual(text, prompt, stream=False)
        mode = "cross_lingual with a synthetic prompt (asset/zero_shot_prompt.wav absent)"
    out: Path | None = None
    for i, j in enumerate(gen):
        out = t.out / f"cosyvoice2_{i}.wav"
        torchaudio.save(str(out), j["tts_speech"], cv.sample_rate)
        break
    if out is None:
        t.fail("the generator produced no speech")
    t.done(out, notes=f"{mode}; {cv.sample_rate} Hz")


if __name__ == "__main__":
    Test("cosyvoice2").run(main)
