#!/usr/bin/env python3
"""Phase 4 step 1 wheel pre-flight, run on the HOST by phase4-engines.sh (_p4_wheel_preflight) BEFORE `docker build` of
docker/rocm-base/Dockerfile (ATLAS_FRAMEWORK_REVIEW.md Section 17 Phase 4 step 1; Section 21 scope note v0.3.2: the
`P4-wheels` row, recorded `info`, never a pass/fail row of its own). Rule §7.4: a pin that AMD renamed or withdrew is
found here, in seconds, not an hour into a 5 GB build; rule §7.9: the pins ARE the image's manifest, so the file names
that satisfy them are printed and kept.

What it does, the way pip does it (PEP 503 "simple" index; the layout under base_image.rocm_index is research-VERIFIED
only as a `pip --index-url` target, so a miss names every file the page did list):
  1. GET <index>/                       a REACHABILITY probe only (fix round 5): a transport/proxy failure -> exit 2
     with the URL and the proxy error; an HTTP error there (404 included) is logged and NOT fatal, because pip never
     fetches the root of a PEP 503 index (only <index>/<project>/) and whether the channel serves a root listing is
     UNVERIFIED; the project pages decide
  2. GET <index>/<project>/ per spec    hrefs parsed; file names parsed per PEP 427 (name-version[-build]-py-abi-plat);
     when EVERY project page is 404 the channel URL is wrong or gone -> exit 2
  3. choose the wheel that satisfies the spec: exact version for NAME==VERSION (string equality after lower-casing;
     the pins and the index spell the local tag the same way, "+rocm10.0.0"), any version for a bare NAME (newest,
     preferring one whose version carries --prefer-local), python tag compatible with --python-tag (cpXYZ, py3, pyX)
     and a platform the container can use (`any`, linux_x86_64, manylinux*_x86_64)
  4. HEAD each chosen wheel URL (a ranged GET when the host refuses HEAD): the bytes must exist behind the href. A
     transport failure HERE (squid's "Tunnel connection failed: 403" on a CONNECT to a host the allowlist does not
     admit; the hosts the index's hrefs point at are NOT research-verified) is exit 2 naming that host, never a
     "missing wheel" (fix round 5: the wheel exists; the allowlist is the cause)
Exit 0: every spec resolved (summary names each file and its size); exit 1: at least one spec has no wheel or its HEAD
answered an HTTP error (summary names the exact expected file name, the project page URL and what was there); exit 2:
the index, a project page or a wheel host could not be fetched through the proxy (summary carries the URL, the host and
the error), or every project page is 404.
The LAST stdout line is the one-line summary the driver records; detail lines go to stderr (the journal). --json writes
the full result (root-held under $ATLAS_STATE/phase4/ by the driver).

Every request goes through the proxy that lib/common.sh proxy_env exported (urllib honours HTTPS_PROXY/https_proxy;
rule §7.1); nothing is downloaded beyond the index pages and one ranged byte per wheel at most.
Unit tests: orchestrator/tests/test_wheel_preflight.py (CONVENTIONS §7.8: fetch/head stubbed, no network).
"""

from __future__ import annotations

import argparse
import html.parser
import http.client
import json
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

TIMEOUT_S = 30
ATTEMPTS = 2  # one retry on a transport error; a 404 is final
ACCEPT = "text/html, application/vnd.pypi.simple.v1+html;q=0.9, */*;q=0.1"
UA = "atlas-day1-wheel-preflight (phase4/wheel_preflight.py)"


class IndexUnreachable(Exception):
    """The index (or a project page) could not be fetched through the proxy; the message names URL and error."""


@dataclass
class Wheel:
    """One wheel file name parsed per PEP 427."""

    name: str
    version: str
    build: str
    py: str
    abi: str
    plat: str
    filename: str


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr, flush=True)


def norm_name(s: str) -> str:
    """PEP 503 normalisation: runs of -, _ and . become one -, lower case."""
    return re.sub(r"[-_.]+", "-", s).lower()


def norm_version(s: str) -> str:
    return s.strip().lower()


def describe(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"{type(exc).__name__}: {exc.reason}"
    return f"{type(exc).__name__}: {exc}"


def parse_wheel(filename: str) -> Wheel | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    if len(parts) == 5:
        name, version, py, abi, plat = parts
        build = ""
    elif len(parts) == 6:
        name, version, build, py, abi, plat = parts
    else:
        return None
    return Wheel(name, version, build, py, abi, plat, filename)


def compatible(w: Wheel, pytag: str) -> bool:
    """cp312 accepts cp312, py3 and py312 wheels (any abi: cp312, abi3, none) whose platform the container can use:
    `any`, linux_x86_64 or manylinux*_x86_64 (never a macosx_*_x86_64 or win wheel, which a suffix test would admit)."""
    major = pytag[2:3]  # "3" of cp312
    ok_py = any(t == pytag or t == f"py{major}" or t == f"py{pytag[2:]}" for t in w.py.split("."))
    ok_plat = any(
        p == "any" or (p.endswith("_x86_64") and p.startswith(("linux", "manylinux"))) for p in w.plat.split(".")
    )
    return ok_py and ok_plat


def version_key(v: str) -> tuple[tuple[int, Any], ...]:
    """Good enough to pick the newest of a few wheels: numeric parts compare as ints, the rest as text."""
    return tuple((0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[.+!]", v.lower()) if x != "")


class _Links(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


def parse_links(page: str) -> list[tuple[str, str]]:
    p = _Links()
    p.feed(page)
    p.close()
    return p.links


def fetch(url: str, *, any_http_ok: bool = False) -> tuple[int, str]:
    """GET a page; (404, "") when the page does not exist; IndexUnreachable on any other failure after ATTEMPTS.
    any_http_ok=True (the root probe) returns (code, "") for EVERY HTTP error instead: the server answered, so the
    proxy path works, and that is all the probe asks."""
    last: BaseException | None = None
    for attempt in range(1, ATTEMPTS + 1):
        req = urllib.request.Request(url, headers={"Accept": ACCEPT, "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404 or any_http_ok:
                return exc.code, ""
            last = exc
        except (OSError, http.client.HTTPException) as exc:  # URLError (proxy CONNECT refused), timeouts, TLS, resets
            last = exc
        if attempt < ATTEMPTS:
            eprint(f"wheel_preflight: {url}: {describe(last)}; retrying once")
            time.sleep(2)
    raise IndexUnreachable(f"{url}: {describe(last)}")


def _total_from_headers(resp: http.client.HTTPResponse) -> int | None:
    cr = resp.headers.get("Content-Range", "")
    m = re.search(r"/(\d+)$", cr)
    if m:
        return int(m.group(1))
    cl = resp.headers.get("Content-Length", "")
    return int(cl) if cl.isdigit() else None


def head(url: str) -> tuple[int, int | None, str]:
    """HEAD the wheel; a server that refuses HEAD gets one ranged GET. Returns (http_code, total_bytes, error_text)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, _total_from_headers(resp), ""
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 405, 501):
            return exc.code, None, describe(exc)
        first = describe(exc)
    except (OSError, http.client.HTTPException) as exc:
        return 0, None, describe(exc)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, _total_from_headers(resp), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HEAD: {first}; ranged GET: {describe(exc)}"
    except (OSError, http.client.HTTPException) as exc:
        return 0, None, f"HEAD: {first}; ranged GET: {describe(exc)}"


def expected_name(name: str, version: str, pytag: str) -> str:
    """The file name a miss is reported as (PEP 427 spelling: dashes in the name become underscores)."""
    return f"{re.sub(r'[-.]+', '_', name)}-{version or '<any version>'}-{pytag}-*.whl"


def resolve(index: str, spec: str, pytag: str, prefer_local: str) -> tuple[dict[str, Any], str | None]:
    """Resolve one spec on the index; returns (record, miss_text). Raises IndexUnreachable on a transport failure to
    the project page OR to the chosen wheel's host (docstring step 4); rec["page_http"] is the project page's status so
    main() can tell "every project page is 404" (the channel is gone) from an ordinary miss."""
    name, _, version = spec.partition("==")
    project = norm_name(name)
    page_url = urllib.parse.urljoin(index, project + "/")
    rec: dict[str, Any] = {"spec": spec, "project": project, "page": page_url, "page_http": None, "filename": None,
                           "url": None, "http": None, "bytes": None}
    status, page = fetch(page_url)
    rec["page_http"] = status
    if status == 404:
        return rec, f"{expected_name(name, version, pytag)}: no project page {page_url} (HTTP 404)"
    seen: list[str] = []
    candidates: list[tuple[Wheel, str]] = []
    for href, text in parse_links(page):
        fname = urllib.parse.unquote(text or posixpath.basename(urllib.parse.urlparse(href).path))
        w = parse_wheel(fname)
        if w is None or norm_name(w.name) != project:
            continue
        seen.append(fname)
        if version and norm_version(w.version) != norm_version(version):
            continue
        if not compatible(w, pytag):
            continue
        candidates.append((w, urllib.parse.urljoin(page_url, href).split("#", 1)[0]))
    if not candidates:
        listed = ", ".join(sorted(seen)[:12]) + (" ..." if len(seen) > 12 else "") or "no wheel of this project"
        return rec, f"{expected_name(name, version, pytag)} not on {page_url} (there: {listed})"
    # Newest first; a version carrying --prefer-local (e.g. rocm10.0.0) wins over one without it, as the Dockerfile's
    # single-index install would land on it rather than on a wheel built against another ROCm.
    candidates.sort(
        key=lambda c: (prefer_local != "" and prefer_local in c[0].version.lower(), version_key(c[0].version)),
        reverse=True,
    )
    w, url = candidates[0]
    code, size, err = head(url)
    rec.update({"filename": w.filename, "url": url, "http": code, "bytes": size})
    if code == 0:
        # No HTTP answer at all: the proxy refused the CONNECT (TCP_DENIED) or the host is down. The wheel IS listed;
        # saying "missing" would send the Principal to change the pins (fix round 5).
        host = urllib.parse.urlparse(url).hostname or "?"
        raise IndexUnreachable(f"{w.filename} is listed on {page_url} but its wheel URL {url} could not be fetched "
                               f"(host {host} must be in config/allowlist.txt): {err}")
    if not 200 <= code < 400:
        return rec, f"{w.filename}: listed on {page_url} but HEAD {url} -> {err or f'HTTP {code}'}"
    eprint(f"wheel_preflight: {spec} -> {w.filename} ({size if size is not None else '?'} bytes, HTTP {code})")
    return rec, None


def write_json(path: str | None, report: dict[str, Any]) -> None:
    if not path:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", required=True, help="the simple index the Dockerfile builds against (rocm_index)")
    ap.add_argument("--python-tag", default="cp312", help="the image's python tag (python3.12 -> cp312)")
    ap.add_argument("--prefer-local", default="", help="prefer wheels whose version carries this local tag")
    ap.add_argument("--json", default=None, help="write the full result here (atomic)")
    ap.add_argument("specs", nargs="+", help="NAME==VERSION (exact) or NAME (any compatible version)")
    args = ap.parse_args(argv)
    index = args.index if args.index.endswith("/") else args.index + "/"
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "(HTTPS_PROXY unset: no proxy)"
    report: dict[str, Any] = {"index": index, "python_tag": args.python_tag, "proxy": proxy,
                              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "wheels": [], "missing": [],
                              "status": None}
    # Docstring step 1: the root is a reachability probe. Only a transport failure is fatal here; an HTTP error (404
    # included) is noted and the project pages, which pip actually fetches, decide.
    try:
        status, _root = fetch(index, any_http_ok=True)
    except IndexUnreachable as exc:
        report["status"] = "unreachable"
        write_json(args.json, report)
        print(f"P4-wheels: index {index} cannot be fetched through proxy {proxy}: {exc}")
        return 2
    report["root_http"] = status
    if not 200 <= status < 400:
        eprint(f"wheel_preflight: {index} answered HTTP {status}: the channel root is not a page pip needs; "
               "continuing to the per-project pages, which decide")
    missing: list[str] = []
    for spec in args.specs:
        try:
            rec, miss = resolve(index, spec, args.python_tag, args.prefer_local)
        except IndexUnreachable as exc:
            report["status"] = "unreachable"
            write_json(args.json, report)
            print(f"P4-wheels: {index} reachable but a project page or a wheel host was not, through proxy {proxy}: "
                  f"{exc}")
            return 2
        report["wheels"].append(rec)
        if miss:
            missing.append(miss)
    report["missing"] = missing
    if report["wheels"] and all(r["page_http"] == 404 for r in report["wheels"]):
        # Docstring step 2: not one project page exists; the channel URL itself is wrong or gone (a wrong base_image
        # .rocm_index, or AMD moved the channel), which is not a pin problem.
        report["status"] = "unreachable"
        write_json(args.json, report)
        print(f"P4-wheels: every project page under {index} answered HTTP 404 (root: HTTP {status}) through proxy "
              f"{proxy}: the channel URL is wrong or gone; check config/phase4-engines.json base_image.rocm_index")
        return 2
    report["status"] = "missing" if missing else "ok"
    write_json(args.json, report)
    if missing:
        print(f"P4-wheels: MISSING on {index} ({args.python_tag}): " + "; ".join(missing))
        return 1
    found = ", ".join(
        f"{r['filename']} ({r['bytes'] // 1048576} MiB)" if r["bytes"] else str(r["filename"]) for r in report["wheels"]
    )
    print(f"P4-wheels: ok, {len(report['wheels'])} wheels on {index} for {args.python_tag}: {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
