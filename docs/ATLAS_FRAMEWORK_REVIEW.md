# A.T.L.A.S. Framework — Deep Sweep Review (Pre-Execution Baseline)

| Field | Value |
|---|---|
| Document | ATLAS_FRAMEWORK_REVIEW.md |
| Version | 0.2.1 — engine watch-list added, FLUX.1 variant pinned |
| Date | 2026-09-21 |
| Supersedes | v0.2 (2026-09-21, same day); v0.1 (2026-09-18) |
| Scope | Everything agreed in the design conversation, through the Principal's completed confirmation workbook and the September hardware change |
| Purpose | A single consolidated statement of the framework, followed by an alignment audit: contradictions resolved, risks, and what Day 1 must prove before anything is trusted |
| Status of this document | Build baseline. Every decision is closed. Nothing has been executed; the Day 1 script is written against this document. |

## How to read this document

Every item carries one of these markers:

| Marker | Meaning |
|---|---|
| **AGREED** | Settled in the brief. Build to this. |
| **RESOLVED** | A contradiction or ambiguity was found during the sweep and is resolved here. Read these; they change earlier text. |
| **CLOSED** | A decision the Principal has answered. The answer is stated; no further input needed. Section 19 lists all fourteen. |
| **VERIFY** | Cannot be confirmed from the desk. Day 1 must prove it. Numbered V1…Vn, listed in Section 21. |
| **RISK** | A known risk with a mitigation. Numbered R1…Rn, listed in Section 20. |

Sections 1–17 are the consolidated framework. Sections 18–22 are the audit. Appendices carry configuration reference and sources.

---

## 0. Executive summary

**Verdict.** The framework is coherent, fully decided, and buildable. All fourteen open decisions are closed, all twenty original contradictions accepted, and six further resolutions have been added since v0.1. The Day 1 script is now written against this document.

**What changed since v0.1, in order of consequence:**

1. **The machine changed (Section 2).** GMKtec EVO-X5 Pro replaces the MINISFORUM MS-S1 Max: 192 GB of memory instead of 128 GB, 273 GB/s instead of ~256, and both NVMe slots at four lanes instead of one fast and one slow. The GPU reports the same `gfx1151` identifier, confirmed by the Principal, so every piece of community ROCm and Vulkan work carries over unchanged and risks R1 and R3 stand exactly as written.
2. **Quantisation rises across the board (Section 5.1).** The extra memory buys accuracy. Nemotron 3 Super and Qwen3.5-122B move from 4-bit to `Q8_0`; Qwen2.5-VL-72B and Meditron run at `Q8_0`. The two gpt-oss engines stay at MXFP4 because that is their native released form, not a compromise.
3. **A fifth core engine joins (Section 5.1).** DeepSeek V4 Flash, 284B total and 13B active, at `UD-Q4_K_XL`, 155 GB. It is the Apex engine: at 4-bit it reads roughly half the weight bytes per token that Nemotron reads at 8-bit, so it is both far larger and about twice as fast, and it is reserved for Deep Think's deep tier, TF_OMEGA, and strong cross-checks.
4. **Two engines may now be resident, but only one ever generates (Section 4.2).** The extra memory removes the swap between a text engine and the vision engine. Simultaneous generation is not adopted at all: the memory bandwidth is shared, so two streams would each run at half speed. The Celery GPU queue keeps its single worker.
5. **The node gets a graphical desktop (Section 3.1).** Ubuntu Server 26.04.1 stays the base, with XFCE added on top and xrdp for remote graphical access from the Principal's Windows PC. This is not the full Ubuntu Desktop image; XFCE costs roughly 300 to 500 MB idle against GNOME's 1 to 1.5 GB, and the AI system itself remains browser-served and independent of any desktop session.
6. **Six domains added, bringing the roster to thirty-six (Section 8.2).** Five further areas the Principal named are folded into existing domains as explicit subspecialties rather than duplicated as new cards.
7. **The vision layer narrowed to one engine (Section 15.2).** GLM-4.6V-Flash and Qwen2.5-VL-7B and -32B are all dropped. Qwen2.5-VL-72B at `Q8_0` is the sole vision engine, chosen for quality with its roughly 3 tokens per second accepted.
8. **Apple `.ipa` builds removed entirely (Section 15.1).** The Principal owns no Mac and rules out a cloud runner. Android and Windows builds stay; iOS source is still written, but compiled elsewhere whenever Mac access exists.
9. **Ubuntu 24.04.5 evaluated and rejected (Section 3.1).** Its September point release backports the same kernel 7.0 and Mesa 26.0 stack, so hardware support is identical; 26.04.1 wins on two further years of support and a newer toolchain, and its one disadvantage, host ROCm, does not apply to a containerised design.
10. **amd64v3 and amd64v4 archives rejected (Section 3.1).** The gain lands on CPU-bound distribution packages, not GPU inference, and llama.cpp and the PyTorch containers already compile against this exact chip. Canonical keeps the standard baseline as 26.04's default for good reason.

---

## 1. Mission and principles

**AGREED — Identity.** A.T.L.A.S.: a zero-cloud, autonomous AI node on bare metal, serving one user, the Principal, through a single interface, with two personas (Ren Ackerman, Arthur Sterling), an eight-director Shadow Cabinet, thirty-six domain knowledge profiles, twenty-three cross-domain task forces, and a set of autonomous protocols.

**AGREED — Zero-cloud, precisely defined.** After the Sentinel discussion the rule is:

- No model inference runs anywhere but this node.
- No Principal data leaves the node except to services the Principal already uses and explicitly connects (Google Workspace, Xero, Cloudflare, Wix, Pay.com, RewardPay), through their own authenticated APIs.
- Outbound reads from public data sources are permitted only from a firewall-enforced allowlist (Sentinel feeds, package repositories during build).
- Dynamic DNS name updates to Cloudflare are permitted; they carry no Principal data.
- No hosted AI, no cloud fallback, no telemetry from any installed component.

**AGREED — Single user.** The Principal. All personas address the Principal; the Principal never addresses the Shadow Cabinet directly.

**AGREED — Locale.** Australia. English only at launch; multilingual capability retained for later (Qwen3.5 and Kokoro carry it). Date format, tax treatment in Silas's rules, and Sentinel's market indices are Australian.

**AGREED — Completion standard.** ATLAS never delegates work back to the Principal. A form arrives filled, a document arrives drafted, a booking arrives confirmed. The only things asked of the Principal are decisions and approvals.

---

## 2. Hardware baseline

**AGREED — Machine.** GMKtec EVO-X5 Pro, "Gorgon Halo" platform. This replaces the MINISFORUM MS-S1 Max named in v0.1.

| Component | Specification | Design consequence |
|---|---|---|
| CPU | AMD Ryzen AI Max+ PRO 495, 16 Zen 5 cores, 32 threads, up to 5.2 GHz, 64 MB cache | Ample for orchestrator, Celery CPU workers, vector DB, browser automation, CPU-only tools (Radiance, EnergyPlus, KiCad, Docling) |
| GPU | Radeon 8065S, 40 CUs, RDNA 3.5, **ROCm target gfx1151, confirmed by the Principal** | Runs all inference. Same identifier as the previous machine, so every community ROCm, Vulkan and llama.cpp finding carries over unchanged |
| NPU | XDNA 2, up to 55 TOPS | Not used. Linux LLM tooling is Windows-first. Zero design dependency |
| Memory | 192 GB LPDDR5X-8533, soldered | 273 GB/s. Decode speed = bandwidth ÷ active weight bytes. Not upgradeable |
| GPU-addressable memory | ~180 GB on Linux via GTT, of which **~170 GB is free for engines** once the resident set is subtracted | **VERIFY V3.** The reason to buy this machine, and what makes Q8 quantisation and a 155 GB Apex engine possible |
| Power | 45–120 W configurable TDP | Principal is adding external cooling (R10) |
| Expansion | USB4 ports with external GPU dock support; a third M.2 slot unused | A CUDA-locked tool (NVIDIA Modulus/PhysicsNeMo) would need an NVIDIA card via the dock |
| Network | **Wi-Fi only by Principal's choice** | Wired 10GbE not used. V1 is informational, not a gate. Affects Phase 3 and 4 download time, not the VPN or the port forward |
| Storage 1 | 8 TB NVMe, PCIe 4.0 x4 | `/srv/atlas`: models, engines, agent data, vector stores, vault. The growing side |
| Storage 2 | 4 TB NVMe, PCIe 4.0 x4 | Operating system, logs, cold storage archives, restic backups. Both drives are now the same speed, so capacity decides the split, not bandwidth |

**AGREED — Expected inference performance (community benchmarks on this chip).**

| Model class | In memory | Decode |
|---|---|---|
| 8B dense, 4-bit | 5 GB | 40–50 tok/s |
| 30B MoE, 3B active | 18 GB | 70–100 tok/s |
| 32B dense, 4-bit | 19 GB | 10–12 tok/s |
| 70B dense, 4-bit | 40 GB | ~5 tok/s |
| 120B MoE, 5B active (gpt-oss-120b) | 63 GB | 30–55 tok/s |
| 120B MoE, 10–12B active at 4-bit (Nemotron, Qwen3.5-122B) | 65–70 GB | 18–19 tok/s |
| Same two at `Q8_0` (the adopted setting) | 120–130 GB | 12–17 tok/s, roughly a quarter slower for near-lossless weights |
| 284B MoE, 13B active at 4-bit (DeepSeek V4 Flash) | 155 GB | 25–32 tok/s. 13B active at 4-bit is ~6.5 GB read per token against Nemotron's ~12.7 GB at 8-bit, which is why the larger model is the faster one |
| 72B dense at `Q8_0` (Qwen2.5-VL) | 79 GB | ~3 tok/s. Dense models are bandwidth-bound and slow here; accepted for quality |

Prompt processing: ~350 tok/s stock, ~1,000 tok/s tuned, measured on a 7B model; larger models scale down proportionally. **This number drives the prompt-cache and injection-size rules in Sections 4.4 and 8.4.**

---

## 3. Platform and operating system

### 3.1 Operating system — AGREED with one refinement

- **Ubuntu Server 26.04.1 LTS**, with **XFCE** installed on top and **xrdp** for remote graphical access. **RESOLVED (C19, reopened and re-decided):** v0.1 specified Server with no graphical layer; the Principal requires a graphics interface. The answer is not the full Ubuntu Desktop image, which ships GNOME and costs 1 to 1.5 GB idle, but Server plus XFCE at roughly 300 to 500 MB. The Principal opens a real desktop session, with a real browser, from the Remote Desktop client already built into Windows. The AI system itself, Open WebUI and Cockpit, remains browser-served and works whether or not a desktop session is open.
- **Kernel 7.0** ships with 26.04 and includes the amdgpu driver for gfx1151.
- **RESOLVED (C21): 24.04.5 evaluated and rejected.** Its September 2026 point release backports kernel 7.0 and Mesa 26.0 from 26.04, so hardware support for this chip is identical. 26.04.1 wins on support to April 2031 against April 2029 and a newer toolchain. 24.04's one real advantage, officially supported host ROCm, is irrelevant because ROCm runs only inside containers here (Section 3.4).
- **RESOLVED (C22): the amd64v3 and amd64v4 archive variants are not adopted.** The benefit falls on CPU-bound distribution packages, not GPU inference, and llama.cpp and the PyTorch containers are already compiled against this exact Zen 5 chip. Canonical kept the standard baseline as 26.04's default after a rollout with known problems; no amd64v4 archive exists. Conversion remains possible post-install if ever wanted.

### 3.2 BIOS — AGREED

| Setting | Value | Why |
|---|---|---|
| UMA frame buffer | Smallest offered, 512 MB if available | On Linux the GPU takes memory dynamically via GTT; a fixed carve-out only wastes it |
| IOMMU | Enabled | Required for containers passing GPU devices |
| fTPM | Enabled | Required for LUKS auto-unlock (Section 3.5). **VERIFY V2** |
| Secure Boot | **Disabled (D2 closed)** | Avoids module-signing friction on a headless node behind a firewall |

### 3.3 Kernel parameters — AGREED, VERIFY V3

```
amdgpu.gttsize=196608 ttm.pages_limit=50331648
```

Sized for 192 GB: `gttsize` is expressed in MiB and `pages_limit` in 4 KiB pages. These were validated on 6.x kernels at 128 GB; **V3** confirms they apply on 7.0 at this capacity, that `rocminfo` reports `gfx1151` as expected, and that `llama-cli --list-devices` sees the full budget. If the reported identifier were ever to differ, the community ROCm wheels would need rebuilding against it before Phase 4 runs; the Principal has confirmed it reads `gfx1151`.

### 3.4 GPU compute stack — RESOLVED (C2)

Two different stacks serve two different layers, and this is deliberate:

| Layer | Backend | Where it runs | Status |
|---|---|---|---|
| LLM inference (llama.cpp) | **Vulkan (RADV)** | Host | Mature on this chip. Matches or beats ROCm for decode. Ships in the box on 26.04, no install |
| LLM prompt processing, optional | ROCm 7.2.2 + hipBLASLt | Container or later host install | ~3× faster prefill on long inputs. Optional upgrade, not a Day 1 dependency |
| PyTorch multimodal engines | **ROCm inside containers** | Docker/distrobox images carrying their own ROCm userspace | This is how the validated Strix Halo image/video toolboxes work. Host supplies only the kernel driver, `/dev/kfd`, `/dev/dri` |

**Why this resolution matters.** The brief chose 26.04 for the LLM layer, where Vulkan is sufficient, but Phase 4's engines need PyTorch-on-ROCm, which does not install cleanly on the 26.04 host kernel. Containerised ROCm removes the conflict. The host never needs a ROCm install.

**RISK R1** — PyTorch has no official gfx1151 wheel yet. The working path is a community-maintained wheel (scottt/rocm-TheRock). AMD's roadmap targets mid-2026 for first-class support. Pin the wheel version. Every Phase 4 engine is tested individually and reported pass/fail. This layer is less battle-tested than the LLM layer and the brief treats it that way.

### 3.5 Disk layout and encryption — GAP filled

**AGREED — Layout.**

| Drive | Mount | Contents |
|---|---|---|
| 4 TB, PCIe 4.0 x4 | `/` (OS), `/var/log`, `/srv/cold`, `/srv/backups` | Ubuntu, XFCE, logs, pruned-memory cold storage, restic repository |
| 8 TB, PCIe 4.0 x4 | `/srv/atlas` | `models/` (GGUF weights), `engines/` (PyTorch weights), `data/` (ChromaDB, graph store, SQLite), `workspace/`, `sandbox/`, `vault/` (encrypted container), `staging/` |

**Why this split, now that both drives are the same speed.** The v0.1 reasoning was bandwidth: keep the fast four-lane drive for weights read under load. That argument is gone; both are four-lane. The remaining argument is capacity and blast radius. The model and engine footprint is the side that grows as capability is added, so it gets the 8 TB drive. The operating system, logs and backups are stable in size and fit the 4 TB comfortably, and keeping the OS on a separate device means a full reinstall never touches the encrypted data volume.

**NEW — Encryption at rest (was unspecified).**

- LUKS2 on the 8 TB data volume and on the OS volume.
- Auto-unlock at boot via `systemd-cryptenroll --tpm2-device=auto`, bound to the firmware TPM, so the headless node boots without a keyboard.
- A recovery passphrase is generated and printed once. **CLOSED (D3):** the Principal keeps one copy on the node for convenience and the authoritative copy on an external USB drive. The USB copy is the one that survives node loss, so it must be stored away from the node, not beside it (R16).
- The vault (Section 11) is a second layer, gocryptfs, on top of the already-encrypted data volume; it protects top-secret files even while the node is running and unlocked.
- Swap is disabled. `/tmp` is tmpfs. Nothing transient touches disk unencrypted.

### 3.6 Base services — AGREED

- SSH, key-only, password auth off, listening on LAN and WireGuard interfaces only.
- `ufw` default deny inbound; allow SSH, Cockpit, Open WebUI, ntfy on LAN and WireGuard; allow UDP 51820 from anywhere. Default deny outbound except the allowlist (Section 12.5).
- Cockpit for system health and in-browser terminal.
- Docker with the GPU device nodes passed to containers that need them; the service user in `render` and `video` groups.
- XFCE desktop and xrdp, listening on LAN and WireGuard interfaces only, never internet-facing. Firefox installed in the desktop session for the Principal's own use.

---

## 4. Memory model and engine arbitration

### 4.1 Budget — AGREED, unified to Linux numbers

**RESOLVED (C3):** the brief carried both Windows (96 GB fixed) and Linux (~120 GB dynamic) numbers. Windows is gone, and the machine now has 192 GB. These are the binding numbers.

| Consumer | Budget | Notes |
|---|---|---|
| Ubuntu Server, XFCE, Docker, Cockpit | 4 GB | XFCE adds roughly 0.5 GB over the headless figure in v0.1 |
| Open WebUI, ChromaDB, graph store, embedding model | 4 GB | |
| Eleanor's resident router model, 4B class | 4 GB | Always loaded |
| Kokoro, Whisper Large-v3-Turbo, PyAnnote | 3 GB | Always loaded |
| Orchestrator, Celery workers, Redis, Sentinel, ntfy, WG-Easy | 1–2 GB | |
| **Always-resident subtotal** | **~17 GB** | |
| **Usable for engines** | **~170 GB** | GTT pool (~180 GB) minus the resident set above. Every figure in the table below is measured against this 170 GB, not against the raw 192 GB. **VERIFY V3** on first boot before anything depends on it |

**What fits together, at the adopted quantisations:**

| Combination | Total | Verdict |
|---|---|---|
| gpt-oss-120b + Qwen2.5-VL-72B, both resident | 142 GB | ~28 GB for both caches. The everyday pairing: text and vision with no swap between them. The vision engine runs at 2 slots rather than 8 while co-resident (Section 4.3) |
| gpt-oss-120b + gpt-oss-120b abliterated | 126 GB | ~44 GB for both caches |
| Nemotron `Q8_0` alone | 120–123 GB | Comfortable, ~47 GB for cache |
| Qwen3.5-122B `Q8_0` alone | 130 GB | Comfortable, ~40 GB for cache |
| Nemotron `Q8_0` + gpt-oss-120b, Ren and Arthur together | 186 GB | **Does not fit.** Accepted by the Principal: hemispheres swap rather than co-reside. Nemotron at `Q6_K` (~95 GB) would restore it if ever wanted |
| DeepSeek V4 Flash `UD-Q4_K_XL` | 155 GB | Runs alone. The Arbiter unloads any co-resident engine first. ~10–15 GB margin, so its context is capped accordingly |

### 4.2 Engine Arbiter — NEW, hard requirement

Nothing in the earlier brief named the component that enforces the residency and generation rules. It is defined here.

**The Engine Arbiter** is a single service inside the orchestrator through which every load and unload of any weight-bearing process passes: the five core LLMs, Meditron, every Phase 4 engine, Chatterbox when invoked. Rules:

1. It holds a ledger of currently resident engines and their measured footprint.
2. A load request states the engine and the context or batch size. The Arbiter computes the projected footprint against the live budget and either grants, queues, or refuses.
3. **Two engines may be resident; exactly one may generate at any moment.** This is the settled reading of the Principal's decision and it applies everywhere, including background work. Residency costs memory only, so a resident pair swaps for free. Generation is bandwidth-bound, so two concurrent streams would each run at roughly half speed; that is why simultaneous generation is not adopted at all.
4. A second generation request queues behind the running one, waiting seconds rather than the 15 to 45 seconds a swap would cost. The Celery `gpu` queue keeps exactly one worker, so a background job simply waits its turn (Section 9.7).
5. Before loading, it confirms the previous engine's memory has actually been released, by polling the GPU memory counters, not by trusting the process exit. This check is the difference between a stable node and one that crashes on the second swap.
6. It never preempts an engine mid-generation. A Sentinel job, a Celery task, or a second persona waits for the current generation to finish.
7. The Apex engine (DeepSeek V4 Flash, 155 GB) is exclusive: requesting it unloads any co-resident engine first, and nothing else loads while it is resident.
8. Deep Think and any multi-branch job must request their full footprint up front; if it does not fit, the Arbiter downgrades the depth tier (Section 9.1) rather than attempt the load.
9. Every decision is logged with the task ID from the Celery layer (Section 9.7).

### 4.3 KV-cache protocol — AGREED, corrected

**RESOLVED (C4):** the proposed protocol mixed shipped features with unmerged research. Only shipped features are adopted.

| Setting | Value | Reason |
|---|---|---|
| Flash Attention | On | Prerequisite for cache quantisation |
| KV cache type, Arthur's engines (Nemotron, Qwen3.5) | `q8_0` | Precision-sensitive audit and legal work |
| KV cache type, Ren's engines (gpt-oss, abliterated) | `q4_0` | More headroom for prose and multi-branch Deep Think |
| KV cache type, Apex engine (DeepSeek V4 Flash) | `q4_0`, context capped | Only ~10–15 GB of margin remains at 155 GB of weights |
| KV cache type, vision engine (Qwen2.5-VL-72B) | `q8_0` | Document and drawing reads are precision-sensitive |
| Per-model quantisation check | Day 1 verification script | **VERIFY V4**: unsupported architectures silently fall back to full precision |
| Context size | Explicit per model, never the default | Default contexts are small and silently truncate agent history |
| Context shift with `n_keep` | On; `n_keep` covers system prompt + persona directive + router state | Anchors never evicted; generation never hard-stops |
| Parallel slots | 8, except where noted | KV cost at 32k × 8 slots: gpt-oss < 20 GB, Nemotron < 10 GB, Qwen3.5 < 15 GB. The vision engine runs 2 slots while co-resident with gpt-oss, keeping the pair inside the ~28 GB of cache the 142 GB combination leaves |
| Hard pre-flight | Engine Arbiter | The actual OOM backstop |
| Long-document recall | Vector Cortex retrieval, never KV tricks | Context shift keeps anchors and recency; it does not recall a mid-document fact once evicted |

**Not adopted:** DuoAttention, SnapKV. Real research, not merged into llama.cpp or Ollama. Watch-list only.

**Asymmetric quantisation** is real in this design only at the per-engine level (each persona's engine loads separately), not per attention head.

### 4.4 Prompt-cache layering — NEW

Prompt processing is slow here; every dispatch that changes the system prompt from the top invalidates the cached prefix and pays full prefill. The system prompt is therefore built in fixed layers, most stable first:

1. Persona core (Ren or Arthur or a director): never changes within a session.
2. Governance block (approval tiers, never-delegate rule, disclosure rule): static.
3. Domain injections (Section 8.4): change per task force, appended after the static layers.
4. Retrieved memory and scars: appended last.
5. Conversation.

Slot-level cache reuse in llama-server keeps layers 1–2 resident across dispatches. Layer 3 changes cost only their own prefill.

---

## 5. Inference stack

### 5.1 The five core engines and their quantisations — CLOSED

**RESOLVED (C23):** v0.1 ran everything at 4-bit because 128 GB forced it. With 192 GB the rule becomes: use the highest quantisation that leaves working headroom, except where a model's native release is already low-bit.

| Engine | Architecture | Quantisation | File | Decode | Role |
|---|---|---|---|---|---|
| gpt-oss-120b | 117B MoE, 5.1B active | **MXFP4, native** | 63 GB | 30–55 tok/s | Default resident engine. Ren, Helena, Victor, Gideon, Minerva |
| gpt-oss-120b abliterated (Huihui) | Same weights, refusals removed | **MXFP4, native** | 63 GB | Same | Valerie default; Ren and Arthur on explicit override |
| Nemotron 3 Super | 120B hybrid Mamba MoE, 12.7B active | **`Q8_0`**, raised from 4-bit | 120–123 GB | ~12–14 tok/s | Arthur default. Silas, Alaric |
| Qwen3.5-122B-A10B | 122B MoE, 10B active, 256k context, multilingual | **`Q8_0`**, raised from 4-bit | 130 GB | ~15–17 tok/s | Override engine for long documents and future languages |
| **DeepSeek V4 Flash** | 284B MoE, 13B active | **`UD-Q4_K_XL`** | 155 GB | 25–32 tok/s | **NEW. The Apex engine.** Deep Think deep tier, TF_OMEGA, strong cross-checks. Runs alone |

Decode speed follows bytes read per token, not parameter count: 13B active at 4-bit is about 6.5 GB per token, while Nemotron's 12.7B active at 8-bit is about 12.7 GB. That is why the 284B engine outruns the 120B one.

**Why the two gpt-oss engines stay at MXFP4.** Their expert weights were released natively in MXFP4. There is no higher-precision original to return to; a `Q8` build would upcast the same 4-bit values, adding 60 GB and halving the speed for no accuracy. MXFP4 is this model's full-quality form.

**Why DeepSeek V4 Flash is Q4 and not Q8.** Its experts, about 96% of the weights, ship natively low-bit, so `UD-Q4_K_XL` and `UD-Q8_K_XL` carry bit-identical experts and differ only in the remaining 4% of tensors, at a cost of 7 GB. `UD-Q8_K_XL` is about 162 GB against the 155 GB of Q4. Both fit the ~170 GB budget, but Q8 leaves roughly 8 GB for the KV cache where Q4 leaves about 15, halving the working context on an engine whose context is already the tightest in the set. Q4 is the adopted setting; Q8 is logged as a post-Day-1 experiment, not a dependency.

**Sixth engine, Phase 3, confirmed included (D12):** Meditron-70B, dense, `Q8_0`, ~74 GB, ~3 tok/s. Secondary cross-check for Minerva only. **RESOLVED (C5):** it runs through llama.cpp like the others, not through the PyTorch layer.

**Seventh, the vision engine (Section 15.2):** Qwen2.5-VL-72B at `Q8_0`, 79 GB, ~3 tok/s. It loads through the same Arbiter and may be co-resident with gpt-oss-120b.

### 5.2 Backend — CLOSED (D1): llama-server

| Option | Vulkan on 26.04 host | Slot-level prompt cache | Nemotron 3 Super GGUF support | Maturity on gfx1151 |
|---|---|---|---|---|
| llama-server (llama.cpp) | Mature, validated by multiple Strix Halo builders | Yes, per slot, save/restore | Yes | Highest |
| Ollama | Vulkan backend introduced as experimental; ROCm backend needs host ROCm, which 26.04 lacks | Coarser | Had loader incompatibilities with this model earlier in 2026 | Lags llama.cpp |

**Decision:** llama-server, accepted by the Principal. Ollama is not installed. Both expose an OpenAI-compatible API, so Appendix B retains the Ollama equivalents only as a fallback reference if the primary path ever fails on this hardware.

### 5.3 Resident small models — CLOSED (D4)

| Role | Requirement | Candidate |
|---|---|---|
| Eleanor / router / classifier | ~4B, instruction-following, fast, always resident | Qwen3.5 4B-class instruct |
| Embeddings | Multilingual-ready, strong retrieval | bge-m3 or nomic-embed-text-v2 |
| Reranker, optional | Improves RAG precision | bge-reranker-v2-m3 |

The Principal accepted these as recommended: Qwen3.5 4B-class instruct for routing, bge-m3 for embeddings, bge-reranker-v2-m3 for reranking.

### 5.4 Model swapping — AGREED

| Step | From either NVMe, both now four-lane |
|---|---|
| Read 65 GB from disk | 10–15 s |
| Read 130 GB (a `Q8_0` engine) | 20–30 s |
| Read 155 GB (the Apex engine) | 25–35 s |
| Release previous engine and allocate | 5–10 s |
| Total per swap | 15–45 s depending on engine size |

Swaps grew with the higher quantisations, which is precisely why two-engine residency (Section 4.2) matters: the everyday text-and-vision pairing no longer swaps at all. Swaps are further minimised by grouping work by engine (Section 6.3), never eliminated.

---

## 6. Cognitive architecture

### 6.1 The two hemispheres — AGREED

| | Ren Ackerman — Vanguard, Prime Director | Arthur Sterling — Architect, Estate Manager |
|---|---|---|
| Hemisphere | Offensive, creative, expansive, outward-facing | Defensive, regimented, private, guardian of personal data |
| Domain mandate | Corporate, Enterprise, Infrastructure, AEC | Estate, Private Health, Logistics, Family |
| Default engine | gpt-oss-120b | Nemotron 3 Super |
| Override engine | gpt-oss-120b abliterated, on Principal's order | Qwen3.5-122B-A10B for deep document reasoning; **and the abliterated engine on the Principal's explicit command (C6 addendum)** |
| Apex escalation | DeepSeek V4 Flash, on Deep Think deep tier or TF_OMEGA | Same |
| Sampling | High temperature (0.8) as Deep Think Generator | Low temperature, not zero, as Deep Think Adversary |
| Deep Think role | Generator: three divergent trajectories | Adversary: rubric-scored verdict, weak concepts killed |
| Code path | Writes sandbox code | Audits code for memory and safety before execution, as a second layer over the OS-level sandbox cap |
| Directors | Gideon, Silas, Valerie, Helena, Eleanor | Alaric, Minerva, Victor |

**RESOLVED (C6):** the original brief bound Arthur's override to the abliterated engine "for legal dissection." Abliteration removes refusals and slightly increases errors; it does not improve reasoning. Arthur's standing override is therefore Qwen3.5 for long-document work. **Addendum accepted by the Principal:** Arthur may additionally be switched to the abliterated engine on explicit command, mirroring Ren. It is a manual escalation, never a default, and its output passes the same approval gate (R13).

### 6.2 The Shadow Cabinet — AGREED, final engine map

| Director | Division | Remit | Default engine | Override | Speaks externally |
|---|---|---|---|---|---|
| Gideon Vance | Corporate | Legal & Compliance | gpt-oss-120b, high reasoning, retrieval-grounded | Qwen3.5-122B for long contracts; Apex engine for TF_OMEGA | Yes, sensitive tier |
| Silas Thorne | Corporate | CFO: capital, audit, banking | Nemotron 3 Super + code interpreter for all arithmetic | none | Yes, standard tier |
| Valerie Cross | Corporate | Infrastructure, DevOps, AEC, cyber, AEGIS Sandbox | gpt-oss-120b abliterated | Qwen2.5-VL-72B for drawings and images | Yes, standard tier |
| Helena Frost | Corporate | Communications, brand, PR, crisis | gpt-oss-120b | none | Yes, standard tier |
| Eleanor Croft | Corporate | Routing, scheduling, triage; the 4-Way Router's classifier | Resident 4B model | none | Yes, routine tier (scheduling) |
| Alaric Stone | Estate | Physical assets, security, threat mitigation; Sentinel threat watch | Nemotron 3 Super | none | Yes, sensitive tier |
| Minerva Hale | Estate | Private health, longevity; medical data | gpt-oss-120b, retrieval-grounded | Qwen2.5-VL-72B for scans, Qwen3.5-122B for records, Meditron-70B cross-check | Yes, sensitive tier |
| Victor Vale | Estate | Aviation, transport, concierge | gpt-oss-120b | none | Yes, routine tier |

**RESOLVED (C7):** the original brief bound Gideon and Minerva to the abliterated engine "for zero-hallucination" reasoning. Hallucination is controlled by retrieval over the Principal's own documents, mandatory citations, and a verifier pass, not by model choice. Both use the standard engine. The brief's "Silas runs exclusively on Nemotron for absolute mathematical precision" is retained with the correction that precision comes from the code interpreter; Nemotron orchestrates and audits.

**AGREED — External correspondence.** Directors correspond externally under their own identities (mailbox or alias, signature, voice) so that Ren and Arthur are reserved for matters that warrant them. Every outbound item passes the approval gate (Section 16.2) at the tier shown above.

### 6.3 Work grouping to minimise swaps — AGREED

gpt-oss-120b is resident by default and carries most of the day. Nemotron loads when Arthur's division or an audit phase begins. Qwen3.5 loads only for long documents, images, or non-English work. The abliterated engine loads only on explicit override or a Valerie sandbox task. The orchestrator batches queued work by engine before swapping.

### 6.4 Persona register — AGREED

Ren and Arthur speak differently to the Principal than to external recipients. The register is a property set by the orchestrator on the outbound draft, not a prompt instruction the model can forget. Directors' external register is professional and role-appropriate; their internal register to Ren/Arthur is terse.

---

## 7. Routing and the privacy membrane

### 7.1 The 4-Way Router — AGREED, relocated

**RESOLVED (C8):** the brief described the router as "an Open WebUI pipeline." It lives in the orchestrator; the Open WebUI Filter is a thin relay into it. Reason: the same router must govern Sentinel, Celery tasks, email ingestion, and any future entry point, not only chat.

| Route | Trigger | Engine |
|---|---|---|
| Ren | Corporate, enterprise, infrastructure, AEC subject matter | gpt-oss-120b |
| Arthur | Estate, health, logistics, family; any privacy keyword | Nemotron 3 Super |
| Ren, override | Explicit Principal prefix | gpt-oss-120b abliterated |
| Arthur, override | Explicit Principal prefix or long-document trigger | Qwen3.5-122B-A10B |

### 7.2 Routing rules — AGREED

1. **Keyword hard rules always win.** The list includes at minimum: family, medical, health, vault, trust, estate, will, children, and the names of family members. A hit routes to Arthur regardless of anything else and is logged.
2. **Eleanor's resident classifier decides everything the keywords miss.** Example of why this is needed: "my mother's company needs a contract" trips "family" but is corporate work; the classifier can flag it for Ren with Arthur's privacy tags attached.
3. **Task-force detection.** Any cross-domain request triggers the matching task-force preset (Section 8.3). This is mandatory, not advisory.
4. **Manual overrides** are short prefixes typed at the start of a message. The set is fixed in configuration and logged.
5. **Every routing decision is logged** with the reason, so the Principal can see why a message went where it went.

### 7.3 What isolation actually is — AGREED, stated plainly

On one machine with sequential engines, the separation between Ren and Arthur is logical, not physical. It is enforced by separate memory collections, separate context windows, the router's hard rules, and the outbound gate. It is not enforced by separate hardware. The brief says this honestly rather than implying otherwise.

---

## 8. Domains and task forces

### 8.1 Scope — AGREED

All original thirty domain profiles and all twenty-three task forces are retained; six domains have been **added**, taking the roster to thirty-six. Nothing was ever removed. The consolidation proposed during review survives only as **tags**: each domain and task force carries a hemisphere, an owning director, and a JIT priority tier. The tags decide default routing; they delete nothing.

### 8.2 The thirty-six domains — owners and tiers

Tier A loads without hesitation. Tier B loads on a clear task-force or keyword match. Tier C loads only on explicit match and is expected to be rare.

| # | Domain | Hemisphere | Owner | Tier |
|---|---|---|---|---|
| 1 | Systems Architect & Cybersecurity | Corporate | Valerie | A |
| 2 | Growth Executive (marketing, sales, PR, SEO) | Corporate | Helena | A |
| 3 | Customer Success & Experience Lead | Corporate | Helena | B |
| 4 | Data Scientist & AI Engineer (incl. rapid data analysis) | Corporate | Valerie | A |
| 5 | CFO & Controller (incl. financial fraud and risk modelling) | Corporate | Silas | A |
| 6 | General Counsel & CHRO (AU construction, strata) | Corporate | Gideon | A |
| 7 | Development Director, Property & Real Estate | Corporate | Valerie, with Silas | A |
| 8 | AEC Computational Designer & Visualizer | Corporate | Valerie | A |
| 9 | Industrial & Energy Engineer (incl. industrial SCADA systems) | Corporate | Valerie | B |
| 10 | Chief Medical Officer & Risk Underwriter | Estate | Minerva | A |
| 11 | Game Director & Systems Designer | Corporate | Helena, with Valerie | C |
| 12 | Creative Director & Narrative Designer | Corporate | Helena | B |
| 13 | Dean of Academia & Pedagogy | Estate | Arthur, tagged to 14 | C |
| 14 | Private Family Advisor & Estate Guardian | Estate | Arthur | A |
| 15 | Chief Longevity Officer & Performance Physiologist (incl. bioinformatics, genomics and drug discovery) | Estate | Minerva | A |
| 16 | COO & Process Architect (incl. logistics optimisation) | Corporate | Eleanor, with Silas | B |
| 17 | High-Stakes Negotiator & Strategic Diplomat | Corporate | Ren, with Gideon | B |
| 18 | Global Asset Guardian & HNW Concierge (incl. logistics optimisation) | Estate | Victor, with Alaric | B |
| 19 | Travel, Leisure & Global Hospitality Director | Estate | Victor | A |
| 20 | Chief AI Officer & Agentic Systems Architect | Corporate | Valerie; self-modification gated (Section 16.3) | B |
| 21 | Chief Investment Officer & Quant Strategist (incl. financial fraud and risk modelling) | Corporate | Silas | A |
| 22 | Director of OSINT & Executive Protection | Estate | Alaric | A |
| 23 | Venture Partner & Private Equity Director | Corporate | Silas, with Gideon | B |
| 24 | Director of Philanthropy, CSR & Community | Both | Arthur and Ren | B |
| 25 | Geopolitical Strategist & Public Affairs Lead | Corporate | Ren | B |
| 26 | Cultural Asset & Fine Art Curator | Estate | Alaric, with Silas | C |
| 27 | Director of Digital Influence & Synthetic Media | Corporate | Helena; phrasing softened (Section 18 C9) | B |
| 28 | Chief Behavioral Architect & Neuro-Optimizer | Corporate | Helena, with Minerva | C |
| 29 | Frontier Technologies & Spatial Engineer | Corporate | Valerie | B |
| 30 | Sovereign Architect & Network State Strategist | Corporate | Ren, tagged to 25 | C |
| 31 | Embedded Systems & Mobile Security (firmware, bare-metal OS internals, mobile platforms) | Corporate | Valerie | B |
| 32 | Communications & Telecom Security (cryptographic protocols, network infrastructure, signals) | Corporate | Valerie, with Alaric | B |
| 33 | Automotive & Vehicle Systems Engineering | Corporate | Valerie | C |
| 34 | Quantum Computing & Post-Quantum Cryptography | Corporate | Valerie | C |
| 35 | Materials Science & Computational Chemistry | Corporate | Valerie, with Minerva | C |
| 36 | Synthetic Biology & Genomic Engineering | Estate | Minerva | C |


**RESOLVED (C24): the reserve list is closed.** The Principal supplied the operational reasons behind the two held-back areas and nine related fields. Six became standalone domains, 31 to 36 above; five fold into existing domains as named subspecialties rather than duplicate cards:

| Principal's area | Treatment |
|---|---|
| Bare-Metal OS Exploitation, Smartphones | Domain 31, Embedded Systems and Mobile Security |
| Communication Security, Telecom Network | Domain 32, Communications and Telecom Security |
| Automotive Systems | Domain 33 |
| Quantum Computing | Domain 34, with post-quantum cryptography |
| Drug Discovery and Materials Design | Domain 35 for materials; drug discovery folds into domain 15's bioinformatics subspecialty |
| Synthetic Biology Innovation | Domain 36 |
| Industrial SCADA Systems | Subspecialty of domain 9, Industrial and Energy Engineer, made explicit |
| Rapid Data Analysis | Subspecialty of domain 4, Data Scientist and AI Engineer |
| Financial Fraud and Risk Modeling | Subspecialty of domains 5 and 21 |
| Logistics Optimization | Subspecialty of domains 16 and 18 |

Bioinformatics and Genomic Sequencing remains folded into domain 15. Psychological Operations and Human Engineering is still **not** added: its legitimate negotiation and behavioural-economics content lives in domains 17 and 28, and operations aimed at named individuals stay outside what this system builds capability for.

**Posture for the security-adjacent additions.** Domains 31, 32 and 34 inherit the framing already set for domain 1: defensive architecture, authorised testing of systems the Principal owns or is engaged to test, vulnerability research, and standards compliance. They do not exist to act against third-party systems, and the approval gate treats anything they produce as sensitive tier.

### 8.3 The twenty-three task forces — owners

Original codes are retained. The grouping column is a tag, not a merge.

| Code | Task force | Group | Owner | Default tier |
|---|---|---|---|---|
| TF_ALPHA | Corporate Mergers & Acquisitions | CORP-DEALS | Gideon | Sensitive |
| TF_BETA | Geopolitical Tariff & Tax / Regulatory Arbitrage | CORP-LEGAL | Gideon, with Silas | Sensitive |
| TF_GAMMA | Capital Deployment & Wealth Allocation | CORP-CAPITAL | Silas | Sensitive |
| TF_DELTA | Banking & Institutional Leverage | CORP-CAPITAL | Silas | Sensitive |
| TF_EPSILON | Venture & Seed Investments | CORP-CAPITAL | Silas, with Gideon | Sensitive |
| TF_ZETA | Corporate Structuring & Holding Entities | CORP-DEALS | Gideon | Sensitive |
| TF_ETA | Global Liability & Litigation Defense | CORP-LEGAL | Gideon | Sensitive |
| TF_THETA | Regulatory Compliance & Audit | CORP-LEGAL | Gideon, with Silas | Standard |
| TF_IOTA | Intellectual Property & Licensing | CORP-CONTRACTS | Gideon | Standard |
| TF_KAPPA | Contract & Vendor Negotiation | CORP-CONTRACTS | Gideon, with Ren | Standard |
| TF_LAMBDA | AEC Oversight | CORP-AEC | Valerie | Standard |
| TF_MU | Software DevOps & Infrastructure | CORP-INFRA | Valerie | Standard |
| TF_NU | Hardware & Cryptographic Security | CORP-INFRA | Valerie | Standard |
| TF_XI | Data Privacy & Zero-Trust Networks | CORP-INFRA | Valerie, with Alaric | Sensitive |
| TF_OMICRON | Public Relations & Crisis Management | CORP-BRAND | Helena | Sensitive |
| TF_PI | Brand Strategy & Sales Architecture | CORP-BRAND | Helena | Standard |
| TF_RHO | Executive Scheduling & Strategic Routing | CORP-ROUTING | Eleanor | Routine |
| TF_SIGMA | Physical Estate & Asset Management | EST-ASSET | Alaric | Sensitive |
| TF_TAU | Private Security & Threat Mitigation | EST-SECURITY | Alaric | Sensitive |
| TF_UPSILON | Private Medical & Longevity Protocols | EST-HEALTH | Minerva | Sensitive |
| TF_PHI | Global Aviation & Transport Logistics | EST-MOBILITY | Victor | Standard |
| TF_CHI | Concierge & Frictionless Travel | EST-MOBILITY | Victor | Routine |
| TF_OMEGA | Absolute Apex Contingency | APEX | Principal-triggered, Ren and Arthur jointly | Sensitive, dual sign-off |

### 8.4 JIT domain injection — NEW, size-bounded

Domains are knowledge, not agents. A domain never spawns a model. It is injected into the running director's system prompt for one dispatch. Because prompt processing is this machine's weakest capability, injection is bounded:

1. Each of the thirty-six domain profiles is stored in two forms: the full seven-field profile (reference, retrievable) and a compact card of roughly 300 to 500 tokens (injected). The card carries the strategic frame, the compliance list for Australia, the cognitive method, and the tooling names.
2. At most three domain cards per dispatch. A task force preset names which cards it pulls; a request that would need more than three is split into a relay (Section 8.5).
3. Cards are appended after the static persona and governance layers so the cached prefix survives (Section 4.4).
4. Tier C cards load only on an explicit match, never speculatively.
5. The full profile is available to the director through a retrieval tool if a card proves insufficient mid-task, which costs one retrieval, not a re-prefill of everything.

### 8.5 How a task force executes — AGREED

The director is the sole lead and the sole inference session for a task force. Other directors join only as a sequential relay, one loaded engine at a time. There is no parallel swarm; the hardware cannot host one and the governance model does not want one.

**Dispatch sequence:**

1. The router or an explicit command identifies the task force.
2. The orchestrator resolves the owning director, or directors for APEX and dual-owner task forces.
3. The domain cards named by the preset are compiled into that director's prompt (Section 8.4).
4. The Engine Arbiter loads the director's engine if it is not resident.
5. The director executes, calling tools as the domain requires.
6. If the preset spans more than one director, the output, never the live context, relays to the next director as its own dispatch with its own task ID and log entry.
7. The result reports to Ren or Arthur for synthesis and the approval-tier check.

**Worked example, TF_ALPHA.** Gideon loads with the M&A and Legal cards, drafts the structuring and risk analysis. His output relays to Silas, who loads with the CFO and Quant cards and runs valuation through the code interpreter. Silas's output relays to Ren, who synthesises and puts the brief through the gate. Three loads, three logged steps, one answer.

The task-force rule matters operationally: a single-director task force is one load and fast; a genuinely cross-domain one costs a relay. The orchestrator chains only the directors whose domain the request touches, never the whole board.

### 8.6 Agentic delegation — RESOLVED (C10)

The domain profiles' original line, "Stateless MRTR, dispatch payload and decouple," is replaced in all thirty-six by:

> Isolated-context dispatch. Spawns with a clean, task-scoped context; reports completion or failure to the spawning director under a task ID; subject to the same tiered approval and Ouroboros logging as any other action.

The stateless half is kept: clean context per dispatch. The decoupled half is removed: nothing fires without a return path, because a decoupled action cannot be approved, cannot be logged as a strike, and would leave incomplete work on the Principal.

---

## 9. Autonomous protocols

### 9.1 Deep Think (adversarial optimisation) — AGREED, corrected

Trigger: `[DEEP THINK: problem]` or the router's task-weight estimate. Eleanor's classifier picks a depth; the Principal can force any depth with a prefix. Nothing is hard-capped; a task declared heavy runs as long as it needs.

| Depth | Shape | Engine swaps | Typical time |
|---|---|---|---|
| Quick | Generator and adversary on the same loaded engine with different role prompts | 0 | 1 to 2 min |
| Standard | Ren generates three trajectories, Arthur scores on his own engine, Ren refines the winner | 2 | 5 to 8 min |
| Deep | Standard plus a second expansion and scoring round, **the Apex engine (DeepSeek V4 Flash) delivering the final synthesis**, Qwen3.5 as third opinion on documents | 3 to 4 | 15 to 30 min |

**Corrections adopted:** Nemotron runs at low temperature, not zero, because reasoning models loop at exactly zero. Scores out of 100 are rubric-based judgements with named criteria, not mathematics; where a real figure exists, Silas computes it through the code interpreter and Arthur scores against the computed number. Phases are batched, never round-by-round ping-pong between engines.

### 9.2 Cross-checking — AGREED

Any answer involving numbers, legal claims, or code can be verified by a second persona on a different engine. Cheap version: the currently loaded engine with a critic prompt. Strong version: an engine swap. The router applies the strong version automatically to the sensitive tier and to anything the Principal marks important.

### 9.3 Sentinel Protocol (autonomous monitoring) — AGREED, corrected

| Element | Design |
|---|---|
| Schedule | systemd timer, hourly. **RESOLVED (C11):** not a WSL2 cron; there is no WSL |
| Feeds | Outbound allowlist only. **CLOSED (D6):** CoinDesk, an index feed for ASX and US markets, RSS news, node telemetry |
| Detection | Statistics first: deviations against recent history, thresholds. A model is invoked only when a threshold trips |
| Engine | Whichever engine is resident, or Eleanor's model; escalates to a large engine only on anomaly. Never swaps an engine out from under an active session; the Engine Arbiter queues it |
| Owners | Alaric for threats, Silas for markets, under Arthur. **RESOLVED (C12):** not Ren |
| Alert | Structured BLUF entry to the log and a push to the Principal's phone through self-hosted ntfy over WireGuard, email fallback |
| Idle | The resident engine stays loaded. "Sleep to save VRAM" does not apply; idle inference costs nothing and unloading would cost a swap |

### 9.4 Ouroboros Protocol (self-healing) — AGREED, refined

| Element | Design |
|---|---|
| Strike input | Manual `[LOG STRIKE: error_name]` **and** automatic: failed tool call, rejected draft, failing sandbox test, overridden routing decision |
| Storage | A dedicated scar collection in the Vector Cortex; each scar tagged to persona and domain, holding context, error, and the correction that worked |
| Injection | Before any task, the closest few scars above a similarity threshold are injected (layer 4, Section 4.4). Not all scars; a pile of irrelevant corrections degrades quality within months |
| Curation | The Principal can review and retire scars. Scars are exempt from the 72-hour general pruning sweep (Section 9.6) |
| Boundary | Scars change behaviour through retrieved guidance. Any change to ATLAS's own code or configuration is a proposal requiring the Principal's approval (Section 16.3) |
| Guarantee | Injected guidance raises the odds of avoiding a repeat; verification (tests, cross-checks) is what guarantees it. The brief states both |

### 9.5 AEGIS Backup Protocol (disaster recovery) — AGREED, corrected

| Element | Design |
|---|---|
| Tool | restic: incremental, deduplicated, AES-256, integrity-checked. **RESOLVED (C13):** not a zip |
| Schedule | Nightly automatic plus the manual `[EXECUTE AEGIS BACKUP]` trigger |
| Freeze | The orchestrator pauses the Celery queues, ChromaDB and the graph store take consistent snapshots, then writes resume |
| Contents | Vector Cortex, graph store, scars, workspace, sandbox, orchestrator code and configuration, Open WebUI database, the vault as-is (still encrypted, never opened) |
| Excluded | Model and engine weights (re-downloadable; would add 400+ GB per snapshot). A manifest of exact model versions and checksums is backed up instead |
| Destination | The 4 TB second drive. **RESOLVED (C14):** a copy on the same 8 TB drive is not disaster recovery. Optional rotating external encrypted drive for off-site |
| Retention | 30 nightly, 12 monthly (**D9 closed**) |
| Passphrase | Off-node, with the LUKS recovery key (D3). A backup whose passphrase died with the machine is useless |
| Restore test | Quarterly restore to a scratch directory, verified by checksum, logged |

### 9.6 Semantic graph pruning — AGREED, scoped

A Celery beat job every 72 hours sweeps both memory layers (Section 10). Permanent facts stay anchored. Temporal operational data, for example a mutex notification from Tuesday, is removed from the active graph and vector collections and compressed into a dated archive under `/srv/cold` on the second drive. Scars are exempt (Section 9.4). Vault-tagged content is never written to memory in the first place (Section 10.5), so it is never pruned or archived.

### 9.7 Celery Shadow Broker — AGREED, with one correction

Redis and Celery in the Docker core. Long CPU-bound work, a multi-hour backtest, a GraphRAG indexing pass, a Docling ingestion of a document set, runs as a Celery task on the CPU workers, and the chat interface returns to the Principal immediately. Celery also carries AEGIS's schedule, Sentinel's pulse, Ouroboros's automatic strikes, and the 72-hour pruning sweep, replacing separate schedulers.

**Queues:** a `cpu` queue with many workers; a `gpu` queue with exactly one worker, which requests engines through the Engine Arbiter. **Correction (C15):** Celery decouples CPU work from the interface. It does not put two things on the GPU at once. A background task that calls an LLM partway through still queues behind the resident engine.

---

## 10. Memory

### 10.1 Vector Cortex (ChromaDB) — AGREED

Local ChromaDB with a local embedding model (D4). Collections: `corporate`, `estate`, `scars`, `documents_corporate`, `documents_estate`, `sentinel`. Each collection is bound to a hemisphere; the router's hard rules decide which collections a dispatch may read. Ingestion runs through Docling (Section 15.1) for PDFs, Office documents, and scanned material, producing structured chunks with page and table provenance so that Gideon's and Minerva's citations point at a page.

### 10.2 Graph layer (GraphRAG) — AGREED, additive

A second memory layer answering relationship questions, "what else touches this contract, this company, this person," which vector similarity cannot answer. It supplements ChromaDB; it does not replace it. **RISK R6:** Microsoft's GraphRAG indexing makes many LLM calls per document and would be slow here. Indexing runs as a Celery `gpu` task during idle periods using Eleanor's resident model for extraction, escalating to a large engine only for documents the Principal marks important. **CLOSED (D7):** LightRAG, chosen over Microsoft's GraphRAG for its lighter indexing on this hardware.

### 10.3 Scar collection — see 9.4.

### 10.4 Retention — CLOSED (D9)

Open WebUI keeps its own chat database, so ChromaDB supplements rather than literally replaces chat logs. The binding rule: chats are retained 90 days in Open WebUI, then summarised into the Vector Cortex and purged; operational logs 30 days hot on the second drive, then archived; Sentinel logs 12 months; scars permanent under curation; backups per Section 9.5. Vault sessions are never retained (Section 10.5).

### 10.5 Vault and memory — RESOLVED (C16)

An earlier version placed Arthur's estate memory collection inside the vault. The vault was then simplified to "top-secret files, opened on command, locked when not in use." A memory store inside a folder that is locked most of the time would leave Arthur without memory most of the time. Resolution:

- Estate memory lives on the always-mounted LUKS data volume like everything else. It is protected at rest by LUKS and in operation by the router's hard rules.
- The vault holds top-secret files only. Anything read from the vault is tagged `vault` for the life of that session; vault-tagged content is not written to any memory collection, not summarised, and not backed up outside the vault itself, unless the Principal explicitly says "remember this."

### 10.6 Backups — see 9.5. Passphrases and recovery keys are the Principal's responsibility off-node (D3).

---

## 11. Vault — AGREED, simplified

An encrypted gocryptfs folder at `/srv/atlas/vault`, on top of the LUKS data volume. Opened by a button in the interface that prompts for the passphrase directly; the passphrase never passes through a model or a chat message. Locked by command and auto-locked after 15 minutes idle (**D13 closed**). No RAM scrubbing, no forensic claims; those were removed from the design at the Principal's instruction. Contents are backed up as ciphertext only. Session handling of vault content is in Section 10.5.

---

## 12. Interface and access

### 12.1 Open WebUI — AGREED

Open WebUI is the face, not the brain. It runs in Docker, installs as an app on phone and laptop, handles chat, voice, and file upload, and presents the orchestrator as a single model named A.T.L.A.S. Ren and Arthur are additionally exposed as direct models for when the Principal wants one hemisphere alone. The 4-Way Router's Filter function relays every prompt to the orchestrator (Section 7.1).

**Offline hardening, mandatory:** offline mode and Hugging Face offline flags set; update checks, community sharing, web search, and any external embedding fetch disabled; speech settings pointed at local Kokoro and Whisper; chat retention per D9. Without this, the node quietly is not zero-cloud.

**Alternatives considered and rejected:** AnythingLLM (no prompt-intercept hook), LibreChat (heavier for one user), Lobe Chat (weaker offline story), a custom app (months before parity).

### 12.2 Remote access — AGREED

| Element | State |
|---|---|
| VPN | WG-Easy in Docker. Generates the phone QR code and laptop config. Admin page bound to LAN and WireGuard interfaces only, never internet-facing |
| Router | UDP 51820 forwarded to the node. **Done by the Principal.** |
| Domain | `sovereign-node.link` on Cloudflare. **Done by the Principal.** |
| DNS record | `vpn.sovereign-node.link` A record, maintained by a dynamic-DNS updater on the node through a scoped Cloudflare token, checked every few minutes, updated only on change. Client configs reference the hostname so they never need reissuing |
| CGNAT | **VERIFY V5:** the port-forward succeeding implies a routable public address, but confirm the node is reachable from mobile data before relying on it |
| Notifications | Self-hosted ntfy on the node, phone app subscribed over WireGuard; email fallback |
| Admin | SSH and Cockpit over LAN or WireGuard from the Principal's Windows PC |

### 12.3 Cloudflare token — CLOSED, automated in Phase 2 step 6b

The token is currently in a plain-text file named `CLOUDFLARE.txt`. The Principal asked for this to be automated rather than done by hand, and it is: Phase 2 step 6b performs it and V23 proves it. The specification the script implements is to create a scoped API Token with `Zone:DNS:Edit` on `sovereign-node.link` only, never the Global API Key; store it in an environment file with mode 600 owned by the updater's service account, outside `/srv/atlas`, outside any git-tracked path, and outside restic's include set; delete the plain-text file. The token value has not been shared in this conversation, so no rotation is required, only relocation.

### 12.4 The Windows PC — AGREED

Accessed on demand only, with write capability, as a shared folder the node mounts when a task needs it and releases afterwards. No software installed on the PC. Most of the Principal's data lives in cloud services (Section 13), so the PC is a minor source. It is not a server.

### 12.5 Trust zones and outbound allowlist — AGREED

| Zone | Members | Reach |
|---|---|---|
| Node | ATLAS services | Everything internal |
| LAN | Windows PC, home devices | Interface, SSH, Cockpit, ntfy, file share |
| WireGuard | Principal's phone and laptop | Same as LAN |
| Internet, inbound | Only UDP 51820 | WireGuard handshake only |
| Internet, outbound | Firewall allowlist | Google APIs, Xero, Cloudflare API, Wix, Pay.com, RewardPay, Sentinel feeds, package mirrors during builds, Hugging Face during model pulls. Everything else denied and logged |

---

## 13. Integrations

| Service | Path | Capability | Approval |
|---|---|---|---|
| Google Workspace and personal Gmail | Gmail and Calendar APIs, one-time OAuth per account via a Principal-owned Google Cloud project | Read, send, label, calendar both ways. Each account tagged corporate or estate for the router | Per message tier (Section 16.2) |
| Google Drive | Mounted as a folder on the node (rclone), both accounts | Read and write, appears as local storage to every persona | Writes to shared folders: standard tier |
| Xero | Official API plus Xero's own MCP tool server | Ledgers, invoices, contacts, bank feeds, reports as structured data for Silas | Any posting or payment: sensitive tier |
| Cloudflare | Full API, scoped token | DNS, domains, security settings | All changes: standard tier, DNS for sovereign-node.link itself: sensitive |
| Wix | Partial API for content and business data, browser for site design | Helena updates content by API, design changes through the browser | Standard tier |
| Pay.com | Developer API for payment data | Reading and reporting | Any payment action: sensitive tier, two-factor stays on the Principal's phone |
| RewardPay | Browser only, no public API found | Agent-driven browser with the Principal approving each action | Sensitive tier, two-factor stays on the Principal's phone |
| Anything without an API | Local browser automation, Playwright text-based DOM reading, UI-TARS 2.0 for vision-driven GUI work | Works with every engine in the set | Per action tier |

APIs are preferred over the browser wherever they exist: faster, unaffected by page redesigns, and they deliver structured data. Money never moves without the Principal; two-factor prompts are never automated.

---

## 14. Voice, speech, and vision

### 14.1 Engines — AGREED, final

| Function | Engine | Status |
|---|---|---|
| Text-to-speech, always-on default for all ten personas | Kokoro-82M | Local, cheap, measured naturalness ~4.2 to 4.5 against a human 4.5 to 4.8 |
| Text-to-speech, character and cloning tier | Chatterbox | Local, MIT licensed, won blind preference against ElevenLabs 65% to 25%; carries a documented continuation quirk, test before it carries anything important |
| Not adopted | Fish Audio S2 (Pro tier is research-licensed), Qwen-Audio-3.0-TTS (hosted only) | Excluded |
| Watch-list | Qwen3-TTS (open, local, 0.6B and 1.7B) | Test after core build; not a Day 1 dependency |
| Speech-to-text | Whisper Large-v3-Turbo via whisper.cpp or faster-whisper | Local |
| Speaker diarisation | PyAnnote 3.1 | Local. **VERIFY V6:** the model is gated on Hugging Face; the licence must be accepted once with a token during Phase 2 |
| Music and sound | Stable Audio Open | Phase 4 |
| Voice cloning alternative | CosyVoice2 | Phase 4, optional |

### 14.2 What makes a voice human — AGREED

The reference audio, not the model. Cloning from five to thirty seconds of a real recording with the target quality is the single largest factor. Prosody control (pauses, emphasis, tone shifts) and a small amount of natural imperfection carried over from the source are the next two. Kokoro reads flat by design and is right for routine lines; Chatterbox is the layer for personas that must carry character.

### 14.3 Voice casting — AGREED as a shortlist, VERIFY V7

Kokoro ships 54 named presets; the English set is 11 American female, 9 American male, 4 British female, 4 British male. Matching below is by name and register only; nobody has heard these presets against the descriptions yet. V7 is a listening test once Phase 2 is up.

| Persona | Description | Kokoro candidates | Path |
|---|---|---|---|
| Arthur | Male. Formal, patient, structured, deep resonance, calming British or Transatlantic cadence | bm_george, alternate bm_daniel | Kokoro preset |
| Ren | Male. Sharp, direct, fast, aggressive modern executive cadence with expressive pauses | am_onyx, alternate am_michael | Kokoro preset, Chatterbox for intensity if needed |
| Alaric Stone | Male. Grounded, authoritative, gravelly veteran security director | No preset delivers gravel | Chatterbox clone from a reference recording |
| Minerva Hale | Female. Calm, analytical, articulate, clinical | af_kore, alternate bf_isabella | Kokoro preset |
| Victor Vale | Male. Crisp, rapid, flawlessly polite, aviation concierge | bm_lewis, alternate bm_daniel | Kokoro preset |
| Gideon Vance | Measured, meticulous, deep, litigator in absolute facts | bm_george if Arthur takes bm_daniel, else am_onyx reassigned | Kokoro preset or Chatterbox clone to separate from Arthur |
| Silas Thorne | Male. Sharp, cold, calculated quant | am_eric, alternate am_echo | Kokoro preset |
| Valerie Cross | Female. Direct, brilliant, slightly impatient engineer | af_nova, alternate af_river | Kokoro preset |
| Helena Frost | Female. Charismatic, persuasive, perfectly modulated PR executive | af_bella, alternate bf_emma | Kokoro preset, Chatterbox for modulation if needed |
| Eleanor Croft | Female. Warm, efficient, observant | af_sarah, alternate af_heart | Kokoro preset |

**Known conflict:** Arthur, Victor, and Gideon all pull toward the same four British male presets. Once heard, expect to spread them apart or move one to a Chatterbox clone, as Alaric is.

### 14.4 Vision — AGREED, policy

ATLAS sees on demand. The Principal, or a defined trigger, says "look now"; one frame is sampled through the vision engine, Qwen2.5-VL-72B (D10 closed). Continuous video inference is not a default: at any real frame rate it would compete for the GPU against everything else and break the sequential design, and an always-on camera feed is a privacy commitment that deserves an explicit decision. Continuous monitoring exists only as a separately enabled capability under Alaric's estate-security domain, tied to specific cameras, with SAM 2 for tracking, and it is logged like any other sensitive action.

---

## 15. Multimodal engines and tools

Two categories. **Tools** are CPU-bound or lightweight, always available, installed in Phase 2. **Engines** carry weights, compete for memory, load through the Engine Arbiter, and are built in Phase 4 inside ROCm containers (Section 3.4).

### 15.1 Phase 2 tools — AGREED, all confirmed real and GPU-independent

| Tool | Job | Owner |
|---|---|---|
| IfcOpenShell and Bonsai (Blender BIM) | IFC parsing, authoring, geometric clash detection | Domain 8, Valerie |
| MCP4IFC | MCP server for LLM-driven parametric IFC creation and editing, this is the "Arch-DiT / BIM-GPT" capability | Domain 8, Valerie |
| Radiance | Physically accurate lighting and daylighting simulation for real coordinates and time, the photometric half of "Lumina-PBR" | Domain 8, Valerie |
| OpenStudio and EnergyPlus | HVAC and building-energy simulation, the fluid-dynamics half of "DeepRoute-AEC" | Domain 8 and 9, Valerie |
| Python via code interpreter | Voltage drop and other formula engineering, all of Silas's arithmetic | All directors |
| KiCad CLI | PCB design, DRC, export, driven through its Python API by the coding engine | Domain 29, Valerie |
| Docling | Document conversion for PDFs, Office files, scans into structured chunks with tables and page provenance | Ingestion for every domain |
| Cross-platform build service | Android NDK plus Gradle for .apk, MinGW-w64 for .exe, containerised. This is the "OmniBuild-KVM" capability, Android and Windows only | Domain 1 and 5, Valerie |
| Playwright and browser automation | Text-based DOM browsing for platforms without an API | All directors, per tier |

### 15.2 Phase 4 engines — tiers

Green: build with confidence. Yellow: attempt with automatic fallback; never blocks the phase. Deferred: not in Day 1; ATLAS adopts when released or when hardware changes.

| Engine | Job | Tier and evidence | Footprint | Owner |
|---|---|---|---|---|
| Wan2.2 (in place of Wan2.1) | Video generation | Green: validated on gfx1151 by maintained toolboxes | 20 to 40 GB | Domain 12, Helena |
| **FLUX.1-dev** | Image generation, architectural visualisation with LoRA (the "Arch-DiT" imagery use). The **dev** variant, chosen for quality over the Apache-licensed schnell variant; the Principal accepts the dev non-commercial licence (15.5) | Green: validated on gfx1151 | 12 to 24 GB | Domains 8 and 12 |
| **Qwen2.5-VL-72B at `Q8_0`** | Vision-language: documents, drawings, screenshots, scans. **The sole vision engine** | Green. Official Apache-2.0 release with GGUF builds published, so it is **pulled in Phase 3 as a GGUF engine, not built here** | 79 GB | General. Valerie for drawings, Minerva for scans, Gideon for scanned contracts |
| Florence-2 | Lightweight vision, detection, captioning, OCR | Green | under 2 GB | General |
| TimesFM or Chronos | Time-series forecasting | Green | under 5 GB | Domain 21, Silas |
| Stable Audio Open | Music and sound generation | Green | under 5 GB | Domain 12, Helena |
| CosyVoice2 | Voice cloning alternative | Green | under 5 GB | Voice layer, optional |
| UI-TARS 2.0 | Vision-driven GUI automation for RewardPay and Wix design | Green | 8 to 16 GB | Domain 1, Valerie |
| OpenVLA | Vision-language-action for robotics | Green technically, dormant until hardware exists | 8 to 16 GB | Domain 29, Valerie |
| Rad-DINO | Radiology vision | Green | under 2 GB | Domains 10 and 15, Minerva |
| SAM 2 | Image and video segmentation and tracking | Green with limitation: build with the CUDA post-processing extension disabled, minor mask cleanup lost | under 4 GB | Domains 22 and 7 |
| PointLLM | Point-cloud understanding | Verify V8: point-cloud ops often carry custom kernels | 8 to 16 GB | Domains 8 and 29 |
| Clay or Prithvi | Satellite and geospatial analysis | Verify V9 | under 5 GB | Domain 7 |
| Microsoft TRELLIS | Image-to-3D | Yellow: community ROCm forks exist but hit build errors on sparse-voxel kernels, attempt, log, move on | 8 to 12 GB | Domains 8 and 12 |
| Blender Cycles (HIP) | Photorealistic rendering, the beauty half of "Lumina-PBR" | Yellow: works on this chip, occasional mid-render crashes reported, CPU-render fallback mandatory | varies | Domain 8, Valerie |
| Meditron-70B | Medical cross-check | Runs through llama.cpp in Phase 3 at `Q8_0`, not here | ~74 GB | Domain 10, Minerva |
| Evo (Arc Institute) | Genomics | Deferred: port unverified, and this GPU lacks the FP8 hardware Evo's larger checkpoints expect | n/a | Domain 15 |
| NVIDIA Modulus / PhysicsNeMo | Physics simulation | Deferred: CUDA-locked, would need an NVIDIA card in a USB4 external GPU dock, since this machine has no PCIe slot | n/a | Domains 8 and 9 |

### 15.3 Names that did not exist — RESOLVED (C17)

| Proposed name | Finding | Replacement in this brief |
|---|---|---|
| DeepRoute-AEC | No such model; the real MEP products are Revit-bound SaaS | OpenStudio/EnergyPlus + IfcOpenShell clash detection + Python calculations + Valerie orchestrating routing (15.1) |
| Lumina-PBR | No such engine | Radiance for physical light + Blender Cycles for the render (15.1, 15.2) |
| Arch-DiT | No such model | FLUX.1-dev with an architectural LoRA; parametric IFC via MCP4IFC |
| OmniBuild-KVM | No such tool; described capability is a build farm | Cross-platform build service (15.1), Android and Windows only; Apple removed entirely (D11 closed) |
| BIM-GPT, LayoutGPT-3D | Real research papers, not installable products | Capability covered by MCP4IFC |
| RTLLM | A benchmark for grading LLM-written Verilog, not a generator | Coding engine prompted against its structure; KiCad for the physical side |

### 15.4 Storage impact — AGREED

| Set | Size |
|---|---|
| gpt-oss-120b and its abliterated twin, MXFP4 | 126 GB |
| Nemotron 3 Super, `Q8_0` | 120–123 GB |
| Qwen3.5-122B-A10B, `Q8_0` | 130 GB |
| DeepSeek V4 Flash, `UD-Q4_K_XL` | 155 GB |
| Qwen2.5-VL-72B, `Q8_0` | 79 GB |
| Meditron-70B, `Q8_0` | ~74 GB |
| **Core engine subtotal** | **~690 GB** |
| Phase 4 engines, green and yellow | ~130 to 210 GB, lower than v0.1 with the vision models consolidated |
| **Total weights** | **under 900 GB of the 8 TB drive** |

Download time over the Principal's Wi-Fi at roughly 100 Mbps: about 15 hours for the core set, several more for the Phase 4 engines, and less predictable than a wired link. Both phases run detached and resumable, so this costs time, not attention.

### 15.5 Engine watch-list — considered, not adopted

Engines the Principal has asked about that fail a standing rule today. Each carries the rule it fails and the single event that would bring it in. Nothing on this list is built on Day 1; ATLAS re-checks the list when a later phase adds engines.

| Engine | What it does | Why not now | What would change the answer | Would replace |
|---|---|---|---|---|
| Qwen3.8-LiveTranslate (19 Sep 2026) | Real-time simultaneous interpretation: 60 languages understood, 29 spoken, about 2.3 s lag | **Hosted API only** (Alibaba Cloud Model Studio, QwenCloud, WebSocket). No open weights. Fails the zero-cloud rule (Section 1) on inference off-node and audio leaving the machine | An open-weights release. Qwen has released weights for Qwen3-Omni, so this is plausible | Nothing. Live interpretation is an open gap; the Whisper to engine to Kokoro pipeline (14.1) does consecutive translation only, and only in Kokoro's eight languages |
| Qwen-Image-2.1 (20 Sep 2026) | Image generation and editing in one 7B checkpoint: native RGBA, 2K, up to ten reference images, strongest text-in-image of its class; about 14 GB BF16, GGUF available, plain BF16 path on gfx1151 | **Qwen Research License, non-commercial only.** Same restriction that excluded Fish Audio S2 (C20). Not a peer of Qwen2.5-VL-72B, which reads images; this one makes them | Re-licensing to Apache-2.0 (Qwen did this for Qwen2.5 within months) or a commercial licence obtained by the Principal | FLUX.1-dev outright: half the footprint, generate and edit in one model |

**Decision recorded 2026-09-21:** the Principal keeps **FLUX.1-dev** as the image engine, quality being the deciding factor, and accepts that FLUX.1-dev itself carries a non-commercial licence (the Apache-2.0 variant is schnell, which trades quality for speed). The commercial-use exposure is therefore the same for FLUX.1-dev and Qwen-Image-2.1; the licence is not what separates them, quality and maturity on gfx1151 are. This is noted so that the two watch-list rows are read consistently: Qwen-Image-2.1 stays off the list because FLUX.1-dev is the better-validated engine today, not because its licence is worse.

---

## 16. Governance

### 16.1 Standing rules — AGREED

1. The Principal speaks only to Ren or Arthur. Shadow Cabinet output is never surfaced directly; the hemisphere synthesises and speaks.
2. Directors correspond externally under their own identities for routine and standard matters; Ren and Arthur are reserved for matters that warrant them.
3. Every outbound action passes the approval gate in code (16.2). This is a hard code path, not a prompt instruction.
4. ATLAS never discloses its AI nature externally. Recorded as a legal exposure in R12; the approval gate is the control.
5. ATLAS never delegates work back to the Principal. A rewrite pass removes any task-shaped request to the Principal; only decisions and approvals may be asked.
6. Register differs between Principal and external recipients (6.4).
7. Cross-domain requests must trigger their task-force preset (8.3).
8. Money never moves without the Principal. Two-factor prompts are never automated.

### 16.2 Approval tiers — AGREED

| Tier | Examples | Behaviour |
|---|---|---|
| Routine | Meeting scheduling, confirmations, acknowledgements | Pre-approved categories, sent automatically, logged for review |
| Standard | Replies with substance, requests, negotiations, content updates, DNS changes | Drafted, held until the Principal approves |
| Sensitive | Legal, financial, medical, estate, security, any payment, anything under a sensitive-tier task force | Drafted, held, flagged with the director's reasoning; strong cross-check applied automatically |

The queue lives in the interface; nothing external executes until the Principal taps approve. A push notification through ntfy announces items waiting.

### 16.3 What ATLAS may never do without the Principal — CLOSED (D8)

Accepted by the Principal and binding:

1. Move money, initiate a payment, or change a payment method.
2. Sign, accept, or bind to a contract or terms.
3. Send external correspondence above the routine tier.
4. Change DNS, domain, or Cloudflare security settings.
5. Delete, move, or modify vault contents or backups.
6. Modify its own code, configuration, approval tiers, router rules, or the allowlist. Domain 20 proposes; the Principal approves.
7. Enable continuous vision or audio capture.
8. Install software or pull models from outside the allowlist.
9. Share any Principal data with a third party not already connected under Section 13.
10. Create new external identities, accounts, or mailboxes.

### 16.4 Sandbox — AGREED

The AEGIS Sandbox runs code under an operating-system-level cap: a container with a hard memory limit, CPU quota, no network unless the task's tier grants it, and a timeout. Arthur's code review is a second layer over this cap, not the guard itself.

### 16.5 Legal flags — recorded, not argued

The brief keeps its rules. These are recorded so the Principal decides with eyes open:

- Fictitious directors corresponding externally under their own names, combined with non-disclosure of AI nature, can engage misleading-conduct provisions of the Australian Consumer Law and disclosure expectations in some professional contexts. Gideon's sensitive-tier check and the approval gate are the controls.
- The Privacy Act 1988 reforms of 2024 to 2026 raise obligations around automated decisions touching individuals; Arthur's division holds family and health data and should treat every external disclosure as sensitive-tier.
- macOS virtualisation on non-Apple hardware was considered and is excluded; the Apple build path is removed from the design entirely (D11 closed).

---

## 17. Day 1 Execution Protocol

Four phases. One entry command per phase. Every phase is idempotent: re-running skips what is complete. Every phase writes a log and ends with a printed pass/fail table. Phases 3 and 4 run detached under systemd so a dropped SSH session cannot kill them, and both are resumable at the file level.

### Phase 1 — Platform (reboot in the middle)

1. Pre-flight, using only what a bare host has: confirm Ubuntu Server 26.04.1, kernel 7.0, both NVMe drives present, fTPM enabled (V2), and the GPU present and identified from `lspci` and `/sys/class/drm`. **The `rocminfo` confirmation of `gfx1151` belongs to the Phase 4 container self-test (V11), because ROCm is never installed on the host (3.4).**
2. LUKS2 on the data volume, TPM2 enrolment, recovery key printed once for off-node storage (D3).
3. Mount layout per Section 3.5; swap off; tmpfs for `/tmp`.
4. System update; kernel parameters (V3); firewall baseline; SSH hardening; Cockpit.
5. Reboot. Post-reboot: `vulkaninfo` shows the Radeon 8065S, and the GTT pool read from `/sys/class/drm` matches the kernel parameters (V3, first half). The `llama-cli --list-devices` confirmation of ~170 GB usable moves to the Phase 2 gate, since llama.cpp is installed in Phase 2 (V3, second half).
5b. XFCE and xrdp installed, bound to LAN and WireGuard only; Firefox in the desktop session; one RDP connection tested from the Principal's Windows PC.
6. Docker with GPU device passthrough; service account in `render` and `video`.
7. WG-Easy, Cloudflare dynamic DNS with the relocated scoped token (R7), ntfy.
8. **Gate:** table of V2, V3 first half, V5 and V19 results. Phase 2 does not start on a red row. V1 is recorded as informational only, since the node runs on Wi-Fi.

### Phase 2 — Engines and services (fast, no large downloads)

1. llama-server, Vulkan build; optional ROCm container for tuned prefill.
2. Redis, Celery workers (`cpu`, `gpu` queues), orchestrator scaffold with the Engine Arbiter, router, approval queue, task ledger.
3. Open WebUI with offline hardening (12.1); A.T.L.A.S., Ren, Arthur registered as models; the router Filter installed.
4. ChromaDB, LightRAG graph store (D7), and the three resident small models named in D4: the Qwen3.5 4B-class router model, bge-m3 embeddings, bge-reranker-v2-m3. All are small and belong in this no-large-downloads phase. Docling ingestion service.
5. Kokoro, Chatterbox, Whisper Large-v3-Turbo, PyAnnote 3.1 (V6).
6. Phase 2 tools (15.1): IfcOpenShell, Bonsai, MCP4IFC, Radiance, OpenStudio/EnergyPlus, KiCad CLI, Playwright, the cross-platform build container (Android and Windows targets only).
6b. Relocate the Cloudflare token automatically: scoped token into a mode-600 environment file owned by the updater service, outside `/srv/atlas` and outside restic's include set, then delete `CLOUDFLARE.txt` (R7, D-closed).
6c. Google OAuth pause: the script prints one authorisation link per account, waits for the Principal to approve in a browser, then continues. Two accounts, roughly five minutes total (V20).
7. restic repository on the second drive; nightly timer; first backup; first restore test.
8. Sentinel timer, enabled with the feeds closed under D6: CoinDesk, an ASX and US index feed, RSS news, node telemetry. Pruning timer.
9. Windows PC share mount unit, on-demand.
10. **Gate:** every service healthy; V3 second half (`llama-cli --list-devices` reports ~170 GB), V6, V7 (listening test, deferred if the reference recordings do not exist yet), V12, V20, V23 recorded. The Arbiter's refusal logic is unit-tested here against stub footprints; the real two-engine test is V21 in Phase 3.

### Phase 3 — Core LLM pull (long, detached, resumable)

1. Pull, with checksum verification, at the quantisations fixed in Section 5.1: gpt-oss-120b (MXFP4), gpt-oss-120b abliterated (MXFP4), Nemotron 3 Super (`Q8_0`), Qwen3.5-122B-A10B (`Q8_0`), DeepSeek V4 Flash (`UD-Q4_K_XL`), Qwen2.5-VL-72B (`Q8_0`), Meditron-70B (`Q8_0`). About 690 GB.
2. For each model, in turn: load through the Engine Arbiter; confirm the quantised KV cache actually applied (V4); measure decode and prefill at 512 and 8k tokens; measure swap time; unload; confirm memory returned.
3. Two-residency test: load gpt-oss-120b and Qwen2.5-VL-72B together, confirm both hold at ~142 GB, and confirm a second generation request queues rather than running concurrently (V14, V21).
4. **Gate:** printed table of load success, KV type, tok/s, swap seconds per engine. This is the day-one load test the whole design depends on (V10). DeepSeek V4 Flash carries its own row: mainline llama.cpp support for this architecture is newer than the rest, so a failure here downgrades it to deferred without blocking the phase (R19).

### Phase 4 — Multimodal engines (long, detached, per-engine pass/fail)

1. Inside the base ROCm container, confirm `rocminfo` reports `gfx1151`, then self-test the community PyTorch wheel: tensor on GPU, matmul, a small diffusion step (V11). This is the first and only place ROCm runs.
2. Build green engines in order of value: FLUX.1-dev, Wan2.2, Florence-2, TimesFM or Chronos, UI-TARS 2.0, Rad-DINO, SAM 2 with the extension flag, Stable Audio Open, CosyVoice2, OpenVLA. The vision engine is not here; Qwen2.5-VL-72B is a GGUF engine pulled in Phase 3.
3. Attempt yellow engines: TRELLIS, Blender Cycles HIP with CPU fallback. Log pass or fail, never block.
4. Verify PointLLM (V8) and Clay or Prithvi (V9); mark deferred if they fail.
5. Register every passing engine with the Engine Arbiter with its measured footprint.
6. **Gate:** per-engine table: built, loaded, sample output produced, footprint, pass/fail/deferred.

### Expected durations

| Phase | Time |
|---|---|
| 1 | 30 to 60 minutes including reboot |
| 2 | 30 to 60 minutes |
| 3 | Download-bound: ~15 hours at 100 Mbps over Wi-Fi for ~690 GB; plus ~30 minutes of load tests |
| 4 | Download and build-bound: several hours; can run overnight |

---

## 18. Alignment findings: contradictions found and resolved

| # | Contradiction or ambiguity | Resolution |
|---|---|---|
| C1 | Backend named as llama.cpp, then Ollama, then llama-server at different points | llama-server primary on Vulkan; Ollama optional. D1 confirms |
| C2 | Ubuntu 26.04 chosen, but Phase 4 needs ROCm PyTorch, which does not install cleanly on the 26.04 host | ROCm lives inside containers; host runs Vulkan only (3.4) |
| C3 | Windows 96 GB and Linux ~120 GB budgets both appeared | Linux numbers are binding (4.1) |
| C4 | KV-cache proposal mixed shipped features with unmerged research | Only shipped features adopted (4.3) |
| C5 | Meditron listed among PyTorch engines | Runs through llama.cpp (5.1) |
| C6 | Arthur's override bound to the abliterated engine "for legal dissection" | Override is Qwen3.5 (6.1) |
| C7 | Gideon and Minerva bound to the abliterated engine "for zero-hallucination" | Standard engine, retrieval-grounded (6.2) |
| C8 | Router described as an Open WebUI pipeline | Lives in the orchestrator; Filter is a relay (7.1) |
| C9 | Domain 27 phrased as "memetic warfare" and "sentiment manipulation" | Retained under Helena with phrasing softened to legitimate growth and attribution work |
| C10 | Domain profiles' "Stateless MRTR, dispatch and decouple" removes the approval gate | Isolated-context dispatch with tracked completion (8.6) |
| C11 | Sentinel described as a WSL2 cron job | systemd timer; there is no WSL (9.3) |
| C12 | Sentinel fed to Ren | Alaric and Silas under Arthur (9.3) |
| C13 | AEGIS described as a single zip | restic (9.5) |
| C14 | AEGIS destination on the same drive | Second drive plus optional external (9.5) |
| C15 | Celery framed as freeing "a background hardware thread" for a backtest that may call an LLM | CPU work decouples; GPU calls still serialise (9.7) |
| C16 | Estate memory inside the vault versus the vault as on-demand top-secret storage | Estate memory on the LUKS volume; vault content never persisted (10.5) |
| C17 | Four engine names that do not exist | Real replacements (15.3) |
| C18 | "Vector Cortex replaces chat logs" versus Open WebUI's own database | Supplements; retention rule under D9 (10.4) |
| C19 | Desktop recommended for a Linux novice, then Server chosen, then the Principal required a graphics interface | **Reopened and re-decided:** Server 26.04.1 plus XFCE and xrdp, not the full Desktop image; Cockpit retained (3.1) |
| C20 | Fish Audio requested, then excluded as non-local and research-licensed | Kokoro plus Chatterbox (14.1) |
| C21 | Ubuntu 24.04.5 versus 26.04.1 left open | 26.04.1. The 24.04.5 point release backports the same kernel 7.0 and Mesa 26.0, so hardware support is identical; 26.04.1 wins on support life and toolchain (3.1) |
| C22 | Whether to adopt the amd64v3 or amd64v4 optimised archives | Neither. The gain is on CPU-bound distribution packages, not GPU inference (3.1) |
| C23 | Everything quantised to 4-bit under a 128 GB constraint that no longer exists | Q8_0 for Nemotron, Qwen3.5, Qwen2.5-VL-72B and Meditron; MXFP4 retained for gpt-oss as its native form; Q4_K_XL for the Apex engine (5.1) |
| C24 | Two domains held in reserve pending a stated need (old D5) | Reserve closed. Six new domains, five subspecialty folds (8.2) |
| C25 | Vision split across GLM-4.6V-Flash, Qwen2.5-VL-7B and a 32B that v0.1 wrongly said did not exist | One engine: Qwen2.5-VL-72B at Q8_0. The 32B is real and official, but the Principal chose maximum quality (15.2) |
| C26 | "Two engines at once" ambiguous between residency and generation | Two may be resident; exactly one generates, everywhere, including Celery jobs (4.2) |

---

## 19. Decisions — all closed

| # | Decision | The Principal's answer | Note |
|---|---|---|---|
| D1 | Inference backend: llama-server or Ollama | llama-server, Ollama not installed | Accepted as recommended |
| D2 | Secure Boot: disable, or enrol keys | Disabled on a headless node behind a firewall | Accepted as recommended |
| D3 | Where the LUKS recovery key and restic passphrase live | On the node and on an external USB drive | Changed by the Principal. See R16: the USB copy is the one that survives node loss, so it must be stored away from the node |
| D4 | Router model and embedding model | Qwen3.5 4B-class instruct, bge-m3, bge-reranker-v2-m3 | Accepted as recommended |
| D5 | Whether the reserve domains are adopted | Adopted now, with nine further areas supplied. Six new domains, five subspecialty folds | Changed by the Principal (8.2, C24) |
| D6 | Sentinel feed list | CoinDesk, ASX and US index feed, RSS news, node telemetry | Accepted as recommended |
| D7 | Graph layer: GraphRAG or LightRAG | LightRAG | Accepted as recommended |
| D8 | The never-without-the-Principal list | The ten items in 16.3 are binding | Accepted as recommended |
| D9 | Retention rule for chats, logs, backups | Defaults in 10.4 and 9.5 are binding | Accepted as recommended |
| D10 | Primary vision engine | Qwen2.5-VL-72B at Q8_0, sole vision engine. GLM-4.6V-Flash and the 7B and 32B variants dropped | Changed by the Principal: maximum quality, slow speed accepted (15.2, C25) |
| D11 | Apple .ipa build path | Removed from the design. No Mac, no cloud runner, no macOS virtualisation. iOS source is still written, compiled elsewhere if Mac access ever exists | Changed by the Principal |
| D12 | Meditron-70B: include or skip | Included in Phase 3 at Q8_0 | Accepted, with "include now" noted |
| D13 | Vault idle auto-lock period | 15 minutes | Accepted as recommended |
| D14 | Director mailboxes or aliases | Aliases auto-generated from each director's name on the Workspace domain, firstname.lastname pattern | Accepted, with automatic generation requested |

---

## 20. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | PyTorch has no official gfx1151 wheel, the community wheel may break on update | High | High for Phase 4, none for the LLM layer | Pin versions, per-engine pass/fail, containerised so the host is untouched |
| R2 | Ollama's Vulkan path immature on 26.04 | Medium | High if chosen | D1: llama-server primary |
| R3 | ROCm 7.2.x does not install on the 26.04 host kernel | Certain | Medium | Never install ROCm on the host, containers only (3.4) |
| R4 | KV quantisation silently falls back to full precision on an unsupported architecture | Medium | High, unexpected OOM | V4 per-model check |
| R5 | A second engine loads before the first releases memory, node crashes | Medium without an arbiter | High | Engine Arbiter polls GPU memory before every load (4.2) |
| R6 | GraphRAG indexing is LLM-heavy and slow here | High | Medium | Idle-time Celery job on the resident small model, D7 LightRAG |
| R7 | Cloudflare token in a plain-text file | Certain today | High | Scoped token, mode 600, outside backups and repo (12.3) |
| R8 | Open WebUI phones home by default | Certain unless hardened | High for the zero-cloud rule | Offline hardening in Phase 2 (12.1) |
| R9 | 10GbE driver unavailable | Not applicable | None | The node runs on Wi-Fi by the Principal's choice; V1 is informational |
| R19 | DeepSeek V4 Flash llama.cpp support is newer than the other engines | Medium | Medium, Apex tier only | Own row in the Phase 3 gate; a failure defers the engine without blocking the phase or the other six |
| R20 | Wi-Fi throughput and stability over a ~690 GB download | Medium | Low, time only | Phases 3 and 4 detached and resumable; retries are free |
| R21 | XFCE and xrdp widen the surface beyond a headless server | Low | Medium | Bound to LAN and WireGuard only, never internet-facing; no desktop autologin |
| R10 | Sustained thermal load in a small chassis | Medium | Low | Monitor temperatures in Cockpit; the chip's configurable ceiling is 120 W and the Principal is adding external cooling |
| R11 | CGNAT prevents inbound WireGuard | Low, port forward already succeeded | High for remote access | V5 from mobile data, static IP from the ISP if needed |
| R12 | Fictitious directors corresponding as humans, AI non-disclosure | Medium | Medium to high, legal | Gideon's sensitive-tier check, approval gate, 16.5 |
| R13 | Abliterated engine used for external output | Medium | Medium | Abliterated output always passes the same gate, never routine tier |
| R14 | Chatterbox continuation quirk in production speech | Medium | Low | V7 testing, Kokoro fallback per line |
| R15 | Prompt bloat from domain injection makes every dispatch slow | High without limits | High, usability | Three-card limit, compact cards, stable-prefix ordering (8.4, 4.4) |
| R16 | Backup passphrase or LUKS key lost with the node | Low | Catastrophic | **Sharpened under D3.** The Principal keeps a copy on the node and on an external USB drive. The on-node copy is convenience only; the USB copy is the one that matters, and it must live away from the node, since a fire or burglary that takes the node takes anything beside it. Quarterly restore test |
| R17 | PyAnnote gated model not accepted, diarisation silently absent | Medium | Low | V6 |
| R18 | Continuous vision enabled by drift rather than decision | Low | High, privacy | Separate capability flag under Alaric, logged, D8 item 7 |

---

## 21. Verification matrix: what Day 1 must prove

| # | Proof | Where |
|---|---|---|
| V1 | Network link up and stable. Informational only: the node runs on Wi-Fi, so this is no longer a gate | Phase 1 pre-flight |
| V2 | fTPM present and enabled, TPM2 enrolment succeeds | Phase 1 step 2 |
| V3 | Kernel parameters accepted on 7.0 and the GTT pool matches them (Phase 1); `llama-cli --list-devices` then reports ~170 GB usable (Phase 2). `rocminfo`'s `gfx1151` confirmation sits in the Phase 4 container under V11 | Phase 1 gate, then Phase 2 gate |
| V4 | Quantised KV cache actually applied per model, no silent fallback | Phase 3 step 2 |
| V5 | WireGuard reachable from mobile data via vpn.sovereign-node.link | Phase 1 step 7 |
| V6 | PyAnnote 3.1 gated model accepted and loading | Phase 2 step 5 |
| V7 | Voice casting listening test, Alaric and Gideon clones sourced | Phase 2 gate |
| V8 | PointLLM builds and runs on ROCm in the container | Phase 4 step 4 |
| V9 | Clay or Prithvi builds and runs | Phase 4 step 4 |
| V10 | All seven GGUF engines load, generate, swap, and release memory at their fixed quantisations, measured tok/s within the expected bands. Eleanor's resident 4B router model is verified separately at the Phase 2 gate, since it is pulled there | Phase 3 gate |
| V11 | Inside the ROCm container: `rocminfo` reports `gfx1151`, and the community PyTorch wheel passes tensor, matmul and diffusion self-tests | Phase 4 step 1 |
| V12 | Open WebUI makes no outbound connection after hardening (verified with the firewall log) | Phase 2 step 3 |
| V13 | restic backup completes and a restore verifies by checksum | Phase 2 step 7 |
| V14 | Engine Arbiter refuses an over-budget load and downgrades a Deep Think depth when the projected footprint exceeds budget. Unit-tested against stub footprints at the Phase 2 gate, then proved against real engines at Phase 3 | Phase 2 gate (stubs), Phase 3 gate (real) |
| V15 | Approval gate holds a standard-tier email until approved, routine-tier auto-sends and logs | Phase 2 gate |
| V16 | Router hard rule routes a "medical" message to Arthur even when the classifier disagrees, decision logged | Phase 2 gate |
| V17 | Sandbox memory cap kills a runaway process without affecting the node | Phase 2 gate |
| V18 | Vault opens by button, locks on idle, vault-tagged content absent from memory collections afterwards | Phase 2 gate |
| V19 | XFCE desktop reachable over xrdp from the Principal's Windows PC, and refused from outside LAN and WireGuard | Phase 1 step 5b |
| V20 | Google OAuth completed for both accounts at the Phase 2 pause; Gmail, Calendar and Drive reachable | Phase 2 step 6c |
| V21 | Two engines resident together at ~142 GB, and a second generation request queues instead of running concurrently | Phase 3 step 3 |
| V22 | DeepSeek V4 Flash loads at `UD-Q4_K_XL` and generates; if not, it is deferred without blocking the phase | Phase 3 gate |
| V23 | Cloudflare token relocated to a mode-600 environment file and `CLOUDFLARE.txt` deleted | Phase 2 step 6b |

---

## 22. Pre-execution checklist

Nothing runs until every box is ticked.

**Principal's actions — what is genuinely left**

- [x] Answer D1 through D14. **Done**, all fourteen closed in Section 19.
- [x] Decide where the LUKS recovery key and restic passphrase live (D3). **Done**: node plus external USB.
- [x] Confirm the Sentinel feed list (D6), the director alias pattern (D14), and the Apple build path (D11, removed).
- [ ] **Store the USB recovery drive away from the node**, not beside it (R16).
- [ ] **Create the Google Cloud OAuth client** for Gmail, Calendar and Drive, and be reachable for roughly five minutes during the Phase 2 pause (V20). This one step cannot be automated: Google requires the account owner to click Allow.
- [ ] **Source reference recordings** for Alaric's gravelly voice and, if the British male presets collide, Gideon's. Until then both fall back to the nearest Kokoro preset and V7 is recorded as deferred, not failed.
- [ ] Have a monitor and keyboard available for Phase 1 only, in case first boot needs a hand.

**Automated on the Principal's instruction, no action needed**

- [x] Cloudflare token relocation and deletion of `CLOUDFLARE.txt`: Phase 2 step 6b (V23).
- [x] Director aliases generated from names rather than confirmed one by one (D14).

**Build-side preconditions**

- [ ] Ubuntu Server 26.04.1 installed on the 4 TB drive; 8 TB drive unpartitioned.
- [ ] BIOS: UMA minimum, IOMMU on, fTPM on, Secure Boot disabled (D2).
- [ ] LAN address reserved for the node on the router, over Wi-Fi; UDP 51820 forward confirmed to that address.
- [ ] Internet bandwidth known, so the ~690 GB Phase 3 download can be planned.

**Accepted resolutions**

- [x] C1 through C20 accepted in the returned workbook. C19 was reopened and re-decided in the Principal's favour.
- [ ] **C21 through C26 are new in v0.2 and await an answer** on sheet 2 of the workbook: the Ubuntu and amd64v3 rejections, the quantisation rise, the closed reserve list, the vision consolidation, and the residency-versus-generation reading.
- [ ] **R9, R16, R19, R20 and R21 await acknowledgement** on sheet 3: two changed wording in v0.2, three are new.

---

## Appendix A — Request lifecycle

```
Principal (phone/laptop, Open WebUI over WireGuard or LAN)
   |
   v
Open WebUI Filter  --relay-->  Orchestrator
                                  |-- 4-Way Router: keyword hard rules -> Eleanor classifier -> task-force detection
                                  |-- Approval-tier assignment
                                  |-- Domain card compilation (max 3) onto the stable persona prefix
                                  |-- Celery task (cpu or gpu queue), task ID, ledger entry
                                  |-- Engine Arbiter: load/queue/refuse; confirm previous engine released
                                  v
                        llama-server (Vulkan) or a Phase 4 engine container (ROCm)
                                  |-- tools: code interpreter, retrieval (ChromaDB + graph), Docling, MCP servers,
                                  |          browser, Google/Xero/Cloudflare/Wix/Pay.com APIs, file share, sandbox
                                  v
                        Director output -> optional relay to next director -> Ren/Arthur synthesis
                                  |-- Cross-check (cheap or strong) per tier
                                  |-- Never-delegate rewrite pass; register set
                                  |-- Outbound gate: routine auto-sends, standard/sensitive wait for approval
                                  v
                        Memory writes (unless vault-tagged) -> Ouroboros strike on failure -> logs -> ntfy
```

## Appendix B — Configuration reference

**Kernel (GRUB):** `amdgpu.gttsize=196608 ttm.pages_limit=50331648`

**llama-server, Arthur's engines (Nemotron `Q8_0`, Qwen3.5 `Q8_0`) and the vision engine (Qwen2.5-VL-72B `Q8_0`):** `-fa on --cache-type-k q8_0 --cache-type-v q8_0 --ctx-size <explicit> --parallel 8 --keep <n_keep> --slot-save-path /srv/atlas/data/slots`

**llama-server, Ren's engines (gpt-oss MXFP4 and its abliterated twin) and the Apex engine (DeepSeek V4 Flash `UD-Q4_K_XL`):** same with `--cache-type-k q4_0 --cache-type-v q4_0`. The Apex engine additionally runs with a reduced `--ctx-size`, since only 10 to 15 GB remain beside 155 GB of weights.

**Desktop:** XFCE with xrdp bound to the LAN and WireGuard interfaces, no autologin, session started on demand.

**Ollama equivalents, if D1 chooses Ollama:** `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0|q4_0` (global, so per-persona asymmetry needs two daemons), `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_NUM_PARALLEL=8`, `OLLAMA_MODELS=/srv/atlas/models`, `num_ctx` set per Modelfile. Note the q-cache allowlist fallback (V4).

**Open WebUI offline:** `OFFLINE_MODE=true`, `HF_HUB_OFFLINE=1`, `ENABLE_COMMUNITY_SHARING=false`, web search disabled, update check disabled, audio STT/TTS endpoints pointed at local Whisper and Kokoro.

**Containers needing the GPU:** pass `/dev/kfd` and `/dev/dri`, run as the service user in `render` and `video`, set the ROCm userspace inside the image, never on the host.

## Appendix C — Storage plan

| Path | Drive | Contents | Backed up |
|---|---|---|---|
| `/srv/atlas/models` | 8 TB | GGUF weights | No, manifest only |
| `/srv/atlas/engines` | 8 TB | PyTorch engine weights and images | No, manifest only |
| `/srv/atlas/data` | 8 TB | ChromaDB, graph store, SQLite, slot saves | Yes |
| `/srv/atlas/workspace`, `/srv/atlas/sandbox` | 8 TB | Working files, sandbox runs | Yes |
| `/srv/atlas/vault` | 8 TB | gocryptfs container | Yes, as ciphertext |
| `/srv/cold` | 4 TB | Pruned-memory archives | Yes |
| `/srv/backups` | 4 TB | restic repository | Is the backup |
| `/var/log` | 4 TB | Logs, 30 days hot | Rotated |

## Appendix D — Sources relied on during review

Hardware: GMKtec EVO-X5 Pro announcement and VideoCardz and T3 specification reports for the Ryzen AI Max+ PRO 495 "Gorgon Halo" platform; Notebookcheck's processor page. Quantisation sizes: Unsloth's DeepSeek V4 and Qwen3.5 run guides, bartowski's Nemotron 3 Super GGUF listing, ggml-org's Qwen2.5-VL-72B GGUF. Ubuntu: OMG Ubuntu and Phoronix on the 24.04.5 point release and the amd64v3 archive experiments. The v0.1 hardware sources for the MS-S1 Max are superseded. Platform: AMD ROCm Strix Halo system-optimisation guide; community Strix Halo local-LLM guides; LucRoot known-good ROCm llama.cpp recipe; llama.cpp discussion on the known-good Strix Halo stack; Phoronix Ubuntu 26.04 Strix Halo benchmarks. Models: Unsloth Nemotron 3 Super guide; Beinsezii Qwen3.5-122B-A10B Strix Halo GGUF; Huihui gpt-oss-120b abliterated MXFP4 GGUF; gpt-oss model card. KV cache: Ollama FAQ and environment reference; llama.cpp server README on `n_keep` and context shift; StreamingLLM paper. Engines: kyuz0 and matthewhand Strix Halo ComfyUI toolboxes; TRELLIS.2 ROCm forks; Evo2StrixHalo port; SAM 2 ROCm issues; MCP4IFC project page and paper; Radiance at LBNL; Genusys and Auto BIM Route for the MEP market. Watch-list: Qwen3.8-LiveTranslate announcement and Model Studio API page; Qwen-Image-2.1 model card, Hugging Face licence file and GGUF listing; Black Forest Labs FLUX.1-dev and FLUX.1-schnell licence pages. Voice: Kokoro-82M VOICES.md; Trelis and Pinggy 2026 TTS comparisons; Qwen3-TTS repository; Fish Audio S2 licence page. Legal: Apple EULA analyses of OSX-KVM.

*End of document.*
