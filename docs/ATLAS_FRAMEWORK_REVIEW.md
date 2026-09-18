# A.T.L.A.S. Framework — Deep Sweep Review (Pre-Execution Baseline)

| Field | Value |
|---|---|
| Document | ATLAS_FRAMEWORK_REVIEW.md |
| Version | 0.1 — pre-execution baseline |
| Date | 2026-09-18 |
| Scope | Everything agreed in the design conversation from hardware selection through the multimodal engine roster and voice casting |
| Purpose | A single consolidated statement of the framework, followed by an alignment audit: contradictions found and resolved, gaps, open decisions, risks, and what Day 1 must prove before anything is trusted |
| Status of this document | Review baseline. Nothing in it has been executed. No script exists yet. |

## How to read this document

Every item carries one of these markers:

| Marker | Meaning |
|---|---|
| **AGREED** | Settled in the brief. Build to this. |
| **RESOLVED** | A contradiction or ambiguity was found during the sweep and is resolved here. Read these; they change earlier text. |
| **DECISION** | Needs the Principal's answer before or during Day 1. Numbered D1…Dn, listed in Section 19. |
| **VERIFY** | Cannot be confirmed from the desk. Day 1 must prove it. Numbered V1…Vn, listed in Section 21. |
| **RISK** | A known risk with a mitigation. Numbered R1…Rn, listed in Section 20. |

Sections 1–17 are the consolidated framework. Sections 18–22 are the audit. Appendices carry configuration reference and sources.

---

## 0. Executive summary

**Verdict.** The framework is coherent and buildable on the chosen hardware. The design decisions that matter most were made correctly: MoE models over dense, sequential engine loading, prompt-cache-first orchestration, a hard approval gate in code rather than in prompts, and a headless Linux node. The brief is ready to become a Day 1 script once the decisions in Section 19 are answered and the resolutions in Section 18 are accepted.

**The ten findings that matter most, in priority order:**

1. **Inference backend must be decided (D1).** The brief drifted between llama.cpp, Ollama, and llama-server. On Ubuntu 26.04, where AMD's ROCm does not yet install cleanly on the host kernel, the LLM layer depends on the Vulkan backend. llama-server's Vulkan path is the validated one on this chip. Ollama's Vulkan path is newer and less proven. Recommendation: llama-server primary, Ollama optional. See Section 5 and Section 18 C1.
2. **The PyTorch engine layer needs ROCm, and the host cannot supply it on 26.04 (RESOLVED, Section 3.4).** The resolution is to run every PyTorch engine inside a container that carries its own ROCm userspace, which is exactly how the working Strix Halo image/video toolboxes are built. The host provides only the kernel driver.
3. **No component was ever named as the single memory arbiter (RESOLVED, Section 4.2).** With four LLMs, a router model, speech, and a dozen PyTorch engines all wanting the same unified memory, one service must own every load and unload. It is defined here as the Engine Arbiter and is a hard requirement of the orchestrator.
4. **The vault and the estate memory design conflicted (RESOLVED, Section 10.5).** An earlier version put Arthur's estate memory inside the vault; the vault was later simplified to "top-secret files only, locked when not in use." Estate memory cannot live in a folder that is locked most of the time. Resolution: estate memory lives on the always-mounted encrypted data volume; the vault is a separate, on-demand encrypted folder.
5. **Disk encryption was never specified (GAP, Section 3.5).** A node holding medical, legal, and financial data with no encryption at rest is a gap. LUKS2 on both drives, auto-unlocked at boot by the AMD firmware TPM, with a recovery key stored off-node.
6. **Domain injection must be size-bounded (Section 8.4).** Thirty domain profiles with seven fields each cannot be injected freely. Prompt processing is this machine's weakest capability; a bloated system prompt on every dispatch costs a minute of prefill. Hard limits: three domains per dispatch, compact form of each, stable-prefix ordering to preserve the KV cache.
7. **The Cloudflare API token must move (Section 12.3).** It is currently in a plain-text file. Scoped token, restricted permissions, outside any backup or repository path.
8. **Two governance inputs are still missing (D8, D9).** The list of actions ATLAS may never take without the Principal, and the retention rule for chats and logs. Proposed defaults are in Section 16 and become binding unless changed.
9. **Fictitious directors corresponding externally under their own names, combined with the "never disclose AI nature" rule, is a legal exposure that belongs in the risk register (R12).** The approval gate is the control; Gideon's tier check is the mitigation. The brief keeps the rule; the risk is recorded, not argued.
10. **Day 1 is four phases, not one script (Section 17).** Platform, services, core LLM pull, multimodal engine build. Each phase ends with a printed pass/fail table. Phases 3 and 4 run detached and resumable.

---

## 1. Mission and principles

**AGREED — Identity.** A.T.L.A.S.: a zero-cloud, autonomous AI node on bare metal, serving one user, the Principal, through a single interface, with two personas (Ren Ackerman, Arthur Sterling), an eight-director Shadow Cabinet, thirty domain knowledge profiles, twenty-three cross-domain task forces, and a set of autonomous protocols.

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

**AGREED — Machine.** MINISFORUM MS-S1 Max.

| Component | Specification | Design consequence |
|---|---|---|
| CPU | AMD Ryzen AI Max+ 395, 16 Zen 5 cores, 32 threads | Ample for orchestrator, Celery CPU workers, vector DB, browser automation, CPU-only tools (Radiance, EnergyPlus, KiCad, Docling) |
| GPU | Radeon 8060S, 40 CUs, RDNA 3.5, ROCm target gfx1151 | Runs all inference. Prompt processing is compute-bound and is the machine's weakest capability |
| NPU | XDNA 2, ~50 TOPS | Not used. Linux LLM tooling is Windows-first. Zero design dependency |
| Memory | 128 GB LPDDR5x-8000, 256-bit bus, soldered | ~256 GB/s theoretical, 210–230 GB/s achieved. Decode speed = bandwidth ÷ active weight bytes. Not upgradeable |
| GPU-addressable memory | ~110–120 GB on Linux via GTT | The reason to buy this machine |
| Power | 130 W sustained, 160 W peak, 320 W internal PSU | Desktop-class TDP for this chip; sustained inference holds. Expect audible fans |
| Expansion | PCIe 4.0 x16 slot wired x4 | Future discrete GPU. A CUDA-locked tool (NVIDIA Modulus/PhysicsNeMo) would need an NVIDIA card here |
| Network | Dual 10GbE, Realtek RTL8127 | **VERIFY V1**: driver present on the 26.04 kernel |
| Storage 1 | 8 TB NVMe, PCIe 4.0 x4 | Models, engines, agent data, vector stores. All hot-loaded weights live here |
| Storage 2 | 4 TB NVMe, PCIe 4.0 x1 (~2 GB/s) | Operating system, logs, cold storage archives, restic backups |

**AGREED — Expected inference performance (community benchmarks on this chip).**

| Model class | In memory | Decode |
|---|---|---|
| 8B dense, 4-bit | 5 GB | 40–50 tok/s |
| 30B MoE, 3B active | 18 GB | 70–100 tok/s |
| 32B dense, 4-bit | 19 GB | 10–12 tok/s |
| 70B dense, 4-bit | 40 GB | ~5 tok/s |
| 120B MoE, 5B active (gpt-oss-120b) | 63 GB | 30–55 tok/s |
| 120B MoE, 10–12B active (Nemotron 3 Super, Qwen3.5-122B) | 65–70 GB | 18–19 tok/s |

Prompt processing: ~350 tok/s stock, ~1,000 tok/s tuned, measured on a 7B model; larger models scale down proportionally. **This number drives the prompt-cache and injection-size rules in Sections 4.4 and 8.4.**

---

## 3. Platform and operating system

### 3.1 Operating system — AGREED with one refinement

- **Ubuntu 26.04 LTS Server.** Chosen by the Principal for its newer kernel and compiler. Server, not Desktop: the node is headless and administered from the Principal's Windows PC via SSH and Cockpit. **RESOLVED**: an earlier recommendation of Desktop as a safety net for a Linux novice is withdrawn; Cockpit's in-browser terminal serves that purpose without a desktop session's memory and GPU cost.
- **Kernel 7.0** ships with 26.04 and includes the amdgpu driver for gfx1151.

### 3.2 BIOS — AGREED

| Setting | Value | Why |
|---|---|---|
| UMA frame buffer | Smallest offered, 512 MB if available | On Linux the GPU takes memory dynamically via GTT; a fixed carve-out only wastes it |
| IOMMU | Enabled | Required for containers passing GPU devices |
| fTPM | Enabled | Required for LUKS auto-unlock (Section 3.5). **VERIFY V2** |
| Secure Boot | Disabled or enrolled for the ROCm/DKMS path | Avoids module-signing friction. **DECISION D2** |

### 3.3 Kernel parameters — AGREED, VERIFY V3

```
amdgpu.gttsize=131072 ttm.pages_limit=31457280
```

These expose ~128 GB of GTT and raise the pinned-page limit. They were validated on 6.x kernels; V3 confirms they still apply unchanged on 7.0 and that `llama-cli --list-devices` reports the full budget.

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
| 4 TB x1 | `/` (OS), `/var/log`, `/srv/cold`, `/srv/backups` | Ubuntu, logs, pruned-memory cold storage, restic repository |
| 8 TB x4 | `/srv/atlas` | `models/` (GGUF weights), `engines/` (PyTorch weights), `data/` (ChromaDB, graph store, SQLite), `workspace/`, `sandbox/`, `vault/` (encrypted container), `staging/` |

**NEW — Encryption at rest (was unspecified).**

- LUKS2 on the 8 TB data volume and on the OS volume.
- Auto-unlock at boot via `systemd-cryptenroll --tpm2-device=auto`, bound to the firmware TPM, so the headless node boots without a keyboard.
- A recovery passphrase is generated once and stored off-node with the backup passphrase (Section 10.6). **DECISION D3**: where the Principal keeps it.
- The vault (Section 11) is a second layer, gocryptfs, on top of the already-encrypted data volume; it protects top-secret files even while the node is running and unlocked.
- Swap is disabled. `/tmp` is tmpfs. Nothing transient touches disk unencrypted.

### 3.6 Base services — AGREED

- SSH, key-only, password auth off, listening on LAN and WireGuard interfaces only.
- `ufw` default deny inbound; allow SSH, Cockpit, Open WebUI, ntfy on LAN and WireGuard; allow UDP 51820 from anywhere. Default deny outbound except the allowlist (Section 12.5).
- Cockpit for system health and in-browser terminal.
- Docker with the GPU device nodes passed to containers that need them; the service user in `render` and `video` groups.
- No desktop environment.

---

## 4. Memory model and engine arbitration

### 4.1 Budget — AGREED, unified to Linux numbers

**RESOLVED (C3):** the brief carried both Windows (96 GB fixed) and Linux (~120 GB dynamic) numbers. Windows is gone. These are the binding numbers.

| Consumer | Budget | Notes |
|---|---|---|
| Ubuntu Server, Docker, Cockpit | 3 GB | |
| Open WebUI, ChromaDB, graph store, embedding model | 4 GB | |
| Eleanor's resident router model, 4B class | 4 GB | Always loaded |
| Kokoro, Whisper Large-v3-Turbo, PyAnnote | 3 GB | Always loaded |
| Orchestrator, Celery workers, Redis, Sentinel, ntfy, WG-Easy | 1–2 GB | |
| **Always-resident subtotal** | **~16 GB** | |
| Largest single LLM engine | 63–70 GB | One at a time |
| KV cache headroom beside it | 30–40 GB | 8 parallel slots at 32k fits every engine (Section 4.3) |
| **Or** one or two multimodal engines in place of the LLM | 5–40 GB each | Section 15 sizing |

### 4.2 Engine Arbiter — NEW, hard requirement

Nothing in the earlier brief named the component that enforces "one large engine at a time." It is defined here.

**The Engine Arbiter** is a single service inside the orchestrator through which every load and unload of any weight-bearing process passes: the four LLMs, Meditron, every Phase 4 engine, Chatterbox when invoked. Rules:

1. It holds a ledger of currently resident engines and their measured footprint.
2. A load request states the engine and the context or batch size. The Arbiter computes the projected footprint against the live budget and either grants, queues, or refuses.
3. Before loading, it confirms the previous engine's memory has actually been released, by polling the GPU memory counters, not by trusting the process exit. This check is the difference between a stable node and one that crashes on the second swap.
4. It never preempts an engine mid-generation. A Sentinel job, a Celery task, or a second persona waits for the current generation to finish, then swaps.
5. Deep Think and any multi-branch job must request their full footprint up front; if it does not fit, the Arbiter downgrades the depth tier (Section 9.1) rather than attempt the load.
6. Every decision is logged with the task ID from the Celery layer (Section 9.7).

### 4.3 KV-cache protocol — AGREED, corrected

**RESOLVED (C4):** the proposed protocol mixed shipped features with unmerged research. Only shipped features are adopted.

| Setting | Value | Reason |
|---|---|---|
| Flash Attention | On | Prerequisite for cache quantisation |
| KV cache type, Arthur's engines (Nemotron, Qwen3.5) | `q8_0` | Precision-sensitive audit and legal work |
| KV cache type, Ren's engines (gpt-oss, abliterated) | `q4_0` | More headroom for prose and multi-branch Deep Think |
| Per-model quantisation check | Day 1 verification script | **VERIFY V4**: unsupported architectures silently fall back to full precision |
| Context size | Explicit per model, never the default | Default contexts are small and silently truncate agent history |
| Context shift with `n_keep` | On; `n_keep` covers system prompt + persona directive + router state | Anchors never evicted; generation never hard-stops |
| Parallel slots | 8 | KV cost at 32k × 8 slots: gpt-oss < 20 GB, Nemotron < 10 GB, Qwen3.5 < 15 GB |
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

### 5.1 The four core engines — AGREED

| Engine | Architecture | Disk, 4-bit | Decode | Role |
|---|---|---|---|---|
| gpt-oss-120b | 117B MoE, 5.1B active, MXFP4 | 63 GB | 30–55 tok/s | Default resident engine. Ren, Helena, Victor, Gideon, Minerva |
| gpt-oss-120b abliterated (Huihui, MXFP4 GGUF) | Same weights, refusals removed | 63 GB | Same | Valerie default; Ren on override |
| Nemotron 3 Super | 120B hybrid Mamba MoE, 12.7B active | 65–70 GB | ~18 tok/s | Arthur default. Silas, Alaric |
| Qwen3.5-122B-A10B | 122B MoE, 10B active, vision-capable, 256k context, multilingual | 65–70 GB | 18–19 tok/s | Override engine for long documents, images, drawings, future languages |

All four are supported by llama.cpp. No two fit together. Swapping is inherent and is managed by the Engine Arbiter, not avoided.

**Optional fifth, Phase 3:** Meditron-70B, dense, ~40 GB at 4-bit, single-digit tok/s. Secondary cross-check for Minerva only. **RESOLVED (C5):** it runs through llama.cpp like the others, not through the PyTorch layer.

### 5.2 Backend — DECISION D1, with recommendation

| Option | Vulkan on 26.04 host | Slot-level prompt cache | Nemotron 3 Super GGUF support | Maturity on gfx1151 |
|---|---|---|---|---|
| llama-server (llama.cpp) | Mature, validated by multiple Strix Halo builders | Yes, per slot, save/restore | Yes | Highest |
| Ollama | Vulkan backend introduced as experimental; ROCm backend needs host ROCm, which 26.04 lacks | Coarser | Had loader incompatibilities with this model earlier in 2026 | Lags llama.cpp |

**Recommendation:** llama-server primary. Ollama only if the Principal has a specific reason. Both expose an OpenAI-compatible API, so the orchestrator is identical either way. Appendix B gives the equivalent settings for both.

### 5.3 Resident small models — DECISION D4

| Role | Requirement | Candidate |
|---|---|---|
| Eleanor / router / classifier | ~4B, instruction-following, fast, always resident | Qwen3.5 4B-class instruct |
| Embeddings | Multilingual-ready, strong retrieval | bge-m3 or nomic-embed-text-v2 |
| Reranker, optional | Improves RAG precision | bge-reranker-v2-m3 |

D4 asks the Principal to accept these defaults or name alternatives.

### 5.4 Model swapping — AGREED

| Step | Linux, from the x4 NVMe |
|---|---|
| Read 65 GB from disk | 10–15 s |
| Release previous engine and allocate | 5–10 s |
| Total per swap | 10–25 s |

Swaps are minimised by grouping work by engine (Section 6.3), never eliminated.

---

## 6. Cognitive architecture

### 6.1 The two hemispheres — AGREED

| | Ren Ackerman — Vanguard, Prime Director | Arthur Sterling — Architect, Estate Manager |
|---|---|---|
| Hemisphere | Offensive, creative, expansive, outward-facing | Defensive, regimented, private, guardian of personal data |
| Domain mandate | Corporate, Enterprise, Infrastructure, AEC | Estate, Private Health, Logistics, Family |
| Default engine | gpt-oss-120b | Nemotron 3 Super |
| Override engine | gpt-oss-120b abliterated, on Principal's order | Qwen3.5-122B-A10B for deep document reasoning |
| Sampling | High temperature (0.8) as Deep Think Generator | Low temperature, not zero, as Deep Think Adversary |
| Deep Think role | Generator: three divergent trajectories | Adversary: rubric-scored verdict, weak concepts killed |
| Code path | Writes sandbox code | Audits code for memory and safety before execution, as a second layer over the OS-level sandbox cap |
| Directors | Gideon, Silas, Valerie, Helena, Eleanor | Alaric, Minerva, Victor |

**RESOLVED (C6):** the original brief bound Arthur's override to the abliterated engine "for legal dissection." Abliteration removes refusals and slightly increases errors; it does not improve reasoning. Arthur's override is Qwen3.5 for long-document work. The abliterated engine is reserved for work that refusals actually block.

### 6.2 The Shadow Cabinet — AGREED, final engine map

| Director | Division | Remit | Default engine | Override | Speaks externally |
|---|---|---|---|---|---|
| Gideon Vance | Corporate | Legal & Compliance | gpt-oss-120b, high reasoning, retrieval-grounded | Qwen3.5-122B for long contracts | Yes, sensitive tier |
| Silas Thorne | Corporate | CFO: capital, audit, banking | Nemotron 3 Super + code interpreter for all arithmetic | none | Yes, standard tier |
| Valerie Cross | Corporate | Infrastructure, DevOps, AEC, cyber, AEGIS Sandbox | gpt-oss-120b abliterated | Qwen3.5-122B for drawings and images | Yes, standard tier |
| Helena Frost | Corporate | Communications, brand, PR, crisis | gpt-oss-120b | none | Yes, standard tier |
| Eleanor Croft | Corporate | Routing, scheduling, triage; the 4-Way Router's classifier | Resident 4B model | none | Yes, routine tier (scheduling) |
| Alaric Stone | Estate | Physical assets, security, threat mitigation; Sentinel threat watch | Nemotron 3 Super | none | Yes, sensitive tier |
| Minerva Hale | Estate | Private health, longevity; medical data | gpt-oss-120b, retrieval-grounded | Qwen3.5-122B for records and scans; Meditron-70B cross-check | Yes, sensitive tier |
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

All thirty domain profiles and all twenty-three task forces are retained. Nothing was removed. The consolidation proposed during review survives only as **tags**: each domain and task force carries a hemisphere, an owning director, and a JIT priority tier. The tags decide default routing; they delete nothing.

### 8.2 The thirty domains — owners and tiers

Tier A loads without hesitation. Tier B loads on a clear task-force or keyword match. Tier C loads only on explicit match and is expected to be rare.

| # | Domain | Hemisphere | Owner | Tier |
|---|---|---|---|---|
| 1 | Systems Architect & Cybersecurity | Corporate | Valerie | A |
| 2 | Growth Executive (marketing, sales, PR, SEO) | Corporate | Helena | A |
| 3 | Customer Success & Experience Lead | Corporate | Helena | B |
| 4 | Data Scientist & AI Engineer | Corporate | Valerie | A |
| 5 | CFO & Controller | Corporate | Silas | A |
| 6 | General Counsel & CHRO (AU construction, strata) | Corporate | Gideon | A |
| 7 | Development Director, Property & Real Estate | Corporate | Valerie, with Silas | A |
| 8 | AEC Computational Designer & Visualizer | Corporate | Valerie | A |
| 9 | Industrial & Energy Engineer | Corporate | Valerie | B |
| 10 | Chief Medical Officer & Risk Underwriter | Estate | Minerva | A |
| 11 | Game Director & Systems Designer | Corporate | Helena, with Valerie | C |
| 12 | Creative Director & Narrative Designer | Corporate | Helena | B |
| 13 | Dean of Academia & Pedagogy | Estate | Arthur, tagged to 14 | C |
| 14 | Private Family Advisor & Estate Guardian | Estate | Arthur | A |
| 15 | Chief Longevity Officer & Performance Physiologist (incl. bioinformatics subspecialty) | Estate | Minerva | A |
| 16 | COO & Process Architect | Corporate | Eleanor, with Silas | B |
| 17 | High-Stakes Negotiator & Strategic Diplomat | Corporate | Ren, with Gideon | B |
| 18 | Global Asset Guardian & HNW Concierge | Estate | Victor, with Alaric | B |
| 19 | Travel, Leisure & Global Hospitality Director | Estate | Victor | A |
| 20 | Chief AI Officer & Agentic Systems Architect | Corporate | Valerie; self-modification gated (Section 16.3) | B |
| 21 | Chief Investment Officer & Quant Strategist | Corporate | Silas | A |
| 22 | Director of OSINT & Executive Protection | Estate | Alaric | A |
| 23 | Venture Partner & Private Equity Director | Corporate | Silas, with Gideon | B |
| 24 | Director of Philanthropy, CSR & Community | Both | Arthur and Ren | B |
| 25 | Geopolitical Strategist & Public Affairs Lead | Corporate | Ren | B |
| 26 | Cultural Asset & Fine Art Curator | Estate | Alaric, with Silas | C |
| 27 | Director of Digital Influence & Synthetic Media | Corporate | Helena; phrasing softened (Section 18 C9) | B |
| 28 | Chief Behavioral Architect & Neuro-Optimizer | Corporate | Helena, with Minerva | C |
| 29 | Frontier Technologies & Spatial Engineer | Corporate | Valerie | B |
| 30 | Sovereign Architect & Network State Strategist | Corporate | Ren, tagged to 25 | C |


Items from the earlier draft list that had no home in the thirty: Bioinformatics & Genomic Sequencing is folded into domain 15 as a subspecialty. Psychological Operations & Human Engineering is not added as a domain; its legitimate negotiation and behavioural-economics content already lives in domains 17 and 28, and operations aimed at named individuals are outside what this system builds capability for. Bare-Metal OS Exploitation and Quantum Computing are held in reserve pending a stated need (**DECISION D5**).

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

1. Each domain profile is stored in two forms: the full seven-field profile (reference, retrievable) and a compact card of roughly 300 to 500 tokens (injected). The card carries the strategic frame, the compliance list for Australia, the cognitive method, and the tooling names.
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

The domain profiles' original line, "Stateless MRTR, dispatch payload and decouple," is replaced in all thirty by:

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
| Deep | Standard plus a second expansion and scoring round, Qwen3.5 as third opinion on documents | 3 to 4 | 15 to 25 min |

**Corrections adopted:** Nemotron runs at low temperature, not zero, because reasoning models loop at exactly zero. Scores out of 100 are rubric-based judgements with named criteria, not mathematics; where a real figure exists, Silas computes it through the code interpreter and Arthur scores against the computed number. Phases are batched, never round-by-round ping-pong between engines.

### 9.2 Cross-checking — AGREED

Any answer involving numbers, legal claims, or code can be verified by a second persona on a different engine. Cheap version: the currently loaded engine with a critic prompt. Strong version: an engine swap. The router applies the strong version automatically to the sensitive tier and to anything the Principal marks important.

### 9.3 Sentinel Protocol (autonomous monitoring) — AGREED, corrected

| Element | Design |
|---|---|
| Schedule | systemd timer, hourly. **RESOLVED (C11):** not a WSL2 cron; there is no WSL |
| Feeds | Outbound allowlist only. Starting set: CoinDesk, an index feed for ASX and US markets, RSS news, node telemetry. **DECISION D6** confirms the list |
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
| Retention | 30 nightly, 12 monthly (proposed under D9) |
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

A second memory layer answering relationship questions, "what else touches this contract, this company, this person," which vector similarity cannot answer. It supplements ChromaDB; it does not replace it. **RISK R6:** Microsoft's GraphRAG indexing makes many LLM calls per document and would be slow here. Indexing runs as a Celery `gpu` task during idle periods using Eleanor's resident model for extraction, escalating to a large engine only for documents the Principal marks important. **DECISION D7:** GraphRAG versus the lighter LightRAG for the same job; recommendation LightRAG for this hardware.

### 10.3 Scar collection — see 9.4.

### 10.4 Retention — DECISION D9, proposed defaults

Open WebUI keeps its own chat database, so ChromaDB supplements rather than literally replaces chat logs. Proposed rule, binding unless changed: chats are retained 90 days in Open WebUI, then summarised into the Vector Cortex and purged; operational logs 30 days hot on the second drive, then archived; Sentinel logs 12 months; scars permanent under curation; backups per Section 9.5. Vault sessions are never retained (Section 10.5).

### 10.5 Vault and memory — RESOLVED (C16)

An earlier version placed Arthur's estate memory collection inside the vault. The vault was then simplified to "top-secret files, opened on command, locked when not in use." A memory store inside a folder that is locked most of the time would leave Arthur without memory most of the time. Resolution:

- Estate memory lives on the always-mounted LUKS data volume like everything else. It is protected at rest by LUKS and in operation by the router's hard rules.
- The vault holds top-secret files only. Anything read from the vault is tagged `vault` for the life of that session; vault-tagged content is not written to any memory collection, not summarised, and not backed up outside the vault itself, unless the Principal explicitly says "remember this."

### 10.6 Backups — see 9.5. Passphrases and recovery keys are the Principal's responsibility off-node (D3).

---

## 11. Vault — AGREED, simplified

An encrypted gocryptfs folder at `/srv/atlas/vault`, on top of the LUKS data volume. Opened by a button in the interface that prompts for the passphrase directly; the passphrase never passes through a model or a chat message. Locked by command and auto-locked after an idle period the Principal sets (proposed 15 minutes). No RAM scrubbing, no forensic claims; those were removed from the design at the Principal's instruction. Contents are backed up as ciphertext only. Session handling of vault content is in Section 10.5.

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

### 12.3 Cloudflare token — RISK R7, action required before Day 1

The token is currently in a plain-text file named `CLOUDFLARE.txt`. Required: create a scoped API Token with `Zone:DNS:Edit` on `sovereign-node.link` only, never the Global API Key; store it in an environment file with mode 600 owned by the updater's service account, outside `/srv/atlas`, outside any git-tracked path, and outside restic's include set; delete the plain-text file. The token value has not been shared in this conversation, so no rotation is required, only relocation.

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

ATLAS sees on demand. The Principal, or a defined trigger, says "look now"; one frame is sampled through the resident vision engine (GLM-4.6V-Flash or Qwen2.5-VL, D10). Continuous video inference is not a default: at any real frame rate it would compete for the GPU against everything else and break the sequential design, and an always-on camera feed is a privacy commitment that deserves an explicit decision. Continuous monitoring exists only as a separately enabled capability under Alaric's estate-security domain, tied to specific cameras, with SAM 2 for tracking, and it is logged like any other sensitive action.

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
| Cross-platform build service | Android NDK plus Gradle for .apk, MinGW-w64 for .exe, containerised, the "OmniBuild-KVM" capability minus Apple | Domain 1 and 5, Valerie |
| Apple .ipa builds | Not on this node. Licensed cloud Mac runner or the Principal's own Mac dispatched over the network. DECISION D11 | Domain 1, Valerie |
| Playwright and browser automation | Text-based DOM browsing for platforms without an API | All directors, per tier |

### 15.2 Phase 4 engines — tiers

Green: build with confidence. Yellow: attempt with automatic fallback; never blocks the phase. Deferred: not in Day 1; ATLAS adopts when released or when hardware changes.

| Engine | Job | Tier and evidence | Footprint | Owner |
|---|---|---|---|---|
| Wan2.2 (in place of Wan2.1) | Video generation | Green: validated on gfx1151 by maintained toolboxes | 20 to 40 GB | Domain 12, Helena |
| FLUX.1 | Image generation, architectural visualisation with LoRA (the "Arch-DiT" imagery use) | Green: validated on gfx1151 | 12 to 24 GB | Domains 8 and 12 |
| GLM-4.6V-Flash (9B) | Vision-language, documents, drawings, screenshots, 128k context, MIT licence | Green: standard architecture | ~18 GB | General, D10 |
| Qwen2.5-VL 7B | Vision-language, alternative or cross-check to GLM | Green | 8 to 16 GB | General, D10 |
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
| Meditron-70B | Medical cross-check | Runs through llama.cpp in Phase 3, not here | ~40 GB | Domain 10, Minerva |
| Evo (Arc Institute) | Genomics | Deferred: port unverified, and this GPU lacks the FP8 hardware Evo's larger checkpoints expect | n/a | Domain 15 |
| NVIDIA Modulus / PhysicsNeMo | Physics simulation | Deferred: CUDA-locked, would need an NVIDIA card in the PCIe slot | n/a | Domains 8 and 9 |

### 15.3 Names that did not exist — RESOLVED (C17)

| Proposed name | Finding | Replacement in this brief |
|---|---|---|
| DeepRoute-AEC | No such model; the real MEP products are Revit-bound SaaS | OpenStudio/EnergyPlus + IfcOpenShell clash detection + Python calculations + Valerie orchestrating routing (15.1) |
| Lumina-PBR | No such engine | Radiance for physical light + Blender Cycles for the render (15.1, 15.2) |
| Arch-DiT | No such model | FLUX.1 with an architectural LoRA; parametric IFC via MCP4IFC |
| OmniBuild-KVM | No such tool; described capability is a build farm | Cross-platform build service (15.1); Apple path per D11 |
| BIM-GPT, LayoutGPT-3D | Real research papers, not installable products | Capability covered by MCP4IFC |
| RTLLM | A benchmark for grading LLM-written Verilog, not a generator | Coding engine prompted against its structure; KiCad for the physical side |

### 15.4 Storage impact — AGREED

| Set | Size |
|---|---|
| Four core LLMs | ~262 GB |
| Meditron-70B, optional | ~40 GB |
| Phase 4 engines, green and yellow | ~150 to 250 GB |
| Total weights | under 600 GB of the 8 TB drive |

Download time on 100 Mbps: roughly 6 hours for the core set, similar again for engines. Both phases run detached and resumable.

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

### 16.3 What ATLAS may never do without the Principal — DECISION D8, proposed defaults

Binding unless the Principal changes them:

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
- macOS virtualisation on non-Apple hardware is excluded (D11).

---

## 17. Day 1 Execution Protocol

Four phases. One entry command per phase. Every phase is idempotent: re-running skips what is complete. Every phase writes a log and ends with a printed pass/fail table. Phases 3 and 4 run detached under systemd so a dropped SSH session cannot kill them, and both are resumable at the file level.

### Phase 1 — Platform (reboot in the middle)

1. Pre-flight: confirm Ubuntu 26.04 Server, kernel 7.0, both NVMe drives present, fTPM enabled (V2), RTL8127 link (V1).
2. LUKS2 on the data volume, TPM2 enrolment, recovery key printed once for off-node storage (D3).
3. Mount layout per Section 3.5; swap off; tmpfs for `/tmp`.
4. System update; kernel parameters (V3); firewall baseline; SSH hardening; Cockpit.
5. Reboot. Post-reboot: `vulkaninfo` shows the Radeon 8060S; `llama-cli --list-devices` reports the expected memory budget.
6. Docker with GPU device passthrough; service account in `render` and `video`.
7. WG-Easy, Cloudflare dynamic DNS with the relocated scoped token (R7), ntfy.
8. **Gate:** table of V1, V2, V3, V5 results. Phase 2 does not start on a red row.

### Phase 2 — Engines and services (fast, no large downloads)

1. llama-server, Vulkan build; optional ROCm container for tuned prefill.
2. Redis, Celery workers (`cpu`, `gpu` queues), orchestrator scaffold with the Engine Arbiter, router, approval queue, task ledger.
3. Open WebUI with offline hardening (12.1); A.T.L.A.S., Ren, Arthur registered as models; the router Filter installed.
4. ChromaDB, graph store, embedding model (D4); Docling ingestion service.
5. Kokoro, Chatterbox, Whisper Large-v3-Turbo, PyAnnote 3.1 (V6).
6. Phase 2 tools (15.1): IfcOpenShell, Bonsai, MCP4IFC, Radiance, OpenStudio/EnergyPlus, KiCad CLI, Playwright, the cross-platform build container.
7. restic repository on the second drive; nightly timer; first backup; first restore test.
8. Sentinel timer (disabled until D6 confirms feeds), pruning timer.
9. Windows PC share mount unit, on-demand.
10. **Gate:** every service healthy; V6, V7 (listening test) recorded.

### Phase 3 — Core LLM pull (long, detached, resumable)

1. Pull gpt-oss-120b, gpt-oss-120b abliterated, Nemotron 3 Super (UD-Q4_K_XL class), Qwen3.5-122B-A10B (Strix Halo-tuned GGUF), with checksum verification. Meditron-70B if D12 is taken.
2. For each model, in turn: load through the Engine Arbiter; confirm the quantised KV cache actually applied (V4); measure decode and prefill at 512 and 8k tokens; measure swap time; unload; confirm memory returned.
3. **Gate:** printed table of load success, KV type, tok/s, swap seconds per model. This is the day-one load test the whole design depends on (V10).

### Phase 4 — Multimodal engines (long, detached, per-engine pass/fail)

1. Self-test the community PyTorch ROCm wheel inside the base container: tensor on GPU, matmul, a small diffusion step (V11).
2. Build green engines in order of value: GLM-4.6V-Flash, FLUX.1, Wan2.2, Florence-2, TimesFM or Chronos, Whisper-adjacent audio, UI-TARS 2.0, Rad-DINO, SAM 2 with the extension flag, Stable Audio Open, CosyVoice2, OpenVLA.
3. Attempt yellow engines: TRELLIS, Blender Cycles HIP with CPU fallback. Log pass or fail, never block.
4. Verify PointLLM (V8) and Clay or Prithvi (V9); mark deferred if they fail.
5. Register every passing engine with the Engine Arbiter with its measured footprint.
6. **Gate:** per-engine table: built, loaded, sample output produced, footprint, pass/fail/deferred.

### Expected durations

| Phase | Time |
|---|---|
| 1 | 30 to 60 minutes including reboot |
| 2 | 30 to 60 minutes |
| 3 | Download-bound: ~6 hours at 100 Mbps, under 1 hour at gigabit; plus ~20 minutes of load tests |
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
| C19 | Desktop recommended for a Linux novice, then Server chosen | Server with Cockpit (3.1) |
| C20 | Fish Audio requested, then excluded as non-local and research-licensed | Kokoro plus Chatterbox (14.1) |

---

## 19. Open decisions for the Principal

| # | Decision | Recommendation | Blocks |
|---|---|---|---|
| D1 | Inference backend: llama-server or Ollama | llama-server | Phase 2 |
| D2 | Secure Boot: disable, or enrol keys for any DKMS module | Disable on a headless node behind a firewall | Phase 1 |
| D3 | Where the LUKS recovery key and restic passphrase are kept off-node | Password manager plus a printed copy in a physical safe | Phase 1 |
| D4 | Router model and embedding model | Qwen3.5 4B-class instruct, bge-m3 embeddings, bge-reranker-v2-m3 | Phase 2 |
| D5 | Whether Bare-Metal OS Exploitation and Quantum Computing become domains | Hold in reserve until a concrete need | None |
| D6 | Sentinel feed list | CoinDesk, ASX and US index feed, RSS news, node telemetry | Sentinel enablement |
| D7 | Graph layer: Microsoft GraphRAG or LightRAG | LightRAG, lighter indexing on this hardware | Phase 2 |
| D8 | The never-without-the-Principal list (16.3) | Adopt the ten proposed items | Phase 2 |
| D9 | Retention rule for chats, logs, backups (10.4, 9.5) | Adopt proposed defaults | Phase 2 |
| D10 | Primary vision engine: GLM-4.6V-Flash or Qwen2.5-VL | Test both in Phase 4, keep the better reader as primary, the other as cross-check | Phase 4 |
| D11 | Apple .ipa build path | Principal's own Mac if one exists, otherwise a licensed cloud Mac runner, never OSX-KVM on this node | Phase 2 tool |
| D12 | Meditron-70B: include or skip | Include, optional, low priority | Phase 3 |
| D13 | Vault idle auto-lock period | 15 minutes | Phase 2 |
| D14 | Whether external directors get real mailboxes or aliases on the Workspace domain | Aliases on one Workspace mailbox per division, distinct display names and signatures | Phase 2 |

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
| R9 | RTL8127 10GbE driver missing on the 26.04 kernel | Low to medium | Medium | V1, fall back to the second port or a USB adapter |
| R10 | Sustained thermal load in a small chassis | Medium | Low | Monitor temperatures in Cockpit, the 130 W sustained rating is desktop-class |
| R11 | CGNAT prevents inbound WireGuard | Low, port forward already succeeded | High for remote access | V5 from mobile data, static IP from the ISP if needed |
| R12 | Fictitious directors corresponding as humans, AI non-disclosure | Medium | Medium to high, legal | Gideon's sensitive-tier check, approval gate, 16.5 |
| R13 | Abliterated engine used for external output | Medium | Medium | Abliterated output always passes the same gate, never routine tier |
| R14 | Chatterbox continuation quirk in production speech | Medium | Low | V7 testing, Kokoro fallback per line |
| R15 | Prompt bloat from domain injection makes every dispatch slow | High without limits | High, usability | Three-card limit, compact cards, stable-prefix ordering (8.4, 4.4) |
| R16 | Backup passphrase or LUKS key lost with the node | Low | Catastrophic | D3, off-node storage, quarterly restore test |
| R17 | PyAnnote gated model not accepted, diarisation silently absent | Medium | Low | V6 |
| R18 | Continuous vision enabled by drift rather than decision | Low | High, privacy | Separate capability flag under Alaric, logged, D8 item 7 |

---

## 21. Verification matrix: what Day 1 must prove

| # | Proof | Where |
|---|---|---|
| V1 | RTL8127 10GbE link up on the 26.04 kernel | Phase 1 pre-flight |
| V2 | fTPM present and enabled, TPM2 enrolment succeeds | Phase 1 step 2 |
| V3 | Kernel parameters accepted on 7.0, GPU reports the expected memory budget | Phase 1 post-reboot |
| V4 | Quantised KV cache actually applied per model, no silent fallback | Phase 3 step 2 |
| V5 | WireGuard reachable from mobile data via vpn.sovereign-node.link | Phase 1 step 7 |
| V6 | PyAnnote 3.1 gated model accepted and loading | Phase 2 step 5 |
| V7 | Voice casting listening test, Alaric and Gideon clones sourced | Phase 2 gate |
| V8 | PointLLM builds and runs on ROCm in the container | Phase 4 step 4 |
| V9 | Clay or Prithvi builds and runs | Phase 4 step 4 |
| V10 | All four core LLMs load, generate, swap, and release memory, measured tok/s within the expected bands | Phase 3 gate |
| V11 | Community PyTorch ROCm wheel passes tensor, matmul, and diffusion self-tests in the container | Phase 4 step 1 |
| V12 | Open WebUI makes no outbound connection after hardening (verified with the firewall log) | Phase 2 step 3 |
| V13 | restic backup completes and a restore verifies by checksum | Phase 2 step 7 |
| V14 | Engine Arbiter refuses a second large load while one is resident, and downgrades a Deep Think depth when the projected footprint exceeds budget | Phase 2 gate |
| V15 | Approval gate holds a standard-tier email until approved, routine-tier auto-sends and logs | Phase 2 gate |
| V16 | Router hard rule routes a "medical" message to Arthur even when the classifier disagrees, decision logged | Phase 2 gate |
| V17 | Sandbox memory cap kills a runaway process without affecting the node | Phase 2 gate |
| V18 | Vault opens by button, locks on idle, vault-tagged content absent from memory collections afterwards | Phase 2 gate |

---

## 22. Pre-execution checklist

Nothing runs until every box is ticked.

**Principal's actions**

- [ ] Answer D1 through D14, or accept the recommendations as written.
- [ ] Relocate the Cloudflare token per 12.3 and delete `CLOUDFLARE.txt`.
- [ ] Decide where the LUKS recovery key and restic passphrase will live off-node (D3).
- [ ] Create the Google Cloud OAuth client for Gmail, Calendar, and Drive; be available for the one-time browser authorisations during Phase 2.
- [ ] Confirm the Sentinel feed list (D6).
- [ ] Confirm which mailboxes or aliases directors will send from (D14).
- [ ] Source reference recordings for Alaric's and, if needed, Gideon's cloned voices.
- [ ] Confirm whether a Mac exists for iOS builds (D11).
- [ ] Have a monitor and keyboard available for Phase 1 only, in case first boot needs a hand.

**Build-side preconditions**

- [ ] Ubuntu 26.04 Server installed on the 4 TB drive; 8 TB drive unpartitioned.
- [ ] BIOS: UMA minimum, IOMMU on, fTPM on, Secure Boot per D2.
- [ ] LAN address reserved for the node on the router; UDP 51820 forward confirmed to that address.
- [ ] Internet bandwidth known, so Phase 3 and 4 durations can be planned.

**Accepted resolutions**

- [ ] C1 through C20 in Section 18 are accepted; where the Principal disagrees, the item returns to the decision list.

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

**Kernel (GRUB):** `amdgpu.gttsize=131072 ttm.pages_limit=31457280`

**llama-server, Arthur's engines:** `-fa on --cache-type-k q8_0 --cache-type-v q8_0 --ctx-size <explicit> --parallel 8 --keep <n_keep> --slot-save-path /srv/atlas/data/slots`

**llama-server, Ren's engines:** same with `--cache-type-k q4_0 --cache-type-v q4_0`

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

Hardware: MINISFORUM MS-S1 MAX product page and VideoCardz specification report. Platform: AMD ROCm Strix Halo system-optimisation guide; community Strix Halo local-LLM guides; LucRoot known-good ROCm llama.cpp recipe; llama.cpp discussion on the known-good Strix Halo stack; Phoronix Ubuntu 26.04 Strix Halo benchmarks. Models: Unsloth Nemotron 3 Super guide; Beinsezii Qwen3.5-122B-A10B Strix Halo GGUF; Huihui gpt-oss-120b abliterated MXFP4 GGUF; gpt-oss model card. KV cache: Ollama FAQ and environment reference; llama.cpp server README on `n_keep` and context shift; StreamingLLM paper. Engines: kyuz0 and matthewhand Strix Halo ComfyUI toolboxes; TRELLIS.2 ROCm forks; Evo2StrixHalo port; SAM 2 ROCm issues; MCP4IFC project page and paper; Radiance at LBNL; Genusys and Auto BIM Route for the MEP market. Voice: Kokoro-82M VOICES.md; Trelis and Pinggy 2026 TTS comparisons; Qwen3-TTS repository; Fish Audio S2 licence page. Legal: Apple EULA analyses of OSX-KVM.

*End of document.*
