# Contributing

Thanks for helping. codexwatch (`codexwatch-cli` on PyPI) has no dependencies beyond the Python standard library, and should stay that way; `gi` for the Linux tray is the one optional import.

## Run it from a clone

```bash
git clone https://github.com/psychofict/codexwatch
cd codexwatch
python -m codexwatch            # your own logs
python -m codexwatch demo       # invented logs, kept apart from your history
python -m pip install pytest ruff
python -m pytest -q
ruff check codexwatch tests
```

Tests never touch your real Codex home or history: the `env` fixture in `tests/conftest.py` points `CODEX_HOME`, `CODEXWATCH_DIR` and `CODEXHOP_DIR` at temporary folders, and `CODEXWATCH_NOTIFY_DRYRUN` keeps notifications off your desktop. Build test logs with `codexwatch.demo.Rollout`, which writes lines in Codex's own format.

## When Codex changes its log format

Add a test in `tests/test_parser.py` with the new lines (strip anything personal), then teach `parser.py` to read them. A change in counting needs a line in [docs/how-it-works.md](docs/how-it-works.md) as well.

## Adding or fixing a price

Edit `codexwatch/data/prices.json`. Every price must come from an OpenAI page: put the page in `sources` if it is new, name it in the model's `source`, and update `as_of`. A model OpenAI does not price stays out of `models`; if Codex uses it, add it to `unpriced` with the reason.

## Style

Plain words in messages and docs, and comments that say why rather than what. Match the code around your change. Every number the tool shows must be computed from the logs or a cited source, never estimated without saying so.

## Pull requests

Keep each one to one change, include a test, and say in the description what you checked by hand (which views, which platform). Screenshots must use `codexwatch demo`, never someone's real usage.
