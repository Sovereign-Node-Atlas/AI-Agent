# Phase 2 gate, vault and sandbox: cross-writer contracts

Written by the writer of `phase2/06d-sandbox.sh`, `phase2/09b-vault.sh`, `phase2/10-gate.sh`,
`docker/sandbox/Dockerfile`, `verify/v14a-arbiter-stubs.sh`, `verify/v15-approval-gate.sh`,
`verify/v16-router-hard-rule.sh`, `verify/v17-sandbox.sh` and `verify/v18-vault.sh`. Everything below is something one
of those files relies on that `CONVENTIONS.md` does not state, or something they define for another writer. Where the
other side does not yet meet a contract, §3 says so; each script fails loudly with the contract named when it is not met.

## Unpinned (rule §7.9; the Principal's record until `scripts/day1/README.md` exists and copies it)

| Item | State | Why |
|---|---|---|
| `python:3.12-slim` (sandbox base, `docker/sandbox/Dockerfile`) | **pinned by digest** `sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016` (label `org.atlas.sandbox.version=4`, `org.atlas.sandbox.base`) | Not a research pin (the research names no python image tag or digest): the multi-arch index Docker Hub served for `python:3.12-slim` on 2026-10-04, resolved by this writer from the registry API. **VERIFIED 2026-10-04** (fix round 4, writer and reviewer independently): `HEAD registry-1.docker.io/v2/library/python/manifests/<digest>` -> 200 and the `3.12-slim` tag's `Docker-Content-Digest` is exactly this digest. A digest Docker Hub stops serving makes `06d-sandbox.sh` die loudly, never float. Re-pin per the Dockerfile header and bump the version label (06d, the gate and v17 check it). |
| `gocryptfs` | **checked against the VERIFIED pin** `2.6.1-1` (services-tools.md §4.11) | apt cannot be told to install an older archive version, so `09b-vault.sh` dies when the installed upstream version is not 2.6.1; override once the man-page facts are re-verified: `sudo env ATLAS_GOCRYPTFS_VERSION_OK=1 ./atlas-day1.sh phase2` |

Not unpinned, recorded here because an earlier revision of this table listed it wrongly: `pytest` is **`pytest==9.1.1`**
in `orchestrator/pyproject.toml` (a resolved pin by the package writer, not a research pin; a runtime dependency installed
by step 2's `pip install -e`, never by the gate).

## 1. Orchestrator package (`orchestrator/`, installed at `/opt/atlas/orchestrator` into `/opt/atlas/venv`)

### Unit tests (V14a, V15, V16)

| Verify script | Runs | Claim the tests must prove |
|---|---|---|
| `verify/v14a-arbiter-stubs.sh` | `/opt/atlas/venv/bin/python -m pytest -p no:cacheprovider tests/test_arbiter.py` | Engine Arbiter refuses an over-budget load and downgrades a Deep Think depth against stub footprints (4.2, 9.1) |
| `verify/v15-approval-gate.sh` | `... tests/test_approval.py` | approval gate holds a standard-tier email until approved; routine-tier auto-sends and logs (16.2) |
| `verify/v16-router-hard-rule.sh` | `... tests/test_router.py` | a "medical" message routes to Arthur even when the (stubbed) classifier disagrees, and the decision is logged (7.2) |

* Working directory is the package directory (`/opt/atlas/orchestrator` when the file exists there, else the
  mirrored `/opt/atlas/day1/orchestrator`). The scripts drop to `atlas` only when that directory is atlas-owned; step 2
  makes it `root:atlas` (g-w,o-w), so on the node the tests run as root (`-p no:cacheprovider` and
  `PYTHONDONTWRITEBYTECODE=1` leave no files behind either way).
* `/etc/atlas/orchestrator.env` is exported before pytest (so settings read at import time exist); the tests must not
  need any live service (CONVENTIONS.md §7.8: stubs for llama-server, Redis, Docker). After that source the scripts
  export `HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_OFFLINE=1 DO_NOT_TRACK=1 ANONYMIZED_TELEMETRY=False
  CHROMA_TELEMETRY_ENABLED=false PIP_DISABLE_PIP_VERSION_CHECK=1` unconditionally (rule §7.1: the package imports
  chromadb/posthog and huggingface_hub at import time; the env file cannot re-enable a beacon).
* Pass = pytest exit 0. Exit 5 (no tests collected) is a fail. The one-line evidence is the pytest summary line
  (`N passed in Xs`): `pyproject.toml` sets `addopts = "-q"`, so the scripts pass no `-q` of their own (a second one
  would silence that line).
* `pytest==9.1.1` is a **runtime dependency** of the `atlas` package (`[project].dependencies` in `pyproject.toml`, a
  resolved pin; the `dev` extra is ruff only), installed by `phase2/02-orchestrator.sh`'s `pip install -e $ORCH_DIR`.
  **The gate installs nothing**: when `python -m pytest --version` fails, `10-gate.sh` warns ("re-run Phase 2 step 2:
  `--force 02`") and V14a/V15/V16 record the failure with the same hint.

### Vault (Section 11, D13, 10.5; V18)

Installed by `phase2/09b-vault.sh`:

| Item | Contract |
|---|---|
| `/etc/atlas/vault.env` (root:atlas 640) | `VAULT_CIPHER_DIR=/srv/atlas/vault/cipher`, `VAULT_MOUNT_DIR=/srv/atlas/vault/open`, `VAULT_TEST_CIPHER_DIR=/srv/atlas/staging/vault-test-cipher`, `VAULT_IDLE=15m`, `VAULT_UNIT=atlas-vault.service`, `VAULT_HELPER=/usr/local/bin/atlas-vault`, `VAULT_RUN_DIR=/run/atlas-vault`, `VAULT_PASS_FILE=/run/atlas-vault/pass`, `VAULT_OVERRIDE_FILE=/run/atlas-vault/override.env`, `VAULT_USER=atlas` |
| `/etc/atlas/orchestrator.env` | gains `VAULT_CIPHER_DIR`, `VAULT_MOUNT_DIR`, `VAULT_IDLE`, `VAULT_UNIT`, `VAULT_HELPER` (ensure_kv; step 9b restarts `atlas-orchestrator` once, only when the file's content changed) |
| `/srv/atlas/staging/vault-test-cipher` (atlas:atlas 700) | the gate's **throw-away test vault** (random passphrase in `/etc/atlas/secrets/vault-test.pass`, root:root 600), OUTSIDE the Appendix C vault tree (fix round 4): staging is excluded from restic entirely by `07-restic.sh`, so `/srv/atlas/vault` holds the real container alone. 9b removes an earlier `/srv/atlas/vault/test-cipher` when it still holds a test vault; 07's `ensure_dir`/exclude line for that old path are harmless and may go |
| `/etc/atlas/secrets/` | left **root:atlas 710** (traverse-only for atlas), the one Phase 2 value (`phase2-services.sh`, 02's `ORCH_SECRETS_MODE=710`, 03, 06c, 07, 08, 09); 9b adds only the root-read `vault-test.pass` (root:root 600). 9b warns about, and the gate records as NOT HEALTHY, any file under it that is group- or world-readable (`find -type f -perm /077`), so 710 can never become listable or readable by drift |
| `/run/atlas-vault/` (root:root 755, tmpfs) | the **only** transient home of the passphrase: `pass` (**root:root 600**, one line, created with mktemp + rename, symlinks refused; no process running as or with group atlas can read it) and `override.env` (root:root 644, written by root verification runs only, one open, expires after 600 s). gocryptfs (user atlas) receives the passphrase on its **inherited stdin**: the unit has `StandardInput=file:/run/atlas-vault/pass` (systemd opens it as root before dropping to `User=atlas`) and gocryptfs reads one line from a non-terminal stdin (v2.6.1 `readpassword`: `Once`/`Twice` -> `readPasswordStdin`, mount and `-init` alike; VERIFIED 2026-10-04 against the v2.6.1 tag, `read.go` lines 34-35/49-50 and `passfile.go:27` `os.Open`, by this writer and the review independently). **No `-passfile`**: `-passfile /dev/stdin` re-opens `/proc/self/fd/0` by name as atlas against the root-only inode and fails with EACCES (the round-4 blocker). `wait-mounted` shreds the file the moment the mount is up, `cleanup`/`lock` otherwise. Declared exception to CONVENTIONS.md §7.2 "secrets only under /etc/atlas/secrets/": tmpfs only (the helper refuses when `findmnt` says otherwise), never `/run/atlas` (atlas-writable: a root helper operating by name there would be a symlink-planting root escalation). §7.2 must admit it in those words (§3 item 11); the file-less alternative was evaluated and rejected (same item) |
| `/usr/local/bin/atlas-vault open` | passphrase on **stdin** (one line), never an argument; prints `open`; exit 0 when `VAULT_MOUNT_DIR` is mounted, 1 when refused (gocryptfs exit 12 = wrong passphrase), 2 on contract errors (including "not initialised", the state after a default, unattended step 9b) |
| `/usr/local/bin/atlas-vault lock` | prints `locked`; exit 0 when unmounted |
| `/usr/local/bin/atlas-vault status` | prints `open` or `locked`; exit 0; needs no privilege (reads `/proc/self/mountinfo`) |
| `/usr/local/bin/atlas-vault override` | root only, not in sudoers: from `VAULT_IDLE_OVERRIDE` (may only **shorten** `VAULT_IDLE`, never 0) and/or `VAULT_CIPHER_OVERRIDE` (an initialised vault dir) writes `override.env` for the **next** open, whoever performs it, so `verify/v18-vault.sh` opens the test vault with a 20 s idle **through the button**; `init` and `override` also honour the variables directly |
| `/etc/sudoers.d/atlas-vault` | `atlas ALL=(root) NOPASSWD:` exactly `atlas-vault open`, `atlas-vault lock`, `atlas-vault status` (sudo-rs, plain syntax, no SETENV: the button path can never carry an override variable). `status` stays although it needs no root because `atlas.vault.VaultController._argv()` runs every verb through `sudo -n`; dropping it would make the package's `status()` log a helper error on every locked check. visudo is used when present and able to check a file; the proof that counts is `runuser -u atlas -- sudo -n atlas-vault status` (the previous fragment is restored on failure). **Baseline contradiction, recorded** (§3 item 6): CONVENTIONS.md §8 and Section 23 S21 name `atlas-engines` as the whole control path |
| `atlas-vault.service` | `gocryptfs -fg -q -nosyslog -idle ${VAULT_IDLE} ${VAULT_CIPHER_DIR} ${VAULT_MOUNT_DIR}` as user `atlas` in the **host** mount namespace, `StandardInput=file:/run/atlas-vault/pass` (gocryptfs reads one line from its non-tty stdin; no `-passfile`), `EnvironmentFile=vault.env` then `-override.env`; `ExecStartPost=+atlas-vault wait-mounted`, `ExecStopPost=-+atlas-vault cleanup` (full privileges: only root may touch the run dir); active exactly while the vault is open; auto-exits on the idle unmount. Step 9b's mechanics proof exercises exactly this path on the test vault, so a wrong assumption about systemd/gocryptfs dies at step 9b, not at the gate |
| `$VAULT_MOUNT_DIR/.atlas-selftest/` | the **only** path Day 1 automation ever creates or writes inside a vault (9b's mechanics file, V18's marker); both remove their file and the directory afterwards, and 9b locks the vault before dying on any failure after the open. The opt-in prompt tells the Principal this in as many words before the passphrase is typed (Section 16.3 rule 5) |

**No interactive pause by default.** CONVENTIONS.md §7.6 lists three pauses and this step is not one of them, so a
plain `sudo ./atlas-day1.sh phase2` never stops at 9b: the real vault is left uninitialised (flag
`/var/lib/atlas/day1/vault-init-pending`, warned, pushed by ntfy), the test vault is initialised and proven, and the gate
runs V18 on it. **V18 is then recorded `deferred`, not `pass`** (fix round 4): `verify/v18-vault.sh` in test mode runs
every check and, while the flag exists, exits 2 with the evidence line `deferred: real vault not initialised (...);
mechanics proven on the test cipher dir: ...`. Section 21 V18 is a proof about *the* vault; a pass for a substitute cipher
dir would be the silent pass §7.4 forbids. `deferred` never blocks (CONVENTIONS §6, the V7 precedent), so Phase 3 may
start, and the gate prints the opt-in command. The Principal initialises (or re-proves) the real vault from a console with

```
sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b
```

(`env`, because sudo-rs's handling of `sudo VAR=value cmd` is UNVERIFIED; the entry script re-execs with `exec`, so the
variable reaches the step). Only then does 9b read the passphrase from `/dev/tty` (twice on first initialisation, once
on a re-run), show the gocryptfs master key once and wait for `WRITTEN DOWN`, prove the mechanics on the real vault and
record V18 (real cipher dir) with the passphrase piped in and **`V18_REAL_VAULT=1`** exported (real mode is explicit;
without the flag v18 never reads stdin and refuses the real cipher dir, so a `yes |` or here-doc driver can never feed a
string to the real vault as a passphrase). `ATLAS_VAULT_INIT=1` without a terminal dies with that message. The button
answers "not initialised" (exit 2) until then. A driver that exports `ATLAS_FORCED_STEPS` naming `09b` is honoured like
`ATLAS_VAULT_INIT=1` (contract requested, §3 item 3).

What the package must implement:

* `POST /vault/open` (body `{"passphrase": "..."}` from the interface button, never from a chat message): pipes the
  passphrase into `sudo -n /usr/local/bin/atlas-vault open` (stdin), returns the helper's verdict as
  `{"ok": true|false, "state": "open"|"locked", "message": ...}` with HTTP 200 when ok (403 when refused). The
  passphrase is not logged, not stored, not passed to a model. V18 opens through this endpoint in both modes and never
  prints a raw response body (its `_redact` drops every `input` key and keeps `detail/msg/message/state/ok` only).
* `POST /vault/lock`: `sudo -n /usr/local/bin/atlas-vault lock`.
* `GET /vault/status`: `{"state": "open"|"locked"}` computed from the service's **own** view (`/proc/self/mountinfo`
  field 5 == `VAULT_MOUNT_DIR`, or `atlas-vault status`). V18 checks it after opening and after the idle lock; a
  mount invisible inside the service's mount namespace fails V18. (`mountpoint -q` must not be used by any user other
  than `atlas`: the FUSE kernel driver denies even root without `-allow_other`, which is deliberately not set so restic,
  root, never sees plaintext.)
* Admin authentication (api.py): `/vault/*` are admin routes. With `ORCH_ADMIN_TOKEN_FILE` set in `orchestrator.env`
  they need `X-Atlas-Token`; V18 reads the token from that root-readable file (`ORCH_ADMIN_TOKEN=...` or the bare
  token) and sends it through a curl config file on tmpfs (`/run/atlas-vault/.v18curl.*`, root 600, created after the
  EXIT trap is armed and shredded by it on every exit path; never argv, never disk), and names a
  401/403-with-token-detail as an auth failure. Without a token file the routes accept loopback callers (V18 is one).
* Session tagging (10.5): anything read from under `VAULT_MOUNT_DIR` is tagged `vault` for the life of that session;
  vault-tagged content is not written to any ChromaDB collection, not to the LightRAG working dir, not summarised.
* `atlas-admin vault-session-test --file PATH` (implemented in `admin.py`): reads PATH inside a vault-tagged session,
  then attempts a memory write through the normal write path **synchronously** (no queued Celery task still pending
  when it returns), closes the file (an open file keeps gocryptfs "not idle"), exits 0 and prints one JSON line, e.g.
  `{"read": true, "chars": 91, "vault_tagged": true, "memory_write_attempted": true, "memory_write_suppressed": true}`.
  V18 does not trust the JSON verdict: it queries every ChromaDB collection and greps the graph store afterwards. V18
  still probes `--help` and falls back to `python -m atlas.vault session-test --file PATH` for an older installed copy.

### Sandbox (Section 16.4; V17)

Owned by **`phase2/06d-sandbox.sh`** (fix round 4; the gate only judges). It builds `docker/sandbox/Dockerfile` ->
`atlas-sandbox:py3.12` (skipped when the image carries label `org.atlas.sandbox.version=4`; a build failure stops the
phase), proves the base pin from the image's own label `org.atlas.sandbox.base` (with BuildKit a base pulled during the
build is not a listed image), smoke-runs it as `65534:<atlas gid>`, creates `/srv/atlas/sandbox` (atlas:atlas 750) and
writes into `/etc/atlas/orchestrator.env`: `SANDBOX_IMAGE=atlas-sandbox:py3.12`, `SANDBOX_DIR=/srv/atlas/sandbox`,
`SANDBOX_MEMORY=2g`, `SANDBOX_CPUS=2`, `SANDBOX_PIDS=256`, `SANDBOX_TMPFS_SIZE=512m`, `SANDBOX_TIMEOUT_S=300`,
`SANDBOX_FSIZE=1073741824`, restarting `atlas-orchestrator` once only when the file's content changed. `10-gate.sh`
records NOT HEALTHY when the image is missing, carries another label version or base, or any key is absent. The run line
the package uses (`atlas.sandbox.build_argv`), as the header of `docker/sandbox/Dockerfile` states it:

```
timeout -k 5 $((SANDBOX_TIMEOUT_S + 10)) docker run --rm --init --pull never --name sb-<job> \
  --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \
  --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=$SANDBOX_TMPFS_SIZE \
  --ulimit fsize=$SANDBOX_FSIZE --cap-drop ALL --security-opt no-new-privileges --user 65534:<atlas gid> \
  -e SANDBOX_TIMEOUT_S=$SANDBOX_TIMEOUT_S \
  -v $SANDBOX_DIR/<job>:/work:rw -w /work atlas-sandbox:py3.12 python3 /work/main.py
docker rm -f sb-<job>      # always afterwards
```

* `--pull never`: an unqualified image name not present locally would otherwise be resolved to
  `docker.io/library/atlas-sandbox:py3.12` and pulled. `--user 65534:<atlas gid>`: the job directory is created setgid
  group atlas (0o2770) and staged files 0o640, so the nobody-uid process reaches `/work` through the group bit only.
  `verify/v17-sandbox.sh` and 06d's smoke run resolve the same gid from `getent group atlas` (fix round 4), so the proof
  runs under the gid the orchestrator will use.
* **The time bound is enforced inside the container.** The image's `ENTRYPOINT` wraps every command in coreutils
  `timeout -s KILL $SANDBOX_TIMEOUT_S` (default 300 when `-e` is absent), so a program that ignores SIGTERM is
  SIGKILLed at the deadline (exit 137; the package tells it from the OOM kill by the elapsed time). The host-side GNU
  timeout only signals the docker client and is a backstop 10 s later. V17 proves both the memory cap and this bound,
  and requires label `org.atlas.sandbox.version=4` (digest-pinned base, in-container timeout, telemetry opt-outs).
* **Telemetry off inside the image** (rule §7.1, fix round 4): the Dockerfile's ENV sets `HF_HUB_DISABLE_TELEMETRY=1
  HF_HUB_OFFLINE=1 DO_NOT_TRACK=1 ANONYMIZED_TELEMETRY=False CHROMA_TELEMETRY_ENABLED=false
  PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONNOUSERSITE=1` (the same set v14a/v15/v16/v18 export), so a library the staged
  code brings cannot beacon to an allowlisted host once a tier grants network.
* **Disk:** `--ulimit fsize` bounds each file a job writes (1 GiB); `/tmp` is a bounded noexec tmpfs. UNVERIFIED /
  open: no cap on the total size of `$SANDBOX_DIR/<job>` on the 8 TB volume exists yet, and the bind mount carries no
  nosuid/nodev. Two durable fixes, either of which changes the package's staging and is therefore recorded, not made,
  here (§3 item 5): an XFS project quota on `$SANDBOX_DIR`, or `--mount type=tmpfs,dst=/work,tmpfs-size=$SANDBOX_WORK_SIZE`
  (charged to the job's memory cgroup) with `docker cp` of `main.py`/inputs in and outputs out. The package removes the
  job directory once outputs are collected.
* **The docker socket is host root.** `atlas` is in the `docker` group (CONVENTIONS.md §2; `SupplementaryGroups=docker`
  in atlas-orchestrator and atlas-celery-cpu/gpu), and a docker-group member can `docker run --privileged -v /:/host`.
  The run line above bounds the **sandboxed job**; it does not bound the orchestrator that launches it. A
  prompt-injected orchestrator that reaches a shell has host root through the socket: `/etc/atlas/secrets`, the vault
  cipher dir, the sudoers fragments, `ProtectSystem=`, the FUSE root-denial and Section 16.3 rules 5/6/8 then rest on
  the model's behaviour. Recorded in the same words in `systemd/atlas-orchestrator.service`, `phase1/06-docker.sh` and
  the Dockerfile header. The remedy (a root-owned `atlas-sandbox-run <job-id>` under its own sudoers line with `atlas`
  out of the docker group, or a rootless/proxied socket) changes the package, the units and Phase 1 and is the
  Principal's decision; not made by this writer.
* A tier that grants network replaces `--network none`. The container is then confined to the allowlist proxy only by
  the DOCKER-USER rules of `atlas-docker-egress.service` (everything leaving a bridge for the LAN other than the pinned
  resolvers is dropped and logged; the container gets `HTTP(S)_PROXY` from the daemon config but code may ignore the
  variables). The orchestrator therefore **refuses a network grant when `systemctl is-active
  atlas-docker-egress.service` is not `active`** (`atlas.sandbox.egress_unit_active`, implemented); a dedicated
  `--network atlas-sandbox` bridge (so the egress rule can match the sandbox subnet alone) is optional.
* The image has no pip, curl or wget; the process runs as nobody on a read-only root.

## 2. Files and names from other writers that these scripts use

| Used by | Contract | Owner |
|---|---|---|
| 06d, 10-gate, v18 | `/etc/atlas/orchestrator.env` written with `ensure_kv` (foreign keys kept); keys `ORCH_PORT`, `ATLAS_DB_PATH`, optional `ORCH_ADMIN_TOKEN_FILE` (a root-readable file under `/etc/atlas/secrets`) ... | `phase2/02-orchestrator.sh` |
| 06d, 10-gate, v18 | units `atlas-orchestrator`, `atlas-celery-cpu`, `atlas-celery-gpu`, `atlas-celery-beat`; `GET /health` -> 200; `POST /vault/open`, `GET /vault/status` as in §1 | `phase2/02-orchestrator.sh`, `systemd/atlas-orchestrator.service`, the package |
| 10-gate, v14a/v15/v16, v18 | `/opt/atlas/venv/bin/{python,atlas-admin}` with pytest importable (`pytest==9.1.1`, runtime dependency); the package at `/opt/atlas/orchestrator` (`root:atlas`, read-only for atlas) with `pyproject.toml` | `phase2/02-orchestrator.sh` |
| 10-gate | `llama-server@router-qwen3.5-4b`, `llama-server@embed-bge-m3`, `llama-server@rerank-bge-v2-m3`; `/etc/atlas/engines/<key>.env` with `LLAMA_ARG_PORT`; fallback `LLAMA_PORT_BASE` + index in `config/engines.json` | `phase2/01-llama.sh`, `phase2/04-memory.sh`, `phase2/engine-env.py` |
| v18 | `/etc/atlas/memory.env`: `CHROMA_URL`, `CHROMA_COLLECTIONS` (quoted, space-separated), `LIGHTRAG_WORKING_DIR`; the six Section 10.1 collections exist; Chroma v2 REST `.../collections` (list) and `.../collections/{id}/get` (query) under `default_tenant/default_database` | `phase2/04-memory.sh`, `docker/core/compose.yml` |
| 10-gate | containers `atlas-redis` (redis-server `--requirepass "$REDIS_PASSWORD"` from its env_file `/etc/atlas/secrets/redis.env`; the gate PINGs with `REDISCLI_AUTH` inside the container and requires the unauthenticated PING to answer NOAUTH, exactly step 2's probe), `atlas-chromadb`, `atlas-openwebui` (host network) from `compose.yml`; `atlas-kokoro`, `atlas-speaches`, `atlas-docling` from `compose.voice.yml` (the bare `kokoro`/`speaches`/`docling` names of an earlier revision are still accepted); HTTP: ChromaDB `127.0.0.1:8000/api/v2/heartbeat`, Kokoro `8880/health`, speaches `8881/health`, docling `5001/docs`, Open WebUI `$OPENWEBUI_PORT/health` | `docker/core/compose.yml`, `docker/core/compose.voice.yml`, `phase2/03-openwebui.sh`, `phase2/05-voice.sh` |
| 10-gate | `atlas-ntfy` on `127.0.0.1:8090/v1/health`; `wg-easy`; units `docker`, `squid`, `cockpit.socket`, `xrdp`, `ssh.service`/`ssh.socket`, `atlas-ddns.timer`, `atlas-docker-egress.service` | Phase 1 steps 4, 5b, 6, 7 |
| 10-gate | long-running units earlier Phase 2 steps `enable --now` and the gate requires active (fix round 4): `atlas-openwebui-egress.service` (step 3, the Open WebUI egress chain V12 relies on), `atlas-aegis-trigger.path` (step 7, the manual AEGIS trigger), `atlas-gdrive@<tag>.service` for every `email:tag` in `GOOGLE_ACCOUNTS` (step 6c; a "not installed" row when `/etc/systemd/system/atlas-gdrive@.service` is absent) | `phase2/03-openwebui.sh`, `phase2/07-restic.sh`, `phase2/06c-google-oauth.sh` |
| 10-gate (bind rule) | squid binds `127.0.0.1:3128` **plus the docker0 gateway** (`http_port <DOCKER_GW>:3128`, `config/squid.conf.tmpl`, re-rendered by step 6; `DOCKER_GW` and `LAN_IP` in `/etc/atlas/docker.env`), fenced by ufw: active, `Default: deny (incoming)`, and the rule `3128/tcp on $LAN_IFACE DENY IN` (`phase1/04-system.sh`). The gate accepts the gateway (or a wildcard) listener only behind that fence, never squid on the LAN address; Redis 6379 loopback only; a Docker publish on `0.0.0.0`/`[::]` (docker-proxy listener or `docker ps` ports) is unhealthy whatever ufw says, because publishes bypass ufw INPUT; of the host daemons, EXACTLY sshd/ssh.socket (22), cockpit.socket (9090), xrdp (3389) and Open WebUI on the host network (`$OPENWEBUI_PORT`) may listen on a wildcard address behind an active default-deny ufw; any other wildcard listener is NOT HEALTHY (§3 item 13) | Phase 1 steps 4 and 6, `config/squid.conf.tmpl`, every compose file |
| 10-gate, 9b | `atlas-aegis.timer`, `atlas-restic-check.timer`, `$ATLAS_ETC/restic-exclude.txt` (excludes `$ATLAS_SRV/staging` entirely, which is where the test vault lives; 9b appends the mount point and the test vault with `ensure_line`; the gate asserts the mount-point exclusion before V13); `atlas-sentinel.timer`, `atlas-prune.timer`; `srv-atlas-winpc.automount` (only when installed; step 9 may opt out) | `phase2/07-restic.sh`, `phase2/08-sentinel.sh`, `phase2/09-windows-share.sh` |
| 10-gate | verify usage lines: `v12-openwebui-offline.sh CONTAINER WINDOW_S` (called with `atlas-openwebui 120`); `v06`, `v07`, `v13`, `v20`, `v23`, `v03b` with their defaults; `v10a-router-resident.sh` (its one line recorded by the gate as `V10 info`, §3 item 8) | the respective writers |
| 06d, 10-gate | `/etc/atlas/docker.env` (`CONTAINER_HTTP(S)_PROXY`, `LAN_IP`, `DOCKER_GW`), `/etc/atlas/core.env`, `/etc/atlas/voice.env` as compose `--env-file`s (used only for `docker compose ps`, each only when present); docker with the daemon proxy and the `atlas` group (06d's smoke run gid) | Phase 1 step 6, `phase2/02-orchestrator.sh`, `phase2/05-voice.sh` |
| 9b | apt `gocryptfs 2.6.1-1` and `fuse3` (VERIFIED versions, services-tools.md §4.11); `config/allowlist.txt` covers the Ubuntu archive and the Docker Hub hosts for the pinned `python:3.12-slim` (`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`); `/run` is tmpfs (systemd default; 9b and the helper verify it); `$ATLAS_SRV/staging` exists (Phase 1 step 3); `$ATLAS_ETC/secrets` is root:atlas 710 (the one Phase 2 value) | Phase 1 steps 3 and 4 (mounts, allowlist), `config/allowlist.txt`, `phase2-services.sh`, `phase2/02-orchestrator.sh`, `phase2/06c-google-oauth.sh` |

## 3. Conflicts and gaps noticed for the other side (no change made to their files)

**Integration note (2026-10-04):** items 1, 2, 6(a), 10, 11, 12 and 13 are now reflected in `CONVENTIONS.md` (§1, §2, §4,
§5, §6, §7.2, §8); item 8 is closed by declaring `V10a` (10-gate.sh records it too, `tools/fill-workbook.py` shows it as the
V10 evidence column); item 14 is clear (ruff passes). `phase1/02,03,07` now use 710 for the secrets directory. Item 3's
`ATLAS_FORCED_STEPS` remains unimplemented in `lib/common.sh` (9b reads it defensively; `ATLAS_VAULT_INIT=1` is the
documented path). The Section 17 wording for 6d/9b is a baseline amendment the Principal owns (README "Known limits").

1. **Vault layout versus Section 11 / Appendix C wording.** The baseline names `/srv/atlas/vault` as "the gocryptfs
   container, backed up as ciphertext". The implemented layout is `/srv/atlas/vault/cipher` (the container) and
   `/srv/atlas/vault/open` (the plaintext mount point); the gate's throw-away test vault moved OUT of that tree to
   `/srv/atlas/staging/vault-test-cipher` (fix round 4), so the tree Appendix C says is backed up holds nothing that is
   not. `phase2/07-restic.sh` includes exactly `vault/cipher` and excludes `vault/open` and all of `staging`;
   `atlas.vault.DEFAULT_MOUNT_DIR` is `vault/open`; `atlas-aegis.service` runs with `--one-file-system`. Requested
   wording for Section 11 and Appendix C: "`/srv/atlas/vault/cipher` — gocryptfs container, backed up as ciphertext;
   `/srv/atlas/vault/open` — plaintext view, never backed up"; and for CONVENTIONS.md §2 the same under the
   `/srv/atlas/{...,vault,...}` row plus `/srv/atlas/staging/vault-test-cipher` (atlas:atlas 700, the gate's test vault,
   excluded). 07's `ensure_dir .../vault/test-cipher` and its exclude line for that path are now harmless leftovers it
   may drop.
2. **Step ids 06d and 09b.** Section 17 and CONVENTIONS.md §1 name neither a sandbox nor a vault step; `06d-sandbox.sh`
   and `09b-vault.sh` are gap-fills under markers `phase2.06d` and `phase2.09b` (their headers say so). Both are
   BASELINE AMENDMENTS the Principal's `--force 06d` / `--force 09b` commands depend on: CONVENTIONS.md §1 must list
   `06d-sandbox` and `09b-vault` in the phase2 list, and Section 17 Phase 2 needs "6d. AEGIS sandbox image (16.4):
   `atlas-sandbox:py3.12` from `docker/sandbox/Dockerfile`, SANDBOX_* settings for the orchestrator; V17 at the gate"
   between 6c and 7 and "9b. gocryptfs vault: helper, unit, sudoers, cipher dir (Section 11, D13; V18 at the gate)"
   between 9 and 10.
3. **No fourth pause (resolved on this side).** Real-vault initialisation is opt-in (`ATLAS_VAULT_INIT=1` from a
   console, §1), so CONVENTIONS.md §7.6 stays at three pauses; `phase2-services.sh`'s header already says so. Contract
   requested of `lib/common.sh`: `parse_common_args` exporting `ATLAS_FORCED_STEPS` (space-separated step ids given to
   `--force`), which 9b honours like `ATLAS_VAULT_INIT=1` when a terminal is present (not implemented there yet; 9b
   reads it defensively).
4. *(resolved)* `admin.py` accepts `atlas-admin vault-session-test --file PATH` and calls
   `vault_session_test(args.idle_seconds, file=args.file)`; V18 uses it and keeps only a `--help` probe with the module
   fallback for an older installed copy.
5. **Sandbox run line (resolved except the disk bound).** `atlas.sandbox.build_argv` produces the line in §1 (`--init`,
   `--pull never`, `--user 65534:<atlas gid>`, `-e SANDBOX_TIMEOUT_S`, `--ulimit fsize`, the noexec/nosuid/nodev tmpfs,
   host timeout `+10 s`, refusal of a network grant while `atlas-docker-egress.service` is inactive); the Dockerfile
   header, §1, 06d's smoke run and v17 now carry `--pull never` and the atlas run gid too, so the copies agree. Still
   open, the package writer's and the Principal's call: a bound on the total size of `/work` (XFS project quota on
   `$SANDBOX_DIR`, or a `tmpfs-size` mount with `docker cp` staging; §1 "Disk").
6. **Control path: a BASELINE CONTRADICTION, not a documentation request.** CONVENTIONS.md §8 says the sudoers fragment
   `atlas-engines` "allows exactly those commands and nothing else" and Section 23 S21 says the orchestrator's control
   path is "a NOPASSWD sudoers fragment with exactly three `systemctl` verbs". The node also carries
   `/etc/sudoers.d/atlas-vault` (exactly `atlas-vault open|lock|status`, §1), which contradicts both literally; two other
   writers code against the literal (`02-orchestrator.sh` "atlas-engines stays the ONLY NOPASSWD grant";
   `07-restic.sh` removes its own fragment and runs a negative sudo test, which probes atlas-aegis verbs only, so the two
   fragments do not collide at run time). Why the fragment exists: the button is pressed by the orchestrator (user atlas,
   `ProtectSystem=full`), the mount must land in the HOST namespace so Celery and the session reader see it, the passfile
   must stay root-only, and atlas-engines' explicit lines name only `llama-server@<key>` — without this grant the
   orchestrator has no root path to start the unit at all. The decision is the baseline writer's: (a) amend §8 and S21 to
   "`atlas-engines` (`systemctl start|stop|restart llama-server@<key>`) and `atlas-vault` (`atlas-vault
   open|lock|status`)" and have 02-orchestrator.sh and 07-restic.sh cite both, or (b) keep the literal and give the vault
   another root path (an `atlas-vault.service` line in atlas-engines plus a credential delivery that is not an
   atlas-written file). (a) is the implemented state and what `atlas.vault.VaultController` codes against.
7. **Gate does setup — RESOLVED (fix round 4).** The sandbox image build, `/srv/atlas/sandbox`, the `SANDBOX_*` keys and
   the one orchestrator restart live in `phase2/06d-sandbox.sh` (a new file of this writer, picked up by
   `run_phase_steps`' `NN-*.sh` glob between 06c and 07); `10-gate.sh` only reads the image labels and the keys. Needs
   item 2's amendments.
8. **V10's Phase 2 half.** The gate records `verify/v10a-router-resident.sh`'s line as **`V10 info`** ("Phase 2 half
   (resident router, gate) pass|FAIL: ..."), as CONVENTIONS §4 (ids V1..V23, halves only V3a/b and V14a/b) and
   `tools/fill-workbook.py` (an undeclared `V10a` is dropped from the workbook silently; an `info` row shows as "Not yet
   run — info: ...") require, and only while verify.jsonl holds no Phase 3 `V10` record, so a Phase 2 re-run can never
   shadow the load test's verdict (gate() takes the latest record per id and treats `info` as non-blocking). A failing
   router also marks the gate unhealthy. `phase2/04-memory.sh`, `phase2-services.sh`'s header and
   `verify/v10a-router-resident.sh`'s header still record/describe the id `V10a` and ask the CONVENTIONS writer to declare
   it; either they return to `V10 info` with the same Phase 3 guard, or CONVENTIONS §4/§6 and fill-workbook HALVES
   declare `V10a`. Until decided both ids exist (harmless: the gate lists `-- V7 V10`).
9. **Pins** are listed at the top of this file (rule §7.9). `pytest==9.1.1` is pinned in `pyproject.toml` (resolved pin by
   the package writer); the README.md writer is asked to copy the table.
10. **Secrets directory mode.** 9b leaves `$ATLAS_ETC/secrets` at root:atlas 710, the one value the Phase 2 writers now
    share (`phase2-services.sh`, 02 `ORCH_SECRETS_MODE=710`, 03, 06c, 07, 08, 09). The fix-round-4 request for 750 "like
    every other writer" rests on a stale premise (round-3 files moved to 710; only `phase1/02,03,07` still write 750,
    and both modes let atlas traverse). CONVENTIONS.md §2's row (`root:root 700`, which cannot hold beside its own
    atlas:atlas entries inside the directory) must read `root:atlas 710 (traverse only; every file inside is 600 owned by
    its one reader)`; `phase1/02-luks.sh`, `03-mounts.sh` and `07-remote.sh` are asked to adopt 710. 9b warns and the gate
    records NOT HEALTHY when any file inside is readable beyond its owner.
11. **§7.2 and the tmpfs passfile.** `/run/atlas-vault/pass` (root:root 600, tmpfs, shredded on mount) is the declared
    exception to "secrets live only under /etc/atlas/secrets/"; §7.2 must admit "the transient tmpfs passfile
    `/run/atlas-vault/pass`, root:root 600, shredded the moment the mount is up". The file-less alternative
    (`systemd-run --pipe --unit atlas-vault ... gocryptfs -fg ...` with the passphrase on the helper's stdin) was evaluated
    and rejected: the transient unit would inherit the orchestrator's stdout/stderr pipes for the whole mount lifetime
    (POST /vault/open would block until the idle lock) and `SetCredential=`/`StandardInputData=` would put the passphrase
    in unit properties readable through `systemctl show`. `LoadCredential=` would still need the same file.
12. **§5 verify list.** `v14a-arbiter-stubs.sh`, `v15-approval-gate.sh` and `v16-router-hard-rule.sh` exist as
    standalone scripts (run through `run_verify`, so the records are identical to a direct `record_v`); §5's file list
    should name them, or its sentence "V14, V15, V16 are produced inside their phase" should say "through the three
    verify scripts above".
13. **§8 bind rule, recorded relaxation (fix round 4).** §8 says everything except WireGuard UDP 51820 "binds to loopback,
    LAN and WireGuard addresses only"; the gate accepts exactly four host daemons on `0.0.0.0`/`[::]` behind an active
    default-deny ufw — sshd/ssh.socket (22), cockpit.socket (9090), xrdp (3389) and Open WebUI on the host network
    (`$OPENWEBUI_PORT`, `docker/core/compose.yml`) — because their owning steps ship them that way, and marks any other
    wildcard listener NOT HEALTHY. §8 should either say so ("host daemons that cannot bind per address — sshd, cockpit-ws,
    xrdp, Open WebUI on the host network — may listen on 0.0.0.0 behind ufw default-deny; squid additionally on the
    docker0 gateway") or the owning steps bind by address (sshd `ListenAddress`, `cockpit.socket` `ListenStream=`, xrdp
    `address=`, Open WebUI `HOST=$LAN_IP`), after which the accepted set shrinks to nothing.
14. **ruff on files outside this set.** `ruff check orchestrator/` reports E501 (line too long) in
    `orchestrator/src/atlas/arbiter.py` and `orchestrator/src/atlas/config.py`; rule §7.8 requires ruff-clean, the owner
    of those files should wrap the lines. Nothing in this writer's files is affected.
