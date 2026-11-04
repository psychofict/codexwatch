# Security

## What codexwatch can reach

codexwatch reads Codex's session logs (`~/.codex/sessions` and `archived_sessions`, or the folders you add with `codexwatch sources add`) and `session_index.jsonl` for session names. It writes only to its own folder: the history database, settings, price overrides and alert state.

It never opens `auth.json`, codexhop's account files, or any other file that holds a login or a key. When codexhop is installed it reads three files that hold no secrets (the `active` pointer, the `usage.json` cache of readings, and the names of the files in `accounts/`), and it runs the `codexhop` command for the things that need a login: a fresh reading of every account, and a hop.

## Network

codexwatch makes no network request of its own except `codexwatch prices update`, which downloads LiteLLM's public price list from `raw.githubusercontent.com`. `codexwatch limits --refresh`, the dashboard's "Read every account" button and `codexwatch autohop` run codexhop, which talks to OpenAI with your logins as its own documentation describes.

## The dashboard

`codexwatch dashboard` listens on `127.0.0.1` and refuses any other address. Each start makes a random token and puts it in the page it serves. Every data request, and both actions (reading accounts and hopping), must carry that token, and every request must name `127.0.0.1` or `localhost` as its host. A web page on another site cannot read the token, and a DNS-rebinding page fails the host check. The page's Content Security Policy allows only its own scripts, styles, fonts and connections, and no framing. Session titles and project names are inserted as text, never as HTML.

The demo (`codexwatch demo`) refuses both actions.

## The share card

The card shows no project or session names unless you ask for them with `--projects` or the tick box. It is written to a file you choose; nothing is uploaded.

## Reporting a problem

Please report anything that could expose a log, a reading or a login privately, through GitHub's "Report a vulnerability" on this repository, rather than in a public issue. You will get a reply within a week.
