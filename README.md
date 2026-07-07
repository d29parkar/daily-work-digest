# Daily Work Digest

A local work-memory agent. It reads your coding-agent sessions (Claude Code,
Codex), your git activity, and your notes, and turns them into two daily
reports: a morning **Daily Work Brief** and a night **End-of-Day Work Digest**.
After one-time setup it runs on its own through Windows Task Scheduler and
emails you the report. You do not run anything manually.

I built this because context evaporates between coding sessions. By the next
morning I had usually lost track of what was finished, what broke, what the
reviewer asked for, and what to tell the coding agent next. The information was
already sitting in transcripts, git history, and notes; nothing was reading it
back to me. This does.

## What you get every morning

The Daily Work Brief covers:

1. Executive summary
2. Yesterday's completed work
3. Open bugs / unresolved questions
4. Decisions made
5. What needs testing
6. Trello-ready updates (paste-ready, first person, no fluff)
7. Prompts to send to coding agents (paste-ready, scoped, specific)
8. Top 3 tasks for today, with the first 60 to 90 minutes spelled out
9. People to follow up with
10. Source coverage and missing inputs

The night digest closes the day: what changed, what is actually done versus in
progress, blockers, risky or untested changes, and notes so tomorrow starts
with execution instead of re-orienting.

There is also a midday Trello mode (weekdays 12:45 PM by default): a
paste-ready card that leads with a "Bigger picture" paragraph on where the
workstream stands, then the short "Today's Update" lines, built from the
morning's coding-agent sessions.

Every claim in the report cites its source (`[S1]` for a session or note,
`[G1]` for git) and is labeled (observed) or (inferred). Anything shaky goes
into a "Needs verification" section instead of being stated as fact. The point
is a report you can trust, not a report that sounds impressive.

## What it ingests

| Source | How | Default |
|---|---|---|
| Claude Code sessions | `~/.claude/projects`, `~/.claude/sessions` (JSONL) | on |
| Codex sessions | `~/.codex/sessions` (JSONL) | on |
| ChatGPT exports / notes / task lists | drop files into `notes/manual/` or `notes/reviewer/` | on |
| Git state | branch, status, commits, diff stats for configured repos | on |
| Gmail | read-only, behind a config flag | off (stub, see [docs/GOOGLE_INTEGRATIONS.md](docs/GOOGLE_INTEGRATIONS.md)) |
| Google Calendar | read-only, behind a config flag | off (stub) |

Sessions are filtered to the repos you name in config, so unrelated chats stay
out of the report. Missing sources degrade gracefully and are listed in every
report under "Source coverage and missing inputs".

Everything runs locally. The only things that leave your machine are the
evidence snippets sent to the OpenAI API for summarization (only if you set a
key) and the digest email you send to yourself.

## Setup

Requires Python 3.12+ on Windows. From the project folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.yaml config.yaml   # set your repos; %USERPROFILE% paths work as-is
Copy-Item .env.example .env                 # add OPENAI_API_KEY + SMTP creds (both optional)
```

Try it:

```powershell
.\.venv\Scripts\python.exe -m digest.cli doctor              # health check
.\.venv\Scripts\python.exe -m digest.cli generate --mode morning
.\.venv\Scripts\python.exe -m digest.cli send --mode morning --dry-run
```

Install the automation (one time):

```powershell
.\scripts\install_windows_task.ps1     # morning 9:00 + logon, night 21:30
```

That's it. Reports land in `outputs/digests/`, logs in `outputs/logs/`, and
`digest doctor` tells you if anything is off.

## CLI

```text
digest doctor                      health check: config, paths, env vars, scheduler
digest ingest                      refresh sources into SQLite
digest generate --mode morning     write markdown + email text (also: night)
digest send --mode morning --dry-run       render only, never send
digest send --mode morning --once-per-day  send once; later runs skip
digest send --mode morning --force         resend even if already sent today
```

## How summarization works

With `OPENAI_API_KEY` set, an OpenAI model (default `gpt-4o-mini`, one config
line to change) writes the report from a tagged evidence pack and is required
to cite it. Without a key, or when the API fails, a deterministic rule-based
fallback produces the same report skeleton and the header says so. Tests run
fully offline against a fixture provider; no test ever calls the real API.

The prompt templates live in `src/digest/prompts/` and are meant to be edited.
Mine encode how I like status updates written (short, first person, exact
function and file names, no vague progress language) and how I write prompts
for coding agents (inspect first, come back with A/B/C, do not propose broad
rewrites). Change them to match how you work.

## Email and scheduling

`digest send` uses SMTP (for Gmail, an app password). Without credentials it
still generates and saves the report, prints a loud "EMAIL NOT SENT" note, and
exits 0 so scheduled runs stay green. `--once-per-day` guarantees at most one
real email per mode per day; a failed send retries on the next trigger.

The installer registers two per-user scheduled tasks (morning has a
5-minutes-after-logon trigger too, for days the laptop was asleep at 9:00).
If the ScheduledTasks cmdlets are blocked it falls back to `schtasks.exe` plus
a Startup-folder shortcut, verifies both tasks, and fails loudly otherwise.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): how ingestion, SQLite tracking, and summarization work
- [CONFIGURATION.md](CONFIGURATION.md): every config key and environment variable
- [OPERATING_MANUAL.md](OPERATING_MANUAL.md): scheduling, manual runs, verifying the next run
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): common failures and fixes
- [docs/GOOGLE_INTEGRATIONS.md](docs/GOOGLE_INTEGRATIONS.md): Gmail/Calendar integration plan

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest -q      # offline, mocked OpenAI
.\scripts\smoke_test.ps1                     # full end-to-end smoke test
```

## Known limitations

- Gmail/Calendar/Trello ingestion are documented stubs, not implemented.
- Terminal logs are only picked up if they land in a configured folder as
  `.log`/`.txt` files.
- The rule-based fallback cannot extract "decisions made" reliably; it says so
  instead of guessing.
- Lookback windows compare local-time ISO strings, so a timezone change
  mid-day can shift what falls inside the window.
