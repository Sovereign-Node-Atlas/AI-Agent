#!/usr/bin/env bash
# phase2-services.sh — Phase 2 driver: engines and services (ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 2).
#
#   sudo ./atlas-day1.sh phase2 [--dry-run] [--force STEP] [--status]     (the entry point re-execs this file)
#
# Sources phase2/NN-*.sh in Section 17 order through run_phase_steps (CONVENTIONS.md §4); every step is idempotent.
# Interactive pauses in this phase (CONVENTIONS.md §7.6): (1) the Hugging Face token prompt below when
# $ATLAS_ETC/secrets/hf-token.env is absent; (2) the two Google OAuth links in step 6c (phase2/06c-google-oauth.sh).
# Step 9b (phase2/09b-vault.sh) pauses ONLY when the Principal opts in with
# `sudo env ATLAS_VAULT_INIT=1 ./atlas-day1.sh phase2 --force 09b` (phase2/README-contracts.md §1); a plain run leaves
# the real vault uninitialised and continues. Everything else is unattended.
#
# Contracts relied on from other writers (CONVENTIONS.md §1): phase2/02-orchestrator.sh, 03-openwebui.sh, 05-voice.sh,
# 06-tools.sh, 06b-cloudflare-token.sh, 06c-google-oauth.sh, 07-restic.sh, 08-sentinel.sh, 09-windows-share.sh,
# 09b-vault.sh and 10-gate.sh live in phase2/ and each defines step_<id>; 10-gate.sh calls `gate phase2 ...` with the
# CONVENTIONS.md §6 ids (V3b, V6, V12, V13, V14a, V15, V16, V17, V18, V20, V23 required -- V7, V10a recorded only).
# V10's Phase 2 half (Section 21: the resident router "is verified separately at the Phase 2 gate") is recorded under
# the id V10a, by step 04 (phase2/04-memory.sh, `run_verify V10a v10a-router-resident.sh`, fatal to the step on fail)
# and again by 10-gate.sh (`run_verify V10a ...`, recorded only). No `V10` row is ever written from Phase 2: gate()
# takes the latest record per id across phases and treats `info` as non-blocking, so a Phase 2 V10 row could stand in
# for the Phase 3 load test. V10a is declared in CONVENTIONS §4 (ids), §5 (verify/v10a-router-resident.sh) and §6
# (Phase 2 "recorded, not blocking": V7, V10a); tools/fill-workbook.py shows it as the Phase 2 evidence of the V10 row.

# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/lib/common.sh"

export ATLAS_PHASE=phase2
require_root
parse_common_args "$@"
load_env

# --- Pre-flight: Phase 1 must have passed its gate (CONVENTIONS.md §6; atlas-day1.sh checks too, this is for direct runs).
if [[ "$ATLAS_DRY_RUN" != "1" ]]; then
  [[ -e "$ATLAS_DONE_DIR/phase1.gate" ]] \
    || die "Phase 1 has not passed its gate ($ATLAS_DONE_DIR/phase1.gate missing). Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase1"
  id -u atlas >/dev/null 2>&1 || die "service account 'atlas' does not exist (Phase 1 step 6 creates it)"
  [[ -f "$ATLAS_ETC/proxy.env" ]] || die "$ATLAS_ETC/proxy.env missing: Phase 1 step 4 did not configure the allowlist proxy (rule §7.1)"
  [[ -d "$ATLAS_SRV/models" ]] || die "$ATLAS_SRV/models missing: the 8 TB data volume is not mounted (Phase 1 step 3)"
fi

# --- Hugging Face token (CONVENTIONS.md §2: /etc/atlas/secrets/hf-token.env, HF_TOKEN=..., atlas:atlas 600) ----------
# Needed by step 5 (PyAnnote 3.1, gated) and Phase 4 (FLUX.1-dev, Stable Audio Open, gated); step 4's resident models
# are public and are pulled without the token (phase2/04-memory.sh hf_download_public). Prompted once, never echoed,
# never logged.
# Ownership (fix round 2, aligned with CONVENTIONS §2 instead of arguing with it): the FILE is atlas:atlas 600, because
# phase2/02-orchestrator.sh points the orchestrator (User=atlas) at it through HF_TOKEN_FILE in orchestrator.env and
# orchestrator/src/atlas/config.py knows the key; root readers (lib/common.sh hf_download, phase3-models.sh,
# phase4/lib-engine.sh, verify/v06-pyannote.sh) read it regardless.
# The DIRECTORY (fix round 3, ONE value): root:atlas 710, traverse-only for the atlas group. atlas-side readers
# (orchestrator, Celery workers, 06c's proof, the D9 retention task) open their own 600 files inside it BY NAME and need
# the x bit only; the r bit (750) would additionally let every atlas-group process list the secret file names, which
# nothing needs. 710 is what phase1/02-luks.sh, 03-mounts.sh, 07-remote.sh and phase2/09b-vault.sh (the last step
# before the gate, so the mode a finished Phase 2 ends with) already set; phase2/02, 03, 06c, 07, 08 and 09 still set
# 750 and are asked to adopt 710 so the directory stops flipping between steps. CONVENTIONS §2's row (`root:root 700`,
# which cannot hold beside its own atlas:atlas entries inside the directory) should read `root:atlas 710`
# (phase2/README-contracts.md §3 item 10 asks the same). Every file inside stays 600, owned by its one reader.
hf_token_prompt() {
  local secrets="$ATLAS_ETC/secrets" tokf="$ATLAS_ETC/secrets/hf-token.env"
  ensure_dir "$secrets" root:atlas 710
  if [[ -s "$tokf" ]]; then
    # Idempotent alignment with §2 for a file written by an earlier revision (root:root).
    chown atlas:atlas "$tokf"
    chmod 600 "$tokf"
    log "hf token: $tokf present"
    return 0
  fi
  if [[ "$ATLAS_DRY_RUN" == "1" ]]; then
    log "DRY-RUN would prompt for the Hugging Face token ($tokf absent)"
    return 0
  fi
  # Non-terminal remedy (fix round 3): a form that never puts the token on argv or in the shell history (printf and
  # read are bash builtins, read -s hides the input; bash, not sh: dash has no read -s), so the only copy is the 600 file (§7.2).
  [[ -t 0 ]] || die "$tokf is absent and stdin is not a terminal. Re-run from a terminal, or create it without the token ever appearing on a command line: sudo bash -c 'umask 077; printf \"HF token: \"; IFS= read -rs t; echo; printf \"HF_TOKEN=%s\\n\" \"\$t\" > $tokf; chown atlas:atlas $tokf; chmod 600 $tokf'"
  echo
  echo "Phase 2 needs a Hugging Face access token (read scope) for the gated models: PyAnnote 3.1 now, FLUX.1-dev and"
  echo "Stable Audio Open in Phase 4. Accept those licences on huggingface.co with the account that owns the token."
  echo "The token is written once to $tokf (atlas:atlas, mode 600, CONVENTIONS §2) and never printed."
  local tok=""
  read -r -s -p "HF token (input hidden): " tok
  echo
  [[ "$tok" =~ ^hf_[A-Za-z0-9_]+$ ]] || die "that does not look like a Hugging Face token (expected hf_...); nothing written"
  (umask 077; printf 'HF_TOKEN=%s\n' "$tok" >"$tokf")
  chown atlas:atlas "$tokf"
  chmod 600 "$tokf"
  # Read-back test through the allowlist proxy: a rejected token must surface now, not in Phase 4. The bearer header is
  # read by curl from STDIN (-H @-, curl >= 7.55): never on argv where /proc/<pid>/cmdline shows it to every local user,
  # and never in a temp file that a SIGINT between mktemp and rm could leave behind (fix round 2, §7.2).
  # The endpoint is PINNED (fix round 3): the token is only ever sent to huggingface.co. An inherited HF_ENDPOINT (a
  # mirror from a shell profile or atlas.env, CONVENTIONS §3) must not receive it; the step stops rather than comply.
  proxy_env
  local hf_base="https://huggingface.co"
  if [[ -n "${HF_ENDPOINT:-}" && "${HF_ENDPOINT%/}" != "$hf_base" ]]; then
    die "HF_ENDPOINT is set to '$HF_ENDPOINT'; the Hugging Face token is only ever sent to $hf_base (rule §7.2). Unset it (atlas.env or the environment) and re-run"
  fi
  local code
  code="$(printf 'Authorization: Bearer %s\n' "$tok" \
          | curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H @- \
            "$hf_base/api/whoami-v2" || true)"
  case "$code" in
    200) log "hf token: accepted by huggingface.co (whoami-v2 200); written to $tokf" ;;
    401|403) rm -f "$tokf"; die "huggingface.co rejected the token (HTTP $code); nothing kept, re-run to try again" ;;
    *) warn "hf token: could not verify with huggingface.co (HTTP ${code:-none}); kept $tokf, hf_download will report a bad token loudly" ;;
  esac
  unset tok
}
hf_token_prompt

log "Phase 2 starting: steps in $ATLAS_DAY1_DIR/phase2 (dry-run=$ATLAS_DRY_RUN)"
run_phase_steps phase2 "$ATLAS_DAY1_DIR/phase2"

if ! compgen -G "$ATLAS_DAY1_DIR/phase2/10-*.sh" >/dev/null; then
  warn "no phase2/10-*.sh gate step found; the Phase 2 gate (CONVENTIONS.md §6) has not been evaluated"
fi
log "Phase 2 driver finished"
