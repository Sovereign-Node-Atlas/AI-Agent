# docker/buildfarm/Dockerfile — the cross-platform build container of Section 15.1 ("OmniBuild-KVM": Android .apk via
# NDK + Gradle, Windows .exe via MinGW-w64; Android and Windows targets only, D11). Built by phase2/06-tools.sh as
# atlas-buildfarm:1 (label org.atlas.buildfarm.version="2" marks this revision of the image).
#
# THE run line (the orchestrator builds it from the BUILDFARM_* keys of /etc/atlas/tools.env; the smoke tests in
# phase2/06-tools.sh use the same flags). Untrusted project code (Gradle/NDK/MinGW build scripts) runs under the
# Section 16.4 caps, as the unprivileged build user, on a read-only root, with the job directory as the ONLY mount:
#
#   timeout -k 5 $BUILDFARM_TIMEOUT_S docker run --rm --network none \
#     --memory $BUILDFARM_MEMORY --memory-swap $BUILDFARM_MEMORY --cpus $BUILDFARM_CPUS --pids-limit $BUILDFARM_PIDS \
#     --cap-drop ALL --security-opt no-new-privileges --user $BUILDFARM_UID:$BUILDFARM_GID \
#     --read-only --tmpfs /tmp:rw,size=$BUILDFARM_TMPFS_SIZE \
#     -v $BUILDFARM_WORK_ROOT/<job>:/work:rw -w /work atlas-buildfarm:1 gradle assembleRelease
#
# atlas is in the docker group (Phase 1 step 6), which is ROOT-EQUIVALENT on the host: any -v path, --privileged and
# --pid host are one flag away. The orchestrator must therefore construct this line itself from tools.env, accept only
# a job directory under BUILDFARM_WORK_ROOT (/srv/atlas/workspace) as the single -v source, never pass user-supplied
# flags or paths, and never relax the caps. --network none means Gradle resolves nothing at build time: dependencies
# must be vendored into the job directory (offline Gradle cache under /work/.gradle, GRADLE_USER_HOME below).
#
# Every version and checksum is typed from services-tools.md §4.8 / S11 (VERIFIED on developer.android.com and
# services.gradle.org unless marked UNVERIFIED). Build-time network: archive.ubuntu.com, dl.google.com,
# services.gradle.org through the allowlist proxy (the daemon and /root/.docker/config.json inject it; the step also
# passes http_proxy/https_proxy build args). Runtime builds run with --network none.
#
# PRINCIPAL DISCLOSURE (Section 16.3 item 2): `yes | sdkmanager --licenses` below accepts the Android SDK licence terms
# (https://developer.android.com/studio/terms) on the Principal's behalf during the image build. phase2/06-tools.sh
# prints this before `docker build`; the README lists it under the Principal's actions.
FROM ubuntu:26.04

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

ENV DEBIAN_FRONTEND=noninteractive \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_HOME=/opt/gradle \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# apt pins VERIFIED on packages.ubuntu.com/resolute (services-tools.md §4.8): openjdk-21-jdk-headless
# 21.0.12.1+1-1~26.04.4, gcc/g++-mingw-w64-x86-64 13.2.0-6ubuntu1+26.1, mingw-w64 13.0.0-2ubuntu1. apt's gradle (4.4.1)
# is deliberately NOT installed. If the archive has moved on (a security update replaces the pinned version), the
# unpinned install runs instead and the drift is printed in the build log — the image label records the pins asked for.
RUN apt-get update && ( \
      apt-get install -y --no-install-recommends \
        openjdk-21-jdk-headless=21.0.12.1+1-1~26.04.4 \
        gcc-mingw-w64-x86-64=13.2.0-6ubuntu1+26.1 g++-mingw-w64-x86-64=13.2.0-6ubuntu1+26.1 mingw-w64=13.0.0-2ubuntu1 \
        unzip curl ca-certificates git python3 make cmake ninja-build \
      || ( echo "atlas-buildfarm: PINNED apt versions unavailable (archive moved on); installing the current versions — VERSION DRIFT:" \
           && apt-get install -y --no-install-recommends \
                openjdk-21-jdk-headless gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 mingw-w64 \
                unzip curl ca-certificates git python3 make cmake ninja-build \
           && dpkg-query -W -f='${Package} ${Version}\n' openjdk-21-jdk-headless gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 mingw-w64 ) \
    ) && rm -rf /var/lib/apt/lists/*

# Gradle 9.7.1 (2026-08-19; sha256 VERIFIED from services.gradle.org/versions/current). Runs on JDK 17-26.
ARG GRADLE_VERSION=9.7.1
ARG GRADLE_SHA256=acd53f1edaf02f1a8ff99879f8a34b302661a057d9b063ae9e35b552f804d20a
RUN curl -fsSLo /tmp/gradle.zip "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
    && echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum -c - \
    && unzip -q /tmp/gradle.zip -d /opt && mv "/opt/gradle-${GRADLE_VERSION}" /opt/gradle && rm /tmp/gradle.zip

# Android command-line tools 15859902 (sha256 VERIFIED on developer.android.com/studio). sdkmanager expects the tools
# under cmdline-tools/latest (directory name convention; wording UNVERIFIED but universally used).
ARG CLT_BUILD=15859902
ARG CLT_SHA256=4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
RUN mkdir -p "${ANDROID_HOME}/cmdline-tools" \
    && curl -fsSLo /tmp/clt.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CLT_BUILD}_latest.zip" \
    && echo "${CLT_SHA256}  /tmp/clt.zip" | sha256sum -c - \
    && unzip -q /tmp/clt.zip -d "${ANDROID_HOME}/cmdline-tools" \
    && mv "${ANDROID_HOME}/cmdline-tools/cmdline-tools" "${ANDROID_HOME}/cmdline-tools/latest" \
    && rm /tmp/clt.zip

ENV PATH=${GRADLE_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${PATH}

# UNVERIFIED: the sdkmanager package ids/versions (dl.google.com's repository XML was blocked during research); they
# are build args so the step can override them, and sdkmanager exits non-zero on an unknown id, failing the build loudly.
# sdkmanager is a Java tool and ignores http(s)_proxy: the build container's direct egress is dropped by the Phase 1
# DOCKER-USER rules, so the proxy is derived from the https_proxy build arg and passed with sdkmanager's own
# --proxy/--proxy_host/--proxy_port options (VERIFIED flags, developer.android.com/tools/sdkmanager). POSIX sh (dash).
ARG ANDROID_PLATFORM="platforms;android-36"
ARG ANDROID_BUILD_TOOLS="build-tools;36.0.0"
RUN p="${https_proxy:-${HTTPS_PROXY:-}}"; p="${p#http://}"; p="${p#https://}"; p="${p%/}"; \
    if [ -n "$p" ]; then PX="--proxy=http --proxy_host=${p%:*} --proxy_port=${p##*:}"; else PX=""; fi; \
    yes | sdkmanager $PX --licenses >/dev/null \
    && sdkmanager $PX "platform-tools" "${ANDROID_PLATFORM}" "${ANDROID_BUILD_TOOLS}"

# NDK r30 LTS (sha1 VERIFIED on developer.android.com/ndk/downloads); Gradle ndkVersion "30.0.16248370".
ARG NDK_VERSION=r30
ARG NDK_SHA1=5107f898313790e449e87eee2183d9a20602dee9
RUN curl -fsSLo /tmp/ndk.zip "https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip" \
    && echo "${NDK_SHA1}  /tmp/ndk.zip" | sha1sum -c - \
    && unzip -q /tmp/ndk.zip -d /opt \
    && ln -s "/opt/android-ndk-${NDK_VERSION}" "${ANDROID_HOME}/ndk-${NDK_VERSION}" \
    && rm /tmp/ndk.zip
ENV ANDROID_NDK_HOME=/opt/android-ndk-r30

# The unprivileged build user (ubuntu:26.04 already carries uid 1000 "ubuntu"; the atlas ids are system ids, but an
# existing id is reused rather than failing the build). Toolchains readable by everyone, writable by nobody but root.
RUN (getent group "${ATLAS_GID}" >/dev/null || groupadd -g "${ATLAS_GID}" build) \
    && (getent passwd "${ATLAS_UID}" >/dev/null || useradd -u "${ATLAS_UID}" -g "${ATLAS_GID}" -M -d /tmp -s /usr/sbin/nologin build) \
    && chmod -R a+rX,go-w /opt/gradle /opt/android-sdk "/opt/android-ndk-${NDK_VERSION}" \
    && mkdir -p /work && chown "${ATLAS_UID}:${ATLAS_GID}" /work && chmod 1777 /work

# Writable locations under --read-only: /tmp (tmpfs from the run line) and the bind-mounted /work. Gradle's user home
# lives in the job directory so a vendored offline cache travels with the project (--network none).
ENV HOME=/tmp \
    GRADLE_USER_HOME=/work/.gradle \
    ANDROID_USER_HOME=/tmp/.android \
    GRADLE_OPTS=-Dorg.gradle.daemon=false

# Proxies must not leak into runtime builds (they run with --network none anyway); ARG values never persist as ENV.
ENV http_proxy= https_proxy= no_proxy= HTTP_PROXY= HTTPS_PROXY= NO_PROXY=

LABEL org.atlas.buildfarm.version="2" \
      org.atlas.buildfarm.gradle="9.7.1" \
      org.atlas.buildfarm.cmdline-tools="15859902" \
      org.atlas.buildfarm.ndk="r30" \
      org.atlas.buildfarm.jdk="openjdk-21-jdk-headless 21.0.12.1+1-1~26.04.4 (pin asked for)" \
      org.atlas.buildfarm.mingw="gcc-mingw-w64-x86-64 13.2.0-6ubuntu1+26.1 (pin asked for)" \
      org.atlas.buildfarm.licences="Android SDK licence terms accepted at build time (Section 16.3 item 2 disclosure)"

WORKDIR /work
USER ${ATLAS_UID}:${ATLAS_GID}
CMD ["gradle", "--version"]
