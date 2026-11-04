"""A share card: one image of your Codex month, made to be posted.

It leads with what your plan returned (API-equivalent value against what the
plan costs), then the totals, a 30-day strip and your top model. Project
names stay off the card unless you ask for them, since a project name can be
a client's name. The SVG embeds its own font, so it looks the same wherever
it is opened, and the dashboard turns it into a PNG.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from xml.sax.saxutils import escape

from . import fmt, limits, periods, reports, snapshot
from .store import open_store

W, H = 1200, 630
FONT = os.path.join(os.path.dirname(__file__), "web", "fonts", "Geist.woff2")
THEMES = {
    "dark": {"bg": "#181818", "ink": "#ffffff", "body": "#969696", "muted": "#6e6e6e", "hair": "#303030",
             "bar": "#4a4a4a", "barhi": "#ffffff"},
    "light": {"bg": "#ffffff", "ink": "#181818", "body": "#5a5a5a", "muted": "#767676", "hair": "#e3e3e3",
              "bar": "#c4c4c4", "barhi": "#181818"},
}
RED = "#da291c"


def _font_face() -> str:
    try:
        with open(FONT, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        return f"@font-face{{font-family:'CW Geist';src:url(data:font/woff2;base64,{data}) format('woff2');font-weight:100 900}}"
    except OSError:
        return ""


def _t(x, y, text, size, color, weight=500, anchor="start", spacing=0.0, upper=False) -> str:
    text = text.upper() if upper else text
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" '
            f'text-anchor="{anchor}"{ls}>{escape(text)}</text>')


def data(store, period: str = "30d") -> dict:
    since, until, label = periods.parse(period)
    f = reports.Filter(since=since, until=until)
    t = reports.totals(store, f)
    cur = limits.current(store)
    plan = snapshot.plan_info(snapshot.headline(cur)["plan"])
    days = reports.daily_series(store, 30)
    models = reports.group(store, "model", f, limit=3)
    projects = reports.group(store, "project", f, limit=3)
    streak = reports.streaks(store, f)
    return {"label": label, "since": since or t.get("first"), "totals": t, "plan": plan, "days": days,
            "models": models, "projects": projects, "streak": streak}


def render(store, period: str = "30d", show_projects: bool = False, theme: str = "dark",
           plan_price: float | None = None) -> str:
    d = data(store, period)
    c = THEMES.get(theme, THEMES["dark"])
    t = d["totals"]
    plan = dict(d["plan"])
    if plan_price:
        plan["usd"] = plan_price
    value = t["cost"] or 0.0
    # Plan value compares a month of usage with a month of plan.
    span_days = max(1.0, ((time.time() - d["since"]) / 86400) if d["since"] else 30.0)
    monthly_value = value * (30.0 / span_days) if span_days > 31 else value
    mult = (monthly_value / plan["usd"]) if plan.get("usd") else None

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
           f"<style>{_font_face()} text{{font-family:'CW Geist',Geist,-apple-system,'Segoe UI',sans-serif;"
           "font-variant-numeric:tabular-nums}</style>",
           f'<rect width="{W}" height="{H}" fill="{c["bg"]}"/>',
           f'<rect x="0" y="0" width="10" height="{H}" fill="{RED}"/>']
    # mark and title
    out.append(f'<g transform="translate(64 52)"><path d="M6.3 17.7A8 8 0 1 1 17.7 17.7" fill="none" '
               f'stroke="{c["ink"]}" stroke-width="2"/><path d="M12 12l4.6-4.6" stroke="{RED}" stroke-width="2.4"/></g>')
    out.append(_t(98, 70, "codexwatch", 14, c["ink"], 600, spacing=3, upper=True))
    since_dt = datetime.fromtimestamp(d["since"]) if d["since"] else None
    since_txt = f"{since_dt.day} {since_dt:%b}" if since_dt else ""
    right = f"my codex · {d['label']}" + (f" · from {since_txt}" if since_txt and period != "all" else "")
    out.append(_t(W - 64, 70, right, 13, c["body"], 600, "end", 2, upper=True))

    # hero figure
    if mult:
        big = (f"{mult:.0f}" if mult >= 10 else f"{mult:.1f}") + "×"
        line1 = f"{fmt.money(value)} of API usage"
        name = plan.get("name") or ""
        # "Pro 200" already says its price; "Plus" does not.
        what = f"{name} plan" if f"{plan['usd']:g}" in name else f"${plan['usd']:g} {name} plan".replace("  ", " ")
        line2 = f"on a {what}" + (", per month" if span_days > 31 else "")
    else:
        big = fmt.money(value)
        line1 = "of API-equivalent usage"
        line2 = f"in {fmt.tokens(t['total'])} tokens"
    size = int(min(230, 620 / (0.58 * max(2, len(big)))))
    if big.endswith("×"):
        # The multiplier sign is set smaller and grey so the figure carries the card.
        out.append(f'<text x="58" y="{120 + size * 0.86:.0f}" font-size="{size}" font-weight="600" fill="{c["ink"]}" '
                   f'letter-spacing="{-size * 0.045:.1f}">{escape(big[:-1])}<tspan font-size="{size * 0.42:.0f}" '
                   f'font-weight="500" fill="{c["body"]}" dx="{size * 0.06:.0f}">×</tspan></text>')
    else:
        out.append(_t(58, 120 + size * 0.86, big, size, c["ink"], 600, spacing=-size * 0.045))
    y = 120 + size * 0.86 + 58
    out.append(_t(64, y, line1, 34, RED if mult else c["ink"], 500, spacing=-0.5))
    out.append(_t(64, y + 42, line2, 34, c["ink"], 500, spacing=-0.5))

    # spec column
    x0, y0 = 760, 128
    rows = [("tokens", fmt.tokens(t["total"])),
            ("cache hit", fmt.pct(t["cache_hit"] * 100, 1) if t["input"] else "-"),
            ("requests", f"{t['requests']:,}"),
            ("active days", f"{d['streak']['active_days']}" + (f" · best run {d['streak']['longest']}" if d["streak"]["longest"] > 1 else ""))]
    if d["models"]:
        top = d["models"][0]
        share = (top["cost"] / value * 100) if value else 0
        rows.append(("top model", f"{top['key']}" + (f" · {share:.0f}%" if share else "")))
    if show_projects and d["projects"]:
        rows.append(("top projects", ", ".join(str(p["key"]) for p in d["projects"][:3])))
    step = 66 if len(rows) <= 5 else 56
    for i, (k, v) in enumerate(rows):
        yy = y0 + i * step
        out.append(f'<line x1="{x0}" x2="{W - 64}" y1="{yy - 30}" y2="{yy - 30}" stroke="{c["hair"]}"/>')
        out.append(_t(x0, yy - 8, k, 11, c["muted"], 600, spacing=1.6, upper=True))
        vs = 26 if len(v) < 20 else 20 if len(v) < 28 else 16
        out.append(_t(x0, yy + 22, v[:44], vs, c["ink"], 500, spacing=-0.3))

    # 30-day strip
    vals = [x["cost"] or 0 for x in d["days"]]
    top = max(vals) or 1
    bx, by, bw, bh = 64, H - 110, W - 128, 52
    n = len(vals)
    cell = bw / n
    peak = vals.index(max(vals)) if any(vals) else -1
    for i, v in enumerate(vals):
        hh = max(2, bh * v / top) if v else 1
        col = RED if i == peak else (c["barhi"] if i == n - 1 else c["bar"])
        out.append(f'<rect x="{bx + i * cell + 2:.1f}" y="{by + bh - hh:.1f}" width="{max(1, cell - 4):.1f}" '
                   f'height="{hh:.1f}" fill="{col}"/>')
    out.append(_t(bx, H - 36, "last 30 days · api value per day", 11, c["muted"], 600, spacing=1.6, upper=True))
    out.append(_t(W - 64, H - 36, "github.com/psychofict/codexwatch", 12, c["body"], 500, "end", 0.4))
    out.append("</svg>")
    return "\n".join(out)


def cmd_card(a) -> int:
    store = open_store()
    if not a.no_scan:
        store.scan()
    svg = render(store, period=a.period or "30d", show_projects=a.projects, theme=a.theme, plan_price=a.plan_price)
    out = a.out or "codexwatch-card.svg"
    if out.lower().endswith(".png"):
        tmp = out[:-4] + ".svg"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(svg)
        conv = shutil.which("rsvg-convert")
        if not conv:
            print(f"wrote {tmp}. For a PNG, install rsvg-convert or use Share card → PNG in `codexwatch dashboard`.")
            return 0
        subprocess.run([conv, "-w", "2400", "-h", "1260", "-o", out, tmp], check=False)
        print(f"wrote {out} (and {tmp})")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"wrote {out}" + ("" if a.projects else "  (project names left off; --projects adds them)"))
    if a.open:
        _open(out)
    return 0


def _open(path: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # noqa: S606 - opening the user's own file
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
