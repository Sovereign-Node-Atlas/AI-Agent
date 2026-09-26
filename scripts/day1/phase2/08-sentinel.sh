#!/usr/bin/env bash
# phase2/08-sentinel.sh — Section 17 Phase 2 step 8: the Sentinel timer with the D6 feeds (Section 9.3) and the
# 72-hour pruning timer (Section 9.6), both as systemd timers whose only job is to enqueue the orchestrator's Celery
# task (CONVENTIONS.md §8). Sourced by phase2-services.sh through run_phase_steps; defines step_08 only.
#
# Order (each part idempotent):
#   1. config/sentinel-feeds.json (D6: CoinDesk RSS, ASX and US index feeds, RSS news, node telemetry) is validated
#      as JSON, every feed host is proven to be in config/allowlist.txt (the file's own contract), and each URL is
#      probed ONCE through the allowlist proxy: an unreachable feed is logged as "feed unreachable" (its URL is
#      UNVERIFIED by the research and the task treats it the same way), never a failure.
#   2. SENTINEL_FEEDS / SENTINEL_LOG_DIR in $ATLAS_ETC/orchestrator.env (step 02 wrote the defaults; re-asserted).
#   3. Units: atlas-sentinel.service/.timer (hourly), atlas-prune.service/.timer (72 h), enabled.
#   4. One Sentinel pulse NOW: `atlas-admin enqueue sentinel --wait 600` (contract, step 02 header) must finish and
#      print the ledger record as one JSON object; that record is the proof the ledger shows the pulse.
#
# Contracts relied on: orch_admin/ORCH_ENV/ORCH_DIR from phase2/02-orchestrator.sh; the orchestrator and the cpu
# worker are running (step 02); Phase 1 step 7's ntfy for the alert path (the task, not this step, pushes).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}
if ! declare -F orch_admin >/dev/null; then
  # shellcheck source=phase2/02-orchestrator.sh
  source "$ATLAS_DAY1_DIR/phase2/02-orchestrator.sh"
fi

SENTINEL_FEEDS_FILE="$ATLAS_DAY1_DIR/config/sentinel-feeds.json"
SENTINEL_ALLOWLIST="$ATLAS_DAY1_DIR/config/allowlist.txt"

_sentinel_validate_feeds() {
  [[ -f "$SENTINEL_FEEDS_FILE" ]] || die "$SENTINEL_FEEDS_FILE is missing"
  [[ -f "$SENTINEL_ALLOWLIST" ]] || die "$SENTINEL_ALLOWLIST is missing"
  # JSON shape + allowlist sync in one pass; prints "id<TAB>url" per feed (primary and fallback URLs).
  local urls
  urls="$(python3 - "$SENTINEL_FEEDS_FILE" "$SENTINEL_ALLOWLIST" <<'PY'
import json, sys
from urllib.parse import urlsplit
feeds_path, allow_path = sys.argv[1:3]
doc = json.load(open(feeds_path, encoding="utf-8"))
feeds = doc.get("feeds") or []
assert isinstance(feeds, list) and feeds, "feeds[] empty"
assert isinstance(doc.get("telemetry"), list) and doc["telemetry"], "telemetry[] empty (D6 node telemetry)"
kinds = {f.get("kind") for f in feeds}
assert "rss" in kinds, "no RSS feed (D6: RSS news)"
ids = {f["id"] for f in feeds}
for need in ("coindesk-news", "asx200", "sp500"):
    assert need in ids, f"feed {need} missing (D6)"
allow = []
for line in open(allow_path, encoding="utf-8"):
    line = line.split("#", 1)[0].strip()
    if line:
        allow.append(line.lower())
def allowed(host: str) -> bool:
    host = host.lower()
    for a in allow:
        if a.startswith("."):
            if host == a[1:] or host.endswith(a):
                return True
        elif host == a:
            return True
    return False
bad = []
out = []
for f in feeds:
    for key in ("url", "fallback_url"):
        u = f.get(key)
        if not u:
            continue
        host = urlsplit(u).hostname or ""
        if not allowed(host):
            bad.append(f"{f['id']}: {host}")
        out.append(f"{f['id']}\t{u}")
if bad:
    print("hosts missing from config/allowlist.txt:", bad, file=sys.stderr)
    sys.exit(1)
print("\n".join(out))
PY
)" || die "config/sentinel-feeds.json is invalid or names a host missing from config/allowlist.txt (see above)"
  log "sentinel-feeds.json valid: $(wc -l <<<"$urls") URL(s), every host allowlisted"
  # One probe each through the proxy (20 s); unreachable = logged, not fatal (URLs are UNVERIFIED by the research).
  proxy_env
  local id url code ok=0 bad=0
  while IFS=$'\t' read -r id url; do
    [[ -n "$url" ]] || continue
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -A 'Mozilla/5.0 (X11; Linux x86_64) ATLAS-Sentinel/1.0' "$url" 2>/dev/null || true)"
    if [[ "$code" =~ ^2 ]]; then
      ok=$(( ok + 1 ))
    else
      bad=$(( bad + 1 ))
      warn "feed unreachable: $id ($url) HTTP ${code:-000} — the Sentinel task logs this the same way and continues (UNVERIFIED feed URL)"
    fi
  done <<<"$urls"
  log "feed probe: $ok reachable, $bad unreachable"
}

_sentinel_env() {
  ensure_dir "$ATLAS_SRV/data/sentinel" atlas:atlas 750
  ensure_kv "$ORCH_ENV" SENTINEL_FEEDS "$SENTINEL_FEEDS_FILE"
  ensure_kv "$ORCH_ENV" SENTINEL_LOG_DIR "$ATLAS_SRV/data/sentinel"
  ensure_kv "$ORCH_ENV" COLD_DIR /srv/cold
  ensure_dir /srv/cold atlas:atlas 750
}

_sentinel_units() {
  export ATLAS_ETC ATLAS_OPT
  local u
  for u in atlas-sentinel atlas-prune; do
    render_template -m 644 "$ATLAS_DAY1_DIR/systemd/$u.service" "/etc/systemd/system/$u.service" ATLAS_ETC ATLAS_OPT
    install -m 644 "$ATLAS_DAY1_DIR/systemd/$u.timer" "/etc/systemd/system/$u.timer"
  done
  systemctl daemon-reload
  systemctl enable --now atlas-sentinel.timer >/dev/null
  systemctl enable --now atlas-prune.timer >/dev/null
  log "timers enabled: $(systemctl list-timers --no-legend atlas-sentinel.timer atlas-prune.timer 2>/dev/null | awk '{print $NF": next "$1" "$2" "$3}' | tr '\n' ';')"
}

_sentinel_pulse_now() {
  systemctl is-active --quiet atlas-orchestrator || die "atlas-orchestrator is not active (step 02)"
  systemctl is-active --quiet atlas-celery-cpu || die "atlas-celery-cpu is not active (step 02): the pulse runs on the cpu queue"
  # Workers read orchestrator.env at start; the SENTINEL_* keys must be live before the pulse.
  systemctl restart atlas-celery-cpu atlas-celery-gpu atlas-celery-beat || die "restarting the Celery units failed (journalctl -u atlas-celery-cpu)"
  sleep 5
  log "running one Sentinel pulse now (atlas-admin enqueue sentinel --wait 600; feeds through the proxy, ~1-2 min)"
  local out rec
  out="$(orch_admin enqueue sentinel --wait 600 2>&1)" || { printf '%s\n' "$out" | tail -n 30 >&2; die "the Sentinel pulse did not complete (see the output above and journalctl -u atlas-celery-cpu)"; }
  rec="$(grep -E '^\{.*\}$' <<<"$out" | tail -n1 || true)"
  [[ -n "$rec" ]] || die "contract: 'atlas-admin enqueue sentinel --wait' printed no JSON ledger record; output: $(tr '\n' ' ' <<<"$out" | cut -c1-300)"
  python3 -c 'import json, sys; d = json.loads(sys.argv[1]); assert isinstance(d, dict) and d, "empty record"' "$rec" \
    || die "the ledger record is not a JSON object: ${rec:0:200}"
  log "ledger shows the pulse: ${rec:0:400}"
  # The Sentinel log dir should now hold the pulse's readings (informational; the ledger line above is the proof).
  local n
  n="$(find "$ATLAS_SRV/data/sentinel" -type f -newer "$SENTINEL_FEEDS_FILE" 2>/dev/null | wc -l)"
  log "Sentinel log files written by this pulse under $ATLAS_SRV/data/sentinel: $n"
}

step_08() {
  [[ -s "$ORCH_ENV" ]] || die "$ORCH_ENV missing: step 02 must run first"
  _sentinel_validate_feeds
  _sentinel_env
  _sentinel_units
  _sentinel_pulse_now
  notify "Phase 2 step 8 done: Sentinel hourly timer (D6 feeds), 72-hour prune timer; first pulse in the ledger"
  log "step 08 done"
}
