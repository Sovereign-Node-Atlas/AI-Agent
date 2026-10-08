# Day 1 scripts — coverage matrix

Maps every step of Section 17 of `docs/ATLAS_FRAMEWORK_REVIEW.md` and every item of Section 21 (V1..V23, with
halves) to the file and function that implements or records it, and to the gate that judges it (CONVENTIONS.md §6).
Step functions are `step_<id>()` in the named file and are run by `run_step <phase> <id> step_<id>` (Phase 1 and 2
through `run_phase_steps`, Phases 3 and 4 by their drivers). Markers: `/var/lib/atlas/day1/done/<phase>.<id>`.

## Section 17 — steps

### Phase 1 (`phase1-platform.sh`, foreground, reboot after step 4)

| Step | Section 17 text (short) | File → function | Notes |
|---|---|---|---|
| 1 | Pre-flight: Ubuntu 26.04.1, kernel 7.0, two NVMe, fTPM, GPU by lspci/DRM | `phase1/01-preflight.sh` → `step_01` | V1 recorded (info). Secure Boot off (D15: the Principal enables it) and a missing SSH key are warnings + to-dos, never stops |
| 2 | LUKS2 on the data volume, TPM2 enrolment (`--tpm2-pcrs=7`), recovery key printed once | `phase1/02-luks.sh` → `step_02` | Pause: recovery key / OS passphrase on the console. V2 (`deferred` + to-do `os-volume-encryption` when only the OS volume is unencrypted; then no on-node recovery copy). Initramfs and crypttab's `tpm2-device=` left to step 4 until tpm2-tools exists. `--force 02` re-seals both TPM2 tokens to the current PCR 7 (TPM first; the OS passphrase and, without an on-node copy, the USB recovery key asked in the pause only when needed) and closes the to-do `secure-boot` once Secure Boot is on |
| 3 | Mount layout 3.5, swap off, tmpfs /tmp verified | `phase1/03-mounts.sh` → `step_03` | Creates `/srv/atlas/*`, `/srv/atlas/staging/inbox/`, `/srv/cold`, `/srv/backups` |
| 4 | System update, kernel parameters, ufw baseline, SSH hardening, Cockpit, squid allowlist proxy | `phase1/04-system.sh` → `step_04`; reboot by `phase1-platform.sh` → `phase1_request_reboot` | Installs tpm2-tools before the dist-upgrade, adds `tpm2-device=auto` to crypttab, rebuilds and checks every initramfs before the reboot (TPM unlock of the OS volume; the check reaches V2 in step 5). chrony NTS egress for uid `_chrony` only; to-do `time-sync` if it does not synchronise. `RDP_ALLOW_FROM` narrows 3389 on the LAN. `--no-reboot` leaves the reboot to the Principal |
| 5 | Reboot; post-reboot vulkaninfo `RADV GFX1151`, GTT pool matches | `phase1-platform.sh` → `phase1_check_reboot`; `phase1/05-postboot.sh` → `step_05` | V2 re-checked after the reboot, V3a |
| 5b | XFCE + xrdp bound to LAN/WireGuard, Google Chrome from Google's apt repo (managed policy: telemetry/sign-in/sync off), one RDP connection tested | `phase1/05b-desktop.sh` → `step_05b` | xrdp hardened (Xorg session only; no root login, no alternate shell; TLS). Waits up to 10 min for the RDP session; a timeout is V19 `deferred` + to-do `rdp-test`; a pass from a LAN address records the optional to-do `rdp-restrict` |
| 6 | Docker with GPU passthrough; `atlas` in `render`, `video`, `docker`; DOCKER-USER egress | `phase1/06-docker.sh` → `step_06`; `phase1/docker-egress-rules.sh`; `systemd/atlas-docker-egress.service` | |
| 7 | WG-Easy, Cloudflare ddns with the scoped token, ntfy | `phase1/07-remote.sh` → `step_07`; `docker/wg-easy/compose.yml`; `docker/ntfy/compose.yml`; `phase1/cloudflare-ddns.sh`; `systemd/atlas-ddns.{service,timer}` | No `CLOUDFLARE.txt`: DDNS skipped, to-do `cloudflare-token`. Prompt for the zone id once if the token cannot list zones (S11). Waits up to 10 min for the phone handshake; a timeout is V5 `deferred` + to-do `vpn-mobile-test` |
| 8 | Gate: V2, V3a, V5, V19; V1 info | `phase1/08-gate.sh` → `step_08` (`gate phase1 V2 V3a V5 V19 -- V1`) | Writes `done/phase1.gate` |

### Phase 2 (`phase2-services.sh`, foreground)

| Step | Section 17 text (short) | File → function | Notes |
|---|---|---|---|
| — | HF token prompt (if `/etc/atlas/secrets/hf-token.env` absent) | `phase2-services.sh` → `hf_token_prompt` | Pause 2 of CONVENTIONS §7.6 |
| 1 | llama-server, Vulkan build (`-DLLAMA_OPENSSL=ON -DLLAMA_USE_PREBUILT_UI=OFF`); ROCm container for tuned prefill is optional and not Day 1 | `phase2/01-llama.sh` → `step_01`; `systemd/llama-server@.service`; `phase2/engine-env.py` | V3b recorded here and at the gate |
| 2 | Redis, Celery `cpu`/`gpu` workers, orchestrator with Arbiter, router, approval queue, task ledger | `phase2/02-orchestrator.sh` → `step_02`; `orchestrator/` (package `atlas`); `docker/core/compose.yml` (redis); `systemd/atlas-orchestrator.service`, `atlas-celery-{cpu,gpu,beat}.service`; `/etc/sudoers.d/atlas-engines` | |
| 3 | Open WebUI, offline hardening, A.T.L.A.S./Ren/Arthur registered, router Filter | `phase2/03-openwebui.sh` → `step_03`; `docker/core/compose.yml` (open-webui, host network); `phase2/atlas-openwebui-egress.sh` + `systemd/atlas-openwebui-egress.service`; `orchestrator/src/atlas/openwebui_filter.py` | V12 recorded here and at the gate |
| 4 | ChromaDB, LightRAG, the three resident small models (router-qwen3.5-4b, embed-bge-m3, rerank-bge-v2-m3), Docling | `phase2/04-memory.sh` → `step_04` (`pull_engine_files`, `_mem_check_residents`); `docker/core/compose.yml` (chromadb); `docker/core/compose.voice.yml` (docling) | V10a (resident router) recorded, fatal to the step on fail |
| 5 | Kokoro, Chatterbox, Whisper Large-v3-Turbo (speaches), PyAnnote 3.1 | `phase2/05-voice.sh` → `step_05`; `docker/core/compose.voice.yml` (kokoro, speaches); `phase2/voice_render.py` | V6, V7 recorded here and at the gate |
| 6 | 15.1 tools: IfcOpenShell, Bonsai, MCP4IFC, Radiance, OpenStudio/EnergyPlus, KiCad CLI, Playwright, build container | `phase2/06-tools.sh` → `step_06`; `docker/buildfarm/Dockerfile` | |
| 6b | Cloudflare token relocated to `/etc/atlas/secrets/cloudflare.env`, `CLOUDFLARE.txt` shredded after the read-back test | `phase2/06b-cloudflare-token.sh` → `step_06b` | V23; `deferred` + to-do while no token or no zone id |
| 6c | Google OAuth pause: one link per account, wait for Allow | `phase2/06c-google-oauth.sh` → `step_06c` (`_g_authorise_all`); `phase2/google_oauth.py`; `atlas-gdrive@<tag>.service` (written by the step) | Prompt of §7.6, skippable. V20; `deferred` + to-do while `GOOGLE_ACCOUNTS` or the client JSON is missing |
| 6d | *(gap-fill, not in Section 17)* AEGIS sandbox image `atlas-sandbox:py3.12`, `SANDBOX_*` settings | `phase2/06d-sandbox.sh` → `step_06d`; `docker/sandbox/Dockerfile` | Needed for V17 (Section 16.4) |
| 7 | restic repository on the OS drive, nightly timer, first backup, first restore test | `phase2/07-restic.sh` → `step_07`; `phase2/atlas-aegis.sh`; `systemd/atlas-aegis.{service,timer}`, `atlas-aegis-trigger.path`, `atlas-aegis-forget.service`, `atlas-aegis-missed.service`, `atlas-restic-check.{service,timer}` | Prints the restic passphrase once (no pause). V13 |
| 8 | Sentinel timer with the D6 feeds; 72-hour pruning timer | `phase2/08-sentinel.sh` → `step_08`; `phase2/atlas-sentinel-telemetry.sh`; `config/sentinel-feeds.json`; `systemd/atlas-sentinel.{service,timer}`, `atlas-prune.{service,timer}` | Timers only enqueue the Celery task (S23) |
| 9 | Windows PC share mount unit, on demand | `phase2/09-windows-share.sh` → `step_09`; `systemd/srv-atlas-winpc.{mount,automount}` | Skipped on Day 1 (the Principal's choice): runs only when `WINDOWS_SHARE` is set and `/etc/atlas/secrets/smb.cred` exists; otherwise to-do `input-windows-share` / `input-smb-cred` |
| 9b | *(gap-fill, not in Section 17)* gocryptfs vault: helper, unit, sudoers, cipher dir | `phase2/09b-vault.sh` → `step_09b`; `orchestrator/src/atlas/vault.py` | Unattended: real vault left uninitialised, V18 `deferred`; `ATLAS_VAULT_INIT=1` initialises it from a console |
| 10 | Gate: every service healthy; V3b, V6, V7, V12, V20, V23 (+ V13, V14a, V15, V16, V17, V18) | `phase2/10-gate.sh` → `step_10` (`_gate_services`, `_gate_verifies`, `_gate_v10_half`, `gate phase2 ... -- V7 V10a`) | Writes `done/phase2.gate` |

### Phase 3 (`phase3-models.sh`, detached as `atlas-day1-phase3`)

| Step | Section 17 text (short) | File → function | Notes |
|---|---|---|---|
| 1 | Pull the seven GGUF engines with checksum verification (~690 GB) | `phase3-models.sh` → `step_01` (uses `pull_engine_files` from `phase2/04-memory.sh`, `_p3_write_manifest`, `_p3_render_envs`) | sha256 from the HF tree API (S7), `MANIFEST.json` |
| 2 | Per engine: load through the Arbiter, V4 KV proof, decode/prefill at 512 and 8k, swap time, unload, memory returned | `phase3-models.sh` → `step_02` (`_p3_loadtest prepare|engine|finish|summarize`); `phase3/loadtest.py` | V4 per engine, V10 per engine + summary, V22 (DeepSeek ladder, R19) |
| 3 | Two-residency test (gpt-oss-120b + qwen2.5-vl-72b at ~142 GB; second request queues) | `phase3-models.sh` → `step_03` (`_p3_loadtest coresident`); `phase3/loadtest.py` | V14b, V21 |
| 4 | Gate: table of load success, KV type, tok/s, swap seconds; DeepSeek row deferrable | `phase3-models.sh` → `step_04` (`_p3_loadtest table`; `gate phase3 V4 V10 V14b V21 -- V22`) | Writes `done/phase3.gate` |

### Phase 4 (`phase4-engines.sh`, detached as `atlas-day1-phase4`)

| Step | Section 17 text (short) | File → function | Notes |
|---|---|---|---|
| 1 | Build the ROCm base image; `rocminfo` reports `gfx1151`; PyTorch tensor/matmul/diffusion self-test | `phase4-engines.sh` → `step_01` (`_p4_wheel_preflight` before the build, then `docker build`); `phase4/wheel_preflight.py`; `docker/rocm-base/Dockerfile`; `verify/v11-rocm-selftest.sh`; `phase4/selftest.py` | `P4-wheels` info row from the pre-flight (a missing pinned wheel or an unreachable index stops the step before the build, naming the file and the index URL); V11; the phase stops here on fail |
| 2 | Green engines in order of value: FLUX.1-dev, Wan2.2, Florence-2, Chronos, UI-TARS-1.5-7B, Rad-DINO, SAM 2, Stable Audio Open, CosyVoice2, OpenVLA | `phase4-engines.sh` → `step_02` (`_p4_run_engine` per key); `phase4/lib-engine.sh`; `phase4/engines/{flux1-dev,wan2.2,florence-2,chronos,ui-tars,rad-dino,sam2,stable-audio-open,cosyvoice2,openvla}.sh` + `<name>_test.py`; `config/phase4-engines.json` | A green failure is recorded `fail`, the phase continues |
| 3 | Yellow engines: TRELLIS, Blender Cycles HIP with CPU fallback | `phase4-engines.sh` → `step_03`; `phase4/engines/trellis.sh`, `blender-cycles.sh` | `deferred` on failure, never blocks |
| 4 | PointLLM (V8), Clay or Prithvi (V9), deferred if they fail | `phase4-engines.sh` → `step_04` (`record_v "$vid"`); `phase4/engines/pointllm.sh`, `clay-prithvi.sh` | |
| 5 | Register every passing engine with the Arbiter with its measured footprint | `phase4-engines.sh` → `step_05` (`POST /arbiter/register`); `orchestrator/src/atlas/api.py` | |
| 6 | Gate: per-engine table built/loaded/sample/footprint/pass-fail-deferred | `phase4-engines.sh` → `step_06` (`_p4_table`; `gate phase4 V11 -- V8 V9 P4-wheels`) | Writes `done/phase4.gate` |

`evo` and `modulus` in `config/phase4-engines.json` are tier `deferred` with no build script: they are the Section 15.5
watch-list entries kept in the json for the Arbiter's reference, not Section 17 steps. No engine entry is blocked only
by an external account action or a licence (evo: FP8 hardware and an unverified port; modulus: CUDA-locked; TRELLIS:
yellow on Section 15.2's sparse-voxel kernel risk, and its DINOv2 weight host is checked against the rendered squid
list before any request; FLUX.1-dev and Stable Audio Open need their Hugging Face licences accepted BEFORE Phase 4 as a
Day 1 prerequisite, README, and fail loudly rather than defer).

### Entry point and reporting

| Command | File → function |
|---|---|
| `sudo ./atlas-day1.sh phase1..4` | `atlas-day1.sh` (mirror to `/opt/atlas/day1`, gate check, `run_foreground_phase` / `run_detached_or_foreground`, `lib/common.sh` → `detached_phase`) |
| `sudo ./atlas-day1.sh status` | `lib/common.sh` → `phase_status`, `verify_table` |
| `sudo ./atlas-day1.sh report` | `atlas-day1.sh` → `do_report`; `tools/fill-workbook.py` |

## Section 21 — verification items

Recording: `record_v` (direct) or `run_verify ID verify/<script>` (exit 0/1/2/3 → pass/fail/deferred/info), both into
`/var/lib/atlas/day1/verify.jsonl`. "Gate" is the phase whose `gate` call reads the id; "required" blocks the next
phase on `fail` or missing, "recorded" never blocks, `deferred` never blocks.

| Id | Proof (Section 21) | Recorded by (file → function; verify script) | Gate |
|---|---|---|---|
| V1 | Network link up (informational; Wi-Fi) | `phase1/01-preflight.sh` → `step_01`; `verify/v01-network.sh` | Phase 1, recorded (info) |
| V2 | fTPM present, TPM2 enrolment succeeds | `phase1/02-luks.sh` → `step_02` (`record_v V2 fail` on the unlock test, `run_verify V2`); re-proved after the reboot by `phase1/05-postboot.sh` → `step_05`; `verify/v02-tpm.sh` (fails on an empty PCR list, S9; exit 2 `deferred` when only the OS volume is unencrypted) | Phase 1, required |
| V3a | Kernel parameters accepted on 7.0, GTT pool matches, `RADV GFX1151` | `phase1/05-postboot.sh` → `step_05`; `verify/v03a-gtt.sh 196608` (accepts the gttsize deprecation warning, S8) | Phase 1, required |
| V3b | `llama-cli --list-devices` reports ~170 GB | `phase2/01-llama.sh` → `step_01` and `phase2/10-gate.sh` → `_gate_verifies`; `verify/v03b-llama-devices.sh` | Phase 2, required |
| V4 | Quantised KV cache applied per model, no silent fallback (`llama_kv_cache ... K (q8_0) V (q8_0)` line + health, S22) | `phase3/loadtest.py` → `record("V4", ...)` per engine, through `phase3-models.sh` → `step_02` (`_p3_loadtest engine`) | Phase 3, required (per engine) |
| V5 | WireGuard reachable from mobile data via `vpn.sovereign-node.link` | `phase1/07-remote.sh` → `step_07`; `verify/v05-wireguard.sh` (handshake from a non-LAN address within 540 s) | Phase 1, required-deferrable (timeout → `deferred`, to-do `vpn-mobile-test`) |
| V6 | PyAnnote 3.1 gated model accepted and loading | `phase2/05-voice.sh` → `step_05` and the gate; `verify/v06-pyannote.sh` | Phase 2, required; `deferred` (exit 2) without an HF token or while the licence is not accepted |
| V7 | Voice casting listening test; Alaric and Gideon clones | `phase2/05-voice.sh` → `step_05` (`_voice_render_v7` → `phase2/voice_render.py`) and the gate; `verify/v07-voice-listen.sh` (`deferred` when the reference recordings are absent) | Phase 2, recorded. Transcoding note: the Principal drops `alaric.*` / `gideon.*` as WAV, MP3 or M4A in `staging/inbox/voice-references/`; `phase2/05-voice.sh` converts them with ffmpeg to the 16 kHz mono WAV the Chatterbox clone reads before `phase2/voice_render.py` renders, so the format of the recording never decides V7 (README, Phase 2 step 5) |
| V8 | PointLLM builds and runs on ROCm in the container | `phase4-engines.sh` → `step_04` (`record_v V8` from `verify_id` in `config/phase4-engines.json`); `phase4/engines/pointllm.sh` | Phase 4, recorded (deferred on failure) |
| V9 | Clay or Prithvi builds and runs | `phase4-engines.sh` → `step_04` (`record_v V9`); `phase4/engines/clay-prithvi.sh` | Phase 4, recorded (deferred on failure) |
| V10 | All seven GGUF engines load, generate, swap, release; tok/s within bands | `phase3/loadtest.py` → `record("V10", ...)` per engine and the summary row (`summarize`), through `phase3-models.sh` → `step_02` | Phase 3, required |
| V10a | Phase 2 half of V10: Eleanor's resident 4B router answers a chat completion | `phase2/04-memory.sh` → `_mem_check_residents` (fatal to step 4 on fail) and `phase2/10-gate.sh` → `_gate_v10_half`; `verify/v10a-router-resident.sh` | Phase 2, recorded (a fail also marks the gate unhealthy) |
| V11 | In the ROCm container: `rocminfo` reports `gfx1151`; tensor, matmul, diffusion self-tests | `phase4-engines.sh` → `step_01` (`run_verify V11`, `record_v V11 fail` with the diagnostic image); `verify/v11-rocm-selftest.sh`; `phase4/selftest.py` | Phase 4, required |
| V12 | Open WebUI makes no outbound connection after hardening (firewall log) | `phase2/03-openwebui.sh` → `step_03` and the gate; `verify/v12-openwebui-offline.sh atlas-openwebui 120` | Phase 2, required |
| V13 | restic backup completes, restore verifies by checksum | `phase2/07-restic.sh` → `step_07` and the gate; `verify/v13-restic.sh` | Phase 2, required |
| V14a | Arbiter refuses an over-budget load and downgrades Deep Think depth (stub footprints) | `phase2/10-gate.sh` → `_gate_verifies`; `verify/v14a-arbiter-stubs.sh` (`pytest tests/test_arbiter.py`) | Phase 2, required |
| V14b | The same against real engines (two-engine test) | `phase3/loadtest.py` → `record("V14b", ...)` in `coresident`, through `phase3-models.sh` → `step_03` | Phase 3, required |
| V15 | Approval gate holds a standard-tier email until approved; routine auto-sends and logs | `phase2/10-gate.sh`; `verify/v15-approval-gate.sh` (`tests/test_approval.py`) | Phase 2, required |
| V16 | Router hard rule routes "medical" to Arthur against the classifier, logged | `phase2/10-gate.sh`; `verify/v16-router-hard-rule.sh` (`tests/test_router.py`) | Phase 2, required |
| V17 | Sandbox memory cap kills a runaway process without affecting the node | `phase2/10-gate.sh`; `verify/v17-sandbox.sh` (image from `phase2/06d-sandbox.sh`) | Phase 2, required |
| V18 | Vault opens by button, locks on idle, vault-tagged content absent from memory | `phase2/09b-vault.sh` → `step_09b` (real vault, only with `ATLAS_VAULT_INIT=1`) and `phase2/10-gate.sh` → `_gate_v18` (test vault → `deferred` while the real vault is uninitialised); `verify/v18-vault.sh` | Phase 2, required (`deferred` does not block) |
| V19 | XFCE over xrdp from the Windows PC; refused outside LAN/WireGuard | `phase1/05b-desktop.sh` → `step_05b`; `verify/v19-xrdp.sh` | Phase 1, required-deferrable (timeout → `deferred`, to-do `rdp-test`) |
| V20 | Google OAuth completed for both accounts; Gmail, Calendar, Drive reachable | `phase2/06c-google-oauth.sh` → `step_06c` (`record_v V20 deferred` + to-do on blank `GOOGLE_ACCOUNTS` or a missing/invalid client JSON, `fail` on a failed consent; `run_verify V20`) and the gate; `verify/v20-google.sh` | Phase 2, required |
| V21 | Two engines resident at ~142 GB; a second request queues | `phase3/loadtest.py` → `record("V21", ...)` in `coresident`, through `phase3-models.sh` → `step_03` | Phase 3, required |
| V22 | DeepSeek V4 Flash loads at `UD-Q4_K_XL` and generates; else deferred | `phase3/loadtest.py` → `record("V22", ...)` (KV ladder f16 → q8_0 → q4_0, S2) in `summarize`, through `step_02` | Phase 3, recorded (R19) |
| V23 | Cloudflare token in a mode-600 env file, `CLOUDFLARE.txt` deleted | `phase2/06b-cloudflare-token.sh` → `step_06b` and the gate; `verify/v23-cloudflare-token.sh` | Phase 2, required; `deferred` + to-do while the token or zone id is missing |

Gate calls, verbatim: `gate phase1 V2 V3a V5 V19 -- V1` (`phase1/08-gate.sh`),
`gate phase2 V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 -- V7 V10a` (`phase2/10-gate.sh`),
`gate phase3 V4 V10 V14b V21 -- V22` (`phase3-models.sh`), `gate phase4 V11 -- V8 V9 P4-wheels` (`phase4-engines.sh`).

### Rows that are not V items (Section 21 scope note, v0.3.2)

Written into the same `verify.jsonl` with the exact line shape `record_v` writes (`{"ts","phase","id","result","msg"}`,
python3 `json.dumps`), but NOT by `record_v`: `lib/common.sh record_v` accepts V ids only (`^V[0-9]+[a-z]?$`,
CONVENTIONS §4), so each writer has a local twin, named in the table. Always `deferred` or `info`, never `pass`/`fail`;
`_atlas_verify_rows`/`verify_table`/`gate` read any id, so they appear in the gate tables (recorded-only ids) and in the
workbook's evidence column, never as pass/fail rows of their own.

| Id | What it records | Recorded by (file → function) | Shown by |
|---|---|---|---|
| `T-<tool>` | A Section 15.1 tool whose Phase 2 step 6 install is soft: the tool did not install (download denied, archive layout changed, licence not accepted) and the phase continued instead of stopping; the message carries the reason and the command to retry | `phase2/06-tools.sh` → `_tools_record_deferred` (called by `_tools_soft` around each `_tools_<tool>` install function), one row per tool | Phase 2 gate table (`phase2/10-gate.sh`), `status` |
| `P4-wheels` | The Phase 4 wheel-index pre-flight: the exact wheel file names on the AMD index that satisfy `docker/rocm-base/Dockerfile`'s pins (rocm, torch, torchvision for cp312; torchaudio and amd-torch-device-gfx1151 present), each HEADed through the proxy; on a miss the exact missing file name and the index URL (the step then stops before `docker build`); on an index, project page or wheel host the proxy will not reach (or a channel whose every project page is 404) the URL, the host and the proxy error. The channel root is a reachability probe only (pip never fetches it) | `phase4-engines.sh` → `_p4_record_info` (the twin; called from `_p4_wheel_preflight`, which `step_01` runs); `phase4/wheel_preflight.py`; full result root-held in `/var/lib/atlas/day1/phase4/wheels-preflight.json` | Phase 4 gate table (`gate phase4 V11 -- V8 V9 P4-wheels`), step 05's `verify_table`, `status` |
`tools/fill-workbook.py` turns the records into sheet 4 of `docs/ATLAS_BUILD_BASELINE.xlsx` (halves V3a/V3b and
V14a/V14b combine; V10a is shown as the Phase 2 evidence of the V10 row; V4 is judged per engine).
