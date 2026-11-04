import time
import xml.dom.minidom

from codexwatch import card
from codexwatch.demo import PROJECTS, generate
from codexwatch.store import open_store


def test_card_is_valid_svg_and_keeps_project_names_off(env):
    generate(str(env), days=20, now=time.time())
    s = open_store()
    s.scan()
    svg = card.render(s)
    doc = xml.dom.minidom.parseString(svg)
    root = doc.documentElement
    assert root.getAttribute("width") == "1200" and root.getAttribute("height") == "630"
    assert not any(p in svg for p in PROJECTS)
    assert "×" in svg  # Pro 200 has a published price, so the card leads with plan value


def test_projects_only_when_asked(env):
    generate(str(env), days=20, now=time.time())
    s = open_store()
    s.scan()
    assert any(p in card.render(s, show_projects=True) for p in PROJECTS)


def test_light_card_and_no_plan_price(env, monkeypatch):
    generate(str(env), days=5, now=time.time())
    s = open_store()
    s.scan()
    monkeypatch.setattr(card.snapshot, "plan_info", lambda plan: {"type": plan, "name": None, "usd": None})
    svg = card.render(s, theme="light")
    xml.dom.minidom.parseString(svg)
    assert "of API-equivalent usage" in svg and 'fill="#ffffff"' in svg


def test_text_is_escaped(env, monkeypatch):
    generate(str(env), days=3, now=time.time())
    s = open_store()
    s.scan()
    s.db.execute("UPDATE usage SET project='<script>&'")
    s.db.commit()
    xml.dom.minidom.parseString(card.render(s, show_projects=True))
