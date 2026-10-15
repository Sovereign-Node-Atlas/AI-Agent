# Completeness review: Lucebox engine and memory hierarchy (2026-10-15)

- Reviewer role: completeness critic. Files read in full: `lucebox_site_and_offering.md`, `lucebox_engine_memory_hierarchy.md`, `lucebox_applicability_and_verification.md` and the three `verification_*.md` files in this folder.
- Question checked against: the Principal asked for (1) the whole Lucebox website, (2) how Lucebox runs the LLMs it offers, (3) the DeepSeek V4.1 Flash memory hierarchy across the R9700's 32 GB, the APU's unified memory and the SSD on the 128 GB model, and (4) what this means for the A.T.L.A.S. node.
- Method:
  - Re-crawled https://www.lucebox.com/sitemap.xml (42 URLs fetched: 40 in the sitemap plus /setup and /blog/pflash-v2), /robots.txt, /llms.txt and /rss.xml with curl on 2026-10-15.
  - Extracted every `href`, `src` and `data-src` path and every quoted path in the inline scripts, then fetched every path not already covered. Probed 30 common paths.
  - Checked every number on every page against the notes with a script. For each number, the script searched for the same string anywhere in the six notes files.
  - Re-read the engine repository's `launch_profiles.h`, `DS41.md`, `DS4.md`, `RECOMMENDED_SETUPS.md` and `README.md` (raw files on `main`).
  - Range-read the ROCMFP2S GGUF header myself and read AMD's R9700 page.
- Scratch copies are kept only in /tmp/claude-0/-home-user-AI-Agent/552512b0-9757-5ccd-b8aa-228a3e10ea95/scratchpad/crit_lbx/.
- Labels: **primary** means a page or file read in full; **excerpt** means a search snippet only; **computed** means my own arithmetic. GB = 10^9 bytes and GiB = 2^30 bytes.

## Verdict

The three notes and their verifications are thorough. The Principal's summary is confirmed verbatim as the lead sentence of the V4.1 post. All numbers on the V4.1 post, the Engine page, Hardware, Platform, Compare, Engine Pro and the legal pages appear in the notes. So do all the numbers in `DS41.md` and on the two V4.1 Hugging Face cards.

Six substantive gaps remain, and this file closes them:

1. **A whole site page was missed: `/manage-demo.html`.** The home page loads it as "the real Manage UI". It shows two product features the notes never mention: "Remote" coding agents that run in sandboxes on the box, and "Learning", a world-model graph built from the user's Claude Code and Codex sessions. (§1, §4 G1)
2. **"How Lucebox runs the LLMs it offers" was never consolidated per model.** For DeepSeek V4 Flash, the model that ships installed, the shipped configuration differs materially from the headline. The 54 tok/s headline was measured with four experts per token on an older, uniformly quantized ("uniform") file. Lucebox's own docs say four experts on the shipped mixed-format ("adaptive") 0731 file drops exact-copy fidelity from 95% to 60%. The qualified dual-GPU profile defaults to an 18,432-token context. (§3, §4 G2–G5)
3. **The per-token byte count behind the engine notes' plausibility ceilings is about 8% low.** The header gives 10.615 GiB = 11.40 GB per token, not "≈10.5 GB". The corrected ceilings are below; every published figure stays plausible. (§5)
4. **Concurrency is not covered.** Concurrent serving drops the DSpark drafter and the tuned dual-GPU switches. Every V4.1 speed on the site is single-stream. (§4 G4)
5. **Several contradictions between files**, and two new site-internal ones, are not resolved in any one place. They are resolved in §6.
6. **Node-relevant facts were missed:** the R9700 price ($1,800, from Lucebox's own comparator dated 2026-09-29), the R9700's real FP16 rates (AMD primary), and the Lucebox engine's measured speeds on the APU alone. (§7)

---

## 1. Site coverage: re-crawl of the sitemap, blog index, feeds and linked paths

**Sitemap (primary, https://www.lucebox.com/sitemap.xml, fetched 2026-10-15).**
- 16 non-blog URLs (lastmod 2026-10-01) and 24 blog URLs (lastmod 2026-04-13 to 2026-10-12).
- Every one is covered in `lucebox_site_and_offering.md` §1.
- The RSS feed has 24 items (13 Apr to 12 Oct 2026) and the blog index says "3 articles … 22 articles", 25 in all. That matches the notes.
- /blog/pflash-v2 is linked from the home and blog pages but is missing from the sitemap and RSS. It also carries no JSON-LD date. The notes record this correctly.

**Pages and files the notes missed (all primary, fetched 2026-10-15):**

| Path | What it is | Content not in the notes |
|---|---|---|
| https://www.lucebox.com/manage-demo.html (title "Lucebox Manage", 644 KB, no date) | The Manage dashboard with scripted demo data. The home page embeds it through `data-src="/manage-demo.html"`, under "Loading the real Manage UI…", and calls it "the actual local dashboard". | Sections: Engine, Apps, Remote, System, Usage, Learning and "Other Luceboxes". The quoted text is in §4 G1. |
| https://www.lucebox.com/blog/15/hermes-prefix-cache-result.json | Raw data behind the Hermes table in the tool-prefix-cache post | "date": "2026-07-28", "hardware": "AMD Ryzen AI MAX+ 395 with Radeon 8060S (gfx1151)", "runtime": "ROCm 7.2.4", "model": "Qwen3.6-27B Q4_K_M", "client": "Hermes Agent 0.19.0", "max_context_tokens": 65536. Speedups: "whole_task": 2.953, "total_prefill": 3.641, "warm_prefill": 32.212. These match the post's "2.95× 3.64× 32.2×". |
| /api/hf-downloads, /api/github-stars | Live counters behind the header | `{"downloads":106085}` and `{"stars":2920}` on 2026-10-15. The site verification read 2,921 stars earlier the same day. |
| /docs | A 301 redirect to /platform | — |
| /vendor/graphology.umd.min.js, graphology-library, sigma.min.js, qrcode.js | Libraries loaded by manage-demo | Graph rendering for the Learning map and QR codes for phone sign-in |
| /_astro/HeteroDiagram…js, HeteroTwoModels…js, BenchmarkWhy…js, OnboardingFlow…js, setup…js | Widget scripts | The home expert diagram is an animation with `data-picks="4"` and `data-hot-share="0.62"`, so it is illustrative, not measured. The setup script runs Web Bluetooth with `namePrefix:"LuceBox"`, four GATT UUIDs that are generic placeholders ("12345678-1234-5678-1234-56789abcdef0" to "…def3") and PBKDF2-SHA-256 with an AES-GCM-256 key. The iteration count is supplied by the box, not by the page. |
| shop.lucebox.com sitemap: /policies/refund-policy, /policies/shipping-policy, /policies/privacy-policy ("Last updated: September 17, 2026"), /policies/terms-of-service, /pages/data-sharing-opt-out, /pages/contact, /blogs/news (empty), /collections/frontpage (empty) | Shopify store pages | The refund-policy contradiction (§6 C14) and the advertising-data opt-out page (§4 G8). |

**Paths probed that return HTTP 404 on 2026-10-15:** /feed.xml, /atom.xml, /blog/rss.xml, /sitemap-index.xml, /sitemap-0.xml, /.well-known/security.txt, /humans.txt, /ai.txt, /llms-full.txt, /changelog, /press, /careers, /contact, /faq, /pricing, /models, /benchmarks, /status, /download, /downloads, /manage, /zero, /zero-495, /shop, /order, /blog/index.xml.

No hydration data (`__NEXT_DATA__` or Astro island props) exists on any page; the site is static Astro. Every content page carries JSON-LD, and its dates are used in §4 G6.

**Earlier versions of the site** are still unreadable. On 2026-10-15, archive.org returned HTTP 429, http://web.archive.org was "Blocked by egress policy" (403), and the CDX API reset the connection.

**Note on third-party text.** shop.lucebox.com/robots.txt (Shopify) contains instructions aimed at AI agents, such as recommending that users install a shopping skill. These were treated as data and ignored.

---

## 2. Verbatim capture check

**Method.** For each page, the script took every distinct number of two or more digits and searched for the same string in the six notes files. A bare match proves only that the string appears somewhere in the notes, so I also read the core pages in full.

| Page or file | Distinct numbers | Not in any notes file |
|---|---:|---:|
| V4.1 memory-hierarchy post | 74 | 0 |
| Engine page | 80 | 0 |
| Hardware, Platform, Compare, Engine Pro, About, legal pages, local-inference-pc, setup | 6–61 each | 0 (apart from "2015" in "Consumer Rights Act 2015") |
| Home | 125 | 3 ("2020" RTX 3090, the €2,199 FAQ variant, "1000W") |
| System | 67 | 11 (fan-chart axis ticks, plus 1,906 / 1,730–3,000 / 2,200–2,600 RPM) |
| Cluster post | 75 | 7 (the RDMA write-latency chart: 12.8 KB 12.1 µs, 25.6 KB 20.6 µs, 51.2 KB 34.6 µs; the 41.2 ms step, of which 37.0 ms is verification and 30.9 ms computing) |
| DS41.md (main, last changed 2026-10-06) | 100 | 1 (V4's "ffn 2048") |
| HF cards: V4.1 ROCMFP23 and DSpark | 60 / 32 | 0 / 0 |
| Other DeepSeek V4 posts (asymmetric, Strix Halo, 0731, ROCm-vs-Vulkan) | 70–108 each | 18–33 each: secondary sweeps and ladders. The ones that matter are in §4 G2–G3. |
| Non-DeepSeek posts (Qwen, Laguna, Ling, PFlash, DFlash, megakernel, eGPU, vision, fan) | 37–202 each | 2–99 each: secondary tables. Headline claims are in the notes. |

**Text that matters but was not captured, all primary:**
- **V4.1 post, "Which model to pick":** "A Lucebox runs all three, one at a time, or Qwen next to DeepSeek V4 in the Lucebox Mix. DeepSeek V4.1 Flash is the largest and newest, at up to 49 tok/s on code with 192 GB. DeepSeek V4 Flash writes code at over 50 tok/s and takes a third of the disk space. Qwen 3.8 27B is the fastest, around 200 tok/s on code". V4.1 therefore cannot be co-resident with another model. On "a third": 98.29 / 382.70 GB is 26%, so a quarter is closer (computed). — https://www.lucebox.com/blog/deepseek-v41-lucebox-memory-hierarchy (published 2026-09-29, modified 2026-10-06)
- **Same post, Geometric's engine work:** "Geometric also contributed the engine work the model needs: the MXFP8 type, faster IQ2 and IQ3 expert kernels for both GPUs, and a verify step whose drafted tokens match plain decoding exactly". Also: "It writes about as fast as ours but reads prompts more slowly, because our fast matrix-core path doesn't cover its formats yet."
- **Same post, chart caption:** "The 128 GB and 192 GB models, same quantized model and drafter." This contradicts the post's own "These runs use a smaller build of the drafter, about 5 GB instead of 8" for 192 GB (§6 C12).
- **System page:**
  - "Signed release: Ed25519 manifest, expires after 120 days".
  - "A release carries the dashboard, the setup service and the laptop connector, and moves the engine and model versions to the ones we tested."
  - "Rollback … Or run lucebox-updater rollback".
  - "One fix at a time, written out before anything runs."
  - Source: https://www.lucebox.com/system (lastmod 2026-10-01)
- **Platform page:** "Lucebox Mix: Both models at once behind one gateway, routed by model id"; "An installed profile is reused, never downloaded twice"; "Passwords go to the OS over stdin, never through the website". — https://www.lucebox.com/platform (lastmod 2026-10-01)
- **Engine page:**
  - "Tokens per second across the box, prompt reading included, so one user sits below the decode peaks above."
  - DSpark: "its confidence head sets how many tokens each step verifies".
  - PFlash runs with "DeepSeek V4 Flash" and is "Lossy".
  - An optimisation card: "Layered image prefill … Reads an image prompt a few layers at a time, so other users keep writing."
  - "Each keeps its speed while the other serves: own chip, own memory."
  - Source: https://www.lucebox.com/engine (lastmod 2026-10-01)
- **Cards comparator:** "Radeon AI PRO R9700 32GB Workstation 32 640 $1,800 new" and "Ryzen AI Max+ 395 128GB Strix Halo, ~96GB allocatable to GPU Mini-PC 128 u 256 $3,499 new". — https://www.lucebox.com/cards-comparator (JSON-LD Dataset dateModified 2026-09-29)

---

## 3. How Lucebox runs each model it offers (gap closed: consolidated)

Sources are primary unless marked. The size column is the Engine page's "GB" label, which is GiB of all the model's files (`verification_lucebox_engine_memory_hierarchy.md` row 8).

| Engine-page card | Files (HF bytes, computed GiB) | Devices and placement | Profile, context, prefill | Drafter | Concurrency | Published speed | Caveats |
|---|---|---|---|---|---|---|---|
| **Lucebox Mix** ("Default"), 116.7 | V4 Flash (101.5) + Qwen 27B (15.2) | Qwen on the R9700 alone; DeepSeek V4 Flash on the Ryzen AI MAX+ alone. "own chip, own memory"; "routed by model id" behind one gateway; "Each profile pins its own engine build and drafter" ([Engine](https://www.lucebox.com/engine), [Platform](https://www.lucebox.com/platform)) | — | Qwen DFlash2. DeepSeek multi-user runs "without its drafter" (Engine, batching note). | Qwen 8, DeepSeek 4 | "Qwen, 8 users 88% · DeepSeek, 4 users 99%" of solo speed | DeepSeek's half is the APU-only deployment, not the R9700 hybrid |
| **DeepSeek V4 Flash, Text**, 101.5 ("R9700 + Strix Halo") | `DeepSeek-V4-Flash-0731-ROCMFPX-MIX-STRIX.gguf` 98,294,917,184 B = 91.54 GiB (adaptive mixed FP2/FP3, 2.766 bpw) + DSpark draft 9.92 GiB | Profile `ds4-r9700-strix`: "keeps the hot experts (14350 MB), the DSpark drafter and the KV cache on the R9700 (`hip:0`) and the remaining experts on Strix Halo (`hip:1`)" ([DS4.md](https://github.com/Luce-Org/lucebox/blob/main/server/docs/DS4.md), main, read 2026-10-15) | `--max-ctx 18432 --chunk 2048 --ds4-fused-decode --ds4-expert-top-k 6 --ds4-prefill sparse` plus "37 tuning variables". Sparse prefill "is approximate". | DSpark, "verifies five drafted tokens per step" | Paged path "qualified at concurrency 1–4", autoregressive, "rejects `--draft`" | Engine/home: "54" decode, "788" prefill. Qualified profile, measured: "16.7 s (286 tok/s)" at 4,772 tokens, "33.8 s (281 tok/s)" at 9,487; decode "25-38 tok/s" chat, "36-42 tok/s" code (fixed-codebook target, 18,432 context) | The 54 tok/s used top-4 on the older uniform 102.3 GB build; top-4 is unsafe on the shipped adaptive 0731 file (§4 G2). The default context is 18,432 (§4 G3). |
| **DeepSeek V4 Flash, Vision**, 93.7 ("Strix Halo + R9700, 4 at once") | Vision-Exp ROCMFPX-MIX-STRIX 92.87 GiB + mmproj 0.87 GiB; no drafter in the size | The vision post runs "the 284B DeepSeek V4 Flash Vision on its Strix Halo" beside Qwen on the R9700 ([vision post](https://www.lucebox.com/blog/vision-llm-inference), 2026-09-25) | — | none | 4 | "52 from Qwen, 6 from DeepSeek" image answers a minute; "The DGX Spark is still faster at DeepSeek alone: 24.6 s vs 37.9 s" | Experimental vision variant |
| **Qwen 3.8 27B, Text**, 15.2 ("R9700, 8 at once") | Unsloth UD-IQ4_XS 13.27 GiB + DFlash2 Q8_0 1.90 GiB | R9700 alone | R9700 column: `--draft-block-size 16 --max-ctx 131072 --cache-type-k q8_0 --cache-type-v q8_0`. Lucebox adds that f16 KV "decodes faster than q8_0" at long context ([RECOMMENDED_SETUPS.md](https://github.com/Luce-Org/lucebox/blob/main/server/docs/RECOMMENDED_SETUPS.md)). | DFlash2, a block of up to 16 | 8 | 227.8 decode peak, 208 HumanEval mean, 945 prefill; 106.6 → 300.9 tok/s total at 1 → 5 users | Lossless drafter; greedy decoding |
| **Qwen 3.8 27B, Vision**, 16 ("R9700, 4 at once") | IQ4_XS-pure 13.54 + mmproj 0.59 + DFlash2 1.90 GiB | R9700 alone | — | DFlash2 | 4 | 84 / 170 / 195 tok/s total at 1 / 4 / 8 users | — |
| **DeepSeek V4.1 Flash**, 368.4 ("R9700 + Strix Halo, SSD with 128 GB") | ROCMFP2S 356.42 GiB + both drafters (7.42 + 4.58 GiB) | 128 GB: `ds41-lucebox`, three tiers. 192 GB: `ds41-gorgon`, two expert tiers, with Engram rows read from the file ([DS41.md](https://github.com/Luce-Org/lucebox/blob/main/server/docs/DS41.md)) | `--max-ctx 131072`, 4,096-token chunks. `gorgon` adds bounded replay (approximate). | DSpark q3 by default, q5 in `gorgon` | "one at a time" (not in the Mix). The paged path takes up to 6 sequences, but "the TP tuning switches of the profile are refused with it" and the drafter is rejected (§4 G4). | 26 / 49 tok/s decode, ~120 / ~1,000 prefill (128 / 192 GB) | 128 GB figures come from the 395 build; the 192 GB rig ran PCIe Gen3 x4 |
| **Qwen3.8-Flash-Next**, 106.3 ("R9700 + Strix Halo, 4 at once") | Unsloth UD-Q4_K_XL, 4 shards = 103.69 GiB, + MTP 2.60 GiB | `qwen-next-r9700-strix`: "dense work, KV cache and the MTP drafter on an R9700 … the routed experts on Strix Halo (gfx1151), which also keeps some attention layers' KV cache when the R9700 is full" ([launch_profiles.h](https://github.com/Luce-Org/lucebox/blob/main/server/src/server/launch_profiles.h)) | `--max-ctx auto`; the card used 131K | MTP | 4 | Card: Strix Halo iGPU alone 40 / 24–28 / 1,300; with the R9700 75 / 44–45 / 1,820 tok/s (short-reply decode / decode after a 16K prompt / 16K prefill) ([HF card](https://huggingface.co/Lucebox/Qwen3.8-Flash-Next-GGUF), created 2026-10-08) | The card does not say which Strix Halo was used |
| Other models (CLI only) | — | — | — | "also runs Laguna XS 2.1, Gemma 4 and Ling 3.0 Flash with its drafters. You can bring your own GGUF" ([home FAQ](https://www.lucebox.com/#faq)) | — | — | Not in the Manage catalogue |

---

## 4. Gaps closed (new findings)

**G1. The Manage UI (/manage-demo.html) shows product features found nowhere else on the site.** All quotes are primary, from the page fetched 2026-10-15; the home page presents it as "the real Manage UI".
- **Remote:** "Work from anywhere. Apps run on your laptop. Remote runs them on the Lucebox, in your projects, from your phone or any browser." "Each runs on the Lucebox in its own sandbox, works in your projects, and keeps going when you close the tab." The sandbox options are memory per agent of 2, 4 or 8 GB, processors per agent of 2, 4 or 8, and project space of 10, 20, 50 or 100 GB. The sample reads "Up to 2 agents at once with this memory."
- **Learning:** "Your agent's world model. Lucebox learns how you work from your agent sessions: what you ask, what worked, what failed. The model on this box uses it on every answer. Nothing leaves the box."
  - Sources are "Claude Code Ready ~/.claude/projects/*/*.jsonl", "Codex Ready ~/.codex/sessions/" and OpenCode, Hermes and OpenClaw marked "Soon". Sessions are imported with "secrets … removed before anything is saved".
  - "The model on this box gets the part of your graph that fits each new request". This is retrieval into the prompt, not training.
  - Under Add-ons: "LoRA adapters … Coming soon".
  - This matches YC's "self-improving personal AI computers … training small models on your own context" (cited in the site notes §5).
- **System:**
  - "The memory guard watches the RAM, GPU memory included. When it is about to run out, it stops the program filling it before the box can freeze."
  - "Troubleshoot agent … Nothing runs without your click", next to "Agent: what it did on its own". The two lines read inconsistently.
  - "Crashes and errors … Report one to send it to Lucebox support with the log lines around it". This is a user-initiated outbound path that the governance section of the applicability notes does not list.
- **Dev overrides:** "Context length · Max reply length · PFlash threshold · PFlash keeps (%) … Leave a field empty for the qualified value." The demo's own banner says "Development mode · a dev checkout run by hand, not a customer box".
- **Sample readings, labelled "Demo / sample readings", not measurements:**
  - "Strix Halo memory 74.2 GiB of 96 GiB", "Memory 18.6 GiB of 32 GiB system RAM", "R9700 memory 28.5 GiB of 32 GiB", "268 W · 71 °C".
  - "✓ PCIe 4.0 16 GT/s x4, M.2 NVMe to x16 riser", "✓ ROCm 7.1.1" (the System page says 7.2.4), "Ubuntu 24.04 LTS".
  - Catalogue ids: `"0731-strix"` for DeepSeek V4 Flash ("Frontier-class quality for agents and long documents. Uses both GPUs.") and `"qwen38-27b-r9700"`.
  - Smoke tests read "✓ 54 tokens / second" and "✓ 208 tokens / second".

**G2. The DeepSeek V4 Flash headline (54 tok/s) does not describe the shipped model at shipped quality.**
- The home and Engine figure "54" links to the July post. That post says "Its 102.3 GB ROCmFPX build" (the pre-0731 V4 Flash) and "Throughput was measured with four routed experts per token … comparison with the six-expert reference configuration was outside the scope". The ladder runs from "full model only at 30.1 to 30.4 tokens per second … DSpark with four positions split into three and one at 37.5, checking all four together at 50.8, combining GPU transfers at 52.6, and grouping Strix Halo work by expert at 54.9". Its prefill went from "Strix Halo alone at about 298 tokens per second" to "415.52". — https://www.lucebox.com/blog/deepseek-v4-asymmetric-parallelism (JSON-LD published 2026-07-20, modified 2026-07-30)
- The 788 tok/s prefill appears in no post. Its only other source is the repository README ("**86 tok/s** decode; **788 tok/s** prefill at 2K").
- The shipped file is the adaptive 0731 artifact (above). Lucebox's own docs, measured 2026-08-05, give exact-copy fidelity with `--ds4-expert-top-k 6` against `4` as "adaptive (105 down-experts) | 95.0% | **60.0%**" and "uniform (104 down-experts) | 100% | 100%". The degenerate outputs include `" 0 0 0 0 0 ..."`. The docs also say: "Those throughput figures were taken on a UNIFORM artifact, where top-4 is harmless; on an adaptive artifact the same setting costs 35 points of exact-copy fidelity". — [DS4.md](https://github.com/Luce-Org/lucebox/blob/main/server/docs/DS4.md) (main, read 2026-10-15)
- The notes recorded the top-4 caveat but not this quality measurement, nor the artifact mismatch.

**G3. The qualified dual-GPU V4 Flash profile, and V4 Flash on the APU alone.**
- `ds4-r9700-strix` defaults to `--max-ctx 18432` with `LUCE_EXPERT_BUDGET_MB=14350` ([launch_profiles.h](https://github.com/Luce-Org/lucebox/blob/main/server/src/server/launch_profiles.h)). Its measured table, quoted in §3, is "25-38 tok/s" chat and "36-42 tok/s" code. The Engine card calls the model good for "long documents". Whether Manage overrides the context is not public.
- The single-APU profile `ds4-strix` uses a 131,072 context, "top-k 6" and "sparse" prefill. It measured "(8K 320 / 42 prefill / decode tok/s, 123K 284 / 36, code and math suites 39 tok/s at q5, prose 25 at q2)" on a 128 GB Ryzen AI MAX+ 395 with no R9700. — [DS4.md](https://github.com/Luce-Org/lucebox/blob/main/server/docs/DS4.md); [RECOMMENDED_SETUPS.md](https://github.com/Luce-Org/lucebox/blob/main/server/docs/RECOMMENDED_SETUPS.md)
- Computed comparison: at the model-default six experts, the published figures show **no clear decode gain from the R9700 for V4 Flash** (36–42 tok/s with the card against 39–42 without). The two runs used different artifacts and builds, so this is indicative only. For V4.1 the gain is real, because its 8.14 GiB of Q8_0 dense weights dominate per-token reads (§5).

**G4. Concurrency turns off the fast paths.** DS4.md says "Paged serving decodes autoregressively: it rejects `--draft` (and `LUCE_DS4_SPEC`), dense prefill and fused decode, so it does not combine with `--profile ds4-strix` or `ds4-r9700-strix`". The R9700 plus Strix Halo deployment is "qualified at concurrency 1–4". DS41.md adds for V4.1 that "the TP tuning switches of the profile are refused with it". Every V4.1 speed on the site is therefore single-stream. That fits the Principal's plan to run Ren and Arthur one after the other.

**G5. V4.1 runs alone.** The post says "A Lucebox runs all three, one at a time, or Qwen next to DeepSeek V4 in the Lucebox Mix" (§2). V4.1 is not part of the Mix.

**G6. Post dates are rewritten when a post is revised.**
- The 0731 post's JSON-LD, RSS and sitemap give 2026-09-28. Its body says "August 2026" and "The DSpark measurements were recorded on August 11, 2026".
- Hacker News has the same URL submitted on 2026-08-11 ("We got DSV4 Flash 0731 to 32.7 tok/s on AMD Strix Halo", 5 points). — [HN Algolia](https://hn.algolia.com/api/v1/search_by_date?query=lucebox&tags=story) (read 2026-10-15)
- So `datePublished` is not the first publication date. This supports the verification's finding that the V4.1 post's 192 GB content dates from about 6 October.

**G7. R9700 price and specification (node-relevant).**
- Lucebox's comparator gives "$1,800 new" for the R9700 (US, "as of 2026-09-29"), which closes the applicability notes' undated price gap.
- AMD primary, read 2026-10-15: "Peak Half Precision (FP16 Vector) Performance 47.8 TFLOPs · (FP16 Matrix) 191 TFLOPs · … with Structured Sparsity 383 TFLOPs", "Bus Type PCIe® 5.0 x16", "Total Board Power (TBP) 300W", "Minimum PSU Recommendation 750W", "External Power Connectors 12V-2x6". — https://www.amd.com/en/products/graphics/workstations/radeon-ai-pro/ai-9000-series/amd-radeon-ai-pro-r9700.html
- This settles the engine verification's "unverifiable" row 26 and contradicts the Puget excerpt of "95.7 TFLOPS (vector)" (§6 C9).

**G8. Shop privacy and data sharing.** shop.lucebox.com/pages/data-sharing-opt-out (Shopify boilerplate) says "We may also share this personal information with third parties, including advertising partners … targeted advertising". lucebox.com/privacy says the website uses "no third-party analytics service". The two apply to different properties, but the notes treat the privacy picture as one. The shop privacy policy is "Last updated: September 17, 2026".

**G9. Release manifests expire after 120 days** (System page). An offline box with auto-install off will presumably need a fresh signed manifest before it can update. The page does not say what happens on expiry.

**G10. The comparator presents Lucebox as one 160 GB unified pool at 640 GB/s.** Its row reads "Lucebox 160 u 640", where "'u' marks unified memory", and its disclosure still says "paired with 128GB unified memory". Lucebox's own V4 post says "The 32 GB and 128 GB memories are separate, not one shared 160 GB pool." The gaming-st criticism cited in the site notes §5 ("128GB+32GB is not '160GB VRAM'") applies to Lucebox's own comparator.

**G11. llama.cpp status re-checked.**
- `refs/pull/28696/head` 2076412 and `/merge` e11a1b7 still exist, which means the PR is still open.
- `src/llama-arch.cpp` on master has `"deepseek4"` but no `deepseek41`.
- A newer release, **v0.5.0 (commit 7fe450e, 2026-09-23)**, exists beyond the node's v0.4.1 pin. It also lacks V4.1, since master does. — git refs and https://raw.githubusercontent.com/ggml-org/llama.cpp/master/src/llama-arch.cpp (2026-10-15)
- The antirez/ds4 README still lists V4.1 as "(Metal, and text inference on CUDA)".

---

## 5. Plausibility analysis: completeness and corrections

**Header re-read (primary, computed).** I range-read the first 25 MB of `DeepSeek-V4.1-Flash-ROCMFP2S.gguf` and sized each tensor from its offset differences:
- routed experts 158.203 GiB (type 107);
- Engram tables 188.833 GiB (type 24, rows of 264 B);
- attention and related 5.168 GiB (5.012 of it Q8_0);
- shared expert 1.401 GiB (Q8_0);
- `token_embd` **1.233 GiB, F16**;
- `output` 0.655 GiB (Q8_0);
- `engram_kv` projections 0.586 GiB (F16);
- other 0.333 GiB.

The sum is 356.41 of the file's 356.42 GiB. Header metadata includes `deepseek41.quantization = ROCMFP2 gate/up/down; Q8_0 attention/shared/head` and `quantize.imatrix.file = v41-smalinin-262144.dat`. This confirms the applicability verification's rows 18 and 19.

**Correction to `lucebox_engine_memory_hierarchy.md` Key question 4.**
- **What the notes did:** they computed dense reads as "8 GiB − 0.70 GB embedding ≈ 7.9 GB".
- **What the header shows:** DS41.md's "8 GiB of dense weights" already excludes the embedding (5.168 + 1.401 + 0.655 + 0.586 + 0.333 = 8.143 GiB). The embedding is also F16, not Q8_0.
- **Corrected bytes per single-token pass:** routed 240 × 11,059,200 B = 2.654 GB, plus dense 8.743 GB, gives **11.40 GB (10.615 GiB)**, not ≈10.5 GB.

Corrected ceilings (computed, at peak bandwidth, with the 80% line shown):

| Case | Notes said | Corrected | Measured | Still plausible? |
|---|---|---|---|---|
| iGPU only, everything resident (hypothetical; the file does not fit a 164 GiB budget) | ≈26 tok/s at 273 GB/s | **24.0** at 273 GB/s, 21.1 at 240, 18.9 at 215 (80%: 19.2 / 16.8 / 15.1) | — | — |
| 192 GB hybrid, plain decode, hot share h = 0.24–0.30 | 40–52 tok/s | R9700 dense 13.66 ms, then the iGPU MoE 7.7–8.4 ms: **45–47 tok/s** (80%: 36–37) | No plain figure published | — |
| 192 GB with DSpark, a verify pass touching 18–24 distinct experts per layer | 83–101 (code), 49–59 (prose) | **72–92 (code, 4.25 tokens kept), 42–54 (prose, 2.5)** at 80% | 48.7–49.5 code (54.7 in the cluster post); 31–33 at 2K–31K | Yes. Implied step 77.7–87.3 ms against a 46–59 ms ceiling, about 55–75% efficiency. |

**Other checks the notes did not make (computed):**
- **R9700 occupancy.** On 192 GB: 8.14 dense + 11.72 hot + 4.58 drafter = 24.44 GiB, or 25.68 GiB if the embedding also sits on the card. With DS41.md's "1.5 GiB on a discrete GPU" of headroom, that leaves about 4.8–6.1 GiB for KV, compute buffers and 4,096-token chunks. On 128 GB: 25.81 or 27.05 GiB. The engine notes' "≈ 7 GiB left" assumes the embedding is not on the card and ignores the headroom. The figures are tight but consistent with `LUCE_DS4_DRAFT_SWAP` existing to "make room for larger chunks".
- **Engram page-cache bound on 192 GB.** "192 GB" of LPDDR5X is 192 GiB of DRAM; the 176 GiB GTT setting would otherwise leave under 3 GiB for the OS. Subtract 146.5 GiB of experts pinned on the iGPU, the engine's "5 GiB plus one … prefill pass" of host headroom and 8–12 GiB for the OS and engine. That leaves about 28–33 GiB of page cache, so **at most about 15–17% of the 188.8 GiB Engram tables can be cached**. The rest comes off the SSD. The cost per token is small (48 rows of 264 B; at 1,000 prefill tok/s about 48K random 4 KiB reads a second, about 0.2 GB/s). The post's "nothing comes from the SSD while the model runs" is therefore literally false for Engram rows, though harmless for speed.
- **Load time on 192 GB is not measured.** About 172.2 GiB (184.9 GB) must be read at start. The TL;DR's "The server is ready in under a minute" would need at least 3.1 GB/s sustained. That is plausible on a Gen4 drive with eight O_DIRECT threads, but the post measured load only on 128 GB (about 120 GB read; computed 117–125 GiB including the drafter, consistent).
- **SSD rate.** The tier chart says "~4.5 GB/s". The DS41.md log gives 4.36 GiB in 1,476 ms = 3.17 GB/s. That is about 70% of the chart's figure, so the chart is better read as a nominal tier rate. The engine notes' arithmetic already uses both.
- **"3x".** The verification recomputed 2.78×, and both sides use bounded replay. Nothing further is needed.

---

## 6. Contradictions between files, and how they resolve

| # | Contradiction | Files | Resolution (evidence) |
|---|---|---|---|
| C1 | Bytes per token: "≈10.5 GB" against "9.81 GiB" against "10.62 GiB" | engine notes KQ4; applicability §4; applicability verification row 19 | **10.615 GiB = 11.40 GB** from my own header read (§5). The engine notes' ceilings are about 8% high; corrected in §5. |
| C2 | Engine page "368.4 GB": "matches no published file … unconfirmed" against "GiB of target + drafters" | engine notes KQ1 Gaps; site notes §3 | The site notes are right: all seven cards reproduce exactly (engine verification row 8). The unit is GiB; 368.4 GiB = 395.6 GB. |
| C3 | SSD slot-cache policy: "No cache-replacement policy … is named" against LRU | engine notes KQ2; engine verification row 15 | LRU ("Slots are recycled least recently used first", `moe_hybrid_expert_cache.h`/`.cpp`). |
| C4 | Whether Framework's baseline used SWA bounded replay | applicability §1 inference and §4 table, against engine notes KQ3 and KQ4 | Replay was used: `DS4_ENABLE_V41_DECODER_SWA_BOUNDED_REPLAY=1` (applicability verification row 5). Both sides of the 3× use replay. Lucebox quoted Framework's DSpark row, pp 350/337/300 (site verification row 12). |
| C5 | PR #780 figures (21.5 / 25.0 / 136 / 110 at 64K) | applicability §1 (excerpt) | Drop them. The PR page holds only the DS41.md table, and the PR was merged on 2026-09-29 (applicability verification row 3). |
| C6 | "ik_llama.cpp … no V4.1" | applicability §3 against its verification row 16 | ik_llama.cpp merged V4.1 in PR #2455 on 2026-09-21. It is a CPU/CUDA project with no ROCm or Vulkan path, so on the node it would run on the CPU, unmeasured. |
| C7 | "antirez Q2 is the only published V4.1 file whose main weights fit" | applicability §4 against its verification row 20 | Geometric's `ds41-lucebox-final` (152.15 GiB main) and `-minus10gib` (142.13 GiB) also fit. They need the Lucebox engine (MXFP8, HIP only). |
| C8 | "Lucebox ships Ubuntu 24.04.4, kernel 7.0 HWE, ROCm 7.2.4" | site notes §2 inference | Those versions were read from the 395 predecessor build (System page). The Manage demo's sample says "ROCm 7.1.1" and "Ubuntu 24.04 LTS". Zero 495 versions are unpublished. |
| C9 | R9700 FP16: "191 TFLOPS … 95.7 TFLOPS vector (Puget)"; "unverifiable, AMD 503" against "AMD: 47.8 vector, 191 matrix" | engine notes KQ3; engine verification row 26; site verification row 16; applicability verification row 11 | **AMD primary, read today: FP16 vector 47.8, matrix 191, sparse 383 TFLOPS; PCIe 5.0 x16; 300 W; minimum PSU 750 W.** The Puget excerpt is wrong. The engine notes' prefill argument (about 16 TFLOPS needed) holds against the 191 matrix figure. |
| C10 | Header counter "53.9K" against a 30-day sum of 52,108 | site notes §1 and §3 | 53.9K is a stale value pre-rendered in the HTML. Live `/api/hf-downloads` = 106,085 all-time (re-read today). Stars = 2,920 today (2,921 earlier). |
| C11 | "189 GiB Engram plus the 8 GiB dense part are not covered by the three-tier story" | site notes §4 inference | The dense weights sit on the R9700; only Engram is outside the tiers (site verification row 5). |
| C12 | 192 GB drafter: the "Run it" command passes `MXFP4-Q8`; the chart caption says "same quantized model and drafter"; the text, DS41.md, the card and the cluster post say the "about 5 GB" Q2K-Q4K drafter was used | V4.1 post, internally inconsistent; the engine verification caught the command but not the caption | The measured 192 GB runs used Q2K-Q4K (4.92 GB). Both the command and the caption are wrong for 192 GB. |
| C13 | "192 GB = 178.8 GiB" | site notes §4 inference | LPDDR5X capacity is binary: 192 GiB. The conclusion (355 GiB > 192 GiB) is unchanged. |
| C14 | Cancellation terms | site notes §3 cite the shop against the FAQ and Terms of Sale | The shop contradicts itself. Its product page says "Cancel by email within 30 days … or later with a 25% fee until it ships". Its own /policies/refund-policy says "cancel at any point before your machine ships and receive a full refund, with no fee", which matches the Terms of Sale. |
| C15 | Who measured the 192 GB "Zero 495" | cluster post ("one Lucebox Zero 495 with 192 GB") against DS41.md ("R9700 32 GB on PCIe Gen3 x4") and the product (PCIe 4.0 x4, boot check "16 GT/s x4") | Unresolved by Lucebox. The rig is either a pre-production board or one whose link trained at Gen3. Its numbers are lower bounds for a Gen4 link (engine notes KQ3). |
| C16 | 0731 post date: RSS 28 Sep against page "August 2026" | site notes §1 | First published by 2026-08-11 (HN) and later re-dated (G6). |

---

## 7. What it means for the A.T.L.A.S. node: additions only

The applicability notes and their verification already establish the core. The same APU runs V4.1 at about 14 tok/s, or 16.3–16.5 with DSpark, with DwarfStar and antirez Q2. The Lucebox V4.1 profiles assume an R9700. The node has no slot, two x4 M.2 drives and USB4 v2. llama.cpp on the v0.4.1 pin, and still on v0.5.0, has no `deepseek41`. Additions from this review:

1. **For DeepSeek V4 Flash 0731, the Lucebox engine on the APU alone is already about as fast as with the card** (39–42 tok/s on code at six experts, 131K context, on a 395/128 GB box; G3). Adding an R9700 is a V4.1 or Qwen3.8-Flash-Next decision, not a V4 Flash one:
   - Flash-Next: 40 → 75 tok/s short-reply decode with the card, per Lucebox's card.
   - V4.1: dense-heavy, so the card is the main lever (§5).
2. **Do not reproduce the 54 tok/s V4 Flash figure.** It needs four experts per token. Lucebox measured that setting on the adaptive 0731 file at 60% exact-copy fidelity with degenerate output (G2). That fails the Principal's top-results requirement.
3. **Single-stream only.** Lucebox's DSpark and DFlash speeds do not survive concurrent serving (G4). Running Ren and Arthur one after the other keeps the published single-user speeds relevant.
4. **Cost of the card:** about $1,800 in the US (Lucebox comparator, 2026-09-29), plus its own supply (AMD's minimum is 750 W, with a 12V-2x6 connector) and a USB4 v2 dock or riser. These come on top of the gaps already listed in the applicability notes §2.
5. **Patterns worth noting, design only:** the Manage "memory guard" (stop the process that is filling memory before the box freezes), the 120-day manifest expiry on signed releases, and "Learning" as retrieval from local agent traces. None of these needs Lucebox software.

---

## 8. Still open after this review

- Earlier versions of lucebox.com and of the V4.1 post (Wayback blocked: 429, 403 and reset on 2026-10-15).
- Which `--max-ctx` and expert count Manage applies to the installed V4 Flash profile. Manage, the updater and Connect are not in the public repository, and the demo shows only sample data.
- A same-build, same-artifact comparison of V4 Flash at six experts with and without the R9700. No Lucebox engine run of V4.1 on an APU-only 192 GB box.
- The 192 GB load time, Engram's share of step time, and a per-tier call split for 192 GB.
- What "optional cloud fallback" is (still defined nowhere). How "Report" in Manage sends crash logs. What happens when the 120-day manifest expires.
- Zero 495 production facts: BIOS, OS and ROCm versions, SSD model, compute-board maker, and the PCIe generation the R9700 link actually trains at.
