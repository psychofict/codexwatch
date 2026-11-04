import time
from datetime import datetime, timedelta

import pytest

from codexwatch import budgets, cli
from codexwatch import store as store_mod
from codexwatch.store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXWATCH_DIR", str(tmp_path / "cw"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEXHOP_DIR", str(tmp_path / "hop"))
    monkeypatch.setenv("CODEXWATCH_NOTIFY_DRYRUN", "1")
    monkeypatch.setattr(store_mod, "_shared", {})
    (tmp_path / "codex" / "sessions").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def s(env):
    st = Store(str(env / "h.db"))
    yield st
    st.close()


def put(s, rid, ts, cost, total, project="app", model="gpt-6-sol"):
    with s.lock:
        s.db.execute(
            "INSERT INTO usage(response_id, ts, session_id, model, project, total, input, output, cost, plan_cost) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", (rid, ts, "s1", model, project, total, total, 0, cost, cost))
        s.db.commit()


def window(s, minutes, pct, now):
    reset = now + 3600
    with s.lock:
        s.db.execute(
            "INSERT INTO limits(ts, session_id, response_id, limit_id, plan, slot, window_minutes, used_pct, "
            "resets_at, win_reset) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (now - 60, "s1", None, "codex", "plus", "primary" if minutes == 300 else "secondary",
             minutes, pct, reset, reset))
        s.db.execute("INSERT INTO windows(limit_id, window_minutes, resets_at, max_pct) VALUES(?,?,?,?)",
                     ("codex", minutes, reset, pct))
        s.db.commit()


def test_day_budget_counts_today_only(s):
    now = time.time()
    midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    put(s, "r1", midnight + 1, 3.0, 1000)
    put(s, "r2", midnight + 2, 2.0, 500)
    put(s, "old", midnight - 3600, 100.0, 9999)
    budgets.add(s, "daily", "usd", 10, "day")
    budgets.add(s, "tight", "usd", 4, "day", notify_pct=50)
    rows = {r["name"]: r for r in budgets.status(s, now)}
    assert rows["daily"]["used"] == pytest.approx(5.0)
    assert rows["daily"]["pct"] == pytest.approx(50.0)
    assert not rows["daily"]["over"] and not rows["daily"]["notify"]
    assert rows["tight"]["over"] and rows["tight"]["notify"]
    assert rows["daily"]["resets_at"] == pytest.approx(midnight + 86400)


def test_scoped_token_and_model_budgets(s):
    now = time.time()
    put(s, "a", now - 10, 1.0, 700, project="web")
    put(s, "b", now - 10, 1.0, 300, project="api", model="gpt-6-astra")
    budgets.add(s, None, "tokens", 1000, "week", "project:web")
    budgets.add(s, None, "usd", 2, "month", "model:gpt-6-astra")
    tok, usd = budgets.status(s, now)
    assert tok["used"] == 700 and tok["kind"] == "tokens"
    assert "web" in tok["name"]
    assert usd["used"] == pytest.approx(1.0) and usd["pct"] == pytest.approx(50.0)


def test_week_starts_monday(s):
    now = time.time()
    monday = (datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
              - timedelta(days=datetime.fromtimestamp(now).weekday())).timestamp()
    assert budgets.period_start("week", now) == pytest.approx(monday)
    first = datetime.fromtimestamp(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    assert budgets.period_start("month", now) == pytest.approx(first)


def test_pct_budget_reads_plan_window(s):
    now = time.time()
    window(s, 300, 85.0, now)
    window(s, 10080, 40.0, now)
    budgets.add(s, "5h", "pct", 80, "5h")
    budgets.add(s, "wk", "pct", 50, "week-window")
    rows = {r["name"]: r for r in budgets.status(s, now)}
    assert rows["5h"]["used"] == pytest.approx(85.0) and rows["5h"]["over"]
    assert rows["wk"]["used"] == pytest.approx(40.0)
    assert rows["wk"]["notify"] and not rows["wk"]["over"]


@pytest.mark.parametrize("kwargs", [
    dict(kind="pct", amount=50, period="day"),
    dict(kind="usd", amount=5, period="5h"),
    dict(kind="usd", amount=0, period="day"),
    dict(kind="usd", amount=5, period="day", scope="team:x"),
    dict(kind="pct", amount=150, period="5h"),
    dict(kind="coins", amount=5, period="day"),
])
def test_rejects_bad_budgets(s, kwargs):
    with pytest.raises(ValueError):
        budgets.add(s, None, **kwargs)


def test_remove(s):
    bid = budgets.add(s, "x", "usd", 5, "day")
    assert budgets.remove(s, bid) == 1
    assert budgets.status(s) == []


def test_cli_add_list_check(env, capsys):
    assert cli.main(["budget", "add", "--usd", "5", "--every", "day"]) == 0
    assert "added budget 1" in capsys.readouterr().out
    assert cli.main(["budget", "list", "--no-color"]) == 0
    assert "$5 a day" in capsys.readouterr().out
    assert cli.main(["budget", "check", "--json"]) == 0
    assert '"over": false' in capsys.readouterr().out
    assert cli.main(["budget", "add", "--usd", "5", "--tokens", "3"]) == 2
    assert cli.main(["budget", "rm", "--id", "1"]) == 0
