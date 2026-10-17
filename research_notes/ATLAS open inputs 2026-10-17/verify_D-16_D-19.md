# Verifier notes: D-16 and D-19 (2026-10-17)

Scripts: rec/scripts/d16_verify.py (llama-perplexity host buffers, server A/B fits, coding wall-clock), rec/scripts/gguf_kv_v.py (own GGUF header parse).
Downloads: rec/vfy/ (HF trees, AA page, Vals page, GGUF header ranges, llama.cpp master log in rec/vfy/lcgit).

## D-16: survives, with corrections
- Re-ran d16.py: every fit figure reproduces (155.96 / 155.88 at 32,768 q8_0; 156.74 / 156.67 at 40,960; 28,672 fallback 155.57 / 155.49).
- HF trees re-read: i1-Q5_K_S 161,881,426,496 B (static Q5_K_S in engines.json is 161,881,426,240 B); bartowski Q5_K_S 161,963,285,952; Q5_K_M 155.43 / 155.36 GiB; mradermacher i1-Q4_K_M 132.39 GiB (smaller than official Q4_K_M 133.91).
- Own GGUF header parse: both qwen3moe, 94 blocks, 4 KV heads, 128/128, ctx 40960; i1 imatrix dataset imatrix-training-full-3 (318 chunks); bartowski calibration_datav5 (818 chunks).
- Pin code: Vulkan FA fa_kv_ok accepts Q8_0 (ggml-vulkan.cpp:15481-15527); rotation llama-kv-cache.cpp:321-355: n_rot_k = 128, n_rot_v = 64 for head size 128.
- NEW: PR #27703 (merged 2026-10-15, post-pin). Comment on a Ryzen AI Max+ PRO 495, 192 GB, kernel 7.0.0-38: f16 KV pp512 @ d32768 14.80 t/s stock vs q8_0 132.8 t/s (MiniMax M2.7). Qwen3-Coder-30B-A3B (4 KV heads x 128, same per-layer shape as Qwen3-235B) 203 -> 318 at 32K with the fix. At the pin q8_0 is the faster cache for long prompts.
- ERROR: the proposed KLD gate (llama-perplexity --kl-divergence at -c 16384) does not fit. perplexity.cpp:510-525 and 1738-1743 keep the second-half logits (n_vocab x 4 B) plus uint16 log-probs per chunk: 10.43 GiB (base) / 8.11 GiB (kld) of host RAM at -c 16384 on top of 155.78 GiB. Fits at -c 4096 -b 512 (2.0-2.6 GiB host). Long-context check must go through llama-server at 1 x 16,384 (f16 155.78/155.70; q8_0 154.40/154.32).
- Overstatement: today the cross-check sends no sources (approval.py:430-431), max_tokens 400; 32K is for the planned source-passing plus Baichuan thinking. No chunk size is set anywhere (4-12K is UNVERIFIED).
- Missed option: stopping the reranker (baseline 4.1 L267 relief valve, no Day 1 caller, ~4.79 GiB) gives Q5_K_S + f16 at native 40,960: 160.18 vs 160.79 GiB. Departs from always-resident set; a fallback before Q4_K_M, not the default.
- Nothing new from internlm or baichuan-inc since 2026-10-15; Intern-S2-397B: Q2_K 143.89 GiB fits, IQ3_XXS 161.39 does not (claim confirmed).

## D-19: survives, with corrections
- AA payload re-read: GLM 41.81 / TB2.1 84.27 / TB4.0 32.83 / SciCode 51.62; Coder-Next 9.24 / 38.20 / 0 / 36.23; V4-Flash-0731 34.33 / 78.65 / 12.12. Vals SWE-bench (updated 2026-09-01): GLM 92.00, V4-Flash 0731 88.80, Qwen 3.8 27B 86.00, GPT OSS 120B 33.60, no Coder-Next row.
- NEW cost: AA output tokens per task. SciCode: GLM 27,041, Coder-Next 1,055, V4-Flash 31,296. Index average: GLM 68,673 (46,649 reasoning), Coder-Next 15,145 (0 reasoning). Wall-clock per SciCode-type task at node decode: GLM 26-38 min, Coder-Next 0.4-0.5 min, V4-Flash ~75 min. The researcher's "2,000-token answer, 2.1-3.5x" understates the gap.
- Under the researcher's own worst-case loss (x0.913), GLM's lead over V4-Flash-0731 disappears on SWE-V (84.0 vs 88.8) and TB2.1 (76.9 vs 78.65); V4-Flash leads Vals LCB 87.26 vs 80.51. GLM keeps TB4.0 and speed/residency. T-03 coding set is a real GLM vs V4-Flash comparison.
- "Only fitting seat that reaches 37-44 tok/s" is wrong: gpt-oss-120b is faster (57-60 projected) but 33.6% on Vals SWE-V.
- New since 2026-10-15: Nemotron 3.5 Bolt 74B-A9B (AA 21.4, TB4.0 0, SciCode 39.7), Nemotron 3.5 Super VL 120B (26.5), Beam 1.0 501B (30.3), Muse Spark Open 816B (38.9). None beats GLM on coding or changes D-19.
- TF_MU today has no engine field and runs on its owner's default (baseline L615); the engine field is a real change the Principal should confirm explicitly.
- llama.cpp master b9acf13..HEAD (232 commits, latest tag b11706): nothing for qwen3next or GLM coding.
