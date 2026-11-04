# Changelog

## 1.0.0

The first release.

- Reads Codex rollout logs into a local history database, counting each model response once by its response id, so forks, subagents, repeated events and synced copies never add tokens twice. Logs from before per-response records are read from their running totals.
- API-equivalent cost from OpenAI's published prices as of 2026-11-04, with long-context and Fast-tier pricing; models with no price are reported as unpriced rather than $0. Optional LiteLLM price list and your own overrides.
- Plan limits from the readings in the logs: both windows, burn rate, when a window fills, and usage since its reset, with no login or network call.
- Quota forensics: dollars of usage per percent of each window and day, and rises the local logs cannot explain.
- Reports per day, week, month, project, model, session, tool, client, tier, effort, machine and subagent; turn latency per model; a weekday-by-hour map; CSV and JSON export.
- A web dashboard on 127.0.0.1, light and dark, phone to desktop. A second launch opens the one already running.
- On Linux, an app-menu entry with its own icon and an optional desktop shortcut (`install.sh --desktop`).
- A share card (SVG and PNG) that leads with what your plan returned and leaves project names off by default.
- Status-bar output for SwiftBar, xbar, waybar, polybar, i3blocks, tmux and shell prompts; a Linux tray; desktop notifications.
- An MCP server for Codex and Claude Code.
- Budgets in dollars, tokens or plan percent, with alerts.
- codexhop support: every account's reading, the account behind each window, and an opt-in autohop that is a dry run unless asked.
- Logs from several machines in one history.
- `codexwatch demo`, on invented data.
