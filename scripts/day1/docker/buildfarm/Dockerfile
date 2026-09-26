# docker/buildfarm/Dockerfile — the cross-platform build container of Section 15.1 ("OmniBuild-KVM": Android .apk via
# NDK + Gradle, Windows .exe via MinGW-w64; Android and Windows targets only, D11). Built by phase2/06-tools.sh as
# atlas-buildfarm:1; the orchestrator runs jobs with `docker run --rm --network none -v $PROJ:/work atlas-buildfarm:1 ...`.
#
# Every version and checksum is typed from services-tools.md §4.8 / S11 (VERIFIED on developer.android.com and
# services.gradle.org unless marked UNVERIFIED). Build-time network: archive.ubuntu.com, dl.google.com,
# services.gradle.org through the allowlist proxy (the daemon and /root/.docker/config.json inject it; the step also
# passes http_proxy/https_proxy build args). Runtime builds run with --network none.
FROM ubuntu:26.04

ARG http_proxy
ARG https_proxy
ARG no_proxy
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

ENV DEBIAN_FRONTEND=noninteractive \
    ANDROID_HOME=/opt/android-sdk \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    GRADLE_HOME=/opt/gradle \
    JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# apt: openjdk-21-jdk-headless 21.0.12.1+1-1~26.04.4, gcc/g++-mingw-w64-x86-64 13.2.0-6ubuntu1+26.1, mingw-w64
# 13.0.0-2ubuntu1 (all VERIFIED on packages.ubuntu.com/resolute). apt's gradle (4.4.1) is deliberately NOT installed.
RUN apt-get update && apt-get install -y --no-install-recommends \
      openjdk-21-jdk-headless unzip curl ca-certificates git python3 make cmake ninja-build \
      gcc-mingw-w64-x86-64 g++-mingw-w64-x86-64 mingw-w64 \
    && rm -rf /var/lib/apt/lists/*

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
ARG ANDROID_PLATFORM="platforms;android-36"
ARG ANDROID_BUILD_TOOLS="build-tools;36.0.0"
RUN yes | sdkmanager --licenses >/dev/null \
    && sdkmanager "platform-tools" "${ANDROID_PLATFORM}" "${ANDROID_BUILD_TOOLS}"

# NDK r30 LTS (sha1 VERIFIED on developer.android.com/ndk/downloads); Gradle ndkVersion "30.0.16248370".
ARG NDK_VERSION=r30
ARG NDK_SHA1=5107f898313790e449e87eee2183d9a20602dee9
RUN curl -fsSLo /tmp/ndk.zip "https://dl.google.com/android/repository/android-ndk-${NDK_VERSION}-linux.zip" \
    && echo "${NDK_SHA1}  /tmp/ndk.zip" | sha1sum -c - \
    && unzip -q /tmp/ndk.zip -d /opt \
    && ln -s "/opt/android-ndk-${NDK_VERSION}" "${ANDROID_HOME}/ndk-${NDK_VERSION}" \
    && rm /tmp/ndk.zip
ENV ANDROID_NDK_HOME=/opt/android-ndk-r30

# Proxies must not leak into runtime builds (they run with --network none anyway).
ENV http_proxy= https_proxy= no_proxy= HTTP_PROXY= HTTPS_PROXY= NO_PROXY=

LABEL org.atlas.buildfarm.version="1" \
      org.atlas.buildfarm.gradle="9.7.1" \
      org.atlas.buildfarm.cmdline-tools="15859902" \
      org.atlas.buildfarm.ndk="r30" \
      org.atlas.buildfarm.mingw="gcc-mingw-w64-x86-64 13.2.0"

WORKDIR /work
CMD ["gradle", "--version"]
