"""What a request would have cost on the OpenAI API.

ChatGPT plans do not bill per token, so every dollar figure codexwatch shows is
an API-equivalent estimate: the same tokens priced at OpenAI's published API
rates. Three sources are layered, later ones winning:

  1. the bundled list (data/prices.json), copied from OpenAI's pricing pages
  2. LiteLLM's list, only if you ran `codexwatch prices update`
  3. your own prices.json in the codexwatch folder

A model none of them knows is *unpriced*: its cost is None and every report
says how many tokens went unpriced, rather than silently counting them as $0.

Token accounting follows the OpenAI usage object: input_tokens includes the
cached and cache-written tokens, and output_tokens includes reasoning tokens.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache

from . import paths

DATA = os.path.join(os.path.dirname(__file__), "data", "prices.json")
FIELDS = ("input", "cached", "cache_write", "output")
_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{8})$")
_STAGES = {"preview", "latest", "beta", "alpha", "exp", "experimental"}


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _from_litellm(doc: dict) -> dict:
    """LiteLLM prices per token, keyed by bare OpenAI model name."""
    out = {}
    for key, row in doc.items():
        if not isinstance(row, dict) or row.get("litellm_provider") != "openai":
            continue
        name = key.split("/", 1)[-1]
        if any(s in name for s in ("_priority", "_flex", "_batches", "_ultrafast", "_above_")):
            continue
        try:
            entry = {
                "input": float(row["input_cost_per_token"]) * 1e6,
                "output": float(row["output_cost_per_token"]) * 1e6,
                "source": "litellm",
            }
        except (KeyError, TypeError, ValueError):
            continue
        if row.get("cache_read_input_token_cost") is not None:
            entry["cached"] = float(row["cache_read_input_token_cost"]) * 1e6
        if row.get("cache_creation_input_token_cost") is not None:
            entry["cache_write"] = float(row["cache_creation_input_token_cost"]) * 1e6
        long_ = doc.get(f"{name}_above_272k_tokens") or doc.get(f"openai/{name}_above_272k_tokens")
        if isinstance(long_, dict) and long_.get("input_cost_per_token") is not None:
            entry["long"] = {
                "input": float(long_["input_cost_per_token"]) * 1e6,
                "output": float(long_.get("output_cost_per_token") or row["output_cost_per_token"]) * 1e6,
                "cached": float(long_.get("cache_read_input_token_cost") or 0) * 1e6 or None,
                "cache_write": float(long_.get("cache_creation_input_token_cost") or 0) * 1e6 or None,
            }
        out[name] = entry
    return out


class RateCard:
    def __init__(self, bundled: dict, litellm: dict | None = None, override: dict | None = None):
        self.as_of = bundled.get("as_of")
        self.threshold = int(bundled.get("long_context_threshold") or 272000)
        self.tiers = {k: float(v) for k, v in (bundled.get("tiers") or {}).items()}
        self.plan_weight = {k: float(v) for k, v in (bundled.get("plan_weight") or {}).items()
                            if not k.startswith("_")}
        self.unpriced = dict(bundled.get("unpriced") or {})
        self.plans = {k: v for k, v in (bundled.get("plans") or {}).items()
                      if not k.startswith("_") and isinstance(v, dict)}
        self.models: dict[str, dict] = {}
        for layer in (_from_litellm(litellm or {}), bundled.get("models") or {},
                      (override or {}).get("models") or {}):
            for name, row in layer.items():
                if isinstance(row, dict):
                    self.models[name.lower()] = {**self.models.get(name.lower(), {}), **row}
        if override:
            self.tiers.update({k: float(v) for k, v in (override.get("tiers") or {}).items()})

    def lookup(self, model: str | None) -> dict | None:
        """The price row for a model id, trying a date-less and a prefix match."""
        if not model:
            return None
        m = model.lower().split("/", 1)[-1]
        bare = _DATE_SUFFIX.sub("", m)
        if m in self.unpriced or bare in self.unpriced:
            return None
        if m in self.models:
            return self.models[m]
        if bare in self.models:
            return self.models[bare]
        # gpt-5.6-terra-preview is gpt-5.6-terra. Only a release-stage suffix is
        # stripped: gpt-5.3-codex-spark is a different model from gpt-5.3-codex,
        # and guessing its price would be worse than saying it has none.
        head, _, tail = bare.rpartition("-")
        if tail in _STAGES and head in self.models:
            return self.models[head]
        return None

    def tier_factor(self, row: dict, tier: str | None) -> float | None:
        t = (tier or "default").lower()
        per_model = row.get("tiers") or {}
        if t in per_model:
            return None if per_model[t] is None else float(per_model[t])
        return self.tiers.get(t, 1.0)

    def cost(self, model, input_tokens, cached, cache_write, output, tier=None) -> float | None:
        """USD for one request, or None when the model has no price."""
        row = self.lookup(model)
        if not row or row.get("input") is None or row.get("output") is None:
            return None
        rates = row
        if input_tokens > self.threshold and isinstance(row.get("long"), dict):
            rates = {**row, **{k: v for k, v in row["long"].items() if v is not None}}
        factor = self.tier_factor(row, tier)
        if factor is None:
            factor = 1.0  # no price for this tier; Standard is the honest floor
        cached = max(0, min(cached, input_tokens))
        cache_write = max(0, min(cache_write, input_tokens - cached))
        fresh = input_tokens - cached - cache_write
        r_in = float(rates["input"])
        r_cached = float(rates.get("cached") if rates.get("cached") is not None else r_in)
        r_write = float(rates.get("cache_write") if rates.get("cache_write") is not None else r_in)
        usd = (fresh * r_in + cached * r_cached + cache_write * r_write + output * float(rates["output"])) / 1e6
        return usd * factor

    def plan_factor(self, tier: str | None) -> float:
        """How many times faster than Standard this tier spends plan limits."""
        return self.plan_weight.get((tier or "default").lower(), 1.0)

    def is_long(self, input_tokens: int) -> bool:
        return input_tokens > self.threshold


@lru_cache(maxsize=4)
def _load_cached(files: tuple) -> RateCard:
    (data, _), (override, _), (litellm, _) = files
    return RateCard(_read(data), _read(litellm), _read(override))


def load() -> RateCard:
    """The rate card, re-read whenever one of its three files changes."""
    def mt(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0

    files = tuple((p, mt(p)) for p in (DATA, paths.prices_override_path(), paths.litellm_prices_path()))
    return _load_cached(files)


LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"


def update_from_litellm(url: str = LITELLM_URL, timeout: float = 20.0) -> int:
    """Download LiteLLM's price list. Returns how many OpenAI models it priced."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "codexwatch"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        doc = json.loads(resp.read().decode("utf-8"))
    n = len(_from_litellm(doc))
    if not n:
        raise ValueError("the downloaded list had no OpenAI prices in it")
    paths.ensure_data_dir()
    tmp = paths.litellm_prices_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, paths.litellm_prices_path())
    _load_cached.cache_clear()
    return n
