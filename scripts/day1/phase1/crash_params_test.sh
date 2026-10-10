#!/usr/bin/env bash
# phase1/crash_params_test.sh — regression test (option (c) of 2026-10-10, doc S48) for phase1/04-system.sh's
# crash-kernel and panic changes: the GRUB drop-in that _grub_crash_dropin renders (zzz-atlas-crash.cfg), _grub_params
# with its post-update-grub checks, and _kdump_off; plus phase1/05-postboot.sh's _panic_log_record.
# The drop-in is sourced the way grub-mkconfig sources it: by dash, under `set -e`, through grub-mkconfig's own sourcing
# loop copied verbatim (grub2-common 2.14-2ubuntu2.1, /usr/sbin/grub-mkconfig lines 1-2 and 160-169), after the stock
# 26.04 /etc/default/grub lines, curtin's 50-curtin-settings.cfg, the 90-atlas.cfg _grub_params writes and the REAL
# /etc/default/grub.d/kdump-tools.cfg of kdump-tools 1:1.10.7ubuntu3 (resolute; its one line is copied below). Its
# output feeds a fake grub.cfg in the two kernel-line shapes of /etc/grub.d/10_linux (normal and advanced entries:
# GRUB_CMDLINE_LINUX then GRUB_CMDLINE_LINUX_DEFAULT; recovery: GRUB_CMDLINE_LINUX_RECOVERY then GRUB_CMDLINE_LINUX).
# dpkg-query, debconf-set-selections, debconf-show, systemctl, kdump-config, sysctl and findmnt are stubs. Touches
# nothing outside a fresh temp dir. Needs dash (Ubuntu's /bin/sh). Run: bash phase1/crash_params_test.sh
# shellcheck disable=SC2016  # file-wide: the single-quoted $ are shell text for dash and grub-mkconfig (the fixtures, the
#                              dash scripts, the ${CP_ROOT} that relocate() writes into the functions), meant unexpanded
set -Euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
T="$(mktemp -d)"
trap 'rm -rf -- "${T:?}"' EXIT
mkdir -p "$T/bin"
pass=0; fail=0
ok() { if [[ "$2" == "$3" ]]; then echo "PASS $1"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
extract() { awk -v n="$2" '$0 ~ "^"n"\\(\\) \\{" {on=1} on{print} on && /^\}/{exit}' "$1"; }
command -v dash >/dev/null || { echo "FAIL dash is not installed (grub-mkconfig runs under /bin/sh = dash on Ubuntu)"; exit 1; }

# --- Fixtures ------------------------------------------------------------------------------------------------------
# /etc/default/grub.d/kdump-tools.cfg from kdump-tools_1.10.7ubuntu3_amd64.deb (the whole file, one line).
KDUMP_CFG='GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT crashkernel=2G-4G:320M,4G-32G:512M,32G-64G:1024M,64G-128G:2048M,128G-:4096M"'
# The kernel-line lines of the stock /etc/default/grub (grub2-common 2.14-2ubuntu2.1, /usr/share/grub/default/grub).
STOCK_GRUB='GRUB_DEFAULT=0
GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=0
GRUB_DISTRIBUTOR=`( . /etc/os-release && echo ${NAME} )`
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""'
# The start of /etc/default/kdump-tools as kdump-tools' postinst renders it with debconf use_kdump=true (the template's
# lines 1-12 and its other settings; the real file differs from the use_kdump=false rendering in the USE_KDUMP line only).
KDUMP_DEFAULTS='# kdump-tools configuration
# ---------------------------------------------------------------------------
# USE_KDUMP - controls kdump will be configured
#     0 - kdump kernel will not be loaded
#     1 - kdump kernel will be loaded and kdump is configured
#
USE_KDUMP=1


# ---------------------------------------------------------------------------
# Kdump Kernel:
# KDUMP_KERNEL - A full pathname to a kdump kernel.
KDUMP_KERNEL=/var/lib/kdump/vmlinuz
KDUMP_INITRD=/var/lib/kdump/initrd.img
KDUMP_COREDIR="/var/crash"'

# grub-mkconfig's sourcing, verbatim (header), then the result and a grub.cfg in 10_linux's shapes. Writes grub.cfg on
# stdout and the two variables to $sysconfdir/result.
cat >"$T/mkconfig.sh" <<'SH'
#! /bin/sh
set -e
sysconfdir="$1"
gettext_printf() { fmt="$1"; shift; printf "$fmt" "$@"; }
# --- /usr/sbin/grub-mkconfig lines 160-169, verbatim ---
if test -f ${sysconfdir}/default/grub ; then
  gettext_printf "Sourcing file \`%s'\n" "${sysconfdir}/default/grub" 1>&2
  . ${sysconfdir}/default/grub
fi
for x in ${sysconfdir}/default/grub.d/*.cfg ; do
  if [ -e "${x}" ]; then
    gettext_printf "Sourcing file \`%s'\n" "${x}" 1>&2
    . "${x}"
  fi
done
# --- end of the verbatim lines ---
printf 'LINUX=[%s]\nDEFAULT=[%s]\n' "$GRUB_CMDLINE_LINUX" "$GRUB_CMDLINE_LINUX_DEFAULT" >"$sysconfdir/result"
printf "menuentry 'Ubuntu' {\n\tlinux\t/boot/vmlinuz-7.0.0-38-generic root=UUID=x ro %s\n}\n" "${GRUB_CMDLINE_LINUX} ${GRUB_CMDLINE_LINUX_DEFAULT}"
printf "submenu 'Advanced options for Ubuntu' {\n"
printf "\tmenuentry 'Ubuntu, with Linux 7.0.0-38-generic' {\n\t\tlinux\t/boot/vmlinuz-7.0.0-38-generic root=UUID=x ro %s\n\t}\n" "${GRUB_CMDLINE_LINUX} ${GRUB_CMDLINE_LINUX_DEFAULT}"
printf "\tmenuentry 'Ubuntu, with Linux 7.0.0-38-generic (recovery mode)' {\n\t\tlinux\t/boot/vmlinuz-7.0.0-38-generic root=UUID=x ro recovery nomodeset %s\n\t}\n}\n" "${GRUB_CMDLINE_LINUX_RECOVERY:-} ${GRUB_CMDLINE_LINUX}"
SH
# update-grub: grub-mkconfig -o /boot/grub/grub.cfg (it writes grub.cfg.new and renames it only on success).
cat >"$T/bin/update-grub" <<'SH'
#!/bin/sh
mkdir -p "$CP_ROOT/boot/grub"
dash "$CP_T/mkconfig.sh" "$CP_ROOT/etc" >"$CP_ROOT/boot/grub/grub.cfg.new" || exit 1
mv "$CP_ROOT/boot/grub/grub.cfg.new" "$CP_ROOT/boot/grub/grub.cfg"
SH
# dpkg-query -W -f='${db:Status-Abbrev}' kdump-tools: the status in $CP_ROOT/kdump_status, or "no packages found".
cat >"$T/bin/dpkg-query" <<'SH'
#!/bin/sh
if [ -s "$CP_ROOT/kdump_status" ]; then cat "$CP_ROOT/kdump_status"; exit 0; fi
echo "dpkg-query: no packages found matching kdump-tools" >&2; exit 1
SH
cat >"$T/bin/debconf-set-selections" <<'SH'
#!/bin/sh
[ -e "$CP_ROOT/debconf_fails" ] && { echo "debconf: DbDriver config: config.dat is locked by another process" >&2; exit 1; }
cat >>"$CP_ROOT/debconf"
SH
cat >"$T/bin/debconf-show" <<'SH'
#!/bin/sh
[ -s "$CP_ROOT/debconf" ] || exit 0
awk '$1 == "kdump-tools" && $2 == "kdump-tools/use_kdump" {v = $4} END {if (v != "") print "* kdump-tools/use_kdump: " v}' "$CP_ROOT/debconf"
SH
# systemctl: kdump-tools.service's state in $CP_ROOT/unit (enabled|disabled|masked|absent, absent-quiet = an is-enabled
# that prints nothing); systemd-pstore.service's in $CP_ROOT/pstore_unit. $CP_ROOT/mask_fails makes mask fail.
cat >"$T/bin/systemctl" <<'SH'
#!/bin/sh
echo "systemctl $*" >>"$CP_ROOT/calls"
unit="$(cat "$CP_ROOT/unit" 2>/dev/null || echo absent)"
case "$1" in
  disable)
    case "$unit" in absent*) echo "Failed to disable unit: Unit file kdump-tools.service does not exist." >&2; exit 1 ;;
                    enabled) echo disabled >"$CP_ROOT/unit" ;; esac ;;
  mask)
    [ -e "$CP_ROOT/mask_fails" ] && exit 1
    echo masked >"$CP_ROOT/unit" ;;
  is-enabled)
    if [ "$2" = systemd-pstore.service ]; then cat "$CP_ROOT/pstore_unit" 2>/dev/null; exit 0; fi
    case "$unit" in absent) echo not-found; exit 1 ;; absent-quiet) exit 1 ;; enabled) echo enabled; exit 0 ;;
                    *) echo "$unit"; exit 1 ;; esac ;;
esac
exit 0
SH
cat >"$T/bin/kdump-config" <<'SH'
#!/bin/sh
echo "kdump-config $*" >>"$CP_ROOT/calls"
[ "$1" = unload ] && echo 0 >"$CP_ROOT/sys/kernel/kexec_crash_loaded"
exit 0
SH
cat >"$T/bin/sysctl" <<'SH'
#!/bin/sh
echo "sysctl $*" >>"$CP_ROOT/calls"
SH
cat >"$T/bin/findmnt" <<'SH'
#!/bin/sh
[ -s "$CP_ROOT/findmnt" ] && { cat "$CP_ROOT/findmnt"; exit 0; }
exit 1
SH
chmod +x "$T/bin/"*
export PATH="$T/bin:$PATH" CP_T="$T" CP_ROOT="$T/none"

die() { echo "DIE: $*" >>"$CP_ROOT/out"; exit 99; }
log() { echo "LOG: $*" >>"$CP_ROOT/out"; }
warn() { echo "WARN: $*" >>"$CP_ROOT/out"; }
apt_wait_idle() { :; }
# The production functions, with every node path moved under $CP_ROOT (expanded at call time, one root per case).
# _grub_crash_dropin is taken unchanged: the test sources exactly what step 4 writes.
relocate() { sed -e 's#/etc/default/#${CP_ROOT}/etc/default/#g' -e 's#/boot/grub/#${CP_ROOT}/boot/grub/#g' \
                 -e 's#/sys/kernel/#${CP_ROOT}/sys/kernel/#g' -e 's#/sys/module/#${CP_ROOT}/sys/module/#g' \
                 -e 's#/sys/fs/pstore#${CP_ROOT}/sys/fs/pstore#g' -e 's#/var/lib/systemd/pstore#${CP_ROOT}/var/pstore#g'; }
# shellcheck source=/dev/null
source <(grep -E '^(export ATLAS_GRUB_PARAMS|ATLAS_GRUB_CRASH_DROPIN)=' "$REPO/phase1/04-system.sh"
         extract "$REPO/phase1/04-system.sh" _grub_crash_dropin
         for f in _kdump_off _grub_params; do extract "$REPO/phase1/04-system.sh" "$f" | relocate; done
         extract "$REPO/phase1/05-postboot.sh" _panic_log_record | relocate
         extract "$REPO/lib/common.sh" ensure_kv)
P="$ATLAS_GRUB_PARAMS"
DROPIN_NAME="${ATLAS_GRUB_CRASH_DROPIN##*/}"   # the production name: its byte order is part of what is tested (1j)
[[ "$ATLAS_GRUB_CRASH_DROPIN" == "/etc/default/grub.d/$DROPIN_NAME" && "$DROPIN_NAME" == *.cfg ]] \
  || { echo "FAIL ATLAS_GRUB_CRASH_DROPIN='$ATLAS_GRUB_CRASH_DROPIN' is not a *.cfg under /etc/default/grub.d"; exit 1; }
for f in _grub_crash_dropin _kdump_off _grub_params _panic_log_record ensure_kv; do
  declare -F "$f" >/dev/null || { echo "FAIL could not extract $f"; exit 1; }
done

# Every production call runs as step 4 and step 5 do: errexit, pipefail and an inherited ERR trap (lib/common.sh).
# strict FUNC — prints "RC:ERR-TRAP-LINES" (a die is rc 99 and no trap line).
strict() {
  : >"$CP_ROOT/err"
  ( set -Eeo pipefail; trap 'echo "ERR trap: line $LINENO: $BASH_COMMAND" >>"$CP_ROOT/err"' ERR; "$1" ); local rc=$?
  printf '%s:%s' "$rc" "$(cat "$CP_ROOT/err")"
}
n=0
new_root() {  # new_root — a fresh node tree for one case; sets CP_ROOT and ATLAS_GRUB_CRASH_DROPIN
  n=$((n + 1)); CP_ROOT="$T/c$n"
  mkdir -p "$CP_ROOT/etc/default/grub.d" "$CP_ROOT/boot/grub" "$CP_ROOT/sys/kernel"
  : >"$CP_ROOT/out"; : >"$CP_ROOT/calls"
  ATLAS_GRUB_CRASH_DROPIN="$CP_ROOT/etc/default/grub.d/$DROPIN_NAME"
}
grub_case() {  # grub_case DEFAULT LINUX [kdump] — a node whose /etc/default/grub carries these two values (single-quoted
               # there, so a value may hold double quotes, as /etc/default/grub would need)
  new_root
  { printf '%s\n' "$STOCK_GRUB" | grep -v '^GRUB_CMDLINE_LINUX'
    printf "GRUB_CMDLINE_LINUX_DEFAULT='%s'\nGRUB_CMDLINE_LINUX='%s'\n" "$1" "$2"; } >"$CP_ROOT/etc/default/grub"
  printf 'GRUB_DISABLE_OS_PROBER=true\n' >"$CP_ROOT/etc/default/grub.d/50-curtin-settings.cfg"
  if [[ "${3:-kdump}" == kdump ]]; then printf '%s\n' "$KDUMP_CFG" >"$CP_ROOT/etc/default/grub.d/kdump-tools.cfg"; fi
}
result() { sed -n "s/^$1=\\[\\(.*\\)\\]\$/\\1/p" "$CP_ROOT/etc/result"; }
words() { tr -s ' \t' ' ' <<<"$1" | sed -e 's/^ //' -e 's/ $//'; }   # whitespace runs as single spaces
first_linux() { grep -m1 -E '^[[:space:]]*linux[[:space:]]' "$CP_ROOT/boot/grub/grub.cfg"; }
recovery_linux() { grep -E '^[[:space:]]*linux[[:space:]].* recovery ' "$CP_ROOT/boot/grub/grub.cfg"; }

# --- 1. The drop-in through _grub_params and the grub-mkconfig harness ---------------------------------------------
# 1a. The node as installed: stock GRUB line, curtin's drop-in, kdump-tools.cfg. crashkernel= gone, panic=10 last.
grub_case "quiet splash" ""
ok "stock: _grub_params exits 0, no ERR trap" "$(strict _grub_params)" "0:"
ok "stock: GRUB_CMDLINE_LINUX_DEFAULT" "$(result DEFAULT)" "quiet splash $P panic=10"
ok "stock: GRUB_CMDLINE_LINUX" "$(result LINUX)" ""
ok "stock: no crashkernel= anywhere in grub.cfg" "$(grep -c 'crashkernel=' "$CP_ROOT/boot/grub/grub.cfg")" "0"
ok "stock: normal and advanced lines end in panic=10" "$(grep -cE '^[[:space:]]*linux[[:space:]].* panic=10$' "$CP_ROOT/boot/grub/grub.cfg")" "2"
ok "stock: recovery line has no panic= (kernel default, console attended)" "$(recovery_linux | grep -c 'panic=')" "0"
for p in $P panic=10; do
  ok "stock: $p exactly once on the first kernel line" "$(first_linux | tr -s '[:space:]' '\n' | grep -cxF -- "$p")" "1"
done
ok "stock: one log line naming panic=10" "$(grep -c "^LOG: GRUB: $P panic=10, no crashkernel=" "$CP_ROOT/out")" "1"
ok "stock: kdump-tools.cfg left untouched (a dpkg conffile)" "$(cat "$CP_ROOT/etc/default/grub.d/kdump-tools.cfg")" "$KDUMP_CFG"
ok "stock: /etc/default/grub left untouched" "$(grep -c "^GRUB_CMDLINE_LINUX_DEFAULT='quiet splash'\$" "$CP_ROOT/etc/default/grub")" "1"
# 1b. Rerun (rule §7.3): the same files, byte for byte, and the same kernel line.
cp "$ATLAS_GRUB_CRASH_DROPIN" "$CP_ROOT/dropin.1"; cp "$CP_ROOT/etc/default/grub.d/90-atlas.cfg" "$CP_ROOT/90.1"
cp "$CP_ROOT/boot/grub/grub.cfg" "$CP_ROOT/grub.cfg.1"
ok "rerun: exits 0" "$(strict _grub_params)" "0:"
ok "rerun: drop-in, 90-atlas.cfg and grub.cfg unchanged" \
   "$(cmp -s "$ATLAS_GRUB_CRASH_DROPIN" "$CP_ROOT/dropin.1" && cmp -s "$CP_ROOT/etc/default/grub.d/90-atlas.cfg" "$CP_ROOT/90.1" \
      && cmp -s "$CP_ROOT/boot/grub/grub.cfg" "$CP_ROOT/grub.cfg.1" && echo same)" "same"
ok "rerun: the drop-in is the rendered one" "$(cmp -s "$ATLAS_GRUB_CRASH_DROPIN" <(_grub_crash_dropin) && echo same)" "same"

# 1c. kdump-tools not installed: same line.
grub_case "quiet splash" "" none
ok "no kdump-tools.cfg: exits 0" "$(strict _grub_params)" "0:"
ok "no kdump-tools.cfg: DEFAULT" "$(result DEFAULT)" "quiet splash $P panic=10"

# 1d. crashkernel= in both variables (a hand edit), with ,high / ,low forms: all gone, other words kept.
grub_case "quiet crashkernel=512M splash" "crashkernel=1G-:256M,high foo=1 crashkernel=72M,low"
ok "crashkernel in both: exits 0" "$(strict _grub_params)" "0:"
ok "crashkernel in both: LINUX" "$(result LINUX)" "foo=1"
ok "crashkernel in both: DEFAULT" "$(words "$(result DEFAULT)")" "quiet splash $P panic=10"

# 1e. panic=0 and panic=5 already present: removed; panic=10 is the only panic= word.
grub_case "panic=0 quiet" "panic=5"
ok "panic=0/panic=5 present: exits 0" "$(strict _grub_params)" "0:"
ok "panic=0/panic=5 present: LINUX" "$(result LINUX)" ""
ok "panic=0/panic=5 present: DEFAULT" "$(result DEFAULT)" "quiet $P panic=10"

# 1f. Words that merely contain "panic" stay: oops=panic, panic_on_warn=1, nopanic, xpanic=3.
grub_case "quiet nopanic xpanic=3" "oops=panic panic_on_warn=1"
ok "oops=panic etc.: exits 0" "$(strict _grub_params)" "0:"
ok "oops=panic etc.: LINUX kept" "$(result LINUX)" "oops=panic panic_on_warn=1"
ok "oops=panic etc.: DEFAULT kept" "$(result DEFAULT)" "quiet nopanic xpanic=3 $P panic=10"

# 1g. Extra spaces and tabs around the words: whole words removed, no leading or trailing blank left.
grub_case $'  quiet\t\tsplash   panic=3  ' $'\t crashkernel=256M   a=1 '
ok "spaces and tabs: exits 0" "$(strict _grub_params)" "0:"
ok "spaces and tabs: LINUX" "$(result LINUX)" "a=1"
ok "spaces and tabs: DEFAULT words" "$(words "$(result DEFAULT)")" "quiet splash $P panic=10"
ok "spaces and tabs: no blank at either end" "$(result DEFAULT | grep -cE '^[[:space:]]|[[:space:]]$')" "0"

# 1h. A quoted value with spaces is one word to the kernel (kernel/params.c next_arg) and stays whole.
grub_case 'quiet dyndbg="module amdgpu +p"' 'foo="a b"'
ok "quoted values: exits 0" "$(strict _grub_params)" "0:"
ok "quoted values: LINUX" "$(result LINUX)" 'foo="a b"'
ok "quoted values: DEFAULT" "$(result DEFAULT)" "quiet dyndbg=\"module amdgpu +p\" $P panic=10"

# 1i. An empty /etc/default/grub (both variables start unset; step 4 needs the file itself, which grub ships).
grub_case "" ""; : >"$CP_ROOT/etc/default/grub"
ok "empty /etc/default/grub: exits 0" "$(strict _grub_params)" "0:"
ok "empty /etc/default/grub: DEFAULT" "$(result DEFAULT)" "$P panic=10"
ok "empty /etc/default/grub: LINUX" "$(result LINUX)" ""

# 1j. zz-kdump-tools.cfg (the DGX crashkernel package's name) sorts before zzz-atlas-crash.cfg in dash's glob: stripped.
grub_case "quiet splash" ""
printf 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT crashkernel=1G-:1G"\n' >"$CP_ROOT/etc/default/grub.d/zz-kdump-tools.cfg"
ok "zz-kdump-tools.cfg: exits 0" "$(strict _grub_params)" "0:"
ok "zz-kdump-tools.cfg: stripped" "$(grep -c 'crashkernel=' "$CP_ROOT/boot/grub/grub.cfg")" "0"

# 1k. A drop-in sorting AFTER ours puts crashkernel= back: step 4 stops with the reason, naming where to look.
grub_case "quiet splash" ""
printf 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT crashkernel=256M"\n' >"$CP_ROOT/etc/default/grub.d/zzzz-local.cfg"
r="$(strict _grub_params)"
ok "later crashkernel drop-in: dies" "${r%%:*}" "99"
ok "later crashkernel drop-in: names the lines (normal and advanced entries)" \
   "$(grep -c 'grub.cfg still carries crashkernel= (lines 2 6) after update-grub' "$CP_ROOT/out")" "1"
ok "later crashkernel drop-in: says where to look" "$(grep -c 'Look for a drop-in that sorts after zzz-atlas-crash.cfg' "$CP_ROOT/out")" "1"

# 1l. A drop-in sorting after ours adds panic=0: step 4 stops.
grub_case "quiet splash" ""
printf 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT panic=0"\n' >"$CP_ROOT/etc/default/grub.d/zzzz-local.cfg"
r="$(strict _grub_params)"
ok "later panic=0 drop-in: dies naming both tokens" "${r%%:*}:$(grep -c "carries the panic tokens 'panic=10 panic=0', expected exactly one panic=10" "$CP_ROOT/out")" "99:1"

# 1m. crashkernel= inside a quoted value: the drop-in leaves the word alone, but the kernel's strstr would still find it,
#     so the substring check stops step 4.
grub_case "quiet splash" 'dyndbg="file x.c crashkernel=1G"'
r="$(strict _grub_params)"
ok "crashkernel= inside a quoted value: dies" "${r%%:*}:$(grep -c 'still carries crashkernel=' "$CP_ROOT/out")" "99:1"

# 1n. A broken drop-in elsewhere (fails under set -e) makes update-grub fail: step 4 stops and says how to find it.
grub_case "quiet splash" ""
printf 'X=1\n[ -n "" ] && X=2\n' >"$CP_ROOT/etc/default/grub.d/60-broken.cfg"
r="$(strict _grub_params)"
ok "broken drop-in: update-grub failure stops step 4" "${r%%:*}:$(grep -c '^DIE: update-grub failed' "$CP_ROOT/out")" "99:1"

# --- 2. The drop-in on its own, under dash and set -e ---------------------------------------------------------------
new_root
_grub_crash_dropin >"$CP_ROOT/zzz.cfg"
ok "drop-in: last line is ':'" "$(tail -n1 "$CP_ROOT/zzz.cfg")" ":"
ok "drop-in: no grep, no && or || (each can abort grub-mkconfig under set -e)" \
   "$(grep -v '^#' "$CP_ROOT/zzz.cfg" | grep -cE 'grep|&&|\|\|')" "0"
cat >"$CP_ROOT/source.sh" <<'SH'
set -e
GRUB_CMDLINE_LINUX_DEFAULT="$2"; GRUB_CMDLINE_LINUX="$3"
. "$1"
printf '%s|%s\n' "$GRUB_CMDLINE_LINUX_DEFAULT" "$GRUB_CMDLINE_LINUX"
SH
src() { dash "$CP_ROOT/source.sh" "$CP_ROOT/zzz.cfg" "$1" "$2"; }
ok "alone: empty variables" "$(src "" "")" "panic=10|"
ok "alone: quoted value containing ' panic=' kept whole" "$(src 'foo="a panic=3 b" q' '')" 'foo="a panic=3 b" q panic=10|'
ok "alone: quoted panic words removed" "$(src '"panic=5" panic="7" "panic=6"x z' '')" "z panic=10|"
ok "alone: crashkernel inside a quoted value left (the substring check catches it)" \
   "$(src 'bar="x crashkernel=1 y"' '')" 'bar="x crashkernel=1 y" panic=10|'
ok "alone: sourced twice, one panic=10" \
   "$(dash -c 'set -e; GRUB_CMDLINE_LINUX_DEFAULT="a panic=1"; . "$1"; . "$1"; printf %s "$GRUB_CMDLINE_LINUX_DEFAULT"' sh "$CP_ROOT/zzz.cfg")" \
   "a panic=10"
ok "alone: same under POSIXLY_CORRECT (no GNU sed escapes)" \
   "$(POSIXLY_CORRECT=1 src 'dyndbg="m a +p" "panic=5" crashkernel=4G b' 'panic=1 x')" 'dyndbg="m a +p" b panic=10|x'
ok "alone: same under bash (grub-efi-amd64's postinst sources grub.d with bash -e)" \
   "$(bash "$CP_ROOT/source.sh" "$CP_ROOT/zzz.cfg" 'q crashkernel=1 panic=2' 'panic=3')" "q panic=10|"
ok "alone: under set -u too" \
   "$(dash -c 'set -eu; . "$1"; printf %s "$GRUB_CMDLINE_LINUX_DEFAULT|$GRUB_CMDLINE_LINUX"' sh "$CP_ROOT/zzz.cfg")" "panic=10|"
# Nothing leaks into grub-mkconfig's shell but the two variables: same variables otherwise, same shell options.
cat >"$CP_ROOT/leak.sh" <<'SH'
set -e
GRUB_CMDLINE_LINUX_DEFAULT="quiet crashkernel=1"; GRUB_CMDLINE_LINUX="panic=1"
set | grep -v '^GRUB_CMDLINE_LINUX' >"$2.before"; set +o >"$2.opts.before"
. "$1"
set | grep -v '^GRUB_CMDLINE_LINUX' >"$2.after"; set +o >"$2.opts.after"
SH
dash "$CP_ROOT/leak.sh" "$CP_ROOT/zzz.cfg" "$CP_ROOT/leak"
ok "alone: no variable leaked" "$(diff "$CP_ROOT/leak.before" "$CP_ROOT/leak.after" | grep -c '^[<>]')" "0"
ok "alone: shell options unchanged" "$(cmp -s "$CP_ROOT/leak.opts.before" "$CP_ROOT/leak.opts.after" && echo same)" "same"
# The harness reproduces grub-mkconfig's set -e (research negative cases): a drop-in ending in a false && list, or a
# command substitution whose last command (grep) finds nothing, aborts it. The drop-in is shaped to avoid both.
neg() {  # neg NAME CONTENT — neg_rc: the harness's exit status with only that drop-in, in a fresh tree
  new_root; printf '%s\n' "$2" >"$CP_ROOT/etc/default/grub.d/$1.cfg"
  neg_rc=0; dash "$T/mkconfig.sh" "$CP_ROOT/etc" >/dev/null 2>&1 || neg_rc=$?
}
neg x '[ -n "" ] && X=2'
ok "harness: a last '[ -n \"\" ] && X=2' aborts" "$neg_rc" "1"
neg x "X=\$(printf 'a\\n' | grep -v a)"
ok "harness: X=\$(... | grep -v ...) with no output aborts" "$neg_rc" "1"
neg "${DROPIN_NAME%.cfg}" "$(_grub_crash_dropin)"
ok "harness: the rendered drop-in alone succeeds" "$neg_rc:$(result DEFAULT)" "0:panic=10"

# --- 3. _kdump_off -------------------------------------------------------------------------------------------------
kd_case() {  # kd_case STATUS UNIT [loaded] [crash_size] — STATUS "" means not installed
  new_root
  printf '%s' "$1" >"$CP_ROOT/kdump_status"; printf '%s\n' "$2" >"$CP_ROOT/unit"
  echo "${3:-0}" >"$CP_ROOT/sys/kernel/kexec_crash_loaded"
  if [[ "${4-0}" != none ]]; then echo "${4:-0}" >"$CP_ROOT/sys/kernel/kexec_crash_size"; fi
  printf '%s\n' "$KDUMP_DEFAULTS" >"$CP_ROOT/etc/default/kdump-tools"
}
# 3a. As installed (debconf true, USE_KDUMP=1, unit enabled) with a crash kernel loaded and 4.25 GiB reserved.
kd_case "ii " enabled 1 4563402752
ok "installed: exits 0, no ERR trap" "$(strict _kdump_off)" "0:"
ok "installed: debconf answer false" "$(debconf-show kdump-tools)" "* kdump-tools/use_kdump: false"
ok "installed: only the USE_KDUMP line changed (= the use_kdump=false rendering, so ucf will not prompt)" \
   "$(diff <(printf '%s\n' "$KDUMP_DEFAULTS") "$CP_ROOT/etc/default/kdump-tools" | grep '^[<>]' | paste -sd'|')" "< USE_KDUMP=1|> USE_KDUMP=0"
ok "installed: crash kernel unloaded" "$(grep -c '^kdump-config unload$' "$CP_ROOT/calls")" "1"
ok "installed: panic_on_oops reset" "$(grep -c '^sysctl -q -w kernel.panic_on_oops=0$' "$CP_ROOT/calls")" "1"
ok "installed: disabled, then masked" "$(grep -E '^systemctl (disable|mask)' "$CP_ROOT/calls" | paste -sd'|')" \
   "systemctl disable --now kdump-tools.service|systemctl mask kdump-tools.service"
ok "installed: unit masked" "$(cat "$CP_ROOT/unit")" "masked"
ok "installed: kdump-tools-dump.service never touched" "$(grep -c 'kdump-tools-dump' "$CP_ROOT/calls")" "0"
ok "installed: one log line with what was done and the memory that comes back" \
   "$(grep -c '^LOG: kdump off, not purged (option (c), S48): debconf use_kdump=false USE_KDUMP=0 in .* loaded crash kernel unloaded, kernel.panic_on_oops=0 kdump-tools.service masked; 4.25 GiB (4563402752 bytes) reserved for a crash kernel on this boot, back in MemTotal after the reboot' "$CP_ROOT/out")" "1"
ok "installed: no warning" "$(grep -c '^WARN' "$CP_ROOT/out")" "0"
# 3b. Rerun: nothing loaded now, the file not rewritten (same inode and content), same final state.
cp "$CP_ROOT/etc/default/kdump-tools" "$CP_ROOT/kdump-tools.1"; ino="$(stat -c %i "$CP_ROOT/etc/default/kdump-tools")"
: >"$CP_ROOT/calls"; : >"$CP_ROOT/out"
ok "rerun: exits 0" "$(strict _kdump_off)" "0:"
ok "rerun: file not rewritten" "$(cmp -s "$CP_ROOT/kdump-tools.1" "$CP_ROOT/etc/default/kdump-tools" && stat -c %i "$CP_ROOT/etc/default/kdump-tools")" "$ino"
ok "rerun: no unload" "$(grep -c '^kdump-config' "$CP_ROOT/calls")" "0"
ok "rerun: still masked, debconf still false" "$(cat "$CP_ROOT/unit"):$(debconf-show kdump-tools)" "masked:* kdump-tools/use_kdump: false"
# 3c. Not installed: nothing touched, one log line; the drop-in still does its job.
kd_case "" absent 0 0
ok "not installed: exits 0" "$(strict _kdump_off)" "0:"
ok "not installed: no debconf, no systemctl" "$( [[ -e "$CP_ROOT/debconf" ]] && echo debconf; cat "$CP_ROOT/calls")" ""
ok "not installed: logged" "$(grep -c '^LOG: kdump: kdump-tools is not installed, nothing to switch off; .*zzz-atlas-crash.cfg still removes any crashkernel= and sets panic=10 (S48); no crash-kernel memory reserved on this boot' "$CP_ROOT/out")" "1"
# 3d. Installed but the unit is absent (disable fails, is-enabled says not-found, or nothing at all): not an error.
kd_case "ii " absent; touch "$CP_ROOT/mask_fails"
ok "unit absent (not-found): exits 0" "$(strict _kdump_off)" "0:"
ok "unit absent (not-found): logged" "$(grep -c 'kdump-tools.service not-found;' "$CP_ROOT/out")" "1"
kd_case "ii " absent-quiet; touch "$CP_ROOT/mask_fails"
ok "unit absent (no output): exits 0" "$(strict _kdump_off)" "0:"
ok "unit absent (no output): logged" "$(grep -c 'kdump-tools.service absent;' "$CP_ROOT/out")" "1"
# 3e. No /etc/default/kdump-tools: not created (ucf recreates it from debconf, now false).
kd_case "ii " enabled; rm -f "$CP_ROOT/etc/default/kdump-tools"
ok "no defaults file: exits 0" "$(strict _kdump_off)" "0:"
ok "no defaults file: not created, said so" "$( [[ -e "$CP_ROOT/etc/default/kdump-tools" ]] && echo created; grep -c 'no .*/etc/default/kdump-tools (ucf recreates it' "$CP_ROOT/out")" "1"
# 3f. A defaults file without a USE_KDUMP line: one appended. Two differing lines: both set to 0.
kd_case "ii " enabled; grep -v '^USE_KDUMP' <<<"$KDUMP_DEFAULTS" >"$CP_ROOT/etc/default/kdump-tools"
ok "no USE_KDUMP line: exits 0" "$(strict _kdump_off)" "0:"
ok "no USE_KDUMP line: appended" "$(grep -c '^USE_KDUMP=0$' "$CP_ROOT/etc/default/kdump-tools"):$(tail -n1 "$CP_ROOT/etc/default/kdump-tools")" "1:USE_KDUMP=0"
kd_case "ii " enabled; printf '  USE_KDUMP="1"\n' >>"$CP_ROOT/etc/default/kdump-tools"
ok "two USE_KDUMP lines: exits 0" "$(strict _kdump_off)" "0:"
ok "two USE_KDUMP lines: both 0" "$(grep -cE '^[[:space:]]*USE_KDUMP=' "$CP_ROOT/etc/default/kdump-tools"):$(grep -c '^USE_KDUMP=0$' "$CP_ROOT/etc/default/kdump-tools")" "2:2"
# 3g. Held ("hi") and removed-with-conffiles ("rc", kdump-tools.cfg still on disk) count as present.
kd_case "hi " enabled
ok "held: switched off" "$(strict _kdump_off):$(cat "$CP_ROOT/unit")" "0::masked"
kd_case "rc " absent
ok "removed, conffiles left: debconf and file still set" "$(strict _kdump_off):$(debconf-show kdump-tools):$(grep -c '^USE_KDUMP=0$' "$CP_ROOT/etc/default/kdump-tools")" \
   "0::* kdump-tools/use_kdump: false:1"
# 3h. Failures stop the step loudly: the unit stays enabled after mask, or debconf cannot be written.
kd_case "ii " enabled; touch "$CP_ROOT/mask_fails"; printf '#!/bin/sh\necho "systemctl $*" >>"$CP_ROOT/calls"\n[ "$1" = is-enabled ] && echo enabled\nexit 0\n' >"$T/bin/systemctl.stuck"
chmod +x "$T/bin/systemctl.stuck"; mv "$T/bin/systemctl" "$T/bin/systemctl.real"; mv "$T/bin/systemctl.stuck" "$T/bin/systemctl"
r="$(strict _kdump_off)"
ok "unit still enabled: dies" "${r%%:*}:$(grep -c "^DIE: kdump-tools.service is still 'enabled' after systemctl disable and mask" "$CP_ROOT/out")" "99:1"
mv "$T/bin/systemctl" "$T/bin/systemctl.stuck"; mv "$T/bin/systemctl.real" "$T/bin/systemctl"
kd_case "ii " enabled; touch "$CP_ROOT/debconf_fails"
r="$(strict _kdump_off 2>/dev/null)"
ok "debconf locked: dies" "${r%%:*}:$(grep -c '^DIE: debconf-set-selections could not set kdump-tools/use_kdump=false' "$CP_ROOT/out")" "99:1"
# 3i. The reservation unreadable: said so, not guessed.
kd_case "ii " enabled 0 none
ok "crash size unreadable: exits 0" "$(strict _kdump_off)" "0:"
ok "crash size unreadable: said so" "$(grep -c 'crash-kernel reservation unreadable' "$CP_ROOT/out")" "1"

# --- 4. _panic_log_record (step 5): read-only, never a stop ----------------------------------------------------------
pl_case() {  # pl_case BACKEND PSTORE_UNIT KDUMP_UNIT
  new_root
  mkdir -p "$CP_ROOT/sys/module/pstore/parameters" "$CP_ROOT/sys/fs/pstore" "$CP_ROOT/var/pstore"
  printf '%s\n' "$1" >"$CP_ROOT/sys/module/pstore/parameters/backend"
  echo 10240 >"$CP_ROOT/sys/module/pstore/parameters/kmsg_bytes"
  echo pstore >"$CP_ROOT/findmnt"; echo "$2" >"$CP_ROOT/pstore_unit"; echo "$3" >"$CP_ROOT/unit"
  printf 'USE_KDUMP=0\n' >"$CP_ROOT/etc/default/kdump-tools"
}
pl_case efi_pstore enabled masked
mkdir -p "$CP_ROOT/sys/module/efi_pstore/parameters"; echo N >"$CP_ROOT/sys/module/efi_pstore/parameters/pstore_disable"
ok "pstore: exits 0" "$(strict _panic_log_record)" "0:"
ok "pstore: log line" "$(grep -c '^LOG: panic log: pstore backend efi_pstore, kmsg_bytes 10240, efi_pstore loaded (pstore_disable=N);.* pstore with 0 record(s); systemd-pstore.service enabled; 0 archived under .*; kdump-tools.service masked, USE_KDUMP=0 (option (c), S48)$' "$CP_ROOT/out")" "1"
ok "pstore: no warning" "$(grep -c '^WARN' "$CP_ROOT/out")" "0"
pl_case "(null)" enabled masked
ok "no backend: exits 0" "$(strict _panic_log_record)" "0:"
ok "no backend: warned" "$(grep -c '^WARN: panic log: no pstore backend is registered' "$CP_ROOT/out")" "1"
pl_case erst enabled enabled
: >"$CP_ROOT/sys/fs/pstore/dmesg-erst-1"; : >"$CP_ROOT/var/pstore/a"; : >"$CP_ROOT/var/pstore/b"
ok "records and kdump back on: exits 0" "$(strict _panic_log_record)" "0:"
ok "records: counted, the live one left in place" "$(grep -c 'with 1 record(s);.* 2 archived' "$CP_ROOT/out"):$(ls "$CP_ROOT/sys/fs/pstore")" "1:dmesg-erst-1"
ok "records and kdump back on: two warnings" "$(grep -c '^WARN' "$CP_ROOT/out")" "2"
ok "never writes: only is-enabled queries" "$(grep -vc ' is-enabled ' "$CP_ROOT/calls")" "0"

echo "crash_params_test: $pass passed, $fail failed"
(( fail == 0 ))
