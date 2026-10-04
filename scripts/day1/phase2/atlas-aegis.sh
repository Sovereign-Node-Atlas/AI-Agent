#!/usr/bin/env bash
# phase2/atlas-aegis.sh — installed by phase2/07-restic.sh as /usr/local/sbin/atlas-aegis (install -m 755; a repository
# file so shellcheck covers it, fix round 2). Run as ROOT by /etc/systemd/system/atlas-aegis.service and
# atlas-aegis-forget.service (their headers explain the sequence):
#   freeze         with atlas-orchestrator AND atlas-celery-cpu active: `atlas-admin enqueue aegis-freeze --wait 900` as
#                  atlas MUST succeed (the gpu consumer pauses, /run/atlas/aegis-freeze goes up), otherwise exit 1 and the
#                  unit fails before restic runs (Section 9.5 Freeze row; an unfrozen backup is never called a success).
#                  With either unit inactive the freeze is skipped EXPLICITLY (crash-consistent backup; the journal says
#                  so). The intent marker is written BEFORE the enqueue, so `finish` thaws even when the wait timed out
#                  after the task had already paused the queue. The marker lives in the unit's own RuntimeDirectory
#                  /run/atlas-aegis (root:root 755): root never creates a file by name inside the atlas-owned /run/atlas
#                  (symlink planting by a compromised atlas process would be a root escalation); it only READS the flag.
#   manifests      AS ATLAS (runuser): copy every regular <models|engines>/<key>/MANIFEST.json into
#                  $ATLAS_SRV/data/manifests (Appendix C). The data tree is atlas-owned, so the owner does the copying;
#                  symlinks are never followed (find -type f, and the destination path is checked), so a planted link
#                  cannot make anything read or overwrite a file outside the tree.
#   finish         ExecStopPost of atlas-aegis.service, always runs. Thaw goes through celery's broadcast channel —
#                  `celery control add_consumer cpu|gpu`, which a worker answers while its task queues are cancelled —
#                  never through a paused queue; then the flag comes down (as atlas: it is atlas's file in atlas's
#                  directory) and the package's aegis-thaw task is enqueued (now deliverable) for its ledger row and
#                  spool replay. No reply from a worker: both workers are restarted and the unit is reported FAILED.
#                  Then a ntfy push whenever systemd's $SERVICE_RESULT is not "success" or the thaw fell back.
#   forget-result  ExecStopPost of atlas-aegis-forget.service: ntfy push when the retention prune did not succeed.
#   notify MSG     push MSG (used by the two above; the bearer token is read by root from $ATLAS_ETC/secrets/ntfy.env and
#                  handed to curl through a process substitution, never on argv where /proc/<pid>/cmdline shows it).
# Environment from the unit: ATLAS_OPT, ATLAS_SRV, ATLAS_ETC, RUNTIME_DIRECTORY, restic.env, orchestrator.env,
# secrets/redis.env and memory.env keys; runuser keeps them, so atlas-admin and celery see the broker URL and the
# settings. Never exits 3 (the unit's SuccessExitStatus for restic).
set -Eeuo pipefail
: "${ATLAS_OPT:=/opt/atlas}"; : "${ATLAS_SRV:=/srv/atlas}"; : "${ATLAS_ETC:=/etc/atlas}"
VENV="$ATLAS_OPT/venv"
RUN_DIR=/run/atlas                                   # atlas-orchestrator.service's RuntimeDirectory (atlas:atlas 750): READ only
OWN_RUN_DIR="${RUNTIME_DIRECTORY:-/run/atlas-aegis}" # this unit's RuntimeDirectory (root:root 755)
INTENT="$OWN_RUN_DIR/unit-froze"
FLAG="${AEGIS_FREEZE_FLAG:-$RUN_DIR/aegis-freeze}"

as_atlas() { runuser -u atlas -- "$@"; }
say() { printf 'atlas-aegis %s\n' "$*"; }
err() { printf 'atlas-aegis %s\n' "$*" >&2; }

orchestrator_up() {
  systemctl -q is-active atlas-orchestrator.service && systemctl -q is-active atlas-celery-cpu.service
}

cmd_freeze() {
  rm -f "$INTENT"
  if ! orchestrator_up; then
    say "freeze skipped: atlas-orchestrator or atlas-celery-cpu is inactive; taking a crash-consistent backup (Section 9.5 Freeze row needs a running orchestrator)"
    return 0
  fi
  [[ -d "$OWN_RUN_DIR" && ! -L "$OWN_RUN_DIR" ]] || { err "freeze: $OWN_RUN_DIR missing (RuntimeDirectory=atlas-aegis in the unit)"; return 1; }
  date -Is >"$INTENT"
  if ! as_atlas "$VENV/bin/atlas-admin" enqueue aegis-freeze --wait 900; then
    err "freeze FAILED with the orchestrator up: the unit stops here (no backup of an unfrozen store is reported as success); finish thaws"
    return 1
  fi
  say "freeze done: Celery gpu consumer paused, flag $FLAG raised"
}

cmd_manifests() {
  local d="$ATLAS_SRV/data/manifests" f rel n=0
  as_atlas mkdir -p -- "$d" || { err "manifests: cannot create $d as atlas"; return 1; }
  [[ -d "$d" && ! -L "$d" ]] || { err "manifests: $d is not a directory (a symlink?)"; return 1; }
  while IFS= read -r f; do
    [[ -f "$f" && ! -L "$f" ]] || continue
    rel="${f#"$ATLAS_SRV"/}"
    [[ "$rel" != /* && "$rel" != *..* ]] || continue
    as_atlas mkdir -p -- "$d/$(dirname "$rel")" || { err "manifests: mkdir $d/$(dirname "$rel") failed as atlas"; return 1; }
    [[ ! -L "$d/$rel" ]] || { err "manifests: $d/$rel is a symlink; refusing to write through it"; return 1; }
    as_atlas cp -f --no-preserve=all -- "$f" "$d/$rel" || { err "manifests: cp $f failed as atlas"; return 1; }
    n=$(( n + 1 ))
  done < <(find "$ATLAS_SRV/models" "$ATLAS_SRV/engines" -mindepth 2 -maxdepth 2 -type f -name MANIFEST.json 2>/dev/null)
  say "manifests copied into $d: $n"
}

thaw() {
  local ok=1 q
  for q in cpu gpu; do
    if ! as_atlas "$VENV/bin/celery" -A atlas.celery_app control --timeout 20 add_consumer "$q" >/dev/null 2>&1; then
      ok=0; err "thaw: 'celery control add_consumer $q' got no reply"
    fi
  done
  as_atlas rm -f -- "$FLAG" || err "thaw: could not remove $FLAG as atlas"
  if (( ok )); then
    if as_atlas "$VENV/bin/atlas-admin" enqueue aegis-thaw --wait 120 >/dev/null; then
      rm -f "$INTENT"
      say "thaw done: consumers resumed on cpu and gpu, flag $FLAG removed, aegis-thaw ledger row written"
      return 0
    fi
    err "thaw: consumers answered add_consumer but 'atlas-admin enqueue aegis-thaw --wait 120' failed"
  fi
  if systemctl restart atlas-celery-cpu.service atlas-celery-gpu.service; then
    rm -f "$INTENT"
    err "thaw FALLBACK: restarted atlas-celery-cpu and atlas-celery-gpu (fresh consumers, queues live); the unit is reported failed so this is seen"
  else
    err "thaw FAILED: workers did not resume and could not be restarted; the Celery queues may still be paused (flag $FLAG removed)"
  fi
  return 1
}

# notify_push MSG — ntfy push as root; never fails the caller. The token travels to curl through a process
# substitution (printf is a builtin), so it is on no argv (fix round 2; lib/common.sh's notify still uses -H on argv).
notify_push() {
  local msg="$1" tokf="$ATLAS_ETC/secrets/ntfy.env" tok="" topic="${NTFY_TOPIC:-atlas}" url="${NTFY_URL:-http://127.0.0.1:8090}"
  [[ -r "$tokf" ]] || { err "notify: $tokf unreadable; message was: $msg"; return 0; }
  tok="$(awk -F= '$1=="NTFY_TOKEN" {print $2; exit}' "$tokf" | tr -d "\"'")"
  [[ -n "$tok" ]] || { err "notify: no NTFY_TOKEN in $tokf; message was: $msg"; return 0; }
  if ! curl -sS --noproxy '*' --max-time 10 -o /dev/null \
       -H @<(printf 'Authorization: Bearer %s\n' "$tok") -H "Title: ATLAS aegis" -H "Priority: high" \
       --data-binary @<(printf '%s' "$msg") "$url/$topic" 2>/dev/null; then
    err "notify: push failed (ntfy down?); message was: $msg"
  fi
  return 0
}

cmd_finish() {
  local rc=0 result="${SERVICE_RESULT:-unknown}" status="${EXIT_STATUS:-?}"
  if [[ -e "$INTENT" ]]; then
    thaw || rc=1
  else
    say "finish: no freeze was requested by this run; nothing to thaw"
  fi
  [[ "$status" == 3 ]] && say "restic exit 3: some source files could not be read (names above); the snapshot exists"
  if [[ "$result" != success || $rc -ne 0 ]]; then
    notify_push "AEGIS backup on $(hostname) FAILED: systemd result=$result exit=$status, thaw=$([[ $rc -eq 0 ]] && echo ok || echo fallback/failed). See: journalctl -u atlas-aegis.service"
    err "finish: result=$result exit=$status thaw_rc=$rc (ntfy pushed)"
  else
    say "finish: backup result=$result exit=$status, thaw ok"
  fi
  return "$rc"
}

cmd_forget_result() {
  local result="${SERVICE_RESULT:-unknown}" status="${EXIT_STATUS:-?}"
  if [[ "$result" != success ]]; then
    notify_push "AEGIS retention (restic forget --prune) on $(hostname) FAILED: systemd result=$result exit=$status. See: journalctl -u atlas-aegis-forget.service"
    err "forget-result: result=$result exit=$status (ntfy pushed)"
    return 0   # the unit is already failed; this post hook only reports
  fi
  say "forget-result: retention prune ok"
}

case "${1:-}" in
  freeze)        cmd_freeze ;;
  manifests)     cmd_manifests ;;
  finish)        cmd_finish ;;
  forget-result) cmd_forget_result ;;
  notify)        shift; notify_push "$*" ;;
  *) err "usage: atlas-aegis freeze|manifests|finish|forget-result|notify MSG"; exit 2 ;;
esac
