#!/usr/bin/env bash
# phase2/04-memory.sh — Section 17 Phase 2 step 4: ChromaDB, LightRAG (D7), Docling, and the three resident small
# models of D4 (Section 5.3, 10.1, 10.2, 15.1). Sourced by phase2-services.sh through run_phase_steps; defines step_04.
#
# Order inside the step (each part idempotent, so a re-run after a failure resumes cheaply):
#   1. Pull router-qwen3.5-4b, embed-bge-m3, rerank-bge-v2-m3 with hf_download, hashes resolved from the HF tree API at
#      pull time and recorded in $ATLAS_SRV/models/<key>/MANIFEST.json (gguf-models.md §0/§11.1, research conflict a).
#   2. Re-render $ATLAS_ETC/engines/*.env (model files now present), enable the three llama-server@ units and start
#      them THROUGH THE ORCHESTRATOR'S CONTROL PATH (svc_user_run -> sudo systemctl start), which proves
#      /etc/sudoers.d/atlas-engines under sudo-rs in both directions: the exact command is granted and the same
#      command with an extra argument is REFUSED. Primary proof: `sudo -n -l -- CMD` (exit code decides, the text only
#      detects an unsupported flag); fallback when -l COMMAND is unsupported: the refused command is actually attempted
#      as atlas and must not be granted (CONVENTIONS.md §8 "nothing else"); then /health and one real request per model.
#      The router's chat completion is recorded as `V10 info` (the Section 21 V10 half placed at the Phase 2 gate;
#      phase2/10-gate.sh records the same id and result, the Phase 3 gate records V10 proper).
#   3. `docker compose ... up -d chromadb` from docker/core/compose.yml, heartbeat, the six Section 10.1 collections
#      through the HTTP API. The published address MUST be loopback (CONVENTIONS §8); anything else is fatal, never
#      rewritten. Telemetry (rule §7.1): the container's env must carry the two opt-outs and a blank proxy, and no
#      direct beacon may have hit the DOCKER-USER drop (kernel log) or squid since compose up.
#   4. LightRAG 1.5.7 into the orchestrator venv, working dir $ATLAS_SRV/data/graph. tiktoken's cl100k_base table
#      (one public BPE file, no Principal data) is cached once from openaipublic.blob.core.windows.net WHEN
#      config/allowlist.txt names that host exactly; when it does not, the step completes, warns with the exact line and
#      the exact non-disruptive reload command (phase1-platform.sh --reload-allowlist, never --force 04), records
#      LIGHTRAG_TIKTOKEN_CACHED=0 in memory.env and repeats the warning at the end of the step (fix round 2: a fresh
#      Phase 2 must be able to run unattended, Section 17; the orchestrator's graph layer refuses to index until the
#      table is cached, with the same seed command, so nothing fails silently).
#   5. Docling 2.129.0 into the same venv (CPU torch from download.pytorch.org first, then docling against it), models
#      prefetched once under HF_HOME=$ATLAS_SRV/engines/hf. This step delivers the IN-PROCESS library and its offline
#      proof only; the docling-serve CONTAINER (atlas-docling, 127.0.0.1:5001) lives in docker/core/compose.voice.yml
#      and is started by step 5 with Kokoro and speaches (that file's header says so). Section 17 names the "Docling
#      ingestion service" under step 4 and CONVENTIONS §1 places docling-serve in docker/core/compose.yml; the split is
#      the voice writer's and this header records it so no one looks for the container after step 4.
#   6. $ATLAS_ETC/memory.env written for the orchestrator (contract below); the venv made root:atlas, not writable
#      by atlas (Section 16.3 item 6: the orchestrator must not be able to modify its own code; step 02 does the same
#      to /opt/atlas/orchestrator and the venv, so the two steps agree; CONVENTIONS §2's "atlas" owner row for
#      /opt/atlas/venv should be amended to "root:atlas, read-only for atlas").
#
# Every python/tool invocation that may write ~/.cache runs with HOME=/var/cache/atlas on that command line only; HOME is
# never exported, because steps 05-10 run in the same driver process and docker finds /root/.docker/config.json via $HOME.
#
# Telemetry (rule §7.1): every python invocation below runs with HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
# ANONYMIZED_TELEMETRY=False (huggingface_hub's send_telemetry, docling/posthog) and HF_HUB_DISABLE_IMPLICIT_TOKEN=1
# HF_TOKEN_PATH=<root-only path> so huggingface_hub can never persist a login token under /srv/atlas (CONVENTIONS §2).
# lib/common.sh proxy_env would be the natural single place for the three telemetry variables (every outbound call
# site calls it); that file is another writer's, so this step exports them itself (_mem_py_env).
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
#     $ATLAS_SRV/data/chroma) and, per CONVENTIONS.md §8, publishes it on host port 8000 bound to loopback/LAN/WG. This
#     step only runs `docker compose -f <that file> up -d chromadb` and then finds the endpoint with
#     `docker compose port chromadb 8000`, falling back to the container's bridge address.
#   * /opt/atlas/venv ($ATLAS_OPT/venv) is created by step 02 (orchestrator); when absent it is created here with
#     `python3 -m venv` and the fact is logged (the task text allows this).
#   * phase1/docker-egress-rules.sh (Phase 1 step 6) logs every dropped container packet to the kernel log with the
#     prefix "[ATLAS docker egress denied] " (rate-limited 5/min) before the terminal DROP; _mem_chroma_telemetry_check
#     reads that prefix through journalctl -k. If the prefix changes, the check reports "no dropped packet" for the
#     wrong reason, so the prefix is part of the contract.
#   * config/allowlist.txt (phase1/04-system.sh's writer) carries huggingface.co and .hf.co (the LFS/CAS hosts that
#     resolve redirects land on *.hf.co), pypi.org, files.pythonhosted.org and download.pytorch.org (CPU torch wheels).
#     REQUESTED of the allowlist writer (fix round 2): the line `openaipublic.blob.core.windows.net` under the
#     "# --- Build-time tool downloads" group with the comment "tiktoken cl100k_base, once, for LightRAG (D7); cached in
#     /srv/atlas/data/graph/tiktoken". Section 12.5 already admits package mirrors during builds, and a one-time public
#     BPE table is that category (download.pytorch.org, gradle and the Playwright CDN were added on the same basis).
#     Until it lands, _mem_preflight warns and the LightRAG sub-step defers the warm-up (header item 4). The exact host
#     only: a `.blob.core.windows.net` wildcard would admit every Azure Blob account and is NOT accepted.
#   * lib/common.sh hf_download: as of this round it still puts the bearer token on curl's argv (`-H "Authorization:
#     Bearer $HF_TOKEN"`), readable by every local account in /proc/<pid>/cmdline for the whole transfer. REQUESTED of
#     that writer: read the header from stdin (`printf ... | curl -H @- ...`, curl >= 7.55) as hf_tree_lfs below does.
#     This file's pull path does not depend on the fix: none of the ten engines.json repos is gated, so
#     pull_engine_files calls hf_download through hf_download_public, which keeps the token out of the call entirely.
# Contract this file defines for others:
#   * $ATLAS_ETC/memory.env (root:atlas 640): CHROMA_URL, CHROMA_COLLECTIONS, ROUTER_URL, EMBEDDING_URL, RERANK_URL,
#     EMBEDDING_DIM, LIGHTRAG_WORKING_DIR, TIKTOKEN_CACHE_DIR, LIGHTRAG_TIKTOKEN_CACHED (1 when cl100k_base is in
#     TIKTOKEN_CACHE_DIR, 0 when the warm-up was deferred for lack of the allowlist line), DOCLING_ARTIFACTS_PATH,
#     HF_HOME, HF_HUB_OFFLINE.
#   * hf_tree_lfs / pull_engine_files below are the reference implementation of the manifest-at-pull-time rule; the
#     Phase 3 driver may `source` this file (it only defines functions) and call pull_engine_files <key> for the seven
#     large engines (it handles enumerate_pattern, join_into and the research-snippet hash comparison).

ATLAS_RESIDENT_KEYS=(router-qwen3.5-4b embed-bge-m3 rerank-bge-v2-m3)
ATLAS_CHROMA_COLLECTIONS=(corporate estate scars documents_corporate documents_estate sentinel)   # Section 10.1
TIKTOKEN_HOST="openaipublic.blob.core.windows.net"   # tiktoken_ext/openai_public.py cl100k_base blob (VERIFIED URL host)
ATLAS_CACHE_DIR="/var/cache/atlas"                   # build caches (pip, tool HOME); never under $ATLAS_STATE (CONVENTIONS §2)
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
  local HF_TOKEN="${HF_TOKEN:-}"
  if [[ -z "$HF_TOKEN" && -f "$ATLAS_ETC/secrets/hf-token.env" ]]; then
    # shellcheck disable=SC1091  # secret file, HF_TOKEN=... (CONVENTIONS.md §2)
    source "$ATLAS_ETC/secrets/hf-token.env"
  fi
  local url="${HF_ENDPOINT:-https://huggingface.co}/api/models/$repo/tree/main${sub:+/$sub}"
  local body code
  body="$(mktemp)"
  # The bearer header travels on curl's STDIN (-H @-, curl >= 7.55): never on argv, where every local user could read
  # it from /proc/<pid>/cmdline for the duration of the request, and never in a temp file that a SIGINT or a die
  # between mktemp and rm could leave behind (fix round 2, §7.2).
  if [[ -n "$HF_TOKEN" ]]; then
    code="$(printf 'Authorization: Bearer %s\n' "$HF_TOKEN" \
            | curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" -H @- "$url" || true)"
  else
    code="$(curl -sS -L --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 120 -w '%{http_code}' -o "$body" "$url" || true)"
  fi
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
  # (phase2/01-llama.sh). Primary proof: `sudo -n -l -- CMD ARGS` asks the policy whether the command is permitted
  # WITHOUT executing it (sudo(8): exit 0 and the path when allowed, exit 1 otherwise). The EXIT CODE is the verdict:
  # sudo 1.9's list mode is SILENT for a refused command (reproduced in the fix round: exit 1, empty output), so no
  # refusal text is required; the text is read only to detect a usage error, i.e. an implementation without
  # `-l COMMAND` (sudo-rs, UNVERIFIED). Fallback in that case (fix round 2, implementation-independent): ATTEMPT the
  # command that must be refused, `systemctl start llama-server@router-qwen3.5-4b --no-block`, as atlas through sudo.
  # Granted, it exits 0 (and starts the router, which this step starts right after anyway): a SECURITY failure.
  # Refused, sudo exits 1 before systemctl runs. Either way nothing harmful happens. The other two probes (a second
  # unit, a verb outside the set) are checked only on the primary path, where they cost nothing.
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
    out="$(svc_user_run sudo -n -l -- "$sc" $probe 2>&1)" || rc=$?
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
    if ! out="$(svc_user_run sudo -n -l -- "$sc" start llama-server@router-qwen3.5-4b 2>&1)"; then
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
  # Router: one chat completion, non-empty reply. Section 21 places this V10 half at the Phase 2 gate; it is recorded as
  # `V10 info` (V10a is not an id CONVENTIONS §4 declares and tools/fill-workbook.py has no row for it), exactly as
  # phase2/10-gate.sh records it; the Phase 3 gate's V10 record supersedes both as the latest result per id (fix round 2).
  local vpath="$ATLAS_DAY1_DIR/verify/v10a-router-resident.sh" vout vrc=0
  [[ -f "$vpath" ]] || die "$vpath does not exist"
  vout="$(timeout --foreground 660 bash "$vpath" 2>/dev/null)" || vrc=$?
  vout="$(printf '%s' "$vout" | tr '\n' ' ' | sed -e 's/[[:space:]]\+/ /g' -e 's/^ //' -e 's/ $//')"
  local verdict
  case "$vrc" in 0) verdict=pass ;; 2) verdict=deferred ;; 3) verdict=info ;; *) verdict="fail (exit $vrc)" ;; esac
  record_v V10 info "Phase 2 half (resident router, step 04): $verdict: ${vout:-(no output)}"
  (( vrc == 0 )) || die "the resident router model did not answer a chat completion ($verdict: ${vout:-(no output)}); see verify.jsonl V10 and journalctl -u llama-server@router-qwen3.5-4b"

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

_mem_chroma_telemetry_check() {
  # Rule §7.1: telemetry is left DISABLED, not merely blocked. docker/core/compose.yml (cross-writer) gives chromadb the
  # two opt-outs (ANONYMIZED_TELEMETRY=false, CHROMA_TELEMETRY_ENABLED=false; both variable names UNVERIFIED for the Rust
  # server) and the *no-proxy anchor (HTTP(S)_PROXY empty, NO_PROXY=*), so a posthog beacon, if the opt-outs are not
  # honoured, goes DIRECT from the container, is dropped by the DOCKER-USER rules (phase1/docker-egress-rules.sh: a LOG
  # rule "[ATLAS docker egress denied] " at 5/min precedes the terminal DROP) and never reaches squid. The observation
  # therefore reads (1) the container's environment, (2) the kernel log for a denied packet from the container's address
  # since compose up and (3) squid's log as well, in case the proxy variables ever come back. Any beacon = failure.
  local cid ip since="$1" log_f=/var/log/squid/access.log hits env_dump
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
  ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{"\n"}}{{end}}' "$cid" | grep -m1 . || true)"
  [[ -n "$ip" ]] || die "chromadb has no bridge address; cannot observe its egress"
  sleep 5   # the Rust server's start-up beacon, if any, fires within seconds of the heartbeat answering
  hits="$(journalctl -k -S "@$since" --no-pager -o cat 2>/dev/null | grep -F 'ATLAS docker egress denied' | grep -F "SRC=$ip " \
          | sed -nE 's/.*DST=([0-9a-f.:]+).*DPT=([0-9]+).*/\1:\2/p' | sort -u | tr '\n' ' ' || true)"
  if [[ -n "$hits" ]]; then
    die "chromadb ($ip) tried to reach $hits directly and was dropped by DOCKER-USER (kernel log since compose up): the telemetry opt-outs are not honoured by chromadb/chroma:1.5.9 (UNVERIFIED variable names in docker/core/compose.yml); find the switch the Rust server reads, or pin an image that has none (rule §7.1)"
  fi
  if [[ -r "$log_f" ]]; then
    hits="$(awk -v since="$since" -v ip="$ip" '$1 >= since && $3 == ip && $4 ~ /TCP_DENIED/ {print $7}' "$log_f" | sort -u | tr '\n' ' ' || true)"
    [[ -z "$hits" ]] || die "chromadb ($ip) reached squid and was denied for $hits (TCP_DENIED in $log_f): a proxy variable is set in the container after all; see the compose.yml *no-proxy anchor (rule §7.1)"
  fi
  log "chromadb: opt-outs present, no proxy in the container, no dropped or denied outbound packet from $ip since compose up"
}

_mem_chroma_up() {
  local compose="$ATLAS_DAY1_DIR/docker/core/compose.yml"
  [[ -f "$compose" ]] || die "$compose is missing (docker/core/compose.yml is the core services writer's file; it must define service 'chromadb')"
  command -v docker >/dev/null || die "docker is not installed (Phase 1 step 6)"
  grep -qE '^[[:space:]]+chromadb:' "$compose" || die "$compose defines no 'chromadb' service (contract in this file's header)"
  proxy_env
  local since
  since="$(date +%s)"
  log "docker compose up -d chromadb ($compose; --env-file $ATLAS_ETC/core.env when present)"
  retry 3 _core_compose up -d chromadb || die "docker compose up chromadb failed (image pull through the proxy? network created by step 02?)"
  # Endpoint: the published port (CONVENTIONS.md §8 says host port 8000), else the container address on its network.
  local published addr
  published="$(_core_compose port chromadb 8000 2>/dev/null | head -n1 || true)"
  if [[ -n "$published" ]]; then
    # ChromaDB 1.x has no authentication (compose.yml header): the publish MUST be loopback (CONVENTIONS §8). A wildcard
    # (0.0.0.0, [::]) would expose the whole Vector Cortex on every interface, so it is fatal, never rewritten (fix round 2).
    case "$published" in
      127.0.0.1:*|\[::1\]:*) addr="$published" ;;
      *) die "chromadb is published on $published; CONVENTIONS.md §8 requires 127.0.0.1 (docker/core/compose.yml chromadb.ports must read \"127.0.0.1:8000:8000\")" ;;
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
  _mem_chroma_telemetry_check "$since"
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

# _mem_py_env — the environment every python/pip/tool invocation in this step runs with (header "Telemetry").
_mem_py_env() {
  proxy_env
  ensure_dir "$ATLAS_CACHE_DIR" root:root 755
  ensure_dir "$ATLAS_CACHE_DIR/pip" root:root 755
  export PIP_CACHE_DIR="$ATLAS_CACHE_DIR/pip" PIP_DISABLE_PIP_VERSION_CHECK=1
  export HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 ANONYMIZED_TELEMETRY=False
  # huggingface_hub persists a login token at $HF_TOKEN_PATH (default $HF_HOME/token); point it into the root-only
  # secrets directory (no such file: nothing is read, and atlas cannot create it), never under /srv/atlas.
  export HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_TOKEN_PATH="$ATLAS_ETC/secrets/hf-token"
  # HOME is deliberately NOT exported here: the Phase 2 driver runs every step in one process, and steps 05, 06 and 10
  # rely on /root/.docker/config.json (the container-side proxy injection, phase1/06-docker.sh) which docker finds via
  # $HOME. Tools that write ~/.cache (docling-tools, docling) get HOME=$ATLAS_CACHE_DIR per invocation instead, never
  # under $ATLAS_STATE (CONVENTIONS §2).
}

_pip() {
  _mem_py_env
  retry 3 "$VENV/bin/python" -m pip install --quiet "$@"
}

# The exact next commands when the tiktoken host is missing from the allowlist (CONVENTIONS §7.10). --reload-allowlist
# copies the repository file over /opt/atlas/day1/config/allowlist.txt, re-renders squid and reloads it: no ufw reset,
# no dist-upgrade, no reboot (config/allowlist.txt header; `--force 04` is NOT the way, it ends in a reboot).
_mem_tiktoken_remedy() {
  printf '%s' "add the line '$TIKTOKEN_HOST' under '# --- Build-time tool downloads' in the repository copy of scripts/day1/config/allowlist.txt (comment: tiktoken cl100k_base, once, for LightRAG (D7); cached in $ATLAS_SRV/data/graph/tiktoken), then: sudo /opt/atlas/day1/phase1-platform.sh --reload-allowlist /path/to/repo/scripts/day1/config/allowlist.txt && sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 --force 04 (every other part of step 04 is idempotent and resumes in seconds)"
}

MEM_TIKTOKEN_ALLOWED=0   # set by _mem_preflight: the host is in config/allowlist.txt
MEM_TIKTOKEN_CACHED=0    # set by _mem_lightrag: cl100k_base is in TIKTOKEN_CACHE_DIR (written to memory.env)
_mem_preflight() {
  # Everything this step needs from other writers' files, checked before the first download. The tiktoken host is a
  # DECISION, not a stop (fix round 2): the repository allowlist does not carry it yet, and a fresh Phase 2 must run
  # unattended (Section 17, CONVENTIONS §7.6). When it is absent the LightRAG sub-step defers the one-time warm-up and
  # the step ends with the exact remedy; when it is present the warm-up runs and a failure is fatal. Only the exact
  # host counts: a `.blob.core.windows.net` wildcard would admit every Azure Blob account in the world, against
  # allowlist.txt's own rule ("hosts are named one by one") and Section 12.5's enumerated list.
  local al="$ATLAS_DAY1_DIR/config/allowlist.txt"
  [[ -f "$al" ]] || die "$al missing (Phase 1 step 4's allowlist)"
  if grep -qxF "$TIKTOKEN_HOST" "$al"; then
    MEM_TIKTOKEN_ALLOWED=1
    log "allowlist: $TIKTOKEN_HOST present; tiktoken's cl100k_base will be cached in this step"
  else
    MEM_TIKTOKEN_ALLOWED=0
    warn "config/allowlist.txt does not name $TIKTOKEN_HOST, which LightRAG's tiktoken contacts ONCE for the cl100k_base BPE table (a public tokenizer file, no Principal data; offline for good once cached). The warm-up is DEFERRED, the rest of step 04 runs; D7 graph indexing refuses to start until the table is cached. Remedy: $(_mem_tiktoken_remedy)"
  fi
  [[ -f /etc/sudoers.d/atlas-engines ]] || die "/etc/sudoers.d/atlas-engines not installed (step 01)"
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
  # tiktoken fetches cl100k_base from $TIKTOKEN_HOST once; cached here so the node can go offline (services-tools.md
  # S2; orchestrator/src/atlas/memory.py uses tiktoken_model_name "gpt-4" = cl100k_base and refuses to index without
  # the cached table). Runs only when _mem_preflight found the host in config/allowlist.txt; otherwise deferred.
  if compgen -G "$wd/tiktoken/*" >/dev/null; then
    MEM_TIKTOKEN_CACHED=1
  elif (( MEM_TIKTOKEN_ALLOWED )); then
    _mem_py_env
    HOME="$ATLAS_CACHE_DIR" TIKTOKEN_CACHE_DIR="$wd/tiktoken" "$VENV/bin/python" -c 'import tiktoken; tiktoken.get_encoding("cl100k_base")' \
      || die "tiktoken could not cache cl100k_base into $wd/tiktoken through the proxy. If /var/log/squid/access.log shows TCP_DENIED for $TIKTOKEN_HOST the running squid has not been re-rendered: sudo /opt/atlas/day1/phase1-platform.sh --reload-allowlist /path/to/repo/scripts/day1/config/allowlist.txt, then re-run sudo ${ATLAS_ENTRY:-./atlas-day1.sh} phase2 (step 04 resumes here)"
    compgen -G "$wd/tiktoken/*" >/dev/null || die "tiktoken reported success but wrote nothing into $wd/tiktoken"
    MEM_TIKTOKEN_CACHED=1
  else
    MEM_TIKTOKEN_CACHED=0
    warn "tiktoken cl100k_base NOT cached (deferred: $TIKTOKEN_HOST is not in config/allowlist.txt); LightRAG is installed but D7 indexing will refuse to start until it is. Remedy: $(_mem_tiktoken_remedy)"
  fi
  chown -R atlas:atlas "$wd"
  log "LightRAG 1.5.7 installed; working dir $wd (JsonKV/NanoVectorDB/NetworkX defaults, Section 10.2 D7); tiktoken cached=$MEM_TIKTOKEN_CACHED"
}

_mem_docling() {
  local hf_home="$ATLAS_SRV/engines/hf" artifacts="$ATLAS_SRV/engines/docling"
  ensure_dir "$ATLAS_SRV/engines" atlas:atlas 755
  ensure_dir "$hf_home" atlas:atlas 755
  ensure_dir "$artifacts" atlas:atlas 755
  if ! "$VENV/bin/python" -c 'import importlib.metadata as m; v=m.version("docling"); assert v=="2.129.0", v' 2>/dev/null; then
    # UNVERIFIED (services-tools.md §2.3): PyTorch CPU wheels for the host's Python 3.14. torch is installed FIRST from
    # the CPU-only index (--index-url, so PyPI's CUDA build is never a candidate), then docling against it: pip keeps an
    # installed torch that satisfies docling's range, so the multi-GB nvidia-* wheels are never pulled. If no cp314
    # torch exists on the CPU index pip fails and this step stops (the docling-serve container still serves Open WebUI).
    log "pip install torch from $TORCH_CPU_INDEX (CPU only), then $DOCLING_PIN (several minutes)"
    _pip --index-url "$TORCH_CPU_INDEX" torch \
      || die "pip install of CPU torch from $TORCH_CPU_INDEX failed in $VENV: no wheel for $("$VENV/bin/python" --version 2>&1)? (services-tools.md §2.3: use the docling-serve container or a Python 3.12 venv)"
    "$VENV/bin/python" -c 'import torch, sys; v=torch.__version__; sys.exit(0 if "+cpu" in v else f"torch {v} is not a +cpu build")' \
      || die "the torch in $VENV is not a +cpu build (see above); refusing to install docling on top of it"
    _pip "$DOCLING_PIN" \
      || die "pip install of $DOCLING_PIN failed in $VENV: no compatible onnxruntime/torch wheels for $("$VENV/bin/python" --version 2>&1)? (services-tools.md §2.3 says to use the docling-serve container or a Python 3.12 venv in that case)"
    "$VENV/bin/python" -c 'import torch, sys; v=torch.__version__; sys.exit(0 if "+cpu" in v else f"torch {v}")' \
      || die "docling's resolver replaced the CPU torch with a non-CPU build; pin torch==<ver>+cpu in this step once the cp314 wheel is known"
  fi
  # Prefetch the default model set once (layout tableformer code_formula picture_classifier rapidocr, VERIFIED
  # docling/cli/models.py) so HF_HUB_OFFLINE=1 can be set afterwards (Section 12.5, rule §7.1).
  if [[ ! -f "$artifacts/.atlas-prefetched" ]]; then
    _mem_py_env
    HOME="$ATLAS_CACHE_DIR" HF_HOME="$hf_home" "$VENV/bin/docling-tools" models download -o "$artifacts" \
      || die "docling-tools models download failed (huggingface.co through the proxy; re-run to resume)"
    date -Is >"$artifacts/.atlas-prefetched"
  fi
  # CONVENTIONS §2: no secret under /srv/atlas. huggingface_hub would persist a login token at $HF_HOME/token.
  [[ ! -e "$hf_home/token" ]] || die "$hf_home/token exists: a Hugging Face login token was persisted under /srv/atlas (CONVENTIONS §2); shred it (shred -u $hf_home/token) and find which tool called login()"
  # Offline proof: convert a tiny HTML document with the network switched off and the artifacts path set.
  local tmp
  tmp="$(mktemp -d)"
  printf '<html><body><h1>ATLAS</h1><p>Docling offline check.</p><table><tr><td>a</td><td>1</td></tr></table></body></html>\n' >"$tmp/check.html"
  _mem_py_env
  HOME="$ATLAS_CACHE_DIR" HF_HUB_OFFLINE=1 DOCLING_ARTIFACTS_PATH="$artifacts" "$VENV/bin/python" - "$tmp/check.html" <<'PY' \
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
  ensure_kv "$f" LIGHTRAG_TIKTOKEN_CACHED "$MEM_TIKTOKEN_CACHED"   # header contract: 0 = warm-up deferred (allowlist)
  ensure_kv "$f" DOCLING_ARTIFACTS_PATH "$ATLAS_SRV/engines/docling"
  ensure_kv "$f" HF_HOME "$ATLAS_SRV/engines/hf"
  ensure_kv "$f" HF_HUB_OFFLINE 1
  # Rule §7.1 (no telemetry) and CONVENTIONS §2 (no token under /srv/atlas) at run time, whichever env file loads first.
  ensure_kv "$f" HF_HUB_DISABLE_TELEMETRY 1
  ensure_kv "$f" DO_NOT_TRACK 1
  ensure_kv "$f" ANONYMIZED_TELEMETRY False
  ensure_kv "$f" HF_HUB_DISABLE_IMPLICIT_TOKEN 1
  ensure_kv "$f" HF_TOKEN_PATH "$ATLAS_ETC/secrets/hf-token"
  chown root:atlas "$f"
  chmod 640 "$f"
  # Section 16.3 item 6 ("modify its own code") enforced by the OS, not only by behaviour: the interpreter and
  # site-packages the orchestrator runs are root:atlas, group-readable, not writable by atlas (pip runs as root here and
  # in step 02/10). Runtime-writable state stays atlas-owned: LIGHTRAG_WORKING_DIR, HF_HOME, DOCLING_ARTIFACTS_PATH.
  # Step 02 applies the same ownership to /opt/atlas/orchestrator and the venv (its header, items 16-18), so the two
  # steps agree; CONVENTIONS §2's row "/opt/atlas/orchestrator/ + /opt/atlas/venv/ ... atlas" is the one to amend to
  # "root:atlas, read-only for atlas" (fix round 2: requested, not silently deviated from).
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
  _mem_write_env
  # What the Principal must know, printed in the phase output (CONVENTIONS §7.10), not only in this file's header:
  warn "ACCEPTED RISK (Day 1): atlas is in the docker group (root-equivalent on this host); Section 16.3 items 5/6/8 are enforced by the orchestrator's fixed docker run line (V17), not by the OS. Follow-up: /usr/local/sbin/atlas-sandbox-run wrapper and removal of atlas from the group (header SECURITY NOTE; README 'Accepted risks' should list it)."
  if (( MEM_TIKTOKEN_CACHED == 0 )); then
    warn "DEFERRED in step 04: tiktoken cl100k_base is not cached, so LightRAG (D7) cannot index yet. Remedy: $(_mem_tiktoken_remedy)"
    notify "Phase 2 step 4 done with ONE deferred item: tiktoken table not cached ($TIKTOKEN_HOST not allowlisted); see the phase log"
  else
    notify "Phase 2 step 4 done: resident models, ChromaDB, LightRAG, Docling"
  fi
  log "step 04 done"
}
