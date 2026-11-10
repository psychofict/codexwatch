import json
import os
import time

from codexwatch import autohop, bar, hop, snapshot, tray

NOW = 1_800_000_000.0

AZURE = (
    '# a profile\nmodel_provider = "azure"\nmodel = "gpt-6-astra"\n\n[model_providers.azure]\n'
    'name = "Azure OpenAI"\nbase_url = "https://user:secret@demo.openai.azure.com/openai/v1?key=abc"\n'
    'env_key = "AZURE_OPENAI_API_KEY"\n'
)


def store(env, accounts=("home",), provider=None, active="home"):
    """A codexhop folder next to the Codex home `env` made, with the named accounts."""
    d = os.environ["CODEXHOP_DIR"]
    os.makedirs(os.path.join(d, "accounts"), exist_ok=True)
    for n in accounts:
        with open(os.path.join(d, "accounts", f"{n}.json"), "w", encoding="utf-8") as f:
            f.write("{}")
    if active:
        with open(os.path.join(d, "active"), "w", encoding="utf-8") as f:
            f.write(active + "\n")
    if provider:
        with open(os.path.join(d, "provider"), "w", encoding="utf-8") as f:
            f.write(provider + "\n")
    cache = {n: {"usage": {"plan": "plus", "short": {"pct": 20.0, "seconds": 18000, "resetsAt": time.time() + 3600},
                           "long": {"pct": 10.0, "seconds": 604800, "resetsAt": time.time() + 86400}},
                 "fetchedAt": time.time() - 60} for n in accounts}
    with open(os.path.join(d, "usage.json"), "w", encoding="utf-8") as f:
        json.dump(cache, f)


def profile(env, name, text):
    with open(os.path.join(str(env), f"{name}.config.toml"), "w", encoding="utf-8") as f:
        f.write(text)


def test_providers_are_profiles_that_leave_openai(env):
    profile(env, "azure", AZURE)
    profile(env, "plain", 'model_provider = "openai"\nmodel = "gpt-5"\n')
    profile(env, "no-provider", 'model = "gpt-5"\n')
    with open(os.path.join(str(env), "config.toml"), "w", encoding="utf-8") as f:
        f.write('model_provider = "azure"\n')  # Codex's own config is not a profile
    assert hop.providers() == [{"name": "azure", "provider": "azure", "model": "gpt-6-astra",
                                "host": "demo.openai.azure.com"}]


def test_a_provider_keeps_only_its_host(env):
    profile(env, "azure", AZURE)
    text = json.dumps(hop.providers())
    assert "secret" not in text and "abc" not in text and "AZURE_OPENAI_API_KEY" not in text


def test_the_provider_pointer_makes_the_provider_live(env):
    profile(env, "azure", AZURE)
    store(env, provider="azure")
    rows = hop.accounts_view(NOW)
    assert [(r["name"], r["kind"], r["active"]) for r in rows] == [
        ("azure", "provider", True), ("home", "account", False)]
    assert rows[0]["short"] is None and rows[0]["model"] == "gpt-6-astra"
    assert hop.live_phrase(rows[0]) == "live on azure (provider)"
    assert hop.live_phrase(rows[1]) == "live account home"


def test_without_a_pointer_the_account_is_live(env):
    profile(env, "azure", AZURE)
    store(env)
    rows = hop.accounts_view(NOW)
    assert [(r["name"], r["active"]) for r in rows] == [("home", True), ("azure", False)]


def test_a_pointer_to_a_missing_profile_is_ignored(env):
    store(env, provider="gone")
    rows = hop.accounts_view(NOW)
    assert [(r["name"], r["active"]) for r in rows] == [("home", True)]


def test_providers_need_codexhop(env, monkeypatch):
    profile(env, "azure", AZURE)  # a Codex profile alone is not a codexhop provider
    monkeypatch.setattr(hop.shutil, "which", lambda *_: None)
    assert hop.accounts_view(NOW) == []


def test_a_provider_is_never_the_account_with_room(env):
    profile(env, "azure", AZURE)
    store(env, accounts=("home", "spare"), active="home")
    rows = hop.accounts_view(NOW)
    assert hop.best_next(rows)["name"] == "spare"
    only = [r for r in rows if r["name"] != "spare"]
    assert hop.best_next(only) is None  # a provider has no window, which must not read as 0% used


def test_autohop_leaves_a_live_provider_alone(env):
    profile(env, "azure", AZURE)
    store(env, accounts=("home", "spare"), provider="azure")
    d = autohop.decide(hop.accounts_view(NOW), NOW)
    assert d["action"] == "stay" and d["live"] == "azure" and "provider" in d["why"]


def test_autohop_never_chooses_a_provider(env):
    profile(env, "azure", AZURE)
    store(env, accounts=("home",), active="home")
    rows = hop.accounts_view(NOW)
    next(r for r in rows if r["name"] == "home")["short"] = {"pct": 99.0, "resetsAt": NOW + 3600}
    d = autohop.decide(rows, NOW)
    assert d["target"] is None and d["candidates"] == []
    assert d["action"] == "stay" and "fewer than two" in d["why"]


def test_the_logs_reading_is_not_put_on_a_provider(env):
    profile(env, "azure", AZURE)
    store(env, provider="azure")
    rows = hop.accounts_view(NOW)
    head = {"read_at": time.time(), "short": {"pct": 99.0, "resets_at": NOW, "minutes": 300}, "plan": "plus"}
    snapshot.overlay_live(rows, head)
    assert rows[0]["name"] == "azure" and rows[0]["short"] is None


def provider_state(env):
    profile(env, "azure", AZURE)
    store(env, provider="azure")
    return {"accounts": hop.accounts_view(), "live_account": "azure", "live_kind": "provider",
            "plan": {"name": "Plus"}, "periods": {"today": {"cost": 4.2, "total": 1000}},
            "headline": {"short": {"pct": 99.0, "label": "5h", "resets_at": None}, "long": None,
                         "read_at": time.time()}}


def test_surfaces_name_a_provider_as_one(env):
    st = provider_state(env)
    cells = bar.account_cells(st)
    assert cells[0]["kind"] == "provider" and cells[0]["note"] == "gpt-6-astra · demo.openai.azure.com"
    lines = tray.menu_text(st)["accounts"]
    assert lines[0] == "● azure: provider · gpt-6-astra · demo.openai.azure.com"
    assert lines[1].startswith("○ home: 5h ")
    assert "live on azure (provider)" in bar.swiftbar(st)


def test_on_a_provider_the_tray_shows_money_not_the_last_plan_reading(env):
    st = provider_state(env)
    t = tray.menu_text(st)
    assert t["label"] == "$4.20" and t["icon_pct"] is None and t["level"] == "none"
    assert t["title"] == "azure (provider)"
    assert t["windows"] == ["On azure: no plan limits"]
    assert "99" not in t["label"] + t["title"] + "".join(t["windows"]) + t["tooltip"]
    assert "Plus" not in t["title"] + t["tooltip"]


def test_on_a_provider_the_bars_show_the_name_and_today_not_the_plan_windows(env):
    st = provider_state(env)
    assert bar.line(st) == "azure · $4.20 today"
    assert "99%" not in bar.tmux(st) + bar.polybar(st) + bar.swiftbar(st) + bar.waybar(st)
    doc = json.loads(bar.headline_json(st))
    assert doc["short"] is None and doc["live_kind"] == "provider" and doc["plan"] is None
    # with no usage today it still says where you are, not "no readings"
    st["periods"] = {}
    assert bar.line(st) == "azure · $0.00 today"


def test_on_a_chatgpt_account_nothing_changes(env):
    store(env)
    st = {"accounts": hop.accounts_view(), "live_account": "home", "live_kind": "account", "plan": {"name": "Plus"},
          "periods": {"today": {"cost": 4.2, "total": 1000}},
          "headline": {"short": {"pct": 99.0, "label": "5h", "resets_at": None}, "long": None, "read_at": None}}
    assert tray.menu_text(st)["label"] == "99%"
    assert bar.line(st) == "5h 99% · $4.20 today"
