# Phase 2 gate, vault and sandbox: cross-writer contracts

Written by the writer of `phase2/09b-vault.sh`, `phase2/10-gate.sh`, `docker/sandbox/Dockerfile`,
`verify/v14a-arbiter-stubs.sh`, `verify/v15-approval-gate.sh`, `verify/v16-router-hard-rule.sh`,
`verify/v17-sandbox.sh` and `verify/v18-vault.sh`. Everything below is something one of those files relies on that
`CONVENTIONS.md` does not state, or something they define for another writer. Where the other side does not yet meet
a contract, §3 says so; each script fails loudly with the contract named when it is not met.

## 1. Orchestrator package (`orchestrator/`, installed at `/opt/atlas/orchestrator` into `/opt/atlas/venv`)

### Unit tests (V14a, V15, V16)

| Verify script | Runs | Claim the tests must prove |
|---|---|---|
| `verify/v14a-arbiter-stubs.sh` | `/opt/atlas/venv/bin/python -m pytest -p no:cacheprovider tests/test_arbiter.py` | Engine Arbiter refuses an over-budget load and downgrades a Deep Think depth against stub footprints (4.2, 9.1) |
| `verify/v15-approval-gate.sh` | `... tests/test_approval.py` | approval gate holds a standard-tier email until approved; routine-tier auto-sends and logs (16.2) |
| `verify/v16-router-hard-rule.sh` | `... tests/test_router.py` | a "medical" message routes to Arthur even when the (stubbed) classifier disagrees, and the decision is logged (7.2) |

* Working directory is the package directory (`/opt/atlas/orchestrator` when the file exists there, else the
  mirrored `/opt/atlas/day1/orchestrator`); the scripts run as `atlas` when that directory is atlas-owned.
* `/etc/atlas/orchestrator.env` is exported before pytest (so settings read at import time exist); the tests must not
  need any live service (CONVENTIONS.md §7.8: stubs for llama-server, Redis, Docker).
* Pass = pytest exit 0. Exit 5 (no tests collected) is a fail. The one-line evidence is the pytest summary line
  (`N passed in Xs`): `pyproject.toml` sets `addopts = "-q"`, so the scripts pass no `-q` of their own (a second one
  would silence that line).
* `pytest` is declared by `pyproject.toml` as the `dev` extra (`pytest>=8`, `ruff>=0.6`); when it is missing from the
  venv, `phase2/10-gate.sh` runs `pip install -e /opt/atlas/orchestrator[dev]` (pip's version self-check off) and
  otherwise lets V14a/V15/V16 record the failure. Nothing ad hoc is installed.

### Vault (Section 11, D13, 10.5; V18)

Installed by `phase2/09b-vault.sh`:

| Item | Contract |
|---|---|
| `/etc/atlas/vault.env` (root:atlas 640) | `VAULT_CIPHER_DIR=/srv/atlas/vault/cipher`, `VAULT_MOUNT_DIR=/srv/atlas/vault/open`, `VAULT_TEST_CIPHER_DIR=/srv/atlas/vault/test-cipher`, `VAULT_IDLE=15m`, `VAULT_UNIT=atlas-vault.service`, `VAULT_HELPER=/usr/local/bin/atlas-vault`, `VAULT_RUN_DIR=/run/atlas-vault`, `VAULT_PASS_FILE=/run/atlas-vault/pass`, `VAULT_OVERRIDE_FILE=/run/atlas-vault/override.env`, `VAULT_USER=atlas` |
| `/etc/atlas/orchestrator.env` | gains `VAULT_CIPHER_DIR`, `VAULT_MOUNT_DIR`, `VAULT_IDLE`, `VAULT_UNIT`, `VAULT_HELPER` (ensure_kv; step 9b restarts `atlas-orchestrator` once, only when the file's content changed) |
| `/run/atlas-vault/` (root:root 755, tmpfs) | the **only** transient home of the passphrase: `pass` (root:atlas 640, one line, created with mktemp + rename, symlinks refused, shredded by the unit's `wait-mounted` the moment the mount is up and by `cleanup`/`lock` otherwise) and `override.env` (root:root 644, written by root verification runs only, one open, expires after 600 s). Declared exception to CONVENTIONS.md §7.2 "secrets only under /etc/atlas/secrets/": tmpfs only (the helper refuses when `findmnt` says otherwise), never `/run/atlas` (atlas-writable: a root helper operating by name there would be a symlink-planting root escalation) |
| `/usr/local/bin/atlas-vault open` | passphrase on **stdin** (one line), never an argument; prints `open`; exit 0 when `VAULT_MOUNT_DIR` is mounted, 1 when refused (gocryptfs exit 12 = wrong passphrase), 2 on contract errors (including "not initialised", the state after a console-less step 9b) |
| `/usr/local/bin/atlas-vault lock` | prints `locked`; exit 0 when unmounted |
| `/usr/local/bin/atlas-vault status` | prints `open` or `locked`; exit 0 |
| `/usr/local/bin/atlas-vault override` | root only, not in sudoers: from `VAULT_IDLE_OVERRIDE` (may only **shorten** `VAULT_IDLE`, never 0) and/or `VAULT_CIPHER_OVERRIDE` (an initialised vault dir) writes `override.env` for the **next** open, whoever performs it, so `verify/v18-vault.sh` opens the test vault with a 20 s idle **through the button**; `init` and `override` also honour the variables directly |
| `/etc/sudoers.d/atlas-vault` | `atlas ALL=(root) NOPASSWD:` exactly `atlas-vault open`, `atlas-vault lock`, `atlas-vault status` (sudo-rs, plain syntax, no SETENV: the button path can never carry an override variable). CONVENTIONS.md §8 names only `atlas-engines` as the control path; this fragment must be listed beside it (§3 item 6) |
| `atlas-vault.service` | `gocryptfs -fg -q -nosyslog -idle ${VAULT_IDLE} -passfile /run/atlas-vault/pass ${VAULT_CIPHER_DIR} ${VAULT_MOUNT_DIR}` as user `atlas` in the **host** mount namespace, `EnvironmentFile=vault.env` then `-override.env`; `ExecStartPost=+atlas-vault wait-mounted`, `ExecStopPost=-+atlas-vault cleanup` (full privileges: only root may touch the run dir); active exactly while the vault is open; auto-exits on the idle unmount |

Interactive pause (declared here because CONVENTIONS.md §7.6 lists three and this is the fourth; §7.6 must gain it):
step 9b reads the vault passphrase from `/dev/tty` (twice on first initialisation, once on a re-run) and, on first
initialisation, waits for `WRITTEN DOWN` after showing the gocryptfs master key once. **Without a terminal the step
does not stop the phase**: the real vault's initialisation is deferred (`/var/lib/atlas/day1/vault-init-pending`),
the test vault is initialised and proven, the gate runs V18 on it and prints the console command
(`sudo ./atlas-day1.sh phase2 --force 09b`); the button answers "not initialised" (exit 2) until then.

What the package must implement:

* `POST /vault/open` (body `{"passphrase": "..."}` from the interface button, never from a chat message): pipes the
  passphrase into `sudo -n /usr/local/bin/atlas-vault open` (stdin), returns the helper's verdict as
  `{"ok": true|false, "state": "open"|"locked", "message": ...}` with HTTP 200 when ok (403 when refused). The
  passphrase is not logged, not stored, not passed to a model. V18 opens through this endpoint in both modes.
* `POST /vault/lock`: `sudo -n /usr/local/bin/atlas-vault lock`.
* `GET /vault/status`: `{"state": "open"|"locked"}` computed from the service's **own** view (`/proc/self/mountinfo`
  field 5 == `VAULT_MOUNT_DIR`, or `atlas-vault status`). V18 checks it after opening and after the idle lock; a
  mount invisible inside the service's mount namespace fails V18. (`mountpoint -q` must not be used by any user other
  than `atlas`: the FUSE kernel driver denies even root without `-allow_other`, which is deliberately not set so restic,
  root, never sees plaintext.)
* Session tagging (10.5): anything read from under `VAULT_MOUNT_DIR` is tagged `vault` for the life of that session;
  vault-tagged content is not written to any ChromaDB collection, not to the LightRAG working dir, not summarised.
* `atlas-admin vault-session-test --file PATH`: reads PATH inside a vault-tagged session, then attempts a memory write
  through the normal write path **synchronously** (no queued Celery task still pending when it returns), closes the
  file (an open file keeps gocryptfs "not idle"), exits 0 and prints one JSON line, e.g.
  `{"read": true, "chars": 91, "vault_tagged": true, "memory_write_attempted": true, "memory_write_suppressed": true}`.
  V18 does not trust the JSON verdict: it queries every ChromaDB collection and greps the graph store afterwards.
  Until `admin.py` accepts `--file` (§3 item 4), V18 probes `--help` and falls back to
  `python -m atlas.vault session-test --file PATH`, naming the gap in its evidence line.

### Sandbox (Section 16.4; V17)

Written into `/etc/atlas/orchestrator.env` by `phase2/10-gate.sh` (the gate builds the image because no earlier step
owns it; a build failure is a warning there and a V17 fail): `SANDBOX_IMAGE=atlas-sandbox:py3.12`,
`SANDBOX_DIR=/srv/atlas/sandbox`, `SANDBOX_MEMORY=2g`, `SANDBOX_CPUS=2`, `SANDBOX_PIDS=256`, `SANDBOX_TMPFS_SIZE=512m`,
`SANDBOX_TIMEOUT_S=300`, `SANDBOX_FSIZE=1073741824`. The run line the package must use is in the header of
`docker/sandbox/Dockerfile`:

```
timeout -k 5 $((SANDBOX_TIMEOUT_S + 10)) docker run --rm --init --name sb-<job> \
  --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \
  --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=$SANDBOX_TMPFS_SIZE \
  --ulimit fsize=$SANDBOX_FSIZE --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
  -e SANDBOX_TIMEOUT_S=$SANDBOX_TIMEOUT_S \
  -v $SANDBOX_DIR/<job>:/work:rw -w /work atlas-sandbox:py3.12 python3 /work/main.py
docker rm -f sb-<job>      # always afterwards
```

* **The time bound is enforced inside the container.** The image's `ENTRYPOINT` wraps every command in coreutils
  `timeout -s KILL $SANDBOX_TIMEOUT_S` (default 300 when `-e` is absent), so a program that ignores SIGTERM is
  SIGKILLed at the deadline (exit 137; the package tells it from the OOM kill by the elapsed time). The host-side GNU
  timeout only signals the docker client and is a backstop 10 s later. V17 proves both the memory cap and this bound.
* **Disk:** `--ulimit fsize` bounds each file a job writes (1 GiB); `/tmp` is a bounded noexec tmpfs. UNVERIFIED /
  open: no cap on the total size of `$SANDBOX_DIR/<job>` on the 8 TB volume exists yet (a project quota is the durable
  fix, a Principal decision); the package removes the job directory once outputs are collected.
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
  variables). The orchestrator must therefore **refuse a network grant when `systemctl is-active
  atlas-docker-egress.service` is not `active`**; a dedicated `--network atlas-sandbox` bridge (so the egress rule can
  match the sandbox subnet alone) is optional.
* The image has no pip, curl or wget; the process runs as nobody on a read-only root.

## 2. Files and names from other writers that these scripts use

| Used by | Contract | Owner |
|---|---|---|
| 10-gate, v18 | `/etc/atlas/orchestrator.env` written with `ensure_kv` (foreign keys kept); keys `ORCH_PORT`, `ATLAS_DB_PATH`, ... | `phase2/02-orchestrator.sh` |
| 10-gate, v18 | units `atlas-orchestrator`, `atlas-celery-cpu`, `atlas-celery-gpu`, `atlas-celery-beat`; `GET /health` -> 200; `POST /vault/open`, `GET /vault/status` as in §1 | `phase2/02-orchestrator.sh`, `systemd/atlas-orchestrator.service`, the package |
| 10-gate, v18 | `/opt/atlas/venv/bin/{python,atlas-admin}`; the package at `/opt/atlas/orchestrator` with `pyproject.toml` (`dev` extra) | `phase2/02-orchestrator.sh` |
| 10-gate | `llama-server@router-qwen3.5-4b`, `llama-server@embed-bge-m3`, `llama-server@rerank-bge-v2-m3`; `/etc/atlas/engines/<key>.env` with `LLAMA_ARG_PORT`; fallback `LLAMA_PORT_BASE` + index in `config/engines.json` | `phase2/01-llama.sh`, `phase2/04-memory.sh`, `phase2/engine-env.py` |
| v18 | `/etc/atlas/memory.env`: `CHROMA_URL`, `CHROMA_COLLECTIONS` (quoted, space-separated), `LIGHTRAG_WORKING_DIR`; the six Section 10.1 collections exist; Chroma v2 REST `.../collections` (list) and `.../collections/{id}/get` (query) under `default_tenant/default_database` | `phase2/04-memory.sh`, `docker/core/compose.yml` |
| 10-gate | containers `atlas-redis`, `atlas-chromadb`, `atlas-openwebui` (host network) from `compose.yml`; `atlas-kokoro`, `atlas-speaches`, `atlas-docling` from `compose.voice.yml` (the bare `kokoro`/`speaches`/`docling` names of an earlier revision are still accepted); HTTP: ChromaDB `127.0.0.1:8000/api/v2/heartbeat`, Kokoro `8880/health`, speaches `8881/health`, docling `5001/docs`, Open WebUI `$OPENWEBUI_PORT/health` | `docker/core/compose.yml`, `docker/core/compose.voice.yml`, `phase2/03-openwebui.sh`, `phase2/05-voice.sh` |
| 10-gate | `atlas-ntfy` on `127.0.0.1:8090/v1/health`; `wg-easy`; units `docker`, `squid`, `cockpit.socket`, `xrdp`, `ssh.service`/`ssh.socket`, `atlas-ddns.timer`, `atlas-docker-egress.service`; ufw active with default-deny incoming (the bind-rule check tolerates wildcard listeners only behind it) | Phase 1 steps 4, 5b, 6, 7 |
| 10-gate, 9b | `atlas-aegis.timer`, `atlas-restic-check.timer`, `$ATLAS_ETC/restic-exclude.txt` (9b appends the mount point and the test vault with `ensure_line`; the gate asserts the exclusion before V13); `atlas-sentinel.timer`, `atlas-prune.timer`; `srv-atlas-winpc.automount` (only when installed; step 9 may opt out) | `phase2/07-restic.sh`, `phase2/08-sentinel.sh`, `phase2/09-windows-share.sh` |
| 10-gate | verify usage lines: `v12-openwebui-offline.sh CONTAINER WINDOW_S` (called with `atlas-openwebui 120`); `v06`, `v07`, `v13`, `v20`, `v23`, `v03b`, `v10a` with their defaults | the respective writers |
| 10-gate | `/etc/atlas/docker.env` (`CONTAINER_HTTP(S)_PROXY`, `LAN_IP`), `/etc/atlas/core.env`, `/etc/atlas/voice.env` as compose `--env-file`s (used only for `docker compose ps`, each only when present) | Phase 1 step 6, `phase2/02-orchestrator.sh`, `phase2/05-voice.sh` |
| 9b | apt `gocryptfs 2.6.1-1` and `fuse3` (VERIFIED versions, services-tools.md §4.11); `config/allowlist.txt` covers the Ubuntu archive and the Docker Hub hosts for `python:3.12-slim` (`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`); `/run` is tmpfs (systemd default; 9b and the helper verify it) | Phase 1 step 4 (allowlist), `config/allowlist.txt` |

## 3. Conflicts and gaps noticed for the other side (no change made to their files)

1. **Vault layout versus Section 11 / Appendix C wording.** The baseline names `/srv/atlas/vault` as "the gocryptfs
   container, backed up as ciphertext". The implemented layout is `/srv/atlas/vault/cipher` (the container),
   `/srv/atlas/vault/open` (the plaintext mount point) and `/srv/atlas/vault/test-cipher`; `phase2/07-restic.sh`
   includes exactly `vault/cipher` and excludes `vault/open` and `vault/test-cipher`, `atlas.vault.DEFAULT_MOUNT_DIR`
   is `vault/open`, and `atlas-aegis.service` runs with `--one-file-system`. So the ciphertext is what is backed up,
   the plaintext view is never in the include set (9b re-asserts the exclusion, the gate checks it before V13), and
   restic never `lstat`s the mount point. Changing the paths now would break the two files that already agree; kept.
2. **Step id.** Section 17 and CONVENTIONS.md §1 name no vault step; `09b-vault.sh` is a gap-fill under marker
   `phase2.09b` (its header says so). CONVENTIONS.md §1 should add `09b-vault` to the phase2 list with that note.
3. **Interactive pause.** CONVENTIONS.md §7.6 must list the vault passphrase / master-key pause (§1 above) as the
   fourth; `phase2-services.sh`'s header comment on pauses should name it too.
4. **`atlas-admin vault-session-test --file` is not accepted by `admin.py`** (its parser has only `--idle-seconds`;
   argparse exits 2). `admin.py` must add `v.add_argument("--file", required=True)` and call
   `vault_session_test(args.idle_seconds, args.file)`. Until then V18 falls back to
   `python -m atlas.vault session-test --file PATH` (which parses and runs) and says so in its evidence.
5. **Sandbox run line changed (fix round).** `atlas.sandbox.build_argv` and `tests/test_sandbox.py` still build the
   earlier line (`timeout -k 5 $SANDBOX_TIMEOUT_S docker run --rm --name ... --tmpfs /tmp:rw,size=...`). The package
   must add `--init`, `-e SANDBOX_TIMEOUT_S=...`, `--ulimit fsize=$SANDBOX_FSIZE`, the `noexec,nosuid,nodev` tmpfs
   options and the host timeout of `SANDBOX_TIMEOUT_S + 10`, and refuse a network grant while
   `atlas-docker-egress.service` is not active. Until it does, the image's entrypoint default (300 s) still bounds the
   job, so the change is additive; the old line keeps working.
6. **Control path.** CONVENTIONS.md §8 says the sudoers fragment `atlas-engines` "allows exactly those commands and
   nothing else"; the node also carries `/etc/sudoers.d/atlas-vault` (exactly `atlas-vault open|lock|status`, §1) and
   `phase2/07-restic.sh`'s `atlas-aegis`. §8 should list them.
7. **Gate does setup.** The sandbox image build and the `[dev]` extra install live in step 10 because no earlier step
   in this writer's set owns them; both are non-fatal there (the matching V records the fail). Moving them into step
   06 (Section 15.1/16.4) or the orchestrator install (`pip install -e "$ORCH_DIR[dev]"`) is the other writers' call.
8. **V10's Phase 2 half.** `verify/v10a-router-resident.sh` exists but `V10a` is not an id CONVENTIONS.md §4 declares
   and `tools/fill-workbook.py` HALVES knows only V3 and V14, so the gate records its result as `V10` with result
   `info` ("Phase 2 half ..."); the Phase 3 gate's V10 record supersedes it. Declaring `V10a`/`V10b` in §4/§6 and in
   HALVES would be the cleaner fix.
9. **Unpinned (rule §7.9; for `scripts/day1/README.md` when it is written):** `python:3.12-slim` (no research digest;
   `phase2/10-gate.sh` logs the resolved RepoDigest so the next revision can pin `FROM python:3.12-slim@sha256:...`;
   re-pull with `docker build --pull`). `pytest` is pinned only by the package's `pytest>=8` lower bound.
