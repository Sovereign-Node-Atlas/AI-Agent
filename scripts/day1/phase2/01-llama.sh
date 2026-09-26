#!/usr/bin/env bash
# phase2/01-llama.sh — Section 17 Phase 2 step 1: llama.cpp with the Vulkan (RADV) backend on the host (Section 3.4, 5.2).
# Sourced by phase2-services.sh through run_phase_steps; defines step_01 only.
#
# What it does, in order (research: llama-cpp-vulkan.md §1, §2, §7; gguf-models.md §1.1; conflicts g, 10):
#   1. apt build and runtime packages (all VERIFIED to exist in resolute).
#   2. RADV sanity: /usr/share/vulkan/icd.d/radeon_icd.json present, no AMDVLK ICD, vulkaninfo names "RADV GFX1151".
#   3. Clone https://github.com/ggml-org/llama.cpp into $ATLAS_OPT/llama.cpp, check out the pinned semver tag v0.4.1
#      and refuse to build anything else (the tag must resolve to the pinned commit).
#   4. cmake -DGGML_VULKAN=ON -DLLAMA_BUILD_IS_DEV=OFF -DLLAMA_OPENSSL=ON -DLLAMA_USE_PREBUILT_UI=OFF, static binaries,
#      installed under $ATLAS_OPT/llama.cpp/dist; llama-server, llama-cli, llama-bench, llama-quantize symlinked into
#      /usr/local/bin. The build is skipped when dist/ATLAS_BUILD already names the pinned commit (rule §7.3).
#   5. llama-server --list-devices must show the Vulkan0 device, also when run as the atlas user (render/video groups).
#   6. systemd/llama-server@.service installed (render_template), /etc/sudoers.d/atlas-engines written (CONVENTIONS §8),
#      $ATLAS_ETC/engines/<key>.env rendered for all ten keys by phase2/engine-env.py, slot directories created.
#   7. V3b recorded (llama-cli --list-devices >= 160000 MiB); a fail is recorded, not fatal here: the gate decides.
#
# TODO (Section 3.4, optional, NOT Day 1): a ROCm 7.2.2 container build of llama.cpp for tuned prefill
#   (-DGGML_HIP=ON -DGPU_TARGETS=gfx1151 -DGGML_HIP_ROCWMMA_FATTN=OFF, llama-cpp-vulkan.md §8). Never on the host.
#
# Contracts relied on from other writers: config/allowlist.txt must allow github.com (clone) and the Ubuntu archive;
# Phase 1 step 6 created the atlas user in groups render and video. No model is pulled here (Section 17 step 1).

LLAMA_CPP_REPO="https://github.com/ggml-org/llama.cpp"
LLAMA_CPP_TAG="v0.4.1"                                                 # gguf-models.md §1.1 VERIFIED (2026-09-14)
LLAMA_CPP_COMMIT="b29c606e28a01b1bc8c1351026a0fa6e616bf6c4"           # the commit v0.4.1 points at, VERIFIED
LLAMA_BINARIES=(llama-server llama-cli llama-bench llama-quantize)

_llama_src() { printf '%s\n' "$ATLAS_OPT/llama.cpp"; }
_llama_dist() { printf '%s\n' "$ATLAS_OPT/llama.cpp/dist"; }

_llama_apt() {
  # llama-cpp-vulkan.md §1: build.md needs libvulkan-dev glslc spirv-headers; the project's own vulkan.Dockerfile adds
  # libssl-dev (LLAMA_OPENSSL=ON, LLAMA_CURL is deprecated: conflict 10). shaderc-tools is NOT a resolute package.
  apt_install build-essential cmake git ninja-build ccache pkg-config \
    libvulkan-dev glslc spirv-headers vulkan-tools mesa-vulkan-drivers libvulkan1 \
    libssl-dev curl jq python3
}

_llama_radv_check() {
  # RADV must be the ICD in use (llama-cpp-vulkan.md §5.4): radeon_icd.json exists, no AMDVLK amd_icd*.json.
  [[ -f /usr/share/vulkan/icd.d/radeon_icd.json ]] \
    || die "RADV ICD /usr/share/vulkan/icd.d/radeon_icd.json is missing (mesa-vulkan-drivers not installed?)"
  if compgen -G "/usr/share/vulkan/icd.d/amd_icd*.json" >/dev/null; then
    die "an AMDVLK ICD is installed under /usr/share/vulkan/icd.d (amd_icd*.json); remove it, RADV is the only supported driver here"
  fi
  # Conflict 6: match the RADV description string, never a PCI id or the marketing name.
  local summary
  summary="$(timeout 60 vulkaninfo --summary 2>/dev/null || true)"
  if grep -q 'RADV GFX1151' <<<"$summary"; then
    log "vulkaninfo: $(grep -m1 'deviceName' <<<"$summary" | sed 's/^[[:space:]]*//')"
  else
    warn "vulkaninfo --summary did not mention 'RADV GFX1151' (headless quirk or wrong driver); llama-server --list-devices below is the real test"
  fi
}

_llama_checkout() {
  local src
  src="$(_llama_src)"
  proxy_env
  if [[ ! -d "$src/.git" ]]; then
    log "cloning $LLAMA_CPP_REPO -> $src (through the allowlist proxy)"
    mkdir -p "$(dirname "$src")"
    retry 3 git clone --quiet "$LLAMA_CPP_REPO" "$src" || die "git clone of llama.cpp failed (is github.com allowlisted?)"
  fi
  git -C "$src" config --local advice.detachedHead false
  if ! git -C "$src" rev-parse -q --verify "refs/tags/$LLAMA_CPP_TAG^{commit}" >/dev/null 2>&1; then
    log "fetching tags for $LLAMA_CPP_TAG"
    retry 3 git -C "$src" fetch --quiet --tags origin || die "git fetch --tags failed"
  fi
  local resolved
  resolved="$(git -C "$src" rev-parse -q --verify "refs/tags/$LLAMA_CPP_TAG^{commit}" 2>/dev/null || true)"
  # Never build HEAD: the pin is the contract (CONVENTIONS.md §7.9; nightly bNNNNN tags move several times a day).
  [[ -n "$resolved" ]] || die "tag $LLAMA_CPP_TAG not found in $src; the pin is missing upstream, refusing to build HEAD"
  [[ "$resolved" == "$LLAMA_CPP_COMMIT" ]] \
    || die "tag $LLAMA_CPP_TAG resolves to $resolved, expected $LLAMA_CPP_COMMIT (gguf-models.md §1.1); refusing to build an unexpected commit"
  if [[ "$(git -C "$src" rev-parse HEAD)" != "$LLAMA_CPP_COMMIT" ]]; then
    git -C "$src" checkout --quiet --detach "$LLAMA_CPP_COMMIT"
  fi
  # Submodules: the Vulkan build needs none today; keep the tree honest if upstream adds one (fails loudly otherwise).
  if [[ -s "$src/.gitmodules" ]]; then
    retry 3 git -C "$src" submodule update --init --recursive --quiet || die "git submodule update failed"
  fi
  log "llama.cpp at $LLAMA_CPP_TAG ($LLAMA_CPP_COMMIT)"
}

_llama_build_needed() {
  local dist b
  dist="$(_llama_dist)"
  [[ -f "$dist/ATLAS_BUILD" && "$(cat "$dist/ATLAS_BUILD")" == "$LLAMA_CPP_COMMIT vulkan" ]] || return 0
  for b in "${LLAMA_BINARIES[@]}"; do [[ -x "$dist/bin/$b" ]] || return 0; done
  return 1
}

_llama_build() {
  local src dist
  src="$(_llama_src)"; dist="$(_llama_dist)"
  if ! _llama_build_needed; then
    log "llama.cpp build for $LLAMA_CPP_COMMIT already installed in $dist; skipping the build"
    return 0
  fi
  log "configuring llama.cpp (Vulkan, static, no UI download; this compiles for the host CPU: GGML_NATIVE=ON default)"
  rm -rf "$src/build"
  # llama-cpp-vulkan.md §2.1 + conflicts (g)/(10): -DLLAMA_BUILD_IS_DEV=OFF is required when building from a v* tag;
  # -DLLAMA_USE_PREBUILT_UI=OFF stops cmake downloading the web UI from Hugging Face at build time (Open WebUI is the
  # interface, Section 12.1); -DLLAMA_OPENSSL=ON is the HTTPS provider (libssl-dev), LLAMA_CURL is deprecated.
  # -DBUILD_SHARED_LIBS=OFF gives self-contained binaries that work through /usr/local/bin symlinks (build.md).
  cmake -S "$src" -B "$src/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_VULKAN=ON \
    -DLLAMA_BUILD_IS_DEV=OFF \
    -DLLAMA_OPENSSL=ON \
    -DLLAMA_USE_PREBUILT_UI=OFF \
    -DLLAMA_BUILD_TESTS=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_INSTALL_PREFIX="$dist" \
    || die "cmake configure failed (see the output above; the Vulkan backend needs libvulkan-dev glslc spirv-headers)"
  log "building llama.cpp with $(nproc) jobs (10-20 minutes on this CPU)"
  cmake --build "$src/build" --config Release -j "$(nproc)" || die "llama.cpp build failed"
  rm -rf "$dist"
  cmake --install "$src/build" || die "cmake --install failed"
  # UNVERIFIED: the exact install layout of every tool (llama-cpp-vulkan.md §2.1 says bin/ exists; the set of installed
  # tools is not documented). A binary the install rules skip is copied from build/bin, and a missing one is fatal.
  local b
  mkdir -p "$dist/bin"
  for b in "${LLAMA_BINARIES[@]}"; do
    if [[ ! -x "$dist/bin/$b" ]]; then
      [[ -x "$src/build/bin/$b" ]] || die "$b was not produced by the build (neither $dist/bin nor build/bin); llama.cpp $LLAMA_CPP_TAG renamed it?"
      install -m 755 "$src/build/bin/$b" "$dist/bin/$b"
    fi
  done
  printf '%s vulkan\n' "$LLAMA_CPP_COMMIT" >"$dist/ATLAS_BUILD"
  log "llama.cpp installed in $dist"
}

_llama_symlinks() {
  local dist b
  dist="$(_llama_dist)"
  for b in "${LLAMA_BINARIES[@]}"; do
    ln -sfn "$dist/bin/$b" "/usr/local/bin/$b"
  done
  local ver
  ver="$(/usr/local/bin/llama-server --version 2>&1 | head -n2 | tr '\n' ' ' || true)"
  log "llama-server --version: ${ver:-?}"
}

_llama_device_check() {
  # The Vulkan device must be visible to root and to the atlas service account (units run as atlas, groups render/video).
  local out
  out="$(timeout 120 /usr/local/bin/llama-server --list-devices 2>&1 || true)"
  grep -q 'Vulkan0:' <<<"$out" \
    || die "llama-server --list-devices shows no Vulkan0 device. Output: $(tr '\n' ' ' <<<"$out")"
  log "devices (root): $(grep -m1 'Vulkan0:' <<<"$out" | sed 's/^[[:space:]]*//')"
  out="$(timeout 120 runuser -u atlas -- /usr/local/bin/llama-server --list-devices 2>&1 || true)"
  grep -q 'Vulkan0:' <<<"$out" \
    || die "the atlas user cannot see the Vulkan device (is atlas in the render and video groups? Phase 1 step 6). Output: $(tr '\n' ' ' <<<"$out")"
  log "devices (atlas): $(grep -m1 'Vulkan0:' <<<"$out" | sed 's/^[[:space:]]*//')"
}

_llama_unit() {
  # CONVENTIONS.md §1: unit files installed from systemd/ through render_template; only the two roots are substituted,
  # %i and $ARGS are systemd syntax and stay literal.
  render_template -m 644 "$ATLAS_DAY1_DIR/systemd/llama-server@.service" \
    /etc/systemd/system/llama-server@.service ATLAS_ETC ATLAS_SRV
  grep -qF "llama-server \$ARGS" /etc/systemd/system/llama-server@.service \
    || die "render_template mangled \$ARGS in llama-server@.service"
  systemctl daemon-reload
  log "installed /etc/systemd/system/llama-server@.service"
}

_llama_sudoers() {
  # CONVENTIONS.md §8 control path: the orchestrator (user atlas) may run exactly start/stop/restart of llama-server@*.
  # Ubuntu 26.04 ships sudo-rs (conflict 7): the fragment uses only plain sudoers syntax (no aliases, no Defaults).
  # UNVERIFIED: sudo-rs's handling of a trailing "*" in command arguments — phase2/04-memory.sh starts the resident
  # units through this exact path as the atlas user, which is the run-time proof; it dies with instructions otherwise.
  local sc frag=/etc/sudoers.d/atlas-engines tmp
  sc="$(readlink -f "$(command -v systemctl)")"
  tmp="$(mktemp)"
  cat >"$tmp" <<SUDO
# atlas-engines — written by scripts/day1/phase2/01-llama.sh (CONVENTIONS.md §8). The orchestrator's Engine Arbiter
# starts and stops engines with: sudo systemctl start|stop|restart llama-server@<key>. Nothing else is permitted.
atlas ALL=(root) NOPASSWD: $sc start llama-server@*
atlas ALL=(root) NOPASSWD: $sc stop llama-server@*
atlas ALL=(root) NOPASSWD: $sc restart llama-server@*
SUDO
  if command -v visudo >/dev/null 2>&1; then
    visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment failed visudo -c; not installed"; }
  else
    warn "visudo not found (sudo-rs without it?); installing $frag unchecked"
  fi
  install -m 440 -o root -g root "$tmp" "$frag"
  rm -f "$tmp"
  log "installed $frag"
}

_llama_engine_envs() {
  ensure_dir "$ATLAS_ETC/engines" root:atlas 750
  ensure_dir "$ATLAS_SRV/data" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/data/slots" atlas:atlas 755
  ensure_dir "$ATLAS_SRV/models" atlas:atlas 755
  # The seven large engines render with ATLAS_MODEL_PRESENT=0 until Phase 3 pulls them (stderr notes are expected).
  python3 "$ATLAS_DAY1_DIR/phase2/engine-env.py" \
    --engines "$ATLAS_DAY1_DIR/config/engines.json" \
    --out "$ATLAS_ETC/engines" --models-dir "$ATLAS_SRV/models" --slots-dir "$ATLAS_SRV/data/slots" \
    --port-base "$LLAMA_PORT_BASE" --overrides "$ATLAS_ETC/engines/overrides.json" \
    || die "engine-env.py failed to render $ATLAS_ETC/engines/*.env"
  chown root:atlas "$ATLAS_ETC/engines"/*.env
  chmod 640 "$ATLAS_ETC/engines"/*.env
}

step_01() {
  _llama_apt
  _llama_radv_check
  _llama_checkout
  _llama_build
  _llama_symlinks
  _llama_device_check
  _llama_unit
  _llama_sudoers
  _llama_engine_envs
  # V3 second half (Section 21): recorded here so the number is in the table early; the Phase 2 gate re-checks it.
  run_verify V3b v03b-llama-devices.sh || warn "V3b recorded as fail: llama-cli --list-devices reports less than 160000 MiB; the Phase 2 gate will block until the GTT budget is right (Section 3.3, 4.1)"
  log "step 01 done: llama.cpp $LLAMA_CPP_TAG (Vulkan) in /usr/local/bin, llama-server@.service, sudoers, $ATLAS_ETC/engines/*.env"
}
