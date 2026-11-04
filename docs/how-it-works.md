# How codexwatch counts

This is for anyone who wants to check a number, or to change how one is made. Everything here can be confirmed in [`codexwatch/parser.py`](../codexwatch/parser.py), [`store.py`](../codexwatch/store.py), [`pricing.py`](../codexwatch/pricing.py) and [`limits.py`](../codexwatch/limits.py), and the tests named below reproduce each rule.

## What Codex writes

Every Codex session (CLI, IDE extension, `codex exec`, desktop app) appends one JSON object per line to a rollout file under `~/.codex/sessions/YYYY/MM/DD/`. Each line looks like this:

```json
{"timestamp": "2026-11-04T04:20:09.123Z", "ordinal": 12, "type": "<kind>", "payload": {"type": "<event>", "...": "..."}}
```

codexwatch uses these kinds of line:

| Line | What codexwatch takes from it |
|---|---|
| `session_meta` | The session id, folder, git repository and branch, client (`cli`, `vscode`, `exec`), Codex version, and for subagents the parent session and nickname |
| `turn_context` | The model and reasoning effort of each turn |
| `event_msg` / `thread_settings_applied` | The service tier; fast mode shows as `priority` |
| `token_usage_record` | One row per model response: its id and its input, cached, cache-written, output and reasoning tokens |
| `event_msg` / `token_count` | The plan's rate-limit reading after a response; in logs older than `token_usage_record`, running token totals |
| `event_msg` / `task_started`, `task_complete`, `turn_aborted` | Turn timing and time to first token |
| `response_item` / `custom_tool_call`, `function_call` | Tool calls, by name |
| `compacted` | That the context was compacted, counted only |

Everything else, including the long lines of tool output, reasoning and compacted history, is skipped on its first 240 bytes and never decoded. That is why a scan of gigabytes of logs takes seconds.

## Counting each response once

The unit is the response, keyed by `response_id` from `token_usage_record`. This rules out the four ways Codex usage has been overcounted:

1. **Repeated events.** Codex sometimes writes the same `token_count` event twice. Tokens are never taken from `token_count` when a `token_usage_record` exists. (`test_one_record_per_response_and_repeated_token_count_ignored`)
2. **Forks.** A forked session's file starts with a copy of its parent's lines, response ids included. The copies hit the same keys and add nothing. (`test_fork_copy_and_second_machine_count_once`)
3. **Subagents.** A subagent's file carries its parent's `session_meta` as a second header. Only the first header names the session, so the subagent's own responses are filed under the subagent, marked as such, and its parent's are not repeated. (`test_subagent_and_copied_parent_header`)
4. **The same logs twice.** Logs synced from another machine, or scanned again, hit the same keys. (`test_fork_copy_and_second_machine_count_once`)

Logs written before `token_usage_record` existed have only running totals in `token_count`. For those, the usage of a request is the difference between successive totals, an unchanged total is a repeat rather than a request, and the row is keyed by the event's timestamp and total, which a copied line shares with its original. (`test_old_logs_with_running_totals_only`)

A scan reads only complete lines. A line Codex is still writing is left for the next scan, which resumes at that byte offset with a small saved state (the current model, effort, tier and the turn-to-model map), so a growing session is never re-read. (`test_partial_last_line_is_left_for_the_next_scan`)

## Cost

A response's API-equivalent cost, at the published Standard price per million tokens, is:

```
(input − cached − cache_written) × input price
+ cached × cached price
+ cache_written × cache-write price
+ output × output price
```

Input includes cached and cache-written tokens, and output includes reasoning tokens, as in OpenAI's usage object. Then:

- A prompt over 272K input tokens uses the long-context price, for the models that have one.
- The Fast tier (`priority` in the logs) costs 2× for most models, with the exceptions OpenAI lists (1.8× for gpt-5-mini, 1.75× for o3 and gpt-4.1); Flex and Batch cost 0.5×.
- A model with no price is unpriced. Its tokens are counted, its cost is left out of every dollar total, and every report says how many tokens that was. A model id is matched exactly, without a date suffix, or without a release-stage suffix such as `-preview`; it is never priced as a relative with a similar name. (`test_unknown_models_are_unpriced_not_free`, `test_lookup_handles_dates_providers_and_suffixes`)

Each row also gets a plan cost, which weights the Standard cost by how fast its tier spends plan limits: 2.5× for Fast and 8× for Ultrafast, from OpenAI's Codex speed page. The quota report uses plan cost, because that is what moves the windows.

## Plan windows

Each `token_count` event carries a reading like this one:

```json
"rate_limits": {"limit_id": "codex", "plan_type": "pro",
                "primary":   {"used_percent": 31.0, "window_minutes": 300,   "resets_at": 1793812560},
                "secondary": {"used_percent": 29.0, "window_minutes": 10080, "resets_at": 1794055140}}
```

The reading is stored with the id of the response just before it, because that is the response it describes.

Readings are grouped into windows by their reset time. The server's reset time drifts by a second or two between readings, so two readings whose reset times are within two minutes belong to the same window. A window that has not started (0%, with a reset a full window from now) is not a window: its reset time moves with every reading. (`test_window_readings_with_drifting_reset_times_are_one_window`, `test_a_window_that_has_not_started_is_not_a_window`)

Two sessions of one account can log their readings a few seconds out of order, so a window's level is the highest reading so far, not the latest. Once its reset time has passed, a window reads 0%, and the last figure is kept as "was". (`test_current_window_takes_the_highest_reading_and_spots_a_reset`)

The burn rate is the rise over the last hour of readings, in points per hour, and "full at" is when the window reaches 100% at that rate, shown only if that is before the reset. (`test_burn_rate_and_full_at`)

## Quota forensics

For each window codexwatch adds up the plan cost of the responses whose readings fall in it, and divides by how far the window moved. That is the window's exchange rate: dollars of API-equivalent usage per percentage point. It is shown only for windows that moved at least 2 points, since readings are whole percentages.

To find rises the logs cannot explain, it walks the window's level from reading to reading. Where the level rose by at least 3 points, it compares the rise with what the local usage in between would explain at the window's own rate, or else at the median rate of earlier windows of the same plan and length. A rise more than 3 times the explained amount is flagged. (`test_exchange_rate_and_a_rise_the_logs_cannot_explain`)

A window that was already part-used at this machine's first reading is listed separately, not flagged, since there is nothing local to compare it with. (`test_window_full_before_the_first_reading_is_not_a_jump`)

Typical causes of a flagged rise are Codex on another machine, ChatGPT chats where plan limits are shared, cloud tasks, or a change in how the plan meters usage.

## Accounts

When [codexhop](https://github.com/psychofict/codexhop) is installed, a window is attributed to an account by matching its reset time with the readings in codexhop's cache (`usage.json`), within two minutes. Two accounts practically never share a reset time to the second. A window read in the last five minutes, while no `codexhop run` terminal is open, is attributed to the live account. codexwatch reads account names from file names and never opens an account file.
