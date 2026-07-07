from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import DigestConfig
from .email_render import render_email_text
from .ingest import IngestResult, run_ingest
from .ingest_git import collect_git_states
from .report import render_report
from .store import DigestStore, now_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerateResult:
    digest_path: Path
    email_path: Path
    digest_markdown: str
    email_body: str
    email_preview: str
    email_html: str
    subject: str
    digest_date: date
    skipped: bool
    provider_label: str
    ingest_result: IngestResult | None


def generate_digest(
    *,
    config: DigestConfig,
    store: DigestStore,
    mode: str,
    once_per_day: bool = False,
    force: bool = False,
    skip_ingest: bool = False,
) -> GenerateResult:
    today = date.today()
    existing = store.get_digest_run(today.isoformat(), mode)
    if once_per_day and existing and not force:
        digest_path = Path(existing["digest_path"])
        email_path = Path(existing["email_path"])
        digest_markdown = _read_if_exists(digest_path)
        subject, body, preview, html = render_email_text(
            config=config,
            mode=mode,
            digest_markdown=digest_markdown,
            digest_date=today,
        )
        logger.info("Skipping generation; %s digest already exists for %s", mode, today)
        return GenerateResult(
            digest_path=digest_path,
            email_path=email_path,
            digest_markdown=digest_markdown,
            email_body=body,
            email_preview=preview,
            email_html=html,
            subject=subject,
            digest_date=today,
            skipped=True,
            provider_label=str(existing["provider"] or "unknown")
            if "provider" in existing.keys()
            else "unknown",
            ingest_result=None,
        )

    ingest_result = None if skip_ingest else run_ingest(config, store)
    lookback_hours = max(
        config.claude_lookback_hours,
        config.codex_lookback_hours,
        config.notes_lookback_hours,
    )
    since = (datetime.now().astimezone() - timedelta(hours=lookback_hours)).isoformat(
        timespec="seconds"
    )
    sources = [
        source
        for source in store.list_sources_since(since)
        # Fresh git state is collected below as [G#] evidence; stored git_state
        # rows would duplicate it in the Sources appendix.
        if source.source_type != "git_state"
        and not _source_is_excluded(source, config.exclude_session_markers)
    ]
    # list_sources_since is newest-first; cap to keep the report and its
    # Sources appendix readable.
    sources = sources[: config.max_sources]
    git_states = collect_git_states(config, lookback_hours=lookback_hours)
    logger.info(
        "Generating %s digest with %d source(s) and %d git state(s)",
        mode,
        len(sources),
        len(git_states),
    )

    generated_at = now_iso()
    digest_markdown, provider_label = render_report(
        config=config,
        mode=mode,
        digest_date=today.isoformat(),
        generated_at=generated_at,
        sources=sources,
        git_states=git_states,
    )
    subject, body, preview, html = render_email_text(
        config=config,
        mode=mode,
        digest_markdown=digest_markdown,
        digest_date=today,
    )

    config.output_path.mkdir(parents=True, exist_ok=True)
    digest_path = config.output_path / f"{today.isoformat()}_{mode}_digest.md"
    email_path = config.output_path / f"{today.isoformat()}_{mode}_email.txt"
    html_path = config.output_path / f"{today.isoformat()}_{mode}_email.html"
    digest_path.write_text(digest_markdown, encoding="utf-8")
    email_path.write_text(preview, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")

    source_ids = [source.source_id for source in sources]
    store.record_digest_run(
        digest_date=today.isoformat(),
        mode=mode,
        digest_path=digest_path,
        email_path=email_path,
        source_ids=source_ids,
        provider=provider_label,
    )
    store.mark_sources_included(source_ids)

    return GenerateResult(
        digest_path=digest_path,
        email_path=email_path,
        digest_markdown=digest_markdown,
        email_body=body,
        email_preview=preview,
        email_html=html,
        subject=subject,
        digest_date=today,
        skipped=False,
        provider_label=provider_label,
        ingest_result=ingest_result,
    )


def _read_if_exists(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _source_is_excluded(source: object, markers: list[str]) -> bool:
    if not markers:
        return False
    haystack = (
        f"{getattr(source, 'path', '')}\n"
        f"{json.dumps(getattr(source, 'summary', {}), sort_keys=True)}"
    ).lower()
    return any(marker and marker in haystack for marker in markers)
