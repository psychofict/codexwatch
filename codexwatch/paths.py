"""Where codexwatch reads from and writes to.

codexwatch reads Codex's session logs and never writes anywhere under a Codex
home. Its own files (the history database, settings, price overrides) live in
one folder per user:

  Linux    $XDG_CONFIG_HOME/codexwatch, normally ~/.config/codexwatch
  macOS    ~/Library/Application Support/codexwatch
  Windows  %APPDATA%\\codexwatch

CODEXWATCH_DIR overrides the folder, which the tests use.
"""

from __future__ import annotations

import os
import socket
import sys

HOME = os.path.expanduser("~")


def codex_home() -> str:
    """The Codex home of this user: $CODEX_HOME, else ~/.codex."""
    return os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")


def data_dir() -> str:
    env = os.environ.get("CODEXWATCH_DIR")
    if env:
        return os.path.expanduser(env)
    if sys.platform == "darwin":
        return os.path.join(HOME, "Library", "Application Support", "codexwatch")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or HOME, "codexwatch")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "codexwatch")


def ensure_data_dir() -> str:
    d = data_dir()
    os.makedirs(d, exist_ok=True)
    return d


def db_path() -> str:
    return os.path.join(data_dir(), "history.db")


def config_path() -> str:
    return os.path.join(data_dir(), "config.json")


def prices_override_path() -> str:
    return os.path.join(data_dir(), "prices.json")


def litellm_prices_path() -> str:
    return os.path.join(data_dir(), "litellm-prices.json")


def state_path(name: str) -> str:
    return os.path.join(data_dir(), name)


def session_dirs(home: str) -> list[str]:
    """The folders under a Codex home that hold rollout logs.

    `home` may also be a sessions folder itself, or any folder of copied logs,
    so `codexwatch sources add` accepts whatever another machine hands over.
    """
    home = os.path.expanduser(home)
    found = [os.path.join(home, d) for d in ("sessions", "archived_sessions")
             if os.path.isdir(os.path.join(home, d))]
    return found or ([home] if os.path.isdir(home) else [])


def codexhop_store(home: str | None = None) -> str:
    return os.environ.get("CODEXHOP_DIR") or os.path.join(home or codex_home(), "codexhop")


def machine_name() -> str:
    try:
        return socket.gethostname().split(".")[0] or "this-machine"
    except OSError:
        return "this-machine"
