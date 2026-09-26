#!/usr/bin/env bash
# phase4/engines/cosyvoice2.sh — CosyVoice2-0.5B, green per Section 15.2 (the research calls it yellow because the
# upstream requirements.txt pins torch/onnxruntime and needs pynini/OpenFst; rocm-containers.md §3.9). Kept green as
# the document says; a failure is a recorded fail, never a block.
# UNVERIFIED workaround (research §3.9): requirements.txt is filtered (torch*, onnxruntime*, deepspeed, pynini,
# WeTextProcessing, ttsfrd, tensorrt, vllm, flash-attn dropped) and installed under the ROCm constraints file; the
# test uses the model's own frontend without pynini text normalisation. Test: cosyvoice2_test.py -> cosyvoice2_0.wav.
P4_KEY="cosyvoice2"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

p4_build() {
  p4_git_from_json
  local src="$P4_HOST_SRC/CosyVoice" req
  [[ -f "$src/requirements.txt" ]] || die "$P4_KEY: $src/requirements.txt not found after the clone (upstream layout changed?)"
  req="$src/requirements-atlas.txt"
  # Drop every pin that would replace the ROCm torch or needs a CUDA/OpenFst build; keep everything else verbatim.
  grep -viE '^\s*(torch|torchaudio|torchvision|onnxruntime|deepspeed|pynini|WeTextProcessing|ttsfrd|tensorrt|vllm|flash[-_]attn)' "$src/requirements.txt" \
    | grep -vE '^\s*(#|$|--)' >"$req" || true
  [[ -s "$req" ]] || die "$P4_KEY: the filtered requirements file is empty"
  chown "$P4_UID:$P4_GID" "$req"
  p4_note "requirements filtered: $(wc -l <"$req") lines kept of $(wc -l <"$src/requirements.txt") (torch/onnxruntime/pynini pins dropped, UNVERIFIED workaround)"
  p4_venv_pip -r "$P4_SRC/CosyVoice/requirements-atlas.txt" onnxruntime
  p4_pull
}

p4_main "$@"
