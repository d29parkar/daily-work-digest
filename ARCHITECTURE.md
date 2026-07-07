# Architecture

## Overview

```
 ~/.claude/projects ─┐
 ~/.claude/sessions ─┤   ingest_sessions.py ──┐
 ~/.codex/sessions ──┘                        │
 notes/reviewer, notes/manual → ingest_notes.py ───┼──► SQLite (data/digest.sqlite)
 repo git state ─────────→ ingest_git.py ─────┘         sources table
                                                            │
                                                            ▼
                     generate.py ──► context.py (tagged evidence pack)
                                              │
                                              ▼
                     llm.py: OpenAI ──fallback──► local_rules ──► report body
                                              │
                                              ▼
                     report.py: header + body + local source-coverage section
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
              outputs/digests/*.md                email_send.py (SMTP)
              outputs/digests/*_email.txt         (dry-run or real send)
```

## Modules

| Module | Responsibility |
|---|---|
| `cli.py` | argparse CLI: `ingest`, `generate`, `send`, `doctor`; exit-code policy |
| `config.py` | config loading (PyYAML if present, else a built-in YAML subset parser), `validate_config` |
| `env.py` | loads `.env` into the environment (existing env vars win) |
| `ingest_sessions.py` | discovers recent Claude/Codex JSONL files, filters to configured repos, extracts snippets |
| `ingest_notes.py` | same for `notes/` files (reviewer notes, ChatGPT exports, task lists) |
| `ingest_git.py` | branch/status/log/diff-stat per configured repo via `git` subprocess (20s timeout, never raises) |
| `ingest_google.py` | Gmail/Calendar read-only stubs behind config flags |
| `summarize.py` | ingest-time rule-based snippet extraction (requests, actions, commands, issues, reviewer comments, keywords) |
| `store.py` | SQLite: `sources` + `digest_runs` tables, schema migration |
| `context.py` | builds the tagged evidence pack and the factual coverage lines |
| `llm.py` | summarizer abstraction: `openai`, `local_rules`, `fixture`, `auto`; fallback logic |
| `report.py` | final markdown assembly |
| `email_render.py` / `email_send.py` | subject/body/preview rendering; SMTP send |
| `logging_setup.py` | daily log file under `outputs/logs/` + console warnings |

## Ingestion and filtering

1. Files under the configured Claude/Codex paths with extensions
   `.jsonl/.json/.md/.txt/.log`, modified within `lookback_hours`, ≤50 MB.
2. JSONL/JSON transcripts are flattened to human text; `system`/`developer`/
   `tool_result` payloads are dropped to reduce noise.
3. A session is kept only if its path or text mentions a configured repo name
   or path (`text_utils.is_related_to_repos`, with a punctuation-insensitive
   compact match). Sessions matching `filters.exclude_session_markers` (by
   default this project itself) are skipped so the digest doesn't ingest its
   own runs.
4. Rule-based extraction (`summarize.py`) stores per-source snippet lists in
   SQLite; this is the retrieval layer the LLM later cites.

## SQLite tracking and idempotency

- `sources`: one row per (source_type, path), keyed by a SHA-256 id, with a
  content hash. Re-ingesting an unchanged file is a no-op; changed files
  update in place (upsert). This makes `ingest` safe to run any number of
  times.
- `digest_runs`: primary key `(digest_date, mode)` with `created_at`,
  `sent_at`, and `provider`. `generate --once-per-day` skips when a row
  exists; `send --once-per-day` skips only when `sent_at` is set, so a morning
  crash before sending doesn't block a retry. Morning and night are tracked
  independently. `--force` overrides both.

## Summarization

`context.py` renders every source as a tagged block (`[S1]`, `[S2]`… sessions
and notes; `[G1]`… git states) with its extracted snippets, truncated to
`model.max_prompt_chars`. `llm.py` sends the pack with a mode-specific prompt
template (`src/digest/prompts/`) to the OpenAI Chat Completions API (plain
HTTPS via stdlib; no SDK dependency). Providers:

- `auto` (default): OpenAI when `OPENAI_API_KEY` is set, else local rules.
- `openai`: OpenAI required; falls back loudly if the key is missing/broken.
- `local_rules`: deterministic, offline, no key.
- `fixture`: returns a canned file; used by tests and the smoke test so no
  test ever needs a real API call.

Switching to a cheaper model is a one-line config change (`model.name`).

### Hallucination safeguards

1. **Grounding by construction**: the LLM only sees extracted evidence, and the
   system prompt forbids claims without a citation tag.
2. **Citations**: every bullet must cite `[S#]`/`[G#]`; the tags map back to
   real files listed in the coverage section.
3. **observed vs inferred**: each item is labeled; inferences are explicit.
4. **Needs verification**: weakly-evidenced items are quarantined into their
   own section instead of being stated as fact.
5. **Section validation**: if the LLM response is missing most expected
   sections it is rejected and the run falls back to local rules, with the
   failure noted in the report header.
6. **Local coverage section**: "Source coverage and missing inputs" is always
   computed from what was actually ingested, never generated by the LLM.
7. **Empty-section honesty**: prompts require "Nothing found in available
   sources." instead of padding.

## Report modes

- **Morning (Daily Work Brief)**: executive summary; yesterday's completed
  work; open bugs/unresolved questions; decisions made; what needs testing;
  Trello-ready updates; prompts to send to coding agents; top 3 tasks; people
  to follow up with; source coverage.
- **Night (End-of-Day Work Digest)**: what changed today; work completed; work
  in progress; blockers; risky or untested changes; notes for tomorrow
  morning; suggested coding-agent prompts; source coverage.

## Email

`generate` always writes the markdown and the email preview to disk **before**
any send attempt, so a send failure never loses the report. Missing SMTP
credentials are treated as degraded-but-successful (loud diagnostic, exit 0);
network/auth failures exit 1.

## Scheduling

`scripts/install_windows_task.ps1` registers two per-user tasks
(morning daily + 5-min-after-logon trigger; night daily). If the
ScheduledTasks cmdlets are blocked it falls back to `schtasks.exe` plus a
Startup-folder shortcut, then re-queries both tasks and fails loudly if either
is missing. `scripts/run_digest.ps1` transcripts every run to
`outputs/logs/` and writes `outputs/logs/LAST_RUN_ERROR.txt` on failure
(cleared on the next success). `digest doctor` reports scheduler status.

## Adding a new source

1. Write an `ingest_<source>.py` that discovers items, extracts text, and calls
   `store.upsert_source(source_type=..., summary=summarize_text_source(...))`.
2. Wire it into `ingest.run_ingest` and add a label in
   `context.SOURCE_TYPE_LABELS`.
3. Add a coverage line in `context.build_coverage_lines`.
4. Add tests mirroring `tests/test_ingest_sessions.py`.
