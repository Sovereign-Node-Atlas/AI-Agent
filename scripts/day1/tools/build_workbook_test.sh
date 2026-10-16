#!/usr/bin/env bash
# tools/build_workbook_test.sh — regression test (v0.4.0, D-27) for tools/build-baseline-workbook.py, the builder of
# docs/ATLAS_BUILD_BASELINE.xlsx. It rebuilds the workbook from docs/ATLAS_FRAMEWORK_REVIEW.md into a temp directory
# and checks that every D, C, R and V row of Sections 18 to 21 and every box of Section 22 reaches sheets 1 to 5 in the
# document's order, that the risks Section 22 leaves open for acknowledgement start blank, that the committed workbook
# is the one the committed document builds, that the repository copy is never touched by a test build, that a missing
# section stops the builder, and that no scratch path is left in it. Needs python3 with openpyxl; without it the
# workbook cases are skipped, said so, and only the static cases run.
# Run: bash tools/build_workbook_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
ROOT="$(cd "$REPO/../.." && pwd)"
B="$REPO/tools/build-baseline-workbook.py"
DOC="$ROOT/docs/ATLAS_FRAMEWORK_REVIEW.md"
WB="$ROOT/docs/ATLAS_BUILD_BASELINE.xlsx"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

# --- static: the builder derives its paths from the repository root ------------------------------------------------
code="$(grep -v '^[[:space:]]*#' "$B")"
ok "no scratch or absolute session path in the builder" "$(grep -cE '/tmp/|scratchpad|/home/' <<<"$code")" "0"
ok "no CSV parts read any more" "$(grep -cE 'read_csv|\.csv' <<<"$code")" "0"
ok "paths derived from the repository root" "$(grep -cE '^REPO = Path\(__file__\)\.resolve\(\)\.parents\[3\]$' "$B")" "1"
ok "the builder sits where REPO assumes (scripts/day1/tools)" "$([[ -f "$ROOT/docs/ATLAS_FRAMEWORK_REVIEW.md" ]] && echo yes)" "yes"

if ! python3 -I -c 'import openpyxl' 2>/dev/null; then
  echo "SKIP the workbook cases: python3 has no openpyxl"
  echo "build_workbook_test: $pass passed, $fail failed"
  (( fail == 0 )); exit
fi

# --- a test build never touches the repository copy -----------------------------------------------------------------
before="$(sha256sum "$WB" | cut -d' ' -f1)"
python3 -I "$B" --out "$T/wb.xlsx" >"$T/build.log" 2>&1; rc=$?
ok "the builder runs on the committed document" "$rc" "0"
ok "the repository workbook is untouched by --out" "$(sha256sum "$WB" | cut -d' ' -f1)" "$before"

# --- the document's rows reach sheets 1 to 5, in order --------------------------------------------------------------
# doc_ids SECTION PREFIX: the first column of the first table after "## SECTION.", rows whose id starts with PREFIX.
doc_ids() {
  awk -v h="## $1." -v p="$2" '
    index($0, h) == 1 { on = 1; next }
    on && /^## / { exit }
    on && /^\|/ { split($0, c, "|"); id = c[2]; gsub(/^ +| +$/, "", id); if (index(id, p) == 1 && id ~ /[0-9]/) print id; seen = 1; next }
    on && seen && !/^\|/ { exit }' "$DOC" | paste -sd' '
}
# wb_ids SHEET: column A from row 5 down, as the builder writes it.
wb_ids() {
  python3 -I - "$T/wb.xlsx" "$1" <<'PY'
import sys
from openpyxl import load_workbook
ws = load_workbook(sys.argv[1])[sys.argv[2]]
print(" ".join(str(ws.cell(row=r, column=1).value) for r in range(5, ws.max_row + 1)
               if ws.cell(row=r, column=1).value is not None))
PY
}
d_doc="$(doc_ids 19 D)"; c_doc="$(doc_ids 18 C)"; r_doc="$(doc_ids 20 R)"; v_doc="$(doc_ids 21 V)"
ok "sheet 1 carries every D row of Section 19" "$(wb_ids '1 Decisions')" "$d_doc"
ok "sheet 2 carries every C row of Section 18" "$(wb_ids '2 Resolutions')" "$c_doc"
ok "sheet 3 carries every R row of Section 20" "$(wb_ids '3 Risks')" "$r_doc"
ok "sheet 4 carries every V row of Section 21" "$(wb_ids '4 Verification')" "$v_doc"
ok "the v0.4.0 rows are among them (D27, C30, R33, V25)" \
   "$(for id in D27 C30 R33 V25; do [[ " $d_doc $c_doc $r_doc $v_doc " == *" $id "* ]] && printf '%s ' "$id"; done)" \
   "D27 C30 R33 V25 "
boxes="$(awk '/^## 22\./ {on=1; next} on && /^## / {exit} on && /^[[:space:]]*- \[( |x)\]/ {n++} END {print n+0}' "$DOC")"
ticked="$(awk '/^## 22\./ {on=1; next} on && /^## / {exit} on && /^[[:space:]]*- \[x\]/ {n++} END {print n+0}' "$DOC")"
read -r rows yes <<<"$(python3 -I - "$T/wb.xlsx" <<'PY'
from openpyxl import load_workbook
import sys
ws = load_workbook(sys.argv[1])["5 Checklist"]
vals = [ws.cell(row=r, column=4).value for r in range(5, ws.max_row + 1) if ws.cell(row=r, column=1).value is not None]
print(len(vals), sum(v == "Yes" for v in vals))
PY
)"
ok "sheet 5 has one row per Section 22 box" "$rows" "$boxes"
ok "sheet 5 ticks exactly the [x] boxes" "$yes" "$ticked"

# --- responses: the acknowledgements Section 22 leaves open start blank; the rest are recorded -----------------------
ok "risk responses: R1 Yes, R4/R17/R19/R20 and R29-R33 blank" "$(python3 -I - "$T/wb.xlsx" <<'PY'
import sys
from openpyxl import load_workbook
ws = load_workbook(sys.argv[1])["3 Risks"]
resp = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=6).value for r in range(5, ws.max_row + 1)}
blank = sorted((k for k, v in resp.items() if k and v is None), key=lambda k: int(k[1:]))
print(resp.get("R1"), " ".join(blank))
PY
)" "Yes R4 R17 R19 R20 R29 R30 R31 R32 R33"
ok "every decision and resolution is recorded (no blank response)" "$(python3 -I - "$T/wb.xlsx" <<'PY'
import sys
from openpyxl import load_workbook
wb = load_workbook(sys.argv[1])
n = 0
for name, col in (("1 Decisions", 5), ("2 Resolutions", 4)):
    ws = wb[name]
    n += sum(1 for r in range(5, ws.max_row + 1)
             if ws.cell(row=r, column=1).value is not None and ws.cell(row=r, column=col).value is None)
print(n)
PY
)" "0"
ok "the Summary names the document version it was built from" \
   "$(python3 -I - "$T/wb.xlsx" "$(grep -m1 -oE '^\| Version \| [0-9.]+' "$DOC" | grep -oE '[0-9.]+$')" <<'PY'
import sys
from openpyxl import load_workbook
print("yes" if f"ATLAS_FRAMEWORK_REVIEW.md v{sys.argv[2]} " in load_workbook(sys.argv[1])["Summary"]["A2"].value else "no")
PY
)" "yes"

# --- the committed workbook is the one the committed document builds (rebuild it after a document change) -----------
dump() {
  python3 -I - "$1" <<'PY'
import sys
from openpyxl import load_workbook
for ws in load_workbook(sys.argv[1]).worksheets:
    print("SHEET", ws.title)
    for row in ws.iter_rows():
        for c in row:
            if c.value is not None:
                print(c.coordinate, repr(c.value))
PY
}
dump "$WB" >"$T/committed.txt"; dump "$T/wb.xlsx" >"$T/built.txt"
ok "docs/ATLAS_BUILD_BASELINE.xlsx is current with the document" "$(cmp -s "$T/committed.txt" "$T/built.txt" && echo current)" "current"

# --- a missing section stops the builder loudly, writing nothing ----------------------------------------------------
sed 's/^## 21\. /## 21x. /' "$DOC" >"$T/broken.md"
out="$(python3 -I "$B" --doc "$T/broken.md" --out "$T/broken.xlsx" 2>&1)"; rc=$?
ok "a missing Section 21 stops the build" "$rc" "1"
ok "and says which heading" "$(grep -c 'heading not found: ## 21\.' <<<"$out")" "1"
ok "and writes no workbook" "$([[ -e "$T/broken.xlsx" ]] && echo written || echo none)" "none"

echo "build_workbook_test: $pass passed, $fail failed"
(( fail == 0 ))
