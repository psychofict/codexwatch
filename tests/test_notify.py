"""Which notifications are due, and that each is sent once."""

import time

import pytest

from codexwatch import notify

NOW = 1_800_000_000.0


def state(pct, starts_at=NOW - 3600, was=None, budgets=None, limit_id="codex", name=None, nxt=None):
    return {
        "limits": [{"limit_id": limit_id, "name": name, "windows": [
            {"label": "5h", "minutes": 300, "pct": pct, "was": was, "starts_at": starts_at,
             "resets_at": starts_at + 300 * 60 + 1.4, "full_at": None, "account": None}]}],
        "budgets": budgets or [],
        "next_account": nxt,
    }


def keys(alerts):
    return [a["key"] for a in alerts]


def test_below_first_step_sends_nothing():
    assert notify.due_alerts(state(60), {}, now=NOW) == []


def test_one_alert_for_the_highest_step_crossed():
    due = notify.due_alerts(state(98), {}, now=NOW)
    assert len(due) == 1
    a = due[0]
    assert a["key"].endswith(":90") and a["level"] == "alert"
    assert sorted(m.rsplit(":", 1)[1] for m in a["marks"]) == ["75", "90"]
    assert "Codex 5-hour window at 98%" == a["title"]


def test_sent_once_per_window():
    hist = {}
    for a in notify.due_alerts(state(80), hist, now=NOW):
        for k in a["marks"]:
            hist[k] = NOW
    assert notify.due_alerts(state(85), hist, now=NOW) == []
    # crossing the next step alerts again, once
    due = notify.due_alerts(state(91), hist, now=NOW)
    assert keys(due) and keys(due)[0].endswith(":90")
    # the same window read with a reset time a second later is still the same window
    st = state(91)
    st["limits"][0]["windows"][0]["resets_at"] += 1.0
    for a in due:
        for k in a["marks"]:
            hist[k] = NOW
    assert notify.due_alerts(st, hist, now=NOW) == []
    # a new window starts the count again
    assert keys(notify.due_alerts(state(80, starts_at=NOW + 3600), hist, now=NOW))


def test_full_window_and_reset_window():
    due = notify.due_alerts(state(100, nxt="work"), {}, now=NOW)
    assert due[0]["title"] == "Codex 5-hour window is full"
    assert "work has the most room" in due[0]["body"]
    assert notify.due_alerts(state(0.0, was=97.0), {}, now=NOW) == []


def test_model_limit_is_named():
    due = notify.due_alerts(state(80, limit_id="codex_bengalfox", name="GPT-5.3-Codex-Spark"), {}, now=NOW)
    assert due[0]["title"].startswith("GPT-5.3-Codex-Spark 5-hour window")


def test_budgets_alert_at_notify_level_and_when_spent():
    b = {"id": 1, "name": "Monthly cap", "used": 85.0, "limit": 100.0, "pct": 85.0, "notify_pct": 80,
         "period": "month", "kind": "usd", "over": False}
    due = notify.due_alerts(state(10, budgets=[b]), {}, now=NOW)
    assert len(due) == 1 and due[0]["title"] == "Budget Monthly cap at 85%"
    assert due[0]["body"] == "$85.00 of $100.00 this month."
    hist = {k: NOW for k in due[0]["marks"]}
    assert notify.due_alerts(state(10, budgets=[b]), hist, now=NOW) == []
    spent = dict(b, used=101.0, pct=101.0, over=True)
    due = notify.due_alerts(state(10, budgets=[spent]), hist, now=NOW)
    assert len(due) == 1 and due[0]["title"] == "Budget spent: Monthly cap"


def test_budget_fields_read_defensively():
    # no pct: worked out from used and limit; odd types are ignored, not raised
    due = notify.due_alerts(state(10, budgets=[{"name": "t", "used": "90", "limit": 100}, "junk", {}]),
                            {}, now=NOW)
    assert len(due) == 1 and due[0]["title"] == "Budget t at 90%"


def test_send_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("CODEXWATCH_NOTIFY_DRYRUN", "1")
    assert notify.send("Title", "Body", "alert")
    assert "[notify:alert] Title — Body" in capsys.readouterr().err


def test_check_and_send_remembers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEXWATCH_DIR", str(tmp_path))
    monkeypatch.setenv("CODEXWATCH_NOTIFY_DRYRUN", "1")
    st = state(92, starts_at=time.time() - 600)
    sent = notify.check_and_send(st, enabled=True)
    assert len(sent) == 1
    assert notify.check_and_send(st, enabled=True) == []
    assert (tmp_path / "notified.json").exists()
    assert notify.check_and_send(state(99, starts_at=time.time() + 60), enabled=False) == []


def test_history_drops_old_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXWATCH_DIR", str(tmp_path))
    now = time.time()
    notify.save_history({"old": now - 40 * 86400, "new": now}, now)
    assert notify.load_history() == {"new": pytest.approx(now)}
