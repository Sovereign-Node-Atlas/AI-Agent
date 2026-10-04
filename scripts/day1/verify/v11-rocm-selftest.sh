#!/usr/bin/env bash
# verify/v11-rocm-selftest.sh — V11 (Section 21; Section 17 Phase 4 step 1): inside the ROCm container, rocminfo
# reports gfx1151 and the PyTorch wheel passes the tensor, matmul and diffusion self-tests (phase4/selftest.py).
# This is the first and only place ROCm runs (rule §7.5: never on the host).
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; one line on stdout; never prompts; under
# 10 minutes: the whole script works inside a 600 s budget (run_verify kills at 660 s). The FIRST run gets 540 s
# (research rocm-containers.md §6.3 says `timeout 900`; a fresh node pays image start, the torch/ROCm import, the CPU
# fp32 4096^3 reference matmul, a 4 GB GTT fill and MIOpen compiling every conv/GroupNorm kernel of the UNet for
# gfx1151 into an EMPTY cache, so 300 s was a realistic false "hang"; fix round 3). A run that uses its whole budget is
# a hang, the kernel-7.0 symptom of ROCm/legacy-rocm-build #6530 / ROCm/ROCm #6182, and counts as a fail, not a wait.
# Usage: v11-rocm-selftest.sh [IMAGE]   (default: base_image.tag of config/phase4-engines.json, built by
#        phase4-engines.sh step 01; the image must already exist — this script builds nothing)
# Relies on /etc/atlas/docker.env (Phase 1 step 6: ATLAS_UID ATLAS_GID RENDER_GID VIDEO_GID) and on
# $ATLAS_SRV/engines existing (Phase 1 step 3). Writes, ROOT-HELD under $ATLAS_STATE/phase4/: v11.json (the full step
# table, captured from the container's stdout as a tagged V11JSON line; the container writes nothing but the MIOpen
# cache) and v11-runflags.txt (contract for phase4/lib-engine.sh GPU runs: one docker flag per line). The flags file
# decides whether every later GPU test container runs without the default seccomp profile and/or with the host IPC
# namespace, so it never sits on the atlas-writable tree; lib-engine.sh refuses a flags file not owned by uid 0.
#
# Mounts (fix round 3: the same least-privilege profile phase4/lib-engine.sh gives GPU runs): ONLY
# $ATLAS_SRV/engines/miopen read-write (the kernel cache that every later run benefits from) and the phase4/ directory
# read-only; HOME is a throw-away tmpfs, PIP_CONFIG_FILE=/dev/null, TORCH_HOME on the tmpfs. No venv, src, hf, dl or
# home tree is mounted: the only code that runs is the image's own.
#
# Container flags (fix round). Appendix B's container line is /dev/kfd, /dev/dri and the service user in render/video.
# The research §6.2 line adds seccomp=unconfined (marked optional) and --ipc=host; SYS_PTRACE is "only for
# debuggers/profilers" and is never used. So the self-test runs FIRST with the hardened profile (default seccomp,
# --cap-drop ALL, no-new-privileges, no --ipc). ONLY when that fails on a non-timeout error is it retried, one flag at
# a time (fix round 3: the two flags were probed together and both then reached every GPU run executing third-party
# code): --security-opt seccomp=unconfined alone; then seccomp=unconfined + --ipc=host; then --ipc=host alone. The
# file records exactly the lines of the FIRST passing rung, every Phase 4 GPU run uses exactly that set, and a WARN
# names the relaxation (it changes the posture of every later GPU run). Each rung only runs while at least 120 s of
# the 600 s budget remain; otherwise the record says so and names `--force 01` for a re-run.
# Every run: --pids-limit 1024 --memory 32g (GTT is not charged to the cgroup; this bounds CPU-side memory only).
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

json="$ATLAS_DAY1_DIR/config/phase4-engines.json"
image="${1:-}"
if [[ -z "$image" ]]; then
  [[ -f "$json" ]] || { echo "V11 fail: $json missing and no IMAGE argument"; exit 1; }
  image="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["base_image"]["tag"])' "$json")"
fi
denv="$ATLAS_ETC/docker.env"
engines="$ATLAS_SRV/engines"
flags_dir="$ATLAS_STATE/phase4"
flags_file="$flags_dir/v11-runflags.txt"
json_out="$flags_dir/v11.json"
command -v docker >/dev/null || { echo "V11 fail: docker is not installed (Phase 1 step 6)"; exit 1; }
[[ -r "$denv" ]] || { echo "V11 fail: $denv missing (Phase 1 step 6 writes the container uids/gids)"; exit 1; }
[[ -d "$engines" ]] || { echo "V11 fail: $engines missing (data volume not mounted, Phase 1 step 3)"; exit 1; }
[[ -f "$ATLAS_DAY1_DIR/phase4/selftest.py" ]] || { echo "V11 fail: $ATLAS_DAY1_DIR/phase4/selftest.py missing"; exit 1; }
docker image inspect "$image" >/dev/null 2>&1 || { echo "V11 fail: image $image does not exist (phase4-engines.sh step 01 builds it)"; exit 1; }
[[ -e /dev/kfd ]] || { echo "V11 fail: /dev/kfd does not exist on the host (amdgpu compute interface not up)"; exit 1; }

denv_get() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$denv"; }
uid="$(denv_get ATLAS_UID)"; gid="$(denv_get ATLAS_GID)"
rgid="$(denv_get RENDER_GID)"; vgid="$(denv_get VIDEO_GID)"
for v in uid gid rgid vgid; do
  [[ -n "${!v}" ]] || { echo "V11 fail: $denv lacks $v (Phase 1 step 6 contract)"; exit 1; }
done
# The MIOpen cache: created ONCE, 700, atlas-owned (kernel DBs land there); never chmod/chown of an existing path and
# never through a symlink (the tree is atlas-writable). Best effort when run by hand as non-root; the driver runs this
# as root.
if [[ -L "$engines/miopen" ]]; then echo "V11 fail: $engines/miopen is a symlink; refusing"; exit 1; fi
[[ -d "$engines/miopen" ]] || install -d -m 700 -o "$uid" -g "$gid" "$engines/miopen" 2>/dev/null || true

# v11_run LABEL EXTRA_FLAG... — one container run under $cap seconds; sets rc, summary and stepjson. Never prompts, no
# TTY. The step table comes back as a tagged V11JSON line on stdout (selftest.py prints it when V11_OUT is unset).
rc=0
summary=""
stepjson=""
v11_run() {
  local label="$1"; shift
  local name="v11-selftest-$$-$label" out
  out="$(mktemp)"
  rc=0
  timeout -k 15 "$cap" docker run --rm --name "$name" \
    --device /dev/kfd --device /dev/dri \
    --group-add "$vgid" --group-add "$rgid" \
    --user "$uid:$gid" --cap-drop ALL --security-opt no-new-privileges \
    --pids-limit 1024 --memory 32g --memory-swap 32g \
    --network none "$@" \
    --tmpfs "/tmp:mode=700,uid=$uid,gid=$gid" -e HOME=/tmp -e XDG_CACHE_HOME=/tmp/.cache \
    -e TORCH_HOME=/tmp/.cache/torch -e PIP_CONFIG_FILE=/dev/null \
    -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 \
    -e PYTHONNOUSERSITE=1 -e V11_EXPECT_DEVICE_WHEEL=1 -e V11_OUT= \
    -v "$engines/miopen:/srv/atlas/engines/miopen" \
    -v "$ATLAS_DAY1_DIR/phase4:/opt/atlas/phase4:ro" \
    "$image" python3.12 /opt/atlas/phase4/selftest.py </dev/null >"$out" || rc=$?
  docker rm -f "$name" >/dev/null 2>&1 || true
  summary="$(grep -a '^V11 ' "$out" | tail -n1 || true)"
  stepjson="$(grep -a '^V11JSON ' "$out" | tail -n1 | sed 's/^V11JSON //' || true)"
  rm -f "$out"
}

# write_json — the step table of the LAST run, root-held (container output is data: parsed before it is kept).
write_json() {
  [[ -n "$stepjson" ]] || return 0
  mkdir -p "$flags_dir" 2>/dev/null || true
  [[ -L "$json_out" ]] && rm -f "$json_out"
  if python3 -c 'import json,sys; json.dump(json.loads(sys.argv[1]), open(sys.argv[2], "w", encoding="utf-8"), indent=2)' "$stepjson" "$json_out.tmp" 2>/dev/null; then
    chmod 644 "$json_out.tmp" && mv -f "$json_out.tmp" "$json_out" || true
  else
    rm -f "$json_out.tmp"; warn "V11: the container's V11JSON line did not parse; step table not kept"
  fi
}

# write_flags LINE... — record the flag set the GPU runs must use, root-held under $ATLAS_STATE/phase4 (header).
write_flags() {
  mkdir -p "$flags_dir" 2>/dev/null || true
  [[ -L "$flags_file" ]] && rm -f "$flags_file"
  if {
    echo "# written by verify/v11-rocm-selftest.sh $(date -Is): the docker flags every Phase 4 GPU run adds"
    echo "# (empty = the default seccomp profile passed; phase4/lib-engine.sh accepts only --security-opt=seccomp=unconfined and --ipc=host, each on its own line)"
    (( $# == 0 )) || printf '%s\n' "$@"
  } >"$flags_file.tmp" 2>/dev/null; then
    { chmod 644 "$flags_file.tmp" && mv -f "$flags_file.tmp" "$flags_file"; } || warn "could not install $flags_file"
  else
    warn "could not write $flags_file (run as root)"
  fi
}

t0=$SECONDS
budget=600          # run_verify kills at 660 s; -k 15 grace per run stays inside it
cap=540
v11_run hardened
write_json
used="hardened (default seccomp, no ipc=host)"
if (( rc == 124 || rc == 137 )); then
  echo "V11 fail: self-test hung and was killed after ${cap} s in $image (hardened run, not retried: a hang is not a seccomp symptom) — on kernel 7.0 + gfx1151 this matches the open hang reports ROCm/legacy-rocm-build #6530 and ROCm/ROCm #6182: the host kernel may be the cause, not the container (Principal: no Day 1 action; see the README)"
  exit 1
fi
passed_flags=()
if (( rc != 0 )); then
  first="${summary:-selftest.py exit $rc with no summary}"
  # The ladder (header): one relaxation at a time, within what is left of the budget.
  rungs=("--security-opt seccomp=unconfined" "--security-opt seccomp=unconfined --ipc=host" "--ipc=host")
  tried=()
  for rung in "${rungs[@]}"; do
    left=$(( budget - (SECONDS - t0) ))
    if (( left < 120 )); then
      echo "V11 fail: hardened run failed ($first); ${#tried[@]} relaxed rung(s) tried (${tried[*]:-none}) and only ${left} s of the 600 s budget remain for [$rung]: not attempted. Re-run with: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase4 --force 01 (image $image)"
      exit 1
    fi
    cap="$left"
    warn "V11: hardened run failed (${first:0:200}); retrying with [$rung] within ${cap} s (research §6.2; the flags that pass become part of EVERY later GPU run)"
    # shellcheck disable=SC2086  # the rung is a short list of docker flags, split on purpose
    v11_run relaxed $rung
    write_json
    tried+=("[$rung] exit $rc")
    if (( rc == 124 || rc == 137 )); then
      echo "V11 fail: hardened run failed ($first); the relaxed run [$rung] then hung and was killed after ${cap} s in $image (kernel 7.0 hang reports #6530 #6182 may apply)"
      exit 1
    fi
    if (( rc == 0 )); then
      # Record exactly the flags of this rung, in lib-engine.sh's spelling (one per line).
      [[ "$rung" == *seccomp=unconfined* ]] && passed_flags+=("--security-opt=seccomp=unconfined")
      [[ "$rung" == *--ipc=host* ]] && passed_flags+=("--ipc=host")
      used="relaxed [$rung] (the default profile failed: ${first:0:160})"
      break
    fi
  done
fi

case "$rc" in
  0)
    write_flags "${passed_flags[@]}"
    if (( ${#passed_flags[@]} > 0 )); then
      warn "V11: the GPU container sandbox is RELAXED for every Phase 4 GPU run: ${passed_flags[*]} (recorded in $flags_file; Principal: the hardened profile did not pass the self-test on this node, and every later GPU container executing third-party engine code now runs with these flags)"
    fi
    [[ -n "$summary" ]] || summary="V11 ok (selftest.py printed no summary line)"
    echo "$summary (image $image; container flags: $used; recorded in $flags_file; step table $json_out)"
    exit 0 ;;
  *)
    echo "${summary:-V11 FAIL: selftest.py exit $rc with no summary (see stderr / journal)} (image $image; the hardened run and every relaxed rung failed: ${tried[*]:-}; kernel 7.0 hang/mmap reports #6530 #6182 may apply)"
    exit 1 ;;
esac
