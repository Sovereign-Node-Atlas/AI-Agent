#!/usr/bin/env bash
# phase2/06-tools.sh — Section 17 Phase 2 step 6: the Section 15.1 tools (IfcOpenShell, Bonsai, MCP4IFC, Radiance,
# OpenStudio/EnergyPlus, KiCad CLI, Playwright, the cross-platform build container). Sourced by phase2-services.sh
# through run_phase_steps; defines step_06 only. Every tool ends with a one-line smoke test that is logged.
#
# Docling (15.1 "Document conversion") is NOT installed here: phase2/04-memory.sh installs docling 2.129.0 into
# /opt/atlas/venv with the models prefetched, and docker/core/compose.voice.yml runs docling-serve for Open WebUI
# (step 5). This step only asserts the venv import so the 15.1 table is complete.
#
# HARD AND SOFT INSTALLS (fix round 5; CONVENTIONS.md §7.4 "fail loudly, never silently", §7.10 the Principal's time).
# The installs whose inputs are VERIFIED pins (IfcOpenShell/IfcMCP from PyPI, KiCad from apt, Playwright from PyPI and
# its VERIFIED CDN, Radiance's sha256-fixed zip) stay HARD: a failure stops the phase with its line, as before. The
# installs whose inputs are UNVERIFIED (the Blender 4.5.N patch chosen from a live listing, Bonsai through the UNVERIFIED
# extensions.blender.org API, the OpenStudio/EnergyPlus 24.04 builds on 26.04, the research-code MCP4IFC checkout, the
# buildfarm image with its UNVERIFIED sdkmanager ids) run under _tools_soft: a failure inside one of them is caught in a
# subshell, recorded in verify.jsonl as a DEFERRED row with the id T-<tool> (the same JSON shape record_v writes, tool
# name and the last error line in the message), logged with the exact `--force 06` re-run command, and the step CONTINUES
# with the next tool and with step 6b, instead of parking the whole phase on an upstream listing or API that moved. A tool
# whose prerequisite was deferred (Bonsai needs Blender, MCP4IFC needs Blender and Bonsai) is deferred without an attempt,
# naming the prerequisite. The step MARKER is written only when every HARD install succeeded (run_step: the function
# returns 0 once the hard part is done); the T-rows are printed by phase2/10-gate.sh as an optional, non-blocking table
# (latest record per id). A soft install that SUCCEEDS on a later `--force 06` re-run after a deferred row therefore
# records a superseding `pass` row for the same T-id (fix round 6; nothing is written when no earlier row exists), so
# the gate's table and its `--status` reminder stop naming a tool that is installed. The stderr of a soft install stays
# stderr (the _atlas_emit split of the rest of the phase): only that stream is captured for the last-error line.
# The Android SDK licence check stays HARD (below): it is the Principal's decision, not an upstream failure.
# T-ids are written by _tools_record_t, a local twin of lib/common.sh record_v, because record_v's ID regex
# (^V[0-9]+[a-z]?$) rejects them; request (lib/common.sh writer, and CONVENTIONS §4 which lists V ids only although
# Section 21's scope notes name `T-<tool>` rows): accept `T-[a-z0-9-]+` (and `P4-wheels`) there and this twin goes.
#
# Facts typed from services-tools.md §4 and S10/S11 (VERIFIED unless marked UNVERIFIED in the code); adjudicated
# conflicts honoured: Blender is the 4.5 LTS tarball under /opt/blender (conflict 18), never apt's 5.0.1; Radiance
# comes from LBNL-ETA (research conflict 1); OpenStudio/EnergyPlus from NatLabRockies (research conflict 2). MCP4IFC
# (fix round 3): Section 15.1 lists it as a Phase 2 tool "confirmed real and GPU-independent"; services-tools.md's
# research conflict 5 proposes treating it as yellow, but that conflict is NOT among the adjudicated ones and
# CONVENTIONS §7.4's yellow semantics belong to Section 15.2 Phase 4 engines only, so the baseline wins on the VERDICT:
# an MCP4IFC install failure is never silently a pass. Since fix round 5 it is one of the SOFT installs (HARD AND SOFT
# INSTALLS below): the failing line is logged, a `T-mcp4ifc` deferred row is recorded with the last error and the
# `--force 06` re-run command, and the step continues to 6b instead of parking the whole phase on unverified upstream
# code (the Principal can still amend 15.1 by moving the row to 15.2 yellow, a request recorded in the notes for
# phase2/README-contracts.md).
#
# WHO RUNS WHAT (fix round 2):
#   * Root installs packages and extracts tarballs; every archive is unpacked with --no-same-owner --no-same-permissions,
#     then chowned root:root and chmodded u=rwX,go=rX, and `find ! -user root` must be empty before the tree is put in
#     place (an upstream archive carrying uid 1000 would otherwise hand the Principal's login user write access to
#     binaries root runs through /etc/profile.d and /usr/local/bin).
#   * /opt/atlas/venv (the orchestrator venv) is root:atlas, group/other WITHOUT write (the invariant steps 02 and 04
#     establish for Section 16.3 item 6: the atlas account never owns the interpreter or the site-packages it runs,
#     and root never executes an atlas-writable file). It is re-asserted here BEFORE root runs pip or any import from
#     it (a venv an earlier revision chowned to atlas is re-owned first) and again at the end of the step. ifcopenshell,
#     ifcmcp and playwright import from it read-only; the browser tree ($ATLAS_OPT/playwright) is root a+rX.
#   * Blender extensions and preferences are PER USER (~/.config/blender/4.5). Every `blender -b` that installs, enables
#     or imports Bonsai/MCP4IFC runs as the atlas account (the orchestrator's identity, CONVENTIONS §8) with its own
#     HOME, always with --offline-mode (extensions.blender.org is allowlisted only for the one-time Bonsai download;
#     Section 12.1 "update checks disabled") and ALWAYS with --python-exit-code 1: without it Blender exits 0 after a
#     Python exception (creator_args: the exit code on a Python error is 0 unless set), so every probe would be a false
#     positive. Probes are positive as well (a printed marker is grepped), so a wrong default can never pass again.
#     tools.env records BLENDER_ARGS and BLENDER_USER_CONFIG for the orchestrator.
#   * MCP4IFC is unpinned upstream research code: it is checked out at MCP4IFC_COMMIT (a fixed 40-hex pin). Its git
#     operations, `uv sync` and install scripts run as atlas under $ATLAS_OPT/tools while the checkout is atlas-owned,
#     and the checkout plus its .venv are re-owned root:atlas go-w once that work is done (the orchestrator spawns the
#     server from it read-only; the next run hands it back to atlas for the duration of the git/uv work only).
#   * Downloads are transient and go to /var/cache/atlas/downloads on the OS drive (never $ATLAS_STATE, which restic
#     backs up, and never $ATLAS_SRV, the atlas-owned data volume); sha256 pins where known, the rest recorded.
#   * The buildfarm image runs as a non-root build user and THE run line is executed by a ROOT-OWNED WRAPPER,
#     /usr/local/sbin/atlas-buildfarm-run JOBDIR CMD..., written by this step: it reads the BUILDFARM_* keys from
#     tools.env itself, accepts only a job directory whose realpath is under BUILDFARM_WORK_ROOT, interprets no other
#     argument, names the container buildfarm-<basename JOBDIR>, runs it with --init, and on the host timeout (exit
#     124/137) kills the container by name (a `timeout` around the docker client alone bounds the client, not the
#     build). /etc/sudoers.d/atlas-buildfarm grants atlas exactly that wrapper (visudo-checked, like atlas-engines), so
#     the orchestrator never needs the docker socket for builds; the Phase 1 writer can then drop atlas from the docker
#     group (recorded for phase2/README-contracts.md; CONVENTIONS §8 should list this fragment beside atlas-engines and
#     atlas-vault). The smoke run goes through the wrapper via `sudo -n` as atlas, so the whole path is proven.
#   * ANDROID SDK LICENCE (Section 16.3 item 2: terms are accepted by the Principal, never by a script on their behalf):
#     the image build runs `sdkmanager --licenses` ONLY when BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes is set in
#     /etc/atlas/atlas.env (the Principal reads https://developer.android.com/studio/terms and sets the key; no pause,
#     CONVENTIONS §7.6). Without it the step STOPS (fix round 3, major: the build container is a Section 17 step 6
#     deliverable, and a WARN-and-succeed left it silently unbuilt on a default run, with no V item or gate row to
#     notice): BUILDFARM_STATUS=licence-not-accepted goes into tools.env and `die` prints the one-line fix; the marker
#     is not written, so the next phase2 run resumes here. This check is HARD and runs BEFORE the soft buildfarm build
#     (fix round 5). The Dockerfile refuses to run sdkmanager without the matching build arg. The key is a Principal key
#     (CONVENTIONS §3, policy v0.3.3): load_env asks for it once with the terms URL and records a to-do when it is left
#     blank; phase2-services.sh's minute-0 input check lists it again, and the build is deferred, never a stop.
#   * APT PINS are strict (fix round 3; rule §7.9): the Dockerfile has no fallback to the archive's current versions, so
#     a moved-on archive fails the image build and this step dies naming the two pins to bump deliberately
#     (BUILDFARM_JDK_PIN, BUILDFARM_MINGW_PIN, mirrored in the Dockerfile and its version label). After a build the
#     dpkg record /opt/buildfarm/versions.txt is read back into tools.env (BUILDFARM_JDK_VERSION, BUILDFARM_MINGW_VERSION,
#     BUILDFARM_APT_DRIFT) and compared with the pins; a mismatch is a stop, never a note.
#   * DOCKER GROUP (fix round 3, major): the wrapper, its sudoers fragment and the Section 16.4 caps bind the BUILD, not
#     the caller: while the atlas account is in the docker group (phase1/06-docker.sh `usermod -aG docker,render,video
#     atlas`) it can bypass all of it with `docker run --privileged -v /:/host` or by retagging atlas-buildfarm:1. The
#     step measures that at run time: BUILDFARM_CAPS_ENFORCED=no (WARN here and in the step summary) while the membership
#     exists, yes once it is dropped. REQUEST (Phase 1 writer, phase2/README-contracts.md): remove `docker` from the
#     usermod line once the AEGIS sandbox path has its own root-owned wrapper like atlas-buildfarm-run (the compose calls
#     in phase2/05-voice.sh already run as root).
#
# UNPINNED / PINNED HERE (rule §7.9; scripts/day1/README.md does not exist yet, so the disclosure lives here and is
# repeated for phase2/README-contracts.md): uv is pinned (UV_PIN, shared with phase2/05-voice.sh); the Blender 4.5.N
# PATCH is chosen from the live release listing (download.blender.org was blocked in research and in this round, so no
# sha256 can be typed here) and the choice is recorded LOUDLY (log line + BLENDER_VERSION + BLENDER_TARBALL_SHA256 in
# tools.env; the published blender-4.5.N.sha256 is verified when present); the Bonsai version comes from the extensions
# API for the installed Blender (BONSAI_VERSION recorded). Everything else is pinned or sha256-fixed.
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * /opt/atlas/venv ($ATLAS_OPT/venv) is the orchestrator venv (step 02; step 04 creates it when absent, and so does
#     this step, logged). ifcopenshell, ifcopenshell-mcp and playwright go there so the orchestrator's MCP client and
#     browser tool import them directly (services-tools.md S10).
#   * config/allowlist.txt: github.com + release-assets/objects.githubusercontent.com, download.blender.org,
#     extensions.blender.org, dl.google.com, services.gradle.org, pypi.org, files.pythonhosted.org, and the Playwright
#     browser CDN hosts cdn.playwright.dev and playwright.download.prss.microsoft.com (VERIFIED 2026-10-04 in
#     playwright v1.63.0 packages/playwright-core/src/server/registry/index.ts, PLAYWRIGHT_CDN_MIRRORS; the
#     playwright.azureedge.net host the allowlist still carries is retired and the allowlist writer is asked to replace
#     it with playwright.download.prss.microsoft.com, otherwise a cdn.playwright.dev outage falls through to a denied
#     mirror).
#   * $ATLAS_ETC/docker.env (Phase 1 step 6): CONTAINER_HTTP_PROXY / CONTAINER_HTTPS_PROXY, ATLAS_UID, ATLAS_GID.
#   * $ATLAS_OPT/python (phase2/05-voice.sh, step 5 before 6): the uv-managed CPython 3.11 MCP4IFC's venv is built on.
#   * /var/cache/atlas (phase2/04-memory.sh): the cache root for pip/uv/downloads.
#   * /etc/atlas/atlas.env (load_env exports every key): BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE (see above).
# Contract this file defines for others:
#   * $ATLAS_ETC/tools.env (root:atlas 640): BLENDER_BIN, BLENDER_VERSION, BLENDER_TARBALL_SHA256, BLENDER_ARGS,
#     BLENDER_USER_CONFIG, BONSAI_VERSION, RADIANCE_BIN, RAYPATH, ENERGYPLUS_BIN, OPENSTUDIO_BIN, KICAD_CLI,
#     PLAYWRIGHT_BROWSERS_PATH, TOOLS_VENV, IFCMCP_BIN, MCP4IFC_DIR, MCP4IFC_COMMIT, MCP4IFC_STATUS (installed when the
#     soft install completed; uv-sync-failed / import-failed / deferred behind a T-mcp4ifc row), MCP4IFC_PYTHON,
#     MCP4IFC_ADDON_ZIP, MCP4IFC_BLENDER_PACKAGES (not-installed: see _tools_mcp4ifc_work), BUILDFARM_STATUS (built;
#     licence-not-accepted only behind a died step; deferred behind a T-buildfarm row), TOOLS_DEFERRED (the T-tools of the
#     last run, space-separated, or none), BUILDFARM_CAPS_ENFORCED (yes | no, see DOCKER GROUP), BUILDFARM_RUN
#     (the wrapper), BUILDFARM_IMAGE, BUILDFARM_MEMORY, BUILDFARM_CPUS, BUILDFARM_PIDS, BUILDFARM_TMPFS_SIZE,
#     BUILDFARM_TIMEOUT_S, BUILDFARM_UID, BUILDFARM_GID, BUILDFARM_WORK_ROOT, BUILDFARM_JDK_VERSION,
#     BUILDFARM_MINGW_VERSION, BUILDFARM_APT_DRIFT, UV_VERSION. Sourceable KEY=VALUE lines. phase2/10-gate.sh does not
#     read this file (grep confirms): nothing here claims a gate row.
#   * /usr/local/sbin/atlas-buildfarm-run JOBDIR CMD... (root 755) and /etc/sudoers.d/atlas-buildfarm: the orchestrator
#     runs builds as `sudo -n /usr/local/sbin/atlas-buildfarm-run $BUILDFARM_WORK_ROOT/<job> gradle assembleRelease`;
#     exit status is the container's (124/137 = killed at BUILDFARM_TIMEOUT_S), 64 = refused argument.

[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

IFCOPENSHELL_PIN="ifcopenshell==0.8.5"             # services-tools.md §4.1 VERIFIED (py314 manylinux wheel exists)
IFCMCP_PIN="ifcopenshell-mcp[mcp]==0.8.5"          # §4.1 VERIFIED (PyPI 2026-04-01)
PLAYWRIGHT_PIN="playwright==1.63.0"                # §4.7 VERIFIED (ubuntu26.04-x64 dependency map at v1.63.0)
UV_PIN="0.12.23"                                   # PyPI release current on 2026-10-04 (VERIFIED); same pin as 05-voice.sh
BLENDER_SERIES="4.5"                               # adjudicated conflict 18: 4.5 LTS
BLENDER_RELEASE_URL="https://download.blender.org/release/Blender4.5/"
# Every headless call: offline (Blender 4.2+; UNVERIFIED on 4.5: the version call dies if rejected) AND a non-zero exit
# on a Python exception (`--python-exit-code <code>`, "zero disables" per `blender --help`; the default IS zero).
BLENDER_HEADLESS=(-b --offline-mode --python-exit-code 1)
BLENDER_ARGS="--offline-mode --python-exit-code 1"  # the same flags for the orchestrator (tools.env), as one string
# The Bonsai probe, ONE text for the already-installed check and the post-install proof (so re-runs agree). Blender 4.2+
# loads an extension as bl_ext.<repo_module>.bonsai; whether an enabled Bonsai also aliases a top-level `bonsai` module
# is UNVERIFIED (services-tools.md §4.2: "the pattern"; MCP4IFC's own add-on does `from bonsai import tool`, read at
# MCP4IFC_COMMIT), so every spelling is tried (the alias, every loaded *.bonsai module, bl_ext.<repo>.bonsai for each
# configured repository) and the one that imported is printed after the marker. Positive proof: Blender exit 0 AND the
# marker (fix round 3).
read -r -d '' TOOLS_BONSAI_PROBE <<'PYPROBE' || true
import importlib, sys, bpy
cands = ["bonsai"] + sorted(k for k in list(sys.modules) if k.endswith(".bonsai"))
try:
    cands += ["bl_ext.%s.bonsai" % r.module for r in bpy.context.preferences.extensions.repos]
except Exception:
    pass
mod = None
for name in cands:
    try:
        mod = importlib.import_module(name)
        break
    except ImportError:
        pass
if mod is None:
    raise ImportError("bonsai is not importable under any of %s" % cands)
importlib.import_module(mod.__name__ + ".tool")
import ifcopenshell
print("BONSAI_OK", mod.__name__, ifcopenshell.version)
PYPROBE
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
BUILDFARM_IMAGE_REVISION="4"                       # org.atlas.buildfarm.version label of docker/buildfarm/Dockerfile (4: strict apt pins)
BUILDFARM_MEMORY="8g"                              # Section 16.4-style caps for the buildfarm run line (header)
BUILDFARM_CPUS="4"
BUILDFARM_PIDS="1024"
BUILDFARM_TMPFS_SIZE="2g"
BUILDFARM_TIMEOUT_S="3600"
BUILDFARM_JDK_PIN="21.0.12.1+1-1~26.04.4"         # services-tools.md §4.8 VERIFIED (packages.ubuntu.com/resolute)
BUILDFARM_MINGW_PIN="13.2.0-6ubuntu1+26.1"         # §4.8 VERIFIED (gcc/g++-mingw-w64-x86-64)
BUILDFARM_WRAPPER="/usr/local/sbin/atlas-buildfarm-run"
BUILDFARM_SUDOERS="/etc/sudoers.d/atlas-buildfarm"
ANDROID_TERMS_URL="https://developer.android.com/studio/terms"
TOOLS_CACHE_DIR="/var/cache/atlas"

TOOLS_VENV=""
TOOLS_STAGING=""
TOOLS_ENV_FILE=""
TOOLS_UV=""
TOOLS_ATLAS_HOME=""
TOOLS_ATLAS_UID=""
TOOLS_ATLAS_GID=""
BUILDFARM_STATUS="not-attempted"
BUILDFARM_CAPS_ENFORCED="unknown"

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
  # Symlinks are excluded from the mode test (their own mode is always 0777; chmod -R never changes them, chown -R
  # re-owns the link itself and never follows it outside the tree).
  stray="$(find "$dir" ! -type l \( ! -user root -o -perm /o+w \) 2>/dev/null | head -n 3 || true)"
  [[ -z "$stray" ]] || die "$dir still has non-root or world-writable entries after the fix: $(tr '\n' ' ' <<<"$stray")"
}

# _tools_venv_harden — the step-02/04 invariant for /opt/atlas/venv: root:atlas, no group/other write, asserted. Called
# before root executes anything from the venv and again at the end of the step (Section 16.3 items 5 and 6).
_tools_venv_harden() {
  local stray
  chown -R root:atlas "$TOOLS_VENV"
  chmod -R go-w "$TOOLS_VENV"
  stray="$(find "$TOOLS_VENV" ! -type l \( ! -user root -o -perm /022 \) 2>/dev/null | head -n 3 || true)"
  [[ -z "$stray" ]] || die "$TOOLS_VENV still has non-root or group/world-writable entries after the fix (Section 16.3 item 6): $(tr '\n' ' ' <<<"$stray")"
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
  # Re-own BEFORE the first execution: a venv an earlier revision chowned to atlas must never run as root.
  _tools_venv_harden
  [[ -x "$TOOLS_VENV/bin/pip" ]] || "$TOOLS_VENV/bin/python" -m ensurepip --upgrade || die "$TOOLS_VENV has no pip"
}

_tools_pip() {
  proxy_env
  ensure_dir "$TOOLS_CACHE_DIR/pip" root:root 755
  export PIP_CACHE_DIR="$TOOLS_CACHE_DIR/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
  retry 3 "$TOOLS_VENV/bin/python" -m pip install --quiet "$@"
  _tools_venv_harden     # new site-packages files take the invariant immediately
}

# --- Soft installs (header: HARD AND SOFT INSTALLS) ---------------------------------------------------------------------
TOOLS_DEFERRED_TOOLS=()   # the T-tools deferred in this run, in order

# _tools_kv_get KEY — the value of KEY in tools.env (the soft installs run in a subshell, so their status lives there).
_tools_kv_get() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$TOOLS_ENV_FILE" 2>/dev/null || true; }

# _tools_record_t TOOL RESULT MSG — one verify.jsonl line {"ts","phase","id":"T-<tool>","result","msg"}, the shape
# record_v writes (lib/common.sh), produced by python3's json module like record_v does; RESULT is deferred (the install
# failed) or pass (a later re-run installed it: the superseding row the gate's latest-per-id table needs, header). A
# local twin because record_v refuses ids outside ^V[0-9]+[a-z]?$ (header).
_tools_record_t() {
  local tool="$1" result="$2" msg="$3" line
  case "$result" in deferred|pass) ;; *) die "_tools_record_t T-$tool: RESULT must be deferred|pass, got '$result'" ;; esac
  _atlas_state_init
  line="$(python3 -c '
import json, sys
print(json.dumps({"ts": sys.argv[1], "phase": sys.argv[2], "id": sys.argv[3], "result": sys.argv[4], "msg": sys.argv[5]}))
' "$(date -Is)" "$ATLAS_PHASE" "T-$tool" "$result" "$msg")"
  printf '%s\n' "$line" >>"$ATLAS_VERIFY_FILE"
  log "verify T-$tool=$result: $msg"
}

# _tools_has_t_row TOOL — true when verify.jsonl already carries a T-<tool> row (an earlier deferred install).
_tools_has_t_row() {
  [[ -f "$ATLAS_VERIFY_FILE" ]] || return 1
  python3 - "$ATLAS_VERIFY_FILE" "T-$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    for line in fh:
        try:
            if json.loads(line).get("id") == sys.argv[2]:
                sys.exit(0)
        except ValueError:
            continue
sys.exit(1)
PY
}

# _tools_soft TOOL FUNC [PREREQ_TOOL...] — run FUNC (an UNVERIFIED-input install) in a subshell with errexit and the ERR
# trap live inside it; a failure (die, or any failing command) records T-<tool> deferred with the last error line, logs
# the re-run command and returns 0 so the step continues. A PREREQ_TOOL that was deferred in this run defers TOOL
# without an attempt. The subshell's stdout goes to the console unchanged; its STDERR is tee'd to the console's stderr
# (the _atlas_emit split: WARN/ERROR/FATAL and tracebacks stay on stderr, fix round 6) and to a temp file the last
# error line is taken from (every line that grep looks for is a stderr line). No process substitution: bash does not
# wait for one, so the file could be read before tee had flushed; the brace group's fd 3 carries stdout past the
# pipeline instead. `set +e` around the pipeline: inside an `if`/`||` bash would switch errexit OFF in the subshell too
# and FUNC would run past its first failing command. The parent's ERR trap is parked for the pipeline (it would name
# `tee` as the failing command) and re-armed INSIDE the subshell, so the failing line of FUNC is still logged by the
# usual "command failed (exit N) at ..." line. On success after an earlier T-row, a superseding pass row (header).
_tools_soft() {
  local tool="$1" func="$2"; shift 2
  local p entry="${ATLAS_ENTRY:-./atlas-day1.sh}" saved_trap
  for p in "$@"; do
    if [[ " ${TOOLS_DEFERRED_TOOLS[*]} " == *" $p "* ]]; then
      TOOLS_DEFERRED_TOOLS+=("$tool")
      _tools_record_t "$tool" deferred "$tool not attempted: prerequisite $p deferred in this run; re-run after fixing it: sudo $entry phase2 --force 06"
      warn "DEFERRED $tool: prerequisite $p was deferred; re-run: sudo $entry phase2 --force 06"
      return 0
    fi
  done
  local outf rc=0 last
  outf="$(mktemp)"
  log "soft install $tool ($func): UNVERIFIED inputs, a failure records T-$tool deferred and the step continues"
  saved_trap="$(trap -p ERR)"
  set +e; trap - ERR
  # stdout -> fd 3 (the console's stdout, untouched); stderr -> the pipe -> tee -> the file and the console's stderr.
  { ( eval "${saved_trap:-:}"; set -e; "$func" ) 2>&1 >&3 | tee -a "$outf" >&2; rc="${PIPESTATUS[0]}"; } 3>&1
  eval "${saved_trap:-:}"; set -e
  if (( rc == 0 )); then
    rm -f "$outf"
    if _tools_has_t_row "$tool"; then
      _tools_record_t "$tool" pass "$tool installed on $(date -I) (re-run after an earlier deferred row; supersedes it in the gate table)"
    fi
    return 0
  fi
  last="$(grep -E ' (FATAL|ERROR|WARN) ' "$outf" | tail -n1 | cut -c1-400 || true)"
  [[ -n "$last" ]] || last="$(tail -n1 "$outf" | cut -c1-400 || true)"
  rm -f "$outf"
  TOOLS_DEFERRED_TOOLS+=("$tool")
  _tools_record_t "$tool" deferred "$tool install failed (exit $rc); last error: ${last:-(no output)}; re-run after fixing the cause: sudo $entry phase2 --force 06"
  warn "DEFERRED $tool (exit $rc): ${last:-(no output)}. The step continues; re-run after fixing the cause: sudo $entry phase2 --force 06"
  return 0
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
# _tools_blender_version BIN -> the first line of `blender -b --offline-mode --python-exit-code 1 --version` (capture
# first, then cut: a `| head -n1` pipeline would SIGPIPE Blender's later output and turn success into 141 under pipefail).
# Runs AS ATLAS like every other Blender call (fix round 3): the binary's patch level is chosen from the live listing
# and hashed against the same host, so root never executes it (header: root runs pinned or root-authored code only).
_tools_blender_version() {
  local out
  out="$(_tools_as_atlas "$1" "${BLENDER_HEADLESS[@]}" --version 2>&1)" || return 1
  printf '%s\n' "${out%%$'\n'*}"
}

# _tools_blender_atlas ARGS... — a headless Blender run as atlas (per-user extensions and preferences), offline, and
# failing (exit 1) on any Python exception.
_tools_blender_atlas() {
  _tools_as_atlas /opt/blender/blender "${BLENDER_HEADLESS[@]}" "$@"
}

# _tools_blender_probe EXPR MARKER — run EXPR as atlas; succeeds only when Blender exits 0 AND MARKER was printed
# (positive proof: a wrong --python-exit-code default alone can never produce a false pass). Output in TOOLS_PROBE_OUT.
TOOLS_PROBE_OUT=""
_tools_blender_probe() {
  local expr="$1" marker="$2"
  TOOLS_PROBE_OUT="$(_tools_blender_atlas --python-expr "$expr" 2>&1)" || return 1
  grep -q -- "$marker" <<<"$TOOLS_PROBE_OUT"
}

_tools_blender() {
  local bin="/opt/blender/blender" line=""
  if [[ -x "$bin" ]] && line="$(_tools_blender_version "$bin")" && [[ "$line" == "Blender $BLENDER_SERIES."* ]]; then
    log "Blender already installed: $line"
  else
    proxy_env
    # UNVERIFIED: the exact 4.5.x patch level (download.blender.org was blocked during research and in the fix rounds,
    # so no sha256 can be typed here). The release directory listing is parsed for the highest
    # blender-4.5.N-linux-x64.tar.xz, its published blender-4.5.N.sha256 is used when present, and the choice is
    # recorded loudly (log + tools.env). §7.9 disclosure in the header.
    local listing matches file ver
    listing="$(curl -fsSL --max-time 60 "$BLENDER_RELEASE_URL")" || die "could not list $BLENDER_RELEASE_URL (download.blender.org allowlisted?)"
    # grep exits 1 on no match: captured with `|| true` so the die below (not the ERR trap) names the problem.
    matches="$(grep -oE "blender-$BLENDER_SERIES\.[0-9]+-linux-x64\.tar\.xz" <<<"$listing" || true)"
    file="$(sort -t. -k3,3n <<<"$matches" | uniq | tail -n1 || true)"
    [[ -n "$file" ]] || die "no blender-$BLENDER_SERIES.N-linux-x64.tar.xz in $BLENDER_RELEASE_URL (layout changed?)"
    ver="$(sed -E 's/^blender-([0-9.]+)-linux-x64\.tar\.xz$/\1/' <<<"$file")"
    warn "blender: UNPINNED patch level (rule §7.9, see header): the listing's newest 4.5 build is $ver; recorded in $TOOLS_ENV_FILE as BLENDER_VERSION / BLENDER_TARBALL_SHA256"
    local sha="none" shafile="$TOOLS_STAGING/blender-$ver.sha256"
    if curl -fsSL --max-time 60 -o "$shafile" "${BLENDER_RELEASE_URL}blender-$ver.sha256" 2>/dev/null; then
      sha="$(awk -v f="$file" '$2==f || $2=="*"f {print $1; exit}' "$shafile")"
      [[ -n "$sha" ]] || { warn "blender-$ver.sha256 does not list $file"; sha="none"; }
    else
      warn "no blender-$ver.sha256 beside the tarball (UNVERIFIED layout); the computed hash will be recorded"
    fi
    _tools_dl "$BLENDER_RELEASE_URL$file" "$TOOLS_STAGING/$file" "$sha"
    _tools_kv BLENDER_TARBALL_SHA256 "$(sha256sum "$TOOLS_STAGING/$file" | cut -d' ' -f1)"
    log "installing Blender $ver into /opt/blender (root-owned, read-only for everyone else)"
    _tools_extract_tar "$TOOLS_STAGING/$file" /opt/blender.new
    rm -rf /opt/blender && mv /opt/blender.new /opt/blender
  fi
  ln -sfn "$bin" /usr/local/bin/blender
  _tools_root_only /opt/blender
  line="$(_tools_blender_version "$bin")" || die "blender ${BLENDER_HEADLESS[*]} --version failed (are --offline-mode and --python-exit-code accepted by this Blender?)"
  [[ "$line" == "Blender $BLENDER_SERIES."* ]] || die "unexpected Blender version line: $line"
  log "smoke blender --version: $line"
  _tools_kv BLENDER_BIN "$bin"
  _tools_kv BLENDER_VERSION "${line#Blender }"
  _tools_kv BLENDER_ARGS "$BLENDER_ARGS"
  _tools_kv BLENDER_USER_CONFIG "$TOOLS_ATLAS_HOME/.config/blender"
  # The atlas account owns its Blender config; the online-access preference is saved off once (UNVERIFIED attribute
  # name on 4.5, warn-only: --offline-mode on every call is the enforced control). Positive probe: exit 0 AND marker.
  ensure_dir "$TOOLS_ATLAS_HOME/.config" atlas:atlas 700
  if _tools_blender_probe 'import bpy; bpy.context.preferences.system.use_online_access = False; bpy.ops.wm.save_userpref(); print("PREF_OK")' PREF_OK; then
    log "blender: use_online_access=False saved in $TOOLS_ATLAS_HOME/.config/blender (atlas)"
  else
    warn "blender: could not save use_online_access=False for atlas (UNVERIFIED preference name): ${TOOLS_PROBE_OUT: -200}; every call passes $BLENDER_ARGS regardless"
  fi
}

_tools_bonsai() {
  local bin="/opt/blender/blender" bver
  bver="$(_tools_blender_version "$bin" | awk '{print $2}')"
  if _tools_blender_probe "$TOOLS_BONSAI_PROBE" BONSAI_OK; then
    log "Bonsai already importable in Blender $bver for atlas: $(grep -m1 BONSAI_OK <<<"$TOOLS_PROBE_OUT")"
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
    warn "bonsai: version ${ver:-?} chosen by the extensions API for Blender $bver (UNPINNED, rule §7.9, see header); recorded as BONSAI_VERSION"
    log "installing Bonsai ${ver:-?} into Blender repo '$repo' for atlas (offline install-file)"
    _tools_blender_atlas --command extension install-file "$TOOLS_STAGING/bonsai-linux-x64.zip" --repo "$repo" --enable \
      || die "blender --command extension install-file failed for Bonsai as atlas (is the zip the linux-x64 build for Blender $bver?)"
    _tools_kv BONSAI_VERSION "${ver:-manual}"
  fi
  _tools_blender_probe "$TOOLS_BONSAI_PROBE" BONSAI_OK \
    || die "Bonsai does not import inside Blender $bver as atlas after the install (no BONSAI_OK line; tried bonsai, *.bonsai, bl_ext.<repo>.bonsai): ${TOOLS_PROBE_OUT: -300}"
  log "smoke bonsai (inside Blender $bver, as atlas): $(grep -m1 BONSAI_OK <<<"$TOOLS_PROBE_OUT")"
}

# --- 3. MCP4IFC ---------------------------------------------------------------------------------------------------------
_tools_uv() {
  local bv="$ATLAS_OPT/venv-uv"
  if [[ ! -x "$bv/bin/uv" ]]; then
    python3 -m venv "$bv" || die "python3 -m venv $bv failed"
  fi
  # Pinned (rule §7.9; the same UV_PIN as phase2/05-voice.sh, which normally brings the shared venv to it first).
  if [[ "$("$bv/bin/uv" --version 2>/dev/null | awk '{print $2}')" != "$UV_PIN" ]]; then
    proxy_env
    log "venv-uv: installing uv==$UV_PIN into $bv (was: $("$bv/bin/uv" --version 2>/dev/null || echo none))"
    retry 3 "$bv/bin/python" -m pip install --quiet --disable-pip-version-check "uv==$UV_PIN" || die "pip install uv==$UV_PIN failed"
    [[ "$("$bv/bin/uv" --version 2>/dev/null | awk '{print $2}')" == "$UV_PIN" ]] || die "uv in $bv is not $UV_PIN after the install"
  fi
  TOOLS_UV="$bv/bin/uv"
  chmod -R a+rX "$bv"
  _tools_kv UV_VERSION "$UV_PIN"
  ensure_dir "$TOOLS_CACHE_DIR/uv-atlas" atlas:atlas 755      # uv cache for the runs made as atlas
}

MCP4IFC_STATUS="not-attempted"

# _tools_mcp4ifc_harden DIR — once the atlas-side git/uv work is done: root:atlas, no group/other write (the orchestrator
# spawns the server from it read-only, Section 16.3 item 6). The next run hands it back to atlas for the git/uv work.
_tools_mcp4ifc_harden() {
  local dir="$1"
  [[ -d "$dir" ]] || return 0
  chown -R root:atlas "$dir"
  chmod -R go-w "$dir"
  chown root:root "$ATLAS_OPT/tools"; chmod 755 "$ATLAS_OPT/tools"
  log "MCP4IFC: $dir re-owned root:atlas, read-only for atlas"
}

_tools_mcp4ifc() {
  local dir="$ATLAS_OPT/tools/ifc-bonsai-mcp"
  _tools_mcp4ifc_work "$dir"
  _tools_mcp4ifc_harden "$dir"
}

_tools_mcp4ifc_work() {
  # Section 15.1 names MCP4IFC a confirmed Phase 2 tool (header: the research's "yellow" is not adjudicated, so every
  # failure below is a die -- caught by _tools_soft as T-mcp4ifc deferred with its line, fix round 5). The upstream code is a research artefact bound to a live
  # Blender + Bonsai GUI session; what Day 1 proves is what can be proven headlessly: the pinned checkout, its venv, the
  # MCP server import, the Blender add-on zip and its headless enable for atlas. Everything below runs as atlas on the
  # pinned commit, in an atlas-owned checkout for the duration of this function only (the caller re-owns it root:atlas).
  # The headless official IfcMCP (installed above) delivers LLM-driven IFC editing regardless.
  local dir="$1"
  _tools_uv
  proxy_env
  # Atlas needs to create the clone on the first run and to write .venv/.git on every run: hand the tree to atlas now.
  ensure_dir "$ATLAS_OPT/tools" atlas:atlas 755
  if [[ -d "$dir" && "$(stat -c %U "$dir")" != atlas ]]; then
    log "MCP4IFC: $dir is owned by $(stat -c %U "$dir"); handing it to atlas for the git/uv work (re-owned root:atlas afterwards)"
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
  # pyproject.toml at MCP4IFC_COMMIT (read 2026-10-04): no uv.lock in the repo, dependencies include sentence-transformers
  # (pulls torch, several GB from pypi.org), langchain and the `anthropic` client LIBRARY (an import-only dependency of
  # upstream's standalone client; nothing here calls it, api.anthropic.com is on the allowlist's never-list, rule §7.1).
  local uvenv=(env UV_CACHE_DIR="$TOOLS_CACHE_DIR/uv-atlas" UV_PYTHON_INSTALL_DIR="$ATLAS_OPT/python" UV_PYTHON_DOWNLOADS=never UV_HTTP_TIMEOUT=600)
  if ! (cd "$dir" && retry 2 _tools_as_atlas "${uvenv[@]}" "$TOOLS_UV" sync --quiet --python "$MCP4IFC_PYTHON_SERIES"); then
    MCP4IFC_STATUS="uv-sync-failed"; _tools_kv MCP4IFC_STATUS "$MCP4IFC_STATUS"
    die "MCP4IFC: 'uv sync --python $MCP4IFC_PYTHON_SERIES' failed in $dir as atlas (Section 15.1 lists MCP4IFC as a confirmed Phase 2 tool; recorded as T-mcp4ifc deferred, the step continues). Needs the managed CPython of step 5 under $ATLAS_OPT/python and pypi.org/files.pythonhosted.org through the proxy; the resolver output above names the package. Fix the cause and re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06, or ask the Principal to move the MCP4IFC row to Section 15.2 yellow (research conflict 5)"
  fi
  local py="$dir/.venv/bin/python"
  if ! _tools_as_atlas "$py" -c 'import blender_mcp.server'; then
    MCP4IFC_STATUS="import-failed"; _tools_kv MCP4IFC_STATUS "$MCP4IFC_STATUS"
    die "MCP4IFC: blender_mcp.server does not import from $py (README module name; traceback above). Section 15.1 tool, recorded as T-mcp4ifc deferred; re-run after fixing: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06"
  fi
  _tools_kv MCP4IFC_PYTHON "$py"
  # scripts/install_blender_packages.py is NOT run (fix round 3): at MCP4IFC_COMMIT it pip-installs ifcopenshell/trimesh/
  # pillow/numpy into Blender's bundled Python found under /opt/blender/*/python (VERIFIED by reading it), which is
  # root-owned read-only by design (WHO RUNS WHAT): as atlas it fails by construction, as root it would run upstream
  # research code against /opt/blender. Not automated, recorded instead. The add-on itself guards every one of those
  # imports (trimesh, PIL: try/except at MCP4IFC_COMMIT; ifcopenshell comes with Bonsai, numpy with Blender), so the
  # headless enable below is provable without them; only the add-on's trimesh/image helpers lose function in the GUI.
  _tools_kv MCP4IFC_BLENDER_PACKAGES not-installed
  if [[ ! -s "$dir/blender_addon.zip" ]]; then
    # scripts/install.py --create-addon-zip (VERIFIED at MCP4IFC_COMMIT: zips blender_addon/ as blender_addon/...).
    (cd "$dir" && _tools_as_atlas "$py" scripts/install.py --create-addon-zip >/dev/null 2>&1) \
      || die "MCP4IFC: 'scripts/install.py --create-addon-zip' failed in $dir as atlas (run it by hand for the error: runuser -u atlas -- $py scripts/install.py --create-addon-zip); re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06"
    [[ -s "$dir/blender_addon.zip" ]] || die "MCP4IFC: scripts/install.py --create-addon-zip exited 0 but $dir/blender_addon.zip is missing or empty"
  fi
  _tools_kv MCP4IFC_ADDON_ZIP "$dir/blender_addon.zip"
  # The add-on module is the zip's top-level directory (blender_addon at MCP4IFC_COMMIT). sed consumes the whole listing:
  # a `| head -n1` would SIGPIPE unzip under pipefail and abort the step with 141 (fix round 3).
  local mod
  mod="$(unzip -Z1 "$dir/blender_addon.zip" | sed -n '1{s#/.*##;p}')"
  [[ -n "$mod" ]] || die "MCP4IFC: $dir/blender_addon.zip lists no entries (unzip -Z1)"
  # Enabled headlessly AS ATLAS (per-user preferences), offline; positive probe (exit 0 AND marker), fatal on failure.
  _tools_blender_probe "import bpy; bpy.ops.preferences.addon_install(filepath='$dir/blender_addon.zip', overwrite=True); bpy.ops.preferences.addon_enable(module='$mod'); bpy.ops.wm.save_userpref(); print('ADDON_OK')" ADDON_OK \
    || die "MCP4IFC: the Blender add-on '$mod' could not be installed/enabled headlessly for atlas (no ADDON_OK line; Blender's output ends: ${TOOLS_PROBE_OUT: -300}). Is Bonsai enabled for atlas (the add-on does 'from bonsai import tool')? Re-run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06"
  log "MCP4IFC: Blender add-on '$mod' installed and enabled for atlas"
  MCP4IFC_STATUS="installed"; _tools_kv MCP4IFC_STATUS "$MCP4IFC_STATUS"
  log "smoke mcp4ifc: $py -c 'import blender_mcp.server' ok as atlas (server: python -m blender_mcp.server, stdio; needs Blender+Bonsai GUI with 'Connect to MCP server' clicked); add-on '$mod' enabled; Blender-side extra packages not installed (MCP4IFC_BLENDER_PACKAGES)"
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
    # Layout VERIFIED 2026-10-08 against the pinned zip (sha256 above): it holds radiance-6.0.c1700d56cc-Linux.tar.gz,
    # whose files sit under radiance-6.0.c1700d56cc-Linux/usr/local/radiance/{bin,lib,man}. The earlier
    # --strip-components=1 left rtrace three levels down and stopped step 6 (doc S43). So: unpack the inner tarball (if
    # any) into a staging dir, find bin/rtrace at whatever depth, and install the tree that holds it, so RAYPATH
    # /opt/radiance/lib matches and a release with a different depth still works.
    local inner root="$tmp" rt
    inner="$(find "$tmp" -maxdepth 2 -name 'radiance-*-Linux.tar.gz' | head -n1 || true)"
    if [[ -n "$inner" ]]; then
      root="$tmp/x"; mkdir -p "$root"
      tar --no-same-owner --no-same-permissions -xzf "$inner" -C "$root" || { rm -rf "$tmp"; die "tar of $inner failed"; }
    fi
    rt="$(find "$root" -path '*/bin/rtrace' -type f | head -n1 || true)"
    [[ -n "$rt" ]] || { rm -rf "$tmp"; die "Radiance archive has no bin/rtrace (contents: $(find "$tmp" -maxdepth 3 | head -n 10 | tr '\n' ' '))"; }
    cp -a "$(dirname "$(dirname "$rt")")"/. /opt/radiance.new/
    rm -rf "$tmp"
    [[ -x /opt/radiance.new/bin/rtrace ]] || die "no bin/rtrace after extracting Radiance"
    _tools_root_only /opt/radiance.new
    rm -rf /opt/radiance && mv /opt/radiance.new /opt/radiance
  fi
  _tools_root_only /opt/radiance
  # Only after ownership is root: this PATH line reaches every login shell, root included. APPENDED, not prepended:
  # Radiance ships ~200 generically named tools (total, cnt, lam, histo, ...) that must not shadow system commands for
  # root or the Principal; the orchestrator uses RADIANCE_BIN from tools.env explicitly.
  cat >/etc/profile.d/radiance.sh <<'EOT'
export PATH=$PATH:/opt/radiance/bin RAYPATH=/opt/radiance/lib
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
  # Both run as root out of the ROOT-OWNED venv (hardened above): no atlas-writable file is executed by root.
  retry 2 "$TOOLS_VENV/bin/playwright" install-deps chromium || die "playwright install-deps chromium failed (apt through the proxy)"
  if ! compgen -G "$browsers/chromium-*" >/dev/null; then
    # Browser CDN (VERIFIED, header): cdn.playwright.dev first, playwright.download.prss.microsoft.com as the mirror.
    retry 3 "$TOOLS_VENV/bin/playwright" install chromium || die "playwright install chromium failed (cdn.playwright.dev / playwright.download.prss.microsoft.com allowlisted? grep TCP_DENIED /var/log/squid/access.log)"
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
# _tools_buildfarm_wrapper — writes THE run line as a root-owned program and the sudoers line that lets atlas call it
# (header: WHO RUNS WHAT). The wrapper reads BUILDFARM_* from tools.env (root:atlas 640, written before it is called).
_tools_buildfarm_wrapper() {
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp" <<'EOT'
#!/usr/bin/env bash
# /usr/local/sbin/atlas-buildfarm-run JOBDIR CMD... — written by phase2/06-tools.sh (Day 1 step 6). THE buildfarm run
# line (docker/buildfarm/Dockerfile header): capped, unprivileged, no network, read-only root, one bind mount, bounded
# in time INSIDE docker (the container is killed by name when the host timeout fires). Called by the orchestrator as
# `sudo -n atlas-buildfarm-run $BUILDFARM_WORK_ROOT/<job> gradle assembleRelease` (/etc/sudoers.d/atlas-buildfarm).
# JOBDIR must resolve under BUILDFARM_WORK_ROOT; no other argument is interpreted; CMD... is passed to the container
# verbatim. Exit: the container's status; 124/137 after the timeout; 64 for a refused argument.
set -Eeuo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# The policy file is FIXED, never taken from the caller's environment: a NOPASSWD program that read its work root, image
# and caps from a file the unprivileged caller names would hand atlas an arbitrary bind mount and image the moment one
# `Defaults env_keep`/SETENV edit landed anywhere in sudoers.d. It must also be root-owned 640 (phase2/06-tools.sh writes
# it so), or the wrapper refuses.
ENVF=/etc/atlas/tools.env
usage() { echo "usage: atlas-buildfarm-run JOBDIR CMD..." >&2; exit 64; }
(( $# >= 2 )) || usage
jobdir="$1"; shift
kv() { awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$ENVF"; }
[[ -r "$ENVF" ]] || { echo "atlas-buildfarm-run: $ENVF is not readable" >&2; exit 64; }
[[ "$(stat -c '%U %a' "$ENVF")" == "root 640" ]] || { echo "atlas-buildfarm-run: $ENVF is $(stat -c '%U:%G %a' "$ENVF"), not root-owned 640; refusing to read policy from it" >&2; exit 64; }
root="$(kv BUILDFARM_WORK_ROOT)"; image="$(kv BUILDFARM_IMAGE)"; mem="$(kv BUILDFARM_MEMORY)"; cpus="$(kv BUILDFARM_CPUS)"
pids="$(kv BUILDFARM_PIDS)"; tmpfs="$(kv BUILDFARM_TMPFS_SIZE)"; tmo="$(kv BUILDFARM_TIMEOUT_S)"; uid="$(kv BUILDFARM_UID)"; gid="$(kv BUILDFARM_GID)"
for v in root image mem cpus pids tmpfs tmo uid gid; do
  [[ -n "${!v}" ]] || { echo "atlas-buildfarm-run: BUILDFARM_${v^^} missing in $ENVF" >&2; exit 64; }
done
[[ "$(kv BUILDFARM_STATUS)" == built ]] || { echo "atlas-buildfarm-run: BUILDFARM_STATUS is '$(kv BUILDFARM_STATUS)', not built (Android SDK licence not accepted? see /etc/atlas/tools.env)" >&2; exit 64; }
[[ "$tmo" =~ ^[0-9]+$ && "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] || { echo "atlas-buildfarm-run: non-numeric BUILDFARM_TIMEOUT_S/UID/GID" >&2; exit 64; }
real="$(realpath -e -- "$jobdir" 2>/dev/null || true)"
rootreal="$(realpath -e -- "$root" 2>/dev/null || true)"
[[ -n "$real" && -n "$rootreal" && -d "$real" && "$real" == "$rootreal"/* ]] \
  || { echo "atlas-buildfarm-run: JOBDIR must be an existing directory under $root (got '$jobdir')" >&2; exit 64; }
base="$(basename -- "$real")"
[[ "$base" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "atlas-buildfarm-run: job directory name '$base' is not [A-Za-z0-9._-]+" >&2; exit 64; }
name="buildfarm-$base"
docker rm -f "$name" >/dev/null 2>&1 || true
rc=0
timeout -k 5 "$tmo" docker run --rm --init --name "$name" --network none \
  --memory "$mem" --memory-swap "$mem" --cpus "$cpus" --pids-limit "$pids" \
  --cap-drop ALL --security-opt no-new-privileges --user "$uid:$gid" \
  --read-only --tmpfs "/tmp:rw,size=$tmpfs" -v "$real:/work:rw" -w /work "$image" "$@" || rc=$?
if (( rc == 124 || rc == 137 )); then
  # The host timeout stopped the CLIENT; the build may still be running: kill the container itself (Section 16.4 cap).
  docker kill "$name" >/dev/null 2>&1 || true
  echo "atlas-buildfarm-run: $name killed after ${tmo}s (BUILDFARM_TIMEOUT_S)" >&2
fi
docker rm -f "$name" >/dev/null 2>&1 || true
exit "$rc"
EOT
  bash -n "$tmp" || { rm -f "$tmp"; die "the generated $BUILDFARM_WRAPPER does not parse"; }
  install -m 755 -o root -g root "$tmp" "$BUILDFARM_WRAPPER"
  rm -f "$tmp"
  # sudoers: exactly the wrapper, nothing else (sudo-rs, plain syntax, visudo-checked like /etc/sudoers.d/atlas-engines).
  tmp="$(mktemp)"
  printf '# Written by phase2/06-tools.sh: the orchestrator (atlas) runs buildfarm jobs through the root-owned wrapper only.\natlas ALL=(root) NOPASSWD: %s\n' "$BUILDFARM_WRAPPER" >"$tmp"
  visudo -c -f "$tmp" >/dev/null || { rm -f "$tmp"; die "sudoers fragment for $BUILDFARM_WRAPPER failed visudo -c; not installed"; }
  install -m 440 -o root -g root "$tmp" "$BUILDFARM_SUDOERS"
  rm -f "$tmp"
  # A syntax error in any sudoers.d file makes sudo refuse EVERY command: atlas must still be able to list its grants.
  svc_user_run sudo -n -l >/dev/null 2>&1 || die "'sudo -n -l' fails as atlas after installing $BUILDFARM_SUDOERS (sudo-rs parse error?); remove the fragment and re-run"
  log "buildfarm: wrapper $BUILDFARM_WRAPPER and $BUILDFARM_SUDOERS installed (atlas -> exactly that program)"
}

# _tools_buildfarm_licence -> 0 when the Principal set BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes in atlas.env.
_tools_buildfarm_licence() {
  local v="${BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE:-}"
  [[ -n "$v" ]] || v="$(awk -F= '$1=="BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE" {gsub(/"/, "", $2); print $2; exit}' "$ATLAS_ETC/atlas.env" 2>/dev/null || true)"
  [[ "$v" == yes ]]
}

_tools_buildfarm_kv_caps() {
  _tools_kv BUILDFARM_IMAGE "$BUILDFARM_IMAGE"
  _tools_kv BUILDFARM_MEMORY "$BUILDFARM_MEMORY"
  _tools_kv BUILDFARM_CPUS "$BUILDFARM_CPUS"
  _tools_kv BUILDFARM_PIDS "$BUILDFARM_PIDS"
  _tools_kv BUILDFARM_TMPFS_SIZE "$BUILDFARM_TMPFS_SIZE"
  _tools_kv BUILDFARM_TIMEOUT_S "$BUILDFARM_TIMEOUT_S"
  _tools_kv BUILDFARM_UID "$TOOLS_ATLAS_UID"
  _tools_kv BUILDFARM_GID "$TOOLS_ATLAS_GID"
  _tools_kv BUILDFARM_WORK_ROOT "$ATLAS_SRV/workspace"
  _tools_kv BUILDFARM_RUN "$BUILDFARM_WRAPPER"
}

# _tools_buildfarm_require_licence — HARD (header: ANDROID SDK LICENCE), before the soft build: the Principal's decision.
_tools_buildfarm_require_licence() {
  _tools_buildfarm_kv_caps
  if ! _tools_buildfarm_licence; then
    # Section 16.3 item 2: accepting the Android SDK terms is the Principal's act, expressed as a setting (§7.6: no
    # pause). Fatal (header: ANDROID SDK LICENCE): the marker is not written, so the next phase2 run resumes here.
    BUILDFARM_STATUS="licence-not-accepted"; _tools_kv BUILDFARM_STATUS "$BUILDFARM_STATUS"
    TOOLS_DEFERRED_TOOLS+=(buildfarm)
    _tools_record_t buildfarm deferred "build container not built: BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE is not 'yes' in $ATLAS_ETC/atlas.env (Section 16.3 item 2: accepting $ANDROID_TERMS_URL is the Principal's act); set it, then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06"
    todo_add input-android-sdk-licence "Android SDK terms not accepted: the Android/Windows build container is deferred" "Read $ANDROID_TERMS_URL; if you accept, set BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes in $ATLAS_ETC/atlas.env and re-run: atlas-day1.sh phase2 --force 06"
    return 0
  fi
}

_tools_buildfarm() {
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  local ctx="$ATLAS_DAY1_DIR/docker/buildfarm"
  [[ -f "$ctx/Dockerfile" ]] || die "$ctx/Dockerfile is missing"
  ensure_dir "$ATLAS_SRV/workspace" atlas:atlas 755
  _tools_buildfarm_licence || die "buildfarm: BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE is not 'yes' (internal: _tools_buildfarm_require_licence runs first)"
  # A failed earlier soft run may have left BUILDFARM_STATUS=deferred in tools.env: the wrapper refuses to run until
  # the status reads built, which this function writes only after the build.
  if [[ "$(docker image inspect -f '{{index .Config.Labels "org.atlas.buildfarm.version"}}' "$BUILDFARM_IMAGE" 2>/dev/null)" == "$BUILDFARM_IMAGE_REVISION" ]]; then
    log "$BUILDFARM_IMAGE already built (revision $BUILDFARM_IMAGE_REVISION)"
  else
    local hp="" sp=""
    hp="$(awk -F= '$1=="CONTAINER_HTTP_PROXY" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
    sp="$(awk -F= '$1=="CONTAINER_HTTPS_PROXY" {print $2; exit}' "$ATLAS_ETC/docker.env" 2>/dev/null || true)"
    [[ -n "$sp" ]] || die "CONTAINER_HTTPS_PROXY is empty in $ATLAS_ETC/docker.env (Phase 1 step 6): the image build (apt, Gradle, sdkmanager) cannot reach the allowlist proxy"
    log "buildfarm: BUILDFARM_ACCEPT_ANDROID_SDK_LICENCE=yes is set in $ATLAS_ETC/atlas.env (Section 16.3 item 2: the Principal accepted $ANDROID_TERMS_URL); sdkmanager --licenses runs with that build arg"
    log "docker build $BUILDFARM_IMAGE (Gradle 9.7.1, cmdline-tools 15859902, NDK r30, MinGW-w64; ~2 GB of downloads through the proxy; build user $TOOLS_ATLAS_UID:$TOOLS_ATLAS_GID)"
    docker build --pull -t "$BUILDFARM_IMAGE" \
      --build-arg "http_proxy=$hp" --build-arg "https_proxy=$sp" --build-arg "HTTP_PROXY=$hp" --build-arg "HTTPS_PROXY=$sp" \
      --build-arg "no_proxy=localhost,127.0.0.1" --build-arg "NO_PROXY=localhost,127.0.0.1" \
      --build-arg "ATLAS_UID=$TOOLS_ATLAS_UID" --build-arg "ATLAS_GID=$TOOLS_ATLAS_GID" \
      --build-arg "ACCEPT_ANDROID_SDK_LICENCE=yes" \
      "$ctx" || die "docker build of $BUILDFARM_IMAGE failed. If the apt layer failed with 'Version ... was not found' the archive has moved past the strict pins (rule §7.9): read the changelogs, then bump openjdk-21-jdk-headless=$BUILDFARM_JDK_PIN and gcc/g++-mingw-w64-x86-64=$BUILDFARM_MINGW_PIN in docker/buildfarm/Dockerfile (apt layer + labels), BUILDFARM_JDK_PIN/BUILDFARM_MINGW_PIN here, and the org.atlas.buildfarm.version label with BUILDFARM_IMAGE_REVISION. Otherwise: dl.google.com (/android/repository/) and services.gradle.org allowlisted? sdkmanager ids are UNVERIFIED build args"
  fi
  BUILDFARM_STATUS="built"; _tools_kv BUILDFARM_STATUS "$BUILDFARM_STATUS"
  _tools_buildfarm_wrapper
  # DOCKER GROUP (header): the caps hold against the caller only once atlas is out of the docker group; measured, recorded.
  if [[ " $(id -nG atlas) " == *" docker "* ]]; then
    BUILDFARM_CAPS_ENFORCED=no
    warn "buildfarm: atlas is in the docker group (phase1/06-docker.sh usermod line): the wrapper, $BUILDFARM_SUDOERS and the Section 16.4 caps are BYPASSABLE by the orchestrator account (docker run --privileged -v /:/host, or retagging $BUILDFARM_IMAGE) until that membership is dropped; recorded as BUILDFARM_CAPS_ENFORCED=no"
  else
    BUILDFARM_CAPS_ENFORCED=yes
    log "buildfarm: atlas is not in the docker group; the wrapper path is the only way the orchestrator reaches docker (BUILDFARM_CAPS_ENFORCED=yes)"
  fi
  _tools_kv BUILDFARM_CAPS_ENFORCED "$BUILDFARM_CAPS_ENFORCED"
  # Smoke runs under THE run line, through the wrapper, through sudo, AS ATLAS (the orchestrator's path end to end):
  # a throw-away job directory under BUILDFARM_WORK_ROOT owned by the build uid.
  local work out
  work="$(mktemp -d "$ATLAS_SRV/workspace/.buildfarm-smoke.XXXXXX")"
  chown "$TOOLS_ATLAS_UID:$TOOLS_ATLAS_GID" "$work"
  out="$(svc_user_run sudo -n "$BUILDFARM_WRAPPER" "$work" gradle --version 2>&1)" || { rm -rf "$work"; die "sudo -n $BUILDFARM_WRAPPER <job> gradle --version failed as atlas under the capped run line: ${out: -400}"; }
  out="$(grep -m1 -E '^Gradle ' <<<"$out")" || { rm -rf "$work"; die "no 'Gradle <version>' line from the buildfarm smoke run"; }
  log "smoke buildfarm (as atlas, via sudo -n $BUILDFARM_WRAPPER) gradle --version: $out"
  out="$(svc_user_run sudo -n "$BUILDFARM_WRAPPER" "$work" x86_64-w64-mingw32-gcc --version 2>&1)" || { rm -rf "$work"; die "x86_64-w64-mingw32-gcc --version failed inside $BUILDFARM_IMAGE: ${out: -400}"; }
  out="${out%%$'\n'*}"
  log "smoke buildfarm mingw: $out"
  # The wrapper must refuse a job directory outside BUILDFARM_WORK_ROOT (exit 64), whoever calls it.
  if svc_user_run sudo -n "$BUILDFARM_WRAPPER" /tmp true >/dev/null 2>&1; then
    rm -rf "$work"; die "$BUILDFARM_WRAPPER accepted a job directory outside $ATLAS_SRV/workspace: the -v boundary is broken"
  fi
  log "smoke buildfarm: a job directory outside BUILDFARM_WORK_ROOT is refused by the wrapper"
  # APT DRIFT (§7.9): what the image actually installed versus the research pins (header).
  out="$(svc_user_run sudo -n "$BUILDFARM_WRAPPER" "$work" cat /opt/buildfarm/versions.txt 2>&1)" || { rm -rf "$work"; die "no /opt/buildfarm/versions.txt in $BUILDFARM_IMAGE (the Dockerfile writes it): ${out: -300}"; }
  rm -rf "$work"
  local jdk mingw drift
  jdk="$(awk '$1=="openjdk-21-jdk-headless" {print $2; exit}' <<<"$out")"
  mingw="$(awk '$1=="gcc-mingw-w64-x86-64" {print $2; exit}' <<<"$out")"
  drift="$(awk -F= '$1=="drift" {print $2; exit}' <<<"$out")"
  [[ -n "$jdk" && -n "$mingw" ]] || die "versions.txt in $BUILDFARM_IMAGE lacks the openjdk/mingw lines: $(tr '\n' ' ' <<<"$out")"
  _tools_kv BUILDFARM_JDK_VERSION "$jdk"
  _tools_kv BUILDFARM_MINGW_VERSION "$mingw"
  _tools_kv BUILDFARM_APT_DRIFT "${drift:-unknown}"
  if [[ "$jdk" != "$BUILDFARM_JDK_PIN" || "$mingw" != "$BUILDFARM_MINGW_PIN" || "$drift" == yes ]]; then
    # Cannot happen with the strict Dockerfile unless the pins here and there disagree, or an older image (revision
    # label matched) was built with the removed fallback: either way the image is not the pinned build (§7.9).
    die "buildfarm: $BUILDFARM_IMAGE carries openjdk-21-jdk-headless $jdk (pin $BUILDFARM_JDK_PIN), gcc-mingw-w64-x86-64 $mingw (pin $BUILDFARM_MINGW_PIN), drift=$drift: not the pinned build. Align BUILDFARM_JDK_PIN/BUILDFARM_MINGW_PIN with the Dockerfile's apt layer (and bump BUILDFARM_IMAGE_REVISION + the version label), then: docker image rm $BUILDFARM_IMAGE && sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06"
  fi
  log "buildfarm: apt versions match the research pins (openjdk $jdk, mingw $mingw)"
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
  # Created with its final mode (root:atlas 640): a die anywhere in the step never leaves it world-readable.
  [[ -e "$TOOLS_ENV_FILE" ]] || install -m 640 -o root -g atlas /dev/null "$TOOLS_ENV_FILE"
  _tools_apt
  _tools_venv
  # HARD installs (VERIFIED inputs) die on failure; SOFT installs (UNVERIFIED inputs) record T-<tool> deferred and
  # continue (header: HARD AND SOFT INSTALLS). The Android SDK licence check is hard and comes before the soft build.
  _tools_ifcopenshell                                   # hard: PyPI pins
  _tools_soft blender _tools_blender                     # soft: patch level from a live listing, no sha256 to type
  _tools_soft bonsai _tools_bonsai blender               # soft: UNVERIFIED extensions API; needs blender
  _tools_soft mcp4ifc _tools_mcp4ifc blender bonsai      # soft: research code, pinned commit; needs blender + bonsai
  _tools_radiance                                       # hard: sha256-fixed zip
  _tools_soft energyplus _tools_energyplus               # soft: 24.04 build on 26.04 UNVERIFIED
  _tools_soft openstudio _tools_openstudio               # soft: 24.04 .deb/tar.gz on 26.04 UNVERIFIED
  _tools_kicad                                          # hard: apt
  _tools_playwright                                     # hard: PyPI pin + VERIFIED CDN hosts
  if _tools_buildfarm_licence; then
    _tools_soft buildfarm _tools_buildfarm               # soft: UNVERIFIED sdkmanager ids, strict apt pins may move
  else
    _tools_buildfarm_require_licence                     # policy v0.3.3: records T-buildfarm deferred + the to-do, never stops
  fi
  _tools_docling_assert
  chown root:atlas "$TOOLS_ENV_FILE"; chmod 640 "$TOOLS_ENV_FILE"
  _tools_venv_harden     # Section 16.3 item 6: root:atlas, no group/other write, asserted (the step-02/04 invariant)
  # Reaching this line means every HARD Section 15.1 tool installed and passed its smoke test (a failure died above).
  # The soft installs ran in subshells, so their statuses are read back from tools.env, never from this shell's globals.
  local mstat bstat caps t
  mstat="$(_tools_kv_get MCP4IFC_STATUS)"; bstat="$(_tools_kv_get BUILDFARM_STATUS)"; caps="$(_tools_kv_get BUILDFARM_CAPS_ENFORCED)"
  for t in "${TOOLS_DEFERRED_TOOLS[@]}"; do
    case "$t" in
      mcp4ifc) mstat="deferred"; _tools_kv MCP4IFC_STATUS deferred ;;
      buildfarm) bstat="deferred"; _tools_kv BUILDFARM_STATUS deferred ;;
    esac
  done
  _tools_kv TOOLS_DEFERRED "${TOOLS_DEFERRED_TOOLS[*]:-none}"
  if (( ${#TOOLS_DEFERRED_TOOLS[@]} == 0 )); then
    [[ "$mstat" == installed ]] || die "step 06 reached its end with MCP4IFC_STATUS=$mstat and no T-mcp4ifc row (internal: every other value must have died or been deferred earlier)"
    [[ "$bstat" == built ]] || die "step 06 reached its end with BUILDFARM_STATUS=$bstat and no T-buildfarm row (internal: every other value must have died or been deferred earlier)"
  else
    warn "SUMMARY step 06: ${#TOOLS_DEFERRED_TOOLS[@]} UNVERIFIED-input install(s) DEFERRED, recorded as T-<tool> rows in $ATLAS_VERIFY_FILE (phase2/10-gate.sh prints them, non-blocking): ${TOOLS_DEFERRED_TOOLS[*]}. The hard installs are complete and the step marker is written; re-run the deferred ones after fixing their cause: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 06"
  fi
  if [[ "$bstat" == built && "$caps" != yes ]]; then
    warn "SUMMARY step 06: BUILDFARM_CAPS_ENFORCED=${caps:-unknown}: the buildfarm wrapper and its Section 16.4 caps are bypassable by the atlas account while it is in the docker group (phase1/06-docker.sh); the Phase 1 writer is asked to drop that membership once the sandbox has its own root-owned wrapper (header: DOCKER GROUP)"
  fi
  [[ "$mstat" != installed ]] || warn "SUMMARY step 06: MCP4IFC_BLENDER_PACKAGES=not-installed: the GUI add-on's extra packages (trimesh, pillow) are not installed into Blender's bundled Python, which is root-owned read-only by design; the MCP server, the add-on zip and its headless enable are proven (see _tools_mcp4ifc_work)"
  log "step 06 done: Section 15.1 hard tools installed; paths in $TOOLS_ENV_FILE (MCP4IFC_STATUS=$mstat, BUILDFARM_STATUS=$bstat, BUILDFARM_CAPS_ENFORCED=${caps:-unknown}, deferred: ${TOOLS_DEFERRED_TOOLS[*]:-none})"
  notify "Phase 2 step 6 done: IfcOpenShell, Radiance, KiCad, Playwright installed; soft installs deferred: ${TOOLS_DEFERRED_TOOLS[*]:-none} (MCP4IFC $mstat, buildfarm $bstat, caps enforced: ${caps:-unknown})"
}
