# A.T.L.A.S. v0.4.0 engine data (collected 2026-10-16)

Data collection for the Day 1 engine roster and the phased engines. Every byte count and sha256 is the Hugging Face tree API value (`lfs.size`, `lfs.oid`) read on 2026-10-16; architecture, context and KV fields come from the GGUF headers read the same day (HTTP range reads, parsed locally) and from the llama.cpp sources at the pin. GB = 10^9 bytes, GiB = 2^30 bytes, rounded to 2 places; bytes and sha256 are exact (HF tree API lfs.size / lfs.oid).

**llama.cpp pin:** b11474 (commit `b9acf138a1e28ce1fc23b5a4fc4b12444b50f7ea`, 2026-10-07, CMake version 0.6.0). The old pin v0.4.1 = b10964 (`b29c606e28a01b1bc8c1351026a0fa6e616bf6c4`) stays for the parity test only.

**KV method.** Bytes per token computed from the GGUF headers read today and the b11474 cache code (src/llama-model.cpp create_memory, llama-kv-cache.cpp, llama-memory-hybrid-idx.cpp, llama-kv-cache-dsv4.cpp, llama-hparams.cpp n_embd_r/n_embd_s). Block sizes: f16 2 B/element; q8_0 34 B per 32; q4_0 18 B per 32. Recurrent/SSM state is f32, per sequence, independent of context. Compute buffers are not included (Phase 3 measures them).

**Exclusion.** No uncensored or abliterated engine was sourced; gpt-oss-120b-abliterated is out of scope and unchanged.

## Findings that change what the register or engines.json assumed

1. Rank head: published Qwen3-Reranker-8B/4B GGUFs WITH the rank head exist (Voodisss, gorse-io, giladgd): qwen3.pooling_type = 4, qwen3.classifier.output_labels [yes,no], tokenizer.chat_template.rerank, cls.output.weight [n_embd,2]. Qwen publishes none; mradermacher (8B and 4B) lacks the head.
2. DSV4 UD-Q8_K_XL (150.75 GiB) does not fit the D-14 (a) 8B companion pair budget (about 150 GiB); the 4B pair (b) or on-demand companions are required for the fixed Apex choice.
3. Register mmproj sizes are low: glm5v mmproj F16 is 1,128,047,296 B (register 0.65 GB); Flash-Next mmproj F16 904,004,000 B (register 0.62 GB).
4. KV figures confirmed against b11474 code: GLM 19,712 B/token f16 (10,472 q8_0); DSV4 6,880 B/token f16 (3,655 q8_0); 235B-class 192,512 B/token f16.
5. Qwen3-Embedding/Reranker are causal qwen3 models: each allocates a KV cache of 147,456 B/token f16 (1.125 GiB per 8,192-token pool), unlike the non-causal bge pair.
6. DSV4 chat template defaults to thinking OFF; pass --reasoning on (enable_thinking) and --reasoning-effort high|max for Apex/Deep Think. GLM defaults to reasoning_effort max.
7. DSV4 card sampling: temperature 1.0 with top_p 1.0 (top_p 0.95 for agentic); Nemotron card now says temperature 1.0 / top_p 0.95 (existing entry 0.6).
8. Flash-Next self-contained MTP head (mtp-Qwen3.8-Flash-Next-Q8_0.gguf) was re-uploaded after mainline #29761 and matches mainline tensor names; shared-* heads need the unsloth fork. Unverified on the node.
9. unsloth GLM repo carries Shard_Rewrite/*.gguf_file duplicates; pull only the UD-IQ4_XS/ folder.
10. Lucebox DSpark drafter uses a non-mainline arch and tensor type; the mainline drafter is unsloth dspark-DeepSeek-V4-Flash-0731-Q8_0.gguf (repo root, b10228+).
11. Old pin v0.4.1 (b10964) cannot load glm5-next (b11279) or gemma-embedding2 (b11452); it loads deepseek4, gpt-oss, qwen35, qwen3 embed/rerank, qwen3moe, nemotron_h_moe, bailingmoe3, qwen3next and qwen4exp (but not its Vulkan hc ops, b11012, or MTP, b11330). The S-02/V25 parity test can therefore cover gpt-oss-120b, DeepSeek-V4-Flash, the router and the Qwen3 embed/rerank pair, not GLM.

## Day 1 engines: summary

| Key | Repo / subdir | Files | Bytes | GB | GiB | Arch | Native ctx | KV B/token f16 / q8_0 / q4_0 | Min build | Licence | Sampling |
|---|---|---:|---:|---:|---:|---|---:|---|---|---|---|
| `glm-5.3-flash` | unsloth/GLM-5.3-Flash-GGUF / UD-IQ4_XS | 5 | 156,822,111,200 | 156.82 | 146.05 | glm5-next | 1,048,576 | 19,712 / 10,472 / 5,544 | b11279 | mit | temperature 1.0, top_p 0.95 |
| `deepseek-v4-flash` | unsloth/DeepSeek-V4-Flash-0731-GGUF / UD-Q8_K_XL | 5 | 161,869,615,520 | 161.87 | 150.75 | deepseek4 | 1,048,576 | 6,880 / 3,655 / 1,935 | b9840 | mit | temperature 1.0, top_p 1.0 |
| `gpt-oss-120b` | unsloth/gpt-oss-120b-GGUF / - | 1 | 65,369,017,728 | 65.37 | 60.88 | gpt-oss | 131,072 | 36,864 / 19,584 / 10,368 | b6096 | apache-2.0 | temperature 1.0, top_p 1.0 |
| `router-qwen3.5-4b` | unsloth/Qwen3.5-4B-GGUF / - | 1 | 4,482,403,488 | 4.48 | 4.17 | qwen35 | 262,144 | 32,768 / 17,408 / 9,216 | b7990 | apache-2.0 | --reasoning off --temp 0.2 (classification; keep) |
| `embed-qwen3-8b` | Qwen/Qwen3-Embedding-8B-GGUF / - | 1 | 8,047,105,824 | 8.05 | 7.49 | qwen3 | 40,960 | 147,456 / 78,336 / 41,472 | b6063 | apache-2.0 | n/a (embedding) |
| `embed-qwen3-4b` | Qwen/Qwen3-Embedding-4B-GGUF / - | 1 | 4,279,660,224 | 4.28 | 3.99 | qwen3 | 40,960 | 147,456 / 78,336 / 41,472 | b6063 | apache-2.0 | n/a (embedding) |
| `rerank-qwen3-8b` | Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp / - | 1 | 8,047,134,304 | 8.05 | 7.49 | qwen3 | 40,960 | 147,456 / 78,336 / 41,472 | b6578 | apache-2.0 | n/a (reranker) |
| `rerank-qwen3-4b` | Voodisss/Qwen3-Reranker-4B-GGUF-llama_cpp / - | 1 | 4,279,678,912 | 4.28 | 3.99 | qwen3 | 40,960 | 147,456 / 78,336 / 41,472 | b6578 | apache-2.0 | n/a (reranker) |

All Day 1 minimum builds are satisfied by b11474. On the old pin b10964 only glm5-next (b11279) fails to load among the Day 1 archs.

## Day 1: `glm-5.3-flash`

- Suggested key: glm-5.3-flash
- Role (fixed choices): Ren, Gideon, Silas, Helena, Minerva, Victor, Alaric; vision engine (glm5v mmproj); long-document route; Deep Think generator; Eleanor persona work (P-10)
- Repo: `unsloth/GLM-5.3-Flash-GGUF`, subdir `UD-IQ4_XS`, quant UD-IQ4_XS; model_file_pattern `GLM-5.3-Flash-UD-IQ4_XS-00001-of-00005.gguf`
- Repo revision `a38483c8cd5df544f53d70fb281afe97369d5ab6`, last modified 2026-10-05T22:23:39.000Z, gated False
- Total: 156,822,111,200 B = 156.82 GB = 146.05 GiB
- Architecture `glm5-next`; native context 1,048,576

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `UD-IQ4_XS/GLM-5.3-Flash-UD-IQ4_XS-00001-of-00005.gguf` | 9,429,984 | `c2f3b6ab98d3df844f322ea376059202d2eaeedcee1f0eff455345b86a137dbd` |
| `UD-IQ4_XS/GLM-5.3-Flash-UD-IQ4_XS-00002-of-00005.gguf` | 49,989,334,176 | `7d64cf0395672c4322012841abec502ea7e20518299bb5e3069003f06f9e6de9` |
| `UD-IQ4_XS/GLM-5.3-Flash-UD-IQ4_XS-00003-of-00005.gguf` | 49,607,025,280 | `7c2c63c9c30f8060428fdf2ac935dbf2ee9ad8f771d62b6ae47a7f7f2c1520e7` |
| `UD-IQ4_XS/GLM-5.3-Flash-UD-IQ4_XS-00004-of-00005.gguf` | 49,486,530,144 | `06c90f191871317c92dd9d25a353b687aed44c50f3ac6c713e9fe410bc2d26dd` |
| `UD-IQ4_XS/GLM-5.3-Flash-UD-IQ4_XS-00005-of-00005.gguf` | 7,729,791,616 | `66ebf9ec85e04d3f44af674ae4f694acbc89cc53db75e42f2f4d3646bf321c0d` |

**mmproj.** Recommended `mmproj-F16.gguf` 1,128,047,296 B (1.05 GiB), sha256 `922b809664d6a89cc1e40bfeaa63618000edc6651baf4082ce0718a784adf947`; alternative `mmproj-BF16.gguf` 1,164,010,176 B, sha256 `82b7adb44411dbb298b1c689567804e5ed0790d80f308ed8b5b6b8ac0527d2ac`. F16 is the conservative choice on Vulkan (BF16 shader support is device/driver dependent); both carry clip.projector_type = glm5v, 564M parameters, 348 tensors.

**KV.** llama_memory_hybrid_idx (src/llama-model.cpp L2468-L2511 at b11474): 34 KDA layers recurrent (f32 state), 11 DSA layers [3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43] with a K-only MLA cache (512 per token) plus an indexer cache (128 x kpool_row 3 = 384 per token, K-only, type_k). The NextN layer 45 is excluded from the main context (filter il < n_layer()). Bytes per token: f16 19,712, q8_0 10,472, q4_0 5,544 (11 x (row(512) + row(384)); f16 = 11 x 896 x 2).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 32,768 | 0.602 | 0.32 | 0.169 |
| 131,072 | 2.406 | 1.278 | 0.677 |
| 262,144 | 4.812 | 2.557 | 1.354 |
| 524,288 | 9.625 | 5.113 | 2.707 |
| 1,048,576 | 19.25 | 10.227 | 5.414 |

Recurrent state: 152,633,344 B (145.6 MiB) per sequence (34 recurrent layers x (r 73728 + s 1048576) f32 elements x 4 B).
Register check: N-03 / D-29 quote 19,712 B/token f16: confirmed by the b11474 code path.

**Sampling.** {"temperature": 1.0, "top_p": 0.95}. Source: zai-org/GLM-5.3-Flash generation_config.json (temperature 1.0, top_p 0.95) and GGUF general.sampling.*; card evaluations use temp 1.0 with top_p 0.95 or 1.0.
Chat template: reasoning_effort in ['low','high'] else 'max' (default max, rendered as a 'Reasoning Effort:' system line); clear_thinking kwarg (default false). At b11474 --reasoning-effort LEVEL sets the template kwarg (common/arg.cpp L3718).

**llama.cpp.** Minimum b11279 (satisfied by b11474: True; loads on old pin b10964: False). glm5-next arch (#27773, 649dcb103, first tag b11279). GLM MTP (#29928) is the pin commit itself (b11474). Later glm5 fixes in the pin: #29745 (b11302), #30042 gather path removed (b11453). Vulkan supports the DSV4_HC_PRE/POST/COMB ops GLM uses for hc=4 with F32 inputs (ggml-vulkan.cpp supports_op at b11474); GATED_DELTA_NET (KDA) has a Vulkan path.
MTP: Embedded NextN block (nextn_predict_layers 1); speculative use via --spec-type draft-mtp is a phased item (D-61).

**Licence.** mit (zai-org/GLM-5.3-Flash card front matter license: mit; GGUF general.license = mit); register D-35 (a) accepted.

**Speed (research, not measured on the node).** {"projected_decode_tok_s": [12.5, 14.0], "measured_reference": "17.4 tok/s Vulkan at 3.84 bpw on a PRO 495 (not this file)", "source": "research_notes/ATLAS specialist LLM arsenal/single_engine_performance.md L12"}

**Caveats.**

- The repo also carries Shard_Rewrite/*.gguf_file copies (same sha256 as the live first shards); do not pull them: select the UD-IQ4_XS/ folder only.
- Register D-20/S-10 quote the glm5v mmproj as 0.65 GB; the published files are 1,128,047,296 B (F16) and 1,164,010,176 B (BF16). Use the measured size.
- D-48: multi-slot GLM may need --kv-unified (claim from #27773 review); no assertion enforcing it was found in src at b11474. T-04 settles it.
- A separate vision engines.json key on the same weights would, under layout_rule (models/<key>/), download the 156.82 GB set twice unless the pull shares the directory.

## Day 1: `deepseek-v4-flash`

- Suggested key: deepseek-v4-flash (keep the existing key; change subdir, files, quant, footprint)
- Role (fixed choices): Arthur; Apex / TF_OMEGA engine; Deep Think pair adversary
- Repo: `unsloth/DeepSeek-V4-Flash-0731-GGUF`, subdir `UD-Q8_K_XL`, quant UD-Q8_K_XL; model_file_pattern `DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00001-of-00005.gguf`
- Repo revision `fbbb5b93fb787c21338159b0af3318bb3f4d9768`, last modified 2026-08-06T14:54:45.000Z, gated False
- Total: 161,869,615,520 B = 161.87 GB = 150.75 GiB
- Architecture `deepseek4`; native context 1,048,576

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `UD-Q8_K_XL/DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00001-of-00005.gguf` | 5,257,408 | `d13ce8f90855547bdaebe7312f531a1f2c4f822178d3103951f27fe884395cfa` |
| `UD-Q8_K_XL/DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00002-of-00005.gguf` | 49,215,492,960 | `3da2f2443063f83635986f9b67fa7e8e3d03c53b81a9a08d2007936612423610` |
| `UD-Q8_K_XL/DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00003-of-00005.gguf` | 49,700,372,160 | `7d622a7760d359ec9257b3493ad531e3bf0bfbe6f6533267e16e6dde8153ddce` |
| `UD-Q8_K_XL/DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00004-of-00005.gguf` | 49,466,495,968 | `6ed2bce452214f156b85e7c5f7d4fc242a3052f409d1b90a61422f60669c2de3` |
| `UD-Q8_K_XL/DeepSeek-V4-Flash-0731-UD-Q8_K_XL-00005-of-00005.gguf` | 13,481,997,024 | `ea4727af4888fdca0fff796ec81ac2f3ebb43c310b2feb4798f41d82744b42ea` |

**KV.** llama_kv_cache_dsv4 (src/llama-kv-cache-dsv4.cpp L1213-L1333): every layer is SWA (window 128) with a K-only raw cache; 21 CSA layers (ratio 4) [2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38, 40, 42] keep a compressed K (512 per 4 tokens) plus a lightning-indexer K (128 per 4 tokens); 20 HCA layers (ratio 128) keep 512 per 128 tokens; layers [0, 1] are window-only. All caches use type_k. Bytes per token: f16 6,880.0, q8_0 3,655.0, q4_0 1,935.0 (21 x (row(512)/4 + row(128)/4) + 20 x row(512)/128; f16 = 21 x 320 + 20 x 8).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 32,768 | 0.21 | 0.112 | 0.059 |
| 131,072 | 0.84 | 0.446 | 0.236 |
| 262,144 | 1.68 | 0.892 | 0.472 |
| 524,288 | 3.359 | 1.785 | 0.945 |
| 1,048,576 | 6.719 | 3.569 | 1.89 |

Fixed per sequence: {"raw_swa_k_cache_f16_bytes": 33816576, "raw_swa_cells": 768, "raw_q8_0_bytes": 17965056, "raw_note": "43 layers x 512 x 2 B x 768 cells per stream (GGML_PAD(128 + n_ubatch 512, 256)); scales with -ub and type_k", "compressor_state_f32_bytes": 12206080, "compressor_note": "csa/hca/lid states: kv + score f32 tensors [n_embd_state, state_size, n_stream x (1 + n_rs_seq)] (llama-kv-cache-dsv4.cpp L968-L969); estimate per stream with n_rs_seq 0"}
Register check: N-01 / D-29 quote about 6,880 B/token: confirmed (f16 exactly 6,880).
KV policy: Approved D-29 (a) / S-42: highest-precision coherent rung that fits (f16, then q8_0, never q4_0).

**Sampling.** {"temperature": 1.0, "top_p": 1.0, "top_p_agentic": 0.95}. Source: deepseek-ai/DeepSeek-V4-Flash-0731 README "How to Run Locally": temperature 1.0, top_p 0.95 for agentic scenarios and 1.0 otherwise; max output 384K for high/max effort; generation_config.json temperature 1.0, top_p 1.0.
Chat template: unsloth template: thinking = thinking or enable_thinking kwarg, default FALSE; reasoning_effort none|high|max (high/max prepend long effort prompts). At b11474 --reasoning on sets enable_thinking=true and --reasoning-effort high|max sets reasoning_effort.

**llama.cpp.** Minimum b9840 (satisfied by b11474: True; loads on old pin b10964: True). deepseek4 arch (#24162, first tag b9840); quantised-KV fix #25202 (b9905); vision input #28154 (b10766); Vulkan DSV4 HC fused ops #26578 (b10844). --parallel must be explicit (#26654, existing known_issue).

**Licence.** mit (deepseek-ai/DeepSeek-V4-Flash-0731 card license: mit (LICENSE); GGUF general.license = mit); register D-34 accepted.

**Speed (research, not measured on the node).** {"projected_decode_tok_s": 7, "note": "about 7 tok/s UD-Q8_K_XL on mainline Vulkan (projection; T-06 measures)", "source": "research_notes/ATLAS specialist LLM arsenal/single_engine_performance.md L12; register T-06"}

**DSpark drafter (phased).** `dspark-DeepSeek-V4-Flash-0731-Q8_0.gguf` 10,896,057,440 B, sha256 `2c7ac54b0b64a99df1f139a9f1371a00198265e1d6a614b77597d20a655a4249`; BF16 `dspark/dspark-DeepSeek-V4-Flash-0731-BF16.gguf` 11,314,832,480 B, sha256 `df7a182b1f62acee6e9e33f11a03876db8031d9d147419309a448d0e776f82d8`. dflash (mainline LLM_ARCH_DFLASH; 81 tensors, block_count 3, dflash.block_size 5, target_layers [41,42,43]). Minimum b10228 (DSV4 DSpark #25784); avoid b10259-b10268 (unsloth dspark/README). Usage: -md <file> --spec-type draft-dspark --spec-draft-n-max <=5; --fit off recommended; drafter borrows target embeddings/output (do not pin -devd). Root-level file; the UD-Q8_K_XL/ subdir pull does not include it. Lucebox variant `Lucebox/DeepSeek-V4-Flash-0731-DSpark-GGUF/DeepSeek-V4-Flash-0731-DSpark-draft-Q4RMFP4-denseF16.gguf` (10,648,656,160 B): general.architecture = deepseek4-dflash-draft and tensor type id 101 (Lucebox ROCm FP4 type): not a mainline arch/type.

**Caveats.**

- Bit-exact to the official weights per N-01 (UD-Q8_K_XL: BF16 + MXFP4 experts; tensor types BF16/F32/MXFP4/I32 in shard 2).
- Fit: 150.75 GiB of weights. At the D-14 (a) 8B companion pair the budget is about 150 GiB, so this file does not fit; it needs the 4B pair (about 157 GiB) or on-demand/CPU companions (see residency_arithmetic).

## Day 1: `gpt-oss-120b`

- Suggested key: gpt-oss-120b (keep the key; repo and file change per D-59 (b))
- Role (fixed choices): Fast utility, drafting, Sentinel escalation; fallback floor for every seat (D-40 (a)) where the runner-up is phased
- Repo: `unsloth/gpt-oss-120b-GGUF`, subdir `None`, quant F16 trunk + native MXFP4 experts (general.file_type 1); model_file_pattern `gpt-oss-120b-F16.gguf`
- Repo revision `ff1a82da6ad466e32284fa3d2b86694db3204789`, last modified 2025-08-25T02:27:41.000Z, gated False
- Total: 65,369,017,728 B = 65.37 GB = 60.88 GiB
- Architecture `gpt-oss`; native context 131,072

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `gpt-oss-120b-F16.gguf` | 65,369,017,728 | `2d1f0298ae4b6c874d5a468598c5ce17c1763b3fea99de10b1a07df93cef014f` |

**KV.** iSWA (models/openai-moe.cpp load_swa_pattern(ml, 2)): 18 SWA layers (window 128, fixed size) and 18 full-attention layers; 8 KV heads x 64; attention sinks. Bytes per token: f16 36,864, q8_0 19,584, q4_0 10,368 (18 full layers x (K 512 + V 512)).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 32,768 | 1.125 | 0.598 | 0.316 |
| 131,072 | 4.5 | 2.391 | 1.266 |
| 262,144 | 9.0 | 4.781 | 2.531 |
| 524,288 | 18.0 | 9.562 | 5.062 |
| 1,048,576 | 36.0 | 19.125 | 10.125 |

Fixed per sequence: SWA layers: 18 x 1024 x bytes x GGML_PAD(128 + n_ubatch, 256) cells per stream (f16, -ub 2048: 2,304 cells = 81 MiB)

**Sampling.** {"temperature": 1.0, "top_p": 1.0}. Source: github.com/openai/gpt-oss README L576 "We recommend sampling with temperature=1.0 and top_p=1.0".
Chat template: reasoning_effort default "medium"; existing entry sets {"reasoning_effort":"high"} via LLAMA_ARG_CHAT_TEMPLATE_KWARGS (at b11474 --reasoning-effort high is equivalent)

**llama.cpp.** Minimum b6096 (satisfied by b11474: True; loads on old pin b10964: True). gpt-oss arch (#15091, first tag b6096)

**Licence.** apache-2.0 (openai/gpt-oss-120b card; GGUF general.license = apache-2.0).

**Speed (research, not measured on the node).** {"projected_decode_tok_s": [57, 60], "measured_reference": "53.8-55.6 tok/s on 128 GB Ryzen AI Max+ 395 boxes (MXFP4 file)", "source": "register N-09"}

**Caveats.**

- The register gives F16-trunk 65.37 GB / 60.88 GiB: confirmed (65,369,017,728 B).
- Replaces ggml-org/gpt-oss-120b-GGUF MXFP4 (3 shards). The gpt-oss-120b-abliterated entry is out of scope and stays as in v0.3.5.

## Day 1: `router-qwen3.5-4b`

- Suggested key: router-qwen3.5-4b (unchanged; P-10: classification only)
- Role (fixed choices): Always-resident router / classifier; Eleanor uses it for classification only (P-10)
- Repo: `unsloth/Qwen3.5-4B-GGUF`, subdir `None`, quant Q8_0; model_file_pattern `Qwen3.5-4B-Q8_0.gguf`
- Repo revision `e87f176479d0855a907a41277aca2f8ee7a09523`, last modified 2026-03-02T14:08:17.000Z, gated False
- Total: 4,482,403,488 B = 4.48 GB = 4.17 GiB
- Architecture `qwen35`; native context 262,144

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Qwen3.5-4B-Q8_0.gguf` | 4,482,403,488 | `10cc391b403021dd11c614679d2fd92f611c3681d29e29651b717316965d61e1` |

**KV.** 8 full-attention layers (every 4th) + 24 Gated DeltaNet layers Bytes per token: f16 32,768, q8_0 17,408, q4_0 9,216 (8 x (K 4x256 + V 4x256)).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 32,768 | 1.0 | 0.531 | 0.281 |
| 131,072 | 4.0 | 2.125 | 1.125 |
| 262,144 | 8.0 | 4.25 | 2.25 |
| 524,288 | 16.0 | 8.5 | 4.5 |
| 1,048,576 | 32.0 | 17.0 | 9.0 |

Recurrent state: 52,690,944 B (50.2 MiB) per sequence (24 recurrent layers x (r 24576 + s 524288) f32 elements x 4 B).

**Sampling.** {"non_thinking_general": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}, "existing_entry": "--reasoning off --temp 0.2 (classification; keep)"}. Source: Qwen/Qwen3.5-4B card L835-L838.

**llama.cpp.** Minimum b7990 (satisfied by b11474: True; loads on old pin b10964: True). qwen35 (#19468, first tag b7990)

**Licence.** apache-2.0 (Qwen/Qwen3.5-4B card; GGUF general.license).

**Caveats.**

- Existing entry has sha256 null; tree API gives the hash below. The mmproj files in the repo are not needed (text router).

## Day 1: `embed-qwen3-8b`

- Suggested key: embed-qwen3-8b
- Role (fixed choices): Primary embedder replacing bge-m3 before Day 1 (D-45 (a)); the 8B/4B choice follows D-14
- Repo: `Qwen/Qwen3-Embedding-8B-GGUF`, subdir `None`, quant Q8_0; model_file_pattern `Qwen3-Embedding-8B-Q8_0.gguf`
- Repo revision `69d0e58a13e463cd99a9b83e3f5fee7c10265fab`, last modified 2025-07-15T02:30:41.000Z, gated False
- Total: 8,047,105,824 B = 8.05 GB = 7.49 GiB
- Architecture `qwen3`; native context 40,960

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Qwen3-Embedding-8B-Q8_0.gguf` | 8,047,105,824 | `d20ddc71e8a5c4344f2343481e242233a997dc5eaff442427a945836c97b4deb` |

**f16 alternative.** `Qwen3-Embedding-8B-f16.gguf` 15,141,156,384 B (14.1 GiB), sha256 `9a2dfcc2e867828909456dd52a69e3775b677bdce1816f7cc55f3657393e7e53`.

**KV.** causal qwen3: a normal KV cache is allocated even in --embedding mode Bytes per token: f16 147,456, q8_0 78,336, q4_0 41,472 (36 x (K 8x128 + V 8x128)).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 8,192 | 1.125 | 0.598 | 0.316 |
| 16,384 | 2.25 | 1.195 | 0.633 |
| 32,768 | 4.5 | 2.391 | 1.266 |


**Server flags.** --embedding --pooling last -ub 8192 (card); pooling_type 3 is baked in, so --pooling may be omitted; /v1/embeddings

**llama.cpp.** Minimum b6063 (satisfied by b11474: True; loads on old pin b10964: True). Qwen3-Embedding support (#15023, first tag b6063)

**Licence.** apache-2.0 (Qwen/Qwen3-Embedding-8B card; GGUF general.license).

**Caveats.**

- Instruction-aware: queries take an "Instruct: ...\nQuery: ..." prefix per the Qwen card; documents none.
- Matryoshka: output dimension can be truncated (32-4096 for 8B, 32-2560 for 4B) but vectors are not interchangeable with bge-m3 (D-45).

## Day 1: `embed-qwen3-4b`

- Suggested key: embed-qwen3-4b
- Role (fixed choices): Primary embedder replacing bge-m3 before Day 1 (D-45 (a)); the 8B/4B choice follows D-14
- Repo: `Qwen/Qwen3-Embedding-4B-GGUF`, subdir `None`, quant Q8_0; model_file_pattern `Qwen3-Embedding-4B-Q8_0.gguf`
- Repo revision `f4602530db1d980e16da9d7d3a70294cf5c190be`, last modified 2025-08-03T14:37:22.000Z, gated False
- Total: 4,279,660,224 B = 4.28 GB = 3.99 GiB
- Architecture `qwen3`; native context 40,960

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Qwen3-Embedding-4B-Q8_0.gguf` | 4,279,660,224 | `b60ae5ce2dd6a0b77f82cadf21def1f310a3e10cde380ad0081b07a9d416949d` |

**f16 alternative.** `Qwen3-Embedding-4B-f16.gguf` 8,049,889,824 B (7.5 GiB), sha256 `e8b4e85c8fcc26079d27418cf8d6a16df1a09890cba0966324a97280f91e782c`.

**KV.** causal qwen3: a normal KV cache is allocated even in --embedding mode Bytes per token: f16 147,456, q8_0 78,336, q4_0 41,472 (36 x (K 8x128 + V 8x128)).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 8,192 | 1.125 | 0.598 | 0.316 |
| 16,384 | 2.25 | 1.195 | 0.633 |
| 32,768 | 4.5 | 2.391 | 1.266 |


**Server flags.** --embedding --pooling last -ub 8192 (card); pooling_type 3 is baked in, so --pooling may be omitted; /v1/embeddings

**llama.cpp.** Minimum b6063 (satisfied by b11474: True; loads on old pin b10964: True). Qwen3-Embedding support (#15023, first tag b6063)

**Licence.** apache-2.0 (Qwen/Qwen3-Embedding-4B card; GGUF general.license).

**Caveats.**

- Instruction-aware: queries take an "Instruct: ...\nQuery: ..." prefix per the Qwen card; documents none.
- Matryoshka: output dimension can be truncated (32-4096 for 8B, 32-2560 for 4B) but vectors are not interchangeable with bge-m3 (D-45).

## Day 1: `rerank-qwen3-8b`

- Suggested key: rerank-qwen3-8b
- Role (fixed choices): Primary reranker replacing bge-reranker-v2-m3 (D-14 / D-45)
- Repo: `Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp`, subdir `None`, quant Q8_0; model_file_pattern `Qwen3-Reranker-8B.Q8_0.gguf`
- Total: 8,047,134,304 B = 8.05 GB = 7.49 GiB
- Architecture `qwen3`; native context 40,960
- Official GGUF: none: Qwen publishes no Qwen3-Reranker GGUF (Qwen/Qwen3-Reranker-8B-GGUF returns 401/not found; author=Qwen search lists only safetensors). The only ggml-org reranker GGUF is Qwen3-Reranker-0.6B-Q8_0.

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Qwen3-Reranker-8B.Q8_0.gguf` | 8,047,134,304 | `13815d68c755c6293161a9aebc986976af90b35a7c9ffd2e074e58d28ddfe4ae` |

**KV.**  Bytes per token: f16 147,456, q8_0 78,336, q4_0 41,472 (36 x (K 8x128 + V 8x128)).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 8,192 | 1.125 | 0.598 | 0.316 |
| 16,384 | 2.25 | 1.195 | 0.633 |
| 32,768 | 4.5 | 2.391 | 1.266 |


**Server flags.** --reranking / --rerank (sets embedding=true and pooling_type=RANK; common/arg.cpp L3494-L3499 at b11474); endpoints /v1/rerank, /rerank, /reranking. At b11474 RANK inputs may exceed n_ubatch (batch split #28876, b11223); before that the query+document had to fit -ub.

**llama.cpp.** Minimum b6578 (satisfied by b11474: True; loads on old pin b10964: True). qwen3 reranker support (#15824, first tag b6578); RANK batch splitting for causal rerankers #28876 (b11223)

**Licence.** apache-2.0 (Qwen/Qwen3-Reranker-8B card; GGUF general.license (Voodisss, gorse-io)); register D-35 (a).

**Published files carrying the rank head** (header read 2026-10-16):

| Repo | File | Bytes | sha256 | pooling_type | labels | rerank template | cls.output.weight | Tensors | Provenance |
|---|---|---:|---|---:|---|---|---|---:|---|
| Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp | `Qwen3-Reranker-8B.Q8_0.gguf` | 8,047,134,304 | `13815d68c755c6293161a9aebc986976af90b35a7c9ffd2e074e58d28ddfe4ae` | 4 | ['yes', 'no'] | True | ['cls.output.weight', [4096, 2], 'Q8_0'] | 399 | card: official convert_hf_to_gguf.py, converted 2026-03-09 (card typo says 2025-03-09); uploader reports relevance 0.99 vs 0.00 on a two-document test; source revision pre-dates the 2026-04-16 sentence-transformers commit (weights unchanged) |
| gorse-io/Qwen3-Reranker-8B-GGUF | `Qwen3-Reranker-8B-Q8_0.gguf` | 8,047,134,336 | `7ad83063f1639b393d5cd9c54a65a461ec3a3109fd57e827614207162ae24304` | 4 | ['yes', 'no'] | True | ['cls.output.weight', [4096, 2], 'Q8_0'] | 399 | conversion.json: llama.cpp d81235049 (b11429), source revision 77d193c791ed (8B); 8B used a "low-memory rowwise reranker head" adapter (conversion_adapter.py, not the stock path); metadata checked, inference not run (their note) |
| giladgd/Qwen3-Reranker-8B-GGUF | `Qwen3-Reranker-8B.Q8_0.gguf` | 8,047,134,336 | `0f062cc2d6b013c2bd708549dcf36a153043323bc37e17b3b993d95fa0a2105d` | 4 | ['yes', 'no'] | True | ['cls.output.weight', [4096, 2], 'Q8_0'] | 399 | node-llama-cpp uploader, 2025-09-27, converter build not stated |

Recommended: `Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp/Qwen3-Reranker-8B.Q8_0.gguf`. Header verified today (pooling_type 4 RANK, labels [yes,no], tokenizer.chat_template.rerank, cls.output.weight [n_embd,2] Q8_0, 399 tensors); stock converter; uploader-tested scores. gorse-io is the newest conversion (b11429) but its 8B used a custom head adapter. Pin the sha256 below either way.

Rejected: mradermacher/Qwen3-Reranker-8B-GGUF: 8B Q8_0 (8,707,188,736 B) read 2026-10-14: no pooling_type/labels/template, plain causal qwen3 with output.weight: unusable. QuantFactory 8B Q8_0 is 8,707,188,288 B (same full-lm_head size as mradermacher; not header-checked); DevQuasar 4B is reported broken by Voodisss.

**Local conversion with the pinned converter (fallback).**

- When: Only if the published file is rejected or a reproducible in-house build is wanted.
- Source: Qwen/Qwen3-Reranker-8B at revision 77d193c791ed757ca307ee72715aa132723da912 (apache-2.0, not gated; safetensors 16.38 GB, tie_word_embeddings false: cls built from lm_head)
- Tool: convert_hf_to_gguf.py and gguf-py from the pinned tree b9acf138 (conversion/qwen.py Qwen3Model L160-L255)
- Python requirements: requirements/requirements-convert_hf_to_gguf.txt at the pin: torch==2.11.0 (CPU index), transformers==4.57.6, numpy~=2.2.6, sentencepiece>=0.1.98,<0.3.0, protobuf>=4.21.0,<5.0.0, gguf (install the pinned ./gguf-py). transformers is required: _find_rerank_config calls AutoTokenizer to find the "yes"/"no" token ids.
- Detection: Reranker mode fires when the source README.md contains "# qwen3-reranker" (the Qwen card heading "# Qwen3-Reranker-8B" does), or the directory name / _name_or_path / model_type contains "qwen3-reranker", or the HF arch contains SequenceClassification. Keep the original README.md in the snapshot directory and name the directory Qwen3-Reranker-<size>.
- Command: python3 convert_hf_to_gguf.py <dir>/Qwen3-Reranker-8B --outtype q8_0 --outfile Qwen3-Reranker-8B-Q8_0.gguf   (or --outtype bf16, then llama-quantize ... Q8_0)
- Writes: qwen3.pooling_type = 4 (RANK), qwen3.classifier.output_labels = [yes, no], tokenizer.chat_template.rerank, cls.output.weight = stack(lm_head[yes], lm_head[no]) shape [n_embd, 2]; 399 tensors
- Verify after: Parse the header: pooling_type 4, labels [yes,no], cls.output.weight present; then POST /v1/rerank with one relevant and one irrelevant document and require a clear score gap.

## Day 1: `rerank-qwen3-4b`

- Suggested key: rerank-qwen3-4b
- Role (fixed choices): Primary reranker replacing bge-reranker-v2-m3 (D-14 / D-45)
- Repo: `Voodisss/Qwen3-Reranker-4B-GGUF-llama_cpp`, subdir `None`, quant Q8_0; model_file_pattern `Qwen3-Reranker-4B.Q8_0.gguf`
- Total: 4,279,678,912 B = 4.28 GB = 3.99 GiB
- Architecture `qwen3`; native context 40,960
- Official GGUF: none: Qwen publishes no Qwen3-Reranker GGUF (Qwen/Qwen3-Reranker-4B-GGUF returns 401/not found; author=Qwen search lists only safetensors). The only ggml-org reranker GGUF is Qwen3-Reranker-0.6B-Q8_0.

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Qwen3-Reranker-4B.Q8_0.gguf` | 4,279,678,912 | `102ac400d68f02877c2fdfecf0732872652298448352b6faf28ec7f8dca8c913` |

**KV.**  Bytes per token: f16 147,456, q8_0 78,336, q4_0 41,472 (36 x (K 8x128 + V 8x128)).

| Context | f16 GiB | q8_0 GiB | q4_0 GiB |
|---:|---:|---:|---:|
| 8,192 | 1.125 | 0.598 | 0.316 |
| 16,384 | 2.25 | 1.195 | 0.633 |
| 32,768 | 4.5 | 2.391 | 1.266 |


**Server flags.** --reranking / --rerank (sets embedding=true and pooling_type=RANK; common/arg.cpp L3494-L3499 at b11474); endpoints /v1/rerank, /rerank, /reranking. At b11474 RANK inputs may exceed n_ubatch (batch split #28876, b11223); before that the query+document had to fit -ub.

**llama.cpp.** Minimum b6578 (satisfied by b11474: True; loads on old pin b10964: True). qwen3 reranker support (#15824, first tag b6578); RANK batch splitting for causal rerankers #28876 (b11223)

**Licence.** apache-2.0 (Qwen/Qwen3-Reranker-4B card; GGUF general.license (Voodisss, gorse-io)); register D-35 (a).

**Published files carrying the rank head** (header read 2026-10-16):

| Repo | File | Bytes | sha256 | pooling_type | labels | rerank template | cls.output.weight | Tensors | Provenance |
|---|---|---:|---|---:|---|---|---|---:|---|
| Voodisss/Qwen3-Reranker-4B-GGUF-llama_cpp | `Qwen3-Reranker-4B.Q8_0.gguf` | 4,279,678,912 | `102ac400d68f02877c2fdfecf0732872652298448352b6faf28ec7f8dca8c913` | 4 | ['yes', 'no'] | True | ['cls.output.weight', [2560, 2], 'Q8_0'] | 399 | card: official convert_hf_to_gguf.py, converted 2026-03-09 (card typo says 2025-03-09); uploader reports relevance 0.99 vs 0.00 on a two-document test; source revision pre-dates the 2026-04-16 sentence-transformers commit (weights unchanged) |
| gorse-io/Qwen3-Reranker-4B-GGUF | `Qwen3-Reranker-4B-Q8_0.gguf` | 4,279,678,944 | `2cf26934b45414d51cb1e1fc68378da02f8a1ae2893b70ccb13c381ec1ec9398` | 4 | ['yes', 'no'] | True | ['cls.output.weight', [2560, 2], 'Q8_0'] | 399 | conversion.json: llama.cpp d81235049 (b11429), source revision 22e683669bc0 (4B); stock converter path; metadata checked, inference not run (their note) |
| giladgd/Qwen3-Reranker-4B-GGUF | `Qwen3-Reranker-4B.Q8_0.gguf` | 4,279,678,944 | `d060a1bfb805debcd6c2258c70ba21c56ee1ea837bc23b0bc49e09d13115987c` | 4 | ['yes', 'no'] | True | ['cls.output.weight', [2560, 2], 'Q8_0'] | 399 | node-llama-cpp uploader, 2025-09-27, converter build not stated |

Recommended: `Voodisss/Qwen3-Reranker-4B-GGUF-llama_cpp/Qwen3-Reranker-4B.Q8_0.gguf`. Header verified today (pooling_type 4 RANK, labels [yes,no], tokenizer.chat_template.rerank, cls.output.weight [n_embd,2] Q8_0, 399 tensors); stock converter; uploader-tested scores. gorse-io is the newest conversion (b11429) but its 8B used a custom head adapter. Pin the sha256 below either way.

Rejected: mradermacher/Qwen3-Reranker-4B-GGUF: 4B Q8_0 header read today: no pooling_type, no classifier labels, no rerank template, no cls.output.weight (398 tensors): unusable as a reranker. QuantFactory 8B Q8_0 is 8,707,188,288 B (same full-lm_head size as mradermacher; not header-checked); DevQuasar 4B is reported broken by Voodisss.

**Local conversion with the pinned converter (fallback).**

- When: Only if the published file is rejected or a reproducible in-house build is wanted.
- Source: Qwen/Qwen3-Reranker-4B at revision 22e683669bc0f0bd69640a1354a6d0aebcfeede5 (apache-2.0, not gated; safetensors about 8.0 GB, tie_word_embeddings true: cls built from embed_tokens)
- Tool: convert_hf_to_gguf.py and gguf-py from the pinned tree b9acf138 (conversion/qwen.py Qwen3Model L160-L255)
- Python requirements: requirements/requirements-convert_hf_to_gguf.txt at the pin: torch==2.11.0 (CPU index), transformers==4.57.6, numpy~=2.2.6, sentencepiece>=0.1.98,<0.3.0, protobuf>=4.21.0,<5.0.0, gguf (install the pinned ./gguf-py). transformers is required: _find_rerank_config calls AutoTokenizer to find the "yes"/"no" token ids.
- Detection: Reranker mode fires when the source README.md contains "# qwen3-reranker" (the Qwen card heading "# Qwen3-Reranker-8B" does), or the directory name / _name_or_path / model_type contains "qwen3-reranker", or the HF arch contains SequenceClassification. Keep the original README.md in the snapshot directory and name the directory Qwen3-Reranker-<size>.
- Command: python3 convert_hf_to_gguf.py <dir>/Qwen3-Reranker-4B --outtype q8_0 --outfile Qwen3-Reranker-4B-Q8_0.gguf   (or --outtype bf16, then llama-quantize ... Q8_0)
- Writes: qwen3.pooling_type = 4 (RANK), qwen3.classifier.output_labels = [yes, no], tokenizer.chat_template.rerank, cls.output.weight = stack(lm_head[yes], lm_head[no]) shape [n_embd, 2]; 399 tensors
- Verify after: Parse the header: pooling_type 4, labels [yes,no], cls.output.weight present; then POST /v1/rerank with one relevant and one irrelevant document and require a clear score gap.

## Phased engines (document only; do not build)

| Key | Repo / subdir | Rung | Bytes | GB | GiB | Arch | Native ctx | KV B/token f16 / q8_0 / q4_0 | Min build | Licence | Sampling |
|---|---|---|---:|---:|---:|---|---:|---|---|---|---|
| `qwen3.8-flash-next` | unsloth/Qwen3.8-Flash-Next-GGUF / UD-Q6_K_XL | UD-Q6_K_XL | 169,165,382,688 | 169.17 | 157.55 | qwen4exp | 262,144 | 30,720 / 16,320 / 8,640 | b11330 | qwen-community-1.0 (license: other) | thinking: temperature 1.0, top_p 0.95, top_k 20, min_p 0.0, presence_penalty 0.0 |
| `nemotron-3-super` | unsloth/NVIDIA-Nemotron-3-Super-120B-A12B-GGUF / Q8_0 | Q8_0 | 128,472,991,680 | 128.47 | 119.65 | nemotron_h_moe | 1,048,576 | 8,192 / 4,352 / 2,304 | b8295 | nvidia-nemotron-open-model-license (license: other) | temperature 1.0, top_p 0.95 |
| `intern-s1` | bartowski/internlm_Intern-S1-GGUF / internlm_Intern-S1-Q5_K_S | Q5_K_S | 161,963,285,952 | 161.96 | 150.84 | qwen3moe | 40,960 | 192,512 / 102,272 / 54,144 | b6113 | apache-2.0 | temperature 0.7, top_p 1.0, top_k 50, min_p 0.0 |
| `intern-s1` | bartowski/internlm_Intern-S1-GGUF / internlm_Intern-S1-Q4_K_M | Q4_K_M | 142,654,184,800 | 142.65 | 132.86 | qwen3moe | 40,960 | 192,512 / 102,272 / 54,144 | b6113 | apache-2.0 | temperature 0.7, top_p 1.0, top_k 50, min_p 0.0 |
| `ling-3.0-flash-fin` | bartowski/Ling-3.0-flash-Fin-GGUF / Ling-3.0-flash-Fin-Q8_0 | Q8_0 | 135,633,373,216 | 135.63 | 126.32 | bailingmoe3 | 262,144 | 9,216 / 4,896 / 2,592 | b10470 | mit | temperature 1.0, top_p 0.95, top_k 20 |
| `baichuan-m3-235b` | baichuan-inc/Baichuan-M3-235B-Q4_K_M-GGUF / - | Q4_K_M_official | 143,782,829,376 | 143.78 | 133.91 | qwen3moe | 40,960 | 192,512 / 102,272 / 54,144 | b6113 | apache-2.0 | temperature 0.6, top_p 0.95, top_k 20 |
| `baichuan-m3-235b` | mradermacher/Baichuan-M3-235B-GGUF / - | Q5_K_S_mradermacher | 161,881,426,240 | 161.88 | 150.76 | qwen3moe | 40,960 | 192,512 / 102,272 / 54,144 | b6113 | apache-2.0 | temperature 0.6, top_p 0.95, top_k 20 |
| `qwen3-coder-next` | Qwen/Qwen3-Coder-Next-GGUF / Qwen3-Coder-Next-Q8_0 | Q8_0 | 84,812,055,968 | 84.81 | 78.99 | qwen3next | 262,144 | 24,576 / 13,056 / 6,912 | b7186 | apache-2.0 | temperature 1.0, top_p 0.95, top_k 40 |
| `embeddinggemma-2` | ggml-org/embeddinggemma-2-GGUF / - | Q8_0 | 309,855,456 | 0.31 | 0.29 | gemma-embedding2 | header says 262144 (duplicate key overwritten in convert.log); the card states 8,192 tokens | 0 / 0 / 0 | b11452 | apache-2.0 | n/a (embedding) |

### Phased: `qwen3.8-flash-next`

- `unsloth/Qwen3.8-Flash-Next-GGUF` subdir `UD-Q6_K_XL`, 169,165,382,688 B = 169.17 GB = 157.55 GiB; model_file_pattern `Qwen3.8-Flash-Next-UD-Q6_K_XL-00001-of-00006.gguf`

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `UD-Q6_K_XL/Qwen3.8-Flash-Next-UD-Q6_K_XL-00001-of-00006.gguf` | 10,946,624 | `8bdc6bad55b6699f46f60708829932ff4018d7ac9bd309c388534fa31753bdb7` |
| `UD-Q6_K_XL/Qwen3.8-Flash-Next-UD-Q6_K_XL-00002-of-00006.gguf` | 682,434,912 | `494ca4ed3dbf97bc28da88af3890b8877b9032f909812d00c0526a9ca5e91d2e` |
| `UD-Q6_K_XL/Qwen3.8-Flash-Next-UD-Q6_K_XL-00003-of-00006.gguf` | 54,400,261,312 | `34efd79a80a1ce540a517a5d56171924b66ce1c38b04c904f17ad6d8ef17cf20` |
| `UD-Q6_K_XL/Qwen3.8-Flash-Next-UD-Q6_K_XL-00004-of-00006.gguf` | 49,814,108,512 | `0ea4b599880a5a52fcd9c188ba1443f646c69c96571904ddbd6a79246a01a997` |
| `UD-Q6_K_XL/Qwen3.8-Flash-Next-UD-Q6_K_XL-00005-of-00006.gguf` | 49,419,317,344 | `9948e81ae8368144b135a7403d8c0f2cf9a6c2f9f692313865855b2832e696c9` |
| `UD-Q6_K_XL/Qwen3.8-Flash-Next-UD-Q6_K_XL-00006-of-00006.gguf` | 14,838,313,984 | `e4ae255234f42b18012f6d6eec5bf615b57479b7e0a0233c47436a321ffeec3f` |

- mtp: recommended_for_mainline `MTP/mtp-Qwen3.8-Flash-Next-Q8_0.gguf` 4,137,429,120 B sha256 `58a47d6a216307e9b97172d5bfdc3436a8994b632f5e436b9bb6edf8951da8d1`; root_copy_same_sha `mtp-Qwen3.8-Flash-Next-Q8_0.gguf` 4,137,429,120 B sha256 `58a47d6a216307e9b97172d5bfdc3436a8994b632f5e436b9bb6edf8951da8d1`; shared_variant_fork_only `MTP/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf` 2,786,568,256 B sha256 `5ff54097406a905cf3a724c709124ceb0e3e10235ee862298969e91c96fa96e6`; notes: Self-contained head (qwen4exp, block_count 49, nextn_predict_layers 1, 34 tensors incl. blk.48.nextn.eh_proj [5120,2560]) re-uploaded 2026-10-05 "due to upstream changes" after mainline #29761 (b11330); tensor names match src/models/qwen4exp.cpp L295-L301. The shared-* heads set qwen4exp.nextn_shared_target_tensors (unknown to mainline; need the unsloth fork). The MTP/README.md (2026-09-02) saying stock llama.cpp cannot use the heads predates #29761. Unverified on the node. Usage: -md <head> --spec-type draft-mtp --spec-draft-n-max 2; MTP is a net loss at concurrency 8 (README).
- mmproj: recommended `mmproj-F16.gguf` 904,004,000 B sha256 `1f7b7f0b984cf065c604360c29c8098362ed61b290db0ff12c6f360bb1a8a980`; alternative `mmproj-BF16.gguf` 907,542,944 B sha256 `2e788f8c511d8093c7b43cb87b2fd7e14228340318057f8fb20c86df2efe2355`; projector_type: qwen3vl_merger; params: 449M; note: Register D-20 quotes 0.62 GB; the F16 file is 904,004,000 B.
- KV per token: f16 30,720, q8_0 16,320, q4_0 8,640 (12 x (512 + 512 + 256)); 12 QSA full-attention layers (K 2x256 + V 2x256) + indexer K-only 128 x kpool_row 2 (raw | pooled) per layer; 36 Gated DeltaNet layers recurrent
- KV GiB at context: 32,768: f16 0.938, q8_0 0.498; 131,072: f16 3.75, q8_0 1.992; 262,144: f16 7.5, q8_0 3.984; 524,288: f16 15.0, q8_0 7.969; 1,048,576: f16 30.0, q8_0 15.938
- Recurrent state: 112.2 MiB per sequence (36 recurrent layers x (r 30720 + s 786432) f32 elements x 4 B)
- Sampling: {"thinking": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0}, "non_thinking": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}, "chat_template": "reasoning_effort default 'xhigh' (low/high/xhigh); enable_thinking false disables thinking"}; source: Qwen/Qwen3.8-Flash-Next card L342-L343; generation_config temp 1.0 top_p 0.95 top_k 20
- llama.cpp: minimum b11330 (qwen4exp arch #27742 (b10660); Vulkan hc ops #28988 (b11012); attention fix #29751 and MTP #29761 (b11330, the register floor); mask/indexer optimisations #29824 (b11352), #29825 (b11372)); loads on old pin: False
- Licence: qwen-community-1.0 (license: other); register D-03

### Phased: `nemotron-3-super`

- `unsloth/NVIDIA-Nemotron-3-Super-120B-A12B-GGUF` subdir `Q8_0`, 128,472,991,680 B = 128.47 GB = 119.65 GiB; model_file_pattern `NVIDIA-Nemotron-3-Super-120B-A12B-Q8_0-00001-of-00004.gguf`

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Q8_0/NVIDIA-Nemotron-3-Super-120B-A12B-Q8_0-00001-of-00004.gguf` | 7,872,576 | `7efc3a9c77a65929897e2fed1d1d670d514297508485f80996e77004f5e81332` |
| `Q8_0/NVIDIA-Nemotron-3-Super-120B-A12B-Q8_0-00002-of-00004.gguf` | 49,004,160,864 | `1dd0a70006703d67ab63a07b49c5ef97cf3081b94549daef6eb792ce225a48b9` |
| `Q8_0/NVIDIA-Nemotron-3-Super-120B-A12B-Q8_0-00003-of-00004.gguf` | 49,284,659,200 | `31dea2f2172a5ce5456ee5d55f4d4cc2bf18e5ddc6c3e6d512281fe65163deba` |
| `Q8_0/NVIDIA-Nemotron-3-Super-120B-A12B-Q8_0-00004-of-00004.gguf` | 30,176,299,040 | `4962476f3703a5f9e613b431b866d0344ac361ccae61a0e7e21ed8f8b47b5d3b` |

- KV per token: f16 8,192, q8_0 4,352, q4_0 2,304 (8 x (K 2x128 + V 2x128))
- KV GiB at context: 32,768: f16 0.25, q8_0 0.133; 131,072: f16 1.0, q8_0 0.531; 262,144: f16 2.0, q8_0 1.062; 524,288: f16 4.0, q8_0 2.125; 1,048,576: f16 8.0, q8_0 4.25
- Recurrent state: 164.7 MiB per sequence (40 recurrent layers x (r 30720 + s 1048576) f32 elements x 4 B)
- Sampling: {"temperature": 1.0, "top_p": 0.95, "note": "existing entry uses temp 0.6 top_p 0.95; the card now says 1.0/0.95"}; source: nvidia card L79 "Use temperature=1.0 and top_p=0.95 across all tasks"; generation_config
- llama.cpp: minimum b8295 (Nemotron 3 Super #20411 first tag b8295 (document closes b8297, S6); MTP extension #29018 (b11025)); loads on old pin: True
- Licence: nvidia-nemotron-open-model-license (license: other); register D-04
- Caveat: #20732 Vulkan fault near 20K-token prompts (closed, not planned) stands (T-12).

### Phased: `intern-s1`

- internlm/Intern-S1-GGUF carries Q8_0 (9 shards) and f16 only; Q5_K_S / Q4_K_M come from bartowski
- Rung Q5_K_S: `bartowski/internlm_Intern-S1-GGUF` subdir `internlm_Intern-S1-Q5_K_S`, 161,963,285,952 B = 161.96 GB = 150.84 GiB

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `internlm_Intern-S1-Q5_K_S/internlm_Intern-S1-Q5_K_S-00001-of-00005.gguf` | 39,794,912,672 | `611e9cd218d75f5b3d167fb7234a9efe7f12cc2b014ca15d118782ca72095122` |
| `internlm_Intern-S1-Q5_K_S/internlm_Intern-S1-Q5_K_S-00002-of-00005.gguf` | 39,999,792,736 | `5b3cebaabce690deeecece54e95ccbdd7b1f86162f4c39c8f3be8a86b23d54e6` |
| `internlm_Intern-S1-Q5_K_S/internlm_Intern-S1-Q5_K_S-00003-of-00005.gguf` | 39,949,967,552 | `f875461c09dd8ca6c1aae93f2b1ea9766d6f264ae9ae44ee03f9bea40d95a6ae` |
| `internlm_Intern-S1-Q5_K_S/internlm_Intern-S1-Q5_K_S-00004-of-00005.gguf` | 39,952,081,216 | `cc6ae181fd03e0abf93ff7115bad56bd9b22f00fab6a2ae42e486235c27a4991` |
| `internlm_Intern-S1-Q5_K_S/internlm_Intern-S1-Q5_K_S-00005-of-00005.gguf` | 2,266,531,776 | `7d00243428b7d8b4e474d449528e22df97e1af543f1f61ad74d398fcecb61bcc` |

- Rung Q4_K_M: `bartowski/internlm_Intern-S1-GGUF` subdir `internlm_Intern-S1-Q4_K_M`, 142,654,184,800 B = 142.65 GB = 132.86 GiB

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `internlm_Intern-S1-Q4_K_M/internlm_Intern-S1-Q4_K_M-00001-of-00004.gguf` | 39,863,615,936 | `c5f7f589ec4bd8b210ecb66baa5911184fe241f4178a809297cbf8c533f7fbdf` |
| `internlm_Intern-S1-Q4_K_M/internlm_Intern-S1-Q4_K_M-00002-of-00004.gguf` | 39,847,230,560 | `35db7feb3c40a53afdea3fc33d5fd1563c4716cb4a22ff260fd270f04889d5a4` |
| `internlm_Intern-S1-Q4_K_M/internlm_Intern-S1-Q4_K_M-00003-of-00004.gguf` | 39,847,230,560 | `be80ae90435dac96b8f8014a00de22248532ab1b3c49daa26686f91a905038d0` |
| `internlm_Intern-S1-Q4_K_M/internlm_Intern-S1-Q4_K_M-00004-of-00004.gguf` | 23,096,107,744 | `2e5295fe745a056205b06e08f7983fbb4283b8a512c35f79f7c059e526a2ba89` |

- mmproj_optional: `mmproj-internlm_Intern-S1-f16.gguf` 11,221,608,064 B, sha256 `29e36a0642bd920e9d10289ea42558829db18483dfe190fc20381aa2a79f313d`
- KV per token: f16 192,512, q8_0 102,272, q4_0 54,144 (94 x (K 4x128 + V 4x128))
- KV GiB at context: 16,384: f16 2.938, q8_0 1.561; 23,552: f16 4.223, q8_0 2.243; 32,768: f16 5.875, q8_0 3.121; 40,960: f16 7.344, q8_0 3.901
- context_target_default (D-16): Q5_K_S if about 20-23K tokens is enough, Q4_K_M otherwise: raise a to-do for the Principal; the stated default is Q5_K_S at about 23K (Q5_K_S 150.84 GiB + 23,552 x 192,512 B = 4.22 GiB f16).
- Sampling: {"temperature": 0.7, "top_p": 1.0, "top_k": 50, "min_p": 0.0}; source: internlm/Intern-S1 card L90-L93
- llama.cpp: minimum b6113 (intern-s1 support #14875 (b6113); text path is qwen3moe); loads on old pin: True
- Licence: apache-2.0; register D-34

### Phased: `ling-3.0-flash-fin`

- `bartowski/Ling-3.0-flash-Fin-GGUF` subdir `Ling-3.0-flash-Fin-Q8_0`, 135,633,373,216 B = 135.63 GB = 126.32 GiB; model_file_pattern `Ling-3.0-flash-Fin-Q8_0-00001-of-00004.gguf`

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Ling-3.0-flash-Fin-Q8_0/Ling-3.0-flash-Fin-Q8_0-00001-of-00004.gguf` | 39,386,128,800 | `650a507efdae86353d949fde757fcdd8771b655a64c5828279f8f11e0f3cb088` |
| `Ling-3.0-flash-Fin-Q8_0/Ling-3.0-flash-Fin-Q8_0-00002-of-00004.gguf` | 39,383,925,568 | `9de157f0745f1811ce5b2adce10e9f6f43dff772ef19812b34702fc30744b92f` |
| `Ling-3.0-flash-Fin-Q8_0/Ling-3.0-flash-Fin-Q8_0-00003-of-00004.gguf` | 39,383,925,568 | `e85a59a58f18ead26dbad2f94048e30fc4c0c82df3e96971e4c56c716406cc19` |
| `Ling-3.0-flash-Fin-Q8_0/Ling-3.0-flash-Fin-Q8_0-00004-of-00004.gguf` | 17,479,393,280 | `55979aa1c19de742e7cbe833c4449c4fb621824dedc7dd1e73c9487a5a545e06` |

- KV per token: f16 9,216, q8_0 4,896, q4_0 2,592 (8 x row(576) (7 trunk + 1 NextN; 8,064 B f16 if NextN excluded)); K-only MLA cache (576 per layer) on the MLA layers; the default hybrid filter keeps the NextN layer 42 in the main cache
- KV GiB at context: 32,768: f16 0.281, q8_0 0.149; 131,072: f16 1.125, q8_0 0.598; 262,144: f16 2.25, q8_0 1.195; 524,288: f16 4.5, q8_0 2.391; 1,048,576: f16 9.0, q8_0 4.781
- Recurrent state: 74.9 MiB per sequence (35 recurrent layers x (r 36864 + s 524288) f32 elements x 4 B)
- Sampling: {"temperature": 1.0, "top_p": 0.95, "top_k": 20}; source: inclusionAI/Ling-3.0-flash-Fin card L45 (thinking on by default)
- llama.cpp: minimum b10470 (bailingmoe3 #26608 (b10470); dedicated Ling 3.0 chat parser #28682 (b11057)); loads on old pin: True
- Licence: mit (card front matter only; no LICENSE file); register D-34

### Phased: `baichuan-m3-235b`

- Rung Q4_K_M_official: `baichuan-inc/Baichuan-M3-235B-Q4_K_M-GGUF`, 143,782,829,376 B = 143.78 GB = 133.91 GiB. stock Q4_K_M, token_embd BF16; general.sampling temp 0.6 top_p 0.95 top_k 20

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `baichuan-m3-235b-q4_k_m-00001-of-00010.gguf` | 16,933,868,960 | `87ca69b63c5ccfb52b0e23610bb36f6239ce216491f2d647e2ce8a84021082e5` |
| `baichuan-m3-235b-q4_k_m-00002-of-00010.gguf` | 14,801,829,984 | `3ba92f1da0830c4936370d963e604faa3b0c79f096c503a86e31a10aefa65cad` |
| `baichuan-m3-235b-q4_k_m-00003-of-00010.gguf` | 14,636,418,560 | `18138252f9a76030d28d6c9ed4a6178703590a26e69fc4aacabab9b09ef606f1` |
| `baichuan-m3-235b-q4_k_m-00004-of-00010.gguf` | 14,636,418,560 | `d00f7d4c2db5913a57906b581fa4cb79bb1d8c00befa51d25bc605405773f22c` |
| `baichuan-m3-235b-q4_k_m-00005-of-00010.gguf` | 14,844,577,280 | `1a8e51645ea68700b9adf50107eb0593f199c0814c3e1cf61831cdcbae676f25` |
| `baichuan-m3-235b-q4_k_m-00006-of-00010.gguf` | 14,636,418,560 | `6a52ac23b06341351177f796a5e08ed207224ca6cdfd1613f7d3cf1f55698531` |
| `baichuan-m3-235b-q4_k_m-00007-of-00010.gguf` | 14,636,418,560 | `5036ba6a53d96738292be8f8c1d53487a7ad47d4d7e858a087432eeb80b20b89` |
| `baichuan-m3-235b-q4_k_m-00008-of-00010.gguf` | 14,844,577,280 | `b54cb0a2e62511eec86184b35f4f0c229e87c7466115bb85339a47d309cdca4c` |
| `baichuan-m3-235b-q4_k_m-00009-of-00010.gguf` | 15,677,212,160 | `3c2cee2d1854fc6c0f5fa71ce63911baf089831a229d9ca431bd1ff9bcb43fd5` |
| `baichuan-m3-235b-q4_k_m-00010-of-00010.gguf` | 8,135,089,472 | `0d414040f43c94d439a8de37a48a2915d365f9b2a9920a22aca4fb01ac5c19b3` |

- Rung Q5_K_S_mradermacher: `mradermacher/Baichuan-M3-235B-GGUF`, 161,881,426,240 B = 161.88 GB = 150.76 GiB. no official Q5_K_S exists (baichuan-inc publishes only the Q4_K_M GGUF)

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Baichuan-M3-235B.Q5_K_S.gguf` | 161,881,426,240 | `f9608a9cda2bc372d3cb78142a41df95aaec07cb6c22b31ea4b15be993a8579f` |

- KV per token: f16 192,512, q8_0 102,272, q4_0 54,144 (94 x (K 4x128 + V 4x128))
- KV GiB at context: 16,384: f16 2.938, q8_0 1.561; 23,552: f16 4.223, q8_0 2.243; 32,768: f16 5.875, q8_0 3.121; 40,960: f16 7.344, q8_0 3.901
- context_target_default (D-16): Q5_K_S if about 20-23K is enough (default), Q4_K_M otherwise; to-do for the Principal.
- Sampling: {"temperature": 0.6, "top_p": 0.95, "top_k": 20}; source: baichuan-inc/Baichuan-M3-235B generation_config.json; card example temperature 0.6
- llama.cpp: minimum b6113 (qwen3moe has been supported since 2025; any recent build); loads on old pin: True
- Licence: apache-2.0; register D-34
- Caveat: Register D-16 gives Baichuan official Q4_K_M as 133.91 GiB: confirmed (143,782,829,376 B). The mradermacher Q4_K_M is a different file (142,154,074,432 B = 132.39 GiB).

### Phased: `qwen3-coder-next`

- `Qwen/Qwen3-Coder-Next-GGUF` subdir `Qwen3-Coder-Next-Q8_0`, 84,812,055,968 B = 84.81 GB = 78.99 GiB; model_file_pattern `Qwen3-Coder-Next-Q8_0-00001-of-00004.gguf`

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00001-of-00004.gguf` | 26,190,321,568 | `30b7554fc0c846a5dc3ecf585884c77471f73e3da698a8ba4fabd8e7868c6533` |
| `Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00002-of-00004.gguf` | 26,212,935,584 | `3f96379de5a5c4655cb378710ea571d5e9cc96f260120a44a6477198efcdc27d` |
| `Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00003-of-00004.gguf` | 26,047,157,792 | `5dd1ce07eaae95ee430331dc9c6f3120ff88e4211ad3a0cceeaa963f25328504` |
| `Qwen3-Coder-Next-Q8_0/Qwen3-Coder-Next-Q8_0-00004-of-00004.gguf` | 6,361,641,024 | `76730702c630bf76305139165cb85421858604030851dfd64fe96a5e67cda99d` |

- KV per token: f16 24,576, q8_0 13,056, q4_0 6,912 (12 full-attention layers x (K 2x256 + V 2x256))
- KV GiB at context: 32,768: f16 0.75, q8_0 0.398; 131,072: f16 3.0, q8_0 1.594; 262,144: f16 6.0, q8_0 3.188; 524,288: f16 12.0, q8_0 6.375; 1,048,576: f16 24.0, q8_0 12.75
- Recurrent state: 75.4 MiB per sequence (36 recurrent layers x (r 24576 + s 524288) f32 elements x 4 B)
- Sampling: {"temperature": 1.0, "top_p": 0.95, "top_k": 40}; source: Qwen/Qwen3-Coder-Next card L194; generation_config
- llama.cpp: minimum b7186 (qwen3next #16095 (b7186)); loads on old pin: True
- Licence: apache-2.0 (card only; LICENSE file missing (D-34))

### Phased: `embeddinggemma-2`

- `ggml-org/embeddinggemma-2-GGUF` subdir `None`, 309,855,456 B = 0.31 GB = 0.29 GiB; model_file_pattern `embeddinggemma-2-Q8_0.gguf`

| File (repo path) | Bytes | sha256 |
|---|---:|---|
| `embeddinggemma-2-Q8_0.gguf` | 309,855,456 | `2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135` |

- mmproj_for_image_audio: q8_0 `mmproj-embeddinggemma-2-Q8_0.gguf` 554,821,024 B sha256 `c4a8a52691ecef40618438928bdf9e68379b854e24166f292592353db0aab64f`; bf16 `mmproj-embeddinggemma-2-BF16.gguf` 982,074,784 B sha256 `d2033b3cd0223cfb2e7a2b50dc80a664c78c816d375f5b017ec3ef80ee0bc766`
- bf16_alternative: `embeddinggemma-2-BF16.gguf` 557,950,176 B, sha256 `68bae29d62fb8c7d23e98d21fd4662753ddd636e6b62b8a70dfe059a9844f216`
- KV per token: f16 0, q8_0 0, q4_0 0; non-causal embedding arch: create_memory returns no cache (llama-model.cpp L2384)
- llama.cpp: minimum b11452 (embeddinggemma2 text+vision+audio #30054 (b11452); ggml-org file converted on a branch build 0.6.0-dev b11451 commit 1e91a0b05 (research note)); loads on old pin: False
- Licence: apache-2.0; register D-35 (if kept, D-60)

## Residency arithmetic (weights only)

Weights only; resident companions also hold KV (Qwen3 embed/rerank: 147,456 B/token f16, 1.125 GiB per 8,192-token pool each) and compute buffers. The Arbiter subtracts the measured resident set and 16 GiB headroom (D-14 why).

| Item | GiB |
|---|---:|
| router Qwen3.5-4B Q8_0 | 4.17 |
| Qwen3-Embedding-8B Q8_0 | 7.49 |
| Qwen3-Reranker-8B Q8_0 | 7.49 |
| Qwen3-Embedding-4B Q8_0 | 3.99 |
| Qwen3-Reranker-4B Q8_0 | 3.99 |
| 8B pair total | 14.99 |
| 4B pair total | 7.97 |
| GLM-5.3-Flash UD-IQ4_XS | 146.05 |
| GLM + mmproj-F16 | 147.1 |
| DeepSeek-V4-Flash-0731 UD-Q8_K_XL | 150.75 |
| gpt-oss-120b F16-trunk | 60.88 |

D-14: about 150 GiB with the 8B pair resident (a), about 157 GiB with the 4B pair (b); bge pair today 1.3 GiB.

**With the 8B pair resident (budget about 150 GiB) DeepSeek-V4-Flash UD-Q8_K_XL (150.75 GiB weights) cannot load; the fixed Day 1 Apex choice therefore needs the 4B pair (D-14 (b), about 157 GiB: 150.75 + KV, f16 6,880 B/token = 1.68 GiB at 256K, 6.72 GiB at 1M) or on-demand / CPU companions (D-14 (d)/(e)). GLM fits either way at moderate context (146.05 + 4.81 GiB f16 KV at 256K; +1.05 GiB mmproj for vision).**

## Sources

- tree_api: https://huggingface.co/api/models/<repo>/tree/main?recursive=true (2026-10-16)
- headers: HTTP range reads of https://huggingface.co/<repo>/resolve/main/<file> (first 4-25 MB; GLM/DSV4/Flash-Next/Nemotron first shards read whole), parsed locally
- cards_configs: https://huggingface.co/<repo>/raw/main/{README.md,config.json,generation_config.json}; /api/models/<repo>; /api/models/<repo>/paths-info/main (last-commit dates)
- llama_cpp: git tree b9acf138 in the session clone (scratchpad lcpp.git); first-support tags by git tag --contains
- gorse_conversion: https://huggingface.co/gorse-io/Qwen3-Reranker-{8B,4B}-GGUF/raw/main/{conversion.json,convert.log}
- engines.json mapping: Each Day 1 entry gives the inputs for engines.json: hf_repo, subdir, arch, licence, files[] (path, bytes, sha256), model_file_pattern, mmproj, quant, footprint (GB/GiB), native context, kv bytes/token, sampling for extra_args, min_llama_cpp_tag. files[].path is the repo path (existing entries keep the subdir prefix in name, e.g. "UD-Q4_K_XL/...").
- verify value: All Day 1 sha256 values come from the tree API read today. files_rule knows verify=unverified|research-snippet only; a tree-API-read hash is closest to research-snippet (compare at pull time, log a mismatch loudly) unless a new value is added to files_rule.
