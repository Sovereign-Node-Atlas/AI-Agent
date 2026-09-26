#!/usr/bin/env bash
# phase2/04-memory.sh — Section 17 Phase 2 step 4: ChromaDB, LightRAG (D7), Docling, and the three resident small
# models of D4 (Section 5.3, 10.1, 10.2, 15.1). Sourced by phase2-services.sh through run_phase_steps; defines step_04.
#
# Order inside the step (each part idempotent, so a re-run after a failure resumes cheaply):
#   1. Pull router-qwen3.5-4b, embed-bge-m3, rerank-bge-v2-m3 with hf_download, hashes resolved from the HF tree API at
#      pull time and recorded in $ATLAS_SRV/models/<key>/MANIFEST.json (gguf-models.md §0/§11.1, research conflict a).
#   2. Re-render $ATLAS_ETC/engines/*.env (model files now present), enable the three llama-server@ units and start
#      them THROUGH THE ORCHESTRATOR'S CONTROL PATH (runuser atlas -> sudo systemctl start), which proves
#      /etc/sudoers.d/atlas-engines under sudo-rs; then check /health and one real request per model.
#   3. `docker compose ... up -d chromadb` from docker/core/compose.yml, heartbeat, the six Section 10.1 collections
#      through the HTTP API.
#   4. LightRAG 1.5.7 into the orchestrator venv, working dir $ATLAS_SRV/data/graph, tiktoken cache warmed.
#   5. Docling 2.129.0 into the same venv, models prefetched once under HF_HOME=$ATLAS_SRV/engines/hf.
#   6. $ATLAS_ETC/memory.env written for the orchestrator (contract below).
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * docker/core/compose.yml defines a service named `chromadb` (image chromadb/chroma:1.5.9, persist volume
#     $ATLAS_SRV/data/chroma) and, per CONVENTIONS.md §8, publishes it on host port 8000 bound to loopback/LAN/WG. This
#     step only runs `docker compose -f <that file> up -d chromadb` and then finds the endpoint with
#     `docker compose port chromadb 8000`, falling back to the container's bridge address.
#   * /opt/atlas/venv ($ATLAS_OPT/venv) is created by step 02 (orchestrator); when absent it is created here with
#     `python3 -m venv` and the fact is logged (the task text allows this).
#   * config/allowlist.txt must allow huggingface.co (+ cdn-lfs*.huggingface.co / cas-bridge), pypi.org,
#     files.pythonhosted.org, download.pytorch.org (CPU torch wheels) and openaipublic.blob.core.windows.net (tiktoken).
# Contract this file defines for others:
#   * $ATLAS_ETC/memory.env (root:atlas 640): CHROMA_URL, CHROMA_COLLECTIONS, ROUTER_URL, EMBEDDING_URL, RERANK_URL,
#     EMBEDDING_DIM, LIGHTRAG_WORKING_DIR, TIKTOKEN_CACHE_DIR, DOCLING_ARTIFACTS_PATH, HF_HOME, HF_HUB_OFFLINE.
#   * hf_tree_lfs / pull_engine_files below are the reference implementation of the manifest-at-pull-time rule; the
#     Phase 3 driver may `source` this file (it only defines functions) and call pull_engine_files <key> for the seven
#     large engines (it handles enumerate_pattern, join_into and the research-snippet hash comparison).

ATLAS_RESIDENT_KEYS=(router-qwen3.5-4b embed-bge-m3 rerank-bge-v2-m3)
ATLAS_CHROMA_COLLECTIONS=(corporate estate scars documents_corporate documents_estate sentinel)   # Section 10.1
LIGHTRAG_PIN="lightrag-hku[api]==1.5.7"       # services-tools.md §2.2 VERIFIED (PyPI 2026-09-02)
DOCLING_PIN="docling==2.129.0"                # services-tools.md §2.3 VERIFIED (PyPI 2026-09-18)
CHROMA_CLIENT_PIN="chromadb-client==1.5.9"    # services-tools.md §2.1 VERIFIED; installed for the orchestrator, not used here
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"

# --- engines.json access -------------------------------------------------------------------------------------------
# ej KEY FIELD — print a scalar field of one engine (empty when null/absent). Arrays print one element per line.
ej() {
  python3 - "$ATLAS_DAY1_DIR/config/engines.json" "$1" "$2" <<'PY'
import json, sys
path, key, field = sys.argv[1:4]
for e in json.load(open(path, encoding="utf-8"))["engines"]:
    if e["key"] == key:
        v = e.get(field)
        if v is None:
            sys.exit(0)
        if isinstance(v, list):
            for item in v:
                print(item if not isinstance(item, dict) else json.dumps(item))
        elif isinstance(v, bool):
            print("1" if v else "0")
        else:
            print(v)
        sys.exit(0)
sys.exit(f"engines.json: no engine {key!r}")
PY
}

# engine_port KEY — CONVENTIONS.md §8: LLAMA_PORT_BASE + 1-based index in engines.json order.
engine_port() {
  python3 - "$ATLAS_DAY1_DIR/config/engines.json" "$1" "${LLAMA_PORT_BASE:-8100}" <<'PY'
import json, sys
for i, e in enumerate(json.load(open(sys.argv[1], encoding="utf-8"))["engines"], start=1):
    if e["key"] == sys.argv[2]:
        print(int(sys.argv[3]) + i)
PY
}

# --- Hugging Face tree API -------------------------------------------------------------------------------------------
# hf_tree_lfs REPO [SUBDIR] — one line per file entry: "<path>\t<bytes>\t<sha256|none>". The sha256 is lfs.oid; a file
# stored outside LFS (none expected for .gguf) prints "none" and hf_download then records its computed hash instead.
hf_tree_lfs() {
  local repo="$1" sub="${2:-}"
  proxy_env
  local hdr=()
  local HF_TOKEN="${HF_TOKEN:-}"
  if [[ -z "$HF_TOKEN" && -f "$ATLAS_ETC/secrets/hf-token.env" ]]; then
    # shellcheck disable=SC1091  # secret file, HF_TOKEN=... (CONVENTIONS.md §2)
    source "$ATLAS_ETC/secrets/hf-token.env"
  fi
  [[ -n "$HF_TOKEN" ]] && hdr=(-H "Authorization: Bearer $HF_TOKEN")
  local url="${HF_ENDPOINT:-https://huggingface.co}/api/models/$repo/tree/main${sub:+/$sub}"
  local body code
  body="$(mktemp)"
  code="$(curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" "${hdr[@]}" "$url" || true)"
  case "$code" in
    200) ;;
    401|403) rm -f "$body"; die "hf_tree_lfs: HTTP $code for $url — gated repo: accept its licence on huggingface.co with the HF_TOKEN account" ;;
    404) rm -f "$body"; die "hf_tree_lfs: HTTP 404 for $url — the repo id or folder from the research is wrong (gguf-models.md §13)" ;;
    *) rm -f "$body"; die "hf_tree_lfs: HTTP ${code:-none} for $url (proxy down or huggingface.co not allowlisted?)" ;;
  esac
  python3 - "$body" <<'PY' || { rm -f "$body"; die "hf_tree_lfs: unexpected JSON from $url"; }
import json, sys
entries = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(entries, list):
    sys.exit(1)
for e in entries:
    if e.get("type") != "file":
        continue
    lfs = e.get("lfs") or {}
    print(f'{e["path"]}\t{lfs.get("size", e.get("size", 0))}\t{lfs.get("oid", "none")}')
PY
  rm -f "$body"
}

# pull_engine_files KEY — resolve the file list (literal files[] or enumerate_pattern), fetch lfs.oid/size from the tree,
# download each with hf_download into $ATLAS_SRV/models/KEY/, check the byte size, join raw splits, write MANIFEST.json.
pull_engine_files() {
  local key="$1"
  local repo subdir pattern join_into dest_dir
  repo="$(ej "$key" hf_repo)"; subdir="$(ej "$key" subdir)"; pattern="$(ej "$key" enumerate_pattern)"; join_into="$(ej "$key" join_into)"
  [[ -n "$repo" ]] || die "pull_engine_files: $key has no hf_repo"
  dest_dir="$ATLAS_SRV/models/$key"
  ensure_dir "$dest_dir" atlas:atlas 755

  local tree_lines=()
  mapfile -t tree_lines < <(hf_tree_lfs "$repo" "$subdir")
  (( ${#tree_lines[@]} > 0 )) || die "pull_engine_files: $repo${subdir:+/$subdir} lists no files"

  # wanted: "path<TAB>research_sha256|-" — literal names from files[] (basename matched against the tree) or the pattern.
  local wanted=() line
  if [[ -n "$pattern" ]]; then
    mapfile -t wanted < <(python3 - "$pattern" "${tree_lines[@]}" <<'PY'
import fnmatch, sys
pat = sys.argv[1].lower()
for line in sys.argv[2:]:
    path = line.split("\t")[0]
    if fnmatch.fnmatch(path.rsplit("/", 1)[-1].lower(), pat):
        print(f"{path}\t-")
PY
)
    (( ${#wanted[@]} > 0 )) || die "pull_engine_files: no file in $repo${subdir:+/$subdir} matches enumerate_pattern '$pattern'; tree: $(printf '%s ' "${tree_lines[@]%%$'\t'*}")"
    if [[ "$key" == embed-bge-m3 && ${#wanted[@]} -ne 1 ]]; then
      die "pull_engine_files: expected exactly one bge-m3 Q8_0 file, got ${#wanted[@]}: $(printf '%s ' "${wanted[@]%%$'\t'*}")"
    fi
  else
    local f name sha
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      name="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d["name"])' "$f")"
      sha="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("sha256") or "-")' "$f")"
      local base="${name##*/}" found=""
      for line in "${tree_lines[@]}"; do
        [[ "${line%%$'\t'*}" == "$name" || "${line%%$'\t'*}" == "${subdir:+$subdir/}$base" || "${line%%$'\t'*}" == "$base" ]] && { found="${line%%$'\t'*}"; break; }
      done
      [[ -n "$found" ]] || die "pull_engine_files: $repo has no file '$name' (research name is wrong or the repo changed); tree: $(printf '%s ' "${tree_lines[@]%%$'\t'*}")"
      wanted+=("$found"$'\t'"$sha")
    done < <(ej "$key" files)
  fi

  local manifest_entries=() w path research_sha bytes oid dest base have_size
  for w in "${wanted[@]}"; do
    path="${w%%$'\t'*}"; research_sha="${w#*$'\t'}"
    bytes=""; oid=""
    for line in "${tree_lines[@]}"; do
      if [[ "${line%%$'\t'*}" == "$path" ]]; then
        IFS=$'\t' read -r _ bytes oid <<<"$line"
        break
      fi
    done
    [[ -n "$oid" ]] || die "pull_engine_files: $path vanished from the tree listing"
    if [[ "$oid" == none ]]; then
      warn "pull_engine_files: $repo/$path has no lfs.oid (not an LFS object); hf_download will record its computed hash (UNVERIFIED)"
    fi
    if [[ "$research_sha" != "-" && "$oid" != none && "${research_sha,,}" != "$oid" ]]; then
      warn "pull_engine_files: research snippet sha256 for $path ($research_sha) differs from the tree API ($oid); using the tree value (gguf-models.md §0: snippets were UNVERIFIED)"
    fi
    base="${path##*/}"
    dest="$dest_dir/$base"
    if [[ -n "$join_into" && -f "$dest_dir/$join_into" && ! -f "$dest" ]]; then
      log "pull_engine_files: $base already consumed into $join_into; skipping"
      manifest_entries+=("$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1], "bytes": int(sys.argv[2]), "sha256": sys.argv[3], "joined": True}))' "$base" "$bytes" "$oid")")
      continue
    fi
    hf_download "$repo" "$path" "$dest" "$oid"
    have_size="$(stat -c %s "$dest")"
    if [[ "$bytes" =~ ^[0-9]+$ && "$bytes" -gt 0 && "$have_size" != "$bytes" ]]; then
      die "pull_engine_files: $dest is $have_size bytes, tree API says $bytes"
    fi
    chown atlas:atlas "$dest" "$dest.sha256" 2>/dev/null || true
    manifest_entries+=("$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1], "bytes": int(sys.argv[2]), "sha256": sys.argv[3]}))' "$base" "$have_size" "$oid")")
  done

  if [[ -n "$join_into" ]]; then
    # Research conflict (d): raw byte splits (TheBloke -split-a/-split-b) are cat-joined in files[] order and the joined
    # size must equal the sum of the parts; the parts are removed only after that check.
    local joined="$dest_dir/$join_into" parts=() sum=0 p
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      p="$dest_dir/$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["name"].rsplit("/",1)[-1])' "$f")"
      parts+=("$p")
    done < <(ej "$key" files)
    if [[ ! -f "$joined" ]]; then
      for p in "${parts[@]}"; do [[ -f "$p" ]] || die "pull_engine_files: split part $p missing before join"; sum=$(( sum + $(stat -c %s "$p") )); done
      log "pull_engine_files: joining ${#parts[@]} raw splits into $joined ($sum bytes)"
      cat "${parts[@]}" >"$joined.part" || die "pull_engine_files: cat-join failed"
      [[ "$(stat -c %s "$joined.part")" == "$sum" ]] || { rm -f "$joined.part"; die "pull_engine_files: joined size $(stat -c %s "$joined.part") != sum of parts $sum"; }
      mv -f "$joined.part" "$joined"
      chown atlas:atlas "$joined"
      rm -f "${parts[@]}"
      log "pull_engine_files: $joined complete; split parts removed"
    fi
  fi

  python3 - "$dest_dir/MANIFEST.json" "$key" "$repo" "${subdir:-}" "${join_into:-}" "${manifest_entries[@]}" <<'PY'
import datetime, json, sys
out, key, repo, subdir, join_into, *entries = sys.argv[1:]
doc = {
    "key": key, "repo": repo, "subdir": subdir or None, "revision": "main",
    "source": "https://huggingface.co/api/models/%s/tree/main%s (lfs.oid, lfs.size)" % (repo, ("/" + subdir) if subdir else ""),
    "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "join_into": join_into or None,
    "files": [json.loads(e) for e in entries],
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
    fh.write("\n")
PY
  chown atlas:atlas "$dest_dir/MANIFEST.json"
  log "pull_engine_files: $key complete, manifest $dest_dir/MANIFEST.json"
}

# --- resident units ---------------------------------------------------------------------------------------------------
_mem_render_envs() {
  python3 "$ATLAS_DAY1_DIR/phase2/engine-env.py" \
    --engines "$ATLAS_DAY1_DIR/config/engines.json" \
    --out "$ATLAS_ETC/engines" --models-dir "$ATLAS_SRV/models" --slots-dir "$ATLAS_SRV/data/slots" \
    --port-base "$LLAMA_PORT_BASE" --overrides "$ATLAS_ETC/engines/overrides.json" 2>/dev/null \
    || die "engine-env.py failed"
  chown root:atlas "$ATLAS_ETC/engines"/*.env
  chmod 640 "$ATLAS_ETC/engines"/*.env
  local key
  for key in "${ATLAS_RESIDENT_KEYS[@]}"; do
    grep -q '^ATLAS_MODEL_PRESENT=1$' "$ATLAS_ETC/engines/$key.env" \
      || die "$ATLAS_ETC/engines/$key.env says the model file is absent after the pull; check $ATLAS_SRV/models/$key"
  done
}

_mem_start_residents() {
  local key unit port sc
  sc="$(readlink -f "$(command -v systemctl)")"
  [[ -f /etc/systemd/system/llama-server@.service ]] || die "llama-server@.service not installed (step 01)"
  [[ -f /etc/sudoers.d/atlas-engines ]] || die "/etc/sudoers.d/atlas-engines not installed (step 01)"
  for key in "${ATLAS_RESIDENT_KEYS[@]}"; do
    unit="llama-server@$key"
    port="$(engine_port "$key")"
    systemctl enable --quiet "$unit"
    if systemctl is-active --quiet "$unit" && [[ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$port/health" || true)" == 200 ]]; then
      log "$unit already active and healthy on $port"
      continue
    fi
    systemctl reset-failed "$unit" 2>/dev/null || true
    # The orchestrator's control path (CONVENTIONS.md §8), exercised for real: atlas -> sudo-rs -> systemctl start.
    log "starting $unit through the control path (runuser atlas -> sudo -n $sc start $unit)"
    if ! runuser -u atlas -- sudo -n "$sc" start "$unit"; then
      warn "control path failed; unit journal follows"
      journalctl -u "$unit" --no-pager -n 30 >&2 || true
      die "'sudo systemctl start $unit' as atlas failed. Either sudo-rs rejected /etc/sudoers.d/atlas-engines (check: runuser -u atlas -- sudo -n -l) or the engine refused to load (journalctl -u $unit)."
    fi
    wait_http "http://127.0.0.1:$port/health" 300 || { journalctl -u "$unit" --no-pager -n 40 >&2 || true; die "$unit did not become healthy on 127.0.0.1:$port"; }
    log "$unit healthy on 127.0.0.1:$port"
  done
}

_mem_check_residents() {
  local port resp
  # Router: one chat completion, non-empty reply (V10 Phase 2 half; the gate runs verify/v10a-router-resident.sh too).
  run_verify V10a v10a-router-resident.sh || die "the resident router model did not answer a chat completion (see verify.jsonl V10a)"

  # Embeddings: /v1/embeddings must return a 1024-dim vector (bge-m3 dense dimension, FlagEmbedding README VERIFIED;
  # LightRAG's EMBEDDING_DIM=1024 and the Chroma collections depend on it).
  port="$(engine_port embed-bge-m3)"
  resp="$(curl -s --noproxy '*' --max-time 120 -H 'Content-Type: application/json' \
          -d '{"model":"embed-bge-m3","input":"A.T.L.A.S. resident embedding check"}' "http://127.0.0.1:$port/v1/embeddings" || true)"
  local dim
  dim="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(len(d["data"][0]["embedding"]))' "$resp" 2>/dev/null || echo 0)"
  [[ "$dim" == "$(ej embed-bge-m3 embedding_dim)" ]] \
    || die "embed-bge-m3 returned an embedding of dimension '$dim' (expected $(ej embed-bge-m3 embedding_dim)); response: ${resp:0:200}"
  log "embed-bge-m3: /v1/embeddings ok, $dim dimensions"

  # Reranker: the relevant document must outscore the irrelevant one (server README example, VERIFIED endpoint).
  port="$(engine_port rerank-bge-v2-m3)"
  resp="$(curl -s --noproxy '*' --max-time 120 -H 'Content-Type: application/json' \
          -d '{"model":"rerank-bge-v2-m3","query":"What is a panda?","top_n":2,"documents":["hi","The giant panda is a bear species endemic to China."]}' \
          "http://127.0.0.1:$port/v1/rerank" || true)"
  python3 - "$resp" <<'PY' || die "rerank-bge-v2-m3 did not rank the panda sentence above 'hi' (or /v1/rerank failed): ${resp:0:200}"
import json, sys
d = json.loads(sys.argv[1])
res = d["results"]
scores = {r["index"]: float(r["relevance_score"]) for r in res}
assert scores[1] > scores[0], scores
print(f"rerank ok: panda={scores[1]:.3f} hi={scores[0]:.3f}")
PY
  log "rerank-bge-v2-m3: /v1/rerank ok"
}

# --- ChromaDB ---------------------------------------------------------------------------------------------------------
CHROMA_URL=""
_mem_chroma_up() {
  local compose="$ATLAS_DAY1_DIR/docker/core/compose.yml"
  [[ -f "$compose" ]] || die "$compose is missing (docker/core/compose.yml is the core services writer's file; it must define service 'chromadb')"
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  grep -qE '^[[:space:]]+chromadb:' "$compose" || die "$compose defines no 'chromadb' service (contract in this file's header)"
  proxy_env
  log "docker compose up -d chromadb ($compose)"
  retry 3 docker compose -f "$compose" up -d chromadb || die "docker compose up chromadb failed (image pull through the proxy? network created by step 02?)"
  # Endpoint: the published port (CONVENTIONS.md §8 says host port 8000), else the container address on its network.
  local published addr
  published="$(docker compose -f "$compose" port chromadb 8000 2>/dev/null | head -n1 || true)"
  if [[ -n "$published" ]]; then
    # "0.0.0.0:8000" means every address; use loopback for our own calls.
    addr="${published/0.0.0.0/127.0.0.1}"
    addr="${addr/\[::\]/127.0.0.1}"
    CHROMA_URL="http://$addr"
  else
    local cid ip
    cid="$(docker compose -f "$compose" ps -q chromadb 2>/dev/null | head -n1 || true)"
    [[ -n "$cid" ]] || die "chromadb container not found after compose up"
    ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' "$cid" | grep -m1 . || true)"
    [[ -n "$ip" ]] || die "chromadb publishes no port and has no bridge address; cannot reach it from the host"
    CHROMA_URL="http://$ip:8000"
    warn "chromadb publishes no host port; using its container address $CHROMA_URL (CONVENTIONS.md §8 expects host port 8000)"
  fi
  wait_http "$CHROMA_URL/api/v2/heartbeat" 120 || die "ChromaDB heartbeat at $CHROMA_URL/api/v2/heartbeat never answered 200 (docker logs chromadb)"
  log "ChromaDB up at $CHROMA_URL"
}

_mem_chroma_collections() {
  # Section 10.1: six collections. Chroma 1.x REST (v2) paths below are UNVERIFIED by the research (it verified only
  # /api/v2/heartbeat); the listing after creation is the proof, and a missing name is fatal.
  local base="$CHROMA_URL/api/v2/tenants/default_tenant/databases/default_database/collections"
  local name code
  for name in "${ATLAS_CHROMA_COLLECTIONS[@]}"; do
    code="$(curl -s --noproxy '*' --max-time 30 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
            -d "{\"name\":\"$name\",\"get_or_create\":true,\"metadata\":{\"atlas\":\"day1\",\"embedding\":\"bge-m3\",\"dim\":1024}}" "$base" || true)"
    case "$code" in
      200|201) ;;
      *) die "ChromaDB refused to create collection '$name' (HTTP ${code:-none} at $base); the v2 collections path is UNVERIFIED, check the server version with: curl $CHROMA_URL/api/v2/version" ;;
    esac
  done
  local listing
  listing="$(curl -s --noproxy '*' --max-time 30 "$base?limit=100" || true)"
  python3 - "$listing" "${ATLAS_CHROMA_COLLECTIONS[@]}" <<'PY' || die "not every Section 10.1 collection is listed by ChromaDB: ${listing:0:300}"
import json, sys
names = {c["name"] for c in json.loads(sys.argv[1])}
missing = [n for n in sys.argv[2:] if n not in names]
if missing:
    print("missing:", missing, file=sys.stderr)
    sys.exit(1)
print("collections:", sorted(n for n in names if n in sys.argv[2:]))
PY
  log "ChromaDB collections present: ${ATLAS_CHROMA_COLLECTIONS[*]}"
}

# --- venv, LightRAG, Docling ---------------------------------------------------------------------------------------------
VENV=""
_mem_venv() {
  VENV="$ATLAS_OPT/venv"
  apt_install python3-venv python3-pip libcairo2   # libcairo2: cairosvg from lightrag-hku[api] (services-tools.md §2.2)
  if [[ ! -x "$VENV/bin/python" ]]; then
    log "$VENV absent (step 02 normally creates it); creating it here with python3 -m venv"
    mkdir -p "$ATLAS_OPT"
    python3 -m venv "$VENV" || die "python3 -m venv $VENV failed"
  fi
  if [[ ! -x "$VENV/bin/pip" ]]; then
    "$VENV/bin/python" -m ensurepip --upgrade || die "$VENV has no pip and ensurepip failed (a uv venv? create it with pip: python3 -m venv)"
  fi
  log "venv: $("$VENV/bin/python" --version 2>&1) at $VENV"
}

_pip() {
  proxy_env
  export PIP_CACHE_DIR="$ATLAS_STATE/pip-cache" PIP_DISABLE_PIP_VERSION_CHECK=1
  mkdir -p "$PIP_CACHE_DIR"
  retry 3 "$VENV/bin/python" -m pip install --quiet "$@"
}

_mem_lightrag() {
  local wd="$ATLAS_SRV/data/graph"
  ensure_dir "$wd" atlas:atlas 750
  ensure_dir "$wd/tiktoken" atlas:atlas 750
  if ! "$VENV/bin/python" -c 'import importlib.metadata as m; v=m.version("lightrag-hku"); assert v=="1.5.7", v' 2>/dev/null; then
    log "pip install $LIGHTRAG_PIN $CHROMA_CLIENT_PIN (host python is 3.14: cp314 wheels for its dependencies are UNVERIFIED, services-tools.md §2.2; a resolver failure stops here)"
    _pip "$LIGHTRAG_PIN" "$CHROMA_CLIENT_PIN" || die "pip install of $LIGHTRAG_PIN failed in $VENV (see above)"
  fi
  "$VENV/bin/python" -c 'import lightrag, importlib.metadata as m; print("lightrag", m.version("lightrag-hku"))' \
    || die "lightrag does not import from $VENV"
  # tiktoken fetches cl100k_base from openaipublic.blob.core.windows.net once; cached here so the node can go offline
  # (services-tools.md S2). That host must be in config/allowlist.txt.
  if ! compgen -G "$wd/tiktoken/*" >/dev/null; then
    proxy_env
    TIKTOKEN_CACHE_DIR="$wd/tiktoken" "$VENV/bin/python" -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")' \
      || die "tiktoken could not cache cl100k_base into $wd/tiktoken (allowlist openaipublic.blob.core.windows.net, then re-run)"
  fi
  chown -R atlas:atlas "$wd"
  log "LightRAG 1.5.7 installed; working dir $wd (JsonKV/NanoVectorDB/NetworkX defaults, Section 10.2 D7)"
}

_mem_docling() {
  local hf_home="$ATLAS_SRV/engines/hf" artifacts="$ATLAS_SRV/engines/docling"
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$hf_home" atlas:atlas 755
  ensure_dir "$artifacts" atlas:atlas 755
  if ! "$VENV/bin/python" -c 'import importlib.metadata as m; v=m.version("docling"); assert v=="2.129.0", v' 2>/dev/null; then
    # UNVERIFIED (services-tools.md §2.3): PyTorch CPU wheels for the host's Python 3.14. The CPU index is offered as an
    # extra index so pip prefers torch+cpu over the multi-GB CUDA build; if no cp314 torch exists anywhere pip fails and
    # this step stops. The container alternative (docling-serve in docker/core/compose.yml) still serves Open WebUI.
    log "pip install $DOCLING_PIN (extra index $TORCH_CPU_INDEX for CPU torch; several minutes)"
    _pip --extra-index-url "$TORCH_CPU_INDEX" "$DOCLING_PIN" \
      || die "pip install of $DOCLING_PIN failed in $VENV: no compatible torch/onnxruntime wheels for $("$VENV/bin/python" --version 2>&1)? (services-tools.md §2.3 says to use the docling-serve container or a Python 3.12 venv in that case)"
  fi
  # Prefetch the default model set once (layout tableformer code_formula picture_classifier rapidocr, VERIFIED
  # docling/cli/models.py) so HF_HUB_OFFLINE=1 can be set afterwards (Section 12.5, rule §7.1).
  if [[ ! -f "$artifacts/.atlas-prefetched" ]]; then
    proxy_env
    HF_HOME="$hf_home" HOME="$ATLAS_STATE" "$VENV/bin/docling-tools" models download -o "$artifacts" \
      || die "docling-tools models download failed (huggingface.co through the proxy; re-run to resume)"
    date -Is >"$artifacts/.atlas-prefetched"
  fi
  # Offline proof: convert a tiny HTML document with the network switched off and the artifacts path set.
  local tmp
  tmp="$(mktemp -d)"
  printf '<html><body><h1>ATLAS</h1><p>Docling offline check.</p><table><tr><td>a</td><td>1</td></tr></table></body></html>\n' >"$tmp/check.html"
  HF_HUB_OFFLINE=1 DOCLING_ARTIFACTS_PATH="$artifacts" HOME="$ATLAS_STATE" "$VENV/bin/python" - "$tmp/check.html" <<'PY' \
    || { rm -rf "$tmp"; die "docling could not convert a trivial HTML file offline from $VENV"; }
import sys
from docling.document_converter import DocumentConverter
doc = DocumentConverter().convert(sys.argv[1]).document
md = doc.export_to_markdown()
assert "ATLAS" in md and "offline check" in md, md
print("docling ok:", md.replace("\n", " ")[:80])
PY
  rm -rf "$tmp"
  chown -R atlas:atlas "$hf_home" "$artifacts"
  log "Docling 2.129.0 installed; models under $artifacts, HF cache $hf_home (HF_HUB_OFFLINE=1 from now on)"
}

_mem_write_env() {
  local f="$ATLAS_ETC/memory.env"
  [[ -e "$f" ]] || { : >"$f"; }
  ensure_kv "$f" CHROMA_URL "$CHROMA_URL"
  ensure_kv "$f" CHROMA_COLLECTIONS "\"${ATLAS_CHROMA_COLLECTIONS[*]}\""
  ensure_kv "$f" ROUTER_URL "http://127.0.0.1:$(engine_port router-qwen3.5-4b)/v1"
  ensure_kv "$f" EMBEDDING_URL "http://127.0.0.1:$(engine_port embed-bge-m3)/v1"
  ensure_kv "$f" RERANK_URL "http://127.0.0.1:$(engine_port rerank-bge-v2-m3)/v1"
  ensure_kv "$f" EMBEDDING_MODEL embed-bge-m3
  ensure_kv "$f" EMBEDDING_DIM "$(ej embed-bge-m3 embedding_dim)"
  ensure_kv "$f" LIGHTRAG_WORKING_DIR "$ATLAS_SRV/data/graph"
  ensure_kv "$f" TIKTOKEN_CACHE_DIR "$ATLAS_SRV/data/graph/tiktoken"
  ensure_kv "$f" DOCLING_ARTIFACTS_PATH "$ATLAS_SRV/engines/docling"
  ensure_kv "$f" HF_HOME "$ATLAS_SRV/engines/hf"
  ensure_kv "$f" HF_HUB_OFFLINE 1
  chown root:atlas "$f"
  chmod 640 "$f"
  chown -R atlas:atlas "$VENV" 2>/dev/null || true
  log "wrote $f"
}

step_04() {
  local key
  [[ -x /usr/local/bin/llama-server ]] || die "llama-server not installed: step 01 must run first (Section 17 order)"
  for key in "${ATLAS_RESIDENT_KEYS[@]}"; do
    log "pulling resident model $key from $(ej "$key" hf_repo)"
    pull_engine_files "$key"
  done
  _mem_render_envs
  _mem_start_residents
  _mem_check_residents
  _mem_chroma_up
  _mem_chroma_collections
  _mem_venv
  _mem_lightrag
  _mem_docling
  _mem_write_env
  notify "Phase 2 step 4 done: resident models, ChromaDB, LightRAG, Docling"
  log "step 04 done"
}
