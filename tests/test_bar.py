"""Status-bar formats and the tray's text, from a synthetic snapshot state."""

import json
import struct
import time
import zlib

from codexwatch import bar, tray

NOW = time.time()


def state(short=98.0, long_=15.0, accounts=None, today=10.39):
    def win(label, minutes, pct, resets_in):
        return {"label": label, "minutes": minutes, "pct": pct, "was": None,
                "resets_at": NOW + resets_in, "starts_at": NOW + resets_in - minutes * 60,
                "full_at": None, "burn_pct_per_h": None, "since_reset": {"plan_cost": 1.0}}

    return {
        "headline": {"short": win("5h", 300, short, 3 * 3600) if short is not None else None,
                     "long": win("week", 10080, long_, 6 * 86400) if long_ is not None else None,
                     "plan": "plus", "read_at": NOW - 120},
        "settings": {"warn_pct": 70, "alert_pct": 90},
        "periods": {"today": {"cost": today, "total": 6_700_000}, "7d": {"cost": 20.0},
                    "month": {"cost": 30.0}, "all": {"cost": 2053.56}},
        "plan": {"name": "Plus"},
        "live_account": "home",
        "next_account": "work" if accounts else None,
        "accounts": accounts or [],
    }


def test_plain_line_and_empty_state():
    assert bar.line(state()) == "5h 98% · wk 15% · $10.39 today"
    assert bar.line({}) == bar.EMPTY
    assert bar.line(state(short=None, long_=None)) == bar.EMPTY
    assert bar.render({}, "waybar")[0].startswith("{")


def test_levels_drive_colour_only_past_warning():
    t = bar.tmux(state(short=50.0))
    assert "#[fg=" not in t
    t = bar.tmux(state(short=75.0))
    assert "#[fg=#f6e500]5h 75%#[default]" in t
    t = bar.tmux(state(short=95.0))
    assert "#[fg=#da291c]5h 95%#[default]" in t
    assert "%{F#da291c}5h 95%%{F-}" in bar.polybar(state(short=95.0))


def test_prompt_has_no_escape_codes_off_a_terminal():
    assert "\033" not in bar.prompt(state(), tty=False)
    assert "\033[38;2;218;41;28m" in bar.prompt(state(), tty=True)


def test_i3blocks_marks_urgent_past_alert():
    text, code = bar.i3blocks(state())
    full, short, color = text.splitlines()
    assert full.startswith("5h 98%") and short == "5h 98%" and color == "#da291c"
    assert code == 33
    assert bar.i3blocks(state(short=10.0))[1] == 0


def test_waybar_json():
    doc = json.loads(bar.waybar(state()))
    assert doc["class"] == "alert" and doc["percentage"] == 98
    assert "5 hours: 98%" in doc["tooltip"]


def test_swiftbar_menu():
    out = bar.swiftbar(state(accounts=[{"name": "home", "active": True, "short": {"pct": 0.0},
                                        "fetched_at": NOW - 2 * 86400},
                                       {"name": "work", "active": False, "short": {"pct": 12.0},
                                        "fetched_at": NOW - 600}]))
    lines = out.splitlines()
    assert lines[0].startswith("5h 98% · wk 15% | ") and "color=#da291c" in lines[0]
    assert "---" in lines
    assert any(ln.startswith("Open dashboard | bash=") and "terminal=false" in ln for ln in lines)
    assert "Refresh | refresh=true" in lines
    # the live account shows the newer reading from the logs, not codexhop's old cache
    assert any("● home  5h 98%" in ln for ln in lines)
    assert any("○ work  5h 12%" in ln for ln in lines)
    assert any("Most room: work" in ln for ln in lines)
    xbar = bar.swiftbar(state(), sf_symbols=False).splitlines()[0]
    assert "sfimage" not in xbar


def test_headline_json():
    doc = json.loads(bar.headline_json(state()))
    assert doc["short"]["pct"] == 98.0 and doc["short"]["level"] == "alert"
    assert doc["long"]["level"] == "ok" and doc["level"] == "alert"


def test_account_cells_prefers_newer_cache():
    st = state(accounts=[{"name": "home", "active": True, "short": {"pct": 40.0}, "fetched_at": NOW - 10}])
    # codexhop read it after the logs' last reading, so its figure wins
    assert bar.account_cells(st, NOW)[0]["pct"] == 40.0


def _png_size(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    # every chunk's CRC must be right or desktops refuse the icon
    pos = 8
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        assert zlib.crc32(kind + body) & 0xFFFFFFFF == crc
        pos += 12 + n
    return w, h


def test_tray_ring_png_is_valid():
    for pct, level in ((None, "none"), (0, "ok"), (57, "warn"), (98, "alert"), (100, "alert")):
        assert _png_size(tray.ring_png(pct, level, 22)) == (22, 22)


def test_tray_menu_text():
    t = tray.menu_text(state())
    assert t["label"] == "98%" and t["level"] == "alert" and t["icon_pct"] == 98.0
    assert t["windows"][0].startswith("5 hours: 98% · resets")
    assert t["today"].startswith("Today: $10.39 API value")
    empty = tray.menu_text({})
    assert empty["label"] == "–" and empty["level"] == "none" and empty["windows"] == []
