import json

from codexwatch import pricing
from codexwatch.pricing import RateCard

BUNDLED = json.load(open(pricing.DATA))


def card(**kw):
    return RateCard(BUNDLED, **kw)


def test_cached_and_written_tokens_are_part_of_input():
    c = card()
    # 200K input of which 120K cached and 20K written to cache; 10K output
    usd = c.cost("gpt-6-astra", 200_000, 120_000, 20_000, 10_000)
    assert abs(usd - (60_000 * 10 + 120_000 * 1 + 20_000 * 12.5 + 10_000 * 50) / 1e6) < 1e-9


def test_long_prompts_use_long_context_prices():
    c = card()
    short = c.cost("gpt-6-sol", 272_000, 0, 0, 0)
    long_ = c.cost("gpt-6-sol", 272_001, 0, 0, 0)
    assert abs(short - 272_000 * 2 / 1e6) < 1e-9
    assert abs(long_ - 272_001 * 4 / 1e6) < 1e-9


def test_fast_mode_costs_double_and_spends_limits_at_two_and_a_half():
    c = card()
    std = c.cost("gpt-6-astra", 1000, 0, 0, 100)
    assert abs(c.cost("gpt-6-astra", 1000, 0, 0, 100, "priority") - 2 * std) < 1e-12
    assert c.plan_factor("priority") == 2.5 and c.plan_factor("default") == 1.0
    assert abs(c.cost("gpt-5-mini", 1000, 0, 0, 0, "fast") - 1.8 * c.cost("gpt-5-mini", 1000, 0, 0, 0)) < 1e-12


def test_unknown_models_are_unpriced_not_free():
    c = card()
    assert c.cost("gpt-9-mystery", 1000, 0, 0, 10) is None
    assert c.cost(None, 1000, 0, 0, 10) is None
    assert c.cost("gpt-5.3-codex-spark", 1000, 0, 0, 10) is None


def test_lookup_handles_dates_providers_and_suffixes():
    c = card()
    assert c.lookup("gpt-5.1-codex-2025-11-13") is c.lookup("gpt-5.1-codex")
    assert c.lookup("openai/gpt-6-sol") is c.lookup("gpt-6-sol")
    assert c.lookup("gpt-6.1-sol-preview") is c.lookup("gpt-6.1-sol")
    assert c.lookup("GPT-6-ASTRA") is c.lookup("gpt-6-astra")
    assert c.lookup("gpt-6-astra-mini") is None  # a different model is never priced as its relative


def test_layers_override_beats_bundled_beats_litellm():
    litellm = {"gpt-6-astra": {"litellm_provider": "openai", "input_cost_per_token": 9e-6, "output_cost_per_token": 9e-5},
               "brand-new-model": {"litellm_provider": "openai", "input_cost_per_token": 1e-6,
                                   "output_cost_per_token": 2e-6, "cache_read_input_token_cost": 1e-7}}
    override = {"models": {"gpt-6-sol": {"input": 100}}}
    c = card(litellm=litellm, override=override)
    assert c.lookup("gpt-6-astra")["input"] == 10.0  # bundled, checked against OpenAI, wins over LiteLLM
    assert abs(c.lookup("brand-new-model")["cached"] - 0.1) < 1e-9  # LiteLLM fills a gap
    assert c.lookup("gpt-6-sol")["input"] == 100 and c.lookup("gpt-6-sol")["output"] == 10.0


def test_every_bundled_model_has_input_and_output():
    for name, row in BUNDLED["models"].items():
        assert row["input"] > 0 and row["output"] > 0, name
        assert row["source"] in BUNDLED["sources"], name


def test_published_plan_prices():
    c = card()
    assert c.plans["pro"]["usd"] == 200 and c.plans["prolite"]["usd"] == 100 and c.plans["plus"]["usd"] == 20
    assert c.plans["enterprise"]["usd"] is None
