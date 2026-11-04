"""Terminal output: numbers, colours, bars and tables.

Colour follows the dashboard: white figures on the terminal's own background,
grey labels, and Rosso Corsa red kept for the one figure that matters on a
screen and for a window past its alert level. NO_COLOR, --no-color or a pipe
turn colour off.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from datetime import datetime

RED = (218, 41, 28)        # Rosso Corsa #da291c
YELLOW = (246, 229, 0)     # #f6e500, a window past its warning level
GREY = (150, 150, 150)     # body text #969696
DIM = (102, 102, 102)      # muted #666666
WHITE = (255, 255, 255)

_ANSI = re.compile(r"\033\[[0-9;]*m")
_enabled: bool | None = None


def color_enabled() -> bool:
    global _enabled
    if _enabled is None:
        _enabled = (sys.stdout.isatty() and not os.environ.get("NO_COLOR")
                    and os.environ.get("TERM") != "dumb")
        if _enabled and os.name == "nt":
            os.system("")  # turns on ANSI escapes in the Windows console
    return _enabled


def set_color(on: bool) -> None:
    global _enabled
    _enabled = on


def _truecolor() -> bool:
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") or os.name == "nt"


def paint(text: str, rgb=None, bold: bool = False, dim: bool = False) -> str:
    if not color_enabled():
        return text
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if rgb:
        r, g, b = rgb
        if _truecolor():
            codes.append(f"38;2;{r};{g};{b}")
        else:
            codes.append(f"38;5;{16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)}")
    return f"\033[{';'.join(codes)}m{text}\033[0m" if codes else text


def red(t, bold=False):
    return paint(t, RED, bold=bold)


def grey(t):
    return paint(t, GREY)


def dim(t):
    return paint(t, DIM)


def bold(t):
    return paint(t, WHITE, bold=True)


def label(t: str) -> str:
    """A section label: uppercase and spaced, like the dashboard's."""
    return paint(t.upper(), GREY, bold=True)


def visible_len(s: str) -> int:
    return len(_ANSI.sub("", s))


def width() -> int:
    return max(60, min(shutil.get_terminal_size((100, 24)).columns, 140))


# ------------------------------------------------------------------- numbers

def tokens(n) -> str:
    n = float(n or 0)
    for div, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.2f}{unit}" if v < 10 else f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return f"{n:.0f}"


def money(v, unpriced: bool = False) -> str:
    if v is None:
        return "unpriced"
    v = float(v)
    if v >= 10000:
        s = f"${v:,.0f}"
    elif v >= 100:
        s = f"${v:,.2f}"
    elif v >= 0.01 or v == 0:
        s = f"${v:.2f}"
    else:
        s = f"${v:.4f}"
    return s + ("*" if unpriced else "")


def pct(v, digits: int = 0) -> str:
    return "-" if v is None else f"{v:.{digits}f}%"


def duration(seconds) -> str:
    if seconds is None:
        return "-"
    s = int(max(0, seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m"
    return f"{s}s"


def clock(ts, with_day: bool = True) -> str:
    if not ts:
        return "-"
    dt = datetime.fromtimestamp(ts)
    now = datetime.now()
    if not with_day or dt.date() == now.date():
        return dt.strftime("%H:%M")
    if abs((dt - now).days) < 6:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%d %b %H:%M")


def ago(ts) -> str:
    if not ts:
        return "never"
    return duration(time.time() - ts) + " ago"


# ---------------------------------------------------------------------- bars

def level_rgb(p: float, warn: float = 70, alert: float = 90):
    if p >= alert:
        return RED
    if p >= warn:
        return YELLOW
    return WHITE


def bar(p: float, n: int = 24, warn: float = 70, alert: float = 90) -> str:
    """A horizontal gauge in eighths of a cell."""
    p = max(0.0, min(100.0, p or 0.0))
    eighths = round(p / 100 * n * 8)
    full, part = divmod(eighths, 8)
    s = "█" * full + (" ▏▎▍▌▋▊▉"[part] if part else "")
    fill = paint(s, level_rgb(p, warn, alert))
    return fill + dim("·" * (n - len(s)))


SPARK = " ▁▂▃▄▅▆▇█"


def spark(values: list[float]) -> str:
    top = max(values or [0]) or 1
    return "".join(SPARK[min(8, int(round(v / top * 8)))] if v else dim("·") for v in values)


# -------------------------------------------------------------------- tables

def table(headers: list[str], rows: list[list], align: str | None = None, highlight: set | None = None) -> str:
    """Columns as wide as their widest cell. align is one char per column: l or r."""
    align = align or ("l" + "r" * (len(headers) - 1))
    cells = [[str(c) for c in r] for r in rows]
    w = [max([visible_len(h)] + [visible_len(r[i]) for r in cells]) for i, h in enumerate(headers)]

    def fmt_row(r, head=False):
        out = []
        for i, c in enumerate(r):
            padn = w[i] - visible_len(c)
            out.append((c + " " * padn) if align[i] == "l" else (" " * padn + c))
        line = "  ".join(out).rstrip()
        return line

    lines = [label_row(headers, w, align)]
    for i, r in enumerate(cells):
        line = fmt_row(r)
        lines.append(bold(line) if highlight and i in highlight else line)
    return "\n".join(lines)


def label_row(headers, w, align) -> str:
    out = []
    for i, h in enumerate(headers):
        padn = w[i] - len(h)
        cell = (h + " " * padn) if align[i] == "l" else (" " * padn + h)
        out.append(cell)
    return dim("  ".join(out).rstrip().upper())
