"""Sentinel Protocol (Section 9.3; D6 feeds in config/sentinel-feeds.json; phase2/08-sentinel.sh; ledger table
sentinel_pulses).

One pulse (`atlas.tasks.sentinel_pulse`, cpu queue, enqueued hourly by atlas-sentinel.timer):
  1. pull every feed in SENTINEL_FEEDS through the allowlist proxy (HTTPS_PROXY from proxy.env; httpx trust_env=True);
     an unreachable or unparsable feed is logged "feed unreachable: <id>" and the pulse continues (the file's own rule:
     every URL is UNVERIFIED and "never a pulse failure");
  2. read the node telemetry sources; a source that cannot be read is logged "telemetry unreadable: <id>";
  3. keep a rolling history in SQLite under SENTINEL_LOG_DIR (history.sqlite3: readings, items);
  4. statistics first: deviations against the last `history_window` readings and the fixed thresholds of the file;
  5. write the pulse (JSON) to SENTINEL_LOG_DIR/pulses/... and to ledger.sentinel_pulses;
  6. ONLY when a threshold tripped: enqueue `atlas.tasks.sentinel_bluf` on the gpu queue, which asks the resident
     router model for a BLUF entry through the orchestrator (the Arbiter's generation lock; never a swap under an
     active session, 4.2 rule 6), writes the BLUF to the log, the ledger and the `sentinel` memory collection, and
     pushes it through ntfy. Owners are carried as fields: alaric for threats, silas for markets, under arthur (C12).

No model is invoked on a quiet pulse. Nothing here calls anything but the allowlisted feeds and loopback services.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from celery import shared_task

from atlas.ledger import Ledger

log = logging.getLogger("atlas.sentinel")

FEED_TIMEOUT_S = 20.0  # sentinel-feeds.json _meta.poll
USER_AGENT = "ATLAS-Sentinel/1.0 (+self-hosted node; RSS/JSON reader)"
OWNER_UNDER = "arthur"  # 9.3 owners row; C12
MARKET_TOPICS = ("crypto", "asx", "us-index", "index", "market", "price")

__all__ = [
    "Anomaly",
    "FeedReading",
    "PulseResult",
    "SentinelHistory",
    "detect",
    "pull_feed",
    "read_telemetry",
    "run_pulse",
    "sentinel_bluf",
    "sentinel_pulse",
    "topic_owner",
]


@dataclass
class FeedReading:
    feed_id: str
    kind: str
    ok: bool
    ts: float
    value: float | None = None  # json/csv series: the latest close
    series: list[tuple[float, float]] = field(default_factory=list)  # (timestamp, value)
    items: list[dict[str, str]] = field(default_factory=list)  # rss: {guid, title, ts}
    error: str = ""
    url: str = ""


@dataclass
class Anomaly:
    feed_id: str
    metric: str
    value: float
    threshold: float
    detail: str
    owner: str
    hemisphere: str
    topic: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PulseResult:
    ts: float
    feeds: dict[str, dict[str, Any]]
    telemetry: dict[str, dict[str, Any]]
    anomalies: list[Anomaly]
    unreachable: list[str]
    duration_s: float = 0.0
    pulse_file: str = ""
    task_id: str | None = None

    @property
    def status(self) -> str:
        return "anomaly" if self.anomalies else "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "status": self.status,
            "feeds": self.feeds,
            "telemetry": self.telemetry,
            "anomalies": [a.as_dict() for a in self.anomalies],
            "unreachable": self.unreachable,
            "duration_s": self.duration_s,
            "pulse_file": self.pulse_file,
            "task_id": self.task_id,
        }


def topic_owner(topic: str, feed_owner: str | None = None) -> str:
    """Silas for markets, Alaric for threats (9.3 owners row); the feed's own owner field wins when set."""
    if feed_owner:
        return feed_owner
    return "silas" if any(k in topic for k in MARKET_TOPICS) else "alaric"


# --- history ----------------------------------------------------------------------------------------------------------


class SentinelHistory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS readings (feed_id TEXT NOT NULL, ts REAL NOT NULL, "
            "value REAL, PRIMARY KEY (feed_id, ts))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS items (feed_id TEXT NOT NULL, guid TEXT NOT NULL, "
            "seen_ts REAL NOT NULL, title TEXT, PRIMARY KEY (feed_id, guid))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS telemetry (source_id TEXT NOT NULL, ts REAL NOT NULL, "
            "value_json TEXT, PRIMARY KEY (source_id, ts))"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def add_reading(self, feed_id: str, ts: float, value: float | None) -> None:
        self._conn.execute("INSERT OR REPLACE INTO readings VALUES (?, ?, ?)", (feed_id, ts, value))
        self._conn.commit()

    def add_series(self, feed_id: str, series: Sequence[tuple[float, float]]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO readings VALUES (?, ?, ?)", [(feed_id, ts, v) for ts, v in series]
        )
        self._conn.commit()

    def readings(self, feed_id: str, limit: int) -> list[tuple[float, float]]:
        rows = self._conn.execute(
            "SELECT ts, value FROM readings WHERE feed_id = ? AND value IS NOT NULL ORDER BY ts DESC LIMIT ?",
            (feed_id, limit),
        ).fetchall()
        return [(float(ts), float(v)) for ts, v in reversed(rows)]

    def new_items(self, feed_id: str, items: Sequence[Mapping[str, str]], now: float) -> list[dict[str, str]]:
        """Insert unseen items; return them."""
        fresh: list[dict[str, str]] = []
        for it in items:
            guid = it.get("guid") or it.get("title") or ""
            if not guid:
                continue
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO items VALUES (?, ?, ?, ?)", (feed_id, guid, now, it.get("title", ""))
            )
            if cur.rowcount:
                fresh.append(dict(it))
        self._conn.commit()
        return fresh

    def add_telemetry(self, source_id: str, ts: float, value: Mapping[str, Any]) -> None:
        self._conn.execute("INSERT OR REPLACE INTO telemetry VALUES (?, ?, ?)", (source_id, ts, json.dumps(value)))
        self._conn.commit()

    def prune(self, keep_days: int = 400) -> None:
        """Sentinel logs are kept 12 months (D9 / 10.4); the history table follows the same window."""
        cutoff = time.time() - keep_days * 86400
        self._conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
        self._conn.execute("DELETE FROM items WHERE seen_ts < ?", (cutoff,))
        self._conn.execute("DELETE FROM telemetry WHERE ts < ?", (cutoff,))
        self._conn.commit()


# --- feeds ------------------------------------------------------------------------------------------------------------


def _json_path(data: Any, path: str) -> Any:
    """`chart.result[0].indicators.quote[0].close` over parsed JSON (the file's `series.path` notation)."""
    cur = data
    for part in path.split("."):
        while part:
            if "[" in part:
                key, rest = part.split("[", 1)
                idx, rest = rest.split("]", 1)
                if key:
                    cur = cur[key]
                cur = cur[int(idx)]
                part = rest
            else:
                cur = cur[part]
                part = ""
    return cur


def _parse_rss(text: str) -> list[dict[str, str]]:
    root = ET.fromstring(text)
    items: list[dict[str, str]] = []
    ns_atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        guid = (it.findtext("guid") or it.findtext("link") or title).strip()
        items.append({"guid": guid, "title": title, "ts": (it.findtext("pubDate") or "").strip()})
    for it in root.iter(f"{ns_atom}entry"):
        title = (it.findtext(f"{ns_atom}title") or "").strip()
        guid = (it.findtext(f"{ns_atom}id") or title).strip()
        items.append({"guid": guid, "title": title, "ts": (it.findtext(f"{ns_atom}updated") or "").strip()})
    return items


def _parse_json_series(text: str, series: Mapping[str, Any]) -> list[tuple[float, float]]:
    data = json.loads(text)
    values = _json_path(data, series["path"])
    stamps = _json_path(data, series["timestamps"]) if series.get("timestamps") else list(range(len(values)))
    out: list[tuple[float, float]] = []
    for ts, v in zip(stamps, values, strict=False):
        if v is None:
            continue
        out.append((float(ts), float(v)))
    return out


def _parse_csv_series(text: str, series: Mapping[str, Any]) -> list[tuple[float, float]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    cols = [c.strip() for c in (series.get("csv_columns") or "").split(",")]
    close_col = "Close" if "Close" in cols or not cols else cols[-2]
    out: list[tuple[float, float]] = []
    for r in rows:
        try:
            date = r.get("Date") or r.get("date") or ""
            ts = datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp() if date else time.time()
            out.append((ts, float(r[close_col])))
        except (KeyError, ValueError):
            continue
    return out


def _fetch(client: httpx.Client, url: str) -> str:
    r = client.get(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}, timeout=FEED_TIMEOUT_S, follow_redirects=True
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.text


def pull_feed(feed: Mapping[str, Any], client: httpx.Client, now: float | None = None) -> FeedReading:
    """One feed, primary then fallback URL; never raises."""
    now = now or time.time()
    fid, kind = str(feed["id"]), str(feed.get("kind", "rss"))
    attempts: list[tuple[str, str]] = [(str(feed["url"]), kind)]
    if feed.get("fallback_url"):
        attempts.append((str(feed["fallback_url"]), str(feed.get("fallback_kind") or kind)))
    last_err = ""
    for url, k in attempts:
        try:
            text = _fetch(client, url)
            if k == "rss":
                return FeedReading(fid, k, True, now, items=_parse_rss(text), url=url)
            series = (
                _parse_json_series(text, feed["series"]) if k == "json" else _parse_csv_series(text, feed["series"])
            )
            if not series:
                raise RuntimeError("empty series")
            return FeedReading(fid, k, True, now, value=series[-1][1], series=series, url=url)
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, IndexError, TypeError, ET.ParseError) as exc:
            last_err = f"{url}: {type(exc).__name__}: {exc}"
            log.warning("feed unreachable: %s (%s)", fid, last_err)
    return FeedReading(fid, kind, False, now, error=last_err, url=attempts[0][0])


# --- telemetry --------------------------------------------------------------------------------------------------------


def _read_loadavg() -> dict[str, float]:
    parts = Path("/proc/loadavg").read_text().split()
    return {"load1": float(parts[0]), "load5": float(parts[1]), "nproc": float(os.cpu_count() or 1)}


def _read_meminfo() -> dict[str, float]:
    kv: dict[str, float] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable"):
            kv[k] = float(v.split()[0])
    return {"mem_available_pct": 100.0 * kv["MemAvailable"] / kv["MemTotal"], **kv}


def _read_gtt() -> dict[str, float]:
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if "-" in card.name:
            continue
        dev = card / "device"
        try:
            if (dev / "vendor").read_text().strip() != "0x1002":
                continue
            used = float((dev / "mem_info_gtt_used").read_text().strip())
            total = float((dev / "mem_info_gtt_total").read_text().strip())
            return {
                "gtt_used_pct": 100.0 * used / total if total else 0.0,
                "gtt_used_bytes": used,
                "gtt_total_bytes": total,
            }
        except OSError:
            continue
    raise OSError("no AMD GPU counters under /sys/class/drm")


def _read_thermal() -> dict[str, float]:
    temps: list[float] = []
    for f in Path("/sys/class/hwmon").glob("hwmon*/temp*_input"):
        try:
            temps.append(float(f.read_text().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    if not temps:
        raise OSError("no hwmon temperature inputs")
    return {"temp_c_max": max(temps), "sensors": float(len(temps))}


def _read_disk(paths: Sequence[str] = ("/", "/srv/atlas", "/srv/backups", "/srv/cold")) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in paths:
        try:
            st = os.statvfs(p)
            used_pct = 100.0 * (1.0 - st.f_bavail / st.f_blocks) if st.f_blocks else 0.0
            out[p] = round(used_pct, 2)
        except OSError:
            continue
    if not out:
        raise OSError("statvfs failed for every path")
    return out


def _read_services(units: Sequence[str]) -> dict[str, Any]:
    if not shutil.which("systemctl"):
        raise OSError("systemctl not available")
    proc = subprocess.run(["systemctl", "is-active", *units], capture_output=True, text=True, timeout=30, check=False)
    states = proc.stdout.split()
    return {u: (states[i] if i < len(states) else "unknown") for i, u in enumerate(units)}


def _read_containers(names: Sequence[str]) -> dict[str, Any]:
    if not shutil.which("docker"):
        raise OSError("docker not available")
    out: dict[str, Any] = {}
    for n in names:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Health.Status}}|{{.State.Status}}", n],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        out[n] = proc.stdout.strip() if proc.returncode == 0 else "absent"
    return out


def _read_firewall() -> dict[str, float]:
    if not shutil.which("journalctl"):
        raise OSError("journalctl not available")
    proc = subprocess.run(
        ["journalctl", "-k", "--since", "-1h", "-o", "cat", "--no-pager"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = proc.stdout.splitlines()
    ufw = sum(1 for ln in lines if "[UFW BLOCK]" in ln)
    dk = sum(1 for ln in lines if "ATLAS docker egress denied" in ln)
    squid = 0.0
    access = Path("/var/log/squid/access.log")
    if access.is_file():
        cutoff = time.time() - 3600
        try:
            with access.open(encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    parts = ln.split()
                    if parts and "TCP_DENIED" in ln:
                        try:
                            if float(parts[0]) >= cutoff:
                                squid += 1
                        except ValueError:
                            continue
        except OSError:
            pass
    return {
        "denied_per_hour": float(ufw + dk + squid),
        "ufw_block": float(ufw),
        "docker_egress_denied": float(dk),
        "squid_denied": squid,
    }


def _read_backup() -> dict[str, float]:
    """Newest restic snapshot age via `journalctl -u atlas-aegis` is what the file names; restic itself needs root.
    Reading the unit's last successful run time from systemd is the atlas-readable equivalent."""
    if not shutil.which("systemctl"):
        raise OSError("systemctl not available")
    proc = subprocess.run(
        [
            "systemctl",
            "show",
            "atlas-aegis.service",
            "-p",
            "ExecMainExitTimestampMonotonic",
            "-p",
            "ExecMainStartTimestamp",
            "-p",
            "Result",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    kv = dict(ln.split("=", 1) for ln in proc.stdout.splitlines() if "=" in ln)
    start = kv.get("ExecMainStartTimestamp", "").strip()
    if not start:
        raise OSError("atlas-aegis.service has never run")
    # systemd prints e.g. "Fri 2026-09-25 02:30:01 AEST"; the weekday and zone name are dropped for parsing.
    parts = start.split()
    try:
        dt = datetime.strptime(" ".join(parts[1:3]), "%Y-%m-%d %H:%M:%S")
    except (ValueError, IndexError) as exc:
        raise OSError(f"unparseable timestamp {start!r}") from exc
    age_h = (time.time() - dt.timestamp()) / 3600.0
    return {
        "snapshot_age_hours": round(age_h, 2),
        "last_result_ok": 1.0 if kv.get("Result", "").strip() == "success" else 0.0,
    }


def read_telemetry(sources: Sequence[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[Anomaly], list[str]]:
    """Each telemetry source of the file; unreadable ones are logged and listed, never fatal."""
    readings: dict[str, dict[str, Any]] = {}
    anomalies: list[Anomaly] = []
    unreadable: list[str] = []
    for src in sources:
        sid = str(src["id"])
        th = dict(src.get("thresholds") or {})
        try:
            if sid == "load":
                v = _read_loadavg()
                ratio = v["load1"] / v["nproc"]
                v["load1_over_nproc"] = round(ratio, 3)
                if ratio > float(th.get("load1_over_nproc", 2.0)):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "load1_over_nproc",
                            ratio,
                            float(th["load1_over_nproc"]),
                            f"load1 {v['load1']:.1f} on {int(v['nproc'])} cpus",
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "memory":
                v = _read_meminfo()
                if v["mem_available_pct"] < float(th.get("mem_available_pct_min", 5.0)):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "mem_available_pct",
                            v["mem_available_pct"],
                            float(th["mem_available_pct_min"]),
                            f"MemAvailable {v['mem_available_pct']:.1f}%",
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "gtt":
                v = _read_gtt()
                if v["gtt_used_pct"] > float(th.get("gtt_used_pct_max", 92.0)):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "gtt_used_pct",
                            v["gtt_used_pct"],
                            float(th["gtt_used_pct_max"]),
                            f"GTT {v['gtt_used_pct']:.1f}% used",
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "thermal":
                v = _read_thermal()
                if v["temp_c_max"] > float(th.get("temp_c_max", 95.0)):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "temp_c_max",
                            v["temp_c_max"],
                            float(th["temp_c_max"]),
                            f"hottest sensor {v['temp_c_max']:.0f} C",
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "disk":
                v = _read_disk()
                for p, pct in v.items():
                    limit = (
                        float(th.get("srv_atlas_used_pct_max", 85.0))
                        if p == "/srv/atlas"
                        else float(th.get("used_pct_max", 90.0))
                    )
                    if pct > limit:
                        anomalies.append(
                            Anomaly(
                                sid,
                                f"used_pct:{p}",
                                pct,
                                limit,
                                f"{p} {pct:.1f}% used",
                                "alaric",
                                "estate",
                                "node-telemetry",
                            )
                        )
            elif sid == "services":
                units = [u for u in str(src.get("source", "")).replace("systemctl is-active", "").split() if u]
                v = _read_services(units)
                down = [u for u, s in v.items() if s != "active"]
                if down and th.get("inactive_alert", True):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "inactive",
                            float(len(down)),
                            0.0,
                            "inactive: " + ", ".join(down),
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "containers":
                names = [n for n in str(src.get("source", "")).split("'")[-1].split() if n] or []
                if not names:
                    names = str(src.get("source", "")).split()[-8:]
                v = _read_containers(names)
                bad = [n for n, s in v.items() if s.startswith("unhealthy") or s == "absent" or "|exited" in s]
                if bad and th.get("unhealthy_alert", True):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "unhealthy",
                            float(len(bad)),
                            0.0,
                            "unhealthy: " + ", ".join(bad),
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "backup":
                v = _read_backup()
                if v["snapshot_age_hours"] > float(th.get("snapshot_age_hours_max", 36)):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "snapshot_age_hours",
                            v["snapshot_age_hours"],
                            float(th["snapshot_age_hours_max"]),
                            f"last AEGIS run {v['snapshot_age_hours']:.0f} h ago",
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            elif sid == "firewall":
                v = _read_firewall()
                if v["denied_per_hour"] > float(th.get("denied_per_hour_max", 200)):
                    anomalies.append(
                        Anomaly(
                            sid,
                            "denied_per_hour",
                            v["denied_per_hour"],
                            float(th["denied_per_hour_max"]),
                            f"{int(v['denied_per_hour'])} denied connections in the last hour",
                            "alaric",
                            "estate",
                            "node-telemetry",
                        )
                    )
            else:
                raise OSError(f"no reader for telemetry source {sid!r}")
            readings[sid] = {"ok": True, **{k: val for k, val in v.items()}}
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, ZeroDivisionError) as exc:
            log.warning("telemetry unreadable: %s (%s)", sid, exc)
            readings[sid] = {"ok": False, "error": str(exc)}
            unreadable.append(sid)
    return readings, anomalies, unreadable


# --- detection --------------------------------------------------------------------------------------------------------


def _pct_change(series: Sequence[tuple[float, float]], seconds: float) -> float | None:
    """Percent change between the latest value and the last value at least `seconds` older."""
    if len(series) < 2:
        return None
    last_ts, last_v = series[-1]
    for ts, v in reversed(series[:-1]):
        if last_ts - ts >= seconds * 0.9 and v:
            return 100.0 * (last_v - v) / v
    return None


def detect(
    feed: Mapping[str, Any], reading: FeedReading, history: SentinelHistory, now: float, window: int
) -> list[Anomaly]:
    fid = str(feed["id"])
    topic = str(feed.get("topic", ""))
    owner = topic_owner(topic, feed.get("owner"))
    hemi = str(feed.get("hemisphere") or "estate")
    th = dict(feed.get("thresholds") or {})
    out: list[Anomaly] = []
    if not reading.ok:
        return out
    if reading.kind == "rss":
        fresh = history.new_items(fid, reading.items, now)
        limit = th.get("new_items_per_hour_max")
        if limit is not None and len(fresh) > int(limit):
            out.append(
                Anomaly(
                    fid,
                    "new_items_per_hour",
                    float(len(fresh)),
                    float(limit),
                    f"{len(fresh)} new items in one pulse",
                    owner,
                    hemi,
                    topic,
                )
            )
        for kw in th.get("keywords_alert") or []:
            hits = [it["title"] for it in fresh if kw.lower() in it.get("title", "").lower()]
            if hits:
                out.append(
                    Anomaly(fid, f"keyword:{kw}", float(len(hits)), 1.0, "; ".join(hits[:3]), owner, hemi, topic)
                )
        return out
    history.add_series(fid, reading.series)
    series = history.readings(fid, max(window, len(reading.series) + window))
    for key, seconds in (
        ("abs_pct_change_1h", 3600),
        ("abs_pct_change_24h", 86400),
        ("abs_pct_change_1d", 86400),
        ("abs_pct_change_5d", 5 * 86400),
    ):
        if key not in th:
            continue
        chg = _pct_change(series, seconds)
        if chg is not None and abs(chg) > float(th[key]):
            out.append(
                Anomaly(
                    fid,
                    key,
                    round(chg, 3),
                    float(th[key]),
                    f"{fid} moved {chg:+.2f}% over {seconds // 3600} h (latest {reading.value})",
                    owner,
                    hemi,
                    topic,
                )
            )
    return out


# --- the pulse --------------------------------------------------------------------------------------------------------


def load_feeds(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"SENTINEL_FEEDS={p} does not exist (phase2/08-sentinel.sh installs it)")
    return json.loads(p.read_text(encoding="utf-8"))


def run_pulse(
    feeds_doc: Mapping[str, Any],
    log_dir: str | Path,
    *,
    ledger: Ledger | None = None,
    task_id: str | None = None,
    client: httpx.Client | None = None,
    telemetry: bool = True,
    now: float | None = None,
) -> PulseResult:
    """The whole pulse without Celery, so phase2/08-sentinel.sh's `--wait` run and the tests share one path."""
    t0 = time.monotonic()
    now = now or time.time()
    log_dir = Path(log_dir)
    history = SentinelHistory(log_dir / "history.sqlite3")
    window = int((feeds_doc.get("detection") or {}).get("history_window", 24))
    own_client = client is None
    # Feeds leave the node: the allowlist proxy from proxy.env applies (trust_env=True), rule §7.1.
    client = client or httpx.Client(trust_env=True, timeout=FEED_TIMEOUT_S)
    feeds_out: dict[str, dict[str, Any]] = {}
    anomalies: list[Anomaly] = []
    unreachable: list[str] = []
    try:
        for feed in feeds_doc.get("feeds") or []:
            reading = pull_feed(feed, client, now)
            feeds_out[reading.feed_id] = {
                "ok": reading.ok,
                "kind": reading.kind,
                "value": reading.value,
                "items": len(reading.items),
                "url": reading.url,
                "error": reading.error,
            }
            if not reading.ok:
                unreachable.append(reading.feed_id)
                continue
            if reading.kind != "rss":
                history.add_reading(reading.feed_id, now, reading.value)
            anomalies.extend(detect(feed, reading, history, now, window))
        tele_out: dict[str, dict[str, Any]] = {}
        if telemetry:
            tele_out, tele_anoms, tele_bad = read_telemetry(feeds_doc.get("telemetry") or [])
            anomalies.extend(tele_anoms)
            unreachable.extend(f"telemetry:{s}" for s in tele_bad)
            for sid, v in tele_out.items():
                if v.get("ok"):
                    history.add_telemetry(sid, now, v)
        history.prune()
    finally:
        history.close()
        if own_client:
            client.close()
    result = PulseResult(
        ts=now,
        feeds=feeds_out,
        telemetry=tele_out,
        anomalies=anomalies,
        unreachable=unreachable,
        duration_s=round(time.monotonic() - t0, 2),
        task_id=task_id,
    )
    stamp = datetime.fromtimestamp(now, tz=UTC)
    pulse_dir = log_dir / "pulses" / stamp.strftime("%Y") / stamp.strftime("%m")
    pulse_dir.mkdir(parents=True, exist_ok=True)
    pulse_file = pulse_dir / f"pulse-{stamp.strftime('%Y%m%dT%H%M%SZ')}.json"
    pulse_file.write_text(json.dumps(result.as_dict(), indent=1, default=str), encoding="utf-8")
    result.pulse_file = str(pulse_file)
    if ledger is not None:
        ledger.insert_sentinel_pulse(
            task_id=task_id,
            status=result.status,
            feeds=feeds_out,
            anomalies=[a.as_dict() for a in anomalies],
            alerted=bool(anomalies),
            duration_s=result.duration_s,
            error=("unreachable: " + ", ".join(unreachable)) if unreachable else None,
        )
    log.info(
        "sentinel pulse: %d feeds, %d unreachable, %d anomalies, %.1fs -> %s",
        len(feeds_out),
        len(unreachable),
        len(anomalies),
        result.duration_s,
        pulse_file,
    )
    return result


# --- BLUF (model, only on anomaly) ------------------------------------------------------------------------------------


def bluf_messages(anomalies: Sequence[Mapping[str, Any]], pulse_ts: float) -> list[dict[str, str]]:
    lines = [
        f"- feed {a['feed_id']} | metric {a['metric']} | value {a['value']} vs threshold {a['threshold']} | "
        f"owner {a['owner']} | {a['detail']}"
        for a in anomalies
    ]
    when = datetime.fromtimestamp(pulse_ts, tz=UTC).isoformat()
    return [
        {
            "role": "system",
            "content": (
                "You are the Sentinel desk of ATLAS, reporting to Arthur. Write a BLUF entry (Bottom Line Up Front): "
                "first "
                "line the bottom line in at most 20 words; then 'Impact:', 'Evidence:', 'Action:' each one line. Plain "
                "text, no headings, no speculation beyond the evidence listed. Markets belong to Silas, threats to "
                "Alaric; "
                "name the owner on the Action line."
            ),
        },
        {"role": "user", "content": f"Pulse at {when}. Threshold trips:\n" + "\n".join(lines)},
    ]


def compose_bluf(anomalies: Sequence[Mapping[str, Any]], pulse_ts: float, text: str) -> dict[str, Any]:
    owners = sorted({str(a["owner"]) for a in anomalies})
    return {
        "ts": time.time(),
        "pulse_ts": pulse_ts,
        "format": "BLUF",
        "text": text.strip(),
        "owners": owners,
        "under": OWNER_UNDER,
        "anomalies": [dict(a) for a in anomalies],
        "hemispheres": sorted({str(a["hemisphere"]) for a in anomalies}),
    }


# --- Celery tasks -----------------------------------------------------------------------------------------------------


@shared_task(name="atlas.tasks.sentinel_pulse", bind=True)
def sentinel_pulse(self: Any) -> dict[str, Any]:
    from atlas.celery_app import TASK_SENTINEL_BLUF, app
    from atlas.tasks import TaskRecord, notify

    rec = TaskRecord(self.request.id, "sentinel")
    env = os.environ
    try:
        feeds_doc = load_feeds(env.get("SENTINEL_FEEDS") or "/opt/atlas/day1/config/sentinel-feeds.json")
        log_dir = env.get("SENTINEL_LOG_DIR") or "/srv/atlas/data/sentinel"
        result = run_pulse(feeds_doc, log_dir, ledger=rec.ledger, task_id=self.request.id)
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    out = result.as_dict()
    if result.anomalies:
        try:
            sub = app.send_task(
                TASK_SENTINEL_BLUF,
                args=[[a.as_dict() for a in result.anomalies], result.ts, self.request.id],
                queue="gpu",
            )
            out["bluf_task_id"] = sub.id
        except Exception as exc:
            # The BLUF could not be queued: the Principal still hears about it, in raw form (rule §7.4).
            log.error("sentinel: could not enqueue the BLUF task (%s); pushing the raw anomaly list", exc)
            notify(
                "\n".join(a.detail for a in result.anomalies),
                title="ATLAS Sentinel (raw, model not reached)",
                priority="high",
                tags=["warning"],
            )
            out["bluf_task_id"] = None
    return rec.done(out)


@shared_task(name="atlas.tasks.sentinel_bluf", bind=True)
def sentinel_bluf(
    self: Any, anomalies: list[dict[str, Any]], pulse_ts: float, parent_task_id: str | None = None
) -> dict[str, Any]:
    """gpu queue: the resident router model writes the BLUF through the orchestrator (Arbiter-locked)."""
    from atlas.memory import MemoryStoreError, build_memory_store
    from atlas.tasks import OrchestratorClient, TaskRecord, notify

    rec = TaskRecord(self.request.id, "sentinel-bluf", queue="gpu", parent_task_id=parent_task_id)
    env = os.environ
    engine = env.get("ATLAS_CLASSIFIER_ENGINE") or "router-qwen3.5-4b"
    try:
        client = OrchestratorClient()
        try:
            text = client.generate(
                engine, bluf_messages(anomalies, pulse_ts), max_tokens=400, temperature=0.2, task_id=self.request.id
            )
        finally:
            client.close()
        if not text.strip():
            raise RuntimeError(f"the resident model {engine} returned an empty BLUF")
        entry = compose_bluf(anomalies, pulse_ts, text)
        log_dir = Path(env.get("SENTINEL_LOG_DIR") or "/srv/atlas/data/sentinel")
        (log_dir / "bluf").mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(entry["ts"], tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        (log_dir / "bluf" / f"bluf-{stamp}.json").write_text(json.dumps(entry, indent=1), encoding="utf-8")
        written = False
        try:
            store = build_memory_store(with_graph=False)
            wr = store.write(
                "sentinel",
                [entry["text"]],
                [
                    {
                        "kind": "sentinel-bluf",
                        "owners": ",".join(entry["owners"]),
                        "under": OWNER_UNDER,
                        "pulse_ts": pulse_ts,
                        "feeds": ",".join(sorted({a["feed_id"] for a in anomalies})),
                    }
                ],
                hemisphere="estate",
                temporal=True,
                ttl_hours=24 * 365,
            )
            written = wr.written
        except MemoryStoreError as exc:
            log.error("sentinel BLUF not written to memory: %s", exc)
        pushed = notify(
            entry["text"],
            title=f"ATLAS Sentinel: {', '.join(entry['owners'])} under {OWNER_UNDER}",
            priority="high",
            tags=["rotating_light"],
        )
        rec.ledger.insert_sentinel_pulse(
            task_id=self.request.id,
            status="bluf",
            anomalies=anomalies,
            alerted=pushed,
            error=None if pushed else "ntfy push failed",
        )
    except Exception as exc:
        rec.failed(f"{type(exc).__name__}: {exc}")
        raise
    return rec.done({"bluf": entry, "memory_written": written, "ntfy": pushed})
