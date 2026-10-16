# v0.4.0 analysis: RAM headroom (N-01, N-03), D-14 placement, P-10 router size

Written 2026-10-16 for the v0.4.0 revision. This is analysis only; it changes nothing in the repository.

- Code read at HEAD `1b93482` (branch `claude/ecstatic-pasteur-br9nsq`, v0.3.5 baseline). Paths below are relative to `scripts/day1/` unless they start with `docs/`, `reports/` or `research_notes/`.
- llama.cpp facts were read from the source at the new pin, b11474 = `b9acf138a1e28ce1fc23b5a4fc4b12444b50f7ea`, in the local clone `<build session scratchpad>/llcpp.git`. Where it matters, the old pin `b29c606` was checked as well.
- Labels: **VERIFIED** means read in code or in a primary page. **INFERRED** means derived by arithmetic or reasoning and not measured. **UNVERIFIED** means not checked.
- Budget arithmetic uses the document's expected figures (Section 4.1, `docs/ATLAS_FRAMEWORK_REVIEW.md:231`): pool = MemTotal ≈ 187.5 GiB, 16 GiB host reserve, budget = pool − GPU resident set − reserve (`orchestrator/src/atlas/arbiter.py:541-551`). With only the router on the iGPU, the GPU resident set is taken as about 5.5 GiB (INFERRED: 4.19 GiB of weights, 0.53 GiB of q8_0 KV at 32,768 tokens, plus compute buffers). Every figure is a projection until Phase 2 and Phase 3 measure it (T-05, T-15, to-dos `kv-estimates` and `cache-ram`).

---

## 1. The Principal's comment on N-01 and N-03

> "explore maximising RAM efficiency so the LLM has more headroom; will the smaller model engines be loaded at the same time after the RAM has been flushed, to avoid out of memory"

### 1.1 What is resident, and when (v0.3.5 code)

| Component | Where it runs | Memory (file or cap) | Loaded | Unloaded | Counted by the Arbiter as |
|---|---|---|---|---|---|
| Router `router-qwen3.5-4b` (Eleanor's classifier) | iGPU, llama-server, 4 slots, ctx 32,768, q8_0 KV (`config/engines.json:255-262`) | 4.5 GB / 4.19 GiB weights + about 0.53 GiB KV | At boot. Step 4 enables the unit (`phase2/04-memory.sh:486-500`) | Never | Part of the measured GPU resident set. `arbiter_class resident` is never budgeted directly (`orchestrator/src/atlas/arbiter.py:578`, `:736`) |
| `embed-bge-m3`, `rerank-bge-v2-m3` | iGPU, llama-server, `-ub 8192` (`config/engines.json:266-319`) | 0.7 GB each | At boot (same loop, `phase2/04-memory.sh:128`) | Never | Resident set |
| The one LLM seat (today any of 7 GGUF engines; under v0.4.0 GLM-5.3-Flash, DeepSeek-V4-Flash-0731 or gpt-oss-120b) | iGPU, `llama-server@<key>` | 59-151 GiB + KV + compute buffers | On demand, inside the single generation slot (`orchestrator/src/atlas/api.py:1148-1170`) | When another seat needs the room | Projected, then measured. Charged against the budget |
| Chatterbox (class external) | CPU, in-process, own venv | 4 GiB planning figure, replaced by the measured peak RSS (`config/engines.json:321-334`) | Per render | When the caller POSTs `/arbiter/unload` with a pid | Budgeted in bytes. Counts as one of `MAX_RESIDENT = 2` (`arbiter.py:123`) |
| Kokoro, speaches (Whisper turbo int8), docling-serve | CPU containers (`docker/core/compose.voice.yml:47-160`) | **No `mem_limit`**. Whisper never unloads (`STT_MODEL_TTL=-1`, `:106`). Docling runs 2 workers (`:150`) | At boot (restart unless-stopped) | Never | Not on the GTT counter. Inside the 16 GiB host reserve |
| Redis, ChromaDB, Open WebUI | CPU containers | `mem_limit` 3g / 8g / 8g (`docker/core/compose.yml:106`, `:149`, `:193`) | At boot | Never | Host reserve |
| Orchestrator, Celery cpu (concurrency `nproc - 2`, so 30 on 32 threads, `phase2/02-orchestrator.sh:411`), Celery gpu (1), beat, Sentinel, AEGIS units | CPU, systemd | **No `MemoryMax` or `MemoryHigh` on any unit** (grep of `systemd/`) | At boot or on timer | Never | Host reserve |
| AEGIS sandbox, buildfarm | Docker with caps | `--memory 2g` (`phase2/06d-sandbox.sh:104`, `orchestrator/src/atlas/sandbox.py:119`). Buildfarm 8g (`phase2/06-tools.sh:196`) | Per job | Per job | Host reserve |
| `/tmp` | tmpfs | `size=16G` (`phase1/03-mounts.sh:140`). Swap is off and swappiness is 0 (`:120-131`), so tmpfs pages cannot be reclaimed | Always | — | Host reserve |

Section 4.1 (`docs/ATLAS_FRAMEWORK_REVIEW.md:226-231`) budgets about 12 GB for the CPU-side rows. The three small models take about 6-8 GiB of GPU memory. The engine budget comes out at about 164 GiB.

### 1.2 How the Arbiter unloads, waits for the release and loads (VERIFIED in code)

1. **Request.** Every generation takes the one FIFO slot first. Inside the slot, `request_load` runs (`api.py:1148-1170`), so a swap can never race a queued request.
2. **Plan.** This step runs under the state lock (`arbiter.py:_plan_load`):
   - The engine is projected as weights plus a KV estimate. Weights currently come from `footprint_gb` read as GiB (`orchestrator/src/atlas/config.py:322-331`). The KV estimate comes from the per-token table (`arbiter.py:150-162`).
   - The request is refused if the projection exceeds the budget (`arbiter.py:789`).
   - Victims are chosen: the Apex engine first, then the least recently used engine, until fewer than `MAX_RESIDENT` remain and the new engine fits (`arbiter.py:916-944`).
   - The request is queued if a victim is generating. A generation is never preempted (`:936`).
   - A placeholder is reserved, and `_busy` is set. While a load is in flight, any second load or unload is queued (`:767`, `:809`). Only one load or unload happens at a time.
3. **Unload each victim** (`_stop_and_confirm`, `arbiter.py:1023-1068`):
   - Read `mem_info_gtt_used`, then run `sudo systemctl stop llama-server@<victim>`. The stop timeout is 120 s, and `KillMode=mixed` sends SIGKILL after it (`systemd/llama-server@.service:55-56`).
   - Poll the GTT counter every 1 s until it falls to `max(resident set, before − share) + 1 GiB` (`:1049`).
   - The poll gives up after 60 s (`:451-452`). The Arbiter then **halts**: every later load is refused until a person intervenes (`:1057`).
4. **Load.** The Arbiter runs `systemctl start`. The unit's `ExecStartPost` polls `/health` until it returns 200, with `TimeoutStartSec=900` (`llama-server@.service:50-53`). The controller then re-checks readiness (`orchestrator/src/atlas/engines.py:680-687`). The GTT delta is recorded as that engine's release target (`arbiter.py:877`).
5. **Re-measuring the small models.** The resident set is measured at orchestrator start, after every leftover non-resident unit has been stopped (`api.py:1690-1704`). It is re-measured whenever nothing is resident (`api.py:1060-1070`).

The small models are never part of a swap. They load once at boot, are measured once, and stay.

### 1.3 Facts the efficiency question turns on (VERIFIED at b11474 unless marked)

- **No double copy of the weights.** On an integrated GPU the Vulkan backend reports `mmap_support = !is_integrated_gpu` (`ggml/src/ggml-vulkan/ggml-vulkan.cpp:15314-15320`). With the default `--load-mode auto`, llama.cpp then turns mmap off (`src/llama-model.cpp:1524-1532`). The old pin behaves the same way (`ggml-vulkan.cpp:19132` at b29c606).
  - The file is read once into GTT buffers. Its pages pass through the page cache as clean, reclaimable cache, which the kernel counts as available.
  - Dropping the caches by hand ("flushing") would add no headroom. The Arbiter budgets from MemTotal, not MemAvailable.
  - `--load-mode mlock` would add nothing on a node with no swap. The unit already allows it (`LimitMEMLOCK=infinity`, `llama-server@.service:57`).
- **The context cache is committed at load.** The pinned server allocates the whole `--ctx-size` when the engine loads (Section 4.3, `docs/ATLAS_FRAMEWORK_REVIEW.md:272`). Every token of context that is configured but never used is memory held for as long as the seat is resident.
- **Host-RAM prompt cache outside the budget.**
  - Each llama-server keeps a prompt cache in host RAM: `std::vector<uint8_t>` states, up to `--cache-ram`, default **8192 MiB per server** (`common/common.h:638`; `tools/server/server-context.cpp:1484-1492`; `tools/server/server-task.cpp:1742-1783`).
  - b11474 adds `--cache-idle-slots`, on by default. It saves every idle slot into that cache when a new task starts (`common/common.h:634`; `server-context.cpp:1553-1556`).
  - The router and the seat could therefore hold up to 16 GiB of host RAM between them. That equals the entire 16 GiB host reserve, and none of it appears on the GTT counter. B-33 flags this. Its `bad_alloc` fallback (`server-task.cpp:1775-1783`) seldom fires under Linux overcommit. The usual outcome would be the OOM killer (INFERRED).
- **Context checkpoints** default to 32 per slot. b11474 spaces them at least 8192 tokens apart (`common.h:635-637`), so a 32K slot holds at most about 4.

### 1.4 OOM safeguards, and the gaps

Present (VERIFIED):

- The pre-flight projection against the budget (rule 2).
- A measured GPU resident set and a fixed 16 GiB host reserve (`config.py:59`).
- Release confirmation before the next load, with a halt on timeout (rule 5).
- No preemption (rule 6).
- One load or unload at a time (`_busy`).
- Host-side use is logged as a WARNING when it exceeds the reserve. It is observed, never budgeted from (`arbiter.py:514-533`).
- Caps on the sandbox, buildfarm, Redis, ChromaDB and Open WebUI.
- `kho=off` and no crash-kernel reservation, so all of MemTotal can hold GTT pages (S48, S49).
- The hardware watchdog (S50), for a hard hang.

The GTT line is not a guard. `amdgpu.gttsize=196608 ttm.pages_limit=50331648` allows 192 GiB, more than MemTotal (Section 3.3; D-36 keeps it), so the kernel will not refuse a GTT allocation before RAM runs out. The Arbiter is the only backstop, as Section 4.3 says ("Hard pre-flight | Engine Arbiter | The actual OOM backstop", `docs/ATLAS_FRAMEWORK_REVIEW.md:276`).

Gaps:

1. **Unbounded host-side consumers.** These include the prompt caches above, a 16 GiB unreclaimable `/tmp`, three uncapped voice and document containers, 30 Celery prefork workers, and no `MemoryMax` on any unit. The capped containers alone sum to 19 GiB of limits, against a 16 GiB reserve.
2. **A failed seat load can restart outside the ledger** (INFERRED from systemd semantics).
   - `llama-server@.service` has `Restart=on-failure` with `RestartSec=5` (`:51-52`).
   - If a seat dies during its load (a Vulkan out-of-memory error, a crash), `systemctl start` fails. The Arbiter then frees the reservation (`arbiter.py:844-857`), but systemd schedules a restart 5 s later. Up to 3 restarts are allowed in 300 s (`:26-27`).
   - Meanwhile the Arbiter may load the D-40 fallback (gpt-oss-120b), so two engines would allocate at once.
   - Fix: the failure path should call `systemctl stop` on the unit (an allowed verb, and a stop cancels a pending restart). An alternative is `Restart=no` for non-resident units.
3. **No ordering for the OOM killer.** No unit sets `OOMScoreAdjust`. If the kernel kills the orchestrator, its restart stops the resident seat (`api.py:1690-1701`) and loses the measured footprints (B-03). Under pressure, the processes that should die first are Celery cpu, docling and Open WebUI, not the orchestrator or the seat.
4. **The CPU companions after D-14.** Once the retrieval pair runs on the CPU (Section 2), its memory leaves the GTT counter. The Arbiter must count it, through a larger reserve or a measured host resident set, or the 16 GiB reserve is quietly overcommitted.

### 1.5 RAM-efficiency measures

"Engine headroom" is GiB added to (or taken from) what a seat can use. "Exposure" is worst-case host RAM that the budget does not see. Exposure measures do not raise the budget. Each one makes the 16 GiB reserve honest, and a measured lower reserve can later return that memory to the seats.

| # | Measure | Expected gain | Cost | Risk | Day 1? |
|---|---|---|---|---|---|
| 1 | **Exact-byte footprints (D-31, S-20).** GiB from the bytes in MANIFEST.json, not `footprint_gb` read as GiB | Projection +10.77 GiB for GLM (156.82 → 146.05), +11.12 GiB for DeepSeek Q8 (161.87 → 150.75), +4.49 GiB for gpt-oss F16-trunk (65.37 GB → 60.88 GiB). Without it, DeepSeek Q8 is refused | Small code change | The hidden 7.4% margin disappears, so add the compute-buffer term (S-17) | Yes (approved, Before Day 1) |
| 2 | **Correct KV per token (S-17, B-03).** DeepSeek 327,680 → about 6,880 B/token. Add GLM 19,712 | DeepSeek projection −9.8 GiB at 32K f16 (10.0 → 0.21 GiB). GLM 8 × 32K f16 = 4.81 GiB | Medium | Estimates no longer err high. Phase 3 `kv-estimates` measures them | Yes (approved) |
| 3 | **One LLM seat resident (D-32, S-23).** Limit made a setting at 1 | Each seat gets the whole budget; the vision 2-slot pairing profile goes (S-22) | Medium | The limit must count **LLM seats only**. If Chatterbox (external, CPU) counted toward a limit of 1, a TTS render would evict the seat | Yes (approved) |
| 4 | **Retrieval pair placement and size (D-14, Section 2)**: 4B pair on the CPU, sized to its callers | 8B pair resident: −16 to −20 GiB. 4B pair, right-sized: −7.9 GiB against today's bge pair. Gain over the 8B pair: +7 to +11 GiB | Medium (engine-env, reserve or host set) | CPU prefill about 3× slower than iGPU | Yes (approved D-14) |
| 5 | **Explicit `--cache-ram` per engine (B-33).** Day 1 defaults, for example router 1024, seat 4096, gpt-oss 4096, embedder and reranker 0 (MiB) | Exposure −11 GiB worst case (16 GiB → 5 GiB) | engines.json `extra_args` (flag not reserved) | Fewer cached prompts, so more re-prefill when GLM switches between its seats | Yes, as defaults. The `cache-ram` to-do stays for the Phase 3 measurement |
| 6 | **Celery cpu concurrency** from `nproc - 2` (30) to about 8 | About 1.5-3 GiB of host RAM (INFERRED, 60-120 MB per prefork child) | One line (`phase2/02-orchestrator.sh:411`) | Fewer parallel background tasks | Yes |
| 7 | **`/tmp` tmpfs cap 16G → 4G** | Exposure −12 GiB worst case (tmpfs cannot be reclaimed without swap) | One line (`phase1/03-mounts.sh:140`) | A tool writing more than 4 GiB to /tmp fails | Yes |
| 8 | **`mem_limit` on kokoro, speaches and docling** (for example 2g / 3g / 6g). Optional `MemoryHigh` on atlas-celery-cpu | 0 GiB freed. It bounds a runaway, such as a large PDF in docling | Compose edits | Too low a cap kills a legitimate job | Yes (generous values) |
| 9 | **`OOMScoreAdjust`**: positive for celery-cpu and docling, negative for the orchestrator and `llama-server@` | 0 GiB. It picks the right victim | Unit lines | Low | Yes (cheap) |
| 10 | **Stop a seat whose start failed** (gap 2) | Closes a double-allocation path | Small (controller failure path) | Low | Yes |
| 11 | **Count the CPU companions in the budget**: measured RSS of their units at `measure_resident_set` (`process_rss_bytes` already exists, `arbiter.py:206`), or a larger default reserve | 0 GiB. It keeps the reserve true | Small to medium | Over-counting RssFile is conservative | Yes (with D-14) |
| 12 | **Seat context targets** (D-16 defaults, D-48 profiles). For example DeepSeek 1 × 131,072 f16 rather than 1M | DeepSeek 1M → 128K saves 5.9 GiB (6.72 → 0.84). GLM q8_0 instead of f16 saves 2.25 GiB but conflicts with D-29 (a) | Config | Shorter windows | Yes (defaults plus a to-do) |
| 13 | **Smaller companion `-ub`** (8192 → 2048). Possible at b11474 because #28876, rank-pooling batch splitting for causal rerankers, is between the pins (`git log`: 4da6337) | About 1-3 GiB of compute buffer for the pair (INFERRED) | Config | Slightly slower bulk embedding | Yes |
| 14 | **The glm5v mmproj on the one GLM unit, not a second engine key with the same weights** | Avoids a full 146 GiB swap (50-85 s, D-23) on every text-to-vision switch. Costs the mmproj (size not recorded; comparable mmprojs are 0.45-1.16 GiB) plus the image-encoder buffer while GLM is resident | Config | GLM's room shrinks by about 1-2 GiB | Yes |
| 15 | **Reserve size**: keep 16 GiB until `host_used` is logged on the node, then set it to the measured peak plus margin | Possibly +4 GiB to every seat (16 → 12) once measures 5-9 bound host use | None now | Under-reserving causes a host OOM | No. Phase 3 to-do |
| 16 | `--load-mode dio` (O_DIRECT, `src/llama-mmap.cpp:200`) for the large seats | 0 GiB of budget. It stops a 146-151 GiB read churning the 10-25 GiB of useful page cache | Config | Load time unmeasured (T-14) | No. Phase 3 test |
| 17 | Whisper TTL (`STT_MODEL_TTL=-1` → 300 s) | About 1 GiB of host RAM while idle (INFERRED) | Config | First transcription is slower | Optional |
| 18 | Already decided against, or no gain: `--swa-full` (costs 2.35 GiB, B-74), `--kv-unified` (memory-neutral, B-74; D-48 Phase 3), mlock, manual cache drops, `--n-cpu-moe` (a loss on unified memory, roster notes), a smaller router quant | — | — | — | No |
| 19 | Phased, so no Day 1 memory cost: OCR companion (about 1.3-1.5 GiB if resident, C-04), Qwen3-ASR (2.17 GB), EmbeddingGemma 2 (0.29-0.52 GiB), slot save/restore (disk, not RAM; D-44) | — | — | — | Record only |

### 1.6 Plain-language answer for the Principal

The small models are never flushed and reloaded. The router (and, after D-14, the embedder and reranker) start once when the node boots and then stay loaded. The Arbiter measures what they take, subtracts that plus a fixed 16 GiB safety reserve for the operating system and services, and treats what is left as the one big model's space.

Under the approved rule (D-32), only one big model is in memory at a time. That holds even for the smaller utility model, gpt-oss-120b. When a different big model is needed, the order is:

1. Wait for the current answer to finish. A generation is never interrupted.
2. Stop the old model.
3. Watch the GPU memory counter until the old model's memory has actually come back. If it has not come back within 60 seconds, stop and ask for a person, rather than risk a crash.
4. Load the new model alone into the space it left.
5. Only after it reports healthy is your request sent to it.

So nothing small is loaded at the moment of a swap. The small ones were already there and were already subtracted. Two big models are never in memory together, and the Deep Think pair (GLM and DeepSeek) takes turns.

The real out-of-memory risks are elsewhere:

- Memory the Arbiter cannot see: each model server's prompt cache (up to 8 GiB each by default), a 16 GiB `/tmp`, and uncapped containers.
- A model that fails to load and is restarted by systemd behind the Arbiter's back.

Measures 5-10 close those risks. Measures 1, 2, 4 and 12 are where real headroom comes from.

---

## 2. D-14: where the retrieval pair runs, and which size

**Recommendation: (e), run the pair on the CPU, at the 4B size (Qwen3-Embedding-4B Q8_0 plus Qwen3-Reranker-4B Q8_0).**

### 2.1 The facts that decide it

1. **The CPU uses the same memory.** Moving the pair from the iGPU to the CPU takes it off the GTT counter and puts it into host use. The budget formula subtracts the GPU set and a fixed host reserve, so the reserve must grow by exactly what the GPU set shrinks. Placement (e) saves no memory compared with placement (a) or (b) (INFERRED from `arbiter.py:541-551`).
2. **On-demand loading (d) would save memory only while the pair is unloaded, and the embedder is needed almost all the time:**
   - On every chat dispatch: `_retrieve` embeds the query for scars and memory (`api.py:1171-1195`), and each turn is written back to memory.
   - By Open WebUI directly: `RAG_OPENAI_API_BASE_URL` points at port 8109 (`docker/core/compose.yml:249-252`).
   - By LightRAG directly: `EMBEDDING_BINDING_HOST` (`phase2/04-memory.sh:1021-1022`).

   Neither direct caller goes through the Arbiter. There are two ways to build on-demand loading, and both fail:
   - llama-server's `--sleep-idle-seconds` frees the model and reloads it on the next request (`server-context.cpp:1055-1070`). That reload is invisible to the Arbiter, so its memory must be reserved anyway. Otherwise a wake-up beside a 150 GiB seat aborts (`GGML_ABORT("failed to reload model after sleeping")`) or overcommits the reserve.
   - An Arbiter-managed version needs a new companion class with wake and evict rules. Retrieval would also queue or fail whenever the resident seat fills the budget.

   In both cases the steady-state memory equals (e). There is more risk and new Day 1 code.
3. **The reranker has no caller in v0.3.5.** No code under `orchestrator/src/atlas` posts to `/v1/rerank`. `memory.query` returns plain vector hits, and Open WebUI has no reranker configured. Only the Phase 2 health check (`phase2/04-memory.sh:528-541`) and the gate use it. What the reranker costs on Day 1 is memory, not latency.
4. **The CPU keeps the pair off the iGPU's compute queue.** Embedding calls are not inside the generation slot. On the iGPU they would interleave with the seat's decode, while on the CPU they use cores that sit idle during Vulkan inference. Memory-bandwidth contention remains during bulk indexing (INFERRED: perhaps 10-25% slower seat decode while a large ingest runs).
5. **On the CPU the Q8_0 weights stay file-backed.**
   - The CPU backend has no x86 repack for Q8_0; Q8_0 repacking exists for NEON only (`ggml/src/ggml-cpu/repack.cpp:5105-5115`).
   - With `--device none`, only the CPU device is listed (`common/arg.cpp:1122-1130`), so mmap stays on. The weights are clean page-cache pages, and the KV cache and compute buffers are the only anonymous memory.
   - This is a soft property: under pressure the kernel could drop the weights, and the next call would re-read them from disk. Still budget them in full (measure 11 in Section 1.5).

### 2.2 Throughput, CPU against iGPU (measured where available)

| Source | Build | Model | CPU pp512 | iGPU (Vulkan) pp512 | Ratio |
|---|---|---|---|---|---|
| [llama.cpp #13565](https://github.com/ggml-org/llama.cpp/issues/13565), Ryzen AI MAX 395, May 2025 | b5392 | Llama-2-7B Q4_0 | 304.42 t/s (native ggml-cpu) | 881.38 t/s | 2.9× |
| [OpenBenchmarking 2602150-NE-STRIXHALO14](https://openbenchmarking.org/result/2602150-NE-STRIXHALO14), Ryzen AI Max+ 395, 2026-02-15 | b7083 | Qwen3-8B Q8_0 | 19.37 t/s (CPU **BLAS** build) | 1017.99 t/s (ROCm 807.54) | 53× (BLAS build, not the backend ATLAS would run) |

No embedding-model throughput has been measured on gfx1151 or on a Zen 5 CPU. Projections (INFERRED, scaled by parameter count from the native-CPU row):

| Model | CPU prefill | iGPU prefill |
|---|---|---|
| 8B | about 250-300 t/s | about 1,000 t/s |
| 4B | about 450-600 t/s | about 1,800-2,000 t/s |

What that means for each workload on the CPU with the 4B:

- **Query embedding** (50-200 tokens per chat turn): about 0.1-0.4 s, close to the iGPU.
- **Document ingest**: about 20 s per 10K tokens, against about 5 s on the iGPU. With D-45 (a) the corpus is empty at Day 1, so there is no initial re-index. Later ingests run as background Celery jobs.
- **Reranking**, if it is ever wired into the chat path: 20 candidates × 400 tokens is about 8K tokens, which takes about 14-18 s on the CPU against about 4 s on the iGPU. Keep reranking out of the per-turn chat path on Day 1, or cap it at about 8-10 candidates of 512 tokens or fewer.

T-15 should add a node measurement: `llama-bench -p 512` with `-dev none` against the iGPU for both 4B models.

### 2.3 Memory each placement takes

Sizes are VERIFIED file bytes (Qwen official and Voodisss GGUFs; `research_notes/ATLAS LLM roster refresh 2026/gap_closure_2026-10-14.md:73-75`). The KV figure is INFERRED from the Qwen3-4B and 8B configs (36 layers × 8 KV heads × 128 dims × K and V × 2 B = 147,456 B/token at f16). Compute buffers are INFERRED.

| | 4B pair | 8B pair |
|---|---|---|
| Weights (Q8_0) | 3.986 + 3.986 = 7.97 GiB | 7.494 + 7.494 = 14.99 GiB |
| Default sizing (ctx 8192 f16 each, `-ub 8192`) | about 13.2 GiB | about 20.2 GiB |
| Right-sized (ctx 4096, q8_0 KV with `--flash-attn on`, `-ub 2048`) | about 9.4 GiB | about 16.4 GiB |
| (a)/(b) iGPU resident | All of it on the GTT counter, so the resident set grows | Same |
| (d) on demand | 0 while unloaded, the same figure while loaded. Must fit beside the seat or be reserved | Same |
| (e) CPU resident | The same figure, as host memory. The reserve must grow by it | Same |

For comparison, today's bge pair takes about 1.3 GiB of weights plus its buffers.

### 2.4 Do DeepSeek UD-Q8_K_XL (150.75 GiB) and GLM UD-IQ4_XS (146.05 GiB) still load with KV room?

Budget = 187.5 − 5.5 (router-only GPU set) − 16 − companions. "Room" is what is left after the weights for KV, compute buffers and host buffers. The research allows about 9 GiB for compute and host buffers (`research_notes/ATLAS specialist LLM arsenal/single_engine_performance.md:178`, `:213-224`; inferred, not logged on gfx1151).

| Companions (placement-independent) | Budget | GLM room | DeepSeek Q8 room |
|---|---|---|---|
| bge pair (v0.3.5) | about 164.5 | 18.5 | 13.8 |
| **4B pair, right-sized** | **about 156.6** | **10.6** | **5.9** |
| 4B pair, default sizing | about 152.8 | 6.7 | 2.0 |
| 8B pair, right-sized | about 149.6 | 3.6 | −1.1 (refused) |
| 8B pair, default sizing | about 145.8 | −0.3 (refused) | −5.0 (refused) |
| 4B embedder only, reranker stopped | about 161.3 | 15.3 | 10.6 |
| 8B embedder only, reranker stopped | about 157.8 | 11.8 | 7.1 |

KV needs of the seats (D-29 (a), f16):

- GLM at 8 × 32K needs 4.81 GiB.
- DeepSeek needs 0.21 GiB at 32K, 0.84 GiB at 128K, 2.52 GiB at 384K and 6.72 GiB at 1M (6,880 B/token; `research_notes/ATLAS specialist LLM arsenal/verification_named_heavyweight_moe.md:42`).

Verdict:

- **The 8B pair is incompatible with the Day 1 seats in every resident placement.** Neither DeepSeek Q8 nor GLM keeps usable room.
- **With the 4B pair right-sized, both seats load:**
  - GLM keeps about 10.6 GiB, which is enough for its 4.81 GiB cache plus a few GiB of compute buffers. If T-04 forces `-fa off` on Vulkan, GLM's attention buffer grows; that is the first number to watch.
  - DeepSeek keeps about 5.9 GiB. That covers a 128K f16 cache (0.84) only if its Vulkan compute buffer is below about 5 GiB (unmeasured; the 24 GiB CUDA report in PR #24162 came from a batched benchmark, `verification_single_engine_performance.md:41`). The 1M window does not fit with any upgraded pair.
  - This is less room than D-11's "about 13 GiB" and below the research's 9 GiB allowance.
- **Day 1 defaults** (to-dos in brackets):
  - DeepSeek runs at `--parallel 1 --ctx-size 131072` f16 [to-do: Principal's DeepSeek context target, D-16 style].
  - If Phase 3 finds DeepSeek short of room, the relief order is:
    1. Stop the reranker, which has no Day 1 caller (+4.7 GiB, DeepSeek room 10.6 GiB).
    2. Cut DeepSeek's context.
    3. Fall back to UD-Q4_K_XL (144.44 GiB, D-11 (b)).

### 2.5 Why 4B and not 8B

- **Quality.** The 4B keeps nearly all of the gain over bge:
  - Embedder, MTEB multilingual: 69.45 against 70.58 (−1.13). English v2: 74.60 against 75.22.
  - Reranker, MMTEB-R: 72.74 against 72.94 (−0.20).
  - The bge pair scores 59.56 (embedder) and 58.36 (reranker).
  - Sources: `research_notes/ATLAS specialist LLM arsenal/named_companion_engines.md:60`, `:96`; D-14 row.
- **Fit.** Only the 4B pair leaves DeepSeek Q8 and GLM loadable (Section 2.4).
- **Speed.** On the CPU the 4B is about twice as fast as the 8B (prefill scales with active parameters).
- **Licensing and server support are equal.** Both are Apache 2.0. The official Qwen3-Embedding GGUF carries `pooling LAST`, and the Voodisss reranker conversions carry the rank head. T-15 still checks the 4B header. The mradermacher 8B file lacks the rank head (`gap_closure_2026-10-14.md:203`).

### 2.6 Implementation notes for (e) at 4B

- **Rendering (engine-env.py).** Render the two companions with `--device none`. `build_args` adds `--n-gpu-layers all` unconditionally (`phase2/engine-env.py:216`), which is harmless with no GPU device listed; a placement field would be cleaner. Also pass:
  - `--embedding --pooling last` (embedder) and `--reranking` (reranker)
  - `--flash-attn on`, plus `--cache-type-k/v q8_0` if the KV cache is quantised
  - `--ctx-size 4096` and `-ub 2048`, with `-b` at least `-ub`
  - `--threads 6-8` and `--cache-ram 0`

  None of these flags is reserved (`engine-env.py:85-94`).
- **Arbiter accounting.** Count the companions through measure 11 in Section 1.5: either a measured host resident set (the VmRSS of the two units' MainPIDs, taken in `measure_resident_set`), or `DEFAULT_ARBITER_HEADROOM_GIB` raised from 16 to 26 (`config.py:59`; the range 4-64 allows it). Otherwise the reserve is silently overcommitted by about 9.4 GiB.
- **Schema and re-index.** Keep the 1024-dimension schema: truncate the output to 1024 dimensions, re-normalise, and add the query instruction prefix (S-41). Re-index before Day 1 per D-45 (a), while the corpus is still empty.
- **Document and records.** Update the 4.1 rows: "embedding model" moves from the iGPU rows into the CPU rows, and the GPU resident set becomes router-only. Record a to-do for the reranker caller. Extend T-15 with the CPU-against-iGPU throughput measurement and the measured host footprint of the pair.

---

## 3. P-10: why Eleanor's routing model is 4B and not 32B

> The Principal: "why is Eleanor's LLm so small, only 4B ... why we cant have a 32B instead, its all about top results."

### 3.1 What the router does

- It runs on **every request**. The classifier verdict is a generation in its own right, held in the single generation slot before the request's own generation (`api.py:1135-1145`), so its latency is added to every chat and blocks the queue behind it.
- It **must be resident and separate from the seat.** Routing decides which seat the Arbiter loads, and it has to answer while a swap is in flight. A classifier on the seat itself would either force a swap to route or change behaviour with whichever seat happens to be loaded.
- Its job is narrow: one JSON object with a closed label set, at temperature 0 with at most 200 tokens. The labels are 2 personas, 23 task-force codes, a long-document flag, privacy tags and a Deep Think depth (`orchestrator/src/atlas/router.py:253-291`).
- The privacy membrane does not depend on it. Deterministic keyword hard rules run first and always win (7.2 rule 1; `router.py:3-11`). The Principal's prefixes override it, every decision is logged with its reason, and an overridden routing decision becomes a strike (9.4).
- It also serves the 4B helper calls: LightRAG extraction (`LLM_MODEL=router-qwen3.5-4b`, `phase2/04-memory.sh:1016-1020`), Sentinel BLUFs and retention summaries.

### 3.2 What a 32B-class router would cost

| | Qwen3.5-4B Q8_0 (current) | Qwen3.8-27B Q8_0 (nearest 2026 dense) | 32B dense Q8_0 (INFERRED about 34.8 GB) | 32B Q4_K_M (INFERRED about 19.8 GB) |
|---|---|---|---|---|
| Resident memory | 4.19 GiB | 27.05 GiB (29.05 GB, roster report L32, L128) | about 32.4 GiB | about 18.4 GiB |
| Engine budget (bge pair) | about 164.5 GiB | about 141.6 GiB | about 136.3 GiB | about 150.3 GiB |
| GLM 146.05 / DeepSeek Q8 150.75 | Both load | **Neither loads** | **Neither loads** | GLM has 4.2 GiB of room; DeepSeek refused |
| Decode | about 45-50 t/s (INFERRED, twice the measured 25.65 t/s of a Qwen3-8B Q8_0) | 7.3-7.74 t/s measured on Strix Halo (Q8_0; `research_notes/ATLAS LLM roster refresh 2026/strix_halo_capacity_performance.md:29`, `:85`, `:131`) | about 6-7 t/s | about 12 t/s |
| Prefill | about 1,800-2,000 t/s (INFERRED) | about 300 t/s (Q4_K_M pp512 334.6 measured, `:100`) | lower | about 300 t/s |
| One verdict (300-3,300 prompt tokens, about 80 output tokens) | about 2-3.5 s | about 12-22 s | about 15-25 s | about 8-15 s |

Every GiB that is resident comes off every engine's budget (`arbiter.py:550`). A 32B router would therefore shut out both Day 1 quality engines, and it would add 10-20 s to every request.

A 30B-class mixture-of-experts model (for example Qwen3.5-35B-A3B) would decode about as fast as the 4B, but at Q8_0 it still holds about 35 GB (INFERRED). The memory objection stands. The largest step that fits is Qwen3.5-9B (about 8.9 GiB, INFERRED). It would cost about 4.7 GiB of every seat's room, and DeepSeek has only about 5.9 GiB after D-14.

### 3.3 Routing-accuracy evidence

- **No routing benchmark exists for ATLAS's classifier**, in any research file or on the node. The arsenal kept Qwen3.5-4B because:
  - no newer sub-10B model exists in the family;
  - the 2026 Qwen releases are 27B and above;
  - the alternatives, Gemma 4 E4B and Granite 4.1 8B, have "no measured routing advantage".

  Sources: register P-10 and C-01; `research_notes/ATLAS specialist LLM arsenal/completeness_review.md:178-191`; `reports/ATLAS LLM roster refresh 2026.md:74`.
- **The vendor's instruction-following figures** ([Qwen3.5-4B card](https://huggingface.co/Qwen/Qwen3.5-4B)), likely measured with thinking on, while ATLAS runs it with `--reasoning off`:

  | Benchmark | Qwen3.5-4B | Qwen3.5-9B | gpt-oss-120b |
  |---|---|---|---|
  | IFEval | 89.8 | 91.5 | 88.9 |
  | IFBench | 59.2 | 64.5 | 69.0 |
  | BFCL-V4 | 50.3 | 66.1 | not given |

  Simple format-following (the router's main need: one valid JSON object from a closed set) is already at the level of models 30 times larger. Harder instruction-following and tool use improve with size.
- The right evidence is the Principal's own traffic. **Proposed to-do:** once the node is live, build a labelled routing set from the `routing_decisions` ledger and the overridden-routing strikes, and measure the 4B's error rate. If it is material, two options cost no resident memory:
  1. Ask the resident seat for a second opinion on ambiguous verdicts (one extra generation on GLM).
  2. A 9B router, at the 4.7 GiB cost above.

### 3.4 What the approved P-10 change does

- **Classification stays on the 4B router.** `router-qwen3.5-4b` remains `classifier_engine` (`config/router-rules.json`), stays always resident, and keeps the LightRAG and helper calls.
- **Eleanor's own work moves to GLM-5.3-Flash.** Her persona work (scheduling, triage, TF_RHO Executive Scheduling & Strategic Routing, domain 16 COO with Silas) now defaults to GLM-5.3-Flash. Today `config/personas/eleanor.md` has `default_engine: router-qwen3.5-4b` (lines 9-10); `resident_model` stays the router.
  - GLM is the default resident seat (D-23), so this normally costs no swap and no extra memory.
  - Her answers come from a 320B-class model (AA 41.8) instead of a 4B.
  - Her D-40 fallback is gpt-oss-120b.
- **Edits that follow:** Section 6.2's register row ("Resident 4B model"), the 5.3 and 5.1 role text, the persona map, and the persona file.

The routing decision itself stays fast and small, and it costs no budget beyond what Section 4.1 already reserves.

---

## Sources

- Code (HEAD 1b93482):
  - `scripts/day1/orchestrator/src/atlas/arbiter.py`, `config.py`, `engines.py`, `api.py`, `router.py`, `memory.py`, `sandbox.py`
  - `scripts/day1/phase2/engine-env.py`, `04-memory.sh`, `05-voice.sh`, `02-orchestrator.sh`, `06-tools.sh`, `06d-sandbox.sh`
  - `scripts/day1/phase1/03-mounts.sh`
  - `scripts/day1/docker/core/compose.yml`, `compose.voice.yml`
  - `scripts/day1/systemd/*.service`
  - `scripts/day1/config/engines.json`, `router-rules.json`, `personas/eleanor.md`
- Document: `docs/ATLAS_FRAMEWORK_REVIEW.md`, Sections 4.1-4.4 (L217-L297), 5.1-5.4, 7.1-7.2 (L401-L420).
- llama.cpp b11474 source (local clone):
  - `common/arg.cpp:1122-1130`, `:1701`, `:1719`, `:1735`, `:2694`, `:2741`, `:2871`, `:3820`
  - `common/common.h:481-489`, `:634-638`, `:658`
  - `src/llama-model.cpp:1524-1532`
  - `ggml/src/ggml-vulkan/ggml-vulkan.cpp:15314-15320`
  - `ggml/src/ggml-cpu/repack.cpp:5105-5115`
  - `tools/server/server-context.cpp:1055-1070`, `:1484-1492`, `:1553-1556`
  - `tools/server/server-task.cpp:1742-1783`
  - `src/llama-mmap.cpp:200`
  - Commit 4da6337 (#28876) lies between b29c606 and b11474.
- Register and responses:
  - `<build session scratchpad>/actions/register_final.json` (rows D-11, D-14, D-16, D-23, D-29, D-31, D-32, D-40, D-44, D-45, D-48, N-01, N-03, P-10, C-01 to C-04, S-17, S-20, S-21, S-23, S-36, S-38, B-02, B-03, B-33, B-74, T-15)
  - `<build session scratchpad>/reg-returned/responses.json`
- Research:
  - `reports/ATLAS specialist LLM arsenal.md` L82-L84
  - `research_notes/ATLAS specialist LLM arsenal/completeness_review.md` G8 and G9 (L168-L204), `named_companion_engines.md`, `single_engine_performance.md`, `verification_named_heavyweight_moe.md`
  - `research_notes/ATLAS LLM roster refresh 2026/gap_closure_2026-10-14.md`, `strix_halo_capacity_performance.md`
- Primary pages read 2026-10-16:
  - [llama.cpp issue #13565](https://github.com/ggml-org/llama.cpp/issues/13565)
  - [OpenBenchmarking result 2602150-NE-STRIXHALO14](https://openbenchmarking.org/result/2602150-NE-STRIXHALO14) (read through the mail.openbenchmarking.org mirror)
  - [Qwen3.5-4B model card](https://huggingface.co/Qwen/Qwen3.5-4B)
