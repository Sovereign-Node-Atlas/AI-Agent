#!/usr/bin/env bash
# phase2/06-tools.sh — Section 17 Phase 2 step 6: the Section 15.1 tools (IfcOpenShell, Bonsai, MCP4IFC, Radiance,
# OpenStudio/EnergyPlus, KiCad CLI, Playwright, the cross-platform build container). Sourced by phase2-services.sh
# through run_phase_steps; defines step_06 only. Every tool ends with a one-line smoke test that is logged.
#
# Docling (15.1 "Document conversion") is NOT installed here: phase2/04-memory.sh installs docling 2.129.0 into
# /opt/atlas/venv with the models prefetched, and docker/core/compose.voice.yml runs docling-serve for Open WebUI
# (step 5). This step only asserts the venv import so the 15.1 table is complete.
#
# Facts typed from services-tools.md §4 and S10/S11 (VERIFIED unless marked UNVERIFIED in the code); adjudicated
# conflicts honoured: Blender is the 4.5 LTS tarball under /opt/blender (conflict 18), never apt's 5.0.1; Radiance
# comes from LBNL-ETA (research conflict 1); OpenStudio/EnergyPlus from NatLabRockies (research conflict 2).
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * /opt/atlas/venv ($ATLAS_OPT/venv) is the orchestrator venv (step 02; step 04 creates it when absent, and so does
#     this step, logged). ifcopenshell, ifcopenshell-mcp and playwright go there so the orchestrator's MCP client and
#     browser tool import them directly (services-tools.md S10).
#   * config/allowlist.txt: github.com + release-assets/objects.githubusercontent.com, download.blender.org,
#     extensions.blender.org, dl.google.com, services.gradle.org, pypi.org, files.pythonhosted.org, and the UNVERIFIED
#     Playwright CDN hosts cdn.playwright.dev / playwright.azureedge.net.
#   * $ATLAS_ETC/docker.env (Phase 1 step 6): CONTAINER_HTTP_PROXY / CONTAINER_HTTPS_PROXY for the image build.
# Contract this file defines for others:
#   * $ATLAS_ETC/tools.env (root:atlas 640): BLENDER_BIN, BLENDER_VERSION, BONSAI_VERSION, RADIANCE_BIN, RAYPATH,
#     ENERGYPLUS_BIN, OPENSTUDIO_BIN, KICAD_CLI, PLAYWRIGHT_BROWSERS_PATH, TOOLS_VENV, IFCMCP_BIN, MCP4IFC_DIR,
#     MCP4IFC_PYTHON, MCP4IFC_ADDON_ZIP, BUILDFARM_IMAGE. Sourceable KEY=VALUE lines.

IFCOPENSHELL_PIN="ifcopenshell==0.8.5"             # services-tools.md §4.1 VERIFIED (py314 manylinux wheel exists)
IFCMCP_PIN="ifcopenshell-mcp[mcp]==0.8.5"          # §4.1 VERIFIED (PyPI 2026-04-01)
PLAYWRIGHT_PIN="playwright==1.63.0"                # §4.7 VERIFIED (ubuntu26.04-x64 dependency map at v1.63.0)
BLENDER_SERIES="4.5"                               # adjudicated conflict 18: 4.5 LTS
BLENDER_RELEASE_URL="https://download.blender.org/release/Blender4.5/"
BONSAI_API_URL="https://extensions.blender.org/api/v1/extensions/"   # UNVERIFIED API (site blocked during research)
RADIANCE_URL="https://github.com/LBNL-ETA/Radiance/releases/download/rad6R0P2/Radiance_c1700d56_Linux.zip"   # §4.4 VERIFIED
ENERGYPLUS_URL="https://github.com/NatLabRockies/EnergyPlus/releases/download/v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Linux-Ubuntu24.04-x86_64.tar.gz"   # §4.5 VERIFIED
OPENSTUDIO_DEB_URL="https://github.com/NatLabRockies/OpenStudio/releases/download/v3.11.0/OpenStudio-3.11.0+241b8abb4d-Ubuntu-24.04-x86_64.deb"   # §4.5 VERIFIED
OPENSTUDIO_TGZ_URL="https://github.com/NatLabRockies/OpenStudio/releases/download/v3.11.0/OpenStudio-3.11.0+241b8abb4d-Ubuntu-24.04-x86_64.tar.gz"   # §4.5 VERIFIED
MCP4IFC_REPO="https://github.com/Show2Instruct/ifc-bonsai-mcp"   # §4.3 VERIFIED README (MCP4IFC, arXiv 2511.05533)
BUILDFARM_IMAGE="atlas-buildfarm:1"

TOOLS_VENV=""
TOOLS_STAGING=""
TOOLS_ENV_FILE=""
TOOLS_UV=""

_tools_paths() {
  TOOLS_VENV="$ATLAS_OPT/venv"
  TOOLS_STAGING="$ATLAS_SRV/staging/tools"
  TOOLS_ENV_FILE="$ATLAS_ETC/tools.env"
}

_tools_kv() { ensure_kv "$TOOLS_ENV_FILE" "$1" "$2"; }

# _tools_dl URL DEST [SHA256|none] — resumable download through the proxy, sha256 checked when given.
_tools_dl() {
  local url="$1" dest="$2" sha="${3:-none}"
  if [[ -f "$dest" && "$sha" != none && "$(sha256sum "$dest" | cut -d' ' -f1)" == "${sha,,}" ]]; then
    log "download: $dest already present with the expected sha256"
    return 0
  fi
  proxy_env
  mkdir -p "$(dirname "$dest")"
  log "download: $url -> $dest"
  retry 3 curl -fL -C - -sS --retry 3 --retry-delay 5 --connect-timeout 30 -o "$dest.part" "$url" \
    || die "download failed: $url (allowlisted? see /var/log/squid/access.log)"
  if [[ "$sha" != none ]]; then
    local have; have="$(sha256sum "$dest.part" | cut -d' ' -f1)"
    [[ "$have" == "${sha,,}" ]] || { rm -f "$dest.part"; die "sha256 mismatch for $url: got $have, expected $sha"; }
  else
    warn "download: no reference sha256 for $url (UNVERIFIED); recording the computed hash in $dest.sha256"
    sha256sum "$dest.part" | sed "s| .*|  $(basename "$dest")|" >"$dest.sha256"
  fi
  mv -f "$dest.part" "$dest"
}

_tools_apt() {
  # kicad 9.0.8+dfsg-1 ships /usr/bin/kicad-cli (§4.6 VERIFIED). The X/GL libraries are what the Blender tarball
  # links against even in -b mode (UNVERIFIED package names on resolute; apt_install dies on an unknown name).
  apt_install kicad unzip curl git jq python3-venv python3-pip xz-utils \
    libgl1 libegl1 libxi6 libxxf86vm1 libxfixes3 libxrender1 libsm6 libxkbcommon0 libxrandr2 libxinerama1 libxcursor1
}

_tools_venv() {
  if [[ ! -x "$TOOLS_VENV/bin/python" ]]; then
    log "$TOOLS_VENV absent (step 02 normally creates it); creating it here with python3 -m venv"
    mkdir -p "$ATLAS_OPT"
    python3 -m venv "$TOOLS_VENV" || die "python3 -m venv $TOOLS_VENV failed"
  fi
  [[ -x "$TOOLS_VENV/bin/pip" ]] || "$TOOLS_VENV/bin/python" -m ensurepip --upgrade || die "$TOOLS_VENV has no pip"
}

_tools_pip() {
  proxy_env
  export PIP_CACHE_DIR="$ATLAS_STATE/pip-cache" PIP_DISABLE_PIP_VERSION_CHECK=1
  mkdir -p "$PIP_CACHE_DIR"
  retry 3 "$TOOLS_VENV/bin/python" -m pip install --quiet "$@"
}

# --- 1. IfcOpenShell + IfcMCP -------------------------------------------------------------------------------------------
_tools_ifcopenshell() {
  if ! "$TOOLS_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("ifcopenshell") == "0.8.5" and m.version("ifcopenshell-mcp") == "0.8.5"' 2>/dev/null; then
    log "pip install $IFCOPENSHELL_PIN '$IFCMCP_PIN' into $TOOLS_VENV"
    _tools_pip "$IFCOPENSHELL_PIN" "$IFCMCP_PIN" || die "pip install of ifcopenshell/ifcopenshell-mcp failed in $TOOLS_VENV"
  fi
  local v
  v="$("$TOOLS_VENV/bin/python" -c 'import ifcopenshell; print(ifcopenshell.version)')" || die "ifcopenshell does not import from $TOOLS_VENV"
  log "smoke ifcopenshell.version: $v"
  [[ -x "$TOOLS_VENV/bin/ifcmcp" ]] || die "$TOOLS_VENV/bin/ifcmcp is missing after installing $IFCMCP_PIN (entry point renamed?)"
  "$TOOLS_VENV/bin/python" -c 'import ifcmcp' || die "ifcmcp package does not import (IfcMCP, services-tools.md §4.1)"
  log "smoke ifcmcp: $TOOLS_VENV/bin/ifcmcp present (stdio MCP server; the orchestrator spawns it)"
  _tools_kv TOOLS_VENV "$TOOLS_VENV"
  _tools_kv IFCMCP_BIN "$TOOLS_VENV/bin/ifcmcp"
}

# --- 2. Blender 4.5 LTS tarball + Bonsai ---------------------------------------------------------------------------------
_tools_blender() {
  local bin="/opt/blender/blender"
  if [[ -x "$bin" ]] && "$bin" -b --version 2>/dev/null | grep -q "^Blender $BLENDER_SERIES\."; then
    log "Blender already installed: $("$bin" -b --version 2>/dev/null | head -n1)"
  else
    proxy_env
    # UNVERIFIED: the exact 4.5.x patch level (download.blender.org was blocked during research). The release directory
    # listing is parsed for the highest blender-4.5.N-linux-x64.tar.xz, and its blender-4.5.N.sha256 file is used.
    local listing file ver
    listing="$(curl -fsSL --max-time 60 "$BLENDER_RELEASE_URL")" || die "could not list $BLENDER_RELEASE_URL (download.blender.org allowlisted?)"
    file="$(grep -oE "blender-$BLENDER_SERIES\.[0-9]+-linux-x64\.tar\.xz" <<<"$listing" | sort -t. -k3,3n | uniq | tail -n1)"
    [[ -n "$file" ]] || die "no blender-$BLENDER_SERIES.N-linux-x64.tar.xz in $BLENDER_RELEASE_URL (layout changed?)"
    ver="$(sed -E 's/^blender-([0-9.]+)-linux-x64\.tar\.xz$/\1/' <<<"$file")"
    local sha="none" shafile="$TOOLS_STAGING/blender-$ver.sha256"
    if curl -fsSL --max-time 60 -o "$shafile" "${BLENDER_RELEASE_URL}blender-$ver.sha256" 2>/dev/null; then
      sha="$(awk -v f="$file" '$2==f || $2=="*"f {print $1; exit}' "$shafile")"
      [[ -n "$sha" ]] || { warn "blender-$ver.sha256 does not list $file"; sha="none"; }
    else
      warn "no blender-$ver.sha256 beside the tarball (UNVERIFIED layout); the computed hash will be recorded"
    fi
    _tools_dl "$BLENDER_RELEASE_URL$file" "$TOOLS_STAGING/$file" "$sha"
    log "installing Blender $ver into /opt/blender"
    rm -rf /opt/blender.new && mkdir -p /opt/blender.new
    tar -xJf "$TOOLS_STAGING/$file" --strip-components=1 -C /opt/blender.new || die "tar -xJf $file failed"
    rm -rf /opt/blender && mv /opt/blender.new /opt/blender
  fi
  ln -sfn "$bin" /usr/local/bin/blender
  local line
  line="$("$bin" -b --version 2>&1 | head -n1)" || die "blender -b --version failed: $line"
  grep -q "^Blender $BLENDER_SERIES\." <<<"$line" || die "unexpected Blender version line: $line"
  log "smoke blender --version: $line"
  _tools_kv BLENDER_BIN "$bin"
  _tools_kv BLENDER_VERSION "${line#Blender }"
}

_tools_bonsai() {
  local bin="/opt/blender/blender" bver
  bver="$("$bin" -b --version 2>/dev/null | head -n1 | awk '{print $2}')"
  if "$bin" -b --python-expr 'import bonsai, bonsai.tool' >/dev/null 2>&1; then
    log "Bonsai already importable in Blender $bver"
  else
    proxy_env
    # UNVERIFIED (services-tools.md "could not verify"): Bonsai's zip name/URL. The extensions platform's JSON API is
    # asked for the build matching this Blender and linux-x64; archive_url + archive_hash (sha256:...) are expected.
    local api json url hash ver
    api="$BONSAI_API_URL?blender_version=$bver&platform=linux-x64"
    json="$(curl -fsSL --max-time 120 "$api")" || die "extensions.blender.org API did not answer ($api); download the Bonsai linux-x64 zip for Blender $bver by hand into $TOOLS_STAGING/bonsai-linux-x64.zip and re-run"
    read -r url hash ver < <(python3 - "$json" <<'PY' || true
import json, sys
d = json.loads(sys.argv[1])
items = d.get("data", d) if isinstance(d, dict) else d
for e in items:
    if isinstance(e, dict) and e.get("id") == "bonsai":
        print(e.get("archive_url", ""), e.get("archive_hash", ""), e.get("version", ""))
        break
PY
) || true
    if [[ -z "${url:-}" ]]; then
      [[ -s "$TOOLS_STAGING/bonsai-linux-x64.zip" ]] || die "the extensions API listed no 'bonsai' build for Blender $bver / linux-x64 (UNVERIFIED API shape). Put the zip from https://extensions.blender.org/add-ons/bonsai/ at $TOOLS_STAGING/bonsai-linux-x64.zip and re-run"
      warn "using the hand-placed $TOOLS_STAGING/bonsai-linux-x64.zip (unknown version)"
      ver="manual"
    else
      hash="${hash#sha256:}"
      [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || hash=none
      _tools_dl "$url" "$TOOLS_STAGING/bonsai-linux-x64.zip" "$hash"
    fi
    # Repository id for --repo (UNVERIFIED; read from repo-list, "user_default" preferred).
    local repos repo
    repos="$("$bin" -b --command extension repo-list 2>&1 || true)"
    repo="$(grep -oE '\buser_default\b' <<<"$repos" | head -n1 || true)"
    [[ -n "$repo" ]] || repo="$(grep -oE '^[A-Za-z0-9_]+' <<<"$repos" | head -n1 || true)"
    [[ -n "$repo" ]] || die "could not read a repository id from 'blender --command extension repo-list': $repos"
    log "installing Bonsai ${ver:-?} into Blender repo '$repo' (offline install-file)"
    "$bin" -b --command extension install-file "$TOOLS_STAGING/bonsai-linux-x64.zip" --repo "$repo" --enable \
      || die "blender --command extension install-file failed for Bonsai (is the zip the linux-x64 build for Blender $bver?)"
    _tools_kv BONSAI_VERSION "${ver:-manual}"
  fi
  local out
  out="$("$bin" -b --python-expr 'import bonsai, bonsai.tool, ifcopenshell; print("BONSAI_OK", ifcopenshell.version)' 2>&1 | grep -m1 BONSAI_OK)" \
    || die "Bonsai does not import inside Blender $bver after the install (blender -b --python-expr 'import bonsai')"
  log "smoke bonsai (inside Blender $bver): $out"
}

# --- 3. MCP4IFC ---------------------------------------------------------------------------------------------------------
_tools_uv() {
  local bv="$ATLAS_OPT/venv-uv"
  if [[ ! -x "$bv/bin/uv" ]]; then
    python3 -m venv "$bv" || die "python3 -m venv $bv failed"
    proxy_env
    # UNVERIFIED: uv version — no pin in the research; installed unpinned from PyPI (shared with phase2/05-voice.sh).
    retry 3 "$bv/bin/python" -m pip install --quiet --disable-pip-version-check uv || die "pip install uv failed"
  fi
  TOOLS_UV="$bv/bin/uv"
  export UV_PYTHON_INSTALL_DIR="$ATLAS_OPT/python" UV_CACHE_DIR="$ATLAS_STATE/uv-cache" UV_HTTP_TIMEOUT=600
  mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_CACHE_DIR"
}

_tools_mcp4ifc() {
  # Research conflict 5: MCP4IFC is real (Show2Instruct/ifc-bonsai-mcp) but is a research artefact bound to a live
  # Blender + Bonsai GUI session; treated as YELLOW: attempted, logged plainly, never blocks the phase. The headless
  # official IfcMCP (installed above) delivers LLM-driven IFC editing regardless.
  local dir="$ATLAS_OPT/tools/ifc-bonsai-mcp"
  _tools_uv
  proxy_env
  if [[ ! -d "$dir/.git" ]]; then
    mkdir -p "$(dirname "$dir")"
    retry 3 git clone --quiet "$MCP4IFC_REPO" "$dir" || die "git clone $MCP4IFC_REPO failed (github.com allowlisted?)"
  fi
  # UNVERIFIED: no commit pin exists in the research; the checked-out HEAD is recorded so the pin is at least visible.
  local head; head="$(git -C "$dir" rev-parse HEAD)"
  _tools_kv MCP4IFC_DIR "$dir"
  _tools_kv MCP4IFC_COMMIT "$head"
  log "MCP4IFC: $MCP4IFC_REPO at $head (UNVERIFIED pin: HEAD of the default branch at clone time)"
  if ! (cd "$dir" && retry 2 "$TOOLS_UV" sync --quiet); then
    warn "MCP4IFC (yellow): 'uv sync' failed in $dir; the official IfcMCP ($TOOLS_VENV/bin/ifcmcp) remains the IFC MCP server"
    _tools_kv MCP4IFC_STATUS "uv-sync-failed"
    return 0
  fi
  local py="$dir/.venv/bin/python"
  if ! "$py" -c 'import blender_mcp.server' 2>/dev/null; then
    warn "MCP4IFC (yellow): blender_mcp.server does not import from $py (README module name); IfcMCP remains the IFC MCP server"
    _tools_kv MCP4IFC_STATUS "import-failed"
    return 0
  fi
  _tools_kv MCP4IFC_PYTHON "$py"
  # Blender-side add-on zip (README: python scripts/install.py --create-addon-zip); the Bonsai/Blender packages step
  # of the README installs into Blender's own Python.
  if [[ ! -s "$dir/blender_addon.zip" ]]; then
    (cd "$dir" && "$py" scripts/install_blender_packages.py >/dev/null 2>&1 && "$py" scripts/install.py --create-addon-zip >/dev/null 2>&1) \
      || warn "MCP4IFC (yellow): the add-on zip could not be created headlessly (scripts/install.py); see $dir/README.md"
  fi
  if [[ -s "$dir/blender_addon.zip" ]]; then
    _tools_kv MCP4IFC_ADDON_ZIP "$dir/blender_addon.zip"
    # UNVERIFIED: the add-on module name; derived from the zip's top-level directory, enabled headlessly, warn-only.
    local mod
    mod="$(unzip -Z1 "$dir/blender_addon.zip" | head -n1 | cut -d/ -f1)"
    if /opt/blender/blender -b --python-expr "import bpy; bpy.ops.preferences.addon_install(filepath='$dir/blender_addon.zip', overwrite=True); bpy.ops.preferences.addon_enable(module='$mod'); bpy.ops.wm.save_userpref()" >/dev/null 2>&1; then
      log "MCP4IFC: Blender add-on '$mod' installed and enabled"
    else
      warn "MCP4IFC (yellow): add-on '$mod' could not be enabled headlessly; enable $dir/blender_addon.zip once in Blender's preferences (XFCE session)"
    fi
  fi
  _tools_kv MCP4IFC_STATUS "installed"
  log "smoke mcp4ifc: $py -c 'import blender_mcp.server' ok (server: python -m blender_mcp.server, stdio; needs Blender+Bonsai GUI with 'Connect to MCP server' clicked)"
}

# --- 4. Radiance 6.0.2 (LBNL-ETA) ----------------------------------------------------------------------------------------
_tools_radiance() {
  local bin=/opt/radiance/bin/rtrace
  if [[ ! -x "$bin" ]]; then
    local zip="$TOOLS_STAGING/Radiance_c1700d56_Linux.zip" tmp
    _tools_dl "$RADIANCE_URL" "$zip" none
    tmp="$(mktemp -d)"
    unzip -oq "$zip" -d "$tmp" || die "unzip $zip failed"
    rm -rf /opt/radiance.new && mkdir -p /opt/radiance.new
    # UNVERIFIED inner layout (services-tools.md §4.4): a radiance-*-Linux.tar.gz with bin/ lib/ man/, else bin/ directly.
    local inner
    inner="$(find "$tmp" -maxdepth 2 -name 'radiance-*-Linux.tar.gz' | head -n1 || true)"
    if [[ -n "$inner" ]]; then
      tar -xzf "$inner" --strip-components=1 -C /opt/radiance.new || die "tar of $inner failed"
    elif [[ -d "$tmp/bin" ]]; then
      cp -a "$tmp"/. /opt/radiance.new/
    else
      local sub; sub="$(find "$tmp" -maxdepth 2 -type d -name bin | head -n1 || true)"
      [[ -n "$sub" ]] || { rm -rf "$tmp"; die "Radiance zip has neither an inner tar.gz nor a bin/ directory (contents: $(find "$tmp" -maxdepth 2 | head -n 10 | tr '\n' ' '))"; }
      cp -a "$(dirname "$sub")"/. /opt/radiance.new/
    fi
    rm -rf "$tmp"
    [[ -x /opt/radiance.new/bin/rtrace ]] || die "no bin/rtrace after extracting Radiance"
    rm -rf /opt/radiance && mv /opt/radiance.new /opt/radiance
  fi
  cat >/etc/profile.d/radiance.sh <<'EOT'
export PATH=/opt/radiance/bin:$PATH RAYPATH=/opt/radiance/lib
EOT
  local out
  out="$(RAYPATH=/opt/radiance/lib "$bin" -version 2>&1 | head -n1)" || die "rtrace -version failed: $out"
  log "smoke rtrace -version: $out"
  _tools_kv RADIANCE_BIN /opt/radiance/bin
  _tools_kv RAYPATH /opt/radiance/lib
}

# --- 5. EnergyPlus 26.1.0 and OpenStudio 3.11.0 ------------------------------------------------------------------------
_tools_energyplus() {
  local bin=/opt/energyplus/energyplus
  if [[ ! -x "$bin" ]]; then
    local tgz="$TOOLS_STAGING/EnergyPlus-26.1.0-Linux-Ubuntu24.04-x86_64.tar.gz"
    _tools_dl "$ENERGYPLUS_URL" "$tgz" none
    rm -rf /opt/energyplus.new && mkdir -p /opt/energyplus.new
    tar -xzf "$tgz" --strip-components=1 -C /opt/energyplus.new || die "tar of $tgz failed"
    [[ -x /opt/energyplus.new/energyplus ]] || die "no energyplus binary at the top of the EnergyPlus tarball (layout changed?)"
    rm -rf /opt/energyplus && mv /opt/energyplus.new /opt/energyplus
  fi
  ln -sfn "$bin" /usr/local/bin/energyplus
  # UNVERIFIED: the Ubuntu-24.04 build running on 26.04 (shared-library names); this is the proof, fatal if it fails.
  local out
  out="$("$bin" --version 2>&1 | head -n1)" || die "energyplus --version failed on 26.04 (24.04 build; missing shared library?): $out"
  grep -qi 'energyplus' <<<"$out" || die "unexpected energyplus --version output: $out"
  log "smoke energyplus --version: $out"
  _tools_kv ENERGYPLUS_BIN "$bin"
}

_tools_openstudio() {
  local bin=""
  if command -v openstudio >/dev/null 2>&1; then
    bin="$(command -v openstudio)"
  elif [[ -x /opt/openstudio/bin/openstudio ]]; then
    bin=/opt/openstudio/bin/openstudio
  else
    local deb="$TOOLS_STAGING/OpenStudio-3.11.0-Ubuntu-24.04-x86_64.deb"
    _tools_dl "$OPENSTUDIO_DEB_URL" "$deb" none
    proxy_env
    export DEBIAN_FRONTEND=noninteractive
    # UNVERIFIED: the 24.04 .deb on 26.04 (services-tools.md §4.5); the tar.gz under /opt/openstudio is the fallback.
    if apt-get install -y -q "$deb" && command -v openstudio >/dev/null 2>&1; then
      bin="$(command -v openstudio)"
    else
      warn "the OpenStudio 24.04 .deb did not install cleanly on 26.04; using the tar.gz under /opt/openstudio"
      apt-get -y -q remove openstudio >/dev/null 2>&1 || true
      local tgz="$TOOLS_STAGING/OpenStudio-3.11.0-Ubuntu-24.04-x86_64.tar.gz"
      _tools_dl "$OPENSTUDIO_TGZ_URL" "$tgz" none
      rm -rf /opt/openstudio.new && mkdir -p /opt/openstudio.new
      tar -xzf "$tgz" --strip-components=1 -C /opt/openstudio.new || die "tar of $tgz failed"
      [[ -x /opt/openstudio.new/bin/openstudio ]] || die "no bin/openstudio in the OpenStudio tarball (layout changed?)"
      rm -rf /opt/openstudio && mv /opt/openstudio.new /opt/openstudio
      bin=/opt/openstudio/bin/openstudio
    fi
  fi
  [[ "$bin" == /usr/local/bin/openstudio ]] || ln -sfn "$bin" /usr/local/bin/openstudio
  local out
  out="$("$bin" --version 2>&1 | head -n1)" || die "openstudio --version failed on 26.04 (24.04 build; missing shared library?): $out"
  grep -qE '^3\.11\.' <<<"$out" || warn "openstudio --version printed '$out' (expected 3.11.x)"
  log "smoke openstudio --version: $out"
  _tools_kv OPENSTUDIO_BIN "$bin"
}

# --- 6. KiCad CLI ---------------------------------------------------------------------------------------------------------
_tools_kicad() {
  local out
  out="$(kicad-cli version 2>&1 | head -n1)" || die "kicad-cli version failed: $out"
  log "smoke kicad-cli version: $out"
  _tools_kv KICAD_CLI "$(command -v kicad-cli)"
}

# --- 7. Playwright + chromium ----------------------------------------------------------------------------------------------
_tools_playwright() {
  local browsers="$ATLAS_OPT/playwright"
  if ! "$TOOLS_VENV/bin/python" -c 'import importlib.metadata as m; assert m.version("playwright") == "1.63.0"' 2>/dev/null; then
    _tools_pip "$PLAYWRIGHT_PIN" || die "pip install $PLAYWRIGHT_PIN failed in $TOOLS_VENV"
  fi
  proxy_env
  export PLAYWRIGHT_BROWSERS_PATH="$browsers"
  mkdir -p "$browsers"
  # `playwright install --with-deps chromium` = install-deps (apt, root) + install (browser download); VERIFIED docs.
  retry 2 "$TOOLS_VENV/bin/playwright" install-deps chromium || die "playwright install-deps chromium failed (apt through the proxy)"
  if ! compgen -G "$browsers/chromium-*" >/dev/null; then
    # UNVERIFIED: the browser CDN host names (cdn.playwright.dev / playwright.azureedge.net in config/allowlist.txt).
    retry 3 "$TOOLS_VENV/bin/playwright" install chromium || die "playwright install chromium failed (browser CDN allowlisted? grep TCP_DENIED /var/log/squid/access.log)"
  fi
  chmod -R a+rX "$browsers"
  local title
  # Headless page title as the atlas user (the orchestrator's account), no network: content is set locally.
  title="$(runuser -u atlas -- env PLAYWRIGHT_BROWSERS_PATH="$browsers" "$TOOLS_VENV/bin/python" - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content("<html><head><title>ATLAS Playwright smoke</title></head><body>ok</body></html>")
    print(page.title())
    b.close()
PY
)" || die "playwright headless chromium smoke test failed as atlas"
  [[ "$title" == "ATLAS Playwright smoke" ]] || die "playwright returned an unexpected title: '$title'"
  log "smoke playwright headless title: '$title'"
  _tools_kv PLAYWRIGHT_BROWSERS_PATH "$browsers"
}

# --- 8. Build container ------------------------------------------------------------------------------------------------------
_tools_buildfarm() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  local ctx="$ATLAS_DAY1_DIR/docker/buildfarm"
  [[ -f "$ctx/Dockerfile" ]] || die "$ctx/Dockerfile is missing"
  if [[ "$(docker image inspect -f '{{index .Config.Labels "org.atlas.buildfarm.version"}}' "$BUILDFARM_IMAGE" 2>/dev/null)" == "1" ]]; then
    log "$BUILDFARM_IMAGE already built"
  else
    local hp="" sp=""
    hp="$(awk -F= '$1=="CONTAINER_HTTP_PROXY" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
    sp="$(awk -F= '$1=="CONTAINER_HTTPS_PROXY" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
    log "docker build $BUILDFARM_IMAGE (Gradle 9.7.1, cmdline-tools 15859902, NDK r30, MinGW-w64; ~2 GB of downloads through the proxy)"
    docker build --pull -t "$BUILDFARM_IMAGE" \
      --build-arg "http_proxy=$hp" --build-arg "https_proxy=$sp" --build-arg "HTTP_PROXY=$hp" --build-arg "HTTPS_PROXY=$sp" \
      --build-arg "no_proxy=localhost,127.0.0.1" --build-arg "NO_PROXY=localhost,127.0.0.1" \
      "$ctx" || die "docker build of $BUILDFARM_IMAGE failed (dl.google.com / services.gradle.org allowlisted? sdkmanager ids are UNVERIFIED build args)"
  fi
  local out
  out="$(docker run --rm --network none "$BUILDFARM_IMAGE" gradle --version 2>&1 | grep -m1 -E '^Gradle ')" \
    || die "docker run $BUILDFARM_IMAGE gradle --version failed"
  log "smoke docker run buildfarm gradle --version: $out"
  out="$(docker run --rm --network none "$BUILDFARM_IMAGE" x86_64-w64-mingw32-gcc --version 2>&1 | head -n1)" \
    || die "x86_64-w64-mingw32-gcc --version failed inside $BUILDFARM_IMAGE"
  log "smoke buildfarm mingw: $out"
  _tools_kv BUILDFARM_IMAGE "$BUILDFARM_IMAGE"
}

# --- 9. Docling (installed by step 04) ------------------------------------------------------------------------------------------
_tools_docling_assert() {
  "$TOOLS_VENV/bin/python" -c 'import importlib.metadata as m; print("docling", m.version("docling"))' \
    || die "docling is not in $TOOLS_VENV: phase2/04-memory.sh installs it (Section 17 order: step 4 before 6)"
  log "smoke docling: installed by step 04 (venv $TOOLS_VENV; docling-serve container from compose.voice.yml, step 05)"
}

step_06() {
  _tools_paths
  ensure_dir "$TOOLS_STAGING" root:root 755
  [[ -e "$TOOLS_ENV_FILE" ]] || : >"$TOOLS_ENV_FILE"
  _tools_apt
  _tools_venv
  _tools_ifcopenshell
  _tools_blender
  _tools_bonsai
  _tools_mcp4ifc
  _tools_radiance
  _tools_energyplus
  _tools_openstudio
  _tools_kicad
  _tools_playwright
  _tools_buildfarm
  _tools_docling_assert
  chown root:atlas "$TOOLS_ENV_FILE"; chmod 640 "$TOOLS_ENV_FILE"
  chown -R atlas:atlas "$TOOLS_VENV" 2>/dev/null || true
  log "step 06 done: Section 15.1 tools installed; paths in $TOOLS_ENV_FILE"
  notify "Phase 2 step 6 done: IfcOpenShell, Bonsai, MCP4IFC, Radiance, EnergyPlus, OpenStudio, KiCad, Playwright, buildfarm"
}
