#!/usr/bin/env bash
# phase2/06d-sandbox.sh — Section 17 Phase 2 (between steps 6c and 7): the AEGIS sandbox image (Section 16.4; V17 at the
# gate). Sourced by phase2-services.sh through run_phase_steps; defines step_06d only.
#
# STEP ID (fix round 4): Section 17 names no sandbox step, yet Section 16.4 is a hard requirement proven by V17 at the
# Phase 2 gate, and Section 17 step 10 is the judging step ("Gate: every service healthy; ... recorded"). Until this round
# the gate built the image, wrote the SANDBOX_* keys and restarted the orchestrator before judging them — setup inside the
# judge, mutated again on every `--force 10` (§7.5: a step's prerequisites are the steps before it). This gap-fill step
# now owns that setup under the marker phase2.06d; phase2/10-gate.sh only reads (image label version, base-image pin,
# keys present) and V17 proves the caps. BASELINE AMENDMENT NEEDED: CONVENTIONS.md §1 must list `06d-sandbox` in the
# phase2 list and Section 17 Phase 2 needs "6d. AEGIS sandbox image (16.4): atlas-sandbox:py3.12 from
# docker/sandbox/Dockerfile, SANDBOX_* settings for the orchestrator; V17 at the gate" between 6c and 7
# (README-contracts.md §3 item 7).
#
# What it does, in order (each part idempotent; a failure stops the phase, rule §7.4 — a sandbox that cannot be built is
# not something the gate should discover):
#   1. docker/sandbox/Dockerfile -> atlas-sandbox:py3.12, skipped when the image already carries the expected label
#      org.atlas.sandbox.version (bumped whenever the Dockerfile changes; the gate and verify/v17-sandbox.sh check the
#      same value). The base image is pinned by digest (`FROM python:3.12-slim@sha256:...`, rule §7.9; VERIFIED
#      2026-10-04 against registry-1.docker.io by HEAD on the digest and by the `3.12-slim` tag resolving to it); the
#      pull goes through the daemon's proxy (daemon.json, Phase 1 step 6: registry-1.docker.io, auth.docker.io,
#      production.cloudflare.docker.com are allowlisted), the build itself runs with --network none. The pin evidence is
#      the built image's own label org.atlas.sandbox.base (fix round 4: with BuildKit a base pulled during `docker build`
#      lives in the build cache and is NOT listed as a tagged image, so `docker image inspect python:3.12-slim@<digest>`
#      would wrongly suggest an unpinned build on every run).
#   2. A smoke run under the package's run line (--init, --pull never, --network none, --read-only, --cap-drop ALL,
#      no-new-privileges, --user 65534:<atlas gid>: the gid the orchestrator uses, so the job directory's group bit is
#      what is exercised) prints the interpreter version.
#   3. /srv/atlas/sandbox (atlas:atlas 750; Appendix C: backed up) and the SANDBOX_* keys in /etc/atlas/orchestrator.env
#      (the contract atlas.sandbox.build_argv reads: README-contracts.md "Sandbox"), written with ensure_kv so step 2's
#      keys are kept; atlas-orchestrator is restarted ONCE, only when the file's content really changed, and /health must
#      answer 200 again (ensure_kv rewrites the file on every call, so mtime is not the signal).
#
# Contracts relied on from other writers: docker and the atlas account in the docker group (Phase 1 step 6);
# $ATLAS_ETC/orchestrator.env written by phase2/02-orchestrator.sh with ensure_kv (foreign keys kept) and read by the
# atlas-orchestrator unit at start; the `atlas` group (CONVENTIONS §2). Nothing cloud: the only outbound request is the
# base-image pull through the allowlist proxy (rule §7.1).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

SANDBOX_IMAGE="atlas-sandbox:py3.12"                 # services-tools.md S9 name
SANDBOX_IMAGE_VERSION="4"                            # label org.atlas.sandbox.version in docker/sandbox/Dockerfile (10-gate.sh and v17 check the same)
SANDBOX_CTX="$ATLAS_DAY1_DIR/docker/sandbox"
SANDBOX_DIR="$ATLAS_SRV/sandbox"
SANDBOX_ENV_CHANGED=0

_sandbox_hash() { [[ -e "$1" ]] && sha256sum "$1" | cut -c1-64 || echo none; }

_sandbox_build() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6); the sandbox image cannot be built"
  docker info >/dev/null 2>&1 || die "the docker daemon does not answer (systemctl status docker)"
  [[ -f "$SANDBOX_CTX/Dockerfile" ]] || die "$SANDBOX_CTX/Dockerfile is missing"
  local pin have_ver have_base
  # The base image pin (rule §7.9): `FROM python:3.12-slim@sha256:<digest>`. A Dockerfile that lost its pin stops here.
  pin="$(sed -nE 's/^FROM[[:space:]]+python:3\.12-slim@(sha256:[0-9a-f]{64}).*/\1/p' "$SANDBOX_CTX/Dockerfile" | head -n1)"
  [[ -n "$pin" ]] || die "$SANDBOX_CTX/Dockerfile carries no digest pin on python:3.12-slim (rule §7.9; README-contracts.md 'Unpinned')"
  grep -qE "^LABEL[[:space:]]+org\.atlas\.sandbox\.version=\"$SANDBOX_IMAGE_VERSION\"" "$SANDBOX_CTX/Dockerfile" \
    || die "$SANDBOX_CTX/Dockerfile labels a different org.atlas.sandbox.version than this step expects ($SANDBOX_IMAGE_VERSION); keep the Dockerfile, 06d-sandbox.sh, 10-gate.sh and verify/v17-sandbox.sh on one value"
  have_ver="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.version"}}' "$SANDBOX_IMAGE" 2>/dev/null || true)"
  if [[ "$have_ver" == "$SANDBOX_IMAGE_VERSION" ]]; then
    log "$SANDBOX_IMAGE already built (label version $SANDBOX_IMAGE_VERSION); build skipped (rule §7.3)"
  else
    [[ -z "$have_ver" ]] || log "$SANDBOX_IMAGE carries label version '$have_ver'; rebuilding for version $SANDBOX_IMAGE_VERSION"
    log "docker build $SANDBOX_IMAGE from $SANDBOX_CTX (base python:3.12-slim@$pin through the daemon's proxy; registry-1.docker.io / auth.docker.io / production.cloudflare.docker.com must be allowlisted)"
    # The base image pull uses the daemon's proxy (daemon.json, Phase 1 step 6); the build itself needs no network.
    docker build --network none -t "$SANDBOX_IMAGE" "$SANDBOX_CTX" \
      || die "docker build of $SANDBOX_IMAGE failed (pull of the pinned python:3.12-slim digest through the proxy? see the output above; if Docker Hub no longer serves that digest, re-pin per the Dockerfile header and README-contracts.md 'Unpinned')"
    have_ver="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.version"}}' "$SANDBOX_IMAGE" 2>/dev/null || true)"
    [[ "$have_ver" == "$SANDBOX_IMAGE_VERSION" ]] || die "$SANDBOX_IMAGE was built but carries label version '${have_ver:-none}', expected $SANDBOX_IMAGE_VERSION"
  fi
  # Pin evidence from the built image's own label (header item 1).
  have_base="$(docker image inspect -f '{{index .Config.Labels "org.atlas.sandbox.base"}}' "$SANDBOX_IMAGE" 2>/dev/null || true)"
  [[ "$have_base" == *"$pin"* ]] || die "$SANDBOX_IMAGE label org.atlas.sandbox.base is '${have_base:-none}', not the Dockerfile pin $pin: the image on the node was not built from this Dockerfile; rebuild: docker rmi $SANDBOX_IMAGE; sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06d"
  if docker image inspect "python:3.12-slim@$pin" >/dev/null 2>&1; then
    log "sandbox base python:3.12-slim@$pin is also listed in the daemon's image store"
  else
    log "the daemon does not list python:3.12-slim@$pin as a tagged image (normal under BuildKit: the base lives in the build cache); the pin evidence is the image label"
  fi
  log "sandbox image $SANDBOX_IMAGE: label version $have_ver, base $have_base"
}

_sandbox_smoke() {
  local atlas_gid out
  atlas_gid="$(getent group atlas | cut -d: -f3)"
  [[ -n "$atlas_gid" ]] || die "group 'atlas' does not exist (Phase 1 step 6 creates the service account)"
  if ! out="$(docker run --rm --init --pull never --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
               --user "65534:$atlas_gid" "$SANDBOX_IMAGE" python3 -c 'import sys; print(sys.version.split()[0])' 2>&1)"; then
    die "docker run $SANDBOX_IMAGE python3 failed under the package's run line: $out"
  fi
  [[ "$out" =~ ^3\.12\. ]] || die "the sandbox image runs python '$out', expected a 3.12.x interpreter (python:3.12-slim)"
  log "sandbox image $SANDBOX_IMAGE runs python $out as 65534:$atlas_gid (nobody:atlas), read-only, no network, under the in-container timeout entrypoint"
}

_sandbox_env() {
  # Contract for the orchestrator (README-contracts.md "Sandbox"): the run line it must use, as KEY=VALUE settings.
  local orch="$ATLAS_ETC/orchestrator.env" before
  [[ -e "$orch" ]] || die "$orch missing: Phase 2 step 2 has not run (it writes orchestrator.env)"
  ensure_dir "$SANDBOX_DIR" atlas:atlas 750
  before="$(_sandbox_hash "$orch")"
  ensure_kv "$orch" SANDBOX_IMAGE "$SANDBOX_IMAGE"
  ensure_kv "$orch" SANDBOX_DIR "$SANDBOX_DIR"
  ensure_kv "$orch" SANDBOX_MEMORY 2g
  ensure_kv "$orch" SANDBOX_CPUS 2
  ensure_kv "$orch" SANDBOX_PIDS 256
  ensure_kv "$orch" SANDBOX_TMPFS_SIZE 512m
  ensure_kv "$orch" SANDBOX_TIMEOUT_S 300
  ensure_kv "$orch" SANDBOX_FSIZE 1073741824          # --ulimit fsize (bytes): 1 GiB per file a job writes
  [[ "$(_sandbox_hash "$orch")" == "$before" ]] || SANDBOX_ENV_CHANGED=1
  log "SANDBOX_* keys in $orch (changed=$SANDBOX_ENV_CHANGED); job directory $SANDBOX_DIR (atlas:atlas 750)"
}

_sandbox_orchestrator_refresh() {
  # The service reads SANDBOX_* from orchestrator.env at start; a running orchestrator needs one restart to see a change.
  systemctl is-active --quiet atlas-orchestrator || { log "atlas-orchestrator not running; nothing to restart"; return 0; }
  if [[ "$SANDBOX_ENV_CHANGED" != 1 ]]; then
    log "orchestrator.env unchanged; atlas-orchestrator not restarted"
    return 0
  fi
  local port="${ORCH_PORT:-8800}"
  log "orchestrator.env changed (SANDBOX_* keys); restarting atlas-orchestrator once"
  systemctl restart atlas-orchestrator || die "systemctl restart atlas-orchestrator failed (journalctl -u atlas-orchestrator)"
  wait_http "http://127.0.0.1:$port/health" 180 || die "the orchestrator did not answer 200 on /health within 180 s after the restart (journalctl -u atlas-orchestrator)"
  log "atlas-orchestrator restarted with the SANDBOX_* settings; /health 200"
}

step_06d() {
  _sandbox_build
  _sandbox_smoke
  _sandbox_env
  _sandbox_orchestrator_refresh
  log "step 06d done: $SANDBOX_IMAGE (label version $SANDBOX_IMAGE_VERSION), $SANDBOX_DIR, SANDBOX_* in $ATLAS_ETC/orchestrator.env; V17 proves the caps at the gate"
  notify "Phase 2 step 6d done: AEGIS sandbox image built and configured"
}
