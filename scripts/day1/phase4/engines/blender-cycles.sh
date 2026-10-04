#!/usr/bin/env bash
# phase4/engines/blender-cycles.sh — Blender 4.5 LTS Cycles with HIP, YELLOW (Section 15.2: "works on this chip,
# occasional mid-render crashes reported, CPU-render fallback mandatory"; Section 17 step 3). Adjudicated conflict 18:
# pin 4.5 LTS (the newest 4.5.x on the release index). Research: rocm-containers.md §3.14.
# Build (fix round 2: nothing here is written by root under /srv/atlas): the host (root) lists the release index through
# the proxy and reads the sibling .sha256 file into memory (UNVERIFIED file name: the listing is searched for it; none
# -> stop); the CONTAINER, as atlas, downloads the tarball into dl/blender-cycles/; root verifies the sha256 read-only
# (regular file, realpath under the download dir); the CONTAINER unpacks it into /srv/atlas/engines/blender-cycles/
# (GNU tar as root would keep archive ownership and setuid bits on the data volume). The checksum comes from the same
# host as the tarball, so it proves transfer integrity, not provenance (the README says so).
# Test: blender-cycles_test.py renders the default cube at 64 samples with HIP (libamdhip64 from the ROCm wheels inside
# the image; UNVERIFIED that Blender's HIP 6.x fatbins load on the 10.0 runtime), then with CPU; pass needs at least
# the CPU render, the notes say which devices succeeded. Blender runs with --offline-mode (>= 4.2): no
# extension-repository sync, ever (rule §7.1).
P4_KEY="blender-cycles"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

BL_INDEX="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["index"])' "$(p4_field download)")"
BL_PATTERN="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pattern"])' "$(p4_field download)")"
BL_HOME="$P4_HOST_PRIVATE"          # host:      /srv/atlas/engines/blender-cycles
BL_CHOME="$P4_PRIVATE"              # container: /srv/atlas/engines/blender-cycles

p4_build() {
  if [[ ! -L "$BL_HOME/blender" && -x "$BL_HOME/blender" ]]; then
    log "$P4_KEY: $BL_HOME/blender already unpacked ($(p4_safe_read "$BL_HOME/.atlas-version" 2>/dev/null || echo '?'))"
  else
    _bl_fetch
  fi
  # The test needs no venv packages: it drives the blender binary; the venv exists only so p4_run_test's python path
  # holds. Created here (inside p4_build) so a re-run of a passed engine touches nothing.
  p4_venv_create
}

_bl_fetch() {
  proxy_env
  local listing tar sha_name
  listing="$(curl -fsSL --max-time 120 "$BL_INDEX")" || die "$P4_KEY: cannot list $BL_INDEX (download.blender.org allowlisted? proxy up?)"
  tar="$(grep -o "$BL_PATTERN" <<<"$listing" | sort -uV | tail -n1 || true)"
  [[ -n "$tar" ]] || die "$P4_KEY: no file matching '$BL_PATTERN' on $BL_INDEX (tarball name pattern UNVERIFIED, research §3.14)"
  local ver="${tar#blender-}"; ver="${ver%-linux-x64.tar.xz}"
  # The checksum file: research says blender-4.5.x.sha256 sits beside the tarball (UNVERIFIED); accept either spelling.
  sha_name="$(grep -oE "blender-${ver}(-linux-x64\.tar\.xz)?\.sha256" <<<"$listing" | sort -u | head -n1 || true)"
  [[ -n "$sha_name" ]] || die "$P4_KEY: no sha256 file for $tar on $BL_INDEX; refusing an unverified 300 MB binary (rule §7.3). Download and verify by hand, then unpack to $BL_HOME"
  local sums expected
  sums="$(curl -fsSL --max-time 120 "$BL_INDEX$sha_name")" || die "$P4_KEY: download of $sha_name failed"
  expected="$(grep -F "$tar" <<<"$sums" | grep -oE '[0-9a-f]{64}' | head -n1 || true)"
  [[ -n "$expected" ]] || expected="$(grep -oE '^[0-9a-f]{64}' <<<"$sums" | head -n1 || true)"
  [[ -n "$expected" ]] || die "$P4_KEY: $sha_name carries no sha256 for $tar"
  # Download inside the container as atlas (resumable; the image has curl and ca-certificates).
  log "$P4_KEY: downloading $BL_INDEX$tar through the proxy into $P4_HOST_DL (as atlas, in the container)"
  p4_docker_run --net -- curl -fsSL -C - --retry 5 --retry-delay 10 --max-time 3600 -o "$P4_DL/$tar" "$BL_INDEX$tar" \
    || die "$P4_KEY: download of $tar failed; re-run to resume"
  # Verify on the host, read-only: a regular file whose real path is under the download dir (never through a planted
  # symlink).
  local tarball="$P4_HOST_DL/$tar" real have
  [[ ! -L "$tarball" && -f "$tarball" ]] || die "$P4_KEY: $tarball is not a regular file after the download"
  real="$(realpath -e "$tarball")" && [[ "$real" == "$(realpath -e "$P4_HOST_DL")/"* ]] \
    || die "$P4_KEY: $tarball resolves outside $P4_HOST_DL; refusing"
  have="$(sha256sum "$real" | cut -d' ' -f1)"
  if [[ "$have" != "$expected" ]]; then
    p4_docker_run -- rm -f "$P4_DL/$tar" || true
    die "$P4_KEY: sha256 mismatch for $tar (got $have, expected $expected); removed, re-run"
  fi
  log "$P4_KEY: $tar verified ($have; checksum from the same index: transfer integrity, not provenance)"
  # Unpack inside the container as atlas into the engine's private tree; the version marker is written there too.
  # shellcheck disable=SC2016  # $1..$3 are the container shell's positional parameters (prefix, tarball, version)
  p4_docker_run -- sh -c 'set -e; rm -rf "$1.tmp"; mkdir -p "$1.tmp"; tar -xJf "$2" -C "$1.tmp" --strip-components=1; test -x "$1.tmp/blender"; test ! -e "$1.tmp/.atlas-version"; printf "%s\n" "$3" > "$1.tmp/.atlas-version"; rm -rf "$1.unpacked"; mv "$1.tmp" "$1.unpacked"' \
      sh "$BL_CHOME/blender" "$P4_DL/$tar" "$ver" \
    || die "$P4_KEY: unpacking $tar inside the container failed (no blender binary at the top of the tarball? layout changed?)"
  # Promote <private>/blender.unpacked -> <private>/* with one container move; the host only checks the result.
  # shellcheck disable=SC2016  # "$1" is the container shell's positional parameter (the private tree)
  p4_docker_run -- sh -c 'set -e; cd "$1"; for f in blender.unpacked/* blender.unpacked/.[!.]*; do [ -e "$f" ] || continue; rm -rf "./$(basename "$f")"; mv "$f" .; done; rmdir blender.unpacked' \
      sh "$BL_CHOME" \
    || die "$P4_KEY: moving the unpacked tree into $BL_CHOME failed"
  [[ ! -L "$BL_HOME/blender" && -x "$BL_HOME/blender" ]] || die "$P4_KEY: $BL_HOME/blender is not an executable regular file after the unpack"
  p4_note "Blender $ver LTS unpacked to $BL_HOME by the container as atlas (sha256 $have verified on the host)"
}

P4_TEST_SETTINGS=("blender=$BL_CHOME/blender")
p4_main "$@"
