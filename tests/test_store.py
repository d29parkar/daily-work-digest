from __future__ import annotations

from pathlib import Path

from digest.store import DigestStore


def make_store(tmp_path: Path) -> DigestStore:
    return DigestStore(tmp_path / "test.sqlite")


def test_upsert_reports_change_only_when_hash_differs(tmp_path):
    store = make_store(tmp_path)
    kwargs = dict(
        source_type="claude",
        path="c:/sessions/a.jsonl",
        modified_at="2026-07-05T10:00:00+00:00",
        content_hash="abc",
        repo_names=["my-api-repo"],
        summary={"actions": ["did a thing"]},
    )
    assert store.upsert_source(**kwargs) is True
    assert store.upsert_source(**kwargs) is False
    kwargs["content_hash"] = "def"
    assert store.upsert_source(**kwargs) is True
    store.close()


def test_digest_run_once_per_day_and_sent_tracking(tmp_path):
    store = make_store(tmp_path)
    assert store.get_digest_run("2026-07-06", "morning") is None
    store.record_digest_run(
        digest_date="2026-07-06",
        mode="morning",
        digest_path=tmp_path / "d.md",
        email_path=tmp_path / "e.txt",
        source_ids=["s1"],
        provider="fixture:test",
    )
    run = store.get_digest_run("2026-07-06", "morning")
    assert run is not None
    assert run["sent_at"] is None
    assert run["provider"] == "fixture:test"
    # Night is tracked independently.
    assert store.get_digest_run("2026-07-06", "night") is None

    store.record_sent("2026-07-06", "morning")
    assert store.get_digest_run("2026-07-06", "morning")["sent_at"] is not None
    store.close()


def test_schema_migration_adds_provider_column(tmp_path):
    import sqlite3

    db_path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        create table digest_runs (
            digest_date text not null,
            mode text not null,
            digest_path text not null,
            email_path text not null,
            source_ids_json text not null,
            created_at text not null,
            sent_at text,
            primary key (digest_date, mode)
        );
        """
    )
    conn.commit()
    conn.close()

    store = DigestStore(db_path)  # must not raise; migration adds 'provider'
    store.record_digest_run(
        digest_date="2026-07-06",
        mode="night",
        digest_path=tmp_path / "d.md",
        email_path=tmp_path / "e.txt",
        source_ids=[],
        provider="local_rules (no LLM)",
    )
    assert store.get_digest_run("2026-07-06", "night")["provider"] == "local_rules (no LLM)"
    store.close()
