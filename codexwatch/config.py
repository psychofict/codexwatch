"""Settings, kept in config.json in the codexwatch folder.

Every key has a default, so the file only holds what you changed:

  sources            where to read logs: [{"path": "~/.codex", "machine": "laptop"}]
  plan_price         your plan's monthly USD, when Codex's plan name has no
                     published price (Enterprise, Edu) or you pay another price
  warn_pct           a window this full turns amber (default 70)
  alert_pct          a window this full turns red (default 90)
  notify             desktop notifications on or off (default on)
  notify_steps       window levels that send a notification (default 75, 90, 100)
  autohop            settings for `codexwatch autohop` (see autohop.py)
  refresh_seconds    how often the tray, dashboard and live view re-read logs
"""

from __future__ import annotations

import json
import os

from . import paths

DEFAULTS = {
    "sources": None,
    "plan_price": None,
    "warn_pct": 70,
    "alert_pct": 90,
    "notify": True,
    "notify_steps": [75, 90, 100],
    "refresh_seconds": 10,
    "autohop": {},
}


def load() -> dict:
    try:
        with open(paths.config_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    return {**DEFAULTS, **(doc if isinstance(doc, dict) else {})}


def save(doc: dict) -> None:
    paths.ensure_data_dir()
    keep = {k: v for k, v in doc.items() if DEFAULTS.get(k, object()) != v}
    tmp = paths.config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=2)
    os.replace(tmp, paths.config_path())


def get(key: str):
    return load().get(key)


def set_value(key: str, value) -> None:
    doc = load()
    doc[key] = value
    save(doc)


def sources() -> list[dict]:
    """Where to read logs. The local Codex home is always first."""
    extra = load().get("sources") or []
    local = {"path": paths.codex_home(), "machine": paths.machine_name()}
    out = [local]
    seen = {os.path.realpath(os.path.expanduser(local["path"]))}
    for s in extra:
        if not isinstance(s, dict) or not s.get("path"):
            continue
        real = os.path.realpath(os.path.expanduser(s["path"]))
        if real in seen:
            continue
        seen.add(real)
        out.append({"path": s["path"], "machine": s.get("machine") or os.path.basename(real)})
    return out


def add_source(path: str, machine: str) -> None:
    doc = load()
    srcs = [s for s in (doc.get("sources") or []) if isinstance(s, dict)]
    real = os.path.realpath(os.path.expanduser(path))
    srcs = [s for s in srcs if os.path.realpath(os.path.expanduser(s.get("path", ""))) != real]
    srcs.append({"path": path, "machine": machine})
    doc["sources"] = srcs
    save(doc)


def remove_source(path_or_machine: str) -> bool:
    doc = load()
    srcs = doc.get("sources") or []
    real = os.path.realpath(os.path.expanduser(path_or_machine))
    keep = [s for s in srcs if s.get("machine") != path_or_machine
            and os.path.realpath(os.path.expanduser(s.get("path", ""))) != real]
    doc["sources"] = keep
    save(doc)
    return len(keep) != len(srcs)
