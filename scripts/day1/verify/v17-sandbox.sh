#!/usr/bin/env bash
# verify/v17-sandbox.sh — V17: "Sandbox memory cap kills a runaway process without affecting the node" (Sections 16.4,
# 21; Phase 2 gate). Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail; exactly one stdout line; never prompts; well
# under 10 minutes; safe to re-run. Must run as root or as a docker-group member.
# Usage: v17-sandbox.sh [IMAGE=atlas-sandbox:py3.12] [TIMEOUT_S=120]
#
# Method, two runs under the run line of docker/sandbox/Dockerfile (--init, --read-only, --cap-drop ALL, --user
# 65534:<atlas gid> — the gid the package's run line uses (orchestrator/src/atlas/sandbox.py build_argv), resolved from
# getent so a gid-dependent failure of the real line is caught here, fix round 4; falls back to 65534 only when the atlas
# group does not exist, and says so):
#   1. Memory cap: a python one-liner that appends 64 MiB of non-zero bytes to a list forever (non-zero so every page
#      is really touched and charged to the cgroup) under --memory=512m --memory-swap=512m. The kernel's OOM killer
#      inside the memory cgroup sends SIGKILL, which docker reports as exit 137 (128 + 9; services-tools.md §6: the
#      OOMKilled flag is VERIFIED, the 137 convention is UNVERIFIED by the Docker docs but universal). Before and
#      after, /proc/meminfo MemAvailable and the 1-minute load are compared: the host must not have moved materially
#      (|ΔMemAvailable| <= max(1 GiB, 2 % of MemTotal), Δload1 < 2.0), and any llama-server unit that was active
#      before must still be active after.
#   2. Timeout (fix round): a python script that ignores SIGTERM and sleeps for ever runs with -e SANDBOX_TIMEOUT_S=5.
#      The image's entrypoint (coreutils `timeout -s KILL`) must SIGKILL it at the deadline (exit 137 within a few
#      seconds of 5 s) and the container must be gone afterwards, so a program that ignores the host-side signal has
#      no way to outlive its time bound. The host-side GNU timeout is only the backstop and must not be what fired.
# The exit codes and the elapsed seconds of both runs are printed, with the image id and the base image the Dockerfile
# pinned by digest (label org.atlas.sandbox.base; fix round 2, rule §7.9). The image is built by phase2/06d-sandbox.sh.
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
  echo "V17 fail: image $image does not exist; build it: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06d (phase2/06d-sandbox.sh runs docker build -t $image $ATLAS_DAY1_DIR/docker/sandbox)"
  exit 1
fi
image_id="$(docker image inspect -f '{{.Id}}' "$image" 2>/dev/null | cut -c8-19 || true)"
image_ver="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.version"}}' "$image" 2>/dev/null || true)"
[[ "$image_ver" == 4 ]] || { echo "V17 fail: $image carries label org.atlas.sandbox.version='${image_ver:-none}', expected 4 (digest-pinned base + the in-container timeout entrypoint + telemetry opt-outs); rebuild: docker rmi $image; sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06d"; exit 1; }
image_base="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.base"}}' "$image" 2>/dev/null || true)"

meminfo() { awk -v k="$1" '$1 == k ":" { print $2; exit }' /proc/meminfo; }   # kB
load1() { cut -d' ' -f1 /proc/loadavg; }
llama_active_before=()
for u in $(systemctl list-units --type=service --state=active --plain --no-legend 'llama-server@*' 2>/dev/null | awk '{print $1}'); do
  llama_active_before+=("$u")
done

mem_total_kb="$(meminfo MemTotal)"
avail_before="$(meminfo MemAvailable)"
load_before="$(load1)"
stamp="$$-$(date +%s)"
name="atlas-v17-$stamp"
name2="atlas-v17-timeout-$stamp"
errf="$(mktemp)"
trap 'rm -f "$errf"; docker rm -f "$name" "$name2" >/dev/null 2>&1 || true' EXIT

# The run line of docker/sandbox/Dockerfile minus the job mount (nothing to mount here). The gid is the atlas group's,
# as the package's build_argv passes it (header).
atlas_gid="$(getent group atlas 2>/dev/null | cut -d: -f3 || true)"
gid_note=""
if [[ -z "$atlas_gid" ]]; then atlas_gid=65534; gid_note=" (atlas group absent: ran as 65534:65534, not the package's gid)"; fi
run_flags=(--init --pull never --cpus=1 --pids-limit=64 --network none --read-only
  --tmpfs "/tmp:rw,noexec,nosuid,nodev,size=64m" --ulimit fsize=1048576
  --cap-drop ALL --security-opt no-new-privileges --user "65534:$atlas_gid")

# --- 1. the memory cap ------------------------------------------------------------------------------------------------
bomb='a = []
while True:
    a.append(b"x" * (64 << 20))'
t0="$(date +%s.%N)"
rc=0
timeout -k 5 "$timeout_s" docker run --rm --name "$name" --memory=512m --memory-swap=512m \
  -e "SANDBOX_TIMEOUT_S=$timeout_s" "${run_flags[@]}" \
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

# --- 2. the time bound, against a program that ignores SIGTERM --------------------------------------------------------
stubborn='import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGINT, signal.SIG_IGN)
while True:
    time.sleep(1)'
in_limit=5
host_limit=40   # the backstop; it must NOT be what ends the run
t0="$(date +%s.%N)"
rc2=0
timeout -k 5 "$host_limit" docker run --rm --name "$name2" --memory=64m --memory-swap=64m \
  -e "SANDBOX_TIMEOUT_S=$in_limit" "${run_flags[@]}" \
  "$image" python3 -c "$stubborn" >/dev/null 2>"$errf" || rc2=$?
t1="$(date +%s.%N)"
elapsed2="$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }')"
left="$(docker ps -q --filter "name=^${name2}$" 2>/dev/null || true)"
docker rm -f "$name2" >/dev/null 2>&1 || true
if [[ -n "$left" ]]; then
  echo "V17 fail: the SIGTERM-ignoring job was still running after the run returned (exit $rc2, ${elapsed2}s): the in-container timeout did not kill it; $host"
  exit 1
fi
if (( rc2 != 137 )); then
  echo "V17 fail: SIGTERM-ignoring job under SANDBOX_TIMEOUT_S=$in_limit: expected exit 137 (SIGKILL by the entrypoint's timeout), got exit $rc2 after ${elapsed2}s: $(tr '\n' ' ' <"$errf" | cut -c1-200); $host"
  exit 1
fi
if awk -v e="$elapsed2" -v t="$in_limit" 'BEGIN { exit (e < t + 10) ? 0 : 1 }'; then :; else
  echo "V17 fail: SIGTERM-ignoring job exited 137 only after ${elapsed2}s (in-container limit ${in_limit}s): the host backstop, not the entrypoint, ended it; $host"
  exit 1
fi

echo "runaway python killed by the 512m cap: exit 137 after ${elapsed}s (limit ${timeout_s}s); SIGTERM-ignoring job killed by the in-container timeout: exit 137 after ${elapsed2}s (limit ${in_limit}s), container gone; $host (Δload1 $load_delta)$llama_note; runs as 65534:$atlas_gid$gid_note; image $image ${image_id:-?} (base ${image_base:-unlabelled})"
exit 0
