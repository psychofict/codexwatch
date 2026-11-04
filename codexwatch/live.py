"""`codexwatch live`: a full-screen view that redraws itself every few seconds.

It uses the terminal's alternate screen, so your scrollback is untouched when
you quit, and always restores the cursor, even after Ctrl-C. Keys: q quits,
r reads the logs again now. When the output is not a terminal (a pipe, a
file) it prints one plain frame and exits, so `codexwatch live | cat` works.
"""

from __future__ import annotations

import os
import sys
import time

from . import fmt, reports, snapshot

ENTER = "\033[?1049h\033[?25l"
LEAVE = "\033[?25h\033[?1049l"
HOME = "\033[H"
EOL = "\033[K"
EOS = "\033[J"


def _big_bar(p: float, n: int, warn: float, alert: float) -> str:
    return fmt.bar(p, max(10, n), warn, alert)


def frame(state: dict, session: dict | None, width: int, interval: float | None = None) -> list[str]:
    """The lines of one screen, from a snapshot state and the newest session."""
    settings = state.get("settings") or {}
    warn, alert = float(settings.get("warn_pct") or 70), float(settings.get("alert_pct") or 90)
    head = state.get("headline") or {}
    plan = (state.get("plan") or {}).get("name")
    acct = state.get("live_account")
    now = time.time()
    lines = []

    right = " · ".join(x for x in (plan, acct and f"live account {acct}",
                                   head.get("read_at") and f"read {fmt.ago(head['read_at'])}") if x)
    left = fmt.red("CODEXWATCH", bold=True) + fmt.dim("  live")
    gap = max(2, width - fmt.visible_len(left) - len(right))
    lines.append(left + " " * gap + fmt.grey(right))
    lines.append(fmt.dim("─" * width))

    wins = [w for w in (head.get("short"), head.get("long")) if w]
    if not wins:
        lines.append("")
        lines.append(fmt.grey("No plan readings in the Codex logs yet. They appear after your first Codex request."))
    bar_w = max(10, width - 16)
    for w in wins:
        name = {"5h": "5 hours", "week": "week"}.get(w.get("label"), w.get("label") or "window")
        p = float(w.get("pct") or 0)
        lines.append("")
        pc = fmt.paint(f"{p:>3.0f}%", fmt.level_rgb(p, warn, alert), bold=True)
        lines.append(fmt.label(name) + " " * max(1, width - len(name) - 4) + pc)
        lines.append(_big_bar(p, bar_w + 12, warn, alert))
        bits = []
        if w.get("was") is not None:
            bits.append(f"reset since the last reading (was {w['was']:.0f}%)")
        else:
            r = w.get("resets_at")
            if r and r > now:
                bits.append(f"resets {fmt.clock(r)} (in {fmt.duration(r - now)})")
            rate = w.get("burn_pct_per_h")
            if rate:
                bits.append(f"burning {rate:.1f} pts/h")
            sr = w.get("since_reset") or {}
            if sr.get("plan_cost"):
                bits.append(f"{fmt.money(sr['plan_cost'])} since reset")
        line = fmt.grey(" · ".join(bits))
        if w.get("full_at") and w.get("was") is None:
            line += fmt.grey(" · ") + fmt.red(f"full at {fmt.clock(w['full_at'])}", bold=True)
        lines.append(line)

    lines.append("")
    lines.append(fmt.label("current session"))
    if session:
        title = session.get("title") or session.get("session_id", "")[:8]
        models = ", ".join(m for m in session.get("models") or [] if m)
        meta = " · ".join(x for x in (session.get("project"), models) if x)
        lines.append(fmt.bold(title[: max(10, width - 2)]))
        lines.append(fmt.grey(meta[:width]))
        lines.append(f"{fmt.bold(fmt.tokens(session.get('total')))} {fmt.dim('tokens')}   "
                     f"{fmt.bold(fmt.money(session.get('cost')))} {fmt.dim('api value')}   "
                     f"{fmt.bold(str(session.get('turns') or 0))} {fmt.dim('turns')}   "
                     f"{fmt.dim('last response ' + fmt.ago(session.get('last')))}")
    else:
        lines.append(fmt.grey("No sessions yet."))

    today = (state.get("periods") or {}).get("today") or {}
    lines.append("")
    lines.append(fmt.label("today"))
    hit = f"{today['cache_hit'] * 100:.0f}% cached" if today.get("input") else "-"
    lines.append(f"{fmt.bold(fmt.money(today.get('cost')))} {fmt.dim('api value')}   "
                 f"{fmt.bold(fmt.tokens(today.get('total')))} {fmt.dim('tokens')}   "
                 f"{fmt.bold(format(today.get('requests') or 0, ','))} {fmt.dim('requests')}   {fmt.dim(hit)}")

    daily = state.get("daily") or []
    if daily:
        lines.append("")
        lines.append(fmt.label(f"last {len(daily)} days") + "  "
                     + fmt.spark([d.get("cost") or (d.get("total") or 0) / 1e6 for d in daily]))

    from . import bar

    accts = bar.account_cells(state, now)
    if accts:
        lines.append("")
        cells = []
        for a in accts:
            cell = f"{'●' if a['active'] else '○'} {a['name']} {fmt.pct(a['pct'])}{bar._age_note(a['age_s'])}"
            cells.append(fmt.bold(cell) if a["active"] else fmt.grey(cell))
        nxt = state.get("next_account")
        lines.append(fmt.label("accounts") + "  " + "   ".join(cells)
                     + ((fmt.dim("   most room: ") + fmt.red(nxt)) if nxt else ""))

    lines.append("")
    foot = "q quit · r refresh"
    if interval:
        foot += f" · every {interval:g}s"
    lines.append(fmt.dim(foot))
    return lines


class _Keys:
    """Single key presses without Enter, on POSIX terminals and Windows."""

    def __init__(self):
        self.old = None
        self.win = os.name == "nt"
        if not self.win and sys.stdin.isatty():
            try:
                import termios
                import tty

                self.old = termios.tcgetattr(sys.stdin.fileno())
                tty.setcbreak(sys.stdin.fileno())
            except (ImportError, OSError, ValueError):
                self.old = None

    def get(self, timeout: float) -> str | None:
        if self.win:
            import msvcrt

            end = time.time() + timeout
            while time.time() < end:
                if msvcrt.kbhit():
                    return msvcrt.getwch()
                time.sleep(0.05)
            return None
        if self.old is None:
            time.sleep(timeout)
            return None
        import select

        r, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1) if r else None

    def close(self):
        if self.old is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old)


def _collect(store, scan: bool) -> tuple[dict, dict | None]:
    st = snapshot.state(store, scan=scan)
    rows = reports.sessions(store, reports.Filter(), limit=1)
    return st, (rows[0] if rows else None)


def cmd_live(a) -> int:
    from .store import open_store

    store = open_store()
    interval = max(1.0, float(getattr(a, "interval", 5.0) or 5.0))
    scan = not getattr(a, "no_scan", False)
    if not sys.stdout.isatty():
        st, sess = _collect(store, scan)
        print("\n".join(fmt._ANSI.sub("", ln) for ln in frame(st, sess, 100)))
        return 0
    fmt.color_enabled()  # on Windows this also switches the console to ANSI mode
    import signal

    def _stop(*_):
        raise KeyboardInterrupt

    # A closed terminal or `kill` must restore the screen as Ctrl-C does.
    for sig in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), _stop)
    keys = _Keys()
    out = sys.stdout
    out.write(ENTER)
    out.flush()
    try:
        while True:
            st, sess = _collect(store, scan)
            lines = frame(st, sess, fmt.width(), interval)
            out.write(HOME + "".join(ln + EOL + "\n" for ln in lines) + EOS)
            out.flush()
            key = keys.get(interval)
            if key and key.lower() == "q":
                break
    except KeyboardInterrupt:
        pass
    finally:
        keys.close()
        out.write(LEAVE)
        out.flush()
    return 0
