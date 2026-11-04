"""The parser against each way Codex logs have been miscounted."""

import json
import time

from codexwatch import parser
from codexwatch.demo import Rollout, iso, usage

T0 = time.time() - 3600


def limits(pct=10.0, reset=None):
    return {"limit_id": "codex", "primary": {"used_percent": pct, "window_minutes": 300, "resets_at": int(reset or T0 + 9000)},
            "secondary": {"used_percent": pct / 2, "window_minutes": 10080, "resets_at": int(T0 + 400000)},
            "plan_type": "pro"}


def test_one_record_per_response_and_repeated_token_count_ignored(env):
    r = Rollout(str(env), T0)
    tid = r.turn(T0 + 1, "gpt-6-astra")
    r.response(T0 + 2, tid, usage(1000, 800, 50, 10), limits(), rid="resp_a", repeat_count=True)
    r.response(T0 + 3, tid, usage(2000, 1900, 70), limits(12), rid="resp_b", repeat_count=True)
    r.close()
    b, off, st = parser.scan(r.path)
    assert [u["response_id"] for u in b.usage] == ["resp_a", "resp_b"]
    assert sum(u["total"] for u in b.usage) == 1050 + 2070
    assert b.usage[0]["model"] == "gpt-6-astra"
    assert b.usage[0]["cached"] == 800 and b.usage[0]["reasoning"] == 10


def test_model_and_tier_follow_each_turn(env):
    r = Rollout(str(env), T0)
    t1 = r.turn(T0 + 1, "gpt-6-astra", "xhigh")
    r.response(T0 + 2, t1, usage(10, 0, 1), limits())
    t2 = r.turn(T0 + 3, "gpt-5.6-luna", "low", tier="priority")
    r.response(T0 + 4, t2, usage(10, 0, 1), limits())
    r.close()
    b, _, _ = parser.scan(r.path)
    assert [(u["model"], u["effort"], u["tier"]) for u in b.usage] == [
        ("gpt-6-astra", "xhigh", "default"), ("gpt-5.6-luna", "low", "priority")]


def test_limit_reading_points_at_the_response_before_it(env):
    r = Rollout(str(env), T0)
    tid = r.turn(T0 + 1, "gpt-6-astra")
    r.response(T0 + 2, tid, usage(10, 0, 1), limits(40), rid="resp_x")
    r.close()
    b, _, _ = parser.scan(r.path)
    assert {x["slot"] for x in b.limits} == {"primary", "secondary"}
    assert all(x["response_id"] == "resp_x" for x in b.limits)
    assert next(x for x in b.limits if x["slot"] == "primary")["used_pct"] == 40


def test_subagent_and_copied_parent_header(env):
    parent = "01a0b325-7eeb-79b0-ba33-075a483bb503"
    r = Rollout(str(env), T0, source={"subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_nickname": "Maxwell"}}},
                forked_from=parent, nickname="Maxwell")
    # A subagent log carries its parent's header as a second session_meta.
    r.line(T0, "session_meta", {"id": parent, "timestamp": iso(T0 - 100), "cwd": "/elsewhere", "source": "cli"})
    tid = r.turn(T0 + 1, "gpt-6-sol")
    r.response(T0 + 2, tid, usage(10, 0, 1), limits())
    r.close()
    b, _, st = parser.scan(r.path)
    assert b.session["session_id"] == r.sid
    assert b.session["subagent"] == 1 and b.session["nickname"] == "Maxwell"
    assert b.session["parent_id"] == parent
    assert b.usage[0]["subagent"] == 1 and b.usage[0]["session_id"] == r.sid


def test_old_logs_with_running_totals_only(env, tmp_path):
    path = tmp_path / "rollout-old.jsonl"
    lines = [{"timestamp": iso(T0), "type": "session_meta", "payload": {"id": "old", "cwd": "/w", "source": "cli"}},
             {"timestamp": iso(T0 + 1), "type": "turn_context", "payload": {"turn_id": "t1", "model": "gpt-5-codex"}}]
    totals = [(1000, 100), (1000, 100), (2500, 300), (2500, 300), (4000, 350)]
    for i, (inp, out) in enumerate(totals):
        tot = {"input_tokens": inp, "cached_input_tokens": 0, "output_tokens": out, "total_tokens": inp + out}
        lines.append({"timestamp": iso(T0 + 2 + i), "type": "event_msg",
                      "payload": {"type": "token_count", "info": {"total_token_usage": tot, "last_token_usage": tot}}})
    path.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines))
    b, _, _ = parser.scan(str(path))
    assert len(b.usage) == 3  # repeats of an unchanged total are not requests
    assert sum(u["total"] for u in b.usage) == 4350
    assert all(u["how"] == "totals" and u["model"] == "gpt-5-codex" for u in b.usage)


def test_partial_last_line_is_left_for_the_next_scan(env):
    r = Rollout(str(env), T0)
    tid = r.turn(T0 + 1, "gpt-6-astra")
    r.response(T0 + 2, tid, usage(10, 0, 1), limits(), rid="resp_1")
    r.f.flush()
    whole = json.dumps({"timestamp": iso(T0 + 3), "ordinal": 99, "type": "token_usage_record",
                        "payload": {"thread_id": r.sid, "turn_id": tid, "response_id": "resp_2",
                                    "usage": usage(5, 0, 1)}}, separators=(",", ":"))
    r.raw(whole[:40])
    r.f.flush()
    b1, off, st = parser.scan(r.path)
    assert [u["response_id"] for u in b1.usage] == ["resp_1"]
    r.raw(whole[40:] + "\n")
    r.close()
    b2, off2, _ = parser.scan(r.path, off, st)
    assert [u["response_id"] for u in b2.usage] == ["resp_2"]
    assert b2.usage[0]["model"] == "gpt-6-astra"  # the turn map survived the resume


def test_tools_including_code_mode_and_mcp(env):
    r = Rollout(str(env), T0)
    r.tool(T0 + 1, ["exec_command", "web__run"])
    r.tool(T0 + 2, [])
    r.line(T0 + 3, "response_item", {"type": "function_call", "name": "apply_patch", "arguments": "{}"})
    r.line(T0 + 4, "compacted", {"message": "", "replacement_history": []})
    r.close()
    b, _, _ = parser.scan(r.path)
    assert sorted(t["name"] for t in b.tools) == ["apply_patch", "code_mode", "exec_command", "web__run"]
    assert len(b.compactions) == 1


def test_malformed_lines_never_stop_a_scan(env):
    r = Rollout(str(env), T0)
    r.raw('{"timestamp":"x","type":"token_usage_record","payload":{"type":\n')
    r.raw("not json at all\n")
    tid = r.turn(T0 + 1, "gpt-6-astra")
    r.response(T0 + 2, tid, usage(10, 0, 1), limits(), rid="resp_ok")
    r.close()
    b, _, _ = parser.scan(r.path)
    assert [u["response_id"] for u in b.usage] == ["resp_ok"]


def test_turn_timing(env):
    r = Rollout(str(env), T0)
    tid = r.turn(T0 + 1, "gpt-6-astra")
    r.done(T0 + 61, tid, T0 + 1, ttft_ms=1500)
    r.close()
    b, _, _ = parser.scan(r.path)
    done = [t for t in b.turns if t["completed"]][0]
    assert done["duration_ms"] == 60000 and done["ttft_ms"] == 1500 and done["model"] == "gpt-6-astra"


def test_project_and_client_names():
    assert parser.project_of("/home/a/work/site", {"repository_url": "git@github.com:me/thing.git"}) == "thing"
    assert parser.project_of("/home/a/work/site", None) == "site"
    assert parser.project_of("C:\\Users\\a\\proj", None) == "proj"
    assert parser.client_of("vscode", "codex-tui") == "ide"
    assert parser.client_of({"subagent": {}}, "codex-tui") == "cli"
    assert parser.client_of("exec", None) == "exec"
    assert parser.client_of(None, "Codex Desktop") == "desktop"


def test_parse_ts_variants():
    assert parser.parse_ts("2026-09-07T05:39:41.895Z") == parser.parse_ts("2026-09-07T05:39:41.895000+00:00")
    assert parser.parse_ts("2026-09-07T05:39:41.895123456Z") is not None  # nanoseconds
    assert parser.parse_ts(1788759581) == 1788759581.0
    assert parser.parse_ts("nonsense") is None
