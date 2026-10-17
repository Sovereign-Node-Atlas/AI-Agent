#!/usr/bin/env python3
"""Build docs/ATLAS_BUILD_BASELINE.xlsx, the Principal's workbook, from docs/ATLAS_FRAMEWORK_REVIEW.md.

What it builds (decision D-27, approved by the Principal on 2026-10-16: the builder is kept in the repository beside
tools/fill-workbook.py instead of in a session's scratch directory):

  * Summary: what this baseline version changed, how to use the workbook, and a progress table of formulas over the
    response columns of sheets 1 to 5.
  * Sheets 1 to 5 follow the document's own tables, row for row: 1 Decisions (Section 19), 2 Resolutions (Section 18),
    3 Risks (Section 20), 4 Verification (Section 21) and 5 Checklist (the boxes of Section 22). Each has a response
    column with a dropdown and green, amber and red formatting. Sheet 4 is filled during Day 1 by
    tools/fill-workbook.py from the node's verify.jsonl (into a copy; `atlas-day1.sh report`).
  * The Ref sheets are reference copies of the document's tables and lists (Sections 2, 3.7, 4.1, 5.1, 5.3, 6.1,
    6.2, 8.2, 8.3, 13, 14.1, 14.3, 15.1, 15.2, 15.5, 16.1 to 16.3 and 17), with the notes written below.

The strings in this file (sheet subtitles, captions, notes, the Summary) are the only text that is not read from the
document; they are updated with each baseline version (v0.4.0 here). Run it in the repository, never on the node,
with any Python 3.12 that has openpyxl, after the document is final:

  python3 scripts/day1/tools/build-baseline-workbook.py            # rewrites docs/ATLAS_BUILD_BASELINE.xlsx
  python3 scripts/day1/tools/build-baseline-workbook.py --out X.xlsx --doc Y.md   # tests (tools/build_workbook_test.sh)

It stops if a heading or table it reads is missing, so a renamed section fails loudly rather than leaving a sheet
empty. Paths are derived from the repository root (this file is scripts/day1/tools/ inside it). Until v0.4.0 the
builder lived in a scratch directory and read six of its Ref tables from CSV copies of the document's tables; those
tables (8.3, 13, 14.3, 15.1, 15.2 and 15.5) are now read from the document itself, which is where the copies came from.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

REPO = Path(__file__).resolve().parents[3]
DEFAULT_MD = REPO / "docs" / "ATLAS_FRAMEWORK_REVIEW.md"
DEFAULT_OUT = REPO / "docs" / "ATLAS_BUILD_BASELINE.xlsx"

_ap = argparse.ArgumentParser(description="Build the baseline workbook from the framework review document.")
_ap.add_argument("--doc", type=Path, default=DEFAULT_MD, help=f"the document (default {DEFAULT_MD})")
_ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"the workbook to write (default {DEFAULT_OUT})")
ARGS = _ap.parse_args()
MD: Path = ARGS.doc
OUT: Path = ARGS.out

LINES = MD.read_text(encoding="utf-8").splitlines()


def clean(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = s.replace("`", "")
    return s


def md_table_after(heading_prefix: str) -> list[list[str]]:
    """Return rows (list of lists) of the first markdown table after a heading line."""
    start = None
    for i, ln in enumerate(LINES):
        if ln.startswith(heading_prefix):
            start = i
            break
    if start is None:
        raise SystemExit("heading not found: " + heading_prefix)
    i = start + 1
    while i < len(LINES) and not LINES[i].startswith("|"):
        i += 1
    rows = []
    while i < len(LINES) and LINES[i].startswith("|"):
        cells = [clean(c) for c in LINES[i].strip().strip("|").split("|")]
        if not all(re.fullmatch(r"-+", c) for c in cells):
            rows.append(cells)
        i += 1
    return rows


def md_checklist() -> list[tuple[str, str, bool]]:
    """Section 22: returns list of (group, item, done)."""
    start = next(i for i, ln in enumerate(LINES) if ln.startswith("## 22."))
    out, group = [], ""
    for ln in LINES[start + 1:]:
        if ln.startswith("## "):
            break
        m = re.match(r"\*\*(.+?)\*\*", ln.strip())
        if m:
            group = m.group(1)
        elif ln.strip().startswith("- [ ]") or ln.strip().startswith("- [x]"):
            done = ln.strip().startswith("- [x]")
            out.append((group, clean(ln.strip()[5:]), done))
    return out


def md_numbered_after(heading_prefix: str) -> list[tuple[str, str]]:
    start = next(i for i, ln in enumerate(LINES) if ln.startswith(heading_prefix))
    out = []
    for ln in LINES[start + 1:]:
        if ln.startswith("#"):
            break
        m = re.match(r"\s*(\d+[a-z]?)\.\s+(.*)", ln)
        if m:
            out.append((m.group(1), clean(m.group(2))))
    return out


# ---------- styling ----------
FONT = "Arial"
F_BASE = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_TITLE = Font(name=FONT, size=14, bold=True)
F_NOTE = Font(name=FONT, size=9, italic=True, color="555555")
FILL_HEAD = PatternFill("solid", fgColor="1F3864")
FILL_INPUT = PatternFill("solid", fgColor="FFFF00")
FILL_REF = PatternFill("solid", fgColor="D9E1F2")
FILL_G = PatternFill("solid", fgColor="C6EFCE")
FILL_A = PatternFill("solid", fgColor="FFEB9C")
FILL_R = PatternFill("solid", fgColor="FFC7CE")
FILL_EX = PatternFill("solid", fgColor="E7E6E6")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="top", wrap_text=True)


def write_table(ws: Worksheet, top: int, headers: list[str], rows: list[list[Any]], widths: list[int],
                input_cols: tuple[int, ...] = (), center_cols: tuple[int, ...] = ()) -> tuple[int, int]:
    """Write a header row at `top` and rows beneath. Returns (first_data_row, last_data_row)."""
    for j, h in enumerate(headers, 1):
        c = ws.cell(row=top, column=j, value=h)
        c.font, c.fill, c.alignment, c.border = F_HEAD, FILL_HEAD, CENTER, BORDER
    r = top
    for row in rows:
        r += 1
        for j, v in enumerate(row, 1):
            c = ws.cell(row=r, column=j, value=v)
            c.font, c.border = F_BASE, BORDER
            c.alignment = CENTER if j in center_cols else WRAP
            if j in input_cols:
                c.fill = FILL_INPUT
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = ws.cell(row=top + 1, column=2)
    ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{max(r, top)}"
    return top + 1, r


def add_dropdown(ws: Worksheet, col_letter: str, first: int, last: int, options: list[str], prompt: str) -> None:
    dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True,
                        showDropDown=False, promptTitle="Choose", prompt=prompt)
    dv.error, dv.errorTitle = "Pick a value from the list.", "Invalid entry"
    dv.showErrorMessage, dv.errorStyle = True, "stop"
    ws.add_data_validation(dv)
    dv.add(f"{col_letter}{first}:{col_letter}{last}")


def color_rules(ws: Worksheet, col_letter: str, first: int, last: int,
                green: list[str], amber: list[str], red: list[str]) -> None:
    rng = f"{col_letter}{first}:{col_letter}{last}"
    for vals, fill in ((green, FILL_G), (amber, FILL_A), (red, FILL_R)):
        for v in vals:
            ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{v}"'], fill=fill))


def title(ws: Worksheet, text: str, sub: str | None = None) -> None:
    ws["A1"] = text
    ws["A1"].font = F_TITLE
    if sub:
        ws["A2"] = sub
        ws["A2"].font = F_NOTE
    ws.sheet_view.showGridLines = False


wb = Workbook()
ws_sum = wb.active
ws_sum.title = "Summary"
RANGES: dict[str, dict[str, Any]] = {}   # sheet -> dict(col, first, last, total)


def fill_sheet(name: str, sub: str, headers: list[str], rows: list[list[Any]], widths: list[int], resp_col: int,
               options: list[str], green: list[str], amber: list[str], red: list[str],
               extra_inputs: tuple[int, ...] = ()) -> Worksheet:
    ws = wb.create_sheet(name)
    title(ws, name, sub)
    input_cols = (resp_col, *extra_inputs)
    first, last = write_table(ws, 4, headers, rows, widths, input_cols=input_cols, center_cols=(1, resp_col))
    col = get_column_letter(resp_col)
    add_dropdown(ws, col, first, last, options, "Pick one: " + " / ".join(options))
    color_rules(ws, col, first, last, green, amber, red)
    RANGES[name] = {"col": col, "first": first, "last": last, "total": len(rows)}
    return ws


# 1 Decisions (Section 19): all closed, 4 columns. D16 to D27 (v0.4.0) record the action register the Principal
# returned on 2026-10-16; D4, D10 and D12 say in their Note that D16 to D18 reopened them, as D2 says of D15.
dec = md_table_after("## 19.")[1:]
rows = [[d[0], d[1], d[2], d[3], (None if d[2].startswith("OPEN") else "Confirmed"), None] for d in dec]
fill_sheet("1 Decisions",
    "Section 19. All twenty-seven are closed. D15 (Secure Boot and the TPM binding) was decided on 2026-10-05 as "
    "option (a), Secure Boot enabled in the BIOS. D16 to D27 (v0.4.0) record the action register the Principal "
    "returned on 2026-10-16, every row approved as recommended (P-10 with the Principal's change); D16, D17 and D18 "
    "reopen D4 (the retrieval models), D10 (the vision engine) and D12 (Meditron), whose rows say so in their Note. "
    "D16, D18, D20, D22, D24 and D26 carry the Principal's answers of 2026-10-17 to the inputs they had left open "
    "(S56: the decode, prefill and deep-tier floors, the D-16 profile, no dedicated coder seat, EmbeddingGemma 2 "
    "dropped, the test-set and trial designs).",
    ["ID", "Decision", "Your answer (as given)", "Note", "Still stands?", "If changed, what instead"],
    rows, [7, 40, 52, 40, 16, 40], 5,
    ["Confirmed", "Changed my mind"],
    ["Confirmed"], [], ["Changed my mind"], extra_inputs=(6,))

# 2 Resolutions (Section 18): C1 to C30
res = md_table_after("## 18.")[1:]
# C1-C26 all accepted in the workbook returned 2026-09-21
# C2's stated reason corrected in v0.3.5 (S51); the resolution itself is unchanged
# v0.4.0 (D-25 option (a), approved 2026-10-16): C28, C29 and C30 supersede C23, C25 and C26; C6's override restated
_V040 = "approved by the Principal 2026-10-16 in the returned action register"
_res_note = {"C2": "Reason corrected in v0.3.5 (S51, approved by the Principal 2026-10-11); the resolution stands",
             "C6": "Override restated in v0.4.0 (GLM-5.3-Flash replaces the retired Qwen3.5-122B-A10B); the addendum "
                   "is unchanged",
             "C23": "Superseded by C28 in v0.4.0 (" + _V040 + "); kept as the record",
             "C25": "Superseded by C29 in v0.4.0 (" + _V040 + "); kept as the record",
             "C26": "Superseded by C30 in v0.4.0 (" + _V040 + "); kept as the record",
             "C28": "New in v0.4.0, " + _V040 + " (D-11 (a), D-29 (a), D-59 (b))",
             "C29": "New in v0.4.0, " + _V040 + " (D-20 (a), D-21 (a))",
             "C30": "New in v0.4.0, " + _V040 + " (D-32 (a))"}
rows = [[c[0], c[1], c[2], "Accept", _res_note.get(c[0])] for c in res]
fill_sheet("2 Resolutions",
    "Section 18. C1 to C26 accepted in the workbook returned 2026-09-21; C27 (Ubuntu Server kept over Fedora 44 KDE "
    "Plasma) decided by the Principal on 2026-10-08; C28 to C30 (v0.4.0: the least-quantised rung that fits each seat "
    "alone, one vision engine on the GLM unit, one LLM seat resident at a time), superseding C23, C25 and C26, "
    "approved by the Principal on 2026-10-16 in the returned action register. Closed record; change a cell only to "
    "reopen one.",
    ["ID", "Contradiction or ambiguity found", "Resolution adopted", "Your response", "Notes"],
    rows, [7, 52, 52, 22, 34], 4,
    ["Accept", "Disagree - return to decisions"], ["Accept"], [], ["Disagree - return to decisions"], extra_inputs=(5,))

# 3 Risks (Section 20): R1 to R33
rk = md_table_after("## 20.")[1:]
# R1-R18 were acknowledged, except R9 and R16 whose wording changed in v0.2; R19-R21 are new
# R1-R21 acknowledged 2026-09-21; R22-R25 (R22-R23 new in v0.3.2, R24-R25 in v0.3.4) acknowledged by the Principal
# 2026-10-08
# R1 and R3 amended in v0.3.5 (S51): references and facts corrected, R3's likelihood lowered from Certain
_amend_note = {1: "Amended in v0.3.5 (S51, approved by the Principal 2026-10-11): wheel and hang-report facts "
                  "corrected",
               3: "Amended in v0.3.5 (S51, approved by the Principal 2026-10-11): out of date, likelihood lowered from "
                  "Certain to Low"}
# R26-R28 new in v0.3.5 (S53, 2026-10-12), recorded with the Principal's approval of the S53 document changes and
# acknowledged by the Principal in the workbook returned 2026-10-13
_s53_note = "Acknowledged 2026-10-13"
# v0.4.0 (S-47, S-48, 2026-10-16): R29-R33 are new and R4, R17, R19 and R20 restated; all nine acknowledged by the
# Principal on 2026-10-17 (S55, with the RAM fixes and the V25 pass marks; Section 22)
_V040_RESTATED = {4: "S-48", 17: "S-48, C-06", 19: "S-26, S-47", 20: "S-34"}
_V040_ACK = "acknowledged 2026-10-17"
# S56 (2026-10-17): R9 (applicable with the cable) and R20 (lowered) restated with B-27, and R33's mitigation amended
# with D-47; both recommendations were approved by the Principal on 2026-10-17 with the rest of the open inputs
_S56_NOTE = {9: "Restated in S56 (B-27, approved by the Principal 2026-10-17): applicable with the cable",
             20: "; restated again in S56 (B-27, approved by the Principal 2026-10-17): lowered to Low",
             33: "; mitigation amended in S56 (D-47, approved by the Principal 2026-10-17)"}


def _ack(i: int) -> tuple[str | None, str | None]:
    """(response, note) of risk Ri."""
    if i >= 29:
        return "Yes", f"New in v0.4.0 (S-47, 2026-10-16); {_V040_ACK}" + _S56_NOTE.get(i, "")
    if i in _V040_RESTATED:
        return "Yes", f"Restated in v0.4.0 ({_V040_RESTATED[i]}, 2026-10-16); {_V040_ACK}" + _S56_NOTE.get(i, "")
    if i in _S56_NOTE:
        return "Yes", _S56_NOTE[i]
    return "Yes", (_s53_note if i >= 26 else "Acknowledged 2026-10-08" if i >= 22 else _amend_note.get(i))


rows = [[r[0], r[1], r[2], r[3], r[4], *_ack(int(r[0][1:]))] for r in rk]
fill_sheet("3 Risks",
    "Section 20. R1 to R21 acknowledged in the workbook returned 2026-09-21; R22 and R23 (v0.3.2) and R24 and R25 "
    "(v0.3.4: the xrdp exposure and the unattended-reboot gap) acknowledged by the Principal on 2026-10-08. R23 is "
    "mitigated by D15 (Secure Boot enabled; the BIOS setting confirmed made on 2026-10-13). R26 to R28 (v0.3.5, S53, "
    "2026-10-12: the ledger's 30-day purge not yet wired into the sweep, Chroma 1.5.9's soft deletes, and the "
    "once-silent failed sweep, now pushed to the phone) acknowledged by the Principal on 2026-10-13. R29 to R33 "
    "(v0.4.0, S-47: the re-pin, GLM-5.3-Flash's lossy rung, swap and re-prefill latency, the companions' share of the "
    "engine budget, the phased DeepSeek V4.1 trial engine) and R4, R17, R19 and R20 as restated in v0.4.0 "
    "acknowledged by the Principal on 2026-10-17 (S55). Later that day (S56) R9 (applicable, the node moving to the "
    "cable on its 10 GbE port) and R20 (lowered to Low) were restated with B-27 and R33's mitigation amended with "
    "D-47, both approved by the Principal with the other open-input recommendations.",
    ["ID", "Risk", "Likelihood", "Impact", "Mitigation", "Acknowledged", "Notes"],
    rows, [7, 44, 18, 22, 52, 18, 28], 6,
    ["Yes", "Discuss"], ["Yes"], [], ["Discuss"], extra_inputs=(7,))

# 4 Verification (Section 21): V1 to V25, filled during Day 1
vr = md_table_after("## 21.")[1:]
rows = [[v[0], v[1], v[2], "Not yet run", None] for v in vr]
fill_sheet("4 Verification",
    "Section 21 (V1 to V25; V25, the output parity of the llama.cpp re-pin, is new in v0.4.0, its pass marks confirmed "
    "by the Principal on 2026-10-17, and V21 now proves single residency). Filled in during Day 1 as each phase gate "
    "runs. Nothing is trusted until its row reads Pass.",
    ["ID", "What Day 1 must prove", "Where", "Result", "Evidence or notes"],
    rows, [7, 70, 24, 16, 40], 4,
    ["Pass", "Fail", "Deferred", "Not yet run"], ["Pass"], ["Deferred", "Not yet run"], ["Fail"], extra_inputs=(5,))

# 5 Checklist (Section 22)
ck = md_checklist()
# The install row that still asks the Principal to confirm the installer's encryption option (found, not numbered by
# hand, since v0.4.0 added rows above it).
ENC_ROW = next(i + 1 for i, (_, item, _) in enumerate(ck) if item.startswith("Ubuntu Server 26.04.1 installed"))
# S56 (B-27, the Principal's choice of 2026-10-17): the one box still to do before Phase 1, the cable (found, not
# numbered by hand).
CABLE_ROW = next(i + 1 for i, (_, item, _) in enumerate(ck) if item.startswith("New in S56 (B-27"))
rows = [[i + 1, g, item, ("Yes" if done else None), None] for i, (g, item, done) in enumerate(ck)]
fill_sheet("5 Checklist",
    "Section 22. The original rows were ticked in the workbook returned 2026-09-21; the rows added or reopened since "
    "were closed as each records: D15 decided and the Windows share deferred on 2026-10-05, C27 decided and R22 to "
    "R25 acknowledged by the Principal on 2026-10-08, the Secure Boot BIOS setting, the USB recovery drive and R26 to "
    "R28 ticked in the workbook returned 2026-10-13, and the action register, its licences, C28 to C30 and D16 to D27 "
    "ticked on 2026-10-16 (v0.4.0), R29 to R33 with the restated R4, R17, R19 and R20 acknowledged on "
    "2026-10-17, and later that day the answers to the open inputs, the bandwidth and R9 and R20 as restated (S56). "
    "Blank rows: the optional items the scripts ask for at a prompt (Yes when "
    "provided, N/A to leave them for the live ATLAS to-do list); the inputs still left to the Principal, each already "
    "built with its stated default and kept as a to-do (the seat profiles and reasoning settings, the HAI-DEF "
    "decision for MedGemma, and after Day 1 the briefs for the seat test sets, the TF_OMEGA situations and the T-03 "
    "coding tasks); and the optional 60-second download test. One row is to do before Phase 1: row "
    f"{CABLE_ROW}, the cable on the 10 GbE port not marked DASH, with the reservation and the forward moved to its MAC "
    "and Wi-Fi off the LAN subnet (S56; pre-flight warns, never stops, if it is not done). The Secure Boot row and "
    f"the install rows are ticked. One request still stands on row {ENC_ROW}, to "
    "confirm that the installer's encryption option (Encrypt the LVM group with LUKS) was taken; pre-flight checks "
    "the OS volume on Day 1, and an unencrypted one is a warning and the to-do os-volume-encryption, never a stop.",
    ["#", "Group", "Item", "Done", "Notes"],
    rows, [7, 34, 76, 12, 34], 4,
    ["Yes", "No", "N/A"], ["Yes", "N/A"], [], ["No"], extra_inputs=(5,))


def ref_sheet(name: str, sub: str, blocks: list[tuple[Any, ...]], widths: list[int]) -> Worksheet:
    """blocks: list of (caption, headers, rows[, note]). Stacked tables, no filters."""
    ws = wb.create_sheet(name)
    title(ws, name, sub)
    r = 4
    for block in blocks:
        caption, headers, rows = block[:3]
        note = block[3] if len(block) > 3 else None
        ws.cell(row=r, column=1, value=caption).font = F_BOLD
        r += 1
        for j, h in enumerate(headers, 1):
            c = ws.cell(row=r, column=j, value=(h if h else "Attribute"))
            c.font, c.fill, c.alignment, c.border = F_HEAD, FILL_HEAD, CENTER, BORDER
        for row in rows:
            r += 1
            for j, v in enumerate(row, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font, c.border, c.alignment = F_BASE, BORDER, WRAP
        if note:
            r += 1
            ws.cell(row=r, column=1, value=note).font = F_NOTE
        r += 2
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.freeze_panes = "A6" if len(blocks) == 1 else "A4"
    return ws


t51 = md_table_after("### 5.1")
t51p = md_table_after("**Phased after Day 1 (v0.4.0;")
t53 = md_table_after("### 5.3")
t61 = md_table_after("### 6.1")
t62 = md_table_after("### 6.2")
ref_sheet("Ref Engines",
    "Sections 5.1, 5.3, 6.1 and 6.2 (v0.4.0). The Day 1 engines and their quantisations, the engines phased after "
    "Day 1, the resident small models, the two hemispheres, and the final persona-to-engine map with each seat's "
    "Day 1 fallback.",
    [("The Day 1 LLM engines (one LLM seat resident at a time; exactly one generates at a time)", t51[0], t51[1:],
      "v0.4.0 (approved by the Principal on 2026-10-16): GLM-5.3-Flash UD-IQ4_XS is the default resident seat, the "
      "vision engine (its glm5v projector loads on the same unit) and the long-document route; DeepSeek-V4-Flash-0731 "
      "UD-Q8_K_XL is Arthur's seat and the Apex; gpt-oss-120b is the fast utility and the fallback floor. "
      "Qwen2.5-VL-72B, Meditron-70B, Qwen3.5-122B-A10B and the bge pair leave the roster, and Nemotron 3 Super becomes "
      "a phased pull. Sizes are decimal GB as Hugging Face lists them; the Arbiter budgets the exact bytes in GiB "
      "(D-31). Phase 3 pulls about 448 GB. Since 2026-10-17 (S56, D-33, D-47) gpt-oss-120b, GLM-5.3-Flash and "
      "DeepSeek-V4-Flash-0731 have decode floors at 8k (25, 6 and 4 tok/s; the abliterated twin keeps its v0.3.5 "
      "band) and gpt-oss-120b is projected at 41-44."),
     ("Phased after Day 1: recorded in the phased list of engines.json, not pulled, rendered or routed on Day 1",
      t51p[0],
      t51p[1:],
      "Where a persona's approved runner-up is one of these, its Day 1 fallback is gpt-oss-120b. To-do for each: the "
      "post-Day-1 pull, with the same checksum and load tests; the event: the Phase 3 gate passed with the measured "
      "budget recorded. Since 2026-10-17 (S56, D-16) Intern-S1 and Baichuan-M3 (on the i1-Q5_K_S file) run Q5_K_S "
      "with a q8_0 cache, the pin's rotation and one 32,768-token slot (about 155.9 GiB projected); Qwen3-Coder-Next "
      "and EmbeddingGemma 2 left the list (D-19 (c), D-60 (b))."),
     ("Resident small models (D16, reopening D4)", t53[0], t53[1:],
      "The router stays at 4B and does classification only (P-10); Eleanor's own persona work runs on GLM-5.3-Flash. "
      "The Qwen3 embedder and reranker run on the CPU (--device none), indexed before Day 1 with the 1024-dim schema "
      "kept by truncation (D-45 (a)), and the Arbiter charges them as a host resident set. The reranker has no caller "
      "on Day 1 (to-do)."),
     ("The two hemispheres", t61[0], t61[1:]),
     ("Shadow Cabinet engine map (each seat's Day 1 fallback in the Override column)", t62[0], t62[1:])],
    [24, 34, 34, 34, 30, 20])

t82 = md_table_after("### 8.2")
ref_sheet("Ref Domains",
    "Section 8.2. Thirty original domains retained and six added, thirty-six in all. Tier A loads freely, B on clear "
    "match, C on explicit match only.",
    [("Thirty-six domain knowledge profiles", t82[0], t82[1:])], [6, 76, 14, 34, 8])

tf = md_table_after("### 8.3")[1:]
ref_sheet("Ref Task Forces", "Section 8.3. All twenty-three retained. Group is a tag, not a merge.",
    [("Twenty-three task forces", ["Code", "Task force", "Group", "Owner", "Default approval tier"], tf,
      "v0.4.0 (S-29, D-37): owners and tiers unchanged. A preset reaches a seat only through its owner's binding or "
      "its engine field, never a domain card; on Day 1 only TF_OMEGA carries one (deepseek-v4-flash, the Apex), and "
      "every other preset runs on its owner's default engine (GLM-5.3-Flash for every director but Valerie, whose "
      "engine is unchanged). The phased engines a preset will carry are named in task-forces.json notes: "
      "Ling-3.0-flash-Fin on TF_GAMMA, TF_DELTA and TF_EPSILON, Baichuan-M3 and Intern-S1 on TF_UPSILON; TF_MU has "
      "none, since D-19 (c) dropped Qwen3-Coder-Next on 2026-10-17. No new prefix.")],
    [14, 46, 18, 34, 24])

integ = md_table_after("## 13.")[1:]
ref_sheet("Ref Integrations", "Section 13. APIs preferred over the browser wherever they exist.",
    [("External services", ["Service", "Path", "Capability", "Approval"], integ)], [30, 46, 46, 40])

voice = md_table_after("### 14.3")[1:]
t141 = md_table_after("### 14.1")
ref_sheet("Ref Voice", "Sections 14.1 and 14.3. Casting is a shortlist for a listening test (V7), not a locked choice.",
    [("Speech engines", t141[0], t141[1:],
      "v0.4.0 (approved by the Principal on 2026-10-16): the one Day 1 change is the diariser, pyannote "
      "speaker-diarization-community-1 in place of 3.1 (C-06; V6 probes 3.1 only as a diagnostic). Nemotron 3 "
      "Diarization, Chatterbox Turbo and Multilingual V3, Qwen3-TTS and the Qwen3-ASR word-error-rate test are phased "
      "after Day 1, each on its node test with the Principal's own audio (T-20 to T-24)."),
     ("Voice casting shortlist", ["Persona", "Description", "Kokoro candidates", "Path"], voice,
      "Known conflict: Arthur, Victor, and Gideon all pull toward the same four British male presets. Once heard, "
      "expect to spread them apart or move one to a Chatterbox clone, as Alaric is.")],
    [30, 60, 46, 40])

tools = md_table_after("### 15.1")[1:]
eng = md_table_after("### 15.2")[1:]
watch = md_table_after("### 15.5")[1:]
notadopted = md_table_after("**Considered and not adopted (v0.4.0")
ref_sheet("Ref Multimodal",
    "Section 15. Tools install in Phase 2 (CPU, always available). Engines build in Phase 4 inside ROCm containers; "
    "since v0.4.0 a Phase 4 engine never runs beside an LLM seat (D-50 (a)). The watch-list (15.5) is considered, not "
    "adopted.",
    [("Phase 2 tools", ["Tool", "Job", "Owner"], tools),
     ("Phase 4 engines and their tiers", ["Engine", "Job", "Tier and evidence", "Footprint", "Owner"], eng,
      "v0.4.0: Qwen2.5-VL-72B and Meditron-70B are retired, their rows kept as the record: the vision engine is "
      "GLM-5.3-Flash with its glm5v projector, a Phase 3 GGUF engine, and Minerva's cross-check moves to the phased "
      "Baichuan-M3. The image tier (Z-Image-Turbo, Qwen-Image-2512) is phased after Day 1 (T-25). Rad-DINO stands "
      "alone until MedGemma 1.5 4B's conditions hold: the Principal's acceptance of the HAI-DEF terms, a non-seat "
      "Arbiter class and a node test against GLM-5.3-Flash's own vision (C-12, S56)."),
     ("Engine watch-list: considered, not adopted (15.5; v0.4.0 adds the 2026 review's items, the phased DeepSeek "
      "V4.1 trial among them; S56 makes EmbeddingGemma 2's row the active one and adds Qwen3-Coder-Next and "
      "Qwen3-VL-Embedding-8B)",
      ["Engine", "What it does", "Why not now", "What would change the answer", "Would replace"], watch,
      "Decision recorded 2026-09-21: FLUX.1-dev stays as the image engine, quality being the deciding factor. "
      "FLUX.1-dev carries the same non-commercial licence as Qwen-Image-2.1, so the licence does not separate them; "
      "validation on gfx1151 does."),
     ("Considered and not adopted (15.5, v0.4.0): the rule each fails", notadopted[0], notadopted[1:],
      "Recorded with the register rows that left each out (approved by the Principal on 2026-10-16). None is on the "
      "node, and none is re-checked unless a later release or a watch-list row changes the answer.")],
    [30, 60, 60, 40, 40])

rules = md_numbered_after("### 16.1")
tiers = md_table_after("### 16.2")
never = md_numbered_after("### 16.3")
ref_sheet("Ref Governance",
    "Section 16. The never-without-the-Principal list is decision D8; the approval tiers are agreed. v0.4.0 adds "
    "standing rules 9 (routed engines use only lossless accelerations, S-50) and 10 (the arsenal admission rules, "
    "S-13).",
    [("Standing rules", ["#", "Rule"], [[n, t] for n, t in rules]),
     ("Approval tiers", tiers[0], tiers[1:]),
     ("Never without the Principal (proposed, D8)", ["#", "Action"], [[n, t] for n, t in never])],
    [12, 70, 60])

dur = md_table_after("### Expected durations")
phases = []
for p in ("Phase 1", "Phase 2", "Phase 3", "Phase 4"):
    for n, t in md_numbered_after("### " + p):
        phases.append([p, f"{n}. {t}"])
tplan = md_table_after("### Phase 3 test plan")
ref_sheet("Ref Day 1",
    "Section 17. Four phases; each ends with a printed pass/fail gate. Phases 3 and 4 run detached and resumable. "
    "v0.4.0: Phase 3 pulls the four Day 1 LLM repositories (about 448 GB), adds step 2b (V25, the output parity of "
    "the re-pin) and step 2c (the quality record, to-do kld-run), and proves single residency in step 3 (V21). "
    "S56 (2026-10-17): V10 gates on the decode floors at 8k, the prefill floors and the placement checks, and the "
    "pull is about 2.5 hours at the 400 Mbps planning figure.",
    [("Expected durations", dur[0], dur[1:]),
     ("Phase steps", ["Phase", "Step"], phases),
     ("Phase 3 test plan: every test in the action register (v0.4.0)", tplan[0], tplan[1:],
      "Day 1 scripted: a Day 1 step runs and records it. Day 1 manual: done by hand during Day 1, with the Principal. "
      "After Day 1: with its to-do; most wait on a phased engine or on the Principal's own material.")],
    [24, 95, 30, 70])

t2 = md_table_after("## 2. Hardware")
t37 = md_table_after("### 3.7")
t41 = md_table_after("### 4.1")
fits = md_table_after("**What each Day 1 seat takes, run alone")
ref_sheet("Ref Hardware",
    "Sections 2, 3.7 and 4.1. The machine v0.2 is built for, what is deliberately left out of its platform, and what "
    "its 192 GB actually buys (v0.4.0: one LLM seat at a time, about 156 GiB for it).",
    [("GMKtec EVO-X5 Pro, Gorgon Halo platform", t2[0], t2[1:]),
     ("Platform watch-list: considered, not adopted, and the guided tests for later (3.7, reviewed 2026-10-09; "
      "W-DRILL added 2026-10-11; W-DGPU, W-PLACEMENT, W-MEMGUARD, W-MANIFEST and W-SESSIONLOG added 2026-10-16)",
      t37[0], t37[1:],
      "Raised by the report 'Linux 7.4 to support the NPU on Ryzen AI Max 400 Gorgon Halo' (2026-09-30): a "
      "device-name patch with nothing to adopt. Day 1 records the NPU and the kernel line in Phase 1 step 5 and holds "
      "the GA 7.0 kernel line in step 4 (S45). W-DRILL (2026-10-11, at the Principal's request) is not a change left "
      "out: it defers the crash and watchdog tests of S48 and S50 to the live ATLAS, which proposes them once Day 1 is "
      "complete and runs each only with the Principal present and agreeing. The five items of 2026-10-16 come from "
      "the Lucebox review (D-62 (a), D-63 (a), D-64 (a), approved by the Principal): none is adopted and none changes "
      "Day 1."),
     ("Memory budget", t41[0], t41[1:],
      "v0.4.0 (D-14 (e), D-31, D-32, approved by the Principal on 2026-10-16): the router is the whole GPU resident "
      "set (about 5.5 GiB); the Qwen3 embedder and reranker run on the CPU and the Engine Arbiter charges them as a "
      "host resident set of their own (about 9.6 GiB projected; once running, their weights in full plus their "
      "measured anonymous memory, never below the projection); every engine is "
      "budgeted from the exact bytes of its files, and one LLM seat runs alone in what is left, about 156 GiB "
      "expected. The Arbiter budgets from min(GTT total, MemTotal) and keeps the 16 GiB host reserve for what the GPU "
      "memory counter never sees (v0.3.5, S44, S48, S49): the CPU-side rows (about 12 GB) plus transient CPU work and "
      "kernel overhead. The fit needs MemTotal near its expected 187 to 188 GiB, which holds only because Phase 1 "
      "step 4 removes the 4.25 GiB that kdump-tools reserves for a crash kernel (S48) and puts kho=off on the kernel "
      "line (S49), V3a proving both; with the reservation in place neither Day 1 seat fits. ATLAS_ARBITER_HEADROOM_GIB "
      "in orchestrator.env changes the reserve and ATLAS_ARBITER_MAX_RESIDENT the seat limit (default 1). Each chat "
      "llama-server's host-RAM prompt cache and its context checkpoints sit outside the budget; since S55 "
      "(approved by the Principal on 2026-10-17) every engine has an explicit --cache-ram (4096 MiB for chat and "
      "vision, 1024 for the router, 0 for the CPU pair, so at most 5 GiB for the router and one seat where the 8 GiB "
      "default allowed 16), /tmp is capped at 4 GiB, the Celery cpu worker runs at most 8 children, Kokoro, speaches "
      "and Docling are capped, and the out-of-memory killer takes background work first. Phase 3 measures the caches "
      "and raises the to-do cache-ram (S52), which asks whether to change a cap."),
     ("What each Day 1 seat takes, run alone (4.1, v0.4.0)", fits[0], fits[1:],
      "One LLM seat is resident at a time (D-32 (a); C30 on sheet 2 supersedes C26); exactly one generation runs, "
      "everywhere, Celery jobs included. Every figure is the Arbiter's own projection at the profile engines.json "
      "renders, INFERRED until Phase 3 measures it (to-dos kv-estimates and cache-ram). GLM-5.3-Flash has the "
      "thinnest margin, 0.9 to 1.9 GiB; its relief order is in its row. The gpt-oss-120b-abliterated entry is "
      "unchanged by this revision.")],
    [34, 46, 76, 60])

# ---------- Summary sheet ----------
ws = ws_sum
title(ws, "A.T.L.A.S. Build Baseline",
      "Companion to docs/ATLAS_FRAMEWORK_REVIEW.md v0.4.0 (2026-10-17), built from it by "
      "scripts/day1/tools/build-baseline-workbook.py (D-27). v0.4.0 records the action register the Principal "
      "returned on 2026-10-16, every row approved as recommended (P-10 with the Principal's change: Eleanor's router "
      "stays at 4B for classification only, and her own persona work moves to GLM-5.3-Flash; the questions on P-10 and "
      "on memory headroom, N-01 and N-03, are answered in the document's 5.3, 4.1 and 4.2): llama.cpp re-pinned to "
      "build tag b11474, with the old pin v0.4.1 kept only for the output-parity test V25 (sheet 4); GLM-5.3-Flash "
      "UD-IQ4_XS (with its glm5v projector, also the vision engine and the long-document route) and "
      "DeepSeek-V4-Flash-0731 UD-Q8_K_XL (Arthur and the Apex) as the Day 1 seats, gpt-oss-120b as the fast utility "
      "and fallback floor, the 4B router unchanged; one LLM seat resident at a time; the Engine Arbiter budgeting "
      "exact file bytes; the Qwen3 4B embedder and reranker on the CPU, replacing the bge pair before Day 1; the "
      "Phase 3 pull cut to about 448 GB; and pyannote community-1 now (C-06). D16 to D27 on sheet 1 record the "
      "decisions (D16 to D18 reopen D4, D10 and D12) and C28 to C30 on sheet 2 supersede C23, C25 and C26; R29 to R33 "
      "on sheet 3, with R4, R17, R19 and R20 restated, were acknowledged by the Principal on 2026-10-17, the day the "
      "Principal also approved the six RAM fixes (S55: an explicit prompt-cache cap on every engine, a 4 GiB /tmp, "
      "at most 8 Celery cpu workers, memory caps on Kokoro, speaches and Docling, an order for the out-of-memory "
      "killer, and a seat whose start failed stopped before its reservation goes) and confirmed V25's pass marks, "
      "with no new version number. The "
      "specialist seats (Intern-S1, Ling-3.0-flash-Fin, Baichuan-M3), slot save and restore, swap "
      "batching, the speech, image and OCR companions, the Qwen3.8-Flash-Next and "
      "Nemotron 3 Super pulls, the drafters and the DeepSeek V4.1 trial are phased after Day 1, each with its to-do; "
      "where an approved row leaves an input to the Principal (the decode floors, the context targets, the test sets), "
      "its default is built and a to-do raised (sheet 5). Later on 2026-10-17 (S56) the Principal approved the "
      "recommendations for those inputs: decode floors at 8k (gpt-oss-120b 25, GLM-5.3-Flash 6, DeepSeek-V4-Flash-0731 "
      "4, the router 20 tok/s) with prefill floors and placement checks, the D-16 profile of the phased medical and "
      "science seats, no dedicated coder seat (Qwen3-Coder-Next not pulled), EmbeddingGemma 2 dropped, MedGemma 1.5 "
      "4B only on conditions, and the designs of the seat test sets and the V4.1 trial set; and recorded a 2,000 Mbps "
      "line with the node moving to a cable on its 10 GbE port before Phase 1. What those answers still leave open is "
      "on sheet 5. The uncensored-seat items (D-07, D-08, D-09, D-58, N-10, "
      "P-05, P-17, S-05) are unchanged by this revision and remain the Principal's to configure. Until v0.4.0 this "
      "workbook was the closed v0.3.5 record of 2026-10-12: Ubuntu Server kept over Fedora 44 KDE Plasma (C27), the "
      "crash-kernel memory reclaimed with panic=10 (S48), kexec handover off (S49), the hardware watchdog (S50), "
      "torchaudio pinned so Phase 4 stays on ROCm 10.0.0 (S51), every request kept inside its engine's slot with the "
      "Phase 3 memory records (S52), and the pruning fixes (S53), with every decision and resolution confirmed and "
      "every risk acknowledged. The Day 1 scripts under scripts/day1 never stop on a missing input from the Principal "
      "(they ask once, defer, and keep a to-do list). Nothing has been executed.")
how = [
    "How to use this workbook",
    "1. Sheets 1, 2 and 3 are a record of your answers of 2026-09-21, 2026-10-05 (D15: Secure Boot enabled), "
    "2026-10-08 (C27: Ubuntu kept; R22 to R25 acknowledged), 2026-10-13 (R26 to R28 acknowledged), 2026-10-16 "
    "(the action register: D16 to D27 and C28 to C30) and 2026-10-17 (R29 to R33 and the restated R4, R17, R19 and "
    "R20 acknowledged; later, S56: your answers to the open inputs in D16 to D26, R9 and R20 restated with B-27). "
    "Nothing on them needs you now. Sheet 5 records the Secure Boot BIOS setting "
    "made and the USB recovery drive in hand (both confirmed 2026-10-13) and lists the optional items the scripts ask "
    "for at a prompt and the inputs still left to you, each with the default already built. Sheet 4 fills during "
    "Day 1.",
    "2. Use the dropdown in each response column. Cells colour green, amber, or red as you answer.",
    "3. Where you choose 'Change' or 'Disagree', write the alternative in the next column so it can be folded back "
    "into the brief.",
    "4. Sheet 4 (Verification) is filled during Day 1 as each phase gate runs, not now; its Result column shows amber "
    "'Not yet run' until then.",
    "5. The Ref sheets carry the agreed maps (hardware, platform watch-list and memory budget, the Day 1 and phased "
    "engines, cabinet, domains, task forces, integrations, voice, multimodal, governance, Day 1 and its test plan) "
    "for checking against.",
    f"6. Outstanding before Phase 1: sheet 5 row {CABLE_ROW}, the cable (your choice of 2026-10-17, S56): plug it into "
    "the 10 GbE port not marked DASH and a 2.5 GbE router port, move the reservation and the UDP 51820 forward to "
    "that port's MAC and take Wi-Fi off the LAN subnet (pre-flight warns, never stops, if it is not done). The other "
    "blank sheet 5 "
    "rows are optional or wait until after Day 1, and can be given at the prompt or left for the live ATLAS; sheet 5 "
    f"row {ENC_ROW} still asks you to confirm that the installer's encryption option was taken (if it was not, Day 1 "
    "warns and records a to-do, never a stop). To reopen any other item, change its response cell (Changed my mind, "
    "Disagree, Discuss or No) and write the reason beside it; the progress table below shows it at once.",
]
for i, t in enumerate(how):
    c = ws.cell(row=4 + i, column=1, value=t)
    c.font = F_BOLD if i == 0 else F_BASE
r = 4 + len(how) + 1
ws.cell(row=r, column=1, value="Legend").font = F_BOLD
legend = [("Yellow fill", "Cell for you to fill in", FILL_INPUT),
          ("Green", "Confirmed / Accept / Yes / N/A / Pass", FILL_G),
          ("Amber", "Deferred / Not yet run (Not yet run counts as Remaining, not Hold)", FILL_A),
          ("Red", "Changed my mind / Disagree - return to decisions / Discuss / No / Fail", FILL_R)]
for i, (k, v, fill) in enumerate(legend):
    a = ws.cell(row=r + 1 + i, column=1, value=k)
    a.fill, a.font, a.border = fill, F_BASE, BORDER
    b = ws.cell(row=r + 1 + i, column=2, value=v)
    b.font, b.border = F_BASE, BORDER
r = r + len(legend) + 3

ws.cell(row=r, column=1, value="Progress (formulas update as you answer)").font = F_BOLD
heads = ["Sheet", "Items", "Answered", "Positive", "Hold", "Negative", "Remaining"]
for j, h in enumerate(heads, 1):
    c = ws.cell(row=r + 1, column=j, value=h)
    c.font, c.fill, c.alignment, c.border = F_HEAD, FILL_HEAD, CENTER, BORDER
spec = [("1 Decisions", ["Confirmed"], [], ["Changed my mind"]),
        ("2 Resolutions", ["Accept"], [], ["Disagree - return to decisions"]),
        ("3 Risks", ["Yes"], [], ["Discuss"]),
        ("4 Verification", ["Pass"], ["Deferred"], ["Fail"]),
        ("5 Checklist", ["Yes", "N/A"], [], ["No"])]


def cnt(rng: str, vals: list[str]) -> str:
    return "+".join(f'COUNTIF({rng},"{v}")' for v in vals) if vals else "0"


row0 = r + 2
for i, (name, pos, hold, neg) in enumerate(spec):
    R = RANGES[name]
    rng = f"'{name}'!${R['col']}${R['first']}:${R['col']}${R['last']}"
    rr = row0 + i
    ids = f"'{name}'!$A${R['first']}:$A${R['last']}"
    rem = f"=COUNTBLANK({rng})" + (f'+COUNTIF({rng},"Not yet run")' if name == "4 Verification" else "")
    vals = [name, f"=COUNTA({ids})",
            f"=B{rr}-G{rr}", f"={cnt(rng, pos)}", f"={cnt(rng, hold)}", f"={cnt(rng, neg)}", rem]
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=rr, column=j, value=v)
        c.font, c.border = F_BASE, BORDER
        c.alignment = WRAP if j == 1 else CENTER
last = row0 + len(spec) - 1
tot = last + 1
ws.cell(row=tot, column=1, value="Total").font = F_BOLD
for j in range(2, 8):
    L = get_column_letter(j)
    c = ws.cell(row=tot, column=j, value=f"=SUM({L}{row0}:{L}{last})")
    c.font, c.border, c.alignment = F_BOLD, BORDER, CENTER
ws.cell(row=tot + 1, column=1,
        value="Positive / Hold / Negative map to each sheet's dropdown: "
              "Decisions = Confirmed / - / Changed my mind; Resolutions = Accept / - / Disagree; Risks = Yes / - / "
              "Discuss; Verification = Pass / Deferred / Fail, with Not yet run counted as Remaining; Checklist = Yes "
              "or N/A / - / No.").font = F_NOTE

r = tot + 4
ws.cell(row=r, column=1,
        value="Example of a completed decision row (format only, not editable, not counted; grey marks where your "
              "text would go)").font = F_BOLD
ex_h = ["ID", "Decision", "Recommendation", "Blocks", "Your response", "Your answer or alternative", "Notes"]
ex_r = ["D0", "Example: which mail client the node uses", "Client A", "Phase 2",
        "Changed my mind", "Use client B, it already holds my accounts", "Discussed 2026-09-21"]
for j, h in enumerate(ex_h, 1):
    c = ws.cell(row=r + 1, column=j, value=h)
    c.font, c.fill, c.alignment, c.border = F_HEAD, FILL_HEAD, CENTER, BORDER
for j, v in enumerate(ex_r, 1):
    c = ws.cell(row=r + 2, column=j, value=v)
    c.font, c.border, c.alignment = F_BASE, BORDER, WRAP
    if j >= 6:
        c.fill = FILL_EX
ws.cell(row=r + 2, column=5).fill = FILL_R
for j, w in enumerate([26, 40, 30, 14, 22, 40, 24], 1):
    ws.column_dimensions[get_column_letter(j)].width = w

wb.calculation = CalcProperties(fullCalcOnLoad=True)
wb.save(OUT)
print("saved", OUT, "sheets:", wb.sheetnames)
for k, v in RANGES.items():
    print(k, v)
