#!/usr/bin/env python3
"""Google OAuth for the two Principal accounts (Section 13, 17 Phase 2 step 6c, 21 V20).

Runs under /opt/atlas/venv (google-api-python-client 2.200.0, google-auth-oauthlib 1.4.1, google-auth 2.58.0:
services-tools.md §5.1 VERIFIED). Three sub-commands; each prints exactly one JSON line on stdout as its last
line ({"ok": true|false, ...}) so the bash caller can parse it; everything human-readable goes to stderr.

  authorise --client FILE --token FILE --email EMAIL --port N --timeout S [--owner USER]
      InstalledAppFlow.run_local_server(host="localhost", port=N, open_browser=False, timeout_seconds=S)
      (services-tools.md §5.1 VERIFIED signature and behaviour): the authorisation URL is printed in a framed
      block with the instruction to open it in Firefox on the node's own desktop (xrdp), because Google redirects
      to http://localhost:N/ which only reaches this process from a browser running on the node. Then the token is
      stored mode 600 owned by --owner and the three APIs are proven. An existing valid/refreshable token skips
      the browser (idempotent re-runs).
  verify --token FILE --email EMAIL
      No browser, no prompt: refresh if needed, prove Gmail (users.labels.list), Calendar (calendarList.list) and
      Drive (about.get), check the signed-in address is EMAIL.
  rclone-remote --token FILE --remote NAME --conf FILE [--owner USER]
      Write the rclone Drive remote from the same token (services-tools.md §4.9 VERIFIED token shape).

Scopes are fixed by the task: gmail.modify, gmail.send, calendar, drive (all VERIFIED in the discovery documents).
"""

from __future__ import annotations

import argparse
import configparser
import datetime as dt
import json
import os
import pwd
import shutil
import sys
from pathlib import Path
from typing import Any

SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
]

# Google may return the granted scopes in a different order or with extras; oauthlib raises otherwise.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def emit(payload: dict[str, Any]) -> int:
    print(json.dumps(payload), flush=True)
    return 0 if payload.get("ok") else 1


def write_private(path: Path, text: str, owner: str | None) -> None:
    """Write mode 600 (created with 0600 from the start), owned by `owner` when running as root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)
    if owner and os.geteuid() == 0:
        try:
            pwd.getpwnam(owner)
        except KeyError as exc:
            raise RuntimeError(f"owner {owner!r} does not exist") from exc
        shutil.chown(path, user=owner, group=owner)


def load_credentials(token_path: Path) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not token_path.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        return creds
    return None


def prove_apis(creds: Any, expected_email: str) -> dict[str, Any]:
    """Gmail labels list, Calendar calendar list, Drive about.get; the signed-in address must be `expected_email`."""
    from googleapiclient.discovery import build

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    profile = gmail.users().getProfile(userId="me").execute()
    labels = gmail.users().labels().list(userId="me").execute().get("labels", [])
    signed_in = str(profile.get("emailAddress", "")).lower()
    if signed_in != expected_email.lower():
        raise RuntimeError(
            f"signed in as {signed_in or '?'}, expected {expected_email}: delete the token file and re-run, "
            f"signing in with the right account"
        )
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    calendars = cal.calendarList().list().execute().get("items", [])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    about = drive.about().get(fields="user,storageQuota").execute()
    drive_user = str(about.get("user", {}).get("emailAddress", ""))
    granted = sorted(set(getattr(creds, "scopes", None) or []))
    missing = [s for s in SCOPES if s not in granted] if granted else []
    return {
        "email": signed_in,
        "gmail_labels": len(labels),
        "calendars": len(calendars),
        "drive_user": drive_user,
        "drive_quota_limit": about.get("storageQuota", {}).get("limit"),
        "missing_scopes": missing,
    }


FRAME = "=" * 96


def cmd_authorise(args: argparse.Namespace) -> int:
    token_path = Path(args.token)
    client_path = Path(args.client)
    if not client_path.is_file():
        return emit({"ok": False, "email": args.email, "error": f"client JSON {client_path} does not exist"})
    try:
        client = json.loads(client_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return emit({"ok": False, "email": args.email, "error": f"{client_path} is not JSON: {exc}"})
    if "installed" not in client:
        # A "Desktop app" client has the top-level key "installed" (flow.py VERIFIED); "web" clients cannot use the
        # loopback redirect this flow depends on.
        return emit(
            {
                "ok": False,
                "email": args.email,
                "error": f"{client_path} has no top-level 'installed' key: create a 'Desktop app' OAuth client in Google Cloud and download its JSON",
            }
        )
    try:
        creds = load_credentials(token_path)
    except Exception as exc:  # broad on purpose: a broken token file means: run the flow again
        eprint(f"existing token {token_path} unusable ({exc}); running the browser flow again")
        creds = None
    reused = creds is not None
    if creds is None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
        prompt = (
            f"\n{FRAME}\n"
            f"  GOOGLE AUTHORISATION ({args.label}): {args.email}\n"
            f"  Open this URL in Firefox ON THE NODE'S OWN DESKTOP (the xrdp/XFCE session), sign in as\n"
            f"  {args.email} and click Allow. Google redirects to http://localhost:{args.port}/ and that address\n"
            f"  only reaches this script from a browser running on the node itself.\n\n"
            f"  {{url}}\n\n"
            f"  Waiting up to {args.timeout // 60} minutes for the redirect ...\n"
            f"{FRAME}\n"
        )
        try:
            creds = flow.run_local_server(
                host="localhost",
                port=args.port,
                open_browser=False,
                authorization_prompt_message=prompt,
                success_message=f"{args.email}: authorised for A.T.L.A.S. You can close this tab.",
                timeout_seconds=args.timeout,
            )
        except Exception as exc:  # broad on purpose: timeout, port busy, user denied: all end the same way
            return emit(
                {"ok": False, "email": args.email, "error": f"authorisation flow failed: {type(exc).__name__}: {exc}"}
            )
        if creds is None or not creds.valid:
            return emit(
                {"ok": False, "email": args.email, "error": f"no redirect within {args.timeout} s (or consent denied)"}
            )
    try:
        write_private(token_path, creds.to_json(), args.owner)
    except Exception as exc:  # broad on purpose: reported in the JSON answer
        return emit({"ok": False, "email": args.email, "error": f"could not store the token: {exc}"})
    try:
        proof = prove_apis(creds, args.email)
    except Exception as exc:  # broad on purpose: reported in the JSON answer
        return emit(
            {
                "ok": False,
                "email": args.email,
                "token": str(token_path),
                "error": f"API proof failed: {type(exc).__name__}: {exc}",
            }
        )
    # A refreshed access token is worth persisting so verify runs never need the browser.
    write_private(token_path, creds.to_json(), args.owner)
    proof.update({"ok": not proof["missing_scopes"], "token": str(token_path), "reused_token": reused})
    if proof["missing_scopes"]:
        proof["error"] = f"consent did not grant every scope: missing {proof['missing_scopes']}"
    return emit(proof)


def cmd_verify(args: argparse.Namespace) -> int:
    token_path = Path(args.token)
    if not token_path.is_file():
        return emit({"ok": False, "email": args.email, "error": f"token {token_path} does not exist (Phase 2 step 6c)"})
    try:
        creds = load_credentials(token_path)
    except Exception as exc:  # broad on purpose: reported in the JSON answer
        return emit(
            {"ok": False, "email": args.email, "error": f"token {token_path} unusable: {type(exc).__name__}: {exc}"}
        )
    if creds is None:
        return emit(
            {
                "ok": False,
                "email": args.email,
                "error": f"token {token_path} expired without a refresh token; re-run step 6c",
            }
        )
    try:
        proof = prove_apis(creds, args.email)
    except Exception as exc:  # broad on purpose: reported in the JSON answer
        return emit({"ok": False, "email": args.email, "error": f"{type(exc).__name__}: {exc}"})
    try:
        write_private(token_path, creds.to_json(), args.owner)
    except Exception as exc:  # broad on purpose: not fatal for a verify: the APIs answered
        eprint(f"note: could not re-save the refreshed token: {exc}")
    proof["ok"] = not proof["missing_scopes"]
    return emit(proof)


def cmd_rclone_remote(args: argparse.Namespace) -> int:
    """rclone.conf [NAME]: type=drive, client_id, client_secret, scope=drive, token={...} (drive.md VERIFIED keys)."""
    from google.oauth2.credentials import Credentials

    token_path = Path(args.token)
    if not token_path.is_file():
        return emit({"ok": False, "remote": args.remote, "error": f"token {token_path} does not exist"})
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds.refresh_token:
        return emit({"ok": False, "remote": args.remote, "error": "token has no refresh_token; rclone needs one"})
    expiry = creds.expiry or dt.datetime.now(dt.timezone.utc)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=dt.timezone.utc)
    token = {
        "access_token": creds.token or "",
        "token_type": "Bearer",
        "refresh_token": creds.refresh_token,
        "expiry": expiry.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    conf = Path(args.conf)
    cfg = configparser.ConfigParser(interpolation=None)
    if conf.is_file():
        cfg.read(conf, encoding="utf-8")
    cfg[args.remote] = {
        "type": "drive",
        "client_id": creds.client_id or "",
        "client_secret": creds.client_secret or "",
        "scope": "drive",
        "token": json.dumps(token),
    }
    buf: list[str] = []

    class _W:
        def write(self, s: str) -> int:
            buf.append(s)
            return len(s)

    cfg.write(_W())
    try:
        write_private(conf, "".join(buf), args.owner)
    except Exception as exc:  # broad on purpose: reported in the JSON answer
        return emit({"ok": False, "remote": args.remote, "error": f"could not write {conf}: {exc}"})
    return emit({"ok": True, "remote": args.remote, "conf": str(conf)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("authorise")
    a.add_argument("--client", required=True)
    a.add_argument("--token", required=True)
    a.add_argument("--email", required=True)
    a.add_argument("--port", type=int, required=True)
    a.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the redirect (never below 900)")
    a.add_argument("--owner", default="atlas")
    a.add_argument("--label", default="account")
    a.set_defaults(func=cmd_authorise)

    v = sub.add_parser("verify")
    v.add_argument("--token", required=True)
    v.add_argument("--email", required=True)
    v.add_argument("--owner", default="atlas")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("rclone-remote")
    r.add_argument("--token", required=True)
    r.add_argument("--remote", required=True)
    r.add_argument("--conf", required=True)
    r.add_argument("--owner", default="atlas")
    r.set_defaults(func=cmd_rclone_remote)

    args = parser.parse_args(argv)
    if getattr(args, "timeout", 900) < 900:
        parser.error("--timeout must be at least 900 seconds (the pause is the Principal's, Section 17 step 6c)")
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
