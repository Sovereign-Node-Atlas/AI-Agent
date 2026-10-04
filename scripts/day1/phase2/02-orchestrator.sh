#!/usr/bin/env bash
# phase2/02-orchestrator.sh — Section 17 Phase 2 step 2: Redis, Celery workers (cpu, gpu queues), the orchestrator
# scaffold with the Engine Arbiter (4.2), router (7.1), approval queue (16.2) and task ledger (9.7). Sourced by
# phase2-services.sh through run_phase_steps; defines step_02 only (plus helpers other steps reuse).
#
# What it does, in order (each part idempotent):
#   1. apt: python3-venv/pip, build tools, rsync.
#   1b. $ATLAS_OPT/venv-uv (uv==ORCH_UV_PIN from PyPI, the SAME pin as phase2/05-voice.sh's UV_PIN, which shares the
#      venv; the two are cross-checked at run time so one bump cannot leave them apart, fix round 3) and a uv-managed CPython 3.12 under
#      $ATLAS_OPT/python. The host python3 on Ubuntu 26.04 is 3.14 (research services-tools.md) and the pinned tree does
#      NOT import there (chromadb-client 1.5.9 -> overrides 7.7.0 uses typing.ByteString, removed in 3.14; fastapi and the
#      package's own annotations fail under PEP 749), although `pip install` succeeds (fix round, blocker): the venv is
#      therefore built from the managed 3.12 and the interpreter version is asserted before anything is installed.
#   2. $ATLAS_ETC/core.env — the ONE compose interpolation file for docker/core/compose.yml: a superset of Phase 1's
#      $ATLAS_ETC/docker.env plus the data-dir and port keys (see _core_env_write). Data directories created.
#   3. $ATLAS_ETC/secrets/redis.env (atlas:atlas 600, generated once): REDIS_PASSWORD plus the three URLs that carry
#      it (REDIS_URL, CELERY_BROKER_URL, CELERY_RESULT_BACKEND). Redis from docker/core/compose.yml (redis:8.10.2,
#      published on 127.0.0.1:6379 only, --requirepass from that file): an authenticated PING must answer PONG and an
#      unauthenticated PING from the host must be refused with NOAUTH (fix round: loopback alone does not authenticate;
#      every local process could otherwise enqueue Celery tasks with chosen arguments).
#   4. The `atlas` package: scripts/day1/orchestrator (mirrored to $ATLAS_OPT/day1/orchestrator by atlas-day1.sh) is
#      synced to $ATLAS_OPT/orchestrator and installed in editable mode into $ATLAS_OPT/venv (uv pip, seeded with pip so
#      phase2/10-gate.sh's `python -m pip` contract still holds). The package contract check imports every Celery task
#      module the way a worker does (app.loader.import_default_modules), so a broken task tree dies here, not as an opaque
#      "inspect ping did not hear the cpu worker" four parts later (fix round). Both trees end up
#      root:atlas with no group/other write (Section 16.3 items 6 and 8: the running service must not be able to
#      rewrite its own code or pip-install into its venv; the units also mount them read-only). The step FAILS LOUDLY
#      when pyproject.toml is missing: that package is written by other writers against CONVENTIONS.md; this step
#      installs what is there.
#   5. $ATLAS_ETC/orchestrator.env (root:atlas 640 from creation): every non-secret setting the package and the units
#      read (list below). It is in the restic include set, so the Redis URLs are NOT in it (they are in redis.env).
#   6. /etc/sudoers.d/atlas-engines (CONVENTIONS.md §8 control path): step 01 writes it; this step re-generates it when
#      absent or when it carries a wildcard, and checks the installed content (30 explicit lines, no `*`: sudoers
#      matches arguments as one string, so `llama-server@*` would also match `stop llama-server@x ufw.service`).
#   7. `atlas-admin init-db` creates the SQLite ledger/approval DB at ATLAS_DB_PATH under $ATLAS_SRV/data.
#   8. Units installed and started: atlas-orchestrator, atlas-celery-cpu (concurrency nproc-2), atlas-celery-gpu
#      (concurrency exactly 1, Section 9.7), atlas-celery-beat (package-owned schedules only; the Sentinel, prune and
#      AEGIS schedules are systemd timers, CONVENTIONS.md §8). Then /health must answer 200, /v1/models must list
#      atlas, ren and arthur, and both Celery workers must answer `celery inspect ping`.
#
# FILE WRITES (fix round): never `... | install /dev/stdin DST`. Ubuntu 26.04's /usr/bin/install is rust-coreutils 0.8.0
# (VERIFIED, phase1/04-system.sh header), which canonicalises the SOURCE when DEST exists, so a pipe source fails on every
# re-run. orch_write_file below (stdin -> mktemp 0600 -> install) is what this step and steps 03, 07 and 08 use.
#
# CONTRACT with the `atlas` package (CONVENTIONS.md does not state it; the package writer codes against this):
#   * orchestrator/pyproject.toml declares console scripts `atlas-orchestrator` and `atlas-admin`, and depends on
#     celery[redis]==5.6.3 (research §3 pin) and the redis client kombu 5.6 accepts (redis==6.4.0: kombu's redis extra
#     caps at <6.5, so the research's 8.1.0 is ResolutionImpossible; pyproject.toml records the conflict) so `celery`
#     lands in the venv.
#   * `atlas-orchestrator --host H --port P`: GET /health -> 200 when ready; GET /v1/models -> {"data":[{"id":"atlas"},
#     {"id":"ren"},{"id":"arthur"}, ...]}; POST /v1/chat/completions (Section 12.1).
#   * `atlas-admin init-db`: creates the SQLite ledger/approval DB at $ATLAS_DB_PATH (idempotent).
#   * `atlas-admin enqueue <sentinel|prune|aegis-freeze|aegis-thaw|chat-retention> [--wait SECONDS]`: sends the task
#     to Celery; exit 0 when accepted (or, with --wait, when finished in time); with --wait prints the ledger record
#     of the run as ONE JSON object line on stdout.
#   * the manual [EXECUTE AEGIS BACKUP] trigger (atlas.tasks.aegis_manual_backup) creates /run/atlas/aegis-request (an
#     empty file, as atlas) and confirms with `systemctl is-active atlas-aegis.service`; it uses NO sudo. The path unit
#     atlas-aegis-trigger.path starts the backup (phase2/07-restic.sh; fix round: no second sudoers fragment, so
#     /etc/sudoers.d/atlas-engines stays the ONLY NOPASSWD grant of the atlas account, CONVENTIONS.md §8).
#   * module `atlas.celery_app` exposes the Celery app as `app` with task_default_queue "cpu", GPU tasks routed to
#     "gpu", and beat_schedule holding ONLY package-owned schedules (chat-retention nightly), never sentinel/prune/aegis.
#     The broker and result URLs are read from CELERY_BROKER_URL / CELERY_RESULT_BACKEND (or REDIS_URL), which the
#     units load from $ATLAS_ETC/secrets/redis.env; a shell that runs atlas-admin by hand sources that file too.
#   * settings are read from the environment; the keys are the ones written by _orch_env_write below. Token files,
#     stated per key because the formats differ (fix round):
#         OPENWEBUI_ADMIN_TOKEN_FILE   bare token on the first line (written by step 03)
#         NTFY_TOKEN_FILE              KEY=VALUE file, key NTFY_TOKEN (Phase 1 step 7's ntfy.env)
#         HF_TOKEN_FILE                KEY=VALUE file, key HF_TOKEN (phase2-services.sh's hf-token.env)
#     The package's tasks.read_secret_line(path, key) accepts both shapes; the comment block at the top of
#     orchestrator.env repeats this.
#   * src/atlas/openwebui_filter.py is a valid Open WebUI Filter (class Filter, Valves.ORCHESTRATOR_URL); step 03 installs it.
# Contracts relied on from other writers: Phase 1 step 6 wrote $ATLAS_ETC/docker.env and the atlas account (groups
# docker, render, video); step 01 wrote /etc/sudoers.d/atlas-engines (re-created here if absent or wildcarded, same
# content: one line per verb and engine key of config/engines.json).
# Contract this file defines for others: $ATLAS_ETC/core.env and the `core_compose` helper
# (`core_compose up -d <service>`), used by step 03; step 04 uses the bare `docker compose -f` form and step 05 merges
# docker/core/compose.voice.yml into the same project (compose.yml carries `name: atlas-core` and the `atlas` network).
# $ATLAS_ETC/secrets (root:atlas 710, fix round 3, ONE value): the directory must be TRAVERSABLE by the atlas account,
# whose workers open the atlas-owned token files inside it BY NAME (ntfy.env, openwebui-admin.token, redis.env,
# hf-token.env; every file stays 600 owned by its one reader). 710 gives the x bit only: 750 would additionally let every
# atlas-group process list the secret file names, which nothing needs (reviewer finding; the "minimum" variant. The
# LoadCredential alternative keeps §2's 700 only for the three worker files and not for atlas-ddns, rclone's google/
# dir or 06c, so it does not remove the traversal need). CONVENTIONS.md §2's row (root:root 700) cannot hold beside its
# own atlas:atlas and atlas-ddns rows and must read root:atlas 710 (recorded for the Principal; phase2/README-contracts.md
# §3 asks the same). 710 is what phase2-services.sh (every phase-2 start) and 09b (the last step before the gate) set;
# 02, 03, 07, 08 and 09 now set the same, so the mode no longer flips during one Phase 2 run. Still at 750 in other
# writers' files: phase1/02-luks.sh, 03-mounts.sh, 07-remote.sh and phase2/06c-google-oauth.sh (they should adopt 710;
# harmless meanwhile: 750 is looser, and the driver tightens it at the next phase-2 start). The failure stays LOUD: the
# atlas-celery-cpu/-gpu and atlas-sentinel units carry an ExecStartPre (run as atlas) that fails the unit when the
# directory cannot be traversed or ntfy.env cannot be read, and this step's own root-side helpers (orch_admin, the
# celery ping) source redis.env as root before dropping to atlas, so they never depend on the traversal.
# smb.cred (fix round 3): step 9 needs $ATLAS_ETC/secrets/smb.cred and §7.6 sanctions no prompt there; this step is the
# first of Phase 2 that this writer owns, so it DIES here when the file is absent (naming the creation command) instead
# of letting the unattended phase run ~40 more minutes to a predictable stop at step 9.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

ORCH_VENV="$ATLAS_OPT/venv"
ORCH_SRC="$ATLAS_DAY1_DIR/orchestrator"
ORCH_DIR="$ATLAS_OPT/orchestrator"
ORCH_ENV="$ATLAS_ETC/orchestrator.env"
REDIS_ENV="$ATLAS_ETC/secrets/redis.env"
CORE_COMPOSE="$ATLAS_DAY1_DIR/docker/core/compose.yml"
CORE_ENV="$ATLAS_ETC/core.env"
ORCH_UNITS=(atlas-orchestrator atlas-celery-cpu atlas-celery-gpu atlas-celery-beat)
ORCH_PYTHON="3.12"                 # pyproject.toml requires-python >=3.12; 3.14 (the host) does not import the pinned tree
ORCH_UV_VENV="$ATLAS_OPT/venv-uv"  # shared with phase2/05-voice.sh (_voice_uv): same path, same bootstrap
ORCH_UV_BIN=""
ORCH_CACHE_DIR="/var/cache/atlas"  # transient caches; never $ATLAS_STATE (restic's include set, CONVENTIONS.md §2)
ORCH_UV_PIN="0.12.23"              # rule §7.9: MUST equal phase2/05-voice.sh's UV_PIN (same venv); _orch_uv cross-checks it
ORCH_SECRETS_MODE=710              # header: the one value for $ATLAS_ETC/secrets (root:atlas, traverse-only for atlas)

# orch_write_file MODE OWNER DST — write stdin to DST atomically (mktemp 0600 -> install). OWNER is "user:group" or "".
# Shared by steps 02, 03, 07 and 08 (header: rust-coreutils install rejects a pipe source when DST exists).
orch_write_file() {
  local mode="$1" owner="$2" dst="$3" tmp
  tmp="$(mktemp)" || die "orch_write_file: mktemp failed"
  cat >"$tmp"
  if [[ -n "$owner" ]]; then
    install -m "$mode" -o "${owner%%:*}" -g "${owner#*:}" "$tmp" "$dst" || { rm -f "$tmp"; die "orch_write_file: install $dst failed"; }
  else
    install -m "$mode" "$tmp" "$dst" || { rm -f "$tmp"; die "orch_write_file: install $dst failed"; }
  fi
  rm -f "$tmp"
}

# core_compose ARGS... — docker compose against docker/core/compose.yml with the shared interpolation file.
core_compose() {
  [[ -f "$CORE_COMPOSE" ]] || die "core_compose: $CORE_COMPOSE is missing"
  [[ -s "$CORE_ENV" ]] || die "core_compose: $CORE_ENV is missing (phase2/02-orchestrator.sh writes it)"
  docker compose -f "$CORE_COMPOSE" --env-file "$CORE_ENV" "$@"
}

# orch_as_atlas CMD... — run CMD as the atlas user with every settings file the package reads in its environment
# (orchestrator.env, secrets/redis.env, memory.env when present: the order the units load them), working directory
# $ORCH_DIR. The files are sourced by ROOT in a subshell and inherited through runuser (which keeps the environment),
# so this never depends on atlas traversing $ATLAS_ETC/secrets (fix round: the units read redis.env through PID 1 the
# same way). Exit status is CMD's.
orch_as_atlas() {
  [[ -s "$ORCH_ENV" ]] || die "orch_as_atlas: $ORCH_ENV missing (step 02 writes it)"
  [[ -s "$REDIS_ENV" ]] || die "orch_as_atlas: $REDIS_ENV missing (step 02 writes it; the broker URL lives there)"
  (
    set -a
    # shellcheck disable=SC1090  # KEY=VALUE settings files written by this step (and step 04 for memory.env)
    source "$ORCH_ENV"
    # shellcheck disable=SC1090
    source "$REDIS_ENV"
    # shellcheck disable=SC1090,SC1091
    [[ -f "$ATLAS_ETC/memory.env" ]] && source "$ATLAS_ETC/memory.env"
    set +a
    cd "$ORCH_DIR" || exit 1
    svc_user_run "$@"
  )
}

# orch_admin ARGS... — run atlas-admin as the atlas user with the settings files loaded.
orch_admin() {
  [[ -x "$ORCH_VENV/bin/atlas-admin" ]] || die "orch_admin: $ORCH_VENV/bin/atlas-admin missing (step 02 contract)"
  orch_as_atlas "$ORCH_VENV/bin/atlas-admin" "$@"
}

# engine_port_of KEY — LLAMA_PORT_BASE + 1-based index in config/engines.json (CONVENTIONS.md §8), like step 04's engine_port.
engine_port_of() {
  python3 - "$ATLAS_DAY1_DIR/config/engines.json" "$1" "${LLAMA_PORT_BASE:-8100}" <<'PY'
import json, sys
for i, e in enumerate(json.load(open(sys.argv[1], encoding="utf-8"))["engines"], start=1):
    if e["key"] == sys.argv[2]:
        print(int(sys.argv[3]) + i)
        sys.exit(0)
sys.exit(f"engines.json: no engine {sys.argv[2]!r}")
PY
}

_orch_apt() {
  apt_install python3 python3-venv python3-pip python3-dev build-essential rsync git jq curl
}

# --- fail fast for step 9 (fix round 3: no prompt mid-phase, §7.6; the credential file must be pre-staged) -----------
# Section 22 / README.md (other writers' files) must carry the checklist line: "before Phase 2, create
# /etc/atlas/secrets/smb.cred with the Windows account that can read and write WINDOWS_SHARE" plus this command.
_orch_preflight_smb_cred() {
  local cred="$ATLAS_ETC/secrets/smb.cred"
  if [[ ! -s "$cred" ]]; then
    die "$cred is missing: step 9 (the Windows PC share, Section 12.4) needs it and §7.6 sanctions no prompt there, so Phase 2 stops NOW rather than ~40 min later. Create it (the password is read from the terminal, never typed on a command line where shell history and /proc/<pid>/cmdline would keep it, §7.2), then re-run the phase: sudo bash -c 'umask 077; read -rp \"Windows user: \" u; read -rsp \"Windows password: \" p; echo; printf \"username=%s\\npassword=%s\\ndomain=WORKGROUP\\n\" \"\$u\" \"\$p\" > $cred; chown root:root $cred; chmod 600 $cred' && sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2"
  fi
  if ! grep -q '^username=.\+' "$cred" || ! grep -q '^password=.\+' "$cred"; then
    die "$cred lacks a username= or password= line (mount.cifs credentials format: username=, password=, domain=); fix it before step 9"
  fi
  log "step 9 pre-flight: $cred present"
}

# --- core.env: one interpolation file for docker/core/compose.yml -------------------------------------------------------
_core_env_write() {
  [[ -s "$ATLAS_ETC/docker.env" ]] || die "$ATLAS_ETC/docker.env is missing (Phase 1 step 6 writes it)"
  local embed_port
  embed_port="$(engine_port_of embed-bge-m3)" || die "cannot compute the embed-bge-m3 port from config/engines.json"
  {
    echo "# Written by ATLAS Phase 2 step 2 (phase2/02-orchestrator.sh). Compose interpolation for docker/core/compose.yml:"
    echo "#   docker compose -f $CORE_COMPOSE --env-file $CORE_ENV up -d <service>"
    echo "# Superset of $ATLAS_ETC/docker.env (Phase 1 step 6). Not a secret (no tokens; the Redis password is in"
    echo "# $REDIS_ENV, the container's env_file). Re-generated on every run of step 2."
    echo "# The voice overlay (docker/core/compose.voice.yml, Phase 2 step 5) uses docker.env plus its own voice.env instead."
    grep -E '^[A-Z_][A-Z0-9_]*=' "$ATLAS_ETC/docker.env"
    echo "ATLAS_SRV=$ATLAS_SRV"
    echo "ATLAS_ETC=$ATLAS_ETC"
    echo "OPENWEBUI_PORT=$OPENWEBUI_PORT"
    echo "ORCH_PORT=$ORCH_PORT"
    echo "EMBED_PORT=$embed_port"
    echo "OPENWEBUI_DATA_DIR=$ATLAS_SRV/data/open-webui"
    echo "REDIS_DATA_DIR=$ATLAS_SRV/data/redis"
    echo "CHROMA_DATA_DIR=$ATLAS_SRV/data/chroma"
  } | orch_write_file 644 root:root "$CORE_ENV"
  log "wrote $CORE_ENV (embed port $embed_port, Open WebUI port $OPENWEBUI_PORT, orchestrator port $ORCH_PORT)"

  # Data directories the compose file bind-mounts (Appendix C: all under $ATLAS_SRV/data are in the restic set).
  # The container data dirs are created ONLY when absent (fix round, major): the redis image's entrypoint chowns /data
  # to its `redis` user once, at container start, and `compose up -d` does not recreate an unchanged container, so an
  # unconditional chown back to root on `--force 02` would leave a running Redis unable to BGSAVE/rewrite its AOF
  # (MISCONF, writes refused, Celery enqueues failing).
  ensure_dir "$ATLAS_SRV/data" atlas:atlas 755
  [[ -d "$ATLAS_SRV/data/redis" ]]      || ensure_dir "$ATLAS_SRV/data/redis" root:root 750       # entrypoint chowns to redis
  [[ -d "$ATLAS_SRV/data/chroma" ]]     || ensure_dir "$ATLAS_SRV/data/chroma" root:root 750      # chroma writes /data as its own user
  [[ -d "$ATLAS_SRV/data/open-webui" ]] || ensure_dir "$ATLAS_SRV/data/open-webui" root:root 750  # Open WebUI runs as UID 0 (research 1.1)
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/data/orchestrator" atlas:atlas 750
  ensure_dir "$ATLAS_SRV/data/sentinel" atlas:atlas 750
  ensure_dir /srv/cold atlas:atlas 750
}

# --- Redis secret ----------------------------------------------------------------------------------------------------------
_orch_redis_secret() {
  ensure_dir "$ATLAS_ETC/secrets" root:atlas "$ORCH_SECRETS_MODE"
  if [[ -s "$REDIS_ENV" ]] && grep -qE '^REDIS_PASSWORD=[A-Za-z0-9]{32,}$' "$REDIS_ENV" \
     && grep -qE '^CELERY_BROKER_URL=redis://:' "$REDIS_ENV"; then
    log "redis secret $REDIS_ENV present"
    return 0
  fi
  local pw
  pw="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"    # 48 hex chars: URL-safe, no quoting needed
  {
    echo "# Redis password and the URLs that carry it (phase2/02-orchestrator.sh; CONVENTIONS.md §2). Loaded by the"
    echo "# atlas-* units (EnvironmentFile=) and by docker/core/compose.yml (env_file for the redis service). Never in"
    echo "# orchestrator.env (restic backs that up). Regenerate by deleting this file and re-running step 2 with --force 02."
    echo "REDIS_PASSWORD=$pw"
    echo "REDIS_URL=redis://:$pw@127.0.0.1:6379/0"
    echo "CELERY_BROKER_URL=redis://:$pw@127.0.0.1:6379/0"
    echo "CELERY_RESULT_BACKEND=redis://:$pw@127.0.0.1:6379/1"
  } | orch_write_file 600 atlas:atlas "$REDIS_ENV"
  log "generated $REDIS_ENV (atlas:atlas 600; the password is never logged)"
}

# --- Redis ---------------------------------------------------------------------------------------------------------------
_orch_redis_up() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  grep -qE '^[[:space:]]+redis:' "$CORE_COMPOSE" || die "$CORE_COMPOSE defines no 'redis' service"
  proxy_env
  log "docker compose up -d redis (redis:8.10.2, 127.0.0.1:6379, requirepass from $REDIS_ENV through the config on stdin; image pull through the proxy)"
  retry 3 core_compose up -d --quiet-pull redis \
    || die "docker compose up redis failed (registry-1.docker.io / the Docker Hub blob CDN must be allowlisted): $(core_compose logs --tail 20 redis 2>&1 | tail -n 20)"
  # Authenticated PING inside the container: the password is read from the container's own environment (env_file) into
  # REDISCLI_AUTH (redis-cli's documented env alternative to -a), so it is on NO command line, host or container.
  local pong=""
  for _ in $(seq 1 30); do
    pong="$(docker exec atlas-redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning ping' 2>/dev/null || true)"
    [[ "$pong" == PONG ]] && break
    sleep 2
  done
  [[ "$pong" == PONG ]] || die "redis did not answer an authenticated PING inside the container after 60 s (docker logs atlas-redis: an exit 78 means REDIS_PASSWORD was empty, a missing docker-entrypoint.sh means the compose command must change)"
  # The password must not be visible in /proc/<pid>/cmdline (world-readable on the host; fix round, major): compose.yml
  # feeds `requirepass` to redis-server as a config document on stdin, never as an argument. Prove it for the server
  # process. The pattern is handed to grep through a process substitution (printf is a builtin), not on grep's argv.
  local rpid pw
  rpid="$(docker inspect -f '{{.State.Pid}}' atlas-redis 2>/dev/null || echo 0)"
  [[ "$rpid" =~ ^[0-9]+$ && "$rpid" -gt 0 ]] || die "no PID for atlas-redis (docker inspect)"
  pw="$(awk -F= '$1=="REDIS_PASSWORD" {print $2; exit}' "$REDIS_ENV")"
  [[ -n "$pw" ]] || die "$REDIS_ENV carries no REDIS_PASSWORD line"
  if tr '\0' ' ' <"/proc/$rpid/cmdline" | grep -qF -f <(printf '%s\n' "$pw"); then
    die "the Redis password is visible in /proc/$rpid/cmdline: docker/core/compose.yml must pass requirepass through the stdin config document, not as an argument (§7.2 'never echoed')"
  fi
  grep -q 'redis-server' "/proc/$rpid/cmdline" || warn "PID $rpid of atlas-redis does not look like redis-server ($(tr '\0' ' ' <"/proc/$rpid/cmdline" | cut -c1-80))"
  unset pw
  # The host-side Celery workers connect to the published loopback port; prove it from the host, and prove that an
  # UNAUTHENTICATED client is refused there (the point of the password).
  local reply
  # shellcheck disable=SC2016  # $l is the inner bash -c's own variable, expanded there on purpose
  reply="$(timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/6379; printf "PING\r\n" >&3; IFS= read -r -t 4 l <&3; printf "%s" "$l"' 2>/dev/null | tr -d '\r' || true)"
  [[ -n "$reply" ]] || die "127.0.0.1:6379 is not reachable from the host although the container answers PING (published port missing?)"
  [[ "$reply" == -NOAUTH* ]] || die "redis on 127.0.0.1:6379 answered an unauthenticated PING with '$reply' instead of NOAUTH: --requirepass is not in effect (docker/core/compose.yml command)"
  local published
  published="$(docker port atlas-redis 6379/tcp 2>/dev/null | tr '\n' ' ' || true)"
  grep -q '127.0.0.1:6379' <<<"$published" || die "redis is published on '$published', expected 127.0.0.1:6379 only (CONVENTIONS.md §8: loopback only)"
  log "redis up: authenticated PONG, unauthenticated PING refused (NOAUTH), password absent from the server's cmdline, published on $published"
}

# --- the atlas package ---------------------------------------------------------------------------------------------------
_orch_sync_source() {
  [[ -d "$ORCH_SRC" ]] || die "$ORCH_SRC does not exist: the orchestrator package (CONVENTIONS.md §1 orchestrator/) has not been written yet; nothing to install"
  [[ -f "$ORCH_SRC/pyproject.toml" ]] || die "$ORCH_SRC/pyproject.toml is missing: the orchestrator package is incomplete (its writer codes against CONVENTIONS.md and the contract in this file's header); refusing to guess"
  ensure_dir "$ATLAS_OPT" root:root 755
  mkdir -p "$ORCH_DIR"
  # Excluded patterns are also protected from --delete on the receiver, so pip's editable metadata survives re-syncs.
  rsync -a --delete --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
    --exclude '*.egg-info' --exclude '.ruff_cache' "$ORCH_SRC/" "$ORCH_DIR/"
  log "synced $ORCH_SRC -> $ORCH_DIR"
}

# _orch_uv — uv in its own venv (the host pip is fine for THAT: uv is a static binary) plus a managed CPython. Same
# bootstrap, paths and PIN as phase2/05-voice.sh _voice_uv so the two steps share one uv and one python store (fix
# round 3: 02 used to install uv unpinned and 05 re-installed its pin into the same venv on every fresh node).
_orch_uv() {
  # The two pins must agree (rule §7.9, one venv): read 05's UV_PIN line when the file is present and die on a mismatch.
  local voice="$ATLAS_DAY1_DIR/phase2/05-voice.sh" other
  if [[ -f "$voice" ]]; then
    other="$(sed -nE 's/^UV_PIN="([^"]+)"$/\1/p' "$voice" | head -n1)"
    [[ -n "$other" ]] || die "$voice carries no UV_PIN=\"x.y.z\" line; cannot prove it shares $ORCH_UV_VENV's uv pin $ORCH_UV_PIN"
    [[ "$other" == "$ORCH_UV_PIN" ]] || die "uv pin mismatch: phase2/02-orchestrator.sh ORCH_UV_PIN=$ORCH_UV_PIN, phase2/05-voice.sh UV_PIN=$other (same venv $ORCH_UV_VENV); make them equal"
  fi
  if [[ ! -x "$ORCH_UV_VENV/bin/uv" ]]; then
    mkdir -p "$ATLAS_OPT"
    python3 -m venv "$ORCH_UV_VENV" || die "python3 -m venv $ORCH_UV_VENV failed"
  fi
  if [[ "$("$ORCH_UV_VENV/bin/uv" --version 2>/dev/null | awk '{print $2}')" != "$ORCH_UV_PIN" ]]; then
    proxy_env
    log "venv-uv: installing uv==$ORCH_UV_PIN into $ORCH_UV_VENV (was: $("$ORCH_UV_VENV/bin/uv" --version 2>/dev/null || echo none))"
    retry 3 "$ORCH_UV_VENV/bin/python" -m pip install --quiet --disable-pip-version-check "uv==$ORCH_UV_PIN" || die "pip install uv==$ORCH_UV_PIN into $ORCH_UV_VENV failed (pypi.org / files.pythonhosted.org through the proxy?)"
    [[ "$("$ORCH_UV_VENV/bin/uv" --version 2>/dev/null | awk '{print $2}')" == "$ORCH_UV_PIN" ]] || die "uv in $ORCH_UV_VENV is not $ORCH_UV_PIN after the install: $("$ORCH_UV_VENV/bin/uv" --version 2>&1)"
  fi
  ORCH_UV_BIN="$ORCH_UV_VENV/bin/uv"
  export UV_PYTHON_INSTALL_DIR="$ATLAS_OPT/python" UV_CACHE_DIR="$ORCH_CACHE_DIR/uv" UV_HTTP_TIMEOUT=600
  mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"
  proxy_env
  # UNVERIFIED: uv fetches python-build-standalone from github.com release assets (allowlisted; 05-voice.sh relies on
  # the same). A failure stops here with the reason.
  if ! "$ORCH_UV_BIN" python find "$ORCH_PYTHON" >/dev/null 2>&1; then
    log "uv: installing a managed CPython $ORCH_PYTHON under $UV_PYTHON_INSTALL_DIR (the host $(python3 --version 2>&1) cannot import the pinned tree)"
    retry 3 "$ORCH_UV_BIN" python install "$ORCH_PYTHON" || die "uv python install $ORCH_PYTHON failed (github.com release assets through the proxy?)"
  fi
  chmod -R a+rX "$UV_PYTHON_INSTALL_DIR"   # the atlas account runs the venv built on this interpreter
  log "uv: $("$ORCH_UV_BIN" --version 2>&1) (pinned $ORCH_UV_PIN, proven) with CPython $ORCH_PYTHON"
}

# _orch_python_ok — 0 when $ORCH_VENV/bin/python exists and is the pinned minor version.
_orch_python_ok() {
  [[ -x "$ORCH_VENV/bin/python" ]] || return 1
  "$ORCH_VENV/bin/python" -c 'import sys; v = tuple(int(x) for x in sys.argv[1].split(".")); sys.exit(0 if sys.version_info[:2] == v else 1)' "$ORCH_PYTHON" 2>/dev/null
}

_uv_pip_orch() {
  proxy_env
  retry 3 "$ORCH_UV_BIN" pip install --quiet --python "$ORCH_VENV/bin/python" "$@"
}

_orch_venv() {
  _orch_uv
  if [[ -x "$ORCH_VENV/bin/python" ]] && ! _orch_python_ok; then
    warn "$ORCH_VENV is not a Python $ORCH_PYTHON venv ($("$ORCH_VENV/bin/python" --version 2>&1)); recreating it from the managed interpreter (an earlier revision used the host python3)"
    systemctl stop "${ORCH_UNITS[@]}" 2>/dev/null || true
    rm -rf "$ORCH_VENV"
  fi
  if [[ ! -x "$ORCH_VENV/bin/python" ]]; then
    log "creating $ORCH_VENV with uv venv --seed --python $ORCH_PYTHON (seeded pip: phase2/10-gate.sh's \`python -m pip\` contract)"
    "$ORCH_UV_BIN" venv --seed --python "$ORCH_PYTHON" "$ORCH_VENV" || die "uv venv $ORCH_VENV failed"
  fi
  _orch_python_ok || die "$ORCH_VENV/bin/python is $("$ORCH_VENV/bin/python" --version 2>&1), not $ORCH_PYTHON (delete $ORCH_VENV and re-run with --force 02)"
  log "uv pip install -e $ORCH_DIR into $ORCH_VENV (the atlas package and its pinned dependencies; minutes on first run)"
  _uv_pip_orch -e "$ORCH_DIR" || die "uv pip install -e $ORCH_DIR failed (see above: a dependency without a wheel for Python $ORCH_PYTHON? the package's pyproject.toml is the other writer's file)"
  local b
  for b in atlas-orchestrator atlas-admin celery; do
    [[ -x "$ORCH_VENV/bin/$b" ]] || die "contract: $ORCH_VENV/bin/$b is missing after the editable install (pyproject.toml must declare the console scripts atlas-orchestrator and atlas-admin and depend on celery[redis]==5.6.3 with the redis client kombu 5.6 accepts, redis==6.4.0)"
  done
  # The contract check imports the task tree the way a WORKER does (fix round): `import atlas.celery_app` alone pulls
  # in none of the `include=` task modules, so a tree that fails to import (atlas.memory -> chromadb on the wrong
  # Python, a syntax error in a task module) would pass here and surface only as a crash-looping worker.
  "$ORCH_VENV/bin/python" -c '
import sys
from atlas.celery_app import app
app.loader.import_default_modules()
names = sorted(t for t in app.tasks if t.startswith("atlas.tasks."))
assert names, "no atlas.tasks.* task registered after import_default_modules(): %s" % sorted(app.tasks)
print("atlas tasks registered:", ", ".join(names), file=sys.stderr)
' || die "contract: the atlas package or one of its Celery task modules does not import in $ORCH_VENV (Python $ORCH_PYTHON; traceback above)"
  # Byte-compile as root so the read-only tree still imports fast for atlas (it cannot write __pycache__ any more).
  "$ORCH_VENV/bin/python" -m compileall -q "$ORCH_DIR/src" >/dev/null 2>&1 || true
  # Section 16.3 items 6 and 8: the service account reads the package and its venv, never writes them (the units mount
  # both read-only as well). root:atlas, group/other without write.
  chown -R root:atlas "$ORCH_DIR" "$ORCH_VENV"
  chmod -R g-w,o-w "$ORCH_DIR" "$ORCH_VENV"
  log "venv ready ($("$ORCH_VENV/bin/python" --version 2>&1)): $("$ORCH_VENV/bin/python" -c 'import importlib.metadata as m; print("atlas", m.version("atlas"))' 2>/dev/null || echo 'atlas (version unknown)'), celery $("$ORCH_VENV/bin/celery" --version 2>/dev/null | head -n1); $ORCH_DIR and $ORCH_VENV root:atlas, read-only for atlas"
}

# --- orchestrator.env ----------------------------------------------------------------------------------------------------
# _orch_env_drop KEY — remove a key other runs wrote (the Redis URLs moved to secrets/redis.env; fix round).
_orch_env_drop() {
  local key="$1"
  [[ -e "$ORCH_ENV" ]] || return 0
  grep -qE "^[[:space:]]*${key}=" "$ORCH_ENV" || return 0
  sed -i -E "/^[[:space:]]*${key}=/d" "$ORCH_ENV"
  log "removed $key from $ORCH_ENV (it lives in $REDIS_ENV now)"
}

_orch_env_write() {
  local cpu_conc
  cpu_conc=$(( $(nproc) - 2 ))
  (( cpu_conc >= 1 )) || cpu_conc=1
  # The orchestrator reads config/ from the ON-NODE copy $ATLAS_OPT/day1 (CONVENTIONS.md §2; the units mount exactly that
  # path read-only, 16.3 item 6), never from whatever checkout this driver happens to run from (fix round). atlas-day1.sh
  # takes that copy on every run; a direct phase2-services.sh run on a node where it never ran stops here.
  [[ -d "$ATLAS_OPT/day1/config" && -f "$ATLAS_OPT/day1/config/sentinel-feeds.json" && -f "$ATLAS_OPT/day1/config/engines.json" ]] \
    || die "$ATLAS_OPT/day1/config is missing or incomplete: run Phase 2 through ${ATLAS_ENTRY:-./atlas-day1.sh} (it copies scripts/day1 to $ATLAS_OPT/day1, the path the orchestrator and its units use)"
  if [[ ! -e "$ORCH_ENV" ]]; then
    # Created with its final owner and mode (root:atlas 640, CONVENTIONS.md §2): it holds FAMILY_NAMES (router hard-rule
    # PII), so no window with the umask default. The comment block states the token-file formats (header contract).
    {
      echo "# /etc/atlas/orchestrator.env — settings of the atlas package and its units (phase2/02-orchestrator.sh writes and"
      echo "# rewrites its own keys with ensure_kv; steps 03, 05, 06, 08, 09, 09b, 10 add theirs). root:atlas 640. No secrets:"
      echo "# the Redis URLs are in secrets/redis.env; the *_TOKEN_FILE keys point at secret files whose formats differ:"
      echo "#   OPENWEBUI_ADMIN_TOKEN_FILE  bare token on the first line"
      echo "#   NTFY_TOKEN_FILE             KEY=VALUE, key NTFY_TOKEN"
      echo "#   HF_TOKEN_FILE               KEY=VALUE, key HF_TOKEN"
    } | orch_write_file 640 root:atlas "$ORCH_ENV"
  fi
  _orch_env_drop REDIS_URL
  _orch_env_drop CELERY_BROKER_URL
  _orch_env_drop CELERY_RESULT_BACKEND
  # ensure_kv keeps keys other steps add (voice, tools, google) and rewrites only these.
  ensure_kv "$ORCH_ENV" ORCH_HOST 127.0.0.1
  ensure_kv "$ORCH_ENV" ORCH_PORT "$ORCH_PORT"
  ensure_kv "$ORCH_ENV" ORCH_URL "http://127.0.0.1:$ORCH_PORT"
  ensure_kv "$ORCH_ENV" CELERY_CPU_CONCURRENCY "$cpu_conc"
  ensure_kv "$ORCH_ENV" CELERY_GPU_CONCURRENCY 1
  ensure_kv "$ORCH_ENV" ATLAS_DB_PATH "$ATLAS_SRV/data/orchestrator/atlas.sqlite3"
  ensure_kv "$ORCH_ENV" ATLAS_CONFIG_DIR "$ATLAS_OPT/day1/config"
  ensure_kv "$ORCH_ENV" ATLAS_ENGINES_ENV_DIR "$ATLAS_ETC/engines"
  ensure_kv "$ORCH_ENV" ATLAS_MODELS_DIR "$ATLAS_SRV/models"
  ensure_kv "$ORCH_ENV" ATLAS_SRV "$ATLAS_SRV"
  ensure_kv "$ORCH_ENV" ATLAS_ETC "$ATLAS_ETC"
  ensure_kv "$ORCH_ENV" ATLAS_OPT "$ATLAS_OPT"
  ensure_kv "$ORCH_ENV" ATLAS_STATE "$ATLAS_STATE"
  ensure_kv "$ORCH_ENV" LLAMA_PORT_BASE "$LLAMA_PORT_BASE"
  ensure_kv "$ORCH_ENV" OPENWEBUI_URL "http://127.0.0.1:$OPENWEBUI_PORT"
  ensure_kv "$ORCH_ENV" OPENWEBUI_ADMIN_TOKEN_FILE "$ATLAS_ETC/secrets/openwebui-admin.token"
  ensure_kv "$ORCH_ENV" OPENWEBUI_CHAT_RETENTION_DAYS 90
  ensure_kv "$ORCH_ENV" NTFY_URL "http://127.0.0.1:8090"
  ensure_kv "$ORCH_ENV" NTFY_TOPIC "$NTFY_TOPIC"
  ensure_kv "$ORCH_ENV" NTFY_TOKEN_FILE "$ATLAS_ETC/secrets/ntfy.env"
  ensure_kv "$ORCH_ENV" HF_TOKEN_FILE "$ATLAS_ETC/secrets/hf-token.env"
  ensure_kv "$ORCH_ENV" SENTINEL_FEEDS "$ATLAS_OPT/day1/config/sentinel-feeds.json"
  ensure_kv "$ORCH_ENV" SENTINEL_LOG_DIR "$ATLAS_SRV/data/sentinel"
  ensure_kv "$ORCH_ENV" COLD_DIR /srv/cold
  ensure_kv "$ORCH_ENV" TZ "$TZ"
  ensure_kv "$ORCH_ENV" DOMAIN "$DOMAIN"
  ensure_kv "$ORCH_ENV" PRINCIPAL_USER "$PRINCIPAL_USER"
  ensure_kv "$ORCH_ENV" FAMILY_NAMES "\"$FAMILY_NAMES\""
  # zero-cloud belts (rule §7.1): library telemetry off; the HF cache is offline once step 4 has prefetched.
  ensure_kv "$ORCH_ENV" ANONYMIZED_TELEMETRY false
  ensure_kv "$ORCH_ENV" HF_HUB_DISABLE_TELEMETRY 1
  ensure_kv "$ORCH_ENV" DO_NOT_TRACK 1
  # re-asserted (ensure_kv rewrites through a temp file)
  chown root:atlas "$ORCH_ENV"
  chmod 640 "$ORCH_ENV"
  log "wrote $ORCH_ENV (cpu workers $cpu_conc, gpu workers 1, db $ATLAS_SRV/data/orchestrator/atlas.sqlite3; broker URLs in $REDIS_ENV)"
}

# --- sudoers (same content as step 01: one explicit line per verb and engine key, no wildcard) ----------------------------
_orch_sudoers_generate() {
  local frag="$1" sc tmp key verb
  sc="$(readlink -f "$(command -v systemctl)")"
  local keys=()
  mapfile -t keys < <(python3 -c 'import json,sys; [print(e["key"]) for e in json.load(open(sys.argv[1], encoding="utf-8"))["engines"]]' \
    "$ATLAS_DAY1_DIR/config/engines.json")
  (( ${#keys[@]} == 10 )) || die "config/engines.json lists ${#keys[@]} engines, CONVENTIONS.md §8 fixes ten; refusing to write $frag"
  for key in "${keys[@]}"; do
    [[ "$key" =~ ^[A-Za-z0-9._-]+$ ]] || die "engine key '$key' is not safe for sudoers/systemd instance names"
  done
  tmp="$(mktemp)"
  {
    echo "# atlas-engines — written by scripts/day1/phase2/02-orchestrator.sh (same content as phase2/01-llama.sh; CONVENTIONS.md §8)."
    echo "# The orchestrator's Engine Arbiter starts and stops engines with: sudo systemctl start|stop|restart llama-server@<key>."
    echo "# Exact commands only: any extra argument (a second unit, --no-block, ...) is refused. Regenerated from config/engines.json."
    for key in "${keys[@]}"; do
      for verb in start stop restart; do
        printf 'atlas ALL=(root) NOPASSWD: %s %s llama-server@%s\n' "$sc" "$verb" "$key"
      done
    done
  } >"$tmp"
  if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment failed visudo -c; not installed"; }
  else
    warn "visudo not found (sudo-rs without it?); installing $frag unchecked"
  fi
  install -m 440 -o root -g root "$tmp" "$frag"
  rm -f "$tmp"
  log "installed $frag ($(( ${#keys[@]} * 3 )) explicit command lines, no wildcard)"
}

_orch_sudoers() {
  local frag=/etc/sudoers.d/atlas-engines
  if [[ -s "$frag" ]] && ! grep -q '\*' "$frag"; then
    log "$frag already present without wildcards (step 01)"
  else
    [[ -s "$frag" ]] && warn "$frag carries a wildcard (sudoers matches arguments as one string: 'llama-server@*' also matches 'stop llama-server@x ufw.service'); regenerating"
    if declare -F _llama_sudoers >/dev/null; then
      _llama_sudoers          # step 01's generator, loaded by run_phase_steps: one source of truth when available
    else
      _orch_sudoers_generate "$frag"
    fi
  fi
  # Content check, not mere presence (fix round): every line is an exact `systemctl <verb> llama-server@<key>`.
  local n
  n="$(grep -cE '^atlas ALL=\(root\) NOPASSWD: /[^ ]*systemctl (start|stop|restart) llama-server@[A-Za-z0-9._-]+$' "$frag" || true)"
  (( n == 30 )) || die "$frag has $n exact command lines, expected 30 (3 verbs x 10 engine keys); refusing a control path that is wider or narrower than CONVENTIONS.md §8"
  grep -q '\*' "$frag" && die "$frag still carries a wildcard after regeneration"
  grep -vE '^(#|atlas ALL=\(root\) NOPASSWD: /[^ ]*systemctl (start|stop|restart) llama-server@[A-Za-z0-9._-]+$|[[:space:]]*$)' "$frag" \
    && die "$frag carries a line that is not a comment or an exact engine command (above)"
  log "$frag verified: 30 exact commands, no wildcard"
}

# --- ledger DB -----------------------------------------------------------------------------------------------------------
_orch_init_db() {
  local db="$ATLAS_SRV/data/orchestrator/atlas.sqlite3"
  orch_admin init-db || die "'atlas-admin init-db' failed (contract in this file's header; see the output above)"
  [[ -s "$db" ]] || die "contract: 'atlas-admin init-db' did not create ATLAS_DB_PATH=$db"
  log "ledger/approval DB initialised: $db ($(stat -c '%U:%G %a' "$db"))"
}

# --- units ---------------------------------------------------------------------------------------------------------------
_orch_units() {
  local u
  export ATLAS_ETC ATLAS_OPT ATLAS_SRV
  for u in "${ORCH_UNITS[@]}"; do
    [[ -f "$ATLAS_DAY1_DIR/systemd/$u.service" ]] || die "$ATLAS_DAY1_DIR/systemd/$u.service is missing"
    render_template -m 644 "$ATLAS_DAY1_DIR/systemd/$u.service" "/etc/systemd/system/$u.service" ATLAS_ETC ATLAS_OPT ATLAS_SRV
  done
  # ${ORCH_HOST}/${ORCH_PORT}/${CELERY_CPU_CONCURRENCY} must reach systemd untouched (they come from orchestrator.env).
  # shellcheck disable=SC2016  # the literal ${...} text is what the rendered unit must still contain
  grep -qF '--host ${ORCH_HOST} --port ${ORCH_PORT}' /etc/systemd/system/atlas-orchestrator.service \
    || die "render_template mangled \${ORCH_HOST}/\${ORCH_PORT} in atlas-orchestrator.service"
  # shellcheck disable=SC2016  # same: systemd, not the shell, expands this one
  grep -qF -- '--concurrency=${CELERY_CPU_CONCURRENCY}' /etc/systemd/system/atlas-celery-cpu.service \
    || die "render_template mangled \${CELERY_CPU_CONCURRENCY} in atlas-celery-cpu.service"
  grep -qF -- '--concurrency=1 ' /etc/systemd/system/atlas-celery-gpu.service \
    || die "atlas-celery-gpu.service must run with --concurrency=1 (Section 9.7)"
  for u in "${ORCH_UNITS[@]}"; do
    grep -qF "EnvironmentFile=$REDIS_ENV" "/etc/systemd/system/$u.service" \
      || die "$u.service does not load $REDIS_ENV (the broker URL lives there; the unit template lost the line)"
    grep -qF "Environment=HOME=$(getent passwd atlas | cut -d: -f6)" "/etc/systemd/system/$u.service" \
      || die "$u.service does not set HOME to the atlas account's home $(getent passwd atlas | cut -d: -f6) (phase1/03-mounts.sh; the docker client's proxy config lives there)"
  done
  # The secrets-dir contract made loud at unit start (header): the workers test the traversal as atlas before running.
  for u in atlas-celery-cpu atlas-celery-gpu; do
    grep -qF "ExecStartPre=/bin/sh -c 'test -x $ATLAS_ETC/secrets" "/etc/systemd/system/$u.service" \
      || die "$u.service lost its secrets-readability ExecStartPre (the loud guard for the root:atlas $ORCH_SECRETS_MODE directory contract)"
  done
  systemctl daemon-reload
  for u in "${ORCH_UNITS[@]}"; do
    systemctl reset-failed "$u" 2>/dev/null || true
    systemctl enable --quiet "$u"
    systemctl restart "$u" || { journalctl -u "$u" --no-pager -n 40 >&2 || true; die "$u failed to start (journal above)"; }
  done
  log "units enabled and (re)started: ${ORCH_UNITS[*]}"
}

_orch_health() {
  local url="http://127.0.0.1:$ORCH_PORT"
  if ! wait_http "$url/health" 180; then
    journalctl -u atlas-orchestrator --no-pager -n 60 >&2 || true
    die "the orchestrator did not answer 200 on $url/health within 180 s (journal above)"
  fi
  local models
  models="$(curl -s --noproxy '*' --max-time 20 "$url/v1/models" || true)"
  python3 - "$models" <<'PY' || die "GET $url/v1/models does not list atlas, ren and arthur (Section 12.1): ${models:0:300}"
import json, sys
d = json.loads(sys.argv[1])
ids = {m["id"] for m in d.get("data", [])}
missing = [m for m in ("atlas", "ren", "arthur") if m not in ids]
if missing:
    print("missing model ids:", missing, "have:", sorted(ids), file=sys.stderr)
    sys.exit(1)
print("models:", sorted(ids))
PY
  log "orchestrator healthy on $url: /health 200, /v1/models lists atlas, ren, arthur"

  # Both workers must be connected to the broker (celery inspect ping, VERIFIED command; 30 s timeout).
  local ping
  ping="$(orch_as_atlas "$ORCH_VENV/bin/celery" -A atlas.celery_app inspect ping --timeout 30 2>&1 || true)"
  # Reply format (VERIFIED celery docs): "->  cpu@<host>: OK"; a node name appears only in a reply.
  local q
  for q in cpu gpu; do
    grep -qE "${q}@[^[:space:]]+:" <<<"$ping" \
      || { journalctl -u "atlas-celery-$q" --no-pager -n 40 >&2 || true; die "celery inspect ping did not hear the $q worker: ${ping:0:400}"; }
  done
  systemctl is-active --quiet atlas-celery-beat || die "atlas-celery-beat is not active ($(journalctl -u atlas-celery-beat --no-pager -n 20 2>&1 | tail -n 5))"
  log "celery workers answered ping (cpu, gpu); beat active"
}

step_02() {
  [[ -n "${ORCH_PORT:-}" && -n "${OPENWEBUI_PORT:-}" ]] || die "ORCH_PORT/OPENWEBUI_PORT are empty (load_env)"
  id -u atlas >/dev/null 2>&1 || die "service account 'atlas' does not exist (Phase 1 step 6)"
  _orch_preflight_smb_cred
  _orch_apt
  _core_env_write
  _orch_redis_secret
  _orch_redis_up
  _orch_sync_source
  _orch_venv
  _orch_env_write
  _orch_sudoers
  _orch_init_db
  _orch_units
  _orch_health
  notify "Phase 2 step 2 done: Redis (auth), orchestrator on 127.0.0.1:$ORCH_PORT, Celery cpu/gpu workers, beat"
  log "step 02 done: $ORCH_VENV, $ORCH_DIR, $ORCH_ENV, $REDIS_ENV, $CORE_ENV, units ${ORCH_UNITS[*]}"
}
