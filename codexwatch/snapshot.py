"""One summary of where things stand, shared by every live surface.

The tray, the status-bar formats, the live view, the MCP server and the
dashboard all call state(), so they never disagree about a number.
"""

from __future__ import annotations

import time

from . import config, hop, limits, periods, pricing, reports


def plan_info(plan_type: str | None) -> dict:
    """The plan's display name and monthly price (yours, if you set plan_price)."""
    card = pricing.load()
    row = card.plans.get(plan_type or "", {})
    own = config.get("plan_price")
    price = float(own) if own not in (None, "") else row.get("usd")
    return {"type": plan_type, "name": row.get("name") or (plan_type.title() if plan_type else None),
            "usd": price, "price_source": "config" if own not in (None, "") else ("published" if price else None)}


def main_limit(cur: list[dict]) -> dict | None:
    return next((c for c in cur if c["limit_id"] == "codex"), cur[0] if cur else None)


def headline(cur: list[dict]) -> dict:
    """The two numbers most surfaces show: the 5-hour and weekly window."""
    m = main_limit(cur)
    out = {"short": None, "long": None, "plan": None, "read_at": None}
    if not m:
        return out
    wins = sorted(m["windows"], key=lambda w: w["minutes"] or 0)
    out["plan"] = m["plan"]
    out["read_at"] = m["read_at"]
    if wins:
        out["short"] = wins[0]
    if len(wins) > 1:
        out["long"] = wins[-1]
    return out


def overlay_live(accts: list[dict], head: dict) -> None:
    """The live account's tile shows the logs' reading when it is newer than codexhop's."""
    live = next((a for a in accts if a["active"]), None)
    if not live or not head.get("read_at") or (live.get("fetched_at") or 0) >= head["read_at"]:
        return
    for key, w in (("short", head.get("short")), ("long", head.get("long"))):
        if w:
            live[key] = {"pct": w["pct"], "resetsAt": w.get("resets_at"), "seconds": (w.get("minutes") or 0) * 60}
    live["fetched_at"] = head["read_at"]
    live["age_s"] = time.time() - head["read_at"]
    live["source"] = "logs"
    if head.get("plan"):
        live["plan"] = head["plan"]


def state(store, scan: bool = True, days: int = 30) -> dict:
    if scan:
        store.scan()
    now = time.time()
    cur = limits.current(store, now)
    accts = hop.accounts_view(now)
    head = headline(cur)
    overlay_live(accts, head)
    out = {
        "generated_at": now,
        "live_account": next((a["name"] for a in accts if a["active"]), None),
        "plan": plan_info(head["plan"]),
        "headline": head,
        "limits": cur,
        "accounts": accts,
        "next_account": (hop.best_next(accts) or {}).get("name") if len(accts) > 1 else None,
        "periods": reports.overview(store),
        "daily": reports.daily_series(store, days),
        "settings": {k: config.get(k) for k in ("warn_pct", "alert_pct")},
    }
    since30 = periods.parse(f"{days}d")[0]
    f30 = reports.Filter(since=since30)
    out["models"] = reports.group(store, "model", f30, limit=8)
    out["projects"] = reports.group(store, "project", f30, limit=8)
    try:
        from . import budgets

        out["budgets"] = budgets.status(store)
    except Exception:  # noqa: BLE001 - budgets are optional; a bad one must not hide the rest
        out["budgets"] = []
    return out


def level(p: float | None) -> str:
    """ok, warn or alert for a window percentage, by the configured thresholds."""
    if p is None:
        return "none"
    if p >= float(config.get("alert_pct") or 90):
        return "alert"
    if p >= float(config.get("warn_pct") or 70):
        return "warn"
    return "ok"
