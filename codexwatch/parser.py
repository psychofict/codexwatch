"""Read Codex rollout logs into usage, limit readings, turns and tool calls.

A rollout is one JSON object per line, appended as a session runs:

  {"timestamp": ..., "ordinal": N, "type": "<kind>", "payload": {"type": "<event>", ...}}

What this module takes from it, and why each choice was made:

* Tokens come from `token_usage_record` lines, one per model response, keyed by
  the response id. A response is counted once however many files hold a copy
  of it: a forked or subagent session starts with its parent's history copied
  in, and Codex repeats `token_count` events, which is how other trackers come
  to count the same tokens two or more times.
* Logs written before `token_usage_record` existed only have `token_count`
  events with running totals. For those the difference between successive
  totals is the usage, and an unchanged total is a repeat, not a request.
* Each `token_count` event also carries the plan's rate-limit reading taken
  right after the response before it. That reading is kept with the id of
  that response, which is what lets `codexwatch quota` say what each
  percentage point of the plan cost.
* The model and reasoning effort come from the `turn_context` of the turn the
  response belongs to; the service tier (fast mode shows as "priority") from
  the latest `thread_settings_applied`.

Lines are filtered on their first bytes before any JSON is decoded. The large
lines (tool output, reasoning, compacted history) are never decoded, which
keeps a scan of gigabytes of logs to seconds. A scan can resume at a byte
offset with a small saved state, so a session that grows is never re-read.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

HEAD = 240
_KIND = re.compile(rb'"type":"([a-z_]+)","payload":\{(?:"type":"([a-z_]+)")?')
_TOOL = re.compile(rb'await tools\.([A-Za-z_][A-Za-z0-9_]*)\(')
_FN_NAME = re.compile(rb'"name":"([^"]{1,120})"')
_ORDINAL = re.compile(rb'"ordinal":(\d+)')
_TS = re.compile(rb'"timestamp":"([^"]{10,40})"')
WANTED_EVENTS = {"token_count", "task_started", "task_complete", "turn_aborted", "thread_settings_applied"}
TURN_MEMORY = 256


def parse_ts(value) -> float | None:
    """Seconds since the epoch from an ISO-8601 string or a number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Python before 3.11 accepts at most six fractional digits.
    m = re.match(r"^(.*?\.\d{1,6})\d*(.*)$", s)
    if m:
        s = m.group(1) + m.group(2)
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _head_ts(head: bytes) -> float | None:
    m = _TS.search(head)
    return parse_ts(m.group(1).decode()) if m else None


def _int(v) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _tokens(u: dict | None) -> dict:
    u = u if isinstance(u, dict) else {}
    out = {
        "input": _int(u.get("input_tokens")),
        "cached": _int(u.get("cached_input_tokens")),
        "cache_write": _int(u.get("cache_write_input_tokens")),
        "output": _int(u.get("output_tokens")),
        "reasoning": _int(u.get("reasoning_output_tokens")),
    }
    out["total"] = _int(u.get("total_tokens")) or out["input"] + out["output"]
    return out


def client_of(source, originator: str | None) -> str:
    """Which Codex surface a session ran in: cli, exec, ide, desktop, cloud or other."""
    s = source if isinstance(source, str) else ""
    o = (originator or "").lower()
    if s in ("cli", "exec", "mcp"):
        return s
    if s in ("vscode", "ide") or "vscode" in o or "jetbrains" in o:
        return "ide"
    if "exec" in o:
        return "exec"
    if "desktop" in o or "app" == s:
        return "desktop"
    if "cloud" in o or s == "cloud":
        return "cloud"
    if "tui" in o or "cli" in o:
        return "cli"
    return s or o or "other"


def project_of(cwd: str | None, git: dict | None) -> str:
    """A short project name: the git repository's name, else the folder's."""
    url = (git or {}).get("repository_url") if isinstance(git, dict) else None
    if isinstance(url, str) and url.strip():
        name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    if not cwd:
        return "(unknown)"
    norm = cwd.rstrip("/\\")
    if norm == os.path.expanduser("~").rstrip("/\\"):
        return "~"
    return re.split(r"[/\\]", norm)[-1] or norm


def new_state() -> dict:
    return {
        "sid": None, "model": None, "effort": None, "tier": None, "cwd": None,
        "turns": {}, "last_rid": None, "has_record": False, "fb_total": None,
        "project": None, "client": None, "subagent": False,
    }


class Batch:
    """Everything one scan of one file found."""

    def __init__(self):
        self.session: dict | None = None
        self.usage: list[dict] = []
        self.limits: list[dict] = []
        self.turns: list[dict] = []
        self.tools: list[dict] = []
        self.compactions: list[dict] = []
        self.last_ts: float | None = None

    def empty(self) -> bool:
        return not (self.session or self.usage or self.limits or self.turns or self.tools or self.compactions)


def _limit_rows(rl: dict, ts: float, sid, rid) -> list[dict]:
    rows = []
    for slot in ("primary", "secondary"):
        w = rl.get(slot)
        if not isinstance(w, dict) or w.get("used_percent") is None:
            continue
        credits = rl.get("credits") if isinstance(rl.get("credits"), dict) else {}
        rows.append({
            "ts": ts, "session_id": sid, "response_id": rid,
            "limit_id": str(rl.get("limit_id") or "codex"),
            "limit_name": rl.get("limit_name") if isinstance(rl.get("limit_name"), str) else None,
            "plan": rl.get("plan_type") if isinstance(rl.get("plan_type"), str) else None,
            "slot": slot,
            "window_minutes": _int(w.get("window_minutes")) or None,
            "used_pct": float(w.get("used_percent")),
            "resets_at": float(w["resets_at"]) if isinstance(w.get("resets_at"), (int, float)) else None,
            "reached": rl.get("rate_limit_reached_type") if isinstance(rl.get("rate_limit_reached_type"), str) else None,
            "credits": str(credits.get("balance")) if credits.get("balance") is not None else None,
        })
    return rows


def scan(path: str, offset: int = 0, state: dict | None = None, max_bytes: int | None = None):
    """Parse a rollout from `offset`. Returns (Batch, new offset, new state).

    Only complete lines are read: a line still being written is left for the
    next scan, and the returned offset points at its start.
    """
    st = dict(state) if state else new_state()
    st["turns"] = dict(st.get("turns") or {})
    out = Batch()
    pos = offset
    with open(path, "rb") as fh:
        fh.seek(offset)
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break  # partial line; Codex is still writing it
            here = pos
            pos += len(line)
            try:
                _line(line, here, st, out)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue  # one malformed line never stops a scan
            if max_bytes and pos - offset >= max_bytes:
                break
    # Keep the turn map small; old turns no longer get responses.
    if len(st["turns"]) > TURN_MEMORY:
        st["turns"] = dict(list(st["turns"].items())[-TURN_MEMORY:])
    return out, pos, st


def _line(line: bytes, offset: int, st: dict, out: Batch) -> None:
    head = line[:HEAD]
    m = _KIND.search(head)
    if not m:
        return
    kind = m.group(1).decode()
    event = m.group(2).decode() if m.group(2) else None

    if kind == "response_item":
        if event == "custom_tool_call":
            names = [n.decode() for n in _TOOL.findall(line[:65536])]
            if not names:
                # Code mode ran code that called no tool, such as plain JavaScript.
                names = ["code_mode"]
        elif event == "function_call":
            fn = _FN_NAME.search(line[:4096])
            names = [fn.group(1).decode()] if fn else []
        else:
            return
        if names and st.get("sid"):
            om = _ORDINAL.search(head)
            ts = _head_ts(head)
            key = om.group(1).decode() if om else str(offset)
            for i, n in enumerate(names):
                out.tools.append({"key": f"{st['sid']}:{key}:{i}", "ts": ts, "session_id": st["sid"],
                                  "name": n})
        return

    if kind == "compacted":
        om = _ORDINAL.search(head)
        ts = _head_ts(head)
        if st.get("sid"):
            out.compactions.append({"key": f"{st['sid']}:{om.group(1).decode() if om else offset}",
                                    "ts": ts, "session_id": st["sid"]})
        return

    if kind == "event_msg" and event not in WANTED_EVENTS:
        return
    if kind not in ("session_meta", "turn_context", "token_usage_record", "event_msg"):
        return

    obj = json.loads(line)
    p = obj.get("payload") or {}
    ts = parse_ts(obj.get("timestamp"))
    if ts:
        out.last_ts = ts

    if kind == "session_meta":
        if st.get("sid"):
            return  # a later header is the parent's, copied into a fork or subagent
        source = p.get("source")
        sub = isinstance(source, dict) and "subagent" in source
        spawn = source.get("subagent", {}).get("thread_spawn", {}) if sub and isinstance(source.get("subagent"), dict) else {}
        st["sid"] = p.get("id") or p.get("session_id")
        st["cwd"] = p.get("cwd")
        st["project"] = project_of(p.get("cwd"), p.get("git"))
        st["client"] = client_of(source, p.get("originator"))
        st["subagent"] = bool(sub)
        git = p.get("git") if isinstance(p.get("git"), dict) else {}
        out.session = {
            "session_id": st["sid"],
            "started": parse_ts(p.get("timestamp")) or ts,
            "cwd": p.get("cwd"),
            "project": st["project"],
            "repo": git.get("repository_url"),
            "branch": git.get("branch"),
            "client": st["client"],
            "originator": p.get("originator"),
            "cli_version": p.get("cli_version"),
            "provider": p.get("model_provider"),
            "parent_id": p.get("parent_thread_id") or spawn.get("parent_thread_id"),
            "forked_from": p.get("forked_from_id"),
            "nickname": p.get("agent_nickname") or spawn.get("agent_nickname"),
            "subagent": 1 if sub else 0,
        }
        return

    if kind == "turn_context":
        tid = p.get("turn_id")
        model = p.get("model") or st.get("model")
        effort = p.get("effort") or p.get("reasoning_effort") or st.get("effort")
        st["model"], st["effort"] = model, effort
        if p.get("cwd"):
            st["cwd"] = p["cwd"]
        if tid:
            st["turns"][tid] = [model, effort]
        return

    if kind == "token_usage_record":
        st["has_record"] = True
        rid = p.get("response_id")
        tid = p.get("turn_id")
        model, effort = (st["turns"].get(tid) or [st.get("model"), st.get("effort")])
        tok = _tokens(p.get("usage"))
        if not rid:
            rid = f"rec:{p.get('thread_id') or st.get('sid')}:{tid}:{obj.get('ordinal', offset)}"
        st["last_rid"] = rid
        out.usage.append(_usage_row(rid, ts, st, tid, model, effort, tok, "record"))
        return

    # event_msg
    if event == "thread_settings_applied":
        ts_ = p.get("thread_settings") or {}
        if ts_.get("model"):
            st["model"] = ts_["model"]
        if "service_tier" in ts_:
            st["tier"] = ts_.get("service_tier")
        if ts_.get("reasoning_effort"):
            st["effort"] = ts_["reasoning_effort"]
        return

    if event == "token_count":
        info = p.get("info") if isinstance(p.get("info"), dict) else None
        rid = st.get("last_rid")
        if info and not st.get("has_record"):
            # Older Codex: no per-response record, only running totals.
            total = _tokens(info.get("total_token_usage"))
            prev = st.get("fb_total")
            if prev is None or total["total"] != prev.get("total"):
                if prev is None or total["total"] < prev.get("total", 0):
                    delta = _tokens(info.get("last_token_usage")) if prev else total
                else:
                    delta = {k: max(0, total[k] - prev.get(k, 0)) for k in total}
                st["fb_total"] = total
                rid = f"fb:{obj.get('timestamp')}:{total['total']}"
                st["last_rid"] = rid
                out.usage.append(_usage_row(rid, ts, st, None, st.get("model"), st.get("effort"), delta, "totals"))
        rl = p.get("rate_limits")
        if isinstance(rl, dict):
            out.limits.extend(_limit_rows(rl, ts, st.get("sid"), rid))
        return

    if event in ("task_started", "task_complete", "turn_aborted"):
        tid = p.get("turn_id")
        if not tid:
            return
        model = (st["turns"].get(tid) or [st.get("model")])[0]
        out.turns.append({
            "turn_id": tid, "session_id": st.get("sid"), "model": model,
            "started": parse_ts(p.get("started_at")) or (ts if event == "task_started" else None),
            "completed": parse_ts(p.get("completed_at")) if event != "task_started" else None,
            "duration_ms": _int(p.get("duration_ms")) or None,
            "ttft_ms": _int(p.get("time_to_first_token_ms")) or None,
            "aborted": 1 if event == "turn_aborted" else 0,
        })


def _usage_row(rid, ts, st, tid, model, effort, tok, how) -> dict:
    return {
        "response_id": rid, "ts": ts, "session_id": st.get("sid"), "turn_id": tid,
        "model": model, "effort": effort, "tier": st.get("tier") or "default",
        "project": st.get("project"), "client": st.get("client"), "subagent": 1 if st.get("subagent") else 0,
        **tok, "how": how,
    }
