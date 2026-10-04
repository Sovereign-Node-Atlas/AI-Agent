#!/usr/bin/env bash
# phase2/atlas-sentinel-telemetry.sh — installed by phase2/08-sentinel.sh as /usr/local/sbin/atlas-sentinel-telemetry
# (install -m 755; a repository file so shellcheck covers it). Run as ROOT by atlas-sentinel.service's
# `ExecStartPre=-+...` right before each hourly enqueue (fix round 2): the D6 "firewall" telemetry item of
# config/sentinel-feeds.json needs the kernel journal ([UFW BLOCK], [ATLAS docker egress denied]) and squid's access.log
# (TCP_DENIED), which the atlas account cannot read (it is in no journal or squid log group, CONVENTIONS.md §2). This
# exporter counts the last hour and writes /var/lib/atlas/telemetry/firewall.json (root:root 644) into a ROOT-OWNED
# directory under root-owned /var/lib/atlas (phase1/03-mounts.sh), so no atlas-planted symlink is ever followed and the
# worker reads a plain file. Contract for the package (atlas.tasks.sentinel._read_firewall): read
# FIREWALL_TELEMETRY_FILE (orchestrator.env) when it exists and its `ts` is less than 2 h old, else report
# "telemetry unreadable: firewall" — never a healthy zero. A source this exporter could not read is listed in `errors`
# and its count is null, so the reader can tell "nothing denied" from "could not count".
set -Eeuo pipefail

OUT_DIR=/var/lib/atlas/telemetry
OUT="$OUT_DIR/firewall.json"
ACCESS=/var/log/squid/access.log
WINDOW_S=3600

[[ -d /var/lib/atlas && ! -L /var/lib/atlas ]] || { echo "atlas-sentinel-telemetry: /var/lib/atlas missing or a symlink" >&2; exit 1; }
install -d -m 755 -o root -g root "$OUT_DIR"
[[ -d "$OUT_DIR" && ! -L "$OUT_DIR" ]] || { echo "atlas-sentinel-telemetry: $OUT_DIR is not a directory" >&2; exit 1; }

ufw=""; dk=""; squid=""
errors=()
if klog="$(journalctl -k --since "-${WINDOW_S}s" -o cat --no-pager 2>&1)"; then
  ufw="$(grep -c '\[UFW BLOCK\]' <<<"$klog" || true)"
  dk="$(grep -c 'ATLAS docker egress denied' <<<"$klog" || true)"
else
  errors+=("journalctl -k: ${klog:0:160}")
fi
if [[ -r "$ACCESS" ]]; then
  cutoff=$(( $(date +%s) - WINDOW_S ))
  # squid's default log format starts with the epoch time (seconds.ms); TCP_DENIED marks an allowlist refusal.
  squid="$(awk -v t="$cutoff" '$1+0 >= t && /TCP_DENIED/' "$ACCESS" | wc -l | tr -d ' ')"
elif [[ -e "$ACCESS" ]]; then
  errors+=("$ACCESS exists but is unreadable")
else
  errors+=("$ACCESS absent (squid not logging?)")
fi

tmp="$(mktemp -p "$OUT_DIR" .firewall.XXXXXX)"
TELE_TS="$(date +%s)" TELE_WINDOW="$WINDOW_S" TELE_UFW="$ufw" TELE_DK="$dk" TELE_SQUID="$squid" TELE_ERRORS="$(printf '%s\n' "${errors[@]+"${errors[@]}"}")" \
python3 - >"$tmp" <<'PY'
import json, os
def num(v: str):
    return int(v) if v.strip().isdigit() else None
ufw, dk, squid = (num(os.environ[k]) for k in ("TELE_UFW", "TELE_DK", "TELE_SQUID"))
counted = [x for x in (ufw, dk, squid) if x is not None]
errors = [e for e in os.environ.get("TELE_ERRORS", "").splitlines() if e.strip()]
print(json.dumps({
    "source": "atlas-sentinel-telemetry",
    "ts": int(os.environ["TELE_TS"]),
    "window_s": int(os.environ["TELE_WINDOW"]),
    "ufw_block": ufw,
    "docker_egress_denied": dk,
    "squid_denied": squid,
    "denied_per_hour": sum(counted) if len(counted) == 3 else None,
    "errors": errors,
}, indent=1))
PY
install -m 644 -o root -g root "$tmp" "$OUT"
rm -f "$tmp"
echo "atlas-sentinel-telemetry: wrote $OUT (ufw=${ufw:-?} docker=${dk:-?} squid=${squid:-?} errors=${#errors[@]})"
