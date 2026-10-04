#!/usr/bin/env bash
# phase2/atlas-openwebui-egress.sh — installed by phase2/03-openwebui.sh as /usr/local/sbin/atlas-openwebui-egress
# (install -m 755; a repository file so shellcheck covers it, fix round 2). Permanent runtime fence for the
# atlas-openwebui container (host network; Section 12.1; the rule verify/v12-openwebui-offline.sh proves for 120 s,
# enforced for ever by systemd/atlas-openwebui-egress.service):
#   * every NEW connection whose socket belongs to the container's cgroup and leaves through a non-loopback interface
#     is logged (rate-limited, prefix "[ATLAS openwebui egress denied]") and DROPPED; replies to LAN/WireGuard clients
#     are ESTABLISHED and never match;
#   * on loopback, connections from that cgroup to tcp 3128 (squid: the way out through the allowlist), 6379 (Redis:
#     the Celery broker) and 8000 (ChromaDB: the Vector Cortex, no auth) are dropped too. The orchestrator (8800), the
#     bge-m3 llama-server, Kokoro, speaches and docling stay reachable.
# Rules live in the chain ATLAS-OPENWEBUI (iptables and ip6tables), jumped to from OUTPUT.
#   apply  [CONTAINER]   rebuild the chain for the container's current cgroup path (no-op while it is not running)
#   watch  [CONTAINER]   apply; poll EVERY SECOND until the running container has been fenced once (at boot and after
#                        a container recreate the first apply may find it not yet running, fix round 2); then re-check
#                        every 10 s and re-apply when the container was recreated (new cgroup path) or the chain or the
#                        OUTPUT jump disappeared (ufw reload flushes OUTPUT). The unit atlas-openwebui-egress runs this.
#   status [CONTAINER]   print the cgroup path and the chain; exit 1 when the running container is not fenced
# Requires the xt_cgroup v2 path match (`-m cgroup --path`, cgroup2 unified hierarchy: Ubuntu's default). If the
# kernel lacks it, apply FAILS (exit 1) and says so: an unfenced Open WebUI is never reported as fenced.
set -euo pipefail

CHAIN=ATLAS-OPENWEBUI
LOOPBACK_DROP_PORTS=3128,6379,8000

cgroup_path_of() {   # prints the cgroup v2 path (without the leading /) of the running container, or nothing
  local ctr="$1" pid
  pid="$(docker inspect -f '{{.State.Pid}}' "$ctr" 2>/dev/null || echo 0)"
  [[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] || return 0
  awk -F: '$1=="0" {print $3; exit}' "/proc/$pid/cgroup" 2>/dev/null | sed 's#^/##'
}

fenced() {   # fenced BIN CGREL -> 0 when the chain carries the DROP for this cgroup and OUTPUT jumps to it
  local bin="$1" cg="$2"
  "$bin" -w -C OUTPUT -j "$CHAIN" 2>/dev/null \
    && "$bin" -w -C "$CHAIN" -m cgroup --path "$cg" ! -o lo -m conntrack --ctstate NEW -j DROP 2>/dev/null
}

build_chain() {   # build_chain BIN CGREL
  local bin="$1" cg="$2"
  "$bin" -w -N "$CHAIN" 2>/dev/null || true
  "$bin" -w -F "$CHAIN"
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" ! -o lo -m conntrack --ctstate NEW -m limit --limit 6/min \
    -j LOG --log-prefix "[ATLAS openwebui egress denied] " --log-level 4
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" ! -o lo -m conntrack --ctstate NEW -j DROP
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" -o lo -p tcp -m multiport --dports "$LOOPBACK_DROP_PORTS" \
    -m conntrack --ctstate NEW -m limit --limit 6/min -j LOG --log-prefix "[ATLAS openwebui egress denied] " --log-level 4
  "$bin" -w -A "$CHAIN" -m cgroup --path "$cg" -o lo -p tcp -m multiport --dports "$LOOPBACK_DROP_PORTS" -j DROP
  "$bin" -w -C OUTPUT -j "$CHAIN" 2>/dev/null || "$bin" -w -I OUTPUT 1 -j "$CHAIN"
}

apply() {   # exit 0 = fenced (or container not running), 1 = could not fence
  local ctr="$1" cg bin
  cg="$(cgroup_path_of "$ctr")"
  if [[ -z "$cg" ]]; then
    echo "atlas-openwebui-egress: $ctr is not running; the chain is left as it is"
    return 0
  fi
  for bin in iptables ip6tables; do
    command -v "$bin" >/dev/null || { [[ "$bin" == ip6tables ]] && continue; echo "atlas-openwebui-egress: $bin missing" >&2; return 1; }
    if ! build_chain "$bin" "$cg"; then
      echo "atlas-openwebui-egress: $bin could not build $CHAIN for cgroup $cg (no xt_cgroup --path support? cgroup v2 unified hierarchy required); Open WebUI is NOT fenced" >&2
      return 1
    fi
  done
  echo "atlas-openwebui-egress: $CHAIN applied for $ctr (cgroup $cg): non-loopback NEW dropped, loopback tcp $LOOPBACK_DROP_PORTS dropped"
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
