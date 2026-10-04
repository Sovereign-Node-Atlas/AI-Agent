#!/usr/bin/env bash
# phase1/05b-desktop.sh — Phase 1 step 5b (Sections 3.1, 3.6, 17, 21 V19; R21; Appendix B): XFCE and xrdp bound to
# the LAN address now (the WireGuard bridge address is added by step 7), no display manager and no autologin,
# Firefox as a .deb from Mozilla's apt repository (adjudicated conflict 5), then V19: (a) xrdp listens only on the
# bound addresses, (b) wait up to 10 minutes for the Principal's RDP session; no session -> V19 recorded as FAIL
# (CONVENTIONS §6: required, no deferral), the step still completes and the gate blocks Phase 2 until `--force 05b`.
# Facts from the platform research item 4 (package names VERIFIED; the Mozilla repo recipe is UNVERIFIED and fails
# loudly if it does not hold). Defines step_05b and the helper phase1_xrdp_bind (re-used by step 7).
[[ -n "${ATLAS_DAY1_DIR:-}" ]] || {
  # shellcheck source=lib/common.sh
  source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/common.sh"
}

# phase1_xrdp_bind ADDR... — xrdp.ini's [Globals] port= line carries the bind addresses (there is no address= key;
# VERIFIED xrdp.ini(5)). xrdp refuses to start if any listed address is not UP, so a service drop-in orders it after
# network-online and docker (the bridge) and restarts it until the addresses exist.
phase1_xrdp_bind() {
  local addrs=("$@") a spec=""
  (( ${#addrs[@]} > 0 )) || die "phase1_xrdp_bind: no addresses"
  for a in "${addrs[@]}"; do spec+="tcp://$a:3389 "; done
  spec="${spec% }"
  [[ -f /etc/xrdp/xrdp.ini ]] || die "/etc/xrdp/xrdp.ini is missing"
  grep -qE '^port=' /etc/xrdp/xrdp.ini || die "/etc/xrdp/xrdp.ini has no port= line in [Globals]"
  sed -i -E "0,/^port=.*/ s|^port=.*|port=$spec|" /etc/xrdp/xrdp.ini
  install -d -m 755 /etc/systemd/system/xrdp.service.d
  cat >/etc/systemd/system/xrdp.service.d/atlas.conf <<'CONF'
# ATLAS Phase 1 step 5b: xrdp binds to the LAN and WireGuard-bridge addresses (Section 3.6), which may appear late.
[Unit]
After=network-online.target docker.service
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Restart=on-failure
RestartSec=10s
CONF
  systemctl daemon-reload
  systemctl enable xrdp >/dev/null
  systemctl restart xrdp || die "xrdp failed to start with 'port=$spec' (are all addresses UP? ip -4 addr): $(journalctl -u xrdp -n 5 --no-pager)"
  sleep 2
  local l; l="$(ss -ltnH 'sport = :3389' | awk '{print $4}' | tr '\n' ' ')"
  log "xrdp listening on: $l"
  for a in "${addrs[@]}"; do grep -q "$a:3389" <<<"$l" || die "xrdp is not listening on $a:3389 (got: $l)"; done
}

# _firefox_policies — enterprise policy written BEFORE the install so the first launch already runs with telemetry,
# Normandy studies, app-update checks, captive-portal probes, DoH and extension updates OFF (rule §7.1 wants
# telemetry disabled in the component, not merely denied by squid; DoH would route the Principal's DNS to a resolver
# the allowlist does not name). Also off in the component (fix round): the periodic Mozilla/Google services that are
# not telemetry but still call home at every start (Safe Browsing list updates, Remote Settings-driven suggestions,
# Push, sponsored top sites / Firefox Suggest, add-on recommendations, the OpenH264/GMP plugin fetch, search
# suggestions), and (fix round 3) the remaining periodic call-home services: Remote Settings region detection
# (location.services.mozilla.com), the add-on blocklist fetch, the geolocation provider, the connectivity checker and
# the search-engine update. Policy names VERIFIED against mozilla/policy-templates (Preferences is restricted to a
# prefix list that includes browser., dom., extensions., geo., media. and network., all used here; toolkit.telemetry.*
# is not allowed there and is covered by DisableTelemetry). Not used: the SearchEngines policy (PreventInstallations),
# which policy-templates marks ESR-only, and Mozilla's apt `firefox` package is the rapid release (UNVERIFIED how a
# release build treats an ESR-only key, so it is left out rather than risk an about:policies error);
# browser.search.update=false covers the periodic engine update, and search suggestions are already off.
# firefox.settings.services.mozilla.com (Remote Settings sync proper) has no supported pref under the allowed prefixes
# and stays denied-and-logged by squid (not allowlisted).
# /etc/firefox/policies/policies.json is the documented Linux system path (VERIFIED, mozilla/policy-templates
# README); UNVERIFIED that Mozilla's own .deb reads it rather than only its install directory, so the same file is
# also placed at /usr/lib/firefox/distribution/policies.json (the .deb's directory).
_firefox_policies() {
  local d
  for d in /etc/firefox/policies /usr/lib/firefox/distribution; do
    install -d -m 755 "$d"
    cat >"$d/policies.json" <<'JSON'
{
  "policies": {
    "DisableTelemetry": true,
    "DisableAppUpdate": true,
    "DisableFirefoxStudies": true,
    "DisablePocket": true,
    "DisableFeedbackCommands": true,
    "DisableFirefoxAccounts": true,
    "DontCheckDefaultBrowser": true,
    "CaptivePortal": false,
    "NetworkPrediction": false,
    "DNSOverHTTPS": { "Enabled": false, "Locked": true },
    "Proxy": { "Mode": "system", "Locked": true },
    "ExtensionUpdate": false,
    "OverrideFirstRunPage": "",
    "OverridePostUpdatePage": "",
    "SearchSuggestEnabled": false,
    "FirefoxSuggest": { "WebSuggestions": false, "SponsoredSuggestions": false, "ImproveSuggest": false, "Locked": true },
    "UserMessaging": { "WhatsNew": false, "ExtensionRecommendations": false, "FeatureRecommendations": false,
                       "UrlbarInterventions": false, "SkipOnboarding": true, "MoreFromMozilla": false, "Locked": true },
    "NewTabPage": false,
    "Homepage": { "URL": "about:blank", "StartPage": "none", "Locked": true },
    "Preferences": {
      "browser.safebrowsing.malware.enabled":              { "Value": false, "Status": "locked" },
      "browser.safebrowsing.phishing.enabled":             { "Value": false, "Status": "locked" },
      "browser.safebrowsing.downloads.remote.enabled":     { "Value": false, "Status": "locked" },
      "browser.newtabpage.activity-stream.feeds.topsites": { "Value": false, "Status": "locked" },
      "dom.push.enabled":                                  { "Value": false, "Status": "locked" },
      "extensions.getAddons.cache.enabled":                { "Value": false, "Status": "locked" },
      "media.gmp-manager.updateEnabled":                   { "Value": false, "Status": "locked" },
      "browser.region.network.url":                        { "Value": "", "Status": "locked" },
      "browser.region.update.enabled":                     { "Value": false, "Status": "locked" },
      "extensions.blocklist.enabled":                      { "Value": false, "Status": "locked" },
      "geo.enabled":                                       { "Value": false, "Status": "locked" },
      "network.connectivity-service.enabled":              { "Value": false, "Status": "locked" },
      "browser.search.update":                             { "Value": false, "Status": "locked" }
    }
  }
}
JSON
    chmod 644 "$d/policies.json"
  done
  log "Firefox enterprise policy written (telemetry, studies, updates, captive portal, DoH, Safe Browsing, Push, suggestions, GMP fetch, region detection, blocklist, geolocation, connectivity checker, search-engine update off; proxy = system)"
}

_firefox_deb() {
  # Mozilla's apt repository (UNVERIFIED recipe: the KB page was unreachable during research; the key URL and suite
  # are the widely published ones). Every failure stops the step with the fix instead of silently keeping the stub.
  _firefox_policies
  install -d -m 0755 /etc/apt/keyrings
  # The signing key is pinned by fingerprint (fix round 3): TLS to the vendor host is otherwise the whole trust chain
  # for every future unattended upgrade from this origin. Mozilla publishes 35BA A0B3 3E9E B396 F59C A838 C0BA 5CE6
  # DC63 15A3 on support.mozilla.org ("Install Firefox on Linux"); UNVERIFIED from the build sandbox (the page was
  # unreachable through its proxy), so a mismatch stops the step with the fingerprint seen, never installs.
  local moz_fpr=35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3 keyf=/etc/apt/keyrings/packages.mozilla.org.asc
  if [[ ! -s "$keyf" ]]; then
    proxy_env
    curl -fsSL --max-time 60 https://packages.mozilla.org/apt/repo-signing-key.gpg -o "$keyf.tmp" \
      || die "could not fetch Mozilla's repo signing key via the proxy (is packages.mozilla.org in config/allowlist.txt? see /var/log/squid/access.log)"
    grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$keyf.tmp" \
      || { rm -f "$keyf.tmp"; die "the Mozilla key file is not an ASCII-armoured key (UNVERIFIED recipe; check https://packages.mozilla.org/apt/)"; }
    mv "$keyf.tmp" "$keyf"; chmod 644 "$keyf"
  fi
  command -v gpg >/dev/null || apt_install gnupg
  local got; got="$(gpg --show-keys --with-fingerprint --with-colons "$keyf" 2>/dev/null | awk -F: '$1=="fpr" {print $10}' | tr '\n' ' ')"
  grep -qw "$moz_fpr" <<<"$got" \
    || die "Mozilla's apt signing key does not carry the published fingerprint $moz_fpr (got: ${got:-none}); refusing to add the repository. Check https://support.mozilla.org/kb/install-firefox-linux for the current fingerprint; if Mozilla rotated the key, update moz_fpr in phase1/05b-desktop.sh; otherwise the download was tampered with (rm $keyf and re-run)"
  cat >/etc/apt/sources.list.d/mozilla.sources <<'SRC'
Types: deb
URIs: https://packages.mozilla.org/apt
Suites: mozilla
Components: main
Signed-By: /etc/apt/keyrings/packages.mozilla.org.asc
SRC
  printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' >/etc/apt/preferences.d/mozilla
  _ATLAS_APT_UPDATED=0
  export DEBIAN_FRONTEND=noninteractive
  retry 3 apt-get -q update || die "apt-get update failed after adding the Mozilla repository"
  apt-cache policy firefox | grep -q 'packages.mozilla.org' \
    || die "apt does not offer firefox from packages.mozilla.org (apt-cache policy firefox); the archive package is a snap stub and is not acceptable (conflict 5)"
  # The archive stub may already be installed; the pin makes Mozilla's build the candidate.
  retry 3 apt-get install -y -q -o Dpkg::Options::=--force-confold firefox || die "apt-get install firefox (Mozilla repo) failed"
  local ver; ver="$(dpkg-query -W -f='${Version}' firefox 2>/dev/null || true)"
  [[ -n "$ver" && "$ver" != *snap* ]] || die "installed firefox is '$ver' (the snap stub); the Mozilla pin did not take"
  log "Firefox $ver installed from packages.mozilla.org (.deb, not snap)"
}

step_05b() {
  [[ -n "${LAN_IP:-}" ]] || die "LAN_IP is empty"
  export DEBIAN_FRONTEND=noninteractive
  # --no-install-recommends keeps display managers out; package set VERIFIED on packages.ubuntu.com/resolute.
  proxy_env
  if [[ "$(dpkg-query -W -f='${Status}' xfce4 2>/dev/null || true)" != "install ok installed" ]]; then
    if [[ "$_ATLAS_APT_UPDATED" != "1" ]]; then retry 3 apt-get -q update || die "apt-get update failed"; _ATLAS_APT_UPDATED=1; fi
    retry 3 apt-get install -y -q --no-install-recommends -o Dpkg::Options::=--force-confold \
      xfce4 xfce4-goodies xfce4-terminal dbus-x11 xorg xrdp xorgxrdp \
      || die "installing XFCE/xrdp failed"
  fi
  apt_install xrdp xorgxrdp
  adduser --quiet xrdp ssl-cert >/dev/null 2>&1 || true        # harmless; needed only if runtime_user=xrdp is enabled
  sed -i -E 's/^AllowRootLogin=.*/AllowRootLogin=false/' /etc/xrdp/sesman.ini
  sed -i -E 's/^security_layer=.*/security_layer=tls/' /etc/xrdp/xrdp.ini   # mstsc speaks TLS; drop plain RDP crypto
  # Per-user session for the Principal; no display manager means no autologin (Appendix B "no autologin").
  # phase1_write_file (04-system.sh): `install /dev/stdin` fails on re-runs with resolute's rust-coreutils install.
  # The account's real primary group, not a same-named group (fix round 3: an LDAP/`users` layout has none).
  printf 'xfce4-session\n' | phase1_write_file 644 "$PRINCIPAL_USER:$(id -gn "$PRINCIPAL_USER")" "/home/$PRINCIPAL_USER/.xsession"
  systemctl get-default | grep -qx multi-user.target || systemctl set-default multi-user.target >/dev/null
  local dm
  for dm in lightdm gdm3 sddm; do
    if systemctl list-unit-files "$dm.service" 2>/dev/null | grep -q "^$dm.service"; then
      systemctl disable --now "$dm.service" >/dev/null 2>&1 || true
      warn "display manager $dm was present and has been disabled (no autologin, R21)"
    fi
  done
  # Bind to the LAN address now; step 7 adds the WireGuard bridge gateway. On a re-run after step 7 (--force 05b)
  # the bridge already exists, so keep its address rather than dropping the VPN path until step 7 runs again.
  local addrs=("$LAN_IP")
  if [[ -n "${ATLAS_WG_BRIDGE:-}" ]] && ip link show "$ATLAS_WG_BRIDGE" >/dev/null 2>&1; then addrs+=("$ATLAS_WG_BRIDGE_GW"); fi
  phase1_xrdp_bind "${addrs[@]}"
  _firefox_deb

  # V19: print the instructions here (the verify script's stdout is the one-line evidence), then wait.
  cat <<MSG

  ==== V19: test the desktop from the Principal's Windows PC (up to 10 minutes) ====
  1. On the Windows PC open "Remote Desktop Connection" (mstsc) and connect to:  $LAN_IP
  2. Accept the certificate warning (self-signed), pick session "Xorg", log in as user "$PRINCIPAL_USER"
     with your Ubuntu password. An XFCE desktop with Firefox should appear.
  Nothing else to do here; V19 passes as soon as the session shows up. If no session appears in 10 minutes,
  V19 is recorded as FAIL (CONVENTIONS §6: required, no deferral), this step still completes, and the Phase 1
  gate blocks Phase 2 until you re-run it (idempotent, waits again) with:
      sudo $ATLAS_ENTRY phase1 --force 05b
  ===============================================================================
MSG
  # phase1_xrdp_bind already died if xrdp is down or not bound as intended, so a fail here is the timeout: recorded
  # (rule §7.4), the step completes, the gate (step 8) shows the red row.
  run_verify V19 v19-xrdp.sh "$PRINCIPAL_USER" 570 "sudo $ATLAS_ENTRY phase1 --force 05b" "${addrs[@]}" \
    || warn "V19 recorded as FAIL (no RDP session in time, or see the verify table). The phase continues to the gate, which blocks Phase 2 until: sudo $ATLAS_ENTRY phase1 --force 05b"
}
