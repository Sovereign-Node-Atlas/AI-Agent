#!/usr/bin/env bash
# phase4/engines/blender-cycles.sh — Blender 4.5 LTS Cycles with HIP, YELLOW (Section 15.2: "works on this chip,
# occasional mid-render crashes reported, CPU-render fallback mandatory"; Section 17 step 3). Adjudicated conflict 18:
# pin 4.5 LTS (the newest 4.5.x on the release index). Research: rocm-containers.md §3.14.
# Build: the Linux x64 tarball from download.blender.org (allowlisted) through the proxy, sha256-checked against the
# sibling checksum file on the same index (UNVERIFIED file name: the index listing is searched for it; none -> stop),
# unpacked to $ATLAS_SRV/engines/blender/. Test: blender-cycles_test.py renders the default cube at 64 samples with
# HIP (libamdhip64 from the ROCm wheels inside the image; UNVERIFIED that Blender's HIP 6.x fatbins load on the 10.0
# runtime), then with CPU; pass needs at least the CPU render, the notes say which devices succeeded.
P4_KEY="blender-cycles"
# shellcheck source=phase4/lib-engine.sh
source "$(dirname "$(readlink -f "$0")")/../lib-engine.sh"

BL_INDEX="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["index"])' "$(p4_field download)")"
BL_PATTERN="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pattern"])' "$(p4_field download)")"
BL_HOME="$P4_ENGINES_DIR/blender"

p4_build() {
  if [[ -x "$BL_HOME/blender" ]]; then
    log "$P4_KEY: $BL_HOME/blender already unpacked ($(cat "$BL_HOME/.atlas-version" 2>/dev/null || echo '?'))"
    return 0
  fi
  proxy_env
  local listing tar sha_name
  listing="$(curl -fsSL --max-time 120 "$BL_INDEX")" || die "$P4_KEY: cannot list $BL_INDEX (download.blender.org allowlisted? proxy up?)"
  tar="$(grep -o "$BL_PATTERN" <<<"$listing" | sort -uV | tail -n1 || true)"
  [[ -n "$tar" ]] || die "$P4_KEY: no file matching '$BL_PATTERN' on $BL_INDEX (tarball name pattern UNVERIFIED, research §3.14)"
  local ver="${tar#blender-}"; ver="${ver%-linux-x64.tar.xz}"
  # The checksum file: research says blender-4.5.x.sha256 sits beside the tarball (UNVERIFIED); accept either spelling.
  sha_name="$(grep -oE "blender-${ver}(-linux-x64\.tar\.xz)?\.sha256" <<<"$listing" | sort -u | head -n1 || true)"
  [[ -n "$sha_name" ]] || die "$P4_KEY: no sha256 file for $tar on $BL_INDEX; refusing an unverified 300 MB binary (rule §7.3). Download and verify by hand, then unpack to $BL_HOME"
  mkdir -p "$P4_HOST_DL"
  local tarball="${P4_HOST_DL:?}/${tar:?}"
  log "$P4_KEY: downloading $BL_INDEX$tar (+ $sha_name) through the proxy"
  curl -fsSL -C - --retry 5 --retry-delay 10 --max-time 3600 -o "$tarball" "$BL_INDEX$tar" \
    || die "$P4_KEY: download of $tar failed; re-run to resume"
  curl -fsSL --max-time 120 -o "$P4_HOST_DL/$sha_name" "$BL_INDEX$sha_name" || die "$P4_KEY: download of $sha_name failed"
  local expected
  expected="$(grep -F "$tar" "$P4_HOST_DL/$sha_name" | grep -oE '[0-9a-f]{64}' | head -n1 || true)"
  [[ -n "$expected" ]] || expected="$(grep -oE '^[0-9a-f]{64}' "$P4_HOST_DL/$sha_name" | head -n1 || true)"
  [[ -n "$expected" ]] || die "$P4_KEY: $sha_name carries no sha256 for $tar"
  local have
  have="$(sha256sum "$tarball" | cut -d' ' -f1)"
  if [[ "$have" != "$expected" ]]; then
    rm -f "${tarball:?}"
    die "$P4_KEY: sha256 mismatch for $tar (got $have, expected $expected); removed, re-run"
  fi
  log "$P4_KEY: $tar verified ($have)"
  rm -rf "${BL_HOME:?}.tmp"
  mkdir -p "$BL_HOME.tmp"
  tar -xJf "$tarball" -C "$BL_HOME.tmp" --strip-components=1 || die "$P4_KEY: tar -xJf $tar failed"
  [[ -x "$BL_HOME.tmp/blender" ]] || die "$P4_KEY: no blender binary at the top of the tarball (layout changed?)"
  echo "$ver" >"$BL_HOME.tmp/.atlas-version"
  rm -rf "${BL_HOME:?}"
  mv "$BL_HOME.tmp" "$BL_HOME"
  chown -R "$P4_UID:$P4_GID" "$BL_HOME"
  p4_note "Blender $ver LTS unpacked to $BL_HOME (sha256 verified)"
}

# The test needs no venv packages: it drives the blender binary; the venv exists only so p4_run_test's python path holds.
P4_TEST_SETTINGS=("blender=/srv/atlas/engines/blender/blender")
p4_venv_create
p4_main "$@"
