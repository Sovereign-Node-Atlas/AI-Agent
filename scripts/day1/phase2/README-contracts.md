# Phase 2 gate, vault and sandbox: cross-writer contracts

Written by the writer of `phase2/09b-vault.sh`, `phase2/10-gate.sh`, `docker/sandbox/Dockerfile`,
`verify/v14a-arbiter-stubs.sh`, `verify/v15-approval-gate.sh`, `verify/v16-router-hard-rule.sh`,
`verify/v17-sandbox.sh` and `verify/v18-vault.sh`. Everything below is something one of those files relies on that
`CONVENTIONS.md` does not state, or something they define for another writer. Where the other side does not yet meet
a contract, §3 says so; each script fails loudly with the contract named when it is not met.

## Unpinned (rule §7.9; the Principal's record until `scripts/day1/README.md` exists and copies it)

| Item | State | Why |
|---|---|---|
| `python:3.12-slim` (sandbox base, `docker/sandbox/Dockerfile`) | **pinned by digest** `sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016` (label `org.atlas.sandbox.version=3`, `org.atlas.sandbox.base`) | UNVERIFIED pin: the research names no python image tag or digest; the digest is the multi-arch index Docker Hub served for `python:3.12-slim` on 2026-10-04, resolved by this writer from the registry API. A digest Docker Hub stops serving makes the gate's build fail loudly (V17 fail), never float. Re-pin per the Dockerfile header and bump the version label (the gate and v17 check it). |
| `pytest` (`orchestrator/pyproject.toml`: `pytest>=8`, a runtime dependency) | lower bound only | no research pin; installed by step 2's `pip install -e`, never by the gate; the package writer's call (§3 item 9) |
| `gocryptfs` | **checked against the VERIFIED pin** `2.6.1-1` (services-tools.md §4.11) | apt cannot be told to install an older archive version, so `09b-vault.sh` dies when the installed upstream version is not 2.6.1; override once the man-page facts are re-verified: `sudo env ATLAS_GOCRYPTFS_VERSION_OK=1 ./atlas-day1.sh phase2` |

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
* `pytest>=8` is a **runtime dependency** of the `atlas` package (`[project].dependencies` in `pyproject.toml`; the
  `dev` extra is ruff only), installed by `phase2/02-orchestrator.sh`'s `pip install -e $ORCH_DIR`. **The gate installs
  nothing**: when `python -m pytest --version` fails, `10-gate.sh` warns ("re-run Phase 2 step 2: `--force 02`") and
  V14a/V15/V16 record the failure with the same hint.

### Vault (Section 11, D13, 10.5; V18)

Installed by `phase2/09b-vault.sh`:

| Item | Contract |
|---|---|
| `/etc/atlas/vault.env` (root:atlas 640) | `VAULT_CIPHER_DIR=/srv/atlas/vault/cipher`, `VAULT_MOUNT_DIR=/srv/atlas/vault/open`, `VAULT_TEST_CIPHER_DIR=/srv/atlas/vault/test-cipher`, `VAULT_IDLE=15m`, `VAULT_UNIT=atlas-vault.service`, `VAULT_HELPER=/usr/local/bin/atlas-vault`, `VAULT_RUN_DIR=/run/atlas-vault`, `VAULT_PASS_FILE=/run/atlas-vault/pass`, `VAULT_OVERRIDE_FILE=/run/atlas-vault/override.env`, `VAULT_USER=atlas` |
| `/etc/atlas/orchestrator.env` | gains `VAULT_CIPHER_DIR`, `VAULT_MOUNT_DIR`, `VAULT_IDLE`, `VAULT_UNIT`, `VAULT_HELPER` (ensure_kv; step 9b restarts `atlas-orchestrator` once, only when the file's content changed) |
| `/etc/atlas/secrets/` | left **root:atlas 710** (traverse-only for atlas), the mode `phase2/06c-google-oauth.sh` fixes for every writer; 9b adds only the root-read `vault-test.pass` (root:root 600) |
| `/run/atlas-vault/` (root:root 755, tmpfs) | the **only** transient home of the passphrase: `pass` (**root:root 600**, one line, created with mktemp + rename, symlinks refused; no process running as or with group atlas can read it) and `override.env` (root:root 644, written by root verification runs only, one open, expires after 600 s). gocryptfs (user atlas) receives the passphrase on **stdin**: the unit has `StandardInput=file:/run/atlas-vault/pass` (systemd opens it as root) and `-passfile /dev/stdin`; `wait-mounted` shreds the file the moment the mount is up, `cleanup`/`lock` otherwise. Declared exception to CONVENTIONS.md §7.2 "secrets only under /etc/atlas/secrets/": tmpfs only (the helper refuses when `findmnt` says otherwise), never `/run/atlas` (atlas-writable: a root helper operating by name there would be a symlink-planting root escalation). §7.2 should admit it in those words (§3 item 10) |
| `/usr/local/bin/atlas-vault open` | passphrase on **stdin** (one line), never an argument; prints `open`; exit 0 when `VAULT_MOUNT_DIR` is mounted, 1 when refused (gocryptfs exit 12 = wrong passphrase), 2 on contract errors (including "not initialised", the state after a default, unattended step 9b) |
| `/usr/local/bin/atlas-vault lock` | prints `locked`; exit 0 when unmounted |
| `/usr/local/bin/atlas-vault status` | prints `open` or `locked`; exit 0; needs no privilege (reads `/proc/self/mountinfo`) |
| `/usr/local/bin/atlas-vault override` | root only, not in sudoers: from `VAULT_IDLE_OVERRIDE` (may only **shorten** `VAULT_IDLE`, never 0) and/or `VAULT_CIPHER_OVERRIDE` (an initialised vault dir) writes `override.env` for the **next** open, whoever performs it, so `verify/v18-vault.sh` opens the test vault with a 20 s idle **through the button**; `init` and `override` also honour the variables directly |
| `/etc/sudoers.d/atlas-vault` | `atlas ALL=(root) NOPASSWD:` exactly `atlas-vault open`, `atlas-vault lock`, `atlas-vault status` (sudo-rs, plain syntax, no SETENV: the button path can never carry an override variable). `status` stays although it needs no root because `atlas.vault.VaultController._argv()` runs every verb through `sudo -n`; dropping it would make the package's `status()` log a helper error on every locked check. CONVENTIONS.md §8 names only `atlas-engines` as the control path; this fragment must be listed beside it (§3 item 6) |
| `atlas-vault.service` | `gocryptfs -fg -q -nosyslog -idle ${VAULT_IDLE} -passfile /dev/stdin ${VAULT_CIPHER_DIR} ${VAULT_MOUNT_DIR}` as user `atlas` in the **host** mount namespace, `StandardInput=file:/run/atlas-vault/pass`, `EnvironmentFile=vault.env` then `-override.env`; `ExecStartPost=+atlas-vault wait-mounted`, `ExecStopPost=-+atlas-vault cleanup` (full privileges: only root may touch the run dir); active exactly while the vault is open; auto-exits on the idle unmount. UNVERIFIED: gocryptfs accepting `/dev/stdin` as `-passfile` (the man page says "file"; process substitution is the documented use); step 9b's mechanics proof exercises exactly this path on the test vault, so a wrong assumption dies at step 9b, not at the gate |
| `$VAULT_MOUNT_DIR/.atlas-selftest/` | the **only** path Day 1 automation ever creates or writes inside a vault (9b's mechanics file, V18's marker); both remove their file and the directory afterwards, and 9b locks the vault before dying on any failure after the open |

**No interactive pause by default.** CONVENTIONS.md §7.6 lists three pauses and this step is not one of them, so a
plain `sudo ./atlas-day1.sh phase2` never stops at 9b: the real vault is left uninitialised (flag
`/var/lib/atlas/day1/vault-init-pending`, warned, pushed by ntfy), the test vault is initialised and proven, the gate
runs V18 on it and prints the opt-in command. The Principal initialises (or re-proves) the real vault from a console with

```
sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b
```

(`env`, because sudo-rs's handling of `sudo VAR=value cmd` is UNVERIFIED; the entry script re-execs with `exec`, so the
variable reaches the step). Only then does 9b read the passphrase from `/dev/tty` (twice on first initialisation, once
on a re-run), show the gocryptfs master key once and wait for `WRITTEN DOWN`, prove the mechanics on the real vault and
record V18 (real cipher dir) with the passphrase piped in. `ATLAS_VAULT_INIT=1` without a terminal dies with that
message. The button answers "not initialised" (exit 2) until then. A driver that exports `ATLAS_FORCED_STEPS` naming
`09b` is honoured like `ATLAS_VAULT_INIT=1` (contract requested, §3 item 3).

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
  token) and sends it through a curl config file, never argv, and names a 401/403-with-token-detail as an auth failure.
  Without a token file the routes accept loopback callers (V18 is one).
* Session tagging (10.5): anything read from under `VAULT_MOUNT_DIR` is tagged `vault` for the life of that session;
  vault-tagged content is not written to any ChromaDB collection, not to the LightRAG working dir, not summarised.
* `atlas-admin vault-session-test --file PATH` (implemented in `admin.py`): reads PATH inside a vault-tagged session,
  then attempts a memory write through the normal write path **synchronously** (no queued Celery task still pending
  when it returns), closes the file (an open file keeps gocryptfs "not idle"), exits 0 and prints one JSON line, e.g.
  `{"read": true, "chars": 91, "vault_tagged": true, "memory_write_attempted": true, "memory_write_suppressed": true}`.
  V18 does not trust the JSON verdict: it queries every ChromaDB collection and greps the graph store afterwards. V18
  still probes `--help` and falls back to `python -m atlas.vault session-test --file PATH` for an older installed copy.

### Sandbox (Section 16.4; V17)

Written into `/etc/atlas/orchestrator.env` by `phase2/10-gate.sh` (the gate builds the image because no Section 17
step owns it; a build failure is a warning there and a V17 fail): `SANDBOX_IMAGE=atlas-sandbox:py3.12`,
`SANDBOX_DIR=/srv/atlas/sandbox`, `SANDBOX_MEMORY=2g`, `SANDBOX_CPUS=2`, `SANDBOX_PIDS=256`, `SANDBOX_TMPFS_SIZE=512m`,
`SANDBOX_TIMEOUT_S=300`, `SANDBOX_FSIZE=1073741824`. The run line the package uses (`atlas.sandbox.build_argv`), as the
header of `docker/sandbox/Dockerfile` states it:

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
* **The time bound is enforced inside the container.** The image's `ENTRYPOINT` wraps every command in coreutils
  `timeout -s KILL $SANDBOX_TIMEOUT_S` (default 300 when `-e` is absent), so a program that ignores SIGTERM is
  SIGKILLed at the deadline (exit 137; the package tells it from the OOM kill by the elapsed time). The host-side GNU
  timeout only signals the docker client and is a backstop 10 s later. V17 proves both the memory cap and this bound,
  and requires label `org.atlas.sandbox.version=3` (digest-pinned base).
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
| 10-gate, v18 | `/etc/atlas/orchestrator.env` written with `ensure_kv` (foreign keys kept); keys `ORCH_PORT`, `ATLAS_DB_PATH`, optional `ORCH_ADMIN_TOKEN_FILE` (a root-readable file under `/etc/atlas/secrets`) ... | `phase2/02-orchestrator.sh` |
| 10-gate, v18 | units `atlas-orchestrator`, `atlas-celery-cpu`, `atlas-celery-gpu`, `atlas-celery-beat`; `GET /health` -> 200; `POST /vault/open`, `GET /vault/status` as in §1 | `phase2/02-orchestrator.sh`, `systemd/atlas-orchestrator.service`, the package |
| 10-gate, v14a/v15/v16, v18 | `/opt/atlas/venv/bin/{python,atlas-admin}` with pytest importable (runtime dependency); the package at `/opt/atlas/orchestrator` (`root:atlas`, read-only for atlas) with `pyproject.toml` | `phase2/02-orchestrator.sh` |
| 10-gate | `llama-server@router-qwen3.5-4b`, `llama-server@embed-bge-m3`, `llama-server@rerank-bge-v2-m3`; `/etc/atlas/engines/<key>.env` with `LLAMA_ARG_PORT`; fallback `LLAMA_PORT_BASE` + index in `config/engines.json` | `phase2/01-llama.sh`, `phase2/04-memory.sh`, `phase2/engine-env.py` |
| v18 | `/etc/atlas/memory.env`: `CHROMA_URL`, `CHROMA_COLLECTIONS` (quoted, space-separated), `LIGHTRAG_WORKING_DIR`; the six Section 10.1 collections exist; Chroma v2 REST `.../collections` (list) and `.../collections/{id}/get` (query) under `default_tenant/default_database` | `phase2/04-memory.sh`, `docker/core/compose.yml` |
| 10-gate | containers `atlas-redis` (redis-server `--requirepass "$REDIS_PASSWORD"` from its env_file `/etc/atlas/secrets/redis.env`; the gate PINGs with `REDISCLI_AUTH` inside the container and requires the unauthenticated PING to answer NOAUTH, exactly step 2's probe), `atlas-chromadb`, `atlas-openwebui` (host network) from `compose.yml`; `atlas-kokoro`, `atlas-speaches`, `atlas-docling` from `compose.voice.yml` (the bare `kokoro`/`speaches`/`docling` names of an earlier revision are still accepted); HTTP: ChromaDB `127.0.0.1:8000/api/v2/heartbeat`, Kokoro `8880/health`, speaches `8881/health`, docling `5001/docs`, Open WebUI `$OPENWEBUI_PORT/health` | `docker/core/compose.yml`, `docker/core/compose.voice.yml`, `phase2/03-openwebui.sh`, `phase2/05-voice.sh` |
| 10-gate | `atlas-ntfy` on `127.0.0.1:8090/v1/health`; `wg-easy`; units `docker`, `squid`, `cockpit.socket`, `xrdp`, `ssh.service`/`ssh.socket`, `atlas-ddns.timer`, `atlas-docker-egress.service` | Phase 1 steps 4, 5b, 6, 7 |
| 10-gate (bind rule) | squid binds `127.0.0.1:3128` **plus the docker0 gateway** (`http_port <DOCKER_GW>:3128`, `config/squid.conf.tmpl`, re-rendered by step 6; `DOCKER_GW` and `LAN_IP` in `/etc/atlas/docker.env`), fenced by ufw: active, `Default: deny (incoming)`, and the rule `3128/tcp on $LAN_IFACE DENY IN` (`phase1/04-system.sh`). The gate accepts the gateway (or a wildcard) listener only behind that fence, never squid on the LAN address; Redis 6379 loopback only; a Docker publish on `0.0.0.0`/`[::]` (docker-proxy listener or `docker ps` ports) is unhealthy whatever ufw says, because publishes bypass ufw INPUT; other wildcard host listeners are tolerated behind an active default-deny ufw | Phase 1 steps 4 and 6, `config/squid.conf.tmpl`, every compose file |
| 10-gate, 9b | `atlas-aegis.timer`, `atlas-restic-check.timer`, `$ATLAS_ETC/restic-exclude.txt` (9b appends the mount point and the test vault with `ensure_line`; the gate asserts the exclusion before V13); `atlas-sentinel.timer`, `atlas-prune.timer`; `srv-atlas-winpc.automount` (only when installed; step 9 may opt out) | `phase2/07-restic.sh`, `phase2/08-sentinel.sh`, `phase2/09-windows-share.sh` |
| 10-gate | verify usage lines: `v12-openwebui-offline.sh CONTAINER WINDOW_S` (called with `atlas-openwebui 120`); `v06`, `v07`, `v13`, `v20`, `v23`, `v03b`, `v10a-router-resident.sh` (recorded as `V10a`) with their defaults | the respective writers |
| 10-gate | `/etc/atlas/docker.env` (`CONTAINER_HTTP(S)_PROXY`, `LAN_IP`, `DOCKER_GW`), `/etc/atlas/core.env`, `/etc/atlas/voice.env` as compose `--env-file`s (used only for `docker compose ps`, each only when present) | Phase 1 step 6, `phase2/02-orchestrator.sh`, `phase2/05-voice.sh` |
| 9b | apt `gocryptfs 2.6.1-1` and `fuse3` (VERIFIED versions, services-tools.md §4.11); `config/allowlist.txt` covers the Ubuntu archive and the Docker Hub hosts for the pinned `python:3.12-slim` (`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`); `/run` is tmpfs (systemd default; 9b and the helper verify it); `$ATLAS_ETC/secrets` is root:atlas 710 (06c's contract) | Phase 1 step 4 (allowlist), `config/allowlist.txt`, `phase2/06c-google-oauth.sh` |

## 3. Conflicts and gaps noticed for the other side (no change made to their files)

1. **Vault layout versus Section 11 / Appendix C wording.** The baseline names `/srv/atlas/vault` as "the gocryptfs
   container, backed up as ciphertext". The implemented layout is `/srv/atlas/vault/cipher` (the container),
   `/srv/atlas/vault/open` (the plaintext mount point) and `/srv/atlas/vault/test-cipher`; `phase2/07-restic.sh`
   includes exactly `vault/cipher` and excludes `vault/open` and `vault/test-cipher`, `atlas.vault.DEFAULT_MOUNT_DIR`
   is `vault/open`, and `atlas-aegis.service` runs with `--one-file-system`. Requested wording for Section 11 and
   Appendix C: "`/srv/atlas/vault/cipher` — gocryptfs container, backed up as ciphertext; `/srv/atlas/vault/open` —
   plaintext view, never backed up; `/srv/atlas/vault/test-cipher` — the gate's throw-away vault, excluded"; and for
   CONVENTIONS.md §2 the same under the `/srv/atlas/{...,vault,...}` row. Kept as implemented (two files already agree).
2. **Step id.** Section 17 and CONVENTIONS.md §1 name no vault step; `09b-vault.sh` is a gap-fill under marker
   `phase2.09b` (its header says so). CONVENTIONS.md §1 should add `09b-vault` to the phase2 list with that note.
3. **No fourth pause (resolved on this side).** Real-vault initialisation is now opt-in (`ATLAS_VAULT_INIT=1` from a
   console, §1), so CONVENTIONS.md §7.6 stays at three pauses and `phase2-services.sh`'s header, which still lists "the
   vault passphrase display in step 9b" as a third pause, should instead say that step 9b runs unattended and that the
   real vault is initialised with `sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b`. Contract requested
   of `lib/common.sh`: `parse_common_args` exporting `ATLAS_FORCED_STEPS` (space-separated step ids given to `--force`),
   which 9b honours like `ATLAS_VAULT_INIT=1` when a terminal is present.
4. *(resolved)* `admin.py` accepts `atlas-admin vault-session-test --file PATH` and calls
   `vault_session_test(args.idle_seconds, file=args.file)`; V18 uses it and keeps only a `--help` probe with the module
   fallback for an older installed copy.
5. **Sandbox run line (resolved except the disk bound).** `atlas.sandbox.build_argv` produces the line in §1 (`--init`,
   `--pull never`, `--user 65534:<atlas gid>`, `-e SANDBOX_TIMEOUT_S`, `--ulimit fsize`, the noexec/nosuid/nodev tmpfs,
   host timeout `+10 s`, refusal of a network grant while `atlas-docker-egress.service` is inactive); the Dockerfile
   header and §1 now carry `--pull never` and the run gid too, so the three copies agree. Still open, the package
   writer's and the Principal's call: a bound on the total size of `/work` (XFS project quota on `$SANDBOX_DIR`, or a
   `tmpfs-size` mount with `docker cp` staging; §1 "Disk").
6. **Control path.** CONVENTIONS.md §8 says the sudoers fragment `atlas-engines` "allows exactly those commands and
   nothing else"; the node also carries `/etc/sudoers.d/atlas-vault` (exactly `atlas-vault open|lock|status`, §1).
   `phase2/07-restic.sh` has since removed its `atlas-aegis` fragment (path-unit trigger instead). §8 and Section 23 S21
   should list `atlas-vault` beside `atlas-engines` as the complete control path.
7. **Gate does setup (open).** The sandbox image build and the `SANDBOX_*` keys live in step 10 because no Section 17
   step owns them; the build is non-fatal there (V17 records the fail) and idempotent (skipped when the labelled image
   exists). A `06d-sandbox.sh` gap-fill step with its own marker, mirroring 9b, would be the cleaner home and would stop
   V17 from being judged against an artefact the gate itself just produced; it is a new file outside this writer's set
   and `run_phase_steps` would pick it up automatically (it globs `NN-*.sh`). Requested of whoever owns step 06.
8. **V10's Phase 2 half (resolved on this side).** The gate records `verify/v10a-router-resident.sh` under its own id
   `V10a` (`record_v` accepts `V[0-9]+[a-z]?`) and lists it as recorded-only (`-- V7 V10a`); no `V10` record is ever
   written from Phase 2, so a Phase 3 gate that fails to record V10 cannot find a stale `info` row. CONVENTIONS.md §4
   (ids) and §6 (Phase 2 "recorded, not blocking": `V7, V10a`) and `tools/fill-workbook.py` HALVES
   (`"V10": ("V10a", "V10b")`, or `V10a` as its own evidence column) should declare it; `phase2-services.sh`'s header
   still says `V10 info` and should be updated.
9. **Unpinned items** are listed at the top of this file (rule §7.9). Requests: the package writer to decide a pytest
   pin in `pyproject.toml`; the README.md writer to copy the table.
10. **Secrets directory mode (blocker fixed here).** 9b now leaves `$ATLAS_ETC/secrets` at root:atlas 710 (06c's
    contract). CONVENTIONS.md §2's row (`root:root 700`) must read `root:atlas 710`, and `phase2/02,03,07,08,09` still
    `ensure_dir ... root:atlas 750` (listable by atlas); 06c's header asks them to use 710 too.
11. **§7.2 and the tmpfs passfile.** `/run/atlas-vault/pass` (root:root 600, tmpfs, shredded on mount) is the declared
    exception to "secrets live only under /etc/atlas/secrets/"; §7.2 should admit it in those words.
12. **§5 verify list.** `v14a-arbiter-stubs.sh`, `v15-approval-gate.sh` and `v16-router-hard-rule.sh` exist as
    standalone scripts (run through `run_verify`, so the records are identical to a direct `record_v`); §5's file list
    should name them, or its sentence "V14, V15, V16 are produced inside their phase" should say "through the three
    verify scripts above".
