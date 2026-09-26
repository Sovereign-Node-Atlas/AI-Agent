#!/usr/bin/env bash
# verify/v10a-router-resident.sh — V10, the Phase 2 half (Section 21): "Eleanor's resident 4B router model is verified
# separately at the Phase 2 gate". Sends one chat completion to llama-server@router-qwen3.5-4b and passes on a non-empty
# reply. Contract (CONVENTIONS.md §5): exit 0 pass / 1 fail / 2 deferred / 3 info; exactly one stdout line; no prompts.

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

result="$(python3 - "$resp" <<'PY' 2>/dev/null || true
import json, sys
try:
    d = json.loads(sys.argv[1])
except json.JSONDecodeError:
    print("FAIL\tnot JSON: " + sys.argv[1][:120].replace("\n", " "))
    sys.exit(0)
if "error" in d:
    print("FAIL\terror: " + json.dumps(d["error"])[:160])
    sys.exit(0)
try:
    content = (d["choices"][0]["message"].get("content") or "").strip()
except (KeyError, IndexError, TypeError):
    print("FAIL\tno choices[0].message.content in: " + json.dumps(d)[:160])
    sys.exit(0)
t = d.get("timings") or {}
tps = t.get("predicted_per_second")
extra = f", {tps:.1f} tok/s decode" if isinstance(tps, (int, float)) else ""
if content:
    print(f"PASS\treply={content[:60]!r} ({d.get('usage', {}).get('completion_tokens', '?')} tokens{extra})")
else:
    print("FAIL\tempty reply (reasoning-only output? the router runs with --reasoning off)")
PY
)"
verdict="${result%%$'\t'*}"
msg="${result#*$'\t'}"
[[ -n "$result" ]] || { echo "could not parse the completion response: ${resp:0:160}"; exit 1; }
echo "router-qwen3.5-4b on 127.0.0.1:$port: $msg"
[[ "$verdict" == "PASS" ]] && exit 0
exit 1
