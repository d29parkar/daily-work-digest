from __future__ import annotations

import json
from pathlib import Path

from conftest import write_config
from digest.config import load_config
from digest.ingest_sessions import ingest_claude, ingest_codex
from digest.store import DigestStore


def _write_jsonl(path: Path, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(m) for m in messages), encoding="utf-8"
    )


def test_claude_session_matching_repo_is_ingested(tmp_path):
    claude_dir = tmp_path / "claude"
    _write_jsonl(
        claude_dir / "proj" / "session1.jsonl",
        [
            {"role": "user", "content": "Fix the upload bug in my-api-repo"},
            {"role": "assistant", "content": "I updated the retry logic and fixed the parser."},
        ],
    )
    config = load_config(write_config(tmp_path))
    store = DigestStore(config.sqlite_path)
    assert ingest_claude(config, store) == 1
    sources = store.list_sources_since("2000-01-01T00:00:00")
    assert len(sources) == 1
    assert sources[0].source_type == "claude"
    assert "my-api-repo" in sources[0].repo_names
    store.close()


def test_unrelated_session_is_skipped(tmp_path):
    claude_dir = tmp_path / "claude"
    _write_jsonl(
        claude_dir / "proj" / "other.jsonl",
        [{"role": "user", "content": "Write a poem about otters"}],
    )
    config = load_config(write_config(tmp_path))
    store = DigestStore(config.sqlite_path)
    assert ingest_claude(config, store) == 0
    store.close()


def test_excluded_marker_session_is_skipped(tmp_path):
    codex_dir = tmp_path / "codex"
    _write_jsonl(
        codex_dir / "rollout.jsonl",
        [
            {
                "role": "user",
                "content": "work on daily-work-digest for my-api-repo",
            }
        ],
    )
    config_path = write_config(
        tmp_path,
        extra="filters:\n  exclude_session_markers:\n    - daily-work-digest",
    )
    config = load_config(config_path)
    store = DigestStore(config.sqlite_path)
    assert ingest_codex(config, store) == 0
    store.close()


def test_codex_session_matching_repo_path_is_ingested(tmp_path):
    codex_dir = tmp_path / "codex"
    repo_path = tmp_path / "repo-fastapi"
    _write_jsonl(
        codex_dir / "rollout.jsonl",
        [{"role": "user", "content": f"cwd is {repo_path}"}],
    )
    config = load_config(write_config(tmp_path))
    store = DigestStore(config.sqlite_path)
    assert ingest_codex(config, store) == 1
    store.close()


def test_reingest_unchanged_file_reports_no_change(tmp_path):
    claude_dir = tmp_path / "claude"
    _write_jsonl(
        claude_dir / "s.jsonl",
        [{"role": "user", "content": "my-api-repo work"}],
    )
    config = load_config(write_config(tmp_path))
    store = DigestStore(config.sqlite_path)
    assert ingest_claude(config, store) == 1
    assert ingest_claude(config, store) == 0  # unchanged content -> idempotent
    store.close()
