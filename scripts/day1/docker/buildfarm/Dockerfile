# docker/buildfarm/Dockerfile — the cross-platform build container of Section 15.1 ("OmniBuild-KVM": Android .apk via
# NDK + Gradle, Windows .exe via MinGW-w64; Android and Windows targets only, D11). Built by phase2/06-tools.sh as
# atlas-buildfarm:1 (label org.atlas.buildfarm.version="4" marks this revision of the image: 4 = the apt pins are
# strict, no fallback to the archive's current versions; an image built as revision 3 with drift=yes is rebuilt).
#
# THE run line is executed by ONE root-owned program, /usr/local/sbin/atlas-buildfarm-run JOBDIR CMD... (written by
# phase2/06-tools.sh, granted to atlas through /etc/sudoers.d/atlas-buildfarm and nothing else): it reads the BUILDFARM_*
# keys of /etc/atlas/tools.env itself, accepts only a job directory under BUILDFARM_WORK_ROOT (/srv/atlas/workspace),
# interprets no other argument, and runs
#
#   timeout -k 5 $BUILDFARM_TIMEOUT_S docker run --rm --init --name buildfarm-<job> --network none \
#     --memory $BUILDFARM_MEMORY --memory-swap $BUILDFARM_MEMORY --cpus $BUILDFARM_CPUS --pids-limit $BUILDFARM_PIDS \
#     --cap-drop ALL --security-opt no-new-privileges --user $BUILDFARM_UID:$BUILDFARM_GID \
#     --read-only --tmpfs /tmp:rw,size=$BUILDFARM_TMPFS_SIZE \
#     -v $BUILDFARM_WORK_ROOT/<job>:/work:rw -w /work atlas-buildfarm:1 gradle assembleRelease
#   docker kill buildfarm-<job>     # when the host timeout fired (exit 124/137): the CAP binds the build, not the client
#
# Untrusted project code (Gradle/NDK/MinGW build scripts) runs under the Section 16.4 caps, as the unprivileged build
# user, on a read-only root, with the job directory as the ONLY mount. The orchestrator (atlas) calls
# `sudo -n /usr/local/sbin/atlas-buildfarm-run $BUILDFARM_WORK_ROOT/<job> <cmd>`: it never needs the docker socket for
# builds, so the Phase 1 writer can drop atlas from the docker group (root-equivalent on the host) once the sandbox path
# has a wrapper of its own (phase2/README-contracts.md). --network none means Gradle resolves nothing at build time:
# dependencies must be vendored into the job directory (offline Gradle cache under /work/.gradle, GRADLE_USER_HOME below).
#
# Every version and checksum is typed from services-tools.md §4.8 / S11 (VERIFIED on developer.android.com and
# services.gradle.org unless marked UNVERIFIED). Build-time network: archive.ubuntu.com, dl.google.com,
# services.gradle.org through the allowlist proxy (the daemon and /root/.docker/config.json inject it; the step also
# passes http_proxy/https_proxy build args). On dl.google.com this image fetches the WHOLE /android/repository/ path:
# the command-line tools zip, the NDK zip, and, through sdkmanager, the repository XMLs and the platform-tools /
# platforms / build-tools packages (Phase 2 step 6 only). config/allowlist.txt's comment names only the command-line
# tools zip; an operator auditing access.log must expect every /android/repository/ line from this build (the
# allowlist writer is asked to widen that comment). Runtime builds run with --network none.
#
# ANDROID SDK LICENCE (Section 16.3 item 2: terms are accepted by the Principal, never by a script on their behalf):
# `sdkmanager --licenses` runs ONLY when the build arg ACCEPT_ANDROID_SDK_LICENCE=yes is passed, which
# phase2/06-tools.sh does only when BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes is set in /etc/atlas/atlas.env (the
# Principal reads https://developer.android.com/studio/terms and sets the key). Without it the build STOPS at that
# layer with the URL; the step never passes the arg on its own.
#
# BASE IMAGE pinned by digest (rule §7.9): ubuntu:26.04's multi-arch index digest as served by registry-1.docker.io on
# 2026-10-04 (VERIFIED with GET /v2/library/ubuntu/manifests/26.04). `docker build --pull` re-checks the digest, never a
# moving tag. Update the digest deliberately when a newer 26.04 base is wanted.
FROM ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

ARG http_proxy
ARG https_proxy
ARG no_proxy
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY
# The build user: the atlas uid/gid of the host (ATLAS_UID/ATLAS_GID from /etc/atlas/docker.env, passed by 06-tools.sh)
# so files written into the bind-mounted job directory belong to atlas on the host, never to root.
ARG ATLAS_UID=1001
ARG ATLAS_GID=1001
# Section 16.3 item 2: "yes" only from phase2/06-tools.sh after the Principal set the atlas.env key (header).
ARG ACCEPT_ANDROID_SDK_LICENCE=no

ENV DEBIAN_FRONTEND=noninteractive \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_HOME=/opt/gradle \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# apt pins VERIFIED on packages.ubuntu.com/resolute (services-tools.md §4.8): openjdk-21-jdk-headless
# 21.0.12.1+1-1~26.04.4, gcc/g++-mingw-w64-x86-64 13.2.0-6ubuntu1+26.1, mingw-w64 13.0.0-2ubuntu1. apt's gradle (4.4.1)
# is deliberately NOT installed. The pins are STRICT (rule §7.9, fix round 3): when the archive has moved on (a security
# update replaced a pinned version) `apt-get install ...=<pin>` fails and so does the build; an unpinned fallback would
# turn a pinned image into a floating one as root in an image that later runs untrusted build scripts. phase2/06-tools.sh
# then stops and names the two pins to bump here (and the version label) after the changelog has been read.
# /opt/buildfarm/versions.txt records what dpkg installed (read back by 06-tools.sh into tools.env as
# BUILDFARM_JDK_VERSION / BUILDFARM_MINGW_VERSION and compared with the pins; drift=no is kept for that reader).
RUN apt-get update && mkdir -p /opt/buildfarm \
    && apt-get install -y --no-install-recommends \
         openjdk-21-jdk-headless=21.0.12.1+1-1~26.04.4 \
         gcc-mingw-w64-x86-64=13.2.0-6ubuntu1+26.1 g++-mingw-w64-x86-64=13.2.0-6ubuntu1+26.1 mingw-w64=13.0.0-2ubuntu1 \
         unzip curl ca-certificates git python3 make cmake ninja-build \
    && echo "drift=no" > /opt/buildfarm/versions.txt \
    && dpkg-query -W -f='${Package} ${Version}\n' openjdk-21-jdk-headless gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 mingw-w64 \
         | tee -a /opt/buildfarm/versions.txt \
    && chmod 644 /opt/buildfarm/versions.txt \
    && rm -rf /var/lib/apt/lists/*

# Gradle 9.7.1 (2026-08-19; sha256 VERIFIED from services.gradle.org/versions/current). Runs on JDK 17-26. The mode fix
# happens in the SAME layer as the unzip: a later chmod would copy every file up into a new overlayfs layer (image twice
# the size).
ARG GRADLE_VERSION=9.7.1
ARG GRADLE_SHA256=acd53f1edaf02f1a8ff99879f8a34b302661a057d9b063ae9e35b552f804d20a
RUN curl -fsSLo /tmp/gradle.zip "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
    && echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum -c - \
    && unzip -q /tmp/gradle.zip -d /opt && mv "/opt/gradle-${GRADLE_VERSION}" /opt/gradle && rm /tmp/gradle.zip \
    && chmod -R a+rX,go-w /opt/gradle

# Android command-line tools 15859902 (sha256 VERIFIED on developer.android.com/studio). sdkmanager expects the tools
# under cmdline-tools/latest (directory name convention; wording UNVERIFIED but universally used).
ARG CLT_BUILD=15859902
ARG CLT_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
RUN mkdir -p "${ANDROID_HOME}/cmdline-tools" \
    && curl -fsSLo /tmp/clt.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CLT_BUILD}_latest.zip" \
    && echo "${CLT_SHA256}  /tmp/clt.zip" | sha256sum -c - \
    && unzip -q /tmp/clt.zip -d "${ANDROID_HOME}/cmdline-tools" \
    && mv "${ANDROID_HOME}/cmdline-tools/cmdline-tools" "${ANDROID_HOME}/cmdline-tools/latest" \
    && rm /tmp/clt.zip \
    && chmod -R a+rX,go-w "${ANDROID_HOME}"

ENV PATH=${GRADLE_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${PATH}

# UNVERIFIED: the sdkmanager package ids/versions (dl.google.com's repository XML was blocked during research); they
# are build args so the step can override them, and sdkmanager exits non-zero on an unknown id, failing the build loudly.
# sdkmanager is a Java tool and ignores http(s)_proxy: the build container's direct egress is dropped by the Phase 1
# DOCKER-USER rules, so the proxy is derived from the https_proxy build arg and passed with sdkmanager's own
# --proxy/--proxy_host/--proxy_port options (VERIFIED flags, developer.android.com/tools/sdkmanager). POSIX sh (dash).
# The licence step runs ONLY behind the Principal's build arg (header); otherwise this layer stops the build.
ARG ANDROID_PLATFORM="platforms;android-36"
ARG ANDROID_BUILD_TOOLS="build-tools;36.0.0"
RUN if [ "$ACCEPT_ANDROID_SDK_LICENCE" != "yes" ]; then \
      echo "atlas-buildfarm: the Android SDK licence terms (https://developer.android.com/studio/terms) have not been accepted by the Principal:" >&2; \
      echo "  set BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes in /etc/atlas/atlas.env (Section 16.3 item 2); phase2/06-tools.sh then passes --build-arg ACCEPT_ANDROID_SDK_LICENCE=yes" >&2; \
      exit 1; \
    fi; \
    p="${https_proxy:-${HTTPS_PROXY:-}}"; p="${p#http://}"; p="${p#https://}"; p="${p%/}"; \
    if [ -n "$p" ]; then PX="--proxy=http --proxy_host=${p%:*} --proxy_port=${p##*:}"; else PX=""; fi; \
    yes | sdkmanager $PX --licenses >/dev/null \
    && sdkmanager $PX "platform-tools" "${ANDROID_PLATFORM}" "${ANDROID_BUILD_TOOLS}" \
    && chmod -R a+rX,go-w "${ANDROID_HOME}"

# NDK r30 LTS (sha1 VERIFIED on developer.android.com/ndk/downloads); Gradle ndkVersion "30.0.16248370".
ARG NDK_VERSION=r30
ARG NDK_SHA1=5107f898313790e449e87eee2183d9a20602dee9
RUN curl -fsSLo /tmp/ndk.zip "https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip" \
    && echo "${NDK_SHA1}  /tmp/ndk.zip" | sha1sum -c - \
    && unzip -q /tmp/ndk.zip -d /opt \
    && ln -s "/opt/android-ndk-${NDK_VERSION}" "${ANDROID_HOME}/ndk-${NDK_VERSION}" \
    && rm /tmp/ndk.zip \
    && chmod -R a+rX,go-w "/opt/android-ndk-${NDK_VERSION}"
ENV ANDROID_NDK_HOME=/opt/android-ndk-r30

# The unprivileged build user (ubuntu:26.04 already carries uid 1000 "ubuntu"; the atlas ids are system ids, but an
# existing id is reused rather than failing the build). Toolchains were made readable by everyone and writable by nobody
# but root in their own layers above; only the user and /work are created here (no copy-up of the toolchains).
RUN (getent group "${ATLAS_GID}" >/dev/null || groupadd -g "${ATLAS_GID}" build) \
    && (getent passwd "${ATLAS_UID}" >/dev/null || useradd -u "${ATLAS_UID}" -g "${ATLAS_GID}" -M -d /tmp -s /usr/sbin/nologin build) \
    && mkdir -p /work && chown "${ATLAS_UID}:${ATLAS_GID}" /work && chmod 1777 /work

# Writable locations under --read-only: /tmp (tmpfs from the run line) and the bind-mounted /work. Gradle's user home
# lives in the job directory so a vendored offline cache travels with the project (--network none).
ENV HOME=/tmp \
    GRADLE_USER_HOME=/work/.gradle \
    ANDROID_USER_HOME=/tmp/.android \
    GRADLE_OPTS=-Dorg.gradle.daemon=false

# Proxies must not leak into runtime builds (they run with --network none anyway); ARG values never persist as ENV.
ENV http_proxy= https_proxy= no_proxy= HTTP_PROXY= HTTPS_PROXY= NO_PROXY=

LABEL org.atlas.buildfarm.version="4" \
      org.atlas.buildfarm.base="ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7" \
      org.atlas.buildfarm.gradle="9.7.1" \
      org.atlas.buildfarm.cmdline-tools="15859902" \
      org.atlas.buildfarm.ndk="r30" \
      org.atlas.buildfarm.jdk="openjdk-21-jdk-headless 21.0.12.1+1-1~26.04.4 (strict pin; dpkg record in /opt/buildfarm/versions.txt)" \
      org.atlas.buildfarm.mingw="gcc-mingw-w64-x86-64 13.2.0-6ubuntu1+26.1 (strict pin; dpkg record in /opt/buildfarm/versions.txt)" \
      org.atlas.buildfarm.licences="Android SDK licence terms accepted by the Principal through BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes (Section 16.3 item 2)"

WORKDIR /work
USER ${ATLAS_UID}:${ATLAS_GID}
CMD ["gradle", "--version"]
