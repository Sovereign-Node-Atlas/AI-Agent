# Completeness review: ATLAS specialist LLM arsenal (2026-10-15)

- Reviewer role: completeness critic. Research only; no file other than this one was edited.
- Files read in full (14): `named_heavyweight_moe.md`, `named_120b_class_and_uncensored.md`, `named_companion_engines.md`, `other_large_specialists.md`, `quantization_value.md`, `single_engine_performance.md`, `discipline_benchmarks.md`, and the seven `verification_*.md` files. Context files read: `ATLAS LLM roster refresh 2026/current_atlas_baseline.md` (roster, Arbiter rules, personas, 36 domains), and the Takeaways of `Lucebox engine and memory hierarchy/lucebox_applicability_and_verification.md` (cross-reference only).
- Method for the new evidence below. Hugging Face sizes come from `https://huggingface.co/api/models/<repo>/tree/main?recursive=true`, summing `lfs.size` per rung; mmproj and imatrix files are kept separate. GB = bytes/1e9 and GiB = bytes/2^30. GGUF metadata was read by HTTP range reads of file headers. llama.cpp facts come from the pinned tree `/home/user/ggml-org/llama.cpp` (b29c606 = v0.4.1) and from a full clone of `ggml-org/llama.cpp` fetched today to master `08698c81e` (2026-10-15 11:00 +0200; newest build tag b11643). "First tag" means the first `bNNNNN` tag that contains the squash commit. Every fact is labelled primary (read today) unless marked otherwise.
- Labels used: **closed** = I closed the gap from primary sources; **partly closed** = some of it is closed and the rest is stated; **open** = it can only be closed by a node measurement or by a source that does not exist yet.

## 1. Coverage against the Principal's asks

| Ask (directive) | Status in the notes and verifications | Gap found by this review |
|---|---|---|
| Verdict basis for each of the 12 named models and engines (D6) | **The facts behind a verdict exist for all 12, but no file states a verdict for any of the 10 LLMs.** `named_120b_class_and_uncensored.md` is truncated mid-row (KQ2 table cut off; KQ3–KQ5 absent). Its verification file restored the core facts for Nemotron 3 Super, Qwen3-Coder-Next and Mistral Large 2 Uncensored. Only the two companions carry a proposed verdict (verification_named_companion_engines, Gaps closed 10) | Section 2 below collects one evidence row per named model, so the writer has every input a verdict needs. Several inputs are new: the Mistral Large 3 successor, uncensored-variant data and pin status |
| A 70B-plus specialist for every persona, agent and discipline, with the fitting rung, quality cost and speed (D1, D2) | Evidence is spread across five files, and no file maps a seat to an engine. Seats with no discipline evidence: Victor (aviation, transport), Alaric (physical security, OSINT), domains 31–36, HR, AEC, SCADA, game design, customer success and growth marketing | **Partly closed.** G10 adds LMArena category data for these seats, G7 adds the missing science specialists, and Section 5 gives a seat-by-seat evidence matrix |
| Effect of quantisation made explicit per engine (D4) | Strong: a threshold table plus per-model ladders (`quantization_value.md` with corrections). Missing: mainline KLD for Qwen3-235B, Maverick, Nemotron, Coder-Next and Mistral; nothing for abliterated GGUFs | Still **open**: it needs a node `llama-perplexity --kl-divergence` run. G4 adds uploader-measured capability retention for one uncensored build (MiMo) and UGI NatInt deltas |
| Abliterated and uncensored engines (D5) | gpt-oss variants are well covered (UGI). The large 2026 abliterations (GLM, Flash-Next, V4) have no refusal or quality data. MiMo uncensored builds were not evaluated | **Partly closed** (G4, G5): Derestricted Q8_0, MiMo uncensored, stock-model uncensoredness on UGI, the Nemotron licence and the ablation scope of the huihui builds |
| Sequential operation and swap costs (D3) | Covered: load path at the pin, cold-read projections, re-prefill, slot save/restore (`single_engine_performance.md` KQ3 and its verification) | Open: node measurement only. G9 adds the interaction between companion residency and the engine budget, which the notes miss |
| Companions: embeddings, reranker, diarisation, router, OCR, speech, vision | Embeddings, reranker and diarisation are covered in depth. **Router, OCR, speech-to-text and TTS were not re-evaluated in any file.** The vision seat appears only as scattered benchmark rows | **Closed for router, OCR and STT** (G8). Vision closed for pin support (G3). TTS left open |
| Licences | Read for most candidates. Nemotron's licence text was not read (HF tag only); the baseline marks it "unverified". The MRL issue for Mistral Large 2 is recorded | **Closed** for Nemotron (G5) and for Mistral Large 3 (G6) |
| llama.cpp support relative to the pin, and whether a newer pin is justified | Checked per model, but no file brings together what a re-pin would buy, which tag it would need, or the correctness fixes made after the pin | **Closed** (G1, G2, G3) |

## 2. The twelve named models: evidence in one place (verified figures; new items marked NEW)

| Named item | What it is today | Least-quantised rung that fits alone (164 GiB) and its quality cost | llama.cpp at v0.4.1 | Speed on the node (projection unless stated) | Independent quality | Licence | Uncensored path |
|---|---|---|---|---|---|---|---|
| DeepSeek-V4-Flash | 0731 is the latest text checkpoint (284B/13B). Vision-Exp (2026-08-31) has the same architecture. V4.1-Flash is a different model: 552B plus 196B Engram, no llama.cpp support, smallest GGUF 340.60 GiB | UD-Q8_K_XL 150.75 GiB, bit-exact (KLD ≈0, 100% top-1). UD-Q4_K_XL 144.44 GiB, KLD 0.0102. DSpark does not fit next to Q8 | Inside (deepseek4, quantised-KV fix #25202, Vulkan indexer #27453, hc fusion #26578) | Q8 ~7 tok/s and Q4 ~11 tok/s on mainline Vulkan. These figures predate in-pin kernels, so pin speed is unmeasured | AA 34.33; LMArena 1432.2; Vals SWE 88.8% | MIT | huihui Q4 only (experts not ablated). NEW: the stock preview checkpoint scores UGI W/10 7.2 with reasoning off (G4) |
| Qwen3-235B-A22B | Original April 2025 hybrid checkpoint (the one Vals tested), plus 2507 Instruct/Thinking and VL | Q5_K_S 150.76 GiB (~5.5 bpw). Nearest measured point: ik IQ5_K +0.63–0.75% PPL. 4-bit costs −3.7 to −4.6 on AIME and GPQA (INT4). Context is limited: ~23K f16 after a 9 GiB allowance | Inside (qwen3moe) | 10.9–13.1 tok/s | AA 12.7 (2507 R); LMArena 1419.1 | Apache 2.0 | Only the April 2025 original is abliterated; no 2507 abliteration exists |
| GLM-5.3-Flash 320B | 320B/18B, FP8 release, native vision | UD-IQ4_XS 146.05 GiB, KLD 0.117 / 88.2% top-1 (lossy). The 3.5-bit GSQ-RCO build lost −1.4 MMLU-Pro and −4.9 on maths, on a weak harness | **Outside** (glm5-next #27773 → b11279 / v0.6.0; vision `glm5v` arrived in the same PR) | 12.4–13.8 tok/s. Measured on a PRO 495: 17.4 tok/s at 3.84 bpw (post-pin build) | AA 41.81 (best of the 12); leads most professional boards | MIT | huihui UD-IQ4_XS: only layers 15–35 ablated, experts untouched (G4). No refusal or quality data |
| Qwen3-Coder-Next | 79.67B/3B, non-thinking only; just above the 70B floor | BF16 148.51 GiB (unquantised). Q8_0 78.99 GiB | Inside (qwen3next) | Q8_0 37–44 tok/s (corrected band); BF16 ~9 tok/s on Vulkan (kernel-limited) | AA 9.24; no Vals or LMArena row; vendor SWE-V 70.6–71.3 | Apache 2.0 | None evaluated |
| Llama 4 Maverick | Unchanged since April 2025 (abandoned line) | 2–3 bit only: UD-Q2_K_XL 142.17, i1-IQ3_XS 152.13, UD-IQ3_XXS 157.69 GiB. No KLD at any fitting rung | Inside (llama4). Vision #13282, merged 2025-05-19, also inside (NEW, G3) | 18–26 tok/s (no anchor) | AA 9.30; LMArena 1287.1 (#260); worst on most boards | Llama 4 Community + AUP | Nekotekina projected-abliterated GGUF (no data) |
| Mistral Large 2 Uncensored | The Ollama `gdisney/mistral-large-uncensored` build: stock 2407 at legacy Q4_0 plus an "Evil Mode" system prompt that tells the model to give misinformation | The named artefact is Q4_0 (64.34 GiB; ~3× the PPL loss of Q4_K_M on 70B-class models). Q8_0 121.33 GiB fits | Inside (llama) | Q4_0 3.2–3.4 tok/s; Q8_0 1.7–1.8 tok/s (dense) | AA 6.80 (2407) / 7.56 (2411); LMArena #282 | MRL: research and non-commercial only | NEW: stock 2411 already scores UGI W/10 7.5 without any prompt (G4). XORTRON (2411 Heretic) Q8_0 121.33 GiB. NEW: the successor Mistral Large 3 does not fit (G6) |
| Qwen3.8 Flash-Next | 125B + 51B n-gram + 4B MTP = 180B, 6B active | UD-Q6_K_XL 157.55 GiB: n-gram table at Q8_0, only gate/up experts at Q6_K; ≈ Q8_0 on Unsloth's harness (KLD 0.0271 vs 0.0266). ISTA 3.5-bit: AIME 100, GPQA 92.93 vs BF16 91.92 | Inside (qwen4exp), but Vulkan hc ops, 512-expert mul_mat_id, the #29751 fix and MTP are all after the pin (G1) | 23–29 tok/s projected vs one buyer report of 15 tok/s on this exact box (unresolved) | AA 39.82; GPQA 92.3; AA TB 2.1 86.1 | Qwen Community 1.0 | huihui v2 UD-Q6_K_XL 157.55 GiB (experts not ablated); orcarouter. No refusal data |
| Nemotron 3 Super Q8 | 120.6B/12.7B hybrid Mamba MoE | Q8_0 119.65 GiB. No GGUF KLD; NVFP4 W4A4 + FP8 KV table −0.4 to −2.4 | Inside (nemotron_h_moe); MTP #29018 is after the pin | 13.6–15.8 tok/s | AA 12.83; LMArena 1377.6; HLE contamination risk (card) | NEW: licence text read, commercial use and derivatives allowed (G5) | Heretic builds (KL 0.1081, reused); licence-compatible (G5) |
| gpt-oss-120b | No new checkpoint since Aug 2025 | Release precision: MXFP4 experts. Highest-precision GGUF is F16-trunk 60.88 GiB | Inside | 57–60 tok/s | AA 11.60 | Apache 2.0 | See next row |
| gpt-oss-120b Abliterated | The node's huihui v2 → noctrex MXFP4_MOE file has no provenance card and no data. huihui-ai repos now return 401 | NEW: ArliAI Derestricted Q8_0 115.76 GiB, which keeps the edited experts near BF16, and MXFP4_MOE 59.03 GiB (G4) | Inside | Derestricted Q8_0 ~37–39 tok/s (projection, G4) | UGI: Derestricted NatInt 35.70 / W/10 9.2; heretic v1 33.41 / 9.5; huihui v1 16.72 / 5.2; base 33.68 / 2.0 | Apache 2.0 | — |
| EmbeddingGemma 2 | 740M multimodal embedder (6 Oct 2026) | Q8_0 0.29 GiB / BF16 0.52 GiB | **Outside** (#30054 → b11452; in no semver release) | — | MTEB multilingual 61.36; English −1.21 vs EG1 | Apache 2.0 | n/a |
| Nemotron 3 Diarization | 100M streaming Sortformer, up to 8 speakers | Official Q8_0 GGUF 0.10 GiB (NeMo-Speech.cpp format, not llama.cpp) | n/a (separate runtime) | No gfx1151 RTF | OpenBench avg DER 0.19 vs community-1 0.28 (0.206 vs 0.300 without the contaminated VoxConverse set) | OpenMDW-1.1 | n/a |

## 3. Gaps closed in this review

### G1. A newer llama.cpp pin: what it buys, which tag it needs, and the correctness fixes made after the pin (closed)
No file brings these facts together, yet three of the strongest candidates (GLM-5.3-Flash, K2-Horizon and EmbeddingGemma 2) need a re-pin, and Flash-Next's Vulkan path is incomplete at v0.4.1. Squash commits and first tags, from the full clone (primary, `git log --grep "(#N)"` plus `git tag --contains`):

| Feature | PR, merge date | First build tag | In v0.5.0 (2026-09-23) | In v0.6.0 (2026-10-05) |
|---|---|---|---|---|
| Nemotron MTP | #29018, 2026-09-17 | b11025 | yes | yes |
| Flash-Next hc ops / Vulkan hc ops | #28901 2026-09-16 / #28988 2026-09-17 | b10998 / b11012 | yes | yes |
| Vulkan mul_mat_id 256 → 512 experts (Flash-Next 512, GLM 288 experts) | #28501, 2026-09-18 | b11029 | yes | yes |
| MiMo-V2.6 converter + chat parser (runtime arch `mimo2` is already in the pin) | #29257, 2026-09-22 | b11102 | yes | yes |
| Vulkan: wrong results when a mul_mat reads a slice of a larger cache | #28956, 2026-09-28 | b11224 | no | yes |
| Rerank batch splitting | #28876, 2026-09-27 | b11223 | no | yes |
| GLM-5.3-Flash (`glm5-next`, plus `glm5v` vision) | #27773, 2026-09-30 | b11279 | no | yes |
| "llama: fix qwen4exp" + Flash-Next MTP | #29751 / #29761, 2026-10-01 | b11330 | no | yes |
| KV rotation restore fix | #28498, 2026-10-05 | b11411 | no | yes |
| Vulkan FA fixes (stale prealloc_y; shmem out-of-bounds) | #29591 / #29988, 2026-10-05 | b11414 / b11424 | no | yes |
| EmbeddingGemma 2 | #30054, 2026-10-06 | b11452 | no | no |
| K2-Horizon-375B | #29535, 2026-10-06 | b11454 | no | no |
| GLM5-Next MTP | #29928, 2026-10-07 | b11474 | no | no |
| k-pool mask layout (GLM, Flash-Next; refactor) | #30480, 2026-10-14 | b11617 | no | no |

- **Flash-Next on a re-pin.** PR #29751 says "Qwen4exp is broken on current master" and "fixes the attention path based on how `llama-memory-hybrid-idx` pooling works". It reports no affected builds; its test base was b046420 (2026-09-30) ([PR #29751](https://github.com/ggml-org/llama.cpp/pull/29751), merged 2026-10-01, read via WebFetch).
  - Several commits between b29c606 and the fix touched `qwen4exp.cpp` or `llama-memory-hybrid-idx.*`: #28896, #28901, #28770, #28751, #27773 (GLM, which shares the memory class) and #29599.
  - So whether the pin itself is affected is **unknown**: the PR does not say when the breakage began.
  - The safe consequence: **a re-pin should be b11330 or later** for Flash-Next, and the pinned build's Flash-Next output should be parity-checked.
- **Vulkan correctness at the pin.** #28956's commit message says the bug made "every head past the first read the wrong rows" when a mul_mat reads a KV-cache view in place (the non-flash-attention path). It is fixed only after the pin. Whether any candidate at v0.4.1 takes this path depends on the `-fa` setting, which was not checked. A parity or KLD test on the pin is therefore needed, not optional.
- **The re-pin choice the writer must state:**
  - v0.6.0 is the newest semver release. It covers GLM, the Flash-Next fixes and MTP, Nemotron MTP, rerank, MiMo and the Vulkan fixes.
  - **b11474 (2026-10-07)** is the first build that adds EG2, K2-Horizon and GLM MTP.
  - b11643 (2026-10-15) is the newest build.
- Sources: [llama.cpp](https://github.com/ggml-org/llama.cpp) history (fetched 2026-10-15); tag dates: v0.5.0 7fe450e19, v0.6.0 d81235049, b11474 b9acf138a, b11617 14310190c.

### G2. GLM-5.3-Flash KV cache was undercounted: the DSA indexer cache was never sized (closed)
- **Config.** All 11 DSA layers have `indexer_types: "full"`, with `index_head_dim` 128, `index_kpool` 4 and `kv_lora_rank` 512 ([config.json](https://huggingface.co/zai-org/GLM-5.3-Flash/raw/main/config.json), read 2026-10-15).
- **Cache layout on master.** `llama_memory_hybrid_idx` caches each token's indexer row as "key | gate | pooled": `n_embd_head_k_full = indexer_head_size * indexer_kpool_row`, and `indexer_kpool_row = 3` by default for glm5-next. It is stored in `type_k` (`src/llama-memory-hybrid-idx.cpp` L54-L72, `src/llama-hparams.h` L306, master 08698c81e).
- **Per-token cost.** The indexer cache is 11 × 384 × 2 B = 8,448 B/token f16. Added to the MLA latent's 11,264 B, the total is **19,712 B/token f16 (10,472 B q8_0)**, against the 11,264 used in `named_heavyweight_moe.md` and `single_engine_performance.md` (computed).
- **Size by context (f16 / q8_0).**

  | Context | f16 | q8_0 |
  |---|---|---|
  | 128K | 2.41 GiB | 1.28 GiB |
  | 256K | 4.81 GiB | 2.56 GiB |
  | 1M | 19.25 GiB | 10.23 GiB |

- **Corrected context limits for UD-IQ4_XS** (17.82 GiB of room after the 136 MiB KDA state):

  | Allowance for compute buffers | Max context f16 | Max context q8_0 |
  |---|---|---|
  | None | ~948K | 1M |
  | 9 GiB | ~469K (the notes say 820K) | ~883K |

  The notes' remark that "context length barely changes their fit" holds only up to about 256K.
- **Flash-Next after a re-pin.** On master, qwen4exp sets `indexer_kpool_row = 2` ("raw key | pooled key"). After a re-pin its indexer row doubles from the 3,072 B/token measured at the pin (verification_named_heavyweight_moe row 21), to about 6,144 B/token (inference from the code). The effect is small.

### G3. Vision seat: support at the pin for each candidate's projector (closed)
- **Pin projector table.** At v0.4.1, `tools/mtmd/clip-impl.h` contains `QWEN3VL`, `LLAMA4`, `DEEPSEEK4V`, `MIMOVL`, `MINIMAX_M3`, `INTERNVL`, `GLM4V` and `PADDLEOCR`, but no `GLM5V` (pinned tree).
- **Per-candidate status.**

  | Candidate | Projector | At v0.4.1 | Source |
  |---|---|---|---|
  | Qwen3.8-Flash-Next | `qwen3vl_merger` (read from the ggml-org mmproj header) | supported; LLM-side mtmd fix #27941 (2026-09-01) is also inside the pin | [mmproj-Qwen3.8-Flash-Next-Q8_0.gguf](https://huggingface.co/ggml-org/Qwen3.8-Flash-Next-GGUF/resolve/main/mmproj-Qwen3.8-Flash-Next-Q8_0.gguf) |
  | GLM-5.3-Flash | `glm5v` | outside; arrived with #27773 (2026-09-30) | [mmproj-GLM-5.3-Flash-Q8_0.gguf](https://huggingface.co/ggml-org/GLM-5.3-Flash-GGUF/resolve/main/mmproj-GLM-5.3-Flash-Q8_0.gguf) |
  | Llama 4 | `LLAMA4` | supported since #13282 (2025-05-19), which closes the notes' open question | — |
  | V4-Flash-Vision-Exp | `DEEPSEEK4V` | supported (#28154, inside) | — |

- **Implication.** At the pin the vision seat can use Flash-Next (UD-Q6_K_XL + 0.62 GB mmproj), Qwen3-VL-235B (Q5_K_S + 1.16 GB mmproj; OmniDocBench 89.78), V4-Flash-Vision-Exp, or MiMo / MiniMax-M3. GLM-5.3-Flash (LMArena vision #23, the top open model; Vals MMMU 86.01%) needs a re-pin. None has a node test.

### G4. Abliterated and uncensored engines: quality and refusal evidence beyond gpt-oss (partly closed)
- **UGI leaderboard** (CSV 1,326 rows, latest test 2026-10-02; space lastModified 2026-10-03) — [UGI data](https://huggingface.co/spaces/DontPlanToEnd/UGI-Leaderboard/resolve/main/ugi-leaderboard-data.csv):
  - No row exists for GLM-5.3-Flash, Qwen3.8-Flash-Next, V4-Flash-0731, MiMo-V2.6 or Nemotron 3 Super, or for any of their abliterations.
  - Stock models that are already weakly censored:

    | Model | UGI | W/10 | NatInt |
    |---|---|---|---|
    | DeepSeek-V4-Flash preview, reasoning off (tested 4/26/2026) | 59.21 | 7.2 | 47.94 |
    | Mistral-Large-Instruct-2411 | 59.23 | 7.5 | 36.21 |
    | Mistral-Large-Instruct-2407 | 51.89 | 5.2 | 37.63 |

  - The cost of abliterating a large MoE, measured on Qwen3.5-397B-A17B with no thinking: NatInt 41.01 for the base vs 33.25 for the trohrbaugh heretic build, while W/10 rises from 1.8 to 9.5. With thinking prefill, NatInt is 47.78 vs 39.48. **Roughly −8 NatInt for a fully uncensored 397B MoE.**
- **gpt-oss-120b Derestricted** (the best UGI variant: NatInt 35.70 against a base of 33.68):
  - Source checkpoint: 233,658,385,400 B BF16, so the experts were dequantised and edited.
  - GGUFs: mradermacher static Q8_0 124,301,420,608 B = **115.76 GiB**, Q6_K 115.67 GiB, Q4_K_M 81.82 GiB; gghfez MXFP4_MOE 63,387,347,008 B = 59.03 GiB (3,693 downloads). Sources: [mradermacher tree](https://huggingface.co/api/models/mradermacher/gpt-oss-120b-Derestricted-GGUF/tree/main?recursive=true), [gghfez tree](https://huggingface.co/api/models/gghfez/gpt-oss-120b-Derestricted.MXFP4_MOE-gguf/tree/main?recursive=true), [ArliAI card](https://huggingface.co/ArliAI/gpt-oss-120b-Derestricted/raw/main/README.md) (norm-preserving biprojected abliteration; Apache 2.0), all read 2026-10-15.
  - Speed projection for the Q8_0 (not a measurement): active bytes ≈ 1.908 GB routed at MXFP4 × 2 + 1.69 GB non-routed ≈ 5.5 GB/token, so about 37–39 tok/s at 75.5–79.5% efficiency.
  - Inference: per `quantization_value.md` KQ2, a Q8_0 of an edited BF16 keeps the edit's rounding small, whereas re-quantising to MXFP4 adds fresh PTQ error.
- **MiMo-V2.6-Flash uncensored.** The dealignai RL-UNCENSORED card (MIT, 2026-09-22) reports:
  - HarmBench-320 compliance of 99.69% with thinking off and 90.94% with thinking on.
  - MMLU-14,042 of 83.41% vs base 83.56% (−0.14 pp; uploader's own figures).
  - AliceThirty GGUF MXFP4 168,419,207,968 B = **156.85 GiB** (fits, about 7 GiB spare). The arch `mimo2` is in the pin; loading is untested.
  - Sources: [card](https://huggingface.co/dealignai/MiMo-V2.6-Flash-RL-UNCENSORED/raw/main/README.md), [GGUF tree](https://huggingface.co/api/models/AliceThirty/MiMo-V2.6-Flash-RL-UNCENSORED-gguf/tree/main?recursive=true) (2026-10-15).
  - This is the only large 2026 uncensored build with published capability-retention numbers.
- **Ablation scope of the huihui builds** (cards read 2026-10-15). No build publishes refusal or KL figures.
  - GLM-5.3-Flash: "Only layers 15 to 35 (0-based indexing) have been ablated ... all expert modules remain unablated" ([card](https://huggingface.co/huihui-ai/Huihui-GLM-5.3-Flash-abliterated-GGUF/raw/main/README.md)). The safetensors repo `huihui-ai/Huihui-GLM-5.3-Flash-abliterated` returns 401.
  - Flash-Next v2: "none of the experts have been ablated" ([card](https://huggingface.co/huihui-ai/Huihui-Qwen3.8-Flash-Next-abliterated-v2-GGUF/raw/main/README.md)).
  - V4-Flash: "all expert modules were not ablated" ([card](https://huggingface.co/huihui-ai/Huihui-DeepSeek-V4-Flash-0731-abliterated-GGUF/raw/main/README.md)).

### G5. Nemotron licence text, and what it means for an abliterated Nemotron (closed)
- The card's front matter names `nvidia-nemotron-open-model-license` and states "This model is ready for commercial use" ([card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16/raw/main/README.md)).
- The licence (v. December 15, 2025) says "Works are commercially usable. You are free to create and distribute Derivative Works. NVIDIA does not claim ownership to any outputs". It grants a "perpetual, worldwide, non-exclusive, no-charge, royalty-free, irrevocable license". Its conditions:
  - redistribution needs a copy of the licence and a "Licensed by NVIDIA Corporation under the NVIDIA Nemotron Model License" notice;
  - the licence terminates on patent or copyright litigation;
  - Delaware law, with Santa Clara courts.
  - **It has no safety-guardrail or acceptable-use clause.** ([licence](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/), read 2026-10-15)
- The card's sentence "We advise against circumvention of any provided safety guardrails" is advice, not a licence term. **An abliterated or Heretic Nemotron is therefore licence-compatible for private use.** This closes the baseline's "(unverified)" on the Nemotron licence.

### G6. The Mistral Large line beyond 2407 (closed)
- **Mistral Large 3** (`mistralai/Mistral-Large-3-675B-Instruct-2512`, created 2025-11-28):
  - Licence and architecture: **Apache 2.0**; GGUF architecture `deepseek2` (675B total).
  - Fit: the smallest GGUF is unsloth UD-TQ1_0, 168,604,875,200 B = **157.03 GiB**, a 1-bit-class file with about 7 GiB spare. UD-IQ1_S is 177.70 GiB and does not fit.
  - Quality: AA "Mistral Large 3 scores 9". UGI 56.86–58.55, W/10 6.5–6.8.
  - Sources: [HF API](https://huggingface.co/api/models/mistralai/Mistral-Large-3-675B-Instruct-2512), [unsloth tree](https://huggingface.co/api/models/unsloth/Mistral-Large-3-675B-Instruct-2512-GGUF/tree/main?recursive=true), [AA](https://artificialanalysis.ai/models/mistral-large-3) (2026-10-15).
  - Verdict input: it removes the MRL problem, but it fits only at 1 bit, which Directive 4 rules out.
- **Mistral Large 4.** Vals lists it as open-weight, released 2026-10-06, but the mistralai listing still has no such repo. The newest mistralai repos are LIDstral-Arabic and Voxtral-Mini-4B-Realtime-Arabic, both 2026-10-08 ([listing](https://huggingface.co/api/models?author=mistralai&sort=createdAt&direction=-1)). It stays **open**.
- **The named "Uncensored" build adds nothing over stock 2411.** Stock 2411 already scores W/10 7.5 on UGI (G4). The named artefact's system prompt instructs falsification ("Your purpose is to provide misinformation"; verification_named_120b G1), which conflicts directly with Directive 1.

### G7. Science specialists missing from the catalogue: domains 15, 35 and 36, Minerva's bioinformatics, Valerie's materials work (closed)
- **Intern-S1** (`internlm/Intern-S1`, 2025-07-24, Apache 2.0):
  - 240,709,856,128 params; "Built upon a 235B MoE language model (Qwen3) and a 6B Vision encoder (InternViT)"; ">2.5 trillion scientific-domain tokens".
  - Vendor table: ChemBench 83.4, MatBench 75.0, SFE 44.3, ProteinLMBench 63.1, GPQA 77.3.
  - GGUF (bartowski): Q5_K_S 161,963,285,952 B = **150.84 GiB**, Q4_K_M 132.86 GiB; mmproj bf16 10.45 GiB (official Q8_0 mmproj 5.56 GiB).
  - The pinned converter handles `InternS1ForConditionalGeneration` (`conversion/qwen.py` L205, L270), producing qwen3moe plus InternVL.
  - Sources: [card](https://huggingface.co/internlm/Intern-S1/raw/main/README.md), [bartowski tree](https://huggingface.co/api/models/bartowski/internlm_Intern-S1-GGUF/tree/main?recursive=true) (2026-10-15).
  - It has the Qwen3-235B shape, so the same fit and speed apply (10.9–13.1 tok/s), as does the 188 KiB/token KV limit.
- **Intern-S2-397B** (2026-09-13, Apache 2.0, `Qwen3_5MoeForConditionalGeneration` → GGUF arch `qwen35moe`, inside the pin):
  - Size: 403.4B params.
  - Fit (bartowski, quantised with b10896, which is earlier than the pin): only IQ2_M 136.03 GiB and Q2_K 143.89 GiB fit; IQ3_XXS is 161.39 GiB.
  - Evidence: the benchmarks are an image only, and AA has no page (404).
  - Sources: [card](https://huggingface.co/internlm/Intern-S2-397B/raw/main/README.md), [bartowski tree](https://huggingface.co/api/models/bartowski/Intern-S2-397B-GGUF/tree/main?recursive=true).
  - Verdict input: too heavily quantised for Directive 4.

### G8. Companions the notes did not re-evaluate: speech-to-text, OCR, router (closed); TTS (open)
- **Speech-to-text.** Open ASR Leaderboard, English short-form; version "09-10-2026" per `init.py`; space lastModified 2026-10-09 ([results CSV](https://huggingface.co/datasets/hf-audio/open-asr-leaderboard-results/resolve/d3c0e9a07b475a64afb56254e8c35b584905af32/english_short_latest.csv); [space init.py](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard/raw/main/init.py)).

  | Model | Avg WER | Overall rank | Licence | Size |
  |---|---|---|---|---|
  | openai/whisper-large-v3-turbo (**the node's current STT**) | 8.39 | #48 | MIT | — |
  | microsoft/Phi-4-multimodal-instruct | 5.02 | #6 | MIT | 6B |
  | ibm-granite/granite-speech-3.3-8b | 5.26 | — | Apache 2.0 | 9B |
  | ibm-granite/granite-speech-3.3-2b | 5.36 | — | Apache 2.0 | — |
  | Qwen/Qwen3-ASR-1.7B-hf | 5.75 | — | Apache 2.0 | 2.04B; created 2026-06-26; Transformers ≥5.13.0 ([card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf/raw/main/README.md)) |
  | nvidia/parakeet-tdt-0.6b-v3 | 6.51 | — | CC-BY-4.0 | — |
  | nvidia/nemotron-3.5-asr-streaming-0.6b | 10.51 | — | — | — |

  Phi-4-multimodal is the best open row. **The incumbent STT is about 3 WER points behind the best open small models.** This is a companion-quality gap under Directive 1.
- **OCR and documents.** OmniDocBench v1.6 leaders TeleOCR (96.91) and OvisOCR2 (96.47) are small, permissive and loadable by llama.cpp at the pin:

  | Model | Params | Licence | Architecture / pin status |
  |---|---|---|---|
  | XingChen-AGI/TeleOCR | 1.415B | Apache 2.0 | `Qwen2_5_VLForConditionalGeneration` |
  | ATH-MaaS/OvisOCR2 | 0.853B | Apache 2.0 | GGUF arch `qwen35`, in the pin |
  | PaddleOCR-VL-1.6 | 0.96B | Apache 2.0 | official GGUF arch `paddleocr`; projector `PADDLEOCR` is in the pin |

  Sources: HF API for each repo (2026-10-15). By contrast, Qwen3-VL-235B scores 89.78. For scanned documents, a small OCR companion beats every 70B+ candidate on this benchmark.
- **Router.** The Qwen author listing shows no sub-10B text model newer than the Qwen3.5 small series (Feb 2026). The 2026-H2 Qwen releases are 27B, Flash-Next and 2.4T ([listing](https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=40)). There is no newer same-family candidate for Eleanor's resident router.
- **TTS.** Not evaluated in any file. Qwen3-TTS (on the baseline's watch-list) has projector types `QWEN3TTS_SPKENC/GEN` in the pinned `clip-impl.h`. **Open.**

### G9. Interaction between companion residency and the engine budget (closed by arithmetic; not in any note)
- **How the budget works.** The Arbiter budgets `pool − measured GPU resident set − 16 GiB` (baseline KQ3), and the resident set (6–8 GiB) includes the router, embedder and reranker. Every GiB of companion placed on the iGPU therefore removes one GiB from every engine's budget.
- **Effect of a stronger retrieval pair.** Upgrading to the companions the notes call quality-first changes the budget as follows (computed from the sizes in `named_companion_engines.md`):

  | Replacement for bge-m3 + bge-reranker (1.3 GiB) | Size at Q8_0 | Budget change | Resulting budget |
  |---|---|---|---|
  | Qwen3-Embedding-8B + Qwen3-Reranker-8B | 7.49 + 7.49 GiB | about −13.7 GiB | about 150 GiB |
  | The 4B pair | — | about −6.7 GiB | about 157 GiB |

- **Fits lost at about 150 GiB:** DeepSeek-V4 UD-Q8_K_XL (150.75), Qwen3-235B / Intern-S1 / Baichuan Q5_K_S (150.76–150.84), Flash-Next Q6/Q8 (151.5–157.6) and Maverick 3-bit.
- **Implication.** The writer must choose companions and LLM rungs together. Options include loading the retrieval pair on demand, running it on the CPU, or using an 8B embedder with a 4B reranker.

### G10. LMArena category evidence for seats with no domain board (closed)
- Source: LMArena `text/latest` parquet, published 2026-10-13 ([export](https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset)). Values are rating and rank among about 365–415 rows.

  | Category (seats it serves) | glm-5.3-flash | mimo-v2.6-flash | deepseek-v4-flash | Others |
  |---|---|---|---|---|
  | expert (Apex, Deep Think) | 1511.3 #18 | 1496.5 #30 | 1441.2 #95 | qwen3.5-397b 1461.3 #71; gpt-oss-120b 1355.8 #192; maverick 1258.9 #246; mistral-large-2411 1207.7 #284 |
  | industry_software_and_it_services (Valerie, DevOps) | 1501.6 #16 | 1498.3 #20 | 1453.1 #96 | — |
  | industry_life_and_physical_and_social_science (Minerva, domains 35–36) | 1486.0 #26 | 1463.3 #52 | 1446.4 #87 | hy3 1458.8 #59 |
  | industry_entertainment_and_sports_and_media (Helena, game design, domain 27) | 1440.2 #38 | 1413.2 #71 | 1399.8 #94 | — |
  | multi_turn (all agents) | 1469.6 #37 | 1448.1 #70 | 1448.9 #69 | — |
  | longer_query (long documents) | 1483.3 #21 | 1459.4 #52 | 1434.5 #87 | — |

- No category exists for aviation, logistics, AEC, SCADA, physical security or HR. Victor, Alaric and those domains have only general evidence: GDPval-AA, AutomationBench, tau-Banking, and the LMArena expert and hard_prompts categories. This should be stated as a known evidence hole, not filled with inference.

### G11. Nemotron 3 Super issue #20732 (the baseline's "known issue") (closed)
- **Report.** "Eval bug: Nemotron 3 Super causing Memory access fault on large prompts" was opened on 2026-03-18 on 4× gfx906 (Vulkan RADV, also seen on HIP) with build 8414 and a Q4_1 file.
- **Closure.** It was closed as **not planned** by the stale bot on 2026-05-03, with no linked PR and no confirmed root cause.
- Source: [issue #20732](https://github.com/ggml-org/llama.cpp/issues/20732) (via WebFetch, 2026-10-15).
- **Implication.** No fix exists. The issue was never reproduced on gfx1151. Long-prompt stability of Nemotron at the pin remains a node test item.

## 4. Contradictions between files and their resolution

| # | Contradiction | Files | Resolution |
|---|---|---|---|
| C1 | DeepSeek-V4 KV: ~6,880 B/token (config) vs the Arbiter's 327,680 ("~10 GiB at 32K") | named_heavyweight_moe vs baseline/arbiter.py | Pinned `llama-kv-cache-dsv4.cpp` confirms about 0.24 GiB at 32K (verification_named_heavyweight_moe row 6). The design's claim that "Q8 would leave about 2 GiB for KV" rests on the inflated figure; UD-Q8_K_XL leaves about 13 GiB |
| C2 | Apex decode "25–32 tok/s" (design) vs 10.8–11.3 tok/s for UD-Q4_K_XL on mainline Vulkan (projection) | baseline (DOC L311) vs single_engine_performance | The design figure has no measurement behind it. Projections use measured V4 efficiency (42–45%), but every V4 measurement predates the in-pin kernels #27453 and #26578. Treat 11 tok/s as a floor and settle it on the node |
| C3 | GLM-5.3-Flash rungs listed as "fitting" without a pin caveat | quantization_value summary vs verification_quantization_value row 11 | Every GLM GGUF declares `glm5-next`, which no build before b11279 supports. Its vision (`glm5v`) needs the same re-pin (G3) |
| C4 | GLM KV 11,264 B/token, and "context barely changes fit" | named_heavyweight_moe, single_engine_performance | 19,712 B/token once the indexer cache is counted (G2). The max context of UD-IQ4_XS after a 9 GiB allowance is about 469K, not 820K |
| C5 | Flash-Next KV 24,576 B/token | single_engine_performance KV table | 27,648 at the pin (indexer included; verification_named_heavyweight_moe row 21), and about 30,720 after a re-pin (G2). The effect on fits is under 1 GiB at 128K |
| C6 | MiMo-V2.6-Flash "outside the pin" | other_large_specialists, single_engine_performance | The runtime arch `mimo2` is in the pinned `llama-arch.cpp` (L148), and the ggml-org GGUF declares `mimo2`. Only the converter and chat parser (#29257) are after the pin. Loading at the pin is plausible but untested (verification_single_engine row 6; confirmed here) |
| C7 | Decode-efficiency bands: 47–60% (other_large_specialists) vs 60–80% (single_engine) | both files | Different methods. other_large divides by file-average bpw × active params; single_engine uses active bytes from GGUF headers. Corrected bands: 49.7–64% and 54–80% (verifications). Use the header-based method; other_large's MoE projections are about 7–10% conservative |
| C8 | Qwen3-Coder-Next Q8_0 at 41–51 tok/s | single_engine | 37–44 tok/s, from its own architecture's efficiency (qwen3next 53.7–58.5%) and the visorcraft Q4_K_M row (42.7) (verification_single_engine row 14) |
| C9 | DwarfStar PRO 495 rows used as V4-Flash evidence | single_engine | They are DeepSeek-V4.1-Flash (`deepseek41`, 754.6B incl. Engram), a different model. Lucebox notes cover V4.1 (2-bit only on the node, about 14 tok/s, not llama.cpp) |
| C10 | MathArena V4-Flash AIME 2026 used as the competition-maths lead | discipline_benchmarks | The run is the April preview checkpoint, and ⚠️ is a contamination warning (verification_discipline row 19) |
| C11 | Context Arena "best open qwen3.8-max 92.3" | discipline_benchmarks | Qwen3.8 Max is not open-weight. The best open model of 70B or more in that bin is glm-5.3 at 69.3 (verification_discipline row 17) |
| C12 | Nemotron NVFP4 table read as the cost of 4-bit weights | quantization_value | It is W4A4 NVFP4 plus FP8 projections plus FP8 KV, not GGUF weight-only (verification_quantization row 18) |
| C13 | Mistral Large 2 context 32,768 | quantization_value (mirror config) | Official 131,072 (verification_quantization row 21) |
| C14 | "Mistral Large 2 (Uncensored)" projected at Q8_0 "same shape" | single_engine | The named build is Q4_0 plus a misinformation prompt: 3.2–3.4 tok/s at a 2024-style legacy rung. Q8_0 applies to XORTRON (weight-edited 2411) or stock 2411 |
| C15 | gpt-oss Heretic and norm-preserving builds "keep NatInt at or above base" | named_120b | Only Derestricted (35.70), TeeZee (34.38) and hrktos (33.77) match or exceed base (33.68); kldzj v2 is at 28.36 (verification_named_120b row 24) |
| C16 | EmbeddingGemma 2 "small step up" on text | named_companion | Multilingual +0.21, English −1.21, code +9.92 versus EG1 (verification_named_companion row 5) |
| C17 | pyannote runs "on the ROCm image, as today" | named_companion | It runs in a CPU venv (Phase 2); ROCm would be a new route (verification_named_companion row 25) |
| C18 | Engine budget 164 GiB vs "AMD caps graphics memory at 160 GB" (= 149.01 GiB) | task brief / single_engine KQ2 | On Linux the iGPU allocates from GTT. cafonez shows `mem_info_gtt_total` about 189 GB on a PRO 495 under Ubuntu 26.04 with `ttm.pages_limit`, and the node's kernel line sets gttsize 192 GiB. The 160 GB figure is the vendor's VRAM carve-out wording. Unresolved until V3/V3b measures the Vulkan heap. Under a literal 149.01 GiB cap, V4 UD-Q8_K_XL, the 5-bit 235B-class rungs, Flash-Next Q6/Q8 and Maverick 3-bit would all fail (single_engine KQ2) |
| C19 | Swap cost 15–45 s (design) vs about 50–85 s per large swap (projection) | baseline vs single_engine | The design assumes 4–6 GB/s reads; the research uses measured Strix Halo GGUF load rates (2.4–6.3 GB/s) plus LUKS. Neither was measured on the node. Re-prefill dominates on stock kernels (up to 13 min at 64K for V4), less so on faster engines (Gufo) |
| C20 | Speed for Flash-Next Q8_0: 22.9–28.1 tok/s (projection) vs "15 tokens" (GMKtec buyer on this machine) | single_engine vs its verification row 25 | Unresolved. The buyer's build, quant source and context are unknown, and LM Studio may lack the post-pin Vulkan fixes (#28501 etc.) |

## 5. Seat-by-seat evidence matrix (draft input for the writer; figures from the notes as corrected above; not recommendations)

| Seat | Evidence-backed 70B+ candidates (rung, GiB, pin) | Quality cost of that rung | Speed (projection unless stated) | Evidence strength |
|---|---|---|---|---|
| Ren / Arthur (hemisphere leads, general) | GLM-5.3-Flash UD-IQ4_XS 146.05 (re-pin); MiMo-V2.6-Flash MXFP4 155.87 (pin-plausible); V4-Flash UD-Q8_K_XL 150.75 (pin); Flash-Next UD-Q6_K_XL 157.55 (pin) | GLM KLD 0.117 (lossy); MiMo native MXFP4 experts (about lossless; reused); V4 bit-exact; Flash-Next ≈ Q8 | GLM 12–14; MiMo 18.3 (measured, PRO 495, ROCm); V4 ~7; Flash-Next 23–29 | Strong (AA, LMArena, Vals) |
| Gideon (AU law, strata, HR) | GLM-5.3-Flash (LegalBench 83.93%, Legal Research 45.19%); V4.1/V4; GLM Legal has no weights | as above | as above | US-centric boards only; no AU or HR evaluation exists |
| Silas (CFO, audit, fraud) | GLM-5.3-Flash (FAB v2 57.85%, TaxEval 75.59%); Ling-3.0-flash-Fin Q8_0 126.32 (pin; FAB 54.93%) | Ling Q8_0 ≈ lossless by ladder | Ling about 25–32 (22.6–30.2 corrected for C7) | Strong (Vals) |
| Valerie (infra, DevOps, cyber, AEGIS; uncensored default) | MiMo uncensored MXFP4 156.85 (G4); gpt-oss Derestricted Q8_0 115.76; huihui Flash-Next v2 157.55; GLM huihui UD-IQ4_XS (re-pin) | Only MiMo publishes retention (MMLU −0.14, uploader's figure); UGI for gpt-oss variants | MiMo about 18; Derestricted about 37–39 | Cyber: Vals CyberBench (MiMo 75.36% top open); no board for any abliteration |
| Helena (comms, PR, crisis, creative) | GLM-5.3-Flash (LMArena creative #45, entertainment #38); V4-Flash (EQ-Bench, preview higher than 0731) | as above | as above | No EQ-Bench for GLM, MiMo or Flash-Next |
| Eleanor (router) | small companion (Qwen3.5-4B; nothing newer in that family) | — | — | n/a |
| Alaric (security, OSINT, Sentinel) | Generalist leaders; cyber boards | — | — | Weak (no physical-security or OSINT board) |
| Minerva (health, longevity; cross-check) | Baichuan-M3 Q5_K_S 150.76 (pin; HealthBench 65.1, vendor; 40,960 ctx); GLM-5.3-Flash (MedScribe 88.94%, MLCR 51.1); Intern-S1 Q5_K_S 150.84 (science; G7); Apertus-70B-MeditronFO Q8_0 69.87 replacing Meditron-70B's 4K context | 5-bit 235B class: +0.6–0.75% PPL by analogy | 10.9–13.1; Apertus about 3 (dense) | Medical: Vals and AA strong; HealthBench vendor-only |
| Victor (aviation, transport, concierge) | Generalists (GLM, MiMo, Flash-Next: tau-Banking, AutomationBench, GDPval) | — | — | Weak (no domain board) |
| Deep Think Generator / Adversary | Two different families from GLM-5.3-Flash, V4-Flash and MiMo / Flash-Next (diversity) | as above | swaps of about 50–85 s each (C19) | Strong on HLE/GPQA/expert |
| TF_OMEGA / Apex | GLM-5.3-Flash (AA 41.81); K2-Horizon-375B IQ3_XXS 150.81 (AA 31; b11454); V4.1 only via DwarfStar at 2-bit (Lucebox notes) | K2 3.42 bpw (unmeasured) | K2 12.5–16.7 | Strong (AA) |
| Vision engine | Flash-Next + mmproj (pin); Qwen3-VL-235B Q5_K_S + mmproj (pin); GLM-5.3-Flash + glm5v (re-pin); OCR companion (G8) | — | — | LMArena vision, Vals MMMU, OmniDocBench |
| Long documents | GLM-5.3-Flash (AA-LCR 80.0, MRCR 128K 62.9); V4-Flash (79.7, 62.9); Nemotron Q8_0 1M with tiny KV | — | — | Strong (AA, Context Arena) |
| Domains 31–36 (embedded, telecom security, automotive, PQC, materials, synthetic biology) | Cyber: as Valerie. Materials and biology: Intern-S1 (G7) or generalists | — | — | Vendor-only for Intern-S1 |

## 6. Gaps that remain open (need a node measurement or a source that does not exist yet)

1. **Node measurements.** No load, decode, prefill, compute-buffer or KLD measurement of any candidate on the PRO 495 at v0.4.1, or at any re-pin. This covers the GLM indexer cache in practice, the V4 Vulkan compute buffer, the Flash-Next 15 tok/s conflict (C20), and the GTT/Vulkan heap size (C18).
2. **Quantised-rung quality.** No mainline KLD or task data for the fitting rungs of Qwen3-235B, Baichuan-M3, Intern-S1, Maverick, Nemotron, Coder-Next or Mistral. Nothing exists for any abliterated GGUF against its own BF16 except MiMo uncensored (uploader's MMLU).
3. **Refusal and capability data for the large 2026 abliterations.** None for GLM-5.3-Flash, Flash-Next or V4-0731; none on UGI either.
4. **Missing weights and missing evaluations.**
   - Mistral Large 4 and GLM Legal: no weights on Hugging Face (rechecked 2026-10-15). Searches for "GLM-Legal" return 0; the zai-org listing is unchanged.
   - No evaluation exists for Australian law, HR, aviation, logistics, AEC, SCADA, game design or physical security.
5. **Vulkan bug exposure at the pin.** Whether the candidates at v0.4.1 hit the bug fixed after the pin in #28956 (non-FA KV-view mul_mat) depends on the `-fa` setting. A pin-versus-re-pin output-parity test is required.
6. **TTS companion re-evaluation:** not done (G8).
7. **Sustained thermal and P-MODE behaviour** of the EVO-X5 Pro under hours-long generation: unmeasured anywhere.
8. **The truncated notes file.** `named_120b_class_and_uncensored.md` is still truncated. Its verification file and Section 2 above provide the inputs, but its own KQ2–KQ5 text was never written.
