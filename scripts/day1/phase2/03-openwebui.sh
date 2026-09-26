#!/usr/bin/env bash
# phase2/03-openwebui.sh — Section 17 Phase 2 step 3: Open WebUI 0.11.4 with the offline hardening of Section 12.1
# and Appendix B; A.T.L.A.S., Ren and Arthur registered as models; the router Filter installed; the D9 retention
# contract; V12. Sourced by phase2-services.sh through run_phase_steps; defines step_03 only.
#
# Order (each part idempotent):
#   1. Secrets: $ATLAS_ETC/secrets/openwebui.env (WEBUI_SECRET_KEY so the JWT secret survives a rebuild, research 1.1;
#      OPENAI_API_KEY=atlas-local, the dummy the orchestrator ignores) and openwebui-principal.env (the first admin's
#      email and password, generated once, root 600, never printed: the step tells the Principal where to read it).
#   2. `core_compose up -d open-webui` from docker/core/compose.yml (host network; every offline variable the
#      services research verified is in that file), wait for /health.
#   3. First admin created NON-INTERACTIVELY: POST /api/v1/auths/signup works while the user table is empty even with
#      ENABLE_SIGNUP=false (research 1.4 VERIFIED); re-runs sign in instead. An admin API key (ENABLE_API_KEYS=true) is
#      stored for the retention task; the JWT is the fallback if the key route differs (UNVERIFIED response shape).
#   4. Models: the orchestrator's /v1/models (atlas, ren, arthur) must appear in Open WebUI's model list.
#   5. Filter: orchestrator/src/atlas/openwebui_filter.py installed through POST /api/v1/functions/create (or
#      .../update), valve ORCHESTRATOR_URL set, toggled active and global (toggles are flips: state is read first).
#   6. Retention (D9, 90 days): the orchestrator's Celery task; this step proves the contract statically (task
#      registered and scheduled in atlas.celery_app) and writes its settings; the run is nightly under beat.
#   7. Short-circuit proof of the update check, then run_verify V12 v12-openwebui-offline.sh (fail recorded, not fatal
#      here: the Phase 2 gate blocks on it).
#
# CONTRACTS (CONVENTIONS.md does not state them):
#   * $ATLAS_DAY1_DIR/orchestrator/src/atlas/openwebui_filter.py exists and is a valid Open WebUI Filter: a class named
#     `Filter` (type detection is hasattr(module, "Filter"), research 1.4 VERIFIED) with `Valves.ORCHESTRATOR_URL`
#     (pydantic field) and no `requirements:` frontmatter (pip at load time is impossible offline).
#   * atlas.celery_app registers the chat-retention task and schedules it nightly in beat_schedule (checked below by
#     name: a task or schedule key containing "retention"). The task reads OPENWEBUI_URL, OPENWEBUI_ADMIN_TOKEN_FILE and
#     OPENWEBUI_CHAT_RETENTION_DAYS (orchestrator.env, step 02), lists chats with GET /api/v1/chats/all/db, keeps pinned
#     chats and vault-tagged content (10.5), summarises the rest into the Vector Cortex, then DELETE /api/v1/chats/{id}
#     (research 1.6 VERIFIED routes). The SQLite fallback in research 1.6 is for a dead API only, container stopped.
#   * core_compose / orch_admin come from phase2/02-orchestrator.sh (sourced earlier by run_phase_steps).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}
if ! declare -F core_compose >/dev/null; then
  # shellcheck source=phase2/02-orchestrator.sh
  source "$ATLAS_DAY1_DIR/phase2/02-orchestrator.sh"
fi

OW_SECRETS="$ATLAS_ETC/secrets/openwebui.env"
OW_PRINCIPAL="$ATLAS_ETC/secrets/openwebui-principal.env"
OW_TOKEN_FILE="$ATLAS_ETC/secrets/openwebui-admin.token"
OW_FILTER_SRC="$ATLAS_DAY1_DIR/orchestrator/src/atlas/openwebui_filter.py"
OW_FILTER_ID="atlas_router"
OW_URL=""
OW_TOKEN=""

_ow_rand() { head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "${1:-32}"; }

# _ow_api METHOD PATH [JSON_BODY] — prints "<http_code>\n<body>"; bearer token added when OW_TOKEN is set.
_ow_api() {
  local method="$1" path="$2" body="${3:-}" hdr=()
  [[ -n "$OW_TOKEN" ]] && hdr=(-H "Authorization: Bearer $OW_TOKEN")
  if [[ -n "$body" ]]; then
    curl -s --noproxy '*' --max-time 60 -X "$method" "${hdr[@]}" -H 'Content-Type: application/json' \
      --data-binary "$body" -w '\n%{http_code}' "$OW_URL$path" 2>/dev/null | python3 -c '
import sys
raw = sys.stdin.read()
body, _, code = raw.rpartition("\n")
print(code or "000"); print(body)'
  else
    curl -s --noproxy '*' --max-time 60 -X "$method" "${hdr[@]}" -w '\n%{http_code}' "$OW_URL$path" 2>/dev/null | python3 -c '
import sys
raw = sys.stdin.read()
body, _, code = raw.rpartition("\n")
print(code or "000"); print(body)'
  fi
}

# _ow_json JSON PYEXPR — evaluate a python expression over the parsed JSON (bound to d); prints the result.
_ow_json() {
  python3 -c 'import json, sys
d = json.loads(sys.argv[1])
print(eval(sys.argv[2]))' "$1" "$2" 2>/dev/null
}

_ow_secrets() {
  ensure_dir "$ATLAS_ETC/secrets" root:root 700
  if [[ ! -s "$OW_SECRETS" ]] || ! grep -q '^WEBUI_SECRET_KEY=.\+' "$OW_SECRETS"; then
    {
      echo "# Open WebUI secrets (compose env_file for docker/core/compose.yml; CONVENTIONS.md §2). Generated by Phase 2 step 3."
      echo "WEBUI_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
      echo "OPENAI_API_KEY=atlas-local"
    } | install -m 600 -o root -g root /dev/stdin "$OW_SECRETS"
    log "generated $OW_SECRETS (WEBUI_SECRET_KEY; root 600)"
  fi
  if [[ ! -s "$OW_PRINCIPAL" ]]; then
    {
      echo "# The Principal's Open WebUI login (first admin). Generated once by Phase 2 step 3; change it in the UI if you like."
      echo "OPENWEBUI_ADMIN_EMAIL=principal@$DOMAIN"
      echo "OPENWEBUI_ADMIN_PASSWORD=$(_ow_rand 24)"
      echo "OPENWEBUI_ADMIN_NAME=Principal"
    } | install -m 600 -o root -g root /dev/stdin "$OW_PRINCIPAL"
    log "generated the Principal's Open WebUI login into $OW_PRINCIPAL (root 600; never printed)"
  fi
}

_ow_up() {
  grep -qE '^[[:space:]]+open-webui:' "$ATLAS_DAY1_DIR/docker/core/compose.yml" || die "docker/core/compose.yml defines no 'open-webui' service"
  wait_http "http://127.0.0.1:$ORCH_PORT/health" 30 || die "the orchestrator is not healthy on 127.0.0.1:$ORCH_PORT (step 02 must have run; systemctl status atlas-orchestrator)"
  ensure_dir "$ATLAS_SRV/data/open-webui" root:root 750
  proxy_env
  log "docker compose up -d open-webui (ghcr.io/open-webui/open-webui:v0.11.4, ~4 GB pull through the proxy on first run)"
  retry 3 core_compose up -d --quiet-pull open-webui \
    || die "docker compose up open-webui failed (ghcr.io and pkg-containers.githubusercontent.com must be allowlisted): $(core_compose logs --tail 20 open-webui 2>&1 | tail -n 20)"
  OW_URL="http://127.0.0.1:$OPENWEBUI_PORT"
  if ! wait_http "$OW_URL/health" 300; then
    core_compose logs --tail 40 open-webui >&2 || true
    die "Open WebUI did not answer 200 on $OW_URL/health within 300 s (container logs above)"
  fi
  # Host network: the listener must be the one on $OPENWEBUI_PORT; ufw scopes it to LAN + WireGuard (Phase 1 step 4).
  local mode
  mode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' atlas-openwebui 2>/dev/null || true)"
  [[ "$mode" == host ]] || die "atlas-openwebui runs with NetworkMode=$mode; docker/core/compose.yml expects host (it must reach the loopback-only orchestrator and llama-server)"
  ss -ltnH "sport = :$OPENWEBUI_PORT" | grep -q ":$OPENWEBUI_PORT" || die "nothing listens on TCP $OPENWEBUI_PORT after the container came up"
  local rules
  rules="$(ufw status 2>/dev/null | grep -c "$OPENWEBUI_PORT/tcp" || true)"
  (( rules > 0 )) || warn "ufw shows no rule for $OPENWEBUI_PORT/tcp: Phase 1 step 4 should have allowed it on the LAN interface and the WireGuard bridge (Section 3.6)"
  log "Open WebUI up on $OW_URL (host network; ufw rules for $OPENWEBUI_PORT/tcp: $rules)"
}

_ow_admin_token() {
  local email pass name resp code body
  email="$(awk -F= '$1=="OPENWEBUI_ADMIN_EMAIL" {print $2; exit}' "$OW_PRINCIPAL")"
  pass="$(awk -F= '$1=="OPENWEBUI_ADMIN_PASSWORD" {print $2; exit}' "$OW_PRINCIPAL")"
  name="$(awk -F= '$1=="OPENWEBUI_ADMIN_NAME" {print $2; exit}' "$OW_PRINCIPAL")"
  [[ -n "$email" && -n "$pass" ]] || die "$OW_PRINCIPAL lacks OPENWEBUI_ADMIN_EMAIL/PASSWORD"
  local payload
  payload="$(python3 -c 'import json, sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2], "name": sys.argv[3]}))' "$email" "$pass" "${name:-Principal}")"
  # First user == admin, only while the table is empty (research 1.4); otherwise sign in.
  resp="$(_ow_api POST /api/v1/auths/signup "$payload")"
  code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
  if [[ "$code" == 200 ]]; then
    OW_TOKEN="$(_ow_json "$body" 'd["token"]')"
    log "first admin created through /api/v1/auths/signup ($email, role $(_ow_json "$body" 'd.get("role")'))"
  else
    payload="$(python3 -c 'import json, sys; print(json.dumps({"email": sys.argv[1], "password": sys.argv[2]}))' "$email" "$pass")"
    resp="$(_ow_api POST /api/v1/auths/signin "$payload")"
    code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
    [[ "$code" == 200 ]] || die "Open WebUI signup and signin both failed (signin HTTP $code: ${body:0:200}). If the data dir $ATLAS_SRV/data/open-webui predates $OW_PRINCIPAL, the stored password differs: reset it in the UI or move the data dir away and re-run with --force 03"
    OW_TOKEN="$(_ow_json "$body" 'd["token"]')"
    log "signed in as the existing admin $email"
  fi
  [[ -n "$OW_TOKEN" ]] || die "no token in the auth response: ${body:0:200}"
  local role
  role="$(_ow_json "$body" 'd.get("role")')"
  [[ "$role" == admin ]] || die "the account $email has role '$role', not admin: the first user was created by someone else (data dir $ATLAS_SRV/data/open-webui)"

  # Long-lived admin credential for the retention task (atlas reads it). Prefer an API key (ENABLE_API_KEYS=true;
  # UNVERIFIED response shape: {"api_key": "sk-..."}); fall back to the JWT, whose default lifetime in Open WebUI is
  # unlimited (JWT_EXPIRES_IN default -1, UNVERIFIED here), and say so.
  local stored="" kind=""
  resp="$(_ow_api POST /api/v1/auths/api_key)"
  code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
  if [[ "$code" == 200 ]]; then
    stored="$(_ow_json "$body" 'd.get("api_key") or ""')"
    kind="api-key"
  fi
  if [[ -z "$stored" ]]; then
    warn "POST /api/v1/auths/api_key gave HTTP $code (${body:0:120}); storing the admin JWT instead (UNVERIFIED lifetime)"
    stored="$OW_TOKEN"; kind="jwt"
  fi
  printf '%s\n' "$stored" | install -m 600 -o atlas -g atlas /dev/stdin "$OW_TOKEN_FILE"
  log "admin credential ($kind) written to $OW_TOKEN_FILE (atlas:atlas 600) for the D9 retention task"
}

_ow_models() {
  # Section 12.1: A.T.L.A.S. as one model, Ren and Arthur as direct models. They are the orchestrator's /v1/models
  # entries; Open WebUI lists them through its OpenAI connection (research 1.3). Path /api/models is the UI's own
  # list (UNVERIFIED path); /openai/models is the VERIFIED proxied list; either proves the registration.
  local path resp code body ids="" found=0
  for path in /api/models /openai/models; do
    resp="$(_ow_api GET "$path")"
    code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
    [[ "$code" == 200 ]] || continue
    ids="$(_ow_json "$body" 'sorted({m.get("id") for m in (d.get("data") if isinstance(d, dict) else d) if isinstance(m, dict)})' || true)"
    if python3 -c 'import sys; ids=sys.argv[1]; sys.exit(0 if all(("\x27%s\x27" % m) in ids for m in ("atlas","ren","arthur")) else 1)' "$ids"; then
      found=1
      log "models registered in Open WebUI via $path: $ids"
      break
    fi
    warn "$path lists $ids (atlas/ren/arthur not all present yet)"
  done
  (( found )) || die "Open WebUI does not list the models atlas, ren and arthur (OPENAI_API_BASE_URL=http://127.0.0.1:$ORCH_PORT/v1; check 'docker logs atlas-openwebui' for the model-list fetch and the orchestrator's GET /v1/models)"
}

_ow_filter() {
  [[ -f "$OW_FILTER_SRC" ]] || die "contract: $OW_FILTER_SRC does not exist (the orchestrator writer's Open WebUI Filter)"
  grep -qE '^class Filter\b' "$OW_FILTER_SRC" || die "contract: $OW_FILTER_SRC defines no top-level 'class Filter' (Open WebUI detects the type by that name)"
  if grep -qiE '^requirements:' "$OW_FILTER_SRC"; then
    die "contract: $OW_FILTER_SRC carries a 'requirements:' frontmatter line; Open WebUI would pip-install at load time, impossible offline (rule §7.1)"
  fi
  local payload resp code body
  payload="$(python3 - "$OW_FILTER_ID" "$OW_FILTER_SRC" <<'PY'
import json, sys
fid, path = sys.argv[1:3]
print(json.dumps({"id": fid, "name": "A.T.L.A.S. Router",
                  "content": open(path, encoding="utf-8").read(),
                  "meta": {"description": "Relays every prompt to the A.T.L.A.S. orchestrator (Section 7.1); the router lives there, this is the thin relay."}}))
PY
)"
  resp="$(_ow_api GET "/api/v1/functions/id/$OW_FILTER_ID")"
  code="${resp%%$'\n'*}"
  if [[ "$code" == 200 ]]; then
    resp="$(_ow_api POST "/api/v1/functions/id/$OW_FILTER_ID/update" "$payload")"
  else
    resp="$(_ow_api POST /api/v1/functions/create "$payload")"
  fi
  code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
  [[ "$code" == 200 ]] || die "installing the Filter function failed (HTTP $code): ${body:0:300} — is the file a valid Open WebUI Filter (research 1.4 skeleton)?"
  local ftype
  ftype="$(_ow_json "$body" 'd.get("type")')"
  [[ "$ftype" == filter ]] || die "Open WebUI classified $OW_FILTER_SRC as type '$ftype', not 'filter' (class must be named Filter)"

  local valves
  valves="$(python3 -c 'import json, sys; print(json.dumps({"ORCHESTRATOR_URL": sys.argv[1]}))' "http://127.0.0.1:$ORCH_PORT")"
  resp="$(_ow_api POST "/api/v1/functions/id/$OW_FILTER_ID/valves/update" "$valves")"
  code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
  [[ "$code" == 200 ]] || die "valves/update failed (HTTP $code): ${body:0:200} — the Filter's Valves must declare ORCHESTRATOR_URL"

  # Toggles flip; read first, flip only when needed, then read back and assert (research 1.4).
  local state flag
  state="$(_ow_api GET "/api/v1/functions/id/$OW_FILTER_ID")"; state="${state#*$'\n'}"
  for flag in is_active is_global; do
    if [[ "$(_ow_json "$state" "bool(d.get('$flag'))")" != True ]]; then
      if [[ "$flag" == is_active ]]; then _ow_api POST "/api/v1/functions/id/$OW_FILTER_ID/toggle" >/dev/null
      else _ow_api POST "/api/v1/functions/id/$OW_FILTER_ID/toggle/global" >/dev/null; fi
    fi
  done
  state="$(_ow_api GET "/api/v1/functions/id/$OW_FILTER_ID")"; state="${state#*$'\n'}"
  [[ "$(_ow_json "$state" "bool(d.get('is_active')) and bool(d.get('is_global'))")" == True ]] \
    || die "the Filter is not active+global after toggling: ${state:0:200}"
  log "Filter '$OW_FILTER_ID' installed from $OW_FILTER_SRC: active, global, ORCHESTRATOR_URL=http://127.0.0.1:$ORCH_PORT"
}

_ow_retention_contract() {
  # D9: 90-day retention is the orchestrator's Celery task under beat. Static proof of the contract, no side effects
  # (running it now would need ChromaDB, which step 4 brings up later in Section 17 order).
  svc_user_run /bin/bash -c "set -a; source '$ORCH_ENV'; set +a; cd '$ORCH_DIR'; exec '$ORCH_VENV/bin/python' -c '
import sys
from atlas.celery_app import app
sched = app.conf.beat_schedule or {}
hit = [k for k, v in sched.items() if \"retention\" in k.lower() or \"retention\" in str(v.get(\"task\", \"\")).lower()]
tasks = [t for t in app.tasks if \"retention\" in t.lower()]
if not hit or not tasks:
    print(\"beat_schedule keys:\", sorted(sched), \"tasks:\", sorted(t for t in app.tasks if not t.startswith(\"celery.\")), file=sys.stderr)
    sys.exit(1)
print(\"retention task\", tasks[0], \"scheduled as\", hit[0], sched[hit[0]].get(\"schedule\"))
'" || die "contract: atlas.celery_app has no chat-retention task in beat_schedule (D9, Section 10.4); see the header of this file"
  ensure_kv "$ORCH_ENV" OPENWEBUI_URL "http://127.0.0.1:$OPENWEBUI_PORT"
  ensure_kv "$ORCH_ENV" OPENWEBUI_ADMIN_TOKEN_FILE "$OW_TOKEN_FILE"
  ensure_kv "$ORCH_ENV" OPENWEBUI_CHAT_RETENTION_DAYS 90
  # beat and the workers read orchestrator.env at start: restart so the retention settings are live.
  systemctl restart atlas-celery-beat || warn "atlas-celery-beat did not restart cleanly (journalctl -u atlas-celery-beat)"
  log "D9 retention: nightly Celery task under beat, 90 days, admin credential $OW_TOKEN_FILE"
}

_ow_update_check_shortcircuit() {
  # research 1.5 (VERIFIED source): with OFFLINE_MODE the endpoint answers {"current": v, "latest": v} without any call.
  local resp code body
  resp="$(_ow_api GET /api/version/updates)"
  code="${resp%%$'\n'*}"; body="${resp#*$'\n'}"
  if [[ "$code" == 200 ]] && [[ "$(_ow_json "$body" 'd.get("current") == d.get("latest") and bool(d.get("current"))')" == True ]]; then
    log "update check short-circuited offline: $body"
  else
    warn "GET /api/version/updates answered HTTP $code: ${body:0:120} (expected current == latest; V12 below is the real proof)"
  fi
}

step_03() {
  [[ -n "${OPENWEBUI_PORT:-}" && -n "${ORCH_PORT:-}" ]] || die "OPENWEBUI_PORT/ORCH_PORT are empty (load_env)"
  [[ -s "$CORE_ENV" ]] || die "$CORE_ENV missing: step 02 must run first"
  _ow_secrets
  _ow_up
  _ow_admin_token
  _ow_models
  _ow_filter
  _ow_retention_contract
  _ow_update_check_shortcircuit
  run_verify V12 v12-openwebui-offline.sh atlas-openwebui 120 \
    || warn "V12 recorded as fail: Open WebUI opened a connection beyond loopback after hardening; the Phase 2 gate will block until it is zero (see the verify table and 'journalctl -k | grep \"ATLAS v12\"')"
  local email
  email="$(awk -F= '$1=="OPENWEBUI_ADMIN_EMAIL" {print $2; exit}' "$OW_PRINCIPAL")"
  cat <<MSG

  ==== Open WebUI is up ====
  URL (LAN):        http://${LAN_IP:-<LAN IP>}:$OPENWEBUI_PORT      (also over WireGuard; refused elsewhere by ufw)
  Login:            $email
  Password:         sudo cat $OW_PRINCIPAL
  Models:           A.T.L.A.S. (default), Ren, Arthur — served by the orchestrator; the router Filter is active.
  Offline:          every update/community/web-search/model-download path is off (Section 12.1); V12 recorded.
  ==========================
MSG
  notify "Phase 2 step 3 done: Open WebUI on http://${LAN_IP:-?}:$OPENWEBUI_PORT (admin $email)"
  log "step 03 done"
}
