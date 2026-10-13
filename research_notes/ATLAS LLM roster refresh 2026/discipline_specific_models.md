# Discipline-specific open-weight models vs large generalists (as of 13 October 2026)

Scope note on sourcing: the session's egress proxy refused huggingface.co, arxiv.org, vals.ai, nature.com, classicdba.com, morphllm.com and reader/archive proxies (HTTP 403 on CONNECT), and the shared web-search budget was exhausted part-way through. Facts below therefore come from (a) web-search excerpts (secondary, quoted as such), and (b) primary GitHub READMEs and data files fetched directly from github.com / raw.githubusercontent.com (Qwen3-Coder, Qwen3.5/3.8, GLM-5, Kimi K3, Kimi K2, Kimi-Dev, gpt-oss, Nemotron, MiniMax-M3, DeepSeek-Math-V2, DeepSeek-Prover-V2, DeepSeek-V3.2-Exp, Goedel-Prover-V2, Lawma, Fin-R1, XuanYuan, Meditron, Baichuan-M3, Seed-Coder, Qwen3-Embedding, SmolLM3, Gemma cookbook, MedGemma, MLEB, BigLaw Bench, the official SWE-bench leaderboard JSON, the Aider polyglot leaderboard YAML, and llama.cpp's `src/llama-arch.cpp`). Hugging Face model cards (licence text, GGUF listings, download counts) could not be opened; licence and GGUF statements that rest only on secondary sources are flagged "unverified". Memory-fit figures are my arithmetic, not sourced.

## 0. Baseline: the large general open-weight models a specialist must beat (October 2026)

### Takeaway
The open-weight frontier in October 2026 is dominated by very large MoE models (Kimi K3 2.8T-A104B, Qwen3.8 2.4T-A95B, DeepSeek V4 Pro 1.6T-A49B, GLM-5.x 744B-A40B, MiniMax-M3 ~428B-A23B), none of which fit in 170 GB at reasonable quantisation; the 100B–400B tier that does fit (GLM-5.3-Flash 320B-A18B, DeepSeek V4 Flash 284B-A13B, Qwen3.5-122B-A10B, Nemotron 3 Super 120B-A12B, gpt-oss-120b) is the realistic "big generalist" comparator for this node.

### Cited Findings
- Kimi K3: 2.8T total / 104B activated parameters, 896 experts (16 selected + 2 shared), 1,048,576-token context, MoonViT-V2 vision encoder; weights released under the custom "Kimi K3 License"; quantisation-aware trained with MXFP4 weights / MXFP8 activations; vendor table (max reasoning): GPQA Diamond 93.5, Terminal-Bench 2.1 88.3, DeepSWE 67.5, Harvey Lab-AA 94.6, τ³-Banking 33.4, SpreadsheetBench 2 34.8, OfficeQA Pro 63.3, versus GLM-5.2 at 82.7 / 46.2 / 91.0 / 26.8 / 28.1 / 41.4 on the same rows — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)
- GLM-5, 5.1, 5.2, 5.3 are all 744B-A40B; GLM-5.3-Flash is 320B-A18B with a new hybrid sparse+linear attention base; GLM-5.2 vendor numbers: Terminal-Bench 2.1 81.0 (vs 62.0 for GLM-5.1; "within a few points of Claude Opus 4.8 (85.0)"), SWE-bench Pro 62.1; GLM-5.3 claims "open-source SOTA on Terminal Bench 3.0 and Agents' Last Exam" and "state of the art on CyberGym for vulnerability discovery" — [GLM-5 README](https://github.com/zai-org/GLM-5)
- GLM-5.2 is listed as MIT per its HF card; GLM-5.3 "uses its own model-specific texts that require separate review", GLM-5.3-Flash is MIT (secondary, unverified) — [kingy.ai roundup](https://kingy.ai/news/best-open-weight-ai-models-in-2026-glm-5-2-vs-deepseek-v4-vs-kimi-k2-6-vs-qwen-vs-mistral/)
- Qwen3.5-397B-A17B released 2026-02-16; Qwen3.5-122B-A10B, 35B-A3B, 27B on 2026-02-24; Qwen3.5-9B/4B/2B/0.8B on 2026-03-02; Qwen3.6-35B-A3B (2026-04-16) and Qwen3.6-27B ("flagship-level coding in a 27B dense model", 2026-04-22); Qwen3.8-2.4T-A95B (2026-08-12) and Qwen3.8-27B (2026-08-14); "201 languages and dialects"; llama.cpp "supports the Qwen3.5 open model series (text & vision)"; licence: "find the license file released with the model weights on Hugging Face" — [Qwen3.5/3.8 README](https://github.com/QwenLM/Qwen3.5)
- Qwen3.5-397B-A17B reported at 76.2% SWE-bench Verified (secondary) — [wavect.io](https://wavect.io/blog/open-weight-llm-comparison-2026/)
- DeepSeek-V4-Pro: 1.6T params / 49B active; DeepSeek-V4-Flash: 284B / 13B active; both MIT, 1M context; a V4.1-Flash at 552B is also listed (secondary, unverified) — [wavect.io](https://wavect.io/blog/open-weight-llm-comparison-2026/)
- gpt-oss-120b: 117B parameters / 5.1B active, Apache 2.0, MoE weights post-trained in MXFP4 so it "run[s] on a single 80GB GPU"; all evals at MXFP4 — [openai/gpt-oss README](https://github.com/openai/gpt-oss)
- MiniMax-M3: "~428B parameters and ~23B activated", native multimodal, 1M context, custom LICENSE file — [MiniMax-M3 README](https://github.com/MiniMax-AI/MiniMax-M3); reported 80.5% SWE-bench Verified and 59% SWE-bench Pro (secondary) — [vals.ai via search](https://www.vals.ai/benchmarks/swebench)
- Nemotron 3 family: Nano 31.6B total / 3.6B active (1M context), Super 120.6B / 12.7B active ("NVFP4 single B200"), Ultra 550B / 55B active; Nemotron 3.5 Lightning 30B-A3B released under OpenMDW-1.1; Nemotron 3 Nano Omni 30B-A3B under the NVIDIA Nemotron Open Model License — [NVIDIA-NeMo/Nemotron README](https://github.com/NVIDIA-NeMo/Nemotron)
- llama.cpp architecture registry (fetched 2026-10-13) includes: `qwen35`, `qwen35moe`, `qwen3next`, `qwen4exp`, `deepseek4`, `deepseek32`, `glm4moe`, `glm-dsa`, `glm5-next`, `kimi-k3`, `kimi-linear`, `gpt-oss`, `minimax-m3`, `nemotron_h_moe`, `gemma4`, `gemma-embedding2`, `lfm2moe`, `smollm3`, `mistral4`, `apertus`, `granitehybrid`, `jina-bert-v3`, `nomic-bert-moe`, `muse-glimmer`, `bailingmoe3` — [llama.cpp src/llama-arch.cpp](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-arch.cpp)

### Inferences
- Rough fit at 170 GB usable (assuming ~0.6 bytes/param at Q4_K_M incl. KV/overhead, ~1.05 at Q8_0): gpt-oss-120b native MXFP4 (~65 GB) fits with room; Qwen3.5-122B-A10B, Nemotron 3 Super 120B, Devstral 2 123B and any 70B dense model fit at Q8; DeepSeek V4 Flash (284B) and GLM-5.3-Flash (320B) fit only around Q4/Q3 (~160–190 GB, borderline); Qwen3.5-397B needs ~Q3; Kimi K3, Qwen3.8-2.4T, DeepSeek V4 Pro, GLM-5.3 (744B) and MiniMax-M3 (428B) do not fit at the "less quantisation" preference.
- The llama.cpp arch registry confirms day-one support for the whole 2026 generalist tier (DeepSeek 4, GLM-5 variants, Kimi K3, Qwen3.5, Gemma 4, Nemotron-H MoE, MiniMax-M3), so GGUF availability is a matter of community quantisation rather than engine support.

### Gaps
- Could not read HF licence files for Qwen3.5/3.6/3.8 or GLM-5.3; Qwen README defers to the HF licence file. Could not get the llama.cpp release tag (GitHub API is session-bound).
- Benchmark numbers for the 100B–400B tier (GLM-5.3-Flash, DeepSeek V4 Flash, Qwen3.5-122B) were not retrievable from primary sources; vendor blog pages (z.ai, qwen.ai) were blocked.

## 1. Law and compliance (Australian focus: construction law, strata, contracts, employment/HR)

### Takeaway
The only 2026 legal specialist is GLM Legal (Z.ai, open-weight, released 2026-10-12), which is the top open-weight model on Vals' LegalBench (~86.2%) but still ~2.4 points below the closed frontier cluster (~88.6%); the older academic specialists (SaulLM 7B/54B/141B, Lawma 8B/70B) are 2024-era and US/EU-centric, and the only Australian-trained open model is a 1.5B GPT-2-class model — so Australian legal work rests on a generalist plus retrieval over Australian sources, where legal-specific embedders (Isaacus Kanon 2, API-only) show measurable but modest gains.

### Cited Findings
- "GLM Legal is an open-weight model from zAI, released October 12, 2026"; Vals lists it at about 86.2% on LegalBench (86.23–86.24%), ranked 14th of 150 overall and the best open-weight score; 7th of 79 on Harvey's Legal Agent Benchmark at 19.17% — [Vals AI GLM Legal page](https://www.vals.ai/models/zai_glm-legal)
- Vals AI: "Zai just debuted a legal specific model: GLM Legal, and it is the new #1 open-weight model on Harvey's Legal Agent Benchmark and LegalBench", "just ahead of Kimi K3"; the post quotes 92.5% of LegalBench tests met (metric differs from the 86.2% page figure) — [Vals AI on X](https://x.com/ValsAI/status/2109678919108690380)
- Vals LegalBench leaderboard (updated 2026-10-01): Claude Fable 5 first at 88.56%, Claude Fable 5.1 and Gemini 4 Argon close behind; "the top models cluster near 88%, so the benchmark barely separates the frontier" — [Vals LegalBench](https://www.vals.ai/benchmarks/legal_bench)
- Legal Research Bench (September 2026 snapshot): Muse Spark 1.3 Max and Claude Opus 5 tied at 55.29%; open-weight DeepSeek V4 Pro 0813 40.87%, GLM-5.2 31.25% — [BenchLM Legal Research Bench](https://benchlm.ai/benchmarks/legalresearchbench)
- Kimi K3 vendor table: Harvey Lab-AA criterion pass rate 94.6 (Kimi K3) vs 93.6 (Claude Fable 5), 91.1 (Claude Opus 4.8), 91.0 (GLM-5.2), scores "cited from Artificial Analysis as of July 23, 2026" — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)
- BigLaw Bench (Harvey) scores "What % of a lawyer-quality work product does the model complete", across transactional and litigation task categories, plus a Workflows set (SPA deal-point extraction) and a Retrieval set; no leaderboard numbers in the repo — [harveyai/biglaw-bench](https://github.com/harveyai/biglaw-bench)
- SaulLM-7B (Mistral-7B base, March 2024) set "a new state-of-the-art on the LegalBench-Instruct benchmark, with an average score of 0.61" (authors built LegalBench-Instruct because original prompts were hard for small open models) — [SaulLM-7B paper](https://arxiv.org/pdf/2403.03883)
- SaulLM-54B and SaulLM-141B (NeurIPS 2024): Equall says both "surpassed similarly sized open and proprietary LLMs on LegalBench, and Saul-141b even outperformed OpenAI's GPT-4 on average" (company-reported); the paper notes they "are slightly weaker at following generic instructions" than Llama3/Mixtral Instruct — [BigDATAwire](https://www.hpcwire.com/bigdatawire/this-just-in/equall-introduces-expanded-saul-family-of-legal-llms-with-54b-and-141b-models/); [NeurIPS 2024 paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/ea3f85a33f9ba072058e3df233cf6cca-Paper-Conference.pdf); [Equall blog](https://blog.equall.com/saul)
- No 2026 SaulLM release or LegalBench result surfaced; all SaulLM sources are 2024 — [search summary of SaulLM sources](https://arxiv.org/html/2403.03883v2)
- Lawma 8B and 70B are "fine-tunes of Llama 3 Instruct" on 260 US Supreme Court / Courts of Appeals classification tasks; mean accuracy: Lawma 70B 81.9, Lawma 8B 80.3, GPT-4 62.9, Llama 3 70B Instruct 58.4; "practitioners may prefer to use Lawma 8B ... with little cost in terms of model performance"; weights on HF (`ricdomolm/lawma-8b`, `ricdomolm/lawma-70b`); authors state Lawma was not evaluated on LegalBench "since our models are specialized to the Supreme Court and Appeals Court data" — [socialfoundations/lawma README](https://github.com/socialfoundations/lawma); [Lawma paper](https://arxiv.org/html/2407.16615)
- Isaacus "Open Australian Legal LLM": 1.5B-parameter GPT-2 XL architecture trained on about 70,000 Australian laws, regulations and judicial decisions from six jurisdictions, Apache 2.0 (aggregator source; no release date given) — [skywork.ai page](https://skywork.ai/blog/models/isaacus-open-australian-legal-llm-free-chat-online-skywork-ai/)
- Isaacus Kanon 2 Embedder "ranks first on the Massive Legal Embedding Benchmark, offering 9% better accuracy for legal information retrieval compared to general-purpose alternatives"; available through the Isaacus API (Isaacus also sponsored MLEB) — [Isaacus docs](https://docs.isaacus.com/capabilities); [Haystack integration](https://haystack.deepset.ai/integrations/isaacus)
- MLEB "contains 10 datasets spanning multiple document types, jurisdictions, areas of law" and the repo holds full per-model results (directory not retrievable here) — [isaacus-dev/mleb](https://github.com/isaacus-dev/mleb); [MLEB paper](https://arxiv.org/pdf/2510.19365)
- Allens AI Australian Law Benchmark (2024): LLM answers on Australian law "can look convincing while being wrong or lacking nuance" — [Allens](https://allens.com.au/insights-news/explore/2024/the-allens-ai-australian-law-benchmark/the-future)
- Lawyers Weekly webcast (17 Feb 2026) "Why LLMs struggle with legal document review" exists; no data in the excerpt — [Lawyers Weekly](https://www.lawyersweekly.com.au/webcasts/why-llms-struggle-with-legal-document-review)
- "A Few Good Clauses" (arXiv 2605.05532, May 2026): the self-hosted domain-trained model Olava Extract "achieved the strongest aggregate performance in the study, with a macro F1 of 0.812 and a micro F1 of 0.842" against five frontier systems on structured contract extraction, with batched extraction cost "78% to 97%" lower — [arXiv 2605.05532](https://arxiv.org/html/2605.05532v1)
- A Substack post claims seven general-purpose models beat a legal company's custom model on its own benchmark (unverified, no primary source) — [Substack](https://majidgolshadi.substack.com/p/is-fine-tuning-llms-still-necessary)

### Inferences
- GLM Legal is almost certainly a post-train of a GLM-5-class base (744B-A40B or 320B-A18B Flash); if it is the 744B base it will not fit the node at acceptable quantisation, and if Flash-based it is borderline (~Q3/Q4). Size, licence and GGUF status must be checked on its HF card.
- Every legal benchmark with open-weight entries (LegalBench, Legal Research Bench, Harvey Lab-AA) shows the generalist open frontier (Kimi K3, DeepSeek V4 Pro, GLM-5.2) within 2–15 points of closed models, while the 2024 specialists (SaulLM, Lawma) were measured against GPT-4-era baselines and have no 2026 comparisons; Lawma's gains are on narrow US annotation tasks, not drafting or advice.
- Nothing found is trained on Australian construction, strata, or employment law; the Australian-specific asset is the Open Australian Legal Corpus (data) rather than a usable model, which favours retrieval over Australian legislation/case law with a general model, optionally a legal embedder.

### Gaps
- GLM Legal parameter count, licence, GGUF/llama.cpp status: HF and z.ai blocked, no GitHub repo found.
- SaulLM-54B/141B licence (7B was MIT at release, per recollection, unverified) and GGUF availability: HF blocked.
- No 2026 Allens AI Australian Law Benchmark results found (search budget exhausted before the query ran).
- Whether Olava Extract has open weights is unknown.

## 2. Finance and accounting (CFO work, capital, audit, banking, fraud and risk modelling)

### Takeaway
Finance specialists are small (Fin-R1 7B, Agentar-Fin-R1 8B/32B, Plutus-8B) or 2024-era 70B Llama-3 fine-tunes (XuanYuan3-70B, XuanYuan-FinX1-Preview, Chinese-market focus), and the one 2026 independent benchmark that scores them (FinReasoning) puts Fin-R1 at 46.9 versus 68.0 for Qwen3-235B; no finance specialist built on a 100B+ 2026 base was found, and no FinanceBench open-model scores were retrievable.

### Cited Findings
- Fin-R1: "Built on Qwen2.5-7B-Instruct", Apache 2.0, SFT + GRPO on ~60k distilled DeepSeek-R1 traces; own benchmark average: DeepSeek-R1 (671B) 78.2, Fin-R1 75.2, Qwen-2.5-32B-Instruct 73.8, DeepSeek-R1-Distill-Qwen-32B 72.4, DeepSeek-R1-Distill-Llama-70B 69.2, Qwen-2.5-7B-Instruct 65.6 (FinQA/ConvFinQA/Ant_Finance/TFNS/Finance-Instruct-500k) — [SUFE-AIFLM-Lab/Fin-R1 README](https://github.com/SUFE-AIFLM-Lab/Fin-R1)
- FinReasoning (arXiv 2603.19254, 2026): Fin-R1 scored 46.9 on average, "well below Qwen3-235B (68.0) and DeepSeek-R1 (67.9), though above Llama-3.1-70B (39.2) and DianJin-R1-7B (34.1)" — [FinReasoning](https://arxiv.org/pdf/2603.19254)
- FinTrust (EMNLP 2025): Fin-R1 "outperforms all the other models in correctness and stability on personal-level fairness, but all models exhibit notable shortcomings on challenging tasks, such as fiduciary alignment"; XuanYuan-Fin-X1-Preview evaluated as "a 70B model fine-tuned from Llama-3.1-70B" and fell behind Fin-R1 on fairness — [FinTrust](https://aclanthology.org/2025.emnlp-main.512.pdf)
- RealFin (arXiv 2602.07096): "As of December 2025, Fin-R1 nearly matched the results of Xuanyuan3-70B" — [RealFin](https://arxiv.org/pdf/2602.07096)
- Agentar-Fin-R1 (Ant Group, July 2025): 8B and 32B built on Qwen3; evaluated on FinEval, FinanceIQ, MATH-500, GPQA-diamond and the authors' Finova agent benchmark; "the 32B version leads on the financial tasks" (self-reported); XuanYuan-FinX1-Preview-70B and Fino1 appear as baselines — [Agentar-Fin-R1](https://arxiv.org/html/2507.16802)
- XuanYuan (Du Xiaoman): XuanYuan3-70B released 2024-09-06 "以LLaMA3-70B模型为底座" (Llama3-70B base; HF id `Duxiaoman-DI/Llama3-XuanYuan3-70B`), claims overall finance performance "媲美GPT4o" (comparable to GPT-4o) on an internal closed evaluation; XuanYuan-FinX1-Preview reasoning model released 2024-12-27, claims to beat GPT-4o and Qwen2.5-72B on FinanceIQ (Chinese CPA/banking/securities certifications) — [Duxiaoman-DI/XuanYuan README](https://github.com/Duxiaoman-DI/XuanYuan)
- Plutus: a Greek financial benchmark plus a fine-tuned Plutus-8B — [The Fin AI Plutus](https://www.thefin.ai/dataset-benchmark/plutus); [Plutus paper](https://arxiv.org/pdf/2502.18772)
- MultiFinBen (mid-2025): "domain-specialized models did best in low-resource languages, such as Greek, where one reached 60.19% against Llama-4's 48.95%" — [MultiFinBen](https://arxiv.org/html/2506.14028v1)
- "Meta-Benchmarks for Financial-Services LLM Evaluation" (arXiv 2607.01740, July 2026) exists; content not retrieved — [arXiv 2607.01740](https://arxiv.org/pdf/2607.01740)
- Generalist agentic finance proxies (vendor table): τ³-Banking Kimi K3 33.4 / Claude Fable 5 26.8 / GPT-5.6 Sol 33.0 / GLM-5.2 26.8; SpreadsheetBench 2: 34.8 / 34.7 / 32.4 / 28.1; OfficeQA Pro: 63.3 / 69.9 / 63.2 / 41.4 — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)

### Inferences
- The only finance specialists above 30B (XuanYuan3-70B, XuanYuan-FinX1-Preview ~70B) predate Qwen3/DeepSeek-R1-era reasoning and were benchmarked against GPT-4o; by December 2025 a 7B model matched them, which implies a 2026 100B+ generalist exceeds them by a wide margin.
- FinReasoning's 21-point gap (7B specialist vs 235B generalist) plus the absence of any 2026 large finance fine-tune is the strongest evidence that CFO/audit/risk work on this node is better served by a large generalist plus a code interpreter (for the arithmetic) and retrieval (for standards/regulations) than by a dedicated finance model.
- Fin-R1's fairness/stability edge in FinTrust is a narrow behavioural win, not a knowledge win.

### Gaps
- No FinanceBench or Vals Finance Agent scores for open models found (the Vals finance query hit the search budget limit).
- Licences for Agentar-Fin-R1 and XuanYuan models unverified (XuanYuan3 is a Llama-3 derivative, so Llama 3 licence terms presumably apply).
- No evidence found on FinGPT, BloombergGPT successors, "Qwen-Fin", or any fraud/AML-specific open model in 2026.

## 3. Software engineering, DevOps, infrastructure, cybersecurity (incl. offensive/defensive, SCADA/industrial)

### Takeaway
Coding is the one discipline where "specialists" are large and current: Qwen3-Coder-Next (80B-A3B, March 2026, official GGUF), Qwen3-Coder-480B-A35B, Devstral 2 (123B) and Qwen3.6-27B/35B-A3B exist, but the top of every agentic-coding board in 2026 is held by the general frontier MoEs (GLM-5.2/5.3, Kimi K3, DeepSeek V4, MiniMax-M3), and on the official SWE-bench Verified data the best open-weight entries are generalists (MiniMax M2.5 75.8, GLM-5 72.8) rather than coder fine-tunes; security specialists are all 7B–8B (Foundation-Sec, WhiteRabbitNeo) and independent 2026 work finds they trail frontier models, while GLM-5.3 claims SOTA on CyberGym.

### Cited Findings
- Official SWE-bench Verified leaderboard data (newest submission 2026-02-26; os_model=true entries, mini-SWE-agent 2.0.0 bash-only unless noted): MiniMax M2.5 (high) 75.8 (2026-02-17), GLM 5 (high) 72.8, Lingxi v1.5 × Kimi K2 71.2 (agent, 2025-10-14), Kimi K2.5 (high) 70.8, DeepSeek V3.2 (high) 70.0, OpenHands + Qwen3-Coder-480B-A35B 69.6, GLM-4.6 68.2, Kimi K2 Thinking 63.4, MiniMax M2 61.0, DeepSeek V3.2 Reasoner 60.0, Devstral Small (2512) 56.4, Qwen3-Coder-480B/A35B 55.4, Devstral (2512) 53.8, OpenHands + Qwen3-Coder-30B-A3B 51.6; overall top is 79.2 (Sonar Foundation Agent + Claude 4.5 Opus) — [swe-bench.github.io leaderboards.json](https://github.com/SWE-bench/swe-bench.github.io)
- Kimi-Dev-72B "achieves 60.4% performance on SWE-bench Verified ... a new state-of-the-art result among open-source models" (2025; Qwen2.5-72B-derived, RL-trained) — [MoonshotAI/Kimi-Dev README](https://github.com/MoonshotAI/Kimi-Dev)
- Qwen3-Coder family: Qwen3-Coder-480B-A35B-Instruct, Qwen3-Coder-30B-A3B-Instruct, and Qwen3-Coder-Next ("built on top of Qwen3-Next-80B-A3B-Base ... hybrid attention and MoE", 256K native context, 1M with YaRN), official `Qwen/Qwen3-Coder-Next-GGUF` and FP8 repos; technical report year 2026 — [QwenLM/Qwen3-Coder README](https://github.com/QwenLM/Qwen3-Coder)
- Qwen3-Coder-Next technical report (2026-03-03): the 80B model "activates only 3B parameters", tested on Terminal-Bench 2.0 and Aider; Aider-Polyglot 66.20 for Coder-Next vs 60.40 for the 480B-A35B model — [arXiv 2603.00729](https://arxiv.org/pdf/2603.00729)
- Qwen3 Coder 480B-A35B Instruct is Apache-2.0 (secondary) — [Kilo](https://kilo.ai/open-source-models)
- Devstral 2 (123B dense) at 72.2% SWE-bench Verified, "fits on a single 96GB card, though tightly"; Devstral Small 2 (24B) 68.0% on a 24GB GPU (vendor numbers via secondary source) — [digitalapplied](https://www.digitalapplied.com/blog/best-open-weight-coding-models-self-host-hardware-match-2026)
- GLM-5.2 vendor: Terminal-Bench 2.1 81.0, SWE-bench Pro 62.1; GLM-5.3 "open-source SOTA on ... Terminal Bench 3.0"; GLM-5.3 "state of the art on CyberGym for vulnerability discovery, and its gains are largest further up the exploitation chain, where it more than doubles GLM-5.2 on exploitation benchmarks" — [GLM-5 README](https://github.com/zai-org/GLM-5)
- Kimi K3 vendor: Terminal-Bench 2.1 88.3 (Kimi Code harness) vs GPT-5.6 Sol 88.8, Claude Fable 5 88.0, GLM-5.2 82.7; DeepSWE 67.5 ("67.3 with the mini-SWE-agent harness"); FrontierSWE 81.2; SWE-Marathon 42.0 — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)
- GLM-5.3 reported as best open-weight on Terminal-Bench 4.0 at 41.8% (secondary) — [slash-digital](https://slash-digital.io/en/insights/open-weight-models-2026/); GLM-5.2 at 81.0 on Terminal-Bench 2.1 and Kimi K3 88.3 (secondary) — [Developers Digest](https://www.developersdigest.tech/blog/glm-5-2-vs-deepseek-v4-vs-qwen3-open-weights-coding-showdown)
- SWE-bench Verified disagreement: Vals reports "DeepSeek V4 Pro 0813 is second overall at 96.40%" and Kimi K3 93.4% (its harness; DataCamp says Vals archived the benchmark as saturated), while llm-stats lists "DeepSeek-V4-Pro-Max ... 0.806 (rank #9)" and Kimi K3 at 76.8% — [Vals SWE-bench](https://www.vals.ai/benchmarks/swebench); [llm-stats](https://llm-stats.com/benchmarks/swe-bench-verified); [DataCamp](https://www.datacamp.com/blog/best-llm-for-coding)
- Official Aider polyglot leaderboard data (last entry 2025-10-03, stale): gpt-5 (high) 88.0; best open: DeepSeek-V3.2-Exp Reasoner 74.2, DeepSeek R1 (0528) 71.4, Qwen3-235B-A22B 59.6, Kimi K2 59.1, gpt-oss-120b (high) 41.8, Qwen3-32B 40.0, Qwen2.5-Coder-32B 16.4 — [Aider polyglot_leaderboard.yml](https://github.com/Aider-AI/aider)
- Seed-Coder: 8B base/instruct/reasoning (May 2025), MIT, "state-of-the-art performance among open-source models at the 8B scale" — [ByteDance-Seed/Seed-Coder](https://github.com/ByteDance-Seed/Seed-Coder)
- Meta's CWM, "an open-weights LLM for research on code generation with world models" (Oct 2025) — [arXiv 2510.02387](https://arxiv.org/pdf/2510.02387); a 2026 study ran five open-weight coding models on one GH200 for a React Native app (content not retrievable) — [arXiv 2604.17187](https://arxiv.org/pdf/2604.17187)
- Foundation-Sec-8B (Cisco): Llama 3.1 8B continued-pretrained on a cybersecurity corpus; authors report it "matches Llama 3.1-70B and GPT-4o-mini on some security tasks" (self-reported) — [arXiv 2504.21039](https://arxiv.org/pdf/2504.21039); Foundation-sec-8B-Reasoning is "open-weight, downloadable from Hugging Face, and usable on-prem or in air-gapped labs without a commercial agreement" — [Cisco blog](https://blogs.cisco.com/security/foundation-sec-8b-reasoning-first-open-weight-security-reasoning-model); dated 29 Jan 2026 by a secondary source — [evoailabs](https://evoailabs.medium.com/cybersecurity-llms-new-locally-deployable-models-for-agentic-ai-54908c3f621b); its technical report compares against 18 baselines including gpt-oss-20b/120b (scores not retrieved) — [arXiv 2601.21051](https://arxiv.org/pdf/2601.21051)
- KTH thesis (2026): "fine-tuned 8B open-weight models still trail frontier models like GPT-5 and Claude-4.5 on cybersecurity benchmarks" — [KTH DiVA](https://kth.diva-portal.org/smash/get/diva2:2060365/FULLTEXT01.pdf)
- WhiteRabbitNeo: Llama 3.1 8B and 70B uncensored offensive-security fine-tunes; V3 is "a 7B open-weight model fine-tuned for DevSecOps" (vendor) — [Kindo](https://www.kindo.ai/blog/introducing-whiterabbitneo-v3-the-next-generation-of-devsecops-ai); the 33B DeepSeek-Coder fine-tune has "no benchmarks of its own" — [AdversariaLLM](https://adversariallm.ai/research/whiterabbitneo-33b)
- CyberCertBench (2026): "Large proprietary models such as Gemini 2.5 Pro, Claude 3.5 Sonnet, and OpenAI GPT 5 consistently achieved the highest scores, while top-tier open-weight models, like Moonshot AI's Kimi K2, demonstrate highly competitive performance" — [arXiv 2604.20389](https://arxiv.org/html/2604.20389v1)
- CTI-REALM (agentic detection engineering): Claude Opus 4.6 (High) top at 0.637 — [arXiv 2603.13517](https://arxiv.org/pdf/2603.13517); "Benchmark Scores Are Pipeline-Dependent: A Reliability Audit of Cybersecurity LLM Benchmarks" (Sept 2026) — [arXiv 2609.08765](https://arxiv.org/pdf/2609.08765); CAIBench meta-benchmark — [arXiv 2510.24317](https://arxiv.org/pdf/2510.24317); ExploitBench capability ladder — [arXiv 2605.14153](https://arxiv.org/pdf/2605.14153)
- Security document classification (2026): a fine-tuned local model beat a properly prompted base model "by about 8.7 percentage points at the category level", attributed largely to schema adherence — [arXiv 2605.20368](https://arxiv.org/pdf/2605.20368)
- A June 2026 practitioner guide on open-weight LLMs for cybersecurity/VAPT exists (page blocked) — [classicdba](https://classicdba.com/security-grc/2026-06-20/open-weight-llms-for-cybersecurity-and-vapt-local-models-and-a-safe-testing-agent)

### Inferences
- Within the 170 GB envelope the strongest coding options with evidence are Qwen3-Coder-Next (80B-A3B, ~85 GB at Q8, official GGUF), Devstral 2 (123B dense, ~125 GB at Q8, 72.2% vendor-reported), Qwen3.6-27B, and the generalists GLM-5.3-Flash / DeepSeek V4 Flash / Qwen3.5-122B at Q4; the official leaderboard's harness-controlled numbers (Devstral 53.8, Qwen3-Coder-480B 55.4) are 15–20 points below vendor claims, so vendor SWE-bench figures should be discounted uniformly.
- Coder "specialists" in 2026 are themselves 80B–480B MoEs trained by the same labs as the generalists; the specialist-vs-generalist distinction is mostly about agentic post-training and harness, not domain pretraining.
- For security, every open specialist is ≤70B and most are 7–8B; GLM-5.3's cyber claims and Kimi K2's CyberCertBench standing suggest a large generalist is the better security knowledge source, with the 8B Foundation-Sec reasoning model useful as a cheap offline classifier/CTI helper rather than a replacement.
- No SCADA/ICS-specific open model surfaced in any result.

### Gaps
- Terminal-Bench 2.0/2.1/3.0/4.0 leaderboard pages (tbench.ai) could not be fetched; versions are not comparable across sources.
- SWE-bench Pro (morphllm Sept 2026) and the Qwen3-Coder-Next full results table were blocked.
- Devstral 2 licence (Apache 2.0 per my recollection of the Dec 2025 announcement) unverified; GGUF availability for Devstral 2, Qwen3-Coder-480B, GLM-5.3-Flash not checked (HF blocked).
- Foundation-Sec-8B-Reasoning numeric comparison vs gpt-oss-120b not retrieved; Lily-Cyber and Trend Micro Cybertron returned no results before the search budget ran out.

## 4. Data science and AI engineering (rapid data analysis, statistics)

### Takeaway
No data-science-specific open model exists in 2026; the relevant benchmarks (DABstep, DSBench, BIRD-SQL, Argo-Bench, SpreadsheetBench 2) are led by agentic generalists with code execution, and the only domain-trained entries are small text-to-SQL fine-tunes on BIRD.

### Cited Findings
- DABstep: "the best agent on the hardest tasks reached only about 14.55% accuracy, with the top entry being o4-mini" (Q1 2025 baselines) — [DABstep](https://arxiv.org/html/2506.23719v1); DS-STAR (2026) evaluates a Gemini-2.5-Pro-based agent on DABstep — [DS-STAR](https://arxiv.org/pdf/2509.21825)
- DSBench: "540 real-world tasks drawn from ModelOff and Kaggle competitions" — [DeepAnalyze](https://arxiv.org/pdf/2510.16872)
- BIRD-SQL: top single-model test scores "in the high-70s to low-80s, against a human baseline of about 92.96%"; "several entries are fine-tuned open models, such as a 4B and a 31B model"; BIRD-Critic-SQLite and RL-trained BIRD-Talon models released March 2026 — [BIRD-bench](https://bird-bench.github.io/); a 2026 paper cites a top BIRD entry at 82.4% vs human 93.0% — [Argo-Bench](https://arxiv.org/pdf/2610.02122)
- Generalist spreadsheet/office proxies: SpreadsheetBench 2 Kimi K3 34.8 / Claude Fable 5 34.7 / GPT-5.6 Sol 32.4 / GLM-5.2 28.1; OfficeQA Pro 63.3 / 69.9 / 63.2 / 41.4 — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)
- Qwen3-Embedding provides "robust multilingual, cross-lingual, and code retrieval capabilities" (MTEB-Code reranker 81.22 for Qwen3-Reranker-8B) — [Qwen3-Embedding README](https://github.com/QwenLM/Qwen3-Embedding)

### Inferences
- The evidence supports a general coding-strong model plus a sandboxed Python/SQL interpreter for this discipline; a BIRD-style SQL fine-tune (4B–31B) is the only specialist pattern with measured value, and only for NL-to-SQL.

### Gaps
- No 2026 leaderboard scoring open models across DABstep/DSBench/BIRD together was found; DABstep figures are 2025.

## 5. Medicine and longevity (private health, medical records, risk underwriting)

### Takeaway
Medicine is the discipline with the strongest 2026 specialist evidence, but it cuts both ways: Baichuan-M3-235B (Qwen3 base, Apache 2.0, Feb 2026) reports HealthBench 65.1 / HealthBench-Hard 44.4, beating GPT-5.2-High (42.0 Hard) and far above gpt-oss-120b (57.6 / 30.0), whereas the 70B Llama-family specialists (Meditron-70B, the fully open Apertus-70B-MeditronFO at 53.77 average vs MedGemma-27B 60.67) underperform, and a June 2026 Nature Medicine study found frontier generalists beat specialised clinical AI tools on every evaluation — so a dedicated medical model is only worth its memory if it is Baichuan-M3-class, not Meditron-70B.

### Cited Findings
- Baichuan-M3-235B: Apache 2.0 ("支持研究和商业使用"), base model Qwen3, HealthBench-Hard 44.4 ("提升 28 个百分点" over Baichuan-M2, "超越 GPT-5.2", first on the overall HealthBench leaderboard), lower hallucination rate than GPT-5.2 without tools, first on all three SCAN-bench stations; official GPTQ-INT4 repo; served with SGLang/vLLM (`--reasoning-parser qwen3`), example on 8×H20 96 GB; no GGUF/llama.cpp mention — [baichuan-inc/Baichuan-M3-235B README](https://github.com/baichuan-inc/Baichuan-M3-235B)
- Baichuan-M3 paper (arXiv 2602.06570): HealthBench total 65.1, HealthBench-Hard 44.4 vs GPT-5.2-High 42.0, hallucination rate 3.5% — [arXiv 2602.06570](https://arxiv.org/abs/2602.06570); [neurohive summary](https://neurohive.io/en/state-of-the-art/baichuan-m3-an-open-medical-model-that-conducts-consultations-like-a-real-doctor-and-outperforms-gpt-5-2-on-benchmarks/)
- gpt-oss-120b (high): HealthBench 57.6%, HealthBench Hard 30.0%, HealthBench Consensus 89.9%; "nearly matches OpenAI o3 performance on HealthBench and HealthBench Hard" — [gpt-oss model card](https://arxiv.org/pdf/2508.10925); [intuitionlabs summary](https://intuitionlabs.ai/articles/openai-gpt-oss-open-weight-models)
- "The choice of evaluation model can materially affect the scores measured on open-ended benchmarks, such as HealthBench" — [InfiMed-ORBIT](https://arxiv.org/pdf/2510.15859)
- MedGemma: "a collection of Gemma 3 variants", "4B multimodal version and a 27B text-only version", model licensed under the Health AI Developer Foundations License (repo code Apache 2.0) — [Google-Health/medgemma README](https://github.com/Google-Health/medgemma); Nature paper: MedQA 86.2 "best for an open model at release, aside from DeepSeek-R1" (91.0); OpenBioLLM 70B 78.2 next — [Nature Medicine MedGemma paper](https://www.nature.com/articles/s41591-026-04626-w); technical report revised 2026-04-08 — [arXiv 2507.05201](https://arxiv.org/pdf/2507.05201); Google blog — [research.google](https://research.google/blog/medgemma-our-most-capable-open-models-for-health-ai-development/)
- MedGemma 1.5: conflicting secondary figures ("roughly 91% on MedQA" vs "MedQA went from 64% to 69% for the update, with the bigger gains on extraction tasks") — [knack.com](https://www.knack.com/blog/healthcare-llm-2/); [nirmitee](https://nirmitee.io/blog/healthcare-llm-landscape-2026-medgemma-meditron-clinical-model-guide/)
- Meditron-70B: continued-pretrained from Llama-2-70B, Llama 2 Community License, knowledge cutoff August 2023, "foundation models without finetuning or instruction-tuning" — [epfLLM/meditron README](https://github.com/epfLLM/meditron); independent test: 52.9% on an Israeli neurology board exam, "the lowest among the 70B models evaluated" — [search summary](https://www.micheledpierri.com/2026/07/14/open-weight-medical-llms/)
- Fully Open Meditron (arXiv 2605.16215, May 2026) introduces Apertus-MeditronFO; Apertus-70B-MeditronFO "is the strongest fully open medical model at 53.77 average across benchmarks, below MedGemma-27B at 60.67" — [arXiv 2605.16215](https://arxiv.org/html/2605.16215); [micheledpierri July 2026](https://www.micheledpierri.com/2026/07/14/open-weight-medical-llms/)
- II-Medical-32B-Preview (July 2025): fine-tuned from Qwen3-32B, "average score of 71.54% across 10 medical QA benchmarks" (incl. HealthBench) — [featherless II-Medical readme](https://featherless.ai/models/Intelligent-Internet/II-Medical-32B-Preview/readme)
- Lingshu (Alibaba DAMO, June 2025): multimodal medical model (7B/32B) that "outperforms proprietary models like GPT-4.1 and Claude Sonnet 4 in most medical multimodal QA and report generation tasks" (authors); no HealthBench result — [Lingshu paper](https://arxiv.org/abs/2506.07044v1)
- Med42-v2 (2024): 70B models "exceed the performance of other leading general-purpose and domain-specific models" (authors, 2024) — [Med42-v2](https://arxiv.org/html/2408.06142v1); OpenBioLLM-70B MedQA 78.16 (model card via blog) — [knack.com](https://www.knack.com/blog/healthcare-llm-2/)
- Nature Medicine brief communication (June 2026): two clinical AI tools vs GPT-5.2, Gemini 3.1 Pro and Claude Opus 4.6; "Frontier LLMs outperformed clinical AI tools in all three evaluations"; authors suggest "scale and cross-domain reasoning may matter more than domain tuning for some medical tasks" — [Nature Medicine](https://www.nature.com/articles/s41591-026-04431-5); [ResearchGate copy](https://www.researchgate.net/publication/406992335_General-purpose_large_language_models_outperform_specialized_clinical_AI_tools_on_medical_benchmarks)
- "A collective capability boundary in frontier large language models on guideline-conformant and case-specific oncology decision-making" (Aug 2026) — [arXiv 2608.28592](https://arxiv.org/pdf/2608.28592)

### Inferences
- Baichuan-M3-235B at Q4 (~130 GB) fits the node; at Q8 (~240 GB) it does not. Its base is Qwen3-235B-A22B (arch `qwen3moe`, supported by llama.cpp), so community GGUFs are plausible but were not verified.
- The planned Meditron-70B Q8 cross-check (~74 GB) is the weakest-evidenced choice in the plan: a 2023 Llama-2 base, no instruction tuning, and 2026 independent results below MedGemma-27B; MedGemma-27B (~28 GB at Q8) or II-Medical-32B would be cheaper cross-checks, and Baichuan-M3 the only specialist with evidence of beating 2026 frontier models.
- The HealthBench gap between Baichuan-M3 (65.1) and gpt-oss-120b (57.6) is larger than typical judge noise, but it is vendor-run and the judge model matters, so an independent run is needed before treating the 14-point Hard-subset gap as real.

### Gaps
- No "MedGemma 2" or "Meditron 4" found in any result; MedGemma 1.5 MedQA numbers conflict and the model card was blocked.
- HealthBench scores for Qwen3.5, DeepSeek V4, GLM-5, Kimi K3 were not found.
- Baichuan-M3 release date (paper is Feb 2026), languages (README is Chinese/English), and GGUF status unverified.

## 6. Communications, marketing, PR, brand, crisis writing; customer success

### Takeaway
No credible open-weight specialist for marketing, PR or customer success surfaced; writing quality is measured by general creative-writing leaderboards (section 9) and commercial listicles, which in October 2026 rank closed models first and GLM-5.3 / Kimi K3 as the best open writers.

### Cited Findings
- evy.so (updated 12 Oct 2026) lists GPT-6 Astra as overall writing leader and GLM-5.3 (Zhipu AI) as "best value"; no open-only ranking — [evy.so](https://evy.so/compare/best-llms-for-writing/)
- llmreference (June 2026) names Claude Opus 4.7 its marketing-copy pick; its "free option" slot for open-weight marketing drafts is "still marked as pending" — [llmreference](https://www.llmreference.com/best/marketing)
- 2026 roundups describe brand-voice fine-tuning as a feature of commercial platforms, not open models — [searchatlas](https://futureinsights.labs.searchatlas.com/best-ai-writing-tools-2026-comparison/)
- Kimi K3 agentic knowledge-work proxies (vendor, Elo): GDPval-AA v2 1686 vs Claude Fable 5 1747 / GPT-5.6 Sol 1736 / GLM-5.2 1510; AA-Briefcase 1548 vs 1583 / 1495 / 1260 — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)

### Inferences
- This discipline has no specialist; the choice reduces to the best general writer that fits memory plus style/brand retrieval or a small LoRA for voice (the "fine-tuning for form" pattern in section 12).

### Gaps
- No benchmark specific to PR/crisis communications or customer-success chat was found.

## 7. Architecture/engineering/construction, property development, computational design, industrial and energy engineering

### Takeaway
No named open-weight AEC or industrial-engineering model exists; 2026 academic work (a BIM-design benchmark with a fine-tuning strategy, IFC-Copilot, compliance-checking agents) reports that general LLMs still perform poorly on BIM tasks but publishes methods and data rather than usable weights.

### Cited Findings
- A 2026 paper "proposes an evaluation benchmark with quantitative indicators, a method for generating textual data from BIM for evaluation and fine-tuning, and a fine-tuning strategy to adapt LLMs for BIM-based design", reporting "general LLMs still perform poorly on these domain tasks" — [arXiv 2602.20812](https://arxiv.org/abs/2602.20812v1)
- Prior work fine-tuned LLMs to fill missing thermal attributes in BIM models and to convert building descriptions into EnergyPlus files; compliance checking combines LLMs, deep learning and ontologies — [arXiv 2508.15820](https://arxiv.org/pdf/2508.15820)
- EG-ICE 2026 programme lists "IFC-Copilot for LLM-driven IFC building design" and an LLMs-for-construction-informatics session — [EG-ICE 2026](https://www.dicea.unipd.it/eg-ice-2026-padua/programme); TUM lists a 2026 review of LLMs for building applications and a 2026 paper on LLM agents for compliance checking — [TUM CCBE publications](https://www.cee.ed.tum.de/en/ccbe/publications/)
- Kimi K3 vendor claims long-horizon coding "from GPU kernel optimization and compiler development to vision-in-the-loop game dev, CAD, and even chip design" — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)

### Inferences
- Computational design and energy engineering reduce to code + math capability; the evidence points to a strong general coder with tool access (IFC/EnergyPlus libraries) and retrieval over standards, not a specialist.

### Gaps
- No open model card or weights for any AEC fine-tune were found; HF search (which might show community BIM LoRAs) was blocked.

## 8. Aviation, transport, logistics, concierge; estate security and threat analysis; family/household management

### Takeaway
Nothing found: no open-weight specialist for aviation, logistics, concierge, physical-security threat analysis or household management surfaced, and the only related sources are vendor marketing pages.

### Cited Findings
- An aviation-marketing article covers ChatGPT campaign use only (2025) — [kommunicate](https://www.kommunicate.io/blog/chatgpt-in-aviation-marketing/)
- Search for "aviation logistics domain model 2026" returned no fine-tuned open model; results were general copywriting listicles — [search summary via llmreference](https://www.llmreference.com/best/marketing)
- Threat-intelligence agent benchmarks (cyber, not physical): CTI-REALM top Claude Opus 4.6 at 0.637 — [arXiv 2603.13517](https://arxiv.org/pdf/2603.13517)

### Inferences
- These are tool-use and planning tasks; the τ³-Banking / APEX-Agents style agentic scores of generalists (Kimi K3 41.0 vs Claude Fable 5 43.3 on APEX-Agents, vendor table) are the closest proxies — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3).

### Gaps
- Only one broad search ran for this group before the budget was exhausted; absence of evidence is weak evidence here.

## 9. Game design and systems design; creative writing

### Takeaway
No game-design specialist exists; for creative writing the 2026 EQ-Bench v3 board (judge switched to Claude Sonnet 4.6 on 2026-03-01) ranks Kimi K3 and GLM-5.3 as the best open-weight writers (~2080 Elo, ranks 5–6 overall), with a 30B creative specialist (Muse Glimmer-30B, llama.cpp-supported) far behind at ~1798.

### Cited Findings
- EQ-Bench: Creative Writing v3 Elo scoring "moved to Claude Sonnet 4.6 as judge on 2026-03-01"; rubric scoring uses claude-sonnet-4 — [eqbench.com/about](https://eqbench.com/about.html); the open benchmark repo recommends `claude-sonnet-4-6` for leaderboard parity and distributes `creative_bench_runs.zip` / `elo_results.zip` — [EQ-bench/creative-writing-bench](https://github.com/EQ-bench/creative-writing-bench)
- Sept 2026 snapshot: Kimi K3 2082 Elo (#5, best open-weight), GLM-5.3 2075 (#6), Muse Glimmer-30B 1798 (#23), GLM-5.2 1757 (#25) — [llmlearner](https://llmlearner.com/rankings/creative-writing)
- Featherless (Sept 2026): leading open model Kimi K2.6 at 16.67 (rubric scale), K3 unscored there — [Featherless](https://featherless.ai/blog/best-ai-for-creative-writing-2026)
- Open Writing Leaderboard (stale snapshot): qwen/qwen3.5-27b #1 at Elo 1948 — [owl.eqbench.com](https://owl.eqbench.com/); Longform: Claude Opus 5 86.3 top — [Sophon](https://sophon.at/evals/eqbench-longform-creative-writing)
- `muse-glimmer` is a registered llama.cpp architecture — [llama.cpp src/llama-arch.cpp](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-arch.cpp)

### Inferences
- The ~280-Elo gap between the 30B creative specialist and the open generalist leaders mirrors the pattern elsewhere: small specialists lose to 100B+ generalists; Qwen3.5-27B's OWL placing suggests a 27B dense generalist is the best creative writer that fits comfortably at Q8.

### Gaps
- eqbench.com live board was not fetched; aggregator snapshots disagree on order and scale. No game-design benchmark found.

## 10. Routing/classification small models and embedding/reranker models

### Takeaway
For the resident 4B router the 2026 candidates with primary-source backing are Qwen3.5-4B (2026-03-02, 262K context, multimodal, llama.cpp-supported), Gemma 4 E4B (April 2026), Granite 4.1 3B/8B, SmolLM3-3B and LFM2.5; no Phi-5 exists in any result. For retrieval, Qwen3-Embedding-8B/4B and Qwen3-Reranker-8B/4B remain the strongest open models with published MTEB numbers (multilingual 70.58 for 8B; reranker MMTEB-R 72.94 vs 58.36 for bge-reranker-v2-m3), though a Sept 2026 comparison places Qwen3-Embedding 4th–5th behind newer entrants.

### Cited Findings
- Qwen3.5-9B/4B/2B/0.8B released 2026-03-02; Qwen3.6-35B-A3B 2026-04-16; llama.cpp supports Qwen3.5 text & vision — [Qwen3.5 README](https://github.com/QwenLM/Qwen3.5)
- Qwen3.5-4B: Apache 2.0, native context 262,144 tokens, text/image/video input, thinking on by default; ~183 tok/s on an RTX 4090 vs ~263 for Phi-4-mini; 3.4 GB download — [tinyweights](https://tinyweights.dev/posts/best-small-language-models-2026/); [tech-insider](https://tech-insider.org/gemma-4-vs-phi-4-mini-vs-qwen3-5-2026/); [pinggy](https://pinggy.io/blog/small_llms_that_fit_in_8gb_memory/)
- Gemma 4: "available in E2B, E4B, 26B A4B, and 31B parameter sizes", text/image/video/audio input; edge variants April 2026; 128K (edge) / 256K context; Apache 2.0 (secondary) — [gemma-cookbook README](https://github.com/google-gemini/gemma-cookbook); [tech-insider](https://tech-insider.org/gemma-4-vs-phi-4-mini-vs-qwen3-5-2026/); `gemma4` arch in llama.cpp — [llama-arch.cpp](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-arch.cpp)
- Phi: latest found is Phi-4-mini (3.8B, MIT, 128K); no "Phi-5 mini" in any result — [localaimaster](https://localaimaster.com/blog/small-language-models-guide-2026)
- Granite 4.1: 3B and 8B, 128K context, Apache 2.0; "Granite 4.1 8B strong at coding and tool calling" — [aicassindra SLM landscape](https://aicassindra.com/blogs/slm/slm_landscape_2026.html)
- LFM2.5: 1.2B dense and 8.3B MoE with 1.5B active, Pythonic tool-call format — [turingpost](https://www.turingpost.com/p/slmslist); `lfm2`/`lfm2moe` in llama.cpp — [llama-arch.cpp](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-arch.cpp)
- SmolLM3: 3B, 11T tokens, Apache 2.0, "Multilingual support for 6 languages: English, French, Spanish, German, Italian, and Portuguese", reasoning/non-reasoning modes — [huggingface/smollm README](https://github.com/huggingface/smollm)
- Nemotron 3 Nano: 31.6B total / 3.6B active, 1M context; one source lists a separate 4B Nano; described as meant for "summarization, retrieval, classification, and general assistant workflows" — [NVIDIA-NeMo/Nemotron](https://github.com/NVIDIA-NeMo/Nemotron); [deepinfra](https://deepinfra.com/blog/nemotron-3-nano-nvidia-efficient-small-llm); [Nemotron 3 Nano paper](https://arxiv.org/pdf/2512.20848)
- Qwen3-Embedding (0.6B/4B/8B) and Qwen3-Reranker (0.6B/4B/8B), 32K context, "over 100 languages"; MTEB multilingual mean(task): 8B 70.58 (#1 as of 5 June 2025), 4B 69.45, 0.6B 64.33, vs BGE-M3 59.56, multilingual-e5-large-instruct 63.22, gemini-embedding-exp 68.37; MTEB Eng v2: 8B 75.22, 4B 74.60; C-MTEB 8B 73.84; reranker MMTEB-R: Qwen3-Reranker-8B 72.94, 4B 72.74, 0.6B 66.36 vs BGE-reranker-v2-m3 58.36, jina-multilingual-reranker-v2-base 63.73; instructions add 1–5% — [QwenLM/Qwen3-Embedding README](https://github.com/QwenLM/Qwen3-Embedding); [Qwen3 Embedding paper](https://arxiv.org/pdf/2506.05176)
- January 2026 snapshot: Qwen3-Embedding-8B first at 70.6; September 2026 comparison: Qwen3-Embedding 5th on MTEB English v2 and 4th on Multilingual v2; EmbeddingGemma-300M 34th/31st; jina-embeddings-v5-text-small 15th/14th; no confirmed MTEB score for jina-embeddings-v4 — [ailog MTEB 2026](https://app.ailog.fr/en/blog/news/rag-benchmark-mteb-2026); [presenc](https://presenc.ai/research/best-open-weight-embedding-models-2026); [maximem](https://www.maximem.ai/blog/best-embedding-models-2026)
- Qwen3-VL-Embedding-8B: 67.9 MMTEB mean task score (Dec 2025 snapshot); Qwen3-VL-Reranker beats baseline rerankers (Jan 2026) — [arXiv 2601.04720](https://arxiv.org/pdf/2601.04720)
- Legal retrieval: Kanon 2 Embedder #1 on MLEB with "9% better accuracy" than general-purpose embedders (API, Isaacus-sponsored benchmark) — [Isaacus docs](https://docs.isaacus.com/capabilities); [MLEB](https://arxiv.org/pdf/2510.19365)
- llama.cpp embedding-side architectures include `jina-bert-v3`, `gemma-embedding`, `gemma-embedding2`, `nomic-bert`, `nomic-bert-moe`, `bert`, `modern-bert`, `llama-embed` — [llama-arch.cpp](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-arch.cpp)

### Inferences
- Qwen3.5-4B is the only 4B router candidate with both a 2026 release date from the vendor and explicit llama.cpp support in the vendor README; Gemma 4 E4B is the multimodal alternative; Nemotron 3 Nano is a 30B-class MoE, not a 4B, so it exceeds a "resident 4B" budget in weights even though it is cheap per token.
- Qwen3-Embedding-8B (~8 GB at Q8) plus Qwen3-Reranker-4B/8B is the best-evidenced open retrieval pair for a multilingual node; newer 2026 embedders that outrank it on MTEB were not identifiable from the excerpts.

### Gaps
- Live MTEB leaderboard (HF space) blocked; the Sept 2026 top-4 names above Qwen3-Embedding are unknown.
- No 2026 publication of Qwen3.5-era embedding models found; jina v4/v5 licences unverified (jina v4 was CC-BY-NC in 2025, per recollection, unverified).

## 11. Mathematics and scientific reasoning (for engineering/finance checks)

### Takeaway
Open math specialists are formal-proof systems (DeepSeekMath-V2 on a 671B-class base with IMO-gold-level results; DeepSeek-Prover-V2 7B/671B; Goedel-Prover-V2 8B/32B, Apache 2.0, matching the 671B prover on miniF2F), which are strong for verifiable proofs but are not evidence of better numeric engineering/finance checking than a 2026 generalist (Kimi K3 GPQA Diamond 93.5; DeepSeek-V3.2-Exp AIME 2025 88–89).

### Cited Findings
- DeepSeekMath-V2 "is built on top of DeepSeek-V3.2-Exp-Base", "achieving gold-level scores on IMO 2025 and CMO 2024 and a near-perfect 118/120 on Putnam 2024 with scaled test-time compute"; README badge Apache 2.0 but "use ... is subject to the Model License" — [deepseek-ai/DeepSeek-Math-V2](https://github.com/deepseek-ai/DeepSeek-Math-V2); press: solved 5 of 6 IMO 2025 problems, weights on HF — [SCMP](https://www.scmp.com/tech/tech-trends/article/3334553/deepseek-releases-first-open-ai-model-gold-level-scores-maths-olympiad); IMO-ProofBench Basic near 99%, Advanced "slightly inferior to Gemini Deep Think" (~61.9% informal) — [implicator](https://www.implicator.ai/deepseeks-math-model-beats-humans-at-their-own-game-then-gives-away-the-playbook/)
- DeepSeek-V3.2-Exp: 671B config, MIT, DeepSeek Sparse Attention; AIME 2025 88.4/89.3, HMMT 2025 86.1/83.6 — [DeepSeek-V3.2-Exp README](https://github.com/deepseek-ai/DeepSeek-V3.2-Exp)
- DeepSeek-Prover-V2: 7B and 671B; the 671B "reaching 88.9% pass ratio on the MiniF2F-test and solving 49 out of 658 problems from PutnamBench"; model licence is DeepSeek's Model Agreement — [deepseek-ai/DeepSeek-Prover-V2](https://github.com/deepseek-ai/DeepSeek-Prover-V2)
- Goedel-Prover-V2: Apache 2.0; "Goedel-Prover-V2-8B, reaches 84.6% on MiniF2F test set at Pass@32, matching ... DeepSeek-Prover-V2-671B while being nearly 100 times smaller"; 32B "88.0% on MiniF2F at Pass@32 ... 90.4% in self-correction mode"; PutnamBench top rank (86 at Pass@192) — [Goedel-LM/Goedel-Prover-V2](https://github.com/Goedel-LM/Goedel-Prover-V2)
- Nemotron appears in a 2026 Lean formalization evaluation only as a baseline — [arXiv 2606.05632](https://arxiv.org/html/2606.05632v1); no DeepSeek-Prover V3 found in any result
- Kimi K3 GPQA Diamond 93.5 vs GPT-5.6 Sol 94.1, Claude Fable 5 92.6, GLM-5.2 91.2; SciCode 58.7 vs 60.2 (Fable 5) — [Kimi K3 README](https://github.com/MoonshotAI/Kimi-K3)

### Inferences
- Goedel-Prover-V2-32B (~34 GB at Q8) is the only math specialist that is both small enough and licence-clean, but its value is Lean-4 proof search; for engineering/finance arithmetic checks a generalist plus a code interpreter or CAS is the evidenced path.
- DeepSeekMath-V2 (~670B) does not fit the node.

### Gaps
- No separate "Qwen3-Math" 2026 model exists in results (math is folded into Qwen3.x); DeepSeek-Math-V2 exact licence text and parameter count not read (HF blocked).

## 12. Cross-cutting: specialist-vs-generalist consensus 2026, disciplines without a credible specialist, specialists above 30B, multilingual coverage

### Takeaway
The 2026 evidence converges on a pattern rather than a verdict: small (≤32B) domain fine-tunes lose to 100B+ generalists on broad domain benchmarks (finance, medicine, cybersecurity), narrow structured tasks (contract extraction, legal annotation, security classification, low-resource-language finance) still reward domain training, and the only specialists that beat 2026 frontier models are themselves frontier-scale post-trains by major labs (Baichuan-M3-235B, GLM Legal) — which means for this node "specialist" is viable only in medicine (Baichuan-M3 at Q4) and arguably law (GLM Legal, size unknown), while finance, security, data science, communications, AEC, logistics/household and game design have no credible specialist and default to a generalist plus retrieval/tools.

### Cited Findings
- Frontier generalists beat clinical tools "in all three evaluations"; "scale and cross-domain reasoning may matter more than domain tuning" — [Nature Medicine June 2026](https://www.nature.com/articles/s41591-026-04431-5)
- Fin-R1 (7B) 46.9 vs Qwen3-235B 68.0 on FinReasoning — [arXiv 2603.19254](https://arxiv.org/pdf/2603.19254)
- Apertus-70B-MeditronFO 53.77 vs MedGemma-27B 60.67 — [micheledpierri](https://www.micheledpierri.com/2026/07/14/open-weight-medical-llms/); Baichuan-M3-235B HealthBench-Hard 44.4 vs GPT-5.2-High 42.0 — [arXiv 2602.06570](https://arxiv.org/abs/2602.06570)
- GLM Legal best open-weight on LegalBench (~86.2%) vs frontier cluster ~88% — [Vals](https://www.vals.ai/models/zai_glm-legal); [Vals LegalBench](https://www.vals.ai/benchmarks/legal_bench)
- Lawma 70B 81.9 vs GPT-4 62.9 on 260 narrow annotation tasks — [Lawma README](https://github.com/socialfoundations/lawma); Olava Extract macro F1 0.812 beating five frontier systems on contract extraction — [arXiv 2605.05532](https://arxiv.org/html/2605.05532v1); +8.7 pp from fine-tuning on security document classification — [arXiv 2605.20368](https://arxiv.org/pdf/2605.20368); Greek finance specialist 60.19% vs Llama-4 48.95% — [MultiFinBen](https://arxiv.org/html/2506.14028v1)
- Fine-tuned 8B security models "still trail frontier models like GPT-5 and Claude-4.5" — [KTH thesis](https://kth.diva-portal.org/smash/get/diva2:2060365/FULLTEXT01.pdf)
- RAG vs fine-tuning: "RAG consistently outperforms unsupervised FT for both in and out-of-distribution knowledge" (Ovadia et al.), gap largest for less popular topics; a hybrid "achieves the best results" in an agricultural study; practitioner framing "fine-tuning is for form, not facts" — [winder.ai](https://winder.ai/rag-vs-fine-tuning-2026-decision-framework/); [arXiv 2510.20260](https://arxiv.org/html/2510.20260v1); [bigdataboutique](https://bigdataboutique.com/blog/fine-tuning-llms-when-rag-isnt-enough); industrial Q&A assessment (2026) exists, results not read — [arXiv 2605.09533](https://arxiv.org/pdf/2605.09533)
- Vendor-style claims of 20%+ DSLM gains cite no traceable studies — [Cogent](https://cogentinfo.com/resources/domain-specific-language-models-dslms-the-end-of-the-general-purpose-llm-hype-in-2026)
- Specialists above 30B found (with base and date): SaulLM-54B / SaulLM-141B (Mixtral, 2024) — [BigDATAwire](https://www.hpcwire.com/bigdatawire/this-just-in/equall-introduces-expanded-saul-family-of-legal-llms-with-54b-and-141b-models/); Lawma-70B (Llama 3, 2024) — [Lawma](https://github.com/socialfoundations/lawma); XuanYuan3-70B / FinX1-Preview (Llama3-70B, 2024) — [XuanYuan](https://github.com/Duxiaoman-DI/XuanYuan); Agentar-Fin-R1-32B (Qwen3, 2025) — [arXiv 2507.16802](https://arxiv.org/html/2507.16802); Meditron-70B (Llama-2, 2023) — [meditron](https://github.com/epfLLM/meditron); Apertus-70B-MeditronFO (2026) — [arXiv 2605.16215](https://arxiv.org/html/2605.16215); Med42-v2-70B (Llama 3, 2024) — [Med42-v2](https://arxiv.org/html/2408.06142v1); OpenBioLLM-70B — [knack](https://www.knack.com/blog/healthcare-llm-2/); II-Medical-32B (Qwen3, 2025) — [featherless](https://featherless.ai/models/Intelligent-Internet/II-Medical-32B-Preview/readme); Lingshu-32B (2025) — [Lingshu](https://arxiv.org/abs/2506.07044v1); Baichuan-M3-235B (Qwen3, 2026) — [Baichuan-M3](https://github.com/baichuan-inc/Baichuan-M3-235B); GLM Legal (2026, size unknown) — [Vals](https://www.vals.ai/models/zai_glm-legal); WhiteRabbitNeo-70B (Llama 3.1) — [arXiv 2504.21039](https://arxiv.org/pdf/2504.21039); Kimi-Dev-72B (2025) — [Kimi-Dev](https://github.com/MoonshotAI/Kimi-Dev); Devstral 2 123B (2025) — [digitalapplied](https://www.digitalapplied.com/blog/best-open-weight-coding-models-self-host-hardware-match-2026); Qwen3-Coder-Next 80B-A3B / 480B-A35B (2026/2025) — [Qwen3-Coder](https://github.com/QwenLM/Qwen3-Coder); DeepSeek-Prover-V2-671B, DeepSeekMath-V2, Goedel-Prover-V2-32B — [DeepSeek-Prover-V2](https://github.com/deepseek-ai/DeepSeek-Prover-V2), [DeepSeek-Math-V2](https://github.com/deepseek-ai/DeepSeek-Math-V2), [Goedel-Prover-V2](https://github.com/Goedel-LM/Goedel-Prover-V2)
- Multilingual statements from primary cards: Qwen3.8 "201 languages and dialects" — [Qwen3.5/3.8 README](https://github.com/QwenLM/Qwen3.5); Qwen3-Embedding "over 100 languages" — [Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding); SmolLM3 six European languages — [smollm](https://github.com/huggingface/smollm); Gemma cookbook lists a translation collection "across 55 languages" — [gemma-cookbook](https://github.com/google-gemini/gemma-cookbook); Baichuan-M3 README is Chinese-first with Chinese usage examples — [Baichuan-M3](https://github.com/baichuan-inc/Baichuan-M3-235B); XuanYuan is Chinese-finance-centric (FinanceIQ) — [XuanYuan](https://github.com/Duxiaoman-DI/XuanYuan); Plutus is Greek-specific — [Plutus](https://arxiv.org/pdf/2502.18772)

### Inferences
- Disciplines with no credible 2026 open specialist (general model + retrieval/tools is the only evidenced option): finance/accounting (nothing above 2024-era 70B or 2025 32B), cybersecurity (≤70B, 2025-era, trailing frontier), data science, communications/marketing/PR, AEC/industrial engineering, aviation/logistics/household/estate security, game design; creative writing has specialists but they trail generalists.
- Disciplines where a specialist has 2026 evidence of beating or matching frontier models: medicine (Baichuan-M3-235B, vendor-run HealthBench), law (GLM Legal, independently run by Vals, top open but below closed frontier), narrow extraction/classification (Lawma, Olava Extract, security classification).
- The winning 2026 specialists are 235B+ post-trains of frontier open bases by well-resourced labs; the "specialist fine-tune of a small model" recipe (SaulLM-7B, Fin-R1, Foundation-Sec-8B, Meditron) consistently loses to 100B+ generalists on broad benchmarks, which is consistent with the node's "quality over speed, 170 GB" posture favouring the largest generalist that fits plus retrieval, with at most one or two large specialists swapped in per domain.
- For a Principal adding languages later, Qwen3.x (201 languages) and Qwen3-Embedding (100+) have the broadest stated coverage among open models; most domain specialists are English- or Chinese-only.

### Gaps
- No single 2026 meta-analysis of specialist vs generalist across domains was found; the consensus above is assembled from per-domain studies.
- Hugging Face model cards (licences, GGUF repos, download counts), arXiv full texts, Vals/BenchLM leaderboard pages, tbench.ai, eqbench.com and the live MTEB leaderboard were all unreachable from this session; every item marked "secondary" or "unverified" should be re-checked against those primary pages when a follow-up search budget is available.
