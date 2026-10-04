#!/usr/bin/env bash
# phase2/atlas-openwebui-egress.sh — installed by phase2/03-openwebui.sh as /usr/local/sbin/atlas-openwebui-egress
# (install -m 755; a repository file so shellcheck covers it, fix round 2). Permanent runtime fence for the
# atlas-openwebui container (host network; Section 12.1; the rule verify/v12-openwebui-offline.sh proves for 120 s,
# enforced for ever by systemd/atlas-openwebui-egress.service):
#   * every NEW connection whose socket belongs to the container's cgroup and leaves through a non-loopback interface
#     is logged (rate-limited, prefix "[ATLAS openwebui egress] ", 25 chars: iptables' LOG keeps at most 29, the former
#     32-char prefix was silently truncated, fix round 3) and DROPPED; replies to LAN/WireGuard clients are ESTABLISHED
#     and never match;
#   * on loopback an ALLOWLIST (fix round 3; the former denylist 3128/6379/8000 left SSH, Cockpit, xrdp, dnsmasq, ntfy,
#     WG-Easy's admin and every llama-server port 8101-8110 open to a root-in-container process that parses untrusted
#     uploads; the engines would have been drivable past the Engine Arbiter and the ledger): a NEW tcp connection from
#     that cgroup over lo is allowed ONLY to the orchestrator (ORCH_PORT), the bge-m3 llama-server (EMBED_PORT), its own
#     listener (OPENWEBUI_PORT: the compose healthcheck curls it from inside the container) — all three read from
#     $ATLAS_ETC/core.env, written by phase2/02-orchestrator.sh — and Kokoro 8880, speaches 8881, docling 5001 (fixed
#     loopback publishes of docker/core/compose.voice.yml); every other NEW tcp and EVERY NEW udp over lo (no DNS: every
#     backend is an IP literal) is logged and dropped. squid 3128, Redis 6379 and ChromaDB 8000 are therefore still
#     refused, as before.
# Rules live in the chain ATLAS-OPENWEBUI (iptables and ip6tables), jumped to from OUTPUT.
#   apply  [CONTAINER]   rebuild the chain for the container's current cgroup path (no-op while it is not running)
#   watch  [CONTAINER]   apply; poll EVERY SECOND until the running container has been fenced once (at boot and after
#                        a container recreate the first apply may find it not yet running, fix round 2); then re-check
#                        every 10 s and re-apply when the container was recreated (new cgroup path) or the chain or the
#                        OUTPUT jump disappeared (ufw reload flushes OUTPUT). The unit atlas-openwebui-egress runs this.
#   status [CONTAINER]   print the cgroup path and the chain; exit 1 when the running container is not fenced
# Requires the xt_cgroup v2 path match (`-m cgroup --path`, cgroup2 unified hierarchy: Ubuntu's default). If the
# kernel lacks it, or $ATLAS_ETC/core.env is missing, apply FAILS (exit 1) and says so: an unfenced Open WebUI is never
# reported as fenced.
set -euo pipefail

CHAIN=ATLAS-OPENWEBUI
LOG_PREFIX="[ATLAS openwebui egress] "   # 25 chars (<= 29, iptables LOG limit)
CORE_ENV="${ATLAS_ETC:-/etc/atlas}/core.env"
LOOPBACK_ALLOW_FIXED=8880,8881,5001      # Kokoro, speaches, docling: loopback publishes of compose.voice.yml
LOOPBACK_ALLOW=""                        # computed by loopback_allow

core_env_port() {   # core_env_port KEY -> the numeric value of KEY in core.env, or failure
  local v
  v="$(awk -F= -v k="$1" '$1==k {print $2; exit}' "$CORE_ENV" 2>/dev/null | tr -d '"'"'"' ')"
  [[ "$v" =~ ^[0-9]{2,5}$ ]] || return 1
  printf '%s' "$v"
}

loopback_allow() {   # sets LOOPBACK_ALLOW (comma list for multiport) from core.env; exit 1 with a message when it cannot
  local orch embed ui
  [[ -r "$CORE_ENV" ]] || { echo "atlas-openwebui-egress: $CORE_ENV missing or unreadable (phase2/02-orchestrator.sh writes it); cannot compute the loopback allowlist" >&2; return 1; }
  orch="$(core_env_port ORCH_PORT)" || { echo "atlas-openwebui-egress: no numeric ORCH_PORT in $CORE_ENV" >&2; return 1; }
  embed="$(core_env_port EMBED_PORT)" || { echo "atlas-openwebui-egress: no numeric EMBED_PORT in $CORE_ENV" >&2; return 1; }
  ui="$(core_env_port OPENWEBUI_PORT)" || { echo "atlas-openwebui-egress: no numeric OPENWEBUI_PORT in $CORE_ENV" >&2; return 1; }
  LOOPBACK_ALLOW="$orch,$embed,$ui,$LOOPBACK_ALLOW_FIXED"
}

cgroup_path_of() {   # prints the cgroup v2 path (without the leading /) of the running container, or nothing
  local ctr="$1" pid
  pid="$(docker inspect -f '{{.State.Pid}}' "$ctr" 2>/dev/null || echo 0)"
  [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] || return 0
  awk -F: '$1=="0" {print $3; exit}' "/proc/$pid/cgroup" 2>/dev/null | sed 's#^/##'
}

fenced() {   # fenced BIN CGREL -> 0 when the chain carries both DROPs for this cgroup and OUTPUT jumps to it
  local bin="$1" cg="$2"
  [[ -n "$LOOPBACK_ALLOW" ]] || loopback_allow || return 1
  "$bin" -w -C OUTPUT -j "$CHAIN" 2>/dev/null \
    && "$bin" -w -C "$CHAIN" -m cgroup --path "$cg" ! -o lo -m conntrack --ctstate NEW -j DROP 2>/dev/null \
    && "$bin" -w -C "$CHAIN" -m cgroup --path "$cg" -o lo -p tcp -m multiport ! --dports "$LOOPBACK_ALLOW" -m conntrack --ctstate NEW -j DROP 2>/dev/null \
    && "$bin" -w -C "$CHAIN" -m cgroup --path "$cg" -o lo -p udp -m conntrack --ctstate NEW -j DROP 2>/dev/null
}

build_chain() {   # build_chain BIN CGREL
  local bin="$1" cg="$2"
  "$bin" -w -N "$CHAIN" 2>/dev/null || true
  "$bin" -w -F "$CHAIN"
  # non-loopback: everything NEW is logged (rate-limited) and dropped
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" ! -o lo -m conntrack --ctstate NEW -m limit --limit 6/min \
    -j LOG --log-prefix "$LOG_PREFIX" --log-level 4
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" ! -o lo -m conntrack --ctstate NEW -j DROP
  # loopback tcp: allowlist (everything NEW except the listed ports is logged and dropped)
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" -o lo -p tcp -m multiport ! --dports "$LOOPBACK_ALLOW" \
    -m conntrack --ctstate NEW -m limit --limit 6/min -j LOG --log-prefix "$LOG_PREFIX" --log-level 4
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" -o lo -p tcp -m multiport ! --dports "$LOOPBACK_ALLOW" \
    -m conntrack --ctstate NEW -j DROP
  # loopback udp: nothing is needed (no DNS: every backend is an IP literal)
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" -o lo -p udp -m conntrack --ctstate NEW -m limit --limit 6/min \
    -j LOG --log-prefix "$LOG_PREFIX" --log-level 4
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" -o lo -p udp -m conntrack --ctstate NEW -j DROP
  "$bin" -w -C OUTPUT -j "$CHAIN" 2>/dev/null || "$bin" -w -I OUTPUT 1 -j "$CHAIN"
}

apply() {   # exit 0 = fenced (or container not running), 1 = could not fence
  local ctr="$1" cg bin
  cg="$(cgroup_path_of "$ctr")"
  if [[ -z "$cg" ]]; then
    echo "atlas-openwebui-egress: $ctr is not running; the chain is left as it is"
    return 0
  fi
  loopback_allow || return 1
  for bin in iptables ip6tables; do
    command -v "$bin" >/dev/null || { [[ "$bin" == ip6tables ]] && continue; echo "atlas-openwebui-egress: $bin missing" >&2; return 1; }
    if ! build_chain "$bin" "$cg"; then
      echo "atlas-openwebui-egress: $bin could not build $CHAIN for cgroup $cg (no xt_cgroup --path support? cgroup v2 unified hierarchy required); Open WebUI is NOT fenced" >&2
      return 1
    fi
  done
  echo "atlas-openwebui-egress: $CHAIN applied for $ctr (cgroup $cg): non-loopback NEW dropped; loopback NEW tcp allowed only to $LOOPBACK_ALLOW, every other tcp and all udp dropped"
}

status() {
  local ctr="$1" cg
  cg="$(cgroup_path_of "$ctr")"
  echo "container=$ctr cgroup=${cg:-<not running>}"
  iptables -w -S "$CHAIN" 2>/dev/null || echo "(no $CHAIN chain)"
  [[ -z "$cg" ]] && return 0
  fenced iptables "$cg"
}

watch() {
  local ctr="$1" cg
  # Fast phase: one-second polling until the running container is fenced once (boot, recreate).
  until cg="$(cgroup_path_of "$ctr")" && [[ -n "$cg" ]] && apply "$ctr" >/dev/null; do
    sleep 1
  done
  echo "atlas-openwebui-egress: $ctr fenced (cgroup $cg); re-checking every 10 s"
  while sleep 10; do
    cg="$(cgroup_path_of "$ctr")"
    [[ -n "$cg" ]] || continue
    if ! fenced iptables "$cg"; then
      apply "$ctr" || true
    fi
  done
}

cmd="${1:-}"; ctr="${2:-atlas-openwebui}"
case "$cmd" in
  apply)  apply "$ctr" ;;
  watch)  watch "$ctr" ;;
  status) status "$ctr" ;;
  *) echo "usage: atlas-openwebui-egress apply|watch|status [CONTAINER]" >&2; exit 2 ;;
esac
