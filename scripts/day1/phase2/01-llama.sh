#!/usr/bin/env bash
# phase2/01-llama.sh — Section 17 Phase 2 step 1: llama.cpp with the Vulkan (RADV) backend on the host (Section 3.4, 5.2).
# Sourced by phase2-services.sh through run_phase_steps; defines step_01 only.
#
# What it does, in order (research: llama-cpp-vulkan.md §1, §2, §7; gguf-models.md §1.1; conflicts g, 10; v0.4.0:
# the re-pin of register D-01 (c) / S-01, approved by the Principal 2026-10-16, reports/ATLAS specialist LLM
# arsenal.md "A newer llama.cpp pin", every fact this file relies on re-read in the source at the new pin):
#   1. apt build and runtime packages (all VERIFIED to exist in resolute).
#   2. RADV sanity: /usr/share/vulkan/icd.d/radeon_icd.json present, no AMDVLK ICD, vulkaninfo names "RADV GFX1151".
#   3. Clone https://github.com/ggml-org/llama.cpp into $ATLAS_OPT/llama.cpp, check out the pinned build tag b11474
#      and refuse to build anything else (the tag must resolve to the pinned commit; b11474 is a lightweight tag and
#      `refs/tags/b11474^{commit}` resolves it the same way as the annotated v0.4.1).
#   4. cmake -DGGML_VULKAN=ON -DLLAMA_OPENSSL=ON -DLLAMA_USE_PREBUILT_UI=OFF, static binaries, installed under
#      $ATLAS_OPT/llama.cpp/dist; llama-server, llama-cli, llama-bench, llama-quantize and llama-perplexity (S-01: the
#      Phase 3 KLD record and the V25 parity test) symlinked into /usr/local/bin. The build is skipped when
#      dist/ATLAS_BUILD already names the pinned commit (rule §7.3).
#   4b. S-02 (V25): the OLD pin v0.4.1 is built beside it, in $ATLAS_OPT/llama-parity (a git worktree of the same
#      clone, so nothing is fetched twice), for the Phase 3 output-parity test ONLY: llama-perplexity alone, never
#      symlinked into /usr/local/bin, never run by a unit, never an engine. It is refused unless v0.4.1 resolves to
#      its pinned commit too.
#   5. llama-server --list-devices must show the Vulkan0 device, also when run as the atlas user (render/video groups).
#   6. systemd/llama-server@.service installed (render_template), /etc/sudoers.d/atlas-engines written (CONVENTIONS §8:
#      one explicit line per engine key and verb, 3 x the engine count (21 lines for the 7 Day 1 keys), no wildcard;
#      proven to parse by `sudo -n -l` as atlas, with visudo -c first when it exists; a sudo that cannot list or needs
#      a password for -l only warns, step 04 proves the policy by executing the granted command),
#      $ATLAS_ETC/engines/<key>.env rendered for every key by phase2/engine-env.py, $ATLAS_SRV/data and the slot
#      directory created (atlas:atlas 750).
#   7. V3b recorded (llama-cli --list-devices at or above the floor of verify/v03b-llama-devices.sh: the largest Day 1
#      seat with its KV, never below 160000 MiB; D-36 (c), S-28); a fail is recorded, not fatal here: the gate decides.
#
# TODO (Section 3.4, optional, NOT Day 1; B-13, D-52 (b) approved 2026-10-16 and phased after Day 1): a ROCm container
#   build of llama.cpp for tuned prefill (-DGGML_HIP=ON -DGPU_TARGETS=gfx1151 -DGGML_HIP_ROCWMMA_FATTN=OFF,
#   llama-cpp-vulkan.md §8) on Phase 4's pinned ROCm 10.0.0 image, not v0.3.5's ROCm 7.2.2: the inbox amdgpu driver
#   only (no DKMS), locked memory for that one container, a pinned image digest and source commit. Never on the host.
#
# Contracts relied on from other writers: config/allowlist.txt must allow github.com (clone) and the Ubuntu archive;
# Phase 1 step 6 created the atlas user in groups render and video. No model is pulled here (Section 17 step 1).

LLAMA_CPP_REPO="https://github.com/ggml-org/llama.cpp"
# S-01 / D-01 (c): build tag b11474 (2026-10-07, "feat: add GLM5Next MTP, optimize (#29928)", CMake version 0.6.0),
# VERIFIED by git fetch on 2026-10-16. A build tag, not a semver release tag (v0.6.0 is a different commit).
LLAMA_CPP_TAG="b11474"
LLAMA_CPP_COMMIT="b9acf138a1e28ce1fc23b5a4fc4b12444b50f7ea"           # the commit b11474 points at, VERIFIED
LLAMA_BINARIES=(llama-server llama-cli llama-bench llama-quantize llama-perplexity)
# S-02 (V25): the old pin, built beside the new one for the Phase 3 output-parity test only (header 4b).
LLAMA_CPP_PARITY_TAG="v0.4.1"                                          # gguf-models.md §1.1 VERIFIED (2026-09-14)
LLAMA_CPP_PARITY_COMMIT="b29c606e28a01b1bc8c1351026a0fa6e616bf6c4"    # the commit v0.4.1 points at (build b10964)
LLAMA_PARITY_BINARIES=(llama-perplexity)
# CONVENTIONS.md §8 / S-33: port = LLAMA_PORT_BASE + index for index 1..20 (8101..8120; atlas/config.py
# ENGINE_PORT_SLOTS); the Day 1 set is 7 keys, a phased engine is appended after them.
LLAMA_ENGINE_SLOTS=20

_llama_src() { printf '%s\n' "$ATLAS_OPT/llama.cpp"; }
_llama_dist() { printf '%s\n' "$ATLAS_OPT/llama.cpp/dist"; }
# The parity build (S-02): phase3/loadtest.py `parity` reads $ATLAS_OPT/llama-parity/dist/bin/llama-perplexity.
_llama_parity_src() { printf '%s\n' "$ATLAS_OPT/llama-parity/src"; }
_llama_parity_dist() { printf '%s\n' "$ATLAS_OPT/llama-parity/dist"; }

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

# _llama_clone_once DIR — one clean clone attempt (removes a partial DIR first); the unit retry works on.
_llama_clone_once() {
  rm -rf "$1" && git clone --quiet "$LLAMA_CPP_REPO" "$1"
}

_llama_checkout() {
  local src
  src="$(_llama_src)"
  proxy_env
  # A clone cut off by the proxy leaves a half-written .git that would make the fetch path below inherit a broken tree
  # (fix round 3): anything that is not a usable repository is removed and cloned afresh.
  if [[ -e "$src" ]] && ! git -C "$src" rev-parse --git-dir >/dev/null 2>&1; then
    warn "$src exists but is not a usable git repository (interrupted clone?); removing it"
    rm -rf "$src"
  fi
  if [[ ! -d "$src/.git" ]]; then
    log "cloning $LLAMA_CPP_REPO -> $src (through the allowlist proxy)"
    mkdir -p "$(dirname "$src")"
    # Each attempt starts clean: git refuses to clone into a non-empty directory, so a retry after a partial first
    # attempt would otherwise fail instantly three times (fix round 3). retry runs a shell function fine.
    retry 3 _llama_clone_once "$src" || die "git clone of llama.cpp failed (is github.com allowlisted?)"
  fi
  git -C "$src" config --local advice.detachedHead false
  if ! git -C "$src" rev-parse -q --verify "refs/tags/$LLAMA_CPP_TAG^{commit}" >/dev/null 2>&1 \
     || ! git -C "$src" rev-parse -q --verify "refs/tags/$LLAMA_CPP_PARITY_TAG^{commit}" >/dev/null 2>&1; then
    log "fetching tags for $LLAMA_CPP_TAG and $LLAMA_CPP_PARITY_TAG"
    retry 3 git -C "$src" fetch --quiet --tags origin || die "git fetch --tags failed"
  fi
  # Never build HEAD: the pin is the contract (CONVENTIONS.md §7.9). A build tag is created once per upstream commit
  # and never moved, but a tag is only a name: the commit it resolves to is checked for both pins.
  _llama_tag_is "$src" "$LLAMA_CPP_TAG" "$LLAMA_CPP_COMMIT"
  _llama_tag_is "$src" "$LLAMA_CPP_PARITY_TAG" "$LLAMA_CPP_PARITY_COMMIT"
  if [[ "$(git -C "$src" rev-parse HEAD)" != "$LLAMA_CPP_COMMIT" ]]; then
    git -C "$src" checkout --quiet --detach "$LLAMA_CPP_COMMIT"
  fi
  # Submodules: the Vulkan build needs none today; keep the tree honest if upstream adds one (fails loudly otherwise).
  if [[ -s "$src/.gitmodules" ]]; then
    retry 3 git -C "$src" submodule update --init --recursive --quiet || die "git submodule update failed"
  fi
  log "llama.cpp at $LLAMA_CPP_TAG ($LLAMA_CPP_COMMIT)"
}

# _llama_tag_is SRC TAG COMMIT — dies unless refs/tags/TAG exists in SRC and resolves to COMMIT.
_llama_tag_is() {
  local src="$1" tag="$2" want="$3" resolved
  resolved="$(git -C "$src" rev-parse -q --verify "refs/tags/$tag^{commit}" 2>/dev/null || true)"
  [[ -n "$resolved" ]] || die "tag $tag not found in $src; the pin is missing upstream, refusing to build HEAD"
  [[ "$resolved" == "$want" ]] \
    || die "tag $tag resolves to $resolved, expected $want (register D-01 / S-01, S-02); refusing to build an unexpected commit"
}

# _llama_build_needed DIST COMMIT BINARY... — true (0) when DIST lacks a build of COMMIT or one of the binaries.
_llama_build_needed() {
  local dist="$1" commit="$2" b
  shift 2
  [[ -f "$dist/ATLAS_BUILD" && "$(cat "$dist/ATLAS_BUILD")" == "$commit vulkan" ]] || return 0
  for b in "$@"; do [[ -x "$dist/bin/$b" ]] || return 0; done
  return 1
}

# _llama_cmake SRC DIST TAG [EXTRA_CMAKE_ARGS...] — configure SRC/build for DIST (shared by both pins).
_llama_cmake() {
  local src="$1" dist="$2" tag="$3"
  shift 3
  log "configuring llama.cpp $tag (Vulkan, static, no UI download; this compiles for the host CPU: GGML_NATIVE=ON default)"
  rm -rf "$src/build"
  # llama-cpp-vulkan.md §2.1 + conflicts (g)/(10): -DLLAMA_USE_PREBUILT_UI=OFF stops cmake downloading the web UI from
  # Hugging Face at build time (Open WebUI is the interface, Section 12.1); -DLLAMA_OPENSSL=ON is the HTTPS provider
  # (libssl-dev), LLAMA_CURL is deprecated. -DBUILD_SHARED_LIBS=OFF gives self-contained binaries that work through
  # /usr/local/bin symlinks (build.md). These flags are unchanged at b11474: the top-level CMakeLists.txt differs from
  # v0.4.1 only in its version lines and scripts/ui-assets.cmake is identical (v0.4.0 pin facts, VERIFIED).
  cmake -S "$src" -B "$src/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_VULKAN=ON \
    -DLLAMA_OPENSSL=ON \
    -DLLAMA_USE_PREBUILT_UI=OFF \
    -DLLAMA_BUILD_TESTS=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_INSTALL_PREFIX="$dist" \
    "$@" \
    || die "cmake configure of $tag failed (see the output above; the Vulkan backend needs libvulkan-dev glslc spirv-headers)"
}

# _llama_take_binaries SRC DIST TAG BINARY... — every binary into DIST/bin, copied from build/bin when the install
# rules skipped it (or when nothing was installed: the parity build); a missing one is fatal.
_llama_take_binaries() {
  local src="$1" dist="$2" tag="$3" b
  shift 3
  mkdir -p "$dist/bin"
  for b in "$@"; do
    if [[ ! -x "$dist/bin/$b" ]]; then
      [[ -x "$src/build/bin/$b" ]] || die "$b was not produced by the build (neither $dist/bin nor build/bin); llama.cpp $tag renamed it?"
      install -m 755 "$src/build/bin/$b" "$dist/bin/$b"
    fi
  done
}

_llama_build() {
  local src dist
  src="$(_llama_src)"; dist="$(_llama_dist)"
  if ! _llama_build_needed "$dist" "$LLAMA_CPP_COMMIT" "${LLAMA_BINARIES[@]}"; then
    log "llama.cpp build for $LLAMA_CPP_COMMIT already installed in $dist; skipping the build"
    return 0
  fi
  # v0.4.0 pin facts (CMakeLists.txt:6-21 at b11474): LLAMA_BUILD_IS_DEV defaults ON and is "set to OFF when making a
  # release from a release tag (vX.Y.Z)". b11474 is a build tag, not a release, so it stays ON and the binaries report
  # 0.6.0-dev rather than claiming the v0.6.0 release (d8123504, a different commit). The v0.4.1 parity build keeps OFF.
  _llama_cmake "$src" "$dist" "$LLAMA_CPP_TAG"
  log "building llama.cpp $LLAMA_CPP_TAG with $(nproc) jobs (10-20 minutes on this CPU)"
  cmake --build "$src/build" --config Release -j "$(nproc)" || die "llama.cpp build failed"
  rm -rf "$dist"
  cmake --install "$src/build" || die "cmake --install failed"
  # VERIFIED at b11474 (v0.4.0 pin facts; tools/server/CMakeLists.txt:63-66, tools/perplexity/CMakeLists.txt:17-24,
  # CMakeLists.txt:96-101, 140): install(TARGETS ... RUNTIME) puts every tool in dist/bin; llama-server always installs
  # and the tools install while LLAMA_TOOLS_INSTALL is on, its default in a standalone build. The copy from build/bin
  # stays as a belt for a tool the install rules skip.
  _llama_take_binaries "$src" "$dist" "$LLAMA_CPP_TAG" "${LLAMA_BINARIES[@]}"
  printf '%s vulkan\n' "$LLAMA_CPP_COMMIT" >"$dist/ATLAS_BUILD"
  log "llama.cpp installed in $dist"
}

# _llama_parity_build — S-02 (header 4b): the old pin v0.4.1 in a worktree of the same clone, llama-perplexity only
# (its own cmake target, so the build is short), in $ATLAS_OPT/llama-parity/dist. Phase 3's V25 compares the engines
# both builds load with `llama-perplexity --kl-divergence` and a small deterministic task set, flash attention on and
# off (phase3/loadtest.py parity). Never symlinked, never a unit, never an engine; removing the directory removes it.
_llama_parity_build() {
  local src psrc dist
  src="$(_llama_src)"; psrc="$(_llama_parity_src)"; dist="$(_llama_parity_dist)"
  if ! _llama_build_needed "$dist" "$LLAMA_CPP_PARITY_COMMIT" "${LLAMA_PARITY_BINARIES[@]}"; then
    log "parity build of llama.cpp $LLAMA_CPP_PARITY_TAG already installed in $dist; skipping it"
    return 0
  fi
  mkdir -p "$(dirname "$psrc")"
  # A worktree removed by hand leaves stale metadata in the main clone: prune it before adding the tree again.
  git -C "$src" worktree prune
  if [[ -e "$psrc" ]] && [[ "$(git -C "$psrc" rev-parse HEAD 2>/dev/null || true)" != "$LLAMA_CPP_PARITY_COMMIT" ]]; then
    warn "$psrc is not a worktree at $LLAMA_CPP_PARITY_COMMIT; removing it"
    git -C "$src" worktree remove --force "$psrc" 2>/dev/null || rm -rf "$psrc"
    git -C "$src" worktree prune
  fi
  if [[ ! -e "$psrc" ]]; then
    git -C "$src" worktree add --quiet --detach "$psrc" "$LLAMA_CPP_PARITY_COMMIT" \
      || die "git worktree add of $LLAMA_CPP_PARITY_TAG ($LLAMA_CPP_PARITY_COMMIT) failed"
  fi
  if [[ -s "$psrc/.gitmodules" ]]; then
    retry 3 git -C "$psrc" submodule update --init --recursive --quiet || die "git submodule update failed (parity build)"
  fi
  # llama-cpp-vulkan.md §2.1 + conflicts (g)/(10): -DLLAMA_BUILD_IS_DEV=OFF when building from a v* release tag.
  _llama_cmake "$psrc" "$dist" "$LLAMA_CPP_PARITY_TAG" -DLLAMA_BUILD_IS_DEV=OFF
  log "building the parity tool llama-perplexity at $LLAMA_CPP_PARITY_TAG with $(nproc) jobs (S-02, V25)"
  cmake --build "$psrc/build" --config Release -j "$(nproc)" --target "${LLAMA_PARITY_BINARIES[@]}" \
    || die "llama.cpp $LLAMA_CPP_PARITY_TAG parity build failed"
  rm -rf "$dist"
  _llama_take_binaries "$psrc" "$dist" "$LLAMA_CPP_PARITY_TAG" "${LLAMA_PARITY_BINARIES[@]}"
  printf '%s vulkan\n' "$LLAMA_CPP_PARITY_COMMIT" >"$dist/ATLAS_BUILD"
  log "parity build of llama.cpp $LLAMA_CPP_PARITY_TAG installed in $dist (for Phase 3 V25 only)"
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
  # timeout goes INSIDE the runuser call: the external `timeout` binary execvp()s its argument and cannot see a bash
  # function, so `timeout 120 svc_user_run ...` always failed with "failed to run command 'svc_user_run'" (fix round 2,
  # blocker). svc_user_run is `runuser -u atlas -- "$@"`, so timeout now execs llama-server as atlas.
  out="$(svc_user_run timeout 120 /usr/local/bin/llama-server --list-devices 2>&1 || true)"
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
  # CONVENTIONS.md §8 control path: the orchestrator (user atlas) may run exactly `systemctl start|stop|restart
  # llama-server@<key>` for the keys of config/engines.json and nothing else. sudoers(5) matches command arguments as
  # ONE space-separated string, so a trailing "*" would also match `stop llama-server@x ufw.service squid.service`
  # (fix round: blocker). Hence one explicit line per key and verb (3 per key), which every sudo implementation accepts,
  # including sudo-rs on Ubuntu 26.04 (conflict 7; plain syntax, no aliases, no Defaults). phase2/04-memory.sh
  # _mem_start_residents proves the positive AND the negative case at run time and dies otherwise. Re-rendered on every
  # run, so a change to engines.json is picked up.
  local sc frag=/etc/sudoers.d/atlas-engines tmp key verb
  sc="$(readlink -f "$(command -v systemctl)")"
  local keys=()
  mapfile -t keys < <(python3 -c 'import json,sys; [print(e["key"]) for e in json.load(open(sys.argv[1], encoding="utf-8"))["engines"]]' \
    "$ATLAS_DAY1_DIR/config/engines.json")
  # S-33 (v0.4.0): the count is no longer fixed at ten; it must fit the port range (8101..8120), which atlas/config.py
  # load_engines refuses to exceed as well. The Day 1 set is 7 keys.
  (( ${#keys[@]} >= 1 && ${#keys[@]} <= LLAMA_ENGINE_SLOTS )) \
    || die "config/engines.json lists ${#keys[@]} engines; CONVENTIONS.md §8 allows 1..$LLAMA_ENGINE_SLOTS (ports LLAMA_PORT_BASE+1..+$LLAMA_ENGINE_SLOTS); refusing to write $frag"
  for key in "${keys[@]}"; do
    # A key becomes part of a sudoers command line and a systemd instance name: keep it to the safe alphabet.
    [[ "$key" =~ ^[A-Za-z0-9._-]+$ ]] || die "engine key '$key' is not safe for sudoers/systemd instance names"
  done
  tmp="$(mktemp)"
  {
    echo "# atlas-engines — written by scripts/day1/phase2/01-llama.sh (CONVENTIONS.md §8). The orchestrator's Engine Arbiter"
    echo "# starts and stops engines with: sudo systemctl start|stop|restart llama-server@<key>. Exact commands only:"
    echo "# any extra argument (a second unit, --no-block, ...) is refused. Regenerated from config/engines.json."
    for key in "${keys[@]}"; do
      for verb in start stop restart; do
        printf 'atlas ALL=(root) NOPASSWD: %s %s llama-server@%s\n' "$sc" "$verb" "$key"
      done
    done
  } >"$tmp"
  if command -v visudo >/dev/null 2>&1; then
    # sudo-rs ships a visudo binary whose support for `-c -f FILE` is UNVERIFIED (platform research, conflict 7): a
    # usage error is not a syntax error, so it only warns and leaves the proof to sudo's own parse below; any other
    # non-zero exit is a real parse failure and stops the step (fix round 3).
    local vout
    if ! vout="$(visudo -c -f "$tmp" 2>&1)"; then
      if grep -qiE 'usage:|unknown option|invalid option|unrecognized|unexpected argument' <<<"$vout"; then
        warn "visudo cannot check a file here (${vout//$'\n'/ }); relying on sudo's own parse below"
      else
        rm -f "$tmp"
        die "sudoers fragment failed visudo -c: ${vout//$'\n'/ }; not installed"
      fi
    fi
  else
    warn "visudo not found (sudo-rs without it? UNVERIFIED); installing $frag and proving it by sudo's own parse below"
  fi
  # Keep the previous fragment so a failed proof can restore it (empty file when there was none: `install` of an empty
  # sudoers fragment is a valid no-op policy).
  local prev
  prev="$(mktemp)"
  [[ -f "$frag" ]] && cp -p "$frag" "$prev"
  install -m 440 -o root -g root "$tmp" "$frag"
  rm -f "$tmp"
  # Implementation-independent proof that the policy still parses with the fragment in place (fix round 2): a syntax
  # error in any sudoers.d file makes sudo refuse EVERY command, so `sudo -n -l` as atlas must exit 0 (sudo(8): list the
  # caller's own privileges; needs no password with NOPASSWD lines under sudo's listpw=any default). On failure the
  # previous fragment is restored and the step stops. Two outcomes are NOT parse failures and only warn (fix round 3):
  # a usage error (sudo-rs without -l, UNVERIFIED) and a password demand (`-n` turns it into "a password is required",
  # exit 1: whether sudo-rs honours listpw=any is UNVERIFIED). In both cases the proof is left to step 04, which proves
  # the policy implementation-independently by executing the granted command and attempting a refused one.
  local lst rc=0
  lst="$(svc_user_run sudo -n -l 2>&1)" || rc=$?
  if (( rc != 0 )); then
    if grep -qiE 'usage:|unknown option|invalid option|unrecognized|unexpected argument|password is required|authentication' <<<"$lst"; then
      warn "sudo -l needs a password or is unsupported here (${lst//$'\n'/ }); the fragment is proven in step 04 by running the granted command"
    else
      install -m 440 -o root -g root "$prev" "$frag"
      rm -f "$prev"
      die "sudo refuses to list atlas's privileges after installing $frag (exit $rc: ${lst//$'\n'/ }); the fragment does not parse under this sudo, previous state restored"
    fi
  elif ! grep -q 'llama-server@router-qwen3.5-4b' <<<"$lst"; then
    rm -f "$prev"
    die "sudo -l as atlas lists no llama-server@router-qwen3.5-4b line: $frag is not being read (includedir? sudo-rs?)"
  else
    log "sudo -l as atlas lists the engine commands ($frag in force)"
  fi
  rm -f "$prev"
  log "installed $frag ($(( ${#keys[@]} * 3 )) explicit command lines, no wildcard)"
}

_llama_engine_envs() {
  ensure_dir "$ATLAS_ETC/engines" root:atlas 750
  # 750, not 755 (fix round 3): the Principal-data root needs no world traverse; its readers are root (containers,
  # restic) and atlas (orchestrator). CONVENTIONS §2 gives /srv/atlas/* to atlas:atlas without requiring world access.
  # Cross-writer: phase2/02-orchestrator.sh _core_env_write still sets this directory 755 and should use 750 too.
  ensure_dir "$ATLAS_SRV/data" atlas:atlas 750
  # Appendix B: --slot-save-path /srv/atlas/data/slots, one dir for all. 750, not 755 (fix round 2): slot files are KV
  # snapshots of the Principal's conversations; the unit's UMask=0077 makes the files 0600 and the directory keeps every
  # other local account (atlas-ddns, future service users) from traversing it. engine-env.py applies the same mode.
  ensure_dir "$ATLAS_SRV/data/slots" atlas:atlas 750
  ensure_dir "$ATLAS_SRV/models" atlas:atlas 755
  # The four LLM seats render with ATLAS_MODEL_PRESENT=0 until Phase 3 pulls them, and the three resident companions
  # until step 04 pulls them (stderr notes are expected).
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
  _llama_parity_build
  _llama_symlinks
  _llama_device_check
  _llama_unit
  _llama_sudoers
  _llama_engine_envs
  # V3 second half (Section 21): recorded here so the number is in the table early; the Phase 2 gate re-checks it.
  run_verify V3b v03b-llama-devices.sh || warn "V3b recorded as fail: llama-cli --list-devices reports less than the largest Day 1 seat with its KV (at least 160000 MiB); the Phase 2 gate will block until the GTT budget is right (Section 3.3, 4.1; D-36 (c))"
  log "step 01 done: llama.cpp $LLAMA_CPP_TAG (Vulkan) in /usr/local/bin, the $LLAMA_CPP_PARITY_TAG parity tool in $(_llama_parity_dist)/bin (V25 only), llama-server@.service, sudoers, $ATLAS_ETC/engines/*.env"
}
