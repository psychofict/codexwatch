"""Move to another of your own codexhop accounts when the live one is nearly spent.

Opt-in, dry run by default.

`codexwatch autohop` looks at the live account's plan windows and, when it is
nearly spent, picks another saved account with room and asks codexhop to
switch to it. Without --apply it only says what it would do. codexhop never
switches by itself on purpose, so this is the one place that does, and only
when you run it.

Every number is a default you can change in the
`autohop` block of config.json:

  1. Stay on the live account (sticky) until it reaches five_hour_hop_at (92%)
     of its 5-hour window or week_hop_at (97%) of its weekly window, or a
     reading says its limit was reached.
  2. Then hop to an account with at least min_free (25%) of its 5-hour window
     free, or min_free_at_limit (10%) when the live one is already at its
     limit. An account at week_hop_at or above, or whose reading is older than
     max_age_min (10 minutes) and was not just refreshed, is never chosen.
  3. Among the accounts with room, the one with the most room wins; within
     close_points (10) of each other, the one whose window resets soonest
     goes first, because what it has left is lost at the reset.
  4. At most one hop per cooldown_min (20 minutes), counted from the last
     change of codexhop's `active` file, so a hop you made by hand counts too.
     A live account at its limit is the exception: every request is failing,
     so waiting protects nothing.
  5. While the file `autohop.off` exists in the codexwatch folder, do nothing.

Only the accounts in `accounts` may be chosen (all saved accounts when it is
not set). Every decision is appended to autohop.log as one JSON line.
"""

from __future__ import annotations

import json
import os
import sys
import time

from . import config, fmt, hop, limits, paths, snapshot

DEFAULTS = {
    "five_hour_hop_at": 92.0,
    "week_hop_at": 97.0,
    "min_free": 25.0,
    "min_free_at_limit": 10.0,
    "cooldown_min": 20.0,
    "max_age_min": 10.0,
    "close_points": 10.0,
    "refresh_above": 80.0,
    "refresh_every_min": 5.0,
    "refresh": True,
    "accounts": None,
}


def settings() -> dict:
    own = config.get("autohop") or {}
    return {**DEFAULTS, **(own if isinstance(own, dict) else {})}


def _pct(win: dict | None) -> float | None:
    return float(win["pct"]) if isinstance(win, dict) and win.get("pct") is not None else None


def decide(readings: list[dict], now: float, cfg: dict | None = None, last_hop_ts: float | None = None,
           fresh: set | None = None) -> dict:
    """What to do now. Pure: no files, no network.

    readings are hop.accounts_view() rows: name, kind, active, short, long,
    limit_reached, age_s. Returns {action: stay|hop|wait, target, why, ...}.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    fresh = fresh or set()
    allow = cfg.get("accounts")
    pool = [r for r in readings if r.get("kind") != "provider" and (not allow or r["name"] in allow)]
    live = next((r for r in readings if r.get("active")), None)
    out = {"action": "stay", "target": None, "live": live["name"] if live else None, "why": "", "candidates": []}

    if live is None:
        out.update(action="wait", why="codexhop has no live account")
        return out
    if live.get("kind") == "provider":
        out["why"] = f"the live entry {live['name']} is a provider, not a ChatGPT login, so it is left alone"
        return out
    if allow and live["name"] not in allow:
        out["why"] = f"the live account {live['name']} is not on the autohop list, so it is left alone"
        return out
    if len(pool) < 2:
        out["why"] = "fewer than two accounts to choose between"
        return out

    s5, wk = _pct(live.get("short")), _pct(live.get("long"))
    at_limit = bool(live.get("limit_reached")) or (s5 or 0) >= 100 or (wk or 0) >= 100
    if s5 is None and wk is None and not at_limit:
        out["why"] = f"no reading yet for {live['name']}"
        return out
    out["live_short"], out["live_week"], out["at_limit"] = s5, wk, at_limit
    spent = (at_limit or (s5 or 0) >= cfg["five_hour_hop_at"] or (wk or 0) >= cfg["week_hop_at"])
    if not spent:
        out["why"] = (f"{live['name']} is at {fmt.pct(s5)} of its 5-hour window and {fmt.pct(wk)} weekly; "
                      f"below {cfg['five_hour_hop_at']:g}% / {cfg['week_hop_at']:g}%, so stay")
        return out

    need = cfg["min_free_at_limit"] if at_limit else cfg["min_free"]
    room = []
    for r in pool:
        if r["name"] == live["name"]:
            continue
        c5, cw = _pct(r.get("short")), _pct(r.get("long"))
        age = r.get("age_s")
        reason = None
        if r["name"] not in fresh and (age is None or age > cfg["max_age_min"] * 60):
            reason = "no reading" if age is None else f"reading {fmt.duration(age)} old"
        elif cw is not None and cw >= cfg["week_hop_at"]:
            reason = f"weekly {cw:.0f}%"
        elif r.get("limit_reached") and (c5 or 0) > 0:
            reason = "limit reached"
        elif 100 - (c5 or 0) < need:
            reason = f"5h {fmt.pct(c5)}, needs {need:g}% free"
        resets = (r.get("short") or {}).get("resetsAt")
        left = (resets - now) if resets and resets > now else float("inf")
        out["candidates"].append({"name": r["name"], "short": c5, "week": cw, "ok": reason is None,
                                  "why": reason or f"5h {fmt.pct(c5)}"})
        if reason is None:
            room.append((int((c5 or 0) // cfg["close_points"]), left, r["name"], c5))

    if not room:
        resets = [(r.get("short") or {}).get("resetsAt") for r in pool]
        resets = [t for t in resets if t and t > now]
        out.update(action="wait", why=f"{live['name']} is nearly spent and no other account has room"
                   + (f"; the first window resets at {fmt.clock(min(resets))}" if resets else ""))
        return out
    room.sort()
    target, t5 = room[0][2], room[0][3]
    out["target"] = target
    since = (now - last_hop_ts) if last_hop_ts else None
    corrective = at_limit and (t5 or 0) < cfg["five_hour_hop_at"]
    if since is not None and since < cfg["cooldown_min"] * 60 and not corrective:
        out.update(action="wait", why=f"{target} has room, but the last hop was {fmt.duration(since)} ago; "
                   f"one hop per {cfg['cooldown_min']:g} minutes")
        return out
    what = "at its limit" if at_limit else f"at {fmt.pct(s5)} of 5 hours, {fmt.pct(wk)} weekly"
    out.update(action="hop",
               why=f"{live['name']} is {what}; {target} has {100 - (t5 or 0):.0f}% of its 5-hour window free")
    return out


# --------------------------------------------------------------- live inputs

def last_hop_ts() -> float | None:
    try:
        return os.path.getmtime(os.path.join(hop.store_dir(), "active"))
    except OSError:
        return None


def readings(store, now: float | None = None) -> list[dict]:
    """codexhop's readings, with the live account's taken from the logs when those are newer.

    A log reading belongs to whichever account was live when the request was
    made, so it is only used when it was taken after the last hop.
    """
    now = now or time.time()
    rows = hop.accounts_view(now)
    live = next((r for r in rows if r["active"]), None)
    if live is None or live.get("kind") == "provider":
        return rows
    head = snapshot.headline(limits.current(store, now))
    read_at = head.get("read_at")
    hopped = last_hop_ts() or 0
    if read_at and read_at > hopped and read_at > (live.get("fetched_at") or 0):
        for key, w in (("short", head.get("short")), ("long", head.get("long"))):
            if w:
                live[key] = {"pct": w["pct"], "resetsAt": w.get("resets_at"), "seconds": (w.get("minutes") or 0) * 60}
        live["fetched_at"] = read_at
        live["age_s"] = now - read_at
        live["source"] = "logs"
        if any((w or {}).get("reached") for w in (head.get("short"), head.get("long"))):
            live["limit_reached"] = True
    return rows


def _state() -> dict:
    try:
        with open(paths.state_path("autohop-state.json"), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(doc: dict) -> None:
    paths.ensure_data_dir()
    tmp = paths.state_path("autohop-state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, paths.state_path("autohop-state.json"))


def _log(rec: dict) -> None:
    paths.ensure_data_dir()
    with open(paths.state_path("autohop.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def run_once(store, apply: bool = False, scan: bool = True, now: float | None = None) -> dict:
    """Read, maybe refresh, decide, maybe hop. Returns the decision with what happened."""
    cfg = settings()
    now = now or time.time()
    if os.path.exists(paths.state_path("autohop.off")):
        d = {"action": "stay", "target": None, "why": "kill switch present (autohop.off); doing nothing"}
        _log({"ts": now, **d, "applied": False})
        return d
    if scan:
        store.scan()
    rows = readings(store, now)
    fresh: set = set()
    allow = cfg.get("accounts")
    pool = [r for r in rows if r.get("kind") != "provider" and (not allow or r["name"] in allow)]
    live = next((r for r in rows if r["active"] and r.get("kind") != "provider"), None)
    st = _state()
    if (cfg.get("refresh") and live and len(pool) >= 2 and hop.installed()
            and (_pct(live.get("short")) or 0) >= cfg["refresh_above"]
            and now - float(st.get("last_refresh") or 0) >= cfg["refresh_every_min"] * 60):
        ok, msg = hop.refresh()
        st["last_refresh"] = now
        st["last_refresh_ok"] = ok
        _save_state(st)
        if ok:
            rows = readings(store, time.time())
            fresh = {r["name"] for r in rows if (r.get("age_s") or 1e9) < 120}
    d = decide(rows, now, cfg, last_hop_ts(), fresh)
    d["applied"] = False
    if d["action"] == "hop" and apply:
        ok, msg = hop.hop_to(d["target"])
        d["applied"] = True
        d["hop_ok"] = ok
        d["hop_message"] = msg
    _log({"ts": now, **{k: v for k, v in d.items() if k != "candidates"}})
    return d


def _print(d: dict, apply: bool) -> None:
    head = {"stay": fmt.grey("stay"), "wait": fmt.paint("wait", fmt.YELLOW), "hop": fmt.red("hop", bold=True)}
    action = head.get(d["action"], d["action"])
    target = f" → {fmt.bold(d['target'])}" if d.get("target") and d["action"] == "hop" else ""
    print(f"{fmt.dim(time.strftime('%H:%M:%S'))}  {action}{target}  {d['why']}")
    for c in d.get("candidates") or []:
        mark = fmt.bold("  ok ") if c["ok"] else fmt.dim("  -- ")
        print(f"   {mark} {c['name']:<20} {fmt.dim(c['why'])}")
    if d["action"] == "hop":
        if not apply:
            print(fmt.dim("   dry run: nothing changed. Run with --apply to hop."))
        elif d.get("hop_ok"):
            print(f"   hopped: {d.get('hop_message')}")
        else:
            print(fmt.red(f"   the hop failed: {d.get('hop_message')}"))


def cmd_autohop(a) -> int:
    from .store import open_store

    if not hop.installed():
        print("autohop switches accounts through codexhop, which is not installed. "
              "See https://github.com/psychofict/codexhop", file=sys.stderr)
        return 1
    s = open_store()
    apply = bool(getattr(a, "apply", False))
    interval = max(30.0, float(getattr(a, "interval", 180) or 180))
    while True:
        d = run_once(s, apply=apply, scan=not getattr(a, "no_scan", False))
        if getattr(a, "json", False):
            print(json.dumps(d, default=str), flush=True)
        else:
            _print(d, apply)
            sys.stdout.flush()
        if not getattr(a, "watch", False):
            return 0 if d.get("hop_ok", True) else 1
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return 0
