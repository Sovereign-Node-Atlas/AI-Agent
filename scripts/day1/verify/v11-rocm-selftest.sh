#!/usr/bin/env bash
# verify/v11-rocm-selftest.sh — V11 (Section 21; Section 17 Phase 4 step 1): inside the ROCm container, rocminfo
# reports gfx1151 and the PyTorch wheel passes the tensor, matmul and diffusion self-tests (phase4/selftest.py).
# This is the first and only place ROCm runs (rule §7.5: never on the host).
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; one line on stdout; never prompts; well
# under 10 minutes (each container run is capped so that both attempts fit in run_verify's 660 s: a hang is the
# kernel-7.0 symptom of ROCm/legacy-rocm-build #6530 / ROCm/ROCm #6182 and must count as a fail, not wait; research
# rocm-containers.md §6.3).
# Usage: v11-rocm-selftest.sh [IMAGE]   (default: base_image.tag of config/phase4-engines.json, built by
#        phase4-engines.sh step 01; the image must already exist — this script builds nothing)
# Relies on /etc/atlas/docker.env (Phase 1 step 6: ATLAS_UID ATLAS_GID RENDER_GID VIDEO_GID) and on
# $ATLAS_SRV/engines existing (Phase 1 step 3). Writes $ATLAS_SRV/engines/v11.json (the full step table) and
# $ATLAS_SRV/engines/v11-runflags.txt (contract for phase4/lib-engine.sh GPU runs: one docker flag per line).
#
# Container flags (fix round). Appendix B's container line is /dev/kfd, /dev/dri and the service user in render/video.
# The research §6.2 line adds seccomp=unconfined (marked optional) and --ipc=host; SYS_PTRACE is "only for
# debuggers/profilers" and is never used. So the self-test runs FIRST with the hardened profile (default seccomp,
# --cap-drop ALL, no-new-privileges, no --ipc) and ONLY when that fails on a non-timeout error is it re-run once with
# seccomp=unconfined + ipc=host; the file records which set passed, and every Phase 4 GPU run uses exactly that set.
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
flags_file="$engines/v11-runflags.txt"
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
# The container HOME and the MIOpen cache: 700 (credential caches and kernel DBs land there). Best effort when run by
# hand as non-root; the driver runs this as root.
for d in home miopen; do
  if [[ ! -d "$engines/$d" || "$(stat -c '%a' "$engines/$d" 2>/dev/null)" != 700 ]]; then
    install -d -m 700 -o "$uid" -g "$gid" "$engines/$d" 2>/dev/null || true
  fi
done

# v11_run LABEL EXTRA_FLAG... — one container run; sets rc and summary. Never prompts, no TTY.
rc=0
summary=""
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
    -e HOME=/srv/atlas/engines/home -e HF_HUB_OFFLINE=1 -e HF_HUB_DISABLE_TELEMETRY=1 -e DO_NOT_TRACK=1 \
    -e V11_OUT=/srv/atlas/engines/v11.json \
    -v "$engines:/srv/atlas/engines" \
    -v "$ATLAS_DAY1_DIR/phase4:/opt/atlas/phase4:ro" \
    "$image" python3.12 /opt/atlas/phase4/selftest.py </dev/null >"$out" || rc=$?
  docker rm -f "$name" >/dev/null 2>&1 || true
  summary="$(grep -a '^V11 ' "$out" | tail -n1 || true)"
  rm -f "$out"
}

# write_flags LINE... — record the flag set the GPU runs must use (never through a symlink: the tree is atlas-writable).
write_flags() {
  [[ -L "$flags_file" ]] && rm -f "$flags_file"
  {
    echo "# written by verify/v11-rocm-selftest.sh $(date -Is): the docker flags every Phase 4 GPU run adds"
    echo "# (empty = the default seccomp profile passed; phase4/lib-engine.sh accepts only the two lines below)"
    printf '%s\n' "$@"
  } >"$flags_file" 2>/dev/null || warn "could not write $flags_file (run as root)"
}

t0=$SECONDS
cap=300
v11_run hardened
elapsed=$(( SECONDS - t0 ))
used="hardened (default seccomp, no ipc=host)"
if (( rc == 124 || rc == 137 )); then
  echo "V11 fail: self-test hung and was killed after ${cap} s in $image (hardened run, not retried: a hang is not a seccomp symptom) — on kernel 7.0 + gfx1151 this matches the open hang reports ROCm/legacy-rocm-build #6530 and ROCm/ROCm #6182: the host kernel may be the cause, not the container (Principal: no Day 1 action; see the README)"
  exit 1
fi
if (( rc != 0 )); then
  first="${summary:-selftest.py exit $rc with no summary}"
  cap=$(( 540 - elapsed )); (( cap < 120 )) && cap=120
  warn "V11: hardened run failed (${first:0:200}); retrying once with --security-opt seccomp=unconfined --ipc=host (research §6.2 line) within ${cap} s"
  v11_run relaxed --security-opt seccomp=unconfined --ipc=host
  used="relaxed (seccomp=unconfined, ipc=host: the default profile failed: ${first:0:160})"
  if (( rc == 124 || rc == 137 )); then
    echo "V11 fail: hardened run failed ($first); the relaxed run then hung and was killed after ${cap} s in $image (kernel 7.0 hang reports #6530 #6182 may apply)"
    exit 1
  fi
fi

case "$rc" in
  0)
    if [[ "$used" == hardened* ]]; then write_flags; else write_flags "--security-opt=seccomp=unconfined" "--ipc=host"; fi
    [[ -n "$summary" ]] || summary="V11 ok (selftest.py printed no summary line)"
    echo "$summary (image $image; container flags: $used; recorded in $flags_file)"
    exit 0 ;;
  *)
    echo "${summary:-V11 FAIL: selftest.py exit $rc with no summary (see stderr / journal)} (image $image; both the hardened and the relaxed flag sets failed; kernel 7.0 hang/mmap reports #6530 #6182 may apply)"
    exit 1 ;;
esac
