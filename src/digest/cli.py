from __future__ import annotations

import argparse
import logging
import os
import smtplib
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from .config import PROJECT_ROOT, ConfigError, load_config, validate_config
from .email_send import EmailConfigError, send_email
from .env import load_dotenv
from .generate import generate_digest
from .ingest import run_ingest
from .ingest_google import calendar_status, gmail_status, log_google_integration_status
from .logging_setup import setup_logging
from .store import DigestStore

logger = logging.getLogger(__name__)

VALID_MODES = {"morning", "night", "trello"}
# --mode auto picks by clock time: before this hour it's a morning brief,
# after it it's a night digest. Used by logon-triggered runs, where a login at
# 8 PM should not produce a "morning" email.
AUTO_MODE_CUTOFF_HOUR = 14


def resolve_mode(mode: str, now: datetime | None = None) -> str:
    if mode != "auto":
        return mode
    current = now or datetime.now()
    return "morning" if current.hour < AUTO_MODE_CUTOFF_HOUR else "night"
SCHEDULED_TASK_NAMES = [
    "Work Digest Morning",
    "Work Digest Trello",
    "Work Digest Night",
    "Daily Work Digest",  # legacy name from the first install script
]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT)
    setup_logging(PROJECT_ROOT, verbose=getattr(args, "verbose", False))

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    errors, warnings = validate_config(config)
    if args.command == "doctor":
        return run_doctor(config, errors, warnings)
    for warning in warnings:
        logger.warning("config: %s", warning)
    if errors:
        for error in errors:
            print(f"Config error: {error}", file=sys.stderr)
        return 1

    if getattr(args, "mode", None) == "auto":
        args.mode = resolve_mode(args.mode)
        print(f"Mode auto resolved to: {args.mode}")

    log_google_integration_status(config)
    store = DigestStore(config.sqlite_path)
    try:
        if args.command == "ingest":
            result = run_ingest(config, store)
            print(
                "Ingest complete: "
                f"Claude {result.claude_changed}, "
                f"Codex {result.codex_changed}, "
                f"notes {result.notes_changed}, "
                f"git {result.git_changed}; "
                f"{result.total_changed} changed source(s)."
            )
            return 0

        if args.command == "generate":
            generated = generate_digest(
                config=config,
                store=store,
                mode=args.mode,
                once_per_day=args.once_per_day,
                force=args.force,
                skip_ingest=args.skip_ingest,
            )
            if generated.skipped:
                print(f"Skipped; digest already exists for {date.today()} {args.mode}.")
            else:
                print(f"Wrote digest: {generated.digest_path}")
                print(f"Wrote email text: {generated.email_path}")
                print(f"Summarizer: {generated.provider_label}")
                if generated.ingest_result is not None:
                    print(
                        f"Ingested {generated.ingest_result.total_changed} changed source(s)."
                    )
            return 0

        if args.command == "send":
            return run_send(config, store, args)

        parser.print_help()
        return 2
    finally:
        store.close()


def run_send(config, store: DigestStore, args) -> int:
    existing = store.get_digest_run(date.today().isoformat(), args.mode)
    if (
        args.once_per_day
        and existing
        and existing["sent_at"]
        and not args.force
        and not args.dry_run
    ):
        print(f"Skipped; digest email was already sent for {date.today()} {args.mode}.")
        return 0

    generated = generate_digest(
        config=config,
        store=store,
        mode=args.mode,
        once_per_day=False,
        force=args.force,
        skip_ingest=args.skip_ingest,
    )
    print(f"Digest saved: {generated.digest_path}")

    if args.dry_run:
        print(f"Dry run only; no email sent. Email-ready text: {generated.email_path}")
        return 0

    try:
        send_email(
            email_config=config.email,
            subject=generated.subject,
            body=generated.email_body,
            html_body=generated.email_html,
        )
    except EmailConfigError as exc:
        # Degraded-but-successful: the report is on disk; email is optional.
        logger.warning("Email not sent: %s", exc)
        print("=" * 60)
        print(f"EMAIL NOT SENT (configuration): {exc}")
        print(
            "The digest was still generated and saved locally. To enable email, "
            "set DIGEST_EMAIL_FROM, DIGEST_SMTP_USERNAME, and DIGEST_SMTP_PASSWORD "
            "(for Gmail, use an App Password) in .env or user environment variables."
        )
        print("=" * 60)
        return 0
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("SMTP send failed: %s", exc)
        print(f"EMAIL SEND FAILED: {exc}", file=sys.stderr)
        print(f"The digest was still saved locally: {generated.digest_path}", file=sys.stderr)
        return 1

    store.record_sent(generated.digest_date.isoformat(), args.mode)
    logger.info("Sent %s digest email to %s", args.mode, config.email.recipient)
    print(f"Sent digest email to {config.email.recipient}.")
    return 0


def run_doctor(config, errors: list[str], warnings: list[str]) -> int:
    """Print a full health check: config, paths, env vars, scheduler, integrations."""
    print(f"Config file: {config.config_path}")
    print(f"Project root: {config.project_root}")
    print()

    print("Config validation:")
    if not errors and not warnings:
        print("  OK - no problems found.")
    for error in errors:
        print(f"  ERROR   {error}")
    for warning in warnings:
        print(f"  WARNING {warning}")
    print()

    print("Repos:")
    for name, path in config.repos.items():
        print(f"  {'OK     ' if path.exists() else 'MISSING'} {name}: {path}")
    print()

    print("Session sources:")
    for label, paths in (("claude", config.claude_paths), ("codex", config.codex_paths)):
        for path in paths:
            print(f"  {'OK     ' if path.exists() else 'MISSING'} {label}: {path}")
    print(
        f"  {'OK     ' if config.notes_path.exists() else 'MISSING'} notes: {config.notes_path}"
    )
    print()

    print("Summarizer:")
    api_key_set = bool(os.environ.get(config.model.api_key_env))
    print(f"  provider: {config.model.provider} | model: {config.model.name}")
    print(f"  {config.model.api_key_env}: {'set' if api_key_set else 'NOT SET (local_rules fallback will be used)'}")
    print()

    print("Email:")
    print(f"  enabled: {config.email.enabled} | recipient: {config.email.recipient or '(none)'}")
    for env_name in (
        config.email.sender_env,
        config.email.smtp_username_env,
        config.email.smtp_password_env,
    ):
        print(f"  {env_name}: {'set' if os.environ.get(env_name) else 'NOT SET'}")
    print()

    print("Optional integrations:")
    for status in (gmail_status(config), calendar_status(config)):
        print(f"  {status.name}: {status.detail}")
    print()

    print("Windows scheduled tasks:")
    found_any = False
    for task_name in SCHEDULED_TASK_NAMES:
        state = _query_scheduled_task(task_name)
        if state:
            found_any = True
            print(f"  INSTALLED {task_name} ({state})")
    if not found_any:
        print("  NOT INSTALLED - run scripts\\install_windows_task.ps1 to set up automation.")
    print()

    sqlite_ok = True
    try:
        store = DigestStore(config.sqlite_path)
        store.close()
    except Exception as exc:  # pragma: no cover - depends on local FS state
        sqlite_ok = False
        print(f"SQLite: ERROR opening {config.sqlite_path}: {exc}")
    if sqlite_ok:
        print(f"SQLite: OK ({config.sqlite_path})")

    print()
    if errors:
        print("Result: ERRORS found - fix the items marked ERROR above.")
        return 1
    print("Result: OK" + (" (with warnings)" if warnings else ""))
    return 0


def _query_scheduled_task(task_name: str) -> str | None:
    """Return the task status string if the Windows scheduled task exists."""
    if sys.platform != "win32":
        return None
    try:
        completed = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return "found"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digest",
        description=(
            "Generate and send local work digests (morning Daily Work "
            "Brief and night End-of-Day Work Digest)."
        ),
    )
    parser.add_argument(
        "--config",
        help="Path to config.yaml. Defaults to config.yaml, then config.example.yaml.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print INFO-level logs to the console.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "ingest", help="Ingest recent Claude/Codex sessions, notes, and git state."
    )
    subparsers.add_parser(
        "doctor",
        help="Check config, source paths, env vars, email setup, and scheduler status.",
    )

    generate_parser = subparsers.add_parser(
        "generate", help="Generate markdown and email text."
    )
    _add_mode_args(generate_parser)
    generate_parser.add_argument(
        "--once-per-day",
        action="store_true",
        help="Skip generation if today's digest for this mode already exists.",
    )
    generate_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if a digest already exists.",
    )
    generate_parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Use stored source data without refreshing ingestion first.",
    )

    send_parser = subparsers.add_parser(
        "send", help="Send the digest email or render a dry-run."
    )
    _add_mode_args(send_parser)
    send_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render email-ready text but do not send.",
    )
    send_parser.add_argument(
        "--once-per-day",
        action="store_true",
        help="Skip real sending if today's email for this mode was already sent.",
    )
    send_parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate and send even if today's digest exists.",
    )
    send_parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Use stored source data without refreshing ingestion first.",
    )

    return parser


def _add_mode_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mode",
        choices=[*sorted(VALID_MODES), "auto"],
        required=True,
        help="morning, night, or auto (picks by time of day; for logon triggers).",
    )


if __name__ == "__main__":
    raise SystemExit(main())
