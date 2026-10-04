#!/usr/bin/env bash
# phase2/10-gate.sh — Section 17 Phase 2 step 10: the gate. "Every service healthy; V3 second half, V6, V7 (deferred
# if the reference recordings do not exist yet), V12, V20, V23 recorded. The Arbiter's refusal logic is unit-tested
# here against stub footprints" — plus V13, V14a, V15, V16, V17, V18 per CONVENTIONS.md §6, and the Phase 2 half of
# V10 (Section 21: the resident router "is verified separately at the Phase 2 gate"), recorded under its own id
# `V10a` (CONVENTIONS §4/§5/§6 declare it; tools/fill-workbook.py shows it as the Phase 2 evidence of the V10 row).
# No `V10` row is ever written from Phase 2: gate() takes the latest record per id across phases, so a Phase 2 re-run
# can never shadow the Phase 3 load test's verdict.
# Sourced by phase2-services.sh through run_phase_steps; defines step_10 only.
#
# THE GATE JUDGES, IT DOES NOT BUILD (fix round 4, major): everything this step used to set up before judging (the
# AEGIS sandbox image, /srv/atlas/sandbox, the SANDBOX_* keys, the orchestrator restart) moved to phase2/06d-sandbox.sh,
# a step of its own that runs before 07 (run_phase_steps picks NN-*.sh up in byte order). §7.5: every step's
# prerequisites are the steps before it, so V17 and the health rows below judge artefacts an earlier step produced, and
# a `--force 10` re-run mutates nothing. What remains here is read-only.
#
# Order:
#   1. Read-only pre-checks (warn only; the matching rows and V records carry the verdict): pytest present in the venv
#      (a RUNTIME dependency of the atlas package, `pytest==9.1.1` in orchestrator/pyproject.toml, installed by step 2's
#      `pip install -e`; the gate installs NOTHING).
#   2. Health of every service BY NAME: systemctl is-active for each unit (including the long-running units earlier
#      steps `enable --now`: atlas-openwebui-egress.service from step 3, atlas-aegis-trigger.path from step 7, one
#      atlas-gdrive@<tag>.service per GOOGLE_ACCOUNTS tag from step 6c), docker compose ps + docker inspect for each
#      container, an AUTHENTICATED redis PING (and the unauthenticated one refused with NOAUTH, as step 2 asserts), HTTP
#      /health (or the documented equivalent) for the orchestrator, Kokoro, speaches, ChromaDB, the three resident
#      llama-servers, Open WebUI, ntfy and docling; the sandbox image (label version, base-image pin) and the SANDBOX_*
#      keys step 06d wrote; the CONVENTIONS.md §8 bind rule as _gate_binds states it; every file under
#      /etc/atlas/secrets unreadable to group and world; the restic exclusion of the vault's plaintext view (§7.2)
#      before V13. Printed as a table; failures collected.
#   3. run_verify for V3b, V6, V7, V12, V13, V14a, V15, V16, V17, V18, V20, V23 (every one runs even when an earlier one
#      fails; a fail is recorded, never omitted, rule §7.4), and the V10 Phase 2 half as V10a (above). V18: a
#      real-vault pass recorded by step 9b is kept (the test-vault run would otherwise supersede the stronger evidence);
#      otherwise the unattended test-vault run happens here, and verify/v18-vault.sh records `deferred` (never `pass`)
#      while the real vault is uninitialised — the V7 pattern (CONVENTIONS §6: deferred never blocks), not a pass for a
#      substitute.
#   4. An unhealthy service stops here with the verify table and the list (no gate marker is written); otherwise
#      `gate phase2` with V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 required and V7 V10a recorded only, and the
#      Phase 3 start command is printed.
#
# Contracts relied on from other writers (all listed in phase2/README-contracts.md): unit names from
# phase2/02-orchestrator.sh (atlas-orchestrator, atlas-celery-cpu/gpu/beat), 01-llama.sh + 04-memory.sh
# (llama-server@<resident key>, $ATLAS_ETC/engines/<key>.env with LLAMA_ARG_PORT), 03-openwebui.sh
# (atlas-openwebui-egress.service), 06c-google-oauth.sh (atlas-gdrive@.service, instances per GOOGLE_ACCOUNTS tag),
# 06d-sandbox.sh (atlas-sandbox:py3.12 with its labels, SANDBOX_* keys), 07-restic.sh (atlas-aegis.timer,
# atlas-aegis-trigger.path, atlas-restic-check.timer, $ATLAS_ETC/restic-exclude.txt), 08-sentinel.sh
# (atlas-sentinel.timer, atlas-prune.timer), 09-windows-share.sh (srv-atlas-winpc.automount, only when installed),
# Phase 1 (docker, squid, cockpit.socket, xrdp, ssh, atlas-ddns.timer, atlas-docker-egress.service,
# /etc/atlas/docker.env with DOCKER_GW and LAN_IP, ufw's "3128/tcp on $LAN_IFACE DENY IN" rule); container names from
# docker/core/compose.yml (atlas-redis, atlas-chromadb, atlas-openwebui; redis under --requirepass with REDIS_PASSWORD in
# the container's env_file) and docker/core/compose.voice.yml (atlas-kokoro, atlas-speaches, atlas-docling; the bare
# kokoro/speaches/docling spellings of an earlier revision are still accepted), docker/ntfy/compose.yml (atlas-ntfy),
# docker/wg-easy (wg-easy); verify script names from CONVENTIONS.md §5 and the writers' usage lines (v12 takes CONTAINER
# WINDOW_S).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

SANDBOX_IMAGE="atlas-sandbox:py3.12"                 # services-tools.md S9 name; built by phase2/06d-sandbox.sh from docker/sandbox/Dockerfile
SANDBOX_IMAGE_VERSION="4"                            # label org.atlas.sandbox.version in the Dockerfile (4 = digest-pinned base + telemetry opt-outs)
SANDBOX_KEYS=(SANDBOX_IMAGE SANDBOX_DIR SANDBOX_MEMORY SANDBOX_CPUS SANDBOX_PIDS SANDBOX_TMPFS_SIZE SANDBOX_TIMEOUT_S SANDBOX_FSIZE)
GATE_UNHEALTHY=()
GATE_HEALTH_ROWS=()

_gate_row() { GATE_HEALTH_ROWS+=("$(printf '%-12s %-36s %s' "$1" "$2" "$3")"); }

# --- 1. read-only pre-checks (warn, never die: the verifications below record what is missing) -----------------------------
_gate_check_pytest() {
  local py="$ATLAS_OPT/venv/bin/python"
  [[ -x "$py" ]] || { warn "$py missing: Phase 2 step 2 (the orchestrator venv) has not run; V14a/V15/V16 will record the failure"; return 0; }
  if "$py" -m pytest --version >/dev/null 2>&1; then
    log "pytest present in $ATLAS_OPT/venv: $("$py" -m pytest --version 2>&1 | head -n1)"
    return 0
  fi
  # orchestrator/pyproject.toml pins `pytest==9.1.1` under [project].dependencies (a RUNTIME dependency, resolved pin; the
  # `dev` extra is ruff only). Step 2's `pip install -e $ORCH_DIR` brings it; the gate installs nothing (rule §7.9, header).
  warn "pytest missing from $ATLAS_OPT/venv: pyproject.toml pins pytest==9.1.1 as a runtime dependency; re-run Phase 2 step 2 (sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02); V14a/V15/V16 will record the failure"
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

# The AEGIS sandbox, read-only (step 06d built it): the image exists with the expected label version, its base label names
# the digest docker/sandbox/Dockerfile pins (rule §7.9; the label is the evidence BuildKit leaves — a base pulled during
# `docker build` is not listed as a tagged image in the daemon's store), and orchestrator.env carries every SANDBOX_* key.
_gate_sandbox() {
  local ctx="$ATLAS_DAY1_DIR/docker/sandbox" pin have_ver have_base
  if ! command -v docker >/dev/null; then
    _gate_row sandbox "$SANDBOX_IMAGE" "NOT HEALTHY: docker not installed (Phase 1 step 6)"
    GATE_UNHEALTHY+=("sandbox: docker not installed")
    return 0
  fi
  pin="$(sed -nE 's/^FROM[[:space:]]+python:3\.12-slim@(sha256:[0-9a-f]{64}).*/\1/p' "$ctx/Dockerfile" 2>/dev/null | head -n1 || true)"
  have_ver="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.version"}}' "$SANDBOX_IMAGE" 2>/dev/null || true)"
  have_base="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.base"}}' "$SANDBOX_IMAGE" 2>/dev/null || true)"
  if [[ -z "$have_ver" ]]; then
    _gate_row sandbox "$SANDBOX_IMAGE" "NOT HEALTHY: image missing (phase2/06d-sandbox.sh builds it: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06d)"
    GATE_UNHEALTHY+=("sandbox: image $SANDBOX_IMAGE missing (step 06d)")
  elif [[ "$have_ver" != "$SANDBOX_IMAGE_VERSION" ]]; then
    _gate_row sandbox "$SANDBOX_IMAGE" "NOT HEALTHY: label version '$have_ver', expected $SANDBOX_IMAGE_VERSION (rebuild: --force 06d)"
    GATE_UNHEALTHY+=("sandbox: $SANDBOX_IMAGE label version '$have_ver' != $SANDBOX_IMAGE_VERSION (step 06d)")
  elif [[ -n "$pin" && "$have_base" != *"$pin"* ]]; then
    _gate_row sandbox "$SANDBOX_IMAGE" "NOT HEALTHY: base label '${have_base:-none}' is not the Dockerfile pin $pin (rebuild: --force 06d)"
    GATE_UNHEALTHY+=("sandbox: $SANDBOX_IMAGE base '${have_base:-none}' != Dockerfile pin $pin (step 06d)")
  else
    _gate_row sandbox "$SANDBOX_IMAGE" "label version $have_ver, base ${have_base:-unlabelled}${pin:+ (= Dockerfile pin)}"
  fi
  local orch="$ATLAS_ETC/orchestrator.env" k missing=()
  for k in "${SANDBOX_KEYS[@]}"; do
    grep -qE "^${k}=.+" "$orch" 2>/dev/null || missing+=("$k")
  done
  if (( ${#missing[@]} > 0 )); then
    _gate_row sandbox "orchestrator.env SANDBOX_*" "NOT HEALTHY: missing ${missing[*]} (step 06d writes them)"
    GATE_UNHEALTHY+=("sandbox: SANDBOX_* keys missing from $orch: ${missing[*]} (step 06d)")
  else
    _gate_row sandbox "orchestrator.env SANDBOX_*" "${#SANDBOX_KEYS[@]} keys present"
  fi
}

# /etc/atlas/secrets is root:atlas 710 (traverse-only; the one Phase 2 value, README-contracts.md §3 item 10). That is
# only safe while no file inside is readable to the group or the world: CONVENTIONS §7.2 says every secret is 600.
_gate_secrets_perms() {
  local dir="$ATLAS_ETC/secrets" loose own
  [[ -d "$dir" ]] || { _gate_row secrets "$dir" "NOT HEALTHY: missing"; GATE_UNHEALTHY+=("secrets: $dir missing"); return 0; }
  own="$(stat -c '%U:%G %a' "$dir" 2>/dev/null || true)"
  loose="$(find "$dir" -type f -perm /077 2>/dev/null | tr '\n' ' ' || true)"
  if [[ -n "$loose" ]]; then
    _gate_row secrets "$dir ($own)" "NOT HEALTHY: group/world-readable file(s): $loose"
    GATE_UNHEALTHY+=("secrets: file(s) under $dir readable beyond their owner (must be 600, CONVENTIONS §7.2): $loose")
  else
    _gate_row secrets "$dir ($own)" "every file owner-only (no -perm /077 hits)"
  fi
}

# The CONVENTIONS.md §8 bind rule, as the other writers implement it (fix rounds 2 and 4):
#   * Redis 6379: loopback only, always (compose publishes 127.0.0.1:6379).
#   * squid 3128 (§8 "loopback only"; config/squid.conf.tmpl, phase1/04-system.sh _squid_render): 127.0.0.1 PLUS the
#     docker0 gateway (DOCKER_GW in /etc/atlas/docker.env, 172.17.0.1 unless Docker picked another range), because
#     containers must use the allowlist proxy (rule §7.1) and reach the host only at a bridge gateway. That second
#     listener (or a wildcard one, should an older render be live) is accepted ONLY behind ufw's fence from step 4:
#     active, default-deny incoming, and the rule "3128/tcp on $LAN_IFACE DENY IN" that keeps the LAN off the proxy.
#     squid on the LAN address itself, or on any other non-loopback non-gateway address, is never accepted.
#   * Docker publishes: a wildcard listener owned by docker-proxy, or a `0.0.0.0:`/`[::]:` publish in `docker ps`, is
#     NOT HEALTHY whatever ufw says: published ports are reached through the DOCKER chain in FORWARD/nat and bypass
#     ufw's INPUT policy, so a future `ports: - "8000:8000"` would be reachable on every interface (CONVENTIONS §8:
#     publish on 127.0.0.1 or ${LAN_IP}). `docker ps` is checked too because with userland-proxy=false nothing listens.
#   * Other host wildcard listeners: §8 is a BIND rule ("binds to loopback, LAN and WireGuard addresses only"), and the
#     adjudicated conflict 4 speaks of the WireGuard bridge and container egress, not of host bind addresses. Four host
#     daemons cannot (or do not, as the other writers ship them) bind per address: sshd (ssh.socket on 22, owned by
#     systemd when socket-activated), cockpit (cockpit.socket on 9090, owned by systemd), xrdp (3389) and Open WebUI on
#     the host network ($OPENWEBUI_PORT, docker/core/compose.yml). EXACTLY those four (port AND owning process) are
#     accepted on 0.0.0.0/[::] behind an active default-deny ufw (interface-scoped LAN/WG allow rules, Phase 1 step 4);
#     every other wildcard listener is NOT HEALTHY, so a daemon that appears on 0.0.0.0 later fails the gate instead of
#     passing silently. This is a RELAXATION of §8 from a bind rule to a firewall rule for those four and is recorded as
#     such (README-contracts.md §3 item 13: §8 should say so, or the owning steps bind by address — sshd ListenAddress,
#     cockpit.socket ListenStream=, xrdp address=, Open WebUI HOST=$LAN_IP — and this list shrinks to nothing).
#     WireGuard UDP 51820 is §8's documented exception.
_gate_binds() {
  command -v ss >/dev/null || { _gate_row bind "ss" "NOT HEALTHY: ss (iproute2) missing"; GATE_UNHEALTHY+=("bind rule: ss missing"); return 0; }
  local docker_gw="" lan_ip=""
  if [[ -r "$ATLAS_ETC/docker.env" ]]; then
    docker_gw="$(sed -nE 's/^DOCKER_GW=(.*)$/\1/p' "$ATLAS_ETC/docker.env" | head -n1)"
    lan_ip="$(sed -nE 's/^LAN_IP=(.*)$/\1/p' "$ATLAS_ETC/docker.env" | head -n1)"
  fi
  [[ -n "$docker_gw" ]] || docker_gw="$(ip -4 -o addr show docker0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
  local ow_port="${OPENWEBUI_PORT:-3000}"
  local lines
  # -p: the owning process (root sees every socket), so docker-proxy can be told from a host daemon.
  lines="$(ss -ltnupH 2>/dev/null | awk '{print $1, $5, $7}' || true)"
  local proto addr proc port host bad=() squid=() wild=() wild_bad=() dockerwild=()
  while read -r proto addr proc; do
    [[ -n "$addr" ]] || continue
    port="${addr##*:}"
    host="${addr%:*}"
    local is_wild=0
    [[ "$host" == 0.0.0.0 || "$host" == "*" || "$host" == "[::]" ]] && is_wild=1
    case "$port" in
      6379)
        [[ "$host" == 127.0.0.1 || "$host" == "[::1]" ]] || bad+=("$proto $addr (redis must be loopback only)")
        ;;
      3128)
        if [[ "$host" == 127.0.0.1 || "$host" == "[::1]" ]]; then
          :
        elif [[ -n "$docker_gw" && "$host" == "$docker_gw" ]] || (( is_wild )); then
          squid+=("$proto $addr")
        else
          bad+=("$proto $addr (squid may bind 127.0.0.1 and the docker0 gateway ${docker_gw:-?} only; never the LAN address)")
        fi
        ;;
      *)
        if (( is_wild )); then
          if [[ "$proc" == *docker-proxy* ]]; then
            dockerwild+=("$proto $addr")
          elif [[ "$proto" == udp && "$port" == "${WG_PORT:-51820}" ]]; then
            :
          else
            # The four accepted (port, owner) pairs; anything else on a wildcard address is a bind-rule failure.
            local ok=0
            case "$port" in
              22)   [[ "$proc" == *sshd* || "$proc" == *systemd* ]] && ok=1 ;;
              9090) [[ "$proc" == *cockpit* || "$proc" == *systemd* ]] && ok=1 ;;
              3389) [[ "$proc" == *xrdp* ]] && ok=1 ;;
              "$ow_port") [[ "$proto" == tcp ]] && ok=1 ;;   # Open WebUI, network_mode: host (its process name is the container's)
            esac
            if (( ok )); then wild+=("$proto $addr ${proc:+(${proc})}"); else wild_bad+=("$proto $addr ${proc:+(${proc})}"); fi
          fi
        fi
        ;;
    esac
  done <<<"$lines"
  # Docker's own view of its publishes (userland-proxy may be off, in which case no socket is listening at all).
  if command -v docker >/dev/null; then
    local pub
    pub="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E '(^|[ ,])(0\.0\.0\.0|\[::\]):[0-9]+->' | tr '\n' ';' || true)"
    [[ -z "$pub" ]] || dockerwild+=("docker ps: $pub")
  fi
  if (( ${#bad[@]} > 0 )); then
    _gate_row bind "redis/squid addresses" "NOT HEALTHY: ${bad[*]}"
    GATE_UNHEALTHY+=("bind rule: ${bad[*]}")
  else
    _gate_row bind "redis/squid addresses" "ok (6379 loopback only; 3128 on loopback${squid[*]:+ and ${squid[*]}})"
  fi
  local ufw="" ufw_ok=0
  if (( ${#squid[@]} > 0 || ${#wild[@]} > 0 )); then
    ufw="$(ufw status verbose 2>/dev/null || true)"
    grep -q '^Status: active' <<<"$ufw" && grep -qE '^Default: deny \(incoming\)' <<<"$ufw" && ufw_ok=1
  fi
  if (( ${#squid[@]} > 0 )); then
    local lan="${LAN_IFACE:-}" lan_note=""
    [[ -n "$lan" ]] || lan_note="; LAN_IFACE is unset in atlas.env"
    if (( ufw_ok )) && [[ -n "$lan" ]] && grep -qE "^3128/tcp on ${lan}[[:space:]]+DENY IN" <<<"$ufw"; then
      _gate_row bind "squid docker0 gateway" "behind ufw (active, default-deny, '3128/tcp on $lan DENY IN'): ${squid[*]}"
    else
      _gate_row bind "squid docker0 gateway" "NOT HEALTHY: ${squid[*]} reachable without ufw's LAN deny rule (LAN_IFACE='${lan:-unset}')"
      GATE_UNHEALTHY+=("bind rule: squid 3128 on ${squid[*]} without ufw active + default-deny + '3128/tcp on ${lan:-<LAN_IFACE>} DENY IN' (phase1/04-system.sh): LAN clients could use the node as a proxy$lan_note")
    fi
  fi
  if (( ${#dockerwild[@]} > 0 )); then
    _gate_row bind "docker publishes" "NOT HEALTHY: wildcard publish bypasses ufw INPUT: ${dockerwild[*]}"
    GATE_UNHEALTHY+=("bind rule: Docker publishes bypass ufw INPUT; publish on 127.0.0.1 or \${LAN_IP}${lan_ip:+ ($lan_ip)} instead of ${dockerwild[*]}")
  else
    _gate_row bind "docker publishes" "none on 0.0.0.0/[::] (all pinned to 127.0.0.1 or the LAN IP)"
  fi
  if (( ${#wild_bad[@]} > 0 )); then
    _gate_row bind "host wildcard listeners" "NOT HEALTHY: not in the accepted set (sshd 22, cockpit 9090, xrdp 3389, open-webui $ow_port): ${wild_bad[*]}"
    GATE_UNHEALTHY+=("bind rule: listener(s) on 0.0.0.0/[::] outside the accepted set (CONVENTIONS §8: bind to loopback/LAN/WG addresses): ${wild_bad[*]}")
  fi
  if (( ${#wild[@]} > 0 )); then
    if (( ufw_ok )); then
      _gate_row bind "host wildcard (accepted set)" "behind ufw default-deny (LAN/WG rules apply; §8 relaxation recorded): ${wild[*]}"
    else
      _gate_row bind "host wildcard (accepted set)" "NOT HEALTHY: ufw not active/default-deny and listeners on 0.0.0.0/[::]: ${wild[*]}"
      GATE_UNHEALTHY+=("bind rule: wildcard listeners without an active default-deny ufw: ${wild[*]}")
    fi
  elif (( ${#wild_bad[@]} == 0 )); then
    _gate_row bind "host wildcard listeners" "none (everything on loopback/LAN/WG addresses)"
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

# Redis has no HTTP endpoint: an AUTHENTICATED PING through the container (compose runs redis-server with
# --requirepass "$REDIS_PASSWORD" from its env_file; REDISCLI_AUTH keeps the password off every command line, as step 2's
# own probe does), and the unauthenticated PING must still be refused with NOAUTH (step 2's assertion, re-checked here).
_gate_redis() {
  local pong noauth
  pong="$(docker exec atlas-redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --no-auth-warning ping' 2>/dev/null || true)"
  if [[ "$pong" == PONG ]]; then
    _gate_row rpc "redis PING (authenticated)" "PONG"
  else
    _gate_row rpc "redis PING (authenticated)" "NOT HEALTHY: ${pong:-no answer}"
    GATE_UNHEALTHY+=("redis: authenticated PING answered '${pong:-nothing}' (REDIS_PASSWORD in the container's env_file, docker/core/compose.yml)")
  fi
  noauth="$(docker exec atlas-redis redis-cli ping 2>&1 || true)"
  if [[ "$noauth" == *NOAUTH* ]]; then
    _gate_row rpc "redis PING (no password)" "refused (NOAUTH), as required"
  else
    _gate_row rpc "redis PING (no password)" "NOT HEALTHY: answered '${noauth:-nothing}' instead of NOAUTH"
    GATE_UNHEALTHY+=("redis: unauthenticated PING answered '${noauth:-nothing}' (--requirepass not in effect)")
  fi
}

_gate_services() {
  log "health check of every service by name"
  local u
  # Every long-running unit an earlier step installed and `enable --now`ed (fix round 4 added atlas-openwebui-egress,
  # the Open WebUI egress chain V12 relies on, and atlas-aegis-trigger.path, the manual AEGIS trigger of step 7).
  for u in docker squid cockpit.socket xrdp atlas-ddns.timer atlas-docker-egress.service atlas-openwebui-egress.service \
           atlas-orchestrator atlas-celery-cpu atlas-celery-gpu atlas-celery-beat \
           llama-server@router-qwen3.5-4b llama-server@embed-bge-m3 llama-server@rerank-bge-v2-m3 \
           atlas-sentinel.timer atlas-prune.timer atlas-aegis.timer atlas-aegis-trigger.path atlas-restic-check.timer; do
    _gate_unit "$u"
  done
  # ssh is socket-activated on recent Ubuntu: either the service or the socket being active is healthy.
  if systemctl is-active --quiet ssh.service || systemctl is-active --quiet ssh.socket; then
    _gate_row unit ssh "active ($(systemctl is-active ssh.service 2>/dev/null || true)/$(systemctl is-active ssh.socket 2>/dev/null || true))"
  else
    _gate_row unit ssh "NOT HEALTHY: neither ssh.service nor ssh.socket is active"
    GATE_UNHEALTHY+=("unit ssh: inactive")
  fi
  # One Drive mount per GOOGLE_ACCOUNTS tag (step 6c: atlas-gdrive@<tag>.service, asserted active by 6c itself).
  local acct tag
  if [[ -f /etc/systemd/system/atlas-gdrive@.service ]]; then
    for acct in ${GOOGLE_ACCOUNTS:-}; do
      tag="${acct##*:}"
      _gate_unit "atlas-gdrive@${tag}.service"
    done
    [[ -n "${GOOGLE_ACCOUNTS:-}" ]] || _gate_row unit "atlas-gdrive@<tag>.service" "no GOOGLE_ACCOUNTS in atlas.env; no instance expected"
  else
    _gate_row unit "atlas-gdrive@<tag>.service" "not installed (step 6c has not written the template)"
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
  _gate_redis

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
  _gate_sandbox
  _gate_secrets_perms
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
# _gate_latest_v ID [PHASE] — the latest verify.jsonl record for ID (optionally only from PHASE) as "result<TAB>ts<TAB>msg",
# empty when none.
_gate_latest_v() {
  [[ -f "$ATLAS_VERIFY_FILE" ]] || return 0
  python3 - "$ATLAS_VERIFY_FILE" "$1" "${2:-}" <<'PY' 2>/dev/null || true
import json
import sys

path, vid, phase = sys.argv[1], sys.argv[2], sys.argv[3]
last = None
with open(path, encoding="utf-8") as fh:
    for line in fh:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("id") == vid and (not phase or rec.get("phase") == phase):
            last = rec
if last:
    print("\t".join(str(last.get(k, "")).replace("\t", " ") for k in ("result", "ts", "msg")))
PY
}

# V10, the Phase 2 half (header): run verify/v10a-router-resident.sh and record the verdict under V10a (the same id
# phase2/04-memory.sh records it under; never V10, which belongs to the Phase 3 load test). A failing router is
# unhealthy (Section 21 places this half at the Phase 2 gate), so it also stops the gate through GATE_UNHEALTHY.
_gate_v10_half() {
  local script="$ATLAS_DAY1_DIR/verify/v10a-router-resident.sh" out rc=0
  [[ -f "$script" ]] || die "_gate_v10_half: $script does not exist"
  out="$(timeout --foreground 660 bash "$script")" || rc=$?   # stderr (the script's log lines) stays on the phase log, as run_verify leaves it
  out="$(printf '%s' "$out" | tr '\n' ' ' | sed -e 's/[[:space:]]\+/ /g' -e 's/^ //' -e 's/ $//')"
  [[ -n "$out" ]] || out="(no output)"
  local verdict
  case "$rc" in
    0) verdict="pass" ;;
    2) verdict="deferred" ;;
    3) verdict="info" ;;
    124) verdict="fail"; out="exit 124 (timed out after 660 s): $out" ;;
    *) verdict="fail"; out="exit $rc: $out" ;;
  esac
  record_v V10a "$verdict" "Phase 2 half (resident router, gate): $out"
  if [[ "$verdict" == fail ]]; then
    warn "V10 Phase 2 half (V10a) failed (resident router): $out"
    GATE_UNHEALTHY+=("resident router llama-server@router-qwen3.5-4b: no chat completion ($out)")
  fi
}

# V18: keep a real-vault pass from step 9b (this phase) rather than superseding it with the test-vault run. Otherwise the
# unattended run on the test cipher dir, which v18 records as `deferred` while the real vault is uninitialised (header).
_gate_v18() {
  local last res msg
  last="$(_gate_latest_v V18 phase2)"
  if [[ -n "$last" ]]; then
    IFS=$'\t' read -r res _ msg <<<"$last"
    if [[ "$res" == pass && "$msg" == *"(real cipher dir)"* ]]; then
      log "V18: keeping step 9b's real-vault pass; the test-vault run is not repeated (it would supersede the stronger evidence)"
      return 0
    fi
  fi
  run_verify V18 v18-vault.sh || warn "V18 fail recorded (test vault). To initialise and prove the real vault from a console: sudo env ATLAS_VAULT_INIT=1 ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  last="$(_gate_latest_v V18 phase2)"
  [[ "${last%%$'\t'*}" != deferred ]] || log "V18 recorded as deferred: the mechanics passed on the test cipher dir but the real vault is not initialised (never blocks; see the NOTE at the end)"
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
  echo "NOTE: the real vault is NOT initialised (step 9b ran unattended, as it does by default; flag $flag)."
  echo "      V18 is recorded as DEFERRED (mechanics proven on the test cipher dir, never a pass for a substitute) and the"
  echo "      interface button answers 'not initialised' until you run, from a console:"
  echo "        sudo env ATLAS_VAULT_INIT=1 ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  warn "real vault initialisation pending (step 9b is unattended unless ATLAS_VAULT_INIT=1); V18 deferred, not passed"
}

step_10() {
  _gate_check_pytest
  _gate_services
  _gate_verifies
  if (( ${#GATE_UNHEALTHY[@]} > 0 )); then
    echo
    verify_table V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 V7 V10a
    echo
    echo "PHASE 2 GATE: FAIL — Section 17 requires every service healthy before the table is judged:"
    printf '  - %s\n' "${GATE_UNHEALTHY[@]}"
    echo "Fix them, then re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2   (the gate step is the only one left to run)"
    _gate_vault_pending_note
    rm -f "$ATLAS_DONE_DIR/phase2.gate"
    notify "Phase 2 gate FAIL: ${#GATE_UNHEALTHY[@]} service(s) unhealthy"
    die "Phase 2 gate: ${#GATE_UNHEALTHY[@]} unhealthy service(s): ${GATE_UNHEALTHY[*]}"
  fi
  if gate phase2 V3b V6 V12 V13 V14a V15 V16 V17 V18 V20 V23 -- V7 V10a; then
    _gate_vault_pending_note
    echo
    echo "Phase 3 (core LLM pull, ~690 GB, detached under systemd) starts with:"
    echo "    sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
    echo "Follow it with:  journalctl -u atlas-day1-phase3 -f"
    notify "Phase 2 gate PASS. Next: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase3"
    return 0
  fi
  _gate_vault_pending_note
  echo "If V18 is the red row: the test vault did not pass; see verify.jsonl. To prove the real vault from a console: sudo env ATLAS_VAULT_INIT=1 ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 09b"
  notify "Phase 2 gate FAIL; see the table in the phase log"
  return 1
}
