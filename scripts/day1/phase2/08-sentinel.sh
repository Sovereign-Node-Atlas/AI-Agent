#!/usr/bin/env bash
# phase2/08-sentinel.sh — Section 17 Phase 2 step 8: the Sentinel timer with the D6 feeds (Section 9.3) and the
# 72-hour pruning timer (Section 9.6), both as systemd timers whose only job is to enqueue the orchestrator's Celery
# task (CONVENTIONS.md §8). Sourced by phase2-services.sh through run_phase_steps; defines step_08 only.
#
# Order (each part idempotent):
#   1. config/sentinel-feeds.json (D6: CoinDesk RSS, ASX and US index feeds, RSS news, node telemetry) — the ON-NODE
#      copy $ATLAS_OPT/day1/config, the path the orchestrator reads and its units mount read-only (fix round 2; step 02
#      dies when the copy is absent) — is validated as JSON, every feed host is proven to be in config/allowlist.txt
#      (the file's own contract), every entry of the
#      allowlist's Sentinel group that no feed references is reported (a wildcard nothing uses widens 12.5's surface;
#      allowlist.txt is another writer's file, so this is a warning naming the line to remove), and each URL is probed
#      ONCE through the allowlist proxy: an unreachable feed is logged as "feed unreachable" (its URL is UNVERIFIED by
#      the research and the task treats it the same way), never a failure.
#   2. SENTINEL_FEEDS / SENTINEL_LOG_DIR / FIREWALL_TELEMETRY_FILE in $ATLAS_ETC/orchestrator.env (step 02 wrote the
#      defaults; re-asserted), and the alert path proven for its reader: the cpu worker (user atlas) must be able to read
#      NTFY_TOKEN_FILE ($ATLAS_ETC/secrets/ntfy.env, atlas:atlas 600 inside the root:atlas 750 secrets dir) — otherwise
#      every BLUF push fails silently at 03:00. The units now repeat that test as atlas at every start (fix round 2).
#   2b. /usr/local/sbin/atlas-sentinel-telemetry from phase2/atlas-sentinel-telemetry.sh: the root-side exporter of the
#      D6 "firewall" telemetry (kernel journal and squid log counts the atlas account cannot read) into the root-owned
#      /var/lib/atlas/telemetry/firewall.json; atlas-sentinel.service runs it (ExecStartPre=-+) before each enqueue.
#      Run once here and the file proven readable by atlas.
#   3. Units: atlas-sentinel.service/.timer (hourly), atlas-prune.service/.timer (daily check, >= 71 h stamp: a true
#      72-hour cadence, Section 9.6), enabled.
#   4. One Sentinel pulse NOW: `atlas-admin enqueue sentinel --wait 600` (contract, step 02 header) must finish and
#      print the ledger record as one JSON object; that record is the proof the ledger shows the pulse.
#
# Contracts relied on: orch_admin/ORCH_ENV/ORCH_DIR/REDIS_ENV from phase2/02-orchestrator.sh; the orchestrator and the
# cpu worker are running (step 02); Phase 1 step 7's ntfy for the alert path (the task, not this step, pushes).
# Contract this step defines for the package (atlas.tasks.sentinel): the "firewall" telemetry reader reads
# FIREWALL_TELEMETRY_FILE (JSON: ts, window_s, ufw_block, docker_egress_denied, squid_denied, denied_per_hour, errors;
# a null count means "could not count") when the file exists and ts is < 2 h old, else "telemetry unreadable: firewall";
# the "backup" reader uses `systemctl show atlas-aegis.service -p ExecMainStartTimestamp -p Result` (what it already
# does; sentinel-feeds.json now says so). Until the package reads the file, its journalctl path still reports unreadable.
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}
if ! declare -F orch_admin >/dev/null; then
  # shellcheck source=phase2/02-orchestrator.sh
  source "$ATLAS_DAY1_DIR/phase2/02-orchestrator.sh"
fi

SENTINEL_FEEDS_FILE="$ATLAS_OPT/day1/config/sentinel-feeds.json"    # the on-node copy the orchestrator reads (02 writes SENTINEL_FEEDS to it)
SENTINEL_ALLOWLIST="$ATLAS_OPT/day1/config/allowlist.txt"
SENTINEL_NTFY_TOKEN="$ATLAS_ETC/secrets/ntfy.env"
SENTINEL_TELE_SRC="$ATLAS_DAY1_DIR/phase2/atlas-sentinel-telemetry.sh"
SENTINEL_TELE_BIN=/usr/local/sbin/atlas-sentinel-telemetry
SENTINEL_TELE_FILE=/var/lib/atlas/telemetry/firewall.json

_sentinel_validate_feeds() {
  [[ -f "$SENTINEL_FEEDS_FILE" ]] || die "$SENTINEL_FEEDS_FILE is missing: run Phase 2 through ${ATLAS_ENTRY:-./atlas-day1.sh} (it copies scripts/day1 to $ATLAS_OPT/day1, the path the orchestrator reads)"
  [[ -f "$SENTINEL_ALLOWLIST" ]] || die "$SENTINEL_ALLOWLIST is missing (same on-node copy)"
  if [[ -f "$ATLAS_DAY1_DIR/config/sentinel-feeds.json" ]] && ! cmp -s "$ATLAS_DAY1_DIR/config/sentinel-feeds.json" "$SENTINEL_FEEDS_FILE"; then
    die "$ATLAS_DAY1_DIR/config/sentinel-feeds.json differs from the on-node copy $SENTINEL_FEEDS_FILE that the orchestrator reads; run through ${ATLAS_ENTRY:-./atlas-day1.sh} so the copy is refreshed"
  fi
  # JSON shape + allowlist sync in one pass; prints "id<TAB>url" per feed (primary and fallback URLs) and
  # "UNUSED<TAB>entry" for every Sentinel-group allowlist entry no feed host matches.
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
for f in feeds:
    assert f.get("hemisphere") in ("corporate", "estate"), f"feed {f['id']}: hemisphere must be corporate|estate (CONVENTIONS.md §8)"
allow: list[str] = []
sentinel_group: list[str] = []
in_group = False
for raw in open(allow_path, encoding="utf-8"):
    stripped = raw.strip()
    if stripped.startswith("#"):
        if "sentinel" in stripped.lower() and stripped.startswith("# ---"):
            in_group = True
        elif stripped.startswith("# ---"):
            in_group = False
        continue
    line = stripped.split("#", 1)[0].strip()
    if line:
        allow.append(line.lower())
        if in_group:
            sentinel_group.append(line.lower())
def matches(host: str, a: str) -> bool:
    if a.startswith("."):
        return host == a[1:] or host.endswith(a)
    return host == a
def allowed(host: str) -> bool:
    host = host.lower()
    return any(matches(host, a) for a in allow)
bad = []
out = []
hosts = set()
for f in feeds:
    for key in ("url", "fallback_url"):
        u = f.get(key)
        if not u:
            continue
        host = (urlsplit(u).hostname or "").lower()
        hosts.add(host)
        if not allowed(host):
            bad.append(f"{f['id']}: {host}")
        out.append(f"{f['id']}\t{u}")
if bad:
    print("hosts missing from config/allowlist.txt:", bad, file=sys.stderr)
    sys.exit(1)
for a in sentinel_group:
    if not any(matches(h, a) for h in hosts):
        out.append(f"UNUSED\t{a}")
print("\n".join(out))
PY
)" || die "config/sentinel-feeds.json is invalid or names a host missing from config/allowlist.txt (see above)"
  local id url n_urls=0
  while IFS=$'\t' read -r id url; do
    [[ -n "$url" ]] || continue
    if [[ "$id" == UNUSED ]]; then
      warn "config/allowlist.txt Sentinel group entry '$url' is referenced by no feed in config/sentinel-feeds.json: an allowlisted domain nothing uses widens the outbound surface (Section 12.5); remove it from allowlist.txt (that file's writer) or add the feed that uses it"
    else
      n_urls=$(( n_urls + 1 ))
    fi
  done <<<"$urls"
  log "sentinel-feeds.json valid: $n_urls URL(s), every host allowlisted"
  # One probe each through the proxy (20 s); unreachable = logged, not fatal (URLs are UNVERIFIED by the research).
  proxy_env
  local code ok=0 bad=0
  while IFS=$'\t' read -r id url; do
    [[ -n "$url" && "$id" != UNUSED ]] || continue
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
  ensure_kv "$ORCH_ENV" NTFY_TOKEN_FILE "$SENTINEL_NTFY_TOKEN"
  ensure_kv "$ORCH_ENV" FIREWALL_TELEMETRY_FILE "$SENTINEL_TELE_FILE"
  chown root:atlas "$ORCH_ENV"; chmod 640 "$ORCH_ENV"
  ensure_dir /srv/cold atlas:atlas 750
  # The alert path's reader is the cpu worker (atlas): prove the read now (fix round).
  ensure_dir "$ATLAS_ETC/secrets" root:atlas 750
  [[ -s "$SENTINEL_NTFY_TOKEN" ]] || die "$SENTINEL_NTFY_TOKEN is missing: Phase 1 step 7 writes it (NTFY_TOKEN=tk_...); without it the Sentinel BLUF alert (Section 9.3) cannot reach the Principal's phone"
  grep -qE '^NTFY_TOKEN=tk_' "$SENTINEL_NTFY_TOKEN" || die "$SENTINEL_NTFY_TOKEN carries no NTFY_TOKEN=tk_... line (Phase 1 step 7's format)"
  svc_user_run cat "$SENTINEL_NTFY_TOKEN" >/dev/null 2>&1 \
    || die "the atlas account cannot read $SENTINEL_NTFY_TOKEN ($(stat -c '%U:%G %a' "$SENTINEL_NTFY_TOKEN"); $ATLAS_ETC/secrets is $(stat -c '%U:%G %a' "$ATLAS_ETC/secrets"), must be root:atlas 750 and the file atlas:atlas 600 — 02's header names the writers that still set 700/710): the cpu worker's ntfy pushes would fail"
  log "alert path: $SENTINEL_NTFY_TOKEN readable by atlas (NTFY_TOKEN_FILE, KEY=VALUE format)"
}

# The root-side firewall telemetry exporter (header item 2b): installed, run once, output proven readable by atlas.
_sentinel_telemetry() {
  [[ -f "$SENTINEL_TELE_SRC" ]] || die "$SENTINEL_TELE_SRC is missing (the firewall telemetry exporter source)"
  bash -n "$SENTINEL_TELE_SRC" || die "$SENTINEL_TELE_SRC does not parse"
  install -m 755 -o root -g root "$SENTINEL_TELE_SRC" "$SENTINEL_TELE_BIN"
  "$SENTINEL_TELE_BIN" || die "$SENTINEL_TELE_BIN failed on its first run (journalctl -k or /var/lib/atlas not usable as root?)"
  [[ -s "$SENTINEL_TELE_FILE" ]] || die "$SENTINEL_TELE_BIN wrote no $SENTINEL_TELE_FILE"
  python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); assert {"ts", "ufw_block", "docker_egress_denied", "squid_denied", "errors"} <= set(d), sorted(d)' "$SENTINEL_TELE_FILE" \
    || die "$SENTINEL_TELE_FILE lacks the keys the package contract names (header)"
  svc_user_run cat "$SENTINEL_TELE_FILE" >/dev/null 2>&1 || die "the atlas account cannot read $SENTINEL_TELE_FILE ($(stat -c '%U:%G %a' "$SENTINEL_TELE_FILE"))"
  local errs
  errs="$(python3 -c 'import json, sys; print("; ".join(json.load(open(sys.argv[1]))["errors"]))' "$SENTINEL_TELE_FILE" 2>/dev/null || true)"
  [[ -z "$errs" ]] || warn "firewall telemetry: sources the exporter could not count on this run: $errs (the task reports them as unreadable, never as zero)"
  log "firewall telemetry exporter $SENTINEL_TELE_BIN -> $SENTINEL_TELE_FILE (root 644, readable by atlas)"
}

_sentinel_units() {
  export ATLAS_ETC ATLAS_OPT
  local u
  for u in atlas-sentinel atlas-prune; do
    render_template -m 644 "$ATLAS_DAY1_DIR/systemd/$u.service" "/etc/systemd/system/$u.service" ATLAS_ETC ATLAS_OPT
    install -m 644 "$ATLAS_DAY1_DIR/systemd/$u.timer" "/etc/systemd/system/$u.timer"
    grep -qF "EnvironmentFile=$REDIS_ENV" "/etc/systemd/system/$u.service" || die "$u.service does not load $REDIS_ENV (the broker URL for the enqueue)"
    grep -qF "Environment=HOME=$(getent passwd atlas | cut -d: -f6)" "/etc/systemd/system/$u.service" || die "$u.service does not set HOME to the atlas account's home"
  done
  grep -qF "ExecStartPre=/bin/sh -c 'test -x $ATLAS_ETC/secrets" /etc/systemd/system/atlas-sentinel.service || die "atlas-sentinel.service lost its secrets-readability ExecStartPre (the loud guard for the alert path)"
  grep -qF "ExecStartPre=-+$SENTINEL_TELE_BIN" /etc/systemd/system/atlas-sentinel.service || die "atlas-sentinel.service does not run the firewall telemetry exporter $SENTINEL_TELE_BIN before the enqueue"
  grep -q '^OnCalendar=\*-\*-\* 03:10:00' /etc/systemd/system/atlas-prune.timer || die "atlas-prune.timer must be the daily 03:10 calendar (Section 9.6 every 72 hours: daily check plus the >= 71 h stamp; a monotonic timer fires after every boot)"
  grep -q '^ExecCondition=.*-mmin +4259' /etc/systemd/system/atlas-prune.service || die "atlas-prune.service lost its >= 71 h ExecCondition stamp check (the 72-hour cadence)"
  grep -q '^StateDirectory=atlas-prune' /etc/systemd/system/atlas-prune.service || die "atlas-prune.service lost StateDirectory=atlas-prune (where the cadence stamp lives)"
  systemctl daemon-reload
  systemctl enable --now atlas-sentinel.timer >/dev/null
  systemctl enable --now atlas-prune.timer >/dev/null
  log "timers enabled: $(systemctl list-timers --no-legend atlas-sentinel.timer atlas-prune.timer 2>/dev/null | awk '{print $NF": next "$1" "$2" "$3}' | tr '\n' ';') (prune: daily check, runs when the stamp is >= 71 h old)"
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
  _sentinel_telemetry
  _sentinel_units
  _sentinel_pulse_now
  notify "Phase 2 step 8 done: Sentinel hourly timer (D6 feeds), 72-hour prune timer; first pulse in the ledger"
  log "step 08 done"
}
