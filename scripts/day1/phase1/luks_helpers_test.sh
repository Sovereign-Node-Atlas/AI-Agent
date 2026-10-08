#!/usr/bin/env bash
# phase1/luks_helpers_test.sh — regression test (review v0.3.4, doc S39) for phase1/02-luks.sh's _luks_os_mapping,
# _luks_os_device and phase1_crypttab_tpm2, and lib/common.sh's todo_is_open, with stubbed findmnt/lsblk/cryptsetup.
# The stub lsblk draws tree prefixes unless -l is given, exactly as util-linux does into a pipe, so dropping -l from
# the helpers fails here instead of on the first encrypted-LVM install. Touches nothing outside a fresh temp dir.
# Run: bash phase1/luks_helpers_test.sh   (exit 0 when every case passes)
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin" "$T/etc" "$T/state"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }

cat >"$T/bin/findmnt" <<'SH'
#!/bin/sh
echo "/dev/mapper/ubuntu--vg-ubuntu--lv"
SH
# lsblk prints what LSBLK_MODE selects; with -l it must drop the tree prefixes, without -l it draws them (the bug).
cat >"$T/bin/lsblk" <<'SH'
#!/bin/bash
list=0; for a in "$@"; do [[ "$a" == -*l* && "$a" != --* ]] && list=1; done
case "$LSBLK_MODE" in
  lvm-crypt)
    if (( list )); then printf 'ubuntu--vg-ubuntu--lv lvm\ndm_crypt-0 crypt\nnvme0n1p3 part\nnvme0n1 disk\n'
    else printf 'ubuntu--vg-ubuntu--lv lvm\n\xe2\x94\x94\xe2\x94\x80dm_crypt-0 crypt\n  \xe2\x94\x94\xe2\x94\x80nvme0n1p3 part\n    \xe2\x94\x94\xe2\x94\x80nvme0n1 disk\n'; fi ;;
  plain)
    if (( list )); then printf 'ubuntu--vg-ubuntu--lv lvm\nnvme0n1p3 part\nnvme0n1 disk\n'
    else printf 'ubuntu--vg-ubuntu--lv lvm\n\xe2\x94\x94\xe2\x94\x80nvme0n1p3 part\n'; fi ;;
esac
SH
cat >"$T/bin/cryptsetup" <<'SH'
#!/bin/bash
if [[ "$1" == status && "$2" == dm_crypt-0 && "$CS_MODE" == ok ]]; then
  printf '/dev/mapper/dm_crypt-0 is active.\n  type:    LUKS2\n  device:  %s\n' "$CS_DEV"; exit 0
fi
if [[ "$1" == luksDump ]]; then echo "Tokens: 0: systemd-tpm2"; exit 0; fi
exit 4
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH"
die() { echo "DIE: $*"; exit 99; }      # exits: every call that may die runs in a subshell below
log() { :; }; warn() { :; }
# shellcheck source=/dev/null
# extract FILE NAME... — print each named top-level function once (a one-line "f() { ...; }" ends on its own line).
extract() {
  local f="$1"; shift
  awk -v names=" $* " '
    !on && match($0, /^[A-Za-z_][A-Za-z0-9_]*\(\) \{/) {
      n = substr($0, 1, index($0, "(") - 1)
      if (index(names, " " n " ")) { on = 1; print; if ($0 ~ /\}[[:space:]]*$/) on = 0; next }
    }
    on { print; if ($0 ~ /^\}/) on = 0 }' "$f"
}
# shellcheck source=/dev/null
source <(extract "$REPO/phase1/02-luks.sh" _luks_has_token _luks_is_blockdev _luks_os_mapping _luks_os_device phase1_crypttab_tpm2 \
  | sed "s#/etc/crypttab#$T/etc/crypttab#g")
export ATLAS_LUKS_MAPPING=atlas-data   # read by phase1_crypttab_tpm2 (sourced above)
# Block devices are stubbed: CS_DEV names a fake device that _luks_is_blockdev accepts, so the cases always run.
_luks_is_blockdev() { [[ -n "$1" && "$1" == "${FAKE_BLOCKDEV:-}" ]]; }
FAKE_BLOCKDEV=/dev/fake-nvme0n1p3

LSBLK_MODE=lvm-crypt; export LSBLK_MODE
ok "mapping on encrypted LVM (tree output avoided)" "$(_luks_os_mapping)" "dm_crypt-0"
CS_MODE=ok; CS_DEV=/dev/not-a-block-device; export CS_MODE CS_DEV
out="$(_luks_os_device 2>/dev/null)"; rc=$?
ok "unresolvable device returns 1, never 'unencrypted'" "$rc:$out" "1:"
CS_DEV="$FAKE_BLOCKDEV"
ok "resolved device on encrypted LVM" "$(_luks_os_device)" "$FAKE_BLOCKDEV"
LSBLK_MODE=plain
out="$(_luks_os_device)"; rc=$?
ok "unencrypted root: empty, rc 0" "$rc:$out" "0:"

# crypttab: data line and OS line gain tpm2-device=auto once, comments untouched, idempotent
cat >"$T/etc/crypttab" <<'CT'
# comment atlas-data UUID=x none nofail
dm_crypt-0 UUID=os-uuid none luks,discard,x-initrd.attach
atlas-data UUID=data-uuid none nofail,headless=true,discard
other UUID=z none luks
CT
cp "$T/etc/crypttab" "$T/orig"
LSBLK_MODE=lvm-crypt; CS_MODE=ok; CS_DEV=/dev/not-a-block-device
out="$( ( phase1_crypttab_tpm2 ) 2>&1 )"; rc=$?
ok "unresolvable OS device: step stops" "$rc" "99"
ok "unresolvable OS device: crypttab unchanged" "$(cmp -s "$T/etc/crypttab" "$T/orig" && echo same)" "same"
CS_DEV="$FAKE_BLOCKDEV"
( phase1_crypttab_tpm2 ) >/dev/null; cp "$T/etc/crypttab" "$T/once"; ( phase1_crypttab_tpm2 ) >/dev/null
ok "crypttab idempotent" "$(cmp -s "$T/etc/crypttab" "$T/once" && echo same)" "same"
ok "data line" "$(grep '^atlas-data' "$T/etc/crypttab")" "atlas-data UUID=data-uuid none nofail,headless=true,discard,tpm2-device=auto"
ok "OS line" "$(grep '^dm_crypt-0' "$T/etc/crypttab")" "dm_crypt-0 UUID=os-uuid none luks,discard,x-initrd.attach,tpm2-device=auto"
ok "other line untouched" "$(grep '^other' "$T/etc/crypttab")" "other UUID=z none luks"
ok "comment untouched" "$(head -n1 "$T/etc/crypttab")" "# comment atlas-data UUID=x none nofail"
LSBLK_MODE=plain
printf 'atlas-data UUID=d none nofail\n' >"$T/etc/crypttab"
( phase1_crypttab_tpm2 ) >/dev/null
ok "unencrypted root: data line only" "$(cat "$T/etc/crypttab")" "atlas-data UUID=d none nofail,tpm2-device=auto"

# todo_is_open (lib/common.sh)
# shellcheck source=/dev/null
source <(extract "$REPO/lib/common.sh" todo_is_open)
ATLAS_TODO_FILE="$T/state/todo.jsonl"
todo_is_open rdp-restrict; ok "no file -> closed" "$?" "1"
printf '{"id": "rdp-restrict", "done": false}\n' >"$ATLAS_TODO_FILE"
todo_is_open rdp-restrict; ok "open" "$?" "0"
printf '{"id": "rdp-restrict", "done": true}\n' >>"$ATLAS_TODO_FILE"
todo_is_open rdp-restrict; ok "closed after done" "$?" "1"
todo_is_open time-sync; ok "other id -> closed" "$?" "1"
echo "luks_helpers_test: $pass passed, $fail failed"
(( fail == 0 ))
