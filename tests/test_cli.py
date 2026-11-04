"""Every command runs, on invented logs, in text and JSON."""

import json
import time

import pytest

from codexwatch import cli
from codexwatch.demo import generate


@pytest.fixture
def demo_env(env):
    generate(str(env), days=21, now=time.time())
    return env


TEXT_COMMANDS = [[], ["daily"], ["weekly"], ["monthly", "--csv"], ["projects"], ["models", "-p", "7d"], ["clients"],
                 ["machines"], ["tiers"], ["efforts"], ["agents"], ["hours"], ["sessions", "-n", "5"], ["tools"],
                 ["latency"], ["limits"], ["quota"], ["quota", "--window", "5h"], ["scan"], ["prices"], ["sources"],
                 ["config"], ["doctor"], ["reprice"], ["export", "--format", "csv", "-p", "today"],
                 ["budget", "list"], ["bar"], ["bar", "-f", "waybar"], ["bar", "-f", "swiftbar"], ["notify"]]


@pytest.mark.parametrize("args", TEXT_COMMANDS, ids=lambda a: " ".join(a) or "overview")
def test_text_commands(demo_env, capsys, args):
    assert cli.main(args) in (0, 33)  # i3blocks-style urgent exit is allowed for bar formats
    out = capsys.readouterr().out
    assert out.strip()


@pytest.mark.parametrize("args", [["--json"], ["daily", "--json"], ["models", "--json"], ["sessions", "--json"],
                                  ["limits", "--json"], ["quota", "--json"], ["tools", "--json"], ["latency", "--json"],
                                  ["export", "--format", "json", "-p", "7d"], ["scan", "--json"]],
                         ids=lambda a: " ".join(a))
def test_json_commands(demo_env, capsys, args):
    assert cli.main(args) == 0
    json.loads(capsys.readouterr().out)


def test_overview_numbers_agree_with_daily(demo_env, capsys):
    cli.main(["--json"])
    ov = json.loads(capsys.readouterr().out)
    cli.main(["daily", "--json", "-p", "all"])
    daily = json.loads(capsys.readouterr().out)
    assert abs(ov["periods"]["all"]["cost"] - daily["totals"]["cost"]) < 1e-6
    assert abs(sum(r["cost"] for r in daily["rows"]) - daily["totals"]["cost"]) < 1e-6


def test_empty_home_does_not_crash(env, capsys):
    assert cli.main([]) == 0
    assert "No plan readings" in capsys.readouterr().out


def test_bad_period_is_a_usage_error(env, capsys):
    assert cli.main(["daily", "-p", "fortnight"]) == 2


def test_config_set_and_sources(env, tmp_path, capsys):
    assert cli.main(["config", "set", "plan_price", "200"]) == 0
    capsys.readouterr()
    cli.main(["config", "get", "plan_price"])
    assert json.loads(capsys.readouterr().out) == 200
    other = tmp_path / "other"
    (other / "sessions").mkdir(parents=True)
    assert cli.main(["sources", "add", str(other), "--machine", "laptop"]) == 0
    capsys.readouterr()
    cli.main(["sources", "--json"])
    assert [s["machine"] for s in json.loads(capsys.readouterr().out)][1:] == ["laptop"]
