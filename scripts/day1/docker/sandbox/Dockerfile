# docker/sandbox/Dockerfile — the AEGIS sandbox image (Section 16.4): "a container with a hard memory limit, CPU
# quota, no network unless the task's tier grants it, and a timeout". Built by phase2/10-gate.sh as
# atlas-sandbox:py3.12; proven by verify/v17-sandbox.sh (a memory bomb dies with exit 137 under --memory=512m and the
# host does not notice; a SIGTERM-ignoring script is still gone when the in-container timeout expires). The
# orchestrator's run line (SANDBOX_* keys in /etc/atlas/orchestrator.env, README-contracts.md "Sandbox"; the gate owns
# the build only because no Section 17 step does, recorded in README-contracts.md §3):
#
#   timeout -k 5 $((SANDBOX_TIMEOUT_S + 10)) docker run --rm --init --pull never --name sb-<job> \
#     --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \
#     --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=$SANDBOX_TMPFS_SIZE \
#     --ulimit fsize=$SANDBOX_FSIZE --cap-drop ALL --security-opt no-new-privileges --user 65534:<atlas gid> \
#     -e SANDBOX_TIMEOUT_S=$SANDBOX_TIMEOUT_S \
#     -v $SANDBOX_DIR/<job>:/work:rw -w /work atlas-sandbox:py3.12 python3 /work/main.py
#   docker rm -f sb-<job>   (always, afterwards: the client may have been killed while the container lived on)
#
# This is the line orchestrator/src/atlas/sandbox.py build_argv() produces (fix round 2: the three copies — this header,
# README-contracts.md §1 and the package — now agree). `--pull never`: an unqualified name that is not present locally
# would otherwise be resolved to docker.io/library/atlas-sandbox:py3.12 and pulled. `--user 65534:<atlas gid>`: the job
# directory is created setgid group atlas (0o2770) and staged files 0o640, so the nobody-uid process reads and writes
# /work through the group bit and nothing else of atlas's.
#
# Two timeouts, on purpose (fix round). GNU timeout on the host only signals the docker CLIENT; a sandboxed program
# that does `signal.signal(SIGTERM, SIG_IGN)` survives the proxied SIGTERM and the follow-up SIGKILL hits the client
# alone. The bound the kernel enforces is therefore INSIDE the container: the ENTRYPOINT below wraps every command in
# coreutils `timeout -s KILL $SANDBOX_TIMEOUT_S` (default 300 when the run line does not pass -e SANDBOX_TIMEOUT_S), so
# the program is SIGKILLed at the deadline whatever it ignores (exit 137, disambiguated from the OOM kill by the
# elapsed time, as the package already does). The host-side timeout is a backstop 10 s later for a hung daemon;
# --init makes docker-init PID 1 so signals and zombies are handled and the wrapper is an ordinary process.
# --ulimit fsize bounds every file a job writes (SANDBOX_FSIZE, default 1 GiB); /tmp is a bounded, noexec tmpfs.
# UNVERIFIED / OPEN: no per-job cap on the TOTAL size of $SANDBOX_DIR/<job> exists here, and the bind mount carries no
# nosuid/nodev of its own, so a job may fill the 8 TB volume with many 1 GiB files. Two durable fixes, both a decision
# outside this file because they change the package's staging (README-contracts.md §3): an XFS project quota on
# $SANDBOX_DIR, or a bounded `--mount type=tmpfs,dst=/work,tmpfs-size=$SANDBOX_WORK_SIZE` charged to the job's memory
# cgroup with `docker cp` in and out instead of the bind mount. Until then the package removes the job directory after
# collecting outputs.
#
# DOCKER SOCKET == HOST ROOT (recorded, fix round; the same decision is in systemd/atlas-orchestrator.service and
# phase1/06-docker.sh): the account that runs this line, `atlas`, is in the docker group (CONVENTIONS.md §2), and a
# docker-group member can `docker run --privileged -v /:/host`. The caps on this run line bound the SANDBOXED JOB;
# they do not bound the orchestrator that launches it. A prompt-injected orchestrator reaching a shell has host root
# through the socket, and then Section 16.3 rules 5, 6 and 8 rest on the model's behaviour, not on this image. The
# recorded remedy (a root-owned `atlas-sandbox-run <job-id>` under its own sudoers line and `atlas` out of the docker
# group, or a rootless/proxied socket) changes the package's run line, the units and phase1/06-docker.sh and is the
# Principal's decision, not made here.
#
# Minimal on purpose: python:3.12-slim ships neither curl nor wget; pip is removed so nothing inside can fetch a
# package even when a tier grants network; the process runs as nobody on a read-only root. Every cap that matters
# (memory, cpu, pids, network, timeout) is enforced by the kernel through the docker run flags (services-tools.md §6,
# VERIFIED flags) and the in-container timeout, not by anything else in this image. Arthur's code review is a second
# layer over this, not the guard. A tier that grants network replaces `--network none`; the container is then still
# confined to the allowlist proxy by the DOCKER-USER rules of atlas-docker-egress.service (README-contracts.md).
#
# Base image pin (rule §7.9; fix round 2). UNVERIFIED: the research names no python image tag or digest, so the digest
# below is the multi-arch index that Docker Hub served for python:3.12-slim on 2026-10-04, resolved by this file's
# writer (GET registry-1.docker.io/v2/library/python/manifests/3.12-slim, Accept: OCI index), not a research pin. A
# digest pin is strictly safer than the floating tag (the image the caps were proven on is the image that runs), and a
# build from a digest Docker Hub no longer serves fails loudly in phase2/10-gate.sh (V17 fail), never silently floats.
# To re-pin: `docker pull python:3.12-slim && docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'`
# on the node, paste the digest here, bump org.atlas.sandbox.version (the gate and v17-sandbox.sh check it) and the
# "Unpinned" section of README-contracts.md. The gate skips the build once the labelled image exists (rule §7.3).
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

LABEL org.atlas.sandbox.version="3" \
      org.atlas.sandbox.section="16.4" \
      org.atlas.sandbox.base="python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_INDEX=1 \
    HOME=/tmp \
    SANDBOX_TIMEOUT_S=300

# No package manager inside: remove pip/setuptools/wheel and the ensurepip bundle, then drop the launcher scripts.
# The entrypoint is the in-container timeout (coreutils, an essential package in Debian slim: /usr/bin/timeout).
RUN python3 -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true \
    && rm -rf /usr/local/lib/python3.12/ensurepip /usr/local/bin/pip* /usr/local/bin/wheel* /root/.cache \
    && mkdir -p /work && chown 65534:65534 /work && chmod 1777 /work \
    && test -x /usr/bin/timeout \
    && printf '%s\n' '#!/bin/sh' \
         '# atlas-sandbox entrypoint: the kernel-enforced deadline (SIGKILL) lives inside the container, Section 16.4.' \
         'case "${SANDBOX_TIMEOUT_S:-300}" in *[!0-9]*|"") echo "atlas-sandbox: SANDBOX_TIMEOUT_S must be an integer" >&2; exit 2;; esac' \
         'exec /usr/bin/timeout -s KILL "${SANDBOX_TIMEOUT_S:-300}" "$@"' \
         >/usr/local/bin/sandbox-entry \
    && chmod 755 /usr/local/bin/sandbox-entry

WORKDIR /work
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/sandbox-entry"]
CMD ["python3"]
