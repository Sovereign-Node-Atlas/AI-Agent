# docker/sandbox/Dockerfile — the AEGIS sandbox image (Section 16.4): "a container with a hard memory limit, CPU
# quota, no network unless the task's tier grants it, and a timeout". Built by phase2/10-gate.sh as
# atlas-sandbox:py3.12; proven by verify/v17-sandbox.sh (a memory bomb dies with exit 137 under --memory=512m and the
# host does not notice). The orchestrator's run line (SANDBOX_* keys in /etc/atlas/orchestrator.env):
#
#   timeout -k 5 $SANDBOX_TIMEOUT_S docker run --rm --name sb-<job> \
#     --network none --memory $SANDBOX_MEMORY --memory-swap $SANDBOX_MEMORY --cpus $SANDBOX_CPUS \
#     --pids-limit $SANDBOX_PIDS --read-only --tmpfs /tmp:rw,size=$SANDBOX_TMPFS_SIZE \
#     --cap-drop ALL --security-opt no-new-privileges --user 65534:65534 \
#     -v $SANDBOX_DIR/<job>:/work:rw -w /work atlas-sandbox:py3.12 python3 /work/main.py
#
# Minimal on purpose: python:3.12-slim ships neither curl nor wget; pip is removed so nothing inside can fetch a
# package even when a tier grants network; the process runs as nobody on a read-only root. Every cap that matters
# (memory, cpu, pids, network, timeout) is enforced by the kernel through the docker run flags (services-tools.md §6,
# VERIFIED flags), not by anything in this image. Arthur's code review is a second layer over this, not the guard.
#
# UNVERIFIED: no digest pin for python:3.12-slim — the research names no python image tag, so the floating minor tag
# is used and the build is skipped once the image exists (rule §7.3); re-pull deliberately with `docker build --pull`.
FROM python:3.12-slim

LABEL org.atlas.sandbox.version="1" \
      org.atlas.sandbox.section="16.4"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_INDEX=1 \
    HOME=/tmp

# No package manager inside: remove pip/setuptools/wheel and the ensurepip bundle, then drop the launcher scripts.
RUN python3 -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true \
    && rm -rf /usr/local/lib/python3.12/ensurepip /usr/local/bin/pip* /usr/local/bin/wheel* /root/.cache \
    && mkdir -p /work && chown 65534:65534 /work && chmod 1777 /work

WORKDIR /work
USER 65534:65534
CMD ["python3"]
