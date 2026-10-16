#!/usr/bin/env bash
# phase2/pull_engine_files_test.sh — regression test (v0.4.0, engines.json files_rule) for phase2/04-memory.sh's
# pull_engine_files: a verify=research-snippet sha256 pinned in engines.json that the Hugging Face tree API contradicts
# stops the pull before any byte moves (Phase 2 step 4 pulls the router, embedder and reranker with it); a
# verify=unverified hash is advisory (a warning, the tree value used); a matching pin pulls and writes MANIFEST.json;
# an enumerate_pattern entry (no pinned hash, the unchanged abliterated entry's shape) pulls as before. The tree
# listing and the download are stubbed. Touches nothing outside a fresh temp dir.
# Run: bash phase2/pull_engine_files_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
has() { if grep -qF -- "$3" <<<"$2"; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: '$3' not in: $2"; fail=$((fail+1)); fi; }

die() { echo "DIE: $*"; exit 99; }
log() { echo "LOG: $*" >>"$T/out"; }
warn() { echo "WARN: $*" >>"$T/out"; }
ensure_dir() { mkdir -p "$1"; }
chown() { :; }
# The tree listing: "path<TAB>bytes<TAB>oid" lines from $T/tree.
engine_tree_lfs() { cat "$T/tree"; }
# The download: a file of the tree's size, and a record of the call (the pull must not start on a refused pin).
hf_download_public() {
  echo "$2" >>"$T/downloads"
  local bytes
  bytes="$(awk -F'\t' -v p="$2" '$1 == p {print $2}' "$T/tree")"
  head -c "$bytes" /dev/zero >"$3"
}
# The two functions are taken from a subshell that sourced the step file, as phase3-models.sh does (declare -f): the
# file's top level is assignments only, and its heredocs hold column-0 braces an awk extraction would stop at.
src="$(
  # shellcheck source=phase2/04-memory.sh
  source "$REPO/phase2/04-memory.sh" >/dev/null || exit 1
  declare -f ej pull_engine_files
)" || { echo "FAIL: could not source phase2/04-memory.sh"; exit 1; }
eval "$src"
declare -F pull_engine_files >/dev/null || { echo "FAIL: phase2/04-memory.sh defines no pull_engine_files"; exit 1; }

export ATLAS_DAY1_DIR="$T/day1" ATLAS_SRV="$T/srv"
mkdir -p "$ATLAS_DAY1_DIR/config"
PIN="aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
OTHER="bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"
write_engines() {  # VERIFY of embed-test's one file
  cat >"$ATLAS_DAY1_DIR/config/engines.json" <<JSON
{"engines": [
  {"key": "embed-test", "hf_repo": "Qwen/Qwen3-Embedding-4B-GGUF",
   "files": [{"name": "Qwen3-Embedding-4B-Q8_0.gguf", "bytes": 16, "sha256": "$PIN", "verify": "$1"}]},
  {"key": "pattern-test", "hf_repo": "someone/abliterated-GGUF", "enumerate_pattern": "*MXFP4_MOE*.gguf"}
]}
JSON
}
run_pull() { ( pull_engine_files "$1" ) 2>&1; }

# 1. research-snippet pin contradicted by the tree: refused, nothing downloaded, no manifest.
write_engines research-snippet
printf 'Qwen3-Embedding-4B-Q8_0.gguf\t16\t%s\n' "$OTHER" >"$T/tree"
: >"$T/downloads"
out="$(run_pull embed-test)"; rc=$?
ok "research-snippet mismatch exits non-zero" "$rc" "99"
has "research-snippet mismatch names both hashes" "$out" "pinned in engines.json ($PIN) differs from the Hugging Face tree API ($OTHER)"
ok "research-snippet mismatch downloads nothing" "$(wc -l <"$T/downloads")" "0"
ok "research-snippet mismatch writes no manifest" "$([[ -f "$ATLAS_SRV/models/embed-test/MANIFEST.json" ]] && echo yes || echo no)" "no"

# 2. the same mismatch on a verify=unverified hash: a warning, the tree value is used and the pull completes.
write_engines unverified
: >"$T/out"
out="$(run_pull embed-test)"; rc=$?
ok "unverified mismatch completes" "$rc" "0"
has "unverified mismatch warns" "$(cat "$T/out")" "WARN: pull_engine_files: unverified sha256 for Qwen3-Embedding-4B-Q8_0.gguf"
ok "unverified mismatch records the tree hash" \
  "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["files"][0]["sha256"])' "$ATLAS_SRV/models/embed-test/MANIFEST.json")" "$OTHER"
rm -rf "$ATLAS_SRV"

# 3. a matching pin (case-insensitive) pulls and records it.
write_engines research-snippet
printf 'Qwen3-Embedding-4B-Q8_0.gguf\t16\t%s\n' "${PIN^^}" >"$T/tree"
: >"$T/downloads"
out="$(run_pull embed-test)"; rc=$?
ok "matching pin completes" "$rc" "0"
ok "matching pin downloads the file" "$(cat "$T/downloads")" "Qwen3-Embedding-4B-Q8_0.gguf"
rm -rf "$ATLAS_SRV"

# 4. an enumerate_pattern entry has no pinned hash: unchanged behaviour, every match pulled.
printf 'x-MXFP4_MOE-00001.gguf\t8\t%s\nREADME.md\t4\tnone\n' "$OTHER" >"$T/tree"
: >"$T/downloads"
out="$(run_pull pattern-test)"; rc=$?
ok "pattern entry completes" "$rc" "0"
ok "pattern entry downloads the match only" "$(cat "$T/downloads")" "x-MXFP4_MOE-00001.gguf"

echo "pull_engine_files_test: $pass passed, $fail failed"
(( fail == 0 ))
