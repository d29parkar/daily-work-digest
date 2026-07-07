# Troubleshooting

Start with `digest doctor`: it checks config, paths, env vars, email setup,
and scheduler status in one shot. Then check the newest file in
`outputs\logs\`. `outputs\logs\LAST_RUN_ERROR.txt` exists only if the most
recent scheduled run failed.

## No email arrived

1. `digest doctor` → Email section. All three env vars must say `set`.
   They live in `.env` or user-level environment variables. For Gmail you need
   an **App Password** (myaccount.google.com/apppasswords); normal passwords
   fail with `535 Username and Password not accepted`.
2. Was it already sent? `send --once-per-day` skips after the first success.
   Resend with `digest send --mode morning --force`.
3. The report itself is always in `outputs\digests\` even when email fails.

## Report says "Summarizer: local_rules (no LLM)"

The OpenAI call didn't happen or failed; the run note in the report header
says why. Common causes:

- `OPENAI_API_KEY` not set → add it to `.env`.
- **HTTP 429 "exceeded your current quota"** → the key's OpenAI account has no
  billing/credits. Add credits at platform.openai.com/settings/organization/billing.
- HTTP 401 → key invalid/revoked.
- Network errors → retried automatically at the next scheduled run.

The fallback report is still grounded (git + rule-extracted snippets); only
the judgment-heavy sections are thinner.

## Scheduled task didn't run

```powershell
schtasks /Query /TN "Work Digest Morning" /V /FO LIST
```

- `Task To Run` wrong or task missing → rerun `.\scripts\install_windows_task.ps1`.
- `Last Result` nonzero → open the newest `outputs\logs\morning-digest-*.log`.
- Laptop was asleep at 9:00 → the task uses catch-up (`StartWhenAvailable`) on
  the cmdlet path and a logon trigger / Startup shortcut, so it fires shortly
  after you log in. If it still misses, the Startup shortcut fallback:
  `.\scripts\install_windows_startup_shortcut.ps1`.
- `Access is denied` during install → the installer automatically falls back
  to `schtasks.exe` + Startup shortcut; check its final "Verification" output.

## Digest is empty / "no evidence collected"

- `digest doctor` → Session sources: are the Claude/Codex paths present?
- Lookback windows (`lookback_hours`) may be too short for a quiet day.
- Sessions must mention a configured repo name/path to be included. Check
  `repos:` in config.yaml.
- Run `digest ingest --verbose` (or `digest --verbose generate ...`) and check
  the day's log file for what was scanned and skipped.

## Digest contains its own sessions (self-noise)

Keep `filters.exclude_session_markers` containing
`daily-work-digest`. Any transcript mentioning that string is skipped.

## "Config error: ..." on startup

The message names the file and key. `config.yaml` is parsed by PyYAML when
installed, otherwise by a built-in subset parser; stick to plain
`key: value`, nested blocks, and `- item` lists (no anchors, no multi-line
strings). Delete `config.yaml` to fall back to `config.example.yaml`.

## Duplicate emails

Should be impossible with `--once-per-day` (the scheduled scripts always pass
it): a send is recorded in SQLite per (date, mode). If you deleted
`data/digest.sqlite` mid-day, that memory is gone; the next trigger may send
again once.

## Wrong/stale content after changing config

State lives in `data/digest.sqlite`. Deleting it forces a clean re-ingest on
the next run; that's always safe.
