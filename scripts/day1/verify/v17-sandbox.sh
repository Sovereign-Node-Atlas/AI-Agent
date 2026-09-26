#!/usr/bin/env bash
# verify/v17-sandbox.sh — V17: "Sandbox memory cap kills a runaway process without affecting the node" (Sections 16.4,
# 21; Phase 2 gate). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; exactly one stdout line; never prompts; well
# under 10 minutes; safe to re-run. Must run as root or as a docker-group member.
# Usage: v17-sandbox.sh [IMAGE=atlas-sandbox:py3.12] [TIMEOUT_S=120]
#
# Method: a python one-liner that appends 64 MiB of non-zero bytes to a list forever (non-zero so every page is really
# touched and charged to the cgroup) runs under
#   docker run --rm --memory=512m --memory-swap=512m --cpus=1 --pids-limit=64 --network none --read-only ...
# with GNU timeout. The kernel's OOM killer inside the memory cgroup sends SIGKILL, which docker reports as exit 137
# (128 + 9; services-tools.md §6: the OOMKilled flag is VERIFIED, the 137 convention is UNVERIFIED by the Docker
# docs but universal). Before and after, /proc/meminfo MemAvailable and the 1-minute load are compared: the host must
# not have moved materially (|ΔMemAvailable| <= max(1 GiB, 2 % of MemTotal), Δload1 < 2.0), and any llama-server
# unit that was active before must still be active after. The exit code and the elapsed seconds are printed.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

image="${1:-atlas-sandbox:py3.12}"
timeout_s="${2:-120}"
[[ "$timeout_s" =~ ^[0-9]+$ ]] || { echo "usage: v17-sandbox.sh [IMAGE] [TIMEOUT_S]"; exit 1; }
command -v docker >/dev/null || { echo "V17 fail: docker not installed (Phase 1 step 6)"; exit 1; }
command -v timeout >/dev/null || { echo "V17 fail: GNU timeout missing (coreutils)"; exit 1; }
docker info >/dev/null 2>&1 || { echo "V17 fail: the docker daemon does not answer (run as root or a docker-group member)"; exit 1; }
if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "V17 fail: image $image does not exist; build it: docker build -t $image $ATLAS_DAY1_DIR/docker/sandbox (phase2/10-gate.sh does this)"
  exit 1
fi

meminfo() { awk -v k="$1" '$1 == k ":" { print $2; exit }' /proc/meminfo; }   # kB
load1() { cut -d' ' -f1 /proc/loadavg; }
llama_active_before=()
for u in $(systemctl list-units --type=service --state=active --plain --no-legend 'llama-server@*' 2>/dev/null | awk '{print $1}'); do
  llama_active_before+=("$u")
done

mem_total_kb="$(meminfo MemTotal)"
avail_before="$(meminfo MemAvailable)"
load_before="$(load1)"
name="atlas-v17-$$-$(date +%s)"
errf="$(mktemp)"
trap 'rm -f "$errf"; docker rm -f "$name" >/dev/null 2>&1 || true' EXIT

bomb='a = []
while True:
    a.append(b"x" * (64 << 20))'
t0="$(date +%s.%N)"
rc=0
timeout -k 5 "$timeout_s" docker run --rm --name "$name" \
  --memory=512m --memory-swap=512m --cpus=1 --pids-limit=64 --network none --read-only \
  --tmpfs /tmp:rw,size=64m --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
  "$image" python3 -c "$bomb" >/dev/null 2>"$errf" || rc=$?
t1="$(date +%s.%N)"
elapsed="$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }')"
# The docker client can be killed by timeout while the container lives on: make sure nothing is left running.
docker rm -f "$name" >/dev/null 2>&1 || true
sleep 1

avail_after="$(meminfo MemAvailable)"
load_after="$(load1)"
delta_kb=$(( avail_after - avail_before ))
abs_delta_kb=$(( delta_kb < 0 ? -delta_kb : delta_kb ))
tol_kb=$(( mem_total_kb / 50 ))            # 2 % of MemTotal
(( tol_kb < 1048576 )) && tol_kb=1048576   # at least 1 GiB
load_delta="$(awk -v a="$load_before" -v b="$load_after" 'BEGIN { printf "%.2f", b - a }')"
gib() { awk -v kb="$1" 'BEGIN { printf "%.1f", kb / 1048576 }'; }
host="host MemAvailable $(gib "$avail_before")→$(gib "$avail_after") GiB (Δ $(gib "$delta_kb") GiB, tolerance $(gib "$tol_kb") GiB), load1 $load_before→$load_after"

llama_note=""
if (( ${#llama_active_before[@]} > 0 )); then
  lost=()
  for u in "${llama_active_before[@]}"; do systemctl is-active --quiet "$u" || lost+=("$u"); done
  if (( ${#lost[@]} > 0 )); then
    echo "V17 fail: exit $rc after ${elapsed}s; llama-server unit(s) no longer active after the sandbox run: ${lost[*]}; $host"
    exit 1
  fi
  llama_note="; ${#llama_active_before[@]} llama-server unit(s) active before and after"
fi

# GNU timeout reports 124 on expiry, or 137 when its own -k SIGKILL was needed: both mean the cap never fired.
timed_out="$(awk -v e="$elapsed" -v t="$timeout_s" 'BEGIN { print (e >= t) ? 1 : 0 }')"
if (( rc == 124 )) || { (( rc == 137 )) && (( timed_out == 1 )); }; then
  echo "V17 fail: the memory cap did not kill the runaway process within ${timeout_s}s (timeout fired, exit $rc after ${elapsed}s); $host"
  exit 1
fi
if (( rc != 137 )); then
  echo "V17 fail: expected exit 137 (SIGKILL by the 512m memory cgroup), got exit $rc after ${elapsed}s: $(tr '\n' ' ' <"$errf" | cut -c1-200); $host"
  exit 1
fi
if (( abs_delta_kb > tol_kb )); then
  echo "V17 fail: exit 137 after ${elapsed}s but the host moved materially: $host"
  exit 1
fi
if awk -v d="$load_delta" 'BEGIN { exit (d < 2.0) ? 0 : 1 }'; then :; else
  echo "V17 fail: exit 137 after ${elapsed}s but load1 rose by $load_delta: $host"
  exit 1
fi
echo "runaway python killed by the 512m cap: exit 137 after ${elapsed}s (limit ${timeout_s}s); $host (Δload1 $load_delta)$llama_note"
exit 0
