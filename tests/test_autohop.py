import json
import os
import stat
import time

import pytest

from codexwatch import autohop, paths
from codexwatch import store as store_mod
from codexwatch.store import Store

NOW = 1_800_000_000.0


def acct(name, s5=None, wk=None, active=False, age=30, reset_in=3600, limit=False):
    return {
        "name": name, "active": active, "limit_reached": limit, "age_s": age,
        "short": None if s5 is None else {"pct": s5, "resetsAt": NOW + reset_in},
        "long": None if wk is None else {"pct": wk, "resetsAt": NOW + 5 * 86400},
    }


def test_stays_below_threshold():
    d = autohop.decide([acct("a", 91, 50, active=True), acct("b", 0, 0)], NOW)
    assert d["action"] == "stay" and "so stay" in d["why"]


def test_hops_at_five_hour_threshold():
    d = autohop.decide([acct("a", 92, 50, active=True), acct("b", 10, 20)], NOW)
    assert d["action"] == "hop" and d["target"] == "b"


def test_hops_at_weekly_threshold():
    d = autohop.decide([acct("a", 10, 97, active=True), acct("b", 10, 20)], NOW)
    assert d["action"] == "hop" and d["target"] == "b"


def test_target_needs_room():
    d = autohop.decide([acct("a", 95, 50, active=True), acct("b", 80, 20)], NOW)
    assert d["action"] == "wait" and d["target"] is None
    # At the limit, 10% free is enough.
    d = autohop.decide([acct("a", 100, 50, active=True), acct("b", 85, 20)], NOW)
    assert d["action"] == "hop" and d["target"] == "b"


def test_never_into_full_week_or_stale_reading():
    rows = [acct("a", 95, 50, active=True), acct("full", 0, 97), acct("stale", 0, 0, age=900)]
    d = autohop.decide(rows, NOW)
    assert d["action"] == "wait"
    why = {c["name"]: c["why"] for c in d["candidates"]}
    assert "weekly" in why["full"] and "old" in why["stale"]
    # A reading that was just refreshed counts even if its age says otherwise.
    assert autohop.decide(rows, NOW, fresh={"stale"})["target"] == "stale"


def test_prefers_soonest_reset_within_ten_points():
    rows = [acct("a", 95, 50, active=True), acct("late", 12, 10, reset_in=4 * 3600),
            acct("soon", 18, 10, reset_in=1800), acct("empty", 40, 10, reset_in=60)]
    assert autohop.decide(rows, NOW)["target"] == "soon"


def test_cooldown_and_corrective_hop():
    rows = [acct("a", 95, 50, active=True), acct("b", 10, 20)]
    d = autohop.decide(rows, NOW, last_hop_ts=NOW - 300)
    assert d["action"] == "wait" and "minutes" in d["why"]
    at_limit = [acct("a", 100, 50, active=True, limit=True), acct("b", 10, 20)]
    assert autohop.decide(at_limit, NOW, last_hop_ts=NOW - 300)["action"] == "hop"


def test_allowlist():
    rows = [acct("a", 95, 50, active=True), acct("b", 10, 20), acct("c", 0, 0)]
    assert autohop.decide(rows, NOW, {"accounts": ["a", "c"]})["target"] == "c"
    d = autohop.decide(rows, NOW, {"accounts": ["b", "c"]})
    assert d["action"] == "stay" and "not on the autohop list" in d["why"]


def test_single_account_stays():
    assert autohop.decide([acct("a", 100, 100, active=True)], NOW)["action"] == "stay"


# ------------------------------------------------------------ with codexhop

@pytest.fixture
def world(tmp_path, monkeypatch):
    hop_dir = tmp_path / "hop"
    (hop_dir / "accounts").mkdir(parents=True)
    for n in ("a", "b"):
        (hop_dir / "accounts" / f"{n}.json").write_text("{}")
    (hop_dir / "active").write_text("a\n")
    old = time.time() - 3600
    os.utime(hop_dir / "active", (old, old))
    now = time.time()
    (hop_dir / "usage.json").write_text(json.dumps({
        "a": {"usage": {"plan": "plus", "short": {"pct": 95.0, "seconds": 18000, "resetsAt": now + 3600},
                        "long": {"pct": 30.0, "seconds": 604800, "resetsAt": now + 86400}}, "fetchedAt": now},
        "b": {"usage": {"plan": "plus", "short": {"pct": 5.0, "seconds": 18000, "resetsAt": now + 7200},
                        "long": {"pct": 20.0, "seconds": 604800, "resetsAt": now + 86400}}, "fetchedAt": now},
    }))
    calls = tmp_path / "calls.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codexhop"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\n"
                    "if [ \"$1\" = usage ]; then echo '{\"accounts\": []}'; fi\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEXHOP_DIR", str(hop_dir))
    monkeypatch.setenv("CODEXWATCH_DIR", str(tmp_path / "cw"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(store_mod, "_shared", {})
    s = Store(str(tmp_path / "h.db"))
    yield s, calls
    s.close()


def _calls(path):
    return path.read_text().splitlines() if path.exists() else []


def test_dry_run_never_hops(world):
    s, calls = world
    d = autohop.run_once(s, apply=False, scan=False)
    assert d["action"] == "hop" and d["target"] == "b" and not d["applied"]
    assert not any(c.startswith("use") for c in _calls(calls))
    log = open(paths.state_path("autohop.log")).read().splitlines()
    assert json.loads(log[-1])["target"] == "b"


def test_apply_calls_codexhop(world):
    s, calls = world
    d = autohop.run_once(s, apply=True, scan=False)
    assert d["applied"] and d["hop_ok"]
    assert "use b --yes" in _calls(calls)
    # Asked codexhop for fresh readings first, since the live account was past 80%.
    assert "usage --json" in _calls(calls)


def test_kill_switch(world):
    s, calls = world
    paths.ensure_data_dir()
    open(paths.state_path("autohop.off"), "w").close()
    d = autohop.run_once(s, apply=True, scan=False)
    assert d["action"] == "stay" and "kill switch" in d["why"]
    assert _calls(calls) == []
