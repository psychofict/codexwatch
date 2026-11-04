"""Totals and breakdowns over the history database.

Every function takes a Filter and returns plain dicts, so the CLI, the
dashboard, the MCP server and the share card all show the same numbers.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SUMS = ("input", "cached", "cache_write", "output", "reasoning", "total")

GROUPS = {
    "day": "strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime')",
    "week": "strftime('%Y-W%W', ts, 'unixepoch', 'localtime')",
    "month": "strftime('%Y-%m', ts, 'unixepoch', 'localtime')",
    "hour": "CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER)",
    "weekday": "CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER)",
    "model": "COALESCE(model, '(unknown)')",
    "project": "COALESCE(project, '(unknown)')",
    "session": "session_id",
    "client": "COALESCE(client, 'other')",
    "tier": "COALESCE(tier, 'default')",
    "effort": "COALESCE(effort, '-')",
    "machine": "COALESCE(machine, '-')",
    "agent": "CASE WHEN subagent=1 THEN 'subagent' ELSE 'main' END",
}


@dataclass
class Filter:
    since: float | None = None
    until: float | None = None
    project: str | None = None
    model: str | None = None
    client: str | None = None
    machine: str | None = None
    account: str | None = None
    session: str | None = None
    extra: list = field(default_factory=list)

    def where(self, alias: str = "") -> tuple[str, list]:
        a = f"{alias}." if alias else ""
        parts, args = [], []
        if self.since is not None:
            parts.append(f"{a}ts >= ?")
            args.append(self.since)
        if self.until is not None:
            parts.append(f"{a}ts < ?")
            args.append(self.until)
        for col in ("project", "model", "client", "machine"):
            v = getattr(self, col)
            if v:
                parts.append(f"{a}{col} LIKE ?")
                args.append(v.replace("*", "%"))
        if self.session:
            parts.append(f"{a}session_id LIKE ?")
            args.append(self.session + "%")
        if self.account:
            parts.append(
                f"{a}response_id IN (SELECT l.response_id FROM limits l JOIN windows w ON w.limit_id=l.limit_id "
                "AND w.window_minutes=l.window_minutes AND w.resets_at=l.win_reset WHERE w.account LIKE ?)")
            args.append(self.account)
        return (" WHERE " + " AND ".join(parts)) if parts else "", args


def _row(r) -> dict:
    d = {k: (r[k] or 0) for k in SUMS}
    d["cost"] = r["cost"] or 0.0
    d["plan_cost"] = r["plan_cost"] or 0.0
    d["unpriced_tokens"] = r["unpriced"] or 0
    d["requests"] = r["requests"] or 0
    d["sessions"] = r["sessions"] or 0
    d["cache_hit"] = (d["cached"] / d["input"]) if d["input"] else 0.0
    return d


_AGG = ("SUM(input) input, SUM(cached) cached, SUM(cache_write) cache_write, SUM(output) output, "
        "SUM(reasoning) reasoning, SUM(total) total, SUM(cost) cost, SUM(plan_cost) plan_cost, "
        "SUM(CASE WHEN cost IS NULL THEN total ELSE 0 END) unpriced, COUNT(*) requests, "
        "COUNT(DISTINCT session_id) sessions")


def totals(store, f: Filter | None = None) -> dict:
    f = f or Filter()
    where, args = f.where()
    r = store.one(f"SELECT {_AGG}, MIN(ts) first, MAX(ts) last FROM usage{where}", args)
    out = _row(r)
    out["first"], out["last"] = r["first"], r["last"]
    out["unpriced_models"] = [x["model"] for x in store.q(
        f"SELECT DISTINCT COALESCE(model,'(unknown)') model FROM usage{where}"
        + (" AND" if where else " WHERE") + " cost IS NULL", args)]
    return out


def group(store, by: str, f: Filter | None = None, limit: int | None = None, order: str = "cost") -> list[dict]:
    if by not in GROUPS:
        raise ValueError(f"cannot group by '{by}'; choose from {', '.join(GROUPS)}")
    f = f or Filter()
    where, args = f.where()
    key = GROUPS[by]
    chrono = by in ("day", "week", "month", "hour", "weekday")
    sort = "k" if chrono else ("SUM(COALESCE(cost,0)) DESC, SUM(total) DESC" if order == "cost" else "SUM(total) DESC")
    sql = f"SELECT {key} k, {_AGG}, MIN(ts) first, MAX(ts) last FROM usage{where} GROUP BY k ORDER BY {sort}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for r in store.q(sql, args):
        d = _row(r)
        d["key"], d["first"], d["last"] = r["k"], r["first"], r["last"]
        rows.append(d)
    if by == "model":
        for d in rows:
            d["models_unpriced"] = d["unpriced_tokens"] > 0
    return rows


def daily_series(store, days: int = 30, f: Filter | None = None, now: float | None = None) -> list[dict]:
    """One row per local day for the last `days` days, zero-filled."""
    now = now or time.time()
    end = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days - 1)
    f = f or Filter()
    f2 = Filter(**{**f.__dict__, "since": start.timestamp(), "until": None})
    by_day = {r["key"]: r for r in group(store, "day", f2)}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        r = by_day.get(d) or {k: 0 for k in (*SUMS, "cost", "plan_cost", "unpriced_tokens", "requests", "sessions")}
        out.append({**r, "key": d})
    return out


def sessions(store, f: Filter | None = None, limit: int = 20) -> list[dict]:
    f = f or Filter()
    where, args = f.where("u")
    rows = store.q(
        f"SELECT u.session_id sid, s.title, s.project, s.client, s.started, s.subagent, s.nickname, s.parent_id, "
        f"s.cli_version, s.branch, MAX(u.ts) last, MIN(u.ts) first, "
        f"SUM(u.total) total, SUM(u.input) input, SUM(u.cached) cached, SUM(u.output) output, "
        f"SUM(u.reasoning) reasoning, SUM(u.cost) cost, COUNT(*) requests, "
        f"GROUP_CONCAT(DISTINCT u.model) models "
        f"FROM usage u LEFT JOIN sessions s ON s.session_id=u.session_id{where} "
        f"GROUP BY u.session_id ORDER BY last DESC LIMIT ?", [*args, limit])
    out = []
    for r in rows:
        extra = store.one("SELECT COUNT(*) turns, SUM(duration_ms) ms FROM turns WHERE session_id=?", (r["sid"],))
        comp = store.one("SELECT COUNT(*) n FROM compactions WHERE session_id=?", (r["sid"],))
        out.append({
            "session_id": r["sid"], "title": r["title"], "project": r["project"], "client": r["client"],
            "subagent": bool(r["subagent"]), "nickname": r["nickname"], "parent_id": r["parent_id"],
            "branch": r["branch"], "cli_version": r["cli_version"],
            "first": r["first"], "last": r["last"], "total": r["total"] or 0, "input": r["input"] or 0,
            "cached": r["cached"] or 0, "output": r["output"] or 0, "reasoning": r["reasoning"] or 0,
            "cost": r["cost"] or 0.0, "requests": r["requests"], "models": (r["models"] or "").split(","),
            "turns": extra["turns"] or 0, "busy_ms": extra["ms"] or 0, "compactions": comp["n"] or 0,
        })
    return out


def tools(store, f: Filter | None = None, limit: int = 30) -> list[dict]:
    f = f or Filter()
    parts, args = [], []
    if f.since is not None:
        parts.append("t.ts >= ?")
        args.append(f.since)
    if f.until is not None:
        parts.append("t.ts < ?")
        args.append(f.until)
    if f.project:
        parts.append("s.project LIKE ?")
        args.append(f.project.replace("*", "%"))
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    rows = store.q(f"SELECT t.name, COUNT(*) n, COUNT(DISTINCT t.session_id) sessions FROM tools t "
                   f"LEFT JOIN sessions s ON s.session_id=t.session_id{where} GROUP BY t.name ORDER BY n DESC LIMIT ?",
                   [*args, limit])
    total = sum(r["n"] for r in rows) or 1
    return [{"name": pretty_tool(r["name"]), "raw": r["name"], "calls": r["n"], "sessions": r["sessions"],
             "share": r["n"] / total} for r in rows]


def pretty_tool(name: str) -> str:
    """exec_command stays; an MCP tool such as web__run reads as web.run."""
    return name.replace("__", ".") if "__" in name else name


def latency(store, f: Filter | None = None) -> list[dict]:
    """Turn duration and time to first token per model, median and 90th percentile."""
    f = f or Filter()
    parts, args = ["duration_ms IS NOT NULL", "aborted=0"], []
    if f.since is not None:
        parts.append("started >= ?")
        args.append(f.since)
    if f.until is not None:
        parts.append("started < ?")
        args.append(f.until)
    by: dict[str, dict] = {}
    for r in store.q("SELECT COALESCE(model,'(unknown)') m, duration_ms, ttft_ms FROM turns WHERE "
                     + " AND ".join(parts), args):
        d = by.setdefault(r["m"], {"dur": [], "ttft": []})
        d["dur"].append(r["duration_ms"])
        if r["ttft_ms"]:
            d["ttft"].append(r["ttft_ms"])

    def pct(xs, q):
        if not xs:
            return None
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]

    out = []
    for m, d in by.items():
        out.append({"model": m, "turns": len(d["dur"]),
                    "median_s": (statistics.median(d["dur"]) / 1000) if d["dur"] else None,
                    "p90_s": (pct(d["dur"], 0.9) or 0) / 1000 if d["dur"] else None,
                    "ttft_median_s": (statistics.median(d["ttft"]) / 1000) if d["ttft"] else None})
    out.sort(key=lambda x: -x["turns"])
    return out


def heatmap(store, f: Filter | None = None) -> list[list[int]]:
    """7 x 24 grid of tokens, Monday first, local time."""
    grid = [[0] * 24 for _ in range(7)]
    f = f or Filter()
    where, args = f.where()
    for r in store.q(f"SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) wd, "
                     f"CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) h, SUM(total) t "
                     f"FROM usage{where} GROUP BY wd, h", args):
        grid[(r["wd"] - 1) % 7][r["h"]] = r["t"] or 0
    return grid


def streaks(store, f: Filter | None = None) -> dict:
    """Active days, the longest run of consecutive days, the current run and the busiest day."""
    days = [r["key"] for r in group(store, "day", f or Filter()) if r["total"]]
    if not days:
        return {"active_days": 0, "longest": 0, "current": 0, "busiest": None}
    dates = [datetime.strptime(d, "%Y-%m-%d").date() for d in days]
    longest = run = 1
    for a, b in zip(dates, dates[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        longest = max(longest, run)
    today = datetime.now().date()
    current = 0
    if dates[-1] >= today - timedelta(days=1):
        current = 1
        for a, b in zip(reversed(dates[:-1]), reversed(dates[1:])):
            if (b - a).days != 1:
                break
            current += 1
    busiest = max(group(store, "day", f or Filter()), key=lambda r: r["total"])
    return {"active_days": len(days), "longest": longest, "current": current,
            "busiest": {"day": busiest["key"], "total": busiest["total"], "cost": busiest["cost"]}}


def overview(store, f: Filter | None = None) -> dict:
    """The numbers for the default screen: today, this week, this month and all time."""
    from . import periods

    out = {}
    for p in ("today", "7d", "month", "all"):
        since, until, label = periods.parse(p)
        base = f or Filter()
        out[p] = {"label": label, **totals(store, Filter(**{**base.__dict__, "since": since, "until": until}))}
    return out
