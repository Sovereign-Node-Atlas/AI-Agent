# Notes: D-60 (EmbeddingGemma 2) and C-12 (medical imaging companion), 2026-10-17

Scope: register rows D-60 (`actions/register_final.json` decisions[59]) and C-12 (companions[11]); doc 5.3 L404, 10.1 L722-726, 15.2 L902, 15.5 L958, 1 L68, 4.1 L254-268. Research only; nothing in the repository was edited.
Downloads are in `rec/dl_comp/`, helper scripts in `rec/tools/`.

## D-60: EmbeddingGemma 2 beside Qwen3-Embedding-4B

### The same boards (vendor or official-results numbers)

| Board | EG2 (768d) | Qwen3-Embedding-4B | Gap | Source |
|---|---|---|---|---|
| MTEB multilingual v2, Mean(Task) | 61.36 | 69.45 | −8.09 | EG2 card L80; Qwen3-Emb-4B card L245 (VERIFIED) |
| MTEB English v2, Mean(Task) | 68.46 | 74.60 | −6.14 | EG2 card L96; Qwen3 card L262 (VERIFIED) |
| MTEB Code v1, Mean(Task) | 78.68 | **80.07** | −1.39 | EG2 card L81 (VERIFIED). Qwen3-4B: my mean over the 12 MTEB(Code, v1) tasks in the official results repo (embeddings-benchmark/results @12eaee4, 2026-10-16), `rec/tools/mteb_code.py`. The same script gives 68.76 for EmbeddingGemma 1, exactly the EG2 card's EG1 figure, so the method matches the card's (VERIFIED computation) |

- The register's "+9.92 on code" is against EmbeddingGemma 1. Against the primary embedder EG2 is lower on all three text boards, code included. So "code side index" adds nothing (INFERRED from the table).
- No EG2 submission exists in the official MTEB results repo as of commit 12eaee4 (2026-10-16). All EG2 scores are Google's own (VERIFIED: `git ls-tree` shows `google__embeddinggemma-300m` only).
- Caveat: the node truncates Qwen3-4B to 1,024 dims (10.1). The published 69.45/74.60/80.07 are at native size, and the truncation loss is UNVERIFIED (T-15).

### What EG2 adds: one shared image, video and audio space

- Card: 740M parameters = a 270M text model + 170M vision + 300M audio, with one shared 768-d space. MIEB lite 64.64; MMEB v2 Image 57.28, VisDoc 67.84, Video 50.67, Overall 59.01; MSEB retrieval 69.54; MAEB 49.39 (card L82-87, L96; VERIFIED). Each image costs 280 tokens, video frames 140 tokens at 1 fps, and audio 25 tokens per second inside an 8,192-token window. Must run in bf16 or fp32, never fp16 (card L189-193; VERIFIED).
- A stronger multimodal embedder sits on the same board: **Qwen3-VL-Embedding** (Apache 2.0, Qwen/Qwen3-VL-Embedding-2B and -8B, created 2026-01-07; card front matter VERIFIED). MMEB-V2: 2B Image 75.0, Video 61.1, VisDoc 80.2, All 73.4; 8B 80.1 / 66.1 / 83.3 / 77.9 (Qwen3-VL-Embedding-8B card, MMEB-V2 table; VERIFIED). Against EG2 (59.01 overall), the 2B is +14.4 overall, +17.7 image, +10.4 video and +12.4 VisDoc. Caveats: both sets are vendor-run, and Qwen re-ran the VisDoc OOD split, so the comparison is approximate (INFERRED). Qwen3-VL-Embedding has **no audio**; audio is the one thing only EG2 offers.
- llama.cpp at b11474 runs both paths:
  - `/v1/embeddings` accepts `{"content":[image_url|input_audio|input_video|text]}` (`tools/server/server-context.cpp:5870-5876`, VERIFIED). It came in with #29556 (680a036, 2026-09-28, first tag b11240, an ancestor of b11474; VERIFIED). Its commit message names Qwen3-VL-Embedding as the target. Video needs the `ffmpeg` binary (`tools/mtmd/CMakeLists.txt:3-10`).
  - EG2: architecture `gemma-embedding2` (`src/llama-arch.cpp:63`; #30054 4fbc76d is an ancestor of b11474, VERIFIED). Its mmproj header is `clip.vision.projector_type = gemma4v` and `clip.audio.projector_type = gemma4a` (my parse of ggml-org mmproj-Q8_0; VERIFIED). Both projector types are in the pin (`tools/mtmd/clip-impl.h:459-460`).
  - Post-pin, one commit touches `gemma-embedding2.cpp`: #30461 (9cf967a, 2026-10-14) drops a redundant expand, with no behavioural change (VERIFIED diff). No EG2 bug fix has landed through b11706 (2026-10-16).
  - The faithfulness of either model on RADV is UNVERIFIED. Each needs a cosine-parity test against the transformers fp32 reference.
- EG2 licence: the front matter says `apache-2.0`. The card's licence link `ai.google.dev/gemma/docs/gemma_4_license` redirects to `ai.google.dev/gemma/apache_2`, which carries the Apache 2.0 text (VERIFIED, fetched 2026-10-17). The card adds "Deployments must adhere to the Gemma Prohibited Use Policy" (card L282). That is a card statement, not a condition of the Apache licence (INFERRED; not legal advice).

### Which ATLAS features would use a multimodal index

- Photos and screenshots on the mounted Google Drive and in Gmail attachments (13). Scanned drawings and site photos for Valerie (domain 8). Art and asset photos (domains 26 and 18). Camera stills for Alaric (14.4, domain 22). Generated images (domain 12).
- Voice notes and meeting audio: their *speech* is already covered better by Whisper transcripts in the Qwen3 text index (INFERRED). Only non-speech sound (sound events, music) would need an audio embedder.
- Cost under single-seat operation (4.1 L264-266): GLM-5.3-Flash has 0.9 to 1.9 GiB to spare and DeepSeek 2.3 to 3.3. Each projection below is the files plus the Arbiter's 0.5 GiB companion compute default (`arbiter.py:198`):
  - EG2 text, Q8_0: 0.79 GiB.
  - EG2 text plus mmproj, Q8_0: 1.31 GiB.
  - EG2 text plus mmproj, BF16: 1.93 GiB.
  - Qwen3-VL-Embedding-2B, Q8_0 plus mmproj Q8_0: 2.62 GiB, plus KV (114,688 B per token, so about 0.88 GiB at 8k).

  Only EG2's text-only query path fits beside GLM, and only at the top of GLM's margin. Every other path evicts the resident seat: on a load that does not fit, the Arbiter's plan evicts LRU for memory (`arbiter.py:961-962`, VERIFIED), and the seat's return costs a 50 to 85 s swap (INFERRED).

### Recommendation D-60: (b) drop

Drop EG2 now and move it to the 15.5 watch-list row the doc already has (L958). It is below the primary embedder on every text board, code included. On image, video and visual documents, Qwen3-VL-Embedding-2B beats it by 10 to 18 points on the same MMEB-V2 board. Its only unique asset is non-speech audio. Nothing is built.

If the Principal later wants search over photos, screenshots, scans or video, raise a new item for **Qwen3-VL-Embedding-8B** (77.9 MMEB-V2; the 2B, at 73.4, if query-time evictions prove too costly). The event: Day 1 complete, T-15 done on the primary index, and a media corpus the Principal wants searchable. Build it as:
- separate per-hemisphere 768-d or native-dimension Chroma collections, never mixed with the Qwen3 text vectors (10.1);
- ingest batched while gpt-oss-120b, or no seat, is resident;
- a cosine-parity test first.

What would change this:
- The Principal wants to search non-speech audio (keep EG2 for audio only).
- Qwen3-VL-Embedding fails parity on the pin while EG2 passes.
- A media search must never evict GLM-5.3-Flash (EG2's text-only query path is the only one that fits its margin).
- An independent MMEB or MTEB run shows EG2 well above its card.

## C-12: medical imaging companion

### The MedGemma family (HF API author=google, 2026-10-17; VERIFIED)

- medgemma-4b-pt and -4b-it (2025-05-19); medgemma-27b-text-it (2025-05-19); medgemma-27b-it, the 27B multimodal (2025-07-09); medgemma-1.5-4b-it (created 2026-01-07, last modified 2026-04-13, sha 9185054). All are gated `auto` under HAI-DEF.
- The MedGemma 1.5 card (last updated 2026-04-21) says it "is only available as a 4B multimodal instruction-tuned variant". There is no 1.5 27B. A web search found no MedGemma 2 (UNVERIFIED negative).
- MedGemma 1.5 4B adds CT and MRI volumes, whole-slide pathology, longitudinal CXR, CXR bounding boxes, lab-report extraction and EHR (card, VERIFIED).

### Imaging, 1.5 4B against MedGemma 1 27B multimodal (the same card table; VERIFIED, vendor-run, * = internal datasets)

| Task | 1.5 4B | 1 27B |
|---|---|---|
| CT Dataset 1*, macro accuracy | 61.1 | 57.8 |
| MRI Dataset 1*, macro accuracy | 64.7 | 57.4 |
| MIMIC-CXR, macro F1 (top 5) | 89.5 | 90.0 |
| CheXpert, macro F1 | 48.2 | 49.9 |
| CXR14, macro F1 | 48.4 | 45.3 |
| PathMCQA*, accuracy | 70.0 | 71.6 |
| WSI-Path*, ROUGE | 49.4 | 4.1 |
| US-DermMCQA*, accuracy | 73.5 | 71.7 |
| EyePACS*, accuracy | 76.8 | 75.3 |
| MS-CXR-T (longitudinal), macro accuracy | 65.7 | 50.1 |
| SLAKE, tokenized F1 / closed accuracy | 59.7 / 82.8 | 70.3 / 85.9 |
| VQA-RAD, tokenized F1 / closed accuracy | 48.1 / 70.2 | 46.7 / 67.1 |
| Chest ImaGenome, IoU | 38.0 | 16.0 |
| MIMIC-CXR report generation, RadGraph F1 | 27.2 | 27.0 |
| MedXpertQA, multimodal | 20.9 | 26.8 |
| MedQA (text) | 69.1 | 85.3 |

- On imaging the 4B leads on 10 rows and trails within 1.7 points on 3 (MIMIC-CXR, CheXpert, PathMCQA). The 27B leads clearly only on SLAKE, which the card's footnote attributes to the Q&A format, and on multimodal reasoning, MedXpertQA (INFERRED from the table).
- The 27B's real advantage is text reasoning (MedQA +16.2). That is the LLM seat's job (GLM-5.3-Flash on Day 1, Baichuan-M3 phased), and MedGemma-27B is already excluded as below the 70B floor (15.5 L967).
- Independent check: an ED study (arXiv 2512.00271, 478 patients, blinded thoracic radiologists, MedGemma version not stated) found MedGemma's sensitivity for common CXR findings at 4.8 to 76.7%. The other VLMs it tested had 16.8 to 43.0% major disagreement and 5.4 to 17.4% hallucination, against 13.9% and 0.1% for radiologists (VERIFIED abstract via WebFetch). Drafts only.
- Card limitations: "not intended to directly inform clinical diagnosis … All outputs … should be considered preliminary". It has been "primarily evaluated on single-image tasks", and it has "not been evaluated or optimized for multi-turn applications" (VERIFIED).

### llama.cpp at b11474 and GGUF availability

- unsloth/medgemma-1.5-4b-it-GGUF (last modified 2026-01-14, not gated, licence HAI-DEF):
  - BF16: 7,767,804,480 B, sha256 a8e48bfc…0085
  - Q8_0: 4,130,403,136 B, sha256 10c7b9a0…0219
  - mmproj-BF16: 851,252,224 B, sha256 9743de36…5a8a

  (HF tree API; VERIFIED.)
- Headers (my parse; VERIFIED): `general.architecture = gemma3`, 34 blocks, 4 KV heads, head dim 256, SWA 1024; `clip.projector_type = gemma3`, 896 px, patch 14. Both are at the pin (`src/llama-arch.cpp:58`, `tools/mtmd/clip-impl.h:456,524`, `tools/mtmd/mtmd.cpp:738`). No post-pin commit touches the gemma3 or siglip vision paths.
- The chat template carries "Unsloth template fixes"; check it against Google's template in the node test.
- CT, MRI and WSI need Google's preprocessing (slice or patch selection) before the images go in. That is orchestrator work (card; the notebooks are linked).

### Memory (INFERRED; KV from the headers, with global layers il % 6 == 5 per `llama-hparams.cpp:15`: 5 of 34 for 4B, 10 of 62 for 27B; f16 KV; 2.0 GiB compute allowance)

| Engine | Weights | mmproj | KV at 32k | Total |
|---|---|---|---|---|
| 1.5 4B BF16 | 7.23 | 0.79 | 0.79 | **10.8 GiB** |
| 1.5 4B Q8_0 | 3.85 | 0.79 | 0.79 | 7.4 GiB |
| 27B multimodal Q8_0 | 26.74 | 0.80 | 3.11 | **32.6 GiB** |
| 27B multimodal BF16 | 50.32 | 0.80 | 3.11 | 56.2 GiB |

- Beside the GLM seat (0.9 to 1.9 spare) or the DeepSeek seat (2.3 to 3.3), none fits. The Arbiter evicts the seat, and the seat's return costs about 50 to 85 s plus re-prefill. That cost is the same for 4B and 27B.
- Beside gpt-oss-120b (about 88 GiB spare) both fit, with no eviction.
- Load: 4B BF16 plus mmproj, 1.4 to 3.6 s cold; 27B Q8_0, 4.7 to 12.3 s, at 2.4 to 6.3 GB/s.
- Decode, bandwidth-bound at 273 GB/s and about 60%: 4B BF16 about 21 tok/s; 27B Q8_0 about 6 tok/s, consistent with the 7.3 to 7.7 tok/s the doc cites for a 27B dense model. A 300-token read takes about 15 s against about 50 s.

### HAI-DEF terms (Terms of Use, last modified 2024-11-15; Prohibited Use Policy, 2024-11-14; both fetched 2026-10-17, VERIFIED)

- 1.1(d): the terms apply to the weights "regardless of the source that you obtained it from", so they bind the unsloth GGUF too. 1.1(h): a quantisation is a Model Derivative. Outputs are not.
- 1.1(a): "Clinical Use" means any use in the diagnosis or treatment of patients, including in a research study. 1.1(b): "Distribution" includes making the functionality available to a third party as a hosted service.
- 3.1: Distribution requires passing on the terms, a Notice file, and "when applicable" seeking Health Regulatory Authorization.
- 3.2: no use under the Prohibited Use Policy; no use that could make Google a medical-device "manufacturer"; no unlawful use. Google may restrict usage.
- Prohibited Use Policy, for ATLAS: no unlicensed practice of medicine; no "automated decisions in domains that affect … healthcare"; no misleading claims of expertise in health; no processing other people's sensitive data without consent.
- 4.3: no warranty, and Google gives no medical advice. 4.5: the Principal indemnifies Google. 4.6: Google may terminate, and all copies must then be deleted. California law.
- Commercial use is not prohibited.
- Rad-DINO is MIT, and its card says "shared for research purposes only … not meant to be used for clinical practice" (microsoft/rad-dino README L20, L27-28; VERIFIED). It covers chest X-ray only and gives embeddings only.

### Recommendation C-12: Rad-DINO plus MedGemma 1.5 4B, as an imaging companion

- Class: an imaging companion, a tool. Section 1 L68 already exempts "the OCR, speech, diarisation, voice and image engines" from the 70B floor. MedGemma is never a routed seat and never answers the Principal directly. It writes a preliminary structured read that Minerva's seat reasons over and labels as a draft.
- Rung: BF16 weights plus mmproj-BF16 (8.62 GB on disk, about 10.8 GiB at 32k), under the less-quantisation rule; it fits because it runs alone or beside gpt-oss-120b. Q8_0 is the fallback if the Vulkan BF16 path is slow or wrong in the node test.
- Placement: a llama-server unit loaded on demand through the Arbiter, never resident. Reads are queued and batched so GLM is evicted at most once per batch, or run while gpt-oss-120b is resident.
- Event: Day 1 complete and the terms accepted. Then a node test (a new T-row):
  - load and decode speed;
  - BF16 against Q8_0 output agreement;
  - the template check;
  - a handful of public de-identified CXR, derm and CT cases, each compared with its reference report.
- Terms the Principal accepts: the HAI-DEF Terms of Use and Prohibited Use Policy (the HF gate on google/medgemma-1.5-4b-it, recorded like pyannote's). With them the Principal accepts:
  - not a medical device and no diagnosis;
  - a human, a licensed clinician for any real decision, decides;
  - no third-party serving;
  - consent for anyone else's images;
  - indemnity and termination.

What would change this:
- The Principal declines HAI-DEF or plans third-party serving (then Rad-DINO alone).
- A MedGemma 2, or a 27B multimodal 1.5, that beats 1.5 4B on imaging.
- An independent study of 1.5 4B showing hallucinated findings at a rate that makes drafts net-harmful.
- The node test failing on Vulkan at both rungs.

## Incidental, outside these items

llama.cpp #30445 (cb55ffe, 2026-10-13, after the pin) says pooled-embedding contexts "still expanded its LM head, so the graph computed n_vocab logits per token". At b11474 that may add CPU work and a host buffer to the Qwen3 embedder (decoder-based). T-15 should measure it; the magnitude is UNVERIFIED.
