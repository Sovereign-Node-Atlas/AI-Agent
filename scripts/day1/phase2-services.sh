#!/usr/bin/env bash
# phase2-services.sh — Phase 2 driver: engines and services (ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 2).
#
#   sudo ./atlas-day1.sh phase2 [--dry-run] [--force STEP] [--status]     (the entry point re-execs this file)
#
# Sources phase2/NN-*.sh in Section 17 order through run_phase_steps (CONVENTIONS.md §4); every step is idempotent.
# Interactive pauses in this phase (CONVENTIONS.md §7.6): the Hugging Face token prompt below when
# $ATLAS_ETC/secrets/hf-token.env is absent, and the two Google OAuth links in step 6c (that step's own file).
#
# Contracts relied on from other writers (CONVENTIONS.md §1): phase2/02-orchestrator.sh, 03-openwebui.sh, 05-voice.sh,
# 06-tools.sh, 06b-cloudflare.sh, 06c-google.sh, 07-restic.sh, 08-sentinel.sh, 09-windows.sh and 10-gate.sh live in
# phase2/ and each defines step_<id>; 10-gate.sh calls `gate 2 ...` with the CONVENTIONS.md §6 ids (V3b, V6, V12, V13,
# V14a, V15, V16, V17, V18, V20, V23 -- V7) and run_verify V10a v10a-router-resident.sh.

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

# --- Hugging Face token (CONVENTIONS.md §2: /etc/atlas/secrets/hf-token.env, HF_TOKEN=..., atlas:atlas 600) ---------
# Needed by step 5 (PyAnnote 3.1, gated) and Phase 4 (FLUX.1-dev, Stable Audio Open, gated); step 4's small models are
# public but hf_download sends the token whenever the file exists. Prompted once, never echoed, never logged.
hf_token_prompt() {
  local secrets="$ATLAS_ETC/secrets" tokf="$ATLAS_ETC/secrets/hf-token.env"
  ensure_dir "$secrets" root:root 700
  if [[ -s "$tokf" ]]; then
    log "hf token: $tokf present"
    return 0
  fi
  if [[ "$ATLAS_DRY_RUN" == "1" ]]; then
    log "DRY-RUN would prompt for the Hugging Face token ($tokf absent)"
    return 0
  fi
  [[ -t 0 ]] || die "$tokf is absent and stdin is not a terminal. Create it by hand: printf 'HF_TOKEN=hf_...\\n' > $tokf && chown atlas:atlas $tokf && chmod 600 $tokf"
  echo
  echo "Phase 2 needs a Hugging Face access token (read scope) for the gated models: PyAnnote 3.1 now, FLUX.1-dev and"
  echo "Stable Audio Open in Phase 4. Accept those licences on huggingface.co with the account that owns the token."
  echo "The token is written once to $tokf (atlas:atlas, mode 600) and never printed."
  local tok=""
  read -r -s -p "HF token (input hidden): " tok
  echo
  [[ "$tok" =~ ^hf_[A-Za-z0-9_]+$ ]] || die "that does not look like a Hugging Face token (expected hf_...); nothing written"
  (umask 077; printf 'HF_TOKEN=%s\n' "$tok" >"$tokf")
  chown atlas:atlas "$tokf"
  chmod 600 "$tokf"
  # Read-back test through the allowlist proxy: a rejected token must surface now, not in Phase 4.
  proxy_env
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -H "Authorization: Bearer $tok" \
          "${HF_ENDPOINT:-https://huggingface.co}/api/whoami-v2" || true)"
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
