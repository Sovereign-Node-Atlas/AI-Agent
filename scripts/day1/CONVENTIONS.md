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
  config/engines.json          the 7 GGUF engines + 3 resident small models (Sections 5.1, 5.3, 4.3)
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
  phase2/NN-<step>.sh          one file per Phase 2 step (01-llama, 02-orchestrator, 03-openwebui, ...)
  phase3-models.sh             Phase 3 driver (detached under systemd); phase3/loadtest.py does the measuring
  phase4-engines.sh            Phase 4 driver (detached); phase4/engines/<name>.sh builds and tests one engine
  verify/vNN-<name>.sh         standalone checks for Section 21; see §5
  systemd/                     unit files installed verbatim to /etc/systemd/system (templated by render_template)
  docker/core/compose.yml      redis, chromadb, open-webui, ntfy, kokoro, speaches (whisper), docling-serve
  docker/wg-easy/compose.yml   WG-Easy (Phase 1 step 7)
  docker/rocm-base/Dockerfile  the one ROCm/PyTorch base image for gfx1151 (Phase 4)
  docker/buildfarm/Dockerfile  Android + MinGW cross-build container (Phase 2 step 6)
  docker/sandbox/Dockerfile    the AEGIS sandbox image (Section 16.4)
  orchestrator/                Python package `atlas` (pyproject.toml, src/atlas/..., tests/)
  tools/fill-workbook.py       reads verify.jsonl and fills sheet 4 of docs/ATLAS_BUILD_BASELINE.xlsx
  README.md                    how to run, in order, and what to have ready
```

## 2. Layout (on the node)

| Path | Purpose | Owner:mode |
|---|---|---|
| `/etc/atlas/atlas.env` | non-secret settings (§3) | root:atlas 640 |
| `/etc/atlas/secrets/` | every secret, one file each, never under `/srv/atlas`, never in restic's include set | root:root 700 |
| `/etc/atlas/secrets/cloudflare.env` | `CF_API_TOKEN=...` | atlas-ddns:atlas-ddns 600 |
| `/etc/atlas/secrets/hf-token.env` | `HF_TOKEN=...` (gated models: PyAnnote, FLUX.1-dev, Stable Audio Open) | atlas:atlas 600 |
| `/etc/atlas/secrets/ntfy.env` | `NTFY_TOKEN=...` | atlas:atlas 600 |
| `/etc/atlas/secrets/smb.cred` | Windows share credentials | root:root 600 |
| `/etc/atlas/secrets/google/` | OAuth client JSON and per-account tokens | atlas:atlas 700 |
| `/etc/atlas/secrets/restic.pass` | restic repository passphrase, printed once for off-node storage (D3) | root:root 600 |
| `/var/lib/atlas/day1/` | state: `done/<phase>.<step>` markers, `verify.jsonl`, `logs/` | root:root 755 |
| `/opt/atlas/day1/` | a copy of `scripts/day1/` taken by `atlas-day1.sh` on every run | root:root 755 |
| `/opt/atlas/llama.cpp/` | llama.cpp source and build; binaries symlinked into `/usr/local/bin` | root |
| `/opt/atlas/orchestrator/` + `/opt/atlas/venv/` | the `atlas` package and its venv | atlas |
| `/srv/atlas/{models,engines,data,workspace,sandbox,vault,staging}` | 8 TB data volume (Section 3.5) | atlas:atlas |
| `/srv/atlas/staging/inbox/` | where the Principal drops files: `voice-references/alaric.wav`, `voice-references/gideon.wav`, `google-oauth-client.json` | principal:atlas 2770 |
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
| `load_env` | installs `config/atlas.env.example` to `/etc/atlas/atlas.env` if missing (auto-detecting what it can), then sources it; dies if a required key is empty |
| `run_step PHASE STEP FUNC` | idempotency: if `/var/lib/atlas/day1/done/PHASE.STEP` exists, logs "skip" and returns 0; else runs `FUNC`, and on success creates the marker. STEP is the two-digit-plus-letter id from Section 17 (`01`, `05b`, `06c`) |
| `record_v ID RESULT MSG` | appends `{"ts","phase","id","result","msg"}` to `verify.jsonl`. RESULT ∈ `pass fail deferred info`. IDs are `V1`..`V23`, with halves `V3a`/`V3b` and `V14a`/`V14b` |
| `run_verify ID SCRIPT [ARGS]` | runs `verify/SCRIPT`, maps exit 0/1/2/3 → pass/fail/deferred/info, records its one-line stdout as MSG |
| `gate PHASE REQUIRED_IDS... [-- OPTIONAL_IDS...]` | prints the phase table (latest result per id) and returns 1 if any REQUIRED id is `fail` or missing; `deferred` never blocks; OPTIONAL ids are printed only |
| `apt_install PKG...` | non-interactive, idempotent, retries 3× |
| `retry N CMD...` | exponential backoff 2s 4s 8s … |
| `ensure_dir PATH OWNER MODE` / `ensure_line FILE LINE` / `ensure_kv FILE KEY VALUE` | idempotent file edits |
| `render_template SRC DST [VAR...]` | `envsubst` restricted to the named variables, then install with mode |
| `wait_http URL TIMEOUT_S` | polls until HTTP 200 |
| `notify MSG` | ntfy push if configured; never fails the caller |
| `hf_download REPO FILE DEST SHA256` | resumable download via the proxy, verifies sha256, skips when the file already matches |
| `gpu_gtt_used_mb` / `gpu_gtt_total_mb` | from `/sys/class/drm/card*/device/mem_info_gtt_*` |
| `svc_user_run CMD...` | run as `atlas` |
| `detached_phase NAME SCRIPT` | starts `SCRIPT --run` as a transient systemd unit `atlas-day1-NAME` (journal-logged, survives SSH loss) and prints how to follow it |

Every phase script accepts `--dry-run` (print steps, run nothing), `--force STEP` (clear one marker), and
`--status`. Phase 3 and 4 also accept `--run` (the in-unit entry).

## 5. Verification scripts (`verify/`)

One per Section 21 item where the check is standalone: `v01-network.sh v02-tpm.sh v03a-gtt.sh v03b-llama-devices.sh
v05-wireguard.sh v06-pyannote.sh v07-voice-listen.sh v11-rocm-selftest.sh v12-openwebui-offline.sh
v13-restic.sh v17-sandbox.sh v18-vault.sh v19-xrdp.sh v20-google.sh v23-cloudflare-token.sh`.
V4, V8, V9, V10, V14, V15, V16, V21, V22 are produced inside their phase (load tests, engine builds,
orchestrator unit tests) and recorded with `record_v` directly.
Contract: exit **0 pass, 1 fail, 2 deferred, 3 info**; print exactly one line of evidence on stdout;
never prompt; never take longer than 10 minutes; safe to re-run.

## 6. Gates, exactly as Section 17 states them

| Phase | Required (red row blocks the next phase) | Recorded, not blocking |
|---|---|---|
| 1 | V2, V3a, V5, V19 | V1 (info) |
| 2 | V3b, V6, V12, V13, V14a, V15, V16, V17, V18, V20, V23, and every service healthy | V7 (deferred if the reference recordings are absent) |
| 3 | V4 per engine, V10, V14b, V21 | V22 (a DeepSeek failure defers it, R19) |
| 4 | V11 | V8, V9 (deferred on failure), per-engine pass/fail/deferred table |

## 7. Rules for the scripts

1. **Nothing cloud.** No model inference off-node, no hosted AI, no telemetry, no analytics beacons. Every outbound
   request goes through the allowlist proxy; the allowlist is `config/allowlist.txt` and nothing else.
2. **Secrets** live only under `/etc/atlas/secrets/`, mode 600, owned by the one service that reads them. Never
   echoed to logs, never in `atlas.env`, never in git, never inside `/srv/atlas`, never in restic's include set.
   The Cloudflare relocation (Phase 2 step 6b) deletes `CLOUDFLARE.txt` with `shred -u` only after the new
   file is written and a read-back test of the API succeeds (V23).
3. **Idempotent and resumable.** Every step is wrapped in `run_step`; re-running a phase skips completed steps.
   Downloads resume. Builds skip when the artefact exists. Phases 3 and 4 are resumable at the file level.
4. **Fail loudly, never silently.** A step that fails stops the phase with the failing line logged. A verification
   that fails is recorded as `fail`, never omitted. Yellow engines (Section 15.2) record `deferred` and continue.
5. **Nothing before its dependency.** ROCm is never installed on the host; `rocminfo` runs only inside the Phase 4
   container. `llama-cli --list-devices` runs only after Phase 2 step 1. Every step's prerequisites are the steps
   before it in Section 17, nothing later.
6. **Interactive pauses are explicit and few:** the recovery passphrase display in Phase 1 step 2 (waits for the
   Principal to confirm it is written down), the HF token prompt at Phase 2 start if the secret file is absent,
   and the two Google OAuth links in Phase 2 step 6c. Everything else runs unattended.
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
the Arbiter ledger, phase 3 tables): `gpt-oss-120b`, `gpt-oss-120b-abliterated`, `nemotron-3-super`,
`qwen3.5-122b`, `deepseek-v4-flash`, `qwen2.5-vl-72b`, `meditron-70b`; resident small models:
`router-qwen3.5-4b`, `embed-bge-m3`, `rerank-bge-v2-m3`. KV classes: `q8_0` (nemotron-3-super, qwen3.5-122b,
qwen2.5-vl-72b, meditron-70b), `q4_0` (gpt-oss-120b, gpt-oss-120b-abliterated, deepseek-v4-flash). Arbiter
classes: `core`, `apex` (deepseek-v4-flash, exclusive), `vision`, `crosscheck` (meditron), `resident` (small
models, never counted against the engine budget), `phase4`.

Persona keys: `ren`, `arthur`, `gideon`, `silas`, `valerie`, `helena`, `eleanor`, `alaric`, `minerva`, `victor`.
Hemispheres: `corporate`, `estate`. Tiers: `routine`, `standard`, `sensitive`. Task-force codes as Section 8.3.

Domain cards (`config/domains/cards/NN-slug.md`): the H1 carries the Section 8.2 name in full, including any
`(incl. ...)` subspecialty parenthetical, followed by a comma-separated `(Hemisphere, Owner, Tier)` triple. Because
the triple is comma-separated, the owner string drops 8.2's inner comma (`Eleanor with Silas` for 8.2's
`Eleanor, with Silas`) and uses an em dash for a tag (`Arthur — tagged to 14` for `Arthur, tagged to 14`). This is
deliberate; the full profile's `Owner:` line is the verbatim 8.2 form and is the one the checker should match.
Memory collections (Section 10.1): `corporate`, `estate`, `scars`, `documents_corporate`, `documents_estate`, `sentinel`.

Ports (host): Open WebUI `$OPENWEBUI_PORT` (3000), orchestrator `$ORCH_PORT` (8800), llama-server engines
`$LLAMA_PORT_BASE + index` in engines.json order (8101..8110), Kokoro 8880, speaches (Whisper) 8881,
ChromaDB 8000, Redis 6379 (loopback only), ntfy 8090, Cockpit 9090, xrdp 3389, WG-Easy admin 51821, squid 3128
(loopback only). Everything except WireGuard UDP 51820 binds to loopback, LAN and WireGuard addresses only.

Control path: the orchestrator runs as `atlas` and starts/stops engines with `sudo systemctl {start,stop,restart}
llama-server@<key>`; the sudoers fragment `/etc/sudoers.d/atlas-engines` allows exactly those commands and
nothing else. Sentinel and the 72-hour prune are systemd timers whose only job is to enqueue the Celery task
(Section 9.3 names a timer, Section 9.7 makes Celery the executor; this satisfies both). AEGIS nightly is the
same pattern.
