# A.T.L.A.S. Framework — Deep Sweep Review (Pre-Execution Baseline)

| Field | Value |
|---|---|
| Document | ATLAS_FRAMEWORK_REVIEW.md |
| Version | 0.3.4 — Ubuntu Server re-confirmed over Fedora 44 KDE Plasma (C27); five Day 1 script defects fixed; support, xrdp and time-sync facts corrected |
| Date | 2026-10-08 |
| Supersedes | v0.3.3 (2026-10-05); v0.3.2 (2026-10-05); v0.3.1 (2026-09-27); v0.3 (2026-09-21); v0.2.1 and v0.2 (2026-09-21); v0.1 (2026-09-18) |
| Scope | Everything agreed in the design conversation, through the Principal's completed confirmation workbook and the September hardware change |
| Purpose | A single consolidated statement of the framework, followed by an alignment audit: contradictions resolved, risks, and what Day 1 must prove before anything is trusted |
| Status of this document | **Closed build baseline.** Every decision confirmed, every resolution accepted, every risk acknowledged, every pre-execution item ticked in the workbook returned 2026-09-21; the risks added since (R22 to R25) were acknowledged by the Principal on 2026-10-08. Nothing has been executed. The Day 1 scripts under `scripts/day1/` are written against this document; the fact-checking that preceded them corrected the baseline in the places listed in Section 23. D15, reopened by the build, was closed by the Principal on 2026-10-05 as option (a): Secure Boot enabled. Policy v0.3.3 (the Principal, 2026-10-05): a missing input from the Principal never stops a phase; the scripts ask once, defer the item and keep a to-do list for the live ATLAS. On 2026-10-08 the Principal re-confirmed Ubuntu Server after a sourced comparison with Fedora 44 KDE Plasma (C27: "stability is key to a good AI node"); the comparison also found five script defects, fixed in v0.3.4 (Section 23, S32 to S38). |

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

**Verdict.** The framework is coherent, fully decided, and buildable. All fifteen decisions are closed (D15, Secure Boot, closed 2026-10-05), all twenty original contradictions accepted, and seven further resolutions have been added since v0.1 (C21 to C26 in v0.2, C27 in v0.3.4). The Day 1 scripts are written against this document and, since v0.3.3, never stop on a missing input from the Principal: they ask once, defer, and keep a to-do list for the live ATLAS.

**What changed since v0.1, in order of consequence:**

1. **The machine changed (Section 2).** GMKtec EVO-X5 Pro replaces the MINISFORUM MS-S1 Max: 192 GB of memory instead of 128 GB, 273 GB/s instead of ~256, and both NVMe slots at four lanes instead of one fast and one slow. The GPU reports the same `gfx1151` identifier, confirmed by the Principal, so every piece of community ROCm and Vulkan work carries over unchanged and risks R1 and R3 stand exactly as written.
2. **Quantisation rises across the board (Section 5.1).** The extra memory buys accuracy. Nemotron 3 Super and Qwen3.5-122B move from 4-bit to `Q8_0`; Qwen2.5-VL-72B and Meditron run at `Q8_0`. The two gpt-oss engines stay at MXFP4 because that is their native released form, not a compromise.
3. **A fifth core engine joins (Section 5.1).** DeepSeek V4 Flash, 284B total and 13B active, at `UD-Q4_K_XL`, 155 GB. It is the Apex engine: at 4-bit it reads roughly half the weight bytes per token that Nemotron reads at 8-bit, so it is both far larger and about twice as fast, and it is reserved for Deep Think's deep tier, TF_OMEGA, and strong cross-checks.
4. **Two engines may now be resident, but only one ever generates (Section 4.2).** The extra memory removes the swap between a text engine and the vision engine. Simultaneous generation is not adopted at all: the memory bandwidth is shared, so two streams would each run at half speed. The Celery GPU queue keeps its single worker.
5. **The node gets a graphical desktop (Section 3.1).** Ubuntu Server 26.04.1 stays the base, with XFCE added on top and xrdp for remote graphical access from the Principal's Windows PC. This is not the full Ubuntu Desktop image; XFCE costs roughly 300 to 500 MB idle against GNOME's 1 to 1.5 GB, and the AI system itself remains browser-served and independent of any desktop session.
6. **Six domains added, bringing the roster to thirty-six (Section 8.2).** Five further areas the Principal named are folded into existing domains as explicit subspecialties rather than duplicated as new cards.
7. **The vision layer narrowed to one engine (Section 15.2).** GLM-4.6V-Flash and Qwen2.5-VL-7B and -32B are all dropped. Qwen2.5-VL-72B at `Q8_0` is the sole vision engine, chosen for quality with its roughly 3 tokens per second accepted.
8. **Apple `.ipa` builds removed entirely (Section 15.1).** The Principal owns no Mac and rules out a cloud runner. Android and Windows builds stay; iOS source is still written, but compiled elsewhere whenever Mac access exists.
9. **Ubuntu 24.04.5 evaluated and rejected (Section 3.1); Fedora 44 KDE Plasma evaluated and rejected (C27, v0.3.4).** 24.04.5 shares the 7.0 kernel through HWE but stays on Mesa 25.2; 26.04.1 wins on two further years of support (May 2031), a newer graphics stack and toolchain, and its one disadvantage, host ROCm, does not apply to a containerised design. Fedora 44 would need a major-version upgrade about once a year and its KDE edition cannot offer a fresh Remote Desktop login without autologin once Plasma 6.8 ships; Ubuntu's fixed 7.0 kernel series is the stability the node needs.
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
- **RESOLVED (C21): 24.04.5 evaluated and rejected. Corrected in v0.3.4.** Its point release brings the 7.0 kernel through HWE, but its Mesa stays on 25.2.8 (noble-updates) against 26.0.8 on 26.04 (VERIFIED on packages.ubuntu.com, 2026-10-08), so hardware support is close, not identical. 26.04.1 wins on standard security maintenance to **May 2031** against May 2029 (ubuntu.com/about/release-cycle; the 26.04 release notes say "until April 2031"; Expanded Security Maintenance through Ubuntu Pro runs to May 2036), a newer graphics stack and a newer toolchain. 24.04's one real advantage, officially supported host ROCm, is irrelevant because ROCm runs only inside containers here (Section 3.4).
- **RESOLVED (C27, v0.3.4): Fedora 44 KDE Plasma evaluated and rejected; Ubuntu Server re-confirmed by the Principal on 2026-10-08 ("stability is key to a good AI node").** A sourced comparison (seven research strands, each decisive claim checked by two independent verifiers) found: Fedora 44 reaches end of life around June 2027, so the node would need a hands-on major-version upgrade about once a year, four or five times before 2031, each a reboot that TPM unlock, Docker and Remote Desktop must survive unattended; Fedora moved Fedora 44 through four kernel series in five months, while Ubuntu keeps the 7.0 series the Day 1 kernel parameters were tuned for; Fedora's KDE edition is Wayland-only and Plasma 6.8 (due 2026-10-14) removes the X11 session that xrdp needs, leaving only KDE's own remote tool, which joins an already-logged-in session and therefore needs autologin (contrary to Appendix B, R21); AMD's ROCm support list names Ubuntu 26.04.1 for this exact chip and not Fedora. Fedora's real advantages were recorded: a current xrdp (0.10.6.1, with the 2026 security fixes), newer Mesa and firmware, and a Strix Halo community that mostly runs Fedora. The first is mitigated on Ubuntu (R24); the others matter only to the host-built llama.cpp Vulkan backend and can reach Ubuntu through its opt-in HWE kernels if V11 or Phase 3 ever needs them. If the Principal later wants the KDE look, Plasma 6.6 with its X11 session is packaged in 26.04 and could replace XFCE in step 5b with a few lines, behind V19; not adopted.
- **Support coverage (v0.3.4).** Ubuntu's free five-year commitment covers the `main` component. Several packages the node relies on are in `universe`, which gets community fixes only: the desktop and remote layer (xfce4, xrdp, xorgxrdp), Cockpit (cockpit-ws, a login page on the LAN), tpm2-tools and gocryptfs. The DNS fence only looks like one: the `dnsmasq` package (unit and configuration) is in `universe`, but its daemon ships in `dnsmasq-base` in `main` and receives Canonical security updates (2.92-1ubuntu0.4 in resolute-security, packages.ubuntu.com, 2026-10-08). Day 1 keeps Ubuntu Pro off (no telemetry from any installed component, Section 1). For xrdp this matters most, because it has known unpatched flaws reachable before login (R24); Cockpit's login page is in the same support position, has no comparable open list today and is on the watch-list (Section 23); the rest is accepted.
- **RESOLVED (C22): the amd64v3 and amd64v4 archive variants are not adopted.** The benefit falls on CPU-bound distribution packages, not GPU inference, and llama.cpp and the PyTorch containers are already compiled against this exact Zen 5 chip. Canonical kept the standard baseline as 26.04's default after a rollout with known problems; no amd64v4 archive exists. Conversion remains possible post-install if ever wanted.

### 3.2 BIOS — AGREED

| Setting | Value | Why |
|---|---|---|
| UMA frame buffer | Smallest offered, 512 MB if available | On Linux the GPU takes memory dynamically via GTT; a fixed carve-out only wastes it |
| IOMMU | Enabled | Required for containers passing GPU devices |
| fTPM | Enabled | Required for LUKS auto-unlock (Section 3.5). **VERIFY V2** |
| Secure Boot | **Enabled (D15 closed, supersedes D2)** | Ties the TPM2 PCR 7 unlock to this OS image; no out-of-tree kernel modules in this stack, so no signing friction. The Principal enables it in the BIOS before Phase 1 (Section 22) |

### 3.3 Kernel parameters — AGREED, VERIFY V3

```
amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000
```

**Amended in v0.3.1 (Section 23):** `ttm.pages_limit` is the parameter of record on kernel 7.0; `amdgpu.gttsize` is still honoured but logs a deprecation warning, which V3 treats as expected. `amdgpu.lockup_timeout` is added because DeepSeek V4 Flash triggers a Vulkan DeviceLost on current kernels without it (llama.cpp issue #25664; the 2-second all-queue watchdog default dates from kernel 6.19, so this is not specific to 7.x); without it V22 would fail for a kernel reason, not a model one. The 26.04.1 server ISO installs the GA kernel, which stays on the 7.0 series with security and stable backports for the life of the release; the later HWE kernels are opt-in and not used.

Sized for 192 GB: `gttsize` is expressed in MiB and `pages_limit` in 4 KiB pages. These were validated on 6.x kernels at 128 GB; **V3** confirms they apply on 7.0 at this capacity, that `rocminfo` reports `gfx1151` as expected, and that `llama-cli --list-devices` sees the full budget. If the reported identifier were ever to differ, the community ROCm wheels would need rebuilding against it before Phase 4 runs; the Principal has confirmed it reads `gfx1151`.

### 3.4 GPU compute stack — RESOLVED (C2)

Two different stacks serve two different layers, and this is deliberate:

| Layer | Backend | Where it runs | Status |
|---|---|---|---|
| LLM inference (llama.cpp) | **Vulkan (RADV)** | Host | Mature on this chip. Matches or beats ROCm for decode. Ships in the box on 26.04, no install |
| LLM prompt processing, optional | ROCm 7.2.2 + hipBLASLt | Container or later host install | ~3× faster prefill on long inputs. Optional upgrade, not a Day 1 dependency |
| PyTorch multimodal engines | **ROCm inside containers** | Docker/distrobox images carrying their own ROCm userspace | This is how the validated Strix Halo image/video toolboxes work. Host supplies only the kernel driver, `/dev/kfd`, `/dev/dri` |

**Why this resolution matters.** The brief chose 26.04 for the LLM layer, where Vulkan is sufficient, but Phase 4's engines need PyTorch-on-ROCm, which does not install cleanly on the 26.04 host kernel. Containerised ROCm removes the conflict. The host never needs a ROCm install.

**RISK R1, amended in v0.3.1** — AMD now publishes gfx1151 PyTorch wheels (ROCm 10.0.0, torch 2.13.0, from AMD's own wheel index), and the Ryzen AI Max+ PRO 495 is on the ROCm support list; the community scottt wheel from 2025 is no longer the path and is not pinned. The residual risk moved: kernel 7.0 with gfx1151 has open, unresolved PyTorch hang reports (ROCm/pytorch #6530, #6182), so a V11 failure may be the host kernel rather than the container; the mitigation would be a 6.18 kernel, which would reopen Section 3.1, so it is flagged for the Principal and not acted on during Day 1. Pin the wheel version. Every Phase 4 engine is tested individually and reported pass/fail. This layer is less battle-tested than the LLM layer and the brief treats it that way.

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
- Time from Canonical's NTS-authenticated servers through chrony, the 26.04 default (v0.3.4): the firewall lets only the chrony daemon's user open UDP 123 and TCP 4460, the same process-level fence as the proxy (80/443) and the DNS forwarder (53).
- Docker with the GPU device nodes passed to containers that need them; the service user in `render` and `video` groups.
- XFCE desktop and xrdp, listening on LAN and WireGuard interfaces only, never internet-facing. **Hardened in v0.3.4 (R24):** only the Xorg session type is offered (the packaged VNC-proxy entry, the path of the most serious 2026 CVE, plus the NeutrinoRDP-proxy and Xvnc entries are switched off), root login and alternate shells are refused, TLS only; `RDP_ALLOW_FROM` (addresses inside the LAN) optionally narrows the LAN side to the Principal's PC. Google Chrome installed in the desktop session for the Principal's own use (the Principal's choice, v0.3.3; Google's apt repository, managed policy with metrics, sign-in, sync and background mode off).

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
| KV cache type, Apex engine (DeepSeek V4 Flash) | `q4_0` **as the target, proven by ladder** (Section 23): Phase 3 loads f16, then `q8_0`, then `q4_0`, running a coherence prompt at each rung, and keeps the lowest coherent setting; K and V types must be identical on this architecture | Only ~10–15 GB of margin remains at 155 GB of weights; quantised KV on `deepseek4` produced garbage output in July–August 2026 (llama.cpp #25382, #26423, fix unmerged), so the setting is measured, not assumed |
| KV cache type, vision engine (Qwen2.5-VL-72B) | `q8_0` | Document and drawing reads are precision-sensitive |
| Per-model quantisation check | Day 1 verification script | **VERIFY V4**: unsupported architectures silently fall back to full precision |
| Context size | Explicit per model, never the default | Default contexts are small and silently truncate agent history |
| Context shift with `n_keep` | On, **passed explicitly as `--context-shift --keep <n>`** on every launch line (it is off by default in current llama-server); `n_keep` covers system prompt + persona directive + router state | Anchors never evicted; generation never hard-stops |
| Parallel slots | 8, except where noted, **always passed explicitly** (`--parallel` defaults to auto). `--ctx-size` is the total pool across slots, so 32k per slot means `--ctx-size 262144`; the Apex engine runs `--parallel 1` | KV cost at 32k × 8 slots: gpt-oss < 20 GB, Nemotron < 10 GB, Qwen3.5 < 15 GB. The vision engine runs 2 slots while co-resident with gpt-oss, keeping the pair inside the ~28 GB of cache the 142 GB combination leaves |
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
| gpt-oss-120b abliterated (Huihui) | Same weights, refusals removed | **MXFP4, re-quantised** (Section 23: the abliteration is applied to the BF16 upcast; the MXFP4 GGUF is a requant, same size and speed, the "no accuracy cost" argument below does not transfer) | 63 GB | Same | Valerie default; Ren and Arthur on explicit override |
| Nemotron 3 Super | 120B hybrid Mamba MoE, 12B active (A12B) | **`Q8_0`**, raised from 4-bit | 120–123 GB | ~12–14 tok/s | Arthur default. Silas, Alaric |
| Qwen3.5-122B-A10B | 122B MoE, 10B active, 256k context, multilingual | **`Q8_0`**, raised from 4-bit | 130 GB | ~15–17 tok/s | Override engine for long documents and future languages |
| **DeepSeek V4 Flash** (0731 checkpoint, MIT) | 284B MoE, 13B active | **`UD-Q4_K_XL`** | 155 GB | 25–32 tok/s | **NEW. The Apex engine.** Deep Think deep tier, TF_OMEGA, strong cross-checks. Runs alone |

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

An encrypted gocryptfs folder under `/srv/atlas/vault` (ciphertext in `vault/cipher`, which is backed up; plaintext mounted at `vault/open` only while unlocked, never backed up), on top of the LUKS data volume. Opened by a button in the interface that prompts for the passphrase directly; the passphrase never passes through a model or a chat message. Locked by command and auto-locked after 15 minutes idle (**D13 closed**). No RAM scrubbing, no forensic claims; those were removed from the design at the Principal's instruction. Contents are backed up as ciphertext only. Session handling of vault content is in Section 10.5.

---

## 12. Interface and access

### 12.1 Open WebUI — AGREED

Open WebUI is the face, not the brain. It runs in Docker, handles chat, voice, and file upload (installing it as a home-screen app needs TLS on the WireGuard address, a Day 2 addition, Section 23 S27), and presents the orchestrator as a single model named A.T.L.A.S. Ren and Arthur are additionally exposed as direct models for when the Principal wants one hemisphere alone. The 4-Way Router's Filter function relays every prompt to the orchestrator (Section 7.1).

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

### 12.4 The Windows PC — AGREED; mount deferred to the live ATLAS (v0.3.3)

Accessed on demand only, with write capability, as a shared folder the node mounts when a task needs it and releases afterwards. No software installed on the PC. Most of the Principal's data lives in cloud services (Section 13), so the PC is a minor source. It is not a server. **Day 1 scope (v0.3.3):** on the Principal's instruction the share is not configured on Day 1; Phase 2 step 9 is skipped while `WINDOWS_SHARE` is blank and the item sits on the live ATLAS to-do list (`input-windows-share`), to be picked up with the Principal once the node is running.

### 12.5 Trust zones and outbound allowlist — AGREED

| Zone | Members | Reach |
|---|---|---|
| Node | ATLAS services | Everything internal |
| LAN | Windows PC, home devices | Interface, SSH, Cockpit, ntfy, file share |
| WireGuard | Principal's phone and laptop | Same as LAN |
| Internet, inbound | Only UDP 51820 | WireGuard handshake only |
| Internet, outbound | Firewall allowlist | Google APIs, Xero, Cloudflare API, Wix, Pay.com, RewardPay, Sentinel feeds, package mirrors during builds, Hugging Face during model pulls, Canonical's NTS time servers (chrony only, v0.3.4). Everything else denied and logged |

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

**Day 1 scope (v0.3.2):** Day 1 installs the Google OAuth tokens and proves Gmail, Calendar and Drive are reachable (V20); the Xero, Cloudflare, Wix, Pay.com and RewardPay connectors and the outbound send channels behind the approval gate are Day 2 work. On Day 1 the approval queue holds, logs and approves exactly as Section 16.2 requires, and an approved item reports "no send channel configured" rather than sending (V15 proves the gate logic with a stub sender; Section 23 S26).

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
| Florence-2 | Lightweight vision, detection, captioning, OCR | Green; the native `florence-community` checkpoints, no remote code (Section 23) | under 2 GB | General |
| Chronos (Chronos-Bolt / Chronos-2) | Time-series forecasting. **TimesFM dropped in v0.3.1:** its current 3.0 release is non-commercial (Section 23) | Green, Apache-2.0 | under 5 GB | Domain 21, Silas |
| Stable Audio Open | Music and sound generation | Green | under 5 GB | Domain 12, Helena |
| CosyVoice2 | Voice cloning alternative | Green | under 5 GB | Voice layer, optional |
| UI-TARS-1.5-7B | Vision-driven GUI automation for RewardPay and Wix design. **UI-TARS 2.0 has no open weights** (Section 23, watch-list 15.5) | Green | 8 to 16 GB | Domain 1, Valerie |
| OpenVLA | Vision-language-action for robotics | Green technically, dormant until hardware exists | 8 to 16 GB | Domain 29, Valerie |
| Rad-DINO | Radiology vision | Green | under 2 GB | Domains 10 and 15, Minerva |
| SAM 2 | Image and video segmentation and tracking | Green with limitation: build with the CUDA post-processing extension disabled, minor mask cleanup lost | under 4 GB | Domains 22 and 7 |
| PointLLM | Point-cloud understanding | Verify V8: point-cloud ops often carry custom kernels. Licence CC-BY-NC-4.0 (research use; recorded under 16.5) | 8 to 16 GB | Domains 8 and 29 |
| Clay or Prithvi | Satellite and geospatial analysis | Verify V9 | under 5 GB | Domain 7 |
| Microsoft TRELLIS | Image-to-3D | Yellow: community ROCm forks exist but hit build errors on sparse-voxel kernels, attempt, log, move on | 8 to 12 GB | Domains 8 and 12 |
| Blender 4.5 LTS, Cycles (HIP) | Photorealistic rendering, the beauty half of "Lumina-PBR" | Yellow: works on this chip, occasional mid-render crashes reported, CPU-render fallback mandatory | varies | Domain 8, Valerie |
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
| UI-TARS 2.0 | Vision-driven GUI agent, successor to the 1.5-7B build in 15.2 | Technical report only; no open weights as of September 2026 | An open-weights release | UI-TARS-1.5-7B |
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

**Implemented by `scripts/day1/`** (entry point `atlas-day1.sh`; `README.md` there gives the commands in order, the prompts and what to have ready; `COVERAGE.md` maps every step and every V item below to its file). Four phases. One entry command per phase. Every phase is idempotent: re-running skips what is complete. Every phase writes a log and ends with a printed pass/fail table. Phases 3 and 4 run detached under systemd so a dropped SSH session cannot kill them, and both are resumable at the file level.

**Policy v0.3.3 — a missing input from the Principal never stops a phase.** Whatever a step needs from the Principal (a token, an SSH key, a Google account, an OAuth client file, a voice recording, a licence acceptance, a test from the phone or the Windows PC) is asked for once, in plain words with an example, and may be skipped. A skipped item is recorded as `deferred` in the verification table (never `fail`), written to the to-do list at `/var/lib/atlas/day1/todo.jsonl` (shown by `atlas-day1.sh status`) with the exact command that completes it later, and the phase continues. Gates never block on `deferred`. Only broken machinery stops a phase. The live ATLAS takes the to-do list up with the Principal.

### Phase 1 — Platform (reboot in the middle)

1. Pre-flight, using only what a bare host has (one exception since v0.3.4, below): confirm Ubuntu Server 26.04.1, kernel 7.0, both NVMe drives present, fTPM enabled (V2), and the GPU present and identified from `lspci` and `/sys/class/drm`. **The `rocminfo` confirmation of `gfx1151` belongs to the Phase 4 container self-test (V11), because ROCm is never installed on the host (3.4).** *v0.3.4: the one package step 1 fetches is systemd's TPM2 library set (libtss2-rc0t64, which the server image lacks, and esys/mu/tcti-device when a minimized install lacks those too), from the Ubuntu archive before the proxy and firewall exist; exactly one TPM is required, since `tpm2-device=auto` refuses two (S42). Every apt call first waits for apt-daily/unattended-upgrades to finish.*
2. LUKS2 on the data volume, TPM2 enrolment, recovery key printed once for off-node storage (D3).
3. Mount layout per Section 3.5; swap off; tmpfs for `/tmp`.
4. System update; kernel parameters (V3); firewall baseline; SSH hardening; Cockpit. *v0.3.4: tpm2-tools is installed before the update and every initramfs is rebuilt with TPM2 support before the reboot (S32); time sync is proven through the closed firewall (S35).*
5. Reboot. Post-reboot: `vulkaninfo` shows the Radeon 8065S, and the GTT pool read from `/sys/class/drm` matches the kernel parameters (V3, first half). The `llama-cli --list-devices` confirmation of ~170 GB usable moves to the Phase 2 gate, since llama.cpp is installed in Phase 2 (V3, second half).
5b. XFCE and xrdp installed, bound to LAN and WireGuard only; Google Chrome in the desktop session (v0.3.3); one RDP connection tested from the Principal's Windows PC (V19; no connection within 10 minutes is deferred with a to-do, not failed).
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
6d. AEGIS sandbox image (Section 16.4): built and proved by killing a runaway process under the memory cap (V17). *Added in v0.3.2; the build showed Section 17 had no home for V17.*
7. restic repository on the second drive; nightly timer; first backup; first restore test.
8. Sentinel timer, enabled with the feeds closed under D6: CoinDesk, an ASX and US index feed, RSS news, node telemetry. Pruning timer.
9. Windows PC share mount unit, on-demand. *Skipped on Day 1 (v0.3.3, Section 12.4): runs when `WINDOWS_SHARE` and the credential file exist.*
9b. Vault (Section 11): gocryptfs initialised with a passphrase the Principal types once, the open/lock/idle mechanics proved, and the memory rule proved (V18). *Added in v0.3.2.*
10. **Gate:** every service healthy; V3 second half (`llama-cli --list-devices` reports ~170 GB), V6, V7 (listening test, deferred if the reference recordings do not exist yet), V12, V13, V14 first half, V15, V16, V17, V18, V20, V23 recorded, plus V10's resident-router half. The Arbiter's refusal logic is unit-tested here against stub footprints; the real two-engine test is V21 in Phase 3. Tool installs whose inputs could not be verified in advance (Bonsai, Blender, OpenStudio/EnergyPlus, MCP4IFC, the build container) record a deferred `T-<tool>` row and never block the gate; `--force 06` re-runs them.

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
| C21 | Ubuntu 24.04.5 versus 26.04.1 left open | 26.04.1. 24.04.5 brings the 7.0 kernel through HWE but stays on Mesa 25.2.8, so hardware support is close, not identical; 26.04.1 wins on support life (May 2031 against May 2029), graphics stack and toolchain (3.1; corrected in v0.3.4, S37) |
| C22 | Whether to adopt the amd64v3 or amd64v4 optimised archives | Neither. The gain is on CPU-bound distribution packages, not GPU inference (3.1) |
| C23 | Everything quantised to 4-bit under a 128 GB constraint that no longer exists | Q8_0 for Nemotron, Qwen3.5, Qwen2.5-VL-72B and Meditron; MXFP4 retained for gpt-oss as its native form; Q4_K_XL for the Apex engine (5.1) |
| C24 | Two domains held in reserve pending a stated need (old D5) | Reserve closed. Six new domains, five subspecialty folds (8.2) |
| C25 | Vision split across GLM-4.6V-Flash, Qwen2.5-VL-7B and a 32B that v0.1 wrongly said did not exist | One engine: Qwen2.5-VL-72B at Q8_0. The 32B is real and official, but the Principal chose maximum quality (15.2) |
| C26 | "Two engines at once" ambiguous between residency and generation | Two may be resident; exactly one generates, everywhere, including Celery jobs (4.2) |
| C27 | Ubuntu Server 26.04.1 versus Fedora 44 KDE Plasma, raised by the Principal on 2026-10-07 | Ubuntu Server 26.04.1, re-confirmed 2026-10-08: five years without a major upgrade, a fixed kernel series, a dependable Remote Desktop login without autologin, AMD's vendor support for the chip, and no rewrite of the reviewed scripts (3.1). New in v0.3.4 |

---

## 19. Decisions — fifteen closed (D15 closed 2026-10-05)

| # | Decision | The Principal's answer | Note |
|---|---|---|---|
| D1 | Inference backend: llama-server or Ollama | llama-server, Ollama not installed | Accepted as recommended |
| D2 | Secure Boot: disable, or enrol keys | Disabled on a headless node behind a firewall | Accepted as recommended; **superseded by D15 (enabled)** |
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
| D15 | Secure Boot and the TPM binding (reopens D2) | **CLOSED 2026-10-05: option (a), Secure Boot enabled.** With Secure Boot off, PCR 7 is the same for any boot medium, so the data volume would unseal for any OS booted on this hardware. Enabling Secure Boot reverses D2 at no cost: this stack has no out-of-tree kernel modules (amdgpu and WireGuard are in-tree, Docker needs none). The Principal enables it in the BIOS; it is a precondition in Section 22. Until it is on, pre-flight warns, every V2 row notes the state and the to-do `secure-boot` stays open (no acknowledgement key exists any more; options (b) and (c) are withdrawn) | **Decided by the Principal: (a).** R23 mitigated once the BIOS setting is made |

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
| R22 | The `atlas` service user is in the `docker` group, which is root-equivalent on the host; the orchestrator needs the socket for the sandbox and Phase 4 containers | Medium | High if the orchestrator is ever compromised | Accepted for Day 1 and recorded in every script header; Day 2 options are rootless Docker or a socket proxy that allows only the sandbox and engine images. New in v0.3.2 |
| R23 | TPM2 unlock bound to PCR 7 with Secure Boot disabled unseals for any boot medium (see D15) | Medium | High for physical theft of the node | D15 closed as Secure Boot enabled (v0.3.3); mitigated once the Principal makes the BIOS setting. Until then pre-flight warns, V2 notes it and the to-do `secure-boot` is open; the enrolment itself proceeds. New in v0.3.2 |
| R24 | xrdp is the LAN service with known unpatched flaws reachable before login: 26.04 packages 0.10.1 in `universe`, eighteen 2026 xrdp CVEs (fixed upstream in 0.10.6 and 0.10.6.1) are still "needs-triage" for 26.04, Ubuntu Pro's esm-apps carries no fix, and 0.10.1 runs as root. (SSH, Cockpit, Open WebUI and ntfy are also reachable before login from the LAN; none has a comparable open list.) | Medium | High (code execution as root) if a LAN device or a WireGuard peer is hostile | v0.3.4: port 3389 only from the LAN subnet and WireGuard; the VNC proxy entry (vnc-any, the CVSS 9.8 CVE-2026-41252 path, also commented out upstream in 0.10.6.1), the NeutrinoRDP proxy entry (its module is not shipped) and Xvnc switched off; no root login, no alternate shell, TLS only; optional `RDP_ALLOW_FROM` (addresses inside the LAN, never the WireGuard bridge, checked before the firewall is touched) narrows the LAN side to the Principal's PC, and step 5b records the to-do with the LAN address the PC connected from. Residual: the remaining pre-login bugs from those hosts. Watch for a 26.04 xrdp security update; a later option is to keep 3389 on WireGuard only. New in v0.3.4 |
| R25 | Unattended upgrades install security updates from the release and security pockets but never reboot, and do not update Docker CE or Google Chrome (third-party repositories), so kernel and library fixes take effect only at the next reboot | Medium | Medium | Accepted for Day 1: an automatic reboot would interrupt long jobs. Day 2: the live ATLAS reports "reboot required" and pending Docker CE/Chrome updates through ntfy and schedules the reboot with the Principal. New in v0.3.4 |
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

**Scope notes added in v0.3.2.** V15 proves the gate logic (hold, auto-send-and-log, cross-check required for sensitive) against a stub send channel; the real channels are Day 2 (Section 13). V14's second half and V21 prove the Arbiter against real engines, but the Deep Think rubric weights and the Ouroboros similarity threshold are untuned until the first real sessions, and Section 9.1's timings are estimates until then. The scripts also record rows that are not V items: `T-<tool>` (deferred Phase 2 tool installs), `P4-wheels` (the Phase 4 wheel-index pre-flight) and the Secure Boot state in every V2 row; they appear in the gate tables and in the workbook's evidence column, never as pass/fail rows of their own.

**Scope note added in v0.3.3.** Six V items can be recorded `deferred` when the Principal's input is missing and are never a red row in that case: V5 (no handshake from the phone yet), V19 (no RDP session yet), V6 (no Hugging Face token, or licence not yet accepted), V20 (no Google accounts or no OAuth client file), V23 (no Cloudflare token or zone id), and V7 as before (no reference recordings). Each leaves a to-do with the `--force` command that records the real result later. *v0.3.4:* V2 is also recorded `deferred` (not pass) when the only gap is an unencrypted OS volume, with the to-do `os-volume-encryption`; the data volume's TPM2 proof still has to pass.

---

## 22. Pre-execution checklist

Nothing runs until every box is ticked. **All boxes were ticked in the workbook returned 2026-09-21.** The list is kept as the record of what was required.

**Principal's actions — what is genuinely left**

- [x] Answer D1 through D14. **Done**, all fourteen closed in Section 19.
- [x] Decide where the LUKS recovery key and restic passphrase live (D3). **Done**: node plus external USB.
- [x] Confirm the Sentinel feed list (D6), the director alias pattern (D14), and the Apple build path (D11, removed).
- [ ] Have the USB recovery drive present on Day 1 and **write it at the Day 1 close-out**: the LUKS recovery key (shown once in Phase 1 step 2), the restic passphrase (`/etc/atlas/secrets/restic.pass`, shown once in Phase 2 step 7) and the vault passphrase you chose (step 9b); then store it away from the node, not beside it (R16). *Reworded in v0.3.2: the material does not exist before Day 1, so the drive cannot be "stored" yet.*
- [ ] **New in v0.3.3:** enable Secure Boot in the BIOS before Phase 1 (D15, option (a)). The one BIOS change since the workbook; without it pre-flight warns and the to-do `secure-boot` stays open, nothing stops.
- [ ] **Optional, asked at the prompt (v0.3.3):** your SSH public key, pasted when Phase 1 step 1 asks (the step prints the PowerShell command that shows it); skipped, password SSH stays on until the key is added (to-do `ssh-key`).
- [ ] **Optional, asked at the prompt (v0.3.3):** `GOOGLE_ACCOUNTS` (two addresses, each tagged `corporate` or `estate`), `FAMILY_NAMES` (for the router's hard rule) and `BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE` (Google's Android SDK licence, needed by the build container) are asked once by the first run, with an example each; a skip records a to-do and the dependent step defers itself. `WINDOWS_SHARE` is not asked (Section 12.4: live to-do).
- [x] ~~`/etc/atlas/secrets/smb.cred` for the Windows share account~~ **Deferred to the live ATLAS (v0.3.3)** with the share itself; Phase 2 step 9 is skipped. No action before Day 1.
- [x] Decide D15 (Secure Boot and the TPM binding). **Done 2026-10-05: option (a).**
- [x] Create the Google Cloud OAuth client for Gmail, Calendar and Drive, and be reachable for roughly five minutes during the Phase 2 pause (V20). **Client created.** The consent click itself still happens during Phase 2; Google requires the account owner to click Allow. The client must be of the **Desktop app** type (its JSON has a top-level `installed` key; a `web` key is the wrong type and Phase 2 stops at minute 0 saying so). Drop the JSON into `/srv/atlas/staging/inbox/google-oauth-client.json` once Phase 1 has created that folder.
- [ ] **Optional, asked at the prompt (v0.3.1, relaxed in v0.3.3):** create a Hugging Face access token (read scope) and, with the same account, accept the licences of the gated models on huggingface.co: `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0` (V6), `black-forest-labs/FLUX.1-dev` and `stabilityai/stable-audio-open-1.0` (Phase 4). Phase 2 asks for the token once at its start (hidden input) and stores it under `/etc/atlas/secrets/`; skipped, V6 and the gated Phase 4 engines are deferred with the to-do `input-hf-token`.
- [ ] **Optional, asked at the prompt (v0.3.1, relaxed in v0.3.3):** have the Cloudflare zone id for `sovereign-node.link` to hand (Overview page of the zone). A `Zone:DNS:Edit`-only token cannot look the zone up by name; Phase 1 step 7 asks for the id once if the token cannot list zones. A missing `CLOUDFLARE.txt` or a skipped zone id defers dynamic DNS and V23 with a to-do.
- [x] Source reference recordings for Alaric's gravelly voice and, if the British male presets collide, Gideon's. **Sourced.** Drop them as `alaric.*` and `gideon.*` (WAV, MP3 or M4A; step 5 converts to 16 kHz mono WAV) into `/srv/atlas/staging/inbox/voice-references/` once Phase 1 has created the folder; if absent, both fall back to the nearest Kokoro preset and V7 is recorded as deferred, not failed.
- [x] Have a monitor and keyboard available for Phase 1 only, in case first boot needs a hand. **Done.**

**Automated on the Principal's instruction, no action needed**

- [x] Cloudflare token relocation and deletion of `CLOUDFLARE.txt`: Phase 2 step 6b (V23).
- [x] Director aliases generated from names rather than confirmed one by one (D14).

**Build-side preconditions**

- [x] Ubuntu Server 26.04.1 installed on the 4 TB drive **with the installer's encrypted-LVM (LUKS) option** (installer storage step: *Use an entire disk*, the 4 TB drive, tick *Encrypt the LVM group with LUKS*, choose a disk passphrase; Section 3.5 requires both volumes encrypted; v0.3.3: an unencrypted OS volume is a warning and the to-do `os-volume-encryption`, not a stop); 8 TB drive unpartitioned. No desktop needed at install time: Phase 1 step 5b installs XFCE, xrdp and Chrome. **Done** per the returned workbook; confirm the encryption option was taken.
- [x] BIOS: UMA minimum, IOMMU on, fTPM on. **Done.** Secure Boot: **enabled** (D15, v0.3.3; see the Principal's actions above).
- [x] LAN address reserved for the node on the router (DHCP reservation for the node's MAC, Wi-Fi or cable: Phase 1 uses whichever interface is up); UDP 51820 forward confirmed to that address. **Done.** The reservation is what keeps the forward pointing at the node; no further IP action is needed, and the VPN test from mobile data (V5) can be done whenever convenient.
- [x] Internet bandwidth known, so the ~690 GB Phase 3 download can be planned. **Done**; the figure was not recorded in the workbook, so the 100 Mbps planning assumption in 15.4 stands until stated.

**Accepted resolutions**

- [x] C1 through C20 accepted in the returned workbook. C19 was reopened and re-decided in the Principal's favour.
- [x] C21 through C26, new in v0.2, **accepted** in the returned workbook: the Ubuntu and amd64v3 rejections, the quantisation rise, the closed reserve list, the vision consolidation, and the residency-versus-generation reading.
- [x] R9, R16, R19, R20 and R21 **acknowledged** in the returned workbook: two changed wording in v0.2, three were new.
- [x] C27, new in v0.3.4: Ubuntu Server 26.04.1 kept over Fedora 44 KDE Plasma. **Decided by the Principal on 2026-10-08.**
- [x] R22 to R25 **acknowledged** by the Principal on 2026-10-08 (R22 and R23 new in v0.3.2, R24 and R25 new in v0.3.4).

---

## 23. Day 1 script build — corrections folded into the baseline (v0.3.1)

The Day 1 scripts under `scripts/day1/` were written after a fact-checking pass over every package, image, model repository and flag the baseline names. Where the checked fact disagreed with the document, the scripts follow the fact and the document is amended above. None of S1–S23 reopens a decision (D1–D14) or a resolution (C1–C26); each is a correction of a literal the baseline typed before it was checked. S24–S29, added when the scripts were complete, record what the build itself had to add or could not deliver on Day 1; S25 reopened D2 as D15, which the Principal closed on 2026-10-05 (Secure Boot enabled). S30–S31 record the Principal's two Day 1 scope instructions of 2026-10-05: no phase stops on a missing input, and the Windows share waits for the live ATLAS. S32–S38 come from the Ubuntu-versus-Fedora comparison of 2026-10-08: five script defects fixed, two factual corrections, and the re-confirmed OS choice. S39–S43 come from the adversarial reviews and the stock-image sweep of those fixes the same day. Listed here so the Principal can see what moved and why.

| # | Where | What the baseline said | What is true (September 2026) | Effect on Day 1 |
|---|---|---|---|---|
| S1 | 5.1 | Abliterated gpt-oss is "MXFP4, native" | Huihui abliterates the BF16 upcast; the MXFP4 GGUF is a re-quantisation (noctrex). Same 63 GB, same speed | None on sizing; the "no accuracy cost" reasoning applies only to the standard gpt-oss |
| S2 | 4.3, App. B | Apex engine KV cache `q4_0` | Quantised KV on `deepseek4` produced garbage in July–August 2026; the fix is unmerged; K and V types must be equal | Phase 3 ladders f16 → q8_0 → q4_0 with a coherence check and records the winner; an f16-only result caps the context and still passes V22 |
| S3 | 5.1 | "DeepSeek V4 Flash" | Two checkpoints exist; the 0731 release (MIT) supersedes the April preview | Pinned to `unsloth/DeepSeek-V4-Flash-0731-GGUF`; launched with `--parallel 1` (the auto slot count OOMs on this architecture) |
| S4 | 4.3, App. B | Context shift with `n_keep` on | Off by default in current llama-server; `--parallel` defaults to auto; `--ctx-size` is the total pool across slots | Every launch line carries `--context-shift --keep <n> --parallel <n>`; 32k per slot × 8 is `--ctx-size 262144` |
| S5 | 5.1 | Meditron-70B Q8_0 pulled as a GGUF | TheBloke's Q8_0 is a raw byte split (`-split-a`, `-split-b`) llama.cpp cannot load; training context 4096 | Phase 3 joins the halves with a size check; context fixed at 4096, one slot |
| S6 | 5.1 | Nemotron 3 Super 12.7B active | 12B active (A12B); needs llama.cpp ≥ b8297; an open report of a GPU memory fault on Vulkan at ~20k-token prompts (llama.cpp #20732) | Phase 3's 8k prefill test checks the server is still alive afterwards and records a warning with the issue number, not a hang |
| S7 | 17, 21 | Checksums pre-recorded | Hugging Face was unreachable from the research sandbox, so only three sha256 values were recovered | The pull step reads `lfs.oid` and `lfs.size` per file from the Hugging Face tree API at pull time, verifies, and writes them to `MANIFEST.json` (the manifest AEGIS backs up) |
| S8 | 3.3, App. B | `amdgpu.gttsize` + `ttm.pages_limit` | `gttsize` deprecated but honoured; DeepSeek V4 triggers DeviceLost on kernel 7.x without `amdgpu.lockup_timeout` | `amdgpu.lockup_timeout=10000,60000,10000,10000` added; V3a accepts the deprecation warning |
| S9 | 3.5, 17 step 2 | `systemd-cryptenroll --tpm2-device=auto` | systemd 259's default PCR mask is empty: the volume would unlock in any boot environment | `--tpm2-pcrs=7` passed; V2 fails on an empty PCR list |
| S10 | 17 step 3 | tmpfs for `/tmp` enabled | Already the 26.04 default; the old unit path no longer exists | Verified, not enabled |
| S11 | 12.3 | Token scoped `Zone:DNS:Edit` only | Such a token cannot list zones to find the zone id | `CF_ZONE_ID` stored beside the token; resolved once if the token allows, else asked for once (Section 22) |
| S12 | 3.6, App. B | Services "bound to LAN and WireGuard interfaces" | WG-Easy runs in Docker: there is no host `wg0`; VPN traffic arrives from the compose bridge. Docker bypasses ufw for published ports and container egress | Binding is expressed as ufw rules on the LAN interface and the WG-Easy bridge; every published port is pinned to the LAN address; container egress is enforced in the `DOCKER-USER` chain; the outbound allowlist is a local Squid proxy with owner-matched egress (hostnames cannot be filtered by ufw) |
| S13 | 17 step 5b | Firefox installed | The archive package is a snap stub | Google Chrome from Google's apt repository (the Principal's choice, v0.3.3), `dl.google.com` on the allowlist, managed policy with telemetry and sign-in off |
| S14 | 17 step 1, 5 | Identify the Radeon 8065S | No public PCI id for the 8065S; `vulkaninfo` prints the RADV device string | Pre-flight identifies the GPU by vendor and DRM class; the post-boot check matches `RADV GFX1151`, never the marketing name |
| S15 | 3.4 R1 | Community scottt PyTorch wheel | AMD publishes gfx1151 wheels (ROCm 10.0.0, torch 2.13.0); open kernel-7.0 hang reports remain | AMD's index is the base image; the kernel risk is flagged, not acted on |
| S16 | 15.2 | UI-TARS 2.0 | No open weights; technical report only | UI-TARS-1.5-7B built; 2.0 on the watch-list |
| S17 | 15.2 | TimesFM or Chronos | TimesFM 3.0 is non-commercial; Chronos is Apache-2.0 | Chronos built |
| S18 | 15.2 | Florence-2 `microsoft/Florence-2-large` | Needs remote code and breaks on current transformers | Native `florence-community` checkpoints |
| S19 | 15.2, 22 | FLUX.1-dev and Stable Audio Open pulled in Phase 4 | Both gated: licence acceptance on huggingface.co plus a token | New Section 22 item; the scripts fail with the licence URL on a 403 |
| S20 | 15.2, 16.5 | PointLLM | Licence CC-BY-NC-4.0 | Recorded under 16.5 with the other research-licensed items |
| S21 | 3.1 | Ubuntu 26.04 | Ships `sudo-rs`, which rejects `sudo -E` | Phases run as root; the orchestrator's control path is two NOPASSWD sudoers fragments: `atlas-engines` (exactly `systemctl start|stop|restart llama-server@*`) and `atlas-vault` (the gocryptfs open/lock/status helper) |
| S22 | 21 V4 | "Unsupported architectures silently fall back" | llama.cpp errors out at context creation rather than falling back | V4 is the `llama_kv_cache ... K (q8_0) V (q8_0)` log line plus a health check |
| S23 | 9.3 vs 9.7 | Sentinel on a systemd timer; Celery replaces separate schedulers | Both, read together | The timers only enqueue the Celery task; Celery executes. AEGIS nightly and the 72-hour prune use the same pattern, with restic's own timer as the fallback if the orchestrator is down |

| S24 | 17, 21 | Phase 2 had no step for V17 or V18 | The sandbox and the vault need an install step | Steps 6d and 9b added to Section 17 |
| S25 | 3.5, 17 step 2, D2 | PCR 7 binding with Secure Boot disabled | Unseals for any boot medium | Decision D15 reopened, then closed 2026-10-05 as Secure Boot enabled (R23); pre-flight warns while it is still off |
| S26 | 13, 21 V15 | Approval gate "sends" on approve | No outbound channel exists on Day 1 (Section 13 connectors are Day 2) | V15 proves the gate against a stub sender; an approved item reports "no send channel configured" |
| S27 | 12.1 | Open WebUI "installs as an app" | A home-screen app needs TLS; Day 1 serves plain HTTP on LAN and WireGuard | Day 2: TLS front on the WireGuard address |
| S28 | 3.6, 16.4 | Service user in `render` and `video` only | The sandbox and Phase 4 need the Docker socket, so `atlas` is also in `docker` (root-equivalent) | Recorded as R22, accepted for Day 1 |
| S29 | 12.5 | Outbound allowlist as enumerated in 12.5 | Three pulls fall outside the named services: Meta's weight host for the TRELLIS DINOv2 conditioner and the SAM 2 `.pt` fallback, the OpenAI tokenizer table LightRAG needs offline, and the Playwright browser CDN | Added to `config/allowlist.txt` under the "package mirrors during builds, Hugging Face during model pulls" clause, each with a comment naming the step: `dl.fbaipublicfiles.com`, `openaipublic.blob.core.windows.net`, `playwright.azureedge.net`, `cdn.playwright.dev`. The Principal may strike any of them; the dependent engine or tool then records deferred |

| S30 | 17, 21, 22 | Pre-flight and `load_env` stop on a missing SSH key, blank settings, a missing credential file or token | The Principal's instruction (2026-10-05): a missing input must never stop a phase | Policy v0.3.3: ask once in plain words, record `deferred` plus a to-do (`/var/lib/atlas/day1/todo.jsonl`), continue; the live ATLAS takes the list up. Section 17 intro, Section 21 scope note, Section 22 rewritten |
| S31 | 12.4, 17 step 9 | Windows PC share mounted on Day 1 | The Principal: not needed during the build | Step 9 skipped while `WINDOWS_SHARE` is blank; to-do `input-windows-share` |
| S32 | 3.5, 17 steps 2 and 4 | The dracut drop-in names the `tpm2-tss` module; crypttab carries `tpm2-device=auto` from step 2 | dracut's tpm2-tss module requires the `tpm2` binary from tpm2-tools, which is not on the 26.04.1 server image, and a requested module that cannot be installed stops dracut. In dracut's default hostonly mode a `tpm2-device=` in crypttab alone pulls the module in. Step 2 and every initramfs build until step 4 (including an unattended kernel update) would have failed | Step 4 installs tpm2-tools through the proxy before the dist-upgrade, then adds `tpm2-device=auto` to crypttab and names tpm2-tss in the drop-in; until then neither mentions TPM2; step 4 rebuilds every initramfs and checks each image before the reboot, and step 5 reports that check in V2 (VERIFIED from dracut-ng and the 26.04.1 manifest; review of 2026-10-08) |
| S33 | 17 step 2, policy v0.3.3, D3 | Step 2 stopped on an unencrypted OS volume | Contradicted the never-stop policy that step 1 already followed | Step 2 encrypts the data volume and continues; V2 is `deferred` with the to-do `os-volume-encryption`. While the OS volume is unencrypted no on-node copy of the recovery key is kept (D3's copy presumes an encrypted OS disk; a plaintext key beside the volume would void its encryption): the written and USB copies are the only ones, which the pause says. A copy left by an earlier run is not only shredded: its key is revoked and a new one issued |
| S34 | 12.5 (rule 7.1 beacons) | snapd purged unless it would remove an Ubuntu metapackage | The check read apt's simulation for "Remv" while a purge prints "Purg", so snapd was always purged, taking `ubuntu-server-minimal` (which Depends on snapd on 26.04) with it | The check reads both; snapd is masked, not purged, while a metapackage depends on it |
| S35 | 3.6, 12.5 | Time sync through systemd-timesyncd pinned to ntp.ubuntu.com on UDP 123 | 26.04 ships chrony with NTS (1..4.ntp.ubuntu.com and ntp-bootstrap.ubuntu.com, TCP 4460 plus UDP 123) and no timesyncd, so behind the default-deny firewall the clock would not have synchronised | UDP 123 and TCP 4460 opened for the chrony user only; `ntp-bootstrap.ubuntu.com` added to the allowlist; a two-minute sync check with the to-do `time-sync` on failure (VERIFIED from the chrony 4.8 package and source) |
| S36 | 3.6, R24 | xrdp installed with the packaged configuration | The packaged xrdp.ini offers a VNC proxy session (the CVE-2026-41252 path), a NeutrinoRDP proxy and Xvnc, and sesman.ini allows root login and alternate shells, on a version with unpatched 2026 CVEs | Hardened in step 5b; R24 added; optional `RDP_ALLOW_FROM`, accepted only as canonical dotted quads with decimal prefixes (forms iptables would misread are refused), inside the LAN and outside the WireGuard bridge, checked before the firewall is reset |
| S37 | 3.1 (C21), 3.3 | 24.04.5 "identical hardware support" incl. Mesa 26.0; support "to April 2031"; DeviceLost "on 7.x kernels" | 24.04 stays on Mesa 25.2.8; standard maintenance ends May 2031 (release notes: April 2031); the 2-second watchdog dates from 6.19 | Text corrected; the decisions stand |
| S38 | 3.1 | Ubuntu Server 26.04.1 | The Principal asked whether Fedora 44 KDE Plasma would be better | Evaluated and rejected (C27); support coverage of `universe` and the unattended-reboot gap recorded (3.1, R24, R25) |
| S39 | 3.5, 17 steps 1, 2 and 5 | Finding the device under "/" with `lsblk -s` | With the NAME column shown lsblk draws tree prefixes into pipes, so on the encrypted-LVM install the crypt mapping read as "└─dm_crypt-0": step 2 would have stopped at its first OS-volume line, and step 1's "DATA_DISK is the root disk" guard never fired (present since the first build; found by the review of 2026-10-08) | List mode (`lsblk -l`) everywhere a NAME is parsed; "encrypted but unresolvable" now stops with a named message instead of being read as "unencrypted" |
| S40 | 17 step 2, D15 | The secure-boot to-do says `--force 02` re-enrols the TPM binding | Step 2 skipped enrolment whenever a TPM2 token already existed, so after Secure Boot was switched on the old tokens stayed and the unlock failed | Every run of step 2 now re-seals both tokens to the current PCR 7 (a no-op when it is unchanged; otherwise new token first, old tpm2 slots wiped with the new one excluded, VERIFIED in systemd-cryptenroll), so `--force 02` and a plain re-run of an unfinished step both work. The TPM itself is checked first; its own authorisation is tried before anything is asked; only when it refuses is the OS passphrase asked once, and the data volume's recovery key when no usable copy is on the node, each checked on the spot. Any previous recovery key is revoked only after the new one is confirmed WRITTEN DOWN, and on an encrypted OS a typed key restores a missing on-node copy instead of being replaced. The `secure-boot` to-do closes once Secure Boot is on |
| S41 | 3.6, R24 | `RDP_ALLOW_FROM` checked by a digits-only pattern | Values ufw rejects (an octet above 255, a leading zero, /33) passed, and step 4 would have failed after `ufw --force reset`, leaving the node without a firewall; values outside the LAN widened access | Accepted only as canonical dotted quads with a decimal prefix (iptables reads a leading-zero prefix as octal and a dotted suffix as a netmask), inside the LAN and outside the WireGuard bridge, in load_env and again before the reset; the to-do `rdp-restrict` closes once the firewall no longer admits the whole subnet, and step 5b only proposes a peer inside the LAN |
| S42 | 3.5, 17 steps 1 and 2 | systemd-cryptenroll speaks to the TPM with what the server image ships | systemd 259 loads libtss2-esys, libtss2-rc and libtss2-mu at runtime for every TPM2 operation, and libtss2-tcti-device for enrolment; the 26.04.1 server image lacks libtss2-rc0t64, and esys/mu/tcti-device arrive only through fwupd (a Recommends of ubuntu-server), so every TPM step would have reported "TPM2 support is not installed" on a stock install (present since the first build; VERIFIED in systemd's tpm2-util.c and the 26.04.1 manifest; found by the fifth review round) | Step 1 installs whichever of the four libraries is missing from the archive before the proxy exists, a declared exception like rsync and squid, after waiting for any apt-daily/unattended-upgrades run (it would otherwise stop at once on a held lock); one shared check in steps 1 and 2 and in V2 names the packages and requires exactly one TPM, since `tpm2-device=auto` refuses two |
| S43 | 15.1, 17 Phase 2 step 6 | Radiance and the OpenStudio fallback unpacked with one leading directory stripped | Both pinned archives nest their trees (radiance-6.0.c1700d56cc-Linux/usr/local/radiance/{bin,lib,man}; OpenStudio-3.11.0+241b8abb4d-Ubuntu-24.04-x86_64/usr/local/openstudio-3.11.0/), so `rtrace` landed three levels too deep and stopped Phase 2 on every node, and the OpenStudio fallback could never succeed (VERIFIED against both pinned archives, sha256 unchanged; found by the stock-image sweep and its review) | Each finds its `bin/` binary at whatever depth and installs that tree (OpenStudio's 1.3 GB unpacked on /opt, not the /tmp tmpfs); RAYPATH is `.:/opt/radiance/lib`, Radiance's documented form, so a scene's own files are found by relative name; the fallback purges the .deb's real package name, openstudio-3.11.0 |

**Watch-list additions from the build:** UI-TARS 2.0 (S16); Qwen3.8-LiveTranslate and Qwen-Image-2.1 (the Principal keeps FLUX.1-dev for quality, 2026-10-05); ROCm 10.1.0 (general availability 2026-10-05; Phase 4 stays pinned to 10.0.0, the version the gfx1151 wheels were validated with, until a deliberate re-test); a 26.04 security update for xrdp (R24), and any Cockpit security advisory (3.1). **Reserved for the Principal:** the kernel-7.0 hang reports in S15, if V11 fails for that reason.

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

**Kernel (GRUB):** `amdgpu.gttsize=196608 ttm.pages_limit=50331648 amdgpu.lockup_timeout=10000,60000,10000,10000`

**llama-server, Arthur's engines (Nemotron `Q8_0`, Qwen3.5 `Q8_0`) and the vision engine (Qwen2.5-VL-72B `Q8_0`):** `-fa on --cache-type-k q8_0 --cache-type-v q8_0 --ctx-size 262144 --parallel 8 --context-shift --keep <n_keep> --slot-save-path /srv/atlas/data/slots --host 127.0.0.1` (ctx-size is the total pool: 32k × 8 slots)

**llama-server, Ren's engines (gpt-oss MXFP4 and its abliterated twin) and the Apex engine (DeepSeek V4 Flash `UD-Q4_K_XL`):** same with `--cache-type-k q4_0 --cache-type-v q4_0`. The Apex engine runs `--parallel 1`, a reduced `--ctx-size` (only 10 to 15 GB remain beside 155 GB of weights), and whichever cache type the Phase 3 ladder proved coherent (Section 4.3). Build: llama.cpp tag `v0.4.1`, `-DGGML_VULKAN=ON -DLLAMA_BUILD_IS_DEV=OFF -DLLAMA_OPENSSL=ON -DLLAMA_USE_PREBUILT_UI=OFF`.

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
| `/srv/atlas/vault/cipher` | 8 TB | gocryptfs ciphertext | Yes, as ciphertext |
| `/srv/atlas/vault/open` | 8 TB | gocryptfs mount point, populated only while unlocked | Never |
| `/srv/cold` | 4 TB | Pruned-memory archives | Yes |
| `/srv/backups` | 4 TB | restic repository | Is the backup |
| `/var/log` | 4 TB | Logs, 30 days hot | Rotated |

## Appendix D — Sources relied on during review

Hardware: GMKtec EVO-X5 Pro announcement and VideoCardz and T3 specification reports for the Ryzen AI Max+ PRO 495 "Gorgon Halo" platform; Notebookcheck's processor page. Quantisation sizes: Unsloth's DeepSeek V4 and Qwen3.5 run guides, bartowski's Nemotron 3 Super GGUF listing, ggml-org's Qwen2.5-VL-72B GGUF. Ubuntu: OMG Ubuntu and Phoronix on the 24.04.5 point release and the amd64v3 archive experiments. The v0.1 hardware sources for the MS-S1 Max are superseded. Platform: AMD ROCm Strix Halo system-optimisation guide; community Strix Halo local-LLM guides; LucRoot known-good ROCm llama.cpp recipe; llama.cpp discussion on the known-good Strix Halo stack; Phoronix Ubuntu 26.04 Strix Halo benchmarks. Models: Unsloth Nemotron 3 Super guide; Beinsezii Qwen3.5-122B-A10B Strix Halo GGUF; Huihui gpt-oss-120b abliterated MXFP4 GGUF; gpt-oss model card. KV cache: Ollama FAQ and environment reference; llama.cpp server README on `n_keep` and context shift; StreamingLLM paper. Engines: kyuz0 and matthewhand Strix Halo ComfyUI toolboxes; TRELLIS.2 ROCm forks; Evo2StrixHalo port; SAM 2 ROCm issues; MCP4IFC project page and paper; Radiance at LBNL; Genusys and Auto BIM Route for the MEP market. Watch-list: Qwen3.8-LiveTranslate announcement and Model Studio API page; Qwen-Image-2.1 model card, Hugging Face licence file and GGUF listing; Black Forest Labs FLUX.1-dev and FLUX.1-schnell licence pages. Voice: Kokoro-82M VOICES.md; Trelis and Pinggy 2026 TTS comparisons; Qwen3-TTS repository; Fish Audio S2 licence page. Legal: Apple EULA analyses of OSX-KVM.

*End of document.*
