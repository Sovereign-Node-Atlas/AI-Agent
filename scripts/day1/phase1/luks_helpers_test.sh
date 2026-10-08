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
die() { echo "DIE: $*"; return 99; }
log() { :; }; warn() { :; }
# shellcheck source=/dev/null
source <(sed -n '/^_luks_has_token() {/,/^}/p; /^_luks_os_mapping() {/,/^}/p; /^_luks_os_device() {/,/^}/p; /^phase1_crypttab_tpm2() {/,/^}/p' "$REPO/phase1/02-luks.sh" | sed "s#/etc/crypttab#$T/etc/crypttab#g")
export ATLAS_LUKS_MAPPING=atlas-data   # read by phase1_crypttab_tpm2 (sourced above)

LSBLK_MODE=lvm-crypt; export LSBLK_MODE
ok "mapping on encrypted LVM (tree output avoided)" "$(_luks_os_mapping)" "dm_crypt-0"
CS_MODE=ok; CS_DEV=/dev/null; export CS_MODE CS_DEV   # /dev/null is not a block device
out="$(_luks_os_device 2>/dev/null)"; rc=$?
ok "unresolvable device returns 1, never 'unencrypted'" "$rc:$out" "1:"
CS_DEV=""; for d in /dev/loop0 /dev/sda /dev/vda /dev/nvme0n1; do [[ -b "$d" ]] && { CS_DEV="$d"; break; }; done; export CS_DEV
if [[ -b "$CS_DEV" ]]; then
  ok "resolved device on encrypted LVM" "$(_luks_os_device)" "$CS_DEV"
else
  echo "SKIP resolved-device case: no block device in this container"
fi
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
LSBLK_MODE=lvm-crypt; CS_MODE=ok
if [[ -b "$CS_DEV" ]]; then
  phase1_crypttab_tpm2; cp "$T/etc/crypttab" "$T/once"; phase1_crypttab_tpm2
  ok "crypttab idempotent" "$(cmp -s "$T/etc/crypttab" "$T/once" && echo same)" "same"
  ok "data line" "$(grep '^atlas-data' "$T/etc/crypttab")" "atlas-data UUID=data-uuid none nofail,headless=true,discard,tpm2-device=auto"
  ok "OS line" "$(grep '^dm_crypt-0' "$T/etc/crypttab")" "dm_crypt-0 UUID=os-uuid none luks,discard,x-initrd.attach,tpm2-device=auto"
  ok "other line untouched" "$(grep '^other' "$T/etc/crypttab")" "other UUID=z none luks"
  ok "comment untouched" "$(head -n1 "$T/etc/crypttab")" "# comment atlas-data UUID=x none nofail"
fi
LSBLK_MODE=plain
printf 'atlas-data UUID=d none nofail\n' >"$T/etc/crypttab"
phase1_crypttab_tpm2
ok "unencrypted root: data line only" "$(cat "$T/etc/crypttab")" "atlas-data UUID=d none nofail,tpm2-device=auto"

# todo_is_open (lib/common.sh)
# shellcheck source=/dev/null
source <(sed -n '/^todo_is_open() {/,/^}/p' "$REPO/lib/common.sh")
ATLAS_TODO_FILE="$T/state/todo.jsonl"
todo_is_open rdp-restrict; ok "no file -> closed" "$?" "1"
printf '{"id": "rdp-restrict", "done": false}\n' >"$ATLAS_TODO_FILE"
todo_is_open rdp-restrict; ok "open" "$?" "0"
printf '{"id": "rdp-restrict", "done": true}\n' >>"$ATLAS_TODO_FILE"
todo_is_open rdp-restrict; ok "closed after done" "$?" "1"
todo_is_open time-sync; ok "other id -> closed" "$?" "1"
echo "luks_helpers_test: $pass passed, $fail failed"
(( fail == 0 ))
