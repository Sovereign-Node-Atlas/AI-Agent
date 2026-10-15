# Single-engine performance on the A.T.L.A.S. node and the cost of sequential engine swapping (as of 2026-10-15)

Scope and conventions. Research date 2026-10-15. Facts and arithmetic, not recommendations. "Primary" = the page, data file, source file or GGUF header was opened and read in this pass; "excerpt" = only a search-result summary was seen; "prior-verified" = read from a primary source on 2026-10-13/14/15 and recorded in the earlier notes under `/home/user/AI-Agent/research_notes/`. Every measured speed carries device, backend, build and quant. Every number marked PROJECTION is arithmetic, not a measurement. Sizes: decimal GB = bytes/1e9, GiB = bytes/2^30. "395" = Ryzen AI Max+ 395 with LPDDR5X-8000 (256 GB/s theoretical); "PRO 495" = Ryzen AI Max+ PRO 495 with LPDDR5X-8533 (273 GB/s theoretical; 8533/8000 = +6.7 %, usually quoted as +6.6 %).

New primary evidence produced for this note (method, so the writer can reuse it):
- Active bytes per decoded token were computed for 41 GGUF rungs by reading each shard's GGUF header over HTTP range requests from Hugging Face (tensor table, offsets, `expert_count`, `expert_used_count`, `context_length`) and summing: every non-expert tensor except the token-embedding table (and except Qwen3.8-Flash-Next's per-layer n-gram embedding table, which is a row lookup) + the routed-expert tensors × (experts used / experts total). Script: `/tmp/claude-0/-home-user-AI-Agent/552512b0-9757-5ccd-b8aa-228a3e10ea95/scratchpad/sep/scripts/gguf_active.py`; raw output: `/tmp/claude-0/-home-user-AI-Agent/552512b0-9757-5ccd-b8aa-228a3e10ea95/scratchpad/sep/active.jsonl`. Repos read: unsloth/{DeepSeek-V4-Flash-0731, Qwen3-235B-A22B-Instruct-2507, GLM-5.3-Flash, Llama-4-Maverick-17B-128E-Instruct, Llama-4-Scout-17B-16E-Instruct, Qwen3.8-Flash-Next, Qwen3.5-122B-A10B, NVIDIA-Nemotron-3-Super-120B-A12B, Qwen3-Coder-Next, Qwen3.5-397B-A17B, Step-3.7-Flash, MiniMax-M2.7, Qwen3.6-35B-A3B}-GGUF; ggml-org/{gpt-oss-120b, DeepSeek-V4-Flash-0731, Qwen3.8-Flash-Next, MiMo-V2.6-Flash-RL}-GGUF; bartowski/Mistral-Large-Instruct-2407-GGUF; stepfun-ai/Step-3.7-Flash-GGUF; cafonez/GLM-5.3-Flash-Gorgon-GGUF (tree URL pattern `https://huggingface.co/api/models/<repo>/tree/main?recursive=true`, shards fetched from `https://huggingface.co/<repo>/resolve/main/<path>`).
- Decode PROJECTION method: tok/s ≈ 273 GB/s × efficiency ÷ active GB per token, where "efficiency" is taken from measured models: efficiency = measured tg × active GB per token ÷ theoretical bandwidth of the machine that measured it. This ignores KV-cache reads (so it is a depth-0 figure) and assumes the PRO 495 reaches the same fraction of its theoretical bandwidth as the 395.

## Key question 1: Measured and projected decode and prompt-processing speed for each candidate running alone

### Takeaway
Decode on gfx1151 is set by bytes read per token: measured stock-llama.cpp Vulkan MoE runs reach 60-80 % of theoretical bandwidth (gpt-oss-120b 79.5 %, Qwen3.5-122B 74 %, Nemotron 3 Super 66-77 %, Llama 4 Scout 71 %, Flash-Next 61-62 %), and the two measurements on the PRO 495 itself (GLM-5.3-Flash 3.84 bpw 17.4 tok/s Vulkan; MiMo-V2.6-Flash MXFP4 18.3 tok/s ROCm) fall in the same band (66 % and 73 %); DeepSeek-V4-Flash on mainline llama.cpp is the exception at 43-45 % (tuned forks 66-67 %). Projected depth-0 decode at the least-quantised rungs that fit alone: gpt-oss-120b MXFP4 57-60 tok/s; Qwen3-Coder-Next Q8_0 41-51 (BF16 only about 9 on Vulkan, about 23 on ROCm); Qwen3.8-Flash-Next 23-29; Qwen3.5-122B Q8_0 17-21; Maverick 2-3-bit 18-26; Qwen3.5-397B 3-bit 13-20; Nemotron 3 Super Q8_0 13.6-15.8; GLM-5.3-Flash 4-bit 12.5-14; Qwen3-235B / Baichuan-M3 Q5_K_S 11-13; DeepSeek-V4-Flash UD-Q4_K_XL 11 and UD-Q8_K_XL about 7 on mainline Vulkan; Mistral Large 2 dense Q8_0 1.7-1.8 and Q6_K 2.2-2.3.

### Cited Findings

**Calibration points (measured decode at depth 0, converted to effective bandwidth with the active bytes computed from GGUF headers)**

| Model, rung (file GiB) | Active GB/token (computed) | Measured tg tok/s | Device, backend, build, date | Effective GB/s | % of theoretical |
|---|---:|---:|---|---:|---:|
| gpt-oss-120b MXFP4 (59.03) | 3.594 | 56.61 | 395, Vulkan RADV, b9187, results generated 2026-05-18 | 203 | 79.5 % |
| gpt-oss-120b MXFP4 (59.03) | 3.594 | 53.8 | 395, Vulkan, upstream 254b17730, 2 Oct 2026 | 193 | 75.5 % |
| Qwen3.5-122B-A10B UD-Q5_K_XL (85.61) | 8.503 | 22.3 | 395, Vulkan RADV, b9193 | 190 | 74.1 % |
| Nemotron 3 Super UD-Q4_K_XL (78.02) | 11.379 | 14.86 | 395, Vulkan RADV, b9193 | 169 | 66.1 % |
| Nemotron 3 Super UD-IQ4_XS (60.06) | 10.379 | 18.93 | 395, Vulkan RADV, b9544, 7 Jun 2026 | 196 | 76.7 % |
| MiniMax-M2.7 UD-Q3_K_S (87.21) | 6.401 | 31.08 | 395, Vulkan RADV, b9187 | 199 | 77.7 % |
| Llama 4 Scout Q4_K_M (60.87, unsloth file used for bytes) | 9.911 | 18.32 | 395, Vulkan RADV, b8933, April 2026 | 182 | 70.9 % |
| Qwen3.6-35B-A3B UD-Q8_K_XL / UD-Q4_K_XL / BF16 | 3.281 / 2.753 / 5.937 | 46.33 / 60.43 / 10.68 | 395, Vulkan RADV, b9193 | 152 / 166 / 63 | 59.4 / 65.0 / 24.8 % |
| Qwen3.6-35B-A3B BF16 | 5.937 | 26.01 | 395, ROCm 7.2.3, b9193 | 154 | 60.3 % |
| GLM-5.3-Flash UD-IQ1_S (86.69) | 8.714 | 17.63 | 395, Vulkan, unsloth glm5next branch 86ebfef2c, 29 Sep 2026 | 154 | 60.0 % |
| GLM-5.3-Flash Gorgon Q5K-IQ3S (143.54) | 10.398 | 17.4 | **PRO 495**, 192 GB, Ubuntu 26.04, kernel 7.0, Mesa 26.0.8 RADV, Vulkan, ROCmFPX main ("plain llama.cpp master runs the file at the same decode speed") | 181 | 66.3 % of 273 |
| MiMo-V2.6-Flash-RL MXFP4 (155.87) | 10.866 | 18.3 | **PRO 495** ("gorgon-halo"), llama.cpp 0.5.0-dev build 11148 (6e60f3560), ROCm 10.0, 23 Sep 2026 | 199 | 72.8 % of 273 |
| Qwen3.8-Flash-Next UD-IQ4_XS (87.25) | 5.839 | 26.85 / 27.16 | 395, Vulkan, PR #28243 build (28 Sep) / strix-halo-guide 30 Aug 2026 | 157 / 159 | 61.2 / 61.9 % |
| DeepSeek-V4-Flash-0731 UD-IQ2_XXS (84.62) | 8.654 | 12.6 / 13.27 | 395, stock llama.cpp Vulkan RADV, 2 Sep 2026 / b10034 | 109 / 115 | 42.6 / 44.9 % |
| DeepSeek-V4-Flash-0731 UD-IQ2_XXS | 8.654 | 19.7 | 395, "llama.cpp performance fork" Vulkan RADV, 2 Sep 2026 | 170 | 66.6 % |
| DeepSeek-V4-Flash-0731 UD-IQ3_XXS (97.05) | 9.216 | 11.94 / 18.4 | 395, mainline b10488 Vulkan / Nathanw1014 fork Vulkan | 110 / 170 | 43.0 / 66.2 % |
| Qwen3-235B-A22B Q3_K_M (104.72) | 10.795 | 17.2 | 395, "best observed configuration"; primary backend ROCm 7.0 nightlies, secondary Vulkan RADV, row backend not labelled | 186 | 72.5 % |

Sources: [kyuz0 results.json](https://kyuz0.github.io/amd-strix-halo-toolboxes/results.json) (generated 2026-05-18; Fedora 43, kernel 6.19.12, builds 9187/9193, all runs `-fa 1`, mmap 0); [huppiflupp/strix-halo-llm-speeds README](https://github.com/huppiflupp/strix-halo-llm-speeds) (measurements August to 29 September 2026, 395, Nobara 44, kernel 7.1-7.2); [strix-halo-guide BENCHMARKS.md](https://github.com/hogeheer499-commits/strix-halo-guide/blob/main/BENCHMARKS.md); [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json) (114 jobs, read 2026-10-15; coding-agent depth curves, pp 2048 / tg 256 per depth, temperature 0); [cafonez/GLM-5.3-Flash-Gorgon-GGUF card](https://huggingface.co/cafonez/GLM-5.3-Flash-Gorgon-GGUF/raw/main/README.md) (repo lastModified 2026-10-02); [visorcraft/strix-halo-llm-perf README](https://raw.githubusercontent.com/visorcraft/strix-halo-llm-perf/main/README.md) (latest finding dated 3 Mar 2026). Active bytes: GGUF headers (primary, this pass). All primary.

**Measured numbers on the PRO 495 (the node's chip) found anywhere**
- GLM-5.3-Flash Gorgon 3.84 bpw (143.54 GiB), Vulkan: prefill 2048 tokens 256.4 tok/s, decode 17.4; at 32K context prefill 95.9, decode 15.0; "Plain llama.cpp `master` runs the file at the same decode speed; prefill is lower there"; the format needs llama.cpp PR #27773 (merged 2026-09-30, i.e. after the v0.4.1 pin). Primary — [cafonez card](https://huggingface.co/cafonez/GLM-5.3-Flash-Gorgon-GGUF/raw/main/README.md); pin status prior-verified — [named_heavyweight_moe.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/named_heavyweight_moe.md).
- MiMo-V2.6-Flash-RL MXFP4 (155.87 GiB; MiMo support merged 2026-09-22, outside the pin): llama.cpp 0.5.0-dev build 11148, ROCm 10.0, decode 18.3 → 17.1 (32K) → 16.2 (64K); prefill 477.4 → 353.9 → 283.2. Primary — [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json) (run 2026-09-23).
- Non-llama.cpp engine "DwarfStar" (`ds4-server`, ROCm 10.0) on the PRO 495: DeepSeek-V4.1-Flash "Q2" (antirez GGUF) decode 14.4-14.8 at depth 0 and 14.2-14.3 at 64K, prefill 240.9-391.6; with DSpark 16.3. GLM-5.3-Flash "Mixed 138GB" 7.1-7.6 decode, prefill 116; GLM "Q2" 11.9 decode, prefill 227. Primary — [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json) (runs 24-30 Sep 2026).
- GMKtec EVO-X5 Pro customer review (undated, anecdotal): "Very quiet. Runs qwen 3.8 flash next q8_0 at 15 tokens in ubuntu 26.04 via lm studio." Primary — [GMKtec product page](https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc) (published 2026-09-28, updated 2026-10-15).
- Framework's Gorgon Halo two-model setup lists "DeepSeek V4 Flash IQ2XXS with DSpark" at tg 15 and "Qwen 3.8 Flash Next Q2 native MTP tg 23/25". Prior-verified — [Framework community](https://community.frame.work/t/85191).

**Active bytes per token computed from GGUF headers for the candidate rungs (primary, this pass)**

| Rung (repo) | File bytes | GiB | Routed-expert GB | Non-routed GB read per token | Active GB/token |
|---|---:|---:|---:|---:|---:|
| DeepSeek-V4-Flash-0731 UD-Q8_K_XL (unsloth) | 161,869,615,520 | 150.75 | 147.17 | 13.63 | 17.085 |
| DeepSeek-V4-Flash-0731 UD-Q4_K_XL (unsloth) | 155,095,241,120 | 144.44 | 147.17 | 7.36 | 10.807 |
| DeepSeek-V4-Flash-0731 MXFP4 (ggml-org) | 154,991,539,328 | 144.35 | 147.17 | 7.25 | 10.703 |
| DeepSeek-V4-Flash-0731 UD-IQ4_XS (unsloth) | 136,662,446,656 | 127.28 | 128.85 | 7.24 | 10.265 |
| Qwen3-235B-A22B-Instruct-2507 Q5_K_S (unsloth) | 161,881,426,816 | 150.76 | 156.13 | 5.32 | 15.077 |
| Qwen3-235B-A22B-Instruct-2507 Q4_K_M | 142,154,074,880 | 132.39 | 137.29 | 4.51 | 13.087 |
| Qwen3-235B-A22B-Instruct-2507 UD-Q4_K_XL | 134,329,371,392 | 125.10 | 129.35 | 4.62 | 12.705 |
| GLM-5.3-Flash UD-IQ4_XS (unsloth) | 156,822,111,200 | 146.05 | 147.13 | 9.01 | 13.100 |
| GLM-5.3-Flash UD-Q3_K_XL (unsloth) | 147,535,922,080 | 137.40 | 137.84 | 9.01 | 12.842 |
| Llama-4-Maverick UD-Q2_K_XL (unsloth) | 152,655,890,592 | 142.17 | 144.20 | 7.86 | 8.987 |
| Llama-4-Maverick UD-IQ3_XXS | 169,314,814,080 | 157.69 | 161.19 | 7.53 | 8.792 |
| Llama-4-Maverick Q3_K_S | 172,655,990,400 | 160.80 | 166.09 | 6.11 | 7.402 |
| Qwen3.8-Flash-Next UD-Q6_K_XL (unsloth; n-gram table 54.4 GB excluded) | 169,165,382,688 | 157.55 | 109.25 | 4.83 | 6.964 |
| Qwen3.8-Flash-Next Q8_0 (ggml-org; n-gram table 28.8 GB excluded) | 162,624,826,784 | 151.46 | 128.35 | 4.79 | 7.299 |
| Qwen3.8-Flash-Next UD-Q5_K_XL | 158,286,406,650 | 147.42 | 98.37 | 4.83 | 6.751 |
| Qwen3.8-Flash-Next UD-Q4_K_XL | 111,334,654,784 | 103.69 | 77.02 | 4.83 | 6.334 |
| Qwen3.5-122B-A10B Q8_0 (unsloth) | 129,871,935,104 | 120.95 | 123.21 | 5.84 | 9.689 |
| Nemotron 3 Super Q8_0 (unsloth) | 128,472,991,680 | 119.65 | 119.79 | 8.10 | 13.253 |
| Nemotron 3 Super UD-Q8_K_XL | 132,485,760,992 | 123.39 | 123.75 | 8.15 | 13.472 |
| gpt-oss-120b MXFP4 (ggml-org) | 63,387,346,208 | 59.03 | 61.07 | 1.69 | 3.594 |
| Qwen3-Coder-Next Q8_0 (unsloth) | 84,812,255,584 | 78.99 | 82.14 | 2.33 | 3.939 |
| Qwen3-Coder-Next BF16 (unsloth) | 159,458,415,168 | 148.51 | 154.62 | 4.21 | 7.231 |
| Mistral-Large-Instruct-2407 Q8_0 (bartowski, dense) | 130,280,380,512 | 121.33 | — | 129.85 | 129.852 |
| Mistral-Large-Instruct-2407 Q6_K (dense) | 100,586,280,928 | 93.68 | — | 100.26 | 100.255 |
| Qwen3.5-397B-A17B UD-IQ3_XXS (unsloth) | 140,333,676,736 | 130.70 | 131.87 | 7.62 | 10.195 |
| Qwen3.5-397B-A17B UD-Q3_K_S | 164,323,293,504 | 153.04 | 154.01 | 9.46 | 12.471 |
| Step-3.7-Flash Q4_K_S (stepfun-ai official) | 111,499,087,872 | 103.84 | 107.40 | 3.81 | 6.785 |
| Step-3.7-Flash UD-Q6_K (unsloth) | 163,340,821,184 | 152.12 | 156.07 | 6.70 | 11.042 |

Expert counts read from the GGUF metadata: deepseek4 6/256, qwen3moe 8/128, glm5-next 8/288, llama4 1/128 (Maverick) and 1/16 (Scout), qwen4exp 10/512, qwen35moe 8/256 (122B) and 10/512 (397B), nemotron_h_moe 22/512, gpt-oss 4/128, qwen3next 10/512, step35 8/288. Primary — GGUF headers via the tree URLs above, e.g. [unsloth V4 tree](https://huggingface.co/api/models/unsloth/DeepSeek-V4-Flash-0731-GGUF/tree/main?recursive=true), [bartowski Mistral Large tree](https://huggingface.co/api/models/bartowski/Mistral-Large-Instruct-2407-GGUF/tree/main?recursive=true).

- DeepSeek-V4-Flash's routed experts total about 277 B parameters (43 layers × 256 experts × 3 × 4096 × 2048; config: `n_routed_experts` 256, `moe_intermediate_size` 2048, `hidden_size` 4096, 43 layers, `n_shared_experts` 1, `expert_dtype` "fp4") and they are stored at native MXFP4 in every rung from UD-Q4_K_XL up (147.17 GB in UD-Q4_K_XL, UD-Q8_K_XL and the ggml-org MXFP4 file alike); the rungs differ in the roughly 7 B non-routed parameters, which UD-Q8_K_XL stores at about twice the bytes of UD-Q4_K_XL (14.7 vs 7.9 GB). Because half the active parameters are non-routed, UD-Q8_K_XL reads 58 % more bytes per token than UD-Q4_K_XL (17.09 vs 10.81 GB). Check: 277.03 B × 4.25 bits ÷ 8 = 147.17 GB, exactly the routed bytes read from the headers. Primary — [DeepSeek-V4-Flash-0731 config.json](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/config.json); GGUF headers above.

**Decode PROJECTIONS for the PRO 495 at depth 0, no speculative decoding (273 GB/s × efficiency ÷ active GB/token)**

| Engine, rung | GiB | Active GB/token | Efficiency band used (source) | PROJECTED tok/s |
|---|---:|---:|---|---:|
| DeepSeek-V4-Flash UD-Q8_K_XL | 150.75 | 17.085 | 42.6-44.9 % (V4 on mainline Vulkan) | 6.8-7.2 |
| DeepSeek-V4-Flash UD-Q4_K_XL | 144.44 | 10.807 | same | 10.8-11.3 |
| DeepSeek-V4-Flash MXFP4 (ggml-org) | 144.35 | 10.703 | same | 10.9-11.5 |
| DeepSeek-V4-Flash UD-IQ4_XS | 127.28 | 10.265 | same | 11.3-11.9 |
| DeepSeek-V4-Flash UD-Q8_K_XL / UD-Q4_K_XL on a tuned fork | — | — | 66.2-66.6 % (V4 forks) | 10.6 / 16.7-16.8 |
| Qwen3-235B-A22B Q5_K_S (also Baichuan-M3-235B Q5_K_S, same 161,881,426,xxx-byte size and qwen3moe shape) | 150.76 | 15.077 | 60 % generic to 72.5 % (Qwen3-235B Q3_K_M measured) | 10.9-13.1 |
| Qwen3-235B-A22B Q4_K_M | 132.39 | 13.087 | same | 12.5-15.1 |
| Qwen3-235B-A22B UD-Q4_K_XL | 125.10 | 12.705 | same | 12.9-15.6 |
| GLM-5.3-Flash UD-IQ4_XS | 146.05 | 13.100 | 60.0 % (395) to 66.3 % (PRO 495) | 12.5-13.8 |
| GLM-5.3-Flash UD-Q3_K_XL | 137.40 | 12.842 | same | 12.8-14.1 |
| Llama-4-Maverick UD-Q2_K_XL | 142.17 | 8.987 | 60 % generic to 70.9 % (Scout measured) | 18.2-21.5 |
| Llama-4-Maverick UD-IQ3_XXS | 157.69 | 8.792 | same | 18.6-22.0 |
| Llama-4-Maverick Q3_K_S | 160.80 | 7.402 | same | 22.1-26.1 |
| Qwen3.8-Flash-Next UD-Q6_K_XL | 157.55 | 6.964 | 61.2 % (Flash-Next measured) to 75 % generic | 24.0-29.4 |
| Qwen3.8-Flash-Next Q8_0 (ggml-org) | 151.46 | 7.299 | same | 22.9-28.1 |
| Qwen3.8-Flash-Next UD-Q5_K_XL | 147.42 | 6.751 | same | 24.7-30.3 |
| Qwen3.8-Flash-Next UD-Q4_K_XL | 103.69 | 6.334 | same | 26.4-32.3 |
| Qwen3.5-122B-A10B Q8_0 | 120.95 | 9.689 | 60 % to 74.1 % (Qwen3.5-122B measured) | 16.9-20.9 |
| Nemotron 3 Super Q8_0 | 119.65 | 13.253 | 66.1-76.7 % (Nemotron measured) | 13.6-15.8 |
| Nemotron 3 Super UD-Q8_K_XL | 123.39 | 13.472 | same | 13.4-15.5 |
| gpt-oss-120b MXFP4 (also the abliterated MXFP4_MOE re-quant, same 63.39 GB shape) | 59.03 | 3.594 | 75.5-79.5 % (gpt-oss measured) | 57.3-60.4 |
| Qwen3-Coder-Next Q8_0 | 78.99 | 3.939 | 59.4 % (Qwen3.6-35B Q8) to 74.1 % | 41.2-51.4 |
| Qwen3-Coder-Next BF16, Vulkan | 148.51 | 7.231 | 24.8 % (BF16 MoE on RADV, Qwen3.6-35B) | 9.4 |
| Qwen3-Coder-Next BF16, ROCm | 148.51 | 7.231 | 60.3 % (BF16 MoE on ROCm 7.2.3) | 22.8 |
| Mistral Large 2 (123B dense) Q8_0 (also the uncensored fine-tunes, same shape) | 121.33 | 129.85 | 80-86 % (dense gemma-4-31B BF16 3.5 tok/s and UD-Q8_K_XL 6.3 tok/s on RADV, whole-file basis) | 1.7-1.8 |
| Mistral Large 2 Q6_K | 93.68 | 100.26 | same | 2.2-2.3 |
| Qwen3.5-397B-A17B UD-IQ3_XXS | 130.70 | 10.195 | 60 % to 74.1 % | 16.1-19.8 |
| Qwen3.5-397B-A17B UD-Q3_K_S | 153.04 | 12.471 | same | 13.1-16.2 |
| Step-3.7-Flash Q4_K_S | 103.84 | 6.785 | 60-75 % generic | 24.1-30.2 |
| Step-3.7-Flash UD-Q6_K | 152.12 | 11.042 | same | 14.8-18.5 |

(PROJECTIONS; computation in `/tmp/claude-0/-home-user-AI-Agent/552512b0-9757-5ccd-b8aa-228a3e10ea95/scratchpad/sep/scripts/tables.py`.)

**Speculative decoding available at the v0.4.1 pin, as measured on gfx1151**
- Qwen3.5-122B-A10B native MTP (inside the pin, PR #22673): 22.1 → 31.0 tok/s mean (UD-Q4_K_XL, Vulkan lab build, 2 Oct 2026) and 19.14 → 26.86 (UD-Q6_K_XL, ROCm 7.2.3, build 9261, 25 May 2026). Prior-verified — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds); [llama.cpp discussion #23659](https://github.com/ggml-org/llama.cpp/discussions/23659).
- DeepSeek-V4-Flash DSpark (inside the pin, #25784, separate 10.9 GB drafter): on stock Vulkan RADV it did not help (UD-IQ2_XXS 12.3 → 11.7 with q8_0 KV, 2026-09-05; mainline b10488 11.94 → 7.7), on stock ROCm 10.0 llama.cpp it did (13.3 → 20.9), and on tuned forks 19.7 → 28.8-29.6. Primary — [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json); prior-verified — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).
- Qwen3.8-Flash-Next MTP and GLM-5.3-Flash MTP are outside the pin (Flash-Next MTP #29761 merged 2026-10-01; GLM MTP #29928 merged 2026-10-07); measured gains where available: Flash-Next 26.85 → 41.3 (MTP length 2, PR build, Vulkan), GLM UD-IQ1_S 17.0 → 25.0. Prior-verified — [named_heavyweight_moe.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/named_heavyweight_moe.md); [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).
- Step-3.7-Flash MTP (#24340, inside the pin): "The low ~27% uplift you saw in tg is expected as the current llama.cpp stepfun 3.7 mtp implementation is incomplete" (Strix Halo, UD-IQ4_XS, patched Vulkan build; no absolute numbers in the excerpt). Excerpt — [Reddit snapshot](https://reddit.sentinel-team.org/posts/1tyj86p/snapshots/2026-06-13T02%3A56%3A06.64152Z).
- gpt-oss-120b with EAGLE3 drafts got slower on Strix Halo (51.7 → 43.7-27.7). Prior-verified — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).

**Prompt processing (prefill) measured on gfx1151 for the candidates or their nearest kin**
- gpt-oss-120b MXFP4: pp512 719.9 (RADV b9187) / 625.6 (ROCm 7.2.3); pp2048 at depth 32K 307.5 RADV vs 589.3 ROCm; at 64K 145.9 vs 371.5; decode at 64K 35.0 RADV vs 26.1 ROCm. Primary — [kyuz0 results.json](https://kyuz0.github.io/amd-strix-halo-toolboxes/results.json). Upstream 254b17730 (2 Oct 2026) pp2048 1407.4. Prior-verified — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).
- Qwen3.5-122B-A10B UD-Q5_K_XL: pp512 308.8 RADV / 336.7 ROCm; pp2048 at 32K 224.8 / 276.2; at 64K 165.1 / 192.3; decode at 32K 20.38 / 17.73, at 64K 18.99 / 15.74. Primary — [kyuz0 results.json](https://kyuz0.github.io/amd-strix-halo-toolboxes/results.json).
- Nemotron 3 Super UD-Q4_K_XL: pp512 270.6 RADV / 275.9 ROCm; pp2048 at 32K 242.8 / 382.4; at 64K 216.3 / 348.1; decode at 32K 14.30 / 14.40, at 64K 13.69 / 13.96 (only 8 % decode loss from 0 to 64K). Primary — same source.
- DeepSeek-V4-Flash UD-IQ2_XXS, stock llama.cpp Vulkan RADV (2026-09-02), follow-up prefill of 2048 tokens: 224.5 (depth 0), 82.2 (32K), 51.1 (64K); decode 12.6 → 11.1 → 10.0. Performance fork: 334.1 → 263.6 → 241.9. Stock llama.cpp ROCm 10.0: 217.0 → 98.4 → 64.6. Primary — [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json).
- Qwen3.8-Flash-Next UD-IQ4_XS: llama-bench `-fa 1 -ub 2048` pp512 504, pp4096 508, pp16384 462 (Vulkan, PR #28243 build); performance-fork Vulkan follow-up prefill 598.8 → 255.0 at 64K; stock ROCm 10.0 470.2 → 190.6. Primary — [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json); prior-verified — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).
- Qwen3-235B-A22B Q3_K_M: pp512 101 (395, backend unlabelled). Primary — [visorcraft README](https://raw.githubusercontent.com/visorcraft/strix-halo-llm-perf/main/README.md). Llama 4 Scout Q4_K_M: pp512 331 (RADV b8933). Prior-verified — [BENCHMARKS.md](https://github.com/hogeheer499-commits/strix-halo-guide/blob/main/BENCHMARKS.md). MiniMax-M2.7 UD-Q3_K_S: pp512 242.4 RADV but pp2048 at 64K only 13.8 RADV; decode 31.1 → 14.7 (32K) → 8.8 (64K). Primary — [kyuz0 results.json](https://kyuz0.github.io/amd-strix-halo-toolboxes/results.json).
- Effect of the quantisation level on prefill (same model, RADV b9193): Qwen3.6-35B-A3B pp512 UD-Q4_K_XL 1113.6, UD-Q8_K_XL 1045.0 (-6 %), BF16 328.4 (-71 %); on ROCm 7.2.3 BF16 525.9. Decode: 60.43 / 46.33 / 10.68 on RADV, BF16 26.01 on ROCm. Primary — [kyuz0 results.json](https://kyuz0.github.io/amd-strix-halo-toolboxes/results.json).
- No measurement on any gfx1151 system was found for: Llama 4 Maverick (any rung), Mistral Large 2 (any rung), Qwen3-Coder-Next Q8_0 or BF16 in llama.cpp (only "qwen3-coder-next 80B MoE, 3B active, 52 GB, Ollama, 51.1 tok/s"), Baichuan-M3, Qwen3.5-397B on a single machine, Step-3.7-Flash with absolute numbers, and any candidate at its least-quantised fitting rung other than GLM Gorgon and MiMo MXFP4. Primary — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds) (Coder-Next Ollama row); searches on 2026-10-15 returned no further data.

**Backend and pin facts that change the numbers**
- At v0.4.1 the Vulkan backend logs `bf16: %d` per device and a BF16 MoE ran at 24.8 % of bandwidth on RADV in May 2026 (Qwen3.6-35B BF16 10.68 tok/s vs 26.01 on ROCm); dense BF16 did not show the gap (gemma-4-31B BF16 3.50 RADV vs 3.55 ROCm). Primary — [ggml-vulkan.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/ggml/src/ggml-vulkan/ggml-vulkan.cpp); [kyuz0 results.json](https://kyuz0.github.io/amd-strix-halo-toolboxes/results.json).
- Issue #28160 (opened 1 Sep 2026, closed 8 Sep 2026 by PR #28326): on a Max+ 395, Vulkan RADV, Mesa 26.0, `--lazy-mode auto` halved Qwen3.8-Flash-Next (qwen4exp, about 87 GiB) prefill: pp512 216 vs 406 with `--lazy-mode off` vs 429 before commit 257813839; tg128 24.1 / 24.2 / 25.6. A ROCm user reported prefill falling from about 800 to 200 with SSD read spikes. Primary — [llama.cpp issue #28160](https://github.com/ggml-org/llama.cpp/issues/28160). The v0.4.1 loader contains the fix: "resolve AUTO on systems without mmap support (e.g. iGPUs): fall back to OFF; see #28160". Primary — [src/llama-model.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-model.cpp).
- GLM-5.3-Flash cannot load at the v0.4.1 pin (glm5-next merged 2026-09-30); MiMo-V2.6 support (#29257, 2026-09-22) is also outside; Qwen3.8-Flash-Next loads at the pin but its Vulkan hyper-connection ops (#28988, 2026-09-17) are outside. Prior-verified — [named_heavyweight_moe.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/named_heavyweight_moe.md); [other_large_specialists.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/other_large_specialists.md).
- "120W vs 85W: 120W generally helps 7B+ models; very small models are often bandwidth-limited and see little benefit" (395, ROCm 7.0 nightlies primary backend). Primary — [visorcraft README](https://raw.githubusercontent.com/visorcraft/strix-halo-llm-perf/main/README.md).

### Inferences
- The efficiency band is narrow enough to project the rungs that were never measured: the 15 MoE measurements above on stock or near-stock llama.cpp builds (excluding DeepSeek-V4 and BF16 on Vulkan) sit between 59 % and 80 % of theoretical bandwidth, and the two PRO 495 rows (66 %, 73 %) sit inside it, which supports carrying the 395's efficiency over to 273 GB/s (+6.6 %). The bands above are therefore usable for ranking, but not as guarantees; each needs a node measurement.
- Quantisation level hits decode speed far less than file size suggests for the hybrid and high-precision-trunk models, because unsloth keeps attention, recurrent and shared tensors at high precision in every rung: Qwen3.5-122B Q8_0 reads only 14 % more bytes per token than UD-Q5_K_XL (9.69 vs 8.50 GB), Flash-Next Q8_0 only 15 % more than UD-Q4_K_XL (7.30 vs 6.33), Nemotron 3 Super Q8_0 only 17 % more than UD-Q4_K_XL (13.25 vs 11.38). The large speed penalties for "less quantisation" are DeepSeek-V4-Flash UD-Q8_K_XL (58 % more bytes than UD-Q4_K_XL, about 7 vs 11 tok/s on mainline), Qwen3-235B Q5_K_S (19 % more than UD-Q4_K_XL), Qwen3-Coder-Next BF16 on Vulkan (about 9 vs 41-51 tok/s at Q8_0, a kernel issue, not bandwidth) and Mistral Large 2 (dense, so every byte is read: Q8_0 about 1.7-1.8 tok/s).
- DeepSeek-V4-Flash on mainline llama.cpp is kernel-limited, not bandwidth-limited: forks reach 66 % on the same files, so a newer pin could lift its decode by about 1.5x with no quality change; the pin also decides whether its DSpark draft helps (it hurt on stock Vulkan, helped on stock ROCm).
- Prefill, unlike decode, is compute-bound and barely changes between 4-bit and 8-bit (Qwen3.6-35B -6 %), but BF16 prefill on Vulkan is 3.4x slower than Q4 (same model). Among the candidates, the 22B-active Qwen3-235B family (pp512 about 100) and DeepSeek-V4-Flash on stock Vulkan at depth (51 tok/s at 64K) have the slowest prefill; Nemotron 3 Super and the Qwen3.5/3.8 hybrids lose the least prefill and decode speed with depth.
- Baichuan-M3-235B has the same qwen3moe shape and identical rung sizes to Qwen3-235B, so its projections are the Qwen3-235B ones (Q5_K_S 10.9-13.1, official Q4_K_M 143.78 GB about 12.4-15.0 tok/s).

### Gaps
- No llama.cpp measurement on the PRO 495 at the v0.4.1 pin exists for any candidate; the only PRO 495 rows are GLM (post-pin build) and MiMo (post-pin, ROCm).
- No measurement anywhere of Llama 4 Maverick, Mistral Large 2, Baichuan-M3 or Qwen3-Coder-Next (llama.cpp, Q8_0/BF16) on gfx1151; the Maverick efficiency borrows Scout's (the Scout file measured by strix-halo-guide may not be the unsloth Q4_K_M whose bytes were used).
- Whether the RADV BF16 MoE slowness measured at b9193 (May 2026) persists at v0.4.1 was not tested by any source found.
- Prefill projections were not attempted (no bandwidth-style model holds for compute-bound prefill); only measured prefill is reported.
- Speculative-decoding gains at the least-quantised rungs (larger verify cost per draft) are unmeasured.

## Key question 2: KV cache per token at f16 and q8_0, the largest context that fits alone, and compute buffers

### Takeaway
Context is not the binding constraint for most candidates: DeepSeek-V4-Flash (6,880 B/token in llama.cpp's DSV4 cache), GLM-5.3-Flash (11 KiB), Nemotron 3 Super (8 KiB), the Qwen3.5/3.8 hybrids and Coder-Next (24-30 KiB) and gpt-oss-120b (36 KiB) reach their native maximum context alone at their least-quantised fitting rungs within 164 GiB, the only exceptions being the rungs that leave under about 9 GiB (Flash-Next UD-Q6_K_XL, Maverick UD-IQ3_XXS). The full-attention models are context-bound: Qwen3-235B / Baichuan-M3 (188 KiB/token) reach only about 72K f16 / 135K q8_0 at Q5_K_S before compute buffers (about 23K / 43K after a 9 GiB allowance), and Mistral Large 2 (352 KiB/token) about 124K f16 at Q8_0. Under a literal 160 GB (149.01 GiB) graphics cap, DeepSeek-V4-Flash UD-Q8_K_XL, Qwen3-235B Q5_K_S, Flash-Next Q6/Q8, Maverick 3-bit, Qwen3.5-397B UD-Q3_K_S and Step-3.7 UD-Q6_K would not fit at all; the PRO 495 under Ubuntu 26.04 has been shown with about 189 GB of GTT.

### Cited Findings

**Per-token KV cost (f16 = 2 bytes; q8_0 = 34/64 of f16 per ggml's 34-byte block of 32 values), derived from config.json / GGUF metadata and llama.cpp v0.4.1 cache code**
- DeepSeek-V4-Flash (deepseek4): the v0.4.1 DSV4 cache is four parts. A raw K-only cache built as iSWA with every layer marked sliding (window 128) — a fixed size per sequence; a CSA compressed cache over the 21 ratio-4 layers with `dsv4_comp_size(kv_size, 4)` cells; an HCA compressed cache over the 20 ratio-128 layers with kv_size/128 cells; a lightning-indexer cache (1 head × `indexer_head_size` 128) over the CSA layers with kv_size/4 cells; plus f32 compressor states ("csa", "hca", "lid"). All are K-only ("llama_kv_cache uses hparams.is_mla() to allocate K-only storage"), and per sequence ("Keep DSV4 KV/state streams per sequence even when public KV mode is unified"). Primary — [src/llama-kv-cache-dsv4.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-kv-cache-dsv4.cpp), [src/models/deepseek4.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/models/deepseek4.cpp) (`hparams.is_swa_impl[il] = true` for all layers); config `head_dim` 512, `index_head_dim` 128, `compress_ratios` [0, 0, 4, 128, … 4, 0, 0, 0] — [config.json](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/raw/main/config.json). Derived growth: 21 × 512/4 + 20 × 512/128 + 21 × 128/4 = 3,440 elements = 6,880 B/token f16 (3,655 B q8_0) = 0.21 GiB at 32K, 0.84 GiB at 128K, 6.7 GiB at 1M; fixed raw window at `-ub 512` ≈ 43 × 512 × (128 + 512) × 2 B ≈ 28 MB per sequence (approximation of the iSWA cell count).
- The node's Arbiter carries 327,680 B/token f16 for deepseek-v4-flash ("UNVERIFIED: ~10 GiB at 32768 tokens f16"), 77,102 for nemotron-3-super and 115,653 for qwen3.5-122b (both "UNVERIFIED", backed out of Section 4.3 bounds), and 73,728 for gpt-oss-120b. Primary — [scripts/day1/orchestrator/src/atlas/arbiter.py](/home/user/AI-Agent/scripts/day1/orchestrator/src/atlas/arbiter.py) lines 140-160.
- Nemotron 3 Super: `hybrid_override_pattern` has 88 characters with 8 attention layers (`*`), 40 Mamba-2 (`M`) and 40 MoE (`E`); 2 KV heads × head_dim 128 → 2 × 8 × 2 × 128 × 2 = 8,192 B/token f16 (4,352 q8_0); Mamba-2 state per layer = 128 heads × 64 head_dim × 128 state × 4 B = 4 MiB plus a conv state of about 0.12 MiB → about 165 MiB per sequence. GGUF `context_length` 1,048,576 (config `max_position_embeddings` 262,144). Primary — [config.json](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16/raw/main/config.json); GGUF header.
- Qwen3.5-122B-A10B: 12 of 48 layers full attention, 2 KV heads, head_dim 256 → 24,576 B/token f16; Gated-DeltaNet state 36 layers × 64 value heads × 128 × 128 × 4 B = 144 MiB per sequence; GGUF context 262,144. Qwen3.5-397B-A17B: 15 of 60 full-attention layers → 30,720 B/token; GDN state 45 × 4 MiB = 180 MiB per sequence. Primary — [Qwen3.5-122B config](https://huggingface.co/Qwen/Qwen3.5-122B-A10B/raw/main/config.json), [Qwen3.5-397B config](https://huggingface.co/Qwen/Qwen3.5-397B-A17B/raw/main/config.json).
- Qwen3-Coder-Next (qwen3_next): `full_attention_interval` 4 over 48 layers (12 full), 2 KV heads, head_dim 256 → 24,576 B/token; linear-attention state 36 × 32 value heads × 128 × 128 × 4 B = 72 MiB per sequence; context 262,144. Primary — [config.json](https://huggingface.co/Qwen/Qwen3-Coder-Next/raw/main/config.json).
- gpt-oss-120b: 36 layers alternating sliding (window 128) and full, 8 KV heads × head_dim 64 → full layers 18 × 2 × 8 × 64 × 2 = 36,864 B/token f16; native 131,072. Primary — [config.json](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json).
- Step-3.7-Flash: 45 layers, `num_attention_groups` 8 (KV heads), head_dim 128, layer pattern full every 4th (12 full of the first 45), sliding window 512 for the other 33 (the sliding layers also use 8 groups × 128) → 49,152 B/token f16 plus a fixed 33 × 512 × 8 × 128 × 4 B ≈ 69 MB per sequence; 262,144 context; 3 MTP layers. Primary — [config.json](https://huggingface.co/stepfun-ai/Step-3.7-Flash/raw/main/config.json).
- Baichuan-M3-235B: `Qwen3MoeForCausalLM`, 94 layers, 4 KV heads, head_dim 128 → 192,512 B/token f16 (same as Qwen3-235B), `max_position_embeddings` 40,960. Primary — [config.json](https://huggingface.co/baichuan-inc/Baichuan-M3-235B/raw/main/config.json).
- Mistral-Large-Instruct-2407: the bartowski GGUF header gives `context_length` 131,072 (resolving the 32,768 in a community config mirror); 88 layers × 8 KV × 128 → 360,448 B/token f16. Primary — GGUF header of [bartowski Q8_0](https://huggingface.co/api/models/bartowski/Mistral-Large-Instruct-2407-GGUF/tree/main?recursive=true); prior-verified layer/head counts — [quantization_value.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/quantization_value.md).
- Reused derived figures: Qwen3-235B 192,512 B/token; Llama 4 Maverick (chunked iSWA at v0.4.1, n_swa 8192, 3 chunked : 1 full) 49,152 B/token for 12 global layers plus about 1.125 GiB fixed; GLM-5.3-Flash 11,264 B/token (11 MLA/DSA layers, latent 512) plus 136 MiB KDA state; Qwen3.8-Flash-Next 24,576 B/token plus 108 MiB. Prior-verified — [named_heavyweight_moe.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/named_heavyweight_moe.md).
- DeepSeek-V4 quantised K-cache: issue "quantized K-cache (--cache-type-k q8_0) produces garbage on all backends" (#25382) was addressed by PR #25202 "llama: fix quantized kv-cache for dsv4" (merged 7 Jul 2026, inside the pin); a stock Vulkan q8_0-KV run completed the 0-64K curve on 2026-09-05. Prior-verified — [quantization_value.md](/home/user/AI-Agent/research_notes/ATLAS%20specialist%20LLM%20arsenal/quantization_value.md); primary — [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json).

**Largest context alone (164 GiB budget; weights + growing KV + fixed per-sequence state for one sequence; "9 GiB allowance" = compute buffers and host-side buffers, see the measured overheads below; capped at the GGUF native context; PROJECTION by arithmetic)**

| Engine and rung | Weights GiB | f16 KV B/token | q8_0 KV B/token | Room before KV (GiB) | Max ctx f16, no allowance | Max ctx q8_0, no allowance | Max ctx f16, 9 GiB allowance | Max ctx q8_0, 9 GiB allowance | Room under a 149.01 GiB (160 GB) cap |
|---|---:|---:|---:|---:|---|---|---|---|---:|
| DeepSeek-V4-Flash UD-Q8_K_XL | 150.75 | 6,880 | 3,655 | 13.22 | 1M (native) | 1M | 643K | 1M | -1.77 |
| DeepSeek-V4-Flash UD-Q4_K_XL | 144.44 | 6,880 | 3,655 | 19.53 | 1M | 1M | 1M | 1M | 4.54 |
| DeepSeek-V4-Flash MXFP4 (ggml-org) | 144.35 | 6,880 | 3,655 | 19.62 | 1M | 1M | 1M | 1M | 4.63 |
| DeepSeek-V4-Flash UD-IQ4_XS | 127.28 | 6,880 | 3,655 | 36.69 | 1M | 1M | 1M | 1M | 21.70 |
| Qwen3-235B-A22B-2507 Q5_K_S | 150.76 | 192,512 | 102,272 | 13.24 | 72K | 135K | 23K | 43K | -1.75 |
| Qwen3-235B-A22B-2507 Q4_K_M | 132.39 | 192,512 | 102,272 | 31.61 | 172K | 256K (native) | 123K | 231K | 16.62 |
| Qwen3-235B-A22B-2507 UD-Q4_K_XL | 125.10 | 192,512 | 102,272 | 38.90 | 211K | 256K | 162K | 256K | 23.91 |
| Baichuan-M3-235B Q5_K_S | 150.76 | 192,512 | 102,272 | 13.24 | 40K (native) | 40K | 23K | 40K | -1.75 |
| GLM-5.3-Flash UD-IQ4_XS | 146.05 | 11,264 | 5,984 | 17.82 | 1M (native) | 1M | 820K | 1M | 2.83 |
| GLM-5.3-Flash Gorgon Q5K-IQ3S | 143.54 | 11,264 | 5,984 | 20.33 | 1M | 1M | 1M | 1M | 5.34 |
| GLM-5.3-Flash UD-Q3_K_XL | 137.40 | 11,264 | 5,984 | 26.47 | 1M | 1M | 1M | 1M | 11.48 |
| Llama-4-Maverick UD-Q2_K_XL | 142.17 | 49,152 | 26,112 | 20.71 | 441K | 831K | 249K | 470K | 5.72 |
| Llama-4-Maverick UD-IQ3_XXS | 157.69 | 49,152 | 26,112 | 5.19 | 110K | 208K | none | none | -9.80 |
| Qwen3.8-Flash-Next UD-Q6_K_XL | 157.55 | 24,576 | 13,056 | 6.34 | 256K (native) | 256K | none | none | -8.64 |
| Qwen3.8-Flash-Next Q8_0 (ggml-org) | 151.46 | 24,576 | 13,056 | 12.43 | 256K | 256K | 146K | 256K | -2.55 |
| Qwen3.8-Flash-Next UD-Q5_K_XL | 147.42 | 24,576 | 13,056 | 16.48 | 256K | 256K | 256K | 256K | 1.49 |
| Qwen3.5-122B-A10B Q8_0 | 120.95 | 24,576 | 13,056 | 42.91 | 256K | 256K | 256K | 256K | 27.92 |
| Nemotron 3 Super Q8_0 | 119.65 | 8,192 | 4,352 | 44.19 | 1M (GGUF native) | 1M | 1M | 1M | 29.20 |
| Nemotron 3 Super UD-Q8_K_XL | 123.39 | 8,192 | 4,352 | 40.45 | 1M | 1M | 1M | 1M | 25.46 |
| gpt-oss-120b MXFP4 | 59.03 | 36,864 | 19,584 | 104.96 | 128K (native) | 128K | 128K | 128K | 89.97 |
| Qwen3-Coder-Next Q8_0 | 78.99 | 24,576 | 13,056 | 84.94 | 256K | 256K | 256K | 256K | 69.95 |
| Qwen3-Coder-Next BF16 | 148.51 | 24,576 | 13,056 | 15.42 | 256K | 256K | 256K | 256K | 0.43 |
| Mistral-Large-2407 Q8_0 | 121.33 | 360,448 | 191,488 | 42.67 | 124K | 128K (native) | 97K | 128K | 27.68 |
| Mistral-Large-2407 Q6_K | 93.68 | 360,448 | 191,488 | 70.32 | 128K | 128K | 128K | 128K | 55.33 |
| Qwen3.5-397B-A17B UD-IQ3_XXS | 130.70 | 30,720 | 16,320 | 33.12 | 256K | 256K | 256K | 256K | 18.14 |
| Qwen3.5-397B-A17B UD-Q3_K_S | 153.04 | 30,720 | 16,320 | 10.78 | 256K | 256K | 60K | 114K | -4.20 |
| Step-3.7-Flash Q4_K_S | 103.84 | 49,152 | 26,112 | 60.10 | 256K | 256K | 256K | 256K | 45.11 |
| Step-3.7-Flash UD-Q6_K | 152.12 | 49,152 | 26,112 | 11.82 | 252K | 256K | 60K | 113K | -3.17 |

(Computation: `/tmp/claude-0/-home-user-AI-Agent/552512b0-9757-5ccd-b8aa-228a3e10ea95/scratchpad/sep/scripts/kv.py`. Nemotron row uses the GGUF native 1M. KV pools in llama-server are totals across slots: 8 slots × 32K = 256K of pool.)

**Measured whole-footprint overheads (what the 9 GiB allowance is based on) and compute buffers**
- Qwen3.8-Flash-Next UD-IQ4_XS (87.25 GiB file) at 262,144 context with MTP: "78.2 GiB GTT plus 27.1 GiB host RAM" ≈ 105.3 GiB, i.e. about 18 GiB above the file, of which KV is about 6 GiB, the MTP head about 2.6 GiB, so about 9 GiB of other buffers. Prior-verified — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).
- Qwen3.5-122B MXFP4 (65 GB file) "occupies 74 GB when loaded" (llama-server, Vulkan, 16 Sep 2026). Primary — [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).
- Flash-Next on Vulkan RADV (20 Sep 2026): `load_tensors: CPU model buffer size = 27465.95 MiB`, `load_tensors: Vulkan0 model buffer size = 59931.83 MiB`, with `RssAnon` 33.8 GB and MemAvailable 8 GB on a 116 GB GTT cap (the n-gram table was held on the host). Primary (via fetch) — [llama.cpp discussion #27950](https://github.com/ggml-org/llama.cpp/discussions/27950).
- GLM Gorgon on the PRO 495: "`-c 131072` fits comfortably with 176 GiB of GTT … `-ub 1024` lowers the compute buffers if you run short"; "`-ub 2048` gives the best prefill; decode is the same at any `-ub`"; `mem_info_gtt_total` "about 189000000000 bytes" with `ttm.pages_limit=46137344 ttm.page_pool_size=46137344`; "Keep the BIOS GPU memory carve-out at its minimum". Primary — [cafonez card](https://huggingface.co/cafonez/GLM-5.3-Flash-Gorgon-GGUF/raw/main/README.md).
- DeepSeek-V4 on CUDA: "compute buffer size of 24476.1461 MiB, does not match expectation of 4168.0000 MiB". Prior-verified — [PR #24162](https://github.com/ggml-org/llama.cpp/pull/24162).
- `-b` "raises reserved memory and `-ub` raises peak memory usage". Primary — [strixhalo.wiki llama.cpp performance](https://strixhalo.wiki/AI/llamacpp-performance/) (undated).
- 160 GB figure: GMKtec "Up to 192GB LPDDR5X-8533MT/s, Up to 160GB VRAM" (primary — [GMKtec page](https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc)); Minisforum "up to 160GB allocatable as VRAM" (prior-verified — [gap_closure_2026-10-14.md](/home/user/AI-Agent/research_notes/ATLAS%20LLM%20roster%20refresh%202026/gap_closure_2026-10-14.md)).

### Inferences
- The node's Arbiter overstates KV for the hybrid and compressed-attention engines by large factors relative to the config-derived figures (DeepSeek-V4 327,680 vs 6,880 B/token, about 48x; Nemotron 77,102 vs 8,192, about 9x; Qwen3.5-122B 115,653 vs 24,576, about 5x; gpt-oss 73,728 vs 36,864 because it charges both iSWA halves the full pool). These are conservative, so they reject fits that would work, chiefly DeepSeek-V4-Flash UD-Q8_K_XL with long context.
- With the KV this small, the budget question for DeepSeek-V4, GLM-5.3, Nemotron, the Qwen3.5/3.8 hybrids and Coder-Next is weights plus compute buffers; for DeepSeek-V4 the unexplained 24 GiB CUDA compute-buffer report is the real risk at UD-Q8_K_XL (13.2 GiB of room).
- The 160 GB "VRAM" figure is a BIOS carve-out / Windows figure; on Linux the GPU allocates from GTT, and the Gorgon card shows about 189 GB of GTT on the same chip and OS. If the node were held to 149.01 GiB for everything, the least-quantised options that remain are DeepSeek-V4 UD-Q4_K_XL/MXFP4 with under 5 GiB spare, Qwen3-235B Q4_K_M, GLM UD-Q3_K_XL, Flash-Next UD-Q5_K_XL (1.5 GiB spare), Qwen3.5-122B Q8_0, Nemotron Q8_0, Coder-Next Q8_0 and Mistral Large 2 Q8_0.
- Qwen3-235B and Baichuan-M3 are the only candidates where choosing the least-quantised rung (Q5_K_S) materially limits context (about 23-43K after buffers); at Q4_K_M with q8_0 KV they reach about 231K.

### Gaps
- No llama.cpp log of compute-buffer sizes (Vulkan0 compute buffer, host compute buffer) for any candidate at its fitting rung on gfx1151; the 9 GiB allowance is inferred from two whole-footprint measurements, not from logs.
- DeepSeek-V4 compute buffers on Vulkan at long context, and the exact iSWA raw-cache cell count, are unmeasured; DSA indexer caches of GLM-5.3 and Flash-Next were not sized.
- Whether q8_0 KV costs measurable quality on these specific engines (other than the fixed V4 bug) was not found.

## Key question 3: Loading from disk and swapping 100-160 GB engines between tasks

### Takeaway
At the v0.4.1 pin the default `--load-mode auto` resolves to non-mmap loading on the integrated GPU for both Vulkan and HIP (the iGPU reports no mmap support), so the ROCm "slow mmap above 64 GB" trap is avoided by default and weights are read through the page cache into pinned staging buffers and copied into GTT. Because a resident 145-158 GiB engine leaves only about 10-25 GiB for page cache in 187-188 GiB, alternating between two such engines makes every switch a full cold read: about 25 s at the 6.3 GB/s buffered rate measured on Strix Halo, about 40 s at 4 GB/s, about 65-70 s at the 2.4 GB/s rates seen in Strix Halo load timings, plus stop, allocation, warm-up and re-prefill of the task context (the last can exceed the load time: 32K context on DeepSeek-V4 stock Vulkan ≈ 4.4 min, 64K ≈ 13 min). The pinned llama-server offers slot save/restore, a router with an LRU `--models-max`, and direct I/O; llama-swap offers TTL, groups and preload hooks.

### Cited Findings

**The node's storage**
- GMKtec EVO-X5 Pro: "Primary Storage (ROM) M.2 2280 PCIe 4.0 ×4 NVMe SSD • 2TB / 4TB Configurations", "3 × M.2 2280 Key-M Slots • SSD1 and SSD2: PCIe 4.0 ×4 • SSD3: PCIe 4.0 ×1 • Maximum Total Storage: 24TB (8TB × 3)"; the SSD model and its rated sequential read are not published. Primary — [GMKtec product page](https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc) (read 2026-10-15).
- The node's design: "8 TB NVMe, PCIe 4.0 x4 | `/srv/atlas`: models, engines, agent data …" and "4 TB NVMe, PCIe 4.0 x4 | Operating system, logs …"; "LUKS2 on the 8 TB data volume and on the OS volume". Primary — [ATLAS_FRAMEWORK_REVIEW.md](/home/user/AI-Agent/docs/ATLAS_FRAMEWORK_REVIEW.md) lines 82-83 and 170.
- The design's swap table: "Read 65 GB from disk 10–15 s; Read 130 GB (a Q8_0 engine) 20–30 s; Read 155 GB (the Apex engine) 25–35 s; Release previous engine and allocate 5–10 s; Total per swap 15–45 s depending on engine size". Primary — same document, section 5.4 (lines 343-352). Deep Think "Standard" has 2 engine swaps (5-8 min) and "Deep" 3-4 swaps (15-30 min). Primary — same document, section 9.1.
- PCIe 4.0 ×4 link ceiling: 16 GT/s × 4 lanes × 128/130 ÷ 8 = 7.88 GB/s (arithmetic).
- dm-crypt / LUKS read overhead: one Framework-laptop user measured 1,900 MB/s raw vs 1,670 MB/s through LUKS2 + Btrfs (about -12 %) and later about 5,000 MB/s on an encrypted SK Hynix P41; an Arch user reported about 5.9 GB/s SEQ1M Q8T1 through LUKS defaults; `--perf-no_read_workqueue` exists since kernel 5.9. Excerpt — [Framework community thread](https://community.frame.work/t/luks-home-encryption-hardware-acceleration/7937); [Arch forum](https://bbs.archlinux.org/viewtopic.php?id=288252); [cryptsetup 2.3.4 release notes](https://www.kernel.org/pub/linux/utils/cryptsetup/v2.3/v2.3.4-ReleaseNotes).

**How the pinned llama.cpp loads on this hardware (primary source code at tag v0.4.1)**
- `-lm, --load-mode MODE` "model loading mode (default: auto) - auto: mmap, unless a device does not support it - none: no special loading mode - mmap … - mlock … - mmap+mlock … - dio: use DirectIO if available"; there is no `--mmap`/`--no-mmap` flag at the pin. `-lzm, --lazy-mode` "on-demand reading of certain tensors, for example per-layer embeddings (default: auto) … auto: on, but only for tensors larger than 4 GiB". Primary — [common/arg.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/common/arg.cpp); enum `llama_load_mode` — [include/llama.h @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/include/llama.h).
- Auto resolution: "if (ml.use_mmap && params.load_mode == LLAMA_LOAD_MODE_AUTO) { for each device … if (!props.caps.mmap_support) { ml.use_mmap = false; …" and lazy AUTO falls back to OFF on such devices ("see #28160"). The Vulkan backend reports `.mmap_support = !ctx->is_integrated_gpu` and `.buffer_from_host_ptr = false` (integrated = `vk::PhysicalDeviceType::eIntegratedGpu`); the CUDA/HIP backend reports `.mmap_support = props->type != GGML_BACKEND_DEVICE_TYPE_IGPU`, with IGPU chosen when `prop.integrated` is set. Primary — [src/llama-model.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-model.cpp), [ggml-vulkan.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/ggml/src/ggml-vulkan/ggml-vulkan.cpp), [ggml-cuda.cu @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/ggml/src/ggml-cuda/ggml-cuda.cu).
- Non-mmap path: "When not using mmaped io use async uploads from pinned memory to GPU memory" with 4 staging buffers of 1 MiB (buffered) or 64 MiB + 2 × alignment (direct I/O); tensors are loaded "biggest-first so the largest staging buffer is allocated while the fewest weights are resident". Direct I/O opens the file `O_RDONLY | O_DIRECT` and falls back to buffered I/O on failure. No `POSIX_FADV_DONTNEED` is issued after loading (only `POSIX_FADV_SEQUENTIAL` and madvise on the mmap path). On UMA the Vulkan backend allocates device buffers as device-local + host-visible + host-coherent ("On UMA, prefer host-visible memory so direct tensor borrowing works"). Primary — [src/llama-model-loader.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-model-loader.cpp), [src/llama-mmap.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-mmap.cpp), [ggml-vulkan.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/ggml/src/ggml-vulkan/ggml-vulkan.cpp).
- Earlier community guidance (pre-pin flags): "always set `--mmap 0` or `--no-mmap`"; mmap "will make loading moderately slower for Vulkan, and catastrophically slower for ROCm". Primary — [strixhalo.wiki](https://strixhalo.wiki/AI/llamacpp-performance/) (undated). "`--no-mmap` is required on ROCm because ROCm's mmap path is very slow above 64GB on gfx1151". Prior-verified — [sypherin/strix-halo-setup](https://github.com/sypherin/strix-halo-setup/blob/master/README.md). Older Vulkan builds failed with "read error: Bad address" under `--no-mmap`; a contributor said builds "starting from b7668 should work fine". Excerpt — [llama.cpp #18473](https://github.com/ggml-org/llama.cpp/issues/18473).

**Measured load times (none found for a 100-160 GB file on gfx1151)**
- PR #18012 "Async DirectIO model loading on Linux" (merged 18 Dec 2025): gpt-oss-120b MXFP4 on a DGX Spark, mmap first load about 110 s, later loads about 67 s, direct I/O about 10.5 s "consistently"; on a Strix Halo with 2 × WD_BLACK SN850X in RAID 0, Btrfs, kernel 6.18, page cache dropped: buffered `ddrescue` read 6.3 GB/s average (9.57 s), direct I/O 1.9 GB/s (31.72 s); Vulkan `--mmap` load 43.37 s on master vs 30.31 s with the PR; Vulkan `--no-mmap` ran out of memory on some PR builds; uncached loads run "at the speed of the sequential disk read speed"; the change "leaves the memory in state `free` instead of `buff/cache`". Direct I/O was later disabled by default (PR #19109, merged 27 Jan 2026). Primary (via fetch) — [llama.cpp PR #18012](https://github.com/ggml-org/llama.cpp/pull/18012).
- Llama 4 Scout 108.6B Q4_K_M (67.4 GB), Strix Halo 128 GB, Ollama ROCm (llama.cpp runner), 19 Jul 2026: with mmap and buffered I/O the 32K text-only start was "Not ready at 144.1 s"; one instrumented mmap start took 311.46 s and read 144.5 GB for a 67.4 GB model; "No mmap, buffered I/O: Ready in 18.36 s (52.3 GB read)"; "No mmap, direct I/O: Ready in ~20 s". Primary — [Sheldon Lewis note](https://sheldylew.com/notes/why-llama-4-took-minutes-to-load).
- Strix Halo 128 GB (GMKtec EVO-X3), Lemonade 11.5.2 ROCm llama.cpp: Qwen3.5-122B-A10B GGUF "loaded in 34.6 seconds" (GPTQ on vLLM 100.1 s); Qwen3.8-27B Q8_0 + MTP (29.58 GiB) 13.38 s, Q6_K 12.87 s, Q4_K_M 3.53 s; cache state, mmap and storage not stated. Primary — [Soot / Silicon, 14 Aug 2026](https://www.soothill.io/blog/2026/08/14/coding-model-benchmark-strix-halo/).
- "loading a model on Strix Halo can take 1-2 minutes", longer than llama-swap's 120 s default health-check timeout. Excerpt — [llama-swap guide](https://www.glukhov.org/llm-hosting/llama-swap/).

**Page cache with two large engines in 192 GB**
- Node memory: MemTotal about 187-188 GiB, GPU resident set 6-8 GiB, fixed 16 GiB host reserve, engine budget about 164 GiB, no swap. Prior-verified — [current_atlas_baseline.md](/home/user/AI-Agent/research_notes/ATLAS%20LLM%20roster%20refresh%202026/current_atlas_baseline.md).
- No source measured alternating two 120-160 GB models on a 192 GB machine.

**Tools to reduce or hide swaps (pinned llama-server and llama-swap)**
- Router mode at v0.4.1: "launch `llama-server` without specifying any model"; `--models-dir`, `--models-preset` (INI presets; options `load-on-startup`, `stop-timeout` "After requested unload, wait for this many seconds before forcing termination (default: 10)"), `--models-max N` "maximum number of models to load simultaneously (default: 4, 0 = unlimited)", `--models-autoload` (default enabled), `POST /models/unload`; the scheduler queues requests when "models_max reached" and evicts "idle LRU" models for queued requests. Primary — [tools/server/README.md @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/tools/server/README.md), [tools/server/server-models.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/tools/server/server-models.cpp).
- `--sleep-idle-seconds` (default -1): "the model and its associated memory (including the KV cache) are unloaded from RAM … Any new incoming task will automatically trigger the model to reload". `--slot-save-path` with `POST /slots/{id}?action=save` / `?action=restore` saves and restores a slot's prompt cache to a file; `--cache-ram` (default 8192 MiB) host prompt cache. Primary — [server README @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/tools/server/README.md). The DSV4 cache implements state write/read of its compressed streams (`dsv4_state_write_tensor_streams`, `dsv4_state_read_tensor_streams`). Primary — [llama-kv-cache-dsv4.cpp @ v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-kv-cache-dsv4.cpp).
- llama-swap: "Automatic unloading of models after timeout by setting a `ttl`", "Preload models on startup with `hooks`", "Run concurrent models with a custom DSL swap matrix", `POST /api/models/unload` and `/unload/:model_id`, `/running`; one binary plus one config file. Primary — [llama-swap README](https://github.com/mostlygeek/llama-swap) (read 2026-10-15). `healthCheckTimeout` default 120 s (minimum 15 s). Excerpt — [glukhov.org llama-swap guide](https://www.glukhov.org/llm-hosting/llama-swap/). kyuz0's recommended server flags include `--parallel 1 --models-max 1`. Prior-verified — [kyuz0 README](https://github.com/kyuz0/amd-strix-halo-toolboxes).

**Re-prefill cost after a swap (from measured depth curves; PROJECTION by summing 2048-token chunks at the measured follow-up prefill rate for each depth)**

| Engine, rung, backend (395, run date) | 16K context | 32K | 64K |
|---|---:|---:|---:|
| DeepSeek-V4-Flash UD-IQ2_XXS, stock llama.cpp Vulkan RADV (2026-09-02) | 100 s | 262 s | 768 s |
| DeepSeek-V4-Flash UD-IQ2_XXS, stock llama.cpp ROCm 10.0 (2026-09-01) | 95 s | 236 s | 646 s |
| DeepSeek-V4-Flash UD-IQ2_XXS, performance fork Vulkan (2026-09-02) | 55 s | 115 s | 244 s |
| Qwen3.8-Flash-Next UD-IQ4_XS, stock llama.cpp ROCm 10.0 (2026-09-04) | 41 s | 94 s | 240 s |
| Qwen3.8-Flash-Next UD-IQ4_XS, performance fork Vulkan (2026-09-04) | 31 s | 71 s | 172 s |
| MiMo-V2.6-Flash MXFP4, llama.cpp ROCm 10.0, PRO 495 (2026-09-23) | 37 s | 79 s | 183 s |

Source data: [local-llm-benchmarks results.json](https://local-llm-benchmarks.dev/data/results.json) (primary); a single long prompt with `-ub 2048` may ingest faster than 2048-token follow-ups.

**Cold-read time per engine file (PROJECTION: file bytes ÷ effective read rate; 1.46 GB/s = Vulkan mmap load measured in PR #18012; 2.4 GB/s ≈ the Strix Halo GGUF load timings above; 4.0 GB/s mid-point; 6.3 GB/s = buffered read measured on Strix Halo RAID 0)**

| Engine file | GB | at 1.46 GB/s | at 2.4 GB/s | at 4.0 GB/s | at 6.3 GB/s |
|---|---:|---:|---:|---:|---:|
| gpt-oss-120b MXFP4 | 63.39 | 43 s | 26 s | 16 s | 10 s |
| Qwen3-Coder-Next Q8_0 | 84.81 | 58 s | 35 s | 21 s | 13 s |
| Nemotron 3 Super Q8_0 | 128.47 | 88 s | 54 s | 32 s | 20 s |
| Qwen3.5-122B Q8_0 | 129.87 | 89 s | 54 s | 32 s | 21 s |
| Mistral Large 2 Q8_0 | 130.28 | 89 s | 54 s | 33 s | 21 s |
| Qwen3-235B Q4_K_M | 142.15 | 97 s | 59 s | 36 s | 23 s |
| GLM-5.3-Flash UD-Q3_K_XL | 147.54 | 101 s | 61 s | 37 s | 23 s |
| DeepSeek-V4-Flash UD-Q4_K_XL | 155.10 | 106 s | 65 s | 39 s | 25 s |
| GLM-5.3-Flash UD-IQ4_XS | 156.82 | 107 s | 65 s | 39 s | 25 s |
| Qwen3-Coder-Next BF16 | 159.46 | 109 s | 66 s | 40 s | 25 s |
| DeepSeek-V4-Flash UD-Q8_K_XL | 161.87 | 111 s | 67 s | 40 s | 26 s |
| Qwen3-235B Q5_K_S | 161.88 | 111 s | 67 s | 40 s | 26 s |
| Qwen3.8-Flash-Next Q8_0 (ggml-org) | 162.62 | 111 s | 68 s | 41 s | 26 s |
| Qwen3.8-Flash-Next UD-Q6_K_XL | 169.17 | 116 s | 70 s | 42 s | 27 s |

### Inferences
- Page cache: with `load-mode auto` → buffered reads and no DONTNEED, the file passes through the page cache, but once a 120-160 GiB engine is resident in GTT only about 187.5 − (120…158) − 7 (GPU resident) − about 12 (host services) ≈ 10-48 GiB, and for the 145-158 GiB engines about 10-23 GiB, can stay cached. So switching between any two engines of 120 GiB or more re-reads the incoming file almost entirely from the SSD; even reloading the same large engine after any other large one is cold. Only a small engine (gpt-oss-120b at 59 GiB) next to a mid-size one could stay partly warm. `--load-mode dio` would skip the page cache entirely (no benefit lost, since nothing survives anyway) but was measured slower than buffered reads in its December 2025 form (1.9 vs 6.3 GB/s on Strix Halo); the v0.4.1 dio path (64 MiB aligned chunks, async upload) is unmeasured.
- The TTM page pool (`ttm.page_pool_size` set equal to `pages_limit` in the Gorgon example) can keep freed GTT pages in the driver's pool after an unload; that may speed the next engine's allocation but also hold memory the page cache could otherwise use during the next read (driver behaviour inferred from the parameter names, not measured).
- Per-switch latency between two 145-160 GB engines therefore decomposes as: graceful stop (router default stop-timeout 10 s) and GTT release (unmeasured), process start and GGUF parse (seconds), cold read 25-70 s (most likely 40-65 s through LUKS on a single Gen4 drive, versus the design's 25-35 s), allocation and warm-up (unmeasured; the design allows 5-10 s), then re-prefill of the task's context unless a saved slot is restored: about 1.5-4.5 min at 16-32K and up to 13 min at 64K for DeepSeek-V4 on stock Vulkan. A Deep Think "Standard" run (2 swaps, at about 50-85 s per swap) thus spends roughly 1.5-3 min swapping before any re-prefill, and a "Deep" run (3-4 swaps) roughly 2.5-5.5 min, which fits within the design's 5-8 and 15-30 min totals but is not negligible; re-prefill, not disk, dominates when contexts are long.
- Slot save/restore removes most re-prefill cost for the compressed-KV engines (a 32K DeepSeek-V4 slot is about 0.2 GiB of KV at f16; a 32K Qwen3-235B slot about 5.9 GiB, both seconds to read back), so saving the task's slot before an engine leaves and restoring it when the engine returns is the largest available saving; whether restore works for every architecture at the pin (DSV4 has state read/write code; recurrent hybrids keep checkpoints) is untested.
- Swap reduction options available at the pin: batching work per engine (the design's grouping rule), keeping the most-used engine resident between tasks (the router's `--models-max 1` with LRU eviction already evicts only on demand), an idle TTL/`--sleep-idle-seconds` only for engines that are not the default, and choosing rungs that leave page-cache room (e.g. a 59 GiB gpt-oss next to another engine) when two engines alternate frequently. `--models-max` must be set to 1 for 100+ GB engines: the default 4 would try to start a second instance.

### Gaps
- No measured llama.cpp load time for any 100-160 GB GGUF on gfx1151 (Vulkan or ROCm, buffered or dio) at or near the pin; no measurement of GTT release or allocation time for 150 GiB, nor of the first-run Vulkan pipeline compile time for these architectures.
- The SSD in the node (model, rated sequential read) and the dm-crypt throughput of the node's LUKS2 volume on Zen 5 are unknown; no Gen4 single-drive LUKS2 read benchmark on Strix Halo was found.
- No source measured page-cache behaviour when alternating two large models on a 192 GB machine; the inference above is arithmetic on known memory sizes.
- No measurement of slot save/restore time or correctness for DeepSeek-V4, the Qwen3.5/3.8 hybrids or Nemotron at the pin.

## Key question 4: Sustained-load behaviour of the EVO-X5 Pro and similar 192 GB systems (thermals, power, throttling)

### Takeaway
No independent sustained-load (hours-long) thermal or power measurement of the EVO-X5 Pro or any other PRO 495 system was found; the vendor specifies Silent 54 W, Balanced 85 W and Performance 120 W sustained / 160 W peak, a vapor chamber with two 8030 fans and one 8015 system fan, and a 330 W LITEON supply, and a single buyer calls it "Very quiet". On 395 systems, throttling depends on the chassis and fan curve (one mini-PC hit 97-100 °C and dropped from 2900 to about 1500 MHz with its stock fan curve), while short benchmarks on well-cooled boxes showed no throttling; decode is bandwidth-bound and gains little from more power, prefill more.

### Cited Findings
- GMKtec EVO-X5 Pro: "Silent Mode: 54W • Balanced Mode: 85W • Performance Mode: 120W Sustained, 160W Peak"; "Cooling System • Vapor Chamber (VC) Cooling • 2 × 8030 Fans + 1 × 8015 System Fan"; "1 × P-MODE Button (Silent / Balanced / Performance)"; "330w LITEON Power Supply", "IN: 19.5V / 16.9A (330W)"; GPU "40 Compute Units • Up to 3000 MHz"; "192GB (8 × 24GB) • Non-upgradeable". Customer review: "A grat one. I like it. Very quiet. Runs qwen 3.8 flash next q8_0 at 15 tokens in ubuntu 26.04 via lm studio." Primary — [GMKtec product page](https://www.gmktec.com/products/gmktec-evo-x5-pro-amd-ryzen-ai-max-pro-495-ai-mini-pc) (published 2026-09-28, updated 2026-10-15).
- A hands-on piece gives "up to 140W TDP" for High Performance mode and describes a dual-fan system, conflicting with the vendor page's 120 W sustained / 160 W peak and three fans. Excerpt — [Geeky Gadgets hands-on](https://www.geeky-gadgets.com/gmktec-evo-x5-pro-hands-on/); [TechTimes](https://www.techtimes.com/articles/328257/20260930/gmktec-evo-x5-pro-ships-192gb-ram-china-law-requires-data-cooperation.htm).
- AMD PRO 495: default TDP 55 W, configurable 45-120 W, Tjmax 100 °C. Excerpt — [AMD product page](https://www.amd.com/en/products/processors/laptop/ryzen-pro/ai-max-pro-400-series/amd-ryzen-ai-max-plus-pro-495.html). Framework Desktop PRO 495: 120 W sustained, 140 W boost. Excerpt — [Framework specs](https://frame.work/desktop?tab=specs). Minisforum MS-S1 MAX-P495: "120W stable PPT and 160W Fast PPT". Excerpt — [Minisforum store](https://store.minisforum.com/products/minisforum-ms-s1-max-p495-ai-workstation).
- Bosgame M5 (Max+ 395) under llama.cpp load with the stock fan curve: "GPU pinned at 97–100 °C", clock falling from 2900 MHz to about 1500 MHz; with a custom fan daemon about 62 °C at about 2900 MHz. Controlled A/B (two concurrent streams of a 35B MoE): daemon peak 78.5 °C, 2820 MHz held, about 4,200 rpm; "stock" 99.2 °C and still climbing when aborted, 2820 → 2662 MHz, about 1,830 rpm (the "stock" arm pinned the fan low rather than using the true auto curve; the "after" run also had a −20 mV CPU undervolt; no tok/s, power or duration reported). Primary — [nathanmarlor/strix-halo-fan-control](https://github.com/nathanmarlor/strix-halo-fan-control).
- Framework Desktop (Max+ 395) under full load sustained about 95 W with temperatures peaking at 75.9 °C and hovering around 69.5 °C (ETA Prime testing as reported). Excerpt — [Notebookcheck](https://www.notebookcheck.net/Framework-Desktop-with-AMD-Ryzen-AI-Max-395-beats-RTX-4060-laptop-in-3DMark-sustains-95W-TDP.1082344.0.html). A five-minute Strix Halo test found throughput essentially flat with about +3 °C. Excerpt — [smfclearinghouse benchmark](https://www.smfclearinghouse.com/blog/2026-08-06-strix-halo-radeon-8060s-llm-inference-benchmark/).
- "120W vs 85W: 120W generally helps 7B+ models; very small models are often bandwidth-limited and see little benefit". Primary — [visorcraft README](https://raw.githubusercontent.com/visorcraft/strix-halo-llm-perf/main/README.md). strix-halo-guide's controlled runs record `tuned accelerator-performance`, AMDGPU DPM `high` and "GPU clock at 2900" as part of the measurement state. Primary — [BENCHMARKS.md](https://github.com/hogeheer499-commits/strix-halo-guide/blob/main/BENCHMARKS.md).
- Under sustained load on a 395 box, `ring gfx_0.0.0 timeout` followed by a MODE2 reset was "described as normal"; the one hard hang came from memory pressure (a second model load with too high a TTM cap); DeepSeek-V4-Flash IQ2_XXS with a draft at 108 of 124 GB "hung the amdgpu driver once". Prior-verified — [sypherin](https://github.com/sypherin/strix-halo-setup/blob/master/README.md); [huppiflupp](https://github.com/huppiflupp/strix-halo-llm-speeds).

### Inferences
- Because decode of every candidate except the dense Mistral Large 2 runs at 60-80 % of memory bandwidth, a power or clock reduction mainly slows prefill and the compute-heavier parts (attention at depth, the DeepSeek-V4 compressor/indexer kernels); a thermally throttled GPU (the 1500 MHz case) would hurt long-prompt work far more than token generation. The P-MODE setting (54/85/120 W) is therefore a prefill and long-context lever more than a decode lever (inferred from the bandwidth-bound decode figures and the visorcraft note, not measured on the EVO-X5 Pro).
- The EVO-X5 Pro's three-fan vapor-chamber design and the buyer's "very quiet" remark do not establish sustained-load behaviour; the Bosgame case shows that firmware fan curves, not the chip, decide whether a Strix-class mini-PC throttles.

### Gaps
- No independent review of the EVO-X5 Pro (or Framework / Minisforum / HP PRO 495 systems) with measured package power, GPU clocks, temperatures, fan noise or tok/s over hours of generation was found; LTT Labs' "AMD Ryzen AI Halo" article (6 Jul 2026) returned HTTP 403.
- No measurement of decode or prefill at 54 W vs 85 W vs 120 W on any PRO 495 system; no evidence on whether the EVO-X5 Pro's P-MODE button state persists across reboots or can be set from Linux.
- Whether the node's 16-GiB-reserve, single-engine regime avoids the memory-pressure hangs reported on 128 GB boxes is untested.
