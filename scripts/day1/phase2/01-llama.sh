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
#   6. systemd/llama-server@.service installed (render_template), /etc/sudoers.d/atlas-engines written (CONVENTIONS §8:
#      one explicit line per engine key and verb, 30 lines, no wildcard; proven to parse by `sudo -n -l` as atlas, with
#      visudo -c first when it exists; a sudo that cannot list or needs a password for -l only warns, step 04 proves the
#      policy by executing the granted command), $ATLAS_ETC/engines/<key>.env rendered for all ten keys by
#      phase2/engine-env.py, $ATLAS_SRV/data and the slot directory created (atlas:atlas 750).
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
  # llama-server@<key>` for the ten keys of config/engines.json and nothing else. sudoers(5) matches command arguments as
  # ONE space-separated string, so a trailing "*" would also match `stop llama-server@x ufw.service squid.service`
  # (fix round: blocker). Hence one explicit line per key and verb (30 lines), which every sudo implementation accepts,
  # including sudo-rs on Ubuntu 26.04 (conflict 7; plain syntax, no aliases, no Defaults). phase2/04-memory.sh
  # _mem_start_residents proves the positive AND the negative case at run time and dies otherwise. Re-rendered on every
  # run, so a change to engines.json is picked up.
  local sc frag=/etc/sudoers.d/atlas-engines tmp key verb
  sc="$(readlink -f "$(command -v systemctl)")"
  local keys=()
  mapfile -t keys < <(python3 -c 'import json,sys; [print(e["key"]) for e in json.load(open(sys.argv[1], encoding="utf-8"))["engines"]]' \
    "$ATLAS_DAY1_DIR/config/engines.json")
  (( ${#keys[@]} == 10 )) || die "config/engines.json lists ${#keys[@]} engines, CONVENTIONS.md §8 fixes ten; refusing to write $frag"
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
