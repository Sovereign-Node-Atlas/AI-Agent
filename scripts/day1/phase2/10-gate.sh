#!/usr/bin/env bash
# phase2/10-gate.sh — Section 17 Phase 2 step 10: the gate. "Every service healthy; V3 second half, V6, V7 (deferred
# if the reference recordings do not exist yet), V12, V20, V23 recorded. The Arbiter's refusal logic is unit-tested
# here against stub footprints" — plus V13, V14a, V15, V16, V17, V18 per CONVENTIONS.md §6, and V10a (the resident
# router half of V10, Section 21). Sourced by phase2-services.sh through run_phase_steps; defines step_10 only.
#
# Order:
#   1. Pre-gate prerequisites that no earlier step owns: the AEGIS sandbox image (docker/sandbox/Dockerfile ->
#      atlas-sandbox:py3.12, Section 16.4) built when absent and its SANDBOX_* keys added to orchestrator.env;
#      pytest present in /opt/atlas/venv for V14a/V15/V16. The orchestrator is restarted once if its env changed.
#   2. Health of every service BY NAME: systemctl is-active for each unit, docker compose ps + docker inspect for
#      each container, HTTP /health (or the documented equivalent) for the orchestrator, Kokoro, speaches, ChromaDB,
#      the three resident llama-servers, Open WebUI, ntfy and docling. Printed as a table; failures collected.
#   3. run_verify for V3b, V10a, V6, V7, V12, V13, V14a, V15, V16, V17, V18, V20, V23 (every one runs even when an
#      earlier one fails; a fail is recorded, never omitted, rule §7.4).
#   4. An unhealthy service stops here with the verify table and the list (no gate marker is written); otherwise
#      `gate phase2` with V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 required and V7 V10a recorded only, and the
#      Phase 3 start command is printed.
#
# Contracts relied on from other writers (all listed in phase2/README-contracts.md): unit names from
# phase2/02-orchestrator.sh (atlas-orchestrator, atlas-celery-cpu/gpu/beat), 01-llama.sh + 04-memory.sh
# (llama-server@<resident key>, $ATLAS_ETC/engines/<key>.env with LLAMA_ARG_PORT), 07-restic.sh (atlas-aegis.timer,
# atlas-restic-check.timer), 08-sentinel.sh (atlas-sentinel.timer, atlas-prune.timer), 09-windows-share.sh
# (srv-atlas-winpc.automount, only when installed), Phase 1 (docker, squid, cockpit.socket, xrdp, ssh, atlas-ddns.timer,
# atlas-docker-egress.service); container names from docker/core/compose.yml (atlas-redis, atlas-chromadb,
# atlas-openwebui, atlas-kokoro, atlas-speaches, atlas-docling — compose.voice.yml names the last three kokoro,
# speaches, docling: both spellings are accepted), docker/ntfy/compose.yml (atlas-ntfy), docker/wg-easy (wg-easy);
# verify script names from CONVENTIONS.md §5 and the writers' usage lines (v12 takes CONTAINER WINDOW_S).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

SANDBOX_IMAGE="atlas-sandbox:py3.12"                 # services-tools.md S9 name; built from docker/sandbox/Dockerfile
GATE_UNHEALTHY=()
GATE_HEALTH_ROWS=()

_gate_row() { GATE_HEALTH_ROWS+=("$(printf '%-12s %-36s %s' "$1" "$2" "$3")"); }

# --- 1. prerequisites ---------------------------------------------------------------------------------------------------
_gate_prepare_sandbox() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  local ctx="$ATLAS_DAY1_DIR/docker/sandbox"
  [[ -f "$ctx/Dockerfile" ]] || die "$ctx/Dockerfile is missing"
  if [[ "$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.version"}}' "$SANDBOX_IMAGE" 2>/dev/null)" == "1" ]]; then
    log "$SANDBOX_IMAGE already built"
  else
    proxy_env
    log "docker build $SANDBOX_IMAGE from $ctx (python:3.12-slim through the proxy; docker.io / registry-1.docker.io must be allowlisted)"
    # The base image pull uses the daemon's proxy (daemon.json, Phase 1 step 6); the build itself needs no network.
    docker build --network none -t "$SANDBOX_IMAGE" "$ctx" \
      || die "docker build of $SANDBOX_IMAGE failed (pull of python:3.12-slim through the proxy? see the output above)"
  fi
  local out
  out="$(docker run --rm --network none --read-only --user 65534:65534 "$SANDBOX_IMAGE" python3 -c 'import sys; print(sys.version.split()[0])' 2>&1)" \
    || die "docker run $SANDBOX_IMAGE python3 failed: $out"
  log "sandbox image $SANDBOX_IMAGE runs python $out as nobody, read-only, no network"
  # Contract for the orchestrator (README-contracts.md "Sandbox"): the run line it must use, as KEY=VALUE settings.
  local orch="$ATLAS_ETC/orchestrator.env"
  [[ -e "$orch" ]] || die "$orch missing: Phase 2 step 2 has not run"
  ensure_dir "$ATLAS_SRV/sandbox" atlas:atlas 750
  ensure_kv "$orch" SANDBOX_IMAGE "$SANDBOX_IMAGE"
  ensure_kv "$orch" SANDBOX_DIR "$ATLAS_SRV/sandbox"
  ensure_kv "$orch" SANDBOX_MEMORY 2g
  ensure_kv "$orch" SANDBOX_CPUS 2
  ensure_kv "$orch" SANDBOX_PIDS 256
  ensure_kv "$orch" SANDBOX_TMPFS_SIZE 512m
  ensure_kv "$orch" SANDBOX_TIMEOUT_S 300
}

_gate_ensure_pytest() {
  local py="$ATLAS_OPT/venv/bin/python"
  [[ -x "$py" ]] || die "$py missing: Phase 2 step 2 (the orchestrator venv) has not run"
  if "$py" -m pytest --version >/dev/null 2>&1; then
    log "pytest present in $ATLAS_OPT/venv: $("$py" -m pytest --version 2>&1 | head -n1)"
    return 0
  fi
  # The orchestrator's pyproject.toml should declare pytest (CONVENTIONS.md §7.8); when it does not, install it here
  # so V14a/V15/V16 can run. UNVERIFIED pin: the research names no pytest version, so it is unpinned (README says so).
  proxy_env
  warn "pytest is not in $ATLAS_OPT/venv (the orchestrator's pyproject.toml should declare it); installing it unpinned"
  svc_user_run "$py" -m pip install --no-cache-dir -q pytest || die "pip install pytest into $ATLAS_OPT/venv failed (pypi.org / files.pythonhosted.org allowlisted?)"
  "$py" -m pytest --version >/dev/null 2>&1 || die "pytest still not importable in $ATLAS_OPT/venv after the install"
}

_gate_orchestrator_refresh() {
  # orchestrator.env may have gained SANDBOX_* keys above (and VAULT_* in step 9b): restart once so the service sees
  # them, then require /health again. Skipped when nothing changed since the service started.
  local orch="$ATLAS_ETC/orchestrator.env" port="${ORCH_PORT:-8800}"
  systemctl is-active --quiet atlas-orchestrator || return 0
  local started env_mtime
  started="$(systemctl show -p ActiveEnterTimestampMonotonic --value atlas-orchestrator 2>/dev/null || true)"
  [[ "$started" =~ ^[0-9]+$ ]] || started=0
  env_mtime="$(stat -c %Y "$orch")"
  local boot_epoch now_mono
  now_mono="$(cut -d' ' -f1 /proc/uptime | cut -d. -f1)"
  boot_epoch=$(( $(date +%s) - now_mono ))
  local started_epoch=$(( boot_epoch + started / 1000000 ))
  if (( env_mtime > started_epoch )); then
    log "orchestrator.env changed after atlas-orchestrator started; restarting the service once"
    systemctl restart atlas-orchestrator || die "systemctl restart atlas-orchestrator failed"
    wait_http "http://127.0.0.1:$port/health" 180 || die "the orchestrator did not answer 200 on /health within 180 s after the restart (journalctl -u atlas-orchestrator)"
  fi
}

# --- 2. health by name --------------------------------------------------------------------------------------------------
_gate_unit() {
  local u="$1" st
  st="$(systemctl is-active "$u" 2>/dev/null || true)"
  if [[ "$st" == active ]]; then
    _gate_row unit "$u" "active"
  else
    _gate_row unit "$u" "NOT HEALTHY: ${st:-unknown}"
    GATE_UNHEALTHY+=("unit $u: ${st:-unknown}")
  fi
}

# _gate_container SERVICE NAME... — the first existing container name wins; running and (if it has one) healthy.
_gate_container() {
  local svc="$1"; shift
  local n found="" st health
  for n in "$@"; do
    if docker inspect "$n" >/dev/null 2>&1; then found="$n"; break; fi
  done
  if [[ -z "$found" ]]; then
    _gate_row container "$svc" "NOT HEALTHY: no container named $* exists"
    GATE_UNHEALTHY+=("container $svc: none of ($*) exists")
    return 0
  fi
  st="$(docker inspect -f '{{.State.Status}}' "$found" 2>/dev/null || echo unknown)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$found" 2>/dev/null || echo unknown)"
  if [[ "$st" == running && ( "$health" == healthy || "$health" == none ) ]]; then
    _gate_row container "$svc ($found)" "running, health=$health"
  else
    _gate_row container "$svc ($found)" "NOT HEALTHY: status=$st health=$health"
    GATE_UNHEALTHY+=("container $svc ($found): status=$st health=$health")
  fi
}

_gate_http() {
  local name="$1" url="$2" code
  code="$(curl -s --noproxy '*' --max-time 15 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || true)"
  if [[ "$code" == 200 ]]; then
    _gate_row http "$name" "200 $url"
  else
    _gate_row http "$name" "NOT HEALTHY: HTTP ${code:-none} $url"
    GATE_UNHEALTHY+=("http $name: HTTP ${code:-none} at $url")
  fi
}

# _gate_engine_port KEY — LLAMA_ARG_PORT from $ATLAS_ETC/engines/KEY.env, else LLAMA_PORT_BASE + engines.json index.
_gate_engine_port() {
  local key="$1" envf="$ATLAS_ETC/engines/$1.env" port=""
  [[ -r "$envf" ]] && port="$(sed -nE 's/^LLAMA_ARG_PORT=([0-9]+)$/\1/p' "$envf" | head -n1)"
  if [[ -z "$port" ]]; then
    port="$(python3 - "$ATLAS_DAY1_DIR/config/engines.json" "$key" "${LLAMA_PORT_BASE:-8100}" <<'PY' 2>/dev/null || true
import json
import sys

for i, e in enumerate(json.load(open(sys.argv[1], encoding="utf-8"))["engines"], start=1):
    if e["key"] == sys.argv[2]:
        print(int(sys.argv[3]) + i)
PY
)"
  fi
  printf '%s\n' "$port"
}

_gate_services() {
  log "health check of every service by name"
  local u
  for u in docker squid cockpit.socket xrdp atlas-ddns.timer atlas-docker-egress.service \
           atlas-orchestrator atlas-celery-cpu atlas-celery-gpu atlas-celery-beat \
           llama-server@router-qwen3.5-4b llama-server@embed-bge-m3 llama-server@rerank-bge-v2-m3 \
           atlas-sentinel.timer atlas-prune.timer atlas-aegis.timer atlas-restic-check.timer; do
    _gate_unit "$u"
  done
  # ssh is socket-activated on recent Ubuntu: either the service or the socket being active is healthy.
  if systemctl is-active --quiet ssh.service || systemctl is-active --quiet ssh.socket; then
    _gate_row unit ssh "active ($(systemctl is-active ssh.service 2>/dev/null || true)/$(systemctl is-active ssh.socket 2>/dev/null || true))"
  else
    _gate_row unit ssh "NOT HEALTHY: neither ssh.service nor ssh.socket is active"
    GATE_UNHEALTHY+=("unit ssh: inactive")
  fi
  # The Windows share automount exists only when step 9 installed it (WINDOWS_SHARE may be the documented opt-out).
  if [[ -f /etc/systemd/system/srv-atlas-winpc.automount ]]; then
    _gate_unit srv-atlas-winpc.automount
  else
    _gate_row unit srv-atlas-winpc.automount "not installed (step 9 opted out)"
  fi
  # The vault unit is healthy when INACTIVE: it runs only while the vault is open (step 9b).
  if systemctl is-active --quiet atlas-vault.service; then
    _gate_row unit atlas-vault.service "active (vault OPEN; it auto-locks after idle)"
  else
    _gate_row unit atlas-vault.service "inactive (vault locked, as expected)"
  fi

  # Containers: compose ps for the record, docker inspect for the decision.
  local core="$ATLAS_DAY1_DIR/docker/core/compose.yml" args=() f
  if [[ -f "$core" ]]; then
    args=(-f "$core")
    [[ -f "$ATLAS_DAY1_DIR/docker/core/compose.voice.yml" ]] && args+=(-f "$ATLAS_DAY1_DIR/docker/core/compose.voice.yml")
    for f in "$ATLAS_ETC/core.env" "$ATLAS_ETC/docker.env" "$ATLAS_ETC/voice.env"; do
      [[ -f "$f" ]] && args+=(--env-file "$f")
    done
    log "docker compose ps (core project):"
    docker compose "${args[@]}" ps 2>&1 | sed 's/^/    /' || warn "docker compose ps failed for the core project (names checked individually below)"
  fi
  _gate_container redis atlas-redis
  _gate_container chromadb atlas-chromadb
  _gate_container open-webui atlas-openwebui
  _gate_container kokoro atlas-kokoro kokoro
  _gate_container speaches atlas-speaches speaches
  _gate_container docling atlas-docling docling
  _gate_container ntfy atlas-ntfy
  _gate_container wg-easy wg-easy
  # Redis has no HTTP endpoint: PING through the container.
  local pong
  pong="$(docker exec atlas-redis redis-cli ping 2>/dev/null || true)"
  if [[ "$pong" == PONG ]]; then _gate_row rpc "redis PING" "PONG"; else _gate_row rpc "redis PING" "NOT HEALTHY: ${pong:-no answer}"; GATE_UNHEALTHY+=("redis: PING answered '${pong:-nothing}'"); fi

  # HTTP endpoints (CONVENTIONS.md §8 ports; the paths are the ones the writers' steps wait on).
  _gate_http orchestrator "http://127.0.0.1:${ORCH_PORT:-8800}/health"
  _gate_http open-webui "http://127.0.0.1:${OPENWEBUI_PORT:-3000}/health"
  _gate_http chromadb "http://127.0.0.1:8000/api/v2/heartbeat"
  _gate_http kokoro "http://127.0.0.1:8880/health"
  _gate_http speaches "http://127.0.0.1:8881/health"
  _gate_http ntfy "http://127.0.0.1:8090/v1/health"
  _gate_http docling "http://127.0.0.1:5001/docs"
  local key port
  for key in router-qwen3.5-4b embed-bge-m3 rerank-bge-v2-m3; do
    port="$(_gate_engine_port "$key")"
    if [[ -n "$port" ]]; then
      _gate_http "llama-server@$key" "http://127.0.0.1:$port/health"
    else
      _gate_row http "llama-server@$key" "NOT HEALTHY: port unknown ($ATLAS_ETC/engines/$key.env and engines.json)"
      GATE_UNHEALTHY+=("llama-server@$key: port unknown")
    fi
  done

  echo
  echo "PHASE 2 SERVICE HEALTH"
  printf '%-12s %-36s %s\n' KIND NAME STATE
  printf '%s\n' "${GATE_HEALTH_ROWS[@]}"
  echo
  if (( ${#GATE_UNHEALTHY[@]} > 0 )); then
    warn "${#GATE_UNHEALTHY[@]} service(s) not healthy: ${GATE_UNHEALTHY[*]}"
  else
    log "every service healthy (${#GATE_HEALTH_ROWS[@]} checks)"
  fi
}

# --- 3. verifications -----------------------------------------------------------------------------------------------------
_gate_verifies() {
  # Each one is recorded whatever the others did; the message in verify.jsonl is the evidence.
  run_verify V3b v03b-llama-devices.sh || warn "V3b fail recorded"
  run_verify V10a v10a-router-resident.sh || warn "V10a fail recorded"
  run_verify V6 v06-pyannote.sh || warn "V6 fail recorded"
  run_verify V7 v07-voice-listen.sh || warn "V7 fail recorded"
  run_verify V12 v12-openwebui-offline.sh atlas-openwebui 120 || warn "V12 fail recorded"
  run_verify V13 v13-restic.sh || warn "V13 fail recorded"
  run_verify V14a v14a-arbiter-stubs.sh || warn "V14a fail recorded"
  run_verify V15 v15-approval-gate.sh || warn "V15 fail recorded"
  run_verify V16 v16-router-hard-rule.sh || warn "V16 fail recorded"
  run_verify V17 v17-sandbox.sh || warn "V17 fail recorded"
  run_verify V18 v18-vault.sh || warn "V18 fail recorded"
  run_verify V20 v20-google.sh || warn "V20 fail recorded"
  run_verify V23 v23-cloudflare-token.sh || warn "V23 fail recorded"
}

# --- 4. the gate --------------------------------------------------------------------------------------------------------
step_10() {
  _gate_prepare_sandbox
  _gate_ensure_pytest
  _gate_orchestrator_refresh
  _gate_services
  _gate_verifies
  if (( ${#GATE_UNHEALTHY[@]} > 0 )); then
    echo
    verify_table V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 V7 V10a
    echo
    echo "PHASE 2 GATE: FAIL — Section 17 requires every service healthy before the table is judged:"
    printf '  - %s\n' "${GATE_UNHEALTHY[@]}"
    echo "Fix them, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2   (the gate step is the only one left to run)"
    rm -f "$ATLAS_DONE_DIR/phase2.gate"
    notify "Phase 2 gate FAIL: ${#GATE_UNHEALTHY[@]} service(s) unhealthy"
    die "Phase 2 gate: ${#GATE_UNHEALTHY[@]} unhealthy service(s): ${GATE_UNHEALTHY[*]}"
  fi
  if gate phase2 V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 -- V7 V10a; then
    echo
    echo "Phase 3 (core LLM pull, ~690 GB, detached under systemd) starts with:"
    echo "    sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
    echo "Follow it with:  journalctl -u atlas-day1-phase3 -f"
    notify "Phase 2 gate PASS. Next: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
    return 0
  fi
  notify "Phase 2 gate FAIL; see the table in the phase log"
  return 1
}
