#!/usr/bin/env bash
# phase4/engines/cosyvoice2.sh — CosyVoice2-0.5B, green per Section 15.2 (the research calls it yellow because the
# upstream requirements.txt pins torch/onnxruntime and needs pynini/OpenFst; rocm-containers.md §3.9). Kept green as
# the document says; a failure is a recorded fail, never a block.
# UNVERIFIED workaround (research §3.9): requirements.txt is filtered (torch*, onnxruntime*, deepspeed, pynini,
# WeTextProcessing, ttsfrd, tensorrt, vllm, flash-attn dropped) and installed under the ROCm constraints file; the
# test uses the model's own frontend without pynini text normalisation, replaces upstream load_wav (torchaudio.load,
# which needs torchcodec on torchaudio >= 2.9) with a soundfile loader and writes with soundfile.
# The filtered file is written INSIDE the container as atlas (root never writes into the atlas-writable checkout).
# Test: cosyvoice2_test.py -> cosyvoice2_0.wav.
P4_KEY="cosyvoice2"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

read -r -d '' P4_REQ_PY <<'PY' || true
import re, sys
src, dst = sys.argv[1:3]
drop = re.compile(r"^\s*(torch|torchaudio|torchvision|onnxruntime|deepspeed|pynini|WeTextProcessing|ttsfrd|tensorrt|vllm|flash[-_]attn)", re.I)
skip = re.compile(r"^\s*(#|$|--)")
lines = open(src, encoding="utf-8").read().splitlines()
keep = [ln for ln in lines if not drop.match(ln) and not skip.match(ln)]
open(dst, "w", encoding="utf-8").write("\n".join(keep) + "\n")
print(f"kept {len(keep)} of {len(lines)} lines", file=sys.stderr)
if not keep:
    sys.exit("filtered requirements file is empty")
PY

p4_build() {
  p4_git_from_json
  local src="$P4_HOST_SRC/CosyVoice"
  [[ -f "$src/requirements.txt" && ! -L "$src/requirements.txt" ]] || die "$P4_KEY: $src/requirements.txt not found after the clone (upstream layout changed?)"
  p4_docker_run -- python3.12 -c "$P4_REQ_PY" "$P4_SRC/CosyVoice/requirements.txt" "$P4_SRC/CosyVoice/requirements-atlas.txt" \
    || die "$P4_KEY: filtering requirements.txt failed"
  p4_note "requirements filtered in-container (torch/onnxruntime/pynini pins dropped, UNVERIFIED workaround)"
  p4_venv_pip -r "$P4_SRC/CosyVoice/requirements-atlas.txt" onnxruntime soundfile
  p4_pull
}

p4_main "$@"
