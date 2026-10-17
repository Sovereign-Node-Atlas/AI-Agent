#!/usr/bin/env bash
# phase2/pull_engine_files_test.sh — regression test (v0.4.0, engines.json files_rule) for phase2/04-memory.sh's
# pull_engine_files: a verify=research-snippet sha256 pinned in engines.json that the Hugging Face tree API contradicts
# stops the pull before any byte moves (Phase 2 step 4 pulls the router, embedder and reranker with it); a
# verify=unverified hash is advisory (a warning, the tree value used); a matching pin pulls and writes MANIFEST.json;
# an enumerate_pattern entry (no pinned hash, the unchanged abliterated entry's shape) pulls as before. The tree
# listing and the download are stubbed. Touches nothing outside a fresh temp dir.
# Cases 5 to 10 (B-27, ATLAS_PULL_JOBS): the real hf_download_public and lib/common.sh hf_download (its .part, curl -C -
# resume, sha256 by the tool _sha256_pick chooses) against a fake curl on PATH that serves six shards from a temp dir:
# one at a time unchanged at jobs=1, at most four at once at jobs=4 with the manifest still in tree order, a bad value
# refused, one failing shard failing the engine's pull by name and a re-run fetching only what is missing, a cut
# transfer resumed from its .part, and a TERM during a four-job pull ending every running curl, then resumed.
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

# --- B-27: shard downloads at once (ATLAS_PULL_JOBS) through the real download path ------------------------------------
# The real hf_download_public (04-memory.sh) and hf_download with its sha256 helpers (lib/common.sh), each taken from a
# subshell that sourced its file; curl is a fake on PATH. The stubs above (die, log, warn, ensure_dir, chown) stay.
real="$(
  # shellcheck source=lib/common.sh
  source "$REPO/lib/common.sh" >/dev/null || exit 1
  declare -f hf_download proxy_env _sha256_parse _sha256_run _sha256_pick _sha256_of
  declare -p _ATLAS_SHA256_CANDIDATES _ATLAS_SHA256_PROBE_BYTES _ATLAS_SHA256_PROBE_SHA _ATLAS_SHA256_TOOL
)" || { echo "FAIL: could not source lib/common.sh"; exit 1; }
eval "$real"
real="$(
  # shellcheck source=phase2/04-memory.sh
  source "$REPO/phase2/04-memory.sh" >/dev/null || exit 1
  declare -f hf_download_public
)" || { echo "FAIL: could not source phase2/04-memory.sh"; exit 1; }
eval "$real"
export ATLAS_ETC="$T/etc" ATLAS_CACHE_DIR="$T/cache" FAKE_REMOTE="$T/remote" FAKE_LOG="$T/calls" FAKE_ACTIVE="$T/active"
mkdir -p "$ATLAS_ETC" "$FAKE_REMOTE" "$FAKE_ACTIVE" "$T/bin"
printf 'HTTPS_PROXY=http://127.0.0.1:3128\n' >"$ATLAS_ETC/proxy.env"
unset HF_TOKEN ATLAS_SHA256_TOOL ATLAS_PULL_JOBS
# The fake curl: serves $FAKE_REMOTE/<URL basename> into the -o file from its current size (curl -C -), prints the HTTP
# code for -w, logs "name offset" per call and how many calls run at that moment. FAKE_FAIL=name answers HTTP 500
# (exit 22); FAKE_CUT=name stops after half the bytes (exit 18, partial file); FAKE_HANG=1 writes half, then waits 30 s.
cat >"$T/bin/curl" <<'SH'
#!/usr/bin/env bash
out="" url=""
while (( $# )); do
  case "$1" in -o|-w|-C|-H|--retry|--retry-delay|--connect-timeout) [[ "$1" == -o ]] && out="$2"; shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac
done
name="${url##*/}" src="$FAKE_REMOTE/${url##*/}" have=0
[[ -f "$out" ]] && have="$(stat -c %s "$out")"
mkdir "$FAKE_ACTIVE/$$"
echo "$name $have $(find "$FAKE_ACTIVE" -mindepth 1 -maxdepth 1 -type d | wc -l)" >>"$FAKE_LOG"
echo "$$" >"$FAKE_ACTIVE/pid.$name"
size="$(stat -c %s "$src")"; half=$(( size / 2 ))
if [[ "$name" == "${FAKE_FAIL:-}" ]]; then rmdir "$FAKE_ACTIVE/$$"; printf 500; exit 22; fi
if [[ "$name" == "${FAKE_CUT:-}" || -n "${FAKE_HANG:-}" ]]; then
  (( have < half )) && tail -c +"$(( have + 1 ))" "$src" | head -c "$(( half - have ))" >>"$out"
  if [[ -n "${FAKE_HANG:-}" ]]; then : >"$FAKE_ACTIVE/started.$name"; sleep 30; : >"$FAKE_ACTIVE/finished.$name"; fi
  rmdir "$FAKE_ACTIVE/$$"; printf 200; exit 18
fi
sleep "${FAKE_SLEEP:-0}"
tail -c +"$(( have + 1 ))" "$src" >>"$out"
rmdir "$FAKE_ACTIVE/$$"
printf 200
SH
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH"
cat >"$ATLAS_DAY1_DIR/config/engines.json" <<'JSON'
{"engines": [{"key": "shards-test", "hf_repo": "someone/big-GGUF", "enumerate_pattern": "*-Q8_0-*.gguf"}]}
JSON
: >"$T/tree"
for i in 1 2 3 4 5 6; do
  n="big-Q8_0-0000$i-of-00006.gguf"
  head -c "$(( 3000 + i * 517 ))" /dev/urandom >"$FAKE_REMOTE/$n"
  printf 'Q8_0/%s\t%s\t%s\n' "$n" "$(stat -c %s "$FAKE_REMOTE/$n")" "$(sha256sum <"$FAKE_REMOTE/$n" | cut -d' ' -f1)" >>"$T/tree"
done
printf 'README.md\t10\tnone\n' >>"$T/tree"
SH6="big-Q8_0-00001-of-00006.gguf big-Q8_0-00002-of-00006.gguf big-Q8_0-00003-of-00006.gguf big-Q8_0-00004-of-00006.gguf big-Q8_0-00005-of-00006.gguf big-Q8_0-00006-of-00006.gguf"
MD="$ATLAS_SRV/models/shards-test"
reset_run() { rm -rf "$ATLAS_SRV" "$FAKE_ACTIVE"; mkdir -p "$FAKE_ACTIVE"; : >"$FAKE_LOG"; : >"$T/out"; }
calls() { awk '{print $1}' "$FAKE_LOG" | tr '\n' ' ' | sed 's/ $//'; }
calls_sorted() { awk '{print $1}' "$FAKE_LOG" | sort | tr '\n' ' ' | sed 's/ $//'; }
most_at_once() { awk 'BEGIN {m = 0} $3 > m {m = $3} END {print m}' "$FAKE_LOG"; }
all_match() {  # every shard present, byte-identical to the remote, no .part left
  local n
  for n in $SH6; do cmp -s "$MD/$n" "$FAKE_REMOTE/$n" || { echo "no: $n"; return; }; [[ ! -e "$MD/$n.part" ]] || { echo "no: $n.part"; return; }; done
  echo yes
}
manifest_names() { python3 -c 'import json,sys; print(" ".join(f["name"] for f in json.load(open(sys.argv[1]))["files"]))' "$MD/MANIFEST.json" 2>/dev/null; }
manifest_ok() {  # names in tree order, each with the remote's size and sha256
  python3 - "$MD/MANIFEST.json" "$FAKE_REMOTE" <<'PY' 2>/dev/null || echo no
import hashlib, json, os, sys
files = json.load(open(sys.argv[1]))["files"]
want = [f"big-Q8_0-0000{i}-of-00006.gguf" for i in range(1, 7)]
ok = [f["name"] for f in files] == want and all(
    f["bytes"] == os.path.getsize(os.path.join(sys.argv[2], f["name"]))
    and f["sha256"] == hashlib.sha256(open(os.path.join(sys.argv[2], f["name"]), "rb").read()).hexdigest() for f in files)
print("yes" if ok else "no")
PY
}

# 5. jobs=1 (unset, then 1): one file after another in tree order, exactly as before; nothing but the shards.
for j in unset 1; do
  reset_run
  if [[ "$j" == unset ]]; then out="$(run_pull shards-test)"; rc=$?; else out="$(ATLAS_PULL_JOBS=1 run_pull shards-test)"; rc=$?; fi
  ok "jobs=$j completes" "$rc" "0"
  ok "jobs=$j fetches the shards one after another in tree order" "$(calls)" "$SH6"
  ok "jobs=$j never runs two downloads at once" "$(most_at_once)" "1"
  ok "jobs=$j: every shard byte-identical, no .part left" "$(all_match)" "yes"
  ok "jobs=$j: manifest in tree order with the remote sizes and hashes" "$(manifest_ok)" "yes"
  ok "jobs=$j logs no parallel line" "$(grep -c 'at once (ATLAS_PULL_JOBS)' "$T/out")" "0"
  has "jobs=$j hashes with the tool chosen once (64 MiB probe)" "$(cat "$T/out")" "LOG: sha256: "
done
ok "jobs=1 chooses the hash tool once per run" "$(grep -c 'LOG: sha256: ' "$T/out")" "1"

# 6. jobs=4: at most four at once (and more than one), every shard intact, the manifest still in tree order.
reset_run
out="$(ATLAS_PULL_JOBS=4 FAKE_SLEEP=0.5 run_pull shards-test)"; rc=$?
ok "jobs=4 completes" "$rc" "0"
ok "jobs=4 fetches each shard once" "$(calls_sorted)" "$SH6"
m="$(most_at_once)"
ok "jobs=4 runs more than one download at once" "$(( m > 1 ))" "1"
ok "jobs=4 never runs more than four at once" "$(( m <= 4 ))" "1"
ok "jobs=4: every shard byte-identical, no .part left" "$(all_match)" "yes"
ok "jobs=4: manifest in tree order with the remote sizes and hashes" "$(manifest_ok)" "yes"
has "jobs=4 logs the plan" "$(cat "$T/out")" "LOG: pull_engine_files: shards-test: 6 files, up to 4 at once (ATLAS_PULL_JOBS)"
ok "jobs=4 chooses the hash tool once, before the jobs start" "$(grep -c 'LOG: sha256: ' "$T/out")" "1"

# 7. A value outside 1-8 stops the pull before any download.
for j in 0 9 x; do
  reset_run
  out="$(ATLAS_PULL_JOBS=$j run_pull shards-test)"; rc=$?
  ok "ATLAS_PULL_JOBS=$j refused" "$rc" "99"
  has "ATLAS_PULL_JOBS=$j message" "$out" "ATLAS_PULL_JOBS must be a whole number from 1 to 8, got '$j'"
  ok "ATLAS_PULL_JOBS=$j downloads nothing" "$(wc -l <"$FAKE_LOG")" "0"
done

# 8. One failing shard at jobs=4: the engine's pull fails naming it, no new download starts after the failure, no
#    manifest; a re-run fetches only the missing shards and completes.
reset_run
out="$(ATLAS_PULL_JOBS=4 FAKE_SLEEP=1.5 FAKE_FAIL=big-Q8_0-00003-of-00006.gguf run_pull shards-test)"; rc=$?
ok "failing shard fails the pull" "$rc" "99"
has "failing shard named with its exit" "$out" "DIE: pull_engine_files: shards-test: 1 of 6 downloads failed: big-Q8_0-00003-of-00006.gguf (exit 99)"
has "failing shard's own reason is shown" "$out" "DIE: hf_download: curl exit 22 (HTTP 500)"
has "failing shard: the others are waited for" "$(cat "$T/out")" "starting no more downloads, waiting for the 3 still running"
ok "failing shard: no download starts after the failure" "$(calls_sorted)" \
  "big-Q8_0-00001-of-00006.gguf big-Q8_0-00002-of-00006.gguf big-Q8_0-00003-of-00006.gguf big-Q8_0-00004-of-00006.gguf"
ok "failing shard: the running ones finish" "$(for n in 1 2 4; do cmp -s "$MD/big-Q8_0-0000$n-of-00006.gguf" "$FAKE_REMOTE/big-Q8_0-0000$n-of-00006.gguf" && printf y; done)" "yyy"
ok "failing shard: no manifest" "$([[ -f "$MD/MANIFEST.json" ]] && echo yes || echo no)" "no"
: >"$FAKE_LOG"
out="$(ATLAS_PULL_JOBS=4 run_pull shards-test)"; rc=$?
ok "re-run after the failure completes" "$rc" "0"
ok "re-run fetches only the missing shards" "$(calls_sorted)" \
  "big-Q8_0-00003-of-00006.gguf big-Q8_0-00005-of-00006.gguf big-Q8_0-00006-of-00006.gguf"
ok "re-run: every shard byte-identical, manifest complete" "$(all_match) $(manifest_ok)" "yes yes"

# 9. A transfer cut half way (curl exit 18) leaves its .part; the re-run resumes it from that offset.
reset_run
cut=big-Q8_0-00002-of-00006.gguf
out="$(ATLAS_PULL_JOBS=4 FAKE_SLEEP=1.5 FAKE_CUT=$cut run_pull shards-test)"; rc=$?
half=$(( $(stat -c %s "$FAKE_REMOTE/$cut") / 2 ))
ok "cut transfer fails the pull" "$rc" "99"
has "cut transfer named" "$out" "1 of 6 downloads failed: $cut (exit 99)"
ok "cut transfer keeps its .part at the cut" "$(stat -c %s "$MD/$cut.part" 2>/dev/null)" "$half"
: >"$FAKE_LOG"
out="$(ATLAS_PULL_JOBS=4 run_pull shards-test)"; rc=$?
ok "resume completes" "$rc" "0"
ok "resume asks curl once for the rest of the cut shard" "$(awk -v n="$cut" '$1 == n {print $2}' "$FAKE_LOG")" "$half"
ok "resume fetches only what is missing" "$(calls_sorted)" "$cut big-Q8_0-00005-of-00006.gguf big-Q8_0-00006-of-00006.gguf"
has "resume logged from the .part size" "$(cat "$T/out")" "(resume from $half bytes)"
ok "resume: every shard byte-identical, manifest complete" "$(all_match) $(manifest_ok)" "yes yes"

# 10. TERM during a four-job pull: every running curl is ended (none left writing), the shell still dies of the TERM,
#     the .part files stay, and the re-run resumes all four and completes.
reset_run
( ATLAS_PULL_JOBS=4 FAKE_HANG=1 pull_engine_files shards-test ) >"$T/term.out" 2>&1 &
bg=$!
for _ in $(seq 1 100); do
  [[ "$(find "$FAKE_ACTIVE" -name 'started.*' | wc -l)" -ge 4 ]] && break
  sleep 0.1
done
ok "TERM case: four downloads running" "$(find "$FAKE_ACTIVE" -name 'started.*' | wc -l)" "4"
kill -TERM "$bg"; wait "$bg"; rc=$?
ok "TERM: the pull's shell ends by the TERM" "$rc" "143"
sleep 0.5
alive=0
for f in "$FAKE_ACTIVE"/pid.*; do kill -0 "$(cat "$f")" 2>/dev/null && alive=$(( alive + 1 )); done
ok "TERM: no curl left running" "$alive" "0"
ok "TERM: no curl went on to finish" "$(find "$FAKE_ACTIVE" -name 'finished.*' | wc -l)" "0"
ok "TERM: four .part files kept" "$(find "$MD" -name '*.part' | wc -l)" "4"
ok "TERM: no manifest" "$([[ -f "$MD/MANIFEST.json" ]] && echo yes || echo no)" "no"
: >"$FAKE_LOG"
out="$(ATLAS_PULL_JOBS=4 run_pull shards-test)"; rc=$?
ok "after TERM the re-run completes" "$rc" "0"
ok "after TERM the four cut shards resume from their .part" \
  "$(awk '$2 > 0 {n++} END {print n + 0}' "$FAKE_LOG")" "4"
ok "after TERM: every shard byte-identical, manifest complete" "$(all_match) $(manifest_ok)" "yes yes"

echo "pull_engine_files_test: $pass passed, $fail failed"
(( fail == 0 ))
