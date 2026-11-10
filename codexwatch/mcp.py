"""codexwatch as an MCP server, so Codex or Claude can ask about usage mid-session.

`codexwatch mcp` speaks the Model Context Protocol over stdin and stdout:
one JSON-RPC 2.0 message per line. stdout carries protocol messages and
nothing else; anything else the process says goes to stderr, because a
stray line on stdout would break the client's parser.

Every tool reads new log lines first (an incremental scan, usually a few
milliseconds), so an agent asking "how much room do I have?" gets the reading
from its own last request.
"""

from __future__ import annotations

import contextlib
import json
import sys
import traceback

from . import __version__, budgets, hop, limits, periods, reports, snapshot

PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
DEFAULT_PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "usage_summary",
        "description": ("Codex token usage and its API-equivalent dollar value for a period: totals, "
                        "cache hit rate, and the top models and projects. Dollar figures are estimates at "
                        "OpenAI API prices; tokens of models with no published price are counted separately."),
        "inputSchema": {"type": "object", "properties": {
            "period": {"type": "string", "enum": ["today", "7d", "30d", "month", "all"], "default": "today",
                       "description": "today, the last 7 or 30 days, this calendar month, or all history"}},
            "additionalProperties": False},
    },
    {
        "name": "plan_limits",
        "description": ("Where the ChatGPT plan's Codex limits stand now: each window's percent used, "
                        "reset time, burn rate per hour and projected time it fills, read from the newest "
                        "session log. With codexhop installed, also every saved account's last reading, the "
                        "account with the most 5-hour room, and its providers (Azure and the like, kind "
                        "'provider', no plan limits) with the live one marked."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "quota_forensics",
        "description": ("What each percentage point of the plan cost in API-equivalent dollars and tokens, per "
                        "window and per day, plus rises in a window that the local logs cannot explain "
                        "(usage from another machine, ChatGPT itself or cloud tasks, or a metering change)."),
        "inputSchema": {"type": "object", "properties": {
            "window": {"type": "string", "enum": ["5h", "week"],
                       "description": "only windows of this length; both when omitted"}},
            "additionalProperties": False},
    },
    {
        "name": "sessions",
        "description": "The most recent Codex sessions with title, project, models, turns, tokens and dollar value.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10}},
            "additionalProperties": False},
    },
    {
        "name": "daily",
        "description": "Tokens and API-equivalent dollars per local day, oldest first, with empty days included.",
        "inputSchema": {"type": "object", "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 14}},
            "additionalProperties": False},
    },
    {
        "name": "budget_status",
        "description": ("The user's codexwatch budgets (dollar, token or plan-window caps) with how much of "
                        "each is used and whether it is near or over its limit."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ToolError(Exception):
    """A tool ran and failed; reported to the model as a tool result, not a protocol error."""


def _round(obj):
    if isinstance(obj, float):
        return round(obj, 4)
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round(v) for v in obj]
    return obj


def _trim_totals(t: dict) -> dict:
    keep = ("input", "cached", "cache_write", "output", "reasoning", "total", "cost", "plan_cost",
            "unpriced_tokens", "unpriced_models", "requests", "sessions", "cache_hit", "first", "last")
    return {k: t[k] for k in keep if k in t}


def _trim_group(rows: list[dict]) -> list[dict]:
    return [{"name": r["key"], "total_tokens": r["total"], "cost": r["cost"], "requests": r["requests"]}
            for r in rows]


class Server:
    def __init__(self, store=None):
        self._store = store

    @property
    def store(self):
        if self._store is None:
            from .store import open_store

            self._store = open_store()
        return self._store

    def _scan(self) -> None:
        try:
            self.store.scan()
        except Exception as e:  # noqa: BLE001 - answer from what is stored rather than fail
            print(f"codexwatch mcp: scan failed: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ tools

    def tool_usage_summary(self, args: dict) -> dict:
        period = args.get("period") or "today"
        if period not in ("today", "7d", "30d", "month", "all"):
            raise ToolError(f"unknown period '{period}'")
        since, until, label = periods.parse(period)
        f = reports.Filter(since=since, until=until)
        return {
            "period": label,
            "totals": _trim_totals(reports.totals(self.store, f)),
            "top_models": _trim_group(reports.group(self.store, "model", f, limit=5)),
            "top_projects": _trim_group(reports.group(self.store, "project", f, limit=5)),
            "note": "Dollar figures are API-equivalent estimates; ChatGPT plans do not bill per token.",
        }

    def tool_plan_limits(self, args: dict) -> dict:
        cur = limits.current(self.store)
        accts = hop.accounts_view()
        head = snapshot.headline(cur)
        snapshot.overlay_live(accts, head)
        nxt = hop.best_next(accts) if len(accts) > 1 else None
        return {
            "plan": snapshot.plan_info(head.get("plan")),
            "limits": cur,
            "codexhop_accounts": accts,
            "most_room": nxt["name"] if nxt else None,
            "note": ("Readings come from the newest Codex session log; usage elsewhere since then "
                     "(other machines, ChatGPT) is not in them yet."),
        }

    def tool_quota_forensics(self, args: dict) -> dict:
        window = args.get("window")
        if window not in (None, "5h", "week"):
            raise ToolError(f"unknown window '{window}'")
        minutes = {"5h": 300, "week": 10080}.get(window)
        fx = limits.forensics(self.store, "codex", minutes, limit=10)
        return {
            "typical_usd_per_pct": {str(k): v for k, v in fx["typical_usd_per_pct"].items()},
            "windows": fx["windows"],
            "unexplained_rises": fx["jumps"],
            "already_used_before_first_reading": fx["already_used"],
            "daily_exchange_rate": fx["timeline"][-14:],
            "note": "usd figures count fast mode at 2.5x, the rate at which it uses plan limits.",
        }

    def tool_sessions(self, args: dict) -> dict:
        limit = int(args.get("limit") or 10)
        return {"sessions": reports.sessions(self.store, reports.Filter(), limit=max(1, min(limit, 100)))}

    def tool_daily(self, args: dict) -> dict:
        days = int(args.get("days") or 14)
        rows = reports.daily_series(self.store, max(1, min(days, 365)))
        return {"days": [{"day": r["key"], "total_tokens": r["total"], "cost": r["cost"],
                          "requests": r["requests"], "unpriced_tokens": r["unpriced_tokens"]} for r in rows]}

    def tool_budget_status(self, args: dict) -> dict:
        rows = budgets.status(self.store)
        return {"budgets": rows,
                "note": None if rows else "No budgets set. The user can add one with `codexwatch budget add`."}

    # --------------------------------------------------------------- protocol

    def handle(self, msg) -> dict | None:
        """One JSON-RPC message in, one response (or None for a notification) out."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        mid = msg.get("id")
        method = msg["method"]
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        is_note = "id" not in msg
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            asked = params.get("protocolVersion")
            result = {
                "protocolVersion": asked if asked in PROTOCOLS else DEFAULT_PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "codexwatch", "version": __version__},
                "instructions": ("Answers questions about this user's OpenAI Codex usage from their local "
                                 "session logs: tokens, API-equivalent dollars, plan limits and budgets."),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            fn = getattr(self, f"tool_{name}", None) if isinstance(name, str) else None
            if fn is None or name not in {t["name"] for t in TOOLS}:
                return None if is_note else _error(mid, -32602, f"unknown tool '{name}'")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            self._scan()
            try:
                data = _round(fn(args))
                text = json.dumps(data, separators=(",", ":"), default=str)
                result = {"content": [{"type": "text", "text": text}],
                          "structuredContent": json.loads(text), "isError": False}
            except (ToolError, ValueError) as e:
                result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            except Exception as e:  # noqa: BLE001 - a tool bug must not kill the server
                traceback.print_exc(file=sys.stderr)
                result = {"content": [{"type": "text", "text": f"codexwatch failed: {e}"}], "isError": True}
        else:
            return None if is_note else _error(mid, -32601, f"method not found: {method}")
        return None if is_note else {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None, server: Server | None = None) -> int:
    stdin = stdin or sys.stdin
    out = stdout or sys.stdout
    server = server or Server()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "parse error")
        else:
            # Anything a tool prints must not reach the protocol stream.
            with contextlib.redirect_stdout(sys.stderr):
                if isinstance(msg, list):
                    replies = [r for r in (server.handle(m) for m in msg) if r is not None]
                    reply = replies or None
                    if not msg:
                        reply = _error(None, -32600, "empty batch")
                else:
                    reply = server.handle(msg)
        if reply is not None:
            out.write(json.dumps(reply, separators=(",", ":"), default=str) + "\n")
            out.flush()
    return 0


def cmd_mcp(a) -> int:
    if sys.stdin.isatty():
        print("codexwatch mcp speaks MCP on stdin and stdout; add it to a client instead of running it here:\n"
              "  codex mcp add codexwatch -- codexwatch mcp\n"
              "  claude mcp add codexwatch -- codexwatch mcp", file=sys.stderr)
    try:
        return serve()
    except KeyboardInterrupt:
        return 0
