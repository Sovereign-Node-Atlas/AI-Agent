# docker/sandbox/Dockerfile — the AEGIS sandbox image (Section 16.4): "a container with a hard memory limit, CPU
# quota, no network unless the task's tier grants it, and a timeout". Built by phase2/06d-sandbox.sh (fix round 4: a
# step of its own before step 7, so the gate only judges; it also writes the SANDBOX_* keys) as atlas-sandbox:py3.12;
# proven by verify/v17-sandbox.sh at the gate (a memory bomb dies with exit 137 under --memory=512m and the host does not
# notice; a SIGTERM-ignoring script is still gone when the in-container timeout expires). The orchestrator's run line
# (SANDBOX_* keys in /etc/atlas/orchestrator.env, README-contracts.md "Sandbox"):
#
#   timeout -k 5 $((SANDBOX_TIMEOUT_S + 10)) docker run --rm --init --pull never --name sb-<job> \
#     --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \
#     --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=$SANDBOX_TMPFS_SIZE \
#     --mount type=tmpfs,dst=/work,tmpfs-size=$SANDBOX_WORK_SIZE \
#     --ulimit fsize=$SANDBOX_FSIZE --cap-drop ALL --security-opt no-new-privileges --user 65534:<atlas gid> \
#     -e SANDBOX_TIMEOUT_S=$SANDBOX_TIMEOUT_S \
#     -v $SANDBOX_DIR/<job>:/stage:ro -w /work atlas-sandbox:py3.12 \
#     sh -c 'cp -R /stage/. /work/ && "$@" >/work/.stdout; rc=$?; \
#            cd /work && tar -cf - .; exit $rc' sh python3 /work/main.py   > $SANDBOX_DIR/<job>/.results.tar
#   docker rm -f sb-<job>   (always, afterwards: the client may have been killed while the container lived on)
#
# This is the line orchestrator/src/atlas/sandbox.py build_argv() produces (fix round 2: the three copies — this header,
# README-contracts.md §1 and the package — now agree). `--pull never`: an unqualified name that is not present locally
# would otherwise be resolved to docker.io/library/atlas-sandbox:py3.12 and pulled. `--user 65534:<atlas gid>`: the job
# directory is created setgid group atlas (0o2770) and staged files 0o640, so the nobody-uid process reads /stage
# through the group bit and nothing else of atlas's.
# WORK DIRECTORY (fix round 5; Section 16.4 "a hard memory limit"): /work is a BOUNDED tmpfs, tmpfs-size =
# SANDBOX_WORK_SIZE (default 2g, SandboxConfig.work_size; 06d may write the key into orchestrator.env), charged to the
# job's memory cgroup like every other page it touches; writing past it fails with ENOSPC inside the container (V17's
# third run asserts exactly that, under THIS run line). The staged inputs are bind-mounted READ-ONLY at /stage and the
# run line's first act copies them into /work (`cp` and `tar` are coreutils/tar, essential packages present in slim),
# so nothing a job writes reaches the 8 TB volume and the earlier unbounded `-v <job>:/work:rw` is gone. The copy has
# NO `--preserve` (fix round 6): uid 65534 with every capability dropped does not own the /work mount point, and GNU cp
# copying `/stage/.` onto it tried to set the source directory's times/mode on /work itself, failed with EPERM and the
# `&&` chain never ran the job (coreutils 9.4, verified as uid 65534). RESULTS: a tmpfs is not part
# of the container's layers, so `docker cp` cannot read it (Docker lists tmpfs among the paths cp does not copy) and it
# is gone at exit; the run line instead sends the job's stdout to /work/.stdout and, once the job has returned, streams
# `tar -cf - .` of /work on the container's stdout, which the package writes to <job dir>/.results.tar (bounded by the
# tmpfs on the producer side) and unpacks there with tarfile's `data` filter; the shell exits with the job's own code,
# so 137 still means the memory cap or the deadline. /work below is the mount point (its mode is irrelevant once the
# tmpfs is mounted); /stage is created by docker for the bind mount. Nothing in the image changed for this, so the
# label version stays 4.
#
# Two timeouts, on purpose (fix round). GNU timeout on the host only signals the docker CLIENT; a sandboxed program
# that does `signal.signal(SIGTERM, SIG_IGN)` survives the proxied SIGTERM and the follow-up SIGKILL hits the client
# alone. The bound the kernel enforces is therefore INSIDE the container: the ENTRYPOINT below wraps every command in
# coreutils `timeout -s KILL $SANDBOX_TIMEOUT_S` (default 300 when the run line does not pass -e SANDBOX_TIMEOUT_S), so
# the program is SIGKILLed at the deadline whatever it ignores (exit 137, disambiguated from the OOM kill by the
# elapsed time, as the package already does). The host-side timeout is a backstop 10 s later for a hung daemon;
# --init makes docker-init PID 1 so signals and zombies are handled and the wrapper is an ordinary process.
# --ulimit fsize bounds every file a job writes (SANDBOX_FSIZE, default 1 GiB); /tmp is a bounded, noexec tmpfs, and
# /work is a bounded tmpfs too (WORK DIRECTORY above): the fix-round-4 "no per-job cap on the total size of
# $SANDBOX_DIR/<job>" item is closed by the run line, not by an XFS quota. The package still removes the job directory
# after collecting the copied-out results (keep_work_dir=False).
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
# Rule §7.1 inside the container too (fix round 4): the ENV block switches off every telemetry beacon a library the
# staged code brings could attempt once a tier grants network (huggingface_hub, chromadb/posthog, pip), because
# huggingface.co is on the allowlist and the egress rules would let such a beacon through; zero cost with no network.
#
# Base image pin (rule §7.9; fix round 2). The research names no python image tag or digest, so the pin is this file's
# writer's, not a research pin: the multi-arch index Docker Hub served for python:3.12-slim on 2026-10-04 (GET
# registry-1.docker.io/v2/library/python/manifests/3.12-slim, Accept: OCI index). VERIFIED 2026-10-04 (fix round 4,
# by this writer and independently by the reviewer): HEAD registry-1.docker.io/v2/library/python/manifests/<digest>
# answers 200 and the `3.12-slim` tag's Docker-Content-Digest is exactly this digest. A digest pin is strictly safer than
# the floating tag (the image the caps were proven on is the image that runs), and a build from a digest Docker Hub no
# longer serves fails loudly in phase2/06d-sandbox.sh (the step dies), never silently floats. To re-pin: `docker pull
# python:3.12-slim && docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'` on the node, paste the
# digest here AND in the org.atlas.sandbox.base label, bump org.atlas.sandbox.version (06d, the gate and v17-sandbox.sh
# check it) and the "Unpinned" section of README-contracts.md. 06d skips the build once the labelled image exists
# (rule §7.3). Label version 4 = digest-pinned base + in-container timeout entrypoint + the telemetry opt-outs.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

LABEL org.atlas.sandbox.version="4" \
      org.atlas.sandbox.section="16.4" \
      org.atlas.sandbox.base="python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PYTHONNOUSERSITE=1 \
    PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_OFFLINE=1 \
    DO_NOT_TRACK=1 \
    ANONYMIZED_TELEMETRY=False \
    CHROMA_TELEMETRY_ENABLED=false \
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
