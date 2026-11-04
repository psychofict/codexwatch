"""Plan windows from the logs, and the quota forensics built on them."""

import time

from codexwatch import limits
from codexwatch.demo import Rollout, usage
from codexwatch.store import Store

NOW = time.time()


def rl(pct5, reset5, pctw, resetw, plan="pro"):
    return {"limit_id": "codex", "plan_type": plan,
            "primary": {"used_percent": pct5, "window_minutes": 300, "resets_at": int(reset5)},
            "secondary": {"used_percent": pctw, "window_minutes": 10080, "resets_at": int(resetw)}}


def test_current_window_takes_the_highest_reading_and_spots_a_reset(env):
    start = NOW - 3600
    r = Rollout(str(env), start)
    tid = r.turn(start + 1, "gpt-6-astra")
    # Two sessions of one account can log readings out of order: 12 then 11.
    for i, p in enumerate((10, 12, 11)):
        r.response(start + 10 + i, tid, usage(1000, 0, 10), rl(p, NOW + 7200, 40, NOW + 86400), rid=f"a{i}")
    r.close()
    s = Store()
    s.scan()
    main = limits.current(s)[0]
    short = main["windows"][0]
    assert main["plan"] == "pro" and short["label"] == "5h" and short["pct"] == 12
    assert short["since_reset"]["requests"] == 3
    gone = limits.current(s, now=NOW + 7300)[0]["windows"][0]
    assert gone["pct"] == 0 and gone["was"] == 11


def test_burn_rate_and_full_at(env):
    start = NOW - 3000
    r = Rollout(str(env), start)
    tid = r.turn(start, "gpt-6-astra")
    for i in range(6):  # ten points every ten minutes: 60 points an hour
        r.response(start + i * 600, tid, usage(1000, 0, 10), rl(20 + 10 * i, NOW + 4 * 3600, 30, NOW + 86400), rid=f"b{i}")
    r.close()
    s = Store()
    s.scan()
    w = limits.current(s, now=start + 3000)[0]["windows"][0]
    assert 55 <= w["burn_pct_per_h"] <= 65
    assert w["full_at"] is not None and w["full_at"] < NOW + 4 * 3600


def test_exchange_rate_and_a_rise_the_logs_cannot_explain(env):
    start = NOW - 20 * 3600
    wreset = NOW + 5 * 86400
    r = Rollout(str(env), start)
    tid = r.turn(start, "gpt-6-astra")
    pct = 0
    for i in range(20):
        u = usage(100_000, 90_000, 2000)  # $0.29 each at gpt-6-astra prices
        pct += 1
        if i == 15:
            pct += 12  # 12 points from somewhere else
        r.response(start + i * 1800, tid, u, rl(5, start + i * 1800 + 9000, pct, wreset), rid=f"c{i}")
    r.close()
    s = Store()
    s.scan()
    fx = limits.forensics(s, minutes=10080)
    week = fx["windows"][0]
    assert week["max_pct"] == 32 and week["requests"] == 20
    assert abs(week["plan_cost"] - 20 * 0.29) < 1e-6
    assert abs(week["usd_per_pct"] - 20 * 0.29 / 32) < 1e-6
    assert len(fx["jumps"]) == 1
    j = fx["jumps"][0]
    assert j["rise"] == 13 and j["explained_pct"] < 2
    assert fx["already_used"] == []  # the first reading was 1%, below the flag level


def test_window_full_before_the_first_reading_is_not_a_jump(env):
    start = NOW - 3600
    r = Rollout(str(env), start)
    tid = r.turn(start, "gpt-6-astra")
    for i in range(3):
        r.response(start + i * 60, tid, usage(1000, 0, 10), rl(5, NOW + 9000, 40 + i, NOW + 86400), rid=f"e{i}")
    r.close()
    s = Store()
    s.scan()
    fx = limits.forensics(s, minutes=10080)
    assert fx["jumps"] == []
    assert fx["already_used"][0]["pct"] == 40


def test_labels():
    assert limits.window_label(300) == "5h"
    assert limits.window_label(10080) == "week"
    assert limits.window_label(43200) == "30d"
    assert limits.window_label(None) == "window"
