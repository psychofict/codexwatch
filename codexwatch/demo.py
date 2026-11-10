"""Made-up Codex logs, for trying codexwatch and for its screenshots.

`codexwatch demo` writes two months of invented sessions into a folder of its
own and opens the dashboard on them, so you can see every view before you
have logs of your own. Nothing here touches your real Codex home or history:
the logs and the database both live in the demo folder.

The writer also builds the test suite's fixtures, so tests and demo share one
idea of what a rollout looks like.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

PROJECTS = ["atlas-api", "lumen-web", "orbit-mobile", "ledger-core", "nimbus-infra", "papertrail-docs"]
MODELS = [("gpt-6-astra", 0.62), ("gpt-6.1-sol", 0.22), ("gpt-5.6-terra", 0.1), ("gpt-5.6-luna", 0.06)]
TITLES = ["Fix flaky payment webhook test", "Add rate limiting to the public API", "Migrate auth to passkeys",
          "Speed up the search index build", "Write the release notes for 4.2", "Refactor the billing worker",
          "Track down the memory leak in sync", "Add dark mode to settings", "Port the CLI to async",
          "Review the database migration plan", "Clean up feature flags", "Draft the onboarding emails"]
TOOLS = ["exec_command", "exec_command", "exec_command", "apply_patch", "write_stdin", "view_image", "web__run"]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def usage(inp: int, cached: int, out: int, reasoning: int = 0, write: int = 0) -> dict:
    return {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": write,
            "output_tokens": out, "reasoning_output_tokens": reasoning, "total_tokens": inp + out}


class Rollout:
    """Writes one rollout file line by line, in Codex's own format."""

    def __init__(self, home: str, start: float, sid: str | None = None, cwd: str = "/work/atlas-api",
                 source="cli", originator: str = "codex-tui", git: dict | None = None,
                 forked_from: str | None = None, nickname: str | None = None):
        self.sid = sid or str(uuid.uuid4())
        dt = datetime.fromtimestamp(start)
        folder = os.path.join(home, "sessions", f"{dt:%Y}", f"{dt:%m}", f"{dt:%d}")
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, f"rollout-{dt:%Y-%m-%dT%H-%M-%S}-{self.sid}.jsonl")
        self.f = open(self.path, "a", encoding="utf-8", newline="\n")
        self.n = 0
        self.thread_total = usage(0, 0, 0)
        meta = {"session_id": self.sid, "id": self.sid, "timestamp": iso(start), "cwd": cwd,
                "originator": originator, "cli_version": "0.166.0", "source": source,
                "model_provider": "openai", "base_instructions": {"text": "You are Codex."}}
        if git:
            meta["git"] = git
        if forked_from:
            meta["forked_from_id"] = forked_from
            meta["parent_thread_id"] = forked_from
        if nickname:
            meta["agent_nickname"] = nickname
        self.line(start, "session_meta", meta)

    def line(self, ts: float, kind: str, payload: dict) -> None:
        self.f.write(json.dumps({"timestamp": iso(ts), "ordinal": self.n, "type": kind, "payload": payload},
                                separators=(",", ":")) + "\n")
        self.n += 1

    def raw(self, text: str) -> None:
        self.f.write(text)

    def turn(self, ts: float, model: str, effort: str = "high", tier: str = "default") -> str:
        tid = str(uuid.uuid4())
        self.line(ts, "event_msg", {"type": "task_started", "turn_id": tid, "started_at": int(ts)})
        self.line(ts, "event_msg", {"type": "thread_settings_applied", "thread_id": self.sid,
                                    "thread_settings": {"model": model, "service_tier": tier}})
        self.line(ts, "turn_context", {"turn_id": tid, "root_turn_id": tid, "cwd": "/work", "model": model,
                                       "effort": effort})
        return tid

    def response(self, ts: float, tid: str, u: dict, rate_limits: dict | None = None, rid: str | None = None,
                 repeat_count: bool = False) -> str:
        rid = rid or "resp_" + uuid.uuid4().hex
        for k in self.thread_total:
            self.thread_total[k] += u.get(k, 0)
        self.line(ts, "token_usage_record", {"thread_id": self.sid, "turn_id": tid, "session_id": self.sid,
                                             "response_id": rid, "usage": u, "turn_token_usage": u,
                                             "thread_token_usage": dict(self.thread_total)})
        tc = {"type": "token_count", "info": {"total_token_usage": dict(self.thread_total), "last_token_usage": u,
                                              "model_context_window": 258400},
              "rate_limits": rate_limits}
        self.line(ts + 0.001, "event_msg", tc)
        if repeat_count:
            self.line(ts + 0.002, "event_msg", tc)  # Codex sometimes repeats this event
        return rid

    def tool(self, ts: float, names: list[str]) -> None:
        code = "\n".join(f"text(await tools.{n}({{cmd:\"ls\"}}))" for n in names) or "console.log(1)"
        self.line(ts, "response_item", {"type": "custom_tool_call", "status": "completed", "call_id": "c",
                                        "name": "exec", "input": code})

    def done(self, ts: float, tid: str, started: float, ttft_ms: int = 2500) -> None:
        self.line(ts, "event_msg", {"type": "task_complete", "turn_id": tid, "started_at": int(started),
                                    "completed_at": int(ts), "duration_ms": int((ts - started) * 1000),
                                    "time_to_first_token_ms": ttft_ms})

    def close(self) -> None:
        self.f.close()


def limits(plan: str, short_pct: float, short_reset: float, long_pct: float, long_reset: float) -> dict:
    return {"limit_id": "codex", "limit_name": None,
            "primary": {"used_percent": round(short_pct), "window_minutes": 300, "resets_at": int(short_reset)},
            "secondary": {"used_percent": round(long_pct), "window_minutes": 10080, "resets_at": int(long_reset)},
            "credits": {"has_credits": False, "unlimited": False, "balance": "0"}, "plan_type": plan,
            "rate_limit_reached_type": None}


def generate(home: str, days: int = 60, seed: int = 7, now: float | None = None) -> dict:
    """Write `days` of invented Pro-plan usage into `home`. Returns counts."""
    from .pricing import load

    rng = random.Random(seed)
    now = now or time.time()
    card = load()
    w = {"week_reset": now - days * 86400 + 7 * 86400 - 3 * 3600, "week": 0.0, "short_reset": None, "short": 0.0,
         "jump": False, "responses": 0}
    usd_per_pct_week, usd_per_pct_5h = 12.5, 2.1  # in line with what real Pro windows show
    sessions = []
    day0 = datetime.fromtimestamp(now - days * 86400).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def run(start: float, end: float, turns: int, per_turn: tuple, last_days: bool) -> float:
        project = rng.choice(PROJECTS)
        r = Rollout(home, start, cwd=f"/home/dev/{project}", source=rng.choice(["cli", "cli", "cli", "vscode", "exec"]),
                    git={"repository_url": f"https://github.com/example/{project}.git", "branch": "main"})
        t = start + 5
        fast = rng.random() < 0.12
        for _ in range(turns):
            if t >= end:
                break
            model = rng.choices([m for m, _w in MODELS], [x for _m, x in MODELS])[0]
            tid = r.turn(t, model, rng.choice(["high", "xhigh", "medium"]), "priority" if fast else "default")
            started = t
            for _k in range(rng.randint(*per_turn)):
                t += rng.uniform(4, 40)
                if t >= end:
                    break
                ctx = rng.randint(80_000, 240_000)
                cached = int(ctx * rng.uniform(0.88, 0.97))
                out = rng.randint(800, 6000)
                u = usage(ctx, cached, out, int(out * rng.uniform(0.2, 0.6)))
                # Fast mode spends plan limits 2.5 times as fast as Standard.
                plan_cost = (card.cost(model, ctx, cached, 0, out, None) or 0) * (2.5 if fast else 1.0)
                while t >= w["week_reset"]:
                    w["week_reset"] += 7 * 86400
                    w["week"] = 0.0
                if w["short_reset"] is None or t >= w["short_reset"]:
                    w["short_reset"], w["short"] = t + 5 * 3600, 0.0
                w["week"] = min(100.0, w["week"] + plan_cost / usd_per_pct_week)
                w["short"] = min(100.0, w["short"] + plan_cost / usd_per_pct_5h)
                if not w["jump"] and last_days and w["week"] > 20:
                    w["week"] = min(100.0, w["week"] + 9)  # usage from another machine
                    w["jump"] = True
                r.response(t, tid, u, limits("pro", w["short"], w["short_reset"], w["week"], w["week_reset"]),
                           repeat_count=rng.random() < 0.05)
                w["responses"] += 1
                if rng.random() < 0.7:
                    r.tool(t + 1, rng.sample(TOOLS, rng.randint(1, 2)))
            t = min(t + rng.uniform(20, 120), end)
            r.done(t, tid, started, rng.randint(1200, 6000))
            t += rng.uniform(60, 600)
        r.close()
        sessions.append(r.sid)
        with open(os.path.join(home, "session_index.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": r.sid, "thread_name": rng.choice(TITLES), "updated_at": iso(t)}) + "\n")
        return t

    live_start = now - 2.4 * 3600
    last_end = 0.0
    for d in range(days + 1):
        base = day0 + d * 86400
        weekday = datetime.fromtimestamp(base).weekday()
        if rng.random() > (0.35 if weekday >= 5 else 0.92):
            continue
        # Sessions in clock order: the plan's counter only moves forward in time.
        starts = sorted(base + rng.uniform(8.5, 21.5) * 3600 for _s in range(rng.randint(2, 5) if weekday < 5 else 1))
        for start in starts:
            start = max(start, last_end + 300)
            if start > live_start - 3600:
                continue
            last_end = run(start, live_start - 600, rng.randint(3, 10), (4, 22), d > days - 9)
    # The session running now, busy enough that the 5-hour window shows a burn rate.
    run(live_start, now - 45, 40, (14, 26), True)
    return {"sessions": len(sessions), "responses": w["responses"]}


def demo_codexhop(store: str, now: float, home: str | None = None) -> None:
    """Three invented codexhop accounts and one invented provider, so the multi-account views have something to show."""
    os.makedirs(os.path.join(store, "accounts"), exist_ok=True)
    if home:
        with open(os.path.join(home, "azure.config.toml"), "w", encoding="utf-8") as f:
            f.write('model_provider = "azure"\nmodel = "gpt-5-codex"\n\n[model_providers.azure]\n'
                    'base_url = "https://demo-foundry.openai.azure.com/openai/v1"\n')
    for name in ("work", "personal", "side"):
        with open(os.path.join(store, "accounts", f"{name}.json"), "w", encoding="utf-8") as f:
            f.write("{}")  # names only; a real account file holds a login, these hold nothing
    with open(os.path.join(store, "active"), "w", encoding="utf-8") as f:
        f.write("work\n")
    # The last hop was yesterday, so today's log readings belong to "work".
    os.utime(os.path.join(store, "active"), (now - 86400, now - 86400))
    cache = {
        "personal": {"usage": {"plan": "pro", "short": {"pct": 12.0, "seconds": 18000, "resetsAt": now + 4200},
                               "long": {"pct": 31.0, "seconds": 604800, "resetsAt": now + 4 * 86400}},
                     "fetchedAt": now - 240},
        "side": {"usage": {"plan": "plus", "short": {"pct": 64.0, "seconds": 18000, "resetsAt": now + 7600},
                           "long": {"pct": 88.0, "seconds": 604800, "resetsAt": now + 2 * 86400}},
                 "fetchedAt": now - 300},
    }
    with open(os.path.join(store, "usage.json"), "w", encoding="utf-8") as f:
        json.dump(cache, f)


def cmd_demo(a) -> int:
    root = os.path.join(tempfile.gettempdir(), "codexwatch-demo")
    home = os.path.join(root, "codex-home")
    if not os.path.isdir(os.path.join(home, "sessions")) or getattr(a, "fresh", False):
        import shutil

        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(home, exist_ok=True)
        n = generate(home, days=getattr(a, "days", 60) or 60)
        demo_codexhop(os.path.join(root, "codexhop"), time.time(), home)
        print(f"wrote {n['sessions']} invented sessions ({n['responses']:,} responses) to {home}")
    os.environ["CODEX_HOME"] = home
    os.environ["CODEXWATCH_DIR"] = os.path.join(root, "data")
    os.environ["CODEXHOP_DIR"] = os.path.join(root, "codexhop")
    os.environ["CODEXWATCH_DEMO"] = "1"  # the dashboard refuses to hop or read accounts
    if getattr(a, "terminal", False):
        from .cli import main

        return main(["--no-color"] if not sys.stdout.isatty() else [])
    from .server import serve

    print("demo data only: none of this is your usage")
    return serve(port=getattr(a, "port", 7879) or 7879, open_browser=not getattr(a, "no_open", False))
