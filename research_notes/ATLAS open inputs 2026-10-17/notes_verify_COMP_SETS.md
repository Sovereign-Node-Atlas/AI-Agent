# Verifier notes: COMP (D-60, C-12) and SETS (D-49, D-46), 2026-10-17

Research only. No repository file edited. Downloads in verify_cs/dl, bare git fetches in verify_cs/{lcpp_bare,ds4_bare,mteb_res}.

## D-60 (EmbeddingGemma 2): survives, minor corrections
- EG2 card re-fetched: L80 61.36, L81 78.68 (EG1 68.76), L83 57.28, L86 69.54, L96 68.46 / 59.01, L189-193 no fp16, L282 Gemma PUP. OK.
- Qwen3-Embedding-4B card: 69.45 at L246, 74.60 at L263 in my fetch (researcher said L245/L262; off by one).
- Primary source for code: Qwen3 Embedding paper Table 3, MTEB(Code, v1) 4B = 80.06 (arxiv.org/html/2506.05176v3). Researcher's own 80.07 agrees.
- Qwen3-VL-Embedding-8B card: apache-2.0; MMEB-V2 rows L81-82 (2B 73.4, 8B 77.9). MMTEB text: 2B 63.87, 8B 67.88, both below Qwen3-Embedding-4B 69.45, so the text index stays on Qwen3.
- Pin: b11474 = b9acf138. Fetching 400 commits shows 4fbc76d (EG2 support, 2026-10-06) and 680a036 (#29556, typed multimodal /v1/embeddings, message names Qwen3-VL-Embedding) are ancestors. File:line checks hold.
- MTEB results @12eaee4 (2026-10-16): no google__embeddinggemma-2. Qwen3-VL-Embedding-2B/8B are present.
- Community GGUFs exist (gorse-io/Qwen3-VL-Embedding-{2B,8B}-GGUF, 2026-10-14). They are unofficial.
- "Only EG2 does non-speech audio" is overstated. LAION CLAP (laion/clap-htsat-unfused, larger_clap_general) is Apache 2.0, older and not compared here.
- Missed doc line: L930 lists EG2 0.31 GB in the phased-engine storage total.

## C-12 (MedGemma 1.5 4B): survives with corrections
- Card re-fetched (last updated 2026-04-21). All quoted figures match. The researcher's "trails by ≤1.7 on 3 rows" omits SLAKE closed-subset accuracy (82.8 vs 85.9, -3.1), which falls under SLAKE.
- arXiv 2512.00271 was submitted 2025-11-29, before MedGemma 1.5 (2026-01-07), so it tested MedGemma 1, not 1.5. From the abstract: hallucination 5.4-17.4% for the non-AIRead VLMs (radiologists 0.1%); clinical acceptability 41.1-71.4% (radiologists 74.3%).
- HAI-DEF terms and PUP re-fetched; the clauses check out.
- unsloth GGUF sizes and hashes are verified, and that repo is NOT gated. The terms still bind (1.1(d)). The click-through on Google's repo is needed only to read Google's chat template/tokenizer for the template check.
- GGUF headers (my own parser): 4B is gemma3, 34 blocks, 4 KV heads, key_length 256, SWA 1024, clip projector gemma3 at 896. 27B is 62 blocks, 16 KV heads, key_length 128.
- KV arithmetic re-done: 4B 0.79 GiB, 27B 3.11 GiB at 32k. Matches.
- BIG CORRECTION: config.py:90 ARBITER_CLASSES has no "companion" class. is_llm_seat (config.py:363-367) counts every chat/vision llama-server that is not resident, phase4 or external. A MedGemma unit would therefore be an LLM seat, and rule 7 exclusivity (arbiter.py:1099-1104) would evict gpt-oss-120b. "Fits beside gpt-oss with no eviction" needs a code change: a new on-demand non-seat class, plus tests and CONVENTIONS §8.
- Missed option: GLM-5.3-Flash's own vision (Minerva's default is glm-5.3-flash, "Scans move you to the vision engine") reads scans with no eviction. The node test should score GLM's read of the same cases as the baseline.
- Missed option: MedSigLIP (google/medsiglip-448, HAI-DEF) as a broader embedding encoder than Rad-DINO, if HAI-DEF is accepted anyway. Its scope is UNVERIFIED (README gated).

## D-49: survives, with corrections
- loadtest.py L320-338 and L2364-2373, doc L54-55, L64, L457, L724, L738, L1313, domain files L7-9, completeness_review L218 and register lines all match.
- Allens 2024 approach page: 30 questions, 10 areas, 5/3/2. Also "a single fictitious citation results in 0 marks for citations" (support for the fatal flag). No 2026 Allens edition was found.
- robots.txt checks hold. AustLII also lists ClaudeBot etc. as user-agents, and its content signal does not grant ai-input. Draft from legislation.gov.au and the state registers.
- Category arithmetic re-summed: all OK. Wilson intervals OK.
- Run time: 28 h holds only at 3,000 tokens per answer and 600 per judge call. All three engines reason at high effort (engines.json L46-48, L157), and max_tokens counts reasoning (D-47 verifier). At 6,000 per answer and 1,500 per judge call the total is 39.2 + 19.6 = 58.8 h (about 6-7 nights).
- Privacy: the "research session" can draft only public-source items. The Principal's briefs, redacted examples and the 30% private items must be drafted on the node (doc L55).
- Framing: D-49 (a) says the Principal supplies the sets. The recommendation keeps (a) and changes the mechanics (ATLAS drafts, the Principal approves). Say so; this is not an undoing.
- Weak-judge risk: gpt-oss-120b judging GLM or DeepSeek on law. Reference-guided grading and calibration mitigate it.

## D-46: survives, with corrections
- Power table reproduced. Correction: n=37 (k=24) already reaches 0.807, so 40 is not "the smallest" size. With 25% ties, 30 decided gives power 0.73.
- PR #28696 re-fetched: Open, no approvals, CPU/CUDA only, ~16K cap, latest Oct 10 comment. No change.
- DeepSeek HF: V4.1 (2026-09-10) is still the newest. The commits are as quoted.
- DwarfStar main @fc80bd6, 2026-10-07: still the newest.
- CORRECTION: DwarfStar's V4.1 fixtures (d9bc45d, 2026-09-19) are "official" hosted-endpoint continuations: 112 cases, temperature 1, top-20 logprobs, prompts up to 23K (gguf-tools/quality-testing/README.md L33-37). They do not come from the reference python. The 2026-10-12 reference-code fixes therefore do not invalidate the fixtures, and the parity check tests DwarfStar against the production model directly. The 10-16 encoding fix covers tool results and images, which are not used, so it does not apply. Keep the gate, reframed.
- Production caps: deep_think.py max_tokens 1024 (scoring, L265), 2048 (L332, L360), 3072 (deep, L406), all including reasoning. 6,000 tokens per task is above production. Run at the production caps per role and record truncation. Run time then ranges about 15-35 h.
- The "promise" bar for T-32 (≥55% preference) is too high for the antirez file. Lucebox's own bench has antirez Q2 at 141/177 against Geometric's 164/177 (lucebox_engine_memory_hierarchy.md L207, vendor). Suggest promise = ≥45% preference with no fatal excess.
- "Build the set now": B, D and E only. A and C (and B's GLM trajectories) need the live node.
- Cheap hardening: run the V4.1 container with no network while it holds Sensitive TF_OMEGA tasks.
