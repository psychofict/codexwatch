"""Spending caps: dollars or tokens per day, week or month, or a plan window level.

A budget is a limit and a period. Dollar and token budgets add up the
API-equivalent usage in the history database over the current local
calendar period (today, this week from Monday, this month), optionally for
one project or one model. A percent budget watches the plan's own 5-hour or
weekly window, read from the logs, because that is the limit that actually
stops Codex on a ChatGPT plan.

Every budget has a notify level (80% of the limit unless set): `status`
marks a budget `notify` once it reaches that level and `over` once it reaches
the limit. Sending the desktop notification is notify.py's job; this module
only measures.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta

from . import fmt, limits, reports, snapshot

KINDS = ("usd", "tokens", "pct")
CALENDAR = ("day", "week", "month")
WINDOWS = ("5h", "week-window")


def period_start(period: str, now: float | None = None) -> float:
    """Start of the current calendar day, week (from Monday) or month, local time."""
    today = datetime.fromtimestamp(now or time.time()).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return today.timestamp()
    if period == "week":
        return (today - timedelta(days=today.weekday())).timestamp()
    if period == "month":
        return today.replace(day=1).timestamp()
    raise ValueError(f"unknown budget period '{period}'; use day, week or month")


def parse_scope(scope: str | None) -> tuple[str | None, str | None]:
    """("project", NAME), ("model", ID) or (None, None) for everything."""
    if not scope:
        return None, None
    kind, sep, value = scope.partition(":")
    if not sep or kind not in ("project", "model") or not value:
        raise ValueError(f"scope must be project:NAME or model:ID, not '{scope}'")
    return kind, value


# ------------------------------------------------------------------- storage

def add(store, name: str | None, kind: str, amount: float, period: str, scope: str | None = None,
        notify_pct: float = 80) -> int:
    if kind not in KINDS:
        raise ValueError(f"budget kind must be one of {', '.join(KINDS)}")
    if amount is None or amount <= 0:
        raise ValueError("a budget needs a limit above zero")
    if kind == "pct":
        if period not in WINDOWS:
            raise ValueError("a percent budget watches a plan window: 5h or week-window")
        if amount > 100:
            raise ValueError("a plan window cannot pass 100%")
        if scope:
            raise ValueError("a percent budget covers the whole plan; it takes no scope")
    elif period not in CALENDAR:
        raise ValueError("a dollar or token budget runs per day, week or month")
    parse_scope(scope)
    if not 0 < notify_pct <= 100:
        raise ValueError("--notify-pct must be between 0 and 100")
    name = name or _default_name(kind, amount, period, scope)
    with store.lock:
        cur = store.db.execute(
            "INSERT INTO budgets(name,kind,amount,period,scope,notify_pct,created) VALUES(?,?,?,?,?,?,?)",
            (name, kind, float(amount), period, scope, float(notify_pct), time.time()))
        store.db.commit()
        return int(cur.lastrowid)


def _default_name(kind, amount, period, scope) -> str:
    if kind == "pct":
        what = f"{amount:g}% of the {'5-hour' if period == '5h' else 'weekly'} window"
    elif kind == "usd":
        what = f"${amount:g} a {period}"
    else:
        what = f"{fmt.tokens(amount)} tokens a {period}"
    return what + (f" for {scope.split(':', 1)[1]}" if scope else "")


def remove(store, budget_id: int | None = None, name: str | None = None) -> int:
    with store.lock:
        if budget_id is not None:
            cur = store.db.execute("DELETE FROM budgets WHERE id=?", (budget_id,))
        elif name:
            cur = store.db.execute("DELETE FROM budgets WHERE name=?", (name,))
        else:
            raise ValueError("say which budget: --id N or --name NAME")
        store.db.commit()
        return cur.rowcount


def all_budgets(store) -> list[dict]:
    return [dict(r) for r in store.q("SELECT * FROM budgets ORDER BY id")]


# ------------------------------------------------------------------- measure

def status(store, now: float | None = None) -> list[dict]:
    """Every budget with how much of it is used now."""
    now = now or time.time()
    rows = all_budgets(store)
    if not rows:
        return []
    head = None
    out = []
    for b in rows:
        kind, period, limit = b["kind"], b["period"], float(b["amount"])
        used, resets = 0.0, None
        if kind == "pct":
            if head is None:
                head = snapshot.headline(limits.current(store, now))
            w = head["short"] if period == "5h" else head["long"]
            if w:
                used = float(w["pct"] or 0)
                resets = w.get("resets_at")
        else:
            try:
                since = period_start(period, now)
                what, value = parse_scope(b["scope"])
            except ValueError:
                continue  # a row written by hand with a bad period or scope is skipped, not fatal
            f = reports.Filter(since=since, project=value if what == "project" else None,
                               model=value if what == "model" else None)
            t = reports.totals(store, f)
            used = float(t["cost"] if kind == "usd" else t["total"])
            if period == "day":
                resets = since + 86400
            elif period == "week":
                resets = since + 7 * 86400
            else:
                d = datetime.fromtimestamp(since)
                resets = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1).timestamp()
        pct = used / limit * 100 if limit else 0.0
        notify_pct = float(b["notify_pct"] if b["notify_pct"] is not None else 80)
        out.append({
            "id": b["id"], "name": b["name"], "kind": kind, "limit": limit, "used": used, "pct": pct,
            "notify_pct": notify_pct, "period": period, "scope": b["scope"], "resets_at": resets,
            "over": used >= limit, "notify": pct >= notify_pct,
        })
    return out


# ----------------------------------------------------------------------- CLI

def _amount(kind: str, v: float) -> str:
    if kind == "usd":
        return fmt.money(v)
    if kind == "tokens":
        return fmt.tokens(v)
    return f"{v:.0f}%"


def _period_text(p: str) -> str:
    return {"5h": "5-hour window", "week-window": "weekly window"}.get(p, f"per {p}")


def cmd_budget(a) -> int:
    from .store import open_store

    s = open_store()
    action = getattr(a, "action", None) or "list"
    if action == "add":
        given = [k for k in ("usd", "tokens", "pct") if getattr(a, k, None) is not None]
        if len(given) != 1:
            print("give one limit: --usd N, --tokens N or --pct N", file=sys.stderr)
            return 2
        kind = given[0]
        period = ("5h" if a.window == "5h" else "week-window") if kind == "pct" else a.every
        bid = add(s, a.name, kind, getattr(a, kind), period, a.scope, a.notify_pct)
        b = next(x for x in all_budgets(s) if x["id"] == bid)
        if a.json:
            print(json.dumps(b, indent=2))
        else:
            print(f"added budget {bid}: {b['name']} ({_period_text(period)}, "
                  f"alert at {b['notify_pct']:g}% of it)")
        return 0
    if action == "rm":
        n = remove(s, a.id, a.name)
        print("removed" if n else "no such budget")
        return 0 if n else 1
    if not getattr(a, "no_scan", False):
        s.scan()
    rows = status(s)
    if a.json:
        print(json.dumps(rows, indent=2, default=str))
        return 1 if action == "check" and any(r["over"] for r in rows) else 0
    if not rows:
        print("no budgets yet. Add one, for example: codexwatch budget add --usd 50 --every month")
        return 0
    out = []
    for r in rows:
        state = fmt.red("over", bold=True) if r["over"] else (
            fmt.paint("near", fmt.YELLOW) if r["notify"] else fmt.grey("ok"))
        out.append([str(r["id"]), r["name"][:36], _period_text(r["period"]),
                    f"{_amount(r['kind'], r['used'])} of {_amount(r['kind'], r['limit'])}",
                    f"{fmt.bar(min(r['pct'], 100), 16, r['notify_pct'], 100)} {r['pct']:>4.0f}%",
                    state, fmt.clock(r["resets_at"]) if r["resets_at"] else "-"])
    print(fmt.label("budgets"))
    print(fmt.table(["id", "budget", "period", "used", "", "", "resets"], out, "rlllll" + "l"))
    if action == "check" and any(r["over"] for r in rows):
        return 1
    return 0
