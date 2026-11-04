"""The database: counted once, however many times or places a log is read."""

import os
import shutil
import time

from codexwatch import config, limits, reports
from codexwatch.demo import Rollout, usage
from codexwatch.store import Store

T0 = time.time() - 7200


def rl(pct, reset, plan="pro", weekly=5.0, wreset=None):
    return {"limit_id": "codex", "plan_type": plan,
            "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": int(reset)},
            "secondary": {"used_percent": weekly, "window_minutes": 10080, "resets_at": int(wreset or T0 + 500000)}}


def session(home, n=3, start=T0, rid_prefix="r", model="gpt-6-astra", pct0=10):
    r = Rollout(str(home), start)
    tid = r.turn(start + 1, model)
    for i in range(n):
        r.response(start + 2 + i, tid, usage(100_000, 90_000, 1000), rl(pct0 + i, start + 15000), rid=f"{rid_prefix}{i}")
    r.close()
    return r


def test_scan_is_incremental_and_idempotent(env):
    r = session(env)
    s = Store()
    first = s.scan()
    assert first["usage"] == 3 and first["changed"] == 1
    assert s.scan()["changed"] == 0
    with open(r.path, "a", encoding="utf-8") as f:
        r2 = Rollout.__new__(Rollout)
        r2.f, r2.n, r2.sid, r2.thread_total = f, 100, r.sid, usage(0, 0, 0)
        tid = r2.turn(T0 + 50, "gpt-6-astra")
        r2.response(T0 + 51, tid, usage(10, 0, 5), rl(20, T0 + 15000), rid="late")
    third = s.scan()
    assert third["usage"] == 1
    assert reports.totals(s)["requests"] == 4


def test_fork_copy_and_second_machine_count_once(env, tmp_path):
    r = session(env)
    # A fork starts with a copy of its parent's lines, response ids and all.
    fork_dir = os.path.dirname(r.path)
    shutil.copy(r.path, os.path.join(fork_dir, "rollout-2026-11-04T00-00-00-fork.jsonl"))
    # Another machine's logs, synced here, hold the same responses again.
    other = tmp_path / "laptop"
    shutil.copytree(env, other)
    config.add_source(str(other), "laptop")
    s = Store()
    s.scan()
    t = reports.totals(s)
    assert t["requests"] == 3
    assert t["total"] == 3 * 101_000


def test_costs_and_unpriced_models(env):
    session(env, n=1, model="gpt-6-astra", rid_prefix="a")
    session(env, n=1, model="gpt-9-mystery", rid_prefix="m", start=T0 + 100)
    s = Store()
    s.scan()
    t = reports.totals(s)
    # gpt-6-astra: 10,000 fresh at $10, 90,000 cached at $1, 1,000 out at $50, per 1M
    assert abs(t["cost"] - (10_000 * 10 + 90_000 * 1 + 1_000 * 50) / 1e6) < 1e-9
    assert t["unpriced_tokens"] == 101_000
    assert t["unpriced_models"] == ["gpt-9-mystery"]


def test_reprice_after_an_override(env):
    session(env, n=2)
    s = Store()
    s.scan()
    before = reports.totals(s)["cost"]
    os.makedirs(os.environ["CODEXWATCH_DIR"], exist_ok=True)
    with open(os.path.join(os.environ["CODEXWATCH_DIR"], "prices.json"), "w") as f:
        f.write('{"models": {"gpt-6-astra": {"input": 20, "cached": 2, "output": 100}}}')
    s.reprice()
    assert abs(reports.totals(s)["cost"] - before * 2) < 1e-9


def test_window_readings_with_drifting_reset_times_are_one_window(env):
    r = Rollout(str(env), T0)
    tid = r.turn(T0 + 1, "gpt-6-astra")
    for i, drift in enumerate((0, 1, 2, 1, 36)):
        r.response(T0 + 2 + i, tid, usage(10, 0, 1), rl(10 + i, T0 + 15000 + drift), rid=f"d{i}")
    r.close()
    s = Store()
    s.scan()
    assert s.one("SELECT COUNT(*) n FROM windows WHERE window_minutes=300")["n"] == 1


def test_a_window_that_has_not_started_is_not_a_window(env):
    r = Rollout(str(env), T0)
    tid = r.turn(T0 + 1, "gpt-6-astra")
    for i in range(3):
        ts = T0 + 2 + i * 60
        r.response(ts, tid, usage(10, 0, 1), {"limit_id": "codex_spark", "plan_type": "pro",
                   "primary": {"used_percent": 0, "window_minutes": 300, "resets_at": int(ts + 18000)}}, rid=f"u{i}")
    r.close()
    s = Store()
    s.scan()
    assert s.one("SELECT COUNT(*) n FROM windows WHERE limit_id='codex_spark'")["n"] == 0
    cur = {c["limit_id"]: c for c in limits.current(s)}
    assert cur["codex_spark"]["windows"][0]["pct"] == 0


def test_session_titles_from_the_index(env):
    r = session(env, n=1)
    (env / "session_index.jsonl").write_text(
        f'{{"id": "{r.sid}", "thread_name": "first"}}\n{{"id": "{r.sid}", "thread_name": "Renamed"}}\n')
    s = Store()
    s.scan()
    assert reports.sessions(s)[0]["title"] == "Renamed"
