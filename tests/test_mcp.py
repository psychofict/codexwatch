import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

from codexwatch import mcp
from codexwatch.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def line(ts, ordinal, kind, payload):
    return json.dumps({"timestamp": iso(ts), "ordinal": ordinal, "type": kind, "payload": payload},
                      separators=(",", ":")) + "\n"


def write_rollout(home, now):
    d = os.path.join(home, "sessions", "2026", "11", "04")
    os.makedirs(d, exist_ok=True)
    sid = "01a1a4c5-0000-7000-8000-000000000001"
    usage = {"input_tokens": 12000, "cached_input_tokens": 10000, "cache_write_input_tokens": 0,
             "output_tokens": 300, "reasoning_output_tokens": 100, "total_tokens": 12300}
    rl = {"limit_id": "codex", "limit_name": None, "plan_type": "plus",
          "primary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": int(now + 3 * 3600)},
          "secondary": {"used_percent": 4.0, "window_minutes": 10080, "resets_at": int(now + 5 * 86400)},
          "credits": {"has_credits": False, "unlimited": False, "balance": "0"}}
    rows = [
        line(now - 120, 0, "session_meta", {"id": sid, "timestamp": iso(now - 120), "cwd": "/work/demo-app",
                                            "originator": "codex-tui", "cli_version": "0.166.0", "source": "cli",
                                            "model_provider": "openai"}),
        line(now - 110, 1, "turn_context", {"turn_id": "t1", "model": "gpt-6-sol", "effort": "high",
                                            "cwd": "/work/demo-app"}),
        line(now - 100, 2, "token_usage_record", {"thread_id": sid, "turn_id": "t1", "response_id": "resp_1",
                                                  "usage": usage}),
        line(now - 100, 3, "event_msg", {"type": "token_count", "info": None, "rate_limits": rl}),
        line(now - 90, 4, "token_usage_record", {"thread_id": sid, "turn_id": "t1", "response_id": "resp_2",
                                                 "usage": usage}),
        line(now - 90, 5, "event_msg", {"type": "token_count", "info": None,
                                        "rate_limits": {**rl, "primary": {**rl["primary"], "used_percent": 13.0}}}),
        line(now - 80, 6, "event_msg", {"type": "task_complete", "turn_id": "t1", "started_at": int(now - 110),
                                        "completed_at": int(now - 80), "duration_ms": 30000,
                                        "time_to_first_token_ms": 1500}),
    ]
    with open(os.path.join(d, f"rollout-2026-11-04T10-00-00-{sid}.jsonl"), "w") as f:
        f.writelines(rows)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    write_rollout(str(home), time.time())
    e = {**os.environ, "CODEXWATCH_DIR": str(tmp_path / "cw"), "CODEX_HOME": str(home),
         "CODEXHOP_DIR": str(tmp_path / "hop"), "CODEXWATCH_NOTIFY_DRYRUN": "1",
         "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")}
    for k in ("CODEXWATCH_DIR", "CODEX_HOME", "CODEXHOP_DIR"):
        monkeypatch.setenv(k, e[k])
    return e


def rpc(i, method, params=None):
    msg = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_end_to_end_over_stdio(env):
    lines = [
        rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "test", "version": "0"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        rpc(2, "tools/list"),
        rpc(3, "tools/call", {"name": "usage_summary", "arguments": {"period": "today"}}),
        rpc(4, "tools/call", {"name": "plan_limits", "arguments": {}}),
        rpc(5, "tools/call", {"name": "quota_forensics", "arguments": {"window": "5h"}}),
        rpc(6, "tools/call", {"name": "sessions", "arguments": {"limit": 5}}),
        rpc(7, "tools/call", {"name": "daily", "arguments": {"days": 3}}),
        rpc(8, "tools/call", {"name": "budget_status", "arguments": {}}),
        rpc(9, "no/such/method"),
        "{not json",
        rpc(10, "tools/call", {"name": "nope", "arguments": {}}),
        rpc(11, "tools/call", {"name": "usage_summary", "arguments": {"period": "decade"}}),
        rpc(12, "ping"),
    ]
    proc = subprocess.run([sys.executable, "-m", "codexwatch", "mcp"], input="\n".join(lines) + "\n",
                          capture_output=True, text=True, cwd=ROOT, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = [json.loads(x) for x in proc.stdout.splitlines()]
    by_id = {m.get("id"): m for m in out}
    assert len(out) == 13  # every request answered once, the notification not at all

    init = by_id[1]["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "codexwatch"
    assert init["capabilities"] == {"tools": {}}
    names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert names == {"usage_summary", "plan_limits", "quota_forensics", "sessions", "daily", "budget_status"}
    for t in by_id[2]["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"

    summary = by_id[3]["result"]
    assert summary["isError"] is False
    assert json.loads(summary["content"][0]["text"]) == summary["structuredContent"]
    assert summary["structuredContent"]["totals"]["total"] == 24600
    assert summary["structuredContent"]["top_projects"][0]["name"] == "demo-app"

    lim = by_id[4]["result"]["structuredContent"]
    wins = lim["limits"][0]["windows"]
    assert lim["plan"]["name"] == "Plus" and wins[0]["pct"] == 13.0
    assert by_id[5]["result"]["isError"] is False
    assert by_id[6]["result"]["structuredContent"]["sessions"][0]["turns"] == 1
    assert len(by_id[7]["result"]["structuredContent"]["days"]) == 3
    assert by_id[8]["result"]["structuredContent"]["budgets"] == []

    assert by_id[9]["error"]["code"] == -32601
    assert by_id[None]["error"]["code"] == -32700
    assert by_id[10]["error"]["code"] == -32602
    assert by_id[11]["result"]["isError"] is True
    assert by_id[12]["result"] == {}


def test_protocol_version_fallback_and_batch(env, tmp_path):
    srv = mcp.Server(Store(str(tmp_path / "h.db")))
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == mcp.DEFAULT_PROTOCOL
    out = io.StringIO()
    batch = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    mcp.serve(io.StringIO(batch + "\n"), out, srv)
    assert json.loads(out.getvalue()) == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_tool_output_never_reaches_stdout(env, tmp_path, monkeypatch, capsys):
    srv = mcp.Server(Store(str(tmp_path / "h.db")))
    monkeypatch.setattr(srv, "tool_budget_status", lambda args: print("stray") or {"budgets": []})
    mcp.serve(io.StringIO(rpc(1, "tools/call", {"name": "budget_status", "arguments": {}}) + "\n"), None, srv)
    cap = capsys.readouterr()
    assert "stray" not in cap.out and "stray" in cap.err
    assert json.loads(cap.out)["result"]["isError"] is False
