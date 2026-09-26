# Phase 2 gate, vault and sandbox: cross-writer contracts

Written by the writer of `phase2/09b-vault.sh`, `phase2/10-gate.sh`, `docker/sandbox/Dockerfile`,
`verify/v14a-arbiter-stubs.sh`, `verify/v15-approval-gate.sh`, `verify/v16-router-hard-rule.sh`,
`verify/v17-sandbox.sh` and `verify/v18-vault.sh`. Everything below is something one of those files relies on that
`CONVENTIONS.md` does not state, or something they define for another writer. Where the other side is not yet
written (the `atlas` package), the contract is the specification that side must meet; each script fails loudly with
the contract named when it is not met.

## 1. Orchestrator package (`orchestrator/`, installed at `/opt/atlas/orchestrator` into `/opt/atlas/venv`)

### Unit tests (V14a, V15, V16)

| Verify script | Runs | Claim the tests must prove |
|---|---|---|
| `verify/v14a-arbiter-stubs.sh` | `/opt/atlas/venv/bin/python -m pytest -q -p no:cacheprovider tests/test_arbiter.py` | Engine Arbiter refuses an over-budget load and downgrades a Deep Think depth against stub footprints (4.2, 9.1) |
| `verify/v15-approval-gate.sh` | `... tests/test_approval.py` | approval gate holds a standard-tier email until approved; routine-tier auto-sends and logs (16.2) |
| `verify/v16-router-hard-rule.sh` | `... tests/test_router.py` | a "medical" message routes to Arthur even when the (stubbed) classifier disagrees, and the decision is logged (7.2) |

* Working directory is the package directory (`/opt/atlas/orchestrator` when the file exists there, else the
  mirrored `/opt/atlas/day1/orchestrator`); the scripts run as `atlas` when that directory is atlas-owned.
* `/etc/atlas/orchestrator.env` is exported before pytest (so settings read at import time exist); the tests must not
  need any live service (CONVENTIONS.md §7.8: stubs for llama-server, Redis, Docker).
* Pass = pytest exit 0. Exit 5 (no tests collected) is a fail. The one-line evidence is the pytest summary line.
* `pytest` should be declared by `pyproject.toml` (a test extra); when it is missing, `phase2/10-gate.sh` installs it
  **unpinned** into the venv (the research names no pytest version) and logs a warning.

### Vault (Section 11, D13, 10.5; V18)

Installed by `phase2/09b-vault.sh`:

| Item | Contract |
|---|---|
| `/etc/atlas/vault.env` (root 644) | `VAULT_CIPHER_DIR=/srv/atlas/vault/cipher`, `VAULT_MOUNT_DIR=/srv/atlas/vault/open`, `VAULT_TEST_CIPHER_DIR=/srv/atlas/vault/test-cipher`, `VAULT_IDLE=15m`, `VAULT_UNIT=atlas-vault.service`, `VAULT_HELPER=/usr/local/bin/atlas-vault`, `VAULT_PASS_FILE=/run/atlas/vault-pass`, `VAULT_OVERRIDE_FILE=/run/atlas/vault-override.env`, `VAULT_USER=atlas` |
| `/etc/atlas/orchestrator.env` | gains `VAULT_CIPHER_DIR`, `VAULT_MOUNT_DIR`, `VAULT_IDLE`, `VAULT_UNIT`, `VAULT_HELPER` (ensure_kv; step 9b restarts `atlas-orchestrator` once) |
| `/usr/local/bin/atlas-vault open` | passphrase on **stdin** (one line), never an argument; prints `open`; exit 0 when `VAULT_MOUNT_DIR` is mounted, 1 when refused (gocryptfs exit 12 = wrong passphrase), 2 on contract errors |
| `/usr/local/bin/atlas-vault lock` | prints `locked`; exit 0 when unmounted |
| `/usr/local/bin/atlas-vault status` | prints `open` or `locked`; exit 0 |
| `/etc/sudoers.d/atlas-vault` | `atlas ALL=(root) NOPASSWD:` exactly `atlas-vault open`, `atlas-vault lock`, `atlas-vault status` (sudo-rs, plain syntax) |
| `atlas-vault.service` | `gocryptfs -fg -q -nosyslog -idle ${VAULT_IDLE} -passfile /run/atlas/vault-pass CIPHER MOUNT` as user `atlas` in the **host** mount namespace; active exactly while the vault is open; auto-exits on the idle unmount |

What the package must implement:

* `POST /vault/open` (body `{"passphrase": "..."}` from the interface button, never from a chat message): pipes the
  passphrase into `sudo -n /usr/local/bin/atlas-vault open` (stdin), returns the helper's verdict. The passphrase is
  not logged, not stored, not passed to a model.
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

### Sandbox (Section 16.4; V17)

Written into `/etc/atlas/orchestrator.env` by `phase2/10-gate.sh` (the gate builds the image because no earlier step
owns it): `SANDBOX_IMAGE=atlas-sandbox:py3.12`, `SANDBOX_DIR=/srv/atlas/sandbox`, `SANDBOX_MEMORY=2g`, `SANDBOX_CPUS=2`,
`SANDBOX_PIDS=256`, `SANDBOX_TMPFS_SIZE=512m`, `SANDBOX_TIMEOUT_S=300`. The run line the package must use is in the
header of `docker/sandbox/Dockerfile` (`--rm --network none --memory --memory-swap --cpus --pids-limit --read-only
--tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user 65534:65534`, wrapped in `timeout -k 5`). A tier
that grants network replaces `--network none`; nothing else changes. The image has no pip, curl or wget.

## 2. Files and names from other writers that these scripts use

| Used by | Contract | Owner |
|---|---|---|
| 10-gate, v18 | `/etc/atlas/orchestrator.env` written with `ensure_kv` (foreign keys kept); keys `ORCH_PORT`, `ATLAS_DB_PATH`, ... | `phase2/02-orchestrator.sh` |
| 10-gate | units `atlas-orchestrator`, `atlas-celery-cpu`, `atlas-celery-gpu`, `atlas-celery-beat`; `GET /health` -> 200 | `phase2/02-orchestrator.sh`, `systemd/atlas-orchestrator.service` |
| 10-gate | `/opt/atlas/venv/bin/{python,atlas-admin}`; the package at `/opt/atlas/orchestrator` | `phase2/02-orchestrator.sh` |
| 9b, v18 | `/run/atlas` is `RuntimeDirectory=atlas` (0750 atlas, preserved) of `atlas-orchestrator.service`; 9b creates it when the service never ran | `systemd/atlas-orchestrator.service` |
| 10-gate | `llama-server@router-qwen3.5-4b`, `llama-server@embed-bge-m3`, `llama-server@rerank-bge-v2-m3`; `/etc/atlas/engines/<key>.env` with `LLAMA_ARG_PORT`; fallback `LLAMA_PORT_BASE` + index in `config/engines.json` | `phase2/01-llama.sh`, `phase2/04-memory.sh`, `phase2/engine-env.py` |
| v18 | `/etc/atlas/memory.env`: `CHROMA_URL`, `CHROMA_COLLECTIONS` (quoted, space-separated), `LIGHTRAG_WORKING_DIR`; the six Section 10.1 collections exist; Chroma v2 REST `.../collections` (list) and `.../collections/{id}/get` (query) under `default_tenant/default_database` | `phase2/04-memory.sh`, `docker/core/compose.yml` |
| 10-gate | containers `atlas-redis`, `atlas-chromadb`, `atlas-openwebui` (host network), `atlas-kokoro`/`kokoro`, `atlas-speaches`/`speaches`, `atlas-docling`/`docling`; HTTP: ChromaDB `127.0.0.1:8000/api/v2/heartbeat`, Kokoro `8880/health`, speaches `8881/health`, docling `5001/docs`, Open WebUI `$OPENWEBUI_PORT/health` | `docker/core/compose.yml`, `docker/core/compose.voice.yml`, `phase2/03-openwebui.sh`, `phase2/05-voice.sh` |
| 10-gate | `atlas-ntfy` on `127.0.0.1:8090/v1/health`; `wg-easy`; units `docker`, `squid`, `cockpit.socket`, `xrdp`, `ssh.service`/`ssh.socket`, `atlas-ddns.timer`, `atlas-docker-egress.service` | Phase 1 steps 4, 5b, 6, 7 |
| 10-gate | `atlas-aegis.timer`, `atlas-restic-check.timer`; `atlas-sentinel.timer`, `atlas-prune.timer`; `srv-atlas-winpc.automount` (only when installed; step 9 may opt out) | `phase2/07-restic.sh`, `phase2/08-sentinel.sh`, `phase2/09-windows-share.sh` |
| 10-gate | verify usage lines: `v12-openwebui-offline.sh CONTAINER WINDOW_S` (called with `atlas-openwebui 120`); `v06`, `v07`, `v13`, `v20`, `v23`, `v03b`, `v10a` with their defaults | the respective writers |
| 10-gate | `/etc/atlas/docker.env` (`CONTAINER_HTTP(S)_PROXY`, `LAN_IP`), `/etc/atlas/core.env`, `/etc/atlas/voice.env` as compose `--env-file`s (used only for `docker compose ps`, each only when present) | Phase 1 step 6, `phase2/02-orchestrator.sh`, `phase2/05-voice.sh` |
| 9b | apt `gocryptfs 2.6.1-1` and `fuse3` (VERIFIED versions, services-tools.md §4.11); `config/allowlist.txt` covers the Ubuntu archive and the Docker Hub hosts for `python:3.12-slim` (`registry-1.docker.io`, `auth.docker.io`, `production.cloudflare.docker.com`) | Phase 1 step 4 (allowlist), `config/allowlist.txt` |

## 3. Conflicts noticed for the other side (no change made to their files)

1. **restic exclude path.** `phase2/07-restic.sh` excludes `/srv/atlas/vault-open`; the task fixes the mount point at
   `/srv/atlas/vault/open`. `atlas-aegis.service` runs restic with `--one-file-system`, so the FUSE view is never
   descended, but restic will still `lstat` the mount point as root and get `EACCES` while the vault is open
   (exit 3, "some files could not be read", which the unit already tolerates). Recommended: exclude
   `/srv/atlas/vault/open` too. The cipher dir `/srv/atlas/vault/cipher` and `/srv/atlas/vault/test-cipher` are
   backed up as ciphertext, as Section 11 wants.
2. **Container names.** `docker/core/compose.yml` names the voice containers `atlas-kokoro`, `atlas-speaches`,
   `atlas-docling`; `docker/core/compose.voice.yml` (merged into the same project by `phase2/05-voice.sh`) names them
   `kokoro`, `speaches`, `docling`. The gate accepts either spelling; the two files should agree.
3. **pytest** is not named by any research file; see §1.
