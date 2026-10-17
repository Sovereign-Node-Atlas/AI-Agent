#!/usr/bin/env bash
# verify/v10a-router-resident.sh — V10, the Phase 2 half (Section 21): "Eleanor's resident 4B router model is verified
# separately at the Phase 2 gate". Sends one chat completion to llama-server@router-qwen3.5-4b and needs a non-empty
# reply, then one timing request (D-33, approved 2026-10-17): POST /completion, 128 tokens at temperature 0, whose
# decode rate fails V10a below the router's decode_floor_tok_s in engines.json (20 tok/s; the timing block says how).
# Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; exactly one stdout line; no prompts.
# Recording (fix round 3): the callers are phase2/04-memory.sh _mem_check_residents (`run_verify V10a
# v10a-router-resident.sh`, fatal to step 04 on fail) and phase2/10-gate.sh (`run_verify V10a v10a-router-resident.sh`,
# recorded only, listed after `--` in `gate phase2`). The id is V10a: record_v accepts `V[0-9]+[a-z]?`, and no `V10` row
# is ever written from Phase 2, because gate() takes the latest record per id across phases and treats `info` as
# non-blocking, so a Phase 2 V10 row could stand in for the Phase 3 load test. CONVENTIONS §4/§5/§6 declare V10a and
# this script; tools/fill-workbook.py shows V10a as the Phase 2 evidence of the V10 row. The Phase 3 gate's V10 is
# never pre-populated.

# shellcheck source-path=SCRIPTDIR
# shellcheck source=../lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"
export ATLAS_LOG_TO_STDERR=1

KEY=router-qwen3.5-4b
envf="$ATLAS_ETC/engines/$KEY.env"
port=""
if [[ -r "$envf" ]]; then
  port="$(sed -nE 's/^LLAMA_ARG_PORT=([0-9]+)$/\1/p' "$envf" | head -n1)"
fi
if [[ -z "$port" ]]; then
  # Fallback: engines.json order (CONVENTIONS.md §8: LLAMA_PORT_BASE + index).
  port="$(python3 - "$ATLAS_DAY1_DIR/config/engines.json" "$KEY" "${LLAMA_PORT_BASE:-8100}" <<'PY' 2>/dev/null || true
import json, sys
engines = json.load(open(sys.argv[1], encoding="utf-8"))["engines"]
for i, e in enumerate(engines, start=1):
    if e["key"] == sys.argv[2]:
        print(int(sys.argv[3]) + i)
PY
)"
fi
[[ -n "$port" ]] || { echo "cannot determine the router port ($envf missing and engines.json unreadable)"; exit 1; }

health="$(curl -s --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" || true)"
if [[ "$health" != "200" ]]; then
  echo "llama-server@$KEY not healthy on 127.0.0.1:$port (HTTP ${health:-none}); systemctl status llama-server@$KEY"
  exit 1
fi

body='{"model":"router-qwen3.5-4b","messages":[{"role":"system","content":"You are Eleanor, the A.T.L.A.S. router. Answer in one word."},{"role":"user","content":"Reply with the single word OK."}],"max_tokens":16,"temperature":0}'
resp="$(curl -s --noproxy '*' --max-time 120 -H 'Content-Type: application/json' \
        -d "$body" "http://127.0.0.1:$port/v1/chat/completions" || true)"
[[ -n "$resp" ]] || { echo "no response from 127.0.0.1:$port/v1/chat/completions"; exit 1; }

# The timing request (D-33, approved 2026-10-17). The 16-token reply above is too short to time, so one raw
# POST /completion generates 128 tokens at temperature 0: "ignore_eos" keeps it from stopping early (VERIFIED at b11474,
# tools/server/server-schema.cpp:475-480), "cache_prompt": false and a fresh nonce keep the prompt uncached, and
# timings.predicted_per_second (tools/server/server-common.cpp:93-96) is the decode rate. Its verdict is the one Phase 3
# gives (phase3/loadtest.py check_band): a fail only below the entry's decode_floor_tok_s, read from engines.json (20
# tok/s for the router, never typed here); no floor (null or absent) or an unreadable engines.json is recorded, never a
# fail; an answer with fewer than 64 generated tokens is re-measured once and, if short again, recorded without a speed
# verdict (phase3/loadtest.py MIN_VERDICT_TOKENS). The review line, 70% of the projection's low end, is printed beside
# it and never fails. The router's slots hold 8,192 tokens each, too few for Phase 3's 8k prompt plus 128 tokens, so
# this short-prompt point is the router's only one (engines.json decode_rule: "judged at V10a").
floor_ref="$(python3 - "$ATLAS_DAY1_DIR/config/engines.json" "$KEY" <<'PY' 2>/dev/null || true
import json, sys
try:
    e = next(x for x in json.load(open(sys.argv[1], encoding="utf-8"))["engines"] if x["key"] == sys.argv[2])
except Exception:
    sys.exit(0)
f = e.get("decode_floor_tok_s")
p = e.get("projected_decode_tok_s") or []
print(f"{'none' if f is None else f}\t{p[0] if p else 'none'}")
PY
)"
timing() {
  local nonce tbody
  nonce="v10a-$(date +%s%N)-$RANDOM"
  tbody="{\"prompt\":\"$nonce Count upwards in words, one number per line, starting at one:\\n\",\"n_predict\":128,"
  tbody+="\"temperature\":0,\"ignore_eos\":true,\"cache_prompt\":false,\"stream\":false}"
  curl -s --noproxy '*' --max-time 120 -H 'Content-Type: application/json' -d "$tbody" \
       "http://127.0.0.1:$port/completion" || true
}
predicted_n() {
  python3 -c 'import json, sys
try:
    print(int((json.loads(sys.argv[1]).get("timings") or {}).get("predicted_n") or 0))
except Exception:
    print(0)' "$1" 2>/dev/null || echo 0
}
tresp="$(timing)"
retimed=0
if (( $(predicted_n "$tresp") < 64 )); then
  tresp="$(timing)"; retimed=1
fi

result="$(python3 - "$resp" "$tresp" "$floor_ref" "$retimed" <<'PY' 2>/dev/null || true
import json, sys
resp, tresp, floor_ref, retimed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
try:
    d = json.loads(resp)
except json.JSONDecodeError:
    print("FAIL\tnot JSON: " + resp[:120].replace("\n", " "))
    sys.exit(0)
if "error" in d:
    print("FAIL\terror: " + json.dumps(d["error"])[:160])
    sys.exit(0)
try:
    content = (d["choices"][0]["message"].get("content") or "").strip()
except (KeyError, IndexError, TypeError):
    print("FAIL\tno choices[0].message.content in: " + json.dumps(d)[:160])
    sys.exit(0)
if not content:
    print("FAIL\tempty reply (reasoning-only output? the router runs with --reasoning off)")
    sys.exit(0)
reply = f"reply={content[:60]!r} ({d.get('usage', {}).get('completion_tokens', '?')} tokens)"
floor_s, low_s = (floor_ref.split("\t") + ["none"])[:2] if floor_ref else ("UNKNOWN", "UNKNOWN")
try:
    t = json.loads(tresp) if tresp else {"error": "no response"}
except json.JSONDecodeError:
    t = {"error": "not JSON: " + tresp[:80].replace("\n", " ")}
if not isinstance(t, dict) or "error" in t or not isinstance(t.get("timings"), dict):
    why = json.dumps(t.get("error"))[:120] if isinstance(t, dict) and "error" in t else "no timings"
    print(f"FAIL\t{reply}; the timing request (POST /completion, 128 tokens) failed: {why}")
    sys.exit(0)
tm = t["timings"]
n = int(tm.get("predicted_n") or 0)
tps = tm.get("predicted_per_second")
if not isinstance(tps, (int, float)):
    print(f"FAIL\t{reply}; the timing request returned no predicted_per_second")
    sys.exit(0)
timed = f"decode {tps:.1f} tok/s over {n} tokens (/completion, temperature 0{', re-measured once' if retimed else ''})"
review = ""
if low_s not in ("none", "UNKNOWN"):
    line = round(0.7 * float(low_s), 2)
    review = f"; review line {line} ({'BELOW: a review item, not a fail' if tps < line else 'met'})"
if floor_s == "UNKNOWN":
    print(f"PASS\t{reply}; {timed}; floor UNKNOWN (engines.json unreadable): recorded, not judged")
elif floor_s == "none":
    print(f"PASS\t{reply}; {timed}; no decode_floor_tok_s in engines.json: recorded, not judged{review}")
elif n < 64:
    print(f"PASS\t{reply}; {timed}; fewer than 64 tokens after one re-measure: no speed verdict (floor {floor_s})")
elif tps < float(floor_s):
    print(f"FAIL\t{reply}; {timed} BELOW the router floor {floor_s} tok/s (D-33){review}")
else:
    print(f"PASS\t{reply}; {timed}, at or above the router floor {floor_s} tok/s (D-33){review}")
PY
)"
verdict="${result%%$'\t'*}"
msg="${result#*$'\t'}"
[[ -n "$result" ]] || { echo "could not parse the completion response: ${resp:0:160}"; exit 1; }
echo "router-qwen3.5-4b on 127.0.0.1:$port: $msg"
[[ "$verdict" == "PASS" ]] && exit 0
exit 1
