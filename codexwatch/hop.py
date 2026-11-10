"""Working with codexhop, when it is installed.

codexhop (https://github.com/psychofict/codexhop) keeps several ChatGPT logins
for Codex and switches between them. codexwatch never opens a login file: it
reads the account names from file names, codexhop's `active` pointer and its
usage cache (readings only, no tokens), and runs `codexhop` itself for the two
things that need a login, a fresh reading of every account and a hop.

codexhop also hops to a provider (Azure, Bedrock, any other endpoint), a Codex
profile file `<name>.config.toml` that is not OpenAI's own service. A provider
has no plan windows, so it is listed beside the accounts with kind "provider",
and is never chosen as the account with room. When codexhop's `provider`
pointer names one, that provider is the live entry, as in `codexhop list`.

Which account a limit window belongs to is worked out from the window's reset
time. Two accounts practically never share a reset time to the second, so a
window in the logs whose reset matches one in codexhop's cache is that
account's window. That works for old history too, as long as codexhop read
the account during that window.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse

from . import paths

MATCH_S = 120  # reset times this close are the same window
PROFILE_SUFFIX = ".config.toml"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_KEYLINE = re.compile(r"^(model_provider|model|base_url)\s*=\s*([\"'])(.*?)\2\s*(#.*)?$")


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


def _host(url: str) -> str | None:
    """Only the host of an endpoint: a user name, password, path or query in it is dropped."""
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None


def _profile_values(path: str) -> dict:
    """model_provider, model and the host of the first base_url in a Codex profile.

    Read line by line so Python 3.9 needs no TOML reader. A profile names the
    variable a key lives in and never holds the key, and only the host of a URL
    is kept.
    """
    out = {"model_provider": None, "model": None, "host": None}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    top = True  # model_provider and model count only above the first [table]
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            top = False
            continue
        m = _KEYLINE.match(s)
        if not m:
            continue
        key, val = m.group(1), m.group(3)
        if key == "base_url":
            out["host"] = out["host"] or _host(val)
        elif top:
            out[key] = val
    return out


def providers() -> list[dict]:
    """Profiles in the Codex home that point Codex somewhere other than OpenAI, as codexhop lists them."""
    home = paths.codex_home()
    try:
        files = sorted(os.listdir(home))
    except OSError:
        return []
    out = []
    for f in files:
        name = f[: -len(PROFILE_SUFFIX)]
        if not f.endswith(PROFILE_SUFFIX) or not _NAME.match(name):
            continue
        v = _profile_values(os.path.join(home, f))
        if v["model_provider"] not in (None, "openai"):
            out.append({"name": name, "provider": v["model_provider"], "model": v["model"], "host": v["host"]})
    return out


def provider_active(provs: list[dict] | None = None) -> str | None:
    """The provider codexhop has hopped to, if any. A pointer to a profile that is gone reads as none."""
    try:
        with open(os.path.join(store_dir(), "provider"), encoding="utf-8") as f:
            name = f.read().strip()
    except OSError:
        return None
    return name if name and any(p["name"] == name for p in (providers() if provs is None else provs)) else None


def live_phrase(row: dict | None) -> str | None:
    """How a surface says which entry is live."""
    if not row:
        return None
    return f"live on {row['name']} (provider)" if row.get("kind") == "provider" else f"live account {row['name']}"


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
    """Every saved account, then every provider, each with its newest reading; the live entry first.

    Rows have kind "account" or "provider". A provider has no plan windows, so
    its readings are empty and it carries its model and host instead.
    """
    now = now or time.time()
    names = accounts()
    provs = providers() if installed() else []
    if not names and not provs:
        return []
    cur = active()
    hopped = provider_active(provs)
    cache = usage_cache()
    rows = []
    for n in names:
        entry = cache.get(n) or {}
        use = entry.get("usage") or {}
        rows.append({
            "name": n,
            "kind": "account",
            "active": n == cur and not hopped,
            "plan": use.get("plan"),
            "short": _win_now(use.get("short"), now),
            "long": _win_now(use.get("long"), now),
            "limit_reached": bool(use.get("limitReached")),
            "fetched_at": entry.get("fetchedAt"),
            "age_s": (now - entry["fetchedAt"]) if entry.get("fetchedAt") else None,
            "throttled_until": entry.get("throttledUntil"),
        })
    for p in provs:
        rows.append({
            "name": p["name"], "kind": "provider", "active": p["name"] == hopped, "plan": None,
            "short": None, "long": None, "limit_reached": False, "fetched_at": None, "age_s": None,
            "throttled_until": None, "model": p["model"], "provider": p["provider"], "host": p["host"],
        })
    rows.sort(key=lambda r: (not r["active"], r["kind"] == "provider", r["name"]))
    return rows


def best_next(rows: list[dict], full_pct: float = 95.0) -> dict | None:
    """The other account with the most 5-hour room, chosen the way `codexhop next` does."""
    room = []
    for r in rows:
        if r["active"] or r.get("kind") == "provider":
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
