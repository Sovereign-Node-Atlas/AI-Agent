# Named 80B-to-123B-class models and uncensored engines: gpt-oss-120b, gpt-oss-120b Abliterated, Nemotron 3 Super (Q8), Qwen3-Coder-Next, Mistral Large 2 Uncensored

Research date 2026-10-15. Scope: each engine running ALONE on the A.T.L.A.S. node with about 164 GiB for weights + KV + compute buffers (Directive 3), quality first (Directive 4), uncensored engines retained (Directive 5). Facts only; no recommendations.

Conventions used in these notes:
- "primary" = read in full from the source (HF API JSON, raw card/LICENSE/config, vendor page, arXiv HTML, git tag) on the date given; "excerpt" = only seen in a search-result snippet; "reused" = verified on 2026-10-14 in `/home/user/AI-Agent/research_notes/ATLAS LLM roster refresh 2026/gap_closure_2026-10-14.md` and not re-read today.
- Byte counts are sums of `lfs.size` from `https://huggingface.co/api/models/<repo>/tree/main?recursive=true`, read 2026-10-15 unless marked reused. GB = bytes/1e9, GiB = bytes/2^30.
- KV-cache arithmetic: f16 K+V per token = 2 x (attention layers) x (KV heads) x (head_dim) x 2 bytes, from each model's `config.json` (read 2026-10-15). These are my computations; llama.cpp's real allocation (padding, compute buffers, recurrent state) is not measured here.
- Decode projections for dense models = 273 GB/s / file bytes (ceiling), then x 0.78 to 0.83 (the efficiency band implied by the measured dense rows cited in Key question 5). Projections are labelled as such.

---

## Key question 1: gpt-oss-120b (new checkpoint? native MXFP4 vs a BF16/F16 GGUF; reasoning effort; exact sizes; Artificial Analysis version drift; strong disciplines)

### Takeaway
No new general-purpose gpt-oss-120b checkpoint exists since August 2025 (the only new 120b from OpenAI is the safety-classifier fine-tune gpt-oss-safeguard-120b, September 2025). Because the experts ship natively in MXFP4, the highest-precision GGUF worth having is the 65.37 GB "F16" file (MXFP4 experts plus 16-bit attention/embeddings/router), only 1.98 GB larger than the current 63.39 GB plan; a full BF16 upcast (233.66 GB) neither fits nor adds information. The Artificial Analysis drop from 58 to 12 is an index-composition change (v2.x knowledge/maths/coding benchmarks to v4.3.2 agentic, long-horizon and knowledge-work tasks), not a change in the model.

### Cited Findings
**Checkpoint status**
- openai/gpt-oss-120b: createdAt 2025-08-04, lastModified 2025-08-26 (unchanged since August 2025); Apache 2.0; 117B total / 5.1B active; `GptOssForCausalLM`, 36 layers, 128 experts, 4 per token; max_position 131072 (reused, primary) — [HF API openai/gpt-oss-120b](https://huggingface.co/api/models/openai/gpt-oss-120b) (read 2026-10-14)
- The author=openai listing on HF (read 2026-10-15) contains gpt-oss-20b, gpt-oss-120b (both 2025-08-04), gpt-oss-safeguard-20b and gpt-oss-safeguard-120b (both 2025-09-18), privacy-filter (2026-04-17), circuit-sparsity (2025-12-11) and older Whisper/CLIP repos; no other gpt-oss text model (primary) — [HF API author=openai](https://huggingface.co/api/models?author=openai&limit=100)
- gpt-oss-safeguard-120b is "a safety reasoning model built-upon gpt-oss" for classifying content against a user-supplied policy; "These models are intended for safety use cases. For other applications, we recommend using gpt-oss models"; Apache 2.0; base_model_relation finetune; reasoning effort low/medium/high (primary) — [openai/gpt-oss-safeguard-120b card](https://huggingface.co/openai/gpt-oss-safeguard-120b/raw/main/README.md) (read 2026-10-15)

**Sizes and precision (MXFP4 experts are native)**
- openai/gpt-oss-120b original safetensors: 65,248,893,184 B = 65.249 GB = 60.768 GiB (15 files) (primary) — [HF tree openai/gpt-oss-120b](https://huggingface.co/api/models/openai/gpt-oss-120b/tree/main?recursive=true) (read 2026-10-15)
- ggml-org gpt-oss-120b-MXFP4.gguf: 63,387,346,208 B = 63.39 GB = 59.03 GiB (reused, primary) — [HF tree ggml-org/gpt-oss-120b-GGUF](https://huggingface.co/api/models/ggml-org/gpt-oss-120b-GGUF/tree/main?recursive=true) (read 2026-10-14)
- unsloth/gpt-oss-120b-GGUF ladder (read 2026-10-15, primary): F16 65,369,017,728 B = 65.369 GB = 60.880 GiB (1 file); UD-Q8_K_XL 64,473,222,720 B = 60.045 GiB; Q8_0 63,387,347,520 B = 59.034 GiB; Q6_K and UD-Q6_K_XL 63,284,496,960 B; UD-Q4_K_XL 63,016,311,392 B; Q4_K_M 62,768,723,552 B; Q4_0 62,620,023,392 B = 58.319 GiB — every rung is within 2.75 GB because the experts stay MXFP4 — [HF tree unsloth/gpt-oss-120b-GGUF](https://huggingface.co/api/models/unsloth/gpt-oss-120b-GGUF/tree/main?recursive=true)
- unsloth/gpt-oss-120b-BF16 (experts dequantised to BF16): 233,658,385,752 B = 233.658 GB = 217.611 GiB (73 safetensors files) (primary) — [HF tree unsloth/gpt-oss-120b-BF16](https://huggingface.co/api/models/unsloth/gpt-oss-120b-BF16/tree/main?recursive=true) (read 2026-10-15)
- The abliterix author describes the same upcast: "pre-dequantises the whole 65 GB MXFP4 checkpoint to a 232 GB BF16 safetensors checkpoint" (primary) — [wangzhang/gpt-oss-120b-abliterated card](https://huggingface.co/wangzhang/gpt-oss-120b-abliterated/raw/main/README.md) (read 2026-10-15)
- OpenAI card (via third-party repackage cards that copy it verbatim): "The models were post-trained with MXFP4 quantization of the MoE weights ... All evals were performed with the same MXFP4 quantization" (primary copy) — [llmfan46/gpt-oss-120b-heretic-v2 card](https://huggingface.co/llmfan46/gpt-oss-120b-heretic-v2/raw/main/README.md) (read 2026-10-15)
- KV geometry: 36 layers alternating 18 full-attention and 18 sliding-attention (window 128), 8 KV heads, head_dim 64 (primary) — [config.json](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json) (read 2026-10-15). Computed: 36,864 B/token for the full-attention layers, so one 131,072-token sequence = 4.5 GiB of f16 KV (the sliding layers hold only 128 tokens each).
- AA Endpoint Accuracy Index for gpt-oss-120b (self-hosted SGLang MXFP4 reference = 100): Bedrock 103.96 ... DeepInfra Turbo 84.66, Together AI 81.09, Google Vertex 72.2 (reused, primary) — [AA gpt-oss-120b providers](https://artificialanalysis.ai/models/gpt-oss-120b) (read 2026-10-14); Token Arena (arXiv 2605.00300): FP8/"Turbo" endpoints average fidelity 92.1 vs BF16-reference 99.7, MATH-500 -5.5 and AIME-25 -9.0 points (reused, primary) — [arXiv 2605.00300](https://arxiv.org/abs/2605.00300)

**Reasoning effort and per-effort results (vendor, model card Table 3, low / medium / high)**
- AIME 2025 no tools 50.4 / 80.0 / 92.5, with tools 72.9 / 91.6 / 97.9; GPQA Diamond no tools 67.1 / 73.1 / 80.1, with tools 68.1 / 73.5 / 80.9; HLE no tools 5.2 / 8.6 / 14.9, with tools 9.1 / 11.3 / 19.0; MMLU 85.9 / 88.0 / 90.0; SWE-Bench Verified 47.9 / 52.6 / 62.4; Tau-Bench Retail 49.4 / 62.0 / 67.8; Tau-Bench Airline 42.6 / 48.6 / 49.2; Aider Polyglot 24.0 / 34.2 / 44.4; MMMLU 74.1 / 79.3 / 81.3; HealthBench 53.0 / 55.9 / 57.6; HealthBench Hard 22.8 / 26.9 / 30.0; HealthBench Consensus 90.6 / 90.8 / 89.9 (primary) — [gpt-oss model card, arXiv 2508.10925 HTML](https://arxiv.org/html/2508.10925v1) (read 2026-10-15)
- AA v4.3.2: gpt-oss-120b (High) Index 12 (raw 11.60); GPQA 78.2, HLE 19.6, SciCode 34.0, AA-LCR 52.0, IFBench 69.0, LiveCodeBench 87.8, AIME25 93.4, tau2 65.8, TB Hard 23.5, TB2.1 26.2, TB4.0 0.0, CritPt 1.1, AA-Omniscience -49.25, GDPval-AA 608.65, AutomationBench 0.2 (reused, primary) — [AA gpt-oss-120b](https://artificialanalysis.ai/models/gpt-oss-120b) (read 2026-10-14)
- AA v4.3.2: gpt-oss-120b (Low) "scores 10 on the Artificial Analysis Intelligence Index"; page record GPQA 0.672, HLE 0.059, AA-LCR 0.46, IFBench 0.583, tau2 0.450, GDPval-AA 172.35, CritPt 0 (primary; component values are the first occurrence in the page's embedded record) — [AA gpt-oss-120b (Low)](https://artificialanalysis.ai/models/gpt-oss-120b-low) (read 2026-10-15)

**Artificial Analysis version drift (58 -> 33.3 -> 24 -> 12)**
- 58 on the launch-era index, August 2025 (excerpt only; the AA X post was not reachable and the Wayback Machine has no snapshot of the model page for 2025-08-20 or 2025-10-01) — [AA on X](https://x.com/ArtificialAnlys/status/1952887733803991070); [Wayback availability API](https://archive.org/wayback/available?url=artificialanalysis.ai/models/gpt-oss-120b&timestamp=20250820) (checked 2026-10-15)
- 33.3 in AA's Nemotron 3 Ultra article dated June 4, 2026 (same article: Nemotron 3 Super 36.0) (reused, primary) — [AA Nemotron 3 Ultra article](https://artificialanalysis.ai/articles/nvidia-nemotron-3-ultra-released)
- 24 for the High variant on an English page labelled v4.1 (excerpt; search-engine cache, not re-read) — [WebSearch result for AA gpt-oss-120b](https://artificialanalysis.ai/models/gpt-oss-120b)
- AA methodology "Version History" (primary, read 2026-10-15) — [AA Intelligence Benchmarking Methodology](https://artificialanalysis.ai/methodology/intelligence-benchmarking):
  - v2.1 (5-6 Aug 2025) added IFBench and AIME 2025; v2.2 (6 Aug-1 Sep 2025) added AA-LCR; v3.0 (2 Sep-Dec 2025) added Terminal-Bench Hard and tau2-Bench Telecom and included MMLU-Pro and LiveCodeBench.
  - v4.0 (Jan 2026) "Added GDPval-AA ... AA-Omniscience ... CritPt" and "Removed MMLU-Pro, LiveCodeBench, AIME 2025"; weighting Agents/Coding/General/Scientific Reasoning 25% each.
  - v4.1 (Jun-Aug 2026): GDPval-AA v2, Terminal-Bench Hard replaced by Terminal-Bench 2.1, tau2 Telecom superseded by tau3-Banking; IFBench "removed from the Intelligence Index in v4.1".
  - v4.2 (Sep 2026): "Added AA-Briefcase (15%) ... Added GDP.pdf (10%) ... Removed GPQA Diamond".
  - v4.3 (Sep 2026): AutomationBench-AA (5%) replaces tau3-Banking; Terminal-Bench 4.0 (66 tasks, mini-swe-agent) replaces 2.1. v4.3.1 refreshed judge panels; v4.3.2 re-anchored GDPval-AA v2.1 Elo to "DeepSeek V4.1 Flash (max) at 1600" with Crowd-BT fitting.
  - Current weights: Agents 30%, Coding 20%, Scientific Reasoning 20%, General 30%; GDPval-AA and AA-Briefcase Elo enter as clamp((Elo - 500) / 2000).

**Disciplines where gpt-oss-120b is strong or weak (independent where marked)**
- HealthBench 57.6 / Hard 30.0 at high effort (vendor) — [arXiv 2508.10925](https://arxiv.org/html/2508.10925v1)
- Vals LegalBench 75.94%, rank 115 of 151 (independent, reused) — [Vals LegalBench](https://www.vals.ai/benchmarks/legal_bench)
- LMArena text 1365.4, rank 191 of 415 (independent, reused, publish date 2026-10-13) — [LMArena](https://lmarena.ai/leaderboard)
- EQ-Bench Creative Writing v3 Elo 961.0, rank 118 of 140 (independent, reused) — [EQ-Bench](https://eqbench.com/creative_writing.html)
- SWE-bench Verified official board 26.0% (mini-SWE-agent, 2025-08-07) vs vendor 62.4%; SWE-bench Pro 16.2 (independent, reused) — [SWE-bench](https://www.swebench.com/); [Scale SWE-bench Pro](https://scale.com/leaderboard/swe_bench_pro_public)
- SWE-rebench (independent, decontaminated, primary, read 2026-10-15): gpt-oss-120b (tools) 27.3% resolved over tasks 2025-08 to 2026-03 (pass@5 41.8); gpt-oss-120b-high 37.0% on the 2025-12 window only (pass@5 62.5; SEM 3.5) — [SWE-rebench](https://swe-rebench.com/)
- Aider polyglot official: gpt-oss-120b (high) 41.8% (vendor 44.4%) (independent, reused) — [Aider leaderboard](https://aider.chat/docs/leaderboards/)

### Inferences
- The size equality of ggml-org's "MXFP4" file (63,387,346,208 B) with unsloth's "Q8_0" (63,387,347,520 B) indicates the plan's file keeps the non-expert tensors (attention, router, embeddings, output) at Q8_0, and the 65.37 GB "F16" file keeps them at 16 bits; the 1.98 GB difference is exactly those tensors. The F16 GGUF is therefore the highest-precision llama.cpp form of the released checkpoint (the experts are bit-identical MXFP4 in both). Its quality gain over the MXFP4/Q8_0 file is unmeasured but bounded (only the non-expert ~4% of weights change precision); its decode cost is a small increase in bytes read per token. Whether the BF16-to-F16 conversion is bit-exact for every tensor was not checked.
- A full BF16 upcast GGUF (about 234 GB class) does not fit the 164 GiB budget and could not gain accuracy, because the upcast reproduces the same 4-bit expert values.
- The AA decline tracks composition: v4.3.2's components on which gpt-oss scores near zero (Terminal-Bench 4.0 0.0, AutomationBench 0.2, CritPt 1.1, GDPval-AA 608.65 which normalises to 0.054) carry most of the weight, while the benchmarks it scores highly on (AIME 93.4, LiveCodeBench 87.8, GPQA 78.2, IFBench 69) were removed in v4.0, v4.1 and v4.2. Scores from different index versions must not be compared.
- Its profile is strongest on graduate-level science QA, competition maths, health QA and tool-calling retail tasks, and weak on creative writing, long-horizon agentic coding and current knowledge-work agent tasks.

### Gaps
- The 58 (Aug 2025) figure rests on a search excerpt of an AA X post; no archived page was retrievable.
- No measurement of F16-GGUF vs MXFP4/Q8_0-GGUF quality or speed for gpt-oss-120b on any hardware.
- gpt-oss-120b decode on the PRO 495 node is unmeasured (128 GB Max+ 395 systems: 53-56 t/s MXFP4 on Vulkan, per the earlier notes `strix_halo_capacity_performance.md`).

---

## Key question 2: gpt-oss-120b Abliterated (every variant, method, sizes, KL, refusals, measured quality loss, huihui status)

### Takeaway
At least fifteen uncensored gpt-oss-120b builds exist; none is from huihui-ai any more (its three 120b repos return HTTP 401 again on 2026-10-15, which on the HF API means private or deleted, not gated). The one independent cross-variant measurement found (the UGI leaderboard) shows huihui's v1 abliteration nearly halving the base model's NatInt score (16.72 vs 33.68), while Heretic and norm-preserving builds keep NatInt at or above base (28.4 to 35.7). The node's current file (noctrex MXFP4_MOE re-quantisation of huihui v2) has no published KL, refusal or benchmark figures and an unreadable provenance card. Higher-precision options now fit alone: 65.37 GB files with 16-bit non-expert tensors (Heretic, HauhauCS) and a 124.30 GB Q8_0 for builds that edited the experts (wangzhang EGA).

### Cited Findings
**huihui-ai status**
- On 2026-10-15 the HF API returns HTTP 401 for huihui-ai/Huihui-gpt-oss-120b-BF16-abliterated, -BF16-abliterated-v2 and -mxfp4-abliterated; the author=huihui-ai search for "gpt-oss" lists only Huihui-gpt-oss-20b-mxfp4-abliterated-v2, -20b-BF16-abliterated and -20b-BF16-abliterated-v2 (primary) — [HF API huihui-ai v2](https://huggingface.co/api/models/huihui-ai/Huihui-gpt-oss-120b-BF16-abliterated-v2); [author search](https://huggingface.co/api/models?author=huihui-ai&search=gpt-oss&limit=50)
- Contrast observed the same day: a gated repo (mistralai/Mistral-Large-Instruct-2407) returns 200 on the API with `"gated":"auto"` and 401 only on its raw files, so an API-level 401 is the private-or-deleted response, not gating (primary) — [HF API Mistral-Large-Instruct-2407](https://huggingface.co/api/models/mistralai/Mistral-Large-Instruct-2407)
- huihui-ai/Huihui-Mistral-Medium-3.5-128B-abliterated also returns 401 (card and API) on 2026-10-15, though a derivative (sakamakismile/...-NVFP4, 2026-05-11) still cites it (primary) — [HF API](https://huggingface.co/api/models/huihui-ai/Huihui-Mistral-Medium-3.5-128B-abliterated)

**Variants (HF search for gpt-oss-120b + abliterated / heretic / uncensored / derestricted / jinx, read 2026-10-15)** — [HF search API](https://huggingface.co/api/models?search=gpt-oss-120b-heretic&limit=100)

| Variant (created) | Method (card) | GGUF and exact size | Published KL / refusals | Independent quality (UGI, see below) |
|---|---|---|---|---|
| huihui v1 BF16-abliterated (2025-08; source now 401) | classic abliteration (card gone) | mradermacher Q8_0 124,301,419,840 B = 115.765 GiB; Q6_K 124,198,569,280 B; Q4_K_M 87,850,987,840 B (reused) | none recoverable | UGI 26.09, W/10 5.2, NatInt 16.72 |
| huihui v2 BF16-abliterated -> noctrex MXFP4_MOE (2025-10-25, current node file) | BF16 abliteration re-quantised to MXFP4_MOE | 63,387,346,976 B = 59.034 GiB, 4 shards (reused) | none | not tested |
| huihui mxfp4-abliterated -> mradermacher | as above | MXFP4_MOE 63,387,347,072 B; Q8_0 124,301,420,672 B (reused) | none | not tested |
| kldzj/gpt-oss-120b-heretic v1 (2025-11-17) | Heretic v1.0.1 | bartowski GGUF exists (not sized) | KL 0.92; 19/100 vs 97/100 (reused) | UGI 46.78, W/10 9.5, NatInt 33.41 |
| kldzj/gpt-oss-120b-heretic-

> **Coordinator note (2026-10-15):** this file stops mid-table. The abliterated-variant table and the sections on Nemotron 3 Super, Qwen3-Coder-Next and Mistral Large 2 Uncensored were not written here. Their facts were re-verified from primary sources in `verification_named_120b_class_and_uncensored.md` ("Gaps closed") and `completeness_review.md`, which the report relies on.
