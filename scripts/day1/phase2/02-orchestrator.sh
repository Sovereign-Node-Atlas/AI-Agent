#!/usr/bin/env bash
# phase2/02-orchestrator.sh — Section 17 Phase 2 step 2: Redis, Celery workers (cpu, gpu queues), the orchestrator
# scaffold with the Engine Arbiter (4.2), router (7.1), approval queue (16.2) and task ledger (9.7). Sourced by
# phase2-services.sh through run_phase_steps; defines step_02 only (plus helpers other steps reuse).
#
# What it does, in order (each part idempotent):
#   1. apt: python3-venv/pip, build tools, rsync.
#   2. $ATLAS_ETC/core.env — the ONE compose interpolation file for docker/core/compose.yml: a superset of Phase 1's
#      $ATLAS_ETC/docker.env plus the data-dir and port keys (see _core_env_write). Data directories created.
#   3. Redis from docker/core/compose.yml (redis:8.10.2, published on 127.0.0.1:6379 only), PING-tested.
#   4. The `atlas` package: scripts/day1/orchestrator (mirrored to $ATLAS_OPT/day1/orchestrator by atlas-day1.sh) is
#      synced to $ATLAS_OPT/orchestrator (owner atlas; CONVENTIONS.md §2, Appendix C include set) and installed in
#      editable mode into $ATLAS_OPT/venv. The step FAILS LOUDLY when pyproject.toml is missing: that package is
#      written by other writers against CONVENTIONS.md; this step installs what is there.
#   5. $ATLAS_ETC/orchestrator.env (root:atlas 640): every setting the package and the units read (list below).
#   6. /etc/sudoers.d/atlas-engines (CONVENTIONS.md §8 control path) if step 01 did not already write it.
#   7. `atlas-admin init-db` creates the SQLite ledger/approval DB at ATLAS_DB_PATH under $ATLAS_SRV/data.
#   8. Units installed and started: atlas-orchestrator, atlas-celery-cpu (concurrency nproc-2), atlas-celery-gpu
#      (concurrency exactly 1, Section 9.7), atlas-celery-beat (package-owned schedules only; the Sentinel, prune and
#      AEGIS schedules are systemd timers, CONVENTIONS.md §8). Then /health must answer 200, /v1/models must list
#      atlas, ren and arthur, and both Celery workers must answer `celery inspect ping`.
#
# CONTRACT with the `atlas` package (CONVENTIONS.md does not state it; the package writer codes against this):
#   * orchestrator/pyproject.toml declares console scripts `atlas-orchestrator` and `atlas-admin`, and depends on
#     celery[redis]==5.6.3 and redis==8.1.0 (research §3 pins; CONVENTIONS.md §7.9) so `celery` lands in the venv.
#   * `atlas-orchestrator --host H --port P`: GET /health -> 200 when ready; GET /v1/models -> {"data":[{"id":"atlas"},
#     {"id":"ren"},{"id":"arthur"}, ...]}; POST /v1/chat/completions (Section 12.1).
#   * `atlas-admin init-db`: creates the SQLite ledger/approval DB at $ATLAS_DB_PATH (idempotent).
#   * `atlas-admin enqueue <sentinel|prune|aegis-freeze|aegis-thaw|chat-retention> [--wait SECONDS]`: sends the task
#     to Celery; exit 0 when accepted (or, with --wait, when finished in time); with --wait prints the ledger record
#     of the run as ONE JSON object line on stdout.
#   * module `atlas.celery_app` exposes the Celery app as `app` with task_default_queue "cpu", GPU tasks routed to
#     "gpu", and beat_schedule holding ONLY package-owned schedules (chat-retention nightly), never sentinel/prune/aegis.
#   * settings are read from the environment; the keys are the ones written by _orch_env_write below.
#   * src/atlas/openwebui_filter.py is a valid Open WebUI Filter (class Filter, Valves.ORCHESTRATOR_URL); step 03 installs it.
# Contracts relied on from other writers: Phase 1 step 6 wrote $ATLAS_ETC/docker.env and the atlas account (groups
# docker, render, video); step 01 wrote /etc/sudoers.d/atlas-engines (re-created here if absent, same content).
# Contract this file defines for others: $ATLAS_ETC/core.env and the `core_compose` helper
# (`core_compose up -d <service>`), used by step 03; step 04 uses the bare `docker compose -f` form and step 05 merges
# docker/core/compose.voice.yml into the same project (compose.yml carries `name: atlas-core` and the `atlas` network).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

ORCH_VENV="$ATLAS_OPT/venv"
ORCH_SRC="$ATLAS_DAY1_DIR/orchestrator"
ORCH_DIR="$ATLAS_OPT/orchestrator"
ORCH_ENV="$ATLAS_ETC/orchestrator.env"
CORE_COMPOSE="$ATLAS_DAY1_DIR/docker/core/compose.yml"
CORE_ENV="$ATLAS_ETC/core.env"
ORCH_UNITS=(atlas-orchestrator atlas-celery-cpu atlas-celery-gpu atlas-celery-beat)

# core_compose ARGS... — docker compose against docker/core/compose.yml with the shared interpolation file.
core_compose() {
  [[ -f "$CORE_COMPOSE" ]] || die "core_compose: $CORE_COMPOSE is missing"
  [[ -s "$CORE_ENV" ]] || die "core_compose: $CORE_ENV is missing (phase2/02-orchestrator.sh writes it)"
  docker compose -f "$CORE_COMPOSE" --env-file "$CORE_ENV" "$@"
}

# orch_admin ARGS... — run atlas-admin as the atlas user with orchestrator.env (and memory.env when present) loaded.
orch_admin() {
  [[ -x "$ORCH_VENV/bin/atlas-admin" ]] || die "orch_admin: $ORCH_VENV/bin/atlas-admin missing (step 02 contract)"
  local cmd
  cmd="set -a; source '$ORCH_ENV'; [ -f '$ATLAS_ETC/memory.env' ] && source '$ATLAS_ETC/memory.env'; set +a; cd '$ORCH_DIR'; exec '$ORCH_VENV/bin/atlas-admin' \"\$@\""
  svc_user_run /bin/bash -c "$cmd" atlas-admin "$@"
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

# --- core.env: one interpolation file for docker/core/compose.yml -------------------------------------------------------
_core_env_write() {
  [[ -s "$ATLAS_ETC/docker.env" ]] || die "$ATLAS_ETC/docker.env is missing (Phase 1 step 6 writes it)"
  local embed_port
  embed_port="$(engine_port_of embed-bge-m3)" || die "cannot compute the embed-bge-m3 port from config/engines.json"
  {
    echo "# Written by ATLAS Phase 2 step 2 (phase2/02-orchestrator.sh). Compose interpolation for docker/core/compose.yml:"
    echo "#   docker compose -f $CORE_COMPOSE --env-file $CORE_ENV up -d <service>"
    echo "# Superset of $ATLAS_ETC/docker.env (Phase 1 step 6). Not a secret (no tokens). Re-generated on every run of step 2."
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
  } | install -m 644 -o root -g root /dev/stdin "$CORE_ENV"
  log "wrote $CORE_ENV (embed port $embed_port, Open WebUI port $OPENWEBUI_PORT, orchestrator port $ORCH_PORT)"

  # Data directories the compose file bind-mounts (Appendix C: all under $ATLAS_SRV/data are in the restic set).
  ensure_dir "$ATLAS_SRV/data" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/data/redis" root:root 750           # redis image runs as root (VERIFIED image default)
  ensure_dir "$ATLAS_SRV/data/chroma" root:root 750          # chroma image writes /data as its own user; root-created is safe
  ensure_dir "$ATLAS_SRV/data/open-webui" root:root 750      # the Open WebUI image runs as UID 0 (research 1.1)
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/data/orchestrator" atlas:atlas 750
  ensure_dir "$ATLAS_SRV/data/sentinel" atlas:atlas 750
  ensure_dir /srv/cold atlas:atlas 750
}

# --- Redis ---------------------------------------------------------------------------------------------------------------
_orch_redis_up() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  grep -qE '^[[:space:]]+redis:' "$CORE_COMPOSE" || die "$CORE_COMPOSE defines no 'redis' service"
  proxy_env
  log "docker compose up -d redis (redis:8.10.2, 127.0.0.1:6379; image pull through the proxy)"
  retry 3 core_compose up -d --quiet-pull redis \
    || die "docker compose up redis failed (registry-1.docker.io / the Docker Hub blob CDN must be allowlisted): $(core_compose logs --tail 20 redis 2>&1 | tail -n 20)"
  local pong=""
  for _ in $(seq 1 30); do
    pong="$(docker exec atlas-redis redis-cli ping 2>/dev/null || true)"
    [[ "$pong" == PONG ]] && break
    sleep 2
  done
  [[ "$pong" == PONG ]] || die "redis did not answer PING inside the container after 60 s (docker logs atlas-redis)"
  # The host-side Celery workers connect to the published loopback port; prove it from the host.
  timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/6379' 2>/dev/null \
    || die "127.0.0.1:6379 is not reachable from the host although the container answers PING (published port missing?)"
  local published
  published="$(docker port atlas-redis 6379/tcp 2>/dev/null | tr '\n' ' ' || true)"
  grep -q '127.0.0.1:6379' <<<"$published" || die "redis is published on '$published', expected 127.0.0.1:6379 only (CONVENTIONS.md §8: loopback only)"
  log "redis up: PONG, published on $published"
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
  chown -R atlas:atlas "$ORCH_DIR"
  log "synced $ORCH_SRC -> $ORCH_DIR (owner atlas)"
}

_pip_orch() {
  proxy_env
  export PIP_CACHE_DIR="$ATLAS_STATE/pip-cache" PIP_DISABLE_PIP_VERSION_CHECK=1
  mkdir -p "$PIP_CACHE_DIR"
  retry 3 "$ORCH_VENV/bin/python" -m pip install --quiet "$@"
}

_orch_venv() {
  if [[ ! -x "$ORCH_VENV/bin/python" ]]; then
    log "creating $ORCH_VENV with python3 -m venv ($(python3 --version 2>&1))"
    mkdir -p "$ATLAS_OPT"
    python3 -m venv "$ORCH_VENV" || die "python3 -m venv $ORCH_VENV failed"
  fi
  [[ -x "$ORCH_VENV/bin/pip" ]] || "$ORCH_VENV/bin/python" -m ensurepip --upgrade || die "$ORCH_VENV has no pip and ensurepip failed"
  _pip_orch --upgrade pip || die "pip self-upgrade failed in $ORCH_VENV (pypi.org / files.pythonhosted.org through the proxy?)"
  log "pip install -e $ORCH_DIR (the atlas package and its pinned dependencies; several minutes on first run)"
  _pip_orch -e "$ORCH_DIR" || die "pip install -e $ORCH_DIR failed (see above: a dependency without a wheel for $("$ORCH_VENV/bin/python" --version 2>&1)? the package's pyproject.toml is the other writer's file)"
  local b
  for b in atlas-orchestrator atlas-admin celery; do
    [[ -x "$ORCH_VENV/bin/$b" ]] || die "contract: $ORCH_VENV/bin/$b is missing after the editable install (pyproject.toml must declare the console scripts atlas-orchestrator and atlas-admin and depend on celery[redis]==5.6.3)"
  done
  "$ORCH_VENV/bin/python" -c 'import atlas, atlas.celery_app; assert hasattr(atlas.celery_app, "app"), "atlas.celery_app.app missing"' \
    || die "contract: 'import atlas.celery_app' fails or exposes no 'app' in $ORCH_VENV (see the traceback above)"
  chown -R atlas:atlas "$ORCH_VENV"
  log "venv ready: $("$ORCH_VENV/bin/python" -c 'import importlib.metadata as m; print("atlas", m.version("atlas"))' 2>/dev/null || echo 'atlas (version unknown)'), celery $("$ORCH_VENV/bin/celery" --version 2>/dev/null | head -n1)"
}

# --- orchestrator.env ----------------------------------------------------------------------------------------------------
_orch_env_write() {
  local cpu_conc
  cpu_conc=$(( $(nproc) - 2 ))
  (( cpu_conc >= 1 )) || cpu_conc=1
  [[ -e "$ORCH_ENV" ]] || { : >"$ORCH_ENV"; }
  # ensure_kv keeps keys other steps add (voice, tools, google) and rewrites only these.
  ensure_kv "$ORCH_ENV" ORCH_HOST 127.0.0.1
  ensure_kv "$ORCH_ENV" ORCH_PORT "$ORCH_PORT"
  ensure_kv "$ORCH_ENV" ORCH_URL "http://127.0.0.1:$ORCH_PORT"
  ensure_kv "$ORCH_ENV" REDIS_URL "redis://127.0.0.1:6379/0"
  ensure_kv "$ORCH_ENV" CELERY_BROKER_URL "redis://127.0.0.1:6379/0"
  ensure_kv "$ORCH_ENV" CELERY_RESULT_BACKEND "redis://127.0.0.1:6379/1"
  ensure_kv "$ORCH_ENV" CELERY_CPU_CONCURRENCY "$cpu_conc"
  ensure_kv "$ORCH_ENV" CELERY_GPU_CONCURRENCY 1
  ensure_kv "$ORCH_ENV" ATLAS_DB_PATH "$ATLAS_SRV/data/orchestrator/atlas.sqlite3"
  ensure_kv "$ORCH_ENV" ATLAS_CONFIG_DIR "$ATLAS_DAY1_DIR/config"
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
  ensure_kv "$ORCH_ENV" SENTINEL_FEEDS "$ATLAS_DAY1_DIR/config/sentinel-feeds.json"
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
  chown root:atlas "$ORCH_ENV"
  chmod 640 "$ORCH_ENV"
  log "wrote $ORCH_ENV (cpu workers $cpu_conc, gpu workers 1, db $ATLAS_SRV/data/orchestrator/atlas.sqlite3)"
}

# --- sudoers (same content as step 01; only written when absent) ---------------------------------------------------------
_orch_sudoers() {
  local frag=/etc/sudoers.d/atlas-engines sc tmp
  if [[ -s "$frag" ]]; then
    log "$frag already present (step 01)"
    return 0
  fi
  sc="$(readlink -f "$(command -v systemctl)")"
  tmp="$(mktemp)"
  cat >"$tmp" <<SUDO
# atlas-engines — written by scripts/day1/phase2/02-orchestrator.sh (CONVENTIONS.md §8). The orchestrator's Engine
# Arbiter starts and stops engines with: sudo systemctl start|stop|restart llama-server@<key>. Nothing else is permitted.
atlas ALL=(root) NOPASSWD: $sc start llama-server@*
atlas ALL=(root) NOPASSWD: $sc stop llama-server@*
atlas ALL=(root) NOPASSWD: $sc restart llama-server@*
SUDO
  if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment failed visudo -c; not installed"; }
  else
    warn "visudo not found (sudo-rs without it?); installing $frag unchecked"
  fi
  install -m 440 -o root -g root "$tmp" "$frag"
  rm -f "$tmp"
  log "installed $frag"
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
  ping="$(svc_user_run /bin/bash -c "set -a; source '$ORCH_ENV'; set +a; cd '$ORCH_DIR'; exec '$ORCH_VENV/bin/celery' -A atlas.celery_app inspect ping --timeout 30" 2>&1 || true)"
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
  _orch_apt
  _core_env_write
  _orch_redis_up
  _orch_sync_source
  _orch_venv
  _orch_env_write
  _orch_sudoers
  _orch_init_db
  _orch_units
  _orch_health
  notify "Phase 2 step 2 done: Redis, orchestrator on 127.0.0.1:$ORCH_PORT, Celery cpu/gpu workers, beat"
  log "step 02 done: $ORCH_VENV, $ORCH_DIR, $ORCH_ENV, $CORE_ENV, units ${ORCH_UNITS[*]}"
}
