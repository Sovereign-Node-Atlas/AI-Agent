#!/usr/bin/env bash
# phase4/engines/cosyvoice2.sh — CosyVoice2-0.5B, green per Section 15.2 (the research calls it yellow because the
# upstream requirements.txt pins torch/onnxruntime and needs pynini/OpenFst; rocm-containers.md §3.9). Kept green as
# the document says; a failure is a recorded fail, never a block.
# UNVERIFIED workaround (research §3.9): requirements.txt is filtered and installed under the ROCm constraints file;
# the test uses the model's own frontend without pynini text normalisation, replaces upstream load_wav (torchaudio.load,
# which needs torchcodec on torchaudio >= 2.9) with a soundfile loader and writes with soundfile.
# Filter (fix round 3, VERIFIED against requirements.txt at the pinned commit 074ca6dc and PyPI on 2026-10-04):
#   dropped: torch*, onnxruntime* (the CPU onnxruntime is added back explicitly), deepspeed, pynini, WeTextProcessing,
#            ttsfrd, tensorrt*, vllm, flash-attn; grpcio==1.57.0, grpcio-tools==1.57.0 and pyworld==0.3.4 (no cp312
#            wheel; grpcio < 1.59 does not compile on Python 3.12, so `pip install -r` was a guaranteed failure after a
#            long source build; pyworld is only used by the training dataset processor); gradio, fastapi, fastapi-cli,
#            uvicorn, tensorboard, lightning, gdown, wget, matplotlib, pyarrow (serving/UI/training only: the inference
#            modules cosyvoice/cli/*.py, flow, llm, hifigan, frontend import none of them); openai-whisper (installed
#            separately, below).
#   kept:    everything the inference path imports: modelscope (cosyvoice/cli/cosyvoice.py imports it at module level,
#            VERIFIED, so it is NOT serving-only), hyperpyyaml, omegaconf, conformer, diffusers==0.29.0,
#            transformers==4.51.3, numpy==1.26.4, soundfile, librosa, x-transformers, inflect, wetext (the frontend's
#            fallback when ttsfrd is absent), hydra-core, onnx, protobuf, pydantic, rich, networkx.
#   openai-whisper==20231117: its setup.py adds `triton>=2.0.0,<3` on Linux x86_64 (VERIFIED), which would install a
#            CUDA triton into the venv in front of the image's ROCm triton; so it goes in with --no-deps and its runtime
#            dependencies (requirements.txt: numba numpy tqdm more-itertools tiktoken; torch is the image's) named.
# The filtered file is written INSIDE the container as atlas (root never writes into the atlas-writable checkout).
# An import smoke check (no GPU) runs in the build: a missing module is a named BUILD failure (rule §7.4), not a
# GPU-test failure hours later.
# Test: cosyvoice2_test.py -> cosyvoice2_0.wav.
P4_KEY="cosyvoice2"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

read -r -d '' P4_REQ_PY <<'PY' || true
import re, sys
src, dst = sys.argv[1:3]
drop = re.compile(
    r"^\s*(torch|torchaudio|torchvision|onnxruntime|deepspeed|pynini|WeTextProcessing|ttsfrd|tensorrt|vllm|flash[-_]attn"
    r"|grpcio|grpcio-tools|pyworld|gradio|fastapi|fastapi-cli|uvicorn|tensorboard|lightning|gdown|wget|matplotlib|pyarrow"
    r"|openai-whisper)\s*([=<>!~;\[]|$)",
    re.I,
)
skip = re.compile(r"^\s*(#|$|--)")
lines = open(src, encoding="utf-8").read().splitlines()
keep = [ln for ln in lines if not drop.match(ln) and not skip.match(ln)]
dropped = [ln for ln in lines if drop.match(ln)]
open(dst, "w", encoding="utf-8").write("\n".join(keep) + "\n")
print(f"kept {len(keep)} of {len(lines)} lines; dropped: {dropped}", file=sys.stderr)
if not keep:
    sys.exit("filtered requirements file is empty")
if not any(ln.lower().startswith("modelscope") for ln in keep):
    sys.exit("modelscope is missing from the filtered file but cosyvoice/cli/cosyvoice.py imports it")
PY

p4_build() {
  p4_git_from_json
  local src="$P4_HOST_SRC/CosyVoice"
  [[ -f "$src/requirements.txt" && ! -L "$src/requirements.txt" ]] || die "$P4_KEY: $src/requirements.txt not found after the clone (upstream layout changed?)"
  [[ -d "$src/third_party/Matcha-TTS/matcha" ]] || die "$P4_KEY: $src/third_party/Matcha-TTS/matcha missing (the submodule did not land; the clone is recursive)"
  p4_docker_run -- python3.12 -c "$P4_REQ_PY" "$P4_SRC/CosyVoice/requirements.txt" "$P4_SRC/CosyVoice/requirements-atlas.txt" \
    || die "$P4_KEY: filtering requirements.txt failed"
  p4_note "requirements filtered in-container (torch/onnxruntime/pynini/grpcio/pyworld and serving-only pins dropped, UNVERIFIED workaround)"
  p4_venv_pip -r "$P4_SRC/CosyVoice/requirements-atlas.txt" onnxruntime soundfile
  # whisper without its triton pin (header).
  p4_venv_pip --no-deps "openai-whisper==20231117"
  p4_venv_pip numba tiktoken more-itertools tqdm
  local marker
  marker="$(p4_marker import-installed)"
  if [[ -e "$marker" ]]; then
    log "$P4_KEY: import smoke check already passed"
  else
    p4_in_venv -e "PYTHONPATH=$P4_SRC/CosyVoice:$P4_SRC/CosyVoice/third_party/Matcha-TTS" -- \
        python -c 'import cosyvoice.cli.cosyvoice; from cosyvoice.cli.cosyvoice import CosyVoice2; import cosyvoice.cli.frontend; import soundfile, onnxruntime, whisper, wetext, modelscope; print("cosyvoice import ok")' \
      || die "$P4_KEY: 'import cosyvoice.cli.cosyvoice' failed in the venv (a dependency of the pinned CosyVoice commit is missing or incompatible with Python 3.12; the ModuleNotFoundError above names it; see $P4_LOG)"
    date -Is >"$marker"
  fi
  p4_pull
}

p4_main "$@"
