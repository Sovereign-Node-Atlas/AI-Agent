#!/usr/bin/env bash
# phase2/10-gate.sh — Section 17 Phase 2 step 10: the gate. "Every service healthy; V3 second half, V6, V7 (deferred
# if the reference recordings do not exist yet), V12, V20, V23 recorded. The Arbiter's refusal logic is unit-tested
# here against stub footprints" — plus V13, V14a, V15, V16, V17, V18 per CONVENTIONS.md §6, and the Phase 2 half of
# V10 (Section 21: the resident router "is verified separately at the Phase 2 gate"; recorded as V10 info, the Phase 3
# gate records the real V10). Sourced by phase2-services.sh through run_phase_steps; defines step_10 only.
#
# Order:
#   1. Pre-gate prerequisites that no earlier step owns (recorded, fix round: Section 17 step 10 is the judging step,
#      so a failure here is a WARNING that the matching verification then records as fail — never an abort before the
#      table exists, rule §7.4): the AEGIS sandbox image (docker/sandbox/Dockerfile -> atlas-sandbox:py3.12, Section
#      16.4) built when absent and its SANDBOX_* keys added to orchestrator.env; the package's [dev] extra (pytest) in
#      /opt/atlas/venv for V14a/V15/V16. The orchestrator is restarted once only if orchestrator.env really changed.
#      Moving the image build into step 06 or a 09c step would change files this writer does not own; noted in
#      README-contracts.md §3.
#   2. Health of every service BY NAME: systemctl is-active for each unit, docker compose ps + docker inspect for
#      each container, HTTP /health (or the documented equivalent) for the orchestrator, Kokoro, speaches, ChromaDB,
#      the three resident llama-servers, Open WebUI, ntfy and docling; the CONVENTIONS.md §8 bind rule (Redis 6379 and
#      squid 3128 on loopback only; a wildcard listener is tolerated only behind an active default-deny ufw); the
#      restic exclusion of the vault's plaintext view (§7.2) before V13. Printed as a table; failures collected.
#   3. run_verify for V3b, V6, V7, V12, V13, V14a, V15, V16, V17, V18, V20, V23 and the V10 half (every one runs even
#      when an earlier one fails; a fail is recorded, never omitted, rule §7.4). V18: a real-vault pass recorded by
#      step 9b is kept (the test-vault run would otherwise supersede the stronger evidence); otherwise the unattended
#      test-vault run happens here.
#   4. An unhealthy service stops here with the verify table and the list (no gate marker is written); otherwise
#      `gate phase2` with V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 required and V7 V10 recorded only, and the
#      Phase 3 start command is printed.
#
# Contracts relied on from other writers (all listed in phase2/README-contracts.md): unit names from
# phase2/02-orchestrator.sh (atlas-orchestrator, atlas-celery-cpu/gpu/beat), 01-llama.sh + 04-memory.sh
# (llama-server@<resident key>, $ATLAS_ETC/engines/<key>.env with LLAMA_ARG_PORT), 07-restic.sh (atlas-aegis.timer,
# atlas-restic-check.timer, $ATLAS_ETC/restic-exclude.txt), 08-sentinel.sh (atlas-sentinel.timer, atlas-prune.timer),
# 09-windows-share.sh (srv-atlas-winpc.automount, only when installed), Phase 1 (docker, squid, cockpit.socket, xrdp,
# ssh, atlas-ddns.timer, atlas-docker-egress.service); container names from docker/core/compose.yml (atlas-redis,
# atlas-chromadb, atlas-openwebui) and docker/core/compose.voice.yml (atlas-kokoro, atlas-speaches, atlas-docling; the
# bare kokoro/speaches/docling spellings of an earlier revision are still accepted), docker/ntfy/compose.yml
# (atlas-ntfy), docker/wg-easy (wg-easy); verify script names from CONVENTIONS.md §5 and the writers' usage lines
# (v12 takes CONTAINER WINDOW_S).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

SANDBOX_IMAGE="atlas-sandbox:py3.12"                 # services-tools.md S9 name; built from docker/sandbox/Dockerfile
SANDBOX_IMAGE_VERSION="2"                            # label org.atlas.sandbox.version in the Dockerfile
GATE_UNHEALTHY=()
GATE_HEALTH_ROWS=()
GATE_ENV_CHANGED=0

_gate_row() { GATE_HEALTH_ROWS+=("$(printf '%-12s %-36s %s' "$1" "$2" "$3")"); }
_gate_hash() { [[ -e "$1" ]] && sha256sum "$1" | cut -c1-64 || echo none; }

# --- 1. prerequisites (warn, never die: the verifications below record what is missing) ------------------------------------
_gate_prepare_sandbox() {
  command -v docker >/dev/null || { warn "docker is not installed (Phase 1 step 6); V17 will record the failure"; return 0; }
  local ctx="$ATLAS_DAY1_DIR/docker/sandbox"
  [[ -f "$ctx/Dockerfile" ]] || { warn "$ctx/Dockerfile is missing; V17 will record the failure"; return 0; }
  if [[ "$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.version"}}' "$SANDBOX_IMAGE" 2>/dev/null)" == "$SANDBOX_IMAGE_VERSION" ]]; then
    log "$SANDBOX_IMAGE already built (label version $SANDBOX_IMAGE_VERSION)"
  else
    proxy_env
    log "docker build $SANDBOX_IMAGE from $ctx (python:3.12-slim through the proxy; docker.io / registry-1.docker.io must be allowlisted)"
    # The base image pull uses the daemon's proxy (daemon.json, Phase 1 step 6); the build itself needs no network.
    if ! docker build --network none -t "$SANDBOX_IMAGE" "$ctx"; then
      warn "docker build of $SANDBOX_IMAGE failed (pull of python:3.12-slim through the proxy? see the output above); V17 will record the failure"
      return 0
    fi
  fi
  # python:3.12-slim is unpinned (README-contracts.md §3): log the digest it resolved to so the next revision can pin it.
  local digest
  digest="$(docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}' 2>/dev/null || true)"
  log "sandbox base image resolved to ${digest:-unknown digest} (unpinned; pin FROM python:3.12-slim@sha256:... at the next revision)"
  local out
  if ! out="$(docker run --rm --init --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
               "$SANDBOX_IMAGE" python3 -c 'import sys; print(sys.version.split()[0])' 2>&1)"; then
    warn "docker run $SANDBOX_IMAGE python3 failed: $out; V17 will record the failure"
    return 0
  fi
  log "sandbox image $SANDBOX_IMAGE runs python $out as nobody, read-only, no network, under the in-container timeout entrypoint"
  # Contract for the orchestrator (README-contracts.md "Sandbox"): the run line it must use, as KEY=VALUE settings.
  local orch="$ATLAS_ETC/orchestrator.env" before
  [[ -e "$orch" ]] || { warn "$orch missing: Phase 2 step 2 has not run; SANDBOX_* keys not written"; return 0; }
  ensure_dir "$ATLAS_SRV/sandbox" atlas:atlas 750
  before="$(_gate_hash "$orch")"
  ensure_kv "$orch" SANDBOX_IMAGE "$SANDBOX_IMAGE"
  ensure_kv "$orch" SANDBOX_DIR "$ATLAS_SRV/sandbox"
  ensure_kv "$orch" SANDBOX_MEMORY 2g
  ensure_kv "$orch" SANDBOX_CPUS 2
  ensure_kv "$orch" SANDBOX_PIDS 256
  ensure_kv "$orch" SANDBOX_TMPFS_SIZE 512m
  ensure_kv "$orch" SANDBOX_TIMEOUT_S 300
  ensure_kv "$orch" SANDBOX_FSIZE 1073741824          # --ulimit fsize (bytes): 1 GiB per file a job writes
  [[ "$(_gate_hash "$orch")" == "$before" ]] || GATE_ENV_CHANGED=1
}

_gate_ensure_pytest() {
  local py="$ATLAS_OPT/venv/bin/python"
  [[ -x "$py" ]] || { warn "$py missing: Phase 2 step 2 (the orchestrator venv) has not run; V14a/V15/V16 will record the failure"; return 0; }
  if "$py" -m pytest --version >/dev/null 2>&1; then
    log "pytest present in $ATLAS_OPT/venv: $("$py" -m pytest --version 2>&1 | head -n1)"
    return 0
  fi
  # orchestrator/pyproject.toml declares `dev = ["pytest>=8", "ruff>=0.6"]`: install that extra, nothing ad hoc (§7.9).
  local pkg="$ATLAS_OPT/orchestrator"
  [[ -f "$pkg/pyproject.toml" ]] || { warn "$pkg/pyproject.toml missing; cannot install the [dev] extra; V14a/V15/V16 will record the failure"; return 0; }
  proxy_env
  log "pytest is not in $ATLAS_OPT/venv; installing the package's [dev] extra: pip install -e ${pkg}[dev]"
  if ! PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1 svc_user_run "$py" -m pip install --no-cache-dir -q -e "${pkg}[dev]"; then
    warn "pip install -e ${pkg}[dev] into $ATLAS_OPT/venv failed (pypi.org / files.pythonhosted.org allowlisted?); V14a/V15/V16 will record the failure"
    return 0
  fi
  "$py" -m pytest --version >/dev/null 2>&1 || warn "pytest still not importable in $ATLAS_OPT/venv after the install; V14a/V15/V16 will record the failure"
}

_gate_orchestrator_refresh() {
  # orchestrator.env gained SANDBOX_* keys above: restart once so the service sees them, then require /health again.
  # Only when the file's content really changed (ensure_kv rewrites it on every call, so mtime is not the signal).
  local port="${ORCH_PORT:-8800}"
  systemctl is-active --quiet atlas-orchestrator || return 0
  if [[ "$GATE_ENV_CHANGED" != 1 ]]; then
    log "orchestrator.env unchanged by the gate; atlas-orchestrator not restarted"
    return 0
  fi
  log "orchestrator.env changed (SANDBOX_* keys); restarting atlas-orchestrator once"
  systemctl restart atlas-orchestrator || die "systemctl restart atlas-orchestrator failed"
  wait_http "http://127.0.0.1:$port/health" 180 || die "the orchestrator did not answer 200 on /health within 180 s after the restart (journalctl -u atlas-orchestrator)"
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

# The CONVENTIONS.md §8 bind rule. Redis 6379 and squid 3128: loopback only, always. Any other TCP/UDP listener on
# 0.0.0.0 or [::] is tolerated only when ufw is active with a default-deny incoming policy (the firewall then confines
# it to LAN and WireGuard, adjudicated conflict 4); WireGuard UDP 51820 is the documented exception.
_gate_binds() {
  command -v ss >/dev/null || { _gate_row bind "ss" "NOT HEALTHY: ss (iproute2) missing"; GATE_UNHEALTHY+=("bind rule: ss missing"); return 0; }
  local lines
  lines="$(ss -ltnuH 2>/dev/null | awk '{print $1, $5}' || true)"
  local proto addr port host bad=() wild=()
  while read -r proto addr; do
    [[ -n "$addr" ]] || continue
    port="${addr##*:}"
    host="${addr%:*}"
    case "$port" in
      6379|3128)
        [[ "$host" == 127.0.0.1 || "$host" == "[::1]" ]] || bad+=("$proto $addr")
        ;;
      *)
        if [[ "$host" == 0.0.0.0 || "$host" == "*" || "$host" == "[::]" ]]; then
          [[ "$proto" == udp && "$port" == "${WG_PORT:-51820}" ]] || wild+=("$proto $addr")
        fi
        ;;
    esac
  done <<<"$lines"
  if (( ${#bad[@]} > 0 )); then
    _gate_row bind "redis/squid loopback only" "NOT HEALTHY: ${bad[*]}"
    GATE_UNHEALTHY+=("bind rule: 6379/3128 not loopback-only: ${bad[*]}")
  else
    _gate_row bind "redis/squid loopback only" "ok (6379 and 3128 on 127.0.0.1 or absent)"
  fi
  if (( ${#wild[@]} > 0 )); then
    local ufw
    ufw="$(ufw status verbose 2>/dev/null || true)"
    if grep -q '^Status: active' <<<"$ufw" && grep -qE '^Default: deny \(incoming\)' <<<"$ufw"; then
      _gate_row bind "wildcard listeners" "behind ufw default-deny (LAN/WG rules apply): ${wild[*]}"
    else
      _gate_row bind "wildcard listeners" "NOT HEALTHY: ufw not active/default-deny and listeners on 0.0.0.0/[::]: ${wild[*]}"
      GATE_UNHEALTHY+=("bind rule: wildcard listeners without an active default-deny ufw: ${wild[*]}")
    fi
  else
    _gate_row bind "wildcard listeners" "none (everything on loopback/LAN/WG addresses)"
  fi
}

# Section 10.5 / CONVENTIONS.md §7.2: the vault's plaintext view must be excluded from restic before V13 runs.
_gate_restic_vault_exclusion() {
  local excl="$ATLAS_ETC/restic-exclude.txt" venv="$ATLAS_ETC/vault.env" mnt=""
  [[ -r "$venv" ]] && mnt="$(sed -nE 's/^VAULT_MOUNT_DIR=(.*)$/\1/p' "$venv" | head -n1)"
  [[ -n "$mnt" ]] || mnt="$ATLAS_SRV/vault/open"
  if [[ ! -f "$excl" ]]; then
    _gate_row restic "vault exclusion" "NOT HEALTHY: $excl missing (step 7)"
    GATE_UNHEALTHY+=("restic: $excl missing")
  elif grep -qx -- "$mnt" "$excl"; then
    _gate_row restic "vault exclusion" "$mnt excluded in $excl"
  else
    _gate_row restic "vault exclusion" "NOT HEALTHY: $mnt not in $excl (plaintext view would be in the include set)"
    GATE_UNHEALTHY+=("restic: $mnt missing from $excl (steps 7/9b)")
  fi
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
  _gate_binds
  _gate_restic_vault_exclusion

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
# The Phase 2 half of V10 (Section 21): recorded as V10 with result info so the id stays one CONVENTIONS.md §4 declares;
# the Phase 3 gate's V10 record (the load-test table) supersedes it as the latest result per id.
_gate_v10_half() {
  local path="$ATLAS_DAY1_DIR/verify/v10a-router-resident.sh" out rc=0
  [[ -f "$path" ]] || { record_v V10 info "Phase 2 half (resident router): verify/v10a-router-resident.sh missing"; return 0; }
  out="$(timeout --foreground 660 bash "$path" 2>/dev/null)" || rc=$?
  out="$(printf '%s' "$out" | tr '\n' ' ' | sed -e 's/[[:space:]]\+/ /g' -e 's/^ //' -e 's/ $//')"
  local verdict
  case "$rc" in 0) verdict=pass ;; 2) verdict=deferred ;; 3) verdict=info ;; *) verdict="fail (exit $rc)" ;; esac
  record_v V10 info "Phase 2 half (resident router, verified separately at the Phase 2 gate): $verdict: ${out:-(no output)}"
  [[ "$verdict" == pass ]] || warn "the resident router check did not pass ($verdict); recorded as V10 info, not blocking (the Phase 3 gate records V10 proper)"
}

# V18: keep a real-vault pass from step 9b (this phase) rather than superseding it with the test-vault run.
_gate_v18() {
  local kept=""
  if [[ -f "$ATLAS_VERIFY_FILE" ]]; then
    kept="$(python3 - "$ATLAS_VERIFY_FILE" <<'PY' 2>/dev/null || true
import json
import sys

last = None
with open(sys.argv[1], encoding="utf-8") as fh:
    for line in fh:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("id") == "V18":
            last = rec
if last and last.get("phase") == "phase2" and last.get("result") == "pass" and "(real cipher dir)" in last.get("msg", ""):
    print(last.get("ts", "?"))
PY
)"
  fi
  if [[ -n "$kept" ]]; then
    log "V18: keeping step 9b's real-vault pass ($kept); the test-vault run is not repeated (it would supersede the stronger evidence)"
    return 0
  fi
  run_verify V18 v18-vault.sh || warn "V18 fail recorded (test vault). If step 9b never had a console, initialise the real vault from one: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
}

_gate_verifies() {
  # Each one is recorded whatever the others did; the message in verify.jsonl is the evidence.
  run_verify V3b v03b-llama-devices.sh || warn "V3b fail recorded"
  _gate_v10_half
  run_verify V6 v06-pyannote.sh || warn "V6 fail recorded"
  run_verify V7 v07-voice-listen.sh || warn "V7 fail recorded"
  run_verify V12 v12-openwebui-offline.sh atlas-openwebui 120 || warn "V12 fail recorded"
  run_verify V13 v13-restic.sh || warn "V13 fail recorded"
  run_verify V14a v14a-arbiter-stubs.sh || warn "V14a fail recorded"
  run_verify V15 v15-approval-gate.sh || warn "V15 fail recorded"
  run_verify V16 v16-router-hard-rule.sh || warn "V16 fail recorded"
  run_verify V17 v17-sandbox.sh || warn "V17 fail recorded"
  _gate_v18
  run_verify V20 v20-google.sh || warn "V20 fail recorded"
  run_verify V23 v23-cloudflare-token.sh || warn "V23 fail recorded"
}

# --- 4. the gate --------------------------------------------------------------------------------------------------------
_gate_vault_pending_note() {
  local flag="$ATLAS_STATE/vault-init-pending"
  [[ -f "$flag" ]] || return 0
  echo
  echo "NOTE: the real vault is NOT initialised (step 9b ran without a console; flag $flag)."
  echo "      The interface button answers 'not initialised' until you run, from a console:"
  echo "        sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  warn "real vault initialisation pending (no console at step 9b); V18 was proven on the test vault"
}

step_10() {
  _gate_prepare_sandbox
  _gate_ensure_pytest
  _gate_orchestrator_refresh
  _gate_services
  _gate_verifies
  if (( ${#GATE_UNHEALTHY[@]} > 0 )); then
    echo
    verify_table V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 V7 V10
    echo
    echo "PHASE 2 GATE: FAIL — Section 17 requires every service healthy before the table is judged:"
    printf '  - %s\n' "${GATE_UNHEALTHY[@]}"
    echo "Fix them, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2   (the gate step is the only one left to run)"
    _gate_vault_pending_note
    rm -f "$ATLAS_DONE_DIR/phase2.gate"
    notify "Phase 2 gate FAIL: ${#GATE_UNHEALTHY[@]} service(s) unhealthy"
    die "Phase 2 gate: ${#GATE_UNHEALTHY[@]} unhealthy service(s): ${GATE_UNHEALTHY[*]}"
  fi
  if gate phase2 V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 -- V7 V10; then
    _gate_vault_pending_note
    echo
    echo "Phase 3 (core LLM pull, ~690 GB, detached under systemd) starts with:"
    echo "    sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
    echo "Follow it with:  journalctl -u atlas-day1-phase3 -f"
    notify "Phase 2 gate PASS. Next: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
    return 0
  fi
  _gate_vault_pending_note
  echo "If V18 is the red row: a missing console at step 9b leaves only the test vault; run from a console: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  notify "Phase 2 gate FAIL; see the table in the phase log"
  return 1
}
