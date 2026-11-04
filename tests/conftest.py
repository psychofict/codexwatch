import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated Codex home, codexwatch folder and codexhop folder."""
    from codexwatch import store

    home = tmp_path / "codex"
    (home / "sessions").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEXWATCH_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CODEXHOP_DIR", str(tmp_path / "codexhop"))
    monkeypatch.setenv("CODEXWATCH_NOTIFY_DRYRUN", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(store, "_shared", {})
    return home
