"""Working with codexhop, when it is installed.

codexhop (https://github.com/psychofict/codexhop) keeps several ChatGPT logins
for Codex and switches between them. codexwatch never opens a login file: it
reads the account names from file names, codexhop's `active` pointer and its
usage cache (readings only, no tokens), and runs `codexhop` itself for the two
things that need a login, a fresh reading of every account and a hop.

Which account a limit window belongs to is worked out from the window's reset
time. Two accounts practically never share a reset time to the second, so a
window in the logs whose reset matches one in codexhop's cache is that
account's window. That works for old history too, as long as codexhop read
the account during that window.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from . import paths

MATCH_S = 120  # reset times this close are the same window


def store_dir() -> str:
    return paths.codexhop_store()


def installed() -> bool:
    return bool(shutil.which("codexhop")) or os.path.isdir(os.path.join(store_dir(), "accounts"))


def accounts() -> list[str]:
    d = os.path.join(store_dir(), "accounts")
    try:
        return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json") and not f.startswith("."))
    except OSError:
        return []


def active() -> str | None:
    try:
        with open(os.path.join(store_dir(), "active"), encoding="utf-8") as f:
            name = f.read().strip()
        return name or None
    except OSError:
        return None


def run_sessions() -> bool:
    """True while any `codexhop run` terminal is open (a second live account)."""
    d = os.path.join(store_dir(), "run")
    try:
        return any(os.listdir(d))
    except OSError:
        return False


def usage_cache() -> dict:
    try:
        with open(os.path.join(store_dir(), "usage.json"), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _win_now(win: dict | None, now: float) -> dict | None:
    if not isinstance(win, dict) or win.get("pct") is None:
        return None
    if win.get("resetsAt") and win["resetsAt"] <= now:
        return {**win, "pct": 0.0, "was": win.get("pct"), "resetsAt": None}
    return dict(win)


def accounts_view(now: float | None = None) -> list[dict]:
    """Every saved account with its newest reading, live one first."""
    now = now or time.time()
    names = accounts()
    if not names:
        return []
    cur = active()
    cache = usage_cache()
    rows = []
    for n in names:
        entry = cache.get(n) or {}
        use = entry.get("usage") or {}
        rows.append({
            "name": n,
            "active": n == cur,
            "plan": use.get("plan"),
            "short": _win_now(use.get("short"), now),
            "long": _win_now(use.get("long"), now),
            "limit_reached": bool(use.get("limitReached")),
            "fetched_at": entry.get("fetchedAt"),
            "age_s": (now - entry["fetchedAt"]) if entry.get("fetchedAt") else None,
            "throttled_until": entry.get("throttledUntil"),
        })
    rows.sort(key=lambda r: (not r["active"], r["name"]))
    return rows


def best_next(rows: list[dict], full_pct: float = 95.0) -> dict | None:
    """The other account with the most 5-hour room, chosen the way `codexhop next` does."""
    room = []
    for r in rows:
        if r["active"]:
            continue
        s, w = r.get("short"), r.get("long")
        if w and w.get("pct", 0) >= 100:
            continue
        pct = s["pct"] if s else 0.0
        if pct >= full_pct:
            continue
        left = (s.get("resetsAt") or 0) - time.time() if s else None
        room.append((int(pct // 10), left if left and left > 0 else float("inf"), r))
    room.sort(key=lambda t: (t[0], t[1]))
    return room[0][2] if room else None


def refresh(timeout: float = 90.0) -> tuple[bool, str]:
    """Ask codexhop for a fresh reading of every account. (ok, message)."""
    exe = shutil.which("codexhop")
    if not exe:
        return False, "codexhop is not installed"
    try:
        proc = subprocess.run([exe, "usage", "--json"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "codexhop did not answer in time"
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        return False, msg[-1] if msg else f"codexhop exited with {proc.returncode}"
    return True, "read every account"


def hop_to(name: str, timeout: float = 90.0) -> tuple[bool, str]:
    """Switch the live login with `codexhop use NAME --yes`."""
    exe = shutil.which("codexhop")
    if not exe:
        return False, "codexhop is not installed"
    try:
        proc = subprocess.run([exe, "use", name, "--yes"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "codexhop did not finish the hop in time"
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out.splitlines()[-1] if out else f"exit {proc.returncode}"


def attribute_windows(store) -> int:
    """Fill windows.account from codexhop's cache. Returns how many were named."""
    names = accounts()
    if not names:
        return 0
    cache = usage_cache()
    known = []  # (resetsAt, seconds, account)
    for n, entry in cache.items():
        use = (entry or {}).get("usage") or {}
        for key in ("short", "long"):
            w = use.get(key)
            if isinstance(w, dict) and w.get("resetsAt"):
                known.append((float(w["resetsAt"]), w.get("seconds"), n))
        for x in use.get("extra") or []:
            for key in ("short", "long"):
                w = (x or {}).get(key)
                if isinstance(w, dict) and w.get("resetsAt"):
                    known.append((float(w["resetsAt"]), w.get("seconds"), n))
    named = 0
    rows = store.q("SELECT limit_id, window_minutes, resets_at FROM windows WHERE account IS NULL")
    for r in rows:
        hit = next((a for (at, secs, a) in known
                    if abs(at - r["resets_at"]) <= MATCH_S
                    and (not secs or not r["window_minutes"] or abs(secs - r["window_minutes"] * 60) < 120)), None)
        if hit:
            with store.lock:
                store.db.execute("UPDATE windows SET account=? WHERE limit_id=? AND window_minutes=? AND resets_at=?",
                                 (hit, r["limit_id"], r["window_minutes"], r["resets_at"]))
            named += 1
    # A window read in the last few minutes, with one live login and no `run`
    # terminal open, belongs to the live login.
    cur = active()
    if cur and not run_sessions():
        recent = store.q(
            "SELECT DISTINCT l.limit_id, l.window_minutes, l.resets_at FROM limits l JOIN windows w "
            "ON w.limit_id=l.limit_id AND w.window_minutes=l.window_minutes AND w.resets_at=l.win_reset "
            "WHERE w.account IS NULL AND l.ts > ?", (time.time() - 300,))
        for r in recent:
            with store.lock:
                store.db.execute("UPDATE windows SET account=? WHERE limit_id=? AND window_minutes=? AND resets_at=?",
                                 (cur, r["limit_id"], r["window_minutes"], r["resets_at"]))
            named += 1
    with store.lock:
        store.db.commit()
    return named
