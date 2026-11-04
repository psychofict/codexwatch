"""The dashboard server: right data for the page, nothing for anyone else."""

import json
import threading
import time
import urllib.error
import urllib.request
import xml.dom.minidom
from http.server import ThreadingHTTPServer

import pytest

from codexwatch import server
from codexwatch.demo import generate
from codexwatch.store import open_store


@pytest.fixture
def running(env):
    generate(str(env), days=14, now=time.time())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    server.Handler.token = "test-token"
    server.Handler.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def get(url, headers=None, method="GET", data=None):
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_page_carries_token_nonce_and_strict_policy(running):
    code, body, headers = get(running + "/")
    assert code == 200
    html = body.decode()
    assert "test-token" in html and "__NONCE__" not in html
    nonce = html.split('nonce="')[1].split('"')[0]
    csp = headers["Content-Security-Policy"]
    assert f"'nonce-{nonce}'" in csp and "frame-ancestors 'none'" in csp


def test_state_needs_the_token(running):
    assert get(running + "/api/state")[0] == 403
    code, body, _ = get(running + "/api/state?period=7d", {"X-Codexwatch-Token": "test-token"})
    assert code == 200
    st = json.loads(body)
    for key in ("headline", "period", "daily", "breakdown", "sessions", "quota", "plan_value", "heatmap", "meta"):
        assert key in st
    assert st["period"]["requests"] > 0
    assert st["plan"]["name"] == "Pro 200" and st["plan_value"]["multiple"] > 0


def test_a_foreign_host_name_is_refused(running):
    # A DNS-rebinding page reaches this port under its own name; the Host check stops it.
    assert get(running + "/", {"Host": "attacker.example"})[0] == 421
    assert get(running + "/api/state", {"Host": "attacker.example", "X-Codexwatch-Token": "test-token"})[0] == 421


def test_actions_need_the_token(running):
    code, _, _ = get(running + "/api/hop", method="POST", data=b'{"name":"x"}')
    assert code == 403
    code, body, _ = get(running + "/api/hop", {"X-Codexwatch-Token": "test-token"}, "POST", b'{"name":"nobody"}')
    assert code == 400 and json.loads(body)["ok"] is False


def test_static_files_cannot_escape_the_web_folder(running):
    assert get(running + "/../store.py")[0] == 404
    assert get(running + "/%2e%2e/store.py")[0] == 404
    code, _, headers = get(running + "/fonts/Geist.woff2")
    assert code == 200 and headers["Content-Type"] == "font/woff2"


def test_card_svg(running):
    code, body, headers = get(running + "/api/card.svg?t=test-token")
    assert code == 200 and headers["Content-Type"] == "image/svg+xml"
    xml.dom.minidom.parseString(body)


def test_dashboard_state_direct(env):
    generate(str(env), days=10, now=time.time())
    st = server.dashboard_state(open_store(), "all")
    assert st["period"]["label"] == "all time"
    assert len(st["heatmap"]) == 7 and len(st["heatmap"][0]) == 24
