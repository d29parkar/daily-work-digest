# Gmail and Google Calendar integration plan

Status: **stubs only** (later-stage, optional). The first production version is
fully useful without them; enabling the flags today just adds an
"enabled but not yet implemented" note to the report's coverage section.

## Design principles (non-negotiable)

- **Read-only.** Scopes will be exactly:
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/calendar.readonly`
- Never send, delete, archive, label, modify, or reply to anything.
- No production email sending through Gmail API; digest delivery stays on
  SMTP with an app password unless explicitly reconfigured later.
- Disabled by default; each source has its own flag under `integrations:` in
  config.yaml.
- OAuth tokens will be stored locally (`tokens/`, already git-ignored) and
  never committed.

## Planned implementation (direct API)

1. `pip install google-api-python-client google-auth-oauthlib` as an optional
   extra (`pip install -e .[google]`).
2. One-time `digest google-auth` command runs the local OAuth consent flow
   with the two read-only scopes and stores the refresh token under `tokens/`.
3. `ingest_gmail.py`: list messages from the lookback window matching a
   configurable query (e.g. `from:your-reviewer OR subject:(project keywords)`), extract
   subject/sender/snippet, store via the existing `store.upsert_source`
   pipeline as `source_type="gmail"`.
4. `ingest_calendar.py`: events in ±24h, store title/time/attendees as
   `source_type="calendar"`; feeds "People to follow up with" and today's
   schedule context.
5. Both wired into `run_ingest` behind their flags; coverage lines flip from
   "stub" to counts automatically.

## MCP alternative

If you already run Google MCP servers (e.g. the claude.ai Gmail/Calendar
connectors), an MCP-based path avoids storing OAuth tokens in this project:
a small adapter could call an MCP server instead of the Google SDK. This is
documented as an option, **not** a requirement; the first usable version of
these ingesters should work standalone via the direct API above, keeping this
project runnable headless from Task Scheduler.

## Safety checklist before enabling (future)

- [ ] Scopes in the consent screen are exactly the two `.readonly` scopes.
- [ ] `tokens/` is git-ignored (already in `.gitignore`).
- [ ] `digest doctor` shows the integration status correctly.
- [ ] A revoked token degrades to a coverage-section warning, never a crash.
