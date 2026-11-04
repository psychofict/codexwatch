"""One line, or one small menu, for status bars and shell prompts.

`codexwatch bar -f FORMAT` prints the 5-hour and weekly window and today's
API value in the shape each bar expects:

  plain, prompt   5h 98% · wk 15% · $10.39 today
  tmux            the same with #[fg=...] colours
  swiftbar, xbar  a macOS menu-bar line and a dropdown, as a plugin's output
  waybar          JSON with text, tooltip, class and percentage
  polybar         the line with %{F...} colours
  i3blocks        full_text, short_text and colour lines (urgent past alert)
  json            the headline figures as JSON

Bars call this every few seconds, so it scans incrementally (only new log
lines) and `--no-scan` skips even that. With no readings yet it prints a
neutral "codex –" rather than failing, so the bar never shows an error.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
from datetime import datetime

COLORS = {"ok": "#ffffff", "warn": "#f6e500", "alert": "#da291c", "none": "#969696"}
EMPTY = "codex –"


def _level(p: float | None, settings: dict | None) -> str:
    if p is None:
        return "none"
    s = settings or {}
    alert = float(s.get("alert_pct") or 90)
    warn = float(s.get("warn_pct") or 70)
    if p >= alert:
        return "alert"
    if p >= warn:
        return "warn"
    return "ok"


def _short_label(w: dict | None) -> str:
    if not w:
        return ""
    lab = w.get("label") or ""
    return "wk" if lab == "week" else lab


def _money(v) -> str:
    v = float(v or 0)
    return f"${v:,.0f}" if v >= 1000 else f"${v:.2f}"


def _clock(ts) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if abs((dt - now).days) < 6:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%d %b %H:%M")


def _left(seconds) -> str:
    if not seconds or seconds <= 0:
        return ""
    s = int(seconds)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def parts(state: dict) -> dict:
    """The pieces every format is built from."""
    head = (state or {}).get("headline") or {}
    short, long_ = head.get("short"), head.get("long")
    settings = (state or {}).get("settings") or {}
    sp = short.get("pct") if short else None
    lp = long_.get("pct") if long_ else None
    today = (((state or {}).get("periods") or {}).get("today") or {})
    lv_s, lv_l = _level(sp, settings), _level(lp, settings)
    order = ["none", "ok", "warn", "alert"]
    worst = max((lv_s, lv_l), key=order.index)
    return {
        "short": short, "long": long_, "short_pct": sp, "long_pct": lp,
        "short_level": lv_s, "long_level": lv_l, "level": worst,
        "today_cost": today.get("cost") or 0.0, "today_tokens": today.get("total") or 0,
        "has": short is not None or long_ is not None,
        "plan": ((state or {}).get("plan") or {}).get("name"),
        "account": (state or {}).get("live_account"),
        "next": (state or {}).get("next_account"),
        "read_at": head.get("read_at"),
    }


def account_cells(state: dict, now: float | None = None) -> list[dict]:
    """codexhop accounts as surfaces should show them: {name, active, pct, age_s}.

    The live account's 5-hour figure comes from whichever reading is newer:
    the logs (written after every request) or codexhop's cache (written when
    you last ran `codexhop usage`). Without this the live account could show
    an old cached 0% beside a 98% window.
    """
    now = now or time.time()
    head = (state or {}).get("headline") or {}
    short = head.get("short")
    read_at = head.get("read_at")
    out = []
    for a in (state or {}).get("accounts") or []:
        s = a.get("short") or {}
        pct = s.get("pct")
        fetched = a.get("fetched_at")
        if a.get("active") and short and read_at and (not fetched or read_at > fetched):
            pct, fetched = short.get("pct"), read_at
        out.append({"name": a.get("name"), "active": bool(a.get("active")), "pct": pct,
                    "age_s": (now - fetched) if fetched else None})
    return out


def _age_note(age_s) -> str:
    if not age_s or age_s < 3600:
        return ""
    return f" ({int(age_s // 86400)}d old)" if age_s >= 86400 else f" ({int(age_s // 3600)}h old)"


def _segments(p: dict) -> list[tuple[str, str]]:
    """(text, level) pieces of the one-line form."""
    segs = []
    if p["short"]:
        segs.append((f"{_short_label(p['short'])} {p['short_pct']:.0f}%", p["short_level"]))
    if p["long"]:
        segs.append((f"{_short_label(p['long'])} {p['long_pct']:.0f}%", p["long_level"]))
    if p["today_cost"]:
        segs.append((f"{_money(p['today_cost'])} today", "ok"))
    return segs


def line(state: dict) -> str:
    p = parts(state)
    if not p["has"]:
        return EMPTY
    return " · ".join(t for t, _ in _segments(p))


def tmux(state: dict) -> str:
    p = parts(state)
    if not p["has"]:
        return f"#[fg={COLORS['none']}]{EMPTY}#[default]"
    out = []
    for text, lv in _segments(p):
        out.append(f"#[fg={COLORS[lv]}]{text}#[default]" if lv in ("warn", "alert") else text)
    return " · ".join(out)


def prompt(state: dict, tty: bool = False) -> str:
    p = parts(state)
    if not p["has"]:
        return EMPTY
    if not tty:
        return line(state)
    out = []
    for text, lv in _segments(p):
        if lv in ("warn", "alert"):
            r, g, b = (int(COLORS[lv][i:i + 2], 16) for i in (1, 3, 5))
            out.append(f"\033[38;2;{r};{g};{b}m{text}\033[0m")
        else:
            out.append(text)
    return " · ".join(out)


def polybar(state: dict) -> str:
    p = parts(state)
    if not p["has"]:
        return EMPTY
    out = []
    for text, lv in _segments(p):
        out.append(f"%{{F{COLORS[lv]}}}{text}%{{F-}}" if lv in ("warn", "alert") else text)
    return " · ".join(out)


def i3blocks(state: dict) -> tuple[str, int]:
    """Three lines (full text, short text, colour) and the exit code: 33 marks urgent."""
    p = parts(state)
    if not p["has"]:
        return f"{EMPTY}\n{EMPTY}\n{COLORS['none']}", 0
    short_text = f"{_short_label(p['short'])} {p['short_pct']:.0f}%" if p["short"] else line(state)
    return f"{line(state)}\n{short_text}\n{COLORS[p['level']]}", (33 if p["level"] == "alert" else 0)


def _tooltip(state: dict) -> str:
    p = parts(state)
    if not p["has"]:
        return "No plan readings in the Codex logs yet."
    now = time.time()
    rows = []
    title = " · ".join(x for x in (p["plan"], p["account"]) if x)
    if title:
        rows.append(title)
    for w in (p["short"], p["long"]):
        if not w:
            continue
        name = {"5h": "5 hours", "week": "Week"}.get(w.get("label"), w.get("label") or "")
        if w.get("was") is not None:
            rows.append(f"{name}: reset (was {w['was']:.0f}%)")
            continue
        r = w.get("resets_at")
        tail = f", resets {_clock(r)} (in {_left(r - now)})" if r and r > now else ""
        rows.append(f"{name}: {w['pct']:.0f}%{tail}")
    per = (state or {}).get("periods") or {}
    for key, name in (("today", "Today"), ("7d", "7 days"), ("month", "This month")):
        if per.get(key):
            rows.append(f"{name}: {_money(per[key].get('cost'))} API value")
    if p["next"]:
        rows.append(f"Most room: {p['next']}")
    return "\n".join(rows)


def waybar(state: dict) -> str:
    p = parts(state)
    if not p["has"]:
        return json.dumps({"text": EMPTY, "tooltip": _tooltip(state), "class": "none", "percentage": 0})
    return json.dumps({
        "text": " · ".join(t for t, _ in _segments(p)[:2]),
        "tooltip": _tooltip(state),
        "class": p["level"],
        "percentage": int(round(p["short_pct"] if p["short_pct"] is not None else (p["long_pct"] or 0))),
    })


def _menu_safe(s: str) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def _self_command() -> list[str]:
    """How a menu-bar plugin, which runs with a bare PATH, can start codexwatch."""
    exe = shutil.which("codexwatch")
    if exe:
        return [exe]
    return [sys.executable, "-m", "codexwatch"]


def swiftbar(state: dict, sf_symbols: bool = True) -> str:
    """Plugin output for SwiftBar and xbar. xbar ignores the parameters it does not know."""
    p = parts(state)
    now = time.time()
    out = []
    params = []
    if p["level"] in ("warn", "alert"):
        params.append(f"color={COLORS[p['level']]}")
    if sf_symbols:
        params.append("sfimage=gauge.with.needle")
    head = " · ".join(t for t, _ in _segments(p)[:2]) if p["has"] else EMPTY
    out.append(_menu_safe(head) + (" | " + " ".join(params) if params else ""))
    out.append("---")
    title = " · ".join(x for x in (p["plan"], p["account"] and f"live account {p['account']}") if x)
    if title:
        out.append(f"{_menu_safe(title)} | color=#969696 size=12")
    if not p["has"]:
        out.append("No plan readings in the Codex logs yet | color=#969696")
    for w in (p["short"], p["long"]):
        if not w:
            continue
        name = {"5h": "5 hours", "week": "Week"}.get(w.get("label"), w.get("label") or "")
        lv = _level(w.get("pct"), (state or {}).get("settings"))
        color = f" color={COLORS[lv]}" if lv in ("warn", "alert") else ""
        if w.get("was") is not None:
            out.append(f"{name:<8} reset, was {w['was']:.0f}% | font=Menlo size=12")
            continue
        r = w.get("resets_at")
        tail = f"  resets {_clock(r)} ({_left(r - now)})" if r and r > now else ""
        out.append(f"{name:<8} {w['pct']:>3.0f}%{tail} | font=Menlo size=12{color}")
        if w.get("full_at"):
            out.append(f"--Full at {_clock(w['full_at'])} at this pace | color=#da291c")
    out.append("---")
    out.append("API value | color=#969696 size=12")
    per = (state or {}).get("periods") or {}
    for key, name in (("today", "Today"), ("7d", "7 days"), ("month", "This month"), ("all", "All time")):
        if per.get(key) is not None:
            out.append(f"{name:<11}{_money(per[key].get('cost')):>11} | font=Menlo size=12")
    accts = account_cells(state, now)
    if accts:
        out.append("---")
        out.append("codexhop accounts | color=#969696 size=12")
        for a in accts:
            pct = f"{a['pct']:.0f}%" if a["pct"] is not None else "-"
            mark = "●" if a["active"] else "○"
            out.append(f"{mark} {_menu_safe(a['name'])}  5h {pct}{_age_note(a['age_s'])} | font=Menlo size=12")
        if p["next"]:
            out.append(f"Most room: {_menu_safe(p['next'])} | color=#da291c")
    out.append("---")
    cmd = _self_command()
    args = cmd[1:] + ["dashboard"]
    bash = f"bash={shlex.quote(cmd[0])} " + " ".join(f"param{i + 1}={shlex.quote(x)}" for i, x in enumerate(args))
    out.append(f"Open dashboard | {bash} terminal=false")
    out.append("Refresh | refresh=true")
    if p["read_at"]:
        out.append(f"Last reading {_clock(p['read_at'])} | color=#666666 size=11")
    return "\n".join(out)


def headline_json(state: dict) -> str:
    p = parts(state)

    def win(w, lv):
        if not w:
            return None
        return {"label": w.get("label"), "pct": w.get("pct"), "resets_at": w.get("resets_at"),
                "full_at": w.get("full_at"), "level": lv, "was": w.get("was")}

    return json.dumps({
        "text": line(state),
        "short": win(p["short"], p["short_level"]),
        "long": win(p["long"], p["long_level"]),
        "level": p["level"],
        "plan": p["plan"],
        "live_account": p["account"],
        "next_account": p["next"],
        "today_cost": p["today_cost"],
        "today_tokens": p["today_tokens"],
        "read_at": p["read_at"],
    })


def render(state: dict, fmt_name: str, tty: bool = False) -> tuple[str, int]:
    """(text, exit code) for a format name."""
    if fmt_name in ("swiftbar", "xbar"):
        return swiftbar(state, sf_symbols=fmt_name == "swiftbar"), 0
    if fmt_name == "waybar":
        return waybar(state), 0
    if fmt_name == "tmux":
        return tmux(state), 0
    if fmt_name == "polybar":
        return polybar(state), 0
    if fmt_name == "i3blocks":
        return i3blocks(state)
    if fmt_name == "json":
        return headline_json(state), 0
    if fmt_name == "prompt":
        return prompt(state, tty), 0
    return line(state), 0


def cmd_bar(a) -> int:
    from . import snapshot
    from .store import open_store

    name = "json" if getattr(a, "json", False) and a.format == "plain" else a.format
    try:
        store = open_store()
        st = snapshot.state(store, scan=not getattr(a, "no_scan", False))
    except Exception:  # noqa: BLE001 - a status bar shows a neutral line, never a traceback
        st = {}
    tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and not getattr(a, "no_color", False)
    text, code = render(st, name, tty)
    print(text)
    return code
