# A.T.L.A.S. Day 1 scripts

`scripts/day1/` implements Section 17 (Day 1 Execution Protocol) of `docs/ATLAS_FRAMEWORK_REVIEW.md` and proves
Section 21 (V1..V24) on the node. Four phases, one command each, every phase idempotent and resumable, every phase
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
  `amdgpu.lockup_timeout=10000,60000,10000,10000`), kdump switched off without a purge and its crash-kernel reservation
  removed from the kernel line, `panic=10` added (doc S48), `kho=off` added so kexec handover sets no memory aside
  (doc S49), ufw, SSH hardening, Cockpit, the squid allowlist proxy; the hardware watchdog (doc S50): the step finds
  it without starting it (`sp5100_tco` loaded by name past Ubuntu's watchdog blacklist; `wdat_wdt` only with an ACPI
  WDAT table; with neither, softdog stands in from the next boot and the to-do `watchdog` is raised), then writes
  `atlas-watchdog-driver.service` (loads that driver at every boot) and systemd's settings (`RuntimeWatchdogSec=2min`,
  `RebootWatchdogSec=10min`), armed from the reboot; reboot; post-reboot V3a (also: no crash-kernel memory reserved,
  one `panic=10`, one `kho=off`, no CMA memory) and V24 (systemd has armed the watchdog with both timeouts; a hardware
  watchdog passes, the softdog fallback is `deferred`); a read-only record of whether a kernel panic's log can be kept
  (pstore); XFCE + xrdp + Google Chrome (Google's apt repo, telemetry/sign-in off by policy, never a snap); Docker with GPU passthrough and the
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
| 1 / 2 | One console session, up to two framed blocks. First, only if needed and each checked on the spot (three tries): the OS LUKS passphrase (the OS volume has no TPM2 token yet, or its token no longer unseals, e.g. Secure Boot switched on) and the data volume's recovery key (nothing on the node can authorise a change). Then, when a new recovery key is issued, it is shown once and you type `WRITTEN DOWN`; only after that is any previous recovery key revoked. The screen is wiped after each block, also if you interrupt. Nothing typed is stored, except that on an encrypted OS a typed key restores a missing on-node copy. | Until you answer |
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
| Settings | `/etc/atlas/atlas.env` (non-secret); `/etc/atlas/{orchestrator,memory,voice,docker,network,vault,proxy}.env` written by the steps. One optional key is never written by a step: `ATLAS_ARBITER_HEADROOM_GIB` in `orchestrator.env`, the memory the Engine Arbiter keeps back for CPU-side services (default 16, whole GiB from 4 to 64; doc S44, S48, S49: the default fits every Apex KV-ladder rung; at the larger GPU resident set, about 7-8 GiB, the f16 and q8_0 rungs fit only because Phase 1 step 4 removes kdump's 4.25 GiB crash-kernel reservation, and all of it assumes kexec handover is off (`kho=off`, step 4 too), V3a proving both; a bad value stops only `atlas-orchestrator`, which says why at start). `atlas-admin arbiter status` shows it beside the CPU-side use the Arbiter warns on (MemTotal - MemAvailable - the GPU's pages, marked when above the reserve) and MemAvailable; restart `atlas-orchestrator` after changing it |
| Secrets | `/etc/atlas/secrets/` (root:atlas 710; every file 600, owned by its one reader): `hf-token.env`, `cloudflare.env`, `ntfy.env`, `smb.cred`, `restic.pass`, `redis.env`, `openwebui.env`, `wg-easy.env`, `google/` |
| The scripts the node runs | `/opt/atlas/day1/` (a mirror of this directory) |
| Watchdog (doc S50) | `/etc/systemd/system/atlas-watchdog-driver.service` (loads the driver step 4 chose, at every boot) and `/etc/systemd/system.conf.d/90-atlas-watchdog.conf` (systemd's two timeouts). To look, read only: `systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec -p WatchdogDevice -p WatchdogLastPingTimestamp`, `cat /sys/class/watchdog/watchdog0/{identity,state,timeout,bootstatus}`, `journalctl -b -o cat SYSLOG_IDENTIFIER=systemd --grep watchdog`, `journalctl -k -b -o cat --grep sp5100`, `journalctl -k -b -o cat --grep 'Previous system reset reason'`, `systemd-analyze cat-config systemd/system.conf`. Never open `/dev/watchdog*` (no `cat`, `echo` or `wdctl` on it) and never install the `watchdog` or `wd_keepalive` packages: an open starts the timer, a close without the magic 'V' leaves it running until the node resets, and only systemd may hold the device. To undo: delete the drop-in, run `systemctl daemon-reload` (systemd disarms the timer), then `systemctl disable atlas-watchdog-driver.service` |

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
| 1 | V2, V3a, V5, V19, V24 | V1 (info); V24 `deferred` when step 4 found no hardware watchdog and softdog stands in (to-do `watchdog`) |
| 2 | V3b, V6, V12, V13, V14a, V15, V16, V17, V18, V20, V23, and every service healthy | V7, V10a (the Phase 2 half of V10); V18 `deferred` until the real vault is initialised |
| 3 | V4 per engine, V10, V14b, V21 | V22 (DeepSeek, R19) |
| 4 | V11 | V8, V9, the per-engine table |

The Phase 2 gate also checks every service by name (units active, containers healthy, authenticated Redis, HTTP health,
the bind rule, the sandbox image labels, secrets unreadable to group/world) and stops with the list before judging the
table. Fix what it names and run `phase2` again: only the gate step is left.

**"deferred"** means the proof could not be made yet for a reason Section 17 or 22 allows, and nothing depends on it
today: V7 without the reference recordings, V18 until the vault is initialised, V22 when DeepSeek V4 Flash fails to load
or no KV rung is coherent, V8/V9 and the yellow Phase 4 engines when their build or test fails, V24 when this board has
no usable hardware watchdog and softdog is armed in its place (to-do `watchdog`, doc S50). A deferred row is
written with the reason, shown in the table and the workbook, and never silently upgraded to pass.

## 6. Known limits

### Baseline amendments the scripts depended on (made in `docs/ATLAS_FRAMEWORK_REVIEW.md` v0.3.2)

- Section 17 Phase 2 now carries step `6d` (AEGIS sandbox image, Section 16.4, V17) and step `9b` (gocryptfs vault,
  Section 11, V18); `CONVENTIONS.md` §1 lists both.
- Section 23 S21 records the two sudoers fragments, `atlas-engines` and `atlas-vault`.
- The Secure Boot / PCR 7 consequence is decision **D15** (closed 2026-10-05 as option (a): Secure Boot enabled) and risk R23; the
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
loudly if the assumption is wrong; none skips. Line numbers are those of this commit (`grep -n UNVERIFIED <file>`).

**lib/common.sh** — 693: hf_transfer ignores proxies (kept off). 735, 777: a download with no reference sha256 records
its computed hash as UNVERIFIED.

**phase1-platform.sh / phase1/01-preflight.sh** — no UNVERIFIED marks beyond the Secure Boot decision above.

**phase1/02-luks.sh** — 33, 102: `--unlock-tpm2-device=auto` (systemd 256+) for re-enrolment. 234, 237: the V2 note
of the initrd check (`phase1_initramfs_rebuild`) when it finds no image or an image without the TPM2 pieces. 516: crypttab
`tpm2-device=auto` / `x-initrd.attach` spelling. VERIFIED since v0.3.4: dracut's tpm2-tss module needs the `tpm2`
binary (tpm2-tools, universe, not on the server image), and in dracut's default hostonly mode a `tpm2-device=` in
crypttab alone pulls that module in. So neither the drop-in nor crypttab mentions TPM2 until step 4 has installed
tpm2-tools through the proxy; step 4 then adds both, rebuilds every initramfs and checks each image before the reboot.

**phase1/04-system.sh** — 406, 410: NetworkManager `main.dns=none` drop-in (only if NM manages the LAN). 522, 598:
Canonical's ntp.ubuntu.com addresses kept as long-lived, and the router serving NTP. Time sync (VERIFIED since v0.3.4):
26.04 runs chrony with NTS (1..4.ntp.ubuntu.com, ntp-bootstrap.ubuntu.com, UDP 123 and TCP 4460), opened for uid
`_chrony` only; the pinned-address and router fallback (522, 598) apply only to a host still on systemd-timesyncd. A
chrony that does not synchronise within two minutes is a warning and the to-do `time-sync`. Kernel line (VERIFIED since v0.3.5, archive indices and the 26.04.1 server ISO): the ISO installs `linux-generic`; step 4 holds that line, swaps HWE metapackages over the same 7.0 image itself unless a GA or HWE kernel metapackage is held with `apt-mark`, and leaves anything else (OEM or platform metapackages, an image outside 7.0, a metapackage not from `linux-meta`, a held kernel metapackage, which the to-do says to release first with `apt-mark unhold`) to the to-do `kernel-line`; step 5 closes that to-do on GA 7.0 and otherwise raises it again with the state after the reboot. Crash kernel and panic (doc S48, option (c) of 2026-10-10), VERIFIED: kdump-tools 1:1.10.7ubuntu3 is on the 26.04.1 server image and its conffile `/etc/default/grub.d/kdump-tools.cfg` adds `crashkernel=…,128G-:4096M` whatever `USE_KDUMP` says; kernel 7.0 then reserves 4.25 GiB on this machine (4096 MiB above 4 GiB plus 256 MiB below); grub-mkconfig sources the drop-ins in byte order under dash and `set -e`, so `zzz-atlas-crash.cfg` is sourced last; every kdump-tools configure rewrites `USE_KDUMP` from the debconf answer through ucf, and `systemctl disable` and `mask` both survive its upgrades; Ubuntu's kernel waits forever after a panic (`CONFIG_PANIC_TIMEOUT=0`). INFERRED: the installer already set `USE_KDUMP=0` and debconf `false` on this node (its rule wants about 935-940 GiB free on `/var`), so `_kdump_off` normally only confirms them; the crash kernel's capture initrd could never open the LUKS root, by TPM or by passphrase, so no dump was ever possible: initramfs-tools builds it without cryptsetup, because cryptsetup itself is on the image but `cryptsetup-initramfs`, the hook that would add it, is not. Left on purpose: `/etc/kernel/postinst.d/kdump-tools` still builds an unused capture initrd at every kernel install (a conffile, not edited). Kexec handover (doc S49, the Principal's decision of 2026-10-10), VERIFIED from Ubuntu's 7.0.0-38 and -39 configs and the v7.0 source: kexec handover is on by default, sets scratch memory aside at every boot and hands it to the page allocator as CMA, which GTT pages never use; `kho=off` stops that and leaves MemTotal unchanged; the kernel takes the last valid `kho=`, so the drop-in sourced last strips every other one and appends `kho=off`. INFERRED: the scratch is about 13 GiB on this node (12.8-13.8, from the sizing rule and the ~3 GiB page map); step 4's GRUB log line measures it on the boot that still has kexec handover on, where kdump-tools' crash-kernel reservation adds about 8.5 GiB to it on the first run (counted twice; doc S49). Field reports, VERIFIED from their commits: a community pull request to NVIDIA's NV-Kernels (#590) found 9.3 GiB of such CMA on the 128 GB GB10, and NVIDIA's own 7.0 branch turned the default off on arm64 only; Supabase's Postgres images add `kho=off` through a GRUB drop-in. UNKNOWN: a Proxmox patch one verifier cited (the list was unreachable; Proxmox's kernel configuration has no such change up to 2026-10-09). Hardware watchdog (doc S50, the Principal's decision of 2026-10-11), VERIFIED from Ubuntu's 7.0.0-38 packages, kmod 34.2, systemd 259.5 and the v7.0 source: every watchdog module is blacklisted in `/usr/lib/modprobe.d/blacklist_linux_<abi>-generic.conf` (a new copy with each kernel ABI); udev, `/etc/modules-load.d`, `modules_load=` and `modprobe@.service` honour that blacklist (systemd logs "Module 'sp5100_tco' is deny-listed (by kmod)" at INFO and succeeds), while a plain `modprobe NAME` without `-b` loads the module, hence the unit; `sp5100_tco` takes its MMIO path on the SMBus controller 1022:790b from revision 0x51, stops the timer when it loads, and on a firmware-disabled timer logs "Watchdog hardware is disabled" yet lets `modprobe` exit 0 with no device, hence the sysfs test; the three candidate modules are signed and in `linux-modules`. INFERRED: this Gorgon Halo board has the same controller as Strix Halo (1022:790b revision 0x71; no public log from a Ryzen AI Max+ 495), and step 4's log line records the real ids; the initramfs loads no watchdog driver (dracut 110 copies the blacklist into it), so no timer started in that boot runs at the LUKS passphrase prompt or in dracut's emergency shell; UNKNOWN: whether this board's reset clears a timer left running by a `panic=10` restart (if not, such a wait can be reset once); softdog does not reliably reset a hard lockup of the whole kernel; the 2-minute timeout is a judgement from field resets at 30 seconds. UNKNOWN until step 4 probes it: whether this board's firmware leaves the FCH watchdog enabled (no Strix Halo log read shows it disabled), whether the BIOS offers a watchdog setting, and whether ACPI has a WDAT table (none on the Strix Halo, Strix Point and Krackan logs read). UNKNOWN beyond Day 1: what an armed timer does across a suspend (nothing on the node suspends by itself).

**phase1/05-postboot.sh** — the panic-log record (doc S48) is read-only and settles nothing by itself. UNKNOWN until it runs: whether this board registers a pstore backend (`efi_pstore`, or `erst` when the firmware has an ACPI ERST table) and whether its firmware accepts the EFI-variable writes. INFERRED: `systemd-pstore.service` is enabled on the image; after a `panic=10` restart the TPM unlocks the disks unattended, since PCR 7 is measured again from the same Secure Boot policy (whether this board's emergency reset can hang is UNKNOWN). Only a deliberate test panic on an idle node (`echo c > /proc/sysrq-trigger`) would settle these; that is the Principal's call and in no script.

**verify/v24-watchdog.sh** — V24 (doc S50) proves the watchdog is armed by systemd, not that this board's reset path works. UNKNOWN until a deliberate test reset on an idle node (the Principal's call; in no script): whether the FCH timer really resets this board, whether the TPM then unlocks unattended (INFERRED, as after `panic=10`), and whether `bootstatus` reads 32 afterwards (the driver's fired bit surviving the reset). The kernel's "Previous system reset reason" line (VERIFIED, v7.0, every Zen CPU) records a watchdog reset as "hardware watchdog timer expired" in the boot after it; the kernel clears the register, so that boot's journal is the only record. VERIFIED (systemd 259.5): `WatchdogLastPingTimestampMonotonic` is systemd's boot-time stamp mapped to the monotonic clock, and `/proc/uptime` counts boot time, so the age V24 computes is exact on a node that never suspends; systemd's lines logged to the kernel log before journald runs keep `SYSLOG_IDENTIFIER=systemd` but lose their `MESSAGE_ID`, hence the text match.

**phase1/05b-desktop.sh** — 7, 75, 86, 91: the Google apt repository recipe for Chrome (key URL
`dl.google.com/linux/linux_signing_key.pub`, suite `stable main`, no fingerprint pin); fails loudly on a non-armoured
key. 52: whether Safe Browsing's standard level (left on) sends more than hash prefixes. Chrome's managed policy path `/etc/opt/chrome/policies/managed/`
and the policy names are VERIFIED against the Chrome Enterprise documentation.

**phase1/06-docker.sh** — 35: default daemon ulimits (baseline silent).

**phase1/07-remote.sh** — 99: WG-Easy admin password minimum length. 125: `com.docker.network.bridge.name`
driver option. 152: WG-Easy v15 `INIT_*` unattended setup and the minimal capability set. 185: Cloudflare
`/user/tokens/verify` endpoint name. 280: ntfy CLI non-tty password read. 325, 350: `ntfy token add` output format.
373, 382: probing ntfy from inside the wg-easy container (no wget in the image; check from the phone).

**phase2/01-llama.sh** — 138: install layout of every llama.cpp tool. 220, 233: sudo-rs `visudo -c -f`. 246, 247:
sudo-rs `-l COMMAND` and `listpw`.

**phase2/02-orchestrator.sh** — 340: uv fetching python-build-standalone from github.com.

**phase2/03-openwebui.sh** — 248: API-key creation path (`ENABLE_API_KEYS`). 269: the model-list path.

**phase2/04-memory.sh** — 136, 912: `+cpu` cp312 torch/torchvision wheels on download.pytorch.org. 324, 327: a tree
entry without `lfs.oid`; research snippet sha256 vs tree API. 416, 426, 432: sudo-rs `-l COMMAND` fallback.
563, 593: ChromaDB telemetry variable names for the Rust server (DOCKER-USER drop is the belt). 654, 663: Chroma v2
REST collection paths.

**phase2/05-voice.sh** — 152: torchcodec 0.8.x / torch 2.9.x compatibility row. 158: whether pyannote.audio 4.0.7
loads the legacy 3.1 pipeline (V6 decides). 290: §2.4 latency figure (recorded, not asserted). 319: which
large-v3-turbo id speaches' registry offers. 439, 536: CPU wheels for torch 2.6.0 cp311 / 2.9.1 on the CPU index.

**phase2/06-tools.sh** — 147: Blender `--offline-mode` on 4.5. 153: Bonsai import pattern. 177, 514, 541, 550:
extensions.blender.org API and Bonsai zip name; repo id for `--repo`. 183, 191, 269: tool tarball URLs without vendor
hashes (hash FIXED at first download). 307: package names on 26.04. 463, 480: Blender 4.5.x patch level and sha256
sidecar. 497, 503: Blender `use_online_access` preference name. 734, 754: OpenStudio's 24.04 build / .deb on 26.04.
957: strict JDK/MinGW apt pins (bump when the archive moves). 13, 15, 31, 369, 393, 1020-1032, 1054: the header and
the soft-install rule for these inputs (a failure records `T-<tool>` deferred and the step continues). VERIFIED since v0.3.4 (S43): the pinned Radiance zip and
OpenStudio tarball nest their trees (`.../usr/local/radiance/{bin,lib,man}`, `.../usr/local/openstudio-3.11.0/`); the
step finds `bin/rtrace` and `bin/openstudio` at whatever depth and installs those trees; RAYPATH is `.:/opt/radiance/lib`.

**phase2/06b-cloudflare-token.sh** — 29, 130: `/user/tokens/verify` endpoint name.

**phase2/06c-google-oauth.sh** — 93, 94, 96, 283, 290, 304: rclone versioned download URL / SHA256SUMS layout. 354:
whether rclone sends `READY=1` for mounts (`Type=simple` used).

**phase2/07-restic.sh** — 197: restic `--files-from` (asserted at run time).

**phase2/08-sentinel.sh** — 16, 137, 147: every feed URL (unreachable = logged, never fatal).

**phase2/09-windows-share.sh** — 25, 37: `seal`, `vers=3.1.1`, `noserverino` spellings.

**phase2/09b-vault.sh** — 98: sudo-rs `sudo VAR=value` handling (`env` used). 134: a `ProtectSystem=` unit seeing a
later host FUSE mount (V18 checks it). 601, 625: sudo-rs `visudo -c -f`. 757: gocryptfs echoing on a wrong passphrase
(journal used).

**phase2/10-gate.sh** — 40, 586: the `T-<tool>` rows of step 6's soft installs (06-tools.sh), shown, never blocking.

**phase2/engine-env.py** — 65: file-loading/network-path llama-server options at v0.4.1.
**phase2/voice_render.py** — 57: Chatterbox real-time factor.

**phase3-models.sh** — 598: whether any of the seven repos is gated (a 401/403 stops with the licence URL).
**phase3/loadtest.py** — 82: per-request `cache_prompt: false`. 84: DeepSeek V4 Flash's chat template emitting
`<think>` by default.

**phase4-engines.sh** — 353: whether stable.repo.amd.com serves a root listing (a 404 there is logged; the
per-project pages decide). 447: cp-tag list of AMD's torch wheels; whether the AMD index mirrors torch's third-party
dependencies (the die message gives the fix).
**phase4/lib-engine.sh** — 80: hf_xet proxy handling (disabled). 618: a research repo id that does not exist.
**phase4/selftest.py** — 5, 19: the self-test as a whole on this hardware; ROCm #5444 symptom.
**phase4/wheel_preflight.py** — 13: whether the AMD channel root serves a listing (the project pages decide).
**phase4/engines/p4common.py** — 47: the MIOpen/SDPA workaround flags as a fix for ROCm 10.0.
**phase4/engines/wan2.2.sh / wan2.2_test.py** — 4, 3: the 5B checkpoint's size; Wan 5B UniPC values.
**phase4/engines/florence-2_test.py** — 3: the `-large` native repo id.
**phase4/engines/ui-tars.sh / ui-tars_test.py** — 6, 3: inference code (Qwen2.5-VL convention).
**phase4/engines/rad-dino.sh / rad-dino_test.py** — 3, 2: loader snippet; research-use licence note.
**phase4/engines/sam2.sh** — 5: `facebook/sam2.1-hiera-large` id (falls back to `sam2-hiera-large`).
**phase4/engines/stable-audio-open.sh / _test.py** — 8, 2: SDPA fallback for every module; inference call.
**phase4/engines/cosyvoice2.sh / _test.py** — 5, 59, 2: requirements filtering workaround; synthesis call.
**phase4/engines/trellis.sh / trellis_test.py** — 7, 37, 51, 2: shim layout, DINOv2 name, rembg `new_session`
API, end-to-end on Linux gfx1151.
**phase4/engines/blender-cycles.sh / _test.py** — 6, 12, 40, 42, 5, 88, 140: tarball and sha256 names, HIP 6.x
fatbins on the ROCm 10.0 runtime, libamdhip64 path inside the wheels.
**phase4/engines/pointllm.sh / pointllm_test.py** — 3, 8, 20, 44, 46, 63, 67, 74, 3: cc-by-nc-4.0 licence, the
dependency relaxations (transformers 4.34.1 / tokenizers 0.14), eval class names.
**phase4/engines/clay-prithvi.sh / _test.py** — 5, 2: TerraTorch `BACKBONE_REGISTRY.build(..., pretrained=True)`.

**orchestrator/src/atlas/arbiter.py** — 133-149: KV bytes-per-token figures for gpt-oss (8 KV heads), Nemotron,
Qwen3.5 and DeepSeek (bounds from Section 4.3; remeasured in Phase 3). **tasks/aegis.py** — 38, 201: ChromaDB has no
snapshot API. **tasks/sentinel.py** — 7: feed URLs. **engines.py** — 4: endpoints marked in the file. **router.py** —
130: ~4 characters per token. **memory.py** — 1215: bge-m3 embedding dimension 1024. **config.py** — the 16 GiB
default host reserve (doc S44, S48, S49) rests on an estimate, not a measurement: MemTotal near 187-188 GiB once Phase 1
step 4 has removed the crash-kernel reservation (192 GiB less the UMA carve-out, a ~3 GiB page map and ~0.5 GiB of
firmware and kernel); every Apex KV-ladder rung fits from about 182.3-184.3 GiB. V3a records the real MemTotal and
fails on a surviving reservation; `ATLAS_ARBITER_HEADROOM_GIB` changes the reserve if the node needs it. MemTotal is
the real limit only because step 4 also puts `kho=off` on the kernel line (doc S49): with kexec handover on, an
estimated 13 GiB of MemTotal (12.8-13.8, INFERRED) would be CMA scratch that GTT pages can never use, and the Apex f16
and q8_0 rungs would likely fail with a Vulkan out-of-memory in Phase 3 although granted. V3a fails while any of it
is set aside; `kho=off` does not change MemTotal.

**config/engines.json** — 13 (env_rule wording), 167 (DeepSeek f16 rung at half the pool), 291 (bge-m3 GGUF file
name; enumerated case-insensitively). **config/phase4-engines.json** — 6, 77, 119, 148, 208, 236, 237, 277, 303,
337, 425, 446, 478, 479, 525: per-engine research notes (sizes, repo ids by snippet, licence notes for Rad-DINO and
PointLLM, TRELLIS shims, Blender tarball pattern, Prithvi build call). **config/allowlist.txt** — 117, 124, 141, 152,
162, 167, 172, 180: Blender extensions API host, Playwright CDN hosts, Drive discovery hosts, Wix/pay.com/RewardPay
hosts, Sentinel feed hosts. **config/sentinel-feeds.json** — 4, 31, 53, 75, 95, 116, 136: every feed URL (`verified=false`).

**docker/core/compose.yml** — 84, 87, 95: redis 8.10.2 entrypoint name and minimal capability set. 142, 152, 153,
171: ChromaDB data-dir needs, telemetry variable names, capability set. 221, 223, 224, 248, 256: Open WebUI
`ENABLE_EVALUATION_ARENA_MODELS`, library telemetry switches, lazy fetches, Docling 2.0 requirement vs docling-serve
1.34. **docker/core/compose.voice.yml** — 73, 89, 155: curl in the Kokoro image, speaches writable paths, docling-serve
`/health` (uses `/docs`). **docker/wg-easy/compose.yml** — 5, 32, 83: v15 entrypoint needs, capability set, bridge-name
option. **docker/ntfy/compose.yml** — none. **docker/rocm-base/Dockerfile** — 22, 30, 84, 123: hf_xet proxy handling,
the build-time assumption list (each fails the build loudly), Blender's shared-library list, `rocm-sdk path --root`.
**docker/buildfarm/Dockerfile** — 26, 94, 107: Gradle host, cmdline-tools directory name, sdkmanager package ids.
**docker/sandbox/Dockerfile** — none: the per-job size item it once carried is closed by the bounded `/work` tmpfs on
the run line (48-51).

**systemd/atlas-orchestrator.service** — 40: docker client needs under the hardening. **systemd/llama-server@.service**
— 86: whether Mesa/RADV needs AF_UNIX (kept). **systemd/srv-atlas-winpc.mount** — 10, 16: the `vers=3.1.1` and
`seal` spellings (step 9 proves them when it runs; skipped on Day 1).

**verify/v03a-gtt.sh** — VERIFIED since v0.3.5: kernel 7.0.0-38 reports the requested 196608 MiB GTT pool whatever MemTotal is, and 7.0.0-39 caps it at MemTotal; both pass (S47). VERIFIED (S48, v7.0 source): `/sys/kernel/kexec_crash_size` counts both crash-kernel regions and reads 0 without a reservation (4563402752 with kdump-tools' value); the kernel finds `crashkernel=` anywhere on its command line, hence the substring check; the last `panic=` wins and `/proc/sys/kernel/panic` is the same variable. VERIFIED (S49, v7.0 source and Ubuntu's 7.0 configs): kexec handover scratch counts in the zone `cma` lines of `/proc/zoneinfo` (4 KiB pages) and never in `CmaTotal`, which counts named CMA areas only; `CmaFree` is only the free part of all CMA; Ubuntu's 7.0 configuration creates no named CMA area unless the command line asks for one, so all three read 0 once `kho=off` holds and the check cannot fail a correct node. UNKNOWN until V3a runs: the real MemTotal, expected near 187-188 GiB once the reservation is gone (the summary prints it, and the kexec handover figures beside it).

**verify/v06-pyannote.sh** — 19: whether current pyannote loads the legacy 3.1 pipeline (a fail blocks the gate).
**verify/v17-sandbox.sh** — 14: exit 137 convention. **verify/v23-cloudflare-token.sh** — 89: `/user/tokens/verify`.

### Open hardware and upstream risks flagged for the Principal (no Day 1 action)

- Kernel 7.0 + gfx1151 PyTorch has open hang reports (ROCm/pytorch #6530, #6182). A V11 failure may be the host
  kernel; the Phase 4 driver logs that possibility with the issue numbers.
- Nemotron 3 Super on Vulkan has an open GPU memory fault report at ~20k-token prompts (llama.cpp #20732); the 8k
  prefill test checks the server survives and records a warning, not a hang.
- DeepSeek V4 Flash: quantised KV produced garbage in mid-2026; Phase 3 ladders f16 → q8_0 → q4_0 with a coherence
  check (S2) and records the winner; the GRUB line carries `amdgpu.lockup_timeout` for the DeviceLost reports.
- `panic=10` (doc S48) restarts the node after a kernel panic, but not after everything that can stop it: a panic before
  the kernel reads its parameters still hangs; soft and hard lockups and hung tasks only warn; a failed TPM unseal waits
  at the passphrase prompt; recovery boots keep the wait-forever default. A panic that recurs at every boot restarts the
  node in a loop (accepted). The hardware watchdog (doc S50, next item) now covers a crash of systemd itself, which
  used to freeze the node, and a hard hang that never panics; lockup panics were not added (each a separate decision).
- The hardware watchdog (doc S50) resets the node two minutes after systemd stops pinging it: a hung or crashed systemd,
  a hard hang of the whole kernel (not reliably with the softdog fallback), and a reboot whose last phase hangs for 10
  minutes. It does not catch a GPU wedge or a lockup while systemd still runs, a hang before the driver loads, or a hang
  in the kernel's own device shutdown at reboot (the kernel stops the timer first). Nothing starts a timer in the
  initramfs, and rescue or emergency shells keep systemd alive and pinging, but a timer left running by a `panic=10`
  restart is stopped only when the driver loads, so whether such a wait can be reset once is UNKNOWN until the
  deliberate test reset. A hang that recurs at every boot restarts the node in a loop (accepted, as with `panic=10`).
  What an armed timer does across a suspend is UNKNOWN; nothing on the node suspends by itself and the sleep targets
  are not masked. The softdog fallback is a build default the Principal may drop. Left to the Principal: a deliberate test reset, the timeout value,
  lockup panics, `CrashAction=reboot`, `watchdog.stop_on_reboot=0`, masking the sleep targets, loading the driver in
  the initramfs.
- The docker socket is host root and `atlas` is in the `docker` group: the sandbox bounds the job, not the
  orchestrator that launches it (recorded in `docker/sandbox/Dockerfile`, `phase2/README-contracts.md`).
- Licences recorded, not argued (Section 16.5): PointLLM cc-by-nc-4.0, Rad-DINO research use, FLUX.1-dev and Stable
  Audio Open gated; UI-TARS 2.0 has no open weights (UI-TARS-1.5-7B is built; 2.0 stays on the watch-list); TimesFM
  3.0 is non-commercial (Chronos is built).

## 7. Developing

`bash -n` and `shellcheck -x` on every `.sh`, `python3 -m py_compile` on every `.py`, `ruff check orchestrator phase2
phase3 phase4 tools`, `bash lib/common_test.sh`, `bash phase1/luks_helpers_test.sh` (the OS-volume and crypttab helpers against stubbed `lsblk`/`cryptsetup`, doc S39), `bash phase1/kernel_line_test.sh` (the kernel-line hold, step 5's kernel-line record that closes or refreshes the to-do, and the NPU record against stubbed `dpkg-query`/`apt-get`/`uname`/`lspci`, doc S45), `bash phase1/crash_params_test.sh` (the `zzz-atlas-crash.cfg` drop-in under grub-mkconfig's own sourcing lines, dash and `set -e`, with the real `kdump-tools.cfg`, `kho=` words included; `_grub_params` end to end with its `panic=10` and `kho=off` checks and the kexec handover scratch record; `_kdump_off` and `_panic_log_record` against stubbed `dpkg-query`/debconf/`systemctl`/`kdump-config`/`sysctl`/`findmnt`; doc S48, S49; needs dash), `bash verify/v03a_gtt_test.sh` (V3a's GTT pool check, capped and uncapped kernels, doc S47, its crash-kernel and panic checks, doc S48, and its `kho=off` and CMA checks against a `/proc/zoneinfo` fixture, doc S49), `bash phase1/watchdog_test.sh` (step 4's watchdog probe, driver unit and systemd drop-in against stubbed `modprobe`/`dmesg`/`systemctl` with every node path under a temp dir, the unit under `systemd-analyze verify` when it is installed, step 5's V24 hand-off, the Phase 1 gate with V24 pass, deferred, fail and missing, and `tools/fill-workbook.py`'s V24 row when openpyxl is installed; doc S50), `bash verify/v24_watchdog_test.sh` (V24's pass, deferred and fail cases against a fake sysfs, `/proc` and stubbed `systemctl`/`journalctl`; doc S50; neither watchdog test opens a real `/dev/watchdog*`), and the package tests (Python 3.12+):

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
