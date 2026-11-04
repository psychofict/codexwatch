## What this changes

<!-- One or two sentences: what a user will see differently, and why. -->

## How it was checked

- [ ] `python -m pytest -q` passes
- [ ] `ruff check codexwatch tests` is clean
- [ ] A new or changed behaviour has a test (log-format changes in `tests/test_parser.py`)
- [ ] `README.md`, `docs/how-it-works.md` and `CHANGELOG.md` are updated if a user can see the change
- [ ] A dashboard change was looked at in light and dark, at phone and desktop width, using `codexwatch demo`

If this changes a price, link the OpenAI page it came from. If it says something new about how
Codex writes its logs, name the Codex version you saw it in.

Never paste an `auth.json`, a token or a real session log into a pull request; screenshots come from `codexwatch demo`.
