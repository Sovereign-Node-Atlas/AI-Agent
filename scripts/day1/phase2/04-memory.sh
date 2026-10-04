#!/usr/bin/env bash
# phase2/04-memory.sh — Section 17 Phase 2 step 4: ChromaDB, LightRAG (D7), Docling, and the three resident small
# models of D4 (Section 5.3, 10.1, 10.2, 15.1). Sourced by phase2-services.sh through run_phase_steps; defines step_04.
#
# Order inside the step (each part idempotent, so a re-run after a failure resumes cheaply):
#   1. Pull router-qwen3.5-4b, embed-bge-m3, rerank-bge-v2-m3 with hf_download, hashes resolved from the HF tree API at
#      pull time and recorded in $ATLAS_SRV/models/<key>/MANIFEST.json (gguf-models.md §0/§11.1, research conflict a).
#      The tree API is queried ANONYMOUSLY first; the token is loaded only on a 401/403 (gated repo), so the secret is
#      never read into a shell variable for the ten public engines.json repos, and it is only ever sent to
#      https://huggingface.co (HF_ENDPOINT set to anything else stops the step; rule §7.2).
#   2. Re-render $ATLAS_ETC/engines/*.env (model files now present), enable the three llama-server@ units and start
#      them THROUGH THE ORCHESTRATOR'S CONTROL PATH (svc_user_run -> sudo systemctl start), which proves
#      /etc/sudoers.d/atlas-engines under sudo-rs in both directions: the exact command is granted and the same
#      command with an extra argument is REFUSED. Primary proof: `sudo -n -l CMD ARGS` (exit code decides, the text only
#      detects an unsupported flag); fallback when -l COMMAND is unsupported: the refused command is actually attempted
#      as atlas and must not be granted (CONVENTIONS.md §8 "nothing else"); then /health and one real request per model.
#      The router's chat completion is verify/v10a-router-resident.sh, recorded through run_verify under the id V10a
#      (the Section 21 V10 half that Section 17 places at the Phase 2 gate; phase2/10-gate.sh records the same id,
#      recorded only). A fail is fatal to this step. No `V10` row is written from Phase 2 (fix round 3): gate() takes the
#      latest record per id across phases and treats `info` as non-blocking, so a Phase 2 V10 row could stand in for
#      the Phase 3 load test. V10a is declared in CONVENTIONS §4/§5/§6 (phase2-services.sh header).
#   3. `docker compose ... up -d chromadb` from docker/core/compose.yml, heartbeat, the six Section 10.1 collections
#      through the HTTP API. The published address must be loopback (what compose.yml publishes) or the LAN address
#      (CONVENTIONS §8 allows loopback, LAN and WireGuard; the LAN case warns because ChromaDB 1.x has no
#      authentication); a wildcard publish (0.0.0.0, [::]) or any other address is fatal, never rewritten. Telemetry
#      (rule §7.1): the container's env must carry the two opt-outs and a blank proxy, the DOCKER-USER LOG rule must be
#      in place, and no direct beacon may have hit the DOCKER-USER drop (kernel log) or squid since compose up; the
#      observation is repeated after the collection operations, where a per-operation beacon would fire.
#   4. LightRAG 1.5.7 into the orchestrator venv (constrained to the atlas package's own `==` pins, then `pip check`),
#      working dir $ATLAS_SRV/data/graph. tiktoken's cl100k_base table (one public BPE file, no Principal data) is
#      looked for in THREE places, first match by sha256 wins (fix round 5; config/tiktoken/README.md): the vendored
#      copy scripts/day1/config/tiktoken/cl100k_base.tiktoken, then $ATLAS_SRV/staging/inbox/cl100k_base.tiktoken (the
#      Principal's drop directory, CONVENTIONS §2), then a ONE-TIME download through the allowlist proxy from
#      TIKTOKEN_URL (openaipublic.blob.core.windows.net, config/allowlist.txt "LightRAG tokenizer table": an ADDITION to
#      the Section 12.5 enumeration pending the Principal's Section 23 row, as that file's comment says; if the line is
#      struck, this path fails loudly and the step defers with the inbox remedy). Whichever path delivers it, the copy is verified against the
#      sha256 tiktoken itself pins, seeded into TIKTOKEN_CACHE_DIR under the cache name tiktoken expects (sha1 of the
#      blob URL) and proven with the network pointed at a closed port, so LIGHTRAG_TIKTOKEN_CACHED=1 is the NORMAL
#      outcome. Only when all three fail does the step complete with LIGHTRAG_TIKTOKEN_CACHED=0 in memory.env, a WARN
#      naming the exact download/checksum/drop commands and `phase2 --force 04` (phase1's --force 04 is the one that
#      reboots), repeated at the end of the step (a fresh Phase 2 must run unattended, Section 17; the orchestrator's
#      graph layer refuses to index until the table is cached, so nothing fails silently).
#   5. Docling 2.129.0 into the same venv: torch AND torchvision pinned to matching +cpu builds from
#      download.pytorch.org/whl/cpu in one resolve, then docling against them, then `pip check`; models prefetched once
#      under HF_HOME=$ATLAS_SRV/engines/hf and proven OFFLINE by converting a generated one-page PDF through the layout
#      model (an HTML file would exercise none of the prefetched models). Then the docling-serve CONTAINER
#      (atlas-docling, 127.0.0.1:5001, docker/core/compose.voice.yml) is started from here, as that file's header
#      invites, so Section 17 step 4's "Docling ingestion service" is delivered by step 4; step 5's `up -d` is a no-op
#      for it.
#   6. $ATLAS_ETC/memory.env written for the orchestrator (contract below); the venv made root:atlas, not writable
#      by atlas (Section 16.3 item 6: the orchestrator must not be able to modify its own code; step 02 does the same
#      to /opt/atlas/orchestrator and the venv, so the two steps agree; CONVENTIONS §2's "atlas" owner row for
#      /opt/atlas/venv should be amended to "root:atlas, read-only for atlas" -- requested, see the notes returned with
#      this file; phase2/README-contracts.md is the gate writer's file and is not edited here).
#
# Every python/tool invocation that may write ~/.cache runs with HOME=/var/cache/atlas on that command line only; HOME is
# never exported, because steps 05-10 run in the same driver process and docker finds /root/.docker/config.json via $HOME.
#
# Telemetry (rule §7.1): every python invocation below runs with HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
# ANONYMIZED_TELEMETRY=False (huggingface_hub's send_telemetry, docling/posthog) and HF_HUB_DISABLE_IMPLICIT_TOKEN=1
# HF_TOKEN_PATH / HF_STORED_TOKENS_PATH pointed at a root-only, non-secret, non-backed-up directory
# (/var/cache/atlas/hf-login) so huggingface_hub can never persist a login token under /srv/atlas or create unlisted
# files under /etc/atlas/secrets (CONVENTIONS §2). lib/common.sh proxy_env would be the natural single place for the
# three telemetry variables (every outbound call site calls it); that file is another writer's, so this step exports
# them itself (_mem_py_env).
#
# SECURITY NOTE the Principal must read (fix round, major; printed as a WARN line at the end of this step, fix round 2,
# so it is in the phase output and not only here): the `atlas` service account is in the `docker` group
# (CONVENTIONS.md §2, Phase 1 step 6) so that the orchestrator can run the AEGIS sandbox containers (Section 16.4) and
# this step can run `docker compose`. Membership of the docker group is root-equivalent on the host: `docker run -v /:/host
# --privileged` as atlas can read every file under /etc/atlas/secrets, rewrite /opt/atlas/day1, config/allowlist.txt and
# /etc/sudoers.d/atlas-engines, and lift every 16.4 cap the orchestrator sets for its own containers. Section 16.3
# items 5, 6 and 8 therefore hold by the orchestrator's behaviour (orchestrator/src/atlas/sandbox.py builds one fixed
# `docker run` line, V17) and not by the OS. Accepted for Day 1 because rootless docker for gfx1151 device pass-through
# and a root-owned sandbox wrapper under a second sudoers fragment are both untested here; the wrapper
# (/usr/local/sbin/atlas-sandbox-run: fixed docker run line, arguments validated, no -v, no --privileged) is the
# follow-up that lets atlas leave the group. Cross-writer: the group add is phase1/06-docker.sh, the wrapper and the
# V17 sub-check (the sandbox invocation refuses `-v /` and `--privileged`) belong to the sandbox and verify writers.
#
# Contracts relied on from other writers (CONVENTIONS.md §1):
#   * docker/core/compose.yml defines a service named `chromadb` (image chromadb/chroma:1.5.9, persist volume
#     $ATLAS_SRV/data/chroma) and publishes it on host port 8000 on 127.0.0.1 (its header; §8 would also allow the LAN
#     address). This step only runs `docker compose -f <that file> up -d chromadb` and then finds the endpoint with
#     `docker compose port chromadb 8000`, falling back to the container's bridge address.
#   * docker/core/compose.voice.yml (the voice writer's overlay, merged into the same project) defines a service named
#     `docling` (image ghcr.io/docling-project/docling-serve-cpu:v1.34.0, container atlas-docling, published on
#     127.0.0.1:5001, no voice.env needed; its header gives the exact merge command this step runs). /docs is the
#     health path (docling-serve documents no /health; the gate uses the same path).
#   * /opt/atlas/venv ($ATLAS_OPT/venv) is created by step 02 (phase2/02-orchestrator.sh) from a uv-managed CPython
#     3.12 (ORCH_PYTHON="3.12", seeded pip, the atlas package installed). This step REQUIRES that venv and stops with
#     `--force 02` when it is absent or not 3.12 (fix round 3: an earlier revision created a host-python venv here,
#     which would have been 3.14 and unable to import the orchestrator's pinned tree).
#   * phase1/docker-egress-rules.sh (Phase 1 step 6) logs every dropped container packet to the kernel log with the
#     prefix "[ATLAS docker egress denied] " (rate-limited 5/min for the whole DOCKER-USER chain) before the terminal
#     DROP; _mem_chroma_telemetry_check asserts that LOG rule is present (`iptables -S DOCKER-USER`) and reads the prefix
#     through journalctl -k. The prefix is part of the contract.
#   * config/allowlist.txt (phase1/04-system.sh's writer) carries huggingface.co and .hf.co (the LFS/CAS hosts that
#     resolve redirects land on *.hf.co), pypi.org, files.pythonhosted.org, download.pytorch.org (CPU torch wheels),
#     ghcr.io for the docling-serve image (the voice writer relies on it already) and, since fix round 5,
#     openaipublic.blob.core.windows.net for the one-time tiktoken table fetch of header item 4 (the fix-round-3
#     withdrawal is reversed: a table that is never there in practice left LIGHTRAG_TIKTOKEN_CACHED=0 the usual outcome;
#     fix round 6: the host is a Section 12.5 addition the Principal approves, recorded as pending in the allowlist
#     comment with a Section 23 row requested; the review sandbox cannot vendor the file, HTTP 403).
#   * $ATLAS_SRV/staging/inbox (phase1/03-mounts.sh, $PRINCIPAL_USER:atlas 2770) is where the Principal may drop
#     cl100k_base.tiktoken; the file is verified by sha256 before use and left in place. config/tiktoken/README.md and
#     SHA256SUMS describe the vendored copy (first in the search order) and how to commit it.
#   * lib/common.sh hf_download: as of this round it still puts the bearer token on curl's argv (`-H "Authorization:
#     Bearer $HF_TOKEN"`), readable by every local account in /proc/<pid>/cmdline for the whole transfer. REQUESTED of
#     that writer: read the header from stdin (`printf ... | curl -H @- ...`, curl >= 7.55) as hf_tree_lfs below does.
#     This file's pull path does not depend on the fix: none of the ten engines.json repos is gated, so
#     pull_engine_files calls hf_download through hf_download_public, which keeps the token out of the call entirely.
#   * orchestrator/src/atlas/memory.py _check_tokenizer_cache names the allowlist as the remedy for a missing tiktoken
#     table; with this round the remedy is the inbox drop below (cross-writer: that message should name it).
# Contract this file defines for others:
#   * $ATLAS_ETC/memory.env (root:atlas 640): CHROMA_URL, CHROMA_COLLECTIONS, ROUTER_URL, EMBEDDING_URL, RERANK_URL,
#     EMBEDDING_MODEL, EMBEDDING_DIM, LIGHTRAG_WORKING_DIR, TIKTOKEN_CACHE_DIR, LIGHTRAG_TIKTOKEN_CACHED (1 when
#     cl100k_base is in TIKTOKEN_CACHE_DIR, 0 when the seed was deferred for lack of a verified copy),
#     DOCLING_ARTIFACTS_PATH, DOCLING_SERVER_URL, HF_HOME, HF_HUB_OFFLINE, the telemetry opt-outs, HF_TOKEN_PATH /
#     HF_STORED_TOKENS_PATH (root-only cache dir), and the loopback pins for any defaulted OpenAI-compatible client
#     (fix round 3; LightRAG 1.5.7's default llm/embedding funcs are OpenAI cloud calls and the installed `openai`
#     SDK resolves api.openai.com unless told otherwise): OPENAI_BASE_URL=<ROUTER_URL>, OPENAI_API_KEY=atlas-local-no-cloud,
#     LLM_BINDING=openai, LLM_BINDING_HOST=<ROUTER_URL>, LLM_MODEL=router-qwen3.5-4b, EMBEDDING_BINDING=openai,
#     EMBEDDING_BINDING_HOST=<EMBEDDING_URL> (LightRAG's own server knobs, env.example names).
#   * hf_tree_lfs / pull_engine_files below are the reference implementation of the manifest-at-pull-time rule; the
#     Phase 3 driver may `source` this file (it only defines functions) and call pull_engine_files <key> for the seven
#     large engines (it handles enumerate_pattern, join_into and the research-snippet hash comparison).

ATLAS_RESIDENT_KEYS=(router-qwen3.5-4b embed-bge-m3 rerank-bge-v2-m3)
ATLAS_CHROMA_COLLECTIONS=(corporate estate scars documents_corporate documents_estate sentinel)   # Section 10.1
ATLAS_CACHE_DIR="/var/cache/atlas"                   # build caches (pip, tool HOME); never under $ATLAS_STATE (CONVENTIONS §2)
HF_BASE="https://huggingface.co"                     # the ONLY host the HF token is ever sent to (rule §7.2)
LIGHTRAG_PIN="lightrag-hku[api]==1.5.7"       # services-tools.md §2.2 VERIFIED (PyPI 2026-09-02)
DOCLING_PIN="docling==2.129.0"                # services-tools.md §2.3 VERIFIED (PyPI 2026-09-18)
CHROMA_CLIENT_PIN="chromadb-client==1.5.9"    # services-tools.md §2.1 VERIFIED; installed for the orchestrator, not used here
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
# UNVERIFIED: the +cpu cp312 wheels for these two versions on download.pytorch.org/whl/cpu — the review session's proxy
# denied that index. VERIFIED on PyPI (2026-10-04): torch 2.14.1 is the newest release inside docling-slim 2.129.0's
# `torch<3.0.0,>=2.2.2` and docling-ibm-models 4.0.3's identical range; torchvision 0.29.1 is its companion
# (requires torch>=2.14.0; `torchvision<1,>=0` in both). pip fails and the step dies if the index lacks either wheel;
# the PDF proof below exercises both libraries for real (rule §7.9: pinned, not floating).
TORCH_CPU_PIN="torch==2.14.1+cpu"
TORCHVISION_CPU_PIN="torchvision==0.29.1+cpu"
# tiktoken's cl100k_base table: URL and sha256 exactly as tiktoken 0.14.0 pins them (tiktoken_ext/openai_public.py,
# VERIFIED from the PyPI sdist 2026-10-04); the cache file name is sha1(URL) (tiktoken/load.py read_file_cached,
# VERIFIED), the same derivation orchestrator/src/atlas/memory.py tiktoken_cache_file uses.
TIKTOKEN_URL="https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
TIKTOKEN_SHA256="223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
TIKTOKEN_FILE="cl100k_base.tiktoken"
DOCLING_SERVE_URL="http://127.0.0.1:5001"     # compose.voice.yml publishes atlas-docling on loopback only

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
# Anonymous first; the token is loaded and sent only when the repo answers 401/403 (gated), and only to $HF_BASE
# (fix round 3): the ten engines.json repos are public, so the secret is never read for them.
_hf_endpoint_check() {
  if [[ -n "${HF_ENDPOINT:-}" && "${HF_ENDPOINT%/}" != "$HF_BASE" ]]; then
    die "HF_ENDPOINT is set to '$HF_ENDPOINT'; this step only talks to $HF_BASE and sends the token nowhere else (rule §7.2). Unset it (atlas.env or the environment) and re-run"
  fi
}

# _hf_tree_get URL BODY_FILE [with-token] — prints the HTTP code; the bearer header travels on curl's STDIN (-H @-,
# curl >= 7.55): never on argv, where every local user could read it from /proc/<pid>/cmdline for the duration of the
# request, and never in a temp file that a SIGINT or a die between mktemp and rm could leave behind (fix round 2, §7.2).
_hf_tree_get() {
  local url="$1" body="$2" mode="${3:-anonymous}"
  if [[ "$mode" == with-token ]]; then
    local HF_TOKEN="${HF_TOKEN:-}"
    if [[ -z "$HF_TOKEN" && -f "$ATLAS_ETC/secrets/hf-token.env" ]]; then
      # shellcheck disable=SC1091  # secret file, HF_TOKEN=... (CONVENTIONS.md §2)
      source "$ATLAS_ETC/secrets/hf-token.env"
    fi
    [[ -n "$HF_TOKEN" ]] || { echo "no-token"; return 0; }
    printf 'Authorization: Bearer %s\n' "$HF_TOKEN" \
      | curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" -H @- "$url" || true
  else
    curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" "$url" || true
  fi
}

hf_tree_lfs() {
  local repo="$1" sub="${2:-}"
  proxy_env
  _hf_endpoint_check
  local url="$HF_BASE/api/models/$repo/tree/main${sub:+/$sub}"
  local body code
  body="$(mktemp)"
  code="$(_hf_tree_get "$url" "$body")"
  if [[ "$code" == 401 || "$code" == 403 ]]; then
    # >&2: this function's stdout is the tree listing pull_engine_files captures; the console copy of the log line must
    # not land in it (the file copy is unaffected).
    log "hf_tree_lfs: $repo answers HTTP $code anonymously (gated); retrying with the token from $ATLAS_ETC/secrets/hf-token.env" >&2
    code="$(_hf_tree_get "$url" "$body" with-token)"
  fi
  case "$code" in
    200) ;;
    no-token) rm -f "$body"; die "hf_tree_lfs: $repo is gated and $ATLAS_ETC/secrets/hf-token.env is absent (phase2-services.sh prompts for it)" ;;
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

# hf_download_public REPO FILE DEST SHA256 — hf_download (lib/common.sh: resumable, through the proxy, sha256-verified)
# WITHOUT a bearer token. None of the ten engines.json repos is gated, so the token is never needed here, and today's
# hf_download would put it on curl's argv for every local account to read (header, cross-writer request). hf_download
# takes the token from $HF_TOKEN or from $ATLAS_ETC/secrets/hf-token.env; an empty $HF_TOKEN does not stop the file
# read, so the call runs with ATLAS_ETC pointed at an empty directory (CONVENTIONS §4: every node path is overridable)
# AFTER proxy_env has exported the proxy variables from the real $ATLAS_ETC/proxy.env (proxy_env returns 0 and leaves
# the exported variables alone when its file is absent). The temporary assignment lasts for the one function call.
hf_download_public() {
  local empty="$ATLAS_CACHE_DIR/no-secrets"
  proxy_env
  ensure_dir "$empty" root:root 755
  [[ -n "${HTTPS_PROXY:-}" ]] || die "hf_download_public: HTTPS_PROXY is not exported (proxy_env found no $ATLAS_ETC/proxy.env; rule §7.1)"
  HF_TOKEN="" ATLAS_ETC="$empty" hf_download "$@"
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

  # Capture first, then split: a die inside a process substitution only ends that subshell and the outer shell would
  # continue with an empty array; an assignment from a failing command substitution propagates under set -e.
  local tree tree_lines=()
  tree="$(hf_tree_lfs "$repo" "$subdir")" || die "pull_engine_files: tree listing of $repo${subdir:+/$subdir} failed (see the message above)"
  [[ -n "$tree" ]] || die "pull_engine_files: $repo${subdir:+/$subdir} lists no files"
  mapfile -t tree_lines <<<"$tree"

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
    hf_download_public "$repo" "$path" "$dest" "$oid"
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
    local joined="$dest_dir/$join_into" parts=() sum=0 p pbase pbytes
    while IFS= read -r f; do
      [[ -n "$f" ]] || continue
      pbase="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["name"].rsplit("/",1)[-1])' "$f")"
      parts+=("$dest_dir/$pbase")
      # The expected size of every part comes from the tree API (recorded in manifest_entries above), not from the
      # part files, which may already be gone.
      pbytes=""
      for line in "${tree_lines[@]}"; do
        if [[ "${line%%$'\t'*}" == *"$pbase" ]]; then IFS=$'\t' read -r _ pbytes _ <<<"$line"; break; fi
      done
      [[ "$pbytes" =~ ^[0-9]+$ && "$pbytes" -gt 0 ]] || die "pull_engine_files: tree API gives no byte size for split part $pbase"
      sum=$(( sum + pbytes ))
    done < <(ej "$key" files)
    if [[ ! -f "$joined" ]]; then
      for p in "${parts[@]}"; do [[ -f "$p" ]] || die "pull_engine_files: split part $p missing before join"; done
      log "pull_engine_files: joining ${#parts[@]} raw splits into $joined ($sum bytes)"
      cat "${parts[@]}" >"$joined.part" || die "pull_engine_files: cat-join failed"
      [[ "$(stat -c %s "$joined.part")" == "$sum" ]] || { rm -f "$joined.part"; die "pull_engine_files: joined size $(stat -c %s "$joined.part") != sum of parts $sum"; }
      mv -f "$joined.part" "$joined"
      chown atlas:atlas "$joined"
    fi
    # Cleanup runs whenever the joined file exists and matches, so a run interrupted between the mv and the rm does
    # not leave ~74 GB of split parts beside the joined file for ever (fix round, idempotency).
    [[ "$(stat -c %s "$joined")" == "$sum" ]] || die "pull_engine_files: $joined is $(stat -c %s "$joined") bytes, the tree API parts sum to $sum; remove it and re-run"
    for p in "${parts[@]}"; do
      if [[ -f "$p" ]]; then rm -f "$p" "$p.sha256"; log "pull_engine_files: removed consumed split part $p"; fi
    done
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
  # stderr is kept (rule §7.4: the failing reason must be logged); only the expected "model file not present yet" note
  # for the seven large engines (pulled in Phase 3) is filtered.
  python3 "$ATLAS_DAY1_DIR/phase2/engine-env.py" \
    --engines "$ATLAS_DAY1_DIR/config/engines.json" \
    --out "$ATLAS_ETC/engines" --models-dir "$ATLAS_SRV/models" --slots-dir "$ATLAS_SRV/data/slots" \
    --port-base "$LLAMA_PORT_BASE" --overrides "$ATLAS_ETC/engines/overrides.json" \
    2> >(grep -v 'model file not present yet' >&2) \
    || die "engine-env.py failed (its message is above)"
  chown root:atlas "$ATLAS_ETC/engines"/*.env
  chmod 640 "$ATLAS_ETC/engines"/*.env
  local key
  for key in "${ATLAS_RESIDENT_KEYS[@]}"; do
    grep -q '^ATLAS_MODEL_PRESENT=1$' "$ATLAS_ETC/engines/$key.env" \
      || die "$ATLAS_ETC/engines/$key.env says the model file is absent after the pull; check $ATLAS_SRV/models/$key"
  done
}

_sudo_l_unsupported() {
  # True when sudo's output is a usage error, i.e. this implementation does not take `-l COMMAND` (sudo-rs, UNVERIFIED).
  grep -qiE 'usage:|unknown option|invalid option|unrecognized|unexpected argument|not supported' <<<"$1"
}

_mem_sudoers_negative_proof() {
  # CONVENTIONS.md §8 "exactly those commands and nothing else": the fragment is per-key, per-verb, no wildcard
  # (phase2/01-llama.sh). Primary proof: `sudo -n -l CMD ARGS` asks the policy whether the command is permitted
  # WITHOUT executing it (sudo(8): exit 0 and the path when allowed, exit 1 otherwise). The EXIT CODE is the verdict:
  # sudo 1.9's list mode is SILENT for a refused command (reproduced in the fix round: exit 1, empty output), so no
  # refusal text is required; the text is read only to detect a usage error, i.e. an implementation without
  # `-l COMMAND` (sudo-rs, UNVERIFIED). Fallback in that case (fix round 2, implementation-independent): ATTEMPT the
  # command that must be refused, `systemctl start llama-server@router-qwen3.5-4b --no-block`, as atlas through sudo.
  # Granted, it exits 0 (and starts the router, which this step starts right after anyway): a SECURITY failure.
  # Refused, sudo exits 1 before systemctl runs. Either way nothing harmful happens. The other two probes (a second
  # unit, a verb outside the set) are checked only on the primary path, where they cost nothing. No `--` before the
  # command (fix round 3): $sc starts with `/`, so there is no option ambiguity, sudo-rs's handling of `--` after -l is
  # UNVERIFIED, and the form now matches the real control-path call in _mem_start_residents exactly.
  local sc="$1" probe rc out
  local probes=(
    "start llama-server@router-qwen3.5-4b --no-block"
    "stop llama-server@router-qwen3.5-4b atlas-day1-negative-probe.service"
    "status llama-server@router-qwen3.5-4b"
  )
  local path=list
  for probe in "${probes[@]}"; do
    rc=0
    # shellcheck disable=SC2086  # the probe is deliberately word-split into separate systemctl arguments
    out="$(svc_user_run sudo -n -l "$sc" $probe 2>&1)" || rc=$?
    if (( rc == 0 )); then
      die "SECURITY: sudo -l says atlas may run 'systemctl $probe'; /etc/sudoers.d/atlas-engines grants more than CONVENTIONS.md §8 allows (fragment: $(tr '\n' ';' </etc/sudoers.d/atlas-engines | cut -c1-300))"
    fi
    if _sudo_l_unsupported "$out"; then
      path=execute
      warn "sudo -n -l COMMAND is not supported here (${out//$'\n'/ }); proving the refusal by attempting the command instead"
      break
    fi
    log "sudoers negative proof ok (sudo -l): 'systemctl $probe' refused for atlas (exit $rc${out:+: ${out//$'\n'/ }})"
  done
  if [[ "$path" == execute ]]; then
    rc=0
    out="$(svc_user_run sudo -n "$sc" start llama-server@router-qwen3.5-4b --no-block 2>&1)" || rc=$?
    if (( rc == 0 )); then
      die "SECURITY: atlas was able to run 'sudo systemctl start llama-server@router-qwen3.5-4b --no-block' (an argument outside the fragment); /etc/sudoers.d/atlas-engines grants more than CONVENTIONS.md §8 allows (fragment: $(tr '\n' ';' </etc/sudoers.d/atlas-engines | cut -c1-300))"
    fi
    # systemctl itself would say "Failed to ..." only if sudo had let it run; that would also be a grant.
    if grep -qE '^Failed to|Unit .* not (found|loaded)' <<<"$out"; then
      die "SECURITY: sudo passed 'systemctl start llama-server@router-qwen3.5-4b --no-block' through to systemctl ('${out//$'\n'/ }'); /etc/sudoers.d/atlas-engines grants more than CONVENTIONS.md §8 allows"
    fi
    log "sudoers negative proof ok (execute): the command with an extra argument was refused for atlas (exit $rc: ${out//$'\n'/ })"
  fi
  # Positive half of the same check: the exact granted command must be listed as allowed (list path); on the execute
  # path the proof is the real `sudo systemctl start` in _mem_start_residents, which dies if refused.
  if [[ "$path" == list ]]; then
    if ! out="$(svc_user_run sudo -n -l "$sc" start llama-server@router-qwen3.5-4b 2>&1)"; then
      die "sudo -l says atlas may NOT run 'systemctl start llama-server@router-qwen3.5-4b' ('${out//$'\n'/ }'); /etc/sudoers.d/atlas-engines is not in force (sudo-rs parse? includedir? check: visudo -c)"
    fi
    log "sudoers positive proof ok (sudo -l): 'systemctl start llama-server@router-qwen3.5-4b' allowed for atlas"
  else
    log "sudoers positive proof: left to the real control-path start below (sudo -l COMMAND unsupported)"
  fi
}

_mem_start_residents() {
  local key unit port sc
  sc="$(readlink -f "$(command -v systemctl)")"
  [[ -f /etc/systemd/system/llama-server@.service ]] || die "llama-server@.service not installed (step 01)"
  [[ -f /etc/sudoers.d/atlas-engines ]] || die "/etc/sudoers.d/atlas-engines not installed (step 01)"
  grep -q 'llama-server@\*' /etc/sudoers.d/atlas-engines \
    && die "/etc/sudoers.d/atlas-engines still carries a wildcard line (llama-server@*); re-run step 01 (--force 01) to regenerate it"
  _mem_sudoers_negative_proof "$sc"
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
    log "starting $unit through the control path (svc_user_run sudo -n $sc start $unit)"
    if ! svc_user_run sudo -n "$sc" start "$unit"; then
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
  # Router: one chat completion, non-empty reply (verify/v10a-router-resident.sh). Section 21 places this V10 half at
  # the Phase 2 gate; it is recorded under its own id V10a (run_verify maps exit 0/1/2/3 and flattens the evidence),
  # exactly as phase2/10-gate.sh records it. Never under V10 (fix round 3): gate() takes the latest record per id
  # across phases and treats `info` as non-blocking, so a Phase 2 V10 row could pass the Phase 3 gate, or a later
  # `phase2 --force 04` could shadow a Phase 3 `V10 fail`. The Phase 3 gate's V10 is never pre-populated.
  run_verify V10a v10a-router-resident.sh \
    || die "the resident router model did not answer a chat completion; see verify.jsonl V10a and journalctl -u llama-server@router-qwen3.5-4b"

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
_core_compose() {
  # Same env file as step 02 (docker/core/compose.yml header: `--env-file /etc/atlas/core.env`), so compose sees one
  # service definition whoever calls it and never recreates the container on a different persist volume (fix round).
  local envf=()
  [[ -f "$ATLAS_ETC/core.env" ]] && envf=(--env-file "$ATLAS_ETC/core.env")
  docker compose -f "$ATLAS_DAY1_DIR/docker/core/compose.yml" "${envf[@]}" "$@"
}

# _mem_lan_ip — the node's LAN address: load_env's LAN_IP, else /etc/atlas/docker.env (Phase 1 step 6).
_mem_lan_ip() {
  if [[ -n "${LAN_IP:-}" ]]; then printf '%s\n' "$LAN_IP"; return 0; fi
  [[ -f "$ATLAS_ETC/docker.env" ]] && sed -nE 's/^LAN_IP=//p' "$ATLAS_ETC/docker.env" | head -n1
  return 0
}

_mem_chroma_telemetry_check() {
  # Rule §7.1: telemetry is left DISABLED, not merely blocked. docker/core/compose.yml (cross-writer) gives chromadb the
  # two opt-outs (ANONYMIZED_TELEMETRY=false, CHROMA_TELEMETRY_ENABLED=false; both variable names UNVERIFIED for the Rust
  # server) and the *no-proxy anchor (HTTP(S)_PROXY empty, NO_PROXY=*), so a posthog beacon, if the opt-outs are not
  # honoured, goes DIRECT from the container, is dropped by the DOCKER-USER rules (phase1/docker-egress-rules.sh: a LOG
  # rule "[ATLAS docker egress denied] " at 5/min precedes the terminal DROP) and never reaches squid. The observation
  # therefore reads (1) the container's environment, (2) the kernel log for a denied packet from the container's address
  # since compose up, (3) squid's log as well, in case the proxy variables ever come back, and (4) the container's own
  # log for telemetry strings (informational). Any beacon = failure. Called twice (fix round 3): after the heartbeat and
  # again after the collection operations, where a per-operation beacon would fire. The LOG rule's presence is asserted
  # because without it the kernel-log evidence would be blind and the claim below false; its 5/min limit is shared by
  # the whole DOCKER-USER chain, so the kernel-log half is evidence, not proof -- blocking itself is unaffected.
  local since="$1" when="${2:-after heartbeat}" cid ip log_f=/var/log/squid/access.log hits env_dump
  cid="$(_core_compose ps -q chromadb 2>/dev/null | head -n1 || true)"
  [[ -n "$cid" ]] || die "chromadb container not found for the telemetry check"
  env_dump="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$cid")"
  local var
  for var in ANONYMIZED_TELEMETRY=false CHROMA_TELEMETRY_ENABLED=false; do
    grep -qix "$var" <<<"$env_dump" \
      || die "atlas-chromadb runs without $var in its environment (docker/core/compose.yml chromadb.environment must set it; rule §7.1)"
  done
  if grep -qE '^(HTTPS?_PROXY|https?_proxy)=.+' <<<"$env_dump"; then
    die "atlas-chromadb has a proxy configured ($(grep -E '^(HTTPS?_PROXY|https?_proxy)=' <<<"$env_dump" | tr '\n' ' ')); docker/core/compose.yml must keep the *no-proxy anchor on chromadb so a beacon can never find squid (rule §7.1)"
  fi
  iptables -w -S DOCKER-USER 2>/dev/null | grep -qF 'ATLAS docker egress denied' \
    || die "contract: no DOCKER-USER LOG rule with prefix '[ATLAS docker egress denied] ' (phase1/docker-egress-rules.sh, unit atlas-docker-egress.service); without it a container beacon would be dropped unseen, so this check cannot stand. Is the unit active?"
  ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' "$cid" | grep -m1 . || true)"
  [[ -n "$ip" ]] || die "chromadb has no bridge address; cannot observe its egress"
  sleep 5   # the Rust server's start-up / per-operation beacon, if any, fires within seconds
  hits="$(journalctl -k -S "@$since" --no-pager -o cat 2>/dev/null | grep -F 'ATLAS docker egress denied' | grep -F "SRC=$ip " \
          | sed -nE 's/.*DST=([0-9a-f.:]+).*DPT=([0-9]+).*/\1:\2/p' | sort -u | tr '\n' ' ' || true)"
  if [[ -n "$hits" ]]; then
    die "chromadb ($ip) tried to reach $hits directly and was dropped by DOCKER-USER (kernel log since compose up): the telemetry opt-outs are not honoured by chromadb/chroma:1.5.9 (UNVERIFIED variable names in docker/core/compose.yml); find the switch the Rust server reads, or pin an image that has none (rule §7.1)"
  fi
  if [[ -r "$log_f" ]]; then
    hits="$(awk -v since="$since" -v ip="$ip" '$1 >= since && $3 == ip && $4 ~ /TCP_DENIED/ {print $7}' "$log_f" | sort -u | tr '\n' ' ' || true)"
    [[ -z "$hits" ]] || die "chromadb ($ip) reached squid and was denied for $hits (TCP_DENIED in $log_f): a proxy variable is set in the container after all; see the compose.yml *no-proxy anchor (rule §7.1)"
  fi
  local clog
  clog="$(docker logs --since "@$since" "$cid" 2>&1 | grep -iE 'posthog|telemetry' | head -n 5 | tr '\n' ' ' || true)"
  if [[ -n "$clog" ]]; then
    warn "chromadb log mentions telemetry since compose up (informational; the kernel and squid logs above show no beacon left the container): ${clog:0:300}"
  fi
  log "chromadb ($when): opt-outs present, no proxy in the container, DOCKER-USER LOG rule in place, no dropped (kernel log, 5/min-limited) or denied (squid) outbound packet from $ip since compose up"
}

_mem_chroma_up() {
  local compose="$ATLAS_DAY1_DIR/docker/core/compose.yml"
  [[ -f "$compose" ]] || die "$compose is missing (docker/core/compose.yml is the core services writer's file; it must define service 'chromadb')"
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  grep -qE '^[[:space:]]+chromadb:' "$compose" || die "$compose defines no 'chromadb' service (contract in this file's header)"
  proxy_env
  MEM_CHROMA_SINCE="$(date +%s)"
  log "docker compose up -d chromadb ($compose; --env-file $ATLAS_ETC/core.env when present)"
  retry 3 _core_compose up -d chromadb || die "docker compose up chromadb failed (image pull through the proxy? network created by step 02?)"
  # Endpoint: the published port (CONVENTIONS.md §8 says host port 8000), else the container address on its network.
  local published addr lan
  published="$(_core_compose port chromadb 8000 2>/dev/null | head -n1 || true)"
  if [[ -n "$published" ]]; then
    # ChromaDB 1.x has no authentication (compose.yml header). CONVENTIONS §8 allows loopback, LAN and WireGuard
    # addresses; compose.yml publishes loopback only and asks §8 to say so. Accepted here: loopback, and the LAN address
    # with a warning. A wildcard (0.0.0.0, [::], a bare port) would expose the whole Vector Cortex on every interface and
    # is fatal, never rewritten; any other address is fatal too (fix round 3: the rule is cited as written).
    lan="$(_mem_lan_ip)"
    case "$published" in
      127.0.0.1:*|\[::1\]:*) addr="$published" ;;
      0.0.0.0:*|\[::\]:*|:*|[0-9]*[^.0-9:]*)
        die "chromadb is published on '$published': a wildcard publish; CONVENTIONS §8 allows loopback, LAN and WireGuard only, and ChromaDB 1.x has no authentication (docker/core/compose.yml chromadb.ports should read \"127.0.0.1:8000:8000\")" ;;
      *)
        if [[ -n "$lan" && "$published" == "$lan:"* ]]; then
          addr="$published"
          warn "chromadb is published on the LAN address $published (allowed by CONVENTIONS §8, but ChromaDB 1.x has no authentication: every LAN host can read and write the Vector Cortex; docker/core/compose.yml's own header asks for 127.0.0.1:8000 only)"
        else
          die "chromadb is published on '$published', which is neither loopback nor the LAN address (${lan:-unknown}); CONVENTIONS §8 allows loopback, LAN and WireGuard only (docker/core/compose.yml chromadb.ports should read \"127.0.0.1:8000:8000\")"
        fi ;;
    esac
    CHROMA_URL="http://$addr"
  else
    local cid ip
    cid="$(_core_compose ps -q chromadb 2>/dev/null | head -n1 || true)"
    [[ -n "$cid" ]] || die "chromadb container not found after compose up"
    ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' "$cid" | grep -m1 . || true)"
    [[ -n "$ip" ]] || die "chromadb publishes no port and has no bridge address; cannot reach it from the host"
    CHROMA_URL="http://$ip:8000"
    warn "chromadb publishes no host port; using its container address $CHROMA_URL (CONVENTIONS.md §8 expects host port 8000)"
  fi
  wait_http "$CHROMA_URL/api/v2/heartbeat" 120 || die "ChromaDB heartbeat at $CHROMA_URL/api/v2/heartbeat never answered 200 (docker logs chromadb)"
  log "ChromaDB up at $CHROMA_URL"
  _mem_chroma_telemetry_check "$MEM_CHROMA_SINCE" "after heartbeat"
}
MEM_CHROMA_SINCE=""

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
  # Second observation window (fix round 3): Chroma's product telemetry, where present, fires per collection operation.
  _mem_chroma_telemetry_check "$MEM_CHROMA_SINCE" "after collection operations"
}

# --- venv, LightRAG, Docling ---------------------------------------------------------------------------------------------
VENV=""
_mem_venv() {
  # The orchestrator venv is step 02's (phase2/02-orchestrator.sh: uv-managed CPython 3.12, seeded pip, the atlas
  # package installed, root:atlas read-only). This step never creates one (fix round 3): a host-python venv here would
  # be 3.14 and could not import the orchestrator's pinned tree, so the only honest remedy is step 02.
  VENV="$ATLAS_OPT/venv"
  apt_install libcairo2   # cairosvg from lightrag-hku[api] (services-tools.md §2.2)
  [[ -x "$VENV/bin/python" ]] \
    || die "$VENV is missing: phase2/02-orchestrator.sh creates it (uv-managed CPython 3.12). Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02"
  local pyver
  pyver="$("$VENV/bin/python" --version 2>&1 || true)"
  "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' \
    || die "$VENV/bin/python is '$pyver', not the Python 3.12 step 02 pins (ORCH_PYTHON); the lightrag/docling pins below are resolved for 3.12. Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02"
  "$VENV/bin/python" -m pip --version >/dev/null 2>&1 \
    || die "$VENV has no pip (step 02 seeds it with uv venv --seed). Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02"
  "$VENV/bin/python" -c 'import importlib.metadata as m; m.version("atlas")' 2>/dev/null \
    || die "the atlas package is not installed in $VENV (step 02 installs it; its == pins constrain the installs below). Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02"
  log "venv: $pyver at $VENV (step 02's; atlas $("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("atlas"))'))"
}

# _mem_py_env — the environment every python/pip/tool invocation in this step runs with (header "Telemetry").
_mem_py_env() {
  proxy_env
  ensure_dir "$ATLAS_CACHE_DIR" root:root 755
  ensure_dir "$ATLAS_CACHE_DIR/pip" root:root 755
  export PIP_CACHE_DIR="$ATLAS_CACHE_DIR/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
  export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 ANONYMIZED_TELEMETRY=False
  # huggingface_hub persists a login token at $HF_TOKEN_PATH (default $HF_HOME/token) and its token store at
  # $HF_STORED_TOKENS_PATH (default dirname(HF_TOKEN_PATH)/stored_tokens). Both are pointed at a root-only, non-secret,
  # non-backed-up cache directory (fix round 3): never under /srv/atlas, and never into /etc/atlas/secrets, where a
  # root-run tool calling login() would create files the CONVENTIONS §2 table does not know about.
  ensure_dir "$ATLAS_CACHE_DIR/hf-login" root:root 700
  export HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    HF_TOKEN_PATH="$ATLAS_CACHE_DIR/hf-login/token" HF_STORED_TOKENS_PATH="$ATLAS_CACHE_DIR/hf-login/stored_tokens"
  # HOME is deliberately NOT exported here: the Phase 2 driver runs every step in one process, and steps 05, 06 and 10
  # rely on /root/.docker/config.json (the container-side proxy injection, phase1/06-docker.sh) which docker finds via
  # $HOME. Tools that write ~/.cache (docling-tools, docling) get HOME=$ATLAS_CACHE_DIR per invocation instead, never
  # under $ATLAS_STATE (CONVENTIONS §2).
}

_pip() {
  _mem_py_env
  retry 3 "$VENV/bin/python" -m pip install --quiet "$@"
}

# _pip_check — pip's resolver only WARNS when a new install breaks an installed package's `==` pins; the step must not
# leave the orchestrator on an untested set (fix round 3).
_pip_check() {
  local what="$1" out
  if ! out="$("$VENV/bin/python" -m pip check 2>&1)"; then
    die "$what left conflicting requirements in $VENV (orchestrator pyproject.toml pins): ${out//$'\n'/; } -- resolve before continuing"
  fi
}

# _mem_constraints FILE — the atlas package's own `==` pins as a pip constraints file (extras stripped: constraints
# cannot carry them), so lightrag's ranges can never move fastapi/httpx/pydantic/redis/celery off the tested set.
_mem_constraints() {
  "$VENV/bin/python" - "$1" <<'PY' || die "could not derive pip constraints from the installed atlas package"
import importlib.metadata as m, re, sys
out = []
for req in m.requires("atlas") or []:
    req = req.split(";")[0].strip()
    mt = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?==([^\s,]+)$", req)
    if mt:
        out.append(f"{mt.group(1)}=={mt.group(3)}")
if not out:
    sys.exit("atlas declares no == pins")
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("constraints:", " ".join(out))
PY
}

# --- tiktoken: cl100k_base, vendored copy / inbox / one-time download (header item 4) ------------------------------------
# The exact next commands when no path delivered the table (CONVENTIONS §7.10). `phase2 --force 04`, not phase1's: step
# 04 of Phase 2 is idempotent and resumes in seconds; phase1's --force 04 is the one that ends in a reboot
# (config/allowlist.txt header).
_mem_tiktoken_remedy() {
  printf '%s' "the one-time download from $TIKTOKEN_URL failed through the proxy (is openaipublic.blob.core.windows.net in config/allowlist.txt and the rendered squid list? grep TCP_DENIED /var/log/squid/access.log). Either fix that and re-run, or on any machine with internet access run: curl -fsSL -o $TIKTOKEN_FILE $TIKTOKEN_URL && sha256sum $TIKTOKEN_FILE  (must print $TIKTOKEN_SHA256); copy the file to $ATLAS_SRV/staging/inbox/$TIKTOKEN_FILE on this node (or commit it as scripts/day1/config/tiktoken/$TIKTOKEN_FILE, see config/tiktoken/README.md); then: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 04 (every other part of step 04 is idempotent and resumes in seconds)"
}

# _mem_tiktoken_fetch DEST — the third path: one GET of TIKTOKEN_URL through the allowlist proxy into DEST (a transient
# file under /var/cache/atlas, never $ATLAS_STATE or $ATLAS_SRV), sha256-checked against the pin; returns 1 (and says
# why) instead of dying, because the caller's deferred path is the honest fallback (header item 4).
_mem_tiktoken_fetch() {
  local dest="$1" have rc=0
  proxy_env
  [[ -n "${HTTPS_PROXY:-}" ]] || { warn "tiktoken: HTTPS_PROXY is not exported (proxy_env found no $ATLAS_ETC/proxy.env; rule §7.1); not fetching $TIKTOKEN_URL"; return 1; }
  ensure_dir "$(dirname "$dest")" root:root 755
  log "tiktoken: no vendored or inbox copy; fetching $TIKTOKEN_URL once through the proxy (config/allowlist.txt: LightRAG tokenizer table, one-time)"
  retry 3 curl -fsSL --connect-timeout 30 --max-time 300 -o "$dest.part" "$TIKTOKEN_URL" || rc=$?
  if (( rc != 0 )); then
    rm -f "$dest.part"
    warn "tiktoken: download of $TIKTOKEN_URL failed (curl exit $rc)"
    return 1
  fi
  have="$(sha256sum "$dest.part" | cut -d' ' -f1)"
  if [[ "$have" != "$TIKTOKEN_SHA256" ]]; then
    rm -f "$dest.part"
    warn "tiktoken: downloaded table has sha256 $have, expected $TIKTOKEN_SHA256 (tiktoken's own pin); discarded"
    return 1
  fi
  mv -f "$dest.part" "$dest"
  chmod 644 "$dest"
  log "tiktoken: fetched $TIKTOKEN_FILE (sha256 verified) into $dest"
}

# _mem_tiktoken_source — print the path of a copy whose sha256 matches the pin, repository copy first, inbox second;
# a copy with the wrong hash is reported and skipped (fix round 3: integrity by hash, availability without a new host).
_mem_tiktoken_source() {
  local cand have
  for cand in "$ATLAS_DAY1_DIR/config/tiktoken/$TIKTOKEN_FILE" "$ATLAS_SRV/staging/inbox/$TIKTOKEN_FILE"; do
    [[ -f "$cand" ]] || continue
    have="$(sha256sum "$cand" | cut -d' ' -f1)"
    if [[ "$have" == "$TIKTOKEN_SHA256" ]]; then
      printf '%s\n' "$cand"
      return 0
    fi
    warn "tiktoken: $cand has sha256 $have, expected $TIKTOKEN_SHA256 (tiktoken's own pin); ignoring it"
  done
  return 1
}

MEM_TIKTOKEN_CACHED=0    # set by _mem_lightrag: cl100k_base is in TIKTOKEN_CACHE_DIR (written to memory.env)
_mem_tiktoken_seed() {
  local wd="$1" src key dest
  key="$(python3 -c 'import hashlib, sys; print(hashlib.sha1(sys.argv[1].encode()).hexdigest())' "$TIKTOKEN_URL")"
  dest="$wd/tiktoken/$key"
  if [[ -f "$dest" && "$(sha256sum "$dest" | cut -d' ' -f1)" == "$TIKTOKEN_SHA256" ]]; then
    log "tiktoken: cl100k_base already cached at $dest (sha256 verified)"
  elif src="$(_mem_tiktoken_source)" || { src="$ATLAS_CACHE_DIR/downloads/$TIKTOKEN_FILE"; _mem_tiktoken_fetch "$src"; }; then
    # Search order (header item 4): vendored copy, inbox, then the one-time download (the third path above).
    install -m 640 -o atlas -g atlas "$src" "$dest.tmp"
    mv -f "$dest.tmp" "$dest"
    log "tiktoken: seeded $dest from $src (sha256 $TIKTOKEN_SHA256)"
  else
    [[ -e "$dest" ]] && rm -f "$dest"
    MEM_TIKTOKEN_CACHED=0
    warn "tiktoken cl100k_base NOT cached (deferred: no verified copy at $ATLAS_DAY1_DIR/config/tiktoken/$TIKTOKEN_FILE or $ATLAS_SRV/staging/inbox/$TIKTOKEN_FILE, and the one-time download failed); LightRAG is installed but D7 indexing will refuse to start until it is. Remedy: $(_mem_tiktoken_remedy)"
    return 0
  fi
  # Offline proof: tiktoken must load the table from the cache alone (it re-checks the sha256 itself). The proxy
  # variables point at a closed loopback port, so any fetch attempt fails instantly instead of reaching squid.
  _mem_py_env
  HOME="$ATLAS_CACHE_DIR" TIKTOKEN_CACHE_DIR="$wd/tiktoken" \
    HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 NO_PROXY='' no_proxy='' \
    "$VENV/bin/python" -c 'import tiktoken; e = tiktoken.get_encoding("cl100k_base"); n = len(e.encode("A.T.L.A.S. tiktoken offline check")); assert n > 0, n; print("tiktoken ok:", n, "tokens")' \
    || die "tiktoken could not load cl100k_base from $wd/tiktoken offline (cache name $key, sha256 pin $TIKTOKEN_SHA256): tiktoken 0.14's cache layout changed, or the venv's tiktoken differs; see the traceback above"
  MEM_TIKTOKEN_CACHED=1
}

_mem_preflight() {
  # Everything this step needs from other writers' files, checked before the first download.
  [[ -f /etc/sudoers.d/atlas-engines ]] || die "/etc/sudoers.d/atlas-engines not installed (step 01)"
  [[ -f "$ATLAS_DAY1_DIR/docker/core/compose.yml" ]] || die "$ATLAS_DAY1_DIR/docker/core/compose.yml missing (core services writer; service chromadb)"
  [[ -f "$ATLAS_DAY1_DIR/docker/core/compose.voice.yml" ]] || die "$ATLAS_DAY1_DIR/docker/core/compose.voice.yml missing (voice writer's overlay; it defines the docling-serve service this step starts)"
  [[ -x "$ATLAS_OPT/venv/bin/python" ]] || die "$ATLAS_OPT/venv missing: step 02 (phase2/02-orchestrator.sh) must have run; the pulls below would otherwise be wasted before the venv check. Run: sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 02"
  if _mem_tiktoken_source >/dev/null; then
    log "tiktoken: a verified copy of $TIKTOKEN_FILE is available; cl100k_base will be seeded offline in this step"
  else
    log "tiktoken: no vendored or inbox copy of $TIKTOKEN_FILE (config/tiktoken/README.md); the step will fetch it once from $TIKTOKEN_URL through the proxy, and defer the LightRAG warm-up only if that fails too"
  fi
}

_mem_lightrag() {
  local wd="$ATLAS_SRV/data/graph"
  ensure_dir "$wd" atlas:atlas 750
  ensure_dir "$wd/tiktoken" atlas:atlas 750
  if ! "$VENV/bin/python" -c 'import importlib.metadata as m; v=m.version("lightrag-hku"); assert v=="1.5.7", v' 2>/dev/null; then
    local cons
    cons="$(mktemp)"
    _mem_constraints "$cons"
    log "pip install $LIGHTRAG_PIN $CHROMA_CLIENT_PIN into $VENV ($("$VENV/bin/python" --version 2>&1)), constrained to the atlas package's == pins; a resolver conflict stops here"
    _pip -c "$cons" "$LIGHTRAG_PIN" "$CHROMA_CLIENT_PIN" \
      || { rm -f "$cons"; die "pip install of $LIGHTRAG_PIN failed in $VENV under the orchestrator's pins (see above; constraints were: $(tr '\n' ' ' <"$cons" 2>/dev/null))"; }
    rm -f "$cons"
    _pip_check "the $LIGHTRAG_PIN install"
  fi
  "$VENV/bin/python" -c 'import lightrag, importlib.metadata as m; print("lightrag", m.version("lightrag-hku"))' \
    || die "lightrag does not import from $VENV"
  # tiktoken's cl100k_base (orchestrator/src/atlas/memory.py uses tiktoken_model_name "gpt-4" = cl100k_base and refuses
  # to index without the cached table): seeded offline from a verified copy, never fetched (header item 4).
  _mem_tiktoken_seed "$wd"
  chown -R atlas:atlas "$wd"
  log "LightRAG 1.5.7 installed; working dir $wd (JsonKV/NanoVectorDB/NetworkX defaults, Section 10.2 D7); tiktoken cached=$MEM_TIKTOKEN_CACHED"
}

# _mem_pdf_write PATH — a minimal valid one-page PDF with one Helvetica text object (offsets computed, so the xref is
# exact). Converting it needs docling's layout model (and the OCR model for the page bitmap), which an HTML file never
# touched (fix round 3): this is what makes the offline proof a proof.
_mem_pdf_write() {
  python3 - "$1" <<'PY'
import sys
text = b"BT /F1 24 Tf 72 700 Td (ATLAS Docling offline check) Tj ET\n"
objs = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    b"<< /Length %d >>\nstream\n" % len(text) + text + b"endstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
offsets = []
for i, body in enumerate(objs, start=1):
    offsets.append(len(out))
    out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
xref = len(out)
out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
for off in offsets:
    out += b"%010d 00000 n \n" % off
out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
with open(sys.argv[1], "wb") as fh:
    fh.write(out)
PY
}

_mem_docling() {
  local hf_home="$ATLAS_SRV/engines/hf" artifacts="$ATLAS_SRV/engines/docling"
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$hf_home" atlas:atlas 755
  ensure_dir "$artifacts" atlas:atlas 755
  if ! "$VENV/bin/python" -c 'import importlib.metadata as m; v=m.version("docling"); assert v=="2.129.0", v' 2>/dev/null; then
    # torch AND torchvision from the CPU index in ONE resolve with exact +cpu pins (fix round 3): docling-slim[standard]
    # requires torchvision, whose PyPI wheel is the CUDA build pinning an exact torch; installed alone, torch +cpu would
    # be replaced by PyPI's CUDA torch plus the nvidia-* wheels, or left beside a torchvision whose _C cannot load.
    # --extra-index-url (not --index-url) so pure-python dependencies may come from PyPI too; a bare `torch` is never
    # requested, so PyPI's CUDA build is not a candidate for either pin.
    log "pip install $TORCH_CPU_PIN $TORCHVISION_CPU_PIN from $TORCH_CPU_INDEX (CPU only), then $DOCLING_PIN (several minutes)"
    _pip --extra-index-url "$TORCH_CPU_INDEX" "$TORCH_CPU_PIN" "$TORCHVISION_CPU_PIN" \
      || die "pip install of $TORCH_CPU_PIN $TORCHVISION_CPU_PIN failed in $VENV ($("$VENV/bin/python" --version 2>&1)): no such wheel on $TORCH_CPU_INDEX? (header: the +cpu wheels are UNVERIFIED; pick the newest torch/torchvision pair the index lists for cp312 inside docling's range and update TORCH_CPU_PIN/TORCHVISION_CPU_PIN)"
    "$VENV/bin/python" -c 'import sys, torch, torchvision; bad=[n+" "+v for n, v in (("torch", torch.__version__), ("torchvision", torchvision.__version__)) if not v.endswith("+cpu")]; sys.exit("not +cpu builds: " + ", ".join(bad) if bad else 0)' \
      || die "torch/torchvision in $VENV are not +cpu builds (see above); refusing to install docling on top of them"
    _pip "$DOCLING_PIN" \
      || die "pip install of $DOCLING_PIN failed in $VENV ($("$VENV/bin/python" --version 2>&1)): see above (services-tools.md §2.3: the docling-serve container keeps serving Open WebUI, the in-process library is what the orchestrator imports)"
    "$VENV/bin/python" -c 'import sys, torch, torchvision; bad=[n+" "+v for n, v in (("torch", torch.__version__), ("torchvision", torchvision.__version__)) if not v.endswith("+cpu")]; sys.exit("not +cpu builds: " + ", ".join(bad) if bad else 0)' \
      || die "docling's resolver replaced the CPU torch/torchvision with non-CPU builds (see above); the pins $TORCH_CPU_PIN/$TORCHVISION_CPU_PIN no longer satisfy docling 2.129.0's range"
    _pip_check "the $DOCLING_PIN install"
  fi
  # Prefetch the default model set once (layout tableformer code_formula picture_classifier rapidocr, VERIFIED
  # docling/cli/models.py) so HF_HUB_OFFLINE=1 can be set afterwards (Section 12.5, rule §7.1). The marker is written
  # only when the download produced model directories (fix round 3: an exit 0 with an empty tree is not a prefetch).
  if [[ ! -f "$artifacts/.atlas-prefetched" ]]; then
    _mem_py_env
    HOME="$ATLAS_CACHE_DIR" HF_HOME="$hf_home" "$VENV/bin/docling-tools" models download -o "$artifacts" \
      || die "docling-tools models download failed (huggingface.co through the proxy; re-run to resume)"
    compgen -G "$artifacts/*/" >/dev/null \
      || die "docling-tools models download exited 0 but $artifacts holds no model directory; docling 2.129.0's artifacts layout differs from what this step expects (docling/cli/models.py)"
    date -Is >"$artifacts/.atlas-prefetched"
  fi
  # CONVENTIONS §2: no secret under /srv/atlas. huggingface_hub would persist a login token at $HF_HOME/token and its
  # token store at $HF_HOME/stored_tokens (both redirected to /var/cache/atlas/hf-login by _mem_py_env).
  local f
  for f in "$hf_home/token" "$hf_home/stored_tokens"; do
    [[ ! -e "$f" ]] || die "$f exists: a Hugging Face login token was persisted under /srv/atlas (CONVENTIONS §2); shred it (shred -u $f) and find which tool called login()"
  done
  # Offline proof: convert a generated one-page PDF with the network pointed at a closed port and the artifacts path
  # set; the layout (and OCR) models must load from $artifacts, so an empty or misplaced prefetch fails HERE.
  local tmp
  tmp="$(mktemp -d)"
  _mem_pdf_write "$tmp/check.pdf"
  _mem_py_env
  HOME="$ATLAS_CACHE_DIR" HF_HUB_OFFLINE=1 HF_HOME="$hf_home" DOCLING_ARTIFACTS_PATH="$artifacts" \
    HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9 https_proxy=http://127.0.0.1:9 NO_PROXY='' no_proxy='' \
    "$VENV/bin/python" - "$tmp/check.pdf" <<'PY' \
    || { rm -rf "$tmp"; die "docling could not convert a one-page PDF offline from $VENV with DOCLING_ARTIFACTS_PATH=$artifacts (traceback above: a model missing from the prefetch, or torch/torchvision not loading)"; }
import sys
from docling.document_converter import DocumentConverter
doc = DocumentConverter().convert(sys.argv[1]).document
md = doc.export_to_markdown()
assert "ATLAS" in md and "offline check" in md, md
print("docling ok (pdf, layout model offline):", md.replace("\n", " ")[:80])
PY
  rm -rf "$tmp"
  chown -R atlas:atlas "$hf_home" "$artifacts"
  log "Docling 2.129.0 installed; models under $artifacts, HF cache $hf_home (HF_HUB_OFFLINE=1 from now on)"
}

# _mem_docling_serve_up — the docling-serve CONTAINER (Section 17 step 4's "Docling ingestion service"), started from
# here with the merge command docker/core/compose.voice.yml's header gives; step 5's `up -d` is then a no-op for it.
_mem_docling_serve_up() {
  local core="$ATLAS_DAY1_DIR/docker/core/compose.yml" voice="$ATLAS_DAY1_DIR/docker/core/compose.voice.yml"
  grep -qE '^[[:space:]]+docling:' "$voice" || die "$voice defines no 'docling' service (contract in this file's header)"
  # Interpolation (compose reads the whole merged file, including kokoro's ${LAN_IP:?}): core.env (step 02) or Phase 1's
  # docker.env; compose.voice.yml gives every voice.env key a safe default, so voice.env is not needed here.
  local envf=()
  if [[ -f "$ATLAS_ETC/core.env" ]]; then envf=(--env-file "$ATLAS_ETC/core.env")
  elif [[ -f "$ATLAS_ETC/docker.env" ]]; then envf=(--env-file "$ATLAS_ETC/docker.env")
  else die "neither $ATLAS_ETC/core.env (step 02) nor $ATLAS_ETC/docker.env (Phase 1 step 6) exists; compose.voice.yml needs LAN_IP from one of them"; fi
  proxy_env
  log "docker compose up -d docling ($voice merged into $core; image pull through the proxy)"
  retry 3 docker compose -f "$core" -f "$voice" "${envf[@]}" up -d --quiet-pull docling \
    || die "docker compose up docling failed (ghcr.io allowlisted? $(docker compose -f "$core" -f "$voice" "${envf[@]}" logs --tail 20 docling 2>&1 | tail -n 20 | tr '\n' ' '))"
  local published
  published="$(docker compose -f "$core" -f "$voice" "${envf[@]}" port docling 5001 2>/dev/null | head -n1 || true)"
  case "$published" in
    127.0.0.1:*|\[::1\]:*|"") ;;
    *) die "docling-serve is published on '$published'; compose.voice.yml's contract is loopback only (an unauthenticated conversion API with no LAN consumer, Section 12.5)" ;;
  esac
  wait_http "$DOCLING_SERVE_URL/docs" 300 || die "docling-serve did not answer 200 on $DOCLING_SERVE_URL/docs within 300 s: docker logs atlas-docling"
  log "docling-serve up at $DOCLING_SERVE_URL (Open WebUI: DOCLING_SERVER_URL=$DOCLING_SERVE_URL)"
}

_mem_write_env() {
  local f="$ATLAS_ETC/memory.env" router_url embed_url
  router_url="http://127.0.0.1:$(engine_port router-qwen3.5-4b)/v1"
  embed_url="http://127.0.0.1:$(engine_port embed-bge-m3)/v1"
  [[ -e "$f" ]] || { : >"$f"; }
  ensure_kv "$f" CHROMA_URL "$CHROMA_URL"
  ensure_kv "$f" CHROMA_COLLECTIONS "\"${ATLAS_CHROMA_COLLECTIONS[*]}\""
  ensure_kv "$f" ROUTER_URL "$router_url"
  ensure_kv "$f" EMBEDDING_URL "$embed_url"
  ensure_kv "$f" RERANK_URL "http://127.0.0.1:$(engine_port rerank-bge-v2-m3)/v1"
  ensure_kv "$f" EMBEDDING_MODEL embed-bge-m3
  ensure_kv "$f" EMBEDDING_DIM "$(ej embed-bge-m3 embedding_dim)"
  ensure_kv "$f" LIGHTRAG_WORKING_DIR "$ATLAS_SRV/data/graph"
  ensure_kv "$f" TIKTOKEN_CACHE_DIR "$ATLAS_SRV/data/graph/tiktoken"
  ensure_kv "$f" LIGHTRAG_TIKTOKEN_CACHED "$MEM_TIKTOKEN_CACHED"   # header contract: 0 = seed deferred (no verified copy)
  ensure_kv "$f" DOCLING_ARTIFACTS_PATH "$ATLAS_SRV/engines/docling"
  ensure_kv "$f" DOCLING_SERVER_URL "$DOCLING_SERVE_URL"
  ensure_kv "$f" HF_HOME "$ATLAS_SRV/engines/hf"
  ensure_kv "$f" HF_HUB_OFFLINE 1
  # Rule §7.1 (no telemetry) and CONVENTIONS §2 (no token under /srv/atlas or in secrets/) at run time, whichever env
  # file loads first.
  ensure_kv "$f" HF_HUB_DISABLE_TELEMETRY 1
  ensure_kv "$f" DO_NOT_TRACK 1
  ensure_kv "$f" ANONYMIZED_TELEMETRY False
  ensure_kv "$f" HF_HUB_DISABLE_IMPLICIT_TOKEN 1
  ensure_kv "$f" HF_TOKEN_PATH "$ATLAS_CACHE_DIR/hf-login/token"
  ensure_kv "$f" HF_STORED_TOKENS_PATH "$ATLAS_CACHE_DIR/hf-login/stored_tokens"
  # Rule §7.1 at run time (fix round 3): LightRAG 1.5.7's default llm_model_func/embedding_func are OpenAI cloud calls
  # and the installed `openai` SDK resolves api.openai.com unless OPENAI_BASE_URL is set. Any defaulted client in the
  # orchestrator or the lightrag-hku[api] server therefore resolves to loopback and fails with a clear local error
  # instead of a squid TCP_DENIED. LLM_BINDING*/EMBEDDING_BINDING* are LightRAG's own server knobs (its env.example).
  ensure_kv "$f" OPENAI_BASE_URL "$router_url"
  ensure_kv "$f" OPENAI_API_KEY atlas-local-no-cloud
  ensure_kv "$f" LLM_BINDING openai
  ensure_kv "$f" LLM_BINDING_HOST "$router_url"
  ensure_kv "$f" LLM_MODEL router-qwen3.5-4b
  ensure_kv "$f" EMBEDDING_BINDING openai
  ensure_kv "$f" EMBEDDING_BINDING_HOST "$embed_url"
  chown root:atlas "$f"
  chmod 640 "$f"
  # Section 16.3 item 6 ("modify its own code") enforced by the OS, not only by behaviour: the interpreter and
  # site-packages the orchestrator runs are root:atlas, group-readable, not writable by atlas (pip runs as root here and
  # in step 02/10). Runtime-writable state stays atlas-owned: LIGHTRAG_WORKING_DIR, HF_HOME, DOCLING_ARTIFACTS_PATH.
  # Step 02 applies the same ownership to /opt/atlas/orchestrator and the venv (its header, items 16-18), so the two
  # steps agree; CONVENTIONS §2's row "/opt/atlas/orchestrator/ + /opt/atlas/venv/ ... atlas" is the one to amend to
  # "root:atlas, read-only for atlas" (requested in the notes returned with this file; not silently deviated from).
  chown -R root:atlas "$VENV"
  chmod -R go-w "$VENV"
  log "wrote $f; $VENV is root:atlas and not writable by atlas"
}

step_04() {
  local key
  [[ -x /usr/local/bin/llama-server ]] || die "llama-server not installed: step 01 must run first (Section 17 order)"
  _mem_preflight
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
  _mem_docling_serve_up
  _mem_write_env
  # What the Principal must know, printed in the phase output (CONVENTIONS §7.10), not only in this file's header:
  warn "ACCEPTED RISK (Day 1): atlas is in the docker group (root-equivalent on this host); Section 16.3 items 5/6/8 are enforced by the orchestrator's fixed docker run line (V17), not by the OS. Follow-up: /usr/local/sbin/atlas-sandbox-run wrapper and removal of atlas from the group (header SECURITY NOTE; README 'Accepted risks' should list it)."
  if (( MEM_TIKTOKEN_CACHED == 0 )); then
    warn "DEFERRED in step 04: tiktoken cl100k_base is not cached, so LightRAG (D7) cannot index yet. Remedy: $(_mem_tiktoken_remedy)"
    notify "Phase 2 step 4 done with ONE deferred item: tiktoken table not cached (no verified $TIKTOKEN_FILE in the inbox); see the phase log"
  else
    notify "Phase 2 step 4 done: resident models, ChromaDB, LightRAG, Docling (library and docling-serve)"
  fi
  log "step 04 done"
}
