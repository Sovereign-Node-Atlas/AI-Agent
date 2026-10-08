# A.T.L.A.S. Day 1 scripts

`scripts/day1/` implements Section 17 (Day 1 Execution Protocol) of `docs/ATLAS_FRAMEWORK_REVIEW.md` and proves
Section 21 (V1..V23) on the node. Four phases, one command each, every phase idempotent and resumable, every phase
ending with a printed gate table and the exact next command. `CONVENTIONS.md` is the contract every file follows;
`COVERAGE.md` maps every Section 17 step and every V item to its file. Nothing here calls a cloud AI, installs ROCm on
the host, or lets a request leave the node except through the local allowlist proxy (`config/allowlist.txt`).

## 1. Before you run anything

**Rule (policy v0.3.3, the Principal's instruction): a missing input never stops a phase.** Whatever the scripts need
from you (a token, a key, an account, a file, a test from your phone or PC) is asked for once, in plain words, with an
example and the option to press Enter and leave it for later. A skipped item goes on the to-do list at
`/var/lib/atlas/day1/todo.jsonl` (shown by `sudo ./atlas-day1.sh status`), the verification it belongs to is recorded
`deferred` (never `fail`), and the phase continues. When ATLAS is live it picks the list up with you. Only broken
machinery (a disk that does not mount, a service that does not start) stops a phase.

**Three things must be true before Phase 1** (the scripts cannot do them for you):

1. **Ubuntu Server 26.04.1 installed on the 4 TB NVMe with the "encrypted LVM" option.** In the installer's storage
   step choose *Use an entire disk*, pick the 4 TB drive, tick *Encrypt the LVM group with LUKS* and type a disk
   passphrase (you will type it at boot once; Phase 1 step 2 then lets the TPM unlock it). Leave the 8 TB NVMe
   untouched (do not select it). Create your login user when asked; that user is the Principal.
2. **Secure Boot enabled in the BIOS** (D15). The TPM only ties the disk unlock to this OS image when Secure Boot is
   on. If it is off, pre-flight warns, every V2 row notes it, to-do `secure-boot` stays open, and nothing stops.
   Also in the BIOS: UMA at its minimum, IOMMU on, fTPM on.
3. **The node's LAN address reserved in the router** (DHCP reservation for the node's MAC address, Wi-Fi or
   cable). Phase 1 uses whatever interface is up; the reservation keeps the address stable so the UDP 51820 forward
   you already made keeps pointing at the node. If the router's WAN address is in 100.64.0.0/10 you are behind
   CGNAT and the VPN test cannot pass from outside (R11).

Also useful for Phase 1: a monitor and keyboard on the node (the recovery key is shown on the console), or an
interactive SSH session with your password, never a detached one. No desktop (GUI) is needed on the install: Phase 1
step 5b installs XFCE, xrdp and Google Chrome, and you reach the desktop from the Windows PC with Remote Desktop.

**Everything else is optional on Day 1** and is asked for at the moment it is needed:

| Item | When it is asked | If you skip it |
|---|---|---|
| Your SSH public key (Windows: `type $env:USERPROFILE\.ssh\id_ed25519.pub` in PowerShell, after `ssh-keygen -t ed25519` once) | Phase 1 step 1, paste the one line | Password SSH stays on (`ATLAS_SSH_PASSWORD_AUTH=keep`), to-do `ssh-key` |
| Google accounts (`you@company.com:corporate you@gmail.com:estate`), family names | First `load_env` run | Gmail/Calendar/Drive (V20) and the family-name rule deferred |
| `yes` to Google's Android SDK terms (https://developer.android.com/studio/terms) | First `load_env` run | The Android/Windows build container is deferred |
| `/home/<you>/CLOUDFLARE.txt` with the `Zone:DNS:Edit` token for `sovereign-node.link` (40 characters, anywhere in the file; optional `Zone ID: <32 hex>` line) | Phase 1 step 7 looks for the file; the zone id is asked once if the token cannot list zones | Dynamic DNS and V23 deferred, to-do `cloudflare-token` |
| WireGuard test from your phone on mobile data (WireGuard and ntfy apps installed) | Phase 1 step 7 waits 10 minutes | V5 deferred, to-do `vpn-mobile-test` |
| One Remote Desktop connection from the Windows PC | Phase 1 step 5b waits 10 minutes | V19 deferred, to-do `rdp-test` |
| Your Windows PC's address, to limit Remote Desktop on the LAN to that PC (`RDP_ALLOW_FROM`) | Never asked: after a successful test from the LAN (a test over WireGuard records nothing), step 5b records the to-do `rdp-restrict` with the address your PC used and the two commands that apply it; it closes once the firewall no longer admits the whole subnet | The whole LAN subnet may reach Remote Desktop, as before |
| Hugging Face token (read scope) with the licences accepted for `pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`, `black-forest-labs/FLUX.1-dev`, `stabilityai/stable-audio-open-1.0` | Phase 2 start, hidden input | PyAnnote (V6) and the gated Phase 4 engines deferred, to-do `input-hf-token` |
| Google OAuth client JSON (Desktop-app type) at `/srv/atlas/staging/inbox/google-oauth-client.json`, then one sign-in per account | Phase 2 step 6c | V20 deferred, to-do `input-google-oauth-client` |
| Voice references `alaric.*` / `gideon.*` (WAV, MP3 or M4A) in `/srv/atlas/staging/inbox/voice-references/` | Phase 2 step 5 | V7 deferred |
| `cl100k_base.tiktoken` in `/srv/atlas/staging/inbox/` | Phase 2 step 4 | Warning with the download command |

**Not on Day 1 at all (the Principal's choice):** the Windows PC share. `WINDOWS_SHARE` stays blank, Phase 2 step 9 is
skipped, and to-do `input-windows-share` reminds the live ATLAS. To add it later: set `WINDOWS_SHARE="//host/share"` in
`/etc/atlas/atlas.env`, create `/etc/atlas/secrets/smb.cred` (root 600, `username=`/`password=`/`domain=` lines; step 2
prints the `read -rs` command so the password never touches a command line) and run `sudo ./atlas-day1.sh phase2
--force 09`.

**Settings file.** On the first run `load_env` installs `config/atlas.env.example` to `/etc/atlas/atlas.env`,
auto-detects what it can (`PRINCIPAL_USER`, `TZ`, `LAN_IFACE`, `LAN_CIDR`, `DATA_DISK` as the largest unmounted NVMe
by `/dev/disk/by-id`), uses defaults for the rest (ports, `DOMAIN`, `VPN_HOST`, `WG_*`, `NTFY_TOPIC`) and asks the
three optional keys above once. Answers are written to the file; you can also edit it by hand at any time:

```
GOOGLE_ACCOUNTS="you@company.com:corporate you@gmail.com:estate"
FAMILY_NAMES="Surname Givenname"
BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes   # after reading https://developer.android.com/studio/terms (Section 16.3 item 2)
WINDOWS_SHARE=                             # blank on Day 1 (above)
RDP_ALLOW_FROM=                            # optional: your PC's reserved address, e.g. "192.168.1.20" (blank = whole LAN)
```

The HF token is typed at the Phase 2 prompt (hidden input) and stored at `/etc/atlas/secrets/hf-token.env`; it never
appears on a command line. Secrets never go in `atlas.env`.

## 2. The commands, in order

```
git clone <repo> && cd <repo>/scripts/day1
sudo ./atlas-day1.sh phase1          # Phase 1 steps 1-4, then the node reboots
sudo ./atlas-day1.sh phase1          # after the reboot: steps 5, 5b, 6, 7, 8 (gate)
sudo ./atlas-day1.sh phase2          # steps 1-10; a few skippable prompts (below)
sudo ./atlas-day1.sh phase3          # detached; ~15 h of download + ~30 min of load tests
journalctl -fu atlas-day1-phase3     # follow it (or: tail -f /var/lib/atlas/day1/logs/phase3-<date>.log)
sudo ./atlas-day1.sh phase4          # detached; several hours, can run overnight
journalctl -fu atlas-day1-phase4
sudo ./atlas-day1.sh status          # done markers, the full verification table and the to-do list
sudo ./atlas-day1.sh report          # fills sheet "4 Verification" of a COPY of docs/ATLAS_BUILD_BASELINE.xlsx
```

On every run the tree is mirrored to `/opt/atlas/day1` and the entry point re-executes from there, so a `git pull`
during a running phase never changes the code that phase is executing. Phase N+1 refuses to start until
`/var/lib/atlas/day1/done/phaseN.gate` exists. Use plain `sudo` (Ubuntu 26.04 ships sudo-rs; `sudo -E` is refused):
when a variable must reach a step, write `sudo env VAR=value ./atlas-day1.sh ...`.

Options for every phase: `--dry-run` (print the steps, run nothing), `--force STEP` (clear one step's done marker,
then run), `--status` (markers and table for that phase). Phases 3 and 4 add `--foreground` (run in this terminal
instead of the transient unit); on those two phases `--force STEP` detaches like the plain command, and the exact
`journalctl -fu atlas-day1-phase3` follow line is printed every time a phase detaches (§5). Phase 1 only, called
directly as `/opt/atlas/day1/phase1-platform.sh`: `--no-reboot`
(stage step 4 and leave the reboot to you) and `--reload-allowlist [FILE]` (re-render the squid allowlist).

### What each phase does

- **Phase 1 — platform** (30-60 min with the reboot): pre-flight on a bare host; LUKS2 + TPM2 (`--tpm2-pcrs=7`) on the
  8 TB drive with the recovery key printed once; mounts under `/srv/atlas`, `/srv/cold`, `/srv/backups`, swap off,
  tmpfs `/tmp` verified; dist-upgrade, kernel parameters (`amdgpu.gttsize`, `ttm.pages_limit`,
  `amdgpu.lockup_timeout=10000,60000,10000,10000`), ufw, SSH hardening, Cockpit, the squid allowlist proxy; reboot;
  post-reboot V3a; XFCE + xrdp + Google Chrome (Google's apt repo, telemetry/sign-in off by policy, never a snap); Docker with GPU passthrough and the
  DOCKER-USER egress rules; WG-Easy, Cloudflare ddns, ntfy; gate.
- **Phase 2 — engines and services** (30-60 min, no large downloads): llama.cpp Vulkan build (`-DLLAMA_OPENSSL=ON`,
  `-DLLAMA_USE_PREBUILT_UI=OFF`); Redis, Celery, the orchestrator (`atlas` package: Arbiter, router, approval queue,
  ledger); Open WebUI hardened offline; ChromaDB, LightRAG, the three resident small models, Docling; Kokoro,
  Chatterbox, Whisper (speaches), PyAnnote; the Section 15.1 tools and the cross-build container; Cloudflare token
  relocation (V23); Google OAuth (V20); the AEGIS sandbox image; restic + AEGIS timers (V13); Sentinel and prune
  timers; the Windows share automount (skipped on Day 1); the gocryptfs vault; gate.
- **Day 1 close-out, before Phase 3 starts downloading:** copy the recovery material to the USB drive and store it
  away from the node, not beside it (Section 22, R16; D3): `/etc/atlas/secrets/restic.pass` (`sudo cat` it; shown once
  in Phase 2 step 7), the LUKS recovery key shown in Phase 1 step 2 (on-node copy under `/etc/atlas/secrets/` only when the OS volume is encrypted; otherwise your written copy is the only other one), and the
  vault passphrase you chose (plus the gocryptfs master key shown once) if you initialised the vault (§3). A fire or
  burglary that takes the node takes anything beside it; the USB copy is the one that survives.
- **Phase 3 — core LLM pull** (download-bound): the seven GGUF engines at their fixed quantisations, sha256 from the
  Hugging Face tree API into `MANIFEST.json`; per-engine load tests through the Arbiter (V4 KV proof, tok/s at 512 and
  8k, swap time, memory returned); the two-residency test (V14b, V21); gate. The Sentinel/prune timers, the GPU Celery
  worker and Open WebUI are quiesced during the tests and resumed afterwards: **do not use the assistant while Phase 3
  runs** (the ntfy push says when it is done).
- **Phase 4 — multimodal engines** (build-bound): the one ROCm base image (AMD's gfx1151 wheels, ROCm 10.0.0, torch
  2.13.0) and its self-test (V11); the green engines, then the yellow ones, then PointLLM (V8) and Clay/Prithvi (V9);
  registration of every passing engine with the Arbiter; the per-engine table and gate.

## 3. The prompts and waits

Every prompt below can be skipped (press Enter, or let the time run out): the item is then recorded `deferred`, goes
on the to-do list, and the phase continues (CONVENTIONS §7.4/§7.6). The one exception that waits for you is the
recovery key, because it is shown once.

| Phase/step | What it asks | How long |
|---|---|---|
| 1 / 1 | Your SSH public key, one pasted line (the step prints the PowerShell command that shows it). | 5 minutes |
| 1 / start | `GOOGLE_ACCOUNTS`, `FAMILY_NAMES`, the Android SDK terms (`load_env`, once each). | 5 minutes each |
| 1 / 2 | The LUKS recovery key is shown on the console once; type `WRITTEN DOWN` to continue (the screen is wiped). In the same pause the OS LUKS passphrase is asked once when the installer encrypted the OS volume and it has no TPM2 token yet, or when `--force 02` finds the token no longer unseals (Secure Boot switched on); and the data volume's recovery key is asked once when `--force 02` must re-seal it and no copy is on the node. Never stored. | Until you answer |
| 1 / 7 | Only when the Cloudflare token cannot list zones and no `Zone ID:` line or `CF_ZONE_ID` exists: the zone id, once. | 5 minutes |
| 2 / start | The Hugging Face token (hidden input), only when `/etc/atlas/secrets/hf-token.env` is absent. | 5 minutes |
| 2 / 6b | The zone id again, only if step 7 was skipped and the token still cannot list zones. | 5 minutes |
| 2 / 6c | One Google authorisation link per account, printed in a framed block. Open it in Google Chrome **on the node's own desktop over Remote Desktop** (the callback is `localhost:8765+i`), sign in as that account and click Allow. | 30 minutes per account |

Waits that are not prompts: Phase 1 step 5b waits up to 10 minutes for your Remote Desktop session from the Windows
PC (V19), and step 7 waits up to 10 minutes for a WireGuard handshake from your phone **on mobile data** (V5): the
step prints the WG-Easy admin URL (`http://127.0.0.1:51821/`, reachable only from the node: Chrome in the Remote
Desktop session, or `ssh -L`), the admin password file, and the ntfy login (`principal`, password in
`/etc/atlas/secrets/ntfy-principal.env`, topic `atlas`). A timeout records the V item as `deferred` with a to-do; the
step completes, the Phase 1 gate does not block on it, and `sudo ./atlas-day1.sh phase1 --force 05b` / `--force 07`
runs the wait again whenever you are ready.

Things shown once, without waiting: the restic passphrase (Phase 2 step 7, also kept root-only at
`/etc/atlas/secrets/restic.pass`); copy it and the LUKS recovery key to the USB drive stored away from the node (D3).

**The vault is opt-in.** A plain `phase2` never asks for a passphrase: step 9b leaves the real vault uninitialised,
proves the mechanics on a throw-away test vault, and V18 is recorded `deferred` (Phase 3 may start). From a console:

```
sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b
```

asks the passphrase twice, shows the gocryptfs master key once (write it down with the other two secrets), proves the
real vault and records V18.

## 4. Where things are

| What | Where |
|---|---|
| Phase logs | `/var/lib/atlas/day1/logs/<phase>-<YYYYMMDD>.log`; detached phases also in `journalctl -u atlas-day1-phase3` / `-phase4` |
| Verification records | `/var/lib/atlas/day1/verify.jsonl` (one JSON line per `record_v`; latest per id wins) |
| Done markers | `/var/lib/atlas/day1/done/<phase>.<step>` and `<phase>.gate` |
| Phase 3 results | `/var/lib/atlas/day1/phase3/results/<engine>.json`; model manifests `/srv/atlas/data/manifests/`, `/srv/atlas/models/<key>/MANIFEST.json` |
| Phase 4 results | `/var/lib/atlas/day1/phase4/<key>.json`; logs `/var/lib/atlas/day1/logs/phase4-<key>.log`; samples `/srv/atlas/workspace/phase4-samples/<key>/`; venv freezes `/srv/atlas/engines/manifests/<key>/freeze.txt` |
| Reports | `/var/lib/atlas/day1/reports/ATLAS_BUILD_BASELINE-<timestamp>.xlsx` (the repo copy is never touched) |
| Settings | `/etc/atlas/atlas.env` (non-secret); `/etc/atlas/{orchestrator,memory,voice,docker,network,vault,proxy}.env` written by the steps |
| Secrets | `/etc/atlas/secrets/` (root:atlas 710; every file 600, owned by its one reader): `hf-token.env`, `cloudflare.env`, `ntfy.env`, `smb.cred`, `restic.pass`, `redis.env`, `openwebui.env`, `wg-easy.env`, `google/` |
| The scripts the node runs | `/opt/atlas/day1/` (a mirror of this directory) |

## 5. Re-running, `--force`, and how the gates work

Every step is wrapped in `run_step`: when its done marker exists it is skipped. Re-running a phase therefore resumes
after the last completed step; a step that failed has no marker and runs again. `--force STEP` clears one marker
first (ids are the Section 17 numbers: `01`, `05b`, `06c`, `09b`; Phase 3 and 4 use `01`..`06`). Downloads resume
(`hf_download` skips files whose sha256 already matches), builds skip when the artefact exists, and Phases 3 and 4 are
resumable per file and per engine.

`--force` exports the ids it was given as `ATLAS_FORCED_STEPS` (space-separated), which step files may read: a forced
`09b` is how Phase 2 step 9b knows the Principal asked for the real vault (together with `ATLAS_VAULT_INIT=1`, §3).

**`--force` on Phases 3 and 4 detaches.** `sudo ./atlas-day1.sh phase3 --force 02` clears the marker and then starts
the transient unit exactly like the plain `phase3` command; the driver prints the follow line
(`journalctl -fu atlas-day1-phase3`, or `tail -f` on the phase log) and the spelling that keeps it in the terminal.
Only `--foreground` runs the phase in your terminal: `sudo ./atlas-day1.sh phase3 --foreground --force 02`. The
foreground run dies with a dropped SSH session, which is why detaching is the default for the two long phases
(Section 17: "detached under systemd"); `--dry-run` and `--status` always run in the terminal.

Each phase ends with `gate <phase> REQUIRED... -- RECORDED...`, which prints the latest record per id and writes the
gate marker only when no required id is `fail` or missing. `deferred` never blocks. `info` never blocks.

| Phase | Required (a red row blocks the next phase) | Recorded, not blocking |
|---|---|---|
| 1 | V2, V3a, V5, V19 | V1 (info) |
| 2 | V3b, V6, V12, V13, V14a, V15, V16, V17, V18, V20, V23, and every service healthy | V7, V10a (the Phase 2 half of V10); V18 `deferred` until the real vault is initialised |
| 3 | V4 per engine, V10, V14b, V21 | V22 (DeepSeek, R19) |
| 4 | V11 | V8, V9, the per-engine table |

The Phase 2 gate also checks every service by name (units active, containers healthy, authenticated Redis, HTTP health,
the bind rule, the sandbox image labels, secrets unreadable to group/world) and stops with the list before judging the
table. Fix what it names and run `phase2` again: only the gate step is left.

**"deferred"** means the proof could not be made yet for a reason Section 17 or 22 allows, and nothing depends on it
today: V7 without the reference recordings, V18 until the vault is initialised, V22 when DeepSeek V4 Flash fails to load
or no KV rung is coherent, V8/V9 and the yellow Phase 4 engines when their build or test fails. A deferred row is
written with the reason, shown in the table and the workbook, and never silently upgraded to pass.

## 6. Known limits

### Baseline amendments the scripts depended on (made in `docs/ATLAS_FRAMEWORK_REVIEW.md` v0.3.2)

- Section 17 Phase 2 now carries step `6d` (AEGIS sandbox image, Section 16.4, V17) and step `9b` (gocryptfs vault,
  Section 11, V18); `CONVENTIONS.md` §1 lists both.
- Section 23 S21 records the two sudoers fragments, `atlas-engines` and `atlas-vault`.
- The Secure Boot / PCR 7 consequence is decision **D15** (open, recommendation: enable Secure Boot) and risk R23; the
  Docker-group reach of the `atlas` user is R22.
- Section 11 and Appendix C name `vault/cipher` (backed up) and `vault/open` (never backed up).
- Section 21 declares `V10a`, the `T-<tool>` and `P4-wheels` recorded-only rows, and the Day 2 scope of V15's send
  channels; Section 23 S24–S29 list what the build added, deferred to Day 2, or admitted to the allowlist.

### Versions without a research pin (rule §7.9)

| Item | State |
|---|---|
| `python:3.12-slim` (sandbox base) | pinned by digest `sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016` resolved from Docker Hub on 2026-10-04 (label `org.atlas.sandbox.version=4`); a digest the registry stops serving makes step 6d die, never float |
| `ubuntu:26.04` (buildfarm base) | pinned by the multi-arch index digest of 2026-10-04 (`docker/buildfarm/Dockerfile`); apt pins inside are strict and the build fails when the archive moves on |
| `gocryptfs` | installed from the archive, must be 2.6.1 (the VERIFIED pin); override once re-verified with `ATLAS_GOCRYPTFS_VERSION_OK=1` |
| `pytest==9.1.1` | a resolved pin by the package writer (runtime dependency of `atlas`) |
| ROCm base image Python packages | no research pin beyond torch 2.13.0 / ROCm 10.0.0; the resolved wheel set is frozen into the image manifest (`/srv/atlas/engines/manifests/`) |
| Phase 4 engine pip lists (FLUX.1-dev, Stable Audio Open 0.0.20 deviation, PointLLM relaxations: `tokenizers>=0.14,<0.15`, `transformers==4.34.1`, `open3d` unpinned) | resolved at build time under the image's constraints file; frozen per engine into `freeze.txt`; hub revisions pinned on first pull (`P4PIN`) |
| Phase 4 diagnostic image | `:latest` on the first pull, then the recorded digest |
| Blender 4.5 LTS | the newest 4.5.x on download.blender.org at run time (tarball name pattern UNVERIFIED) |

### UNVERIFIED items, by file

Every `UNVERIFIED` comment in the code marks a fact the research could not confirm. The step that depends on it fails
loudly if the assumption is wrong; none skips. Line numbers are those of this commit.

**lib/common.sh** — 504: hf_transfer ignores proxies (kept off). 546, 588: a download with no reference sha256 records
its computed hash as UNVERIFIED.

**phase1-platform.sh / phase1/01-preflight.sh** — no UNVERIFIED marks beyond the Secure Boot decision above.

**phase1/02-luks.sh** — 23, 87: `--unlock-tpm2-device=auto` (systemd 256+) for re-enrolment. 363: crypttab
`tpm2-device=auto` / `x-initrd.attach` spelling. VERIFIED since v0.3.4: dracut's tpm2-tss module needs the `tpm2`
binary (tpm2-tools, universe, not on the server image), and in dracut's default hostonly mode a `tpm2-device=` in
crypttab alone pulls that module in. So neither the drop-in nor crypttab mentions TPM2 until step 4 has installed
tpm2-tools through the proxy; step 4 then adds both, rebuilds every initramfs and checks each image before the reboot.

**phase1/04-system.sh** — 387, 391: NetworkManager `main.dns=none` drop-in (only if NM manages the LAN). Time sync
(VERIFIED since v0.3.4): 26.04 runs chrony with NTS (1..4.ntp.ubuntu.com, ntp-bootstrap.ubuntu.com, UDP 123 and TCP
4460), opened for uid `_chrony` only; the pinned-address and router fallback apply only to a host still on
systemd-timesyncd. A chrony that does not synchronise within two minutes is a warning and the to-do `time-sync`.

**phase1/05b-desktop.sh** — the Google apt repository recipe for Chrome (key URL `dl.google.com/linux/linux_signing_key.pub`,
suite `stable main`); fails loudly on a non-armoured key. Chrome's managed policy path `/etc/opt/chrome/policies/managed/`
and the policy names are VERIFIED against the Chrome Enterprise documentation.

**phase1/06-docker.sh** — 35: default daemon ulimits (baseline silent).

**phase1/07-remote.sh** — 97: WG-Easy admin password minimum length. 123: `com.docker.network.bridge.name`
driver option. 150: WG-Easy v15 `INIT_*` unattended setup and the minimal capability set. 175: Cloudflare
`/user/tokens/verify` endpoint name. 257: ntfy CLI non-tty password read. 302, 327: `ntfy token add` output format.
350, 359: probing ntfy from inside the wg-easy container (no wget in the image; check from the phone).

**phase2/01-llama.sh** — 138: install layout of every llama.cpp tool. 220, 233: sudo-rs `visudo -c -f`. 246, 247:
sudo-rs `-l COMMAND` and `listpw`.

**phase2/02-orchestrator.sh** — 334: uv fetching python-build-standalone from github.com.

**phase2/03-openwebui.sh** — 248: API-key creation path (`ENABLE_API_KEYS`). 269: the model-list path.

**phase2/04-memory.sh** — 129, 878: `+cpu` cp312 torch/torchvision wheels on download.pytorch.org. 317, 320: a tree
entry without `lfs.oid`; research snippet sha256 vs tree API. 409, 419, 425: sudo-rs `-l COMMAND` fallback.
556, 586: ChromaDB telemetry variable names for the Rust server (DOCKER-USER drop is the belt). 647, 656: Chroma v2
REST collection paths.

**phase2/05-voice.sh** — 127: torchcodec 0.8.x / torch 2.9.x compatibility row. 252: §2.4 latency figure (recorded,
not asserted). 281: which large-v3-turbo id speaches' registry offers. 401, 490: CPU wheels for torch 2.6.0 cp311 /
2.9.1 on the CPU index.

**phase2/06-tools.sh** — 122: Blender `--offline-mode` on 4.5. 128: Bonsai import pattern. 152, 405, 432, 441:
extensions.blender.org API and Bonsai zip name; repo id for `--repo`. 158, 166, 244: tool tarball URLs without vendor
hashes (hash FIXED at first download). 282: package names on 26.04. 354, 371: Blender 4.5.x patch level and sha256
sidecar. 388, 394: Blender `use_online_access` preference name. 577: Radiance tarball layout. 621, 641: OpenStudio's
24.04 build / .deb on 26.04. 823: strict JDK/MinGW apt pins (bump when the archive moves).

**phase2/06b-cloudflare-token.sh** — 29, 130: `/user/tokens/verify` endpoint name.

**phase2/06c-google-oauth.sh** — 87, 88, 90, 239, 246, 260: rclone versioned download URL / SHA256SUMS layout. 310:
whether rclone sends `READY=1` for mounts (`Type=simple` used).

**phase2/07-restic.sh** — 197: restic `--files-from` (asserted at run time).

**phase2/08-sentinel.sh** — 16, 137, 147: every feed URL (unreachable = logged, never fatal).

**phase2/09-windows-share.sh** — 25, 37: `seal`, `vers=3.1.1`, `noserverino` spellings.

**phase2/09b-vault.sh** — 98: sudo-rs `sudo VAR=value` handling (`env` used). 134: a `ProtectSystem=` unit seeing a
later host FUSE mount (V18 checks it). 601, 625: sudo-rs `visudo -c -f`. 757: gocryptfs echoing on a wrong passphrase
(journal used).

**phase2/engine-env.py** — 65: file-loading/network-path llama-server options at v0.4.1.
**phase2/voice_render.py** — 37: Chatterbox real-time factor.

**phase3-models.sh** — 598: whether any of the seven repos is gated (a 401/403 stops with the licence URL).
**phase3/loadtest.py** — 82: per-request `cache_prompt: false`. 84: DeepSeek V4 Flash's chat template emitting
`<think>` by default.

**phase4-engines.sh** — 366: cp-tag list of AMD's torch wheels; whether the AMD index mirrors torch's third-party
dependencies (the die message gives the fix).
**phase4/lib-engine.sh** — 80: hf_xet proxy handling (disabled). 618: a research repo id that does not exist.
**phase4/selftest.py** — 5, 19: the self-test as a whole on this hardware; ROCm #5444 symptom.
**phase4/engines/p4common.py** — 47: the MIOpen/SDPA workaround flags as a fix for ROCm 10.0.
**phase4/engines/flux1-dev.sh / wan2.2.sh / wan2.2_test.py** — checkpoint sizes; Wan 5B UniPC values.
**phase4/engines/florence-2_test.py** — 3: the `-large` native repo id.
**phase4/engines/ui-tars.sh / ui-tars_test.py** — 6, 3: inference code (Qwen2.5-VL convention).
**phase4/engines/rad-dino.sh / rad-dino_test.py** — 3, 2: loader snippet; research-use licence note.
**phase4/engines/sam2.sh** — 5: `facebook/sam2.1-hiera-large` id (falls back to `sam2-hiera-large`).
**phase4/engines/stable-audio-open.sh / _test.py** — 8, 2: SDPA fallback for every module; inference call.
**phase4/engines/cosyvoice2.sh / _test.py** — 5, 59, 2: requirements filtering workaround; synthesis call.
**phase4/engines/trellis.sh / trellis_test.py** — 7, 29, 43, 2: shim layout, DINOv2 name, rembg `new_session`
API, end-to-end on Linux gfx1151.
**phase4/engines/blender-cycles.sh / _test.py** — 6, 12, 40, 42, 5, 88, 140: tarball and sha256 names, HIP 6.x
fatbins on the ROCm 10.0 runtime, libamdhip64 path inside the wheels.
**phase4/engines/pointllm.sh / pointllm_test.py** — 3, 8, 20, 44, 46, 63, 67, 74, 3: cc-by-nc-4.0 licence, the
dependency relaxations (transformers 4.34.1 / tokenizers 0.14), eval class names.
**phase4/engines/clay-prithvi.sh / _test.py** — 5, 2: TerraTorch `BACKBONE_REGISTRY.build(..., pretrained=True)`.

**orchestrator/src/atlas/arbiter.py** — 97-112: KV bytes-per-token figures for gpt-oss (8 KV heads), Nemotron,
Qwen3.5 and DeepSeek (bounds from Section 4.3; remeasured in Phase 3). **tasks/aegis.py** — 38, 201: ChromaDB has no
snapshot API. **tasks/sentinel.py** — 7: feed URLs. **engines.py** — 4: endpoints marked in the file. **router.py** —
130: ~4 characters per token. **memory.py** — 1215: bge-m3 embedding dimension 1024.

**config/engines.json** — 13 (env_rule wording), 167 (DeepSeek f16 rung at half the pool), 291 (bge-m3 GGUF file
name; enumerated case-insensitively). **config/phase4-engines.json** — 6, 77, 119, 148, 208, 236, 237, 277, 303,
337, 425, 446, 478, 479, 525: per-engine research notes (sizes, repo ids by snippet, licence notes for Rad-DINO and
PointLLM, TRELLIS shims, Blender tarball pattern, Prithvi build call). **config/allowlist.txt** — 98, 102, 118, 129,
139, 144, 149, 157: Blender extensions API host, Playwright CDN hosts, Drive discovery hosts, Wix/pay.com hosts, Sentinel
feed hosts. **config/sentinel-feeds.json** — 4, 31, 53, 75, 95, 116, 136: every feed URL (`verified=false`).

**docker/core/compose.yml** — 84, 87, 95: redis 8.10.2 entrypoint name and minimal capability set. 142, 152, 153,
171: ChromaDB data-dir needs, telemetry variable names, capability set. 221, 223, 224, 248, 256: Open WebUI
`ENABLE_EVALUATION_ARENA_MODELS`, library telemetry switches, lazy fetches, Docling 2.0 requirement vs docling-serve
1.34. **docker/core/compose.voice.yml** — 73, 89, 155: curl in the Kokoro image, speaches writable paths, docling-serve
`/health` (uses `/docs`). **docker/wg-easy/compose.yml** — 5, 32, 83: v15 entrypoint needs, capability set, bridge-name
option. **docker/ntfy/compose.yml** — none. **docker/rocm-base/Dockerfile** — 22, 30, 78, 117: hf_xet proxy handling,
the build-time assumption list (each fails the build loudly), Blender's shared-library list, `rocm-sdk path --root`.
**docker/buildfarm/Dockerfile** — 26, 94, 107: Gradle host, cmdline-tools directory name, sdkmanager package ids.
**docker/sandbox/Dockerfile** — 30: no cap on the total size of a sandbox job directory (recorded, not fixed).

**systemd/atlas-orchestrator.service** — 40: docker client needs under the hardening. **systemd/llama-server@.service**
— 86: whether Mesa/RADV needs AF_UNIX (kept).

**verify/v06-pyannote.sh** — 17: whether current pyannote loads the legacy 3.1 pipeline (a fail blocks the gate).
**verify/v17-sandbox.sh** — 14: exit 137 convention. **verify/v23-cloudflare-token.sh** — 89: `/user/tokens/verify`.

### Open hardware and upstream risks flagged for the Principal (no Day 1 action)

- Kernel 7.0 + gfx1151 PyTorch has open hang reports (ROCm/pytorch #6530, #6182). A V11 failure may be the host
  kernel; the Phase 4 driver logs that possibility with the issue numbers.
- Nemotron 3 Super on Vulkan has an open GPU memory fault report at ~20k-token prompts (llama.cpp #20732); the 8k
  prefill test checks the server survives and records a warning, not a hang.
- DeepSeek V4 Flash: quantised KV produced garbage in mid-2026; Phase 3 ladders f16 → q8_0 → q4_0 with a coherence
  check (S2) and records the winner; the GRUB line carries `amdgpu.lockup_timeout` for the DeviceLost reports.
- The docker socket is host root and `atlas` is in the `docker` group: the sandbox bounds the job, not the
  orchestrator that launches it (recorded in `docker/sandbox/Dockerfile`, `phase2/README-contracts.md`).
- Licences recorded, not argued (Section 16.5): PointLLM cc-by-nc-4.0, Rad-DINO research use, FLUX.1-dev and Stable
  Audio Open gated; UI-TARS 2.0 has no open weights (UI-TARS-1.5-7B is built; 2.0 stays on the watch-list); TimesFM
  3.0 is non-commercial (Chronos is built).

## 7. Developing

`bash -n` and `shellcheck -x` on every `.sh`, `python3 -m py_compile` on every `.py`, `ruff check orchestrator phase2
phase3 phase4 tools`, `bash lib/common_test.sh`, `bash phase1/luks_helpers_test.sh` (the OS-volume and crypttab helpers against stubbed `lsblk`/`cryptsetup`, doc S39), and the package tests (Python 3.12+):

```
cd orchestrator && pip install -e . && env -u CONFIG_DIR -u ATLAS_CONFIG_DIR python -m pytest
```

The orchestrator's config directory is `ATLAS_CONFIG_DIR` from `/etc/atlas/orchestrator.env`, which Phase 2 step 2
writes as `/opt/atlas/day1/config` (the mirror of this `config/`; `load_env` itself fixes the scripts' own view to
`$ATLAS_DAY1_DIR/config`). Nothing on the node sets `CONFIG_DIR`. Only the orchestrator tests override the directory:
the `config_dir` fixture in `tests/conftest.py` points `ATLAS_CONFIG_DIR` at `tests/fixtures/config` and scrubs
`CONFIG_DIR` for the tests that use it; tests that do not use the fixture read whatever your shell exports, which is
what the `env -u CONFIG_DIR -u ATLAS_CONFIG_DIR` above removes.
`tests/test_config.py::test_real_config_tree_loads` also loads the real `config/` tree beside the checkout, so a
renamed engine key or a domain card outside the §8 H1 form fails there before it fails on the node.
