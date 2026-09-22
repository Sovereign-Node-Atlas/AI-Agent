#!/usr/bin/env python3
"""Fill sheet "4 Verification" of docs/ATLAS_BUILD_BASELINE.xlsx from the Day 1 verify.jsonl.

Section 21 of the framework review lists V1..V23. The Day 1 scripts record results with
`record_v` (lib/common.sh) as JSON lines: {"ts","phase","id","result","msg"} where result is
one of pass|fail|deferred|info and id is V1..V23 or a half: V3a/V3b, V14a/V14b.

Rules for turning records into one row per V-item:
  * latest record per exact id wins, except V4 (per engine) where every latest-per-message
    record counts and any fail makes the row Fail;
  * halves combine: both pass -> Pass; either fail -> Fail; otherwise Deferred (one half not
    yet run means the item is not proven, and the sheet has no "partial" value);
  * info records never set Pass; they are written into the evidence column with the row left
    as "Not yet run" unless another record exists (V1 is informational only, Section 21).

Usage:
  fill-workbook.py [--verify /var/lib/atlas/day1/verify.jsonl] [--workbook docs/ATLAS_BUILD_BASELINE.xlsx]
                   [--out same-as-workbook] [--print]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

RESULT_WORD = {"pass": "Pass", "fail": "Fail", "deferred": "Deferred"}
HALVES = {"V3": ("V3a", "V3b"), "V14": ("V14a", "V14b")}
PER_ENGINE = {"V4"}


def load_records(path: Path) -> list[dict]:
    records = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"warning: line {n} is not JSON ({e}); skipped", file=sys.stderr)
            continue
        if not {"id", "result", "msg"} <= rec.keys():
            print(f"warning: line {n} lacks id/result/msg; skipped", file=sys.stderr)
            continue
        records.append(rec)
    return records


def latest_by_id(records: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rec in records:  # file order is time order; later lines overwrite
        out[rec["id"]] = rec
    return out


def per_engine_rows(records: list[dict], vid: str) -> tuple[str | None, str]:
    """V4 is recorded once per engine; key the latest record by its message prefix (engine name)."""
    latest: "OrderedDict[str, dict]" = OrderedDict()
    for rec in records:
        if rec["id"] != vid:
            continue
        key = rec["msg"].split(":", 1)[0].strip() or rec["msg"]
        latest[key] = rec
    if not latest:
        return None, ""
    results = {r["result"] for r in latest.values()}
    evidence = "; ".join(r["msg"] for r in latest.values())
    if "fail" in results:
        return "Fail", evidence
    if results <= {"pass"}:
        return "Pass", evidence
    return "Deferred", evidence


def combine(vid: str, records: list[dict], latest: dict[str, dict]) -> tuple[str, str]:
    if vid in PER_ENGINE:
        word, evidence = per_engine_rows(records, vid)
        return (word or "Not yet run"), evidence
    if vid in HALVES:
        parts = [latest.get(h) for h in HALVES[vid]]
        evidence = " | ".join(f"{h}: {p['msg']}" for h, p in zip(HALVES[vid], parts) if p)
        if not any(parts):
            return "Not yet run", ""
        results = [p["result"] if p else "missing" for p in parts]
        if "fail" in results:
            return "Fail", evidence
        if all(r == "pass" for r in results):
            return "Pass", evidence
        return "Deferred", evidence
    rec = latest.get(vid)
    if rec is None:
        return "Not yet run", ""
    if rec["result"] == "info":
        return "Not yet run", f"info: {rec['msg']}"
    return RESULT_WORD.get(rec["result"], "Not yet run"), rec["msg"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", default="/var/lib/atlas/day1/verify.jsonl")
    ap.add_argument("--workbook", default="docs/ATLAS_BUILD_BASELINE.xlsx")
    ap.add_argument("--out", default=None, help="write here instead of overwriting the workbook")
    ap.add_argument("--print", action="store_true", help="print the table without touching the workbook")
    args = ap.parse_args()

    verify = Path(args.verify)
    if not verify.exists():
        print(f"no verify file at {verify}", file=sys.stderr)
        return 2
    records = load_records(verify)
    latest = latest_by_id(records)
    table = {f"V{n}": combine(f"V{n}", records, latest) for n in range(1, 24)}

    if args.print:
        for vid, (word, evidence) in table.items():
            print(f"{vid:<4} {word:<12} {evidence}")
        return 0

    try:
        from openpyxl import load_workbook
    except ImportError:
        print("openpyxl is required: pip install openpyxl", file=sys.stderr)
        return 2
    wb_path = Path(args.workbook)
    wb = load_workbook(wb_path)
    ws = wb["4 Verification"]
    header = [c.value for c in ws[4]]
    col_result = header.index("Result") + 1
    col_evidence = header.index("Evidence or notes") + 1
    written = 0
    for row in range(5, ws.max_row + 1):
        vid = ws.cell(row=row, column=1).value
        if vid not in table:
            continue
        word, evidence = table[vid]
        ws.cell(row=row, column=col_result, value=word)
        if evidence:
            ws.cell(row=row, column=col_evidence, value=evidence[:2000])
        written += 1
    out = Path(args.out) if args.out else wb_path
    wb.save(out)
    print(f"wrote {written} rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
