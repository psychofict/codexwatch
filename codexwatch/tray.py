"""`codexwatch tray`: a tray icon with your Codex limits, for Linux desktops.

The icon is a ring filled to the 5-hour window, white while there is room,
yellow past the warning level and Rosso Corsa red past the alert level, with
the percentage beside it. The menu holds both windows with their resets,
today's API value, the codexhop accounts and the account with the most room.

It uses AyatanaAppIndicator3 or AppIndicator3 (GNOME with the AppIndicator
extension, KDE, Cinnamon, XFCE), and Gtk.StatusIcon where neither exists.
The ring is drawn by a few lines of Python into a PNG, so the tray needs
nothing beyond the GTK bindings. A background thread re-reads the logs every
`refresh_seconds` and sends any due notification; the GTK thread only draws.

A hop always asks first: it changes the login every new `codex` session
starts with, and codexhop itself refuses one that would land mid-renewal.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import zlib

from . import paths

APP_ID = "codexwatch"
INSTALL_HINT = ("The tray needs GTK 3 bindings. On Debian or Ubuntu: "
                "sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1")
RING_RGB = {"ok": (255, 255, 255), "warn": (246, 229, 0), "alert": (218, 41, 28), "none": (150, 150, 150)}
MAX_ACCOUNTS = 10


# ------------------------------------------------------------------- the icon

def _png(width: int, height: int, rgba_rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rgba_rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def ring_png(pct: float | None, level: str = "ok", size: int = 44) -> bytes:
    """A ring filled clockwise from the top to `pct`, as PNG bytes.

    A translucent white track shows the empty part; the arc has round ends.
    With no reading the ring stays empty with a dot in the middle, so it never
    passes for an empty window. Each pixel is sampled 4x4 for smooth edges.
    """
    c = size / 2.0
    thick = size * 0.17
    mid = c - thick / 2 - size * 0.05
    r_out, r_in = mid + thick / 2, mid - thick / 2
    frac = None if pct is None else max(0.0, min(100.0, float(pct))) / 100.0
    if frac is not None and 0 < frac < 0.03:
        frac = 0.03
    end = (frac or 0) * 2 * math.pi
    color = RING_RGB.get(level, RING_RGB["ok"])
    caps = []
    # Past 90% round ends would close the last gap, and 98% would read as full.
    if frac and frac <= 0.9:
        for ang in (0.0, end):
            caps.append((c + mid * math.sin(ang), c - mid * math.cos(ang)))
    n = 4
    rows = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            arc = track = dot = 0
            for sy in range(n):
                for sx in range(n):
                    px = x + (sx + 0.5) / n
                    py = y + (sy + 0.5) / n
                    dx, dy = px - c, py - c
                    d = math.hypot(dx, dy)
                    if frac is None:
                        if d <= size * 0.08:
                            dot += 1
                        elif r_in <= d <= r_out:
                            track += 1
                        continue
                    in_ring = r_in <= d <= r_out
                    theta = math.atan2(dx, -dy) % (2 * math.pi)
                    on_arc = frac >= 1 or (in_ring and theta <= end)
                    if not on_arc and frac:
                        on_arc = any(math.hypot(px - cx, py - cy) <= thick / 2 for cx, cy in caps)
                    if on_arc and (in_ring or frac < 1):
                        arc += 1
                    elif in_ring:
                        track += 1
            total = n * n
            if arc:
                a = arc / total
                # the track shows through where the arc only partly covers the pixel
                t = (track / total) * 0.28
                alpha = a + t * (1 - a)
                rgb = tuple(int(round((color[i] * a + 255 * t * (1 - a)) / alpha)) for i in range(3))
                row += bytes((*rgb, int(round(alpha * 255))))
            elif dot:
                row += bytes((255, 255, 255, int(round(dot / total * 0.8 * 255))))
            elif track:
                row += bytes((255, 255, 255, int(round(track / total * 0.28 * 255))))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return _png(size, size, rows)


def icon_path(pct: float | None, level: str, size: int = 44) -> str:
    """The cached PNG for this figure. The name changes with the figure, because
    AppIndicator only reloads an icon whose path changed."""
    d = paths.state_path("tray-icons")
    os.makedirs(d, exist_ok=True)
    key = "na" if pct is None else str(int(round(max(0.0, min(100.0, pct)))))
    path = os.path.join(d, f"ring-{level}-{key}-{size}.png")
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(ring_png(pct, level, size))
        os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ the text

def menu_text(state: dict) -> dict:
    """Every label the tray shows, from a snapshot state. Kept apart from GTK so
    it can be read and tested without a desktop."""
    from . import bar

    p = bar.parts(state or {})
    out = {"label": "–", "icon_pct": None, "level": "none", "title": "codexwatch", "windows": [],
           "today": "Today: no usage yet", "accounts": [], "next": p["next"], "tooltip": bar._tooltip(state or {})}
    if p["short"]:
        out["label"] = f"{p['short_pct']:.0f}%"
        out["icon_pct"] = p["short_pct"]
        out["level"] = p["short_level"]
    elif p["long"]:
        out["label"] = f"{p['long_pct']:.0f}%"
        out["icon_pct"] = p["long_pct"]
        out["level"] = p["long_level"]
    title = " · ".join(x for x in (p["plan"], p["account"]) if x)
    if title:
        out["title"] = title
    import time

    now = time.time()
    for w in (p["short"], p["long"]):
        if not w:
            continue
        name = {"5h": "5 hours", "week": "Week"}.get(w.get("label"), w.get("label") or "")
        if w.get("was") is not None:
            out["windows"].append(f"{name}: reset (was {w['was']:.0f}%)")
            continue
        r = w.get("resets_at")
        tail = f" · resets {bar._clock(r)} (in {bar._left(r - now)})" if r and r > now else ""
        text = f"{name}: {w['pct']:.0f}%{tail}"
        if w.get("full_at"):
            text += f" · full at {bar._clock(w['full_at'])}"
        out["windows"].append(text)
    today = ((state or {}).get("periods") or {}).get("today") or {}
    if today.get("total"):
        out["today"] = f"Today: {bar._money(today.get('cost'))} API value, {_tok(today.get('total'))} tokens"
    for a in bar.account_cells(state or {}, now):
        pct = f"{a['pct']:.0f}%" if a["pct"] is not None else "no reading"
        out["accounts"].append(f"{'●' if a['active'] else '○'} {a['name']}: 5h {pct}{bar._age_note(a['age_s'])}")
    return out


def _tok(n) -> str:
    n = float(n or 0)
    for div, unit in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


def _self_command() -> list[str]:
    exe = shutil.which("codexwatch")
    return [exe] if exe else [sys.executable, "-m", "codexwatch"]


# -------------------------------------------------------------------- the app

class Tray:
    def __init__(self, Gtk, GLib, indicator_mod):
        from . import config

        self.Gtk, self.GLib = Gtk, GLib
        self.indicator = None
        self.status_icon = None
        self.wake = threading.Event()
        self.busy = False
        self.state: dict = {}
        self.interval = max(5.0, float(config.get("refresh_seconds") or 10))
        self.menu = Gtk.Menu()
        self.items: dict = {}
        self._build_menu()
        first_icon = icon_path(None, "none")
        if indicator_mod is not None:
            ind = indicator_mod.Indicator.new(APP_ID, first_icon,
                                              indicator_mod.IndicatorCategory.APPLICATION_STATUS)
            ind.set_status(indicator_mod.IndicatorStatus.ACTIVE)
            try:
                ind.set_title("codexwatch")
            except AttributeError:
                pass
            ind.set_label("–", "100%")
            ind.set_menu(self.menu)
            self.indicator = ind
        else:
            si = Gtk.StatusIcon.new_from_file(first_icon)
            si.set_tooltip_text("codexwatch")
            si.connect("popup-menu", lambda icon, button, t: self.menu.popup(
                None, None, Gtk.StatusIcon.position_menu, icon, button, t))
            si.connect("activate", lambda icon: self.menu.popup(
                None, None, Gtk.StatusIcon.position_menu, icon, 1, Gtk.get_current_event_time()))
            self.status_icon = si

    def _item(self, key: str, text: str, cb=None, sensitive: bool = True):
        it = self.Gtk.MenuItem(label=text)
        if cb:
            it.connect("activate", cb)
        it.set_sensitive(sensitive and cb is not None)
        self.menu.append(it)
        self.items[key] = it
        return it

    def _sep(self):
        self.menu.append(self.Gtk.SeparatorMenuItem())

    def _build_menu(self):
        self._item("title", "codexwatch")
        self._item("win0", "Reading the Codex logs…")
        self._item("win1", "")
        self._sep()
        self._item("today", "")
        self.items["acct_sep"] = self.Gtk.SeparatorMenuItem()
        self.menu.append(self.items["acct_sep"])
        for i in range(MAX_ACCOUNTS):
            self._item(f"acct{i}", "")
        self._item("hop", "Hop to…", self._on_hop)
        self._item("read", "Read every account", self._on_read_all)
        self._sep()
        self._item("dash", "Open dashboard", self._on_dashboard)
        self._item("refresh", "Refresh", lambda *_: self.wake.set())
        self._item("quit", "Quit", lambda *_: self.Gtk.main_quit())
        self.menu.show_all()
        for k in ("win1", "acct_sep", "hop", "read", *[f"acct{i}" for i in range(MAX_ACCOUNTS)]):
            self.items[k].hide()

    # -- refresh -------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, name="codexwatch-refresh", daemon=True).start()

    def _loop(self):
        from . import notify, snapshot
        from .store import open_store

        store = open_store()
        while True:
            try:
                st = snapshot.state(store, scan=True)
                self.GLib.idle_add(self.apply, st)
                notify.check_and_send(st)
            except Exception as e:  # noqa: BLE001 - keep the tray alive; say why once per failure
                print(f"codexwatch tray: {e}", file=sys.stderr)
            self.wake.wait(self.interval)
            self.wake.clear()

    def apply(self, st: dict):
        self.state = st
        t = menu_text(st)
        self.items["title"].set_label(t["title"])
        for i in range(2):
            it = self.items[f"win{i}"]
            if i < len(t["windows"]):
                it.set_label(t["windows"][i])
                it.show()
            elif i == 0:
                it.set_label("No plan readings in the Codex logs yet")
                it.show()
            else:
                it.hide()
        self.items["today"].set_label(t["today"])
        accts = t["accounts"]
        self.items["acct_sep"].set_visible(bool(accts))
        for i in range(MAX_ACCOUNTS):
            it = self.items[f"acct{i}"]
            if i < len(accts):
                it.set_label(accts[i])
                it.show()
            else:
                it.hide()
        has_hop = bool(accts) and shutil.which("codexhop") is not None
        self.items["read"].set_visible(has_hop)
        self.items["read"].set_label("Reading every account…" if self.busy else "Read every account")
        self.items["read"].set_sensitive(not self.busy)
        self.items["hop"].set_visible(has_hop and bool(t["next"]))
        if t["next"]:
            self.items["hop"].set_label(f"Hop to {t['next']} (most room)…")
        path = icon_path(t["icon_pct"], t["level"])
        desc = f"Codex 5-hour window {t['label']} used" if t["icon_pct"] is not None else "No current reading"
        if self.indicator is not None:
            self.indicator.set_icon_full(path, desc)
            self.indicator.set_label(t["label"], "100%")
        elif self.status_icon is not None:
            self.status_icon.set_from_file(path)
            self.status_icon.set_tooltip_text(t["tooltip"])
        return False

    # -- actions ---------------------------------------------------------------

    def _on_dashboard(self, *_):
        try:
            subprocess.Popen(_self_command() + ["dashboard"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            print(f"codexwatch tray: could not open the dashboard: {e}", file=sys.stderr)

    def _confirm(self, text: str, detail: str) -> bool:
        Gtk = self.Gtk
        dlg = Gtk.MessageDialog(transient_for=None, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.YES_NO, text=text)
        dlg.format_secondary_text(detail)
        dlg.set_title("codexwatch")
        try:
            return dlg.run() == Gtk.ResponseType.YES
        finally:
            dlg.destroy()

    def _on_hop(self, *_):
        target = (self.state or {}).get("next_account")
        if not target or self.busy:
            return
        if not self._confirm(f"Hop Codex to {target}?",
                             "New codex sessions will start as this account. Sessions already open keep "
                             "their login until it next renews."):
            return
        self._run_bg(lambda: __import__("codexwatch.hop", fromlist=["hop_to"]).hop_to(target))

    def _on_read_all(self, *_):
        if self.busy:
            return
        self._run_bg(lambda: __import__("codexwatch.hop", fromlist=["refresh"]).refresh())

    def _run_bg(self, fn):
        self.busy = True
        if self.state:
            self.apply(self.state)

        def work():
            try:
                ok, msg = fn()
                if not ok:
                    print(f"codexwatch tray: {msg}", file=sys.stderr)
            finally:
                self.busy = False
                self.wake.set()

        threading.Thread(target=work, daemon=True).start()


def _single_instance():
    """Hold a lock for as long as the tray runs; None when another tray has it."""
    try:
        import fcntl
    except ImportError:
        return True
    paths.ensure_data_dir()
    fh = open(paths.state_path("tray.lock"), "w")  # noqa: SIM115 - held open for the process lifetime
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def _load_gtk():
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    indicator = None
    for name in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(name, "0.1")
            indicator = getattr(__import__("gi.repository", fromlist=[name]), name)
            break
        except (ValueError, ImportError, AttributeError):
            continue
    return Gtk, GLib, indicator


def _system_python_with_gtk() -> str | None:
    """The distribution's Python, when it has the GTK bindings this one lacks.

    PyGObject comes from the distribution (python3-gi), not from PyPI, so a
    pipx or venv install cannot import it. The system Python can, and
    codexwatch needs nothing else, so the tray runs there instead.
    """
    import subprocess

    for exe in ("/usr/bin/python3", "/usr/local/bin/python3"):
        # A venv's python is often a link to this very binary; what differs is the venv.
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        if not os.path.exists(exe) or (not in_venv and os.path.realpath(exe) == os.path.realpath(sys.executable)):
            continue
        probe = subprocess.run([exe, "-c", "import gi; gi.require_version('Gtk', '3.0')"],
                               capture_output=True, timeout=20)
        if probe.returncode == 0:
            return exe
    return None


def _rerun_with(exe: str) -> None:
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    sys.stdout.flush()
    os.execve(exe, [exe, "-m", "codexwatch", "tray"], env)


def cmd_tray(a) -> int:
    if sys.platform == "darwin" or os.name == "nt":
        print("The tray is for Linux. On macOS use `codexwatch bar -f swiftbar` with SwiftBar or xbar; "
              "everywhere, `codexwatch dashboard`.", file=sys.stderr)
        return 1
    try:
        Gtk, GLib, indicator = _load_gtk()
    except (ImportError, ValueError):
        system = _system_python_with_gtk()
        if system:
            _rerun_with(system)  # does not return
        print(INSTALL_HINT, file=sys.stderr)
        return 1
    ok = Gtk.init_check(sys.argv[:1])
    if isinstance(ok, tuple):  # PyGObject returns (ok, argv)
        ok = ok[0]
    if not ok:
        print("No desktop to show a tray on (DISPLAY or WAYLAND_DISPLAY is not set).", file=sys.stderr)
        return 1
    lock = _single_instance()
    if lock is None:
        print("codexwatch tray is already running.", file=sys.stderr)
        return 0
    tray = Tray(Gtk, GLib, indicator)
    tray.start()
    import signal

    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Ctrl-C ends Gtk.main()
    Gtk.main()
    return 0
