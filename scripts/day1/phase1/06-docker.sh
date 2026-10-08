#!/usr/bin/env bash
# phase1/06-docker.sh — Phase 1 step 6 (Sections 3.4, 3.6, 17; Appendix B "Containers needing the GPU"): Docker from
# Docker's own apt repository (resolute channel VERIFIED; archive docker.io as the loud fallback), the allowlist
# proxy in daemon.json, container egress enforced in DOCKER-USER (adjudicated conflict 4; the chain's baseline
# already exists from ufw's after.rules, step 4), squid's second listener on the docker0 gateway, the atlas account
# in docker, render and video, a compose-friendly env file, and a GPU device-node passthrough test run AS THE
# SERVICE USER. ROCm is never installed on the host. Facts from the platform research item 7. Defines step_06 only.
#
# CONTAINER DNS: none (fix round, Section 12.5). Containers reach squid at the docker0 gateway and send the hostname in
# CONNECT/absolute-URI form (apt, pip, curl, huggingface_hub and dockerd all do), so nothing behind the proxy needs to
# resolve anything; Docker's embedded 127.0.0.11 still resolves compose service names locally. daemon.json therefore
# sets no "dns" and DOCKER-USER opens no port 53 for any bridge: a container's query to any resolver is dropped and
# logged ("[ATLAS docker egress denied]"), which is the intended fate of a DNS exfiltration attempt.
#
# DOCKER GROUP = HOST ROOT (open risk, not a recorded decision): CONVENTIONS §2 puts the atlas service account in the
# docker group so it can run compose and the AEGIS sandbox. Any docker-group member is root-equivalent on the host
# (`docker run -v /:/host --privileged`), so the sudoers fragment that "allows exactly those systemctl commands" (§8)
# and Section 16.3 item 6 (never modify its own code, configuration or the allowlist) are NOT enforceable against the
# account that runs the orchestrator: a prompt-injected task that reaches a shell as atlas has host root. Neither
# Section 16.3 nor D1-D14 records the Principal accepting this; it belongs in Section 20 as a new R-item (proposed
# R22, wording below in _docker_group_warning; the docs file and CONVENTIONS §2's service-account line are other
# writers' files, so this step also WARNS the same sentence at run time, into the Phase 1 log and gate output, until
# the Principal has read it there). Proposed mitigations (either is a Phase 2 contract change, not made here): a
# restricted socket (tecnativa/docker-socket-proxy with CONTAINERS=1 POST=1 IMAGES=1 NETWORKS=1 VOLUMES=0 EXEC=0
# PRIVILEGED=0 and DOCKER_HOST pointed at it) or root-owned systemd units for every compose lifecycle that atlas may
# only `systemctl start` through the sudoers fragment.
#
# SANDBOX CAPS (Section 16.4): daemon.json sets "no-new-privileges": true for every container the daemon starts (a
# contract other writers' compose files inherit; recorded in /etc/atlas/docker.env as DOCKER_NO_NEW_PRIVILEGES). The
# AEGIS sandbox image (docker/sandbox) must in addition run with --network none, --memory, --cpus, --pids-limit,
# --cap-drop ALL and a `timeout`, exactly as the three test containers below do (fix round 3: all three now carry
# --cpus and --cap-drop ALL; apt inside them runs with APT::Sandbox::User=root because apt's own privilege drop needs
# CAP_SETUID/SETGID). The full line is published once as SANDBOX_RUN_FLAGS in docker.env for docker/sandbox and the
# orchestrator to consume; 16.4 names no numbers, so the memory/cpu/pids figures there are this step's defaults
# (UNVERIFIED by the baseline) and the Phase 2 writer may tighten them. No daemon-wide nproc ulimit: RLIMIT_NPROC
# counts every process of a uid host-wide (container root = host root without userns), so a global cap could starve
# the host; --pids-limit per container is the right tool and is applied per container.
#
# CONTAINER TELEMETRY (fix round 3): the telemetry-off keys step 4 installs for the host do not reach containers
# (/root/.docker/config.json injects the proxy variables only), and Open WebUI, docling-serve, speaches, kokoro and the
# Phase 4 ROCm images all import huggingface_hub, which posts to huggingface.co/api/telemetry (an allowlisted host, so
# squid lets it through). docker.env therefore carries CONTAINER_HF_HUB_DISABLE_TELEMETRY and friends, and EVERY compose
# service MUST interpolate them into its `environment:` (contract; the second test container proves the mechanism).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

_docker_install() {
  if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    log "docker already installed: $(docker --version) / $(docker compose version --short)"
    return 0
  fi
  proxy_env
  export DEBIAN_FRONTEND=noninteractive
  apt_install ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  local ok=1 keyf=/etc/apt/keyrings/docker.asc
  if [[ ! -s "$keyf" ]]; then
    if curl -fsSL --max-time 60 https://download.docker.com/linux/ubuntu/gpg -o "$keyf.tmp"; then
      mv "$keyf.tmp" "$keyf"; chmod a+r "$keyf"
    else
      rm -f "$keyf.tmp"; ok=0
    fi
  fi
  if (( ok )); then
    # Fingerprint pinned (fix round 3): 9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88 as published on
    # docs.docker.com/engine/install/ubuntu; VERIFIED live during the fix round against the key file served by
    # download.docker.com. A mismatch is a stop (tampered download or a rotated key), never a fallback to the archive.
    command -v gpg >/dev/null || apt_install gnupg
    local docker_fpr=9DC858229FC7DD38854AE2D88D81803C0EBFCD88 got
    got="$(gpg --show-keys --with-fingerprint --with-colons "$keyf" 2>/dev/null | awk -F: '$1=="fpr" {print $10}' | tr '\n' ' ')"
    grep -qw "$docker_fpr" <<<"$got" \
      || die "Docker's apt signing key does not carry the published fingerprint $docker_fpr (got: ${got:-none}); refusing to add the repository (rm $keyf and re-run after checking https://docs.docker.com/engine/install/ubuntu/)"
    local codename; codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
    cat >/etc/apt/sources.list.d/docker.sources <<SRC
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $codename
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
SRC
    _ATLAS_APT_UPDATED=0
    apt_wait_idle
    if retry 3 apt-get -q update \
       && retry 2 apt-get install -y -q -o Dpkg::Options::=--force-confold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
      log "Docker CE installed from download.docker.com ($codename/stable)"
      return 0
    fi
    ok=0
  fi
  if (( ! ok )); then
    warn "Docker's repository is unreachable or failed; falling back to the Ubuntu archive (docker.io 29.x, docker-compose-v2)"
    rm -f /etc/apt/sources.list.d/docker.sources
    _ATLAS_APT_UPDATED=0
    apt_install docker.io docker-compose-v2
  fi
}

# _docker_group_warning — the Section 20 R-item this step proposes (R22), warned at run time so it reaches the Phase 1
# log and the gate output until docs/ATLAS_FRAMEWORK_REVIEW.md and CONVENTIONS §2 carry it (other writers' files).
_docker_group_warning() {
  warn "R22 (proposed, Section 20): the atlas service account is in the docker group (CONVENTIONS §2), which is root-equivalent on the host (docker run -v /:/host --privileged); the sudoers fragment /etc/sudoers.d/atlas-engines and Section 16.3 item 6 are therefore NOT enforceable against the orchestrator account. Mitigations for the Principal to choose in Phase 2: a restricted socket (tecnativa/docker-socket-proxy, EXEC=0 PRIVILEGED=0 VOLUMES=0, DOCKER_HOST pointed at it) or root-owned systemd units for every compose lifecycle reached through the sudoers fragment."
}

step_06() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP is empty"
  [[ -c /dev/kfd ]] || die "/dev/kfd is absent: the amdgpu KFD interface is required for the Phase 4 containers (Section 3.4)"
  declare -F phase1_write_file >/dev/null || die "phase1_write_file is not defined: step 6 must run under phase1-platform.sh (04-system.sh defines it)"
  declare -F _squid_render >/dev/null || die "_squid_render is not defined: step 6 must run under phase1-platform.sh (04-system.sh defines it)"
  _docker_install

  # Container egress rules in DOCKER-USER: installed BEFORE dockerd is (re)started, as an ExecStartPre of
  # docker.service (so restart:unless-stopped containers never start ahead of the rules) and as the oneshot unit
  # that re-applies them whenever docker restarts. ufw's after.rules (step 4) already carries the baseline DROPs.
  install -m 755 "$ATLAS_DAY1_DIR/phase1/docker-egress-rules.sh" /usr/local/sbin/atlas-docker-egress
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-docker-egress.service" /etc/systemd/system/atlas-docker-egress.service
  install -d -m 755 /etc/systemd/system/docker.service.d
  printf '# ATLAS Phase 1 step 6: DOCKER-USER egress rules exist before any container starts (Section 12.5).\n[Service]\nExecStartPre=/usr/local/sbin/atlas-docker-egress\n' \
    >/etc/systemd/system/docker.service.d/atlas-egress.conf

  # Daemon: proxy for image pulls, bounded logs, no-new-privileges for every container (header), NO "dns" key
  # (header: containers resolve nothing external by design). VERIFIED daemon.json "proxies" and "no-new-privileges".
  install -d -m 755 /etc/docker
  cat >/etc/docker/daemon.json <<JSON
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "5" },
  "no-new-privileges": true,
  "proxies": {
    "http-proxy":  "$ATLAS_PROXY_URL",
    "https-proxy": "$ATLAS_PROXY_URL",
    "no-proxy":    "$ATLAS_NO_PROXY"
  }
}
JSON
  systemctl daemon-reload
  systemctl enable docker >/dev/null
  systemctl restart docker || die "docker.service failed to start: $(journalctl -u docker -n 10 --no-pager)"
  local info; info="$(docker info --format '{{.ServerVersion}} proxy={{.HTTPProxy}}' 2>/dev/null || true)"
  log "docker: $info (no container dns: Section 12.5, header)"
  grep -q '3128' <<<"$info" || die "dockerd did not pick up the proxy from daemon.json ($info)"
  systemctl enable --now atlas-docker-egress.service >/dev/null || die "atlas-docker-egress.service failed: $(journalctl -u atlas-docker-egress -n 10 --no-pager)"
  iptables -w -S DOCKER-USER | grep -q -- '-j DROP' || die "DOCKER-USER carries no DROP rule after atlas-docker-egress ran"
  log "DOCKER-USER egress rules active: $(iptables -w -S DOCKER-USER | wc -l) rules"

  # Service account groups (Section 3.6, CONVENTIONS §2): docker for compose, render+video for the GPU device nodes.
  # See the header: docker-group membership makes this account root-equivalent on the host (open risk, proposed R22).
  usermod -aG docker,render,video atlas
  _docker_group_warning
  local render_gid video_gid atlas_uid atlas_gid atlas_home
  render_gid="$(getent group render | cut -d: -f3)"; video_gid="$(getent group video | cut -d: -f3)"
  atlas_uid="$(id -u atlas)"; atlas_gid="$(id -g atlas)"
  atlas_home="$(getent passwd atlas | cut -d: -f6)"
  [[ -d "$atlas_home" ]] || die "the atlas account's home $atlas_home does not exist (step 3 creates /var/lib/atlas/home)"

  # The docker0 gateway is where containers reach squid (compose bridges too: it is a local address of the host).
  local gw; gw="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 172.17.0.1)"
  [[ "$gw" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || gw=172.17.0.1

  # Compose-friendly env (CONTRACT for Phase 2+ compose files: `docker compose --env-file /etc/atlas/docker.env`).
  # NTFY_DATA_DIR holds ntfy's config and message cache (backed up); NTFY_AUTH_DIR holds user.db (password hashes and
  # the node token, the same secret as secrets/ntfy.env) under /etc/atlas/secrets/ntfy (atlas:atlas 700; fix round 3,
  # CONVENTIONS §7.2 "secrets live only under /etc/atlas/secrets/"); WG_DATA_DIR holds the WireGuard server and peer
  # private keys under /etc/atlas/secrets/wg-easy. Neither is under /srv/atlas or in restic's include set. The
  # CONTAINER_* telemetry keys and SANDBOX_RUN_FLAGS are contracts described in the header. Written BEFORE the squid
  # re-render because _squid_render reads DOCKER_GW from this file.
  {
    echo "# Written by ATLAS Phase 1 step 6; sourceable and usable as a compose --env-file. Not a secret (no tokens)."
    echo "LAN_IP=$LAN_IP"
    echo "LAN_CIDR=$LAN_CIDR"
    echo "LAN_IFACE=$LAN_IFACE"
    echo "TZ=$TZ"
    echo "ATLAS_UID=$atlas_uid"
    echo "ATLAS_GID=$atlas_gid"
    echo "RENDER_GID=$render_gid"
    echo "VIDEO_GID=$video_gid"
    echo "DOCKER_GW=$gw"
    echo "CONTAINER_HTTP_PROXY=http://$gw:3128"
    echo "CONTAINER_HTTPS_PROXY=http://$gw:3128"
    echo "CONTAINER_NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    echo "# daemon.json sets no-new-privileges for EVERY container (Section 16.4); containers have NO external DNS (Section 12.5)."
    echo "DOCKER_NO_NEW_PRIVILEGES=true"
    echo "CONTAINER_DNS=none"
    echo "WG_BRIDGE=$ATLAS_WG_BRIDGE"
    echo "WG_BRIDGE_NET=$ATLAS_WG_BRIDGE_NET"
    echo "WG_BRIDGE_GW=$ATLAS_WG_BRIDGE_GW"
    echo "NTFY_DATA_DIR=$ATLAS_SRV/data/ntfy"
    echo "NTFY_AUTH_DIR=$ATLAS_ETC/secrets/ntfy"
    echo "WG_DATA_DIR=$ATLAS_ETC/secrets/wg-easy"
    echo "# Telemetry off INSIDE containers (rule §7.1; huggingface_hub posts to huggingface.co/api/telemetry otherwise)."
    echo "# CONTRACT: every compose service interpolates these into its environment:, e.g."
    echo "#   - HF_HUB_DISABLE_TELEMETRY=\${CONTAINER_HF_HUB_DISABLE_TELEMETRY}   (one line per key below)"
    echo "CONTAINER_HF_HUB_DISABLE_TELEMETRY=1"
    echo "CONTAINER_HF_HUB_DISABLE_IMPLICIT_TOKEN=1"
    echo "CONTAINER_HF_HUB_ENABLE_HF_TRANSFER=0"
    echo "CONTAINER_DO_NOT_TRACK=1"
    echo "CONTAINER_PIP_DISABLE_PIP_VERSION_CHECK=1"
    echo "# AEGIS sandbox run line (Section 16.4: hard memory limit, CPU quota, no network, a timeout; 16.4 names no"
    echo "# numbers, so these are Phase 1 defaults the sandbox writer may tighten). Consumed by docker/sandbox and the"
    echo "# orchestrator; the caller adds the \`timeout\` and, when the task's tier grants network, replaces --network none."
    echo "SANDBOX_RUN_FLAGS='--network none --memory 4g --cpus 2 --pids-limit 256 --security-opt no-new-privileges --cap-drop ALL --read-only'"
  } | phase1_write_file 644 '' "$ATLAS_ETC/docker.env"
  log "wrote $ATLAS_ETC/docker.env (LAN_IP, uids/gids, container proxy at $gw:3128)"

  # squid: add the docker0 gateway as its second (and last) listener (config/squid.conf.tmpl; CONVENTIONS §8).
  _squid_render
  systemctl reload squid || systemctl restart squid || die "squid failed to reload with the $gw:3128 listener: systemctl status squid"
  local tries=0
  until ss -ltnH "sport = :3128" | grep -q "$gw:3128"; do
    (( ++tries < 15 )) || die "squid does not listen on $gw:3128 after the reload (ss -ltn sport = :3128; net.ipv4.ip_nonlocal_bind must be 1: sysctl net.ipv4.ip_nonlocal_bind)"
    sleep 1
  done
  log "squid listening on 127.0.0.1:3128 and $gw:3128"

  # Container-side proxy for every container the atlas user starts (VERIFIED ~/.docker/config.json "proxies").
  install -d -m 700 -o atlas -g atlas "$atlas_home/.docker"
  cat >"$atlas_home/.docker/config.json" <<JSON
{ "proxies": { "default": {
    "httpProxy":  "http://$gw:3128",
    "httpsProxy": "http://$gw:3128",
    "noProxy":    "localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" } } }
JSON
  chown atlas:atlas "$atlas_home/.docker/config.json"; chmod 600 "$atlas_home/.docker/config.json"
  install -d -m 700 -o root -g root /root/.docker
  cp "$atlas_home/.docker/config.json" /root/.docker/config.json

  # GPU device-node passthrough test, exactly the way Appendix B says the Phase 4 containers run: /dev/kfd and
  # /dev/dri passed, the SERVICE USER (not root) with the render and video groups, the DEFAULT seccomp profile and
  # no added capabilities; and with the Section 16.4 caps the sandbox must carry (no network, memory, pids, no new
  # privileges, a timeout). Phase 4 containers must keep the default seccomp profile and run with --cap-drop ALL,
  # adding only what a measured failure proves necessary. ubuntu:26.04 tag VERIFIED (services research 4.8).
  log "pulling ubuntu:26.04 through the proxy for the passthrough test"
  retry 3 docker pull -q ubuntu:26.04 >/dev/null || die "docker pull ubuntu:26.04 failed (registry-1.docker.io, auth.docker.io and the blob CDN production.cloudfront.docker.com must be allowlisted; see /var/log/squid/access.log for the TCP_DENIED host)"
  local out
  out="$(timeout -s KILL 60 docker run --rm --network none --memory 256m --cpus 2 --pids-limit 64 --security-opt no-new-privileges \
           --device /dev/kfd --device /dev/dri --cap-drop ALL \
           --user "$atlas_uid:$atlas_gid" --group-add "$render_gid" --group-add "$video_gid" \
           ubuntu:26.04 sh -c 'ls /dev/kfd /dev/dri/renderD* && id -G' 2>&1)" \
    || die "GPU device passthrough test (as uid $atlas_uid, groups render/video) failed: $out"
  if ! grep -q '/dev/kfd' <<<"$out" || ! grep -q 'renderD' <<<"$out"; then
    die "container did not see /dev/kfd and /dev/dri/renderD*: $out"
  fi
  grep -qw "$render_gid" <<<"$out" || die "the container process is not in the render group ($render_gid): $out"
  log "GPU passthrough test as atlas: $(tr '\n' ' ' <<<"$out")"
  # Containers must reach the proxy and nothing else: prove both from inside a container with apt (ubuntu:26.04
  # ships no curl; apt honours http_proxy/https_proxy, sends the hostname to squid, needs no DNS, and
  # .archive.ubuntu.com is allowlisted). Same cap set as the sandbox (--cpus, --cap-drop ALL): apt's own privilege drop
  # to _apt needs CAP_SETUID/SETGID, so APT::Sandbox::User=root keeps it from failing for a reason unrelated to egress.
  # The same run proves the docker.env telemetry contract: an -e key must be visible to the process (exit 42 if not).
  local rc=0
  timeout -s KILL 120 docker run --rm --memory 512m --cpus 2 --pids-limit 128 --security-opt no-new-privileges --cap-drop ALL \
       -e "http_proxy=http://$gw:3128" -e "https_proxy=http://$gw:3128" -e HF_HUB_DISABLE_TELEMETRY=1 ubuntu:26.04 \
       sh -c 'env | grep -qx HF_HUB_DISABLE_TELEMETRY=1 || exit 42; exec apt-get -qq -o APT::Sandbox::User=root -o Acquire::http::Timeout=20 -o Acquire::Retries=1 update' >/dev/null 2>&1 || rc=$?
  if (( rc == 0 )); then
    log "container -> proxy ($gw:3128) -> archive.ubuntu.com: ok (with --cpus 2 --cap-drop ALL; -e HF_HUB_DISABLE_TELEMETRY=1 visible inside)"
  elif (( rc == 42 )); then
    die "an environment key passed with -e was not visible inside the container; the docker.env CONTAINER_* telemetry contract cannot work on this daemon"
  else
    die "a container could not reach archive.ubuntu.com through the proxy at $gw:3128 (rc $rc). Check 'ss -ltn sport = :3128' (squid must listen on $gw), 'ufw status' for the docker0/172.16.0.0/12 port 3128 rules, /var/log/squid/access.log, and 'iptables -S DOCKER-USER'. (Containers have no DNS by design: that is not the fault here, apt sends the name to squid.)"
  fi
  # /root/.docker/config.json injects the proxy into every container root starts, so the negative test blanks it. The
  # flags are identical to the positive test above, so a failure here can only be the egress block.
  docker rm -f atlas-egress-test >/dev/null 2>&1 || true
  if timeout -s KILL 90 docker run --rm --name atlas-egress-test --memory 512m --cpus 2 --pids-limit 128 --security-opt no-new-privileges --cap-drop ALL \
       -e http_proxy= -e https_proxy= -e HTTP_PROXY= -e HTTPS_PROXY= ubuntu:26.04 \
       apt-get -qq -o APT::Sandbox::User=root -o Acquire::http::Timeout=8 -o Acquire::Retries=0 update >/dev/null 2>&1; then
    docker rm -f atlas-egress-test >/dev/null 2>&1 || true
    die "a container reached the internet WITHOUT the proxy: the DOCKER-USER egress rules are not enforcing (iptables -S DOCKER-USER)"
  fi
  docker rm -f atlas-egress-test >/dev/null 2>&1 || true
  log "container direct egress: blocked (DOCKER-USER), as required by Section 12.5"
}
