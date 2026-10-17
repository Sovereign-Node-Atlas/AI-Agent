# D-19 working notes: the coder seat (2026-10-17)

## Recommendation

**Option (c): no dedicated coder seat. GLM-5.3-Flash does the coding, Qwen3-Coder-Next is not pulled, and (d) Qwen3.8-27B stays dropped.**

A bake-off on the Principal's own code should **not** decide this. A small set of the Principal's coding tasks should be added to the T-03 task-delta run instead. That run compares GLM `UD-IQ4_XS` with DeepSeek-V4-Flash-0731 `UD-Q8_K_XL`. Both are Day 1 files, so nothing extra is pulled, and the set checks that GLM's 4-bit file is not damaged on code.

## Register row and sources read

- register_final.json `decisions` D-19, VERIFIED:
  - The options are (a) Q8_0, 84.81 GB / 78.99 GiB, 37-44 tok/s; (b) BF16, 148.51 GiB, about 9 tok/s; (c) GLM covers coding; (d) Qwen3.8-27B.
  - The register recommends dropping (d). It then recommends (a) if a dedicated seat is wanted, (b) only if unquantised weights are worth the speed, and otherwise (c).
- P-16 says "Coder (if dedicated)". Its runner-up is GLM-5.3-Flash, at 92% on Vals SWE-bench.
- N-04 says "Include with conditions".
- Arsenal report, VERIFIED:
  - L32: coder row. BF16 is listed, at about 9 tok/s; Q8_0 runs at 37-44.
  - L45: verdict row 4. Q8_0 is the practical rung, and GLM beats Coder-Next on SWE-bench.
  - L67: there is no per-rung KLD, and Q8_0 is taken as about equal to BF16 from the general ladder.
- Roster report, VERIFIED:
  - L70 and L90: open models score 15 to 20 points below their vendor tables on a fixed harness.
  - L128: the sizes.
  - L134: AA puts Coder-Next at 9.
- gap_closure L61-L63: the Q8_0 size is 84,812,055,968 B. The card says apache-2.0, but the LICENSE file returns 404. VERIFIED.
- FRAMEWORK_REVIEW, VERIFIED:
  - L375: the 5.1 phased row, "the dedicated coder seat (P-16), on TF_MU".
  - L592: TF_MU belongs to Valerie.
  - L615: rules in 8.4. A seat is reached only through a director binding or a preset's `engine` field. Each extra engine costs a swap of about 50-85 s plus re-prefill.
  - L107 and L109: GLM decodes at a projected 12-14 tok/s. On a PRO 495, a 3.84 bpw file measured 17.4 tok/s, and prefill measured 256.4 tok/s at 2K and 95.9 at 32K.
- config:
  - `task-forces.json:183-197`: TF_MU has no engine field on Day 1, and its notes name Coder-Next as the phased engine field.
  - `engines.json:383-389`: the phased entry.
  - `personas/valerie.md:11-13`: `override_engines: []`.

## Quality evidence: GLM-5.3-Flash against Qwen3-Coder-Next

These are same-harness, independent results.

| Board (same harness for both) | GLM-5.3-Flash | Qwen3-Coder-Next | Status |
|---|---:|---:|---|
| AA Intelligence Index v4.3.2 | 41.81 | 9.24 | VERIFIED, AA payload re-read 2026-10-17 |
| AA Terminal-Bench 2.1 | 84.27 | 38.20 | VERIFIED, same |
| AA Terminal-Bench 4.0 | 32.83 | 0.00 | VERIFIED, same |
| AA SciCode | 51.62 | 36.23 | VERIFIED, same |
| AA FrontierCode | 41.00 | n/a | VERIFIED, same |

Source: https://artificialanalysis.ai/models/glm-5-3-flash, payload records `glm-5-3-flash` and `qwen3-coder-next`, parsed by brace-matched record.

For reference, from the same payload:
- DeepSeek-V4-Flash (0731, release date 2026-07-31): index 34.33, TB 2.1 78.65, TB 4.0 12.12, SciCode 50.35.
- gpt-oss-120b: index 11.60, TB 2.1 26.22, TB 4.0 0.

Other boards and vendor numbers:

- **Vals SWE-bench Verified.** This runs 500 tasks on a bash-only mini-SWE-agent, last updated 2026-09-01; the page was re-read 2026-10-17. VERIFIED: https://www.vals.ai/benchmarks/swebench
  - GLM 5.3 Flash 92.00%.
  - DeepSeek V4 Flash 0731 88.80%.
  - Qwen 3.8 27B 86.00%.
  - MiniMax-M3 75.00%.
  - Devstral 2 62.80%.
  - GPT OSS 120B 33.60%.
  - There is still no Qwen3-Coder-Next row.
- **Coder-Next vendor tech report**, with baselines re-run by Qwen, VERIFIED, `verification_discipline_benchmarks.md:41-48`:
  - SWE-bench Verified: 70.6 with SWE-Agent, 71.1 with mini-SWE-agent, 71.3 with OpenHands.
  - Terminal-Bench 2.0: 25.8 to 36.2.
  - LiveCodeBench v6: 58.93.
  - Aider polyglot: 66.20.
- **Vals LiveCodeBench**: GLM 5.3 Flash 80.51% and DeepSeek V4 Flash 0731 87.26%. VERIFIED, `discipline_benchmarks.md:72`.
- **Vals Terminal-Bench 4**: GLM 37.37% and V4-Flash 18.69%. VERIFIED, `discipline_benchmarks.md:68`.
- **swebench.com**: there is still no Qwen3-Coder-Next or GLM-5.3 row. VERIFIED: the page was fetched 2026-10-17 and grep found no match.

### Arithmetic (INFERRED)

- **Terminal-Bench 2.1.** On AA's harness the gap is 84.27 − 38.20 = 46.07 points. For GLM at IQ4_XS to fall to Coder-Next's level, it would have to lose 46.07 / 84.27 = 54.7% of its score.
- **The largest documented compression loss on agentic coding** in the sources is the ISTA-DASLab Flash-Next coder, with half its experts pruned and the rest at 3.5 bpw. Its SWE-bench Verified falls from 82.80 to 75.60, so 91.3% is retained, a relative loss of 8.7%. VERIFIED: `quantization_value.md:61` and https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF (card L54-L64).
- **GLM after that loss.** Applying the same 8.7% to GLM's 92.00 gives 84.0. That is still above Coder-Next's own vendor 71.1 on the same mini-SWE-agent scaffold.
- **Vendor numbers run high.** Per the roster, vendor tables are 15-20 points above fixed-harness results. This widens the gap further.
- **SciCode** gap: 51.62 − 36.23 = 15.39 points.
- **The 27B comparison.** Qwen3.8-27B (dense, 27B, August 2026) scores 86.00 on Vals SWE-bench. That is 15 points above Coder-Next's own vendor number, which shows Coder-Next (February 2026) is a generation behind. The 27B itself is ruled out by the 70B floor, so (d) stays dropped.

### What GLM's KLD costs

- GLM `UD-IQ4_XS` has a mean KLD of 0.117 and top-1 agreement of 88.18% on Unsloth's harness. VERIFIED, `quantization_value.md:18`.
- The sources' rule for a KLD of 0.07-0.15 is about −1 to −2 MMLU-class points and about −5 on maths, with long agentic chains amplifying the loss. VERIFIED, `quantization_value.md:214-215`.
- No source measures GLM's 4-bit loss on coding. UNVERIFIED. This is the one real gap, and T-03 must close it on the node.

## Q8_0 against BF16 (option b)

- **ubergarm measured Qwen3-Coder-Next itself** on wiki.test.raw, 584 chunks, n_ctx 512 (ik_llama.cpp). VERIFIED: https://huggingface.co/ubergarm/Qwen3-Coder-Next-GGUF (card, "Quant Collection").
  - BF16, 148.502 GiB: PPL 8.2278 ± 0.0639.
  - Q8_0, 78.982 GiB: PPL 8.2239 ± 0.0639.
  - Q4_0 mix, 44.355 GiB: 8.2543.
- **Arithmetic** (INFERRED): Q8_0 minus BF16 is −0.0039 PPL (−0.05%), about one sixteenth of one standard error, so there is no measurable difference. Even the 4.8 bpw mix costs only +0.32%. No KLD is published.
- **Aider polyglot** (Unsloth, third party): UD-IQ3_XXS "comes close to BF16". VERIFIED: https://unsloth.ai/docs/models/qwen3-coder-next.md L956.
- **Speed.** BF16 decodes at about 9.4 tok/s on Vulkan, against 37-44 for Q8_0. VERIFIED, `single_engine_performance.md:109-110` and `verification_single_engine_performance.md:31`.
- **Fit.** At the current ~156 GiB budget, BF16 leaves 156 − 148.51 = 7.49 GiB. A 256K context needs 6.0 GiB of f16 KV plus 0.07 GiB of recurrent state plus compute buffers, so 256K does not fit. INFERRED.
- **So (b) buys nothing and costs a fourfold speed loss.**

## Cost of a dedicated seat (option a)

- **Disk.** 84.81 GB is 1.06% of the 8 TB volume, which is small. INFERRED.
- **Swaps.** Each excursion from the resident GLM to Coder-Next means loading Coder-Next (84.81 GB at 1.46-2.4 GB/s, 35-58 s) and reloading GLM (156.82 GB, 65-107 s). A round trip is about 100-165 s, plus the re-prefill of the director's context: GLM prefills 32K in about 3-4 min with no prompt cache. INFERRED, from `single_engine_performance.md:286,294` and FRAMEWORK L415.
- **Under (c) a coding request costs no swap**, because GLM is the default resident seat (D-23).
- **Engineering.**
  - A Phase 3 load test.
  - A T-02 parity row and a T-03 KLD row.
  - An Arbiter class and a P-16 persona.
  - The TF_MU binding.
  - The licence acceptance. The LICENSE file still returns 404 (VERIFIED 2026-10-17: https://huggingface.co/Qwen/Qwen3-Coder-Next/resolve/main/LICENSE returned HTTP 404). The repository's siblings list only README.md, and its lastModified is 2026-02-03.
- **The real benefit of (a) is speed.** Decode is 37-44 tok/s against GLM's 12.5-17.4, which is 2.1-3.5× faster. INFERRED: 37 / 17.4 = 2.13 and 44 / 12.5 = 3.52. Prefill is also faster, since only 3B parameters are active against GLM's 18B (INFERRED). A 2,000-token answer takes 45-54 s against 115-160 s. The Principal ranks quality first, so speed does not decide.

## Web check for releases since 2026-10-15

Every check below was run on 2026-10-17. Each source was read by HF API, which counts as VERIFIED.

- **Qwen.** The org has published no new coder; the newest is Qwen-Image-2.1-Turbo (2026-10-09), and no Qwen3.8-Coder exists. The Qwen3-Coder listing ends at Qwen3-Coder-Next-GGUF (2026-02-02). Source: https://huggingface.co/api/models?author=Qwen&sort=createdAt
- **New or recent coder releases, none of which qualifies:**
  - Kwaipilot KAT-Coder-V2.5-Dev (2026-07-23) has 34.66B parameters, which is under the floor. https://huggingface.co/api/models/Kwaipilot/KAT-Coder-V2.5-Dev
  - moonshotai Kimi-K2.7-Code (2026-06-11) has 1.03T parameters and does not fit.
  - inclusionAI Ling-3.1-flash (2026-10-13) has 572B parameters in FP8 and does not fit at a sane rung.
  - stepfun Step-5-Preview has 604B parameters and does not fit.
  - Meta Muse Spark Open (2026-10-15) is 816B-A42B and does not fit. https://huggingface.co/unsloth/Muse-Spark-Open-816B-A42B-GGUF
  - tencent Hy-Memory-30B-A3B (2026-10-15) is 30B, under the floor.
  - JetBrains Mellum2.1 is 12B (search excerpt only, UNVERIFIED).
- **Community Flash-Next coder prunes** include ISTA-DASLab GSQ-RCO-Coder at 58.4 GB and 1.89 effective bpw, and Schiltmans cut240. These are heavily compressed third-party derivatives, which runs against "less quantisation", so they are not candidates.
- **llama.cpp after b11474** (b11706 on 2026-10-17): nothing found that changes qwen3next or GLM coding behaviour. The GLM5Next MTP work, #29928, is already in the pin; the pin is commit b9acf13 of 2026-10-07. VERIFIED: https://github.com/ggml-org/llama.cpp/releases and the PR searches for qwen3next and glm5.
- **Unsloth's Coder-Next GGUF note**: "Feb 4: llama.cpp fixed a bug ... vectorized key_gdiff ... please re-download". This was a runtime fix, long inside the pin. VERIFIED, Unsloth doc L16.

## Should a bake-off decide? No.

INFERRED arithmetic:
- With 30 tasks per engine, the 95% confidence interval at p = 0.5 is ±17.9 points for one engine and ±25 points for the difference between two unpaired engines. A paired design narrows this, but not below about ±15.
- The public same-harness gaps are 15 to 46 points in GLM's favour, so a Principal-sized bake-off can only confirm the choice, unless Coder-Next wins by a margin that contradicts every independent board.

What the Principal's own code should test instead is whether GLM's 4-bit file is broken on code:
- Add 20-30 of the Principal's coding tasks to T-03.
- Score GLM `UD-IQ4_XS` against DeepSeek-V4-Flash-0731 `UD-Q8_K_XL`, which is lossless and on disk on Day 1, so nothing is pulled.
- At full precision, GLM leads V4-Flash on Vals SWE-bench (92.0 against 88.8) and on TB 4 (37.4 against 18.7).
- If the node shows GLM clearly below V4-Flash on the Principal's code, the 4-bit loss is real. In that case point TF_MU at deepseek-v4-flash, which is also a Day 1 seat, not at Coder-Next.

## What would change the recommendation

1. **Speed becomes the deciding factor.** If the Principal sets a coding decode floor (D-47) above what GLM measures in Phase 3 (projected 12.5-14 tok/s, 17.4 measured on a PRO 495 at 3.84 bpw), then choose (a) Q8_0. It is the only fitting seat at or above the floor that reaches 37-44 tok/s.
2. **Independent evidence closes the gap.** A fixed-harness result from Vals, AA or swebench.com that puts Qwen3-Coder-Next within about 5 points of GLM-5.3-Flash on SWE-bench Verified or Terminal-Bench would reopen (a). So would a new Qwen coder at or above 70B that fits about 156 GiB.
3. **GLM fails on code on the node.** If T-03 shows GLM `UD-IQ4_XS` losing heavily on code, move TF_MU to DeepSeek-V4-Flash-0731 (lossless, Day 1), or later to the phased Qwen3.8-Flash-Next (AA TB 2.1 86.1, BF16 SWE-V 82.8). Neither change is (a).
