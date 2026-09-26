#!/usr/bin/env bash
# phase1/06-docker.sh — Phase 1 step 6 (Sections 3.4, 3.6, 17; Appendix B "Containers needing the GPU"): Docker from
# Docker's own apt repository (resolute channel VERIFIED; archive docker.io as the loud fallback), the allowlist
# proxy in daemon.json, container egress enforced in DOCKER-USER (adjudicated conflict 4), the atlas account in
# docker, render and video, a compose-friendly env file, and a GPU device-node passthrough test. ROCm is never
# installed on the host. Facts from the platform research item 7. Defines step_06 only.
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
  local ok=1
  if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL --max-time 60 https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc || ok=0
    [[ -s /etc/apt/keyrings/docker.asc ]] && chmod a+r /etc/apt/keyrings/docker.asc
  fi
  if (( ok )); then
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

step_06() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP is empty"
  [[ -c /dev/kfd ]] || die "/dev/kfd is absent: the amdgpu KFD interface is required for the Phase 4 containers (Section 3.4)"
  _docker_install

  # Daemon: proxy for image pulls, bounded logs. (VERIFIED daemon.json "proxies" keys.)
  install -d -m 755 /etc/docker
  cat >/etc/docker/daemon.json <<JSON
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "5" },
  "proxies": {
    "http-proxy":  "$ATLAS_PROXY_URL",
    "https-proxy": "$ATLAS_PROXY_URL",
    "no-proxy":    "$ATLAS_NO_PROXY"
  }
}
JSON
  systemctl enable docker >/dev/null
  systemctl restart docker || die "docker.service failed to start: $(journalctl -u docker -n 10 --no-pager)"
  local info; info="$(docker info --format '{{.ServerVersion}} proxy={{.HTTPProxy}}' 2>/dev/null || true)"
  log "docker: $info"
  grep -q '3128' <<<"$info" || die "dockerd did not pick up the proxy from daemon.json ($info)"

  # Service account groups (Section 3.6): docker for compose, render+video for the GPU device nodes.
  usermod -aG docker,render,video atlas
  local render_gid video_gid
  render_gid="$(getent group render | cut -d: -f3)"; video_gid="$(getent group video | cut -d: -f3)"

  # Container-side proxy for every container the atlas user starts (VERIFIED ~/.docker/config.json "proxies").
  # Containers reach squid at the docker0 gateway; compose bridges use their own gateway or host-gateway.
  local gw; gw="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 172.17.0.1)"
  [[ -n "$gw" ]] || gw=172.17.0.1
  install -d -m 700 -o atlas -g atlas /var/lib/atlas/.docker
  cat >/var/lib/atlas/.docker/config.json <<JSON
{ "proxies": { "default": {
    "httpProxy":  "http://$gw:3128",
    "httpsProxy": "http://$gw:3128",
    "noProxy":    "localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" } } }
JSON
  chown atlas:atlas /var/lib/atlas/.docker/config.json; chmod 600 /var/lib/atlas/.docker/config.json
  install -d -m 700 -o root -g root /root/.docker
  cp /var/lib/atlas/.docker/config.json /root/.docker/config.json

  # Compose-friendly env (CONTRACT for Phase 2+ compose files: `docker compose --env-file /etc/atlas/docker.env`).
  {
    echo "# Written by ATLAS Phase 1 step 6; sourceable and usable as a compose --env-file. Not a secret (no tokens)."
    echo "LAN_IP=$LAN_IP"
    echo "LAN_CIDR=$LAN_CIDR"
    echo "LAN_IFACE=$LAN_IFACE"
    echo "TZ=$TZ"
    echo "ATLAS_UID=$(id -u atlas)"
    echo "ATLAS_GID=$(id -g atlas)"
    echo "RENDER_GID=$render_gid"
    echo "VIDEO_GID=$video_gid"
    echo "DOCKER_GW=$gw"
    echo "CONTAINER_HTTP_PROXY=http://$gw:3128"
    echo "CONTAINER_HTTPS_PROXY=http://$gw:3128"
    echo "CONTAINER_NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    echo "WG_BRIDGE=$ATLAS_WG_BRIDGE"
    echo "WG_BRIDGE_NET=$ATLAS_WG_BRIDGE_NET"
    echo "WG_BRIDGE_GW=$ATLAS_WG_BRIDGE_GW"
    echo "NTFY_DATA_DIR=$ATLAS_SRV/data/ntfy"
    echo "WG_DATA_DIR=$ATLAS_SRV/data/wg-easy"
  } | install -m 644 /dev/stdin "$ATLAS_ETC/docker.env"
  log "wrote $ATLAS_ETC/docker.env (LAN_IP, uids/gids, container proxy at $gw:3128)"

  # Container egress rules in DOCKER-USER (the unit re-applies them whenever docker restarts).
  install -m 755 "$ATLAS_DAY1_DIR/phase1/docker-egress-rules.sh" /usr/local/sbin/atlas-docker-egress
  install -m 644 "$ATLAS_DAY1_DIR/systemd/atlas-docker-egress.service" /etc/systemd/system/atlas-docker-egress.service
  systemctl daemon-reload
  systemctl enable --now atlas-docker-egress.service >/dev/null || die "atlas-docker-egress.service failed: $(journalctl -u atlas-docker-egress -n 10 --no-pager)"
  iptables -w -S DOCKER-USER | grep -q -- '-j DROP' || die "DOCKER-USER carries no DROP rule after atlas-docker-egress ran"
  log "DOCKER-USER egress rules active: $(iptables -w -S DOCKER-USER | wc -l) rules"

  # GPU device-node passthrough test: proves /dev/kfd and /dev/dri are passable (Phase 4 uses the same flags), and
  # that the daemon pulls through the proxy. ubuntu:26.04 tag VERIFIED (services research 4.8). No ROCm involved.
  log "pulling ubuntu:26.04 through the proxy for the passthrough test"
  retry 3 docker pull -q ubuntu:26.04 >/dev/null || die "docker pull ubuntu:26.04 failed (registry-1.docker.io / auth.docker.io / the blob CDN must be allowlisted; see /var/log/squid/access.log)"
  local out
  out="$(docker run --rm --device /dev/kfd --device /dev/dri --security-opt seccomp=unconfined \
           --group-add video --group-add "$render_gid" ubuntu:26.04 sh -c 'ls /dev/kfd /dev/dri/renderD* && id -G' 2>&1)" \
    || die "GPU device passthrough test failed: $out"
  if ! grep -q '/dev/kfd' <<<"$out" || ! grep -q 'renderD' <<<"$out"; then
    die "container did not see /dev/kfd and /dev/dri/renderD*: $out"
  fi
  log "GPU passthrough test: $(tr '\n' ' ' <<<"$out")"
  # Containers must reach the proxy and nothing else: prove both from inside a container with apt (ubuntu:26.04
  # ships no curl; apt honours http_proxy/https_proxy and archive.ubuntu.com is allowlisted).
  if docker run --rm -e "http_proxy=http://$gw:3128" -e "https_proxy=http://$gw:3128" ubuntu:26.04 \
       apt-get -qq -o Acquire::http::Timeout=20 -o Acquire::Retries=1 update >/dev/null 2>&1; then
    log "container -> proxy ($gw:3128) -> archive.ubuntu.com: ok"
  else
    die "a container could not reach archive.ubuntu.com through the proxy at $gw:3128; check 'ufw status' for the docker0/172.16.0.0/12 port 3128 rules and /var/log/squid/access.log"
  fi
  # /root/.docker/config.json injects the proxy into every container root starts, so the negative test blanks it.
  docker rm -f atlas-egress-test >/dev/null 2>&1 || true
  if timeout -s KILL 90 docker run --rm --name atlas-egress-test \
       -e http_proxy= -e https_proxy= -e HTTP_PROXY= -e HTTPS_PROXY= ubuntu:26.04 \
       apt-get -qq -o Acquire::http::Timeout=8 -o Acquire::Retries=0 update >/dev/null 2>&1; then
    docker rm -f atlas-egress-test >/dev/null 2>&1 || true
    die "a container reached the internet WITHOUT the proxy: the DOCKER-USER egress rules are not enforcing (iptables -S DOCKER-USER)"
  fi
  docker rm -f atlas-egress-test >/dev/null 2>&1 || true
  log "container direct egress: blocked (DOCKER-USER), as required by Section 12.5"
}
