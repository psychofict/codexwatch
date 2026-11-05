"""The web dashboard: a small local server and one page.

It listens on 127.0.0.1 only. The page and its data come from this process;
nothing is fetched from the internet. Two requests can act on the outside
world, a fresh reading of every codexhop account and a hop, so they need the
random token baked into the page this server handed out, and every request
must name this machine as its Host. A web page elsewhere cannot read the
token, and a rebound DNS name fails the Host check, so a site you visit can
neither read your usage nor hop your account.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, config, hop, limits, paths, periods, reports, snapshot
from .store import open_store

WEB = os.path.join(os.path.dirname(__file__), "web")
# The file types the page is made of, fixed here rather than read from the
# system: Windows can map .js to text/plain, which nosniff then refuses to run.
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}


def dashboard_state(store, period: str = "30d", scan: bool = True) -> dict:
    st = snapshot.state(store, scan=scan, days=90)
    since, until, label = periods.parse(period)
    f = reports.Filter(since=since, until=until)
    st["period"] = {"key": period, "label": label, "since": since, "until": until,
                    **reports.totals(store, f)}
    st["breakdown"] = {by: reports.group(store, by, f, limit=10)
                       for by in ("model", "project", "client", "tier", "effort", "machine", "agent")}
    st["sessions"] = reports.sessions(store, f, limit=14)
    st["tools"] = reports.tools(store, f, limit=12)
    st["latency"] = reports.latency(store, f)
    st["heatmap"] = reports.heatmap(store, f)
    st["streaks"] = reports.streaks(store)
    fx = limits.forensics(store, "codex", None, limit=10)
    fx["timeline"] = fx["timeline"][-45:]
    st["quota"] = fx
    last30 = reports.totals(store, reports.Filter(since=periods.parse("30d")[0]))
    price = st["plan"].get("usd")
    st["plan_value"] = {
        "api_value_30d": last30["cost"],
        "plan_usd": price,
        "multiple": (last30["cost"] / price) if price else None,
    }
    st["meta"] = {"version": __version__, "data_dir": paths.data_dir(), "codexhop": hop.installed(),
                  "sources": len(config.sources()), "last_scan": store.meta("last_scan")}
    return st


class Handler(BaseHTTPRequestHandler):
    server_version = f"codexwatch/{__version__}"
    token = ""
    hosts: set = set()

    def log_message(self, fmt, *args):  # quiet unless asked
        if os.environ.get("CODEXWATCH_DEBUG"):
            sys.stderr.write("dashboard: " + fmt % args + "\n")

    # ------------------------------------------------------------ plumbing
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in self.hosts

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None, nonce: str = ""):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        script = f"script-src 'self' 'nonce-{nonce}'; " if nonce else ""
        self.send_header("Content-Security-Policy",
                         f"default-src 'self'; {script}img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                         "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, default=str).encode(), "application/json")

    # ---------------------------------------------------------------- GET
    def do_GET(self):  # noqa: N802 - http.server API
        if not self._host_ok():
            return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"wrong host", "text/plain")
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
                html = f.read()
            nonce = secrets.token_urlsafe(16)
            html = (html.replace("__CODEXWATCH_TOKEN__", self.token).replace("__VERSION__", __version__)
                    .replace("__NONCE__", nonce))
            return self._send(200, html.encode(), "text/html; charset=utf-8", nonce=nonce)
        if url.path == "/api/state":
            if self.headers.get("X-Codexwatch-Token") != self.token:
                return self._json({"error": "missing token"}, 403)
            try:
                period = q.get("period") or "30d"
                periods.parse(period)
                return self._json(dashboard_state(open_store(), period))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if url.path == "/api/card.svg":
            if q.get("t") != self.token:
                return self._send(403, b"missing token", "text/plain")
            from . import card

            svg = card.render(open_store(), period=q.get("period") or "30d",
                              show_projects=q.get("projects") == "1", theme=q.get("theme") or "dark")
            return self._send(200, svg.encode(), "image/svg+xml",
                              {"Content-Disposition": 'inline; filename="codexwatch-card.svg"'})
        rel = url.path.lstrip("/")
        full = os.path.realpath(os.path.join(WEB, rel))
        if not full.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ctype = TYPES.get(os.path.splitext(full)[1])
        if not ctype:
            return self._send(404, b"not found", "text/plain")
        with open(full, "rb") as f:
            return self._send(200, f.read(), ctype)

    do_HEAD = do_GET

    # --------------------------------------------------------------- POST
    def do_POST(self):  # noqa: N802
        if not self._host_ok() or self.headers.get("X-Codexwatch-Token") != self.token:
            return self._json({"error": "forbidden"}, 403)
        length = min(int(self.headers.get("Content-Length") or 0), 4096)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        path = urlparse(self.path).path
        if os.environ.get("CODEXWATCH_DEMO"):
            return self._json({"ok": False, "message": "this is the demo; it never hops or reads a real account"})
        if path == "/api/refresh-accounts":
            ok, msg = hop.refresh()
            open_store().scan()
            return self._json({"ok": ok, "message": msg})
        if path == "/api/hop":
            name = str(body.get("name") or "")
            if name not in hop.accounts():
                return self._json({"ok": False, "message": "no such account"}, 400)
            ok, msg = hop.hop_to(name)
            return self._json({"ok": ok, "message": msg})
        return self._json({"error": "not found"}, 404)


def running_dashboard(port: int) -> str | None:
    """The URL of a codexwatch dashboard already serving on this port, if any."""
    import urllib.request

    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=0.6) as r:
            return url if (r.headers.get("Server") or "").startswith("codexwatch/") else None
    except (OSError, ValueError):
        return None


def serve(host: str = "127.0.0.1", port: int = 7878, open_browser: bool = True) -> int:
    # A second launch (the app-menu shortcut, the tray's "Open dashboard")
    # opens the dashboard that is already running instead of starting another.
    existing = running_dashboard(port)
    if existing:
        print(f"codexwatch dashboard is already running on {existing}")
        if open_browser and not os.environ.get("CODEXWATCH_NO_BROWSER"):
            webbrowser.open(existing)
        return 0
    token = secrets.token_urlsafe(24)
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"no free port from {port}", file=sys.stderr)
        return 1
    Handler.token = token
    Handler.hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{host}:{port}".lower()}
    url = f"http://127.0.0.1:{port}/"
    print(f"codexwatch dashboard on {url}  (Ctrl-C stops it)")
    store = open_store()
    store.scan()

    def rescan():
        while True:
            time.sleep(max(5, float(config.get("refresh_seconds") or 10)))
            try:
                store.scan()
            except Exception:  # noqa: BLE001 - keep serving; the next pass retries
                pass

    threading.Thread(target=rescan, daemon=True).start()
    if open_browser and not os.environ.get("CODEXWATCH_NO_BROWSER"):
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def cmd_dashboard(a) -> int:
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        print("codexwatch: the dashboard only listens on this machine (127.0.0.1).", file=sys.stderr)
        return 2
    return serve(a.host, a.port, not a.no_open)
