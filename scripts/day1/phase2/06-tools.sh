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
# comes from LBNL-ETA (research conflict 1); OpenStudio/EnergyPlus from NatLabRockies (research conflict 2); MCP4IFC is
# YELLOW (research conflict 5: attempted, logged, never blocks; the WARN summary at the end of the step names its status).
#
# WHO RUNS WHAT (fix round):
#   * Root installs packages and extracts tarballs; every archive is unpacked with --no-same-owner --no-same-permissions,
#     then chowned root:root and chmodded u=rwX,go=rX, and `find ! -user root` must be empty before the tree is put in
#     place (an upstream archive carrying uid 1000 would otherwise hand the Principal's login user write access to
#     binaries root runs through /etc/profile.d and /usr/local/bin).
#   * Blender extensions and preferences are PER USER (~/.config/blender/4.5). Every `blender -b` that installs, enables
#     or imports Bonsai/MCP4IFC runs as the atlas account (the orchestrator's identity, CONVENTIONS §8) with its own
#     HOME, and always with --offline-mode (extensions.blender.org is allowlisted only for the one-time Bonsai download;
#     Section 12.1 "update checks disabled"). tools.env records BLENDER_ARGS and BLENDER_USER_CONFIG for the orchestrator.
#   * MCP4IFC is unpinned upstream research code: it is checked out at MCP4IFC_COMMIT (a fixed 40-hex pin), and its
#     `uv sync` and install scripts run as atlas under $ATLAS_OPT/tools, never as root.
#   * Downloads are transient and go to /var/cache/atlas/downloads on the OS drive (never $ATLAS_STATE, which restic
#     backs up, and never $ATLAS_SRV, the atlas-owned data volume); sha256 pins where known, the rest recorded.
#   * The buildfarm image runs as a non-root build user and the only run line is the capped one in its header; atlas is
#     in the docker group, which is root-equivalent on the host (any -v path, --privileged, --pid host), so the
#     orchestrator must construct the docker run line itself from tools.env, accept only a job directory under
#     BUILDFARM_WORK_ROOT as the single -v source, never pass user-supplied flags, and keep the caps below.
#   * PRINCIPAL DISCLOSURE (Section 16.3 item 2): building the buildfarm image accepts the Android SDK licence terms
#     (https://developer.android.com/studio/terms) on the Principal's behalf; printed before the build, README item.
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * /opt/atlas/venv ($ATLAS_OPT/venv) is the orchestrator venv (step 02; step 04 creates it when absent, and so does
#     this step, logged). ifcopenshell, ifcopenshell-mcp and playwright go there so the orchestrator's MCP client and
#     browser tool import them directly (services-tools.md S10).
#   * config/allowlist.txt: github.com + release-assets/objects.githubusercontent.com, download.blender.org,
#     extensions.blender.org, dl.google.com, services.gradle.org, pypi.org, files.pythonhosted.org, and the UNVERIFIED
#     Playwright CDN hosts cdn.playwright.dev / playwright.azureedge.net.
#   * $ATLAS_ETC/docker.env (Phase 1 step 6): CONTAINER_HTTP_PROXY / CONTAINER_HTTPS_PROXY, ATLAS_UID, ATLAS_GID.
#   * $ATLAS_OPT/python (phase2/05-voice.sh, step 5 before 6): the uv-managed CPython 3.11 MCP4IFC's venv is built on.
#   * /var/cache/atlas (phase2/04-memory.sh): the cache root for pip/uv/downloads.
# Contract this file defines for others:
#   * $ATLAS_ETC/tools.env (root:atlas 640): BLENDER_BIN, BLENDER_VERSION, BLENDER_ARGS, BLENDER_USER_CONFIG,
#     BONSAI_VERSION, RADIANCE_BIN, RAYPATH, ENERGYPLUS_BIN, OPENSTUDIO_BIN, KICAD_CLI, PLAYWRIGHT_BROWSERS_PATH,
#     TOOLS_VENV, IFCMCP_BIN, MCP4IFC_DIR, MCP4IFC_COMMIT, MCP4IFC_STATUS, MCP4IFC_PYTHON, MCP4IFC_ADDON_ZIP,
#     BUILDFARM_IMAGE, BUILDFARM_MEMORY, BUILDFARM_CPUS, BUILDFARM_PIDS, BUILDFARM_TMPFS_SIZE, BUILDFARM_TIMEOUT_S,
#     BUILDFARM_UID, BUILDFARM_GID, BUILDFARM_WORK_ROOT. Sourceable KEY=VALUE lines.

[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

IFCOPENSHELL_PIN="ifcopenshell==0.8.5"             # services-tools.md §4.1 VERIFIED (py314 manylinux wheel exists)
IFCMCP_PIN="ifcopenshell-mcp[mcp]==0.8.5"          # §4.1 VERIFIED (PyPI 2026-04-01)
PLAYWRIGHT_PIN="playwright==1.63.0"                # §4.7 VERIFIED (ubuntu26.04-x64 dependency map at v1.63.0)
BLENDER_SERIES="4.5"                               # adjudicated conflict 18: 4.5 LTS
BLENDER_RELEASE_URL="https://download.blender.org/release/Blender4.5/"
BLENDER_ARGS="--offline-mode"                      # every headless call, one flag (quoted as one word below); Blender 4.2+ (UNVERIFIED on 4.5: the version call dies if rejected)
BONSAI_API_URL="https://extensions.blender.org/api/v1/extensions/"   # UNVERIFIED API (site blocked during research)
RADIANCE_URL="https://github.com/LBNL-ETA/Radiance/releases/download/rad6R0P2/Radiance_c1700d56_Linux.zip"   # §4.4 VERIFIED
ENERGYPLUS_URL="https://github.com/NatLabRockies/EnergyPlus/releases/download/v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Linux-Ubuntu24.04-x86_64.tar.gz"   # §4.5 VERIFIED
OPENSTUDIO_DEB_URL="https://github.com/NatLabRockies/OpenStudio/releases/download/v3.11.0/OpenStudio-3.11.0+241b8abb4d-Ubuntu-24.04-x86_64.deb"   # §4.5 VERIFIED
OPENSTUDIO_TGZ_URL="https://github.com/NatLabRockies/OpenStudio/releases/download/v3.11.0/OpenStudio-3.11.0+241b8abb4d-Ubuntu-24.04-x86_64.tar.gz"   # §4.5 VERIFIED
# sha256 of the four release assets, computed in the fix round (2026-09-27) from the assets fetched at the URLs above
# through the allowlist proxy (the research gave URLs, no hashes: UNVERIFIED against a vendor-published digest, but FIXED
# here so a changed asset stops the step instead of being installed as root).
RADIANCE_SHA256="04ee53cafbb64b943a53616b3d0ee379dd7ef80379c83aa7a145e547d9809c28"
ENERGYPLUS_SHA256="b651f4197bfc147a0f66dc92c58895d1748bdadb7a0288145fa9d50375edfbca"
OPENSTUDIO_DEB_SHA256="0206b02bf610556e54857cb32d1c5104be5588366cb2a4e08dc717bc7295b1d1"
OPENSTUDIO_TGZ_SHA256="69456da262a4c2c11e6d12b554e27d4fb1947850bb219f987d7b2d0d84893fc7"
MCP4IFC_REPO="https://github.com/Show2Instruct/ifc-bonsai-mcp"   # §4.3 VERIFIED README (MCP4IFC, arXiv 2511.05533)
# HEAD of the default branch as read with `git ls-remote` on 2026-09-27 (fix round). The research names no commit
# (UNVERIFIED as a vendor pin) but the value is FIXED: a different upstream HEAD never runs here.
MCP4IFC_COMMIT="62154932f99d8bff8494c51ddb0b16840fac8fec"
MCP4IFC_PYTHON_SERIES="3.11"                       # README: Python 3.10+; the managed 3.11 of step 5 is reused
BUILDFARM_IMAGE="atlas-buildfarm:1"
BUILDFARM_MEMORY="8g"                              # Section 16.4-style caps for the buildfarm run line (header)
BUILDFARM_CPUS="4"
BUILDFARM_PIDS="1024"
BUILDFARM_TMPFS_SIZE="2g"
BUILDFARM_TIMEOUT_S="3600"
TOOLS_CACHE_DIR="/var/cache/atlas"

TOOLS_VENV=""
TOOLS_STAGING=""
TOOLS_ENV_FILE=""
TOOLS_UV=""
TOOLS_ATLAS_HOME=""
TOOLS_ATLAS_UID=""
TOOLS_ATLAS_GID=""

_tools_paths() {
  TOOLS_VENV="$ATLAS_OPT/venv"
  TOOLS_STAGING="$TOOLS_CACHE_DIR/downloads"
  TOOLS_ENV_FILE="$ATLAS_ETC/tools.env"
  id -u atlas >/dev/null 2>&1 || die "service account atlas does not exist (Phase 1 step 3)"
  TOOLS_ATLAS_HOME="$(getent passwd atlas | cut -d: -f6)"
  [[ -n "$TOOLS_ATLAS_HOME" ]] || die "cannot read the home directory of the atlas account"
  TOOLS_ATLAS_UID="$(awk -F= '$1=="ATLAS_UID" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
  TOOLS_ATLAS_GID="$(awk -F= '$1=="ATLAS_GID" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
  [[ -n "$TOOLS_ATLAS_UID" ]] || TOOLS_ATLAS_UID="$(id -u atlas)"
  [[ -n "$TOOLS_ATLAS_GID" ]] || TOOLS_ATLAS_GID="$(id -g atlas)"
}

_tools_kv() { ensure_kv "$TOOLS_ENV_FILE" "$1" "$2"; }

# _tools_as_atlas CMD... — run as the atlas account with its HOME and the proxy (svc_user_run, lib/common.sh).
_tools_as_atlas() {
  svc_user_run env HOME="$TOOLS_ATLAS_HOME" XDG_CONFIG_HOME="$TOOLS_ATLAS_HOME/.config" \
    HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" NO_PROXY="${NO_PROXY:-}" "$@"
}

# _tools_dl URL DEST [SHA256|none] — resumable download through the proxy, sha256 checked when given.
_tools_dl() {
  local url="$1" dest="$2" sha="${3:-none}"
  sha="${sha,,}"
  if [[ -f "$dest" && "$sha" != none && "$(sha256sum "$dest" | cut -d' ' -f1)" == "$sha" ]]; then
    log "download: $dest already present with the expected sha256"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  # A .part left by an interrupted run after the transfer had completed: the hash decides, no Range request is sent.
  if [[ -f "$dest.part" && "$sha" != none && "$(sha256sum "$dest.part" | cut -d' ' -f1)" == "$sha" ]]; then
    mv -f "$dest.part" "$dest"
    log "download: $dest.part was already complete (sha256 verified); moved into place"
    return 0
  fi
  proxy_env
  log "download: $url -> $dest (resume from $(stat -c %s "$dest.part" 2>/dev/null || echo 0) bytes)"
  local rc=0
  retry 3 curl -fL -C - -sS --retry 3 --retry-delay 5 --connect-timeout 30 -o "$dest.part" "$url" || rc=$?
  if (( rc == 33 )); then
    # curl 33 = HTTP range error: the .part is complete (416) or the server ignores ranges; once more from scratch.
    warn "download: resume of $url refused (curl 33); restarting the download from zero"
    rm -f "$dest.part"; rc=0
    retry 3 curl -fL -sS --retry 3 --retry-delay 5 --connect-timeout 30 -o "$dest.part" "$url" || rc=$?
  fi
  (( rc == 0 )) || die "download failed (curl exit $rc): $url (allowlisted? see /var/log/squid/access.log)"
  local have; have="$(sha256sum "$dest.part" | cut -d' ' -f1)"
  if [[ "$sha" != none ]]; then
    [[ "$have" == "$sha" ]] || { rm -f "$dest.part"; die "sha256 mismatch for $url: got $have, expected $sha (partial removed; re-run)"; }
  else
    warn "download: no reference sha256 for $url (UNVERIFIED); recording the computed hash in $dest.sha256"
    printf '%s  %s\n' "$have" "$(basename "$dest")" >"$dest.sha256"
  fi
  mv -f "$dest.part" "$dest"
  chmod 644 "$dest"
}

# _tools_extract_tar ARCHIVE DEST [TAR-OPTS...] — root-safe extraction: no upstream owners or mode bits survive.
_tools_extract_tar() {
  local archive="$1" dest="$2"; shift 2
  rm -rf "$dest" && mkdir -p "$dest"
  tar --no-same-owner --no-same-permissions -xf "$archive" --strip-components=1 -C "$dest" "$@" || die "tar -xf $archive failed"
  _tools_root_only "$dest"
}

# _tools_root_only DIR — owner root:root, u=rwX,go=rX everywhere, asserted (privilege boundary for /opt/<tool>).
_tools_root_only() {
  local dir="$1" stray
  chown -R root:root "$dir"
  chmod -R u=rwX,go=rX "$dir"
  stray="$(find "$dir" ! -user root -o -perm /o+w 2>/dev/null | head -n 3 || true)"
  [[ -z "$stray" ]] || die "$dir still has non-root or world-writable entries after the fix: $(tr '\n' ' ' <<<"$stray")"
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
  ensure_dir "$TOOLS_CACHE_DIR/pip" root:root 755
  export PIP_CACHE_DIR="$TOOLS_CACHE_DIR/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
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
# _tools_blender_version BIN -> the first line of `blender -b --offline-mode --version` (capture first, then cut: a
# `| head -n1` pipeline would SIGPIPE Blender's later output and turn success into 141 under pipefail).
_tools_blender_version() {
  local out
  out="$("$1" -b "$BLENDER_ARGS" --version 2>&1)" || return 1
  printf '%s\n' "${out%%$'\n'*}"
}

# _tools_blender_atlas ARGS... — a headless Blender run as atlas (per-user extensions and preferences), offline.
_tools_blender_atlas() {
  _tools_as_atlas /opt/blender/blender -b "$BLENDER_ARGS" "$@"
}

_tools_blender() {
  local bin="/opt/blender/blender" line=""
  if [[ -x "$bin" ]] && line="$(_tools_blender_version "$bin")" && [[ "$line" == "Blender $BLENDER_SERIES."* ]]; then
    log "Blender already installed: $line"
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
    log "installing Blender $ver into /opt/blender (root-owned, read-only for everyone else)"
    _tools_extract_tar "$TOOLS_STAGING/$file" /opt/blender.new
    rm -rf /opt/blender && mv /opt/blender.new /opt/blender
  fi
  ln -sfn "$bin" /usr/local/bin/blender
  _tools_root_only /opt/blender
  line="$(_tools_blender_version "$bin")" || die "blender -b $BLENDER_ARGS --version failed (is --offline-mode accepted by this Blender?)"
  [[ "$line" == "Blender $BLENDER_SERIES."* ]] || die "unexpected Blender version line: $line"
  log "smoke blender --version: $line"
  _tools_kv BLENDER_BIN "$bin"
  _tools_kv BLENDER_VERSION "${line#Blender }"
  _tools_kv BLENDER_ARGS "$BLENDER_ARGS"
  _tools_kv BLENDER_USER_CONFIG "$TOOLS_ATLAS_HOME/.config/blender"
  # The atlas account owns its Blender config; the online-access preference is saved off once (UNVERIFIED attribute
  # name on 4.5, warn-only: --offline-mode on every call is the enforced control).
  ensure_dir "$TOOLS_ATLAS_HOME/.config" atlas:atlas 700
  if _tools_blender_atlas --python-expr 'import bpy; bpy.context.preferences.system.use_online_access = False; bpy.ops.wm.save_userpref()' >/dev/null 2>&1; then
    log "blender: use_online_access=False saved in $TOOLS_ATLAS_HOME/.config/blender (atlas)"
  else
    warn "blender: could not save use_online_access=False for atlas (UNVERIFIED preference name); every call passes $BLENDER_ARGS regardless"
  fi
}

_tools_bonsai() {
  local bin="/opt/blender/blender" bver
  bver="$(_tools_blender_version "$bin" | awk '{print $2}')"
  if _tools_blender_atlas --python-expr 'import bonsai, bonsai.tool' >/dev/null 2>&1; then
    log "Bonsai already importable in Blender $bver for atlas"
  else
    proxy_env
    # UNVERIFIED (services-tools.md "could not verify"): Bonsai's zip name/URL. The extensions platform's JSON API is
    # asked for the build matching this Blender and linux-x64; archive_url + archive_hash (sha256:...) are expected.
    # The answer lists every compatible extension (megabytes): it goes to a temp file, never into argv (128 KiB cap).
    local api json url="" hash="" ver="" tmpj
    api="$BONSAI_API_URL?blender_version=$bver&platform=linux-x64"
    tmpj="$(mktemp)"
    if ! curl -fsSL --max-time 120 -o "$tmpj" "$api"; then
      rm -f "$tmpj"
      die "extensions.blender.org API did not answer ($api); download the Bonsai linux-x64 zip for Blender $bver by hand into $TOOLS_STAGING/bonsai-linux-x64.zip and re-run"
    fi
    if json="$(python3 - "$tmpj" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
items = d.get("data", d) if isinstance(d, dict) else d
for e in items:
    if isinstance(e, dict) and e.get("id") == "bonsai":
        print(e.get("archive_url", ""), e.get("archive_hash", ""), e.get("version", ""))
        break
PY
)"; then
      read -r url hash ver <<<"$json" || true
    else
      warn "the extensions API answer did not parse as JSON: $(head -c 200 "$tmpj" | tr '\n' ' ')"
    fi
    rm -f "$tmpj"
    if [[ -z "$url" ]]; then
      [[ -s "$TOOLS_STAGING/bonsai-linux-x64.zip" ]] || die "the extensions API listed no 'bonsai' build for Blender $bver / linux-x64 (UNVERIFIED API shape; answer began: ${json:0:200}). Put the zip from https://extensions.blender.org/add-ons/bonsai/ at $TOOLS_STAGING/bonsai-linux-x64.zip and re-run"
      warn "using the hand-placed $TOOLS_STAGING/bonsai-linux-x64.zip (unknown version)"
      ver="manual"
    else
      hash="${hash#sha256:}"
      [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || hash=none
      _tools_dl "$url" "$TOOLS_STAGING/bonsai-linux-x64.zip" "$hash"
    fi
    chmod 644 "$TOOLS_STAGING/bonsai-linux-x64.zip"
    # Repository id for --repo (UNVERIFIED; read from repo-list as atlas, "user_default" preferred).
    local repos repo
    repos="$(_tools_blender_atlas --command extension repo-list 2>&1 || true)"
    repo="$(grep -oE '\buser_default\b' <<<"$repos" | head -n1 || true)"
    [[ -n "$repo" ]] || repo="$(grep -oE '^[A-Za-z0-9_]+' <<<"$repos" | head -n1 || true)"
    [[ -n "$repo" ]] || die "could not read a repository id from 'blender --command extension repo-list' (as atlas): $repos"
    log "installing Bonsai ${ver:-?} into Blender repo '$repo' for atlas (offline install-file)"
    _tools_blender_atlas --command extension install-file "$TOOLS_STAGING/bonsai-linux-x64.zip" --repo "$repo" --enable \
      || die "blender --command extension install-file failed for Bonsai as atlas (is the zip the linux-x64 build for Blender $bver?)"
    _tools_kv BONSAI_VERSION "${ver:-manual}"
  fi
  local out
  out="$(_tools_blender_atlas --python-expr 'import bonsai, bonsai.tool, ifcopenshell; print("BONSAI_OK", ifcopenshell.version)' 2>&1)" \
    || die "Bonsai does not import inside Blender $bver as atlas after the install: ${out: -300}"
  out="$(grep -m1 BONSAI_OK <<<"$out")" || die "no BONSAI_OK line from Blender $bver (as atlas): ${out: -300}"
  log "smoke bonsai (inside Blender $bver, as atlas): $out"
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
  chmod -R a+rX "$bv"
  ensure_dir "$TOOLS_CACHE_DIR/uv-atlas" atlas:atlas 755      # uv cache for the runs made as atlas
}

MCP4IFC_STATUS="not-attempted"

_tools_mcp4ifc() {
  # Research conflict 5: MCP4IFC is real (Show2Instruct/ifc-bonsai-mcp) but is a research artefact bound to a live
  # Blender + Bonsai GUI session; treated as YELLOW: attempted, logged plainly, never blocks the phase (the WARN summary
  # at the end of step_06 repeats MCP4IFC_STATUS). The headless official IfcMCP (installed above) delivers LLM-driven
  # IFC editing regardless. Everything below runs as atlas on the pinned commit.
  local dir="$ATLAS_OPT/tools/ifc-bonsai-mcp"
  _tools_uv
  proxy_env
  ensure_dir "$ATLAS_OPT/tools" atlas:atlas 755
  # A clone left root-owned by an earlier run of this step is handed to atlas once (git refuses "dubious ownership").
  if [[ -d "$dir" && "$(stat -c %U "$dir")" != atlas ]]; then
    warn "MCP4IFC: $dir is owned by $(stat -c %U "$dir"); chowning to atlas (everything below runs as atlas)"
    chown -R atlas:atlas "$dir"
  fi
  if [[ ! -d "$dir/.git" ]]; then
    retry 3 _tools_as_atlas git clone --quiet "$MCP4IFC_REPO" "$dir" || die "git clone $MCP4IFC_REPO failed as atlas (github.com allowlisted?)"
  fi
  # The pin: the working tree is exactly MCP4IFC_COMMIT or the step stops (unpinned upstream code never runs here).
  if [[ "$(_tools_as_atlas git -C "$dir" rev-parse HEAD 2>/dev/null || true)" != "$MCP4IFC_COMMIT" ]]; then
    _tools_as_atlas git -C "$dir" cat-file -e "$MCP4IFC_COMMIT^{commit}" 2>/dev/null \
      || retry 3 _tools_as_atlas git -C "$dir" fetch --quiet origin "$MCP4IFC_COMMIT" \
      || die "MCP4IFC: commit $MCP4IFC_COMMIT is not reachable in $MCP4IFC_REPO (rewritten history? update MCP4IFC_COMMIT deliberately after reading the diff)"
    _tools_as_atlas git -C "$dir" checkout --quiet --detach "$MCP4IFC_COMMIT" || die "MCP4IFC: git checkout $MCP4IFC_COMMIT failed in $dir"
  fi
  local head; head="$(_tools_as_atlas git -C "$dir" rev-parse HEAD)"
  [[ "$head" == "$MCP4IFC_COMMIT" ]] || die "MCP4IFC: HEAD is $head, pin is $MCP4IFC_COMMIT"
  _tools_kv MCP4IFC_DIR "$dir"
  _tools_kv MCP4IFC_COMMIT "$head"
  log "MCP4IFC: $MCP4IFC_REPO at $head (pinned; research names no commit, this one is fixed by hand)"
  local uvenv=(env UV_CACHE_DIR="$TOOLS_CACHE_DIR/uv-atlas" UV_PYTHON_INSTALL_DIR="$ATLAS_OPT/python" UV_PYTHON_DOWNLOADS=never UV_HTTP_TIMEOUT=600)
  if ! (cd "$dir" && retry 2 _tools_as_atlas "${uvenv[@]}" "$TOOLS_UV" sync --quiet --python "$MCP4IFC_PYTHON_SERIES"); then
    warn "MCP4IFC (yellow): 'uv sync --python $MCP4IFC_PYTHON_SERIES' failed in $dir as atlas (needs the managed CPython of step 5 under $ATLAS_OPT/python); the official IfcMCP ($TOOLS_VENV/bin/ifcmcp) remains the IFC MCP server"
    MCP4IFC_STATUS="uv-sync-failed"; _tools_kv MCP4IFC_STATUS "$MCP4IFC_STATUS"
    return 0
  fi
  local py="$dir/.venv/bin/python"
  if ! _tools_as_atlas "$py" -c 'import blender_mcp.server' 2>/dev/null; then
    warn "MCP4IFC (yellow): blender_mcp.server does not import from $py (README module name); IfcMCP remains the IFC MCP server"
    MCP4IFC_STATUS="import-failed"; _tools_kv MCP4IFC_STATUS "$MCP4IFC_STATUS"
    return 0
  fi
  _tools_kv MCP4IFC_PYTHON "$py"
  # Blender-side add-on zip (README: python scripts/install.py --create-addon-zip). install_blender_packages.py wants
  # to pip-install into Blender's bundled Python under /opt/blender, which is root-owned and read-only for atlas: when
  # it fails, that is reported, not worked around by running upstream code as root.
  if [[ ! -s "$dir/blender_addon.zip" ]]; then
    (cd "$dir" && _tools_as_atlas "$py" scripts/install_blender_packages.py >/dev/null 2>&1) \
      || warn "MCP4IFC (yellow): scripts/install_blender_packages.py could not install into Blender's Python as atlas (/opt/blender is root-owned by design); Bonsai bundles ifcopenshell, the rest is only needed by the GUI add-on"
    (cd "$dir" && _tools_as_atlas "$py" scripts/install.py --create-addon-zip >/dev/null 2>&1) \
      || warn "MCP4IFC (yellow): the add-on zip could not be created headlessly (scripts/install.py); see $dir/README.md"
  fi
  if [[ -s "$dir/blender_addon.zip" ]]; then
    _tools_kv MCP4IFC_ADDON_ZIP "$dir/blender_addon.zip"
    # UNVERIFIED: the add-on module name; derived from the zip's top-level directory, enabled headlessly AS ATLAS
    # (per-user preferences), warn-only.
    local mod
    mod="$(unzip -Z1 "$dir/blender_addon.zip" | head -n1 | cut -d/ -f1)"
    if _tools_blender_atlas --python-expr "import bpy; bpy.ops.preferences.addon_install(filepath='$dir/blender_addon.zip', overwrite=True); bpy.ops.preferences.addon_enable(module='$mod'); bpy.ops.wm.save_userpref()" >/dev/null 2>&1; then
      log "MCP4IFC: Blender add-on '$mod' installed and enabled for atlas"
    else
      warn "MCP4IFC (yellow): add-on '$mod' could not be enabled headlessly for atlas; enable $dir/blender_addon.zip once in Blender's preferences (XFCE session, as atlas)"
    fi
  fi
  MCP4IFC_STATUS="installed"; _tools_kv MCP4IFC_STATUS "$MCP4IFC_STATUS"
  log "smoke mcp4ifc: $py -c 'import blender_mcp.server' ok as atlas (server: python -m blender_mcp.server, stdio; needs Blender+Bonsai GUI with 'Connect to MCP server' clicked)"
}

# --- 4. Radiance 6.0.2 (LBNL-ETA) ----------------------------------------------------------------------------------------
_tools_radiance() {
  local bin=/opt/radiance/bin/rtrace
  if [[ ! -x "$bin" ]]; then
    local zip="$TOOLS_STAGING/Radiance_c1700d56_Linux.zip" tmp
    _tools_dl "$RADIANCE_URL" "$zip" "$RADIANCE_SHA256"
    tmp="$(mktemp -d)"
    unzip -oq "$zip" -d "$tmp" || die "unzip $zip failed"
    rm -rf /opt/radiance.new && mkdir -p /opt/radiance.new
    # UNVERIFIED inner layout (services-tools.md §4.4): a radiance-*-Linux.tar.gz with bin/ lib/ man/, else bin/ directly.
    local inner
    inner="$(find "$tmp" -maxdepth 2 -name 'radiance-*-Linux.tar.gz' | head -n1 || true)"
    if [[ -n "$inner" ]]; then
      tar --no-same-owner --no-same-permissions -xzf "$inner" --strip-components=1 -C /opt/radiance.new || die "tar of $inner failed"
    elif [[ -d "$tmp/bin" ]]; then
      cp -a "$tmp"/. /opt/radiance.new/
    else
      local sub; sub="$(find "$tmp" -maxdepth 2 -type d -name bin | head -n1 || true)"
      [[ -n "$sub" ]] || { rm -rf "$tmp"; die "Radiance zip has neither an inner tar.gz nor a bin/ directory (contents: $(find "$tmp" -maxdepth 2 | head -n 10 | tr '\n' ' '))"; }
      cp -a "$(dirname "$sub")"/. /opt/radiance.new/
    fi
    rm -rf "$tmp"
    [[ -x /opt/radiance.new/bin/rtrace ]] || die "no bin/rtrace after extracting Radiance"
    _tools_root_only /opt/radiance.new
    rm -rf /opt/radiance && mv /opt/radiance.new /opt/radiance
  fi
  _tools_root_only /opt/radiance
  # Only after ownership is root: this PATH line reaches every login shell, root included.
  cat >/etc/profile.d/radiance.sh <<'EOT'
export PATH=/opt/radiance/bin:$PATH RAYPATH=/opt/radiance/lib
EOT
  local out
  out="$(RAYPATH=/opt/radiance/lib "$bin" -version 2>&1)" || die "rtrace -version failed: ${out: -300}"
  out="${out%%$'\n'*}"
  log "smoke rtrace -version: $out"
  _tools_kv RADIANCE_BIN /opt/radiance/bin
  _tools_kv RAYPATH /opt/radiance/lib
}

# --- 5. EnergyPlus 26.1.0 and OpenStudio 3.11.0 ------------------------------------------------------------------------
_tools_energyplus() {
  local bin=/opt/energyplus/energyplus
  if [[ ! -x "$bin" ]]; then
    local tgz="$TOOLS_STAGING/EnergyPlus-26.1.0-Linux-Ubuntu24.04-x86_64.tar.gz"
    _tools_dl "$ENERGYPLUS_URL" "$tgz" "$ENERGYPLUS_SHA256"
    _tools_extract_tar "$tgz" /opt/energyplus.new
    [[ -x /opt/energyplus.new/energyplus ]] || die "no energyplus binary at the top of the EnergyPlus tarball (layout changed?)"
    rm -rf /opt/energyplus && mv /opt/energyplus.new /opt/energyplus
  fi
  _tools_root_only /opt/energyplus
  ln -sfn "$bin" /usr/local/bin/energyplus
  # UNVERIFIED: the Ubuntu-24.04 build running on 26.04 (shared-library names); this is the proof, fatal if it fails.
  local out
  out="$("$bin" --version 2>&1)" || die "energyplus --version failed on 26.04 (24.04 build; missing shared library?): ${out: -300}"
  out="${out%%$'\n'*}"
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
    _tools_dl "$OPENSTUDIO_DEB_URL" "$deb" "$OPENSTUDIO_DEB_SHA256"     # hash verified BEFORE apt runs its maintainer scripts
    proxy_env
    export DEBIAN_FRONTEND=noninteractive
    # UNVERIFIED: the 24.04 .deb on 26.04 (services-tools.md §4.5); the tar.gz under /opt/openstudio is the fallback.
    if apt-get install -y -q "$deb" && command -v openstudio >/dev/null 2>&1; then
      bin="$(command -v openstudio)"
    else
      warn "the OpenStudio 24.04 .deb did not install cleanly on 26.04; using the tar.gz under /opt/openstudio"
      apt-get -y -q remove openstudio >/dev/null 2>&1 || true
      local tgz="$TOOLS_STAGING/OpenStudio-3.11.0-Ubuntu-24.04-x86_64.tar.gz"
      _tools_dl "$OPENSTUDIO_TGZ_URL" "$tgz" "$OPENSTUDIO_TGZ_SHA256"
      _tools_extract_tar "$tgz" /opt/openstudio.new
      [[ -x /opt/openstudio.new/bin/openstudio ]] || die "no bin/openstudio in the OpenStudio tarball (layout changed?)"
      rm -rf /opt/openstudio && mv /opt/openstudio.new /opt/openstudio
      bin=/opt/openstudio/bin/openstudio
    fi
  fi
  [[ -d /opt/openstudio ]] && _tools_root_only /opt/openstudio
  [[ "$bin" == /usr/local/bin/openstudio ]] || ln -sfn "$bin" /usr/local/bin/openstudio
  local out
  out="$("$bin" --version 2>&1)" || die "openstudio --version failed on 26.04 (24.04 build; missing shared library?): ${out: -300}"
  out="${out%%$'\n'*}"
  grep -qE '^3\.11\.' <<<"$out" || warn "openstudio --version printed '$out' (expected 3.11.x)"
  log "smoke openstudio --version: $out"
  _tools_kv OPENSTUDIO_BIN "$bin"
}

# --- 6. KiCad CLI ---------------------------------------------------------------------------------------------------------
_tools_kicad() {
  local out
  out="$(kicad-cli version 2>&1)" || die "kicad-cli version failed: ${out: -300}"
  out="${out%%$'\n'*}"
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
  title="$(_tools_as_atlas env PLAYWRIGHT_BROWSERS_PATH="$browsers" "$TOOLS_VENV/bin/python" - <<'PY'
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
# _tools_buildfarm_run WORKDIR CMD... — THE run line (docker/buildfarm/Dockerfile header): capped, unprivileged, no
# network, read-only root, one bind mount. The orchestrator builds the same line from tools.env.
_tools_buildfarm_run() {
  local work="$1"; shift
  timeout -k 5 600 docker run --rm --network none \
    --memory "$BUILDFARM_MEMORY" --memory-swap "$BUILDFARM_MEMORY" --cpus "$BUILDFARM_CPUS" --pids-limit "$BUILDFARM_PIDS" \
    --cap-drop ALL --security-opt no-new-privileges --user "$TOOLS_ATLAS_UID:$TOOLS_ATLAS_GID" \
    --read-only --tmpfs "/tmp:rw,size=$BUILDFARM_TMPFS_SIZE" -v "$work:/work:rw" -w /work "$BUILDFARM_IMAGE" "$@"
}

_tools_buildfarm() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  local ctx="$ATLAS_DAY1_DIR/docker/buildfarm"
  [[ -f "$ctx/Dockerfile" ]] || die "$ctx/Dockerfile is missing"
  if [[ "$(docker image inspect -f '{{index .Config.Labels "org.atlas.buildfarm.version"}}' "$BUILDFARM_IMAGE" 2>/dev/null)" == "2" ]]; then
    log "$BUILDFARM_IMAGE already built"
  else
    local hp="" sp=""
    hp="$(awk -F= '$1=="CONTAINER_HTTP_PROXY" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
    sp="$(awk -F= '$1=="CONTAINER_HTTPS_PROXY" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
    [[ -n "$sp" ]] || die "CONTAINER_HTTPS_PROXY is empty in $ATLAS_ETC/docker.env (Phase 1 step 6): the image build (apt, Gradle, sdkmanager) cannot reach the allowlist proxy"
    echo
    echo "  NOTE (Section 16.3 item 2): building $BUILDFARM_IMAGE runs 'sdkmanager --licenses' and accepts the Android SDK"
    echo "  licence terms on your behalf. The text is at https://developer.android.com/studio/terms."
    echo
    log "docker build $BUILDFARM_IMAGE (Gradle 9.7.1, cmdline-tools 15859902, NDK r30, MinGW-w64; ~2 GB of downloads through the proxy; build user $TOOLS_ATLAS_UID:$TOOLS_ATLAS_GID)"
    docker build --pull -t "$BUILDFARM_IMAGE" \
      --build-arg "http_proxy=$hp" --build-arg "https_proxy=$sp" --build-arg "HTTP_PROXY=$hp" --build-arg "HTTPS_PROXY=$sp" \
      --build-arg "no_proxy=localhost,127.0.0.1" --build-arg "NO_PROXY=localhost,127.0.0.1" \
      --build-arg "ATLAS_UID=$TOOLS_ATLAS_UID" --build-arg "ATLAS_GID=$TOOLS_ATLAS_GID" \
      "$ctx" || die "docker build of $BUILDFARM_IMAGE failed (dl.google.com / services.gradle.org allowlisted? sdkmanager ids are UNVERIFIED build args)"
  fi
  # Smoke runs under the real run line: a throw-away work dir owned by the build uid.
  local work out
  work="$(mktemp -d "$TOOLS_CACHE_DIR/buildfarm-smoke.XXXXXX")"
  chown "$TOOLS_ATLAS_UID:$TOOLS_ATLAS_GID" "$work"
  out="$(_tools_buildfarm_run "$work" gradle --version 2>&1)" || { rm -rf "$work"; die "docker run $BUILDFARM_IMAGE gradle --version failed under the capped run line: ${out: -400}"; }
  out="$(grep -m1 -E '^Gradle ' <<<"$out")" || { rm -rf "$work"; die "no 'Gradle <version>' line from the buildfarm smoke run"; }
  log "smoke docker run buildfarm gradle --version: $out"
  out="$(_tools_buildfarm_run "$work" x86_64-w64-mingw32-gcc --version 2>&1)" || { rm -rf "$work"; die "x86_64-w64-mingw32-gcc --version failed inside $BUILDFARM_IMAGE: ${out: -400}"; }
  out="${out%%$'\n'*}"
  log "smoke buildfarm mingw: $out"
  rm -rf "$work"
  ensure_dir "$ATLAS_SRV/workspace" atlas:atlas 755
  _tools_kv BUILDFARM_IMAGE "$BUILDFARM_IMAGE"
  _tools_kv BUILDFARM_MEMORY "$BUILDFARM_MEMORY"
  _tools_kv BUILDFARM_CPUS "$BUILDFARM_CPUS"
  _tools_kv BUILDFARM_PIDS "$BUILDFARM_PIDS"
  _tools_kv BUILDFARM_TMPFS_SIZE "$BUILDFARM_TMPFS_SIZE"
  _tools_kv BUILDFARM_TIMEOUT_S "$BUILDFARM_TIMEOUT_S"
  _tools_kv BUILDFARM_UID "$TOOLS_ATLAS_UID"
  _tools_kv BUILDFARM_GID "$TOOLS_ATLAS_GID"
  _tools_kv BUILDFARM_WORK_ROOT "$ATLAS_SRV/workspace"
}

# --- 9. Docling (installed by step 04) ------------------------------------------------------------------------------------------
_tools_docling_assert() {
  "$TOOLS_VENV/bin/python" -c 'import importlib.metadata as m; print("docling", m.version("docling"))' \
    || die "docling is not in $TOOLS_VENV: phase2/04-memory.sh installs it (Section 17 order: step 4 before 6)"
  log "smoke docling: installed by step 04 (venv $TOOLS_VENV; docling-serve container from compose.voice.yml, step 05)"
}

step_06() {
  _tools_paths
  ensure_dir "$TOOLS_CACHE_DIR" root:root 755
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
  if [[ "$MCP4IFC_STATUS" != installed ]]; then
    warn "SUMMARY step 06: MCP4IFC_STATUS=$MCP4IFC_STATUS (yellow, research conflict 5): the Section 15.1 row is served by IfcMCP ($TOOLS_VENV/bin/ifcmcp) until the Principal decides; recorded in $TOOLS_ENV_FILE for the gate table"
  fi
  log "step 06 done: Section 15.1 tools installed; paths in $TOOLS_ENV_FILE (MCP4IFC_STATUS=$MCP4IFC_STATUS)"
  notify "Phase 2 step 6 done: IfcOpenShell, Bonsai, MCP4IFC ($MCP4IFC_STATUS), Radiance, EnergyPlus, OpenStudio, KiCad, Playwright, buildfarm"
}
