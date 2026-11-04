"""The history database: every Codex response, kept after Codex deletes its logs.

One SQLite file. Rows are upserts keyed by something Codex itself made unique
(the response id, the turn id, a line's ordinal), so scanning the same log
twice, or a copy of it from another machine, never counts anything twice, and
a parser or price fix repairs old rows on the next scan.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from . import parser, paths, pricing

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY, machine TEXT, size INTEGER, mtime REAL,
  offset INTEGER, state TEXT, scanned_at REAL);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, machine TEXT, file TEXT, started REAL, last_ts REAL,
  cwd TEXT, project TEXT, repo TEXT, branch TEXT, client TEXT, originator TEXT,
  cli_version TEXT, provider TEXT, parent_id TEXT, forked_from TEXT, nickname TEXT,
  subagent INTEGER DEFAULT 0, title TEXT);
CREATE TABLE IF NOT EXISTS usage (
  response_id TEXT PRIMARY KEY, ts REAL, session_id TEXT, turn_id TEXT, machine TEXT,
  model TEXT, effort TEXT, tier TEXT, project TEXT, client TEXT, subagent INTEGER,
  input INTEGER, cached INTEGER, cache_write INTEGER, output INTEGER, reasoning INTEGER, total INTEGER,
  cost REAL, plan_cost REAL, long_ctx INTEGER, how TEXT);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_session ON usage(session_id);
CREATE TABLE IF NOT EXISTS limits (
  ts REAL, session_id TEXT, response_id TEXT, machine TEXT, limit_id TEXT, limit_name TEXT, plan TEXT,
  slot TEXT, window_minutes INTEGER, used_pct REAL, resets_at REAL, reached TEXT, credits TEXT,
  win_reset REAL,
  PRIMARY KEY (ts, limit_id, slot, used_pct));
CREATE INDEX IF NOT EXISTS limits_window ON limits(limit_id, window_minutes, win_reset);
CREATE INDEX IF NOT EXISTS limits_response ON limits(response_id);
CREATE TABLE IF NOT EXISTS turns (
  turn_id TEXT PRIMARY KEY, session_id TEXT, model TEXT, started REAL, completed REAL,
  duration_ms INTEGER, ttft_ms INTEGER, aborted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tools (key TEXT PRIMARY KEY, ts REAL, session_id TEXT, name TEXT);
CREATE INDEX IF NOT EXISTS tools_ts ON tools(ts);
CREATE TABLE IF NOT EXISTS compactions (key TEXT PRIMARY KEY, ts REAL, session_id TEXT);
CREATE TABLE IF NOT EXISTS windows (
  limit_id TEXT, window_minutes INTEGER, resets_at REAL, account TEXT, plan TEXT, limit_name TEXT,
  first_ts REAL, last_ts REAL, max_pct REAL,
  PRIMARY KEY (limit_id, window_minutes, resets_at));
CREATE TABLE IF NOT EXISTS budgets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT, amount REAL, period TEXT,
  scope TEXT, notify_pct REAL DEFAULT 80, created REAL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

WINDOW_MATCH_S = 120

USAGE_COLS = ("response_id", "ts", "session_id", "turn_id", "machine", "model", "effort", "tier",
              "project", "client", "subagent", "input", "cached", "cache_write", "output", "reasoning",
              "total", "cost", "plan_cost", "long_ctx", "how")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or paths.db_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._anchors: dict = {}
        with self.lock:
            if self.path != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------------ helpers

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def meta(self, key: str, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value)))
            self.db.commit()

    # --------------------------------------------------------------------- scan

    def scan(self, sources: list[dict] | None = None, progress=None) -> dict:
        """Read new lines from every rollout in every source. Returns counts."""
        from . import config

        sources = sources or config.sources()
        card = pricing.load()
        stats = {"files": 0, "changed": 0, "usage": 0, "limits": 0, "seconds": 0.0}
        t0 = time.time()
        known = {r["path"]: r for r in self.q("SELECT path,size,mtime,offset,state FROM files")}
        for src in sources:
            machine = src.get("machine") or paths.machine_name()
            for folder in paths.session_dirs(src["path"]):
                for root, _dirs, files in os.walk(folder):
                    for name in files:
                        if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                            continue
                        path = os.path.join(root, name)
                        stats["files"] += 1
                        try:
                            st = os.stat(path)
                        except OSError:
                            continue
                        prev = known.get(path)
                        if prev and prev["size"] == st.st_size and prev["mtime"] == st.st_mtime:
                            continue
                        offset, state = 0, None
                        if prev and st.st_size >= (prev["offset"] or 0) and prev["state"]:
                            offset, state = prev["offset"], json.loads(prev["state"])
                        try:
                            batch, new_off, new_state = parser.scan(path, offset, state)
                        except OSError:
                            continue
                        stats["changed"] += 1
                        n = self._write(batch, path, machine, card, st, new_off, new_state)
                        stats["usage"] += n[0]
                        stats["limits"] += n[1]
                        if progress:
                            progress(stats)
            self._titles(src["path"])
        self._attribute_accounts()
        stats["seconds"] = round(time.time() - t0, 2)
        self.set_meta("last_scan", time.time())
        return stats

    def _write(self, b: parser.Batch, path, machine, card, st, offset, state) -> tuple[int, int]:
        with self.lock:
            cur = self.db.cursor()
            if b.session:
                s = dict(b.session, machine=machine, file=path, last_ts=b.last_ts)
                cols = list(s)
                cur.execute(
                    f"INSERT INTO sessions({','.join(cols)}) VALUES({','.join('?' * len(cols))}) "
                    f"ON CONFLICT(session_id) DO UPDATE SET "
                    + ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("session_id", "title")),
                    [s[c] for c in cols])
            elif b.last_ts and state.get("sid"):
                cur.execute("UPDATE sessions SET last_ts=MAX(COALESCE(last_ts,0),?) WHERE session_id=?",
                            (b.last_ts, state["sid"]))
            for u in b.usage:
                row = dict(u, machine=machine)
                row["cost"] = card.cost(u["model"], u["input"], u["cached"], u["cache_write"], u["output"], u["tier"])
                std = card.cost(u["model"], u["input"], u["cached"], u["cache_write"], u["output"], None)
                row["plan_cost"] = None if std is None else std * card.plan_factor(u["tier"])
                row["long_ctx"] = 1 if card.is_long(u["input"]) else 0
                cur.execute(
                    f"INSERT INTO usage({','.join(USAGE_COLS)}) VALUES({','.join('?' * len(USAGE_COLS))}) "
                    "ON CONFLICT(response_id) DO UPDATE SET "
                    + ",".join(f"{c}=excluded.{c}" for c in USAGE_COLS if c not in ("response_id", "machine")),
                    [row.get(c) for c in USAGE_COLS])
            for r in b.limits:
                anchor = self._anchor(cur, r)
                cur.execute(
                    "INSERT OR IGNORE INTO limits(ts,session_id,response_id,machine,limit_id,limit_name,plan,slot,"
                    "window_minutes,used_pct,resets_at,reached,credits,win_reset) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["ts"], r["session_id"], r["response_id"], machine, r["limit_id"], r["limit_name"], r["plan"],
                     r["slot"], r["window_minutes"], r["used_pct"], r["resets_at"], r["reached"], r["credits"], anchor))
            for t in b.turns:
                cur.execute(
                    "INSERT INTO turns(turn_id,session_id,model,started,completed,duration_ms,ttft_ms,aborted) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(turn_id) DO UPDATE SET "
                    "model=COALESCE(excluded.model,model), started=COALESCE(started,excluded.started), "
                    "completed=COALESCE(excluded.completed,completed), "
                    "duration_ms=COALESCE(excluded.duration_ms,duration_ms), ttft_ms=COALESCE(excluded.ttft_ms,ttft_ms), "
                    "aborted=MAX(aborted,excluded.aborted)",
                    (t["turn_id"], t["session_id"], t["model"], t["started"], t["completed"], t["duration_ms"],
                     t["ttft_ms"], t["aborted"]))
            cur.executemany("INSERT OR IGNORE INTO tools(key,ts,session_id,name) VALUES(?,?,?,?)",
                            [(t["key"], t["ts"], t["session_id"], t["name"]) for t in b.tools])
            cur.executemany("INSERT OR IGNORE INTO compactions(key,ts,session_id) VALUES(?,?,?)",
                            [(c["key"], c["ts"], c["session_id"]) for c in b.compactions])
            cur.execute(
                "INSERT INTO files(path,machine,size,mtime,offset,state,scanned_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, offset=excluded.offset, "
                "state=excluded.state, scanned_at=excluded.scanned_at",
                (path, machine, st.st_size, st.st_mtime, offset, json.dumps(state), time.time()))
            self.db.commit()
        return len(b.usage), len(b.limits)

    def _anchor(self, cur, r: dict) -> float | None:
        """The window a reading belongs to, named by its first reset time.

        The server's reset time drifts by a second or two between readings, so
        readings whose reset is within two minutes of a known window's are that
        window. A window that has not started (0% and a reset one full window
        from now) belongs to no window: its reset moves with every reading.
        """
        reset, mins = r["resets_at"], r["window_minutes"]
        if not reset or not r["ts"]:
            return None
        if r["used_pct"] <= 0 and mins and abs(reset - (r["ts"] + mins * 60)) < WINDOW_MATCH_S:
            return None
        key = (r["limit_id"], mins)
        anchors = self._anchors.get(key)
        if anchors is None:
            anchors = [x[0] for x in cur.execute(
                "SELECT resets_at FROM windows WHERE limit_id=? AND window_minutes IS ?", key)]
            self._anchors[key] = anchors
        hit = next((a for a in anchors if abs(a - reset) <= WINDOW_MATCH_S), None)
        if hit is None:
            hit = reset
            anchors.append(hit)
            cur.execute("INSERT OR IGNORE INTO windows(limit_id,window_minutes,resets_at) VALUES(?,?,?)",
                        (r["limit_id"], mins, hit))
        cur.execute(
            "UPDATE windows SET plan=COALESCE(?,plan), limit_name=COALESCE(?,limit_name), "
            "first_ts=MIN(COALESCE(first_ts,?),?), last_ts=MAX(COALESCE(last_ts,0),?), "
            "max_pct=MAX(COALESCE(max_pct,0),?) WHERE limit_id=? AND window_minutes IS ? AND resets_at=?",
            (r["plan"], r["limit_name"], r["ts"], r["ts"], r["ts"], r["used_pct"], r["limit_id"], mins, hit))
        return hit

    def _titles(self, home: str) -> None:
        """Session names from Codex's own index (the last name given wins)."""
        idx = os.path.join(os.path.expanduser(home), "session_index.jsonl")
        try:
            with open(idx, encoding="utf-8") as f:
                names = {}
                for line in f:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if o.get("id") and isinstance(o.get("thread_name"), str):
                        names[o["id"]] = o["thread_name"][:200]
        except OSError:
            return
        with self.lock:
            self.db.executemany("UPDATE sessions SET title=? WHERE session_id=?", [(v, k) for k, v in names.items()])
            self.db.commit()

    def _attribute_accounts(self) -> None:
        """Name the codexhop account behind each limit window we can identify."""
        from . import hop

        try:
            hop.attribute_windows(self)
        except Exception:  # noqa: BLE001 - attribution is a nicety; never fail a scan for it
            pass

    def reprice(self) -> int:
        card = pricing.load()
        rows = self.q("SELECT response_id, model, input, cached, cache_write, output, tier FROM usage")
        with self.lock:
            for r in rows:
                cost = card.cost(r["model"], r["input"], r["cached"], r["cache_write"], r["output"], r["tier"])
                std = card.cost(r["model"], r["input"], r["cached"], r["cache_write"], r["output"], None)
                self.db.execute("UPDATE usage SET cost=?, plan_cost=?, long_ctx=? WHERE response_id=?",
                                (cost, None if std is None else std * card.plan_factor(r["tier"]),
                                 1 if card.is_long(r["input"]) else 0, r["response_id"]))
            self.db.commit()
        return len(rows)

    def forget_files(self) -> None:
        """Make the next scan re-read every log from the start."""
        with self.lock:
            self.db.execute("DELETE FROM files")
            self.db.commit()


_shared: dict[str, Store] = {}


def open_store(path: str | None = None) -> Store:
    """One Store per database file for the whole process."""
    path = path or paths.db_path()
    if path not in _shared:
        _shared[path] = Store(path)
    return _shared[path]
