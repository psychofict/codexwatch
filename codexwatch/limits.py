"""Plan limits from the logs, and what each percentage point of them cost.

Codex writes the plan's rate-limit reading into the session log after every
response: how full each window is, how long the window is and when it
resets. codexwatch reads those, so the live 5-hour and weekly figures need no
network call and no login.

Quota forensics. Because every reading follows a known response, the tokens
spent inside a window can be set against how far the window moved. That
gives an exchange rate, dollars of API-equivalent usage per percentage
point, per window and per day, which shows when the same work started
costing more of the plan. A jump in the window that the tokens in these logs
do not account for is flagged: that usage came from somewhere the logs do not
see (another machine, ChatGPT itself, cloud tasks) or the metering changed.

Readings from two sessions of one account can arrive a few seconds out of
order, so the window's level is the running maximum of its readings.
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from datetime import datetime

MIN_PCT_FOR_RATE = 2.0     # a window this empty says nothing about its rate
JUMP_MIN_PCT = 3.0         # a rise smaller than this is never flagged
JUMP_FACTOR = 3.0          # flagged when the rise is this many times what local usage explains
BURN_SPAN_S = 3600         # burn rate is measured over the last hour


def window_label(minutes: int | None) -> str:
    if not minutes:
        return "window"
    if minutes == 300:
        return "5h"
    if minutes == 10080:
        return "week"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _envelope(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out, top = [], -1.0
    for ts, pct in sorted(points):
        if pct > top:
            top = pct
            out.append((ts, pct))
    return out


def _local_usage(store, limit_id, minutes, anchor, since=None, until=None) -> dict:
    parts = ["l.limit_id=?", "l.window_minutes IS ?", "l.win_reset=?"]
    args = [limit_id, minutes, anchor]
    if since is not None:
        parts.append("l.ts > ?")
        args.append(since)
    if until is not None:
        parts.append("l.ts <= ?")
        args.append(until)
    r = store.one(
        "SELECT SUM(u.total) tokens, SUM(u.cost) cost, SUM(u.plan_cost) plan_cost, COUNT(*) requests "
        "FROM usage u WHERE u.response_id IN (SELECT DISTINCT l.response_id FROM limits l WHERE "
        + " AND ".join(parts) + ")", args)
    return {"tokens": r["tokens"] or 0, "cost": r["cost"] or 0.0, "plan_cost": r["plan_cost"] or 0.0,
            "requests": r["requests"] or 0}


def current(store, now: float | None = None) -> list[dict]:
    """Every limit's windows as they stand now, from the newest readings."""
    now = now or time.time()
    out = []
    latest = store.q("SELECT limit_id, MAX(ts) ts FROM limits WHERE window_minutes IS NOT NULL GROUP BY limit_id")
    for lim in sorted(latest, key=lambda r: (r["limit_id"] != "codex", -r["ts"])):
        rows = store.q(
            "SELECT * FROM limits WHERE limit_id=? AND ts=(SELECT MAX(ts) FROM limits WHERE limit_id=?) "
            "ORDER BY window_minutes", (lim["limit_id"], lim["limit_id"]))
        windows = []
        for r in rows:
            windows.append(_window_now(store, r, now))
        windows.sort(key=lambda w: w["minutes"] or 0)
        name = next((r["limit_name"] for r in rows if r["limit_name"]), None)
        out.append({"limit_id": lim["limit_id"], "name": name, "plan": rows[0]["plan"] if rows else None,
                    "read_at": lim["ts"], "age_s": now - lim["ts"], "windows": windows,
                    "credits": rows[0]["credits"] if rows else None})
    return out


def _window_now(store, r, now: float) -> dict:
    minutes, anchor = r["window_minutes"], r["win_reset"]
    resets = r["resets_at"]
    pct = r["used_pct"]
    was = None
    if resets and resets <= now:
        was, pct, resets = pct, 0.0, None
    elif anchor is not None:
        top = store.one("SELECT MAX(used_pct) p FROM limits WHERE limit_id=? AND window_minutes IS ? AND win_reset=?",
                        (r["limit_id"], minutes, anchor))
        pct = max(pct, top["p"] or 0)
    acct = None
    if anchor is not None:
        w = store.one("SELECT account FROM windows WHERE limit_id=? AND window_minutes IS ? AND resets_at=?",
                      (r["limit_id"], minutes, anchor))
        acct = w["account"] if w else None
    since_reset = _local_usage(store, r["limit_id"], minutes, anchor) if anchor is not None and was is None else \
        {"tokens": 0, "cost": 0.0, "plan_cost": 0.0, "requests": 0}
    rate = burn_rate(store, r["limit_id"], minutes, anchor, now) if anchor is not None and was is None else None
    eta = None
    if rate and rate > 0 and pct < 100:
        eta = now + (100 - pct) / rate * 3600
        if resets and eta >= resets:
            eta = None  # the window resets before it would fill
    return {
        "minutes": minutes, "label": window_label(minutes), "slot": r["slot"], "pct": pct, "was": was,
        "resets_at": resets, "resets_in_s": (resets - now) if resets else None,
        "starts_at": (anchor - minutes * 60) if (anchor and minutes) else None,
        "account": acct, "since_reset": since_reset, "burn_pct_per_h": rate, "full_at": eta,
        "reached": r["reached"],
    }


def burn_rate(store, limit_id, minutes, anchor, now: float | None = None) -> float | None:
    """Percentage points per hour over the last hour of readings, or None."""
    now = now or time.time()
    pts = _envelope([(x["ts"], x["used_pct"]) for x in store.q(
        "SELECT ts, used_pct FROM limits WHERE limit_id=? AND window_minutes IS ? AND win_reset=? AND ts >= ?",
        (limit_id, minutes, anchor, now - BURN_SPAN_S * 1.5))])
    if len(pts) < 2:
        return None
    span = max(pts[-1][0], now - 60) - pts[0][0]
    if span < 600:
        return None
    return (pts[-1][1] - pts[0][1]) / (span / 3600)


def _plan_name(plan: str | None) -> str | None:
    from . import pricing

    return (pricing.load().plans.get(plan or "") or {}).get("name") or plan


def windows(store, limit_id: str = "codex", minutes: int | None = None, limit: int = 50) -> list[dict]:
    """Past and current windows, newest first, each with what it cost."""
    parts, args = ["limit_id=?", "max_pct > 0"], [limit_id]
    if minutes:
        parts.append("window_minutes=?")
        args.append(minutes)
    rows = store.q(f"SELECT * FROM windows WHERE {' AND '.join(parts)} ORDER BY resets_at DESC LIMIT ?",
                   [*args, limit])
    out = []
    for w in rows:
        use = _local_usage(store, limit_id, w["window_minutes"], w["resets_at"])
        pct = w["max_pct"] or 0
        out.append({
            "limit_id": limit_id, "minutes": w["window_minutes"], "label": window_label(w["window_minutes"]),
            "starts_at": w["resets_at"] - (w["window_minutes"] or 0) * 60, "resets_at": w["resets_at"],
            "account": w["account"], "plan": w["plan"], "plan_name": _plan_name(w["plan"]), "max_pct": pct,
            "first_ts": w["first_ts"], "last_ts": w["last_ts"], **use,
            "usd_per_pct": (use["plan_cost"] / pct) if pct >= MIN_PCT_FOR_RATE and use["plan_cost"] else None,
            "tokens_per_pct": (use["tokens"] / pct) if pct >= MIN_PCT_FOR_RATE and use["tokens"] else None,
        })
    return out


def forensics(store, limit_id: str = "codex", minutes: int | None = None, limit: int = 20) -> dict:
    """Exchange rate per window and per day, and rises the local logs cannot explain."""
    wins = windows(store, limit_id, minutes, limit=200)
    by_len: dict[int, list[float]] = defaultdict(list)
    by_plan: dict[tuple, list[float]] = defaultdict(list)
    for w in wins:
        if w["usd_per_pct"] and w["max_pct"] >= 10:
            by_len[w["minutes"]].append(w["usd_per_pct"])
            by_plan[(w["minutes"], w["plan"])].append(w["usd_per_pct"])
    typical = {m: statistics.median(v) for m, v in by_len.items() if v}
    typical_plan = {k: statistics.median(v) for k, v in by_plan.items() if v}
    # Plans differ by an order of magnitude, so a window is compared with
    # earlier windows of its own plan; other windows of its length are the fallback.
    for w in wins:
        w["typical_usd_per_pct"] = typical_plan.get((w["minutes"], w["plan"]))

    jumps, days = [], defaultdict(lambda: {"pct": 0.0, "plan_cost": 0.0, "tokens": 0})
    before = []
    for w in wins:
        pts = _envelope([(x["ts"], x["used_pct"]) for x in store.q(
            "SELECT ts, used_pct FROM limits WHERE limit_id=? AND window_minutes IS ? AND win_reset=? ORDER BY ts",
            (limit_id, w["minutes"], w["resets_at"]))])
        rate = w["usd_per_pct"] or w["typical_usd_per_pct"] or typical.get(w["minutes"])
        prev_ts, prev_pct = None, 0.0
        for ts, pct in pts:
            d_pct = pct - prev_pct
            use = _local_usage(store, limit_id, w["minutes"], w["resets_at"], since=prev_ts, until=ts)
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            key = (w["minutes"], w["plan"] or "", day)
            days[key]["pct"] += d_pct
            days[key]["plan_cost"] += use["plan_cost"]
            days[key]["tokens"] += use["tokens"]
            if prev_ts is None:
                # The window was already this full when this machine first read it.
                if d_pct >= JUMP_MIN_PCT:
                    before.append({"ts": ts, "label": w["label"], "account": w["account"], "plan": w["plan"],
                                   "pct": pct, "resets_at": w["resets_at"]})
                prev_ts, prev_pct = ts, pct
                continue
            if rate and d_pct >= JUMP_MIN_PCT:
                explained = use["plan_cost"] / rate
                if d_pct > max(1.0, explained) * JUMP_FACTOR:
                    jumps.append({
                        "ts": ts, "from_ts": prev_ts, "label": w["label"], "account": w["account"],
                        "from_pct": prev_pct, "to_pct": pct, "rise": d_pct,
                        "explained_pct": round(explained, 1), "local_plan_cost": use["plan_cost"],
                        "local_tokens": use["tokens"], "minutes": (ts - prev_ts) / 60,
                    })
            prev_ts, prev_pct = ts, pct
    timeline = []
    for (mins, plan, day), v in sorted(days.items(), key=lambda kv: (kv[0][2], kv[0][0])):
        if v["pct"] >= 1:
            timeline.append({"day": day, "minutes": mins, "label": window_label(mins), "plan": plan or None,
                             "pct": round(v["pct"], 1),
                             "plan_cost": v["plan_cost"], "tokens": v["tokens"],
                             "usd_per_pct": v["plan_cost"] / v["pct"] if v["plan_cost"] else None})
    jumps.sort(key=lambda j: -j["ts"])
    before.sort(key=lambda b: -b["ts"])
    return {"limit_id": limit_id, "typical_usd_per_pct": typical,
            "typical_by_plan": [{"minutes": m, "label": window_label(m), "plan": p, "usd_per_pct": v}
                                for (m, p), v in sorted(typical_plan.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))],
            "windows": wins[:limit],
            "timeline": timeline, "jumps": jumps[:limit], "already_used": before[:limit]}
