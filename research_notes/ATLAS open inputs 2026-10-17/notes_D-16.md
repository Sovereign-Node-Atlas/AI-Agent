# D-16 working notes: context targets for Baichuan-M3-235B and Intern-S1

Date 2026-10-17. Research only; no repository file edited. Labels: VERIFIED (read in a primary source),
INFERRED (my arithmetic or judgement, shown), UNVERIFIED.

## 1. Recommendation (one line)

Both seats: **32,768 tokens in one slot (`--parallel 1 --ctx-size 32768`), Q5_K_S weights, q8_0 K and V cache.**
Baichuan-M3 moves from mradermacher's static Q5_K_S to the same-size imatrix file `i1-Q5_K_S`. Intern-S1 stays on
bartowski Q5_K_S (already imatrix) and runs text-only (no mmproj); its figures go through GLM-5.3-Flash's glm5v, the
approved vision engine. Raise either seat to its native 40,960 once the measured footprint at pull shows the room.
Q4_K_M is not used.

## 2. What the register and the baseline say

- Register D-16 (register_final.json): options (a) Q5_K_S, about 23K at f16 after a 9 GiB allowance, (b) Q4_K_M for a longer
  context; "These Qwen3-235B-shaped engines carry about 188 KiB/token of f16 KV (192,512 B)". VERIFIED.
- Register D-29 (a), approved: "f16 where the cache is cheap (GLM 19,712 B/token, V4-Flash about 6,880 B/token), q8_0
  elsewhere, q4_0 nowhere". VERIFIED. At 192,512 B/token these seats are ~10x GLM, so under D-29 (a) they are "elsewhere":
  **q8_0 is already the approved policy for them**. The f16 framing in D-16 predates/contradicts it (INFERRED).
- The 23K figure came from the arsenal research's ~164 GiB budget with a 9 GiB allowance
  (research_notes/ATLAS specialist LLM arsenal/single_engine_performance.md: table row "Baichuan-M3-235B Q5_K_S ... 23K"; VERIFIED).
  The baseline now budgets ~156 GiB (155.9-156.9) with a 2 GiB compute allowance (docs/ATLAS_FRAMEWORK_REVIEW.md 4.1 L258,
  arbiter.py L197 `DEFAULT_COMPUTE_BUFFER_GIB = 2.0`), and 4.1 L273 already says the Q5_K_S rungs "leave about 3 GiB for
  context ... short of the D-16 default of about 23K tokens at f16". VERIFIED.
- Doc 5.1 L372 (Intern-S1) and L374 (Baichuan): defaults Q5_K_S at about 23K, Q4_K_M for longer. VERIFIED.
- Arbiter: KV_CLASSES = {f16, q8_0, q4_0, none} for every arch, no qwen3moe restriction (config.py L98-L103, L313-L318);
  KV_CLASS_FACTOR q8_0 = 8.5/16 (arbiter.py L188-L190); a phased engine brings `kv_bytes_per_token_f16` in engines.json
  (arbiter.py L161); both phased entries carry 192512 (engines.json L335, L376) and native_ctx 40960 (L334, L375). VERIFIED.
- Pinned llama.cpp b11474: Vulkan flash attention accepts Q8_0 K and V (ggml-vulkan.cpp L15481-L15527, `fa_kv_ok`),
  and a quantised cache gets the Hadamard rotation automatically when head size % 64 == 0 (llama-kv-cache.cpp L321-L355;
  qwen3moe head size 128, so n_rot_k = 128, n_rot_v = 64; logged at L358-L359). VERIFIED in source.

## 3. Bytes and windows (VERIFIED unless marked)

| Item | Value | Source |
|---|---|---|
| KV per token, f16 | 94 layers x 4 KV heads x 128 x (K+V) x 2 B = 192,512 B | GGUF headers read today: block_count 94, head_count_kv 4, key/value_length 128 (bartowski Intern-S1 Q5_K_S shard 1; mradermacher Baichuan i1-Q5_K_S) |
| KV per token, q8_0 | 192,512 x 34/64 = 102,272 B (0.53125x) | ggml q8_0 block = 34 B per 32 values (arbiter.py L185) |
| Intern-S1 Q5_K_S | 161,963,285,952 B = 150.84 GiB (imatrix: `quantize.imatrix.dataset = calibration_datav5`) | HF tree + header |
| Intern-S1 Q4_K_M | 142,654,184,800 B = 132.86 GiB; Q5_K_M 155.43 GiB; Q4_1 137.21; nothing between Q4_K_M and Q5_K_S except Q4_1 | bartowski tree |
| Intern-S1 mmproj | f16/bf16 11,221,608,064 B = 10.45 GiB; official Q8_0 5,972,727,424 B = 5.56 GiB | bartowski and internlm/Intern-S1-GGUF trees |
| Baichuan i1-Q5_K_S | 161,881,426,496 B = 150.76 GiB (imatrix `imatrix-training-full-3`, 318 chunks) | mradermacher/Baichuan-M3-235B-i1-GGUF tree + header |
| Baichuan static Q5_K_S | 161,881,426,240 B = 150.76 GiB (the current engines.json file) | mradermacher/Baichuan-M3-235B-GGUF tree |
| Baichuan official Q4_K_M | 143,782,829,376 B = 133.91 GiB; i1-Q5_K_M 155.36 GiB (no room for any KV) | trees |
| Native window | Both GGUF headers: `qwen3moe.context_length = 40960`. Baichuan config max_position_embeddings 40,960, rope_theta 5e6, no rope_scaling. Intern-S1 upstream config was raised to 65,536 on 2025-08-29 ("Enlarging max_position_embeddings to support long-context generation for the specific inference engine"); bartowski's GGUF (2025-08-12) still says 40,960 | headers; config.json; HF commits API |

KV at context: 16,384 f16 2.94 GiB / q8_0 1.56; 32,768 f16 5.88 / q8_0 3.12; 40,960 f16 7.34 / q8_0 3.90. (INFERRED arithmetic.)

## 4. Fit arithmetic (INFERRED; budget B = 155.9 / 156.0 / 156.9 GiB, Arbiter compute allowance 2.0 GiB, no per-slot state for qwen3moe)

Room for KV = B - weights - 2.0:

| Seat, rung | Room at 155.9 / 156.0 / 156.9 | Max f16 tokens | Max q8_0 tokens |
|---|---|---|---|
| Intern-S1 Q5_K_S | 3.06 / 3.16 / 4.06 GiB | 17,066 / 17,624 / 22,644 | 32,125 / 33,175 / 42,624 |
| Baichuan i1-Q5_K_S | 3.14 / 3.24 / 4.14 | 17,492 / 18,049 / 23,069 | 32,926 / 33,976 / 43,425 |
| Intern-S1 Q4_K_M | 21.04 / 21.14 / 22.04 | 117K (cap 40,960) | cap 40,960 |
| Baichuan Q4_K_M official | 19.99 / 20.09 / 20.99 | 111K (cap 40,960) | cap 40,960 |

So the D-16 default "Q5_K_S at about 23K, f16" is refused by the Arbiter at the expected 156 GiB
(150.84 + 4.22 + 2.0 = 157.06 GiB). At f16 the honest Q5_K_S figure is ~17.6K.

Projected totals for the recommended profile, 32,768 tokens q8_0:
- Intern-S1: 150.84 + 3.12 + 2.0 = **155.96 GiB** (margin -0.06 to +0.94 GiB).
- Baichuan: 150.76 + 3.12 + 2.0 = **155.88 GiB** (margin +0.02 to +1.02 GiB).
- At 40,960 q8_0: 156.74 / 156.67 GiB, fits only at the top of the range or with a measured compute buffer of about 1.2 GiB or less.
- Fallback 28,672 q8_0: 155.57 / 155.49 GiB (margin 0.33 to 1.41).

The 2.0 GiB allowance is a planning figure (arbiter.py L192-L197, "No log of them exists for any Day 1 engine on
gfx1151"). For qwen3moe at -ub 512 with flash attention, the reservation is dominated by the logits tensor
(151,936 vocab x 512 outputs x 4 B = 311 MB; reservation uses n_outputs = min(n_ubatch, n_outputs_max = n_batch),
llama-context.cpp L252, L673) plus MoE and attention intermediates of tens of MB. I estimate 0.5 to 1.5 GiB in total,
Vulkan preallocations included (INFERRED, not logged). So the real margin at 32K is probably about 1 GiB more than the
projection shows.

Intern-S1 with vision: Q5_K_S + the official Q8_0 mmproj is 150.84 + 5.56 + 2.0 = 158.40 GiB before any KV, so it does not fit.
Vision needs Q4_K_M: + Q8_0 mmproj, 40,960 f16 = 148.76 GiB; + f16 mmproj, 40,960 f16 = 153.65 GiB (1.0 GiB vision compute
included, INFERRED).

## 5. What each seat actually reads

**Baichuan-M3, Minerva's medical cross-check (TF_UPSILON, 9.2).**
- As built, the checker sends a fixed system prompt plus the draft and the drafter's reasoning, with the draft cut at 24,000
  characters and `max_tokens` 400 (approval.py L337-L343, L386-L388, L430-L431). VERIFIED. It does **not** send the cited
  sources today.
- Prompt size: 24,000 chars is about 6,000 tokens at about 4 chars/token, plus 0.5 to 2K of reasoning and about 150 tokens of
  system text, so at most about 8K (INFERRED). Adding the cited sources, which a medical check needs if it is to check claims
  against evidence, takes about 4 to 12K more: retrieval returns k = 4 chunks per query by default (memory.py L684-L685,
  VERIFIED), there are 1 to 3 queries, and chunks run about 500 to 1,000 tokens (INFERRED). That makes about 12 to 20K.
- Output: the card's example runs `thinking_mode='on'` with `max_new_tokens=32768` (Baichuan-M3 README, VERIFIED). A verdict
  with reasoning needs a few K tokens (INFERRED). With thinking on, `max_tokens` 400 would truncate the reasoning before the
  JSON, so the checker has to change when Baichuan takes over the check (build change).
- Minerva drafts on GLM within a 32K slot (4.3 L308, VERIFIED), so a 32K checker can see everything the drafter saw and still
  keep about 12K for its reasoning. At ~17.6K (f16) the sources and the reasoning cannot both fit.

**Intern-S1, the science seat (domains 15, 35, 36; Minerva's runner-up).**
- Inputs are papers: a 6 to 10K-word paper is about 8 to 15K tokens (INFERRED). Docling gives tables as structured text.
  Datasets come as excerpts, and a CSV quickly reaches 10K or more tokens.
- Thinking is on by default ("Intern-S1 enables thinking mode by default"), and every card example uses `max_new_tokens=32768`
  (Intern-S1 README L123, L150, L189, L404, VERIFIED). Qwen3's 40,960 window is 32,768 output plus 8,192 input, which is why
  the native figure is 40,960 (INFERRED).
- Figures: 448 x 448 tiles of 256 tokens each (config `image_seq_length 256`, `downsample_ratio 0.5`, VERIFIED). At the pin,
  llama.cpp tiles an InternVL image into up to 12 tiles plus an overview (clip.cpp L1479-L1489, mtmd-image.cpp L1149-L1160,
  VERIFIED), so one figure costs up to 13 x 256 = 3,328 tokens. The vision encoder is InternViT-6B (45 layers, hidden 3200,
  VERIFIED config). Its mmproj (5.56 to 10.45 GiB) rules out Q5_K_S.
- 32K holds one full paper plus about 15K of reasoning. Several papers go through retrieval (4.3 "long-document recall": Vector
  Cortex, never KV tricks). 40,960 is the most the model offers.

## 6. The third option: Q5_K_S + q8_0 KV

- **What it buys:** the same ~3.1 GiB holds 1.88x the tokens (102,272 vs 192,512 B/token), so 32,768 instead of 17,408 to
  17,600. All 5-bit weights are kept.
- **What it costs (published evidence):**
  - llama.cpp discussion #23470: q8_0/q8_0 against f16 gives mean KLD 0.001782 and 98.0% same top token (Qwen2.5-7B, c=512),
    at most 4 of 500 ARC answers changed on any model, and 0 flips in 1,600 deep-context comparisons up to 41K tokens.
    Qwen3.8-27B at 65,536 context has a 99% KLD of 0.006425. VERIFIED (WebFetch of
    https://github.com/ggml-org/llama.cpp/discussions/23470).
  - PR #21038 rotation, which is on at the pin for this head size: gpt-oss-20b AIME25 with a q8_0 cache went from 31.7% without
    rotation to 37.1% with it, against 37.9% at f16 (research_notes quantization_value.md L160, prior-verified). The rotation
    matters, and it is on by default here (VERIFIED source above).
  - Weight error for scale: in the same Qwen3 MoE family with the same KV shape (4 KV heads, head size 128), Qwen3-30B-A3B
    against BF16 measured Q8_0 weights at mean KLD 0.002337 and bartowski Q4_K_M at 0.010136 (94.33% same top). VERIFIED (ubergarm
    gist https://gist.github.com/ubergarm/0f9663fd56fc181a00ec9f634635eb38). For Qwen3-235B, ik IQ5_K costs +0.63/+0.75% PPL and
    IQ4_K +1.37/+1.21% (ubergarm cards; research_notes quantization_value.md, prior-verified).
  - Conclusion (INFERRED): a q8_0 cache costs about what 8-bit weights cost, roughly a fifth of Q4_K_M's weight error. If
    small errors add, Q5_K_S + q8_0 KV (about 0.005 + 0.002) stays nearer the reference than Q4_K_M + f16 KV (about 0.010).
    So (c) beats (b) on quality wherever (c) has room, and (b)'s extra 8K tokens up to the 40,960 cap is all (b) adds.
- **Counter-evidence:** localbench (2026-04-24, a patched llama.cpp, Qwen 3.6 hybrids, top-logprob KL) reports q8_0 KL
  0.024/0.039 overall and 0.142 on long documents (https://localbench.substack.com/p/kv-cache-quantization-benchmark). The method
  and models are not comparable to a pure-attention qwen3moe, and the numbers conflict with #23470. UNVERIFIED for these seats,
  so the node test in section 8 settles it.
- **Gap:** I found no published q8_0-KV measurement for Qwen3-235B, Baichuan-M3 or Intern-S1 (search 2026-10-17). ubergarm runs
  Qwen3-235B with `-ctk q8_0 -ctv q8_0`, but that is usage, not a measurement (https://huggingface.co/ubergarm/Qwen3-235B-A22B-GGUF/discussions/6).

## 7. Newer versions and rungs as of 2026-10-17

- internlm on HF, newest first (API, VERIFIED): AdvancedMathBench-AutoVerifier (09-29), Intern-Decision-0.8/2/4B (09-26),
  Intern-S2-397B (09-13, 403.4B params, only IQ2_M/Q2_K fit, per research notes), Atria-Dawn-Preview (09-11, 753B, MIT,
  GlmMoeDsa: does not fit), Intern-S2-Mobius (07-29, 36.0B), Intern-S2-Preview (05-15, 36.1B: both below the 70B floor),
  Intern-S1-Pro (917B: does not fit). Nothing has appeared since 2026-10-15. Intern-S1's own repo has had only tokenizer, video
  and config commits since 2025-07 (the last, 2026-03-29, adapts the tokenizer to transformers 5), so the weights are unchanged.
  **Intern-S1 is still the only science model of 70B or more that fits at 5-bit.**
- Intern-S1 GGUF repos: bartowski (imatrix, Q5_K_S used), lmstudio-community (Q4_K_M/Q6_K/Q8_0), the official Q8_0/f16,
  and noctrex/essigpeng MXFP4_MOE (a 4.25-bit re-quant of a BF16 model, so no). There is no Unsloth UD and no mradermacher
  235B GGUF. VERIFIED (HF search API).
- baichuan-inc on HF: newest is Baichuan-M3-235B-Q4_K_M-GGUF (2026-02-06). VERIFIED. Baichuan-M4 (arXiv 2606.08982, June 2026)
  has no HF repo under baichuan-inc. That it has no open weights is INFERRED from the listing; the arXiv page states nothing either way.
- Baichuan GGUF: **mradermacher i1-Q5_K_S (imatrix) is byte-for-byte the same size as the static Q5_K_S** now in engines.json.
  At equal size an imatrix quant generally has lower error (INFERRED; I found no Baichuan-specific measurement). spicyneuron
  UD-Q4_K_XL and DevQuasar files exist, but none falls between Q5_K_S and the 155.36 GiB Q5_K_M.
- Licences: Intern-S1 apache-2.0 (API cardData); Baichuan-M3 Apache 2.0 (README). Unchanged, VERIFIED.

## 8. Build and test changes if approved (not made)

1. engines.json `phased`: both entries `kv_class q8_0`, `ctx_size 32768`, `parallel 1`; Baichuan `hf_repo
   mradermacher/Baichuan-M3-235B-i1-GGUF`, file `Baichuan-M3-235B.i1-Q5_K_S.gguf`, 161,881,426,496 B (sha256 read at pull);
   Intern-S1 without mmproj; rewrite the notes, which still carry the 23K f16 default (L339, and Baichuan's equivalent).
2. Baseline text: 4.1 L273, 5.1 L372/L374, D18/D24 rows, open-items list L1312: replace "Q5_K_S at about 23K" with "Q5_K_S,
   q8_0 KV, 1 x 32,768 (native 40,960 if the measured footprint allows)".
3. At each pull, before granting 32K: (a) load at 1 x 16,384 q8_0 and record the Vulkan compute buffer from the load log;
   write it into `compute_buffer_gib`; (b) check that the KV lines read q8_0 and the rotation lines read n_rot_k = 128 and
   n_rot_v = 64, and that LLAMA_ATTN_ROT_DISABLE is unset; (c) quality: llama-perplexity --kl-divergence at -c 16384, f16 cache
   against q8_0 cache on a long medical and scientific corpus (both fit at 16,384: 155.78 / 155.70 GiB); accept q8_0 if mean KLD
   ≤ ~0.005 and same top ≥ ~98% (thresholds INFERRED from #23470); (d) raise to 40,960 if the measured footprint leaves ≥ 0.5 GiB.
4. Cross-checker (approval.py): when Baichuan becomes Minerva's checker, raise `max_tokens` from 400 to cover its fixed
   reasoning setting (D-30), and pass the cited source chunks with the draft.
5. T-11 (the HealthBench subset) runs at the chosen profile.

## 9. What would change the recommendation

- V3a's MemTotal gives a budget below 155.96 GiB and the measured compute buffer is near 2 GiB. Then 28,672 q8_0, still Q5_K_S.
- The node KLD test (8.3c) finds q8_0 clearly worse than f16 on long inputs (mean KLD > ~0.01, or answer flips on the T-11
  set). Then Q4_K_M with an f16 cache at 40,960 for that seat.
- The Principal wants Intern-S1 to read figures itself (chemical structures, spectra, micrographs). Then Intern-S1 runs Q4_K_M +
  the official Q8_0 mmproj at 40,960 (148.76 GiB at f16 KV), and Baichuan stays as recommended.
- A seat needs more than 40,960 tokens in one window. Neither model is trained for that; use retrieval or another seat, not a
  lower rung.
- A newer open science or medical model of 70B or more that fits at a higher rung with a cheaper cache (none as of 2026-10-17).
