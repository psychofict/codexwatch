"""Desktop notifications when a plan window or a budget fills up.

Which alerts are due is decided by due_alerts(), a pure function of the
current state and the alerts already sent, so the tray, `codexwatch notify`
from a timer, and the tests all agree. Each alert is sent once:

* a plan window, once per step (75, 90 and 100 by default) per window. A
  window is named by its start, which stays put, rather than its reset time,
  which drifts by a second or two between readings. When a window jumps past
  several steps at once only the highest is sent, and the lower ones are
  marked sent with it.
* a budget, at its own notify level and again when it is spent, once per
  budget period (a day, a week, a month, or one plan window).

Sent alerts are remembered in notified.json in the codexwatch folder for 35
days. CODEXWATCH_NOTIFY_DRYRUN prints alerts to stderr instead of showing
them, which the tests use.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

from . import paths

KEEP_S = 35 * 86400
DEFAULT_STEPS = (75, 90, 100)


# ------------------------------------------------------------------ deciding

def _num(v) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _window_key(limit_id: str, w: dict) -> str | None:
    anchor = w.get("starts_at") or w.get("resets_at")
    if not anchor:
        return None
    return f"win:{limit_id}:{w.get('minutes') or 0}:{int(anchor)}"


def _window_name(lim: dict, w: dict) -> str:
    length = {"5h": "5-hour", "week": "weekly"}.get(w.get("label") or "", w.get("label") or "plan")
    if lim.get("limit_id") not in (None, "codex") and lim.get("name"):
        return f"{lim['name']} {length} window"
    return f"Codex {length} window"


def _reset_text(w: dict, now: float) -> str:
    at = w.get("resets_at")
    if not at or at <= now:
        return ""
    left = int(at - now)
    h, m = divmod(left // 60, 60)
    when = datetime.fromtimestamp(at).strftime("%H:%M" if left < 20 * 3600 else "%a %H:%M")
    span = f"{h}h {m:02d}m" if h else f"{m}m"
    return f"Resets at {when} (in {span})."


def _period_bucket(item: dict, now: float) -> str:
    """Which period a budget reading belongs to, so a new period alerts again."""
    for k in ("period_start", "since", "window_start", "resets_at", "starts_at"):
        v = _num(item.get(k))
        if v:
            return str(int(v))
    period = str(item.get("period") or item.get("every") or "").lower()
    dt = datetime.fromtimestamp(now)
    if period in ("day", "daily", "today"):
        return dt.strftime("%Y-%m-%d")
    if period in ("week", "weekly", "7d"):
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"
    if period in ("month", "monthly"):
        return dt.strftime("%Y-%m")
    if period in ("5h",):
        return dt.strftime("%Y-%m-%dT") + str(dt.hour // 5)
    return dt.strftime("%Y-%m")


def due_alerts(state: dict, history: dict | None, steps=DEFAULT_STEPS, now: float | None = None) -> list[dict]:
    """The alerts that should be sent now. Each is {key, marks, title, body, level}.

    `marks` lists every key the alert settles, so a jump from 60% to 98% sends
    one alert and never the 75% one afterwards. Nothing here has side effects.
    """
    now = now or time.time()
    sent = history or {}
    steps = sorted({float(s) for s in (steps or DEFAULT_STEPS) if _num(s) is not None})
    out: list[dict] = []

    for lim in state.get("limits") or []:
        lid = str(lim.get("limit_id") or "codex")
        for w in lim.get("windows") or []:
            if w.get("was") is not None:
                continue  # the window has reset since this reading
            p = _num(w.get("pct"))
            base = _window_key(lid, w)
            if p is None or base is None:
                continue
            crossed = [s for s in steps if p >= s]
            fresh = [s for s in crossed if f"{base}:{s:g}" not in sent]
            if not fresh:
                continue
            top = max(fresh)
            name = _window_name(lim, w)
            if top >= 100 or p >= 100:
                title = f"{name} is full"
                body = "New requests wait until it resets. " + _reset_text(w, now)
            else:
                title = f"{name} at {p:.0f}%"
                body = _reset_text(w, now)
                full_at = _num(w.get("full_at"))
                if full_at and full_at > now:
                    body = (body + " " if body else "") + \
                        f"At this pace it fills at {datetime.fromtimestamp(full_at):%H:%M}."
                acct = w.get("account")
                if acct:
                    body = f"Account {acct}. " + body
            nxt = state.get("next_account")
            if nxt and top >= 90:
                body += f" {nxt} has the most room; `codexhop next` moves there."
            out.append({
                "key": f"{base}:{top:g}",
                "marks": [f"{base}:{s:g}" for s in crossed],
                "title": title,
                "body": body.strip(),
                "level": "alert" if top >= 90 else "warn",
            })

    for b in state.get("budgets") or []:
        if not isinstance(b, dict):
            continue
        p = _num(b.get("pct"))
        if p is None:
            used, cap = _num(b.get("used")), _num(b.get("limit") or b.get("amount"))
            p = (used / cap * 100) if used is not None and cap else None
        if p is None:
            continue
        ident = b.get("id") if b.get("id") is not None else b.get("name")
        base = f"budget:{ident}:{_period_bucket(b, now)}"
        level_at = _num(b.get("notify_pct")) or 80.0
        over = bool(b.get("over")) or p >= 100
        marks = []
        if p >= level_at:
            marks.append(f"{base}:notify")
        if over:
            marks.append(f"{base}:over")
        fresh = [m for m in marks if m not in sent]
        if not fresh:
            continue
        name = b.get("name") or f"budget {ident}"
        kind = str(b.get("kind") or "")
        if over:
            title = f"Budget spent: {name}"
        else:
            title = f"Budget {name} at {p:.0f}%"
        used, cap = _num(b.get("used")), _num(b.get("limit") or b.get("amount"))
        if used is not None and cap:
            if kind in ("usd", "cost", "dollars"):
                body = f"${used:,.2f} of ${cap:,.2f}"
            elif kind in ("pct", "plan"):
                body = f"{used:.0f}% of the {cap:.0f}% cap"
            else:
                body = f"{used:,.0f} of {cap:,.0f}"
            period = b.get("period") or b.get("every")
            if period:
                body += f" this {period}" if period in ("day", "week", "month") else f" ({period})"
            body += "."
        else:
            body = f"{p:.0f}% used."
        out.append({"key": fresh[-1], "marks": marks, "title": title, "body": body,
                    "level": "alert" if over else "warn"})
    return out


# ------------------------------------------------------------------- history

def load_history() -> dict:
    try:
        with open(paths.state_path("notified.json"), encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_history(history: dict, now: float | None = None) -> None:
    now = now or time.time()
    keep = {k: v for k, v in history.items() if isinstance(v, (int, float)) and now - v < KEEP_S}
    try:
        paths.ensure_data_dir()
        tmp = paths.state_path("notified.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keep, f)
        os.replace(tmp, paths.state_path("notified.json"))
    except OSError:
        pass


# ------------------------------------------------------------------- sending

def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def send(title: str, body: str, level: str = "warn") -> bool:
    """Show one desktop notification. Best effort: False when nothing showed it."""
    if os.environ.get("CODEXWATCH_NOTIFY_DRYRUN"):
        print(f"[notify:{level}] {title} — {body}", file=sys.stderr)
        return True
    try:
        if sys.platform == "darwin":
            def esc(s):
                return s.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{esc(body)}" with title "{esc(title)}" subtitle "codexwatch"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
            return True
        if os.name == "nt":
            ps = (
                "if (Get-Module -ListAvailable -Name BurntToast) {"
                f" New-BurntToastNotification -Text {_ps_quote(title)}, {_ps_quote(body)} }}"
                " else { Add-Type -AssemblyName System.Windows.Forms;"
                " $n = New-Object System.Windows.Forms.NotifyIcon;"
                " $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true;"
                f" $n.ShowBalloonTip(8000, {_ps_quote(title)}, {_ps_quote(body)}, 'Info');"
                " Start-Sleep -Seconds 9; $n.Dispose() }"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return True
        exe = shutil.which("notify-send")
        if not exe:
            return False
        urgency = "critical" if level == "alert" else "normal"
        subprocess.run([exe, "--app-name=codexwatch", f"--urgency={urgency}", title, body],
                       capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def check_and_send(state: dict, steps=None, enabled: bool | None = None) -> list[dict]:
    """Send whatever is due for this state and remember it. Returns what was sent."""
    from . import config

    cfg = config.load()
    if enabled is None:
        enabled = bool(cfg.get("notify", True))
    if not enabled:
        return []
    history = load_history()
    due = due_alerts(state, history, steps or cfg.get("notify_steps") or DEFAULT_STEPS)
    if not due:
        return []
    now = time.time()
    for alert in due:
        send(alert["title"], alert["body"], alert["level"])
        for k in alert["marks"]:
            history[k] = now
    save_history(history, now)
    return due


def cmd_notify(a) -> int:
    """`codexwatch notify`: send due alerts once; made for a timer or cron job."""
    from . import config, snapshot
    from .store import open_store

    if not config.get("notify"):
        if getattr(a, "json", False):
            print(json.dumps({"enabled": False, "sent": []}))
        else:
            print("notifications are off (codexwatch config set notify true turns them on)")
        return 0
    store = open_store()
    st = snapshot.state(store, scan=not getattr(a, "no_scan", False))
    sent = check_and_send(st, enabled=True)
    if getattr(a, "json", False):
        print(json.dumps({"enabled": True, "sent": sent}, indent=2))
    elif sent:
        for s in sent:
            print(f"sent: {s['title']}")
    else:
        print("nothing due")
    return 0
