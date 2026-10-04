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
#   3. Work-directory cap AND the package's run line (fix rounds 5 and 6; Section 16.4 "a hard memory limit" extends to
#      the job's files): /work is the package's bounded tmpfs (`--mount type=tmpfs,dst=/work,tmpfs-size=...`,
#      SANDBOX_WORK_SIZE, default 2g), here 64m. This run is the package's EXACT line: a job directory staged like
#      sandbox.run() stages it (directory 2770 group atlas, main.py 0640) bind-mounted read-only at /stage, and the
#      in-container prologue/epilogue RUN_SHELL read from the installed package (/opt/atlas/venv; the repository source
#      when the venv has no package yet, said in the message): `cp` of /stage into /work as uid 65534, the job's stdout
#      to /work/.stdout, `tar -cf - .` of /work on the container's stdout, the job's own exit code. main.py writes 1 MiB
#      chunks to /work/fill until the write fails and must see ENOSPC (errno 28) after no more than 64 MiB and no less
#      than 32 MiB (a cap that is not there would let 128 MiB through; one far smaller than asked is a different mount),
#      removes /work/fill (the job's stdout is /work/.stdout under this run line, so a job that has filled /work must
#      free space before its last line can land there; it also keeps the results tar small) and exits 0 printing the
#      figure. Asserted: exit 0 (so the prologue ran: fix round 6 found the earlier
#      `cp --preserve` failing with EPERM on the root-owned mount point and no job ever running), the results tar lists
#      ./.stdout and ./main.py, and ./.stdout carries the ENOSPC line. The old unbounded `-v <job>:/work:rw` bind mount
#      would have put those bytes on the 8 TB volume; the tmpfs is charged to the job's memory cgroup.
# The exit codes and the elapsed seconds of the runs are printed, with the image id and the base image the Dockerfile
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
name3="atlas-v17-work-$stamp"
errf="$(mktemp)"
stage3="$(mktemp -d)"
tar3="$(mktemp)"
trap 'rm -rf "$errf" "$stage3" "$tar3"; docker rm -f "$name" "$name2" "$name3" >/dev/null 2>&1 || true' EXIT

# The run line of docker/sandbox/Dockerfile minus the job mount (nothing to mount here). The gid is the atlas group's,
# as the package's build_argv passes it (header).
atlas_gid="$(getent group atlas 2>/dev/null | cut -d: -f3 || true)"
gid_note=""
if [[ -z "$atlas_gid" ]]; then atlas_gid=65534; gid_note=" (atlas group absent: ran as 65534:65534, not the package's gid)"; fi
# fsize is per run: 1 MiB for the two bombs (nothing to write), the package's default SANDBOX_FSIZE (1 GiB) for run 3,
# whose writer must reach the tmpfs cap, not the file-size cap (RLIMIT_FSIZE is in bytes).
run_flags=(--init --pull never --cpus=1 --pids-limit=64 --network none --read-only
  --tmpfs "/tmp:rw,noexec,nosuid,nodev,size=64m"
  --cap-drop ALL --security-opt no-new-privileges --user "65534:$atlas_gid")

# --- 1. the memory cap ------------------------------------------------------------------------------------------------
bomb='a = []
while True:
    a.append(b"x" * (64 << 20))'
t0="$(date +%s.%N)"
rc=0
timeout -k 5 "$timeout_s" docker run --rm --name "$name" --memory=512m --memory-swap=512m \
  -e "SANDBOX_TIMEOUT_S=$timeout_s" "${run_flags[@]}" --ulimit fsize=1048576 \
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
  -e "SANDBOX_TIMEOUT_S=$in_limit" "${run_flags[@]}" --ulimit fsize=1048576 \
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

# --- 3. the /work tmpfs cap under the package's run line (Section 16.4; orchestrator/src/atlas/sandbox.py build_argv) ------
# The same mount flag the package emits, with a 64 MiB cap; the memory cap is well above it so the OOM killer cannot be
# what stops the writer (tmpfs pages are charged to the cgroup). The writer stops at the first failed write. It runs as
# the staged /work/main.py of a job directory, through RUN_SHELL exactly as the package types it (header item 3).
run_shell=""
run_shell_src="$ATLAS_OPT/venv (installed package)"
run_shell="$("$ATLAS_OPT/venv/bin/python" -c 'from atlas.sandbox import RUN_SHELL; print(RUN_SHELL)' 2>/dev/null || true)"
if [[ -z "$run_shell" ]]; then
  # No installed package yet (V17 run by hand before step 02): the repository source is the same text; sandbox.py
  # imports the standard library only, so the host python3 can read it.
  run_shell="$(PYTHONPATH="$ATLAS_DAY1_DIR/orchestrator/src" python3 -c 'from atlas.sandbox import RUN_SHELL; print(RUN_SHELL)' 2>/dev/null || true)"
  run_shell_src="$ATLAS_DAY1_DIR/orchestrator/src (repository source; the venv has no atlas package)"
fi
[[ -n "$run_shell" ]] || { echo "V17 fail: cannot read atlas.sandbox.RUN_SHELL from $ATLAS_OPT/venv or $ATLAS_DAY1_DIR/orchestrator/src (the package's run line is what this run proves); $host"; exit 1; }
# Unbuffered writes (buffering=0): the failing write raises where it happens, not again at close. The fill is removed
# before printing: under the package's run line stdout is /work/.stdout on the very tmpfs the job has just filled.
filler='import errno, os, sys
chunk = b"x" * (1 << 20)
written = 0
err = None
try:
    fh = open("/work/fill", "wb", buffering=0)
    while written < 128:
        fh.write(chunk)
        os.fsync(fh.fileno())
        written += 1
except OSError as exc:
    err = exc
try:
    fh.close()
except Exception:
    pass
try:
    os.remove("/work/fill")
except OSError:
    pass
if err is not None and err.errno == errno.ENOSPC:
    print(f"ENOSPC after {written} MiB")
    sys.exit(0)
if err is not None:
    print(f"OSError {err.errno} {err}")
    sys.exit(4)
print(f"no ENOSPC: {written} MiB written")
sys.exit(5)'
work_cap=64m
# The job directory as sandbox.run() stages it: 2770 group atlas (the gid the container runs with), main.py 0640, so the
# nobody-uid process reads /stage through the group bit exactly as a real job does (sandbox.py module docstring).
printf '%s\n' "$filler" >"$stage3/main.py"
chgrp "$atlas_gid" "$stage3" "$stage3/main.py"
chmod 2770 "$stage3"; chmod 640 "$stage3/main.py"
t0="$(date +%s.%N)"
rc3=0
timeout -k 5 60 docker run --rm --name "$name3" --memory=512m --memory-swap=512m \
  -e "SANDBOX_TIMEOUT_S=50" "${run_flags[@]}" --ulimit "fsize=$((1 << 30))" \
  --mount "type=tmpfs,dst=/work,tmpfs-size=$work_cap" -v "$stage3:/stage:ro" -w /work \
  "$image" sh -c "$run_shell" sh python3 /work/main.py >"$tar3" 2>"$errf" || rc3=$?
t1="$(date +%s.%N)"
elapsed3="$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }')"
docker rm -f "$name3" >/dev/null 2>&1 || true
if (( rc3 != 0 )); then
  echo "V17 fail: the package's run line (RUN_SHELL from $run_shell_src; /work tmpfs $work_cap): expected the staged main.py to hit ENOSPC and the shell to exit 0, got exit $rc3 after ${elapsed3}s: $(tr '\n' ' ' <"$errf" | cut -c1-300); a prologue that cannot run as 65534:$atlas_gid (cp of /stage into /work) fails every sandbox job; $host"
  exit 1
fi
listing="$(tar -tf "$tar3" 2>/dev/null || true)"
if ! grep -qx './.stdout' <<<"$listing" || ! grep -qx './main.py' <<<"$listing"; then  # `./` and ./fill are gone
  echo "V17 fail: the results tar the run line streamed ($(stat -c %s "$tar3" 2>/dev/null || echo ?) bytes) does not list ./.stdout and ./main.py (got: $(tr '\n' ' ' <<<"$listing" | cut -c1-200)): the copy into /work or the tar epilogue did not run as the package expects; $host"
  exit 1
fi
out3="$(tar -xOf "$tar3" ./.stdout 2>/dev/null | tr -d '\r' | tail -n1 || true)"
mib="$(sed -nE 's/^ENOSPC after ([0-9]+) MiB$/\1/p' <<<"$out3")"
if [[ -z "$mib" ]] || (( mib > 64 || mib < 32 )); then
  echo "V17 fail: /work tmpfs cap ($work_cap) under the package's run line: ./.stdout in the results tar reads '${out3}' (expected ENOSPC between 32 and 64 MiB); the mount is not the bounded tmpfs the package's run line declares; $host"
  exit 1
fi

echo "runaway python killed by the 512m cap: exit 137 after ${elapsed}s (limit ${timeout_s}s); SIGTERM-ignoring job killed by the in-container timeout: exit 137 after ${elapsed2}s (limit ${in_limit}s), container gone; the package's run line (RUN_SHELL from $run_shell_src: cp /stage -> /work, .stdout, results tar) ran a staged main.py to exit 0 and /work tmpfs cap $work_cap gave ENOSPC after ${mib} MiB (${elapsed3}s; the package mounts /work as tmpfs-size=SANDBOX_WORK_SIZE, default 2g); $host (Δload1 $load_delta)$llama_note; runs as 65534:$atlas_gid$gid_note; image $image ${image_id:-?} (base ${image_base:-unlabelled})"
exit 0
