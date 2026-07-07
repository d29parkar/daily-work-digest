from __future__ import annotations

import json
from pathlib import Path

from conftest import write_config
from digest.config import load_config
from digest.generate import generate_digest
from digest.llm import MORNING_SECTIONS, NIGHT_SECTIONS
from digest.store import DigestStore


def _setup(tmp_path: Path, provider: str = "local_rules"):
    # A session file that mentions the repo so it gets ingested.
    session = tmp_path / "claude" / "s.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        json.dumps(
            {
                "role": "user",
                "content": "Fixed the ingest bug in my-api-repo; pytest failed once",
            }
        ),
        encoding="utf-8",
    )
    config = load_config(write_config(tmp_path, provider=provider))
    store = DigestStore(config.sqlite_path)
    return config, store


def test_generate_morning_report_has_required_sections(tmp_path):
    config, store = _setup(tmp_path)
    result = generate_digest(config=config, store=store, mode="morning")
    assert not result.skipped
    assert result.digest_path.exists()
    assert result.email_path.exists()
    text = result.digest_markdown
    assert text.startswith("# Daily Work Brief")
    for section in MORNING_SECTIONS:
        assert f"## {section}" in text
    assert "## Sources" in text  # human-readable citation appendix
    assert "## Source coverage and missing inputs" in text
    assert "Claude Code sessions: 1 included" in text
    assert result.email_html.startswith("<html>")
    assert "<h2>" in result.email_html
    store.close()


def test_generate_night_report_has_required_sections(tmp_path):
    config, store = _setup(tmp_path)
    result = generate_digest(config=config, store=store, mode="night")
    text = result.digest_markdown
    assert text.startswith("# End-of-Day Work Digest")
    for section in NIGHT_SECTIONS:
        assert f"## {section}" in text
    assert "## Source coverage and missing inputs" in text
    store.close()


def test_generate_trello_update_has_card_sections(tmp_path):
    config, store = _setup(tmp_path)
    result = generate_digest(config=config, store=store, mode="trello")
    text = result.digest_markdown
    assert text.startswith("# Trello Update")
    assert "## Bigger picture" in text
    assert "## Today's Update" in text
    assert "Trello Update" in result.subject
    # Tracked independently of morning/night.
    assert store.get_digest_run(result.digest_date.isoformat(), "trello") is not None
    assert store.get_digest_run(result.digest_date.isoformat(), "morning") is None
    store.close()


def test_once_per_day_skips_and_force_regenerates(tmp_path):
    config, store = _setup(tmp_path)
    first = generate_digest(config=config, store=store, mode="morning")
    assert not first.skipped

    second = generate_digest(config=config, store=store, mode="morning", once_per_day=True)
    assert second.skipped
    assert second.digest_path == first.digest_path

    third = generate_digest(
        config=config, store=store, mode="morning", once_per_day=True, force=True
    )
    assert not third.skipped
    store.close()


def test_morning_and_night_are_tracked_separately(tmp_path):
    config, store = _setup(tmp_path)
    generate_digest(config=config, store=store, mode="morning")
    night = generate_digest(config=config, store=store, mode="night", once_per_day=True)
    assert not night.skipped  # morning run must not block the night run
    store.close()


def test_fixture_provider_body_is_used(tmp_path):
    fixture = tmp_path / "canned.md"
    fixture.write_text("## Executive summary\n- canned body", encoding="utf-8")
    config = load_config(
        write_config(tmp_path, provider="fixture", fixture_path=str(fixture))
    )
    store = DigestStore(config.sqlite_path)
    result = generate_digest(config=config, store=store, mode="morning")
    assert "canned body" in result.digest_markdown
    assert result.provider_label == "fixture:canned.md"
    store.close()


def test_email_preview_contains_envelope_but_body_does_not(tmp_path):
    config, store = _setup(tmp_path)
    result = generate_digest(config=config, store=store, mode="morning")
    assert result.email_preview.startswith("To: test@example.com")
    assert "Subject: Test Digest - Daily Work Brief" in result.email_preview
    assert not result.email_body.startswith("To:")
    store.close()


def test_provider_recorded_in_store(tmp_path):
    config, store = _setup(tmp_path)
    result = generate_digest(config=config, store=store, mode="morning")
    run = store.get_digest_run(result.digest_date.isoformat(), "morning")
    assert run["provider"] == "local_rules (no LLM)"
    store.close()
