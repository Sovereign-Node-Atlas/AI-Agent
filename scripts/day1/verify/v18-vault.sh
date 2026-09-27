#!/usr/bin/env bash
# verify/v18-vault.sh — V18: "Vault opens by button, locks on idle, vault-tagged content absent from memory
# collections afterwards" (Sections 10.5, 11, 21; D13; Phase 2 step 9b and the Phase 2 gate). Contract (CONVENTIONS.md
# §5): exit 0 pass / 1 fail; exactly one stdout line; never prompts; under 10 minutes (idle window 20 s); safe to
# re-run. Must run as root (unit control, the override file, the test passphrase file, runuser).
# Usage: [printf '%s\n' "$passphrase" |] v18-vault.sh [CIPHER_DIR]
#   * passphrase on STDIN (a pipe, never an argument): used for CIPHER_DIR (default: the real vault when a passphrase
#     arrives). phase2/09b-vault.sh runs it this way with the Principal's passphrase on the real cipher dir.
#   * nothing on stdin: CIPHER_DIR defaults to VAULT_TEST_CIPHER_DIR (the test vault step 9b initialised) and the
#     passphrase is read from $ATLAS_ETC/secrets/vault-test.pass — the unattended gate run. The Principal's passphrase
#     is never stored, so the gate proves the mechanics on a cipher dir whose passphrase is not the Principal's.
#
# Steps (every one fails loudly, nothing is skipped):
#   1. The vault must be locked; the helper, unit, memory.env (CHROMA_URL, CHROMA_COLLECTIONS), the orchestrator's
#      /health and atlas-admin exist.
#   2. `atlas-vault override` (root) writes the one-shot override (idle 20 s, cipher dir CIPHER_DIR) into the root-only
#      /run/atlas-vault, then the vault is opened BY THE BUTTON: POST /vault/open on the orchestrator with the
#      passphrase in the JSON body (built by python from stdin, never argv), which pipes it into `sudo -n atlas-vault
#      open` -> atlas-vault.service. Asserted afterwards: the mount point mounted, the unit active, the tmpfs passfile
#      and the override file gone, gocryptfs's cmdline carrying exactly CIPHER_DIR and -idle 20s (the override was
#      honoured), `sudo -n atlas-vault status` as atlas == open, GET /vault/status == "open" (a mount invisible inside
#      the orchestrator's mount namespace would fail here).
#   3. A marker phrase is written to a file in the vault as atlas.
#   4. `atlas-admin vault-session-test --file <that file>` (contract: reads the file inside a vault-tagged session,
#      then attempts a memory write through the normal path, synchronously; exit 0; one JSON line) as atlas. When the
#      installed atlas-admin does not accept --file (README-contracts.md §3 item 4), `python -m atlas.vault
#      session-test --file` is the fallback and the evidence says so. The marker file is then deleted (the Principal's
#      real vault must not accumulate test litter; Section 16.3 rule 5).
#   5. Wait past the idle window: the mount must be gone and the unit inactive within 120 s (gocryptfs checks idleness
#      on a timer, so the unmount lands between 1x and 2x the -idle value); GET /vault/status == "locked".
#   6. Every ChromaDB collection is queried for the marker phrase (where_document $contains; a paging scan is the
#      fallback when the server refuses that filter) and the LightRAG working dir is grepped: PASS only when the
#      phrase is absent everywhere AND the six Section 10.1 collections all exist.
export ATLAS_LOG_TO_STDERR=1
# shellcheck source=lib/common.sh
source "$(dirname "$(readlink -f "$0")")/../lib/common.sh"

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { echo "V18 fail: must run as root"; exit 1; }
vault_env="$ATLAS_ETC/vault.env"
[[ -r "$vault_env" ]] || { echo "V18 fail: $vault_env missing (Phase 2 step 9b has not run)"; exit 1; }
set -a
# shellcheck disable=SC1090  # KEY=VALUE lines written by phase2/09b-vault.sh
source "$vault_env"
set +a
: "${VAULT_CIPHER_DIR:?}" "${VAULT_MOUNT_DIR:?}" "${VAULT_TEST_CIPHER_DIR:?}"
: "${VAULT_UNIT:=atlas-vault.service}" "${VAULT_HELPER:=/usr/local/bin/atlas-vault}"
: "${VAULT_PASS_FILE:=/run/atlas-vault/pass}" "${VAULT_OVERRIDE_FILE:=/run/atlas-vault/override.env}"
IDLE=20s
IDLE_WAIT_S=120

# --- passphrase source and cipher dir -------------------------------------------------------------------------------------
pass=""
if [[ ! -t 0 ]]; then
  IFS= read -r -t 5 pass || true
fi
if [[ -n "$pass" ]]; then
  cipher="${1:-$VAULT_CIPHER_DIR}"
  mode="real"
else
  cipher="${1:-$VAULT_TEST_CIPHER_DIR}"
  mode="test"
  tpf="$ATLAS_ETC/secrets/vault-test.pass"
  [[ -r "$tpf" ]] || { echo "V18 fail: nothing on stdin and $tpf is missing (step 9b writes it); pipe a passphrase or re-run step 9b"; exit 1; }
  IFS= read -r pass <"$tpf" || true
  [[ -n "$pass" ]] || { echo "V18 fail: $tpf is empty"; exit 1; }
  [[ "$cipher" == "$VAULT_CIPHER_DIR" ]] && { echo "V18 fail: the real vault needs the Principal's passphrase on stdin; nothing arrived"; exit 1; }
fi
[[ -f "$cipher/gocryptfs.conf" ]] || { echo "V18 fail: $cipher/gocryptfs.conf missing: not an initialised vault (step 9b)"; exit 1; }

# --- preconditions ----------------------------------------------------------------------------------------------------
is_mounted() { awk -v m="$VAULT_MOUNT_DIR" '$5 == m { f = 1 } END { exit !f }' /proc/self/mountinfo; }
[[ -x "$VAULT_HELPER" ]] || { echo "V18 fail: $VAULT_HELPER missing (step 9b installs it)"; exit 1; }
[[ -f "/etc/systemd/system/$VAULT_UNIT" ]] || { echo "V18 fail: /etc/systemd/system/$VAULT_UNIT missing (step 9b installs it)"; exit 1; }
if is_mounted; then
  echo "V18 fail: the vault is open at $VAULT_MOUNT_DIR; lock it first ($VAULT_HELPER lock) and re-run"; exit 1
fi
mem_env="$ATLAS_ETC/memory.env"
[[ -r "$mem_env" ]] || { echo "V18 fail: $mem_env missing (Phase 2 step 4 writes CHROMA_URL and CHROMA_COLLECTIONS)"; exit 1; }
CHROMA_URL=""; CHROMA_COLLECTIONS=""; LIGHTRAG_WORKING_DIR=""
set -a
# shellcheck disable=SC1090  # KEY=VALUE lines written by phase2/04-memory.sh
source "$mem_env"
set +a
[[ -n "$CHROMA_URL" ]] || { echo "V18 fail: CHROMA_URL empty in $mem_env"; exit 1; }
[[ -n "$CHROMA_COLLECTIONS" ]] || CHROMA_COLLECTIONS="corporate estate scars documents_corporate documents_estate sentinel"   # Section 10.1
orch_env="$ATLAS_ETC/orchestrator.env"
[[ -r "$orch_env" ]] || { echo "V18 fail: $orch_env missing (Phase 2 step 2)"; exit 1; }
admin="$ATLAS_OPT/venv/bin/atlas-admin"
venv_py="$ATLAS_OPT/venv/bin/python"
[[ -x "$admin" ]] || { echo "V18 fail: $admin missing (Phase 2 step 2 contract)"; exit 1; }
[[ -x "$venv_py" ]] || { echo "V18 fail: $venv_py missing (Phase 2 step 2 contract)"; exit 1; }
orch_dir="$ATLAS_OPT/orchestrator"
orch_port="$(awk -F= '$1=="ORCH_PORT" {print $2; exit}' "$orch_env" 2>/dev/null || true)"
[[ "$orch_port" =~ ^[0-9]+$ ]] || orch_port=8800
orch_base="http://127.0.0.1:$orch_port"
hb="$(curl -s --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' "$CHROMA_URL/api/v2/heartbeat" || true)"
[[ "$hb" == 200 ]] || { echo "V18 fail: ChromaDB heartbeat at $CHROMA_URL/api/v2/heartbeat answered HTTP ${hb:-none}"; exit 1; }
oh="$(curl -s --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code}' "$orch_base/health" || true)"
[[ "$oh" == 200 ]] || { echo "V18 fail: the orchestrator's $orch_base/health answered HTTP ${oh:-none}; the button (POST /vault/open) cannot be exercised (systemctl status atlas-orchestrator)"; exit 1; }

orch_vault_status() {
  curl -s --noproxy '*' --max-time 10 "$orch_base/vault/status" 2>/dev/null \
    | python3 -c 'import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("unparseable"); sys.exit(0)
print(d.get("state") or d.get("status") or "missing-state")' 2>/dev/null || echo "no-answer"
}

mfile=""
# shellcheck disable=SC2329  # invoked through the EXIT trap below
cleanup() {
  # Never leave the marker, the vault open, an override or a passfile behind, whatever happened above.
  if [[ -n "$mfile" ]] && is_mounted; then runuser -u atlas -- rm -f "$mfile" 2>/dev/null || true; fi
  if is_mounted || systemctl is-active --quiet "$VAULT_UNIT"; then "$VAULT_HELPER" lock >/dev/null 2>&1 || true; fi
  rm -f "$VAULT_OVERRIDE_FILE" 2>/dev/null || true
  if [[ -e "$VAULT_PASS_FILE" && ! -L "$VAULT_PASS_FILE" ]]; then shred -u "$VAULT_PASS_FILE" 2>/dev/null || rm -f "$VAULT_PASS_FILE"; fi
  pass=""
  return 0
}
trap cleanup EXIT

# --- 2. open by the button ---------------------------------------------------------------------------------------------
# The one-shot override (root-only directory; the helper refuses an idle longer than VAULT_IDLE or a cipher dir that is
# not an initialised vault) applies to the next open, which the orchestrator performs through sudo -n like the button.
if ! out="$(VAULT_IDLE_OVERRIDE="$IDLE" VAULT_CIPHER_OVERRIDE="$cipher" "$VAULT_HELPER" override 2>&1)"; then
  echo "V18 fail: 'atlas-vault override' (idle $IDLE, cipher $cipher) refused: $(tr '\n' ' ' <<<"$out" | cut -c1-250)"; exit 1
fi
[[ -s "$VAULT_OVERRIDE_FILE" ]] || { echo "V18 fail: $VAULT_OVERRIDE_FILE did not appear after 'atlas-vault override' (helper printed '$out')"; exit 1; }
t_open="$(date +%s)"
# The passphrase goes from this process's memory to python's stdin to curl's stdin: never an argument anywhere.
resp="$(printf '%s' "$pass" \
  | python3 -c 'import json, sys; print(json.dumps({"passphrase": sys.stdin.read().rstrip("\n")}))' \
  | curl -s --noproxy '*' --max-time 200 -X POST -H 'Content-Type: application/json' --data-binary @- \
      -w '\n%{http_code}' "$orch_base/vault/open" 2>&1 || true)"
code="${resp##*$'\n'}"
body="${resp%$'\n'*}"
if [[ "$code" != 200 ]]; then
  echo "V18 fail: POST /vault/open (the button) answered HTTP ${code:-none} for the $mode cipher dir: $(tr '\n' ' ' <<<"$body" | cut -c1-250) (journalctl -u $VAULT_UNIT -n 8; a 403 = the helper refused the passphrase, gocryptfs exit 12)"; exit 1
fi
if ! python3 -c 'import json, sys; d = json.loads(sys.argv[1]); sys.exit(0 if d.get("ok") and d.get("state") == "open" else 1)' "$body" 2>/dev/null; then
  echo "V18 fail: POST /vault/open answered 200 but not {ok: true, state: open}: ${body:0:250}"; exit 1
fi
is_mounted || { echo "V18 fail: the button answered open but $VAULT_MOUNT_DIR is not mounted on the host"; exit 1; }
systemctl is-active --quiet "$VAULT_UNIT" || { echo "V18 fail: mounted but $VAULT_UNIT is not active (the mount did not come from the unit)"; exit 1; }
[[ ! -e "$VAULT_PASS_FILE" ]] || { echo "V18 fail: $VAULT_PASS_FILE survived the mount (the unit's wait-mounted must shred it)"; exit 1; }
[[ ! -e "$VAULT_OVERRIDE_FILE" ]] || { echo "V18 fail: $VAULT_OVERRIDE_FILE survived the mount (wait-mounted must remove the one-shot override)"; exit 1; }
# The override must have been honoured: gocryptfs's own command line names the cipher dir and the idle.
main_pid="$(systemctl show -p MainPID --value "$VAULT_UNIT" 2>/dev/null || true)"
gcmd="$(tr '\0' ' ' <"/proc/${main_pid:-0}/cmdline" 2>/dev/null || true)"
[[ "$gcmd" == *"-idle $IDLE "* ]] || { echo "V18 fail: the unit did not apply the idle override ($IDLE): gocryptfs cmdline '$gcmd'"; exit 1; }
[[ "$gcmd" == *" $cipher $VAULT_MOUNT_DIR"* ]] || { echo "V18 fail: the unit mounted a different cipher dir than $cipher: gocryptfs cmdline '$gcmd'"; exit 1; }
# The button path once more from the shell: user atlas -> sudo-rs -> the helper (NOPASSWD fragment /etc/sudoers.d/atlas-vault).
sudo_st="$(runuser -u atlas -- sudo -n "$VAULT_HELPER" status 2>&1 || true)"
[[ "$sudo_st" == open ]] || { echo "V18 fail: as atlas, 'sudo -n $VAULT_HELPER status' answered '${sudo_st:0:120}' instead of 'open' (sudo-rs and /etc/sudoers.d/atlas-vault)"; exit 1; }
# The orchestrator's own view (its unit runs with ProtectSystem=; the host mount must have propagated into it).
orch_open="$(orch_vault_status)"
[[ "$orch_open" == open ]] || { echo "V18 fail: GET /vault/status on the orchestrator says '$orch_open' while the host shows the vault mounted (the mount is invisible inside atlas-orchestrator's mount namespace)"; exit 1; }

# --- 3. marker file, written as atlas (the mount owner) -----------------------------------------------------------------
marker="ATLAS-V18-VAULT-MARKER-$(date +%s)-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n') vermilion lighthouse"
mfile="$VAULT_MOUNT_DIR/v18-marker-$(date +%s).txt"
# shellcheck disable=SC2016  # $1/$2 are expanded by the inner bash, on purpose
runuser -u atlas -- bash -c 'printf "Top secret note for the vault test: %s\n" "$1" >"$2"' _ "$marker" "$mfile" \
  || { echo "V18 fail: could not write $mfile as atlas"; exit 1; }

# --- 4. the orchestrator reads it in a vault-tagged session and tries to remember it ----------------------------------------
# README-contracts.md §1 states `atlas-admin vault-session-test --file PATH`; the installed admin.py (§3 item 4) may
# still lack --file. Probe its help text and fall back to the module entry that does accept it.
admin_via="atlas-admin"
if ! "$admin" vault-session-test --help 2>/dev/null | grep -q -- '--file'; then
  admin_via="python -m atlas.vault (atlas-admin lacks --file; README-contracts.md §3 item 4)"
  admin_exec="exec '$venv_py' -m atlas.vault session-test --file '$mfile'"
else
  admin_exec="exec '$admin' vault-session-test --file '$mfile'"
fi
admin_cmd="set -a; source '$orch_env'; source '$mem_env'; source '$vault_env'; set +a; cd '$orch_dir'; $admin_exec"
rc=0
admin_out="$(timeout 300 runuser -u atlas -- /bin/bash -c "$admin_cmd" 2>&1)" || rc=$?
if (( rc != 0 )); then
  echo "V18 fail: vault-session-test --file $mfile via $admin_via exited $rc (contract in phase2/README-contracts.md): $(tr '\n' ' ' <<<"$admin_out" | cut -c1-250)"; exit 1
fi
admin_json="$(grep -E '^\{' <<<"$admin_out" | tail -n1 || true)"
[[ -n "$admin_json" ]] || { echo "V18 fail: vault-session-test printed no JSON line (contract: exactly one): ${admin_out:0:200}"; exit 1; }
if ! python3 -c 'import json, sys; d = json.loads(sys.argv[1]); sys.exit(0 if d.get("read") else 1)' "$admin_json" 2>/dev/null; then
  echo "V18 fail: vault-session-test reports it did not read the file: ${admin_json:0:200}"; exit 1
fi
# The test litter goes now (before the idle window starts counting); the session test must not hold the file open.
runuser -u atlas -- rm -f "$mfile" || { echo "V18 fail: could not remove $mfile as atlas"; exit 1; }
mfile=""

# --- 5. idle lock -----------------------------------------------------------------------------------------------------------
waited=0
while is_mounted && (( waited < IDLE_WAIT_S )); do
  sleep 5
  waited=$(( waited + 5 ))
done
if is_mounted; then
  echo "V18 fail: still mounted ${waited}s after opening with -idle $IDLE (gocryptfs idle unmount did not fire; a process holds files open? lsof +f -- $VAULT_MOUNT_DIR)"; exit 1
fi
# The unit follows the daemon's exit within moments.
for _ in $(seq 1 12); do
  systemctl is-active --quiet "$VAULT_UNIT" || break
  sleep 5
done
if systemctl is-active --quiet "$VAULT_UNIT"; then
  echo "V18 fail: unmounted after idle but $VAULT_UNIT is still active"; exit 1
fi
locked_after=$(( $(date +%s) - t_open ))
orch_locked="$(orch_vault_status)"
[[ "$orch_locked" == locked ]] || { echo "V18 fail: after the idle unmount GET /vault/status says '$orch_locked', not 'locked'"; exit 1; }

# --- 6. the memory rule: the marker must be in no collection and not in the graph store -----------------------------------
result="$(python3 - "$CHROMA_URL" "$marker" "$CHROMA_COLLECTIONS" <<'PY' 2>&1 || true
import json
import sys
import urllib.error
import urllib.request

base, marker, wanted = sys.argv[1].rstrip("/"), sys.argv[2], set(sys.argv[3].split())
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through the allowlist proxy
root = f"{base}/api/v2/tenants/default_tenant/databases/default_database/collections"


def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=60) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


try:
    cols = call("GET", f"{root}?limit=1000") or []
except urllib.error.HTTPError as e:
    print(f"FAIL\tGET {root} -> HTTP {e.code} (the v2 collections path phase2/04-memory.sh used no longer answers)")
    sys.exit(0)
names = {c["name"]: c["id"] for c in cols if isinstance(c, dict) and "name" in c}
missing = sorted(wanted - set(names))
if missing:
    print(f"FAIL\tSection 10.1 collections missing in ChromaDB: {missing} (have {sorted(names)})")
    sys.exit(0)
hits, how = [], []
for name, cid in sorted(names.items()):
    url = f"{root}/{cid}/get"
    try:
        res = call("POST", url, {"where_document": {"$contains": marker}, "include": ["documents"], "limit": 10}) or {}
        n = len(res.get("ids") or [])
        how.append(f"{name}:contains")
    except urllib.error.HTTPError:
        # Fallback: page through every document and match locally (semantics certain, cost fine on Day 1).
        n, offset, total = 0, 0, 0
        while True:
            try:
                res = call("POST", url, {"include": ["documents"], "limit": 500, "offset": offset}) or {}
            except urllib.error.HTTPError as e2:
                print(f"FAIL\tcollection {name}: POST {url} -> HTTP {e2.code} (cannot query ChromaDB; check the v2 API path)")
                sys.exit(0)
            docs = res.get("documents") or []
            if not docs:
                break
            total += len(docs)
            n += sum(1 for d in docs if d and marker in d)
            offset += len(docs)
            if total > 200000:
                print(f"FAIL\tcollection {name}: more than 200000 documents and the $contains filter is refused; cannot prove absence")
                sys.exit(0)
        how.append(f"{name}:scan{total}")
    if n:
        hits.append(f"{name}({n})")
if hits:
    print(f"FAIL\tmarker phrase FOUND in collection(s) {', '.join(hits)}: vault-tagged content reached memory (Section 10.5)")
else:
    print(f"PASS\tabsent from {len(names)} collection(s) [{', '.join(sorted(names))}] via {', '.join(how)}")
PY
)"
verdict="${result%%$'\t'*}"; detail="${result#*$'\t'}"
[[ "$verdict" == PASS ]] || { echo "V18 fail: ${detail:-$result}"; exit 1; }
graph_note="graph store not present"
if [[ -n "$LIGHTRAG_WORKING_DIR" && -d "$LIGHTRAG_WORKING_DIR" ]]; then
  if grep -rqF -- "$marker" "$LIGHTRAG_WORKING_DIR" 2>/dev/null; then
    echo "V18 fail: marker phrase found under the LightRAG working dir $LIGHTRAG_WORKING_DIR (Section 10.2 is a memory layer too)"; exit 1
  fi
  graph_note="absent from the graph store ($LIGHTRAG_WORKING_DIR)"
fi
echo "vault ($mode cipher dir) opened by the button (POST /vault/open -> sudo -n atlas-vault -> $VAULT_UNIT; override honoured; sudo status and GET /vault/status open), marker read by vault-session-test via $admin_via and removed, auto-locked after ${locked_after}s with -idle $IDLE (/vault/status locked); marker $detail; $graph_note"
exit 0
