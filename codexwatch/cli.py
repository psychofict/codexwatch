"""The `codexwatch` command."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from . import __version__, config, fmt, hop, limits, paths, periods, pricing, reports
from .store import open_store

PROG = "codexwatch"


# -------------------------------------------------------------------- helpers

def _filter(a) -> reports.Filter:
    since = until = None
    if getattr(a, "period", None):
        since, until, _ = periods.parse(a.period)
    if getattr(a, "since", None):
        since = periods.parse_date(a.since)
    if getattr(a, "until", None):
        until = periods.parse_date(a.until, end=True)
    return reports.Filter(since=since, until=until, project=getattr(a, "project", None),
                          model=getattr(a, "model", None), client=getattr(a, "client", None),
                          machine=getattr(a, "machine", None), account=getattr(a, "account", None))


def _label(a) -> str:
    if getattr(a, "since", None) or getattr(a, "until", None):
        return f"{a.since or 'start'} to {a.until or 'now'}"
    return periods.parse(getattr(a, "period", None) or "all")[2]


def _store(a):
    s = open_store()
    if not getattr(a, "no_scan", False):
        s.scan()
    return s


def _accounts(cur: list[dict]) -> list[dict]:
    """codexhop's accounts, with the live one's reading taken from the logs when newer."""
    from . import snapshot

    accts = hop.accounts_view()
    snapshot.overlay_live(accts, snapshot.headline(cur))
    return accts


def _emit_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _cost_cell(r: dict) -> str:
    return fmt.money(r.get("cost"), unpriced=bool(r.get("unpriced_tokens")))


def _unpriced_note(t: dict) -> str | None:
    if not t.get("unpriced_tokens"):
        return None
    models = ", ".join(t.get("unpriced_models") or []) or "some models"
    return fmt.dim(f"* {fmt.tokens(t['unpriced_tokens'])} tokens have no published price ({models}) "
                   "and are not in the dollar figures.")


# ------------------------------------------------------------------- overview

def cmd_overview(a) -> int:
    s = _store(a)
    f = _filter(a)
    ov = reports.overview(s, f)
    cur = limits.current(s)
    accts = _accounts(cur)
    if a.json:
        _emit_json({"periods": ov, "limits": cur, "accounts": accts,
                    "daily": reports.daily_series(s, 30, f)})
        return 0
    w = fmt.width()
    last = s.meta("last_scan")
    head = fmt.red("codexwatch", bold=True)
    right = fmt.dim(f"read {fmt.clock(last, False)}")
    print(head + " " * max(1, w - 10 - fmt.visible_len(right)) + right)
    print(fmt.dim("─" * w))
    _print_limits(cur, accts, compact=True)
    print()
    rows = []
    for key in ("today", "7d", "month", "all"):
        t = ov[key]
        rows.append([t["label"], _cost_cell(t), fmt.tokens(t["total"]),
                     f"{t['requests']:,}", fmt.pct(t["cache_hit"] * 100, 1) if t["input"] else "-"])
    print(fmt.table(["", "api value", "tokens", "requests", "cache hit"], rows, "lrrrr"))
    series = reports.daily_series(s, 30, f)
    print()
    print(fmt.label("last 30 days") + "  " + fmt.spark([d["cost"] or d["total"] / 1e6 for d in series])
          + "  " + fmt.dim(f"{series[0]['key'][5:]} → today"))
    print()
    since30 = periods.parse("30d")[0]
    f30 = reports.Filter(**{**f.__dict__, "since": since30})
    models = reports.group(s, "model", f30, limit=5)
    projects = reports.group(s, "project", f30, limit=5)
    tot = sum(r["cost"] for r in models) or 1
    print(fmt.label("models · 30 days"))
    for r in models:
        print(f"  {r['key']:<22} {fmt.money(r['cost']):>11}  {fmt.bar(r['cost'] / tot * 100, 16, 101, 101)}"
              f"  {fmt.tokens(r['total']):>7}")
    print()
    print(fmt.label("projects · 30 days"))
    tot = sum(r["cost"] for r in projects) or 1
    for r in projects:
        print(f"  {r['key'][:22]:<22} {fmt.money(r['cost']):>11}  {fmt.bar(r['cost'] / tot * 100, 16, 101, 101)}"
              f"  {fmt.tokens(r['total']):>7}")
    note = _unpriced_note(ov["all"])
    if note:
        print("\n" + note)
    print(fmt.dim(f"\nDollar figures are API-equivalent estimates. `{PROG} help` lists every view."))
    return 0


def _print_limits(cur: list[dict], accts: list[dict], compact: bool = False) -> None:
    cfg = config.load()
    warn, alert = cfg["warn_pct"], cfg["alert_pct"]
    main = [c for c in cur if c["limit_id"] == "codex"] or cur[:1]
    if not main:
        print(fmt.dim("No plan readings in the logs yet. They appear after your first Codex request."))
    main_read = main[0]["read_at"] if main else 0
    for c in (main if compact else cur):
        if c not in main and c["read_at"] < main_read - 6 * 3600:
            # Another limit (a model's own cap) that has not been read lately.
            print(fmt.dim(f"  {c['name'] or c['limit_id']}: last read {fmt.ago(c['read_at'])}"))
            continue
        plan = pricing.load().plans.get(c["plan"] or "", {}).get("name") or c["plan"] or "plan"
        who = next((a["name"] for a in accts if a["active"]), None)
        title = f"{c['name'] or plan}" + (f" · live account {who}" if who and c["limit_id"] == "codex" else "")
        age = fmt.dim(f"read {fmt.ago(c['read_at'])}")
        print(fmt.label("limits") + "  " + fmt.bold(title) + "  " + age)
        for wdw in c["windows"]:
            p = wdw["pct"]
            if wdw["was"] is not None:
                tail = fmt.dim(f"reset since the last reading (was {wdw['was']:.0f}%)")
            else:
                tail = fmt.grey(f"resets {fmt.clock(wdw['resets_at'])} ({fmt.duration(wdw['resets_in_s'])})")
                if wdw["full_at"]:
                    tail += "  " + fmt.red(f"full at {fmt.clock(wdw['full_at'])}")
                sr = wdw["since_reset"]
                if sr["plan_cost"]:
                    tail += fmt.dim(f"  {fmt.money(sr['plan_cost'])} since reset")
            pc = fmt.paint(f"{p:>3.0f}%", fmt.level_rgb(p, warn, alert), bold=p >= alert)
            print(f"  {wdw['label']:<5} {fmt.bar(p, 26, warn, alert)} {pc}   {tail}")
    if len(accts) > 1:
        nxt = hop.best_next(accts)
        line = []
        for r in accts:
            s5 = r["short"]["pct"] if r["short"] else None
            cell = f"{r['name']} {fmt.pct(s5)}"
            line.append(fmt.bold(cell) if r["active"] else fmt.grey(cell))
        print("  " + fmt.dim("accounts ") + "  ".join(line)
              + (fmt.dim("   most room: ") + fmt.red(nxt["name"]) if nxt else ""))


# -------------------------------------------------------------- report views

def cmd_report(a) -> int:
    by = {"daily": "day", "weekly": "week", "monthly": "month", "projects": "project", "models": "model",
          "clients": "client", "machines": "machine", "hours": "hour", "tiers": "tier", "efforts": "effort",
          "agents": "agent"}[a.cmd]
    s = _store(a)
    f = _filter(a)
    rows = reports.group(s, by, f, limit=getattr(a, "limit", None))
    t = reports.totals(s, f)
    if a.json:
        _emit_json({"period": _label(a), "group": by, "rows": rows, "totals": t})
        return 0
    if getattr(a, "csv", False):
        return _write_csv(rows, sys.stdout)
    if by == "hour":
        return _print_hours(s, f, a)
    keyname = {"day": "date", "week": "week", "month": "month"}.get(by, by)
    out = []
    for r in rows:
        out.append([str(r["key"]), fmt.tokens(r["input"]), fmt.tokens(r["cached"]), fmt.tokens(r["output"]),
                    fmt.tokens(r["reasoning"]), fmt.tokens(r["total"]), _cost_cell(r),
                    fmt.pct(r["cache_hit"] * 100) if r["input"] else "-"])
    out.append([fmt.bold("total"), fmt.tokens(t["input"]), fmt.tokens(t["cached"]), fmt.tokens(t["output"]),
                fmt.tokens(t["reasoning"]), fmt.bold(fmt.tokens(t["total"])), fmt.red(_cost_cell(t), bold=True),
                fmt.pct(t["cache_hit"] * 100) if t["input"] else "-"])
    print(fmt.label(f"{a.cmd} · {_label(a)}"))
    print(fmt.table([keyname, "input", "cached", "output", "reasoning", "total", "api value", "cache"], out))
    note = _unpriced_note(t)
    if note:
        print(note)
    return 0


def _print_hours(s, f, a) -> int:
    grid = reports.heatmap(s, f)
    top = max(max(r) for r in grid) or 1
    shades = " ░▒▓█"
    print(fmt.label(f"when you use codex · {_label(a)}"))
    print("      " + "".join(f"{h:<3}" if h % 3 == 0 else "   " for h in range(24)))
    for i, day in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        cells = ""
        for v in grid[i]:
            k = 0 if not v else 1 + min(3, int(v / top * 4))
            cells += fmt.paint(shades[k] * 3, fmt.RED if k == 4 else fmt.WHITE) if k else fmt.dim(" · ")
        print(f"  {day} {cells}")
    return 0


def cmd_sessions(a) -> int:
    s = _store(a)
    rows = reports.sessions(s, _filter(a), limit=a.limit)
    if a.json:
        _emit_json(rows)
        return 0
    out = []
    for r in rows:
        name = r["title"] or r["session_id"][:8]
        if r["subagent"]:
            name = f"↳ {r['nickname'] or 'subagent'}: {name}"
        out.append([fmt.clock(r["last"]), name[:44], (r["project"] or "")[:18], ",".join(m for m in r["models"] if m)[:16],
                    str(r["turns"]), fmt.tokens(r["total"]), fmt.money(r["cost"]),
                    str(r["compactions"]) if r["compactions"] else "-"])
    print(fmt.label(f"sessions · {_label(a)}"))
    print(fmt.table(["last", "session", "project", "model", "turns", "tokens", "api value", "compacted"],
                    out, "llllrrrr"))
    return 0


def cmd_tools(a) -> int:
    s = _store(a)
    rows = reports.tools(s, _filter(a), limit=a.limit)
    if a.json:
        _emit_json(rows)
        return 0
    print(fmt.label(f"tool calls · {_label(a)}"))
    for r in rows:
        print(f"  {r['name'][:28]:<28} {r['calls']:>7,}  {fmt.bar(r['share'] * 100, 20, 101, 101)}"
              f"  {fmt.dim(str(r['sessions']) + ' sessions')}")
    return 0


def cmd_latency(a) -> int:
    s = _store(a)
    rows = reports.latency(s, _filter(a))
    if a.json:
        _emit_json(rows)
        return 0
    out = [[r["model"], str(r["turns"]), fmt.duration(r["median_s"]), fmt.duration(r["p90_s"]),
            f"{r['ttft_median_s']:.1f}s" if r["ttft_median_s"] else "-"] for r in rows]
    print(fmt.label(f"turn time by model · {_label(a)}"))
    print(fmt.table(["model", "turns", "median turn", "90% under", "first token"], out))
    return 0


def cmd_limits(a) -> int:
    s = _store(a)
    if a.refresh:
        ok, msg = hop.refresh()
        print(fmt.dim(("codexhop: " if ok else "codexhop failed: ") + msg), file=sys.stderr)
        s.scan()
    cur = limits.current(s)
    accts = _accounts(cur)
    if a.json:
        _emit_json({"limits": cur, "accounts": accts, "next": hop.best_next(accts)})
        return 0
    _print_limits(cur, accts)
    if accts:
        print()
        print(fmt.label("codexhop accounts"))
        out = []
        for r in accts:
            sh, lg = r["short"], r["long"]
            out.append([("* " if r["active"] else "  ") + r["name"], r["plan"] or "-",
                        f"{fmt.pct(sh['pct'])} {fmt.dim('resets ' + fmt.clock(sh.get('resetsAt')))}" if sh else "-",
                        f"{fmt.pct(lg['pct'])} {fmt.dim('resets ' + fmt.clock(lg.get('resetsAt')))}" if lg else "-",
                        fmt.ago(r["fetched_at"])])
        print(fmt.table(["account", "plan", "5 hours", "week", "read"], out, "lllll"))
        if not a.refresh:
            print(fmt.dim(f"Readings are codexhop's last. `{PROG} limits --refresh` asks for new ones."))
    return 0


def cmd_quota(a) -> int:
    s = _store(a)
    minutes = {"5h": 300, "week": 10080, None: None}.get(a.window, None)
    fx = limits.forensics(s, a.limit_id, minutes, limit=a.limit)
    if a.json:
        _emit_json(fx)
        return 0
    print(fmt.label("what a percent of your plan costs"))
    out = []
    for w in fx["windows"]:
        out.append([w["label"], w.get("plan_name") or w["plan"] or "-", w["account"] or "-", fmt.clock(w["starts_at"]),
                    fmt.pct(w["max_pct"]), fmt.money(w["plan_cost"]), fmt.tokens(w["tokens"]),
                    fmt.money(w["usd_per_pct"]) if w["usd_per_pct"] else "-",
                    fmt.tokens(w["tokens_per_pct"]) if w["tokens_per_pct"] else "-"])
    print(fmt.table(["window", "plan", "account", "started", "used", "api value", "tokens", "per 1%", "tokens/1%"],
                    out, "llllrrrrr"))
    if fx["jumps"]:
        print()
        print(fmt.label("rises these logs cannot explain"))
        for j in fx["jumps"]:
            to = fmt.red(f"{j['to_pct']:.0f}%")
            print(f"  {fmt.clock(j['ts'])}  {j['label']:<5} {j['from_pct']:.0f}% → {to}"
                  f" in {fmt.duration((j['minutes'] or 0) * 60)}; local usage explains about {j['explained_pct']:.0f}"
                  f" point{'' if round(j['explained_pct']) == 1 else 's'}"
                  + (fmt.dim(f" ({j['account']})") if j["account"] else ""))
        print(fmt.dim("  Usage from another machine, ChatGPT itself or cloud tasks counts here but is not in these logs."))
    if fx["already_used"]:
        print()
        print(fmt.label("already used before this machine's first reading"))
        for b in fx["already_used"][:8]:
            print(f"  {fmt.clock(b['ts'])}  {b['label']:<5} {b['pct']:.0f}%" + (fmt.dim(f" ({b['account']})") if b["account"] else ""))
    if fx["timeline"]:
        print()
        print(fmt.label("exchange rate by day"))
        out = [[d["day"], d["label"], limits._plan_name(d["plan"]) or "-", f"{d['pct']:.0f} pts", fmt.money(d["plan_cost"]),
                fmt.money(d["usd_per_pct"]) if d["usd_per_pct"] else "-"] for d in fx["timeline"][-a.limit:]]
        print(fmt.table(["day", "window", "plan", "rise", "api value", "per 1%"], out, "lllrrr"))
    print(fmt.dim("\nAPI value counts fast mode at 2.5x, the rate at which it uses plan limits."))
    return 0


# --------------------------------------------------------------------- other

def cmd_scan(a) -> int:
    s = open_store()
    if a.full:
        s.forget_files()
    st = s.scan(progress=None)
    if a.json:
        _emit_json(st)
    else:
        print(f"read {st['changed']} of {st['files']} logs in {st['seconds']}s: "
              f"{st['usage']:,} responses, {st['limits']:,} limit readings")
    return 0


def cmd_export(a) -> int:
    s = _store(a)
    f = _filter(a)
    where, args = f.where()
    rows = [dict(r) for r in s.q(f"SELECT * FROM usage{where} ORDER BY ts", args)]
    if a.format == "json":
        _emit_json(rows)
    else:
        _write_csv(rows, sys.stdout)
    return 0


def _write_csv(rows: list[dict], out) -> int:
    if not rows:
        return 0
    w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return 0


def cmd_prices(a) -> int:
    card = pricing.load()
    if a.action == "update":
        try:
            n = pricing.update_from_litellm()
        except Exception as e:  # noqa: BLE001 - shown to the user
            print(f"could not update prices: {e}", file=sys.stderr)
            return 1
        s = open_store()
        s.reprice()
        print(f"saved LiteLLM's prices for {n} OpenAI models and re-priced the history. "
              f"Your own prices.json still wins.")
        return 0
    if a.action == "path":
        print(paths.prices_override_path())
        return 0
    if a.json:
        _emit_json({"as_of": card.as_of, "models": card.models, "tiers": card.tiers, "plans": card.plans})
        return 0
    out = []
    for name, r in sorted(card.models.items()):
        out.append([name, f"{r.get('input', 0):g}", f"{r['cached']:g}" if r.get("cached") is not None else "-",
                    f"{r['cache_write']:g}" if r.get("cache_write") is not None else "-", f"{r.get('output', 0):g}",
                    "yes" if r.get("long") else "-", r.get("source", "")])
    print(fmt.label(f"prices · usd per 1m tokens · as of {card.as_of}"))
    print(fmt.table(["model", "input", "cached", "cache write", "output", "long ctx", "source"], out, "lrrrrll"))
    print(fmt.dim(f"Override in {paths.prices_override_path()}; `{PROG} prices update` pulls LiteLLM's list."))
    return 0


def cmd_sources(a) -> int:
    if a.action == "add":
        if not a.path:
            print("give the folder: codexwatch sources add PATH --machine NAME", file=sys.stderr)
            return 2
        if not paths.session_dirs(a.path):
            print(f"no Codex logs found under {a.path}", file=sys.stderr)
            return 1
        name = a.machine or os.path.basename(os.path.realpath(os.path.expanduser(a.path)))
        config.add_source(a.path, name)
        st = open_store().scan()
        print(f"added {a.path} as {name}; read {st['changed']} logs, {st['usage']:,} new responses "
              "(responses already counted from another copy are not counted again)")
        return 0
    if a.action == "rm":
        print("removed" if config.remove_source(a.path or "") else "no such source")
        return 0
    srcs = config.sources()
    if a.json:
        _emit_json(srcs)
        return 0
    for s in srcs:
        print(f"  {s['machine']:<20} {s['path']}")
    return 0


def cmd_config(a) -> int:
    if a.action == "path":
        print(paths.data_dir())
        return 0
    if a.action == "get":
        doc = config.load()
        _emit_json(doc if not a.key else doc.get(a.key))
        return 0
    if a.action == "set":
        if not a.key or a.value is None:
            print("usage: codexwatch config set KEY VALUE", file=sys.stderr)
            return 2
        try:
            value = json.loads(a.value)
        except ValueError:
            value = a.value
        config.set_value(a.key, value)
        print(f"{a.key} = {json.dumps(value)}")
        return 0
    return 2


def cmd_doctor(a) -> int:
    ok = True
    home = paths.codex_home()
    dirs = paths.session_dirs(home)
    print(f"codexwatch {__version__}, Python {sys.version.split()[0]}, {sys.platform}")
    print(f"Codex home      {home} {'(found)' if os.path.isdir(home) else '(missing)'}")
    print(f"log folders     {', '.join(dirs) or 'none'}")
    if not dirs:
        ok = False
    print(f"codexwatch data {paths.data_dir()}")
    s = open_store()
    st = s.scan()
    t = reports.totals(s)
    print(f"history         {t['requests']:,} responses in {t['sessions']} sessions; scan took {st['seconds']}s")
    if t["unpriced_tokens"]:
        print(f"unpriced        {fmt.tokens(t['unpriced_tokens'])} tokens from {', '.join(t['unpriced_models'])}")
    n = len(hop.accounts())
    print(f"codexhop        {f'installed, {n} account' + ('' if n == 1 else 's') if hop.installed() else 'not installed (optional)'}")
    card = pricing.load()
    print(f"prices          bundled list as of {card.as_of}"
          + ("; LiteLLM list present" if os.path.exists(paths.litellm_prices_path()) else ""))
    return 0 if ok else 1


# ---------------------------------------------------------------- dispatch

def _lazy(module: str, fn: str):
    def run(a):
        mod = __import__(f"codexwatch.{module}", fromlist=[fn])
        return getattr(mod, fn)(a)
    return run


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="print JSON")
    common.add_argument("--no-scan", action="store_true", help="use the database as it is; read no new logs")
    common.add_argument("--no-color", action="store_true", help="plain text")

    filt = argparse.ArgumentParser(add_help=False)
    filt.add_argument("-p", "--period", help=f"one of {periods.HELP}")
    filt.add_argument("--since", help="YYYY-MM-DD")
    filt.add_argument("--until", help="YYYY-MM-DD, inclusive")
    filt.add_argument("--project", help="project name; * matches anything")
    filt.add_argument("--model", help="model id; * matches anything")
    filt.add_argument("--client", help="cli, exec, ide, desktop, cloud")
    filt.add_argument("--machine", help="a machine added with `sources add`")
    filt.add_argument("--account", help="a codexhop account")

    p = argparse.ArgumentParser(prog=PROG, parents=[common, filt],
                                description="See where your OpenAI Codex tokens, dollars and plan limits go.")
    p.add_argument("-V", "--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="cmd")

    def add(name, fn, help_, parents=(common, filt), **kw):
        sp = sub.add_parser(name, parents=list(parents), help=help_, description=help_, **kw)
        sp.set_defaults(fn=fn)
        return sp

    for name, h in (("daily", "usage per day"), ("weekly", "usage per week"), ("monthly", "usage per month"),
                    ("projects", "usage per project"), ("models", "usage per model"),
                    ("clients", "usage per Codex surface: cli, exec, ide, desktop"),
                    ("machines", "usage per machine"), ("tiers", "usage per service tier (fast mode is priority)"),
                    ("efforts", "usage per reasoning effort"), ("agents", "main sessions against subagents"),
                    ("hours", "a weekday by hour heat map")):
        sp = add(name, cmd_report, h)
        sp.add_argument("--csv", action="store_true", help="print CSV")
        sp.add_argument("--limit", type=int)
    sp = add("sessions", cmd_sessions, "recent sessions with their cost, turns and compactions")
    sp.add_argument("-n", "--limit", type=int, default=20)
    sp = add("tools", cmd_tools, "which tools Codex called, and how often")
    sp.add_argument("-n", "--limit", type=int, default=25)
    add("latency", cmd_latency, "how long turns take, per model")
    sp = add("limits", cmd_limits, "5-hour and weekly plan limits, from the logs and codexhop")
    sp.add_argument("--refresh", action="store_true", help="ask codexhop for a fresh reading of every account")
    sp = add("quota", cmd_quota, "what each percent of your plan cost, and rises the logs cannot explain")
    sp.add_argument("--limit-id", default="codex", help="limit to analyse (default codex)")
    sp.add_argument("--window", choices=["5h", "week"], help="only this window length")
    sp.add_argument("-n", "--limit", type=int, default=15)
    sp = add("scan", cmd_scan, "read new log lines into the history database", parents=(common,))
    sp.add_argument("--full", action="store_true", help="re-read every log from the start")
    sp = add("export", cmd_export, "every response as CSV or JSON")
    sp.add_argument("--format", choices=["csv", "json"], default="csv")
    sp = add("prices", cmd_prices, "show or update the price list", parents=(common,))
    sp.add_argument("action", nargs="?", choices=["list", "update", "path"], default="list")
    sp = add("sources", cmd_sources, "read logs from other machines too", parents=(common,))
    sp.add_argument("action", nargs="?", choices=["list", "add", "rm"], default="list")
    sp.add_argument("path", nargs="?")
    sp.add_argument("--machine", help="a name for that machine")
    sp = add("config", cmd_config, "read or change settings", parents=(common,))
    sp.add_argument("action", nargs="?", choices=["get", "set", "path"], default="get")
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")
    add("doctor", cmd_doctor, "check the setup", parents=(common,))

    sp = add("live", _lazy("live", "cmd_live"), "a live view that refreshes in place")
    sp.add_argument("-i", "--interval", type=float, default=5.0)
    sp = add("dashboard", _lazy("server", "cmd_dashboard"), "open the web dashboard", parents=(common,))
    sp.add_argument("--port", type=int, default=7878)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--no-open", action="store_true", help="do not open a browser")
    sp = add("bar", _lazy("bar", "cmd_bar"), "one line or menu for status bars and prompts", parents=(common,))
    sp.add_argument("-f", "--format", default="plain",
                    choices=["plain", "prompt", "tmux", "swiftbar", "xbar", "waybar", "polybar", "i3blocks", "json"])
    add("tray", _lazy("tray", "cmd_tray"), "a tray icon with your limits (Linux, GTK)", parents=(common,))
    sp = add("card", _lazy("card", "cmd_card"), "a share card of your usage, as SVG")
    sp.add_argument("-o", "--out", help="file to write (default codexwatch-card.svg)")
    sp.add_argument("--projects", action="store_true", help="name your top projects on the card")
    sp.add_argument("--plan-price", type=float, help="your plan's monthly USD")
    sp.add_argument("--theme", choices=["dark", "light"], default="dark")
    sp.add_argument("--open", action="store_true", help="open the card when it is written")
    sp = add("budget", _lazy("budgets", "cmd_budget"), "spending caps with desktop alerts", parents=(common,))
    sp.add_argument("action", nargs="?", choices=["list", "add", "rm", "check"], default="list")
    sp.add_argument("--name")
    sp.add_argument("--usd", type=float)
    sp.add_argument("--tokens", type=float)
    sp.add_argument("--pct", type=float, help="a plan window level, with --window")
    sp.add_argument("--window", choices=["5h", "week"], default="5h")
    sp.add_argument("--every", dest="every", choices=["day", "week", "month"], default="month")
    sp.add_argument("--scope", help="project:NAME or model:ID")
    sp.add_argument("--notify-pct", type=float, default=80)
    sp.add_argument("--id", type=int)
    sp = add("autohop", _lazy("autohop", "cmd_autohop"),
             "move to another of your own codexhop accounts when the live one is nearly spent (dry run unless --apply)", parents=(common,))
    sp.add_argument("--apply", action="store_true", help="really hop; without it, only say what would happen")
    sp.add_argument("--watch", action="store_true", help="keep checking")
    sp.add_argument("-i", "--interval", type=float, default=180)
    add("mcp", _lazy("mcp", "cmd_mcp"), "run as an MCP server on stdin and stdout", parents=(common,))
    sp = add("demo", _lazy("demo", "cmd_demo"), "try codexwatch on invented logs (never your own)", parents=(common,))
    sp.add_argument("--terminal", action="store_true", help="show the terminal overview instead of the dashboard")
    sp.add_argument("--fresh", action="store_true", help="write new demo logs")
    sp.add_argument("--days", type=int, default=60)
    sp.add_argument("--port", type=int, default=7879)
    sp.add_argument("--no-open", action="store_true")
    sp = add("notify", _lazy("notify", "cmd_notify"), "send due limit and budget alerts once", parents=(common,))
    add("reprice", lambda a: (print(f"re-priced {open_store().reprice():,} responses"), 0)[1],
        "recompute every cost after a price change", parents=(common,))
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["help"]:
        argv = ["--help"]
    p = build_parser()
    a = p.parse_args(argv)
    if a.no_color:
        fmt.set_color(False)
    fn = getattr(a, "fn", None) or cmd_overview
    try:
        return fn(a) or 0
    except ValueError as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


def console_main() -> int:
    return main()
