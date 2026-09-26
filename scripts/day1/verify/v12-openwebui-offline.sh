#!/usr/bin/env bash
# verify/v12-openwebui-offline.sh — V12: "Open WebUI makes no outbound connection after hardening (verified with the
# firewall log)" (Sections 12.1, 21; Phase 2 step 3). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; one stdout
# line; never prompts; well under 10 minutes; safe to re-run. Must run as root (iptables, docker, journal).
# Usage: v12-openwebui-offline.sh [CONTAINER=atlas-openwebui] [WINDOW_S=120]
#
# Method. The container is restarted (model-cache loads and update checks happen at start-up, research 1.5) and
# watched for WINDOW_S seconds. Packets are attributed to the container, not guessed from timing:
#   * host network (docker/core/compose.yml): two temporary iptables LOG rules at the top of OUTPUT match the
#     container's cgroup v2 path (-m cgroup --path, socket owner) and log every NEW connection that leaves via a
#     non-loopback interface ("[ATLAS v12 egress]") and every NEW connection to the squid proxy on 127.0.0.1:3128
#     ("[ATLAS v12 proxy]"). Replies to LAN/WireGuard clients are ESTABLISHED, not NEW, so they never count; the
#     loopback services (orchestrator, llama-server, ChromaDB, Kokoro, speaches, docling) go over lo and never count.
#   * bridge network (defensive; not the shipped layout): LOG rules on DOCKER-USER and INPUT for the container's IP.
#   * if the kernel lacks the cgroup match, the fallback polls `ss -tuanp` every second for sockets owned by the
#     container's processes with a non-loopback peer (may miss sub-second connections; the message says which method ran).
# After the window the rules are removed and the kernel journal is read back. Corroborating counts that cannot be
# attributed to the container (squid access.log lines and TCP_DENIED in the window, [UFW BLOCK] OUT lines,
# [ATLAS docker egress denied] lines) are printed as information. PASS only when the attributed count is zero AND
# /health answered 200 inside the window.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

ctr="${1:-atlas-openwebui}"; window="${2:-120}"
[[ "$window" =~ ^[0-9]+$ ]] || { echo "usage: v12-openwebui-offline.sh [CONTAINER] [WINDOW_S]"; exit 1; }
[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "V12 must run as root (iptables, docker, journal)"; exit 1; }
command -v docker >/dev/null || { echo "docker not installed"; exit 1; }
command -v iptables >/dev/null || { echo "iptables not installed (Phase 1 step 4 installs it)"; exit 1; }

port="$(awk -F= '$1=="OPENWEBUI_PORT" {print $2; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)"
[[ "$port" =~ ^[0-9]+$ ]] || port=3000

docker inspect -f '{{.State.Running}}' "$ctr" 2>/dev/null | grep -qx true \
  || { echo "V12 fail: container $ctr is not running"; exit 1; }
mode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$ctr")"

t0="$(date +%s)"
docker restart "$ctr" >/dev/null 2>&1 || { echo "V12 fail: docker restart $ctr failed"; exit 1; }
pid="$(docker inspect -f '{{.State.Pid}}' "$ctr" 2>/dev/null || echo 0)"
[[ "$pid" =~ ^[0-9]+$ && "$pid" -gt 0 ]] || { echo "V12 fail: no PID for $ctr after restart"; exit 1; }
cg="$(awk -F: '$1=="0" {print $3; exit}' "/proc/$pid/cgroup" 2>/dev/null || true)"   # e.g. /system.slice/docker-<id>.scope
cgrel="${cg#/}"

rules=()      # each entry: "<v4|v6>|<chain>|<args joined by \x1f>" so the trailing space of a --log-prefix survives
add_rule() {  # add_rule v4|v6 CHAIN ARGS... -> 0 when inserted
  local fam="$1" chain="$2"; shift 2
  local bin=iptables; [[ "$fam" == v6 ]] && bin=ip6tables
  command -v "$bin" >/dev/null || return 1
  "$bin" -w -I "$chain" 1 "$@" 2>/dev/null || return 1
  local joined
  joined="$(IFS=$'\x1f'; printf '%s' "$*")"
  rules+=("$fam|$chain|$joined")
}
cleanup() {
  local r fam chain joined bin argv
  for r in "${rules[@]}"; do
    IFS='|' read -r fam chain joined <<<"$r"
    IFS=$'\x1f' read -r -a argv <<<"$joined"
    bin=iptables; [[ "$fam" == v6 ]] && bin=ip6tables
    "$bin" -w -D "$chain" "${argv[@]}" 2>/dev/null || true
  done
}
trap cleanup EXIT

method=""
if [[ "$mode" == host ]]; then
  if [[ -n "$cgrel" ]] \
     && add_rule v4 OUTPUT -m cgroup --path "$cgrel" ! -o lo -m conntrack --ctstate NEW -j LOG --log-prefix "[ATLAS v12 egress] " --log-level 4 \
     && add_rule v4 OUTPUT -m cgroup --path "$cgrel" -o lo -p tcp --dport 3128 -m conntrack --ctstate NEW -j LOG --log-prefix "[ATLAS v12 proxy] " --log-level 4; then
    method="cgroup-iptables"
    add_rule v6 OUTPUT -m cgroup --path "$cgrel" ! -o lo -m conntrack --ctstate NEW -j LOG --log-prefix "[ATLAS v12 egress] " --log-level 4 || true
  else
    method="ss-poll"
  fi
else
  cip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$ctr" 2>/dev/null || true)"
  if [[ -n "$cip" ]] \
     && add_rule v4 DOCKER-USER -s "$cip" -m conntrack --ctstate NEW -j LOG --log-prefix "[ATLAS v12 egress] " --log-level 4 \
     && add_rule v4 INPUT -s "$cip" -m conntrack --ctstate NEW -j LOG --log-prefix "[ATLAS v12 egress] " --log-level 4; then
    method="bridge-iptables"
  else
    echo "V12 fail: $ctr is on network mode '$mode' and no LOG rule could be inserted (ip=${cip:-none})"; exit 1
  fi
fi

# --- the window: health polling plus the ss fallback ---------------------------------------------------------------
declare -A poll_hits=()
health_at=""
deadline=$(( t0 + window ))
while (( $(date +%s) < deadline )); do
  if [[ -z "$health_at" ]]; then
    code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port/health" 2>/dev/null || true)"
    [[ "$code" == 200 ]] && health_at=$(( $(date +%s) - t0 ))
  fi
  if [[ "$method" == ss-poll && -n "$cg" && -r "/sys/fs/cgroup$cg/cgroup.procs" ]]; then
    pids="$(tr '\n' '|' <"/sys/fs/cgroup$cg/cgroup.procs" | sed 's/|$//')"
    if [[ -n "$pids" ]]; then
      while read -r peer; do
        [[ -n "$peer" ]] || continue
        case "$peer" in 127.*|'[::1]'*|'*'*|0.0.0.0*|'[::]'*) continue ;; esac
        poll_hits["$peer"]=1
      done < <(ss -Htuanp 2>/dev/null | grep -E "pid=($pids)," | awk '{print $6}')
    fi
  fi
  sleep 1
done
cleanup; rules=()

# --- read back ---------------------------------------------------------------------------------------------------
# Only the journal is time-bounded; a raw dmesg would carry LOG lines from an earlier run, so it is not a fallback.
klog="$(journalctl -k --since "@$t0" -o cat 2>/dev/null)" || { echo "V12 fail: journalctl -k --since @$t0 failed; cannot read the firewall log"; exit 1; }
attributed="$(printf '%s\n' "$klog" | python3 -c '
import re, sys
seen = []
for line in sys.stdin:
    m = re.search(r"\[ATLAS v12 (\w+)\].*?DST=([0-9a-fA-F.:]+).*?PROTO=(\w+)(?:.*?DPT=(\d+))?", line)
    if not m:
        continue
    kind, dst, proto, dpt = m.groups()
    key = f"{kind}:{proto}:{dst}" + (f":{dpt}" if dpt else "")
    if key not in seen:
        seen.append(key)
print("\n".join(seen))
')"
hits=()
if [[ -n "$attributed" ]]; then mapfile -t hits <<<"$attributed"; fi
for k in "${!poll_hits[@]}"; do hits+=("ss:$k"); done

squid_all=0; squid_denied=0
if [[ -r /var/log/squid/access.log ]]; then
  squid_all="$(awk -v t="$t0" '$1+0 >= t' /var/log/squid/access.log | wc -l)"
  squid_denied="$(awk -v t="$t0" '$1+0 >= t && /TCP_DENIED/' /var/log/squid/access.log | wc -l)"
fi
ufw_out="$(grep -c '\[UFW BLOCK\] IN= OUT=' <<<"$klog" || true)"
dk_denied="$(grep -c 'ATLAS docker egress denied' <<<"$klog" || true)"
info="method=$method window=${window}s health=${health_at:+200 after ${health_at}s}${health_at:-never 200}; not-attributable in window: squid ${squid_all} lines/${squid_denied} denied, ufw OUT blocks ${ufw_out}, docker egress denied ${dk_denied}"

if [[ -z "$health_at" ]]; then
  echo "V12 fail: $ctr did not answer 200 on /health within ${window}s of the restart; attributed connections: ${#hits[@]}; $info"
  exit 1
fi
if (( ${#hits[@]} > 0 )); then
  echo "V12 fail: $ctr opened ${#hits[@]} connection(s) beyond loopback after hardening: $(printf '%s ' "${hits[@]:0:8}")| $info"
  exit 1
fi
echo "0 connections from $ctr beyond the loopback services in ${window}s after restart; $info"
exit 0
