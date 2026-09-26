#!/usr/bin/env bash
# verify/v11-rocm-selftest.sh — V11 (Section 21; Section 17 Phase 4 step 1): inside the ROCm container, rocminfo
# reports gfx1151 and the PyTorch wheel passes the tensor, matmul and diffusion self-tests (phase4/selftest.py).
# This is the first and only place ROCm runs (rule §7.5: never on the host).
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; one line on stdout; never prompts; well
# under 10 minutes (the container run is capped at 540 s: a hang is the kernel-7.0 symptom of ROCm/legacy-rocm-build
# #6530 / ROCm/ROCm #6182 and must count as a fail, not wait; research rocm-containers.md §6.3).
# Usage: v11-rocm-selftest.sh [IMAGE]   (default: base_image.tag of config/phase4-engines.json, built by
#        phase4-engines.sh step 01; the image must already exist — this script builds nothing)
# Relies on /etc/atlas/docker.env (Phase 1 step 6: ATLAS_UID ATLAS_GID RENDER_GID VIDEO_GID) and on
# $ATLAS_SRV/engines existing (Phase 1 step 3). Writes $ATLAS_SRV/engines/v11.json (the full step table).
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
command -v docker >/dev/null || { echo "V11 fail: docker is not installed (Phase 1 step 6)"; exit 1; }
[[ -r "$denv" ]] || { echo "V11 fail: $denv missing (Phase 1 step 6 writes the container uids/gids)"; exit 1; }
[[ -d "$ATLAS_SRV/engines" ]] || { echo "V11 fail: $ATLAS_SRV/engines missing (data volume not mounted, Phase 1 step 3)"; exit 1; }
[[ -f "$ATLAS_DAY1_DIR/phase4/selftest.py" ]] || { echo "V11 fail: $ATLAS_DAY1_DIR/phase4/selftest.py missing"; exit 1; }
docker image inspect "$image" >/dev/null 2>&1 || { echo "V11 fail: image $image does not exist (phase4-engines.sh step 01 builds it)"; exit 1; }
[[ -e /dev/kfd ]] || { echo "V11 fail: /dev/kfd does not exist on the host (amdgpu compute interface not up)"; exit 1; }

denv_get() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$denv"; }
uid="$(denv_get ATLAS_UID)"; gid="$(denv_get ATLAS_GID)"
rgid="$(denv_get RENDER_GID)"; vgid="$(denv_get VIDEO_GID)"
for v in uid gid rgid vgid; do
  [[ -n "${!v}" ]] || { echo "V11 fail: $denv lacks $v (Phase 1 step 6 contract)"; exit 1; }
done
mkdir -p "$ATLAS_SRV/engines/home" "$ATLAS_SRV/engines/miopen" 2>/dev/null || true
chown "$uid:$gid" "$ATLAS_SRV/engines/home" "$ATLAS_SRV/engines/miopen" 2>/dev/null || true

name="v11-selftest-$$"
out="$(mktemp)"
rc=0
# Research §6.2 flags; --network none: the self-test downloads nothing (a random UNet, no weights).
timeout --foreground -k 15 540 docker run --rm --name "$name" \
  --device /dev/kfd --device /dev/dri \
  --group-add "$vgid" --group-add "$rgid" \
  --security-opt seccomp=unconfined --cap-add=SYS_PTRACE --ipc=host \
  --user "$uid:$gid" --network none \
  -e HOME=/srv/atlas/engines/home -e HF_HUB_OFFLINE=1 -e V11_OUT=/srv/atlas/engines/v11.json \
  -v "$ATLAS_SRV/engines:/srv/atlas/engines" \
  -v "$ATLAS_DAY1_DIR/phase4:/opt/atlas/phase4:ro" \
  "$image" python3.12 /opt/atlas/phase4/selftest.py </dev/null >"$out" 2> >(cat >&2) || rc=$?
docker rm -f "$name" >/dev/null 2>&1 || true
summary="$(grep -a '^V11 ' "$out" | tail -n1 || true)"
rm -f "$out"

case "$rc" in
  0)
    [[ -n "$summary" ]] || summary="V11 ok (selftest.py printed no summary line)"
    echo "$summary (image $image)"
    exit 0 ;;
  124|137)
    echo "V11 fail: self-test hung and was killed after 540 s in $image — on kernel 7.0 + gfx1151 this matches the open hang reports ROCm/legacy-rocm-build #6530 and ROCm/ROCm #6182: the host kernel may be the cause, not the container (Principal: no Day 1 action; see the README)"
    exit 1 ;;
  *)
    echo "${summary:-V11 FAIL: selftest.py exit $rc with no summary (see stderr / journal)} (image $image; kernel 7.0 hang/mmap reports #6530 #6182 may apply)"
    exit 1 ;;
esac
