# Named companion engines (EmbeddingGemma 2, Nemotron 3 Diarization) and the best-quality embedding, reranking and diarisation alternatives for A.T.L.A.S., as of 2026-10-15

Evidence conventions used below. "Primary" = read in full from the vendor's Hugging Face card, API, licence file, GitHub source or the vendor's own blog, retrieved 2026-10-15 unless another date is given. "Excerpt" = taken only from a search-result summary and not opened. "Earlier notes" = a verified fact reused from `research_notes/ATLAS LLM roster refresh 2026/` (gap_closure_2026-10-14.md, discipline_specific_models.md, vision_speech_image_models.md), with the original source URL carried over. Byte counts come from the Hugging Face tree API (`/api/models/<repo>/tree/main?recursive=true`); GB = bytes/1e9, GiB = bytes/2^30. GGUF metadata was parsed from ranged downloads of the file headers (bytes 0 to 12-30 MB) on 2026-10-15. The current node engines are bge-m3 Q8_0 (`ggml-org/bge-m3-Q8_0-GGUF`, 1024-dim, 8192 tokens) and bge-reranker-v2-m3 Q8_0 (`gpustack/bge-reranker-v2-m3-GGUF`) as llama-server units, and pyannote `speaker-diarization-3.1` with `speaker-diarization-community-1` as fallback on `pyannote.audio==4.0.7` (per current_atlas_baseline.md).

## Key question 1: Does EmbeddingGemma 2 exist, and what is it?

### Takeaway
Yes. Google DeepMind released EmbeddingGemma 2 (`google/embeddinggemma-2`) on 6 October 2026 under Apache 2.0, not gated. It is a 740M-parameter multimodal embedder (text, code, image, video and audio). The text-only path is 270M parameters and produces a 768-dim output with Matryoshka truncation, an 8,192-token context and mean pooling. Its MTEB multilingual v2 score of 61.36 is only 0.21 above EmbeddingGemma 1 and far below the 8B-27B leaders (70-74). It beats bge-m3 (59.56) by about 1.8 points. Its gains are in code (78.68 vs 68.76) and in the new modalities. llama.cpp support (`gemma-embedding2` architecture) was merged on 6 October 2026 (PR #30054, first build b11452), after the node's v0.4.1 pin (build b10964). The pinned build therefore cannot load it.

### Cited Findings
- Identity and release: repo `google/embeddinggemma-2`, Hugging Face `createdAt` 2026-09-14 (repository creation, probably private before launch), `lastModified` 2026-10-06, `gated: false`, `license: apache-2.0`, library transformers, architecture `EmbeddingGemma2Model`, model_type `embedding_gemma2`, safetensors BF16 744,371,512 parameters; 135,175 downloads at retrieval (primary) — [HF API google/embeddinggemma-2](https://huggingface.co/api/models/google/embeddinggemma-2)
- Launch date and positioning: Google blog "EmbeddingGemma 2: an open, lightweight multimodal embedding model", dated Oct 06, 2026 (`article:published_time` 2026-10-06). It is "Built on the Gemma 4 architecture and released under a commercially permissive Apache 2.0 license … 740 million parameters, making it optimal for on-device inference". Listed serving tools: "transformers, sentence-transformers, MLX, vLLM, llama.cpp, SGLang, Ollama, and LMStudio" (primary) — [Google blog, 2026-10-06](https://blog.google/innovation-and-ai/technology/developers-tools/embeddinggemma-2/)
- Card architecture (primary): 740M total = 130M transformer backbone + 140M embedder (270M text model) + vision encoder 170M + audio encoder 300M. 24 layers, model dimension 512, hidden 2048, sliding window 1024, vocabulary 262,144, 4 heads, KV heads local/global 2/1, local:global 5:1. Pooling "Mean Pooling", projection 512→768, context window 8,192 tokens, native output 768-dim with MRL truncation to 512/256/128, "100+ languages", training-data cutoff January 2025 — [EmbeddingGemma 2 card](https://huggingface.co/google/embeddinggemma-2/raw/main/README.md)
- Card benchmarks at 768d, full-precision checkpoint (primary). EmbeddingGemma 2 vs EmbeddingGemma 1: MTEB multilingual v2 Mean(Task) 61.36 vs 61.15. MTEB code v1 78.68 vs 68.76. MTEB English v2 68.46 (truncation table). MIEB lite 64.64, MMEB v2 image 57.28, VisDoc 67.84, video 50.67, MSEB retrieval 69.54, MAEB 49.39. Truncation: 512d 61.17 / 68.41 / 77.24; 256d 60.41 / 67.78 / 76.18; 128d 57.89 / 65.68 / 71.41 (multilingual / English / code) — [EmbeddingGemma 2 card](https://huggingface.co/google/embeddinggemma-2/raw/main/README.md)
- Usage requirements (primary). Task-instruction prefixes are part of the training (e.g. `task: search result | query: {query}` and `title: {title} | text: {content}`), and omitting them "reduces precision". Encoders can be loaded selectively (text only = 270M). "Run inference in `bfloat16` or `float32`. Do not use `float16` … In `float16` the model returns NaN or silently degraded embeddings rather than raising an error" — [EmbeddingGemma 2 card](https://huggingface.co/google/embeddinggemma-2/raw/main/README.md)
- Licence wording: the card front matter says `apache-2.0`, but the card's licence link points to `ai.google.dev/gemma/docs/gemma_4_license` labelled "Apache 2.0". The card also says "Deployments must adhere to the Gemma Prohibited Use Policy" (primary) — [EmbeddingGemma 2 card](https://huggingface.co/google/embeddinggemma-2/raw/main/README.md)
- GGUF availability (primary). `ggml-org/embeddinggemma-2-GGUF` (created 2026-10-06, licence apache-2.0):
  - embeddinggemma-2-BF16.gguf 557,950,176 B = 0.558 GB = 0.520 GiB
  - embeddinggemma-2-Q8_0.gguf 309,855,456 B = 0.310 GB = 0.289 GiB
  - mmproj-embeddinggemma-2-BF16.gguf 982,074,784 B = 0.982 GB = 0.915 GiB
  - mmproj-embeddinggemma-2-Q8_0.gguf 554,821,024 B = 0.555 GB = 0.517 GiB

  `unsloth/embeddinggemma-2-GGUF` (2026-10-06) and `mradermacher/embeddinggemma-2-GGUF` (mmproj only at retrieval) also exist — [HF tree ggml-org/embeddinggemma-2-GGUF](https://huggingface.co/api/models/ggml-org/embeddinggemma-2-GGUF/tree/main?recursive=true); [HF search embeddinggemma](https://huggingface.co/api/models?author=unsloth&search=embeddinggemma)
- GGUF metadata (header parse of the ggml-org Q8_0, primary): `general.architecture = gemma-embedding2`, size label 271M, `gemma-embedding2.embedding_length_out = 768`, `attention.causal = False`, `pooling_type = 1` (MEAN, baked in). The header also stores `context_length = 262144`, which disagrees with the card's 8,192-token window. The conversion log shows the converter overwriting a duplicate context-length key — [ggml-org Q8_0 file](https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/main/embeddinggemma-2-Q8_0.gguf); [convert.log](https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/raw/main/convert.log)
- Converter build: ggml-org quantised the files with llama.cpp "version: 0.6.0-dev (build 11451, commit 1e91a0b05)" (primary) — [convert.log](https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/raw/main/convert.log)
- llama.cpp support at the pin (primary). `src/llama-arch.cpp` at tag v0.4.1 has `gemma-embedding` (EmbeddingGemma 1) but no `gemma-embedding2`; master has `{ LLM_ARCH_GEMMA_EMBEDDING2, "gemma-embedding2" }`. The adding commit is `4fbc76dec` "model: support embeddinggemma2 (text+vision+audio) (#30054)", 2026-10-06 18:20 +0200, first contained in release tag b11452. Tag v0.4.1 is commit `b29c606e2` (2026-09-14, build b10964). Other archs added after the pin: clef, glm5-next, hrm_text, k2-horizon, vllm-decision2 — [llama-arch.cpp @v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-arch.cpp); [llama-arch.cpp @master](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-arch.cpp); git log of https://github.com/ggml-org/llama.cpp (cloned 2026-10-15, master head `e467e5b5e`, latest tag b11641 dated 2026-10-15)
- Secondary coverage agrees on 740M, Gemma 4 basis, Apache 2.0 and the code gain, and reports that "multilingual text performance was roughly flat compared to EmbeddingGemma 1" (excerpt) — [NewsBytes](https://www.newsbytesapp.com/news/science/google-launches-embeddinggemma-2-740-million-parameter-gemma-4-based-on-device-model/tldr); [OfficeChai](https://officechai.com/ai/google-launches-embeddinggemma-2-for-multimodal-on-device-embeddings/)
- Predecessor for reference: `google/embeddinggemma-300m` (created 2025-07-17); MMTEB Mean(Task) 61.15 (the tencent KaLM card's comparison table, primary) — [KaLM-Embedding-Gemma3-12B-2511 card](https://huggingface.co/tencent/KaLM-Embedding-Gemma3-12B-2511/raw/main/README.md); [HF API author=google](https://huggingface.co/api/models?author=google&search=embed)

### Inferences
- On text retrieval EmbeddingGemma 2 is a small step up from bge-m3: 61.36 vs 59.56 on MTEB multilingual, with bge-m3's figure from Qwen's table. It is 9-13 points below the 8B-27B leaders (Key question 2). Under the "top results at all times" directive it does not qualify as the primary text embedder. Its distinctive value is one shared vector space for images, video and audio alongside text, plus strong code retrieval at very small size.
- To run it in llama-server, the node must move its llama.cpp pin to b11452 or later, which also brings the rerank batch fix #28876 (Key question 2). At the pin, a PyTorch/sentence-transformers path in the Phase 4 ROCm 10.0 image is the only option. Per the card, that path must run in bf16 or fp32, not fp16.
- The card's float16 warning matters for the Vulkan backend, because llama.cpp's Vulkan matmul paths can use fp16 arithmetic. Whether the Q8_0/BF16 GGUF produces correct vectors on RADV must be checked with a cosine comparison against the sentence-transformers fp32 output. This is unverified.
- The GGUF's baked `context_length = 262144` does not override the model's real training window. The node should set `-c 8192` explicitly (inference from the card's 8,192 limit).

### Gaps
- No independent MTEB leaderboard entry for EmbeddingGemma 2 was read; all scores are Google's own.
- Whether the llama.cpp port reproduces sentence-transformers outputs, and its RTF on gfx1151, was not tested.
- The exact text of the linked `gemma_4_license` page was not read; the HF front matter and blog both say Apache 2.0.

## Key question 2: Which embedding and reranking models lead on quality in 2026 and can run locally, and does llama-server support them at the pin?

### Takeaway
Several open embedders now exceed Qwen3-Embedding-8B, the earlier choice.
- Microsoft harrier-oss-v1-27b (MIT, 27B) claims MTEB multilingual v2 74.3.
- Tencent KaLM-Embedding-Gemma3-12B-2511 claims MMTEB 72.32, but under a custom licence.
- NVIDIA Nemotron-3-Embed-8B (OpenMDW-1.1, 8B, bidirectional, July 2026) claims state-of-the-art RTEB with MMTEB-retrieval 75.45.
- Octen-Embedding-8B (Apache 2.0, a Qwen3-Embedding-8B fine-tune) claims RTEB #1 as of January 2026.

Qwen3-Embedding-8B is 70.58 and bge-m3 59.56. llama-server support at the v0.4.1 pin varies:
- Qwen3-Embedding: works (official GGUF, `--pooling last`).
- Octen: Qwen3 architecture; community GGUFs only.
- Nemotron-3-Embed-8B: a community GGUF carries `attention.causal = False` and MEAN pooling on the `mistral3` arch. Plausible but untested.
- Harrier-27B and KaLM-12B: causal, last-token-pooled Gemma 3 models. The pinned converter maps `Gemma3TextModel` to the `gemma-embedding` arch, which llama.cpp forces to bidirectional attention, so their llama.cpp GGUFs probably run with the wrong attention.

For reranking, Qwen3-Reranker-8B/4B remain the strongest permissively licensed models with llama-server rerank support, and only via a correctly converted GGUF. Newer rerankers are either non-commercial (jina-reranker-v3.5, ctxl-rerank-v2) or lack llama-server conversion support (zerank-2 4B, NVIDIA llama-nemotron-rerank-1b-v2).

### Cited Findings
Embedders. Vendor-reported scores, read from the cards, primary:
- Qwen3-Embedding-8B / 4B (Apache 2.0, not gated): MTEB multilingual Mean(Task) 70.58 / 69.45, English v2 75.22 / 74.60, vs BGE-M3 59.56. Official GGUF sizes: 8B Q8_0 8,047,105,824 B = 8.047 GB = 7.494 GiB and f16 15,141,156,384 B = 14.101 GiB; 4B Q8_0 4,279,660,224 B = 3.986 GiB and f16 8,049,889,824 B = 7.497 GiB. Card flags: `llama-server -m model.gguf --embedding --pooling last -ub 8192`. GGUF header `qwen3.pooling_type = 3` (LAST), `embedding_length = 4096` (earlier notes) — [Qwen3-Embedding README](https://github.com/QwenLM/Qwen3-Embedding); [Qwen3-Embedding-8B card](https://huggingface.co/Qwen/Qwen3-Embedding-8B/raw/main/README.md); [Qwen3-Embedding-8B-GGUF tree](https://huggingface.co/api/models/Qwen/Qwen3-Embedding-8B-GGUF/tree/main?recursive=true)
- microsoft/harrier-oss-v1 family (created 2026-03-30, MIT, not gated). "State-of-the-art results on the Multilingual MTEB v2 benchmark as of the release date": 270M (640-dim) 66.5; 0.6B (1,024-dim) 69.0; 27B (5,376-dim, 32,768 tokens) **74.3**. config `Gemma3TextModel`, 62 layers, hidden 5376, `use_bidirectional_attention: false`; sentence-transformers pooling `pooling_mode_lasttoken: true` — [harrier-oss-v1-27b card](https://huggingface.co/microsoft/harrier-oss-v1-27b/raw/main/README.md); [config.json](https://huggingface.co/microsoft/harrier-oss-v1-27b/raw/main/config.json); [1_Pooling/config.json](https://huggingface.co/microsoft/harrier-oss-v1-27b/raw/main/1_Pooling/config.json)
- harrier-27b community GGUF `Abiray/harrier-oss-v1-27b-GGUF` (2026-04-02):
  - Q8_0: 28,716,174,368 B = 28.716 GB = 26.744 GiB
  - Q6_K: 22,175,176,352 B = 20.652 GiB
  - Q5_K_M: 19,279,877,792 B
  - Q4_K_M: 16,554,890,912 B = 15.418 GiB

  Header: `general.architecture = gemma-embedding`, `pooling_type = 3` (LAST), no `attention.causal` key — [HF tree](https://huggingface.co/api/models/Abiray/harrier-oss-v1-27b-GGUF/tree/main?recursive=true); [Q8_0 header](https://huggingface.co/Abiray/harrier-oss-v1-27b-GGUF/resolve/main/harrier-27b-Q8_0.gguf)
- tencent/KaLM-Embedding-Gemma3-12B-2511 (2025-11-04, licence `other`/`tencent-kalm-embedding-community`, 11.77B params, 3,840-dim, 32k tokens, last-token pooling). Its MMTEB table: KaLM 72.32; llama-embed-nemotron-8b 69.46; Qwen3-Embedding-8B 70.58; gemini-embedding-001 68.37; Qwen3-Embedding-4B 69.45; embeddinggemma-300m 61.15. The card claims "SOTA performance in MMTEB (due to 11-2025)". Community GGUFs: mradermacher (static and i1) — [KaLM card](https://huggingface.co/tencent/KaLM-Embedding-Gemma3-12B-2511/raw/main/README.md); [HF API](https://huggingface.co/api/models/tencent/KaLM-Embedding-Gemma3-12B-2511)
- nvidia/Nemotron-3-Embed-8B-BF16 (created 2026-07-14, licence OpenMDW-1.1, 7,952,683,008 params, not gated):
  - "achieves state-of-the-art performance on the multilingual RTEB leaderboard as of July 16, 2026".
  - Architecture: `Ministral3Model`, `is_causal: false` ("bidirectional attention masking"), average pooling.
  - Output 4,096-dim (sliceable to 2,048/1,024); maximum sequence 32,768.
  - Avg NDCG@10 at sequence length 4096 (RTEB-16 / ViDoRe-V3 text / MMTEB retrieval): 8B 78.46 / 60.60 / 75.45; 1B 72.38 / 57.74 / 71.04; llama-nemotron-embed-vl-1b-v2 61.98 / 52.54 / 59.71.
  - Test hardware listed: A100 and H100 only.

  [Nemotron-3-Embed-8B card](https://huggingface.co/nvidia/Nemotron-3-Embed-8B-BF16/raw/main/README.md); [config.json](https://huggingface.co/nvidia/Nemotron-3-Embed-8B-BF16/raw/main/config.json)
- Nemotron-3-Embed-8B community GGUF `Abiray/Nemotron-3-Embed-8B-GGUF` (2026-07-21, 142,897 downloads): Q8_0 8,458,435,477 B = 8.458 GB = 7.878 GiB; Q6_K 6,532,463,509 B; Q4_K_M 4,896,390,039 B. Header: `general.architecture = mistral3`, `mistral3.attention.causal = False`, `mistral3.pooling_type = 1` (MEAN), yarn RoPE (factor 16, original context 16,384). Imatrix computed on wikitext-2 — [HF tree](https://huggingface.co/api/models/Abiray/Nemotron-3-Embed-8B-GGUF/tree/main?recursive=true); [Q8_0 header](https://huggingface.co/Abiray/Nemotron-3-Embed-8B-GGUF/resolve/main/Nemotron-3-Embed-8B-Q8_0.gguf)
- Octen/Octen-Embedding-8B (2025-12-23, Apache 2.0, `Qwen3Model`, fine-tuned from Qwen3-Embedding-8B, 4,096-dim, 32,768 tokens). "ranks #1 on the RTEB Leaderboard (as of January 12, 2026)", RTEB Mean(Task) 0.8045. The same table gives Qwen3-Embedding-8B 0.7547, voyage-3-large 0.7812, gemini-embedding-001 0.7602 and bge-m3 0.5893. Community GGUFs only (mradermacher and others) — [Octen card](https://huggingface.co/Octen/Octen-Embedding-8B/raw/main/README.md); [HF search](https://huggingface.co/api/models?search=Octen-Embedding-8B)
- Other 2026 open embedders found (HF API metadata only; scores not read):
  - perplexity-ai/pplx-embed-v1-4b: MIT, 4.02B, 2026-01-20.
  - ai-sage/Giga-Embeddings-instruct-10B-A1.8B-0826: MIT, 10.48B MoE, 2026-08-10.
  - jinaai/jina-embeddings-v5-text-small: CC-BY-NC-4.0, 0.6B.
  - Qwen/Qwen3-VL-Embedding-8B: Apache 2.0, 8.14B, 2026-01-07.
  - nvidia/llama-embed-nemotron-8b: licence `customized-nscl-v1`, 2025-10-07.
  - LiquidAI/LFM2.5-Embedding-350M, ibm-granite granite-embedding r2.

  [HF API pipeline_tag=feature-extraction / sentence-similarity sorted by downloads](https://huggingface.co/api/models?pipeline_tag=sentence-similarity&sort=downloads&direction=-1&limit=300)
- Live MTEB snapshot: the search found no October 2026 leaderboard snapshot (excerpt) — [WebSearch summary pointing to huggingface.co/spaces/mteb/leaderboard](https://huggingface.co/spaces/mteb/leaderboard)

llama.cpp behaviour at the v0.4.1 pin (primary source code):
- The converter registers `Gemma3TextModel` as `class EmbeddingGemma(Gemma3Model)` with `model_arch = gguf.MODEL_ARCH.GEMMA_EMBEDDING` (conversion/gemma.py L182-186). The `gemma-embedding` model loader then sets `hparams.causal_attn = false; // embeddings do not use causal attention` and symmetric SWA (src/models/gemma-embedding.cpp L4-12) — `git show v0.4.1:conversion/gemma.py` and `git show v0.4.1:src/models/gemma-embedding.cpp` in [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp/tree/v0.4.1)
- Generic hparams loading reads `LLM_KV_ATTENTION_CAUSAL` and `LLM_KV_POOLING_TYPE` from the GGUF for every architecture (src/llama-model.cpp L1232-1233 at v0.4.1) — [llama-model.cpp @v0.4.1](https://raw.githubusercontent.com/ggml-org/llama.cpp/v0.4.1/src/llama-model.cpp)

Rerankers:
- Qwen3-Reranker-8B / 4B (Apache 2.0, 32k context). MMTEB-R 72.94 / 72.74 vs bge-reranker-v2-m3 58.36 and jina multilingual v2 63.73 (earlier notes). No Qwen-authored GGUF. The working llama.cpp conversions are `Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp` (Q8_0 8,047,134,304 B = 7.494 GiB; f16 15,141,207,904 B) and the 4B equivalent (Q8_0 4,279,678,912 B = 3.986 GiB). mradermacher's 8B Q8_0 lacks `pooling_type`, the classifier labels and the rerank template, so it is unusable as a reranker — [QwenLM/Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding); [Voodisss 8B](https://huggingface.co/Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp); [mradermacher 8B Q8_0](https://huggingface.co/mradermacher/Qwen3-Reranker-8B-GGUF/resolve/main/Qwen3-Reranker-8B.Q8_0.gguf)
- `/v1/rerank` at v0.4.1 requires `--reranking` (or `--embedding --pooling rank`) and a GGUF with pooling RANK, classifier labels and a `rerank` chat template. The pinned converter's `conversion/qwen.py` writes these for Qwen3-Reranker (PR #15824, merged 2025-09-25). A RANK-pooled query+document longer than `n_ubatch` is rejected at the pin; this was fixed by PR #28876, merged 2026-09-27 (after the pin). The workaround is a large `-ub` (earlier notes) — [server-context.cpp @v0.4.1](https://github.com/ggml-org/llama.cpp/blob/v0.4.1/tools/server/server-context.cpp); [conversion/qwen.py @v0.4.1](https://github.com/ggml-org/llama.cpp/blob/v0.4.1/conversion/qwen.py); [PR #28876](https://github.com/ggml-org/llama.cpp/pull/28876)
- jina-reranker-v3.5 (2026-07-14, **CC-BY-NC-4.0**, 0.6B, Qwen3-0.6B base, listwise; official GGUF exists). nDCG@10 BEIR / MIRACL / RTEB / Struct-IR:
  - jina-reranker-v3.5: 63.20 / 74.11 / 70.95 / 48.3
  - Qwen3-Reranker-4B: 62.28 / 76.56 / 77.68 / 55.6
  - mxbai-rerank-large-v2 (1.5B): 62.45 / 69.65 / 70.81 / 43.0

  [jina-reranker-v3.5 card](https://huggingface.co/jinaai/jina-reranker-v3.5/raw/main/README.md)
- zeroentropy/zerank-2-reranker (2025-11-19, Apache 2.0, 4B, base Qwen/Qwen3-4B, `Qwen3ForCausalLM`, 32k context, card language `en`). The card claims it outperforms cohere-rerank-v3.5 and gemini-2.5-flash. It scores via the raw "Yes" logit; a May 2026 breaking change returns logits instead of sigmoid probabilities — [zerank-2 card](https://huggingface.co/zeroentropy/zerank-2-reranker/raw/main/README.md); [config.json](https://huggingface.co/zeroentropy/zerank-2-reranker/raw/main/config.json)
- nvidia/llama-nemotron-rerank-1b-v2 (2025-10-16, OpenMDW-1.1 plus "Llama 3.2 Community Model License" as additional information, 1.24B, multilingual and cross-lingual, "evaluated to work successfully with up to a sequence length of 8192 tokens") — [card](https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2/raw/main/README.md)
- ContextualAI ctxl-rerank-v2-instruct-multilingual-1b (**CC-BY-NC-SA-4.0**, 100+ languages, 32K context, instruction-following) — [card](https://huggingface.co/ContextualAI/ctxl-rerank-v2-instruct-multilingual-1b-fp8/raw/main/README.md)
- Qwen3-VL-Reranker-8B (Apache 2.0, 8.77B, 2026-01-07) exists. llama.cpp PR #20332 "Model fix qwen3vl reranker support" was merged 2026-03-10, inside the pin (earlier notes) — [HF API](https://huggingface.co/api/models/Qwen/Qwen3-VL-Reranker-8B); [conversion/qwen.py @v0.4.1](https://github.com/ggml-org/llama.cpp/blob/v0.4.1/conversion/qwen.py)

### Inferences
- Size budget: under the new rule that every LLM engine runs alone, even the largest retrieval pair is small next to the 164 GiB budget. Harrier-27B Q8_0 is 26.7 GiB; Qwen3-Embedding-8B f16 is 14.1 GiB plus Qwen3-Reranker-8B f16 14.1 GiB. If the retrieval pair stays always resident beside each LLM, as the baseline's "embedding 4 GB" row assumes, it subtracts directly from the LLM budget. A 27B embedder would cost about 27 GiB of LLM headroom. An 8B pair at Q8_0 would cost about 15 GiB, and at f16 about 28 GiB.
- Licence-clean, quality-first candidates are Harrier-27B (MIT, highest MTEB v2 claim), Nemotron-3-Embed-8B (OpenMDW, permissive; highest retrieval-specific claims) and Qwen3-Embedding-8B / Octen-8B (Apache 2.0, proven llama-server path). KaLM-12B carries a custom Tencent licence; jina v5 and v3.5 and ctxl v2 are non-commercial.
- Harrier-27B and KaLM-12B are causal models with last-token pooling. The pinned llama.cpp converts any `Gemma3TextModel` checkpoint to the `gemma-embedding` arch and forces bidirectional attention, so their GGUFs most likely produce vectors that differ from the reference implementation. Until a cosine-similarity check against sentence-transformers proves otherwise, they belong in the PyTorch/ROCm image (or vLLM), not in llama-server. Harrier at 27B in bf16 would need about 54 GB (27B × 2 bytes; inference, not measured).
- Nemotron-3-Embed-8B's community GGUF declares non-causal attention and MEAN pooling, which llama.cpp reads generically. It is the most plausible high-end llama-server embedder, but the GGUF is third-party (imatrix on wikitext) and untested against NVIDIA's reference.
- Embedding quantisation is cheap at this scale, so Q8_0 or f16 should be used rather than Q4 to protect retrieval quality. No published measurement of Q8_0 vs f16 retrieval loss was found for these models (see Gaps).
- For reranking, Qwen3-Reranker-8B (or 4B) converted locally with the pinned `convert_hf_to_gguf.py` remains the best permissive llama-server option. zerank-2 is Qwen3-4B-based but scores with a "Yes"-logit head; whether the pinned Qwen3 reranker conversion detects it was not checked.

### Gaps
- The live MTEB leaderboard (multilingual v2 and RTEB) could not be read. All rankings above are vendor claims on different dates, and no single same-date ranking was obtained.
- No primary Q8_0-vs-f16 retrieval-quality figures exist for Qwen3-Embedding, Nemotron-3-Embed, Harrier or the Qwen3 rerankers.
- Not tested: llama-server output parity for the Nemotron-3-Embed-8B mistral3 non-causal GGUF and for the Harrier/KaLM gemma-embedding GGUFs.
- Not read: the licence texts of `customized-nscl-v1` (llama-embed-nemotron-8b) and `tencent-kalm-embedding-community`.
- Not read: the MTEB scores of pplx-embed-v1-4b and Giga-Embeddings-10B.

## Key question 3: Does Nemotron 3 Diarization exist, and what is it?

### Takeaway
Yes. NVIDIA released `nvidia/Nemotron-3-Diarization` on 23 September 2026. It is a 100M-parameter streaming Sortformer-family model (31-layer RoPE Transformer encoder, AOSC speaker cache plus FIFO) for up to eight speakers, with streaming latencies from 0.32 s to an offline-style 30.4 s buffer. It is licensed OpenMDW-1.1 (permissive, commercial use allowed) and not gated. It runs on three runtimes: NeMo (Speech/NeMo Framework 3.0), Hugging Face Transformers (native `Nemotron3DiarizationForAudioFrameClassification`), and NVIDIA's ggml-based NeMo-Speech.cpp, which has CPU and Vulkan backends. The official repo ships a Q8_0 GGUF. NVIDIA reports large DER cuts over the 4-speaker Sortformer v2.1. On the same third-party harness (Argmax OpenBench, 2026-09-22) it averages 0.19 DER vs 0.28 for pyannote community-1 and 0.20 for paid pyannoteAI precision-3.

### Cited Findings
- Repo facts (primary):
  - Created 2026-09-01, `lastModified` 2026-09-24, `gated: false`, licence `openmdw-1.1`, library `nemo`, pipeline `voice-activity-detection`.
  - Tags: streaming-sortformer, transformers, gguf; arXiv 2409.06656, 2507.18446, 2605.15442, 2507.09226, 2408.13106.
  - Safetensors F32, 99,226,504 parameters.
  - Files: `model.safetensors` 396,954,592 B = 0.397 GB; `Nemotron-3-Diarization.nemo` 198,676,480 B = 0.199 GB; `Nemotron-3-Diarization.q8_0.gguf` 107,012,128 B = 0.107 GB = 0.100 GiB; `ASR_INTEGRATION_GUIDE.md`; `diarization_evaluation.md`.

  [HF API](https://huggingface.co/api/models/nvidia/Nemotron-3-Diarization); [HF tree](https://huggingface.co/api/models/nvidia/Nemotron-3-Diarization/tree/main?recursive=true)
- Card (primary):
  - "open-weight speaker diarization model … supports both streaming and offline inference and handles up to eight speakers". Release Date "September 23, 2026". "This model is ready for commercial or non-commercial use."
  - Architecture: "A 31-layer Transformer encoder with Rotary Positional Embeddings", 10 ms mel features downsampled ×8 to an 80 ms encoder frame rate, a Conv1D upsampler back to 10 ms, and AOSC plus FIFO for streaming. 100M parameters.
  - Input: 16 kHz mono. Output: `[T, 8]` per-speaker activity probabilities, speakers ordered by arrival. "Maximum Duration: Not limited when chunked inference is used."
  - Runtime engine: "NeMo Framework v3.0". Supported hardware lists only NVIDIA Ampere, Ada, Blackwell and Hopper GPUs; OS Linux.

  [Nemotron 3 Diarization card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- Latency presets (input-buffer latency, primary): 30.4 s offline (spkcache 264, fifo 40, chunk 340, right context 40, update 300); 1.04 s (264/264/9/4/222); 0.64 s (264/264/6/2/222); 0.32 s (264/264/3/1/222). The card says 80 ms is technically possible but "the lowest recommended configuration is 0.32 s" — [card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- Config (primary): `Nemotron3DiarizationForAudioFrameClassification`, audio encoder hidden 512, 31 layers, 8 heads, 128 mel bins, subsampling 8, `num_speakers: 8`, `transformers_version 5.18.0.dev0`. The card's Transformers instructions say "Install from source" — [config.json](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/config.json); [card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- Training (primary): about 10,000 h of real conversation (Fisher, AMI, ICSI, VoxConverse dev/test, AISHELL-4, DIHARD3 dev, CALLHOME Part 1, AliMeeting, DiPCo, NOTSOFAR1, DISPLACE, David AI, YODAS-v2 pseudo-labels) plus 82,611 h of simulated 1-8 speaker mixtures. Initialised from NEST SSL; trained on 8 × 8 A100-80GB — [card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- DER, NVIDIA protocol, NeMo `e2e_diarize_speech.py`, bf16, overlap included (primary). Collar 0 s except CALLHOME-Part2 at 0.25 s. **Forced-alignment reference RTTMs** are used for AMI, AliMeeting and NOTSOFAR1, and the card warns that results on other references "are not directly comparable". Full-set DER, Nemotron 3 Diarization at 30.4 s / 1.04 s / 0.32 s vs Sortformer 4spk-v2.1 at 30.4 s:

  | Dataset | Nemotron 3 (30.4 s / 1.04 s / 0.32 s) | Sortformer 4spk-v2.1 (30.4 s) |
  | --- | --- | --- |
  | DIHARD III | 12.73 / 13.18 / 13.55 | 19.09 |
  | DIHARD III, 5–9 speakers | 27.58 | 40.21 |
  | CALLHOME-Part2 | 9.10 / 10.29 / 11.32 | 10.32 |
  | AliMeeting near | 6.40 | 11.57 |
  | AliMeeting far | 10.47 | 13.69 |
  | AMI MHM | 9.25 / 9.48 / 10.05 | 15.81 |
  | AMI SDM | 11.14 / 12.80 / 12.95 | 21.42 |
  | NOTSOFAR1 MHM | 6.77 | 21.77 |
  | NOTSOFAR1 SC | 11.00 | 30.49 |

  [card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- No VoxConverse DER appears in the NVIDIA card. VoxConverse v0.3 dev and test are listed among the **training** sets (primary) — [card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- Speed (primary): RTFx on an NVIDIA Blackwell RTX PRO 5000, BF16, NeMo/PyTorch, eager / `torch.compile`. Batch 1: 30.4 s 1340 / 4385; 1.04 s 38 / 164; 0.32 s 12.5 / 54. Batch 32 offline: 12,196 / 15,113 — [card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- NVIDIA blog (primary, published September 23, 2026). The model is "ranked #1 in VoiceArena's Diarization leaderboard with a 14.72% Diarization Error Rate (DER)". That run covered 12 systems / 17 configurations on 139 English conversations (~22 h), with overlap scored, system SAD and no collar; the next-ranked system scored 19.3%. "These initial results may change as Voice Arena completes its Version 1 evaluation". The blog also reports an unweighted mean 41.0% relative DER reduction vs Sortformer v2.1 at 1.04 s — [HF blog nvidia/nemotron-diarization](https://huggingface.co/blog/nvidia/nemotron-diarization)
- A secondary report says the same output scores 4.29% DER with a 250 ms collar (excerpt) — [Digg summary](https://digg.com/ai/ca8hbnt1). A RuntimeWire headline says the "preview is evaluation-only" (excerpt, not opened) — [RuntimeWire](https://runtimewire.com/article/nvidia-nemotron-diarization-eight-speakers-open-weights). The final HF repo carries OpenMDW-1.1 (primary, above), and NVIDIA's evaluation subcard still refers to a `Nemotron-3-Diarization-preview.nemo` path — [diarization_evaluation.md](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/diarization_evaluation.md)
- OpenMDW-1.1 licence text (primary): permission "free of charge, to deal in the Model Materials without restriction, including under all copyright, patent, database, and trade secret rights". Redistribution must retain the agreement and notices. Rights terminate if the licensee sues alleging the Model Materials infringe. The licence imposes no restrictions on outputs — [OpenMDW-1.1](https://openmdw.ai/license/1-1/)
- Earlier NVIDIA diarisers on HF (primary): `nvidia/diar_sortformer_4spk-v1` (2024-12-09), `diar_streaming_sortformer_4spk-v2` (2025-06-04) and `diar_streaming_sortformer_4spk-v2.1` (2025-10-22). All are capped at four speakers — [HF API author=nvidia search=diar](https://huggingface.co/api/models?author=nvidia&search=diar)
- NeMo-Speech.cpp (primary). It calls itself "NVIDIA's official solution for local speech inference … native inference powered by ggml". Supported models include "Streaming Sortformer 4-speaker v2 and Nemotron 3 Diarization, standalone or combined with ASR", plus Nemotron 3.5 ASR Streaming 0.6B, Nemotron Speech Streaming 0.6B, Parakeet TDT 0.6B v3 and Parakeet CTC 1.1B. Backends are CPU, CUDA, Metal and Vulkan ("Vulkan archives use the host's Vulkan loader and vendor driver"); code is Apache 2.0. CLI: `nemo-speech diarize meeting.wav [--format rttm]`; "V2 supports up to four speakers and V3 up to eight". `--offline` full attention is limited "to about 6.6 minutes", and longer audio uses the default streaming pass — [NeMo-Speech.cpp README](https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/README.md); [docs/install.md](https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/docs/install.md); [docs/cli.md](https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/docs/cli.md)
- NeMo toolkit latest on PyPI: `nemo-toolkit` 3.0.0 (2026-08-07), requires Python ≥3.10. The card asks for Python 3.12+ (primary) — [PyPI nemo-toolkit](https://pypi.org/pypi/nemo-toolkit/json)
- Community exports appeared within days of release: ONNX (`onnx-community/Nemotron-3-Diarization-ONNX`, 3,530 downloads), LiteRT, CoreML/CoreAI, OpenVINO, and several GGUFs — [HF search nemotron diariz](https://huggingface.co/api/models?search=nemotron%20diariz&sort=createdAt&direction=-1)

### Inferences
- Nemotron 3 Diarization is a real, permissively licensed (OpenMDW) diariser with three runtime paths. One of them, NeMo-Speech.cpp, uses the same ggml/Vulkan stack the node already runs for llama.cpp, so it does not depend on ROCm.
- The eight-speaker cap is a hard limit. The prior notes' objection to Sortformer's four-speaker cap is halved but not removed: meetings with more than eight distinct speakers will be mis-attributed. pyannote's clustering approach has no such fixed cap.
- NVIDIA's own DER numbers use tighter forced-alignment references and must not be set against pyannote's README table. The OpenBench figures in Key question 4 are the only same-harness comparison found.

### Gaps
- No NVIDIA DER on VoxConverse (it is in the training data). Independent DIHARD3 and CALLHOME runs exist only through OpenBench.
- No published accuracy figures for the Q8_0 GGUF vs the F32/BF16 checkpoint.
- The exact transformers release that first contains `nemotron3_diarization` was not established; the config says 5.18.0.dev0 and the card says install from source.
- The VoiceArena leaderboard itself was not opened, so whether pyannote community-1 was among its 12 systems is unknown.

## Key question 4: Can the diarisation candidates run on the node (Phase 4 ROCm 10.0 image on gfx1151, or the CPU), at what real-time factor, and how does pyannote community-1 compare on the same sets?

### Takeaway
No source measures any of these diarisers on gfx1151. The node has three routes:
- Nemotron 3 Diarization through NeMo-Speech.cpp's Vulkan or CPU build. This is the same ggml/RADV stack as llama.cpp, and the GGUF is official.
- Nemotron 3 Diarization through Transformers or NeMo on the ROCm 10.0 PyTorch image. Plain PyTorch, but NVIDIA lists only NVIDIA GPUs.
- pyannote community-1 on the same ROCm image, as today.

Real-time factors are published only for an NVIDIA Blackwell GPU, an H100 (pyannote) and an M2 Ultra (OpenBench). On OpenBench's same-harness run, Nemotron 3 averages DER 0.19 vs pyannote community-1 0.28, wins on 9 of 11 datasets, and loses on Earnings-21 (0.20 vs 0.10).

### Cited Findings
- Same-harness comparison: Argmax OpenBench, all runs dated 2026-09-22. Pyannote community-1, Sortformer v2.1 and Nemotron 3 were run through Argmax SDK 3.1.6 SpeakerKit on an M2 Ultra Mac Studio; precision-3 through the pyannoteAI cloud API. AMI-IHM/SDM use the forced-alignment references (`argmaxinc/ami-openbench`). DER (fraction), pyannoteAI precision-3 / pyannote community-1 / Sortformer v2 / Nemotron 3:

  | Dataset | precision-3 | community-1 | Sortformer v2 | Nemotron 3 |
  | --- | --- | --- | --- | --- |
  | AISHELL-4 | 0.10 | 0.12 | 0.32 | 0.11 |
  | AMI-IHM | 0.30 | 0.36 | 0.18 | 0.09 |
  | AMI-SDM | 0.32 | 0.38 | 0.23 | 0.11 |
  | AVA-AVD | 0.34 | 0.48 | 0.60 | 0.45 |
  | AliMeeting | 0.11 | 0.23 | 0.21 | 0.18 |
  | CallHome | 0.15 | 0.30 | 0.19 | 0.15 |
  | DIHARD-III | 0.14 | 0.22 | 0.21 | 0.13 |
  | EGO4D | 0.37 | 0.48 | 0.50 | 0.41 |
  | Earnings-21 | 0.10 | 0.10 | 0.45 | 0.20 |
  | MSDWILD | 0.22 | 0.33 | 0.33 | 0.23 |
  | VoxConverse | 0.09 | 0.11 | 0.22 | 0.08 |
  | **Average** | **0.20** | **0.28** | **0.31** | **0.19** |

  Speed factor averages (M2 Ultra, Argmax ports): community-1 558×, Sortformer v2.1 162×, Nemotron 3 242×, precision-3 cloud 50×. Speaker-count accuracy averages: Nemotron 3 71%, precision-3 64%, community-1 50% (primary) — [OpenBench BENCHMARKS.md](https://raw.githubusercontent.com/argmaxinc/OpenBench/main/BENCHMARKS.md)
- pyannote's own table (README, "last updated in 2025-09", pyannote protocol and references). DER % for 3.1 / community-1 / precision-2:

  | Dataset | 3.1 | community-1 | precision-2 |
  | --- | --- | --- | --- |
  | AISHELL-4 | 12.2 | 11.7 | 11.4 |
  | AliMeeting (channel 1) | 24.5 | 20.3 | 15.2 |
  | AMI IHM | 18.8 | 17.0 | 12.9 |
  | AMI SDM | 22.7 | 19.9 | 15.6 |
  | AVA-AVD | 49.7 | 44.6 | 37.1 |
  | CALLHOME part 2 | 28.5 | 26.7 | 16.6 |
  | DIHARD 3 full | 21.4 | 20.2 | 14.7 |
  | Ego4D dev | 51.2 | 46.8 | 39.0 |
  | MSDWild | 25.4 | 22.8 | 17.3 |
  | RAMC | 22.2 | 20.8 | 10.5 |
  | REPERE phase 2 | 7.9 | 8.9 | 7.4 |
  | VoxConverse v0.3 | 11.2 | 11.2 | 8.5 |

  Self-hosted speed on an H100 80GB: community-1 takes 31 s per hour of audio on AMI-IHM (~116× real time) and 37 s per hour on DIHARD3 (~97×) (primary; the ×-conversions are computed) — [pyannote-audio README](https://raw.githubusercontent.com/pyannote/pyannote-audio/main/README.md)
- pyannote releases: `pyannote.audio` 4.0.7 (2026-06-30) is still the latest on PyPI. No new open pipeline newer than `speaker-diarization-community-1` was found under the pyannote HF org; `speaker-diarization-precision` is the hosted product's repo (primary) — [PyPI pyannote.audio](https://pypi.org/pypi/pyannote.audio/json); [HF API author=pyannote](https://huggingface.co/api/models?author=pyannote&sort=createdAt&direction=-1)
- community-1 licence: CC-BY-4.0, gated with an auto-approve consent form (earlier notes) — [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
- NVIDIA's RTFx on an RTX PRO 5000 Blackwell, BF16: offline 30.4 s preset 1340× eager at batch 1; 1.04 s streaming 38× eager / 164× compiled (primary) — [Nemotron 3 Diarization card](https://huggingface.co/nvidia/Nemotron-3-Diarization/raw/main/README.md)
- CPU reference point (ASR only, not diarisation): NeMo-Speech.cpp runs the 0.6B Nemotron Speech Streaming ASR at Q8_0 at "6× real time" on CPU (Intel Core i7-11700K) vs 67× on an RTX 4090. The README says both ASR and TTS "stay faster than real time even on a CPU" (primary) — [NeMo-Speech.cpp README](https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/README.md); [BENCHMARK.md](https://raw.githubusercontent.com/NVIDIA/NeMo-Speech.cpp/main/BENCHMARK.md)
- No benchmark of NeMo-Speech.cpp on Vulkan or AMD Radeon hardware was found. A Japanese write-up reports results from macOS only (excerpt) — [search summary citing dev.classmethod.jp](https://dev.classmethod.jp/articles/using-nemotron-3-diarization-by-nemo-speech/)
- ROCm on gfx1151 for the Phase 4 image, from earlier notes. pyannote is plain PyTorch and no gfx1151 report was found either way. Kokoro on gfx1151 needed ROCm 7.2 wheels from repo.radeon.com (pytorch.org ROCm wheels segfaulted) and `MIOPEN_FIND_MODE=2`. CTranslate2's ROCm wheel was confirmed on gfx1151 with ROCm 7.2.2 — [vision_speech_image_models.md, citing Kokoro-FastAPI and CTranslate2 PR #1989](https://github.com/OpenNMT/CTranslate2/pull/1989)

### Inferences
- Feasibility. Nemotron 3 Diarization is 100M parameters (107 MB at Q8_0), so CPU inference on 16 Zen 5 cores is very likely faster than real time. The 0.6B ASR runs at 6× real time on an older 8-core desktop CPU in the same runtime. This is an extrapolation, not a measurement. The Vulkan build of NeMo-Speech.cpp is the lowest-risk GPU path because the node's llama.cpp already depends on RADV. The ROCm 10.0 PyTorch path (Transformers `AutoModelForAudioFrameClassification`) should work in principle, but NVIDIA lists no AMD hardware and nobody has reported it on gfx1151.
- Quality. On the only same-harness evidence, Nemotron 3 beats community-1 on 9 of 11 sets and roughly matches paid precision-3 on average (0.19 vs 0.20). The biggest margins are on meetings: AMI-IHM 0.09 vs 0.36, AMI-SDM 0.11 vs 0.38, CallHome 0.15 vs 0.30, DIHARD-III 0.13 vs 0.22. community-1 is better on Earnings-21 (0.10 vs 0.20), which plausibly reflects many-speaker calls beyond the eight-speaker cap (unverified cause). It also has no speaker cap.
- The two harnesses disagree sharply on community-1's AMI scores: OpenBench 0.36 / 0.38 on forced-alignment references vs pyannote's own 17.0 / 19.9. OpenBench also runs community-1 through Argmax's port, not `pyannote.audio`. Reference annotations, collar and port all move DER, so a node-local test on the Principal's own recordings is the deciding evidence.
- Quantisation. NVIDIA's DER figures are BF16 on GPU, and the official Q8_0 GGUF has no published DER. For a 100M model the F32 safetensors (0.37 GiB) cost nothing in the memory budget, so the full-precision path can be kept if Q8_0 shows any drift.

### Gaps
- No real-time factor or DER measurement exists for any diariser on gfx1151, ROCm 10.0 or a Zen 5 CPU. RTF on the node is unresolved.
- OpenBench's collar and overlap settings for its DER table were not stated in the file read, and the commit date of BENCHMARKS.md could not be fetched (GitHub API refused). The run dates of 2026-09-22 come from the file itself.
- It is unknown whether NeMo (nemo-toolkit 3.0.0) installs cleanly against torch 2.13.0+rocm10.0.0. Its CUDA-specific extras may not build, and no source was found either way.
- DiariZen (CC BY-NC weights, earlier notes) and pyannoteAI precision-3 (cloud/on-premise licence) were not re-evaluated; both remain outside a zero-cloud, commercial-clean policy per the earlier notes.
