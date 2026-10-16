# Day 1 scripts — conventions

Binding for every file under `scripts/day1/`. The scripts implement Section 17 of
`docs/ATLAS_FRAMEWORK_REVIEW.md` (v0.3, the closed baseline) and prove Section 21.
Where this file and the document disagree, the document wins and this file is wrong.

## 1. Layout (repository)

```
scripts/day1/
  atlas-day1.sh                entry point: sudo ./atlas-day1.sh <phase1|phase2|phase3|phase4|status|report>
  lib/common.sh                the shared library; every script sources it first
  config/atlas.env.example     non-secret settings; installed to /etc/atlas/atlas.env on first run
  config/engines.json          the v0.4.0 Day 1 engines: 4 LLM seats + 3 resident companions (Sections 5.1, 5.3, 4.3), and
                               the record-only `phased` list (nothing reads it; D-26 (c))
  config/phase4-engines.json   the Phase 4 engines with tier, repos, build script, footprint (Section 15.2)
  config/allowlist.txt         outbound domain allowlist, one per line (Section 12.5)
  config/sentinel-feeds.json   the D6 feeds
  config/voice-casting.json    Section 14.3 shortlist
  config/task-forces.json      23 presets (Section 8.3): owner(s), domain cards, default tier
  config/personas/<name>.md    persona core prompts: ren, arthur, gideon, silas, valerie, helena, eleanor, alaric, minerva, victor
  config/domains/NN-slug.md    36 full seven-field profiles (Section 8.2, 8.6)
  config/domains/cards/NN-slug.md   36 compact cards, 300-500 tokens each (Section 8.4)
  phase1-platform.sh           Phase 1 driver; sources phase1/NN-*.sh in order; reboots after step 04 and resumes
  phase1/NN-<step>.sh          one file per Phase 1 step (01-preflight, 02-luks, 03-mounts, 04-system, 05-postboot, 05b-desktop, 06-docker, 07-remote, 08-gate)
  phase2-services.sh           Phase 2 driver; sources phase2/NN-*.sh in order
  phase2/NN-<step>.sh          one file per Phase 2 step (01-llama, 02-orchestrator, 03-openwebui, 04-memory, 05-voice, 06-tools,
                               06b-cloudflare-token, 06c-google-oauth, 06d-sandbox, 07-restic, 08-sentinel, 09-windows-share,
                               09b-vault, 10-gate). 06d (AEGIS sandbox image, Section 16.4; V17) and 09b (gocryptfs vault,
                               Section 11; V18) are gap-fills: Section 17 names neither step, the gate needs both
  phase3-models.sh             Phase 3 driver (detached under systemd); phase3/loadtest.py does the measuring;
                               phase3/parity-tasks.txt is the V25 task set (24 tasks, written for this project)
  phase4-engines.sh            Phase 4 driver (detached); phase4/engines/<name>.sh builds and tests one engine
  verify/vNN-<name>.sh         standalone checks for Section 21; see §5
  systemd/                     unit files installed verbatim to /etc/systemd/system (templated by render_template)
  docker/core/compose.yml      redis, chromadb, open-webui (Phase 2 steps 2-4)
  docker/core/compose.voice.yml kokoro, speaches (whisper), docling (Phase 2 steps 4-5)
  docker/ntfy/compose.yml      ntfy (Phase 1 step 7)
  docker/wg-easy/compose.yml   WG-Easy (Phase 1 step 7)
  docker/rocm-base/Dockerfile  the one ROCm/PyTorch base image for gfx1151 (Phase 4)
  docker/buildfarm/Dockerfile  Android + MinGW cross-build container (Phase 2 step 6)
  docker/sandbox/Dockerfile    the AEGIS sandbox image (Section 16.4)
  orchestrator/                Python package `atlas` (pyproject.toml, src/atlas/..., tests/)
  tools/fill-workbook.py       reads verify.jsonl and fills sheet 4 of docs/ATLAS_BUILD_BASELINE.xlsx
  tools/build-baseline-workbook.py  rebuilds docs/ATLAS_BUILD_BASELINE.xlsx from docs/ATLAS_FRAMEWORK_REVIEW.md (D-27;
                               run in the repository, never on the node)
  README.md                    how to run, in order, and what to have ready
```

## 2. Layout (on the node)

| Path | Purpose | Owner:mode |
|---|---|---|
| `/etc/atlas/atlas.env` | non-secret settings (§3) | root:atlas 640 |
| `/etc/atlas/secrets/` | every secret, one file each, never under `/srv/atlas`, never in restic's include set | root:atlas 710 (traverse only; every file inside is 600, owned by its one reader) |
| `/etc/atlas/secrets/cloudflare.env` | `CF_API_TOKEN=...` | atlas-ddns:atlas-ddns 600 |
| `/etc/atlas/secrets/hf-token.env` | `HF_TOKEN=...` (gated models: PyAnnote, FLUX.1-dev, Stable Audio Open) | atlas:atlas 600 |
| `/etc/atlas/secrets/ntfy.env` | `NTFY_TOKEN=...` | atlas:atlas 600 |
| `/etc/atlas/secrets/smb.cred` | Windows share credentials | root:root 600 |
| `/etc/atlas/secrets/google/` | OAuth client JSON and per-account tokens | atlas:atlas 700 |
| `/etc/atlas/secrets/restic.pass` | restic repository passphrase, printed once for off-node storage (D3) | root:root 600 |
| `/var/lib/atlas/day1/` | state: `done/<phase>.<step>` markers, `verify.jsonl`, `logs/` | root:root 755 |
| `/opt/atlas/day1/` | a copy of `scripts/day1/` taken by `atlas-day1.sh` on every run | root:root 755 |
| `/opt/atlas/llama.cpp/` | llama.cpp source and build at the pin b11474; binaries symlinked into `/usr/local/bin` | root |
| `/opt/atlas/llama-parity/{src,dist}` | the old pin v0.4.1, a git worktree of the same clone, built as `llama-perplexity` only for the Phase 3 parity test (V25, S-02); never symlinked, never a unit | root |
| `/opt/atlas/orchestrator/` + `/opt/atlas/venv/` | the `atlas` package and its venv | atlas |
| `/srv/atlas/{models,engines,data,workspace,sandbox,vault,staging}` | 8 TB data volume (Section 3.5). `vault/cipher` is the gocryptfs container (backed up as ciphertext), `vault/open` the plaintext view (never backed up); `staging/vault-test-cipher` (atlas:atlas 700) is the gate's throw-away test vault, excluded with all of staging | atlas:atlas |
| `/srv/atlas/staging/inbox/` | where the Principal drops files: `voice-references/alaric.*`, `voice-references/gideon.*` (WAV, MP3 or M4A; Phase 2 step 5 normalises them to 16 kHz mono WAV under `staging/voice-references-normalised/`) and `google-oauth-client.json` | principal:atlas 2770 |
| `/srv/cold`, `/srv/backups` | 4 TB OS drive (Section 3.5) | atlas / root |

Service accounts: `atlas` (system user; groups `render`, `video`, `docker`), `atlas-ddns` (system user, nothing
else). The Principal's own login user is `$PRINCIPAL_USER` from the env (created by the Ubuntu installer).

## 3. Settings: `/etc/atlas/atlas.env`

Sourced by `load_env`. Keys (all with sane defaults or auto-detection in `config/atlas.env.example`):
`PRINCIPAL_USER`, `TZ`, `LAN_IFACE`, `LAN_CIDR`, `WG_IFACE=wg0`, `WG_CIDR=10.8.0.0/24`, `WG_PORT=51820`,
`DOMAIN=sovereign-node.link`, `VPN_HOST=vpn.sovereign-node.link`, `DATA_DISK` (by-id path of the 8 TB drive,
auto-detected as the largest unmounted NVMe), `CLOUDFLARE_TXT` (default `/home/$PRINCIPAL_USER/CLOUDFLARE.txt`),
`GOOGLE_ACCOUNTS` (two emails, space-separated, each tagged `corporate` or `estate` as `email:tag`),
`WINDOWS_SHARE` (`//host/share`), `NTFY_TOPIC=atlas`, `OPENWEBUI_PORT=3000`, `ORCH_PORT=8800`,
`LLAMA_PORT_BASE=8100` (engine N listens on 8100+N), `HF_ENDPOINT` (unset), `DOWNLOAD_MBPS=100`.
`ATLAS_SSH_PASSWORD_AUTH` (blank; pre-flight writes `keep` itself when the Principal gave no SSH public key at its
prompt, so password SSH stays on until the key is added, to-do `ssh-key`), `RDP_ALLOW_FROM` (blank = the whole LAN
subnet may reach xrdp; IPv4 addresses or CIDRs inside `LAN_CIDR` narrow the LAN side of port 3389 to the Principal's
PC (canonical dotted quads, decimal prefixes without leading zeros, never the WireGuard bridge network), checked by
`load_env` (`atlas_rdp_sources_check`) and again by step 4 before it resets ufw;
step 5b records the optional to-do `rdp-restrict` with the LAN address the PC connected from, closed once the firewall no
longer admits the whole subnet on 3389), `BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE`
(blank; `yes` records that the Principal read and accepts the Android SDK terms, Section 16.3 item 2; Phase 2 step 6
defers the cross-build container otherwise).
**Principal-provided keys are optional (policy v0.3.3).** `GOOGLE_ACCOUNTS`, `FAMILY_NAMES` and
`BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE`, when blank, are each asked ONCE on the terminal by `load_env` in plain words
with an example value (Enter skips); an answer is written to `atlas.env`, a skip records a to-do (`todo_add`) and the
step that needs the key defers itself (Section 21 `deferred`, never `fail`). `WINDOWS_SHARE` is never asked: the
Windows share is a live-ATLAS to-do (Section 12.4) and Phase 2 step 9 is skipped while it is blank. Secure Boot has
no acknowledgement key: D15 closes it as enabled by the Principal in the BIOS (README §1 precondition); while it is
off, pre-flight warns, V2 notes it, and to-do `secure-boot` is open.

Secrets never go in this file.

## 4. `lib/common.sh` — the API every script uses

Every script starts with:
```bash
#!/usr/bin/env bash
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"   # (or ../lib for step files)
```
`common.sh` sets `set -Eeuo pipefail`, an ERR trap that logs the failing line, and exports the functions below.
All node paths are overridable for tests: `ATLAS_ETC` (default `/etc/atlas`), `ATLAS_STATE` (`/var/lib/atlas/day1`),
`ATLAS_OPT` (`/opt/atlas`), `ATLAS_SRV` (`/srv/atlas`), so `lib/common_test.sh` can exercise every function in a temp dir.
Step files never define `main`; each defines `step_<id>()` and the driver calls `run_step`.

| Function | Contract |
|---|---|
| `log MSG` / `warn MSG` / `die MSG` | timestamped to stdout and to the phase log; `die` exits 1 |
| `require_root` | exits unless EUID 0 |
| `load_env` | installs `config/atlas.env.example` to `/etc/atlas/atlas.env` if missing (auto-detecting what it can), then sources it; dies only on blank auto-detected keys (`LAN_IFACE`, `LAN_CIDR`, `DATA_DISK`); each blank Principal key is asked once, else recorded as a to-do (§3) |
| `ask VAR PROMPT [secret]` | one plain question on `/dev/tty`, 300 s; no terminal or no answer leaves VAR empty and the caller records a to-do instead of stopping. `ATLAS_ASK_ANSWER` is the scripted answer (tests) |
| `todo_add ID TITLE [DETAIL]` / `todo_done ID` / `todo_list` | the live ATLAS to-do list, `/var/lib/atlas/day1/todo.jsonl` (`ATLAS_TODO_FILE`), one JSON line per event; `todo_list` prints the open items and `atlas-day1.sh status` shows it. IDs: `input-google-accounts`, `input-family-names`, `input-android-sdk-licence`, `input-windows-share`, `input-smb-cred`, `input-google-oauth-client`, `input-hf-token`, `secure-boot`, `os-volume-encryption`, `ssh-key`, `cloudflare-token`, `cloudflare-zone-id`, `vpn-mobile-test`, `rdp-test`, `rdp-restrict`, `time-sync`, `kernel-line` (Phase 1 steps 4 and 5, doc S45: the node is not on the GA 7.0 kernel line; raised by step 4, and by step 5 again with each boot's state while the line is not GA; closed by step 4 when it finds or makes the line GA, or by step 5 on GA), `watchdog` (Phase 1 step 4, doc S50: no hardware watchdog was found, so softdog stands in, which restarts a frozen or crashed systemd but not reliably a hard lockup of the whole kernel; raised by every step 4 run that falls back to softdog, with that run's findings and the re-run command; closed by step 4 once it finds a hardware watchdog; step 5 and V24 never change it), `w-drill` (Phase 1 step 5, doc 3.7 W-DRILL: the crash and watchdog tests the live ATLAS runs once Day 1 is complete, with the Principal present and agreeing to each; raised by step 5 unless already open, so a rerun of step 5 raises it again after the live ATLAS closed it; only the live ATLAS closes it, once the results are recorded), `kv-estimates` (Phase 3 step 4, doc S52: each engine's context memory measured from its own load log beside the Arbiter's estimate for the same profile, and whether to correct the estimates in `atlas/arbiter.py`; raised by every run of step 4 with the latest figures, or with the reason they are UNKNOWN; only the Principal closes it), `cache-ram` (Phase 3 step 4, doc S52: each chat llama-server's host RAM and what its log says of the prompt cache and the context checkpoints, which sit outside the Arbiter's budget, with suggested explicit `--cache-ram` and `--ctx-checkpoints` values, INFERRED; raised and closed like `kv-estimates`), `decode-floors` (Phase 3 step 2, v0.4.0, D-33 (c), D-47: every LLM seat whose `decode_floor_tok_s` is null, with its measured decode beside its projection; re-raised by every run of step 2 while any floor is null; only the Principal closes it, by setting the floors), `kld-run` (Phase 3 step 2c, v0.4.0, S-03, T-03: each seat's corpus perplexity and task score on the pin with its reference status; the reference-based KLD needs files Day 1 does not pull; with it the Principal fixes each seat's reasoning setting, D-30 (b); raised by every run of step 2c, only the Principal closes it), `context-targets` (D-16), `trial-task-set` (D-46), `seat-test-sets` (D-49) and `seat-profiles` (D-48, S-30, T-04: DeepSeek's one 131,072-token slot, GLM's f16 8 × 32k, the 24,000-token long-document trigger) (Phase 3 step 2, v0.4.0: the inputs an approved row leaves to the Principal, each with its built default in the detail; re-raised by every run of step 2, only the Principal closes them), `tool-<name>` |
| `run_step PHASE STEP FUNC` | idempotency: if `/var/lib/atlas/day1/done/PHASE.STEP` exists, logs "skip" and returns 0; else runs `FUNC`, and on success creates the marker. STEP is the two-digit-plus-letter id from Section 17 (`01`, `05b`, `06c`) |
| `record_v ID RESULT MSG` | appends `{"ts","phase","id","result","msg"}` to `verify.jsonl`. RESULT ∈ `pass fail deferred info`. IDs are `V1`..`V25` (V25 new in v0.4.0, S-02), with halves `V3a`/`V3b` and `V14a`/`V14b`, plus `V10a` (the Phase 2 resident-router half of V10; V10 itself is written only by the Phase 3 load test). Two recorded-only id families are also accepted and never gate: `T-<tool>` (Phase 2 step 6 soft installs, result deferred) and `P4-wheels` (the Phase 4 wheel-index pre-flight, result info) |
| `run_verify ID SCRIPT [ARGS]` | runs `verify/SCRIPT`, maps exit 0/1/2/3 → pass/fail/deferred/info, records its one-line stdout as MSG |
| `gate PHASE REQUIRED_IDS... [-- OPTIONAL_IDS...]` | prints the phase table (latest result per id) and returns 1 if any REQUIRED id is `fail` or missing; `deferred` never blocks; OPTIONAL ids are printed only |
| `apt_install PKG...` | non-interactive, idempotent, retries 3× |
| `retry N CMD...` | exponential backoff 2s 4s 8s … |
| `ensure_dir PATH OWNER MODE` / `ensure_line FILE LINE` / `ensure_kv FILE KEY VALUE` / `ensure_kv_default FILE KEY VALUE` | idempotent file edits; `ensure_kv` rewrites the key's value on every run, `ensure_kv_default` writes `KEY=VALUE` only when no such key exists (a blank value counts as present), so a value the Principal set by hand survives a re-run of the step: `PRUNE_WINDOW_HOURS` in step 02 (S53) |
| `render_template SRC DST [VAR...]` | `envsubst` restricted to the named variables, then install with mode |
| `wait_http URL TIMEOUT_S` | polls until HTTP 200 |
| `notify MSG` | ntfy push if configured; never fails the caller |
| `hf_download REPO FILE DEST SHA256` | resumable download via the proxy, verifies sha256, skips when the file already matches |
| `gpu_gtt_used_mb` / `gpu_gtt_total_mb` | from `/sys/class/drm/card*/device/mem_info_gtt_*` |
| `svc_user_run CMD...` | run as `atlas` |
| `detached_phase NAME SCRIPT` | starts `SCRIPT --run` as a transient systemd unit `atlas-day1-NAME` (journal-logged, survives SSH loss) and prints how to follow it |

Every phase script accepts `--dry-run` (print steps, run nothing), `--force STEP` (clear one marker; the bare ids are
exported as `ATLAS_FORCED_STEPS`, space-separated, for step files such as `phase2/09b-vault.sh`), and `--status`.
Phase 3 and 4 also accept `--run` (the in-unit entry); without it they detach themselves, so `--force STEP` detaches
like the plain command and only `--foreground` (the entry point adds `--run`) keeps them in the terminal.

## 5. Verification scripts (`verify/`)

One per Section 21 item where the check is standalone: `v01-network.sh v02-tpm.sh v03a-gtt.sh v03b-llama-devices.sh
v05-wireguard.sh v06-pyannote.sh v07-voice-listen.sh v10a-router-resident.sh v11-rocm-selftest.sh v12-openwebui-offline.sh
v13-restic.sh v14a-arbiter-stubs.sh v15-approval-gate.sh v16-router-hard-rule.sh v17-sandbox.sh v18-vault.sh v19-xrdp.sh
v20-google.sh v23-cloudflare-token.sh v24-watchdog.sh`. V14a, V15 and V16 run the orchestrator's unit tests through those three scripts.
V4, V8, V9, V10, V14b, V21, V22, V25 are produced inside their phase (load tests, engine builds) and recorded with
`record_v` directly (Phase 3 through loadtest.py's RECORD lines, Phase 4 by the driver). Three loadtest.py subcommands also print `TODO<TAB>ID<TAB>TITLE<TAB>DETAIL` lines, which the driver raises with `todo_add`: `summarize` (Phase 3 step 2, `decode-floors`, `context-targets`, `trial-task-set`, `seat-test-sets` and `seat-profiles`, v0.4.0), `quality` (step 2c, `kld-run`, v0.4.0) and `memory` (step 4, `kv-estimates` and `cache-ram`, doc S52); they never reach `verify.jsonl`.
Contract: exit **0 pass, 1 fail, 2 deferred, 3 info**; print exactly one line of evidence on stdout;
never prompt; never take longer than 10 minutes; safe to re-run.

## 6. Gates, exactly as Section 17 states them

| Phase | Required (red row blocks the next phase) | Recorded, not blocking |
|---|---|---|
| 1 | V2, V3a, V5, V19, V24 | V1 (info). V2 is `deferred` (to-do `os-volume-encryption`) when the only gap is an unencrypted OS volume. V5/V19 are required-deferrable: a wait for the Principal's phone/PC that times out is `deferred` + to-do (`vpn-mobile-test`, `rdp-test`), a real failure is `fail`. V24 (doc S50) is `deferred` (to-do `watchdog`, raised by step 4) when step 4 found no hardware watchdog and systemd has armed the softdog fallback exactly as configured; no watchdog device at all, or one not armed by systemd with step 4's timeouts, is `fail` (step 5 has then already stopped) |
| 2 | V3b, V6, V12, V13, V14a, V15, V16, V17, V18, V20, V23, and every service healthy | V7 (deferred if the reference recordings are absent), V10a (resident router; also fatal to step 4), V18 is `deferred` until the real vault is initialised (`ATLAS_VAULT_INIT=1`). V6 (no HF token or licence not accepted), V20 (no Google accounts or no OAuth client file) and V23 (no `CLOUDFLARE.txt`) are `deferred` + to-do while the Principal's input is missing |
| 3 | V4 per engine, V10, V14b, V21, V25 (v0.4.0: V21 is single residency, S-24; V25 the output parity of the re-pin, S-02) | V22: one row per engine newer than v0.4.1's b10964 (`glm-5.3-flash` on Day 1) and one for the Apex, each failure deferred, never blocking (R19 generalised, S-26) |
| 4 | V11 | V8, V9 (deferred on failure), per-engine pass/fail/deferred table |

## 7. Rules for the scripts

1. **Nothing cloud.** No model inference off-node, no hosted AI, no telemetry, no analytics beacons. Every outbound
   request goes through the allowlist proxy; the allowlist is `config/allowlist.txt` and nothing else. The only
   packages fetched before the proxy exists (from the Ubuntu archive, before the firewall closes) are rsync (the entry
   point), systemd's TPM2 libraries (Phase 1 step 1: libtss2-rc0t64, which the server image lacks, plus libtss2-esys,
   -mu and -tcti-device with their libtss2 dependencies when a minimized install lacks those too) and squid,
   dnsmasq, jq and gettext-base (step 4, to build the proxy itself).
2. **Secrets** live only under `/etc/atlas/secrets/`, mode 600, owned by the one service that reads them. Never
   echoed to logs, never in `atlas.env`, never in git, never inside `/srv/atlas`, never in restic's include set.
   The one declared exception is the transient tmpfs passfile `/run/atlas-vault/pass` (root:root 600, written by the
   root helper for one gocryptfs mount and shredded the moment the mount is up; the helper refuses a non-tmpfs `/run`).
   The Cloudflare relocation (Phase 2 step 6b) deletes `CLOUDFLARE.txt` with `shred -u` only after the new
   file is written and a read-back test of the API succeeds (V23).
3. **Idempotent and resumable.** Every step is wrapped in `run_step`; re-running a phase skips completed steps.
   Downloads resume. Builds skip when the artefact exists. Phases 3 and 4 are resumable at the file level.
4. **Fail loudly, never silently.** A step that fails stops the phase with the failing line logged. A verification
   that fails is recorded as `fail`, never omitted. Yellow engines (Section 15.2) record `deferred` and continue.
   **A missing input from the Principal is never a failure** (policy v0.3.3): a token, recording, key, licence,
   account or test that the Principal has not provided yet is asked for once, in plain words, then recorded as
   `deferred` with a to-do, and the phase goes on. Only broken machinery stops a phase.
5. **Nothing before its dependency.** ROCm is never installed on the host; `rocminfo` runs only inside the Phase 4
   container. `llama-cli --list-devices` runs only after Phase 2 step 1. Every step's prerequisites are the steps
   before it in Section 17, nothing later.
6. **Interactive prompts are explicit, few and all skippable** (Enter, or no terminal, defers and records a to-do):
   the recovery passphrase display in Phase 1 step 2 (the only one that waits for a confirmation, because the
   passphrase is shown once), the SSH public key in step 1, the Principal keys in `load_env` (§3), the HF token at
   Phase 2 start, the Google OAuth links in step 6c (one per account, each waits up to 30 minutes), the Cloudflare
   zone in step 6b. Nothing waits longer than its stated time; everything else runs unattended.
7. **Approval gate, never-delegate, disclosure** are code paths in the orchestrator, with unit tests (V14a, V15, V16).
8. **Style:** bash with `shellcheck` clean (no disables without a comment saying why); Python 3.12+ with type hints,
   `ruff`-clean, tests under `orchestrator/tests` runnable with `pytest` and no live services (stubs for
   llama-server, Redis, Docker). Comments explain *why*, citing the section (`# Section 4.2 rule 5`).
9. **Versions are pinned** where the research findings give a pin (image tags, wheel index, llama.cpp tag/commit,
   pip packages). Unpinned only where the finding says no stable pin exists, and then the README says so.
10. **The Principal's time is the scarcest resource.** The scripts print what they are doing, what they need,
    and, at the end of every phase, the gate table and the exact next command.

## 8. Names that must agree across every file

Engine keys (used in `config/engines.json`, `llama-server@<key>.service`, persona front-matter, router routes,
the Arbiter ledger, phase 3 tables), the v0.4.0 Day 1 set in `engines.json` order (approved by the Principal on
2026-10-16; `orchestrator/src/atlas/config.py` `ENGINE_KEYS`): LLM seats `gpt-oss-120b` (fast utility and fallback
floor, F16-trunk GGUF), `gpt-oss-120b-abliterated` (the v0.3.5 entry, unchanged), `glm-5.3-flash` (Ren, Gideon,
Silas, Helena, Minerva, Victor, Alaric, Eleanor's persona work, the vision engine through its glm5v mmproj, the
long-document route), `deepseek-v4-flash` (DeepSeek-V4-Flash-0731 `UD-Q8_K_XL`: Arthur and the Apex); resident
companions: `router-qwen3.5-4b` (classification only, P-10), `embed-qwen3-4b`, `rerank-qwen3-4b`; external (no unit,
port 0): `chatterbox`. Retired in v0.4.0 and absent from `engines[]` and from every persona, route and preset (one that
still names one is refused as an unknown key): `qwen3.5-122b`, `qwen2.5-vl-72b`, `meditron-70b`, `embed-bge-m3`,
`rerank-bge-v2-m3`; `nemotron-3-super` leaves `engines[]` and is recorded as a phased `planned_key`. The phased engines are recorded in `engines.json` `phased`
under `planned_key` only (`qwen3.8-flash-next`, `nemotron-3-super`, `intern-s1`, `ling-3.0-flash-fin`, `baichuan-m3-235b`, `qwen3-coder-next`, `embeddinggemma-2`);
no persona, route or preset names one until it is appended to `engines[]` after index 7 at its pull. Router routes:
`ren`, `arthur`, `arthur-long` (v0.3.5's `arthur-qwen`, now refused), `ren-abliterated`, `arthur-abliterated`,
`deep-think:deep`. KV classes: `f16` (glm-5.3-flash, deepseek-v4-flash; D-29 (a); DeepSeek's ladder is f16 then
q8_0, never q4_0), `q8_0` (gpt-oss-120b, router-qwen3.5-4b, embed-qwen3-4b, rerank-qwen3-4b), `q4_0`
(gpt-oss-120b-abliterated, unchanged). Arbiter classes: `core` (gpt-oss-120b, its abliterated twin, glm-5.3-flash),
`apex` (deepseek-v4-flash, exclusive), `resident` (the three companions, never counted against the engine budget:
the router is in the measured GPU resident set, the CPU pair in the host resident set), `phase4`, `external`
(chatterbox); `vision` and `crosscheck` stay valid classes with no Day 1 entry (the vision engine is glm-5.3-flash,
`mode: vision`, class core). Placement: `gpu` (default) or `cpu`, which only a `resident` entry may take (the Qwen3
pair, rendered `--device none`; D-14 (e)). LLM seats (mode chat or vision, never resident, external or phase4): one
resident at a time, `ATLAS_ARBITER_MAX_RESIDENT` in `orchestrator.env`, default 1, range 1..2 (D-32 (a)).

Persona keys: `ren`, `arthur`, `gideon`, `silas`, `valerie`, `helena`, `eleanor`, `alaric`, `minerva`, `victor`.
Hemispheres: `corporate`, `estate`. Tiers: `routine`, `standard`, `sensitive`. Task-force codes as Section 8.3.

Domain cards (`config/domains/cards/NN-slug.md`): the H1 carries the Section 8.2 name in full, including any
`(incl. ...)` subspecialty parenthetical, followed by a comma-separated `(Hemisphere, Owner, Tier)` triple. Because
the triple is comma-separated, the owner string drops 8.2's inner comma (`Eleanor with Silas` for 8.2's
`Eleanor, with Silas`) and uses an em dash for a tag (`Arthur — tagged to 14` for `Arthur, tagged to 14`). This is
deliberate; the full profile's `Owner:` line is the verbatim 8.2 form and is the one the checker should match.
Memory collections (Section 10.1): `corporate`, `estate`, `scars`, `documents_corporate`, `documents_estate`, `sentinel`.

Ports (host): Open WebUI `$OPENWEBUI_PORT` (3000), orchestrator `$ORCH_PORT` (8800), llama-server engines
`$LLAMA_PORT_BASE + index` in engines.json order, index 1..20 (8101..8120, S-33; the Day 1 set uses 8101..8107:
gpt-oss-120b 8101, gpt-oss-120b-abliterated 8102, glm-5.3-flash 8103, deepseek-v4-flash 8104, router-qwen3.5-4b
8105, embed-qwen3-4b 8106, rerank-qwen3-4b 8107; a phased engine is appended, so no Day 1 port moves; v0.3.5's
router, embedder and reranker ports were 8108..8110), Kokoro 8880, speaches (Whisper) 8881,
ChromaDB 8000, Redis 6379 (loopback only), ntfy 8090, Cockpit 9090, xrdp 3389, WG-Easy admin 51821, squid 3128
(loopback only). Everything except WireGuard UDP 51820 binds to loopback, LAN and WireGuard addresses only.

Control path: the orchestrator runs as `atlas` and starts/stops engines with `sudo systemctl {start,stop,restart}
llama-server@<key>`; the sudoers fragment `/etc/sudoers.d/atlas-engines` allows exactly those commands, for the keys
of `config/engines.json`, and nothing else on the engine side (the CPU pair's MainPID is read with
`systemctl show -p MainPID --value llama-server@<key>`, which needs no sudo). The vault button needs a second, equally narrow root path: `/etc/sudoers.d/atlas-vault`
allows exactly `atlas-vault open|lock|status` (passphrase on stdin, never an argument). No other NOPASSWD grant exists;
`phase2/07-restic.sh` proves that no fragment lets `atlas` touch `atlas-aegis.service`.

Bind rule, as implemented: host daemons that cannot bind per address — sshd (22), cockpit-ws (9090), xrdp (3389) and
Open WebUI on the host network (`$OPENWEBUI_PORT`) — listen on 0.0.0.0 behind ufw's default-deny with LAN/WireGuard
allow rules; squid additionally listens on the docker0 gateway for the containers. Every other listener is loopback or
pinned to the LAN address, and the Phase 2 gate marks any further wildcard listener NOT HEALTHY. Sentinel and the 72-hour prune are systemd timers whose only job is to enqueue the Celery task
(Section 9.3 names a timer, Section 9.7 makes Celery the executor; this satisfies both). AEGIS nightly is the
same pattern. The prune stamp is touched after the enqueue, so a sweep that fails inside the worker is not retried before
the next >= 71 h slot; the task itself pushes the alert (S53).

Retention names (Sections 9.6 and 10.4, S53): `PRUNE_WINDOW_HOURS` (`orchestrator.env`; seeded once by
`phase2/02-orchestrator.sh` with `ensure_kv_default`, read by `atlas.tasks.prune.prune_sweep` with the fallback
`DEFAULT_WINDOW_HOURS` 72), `COLD_DIR` (steps 02 and 08; the sweep's archive root) and `OPENWEBUI_CHAT_RETENTION_DAYS`
(step 02; `atlas.tasks.retention`); the ntfy titles `ATLAS prune: sweep failed` (`FAILURE_TITLE`) and `ATLAS prune:
vault invariant breach`; the archive contract: `graph.json` members carry `doc_id`, `document`, `ts`, `hemisphere`,
`expires_at`, `meta`, collection members `id`, `document`, `metadata`, and `manifest.json` carries `graph` and
`graph_without_text`; the graph index column `text` (`atlas.memory.GraphIndexRow.text`). The chat-turn TTL (`atlas.api`
`CHAT_TURN_TTL_HOURS`, 90 days), the Sentinel TTL (`atlas.tasks.sentinel` `SENTINEL_KEEP_DAYS`, 366) and the ledger
windows (`atlas.ledger` `RETENTION_S`) are code constants fixed by D9, not env keys.
