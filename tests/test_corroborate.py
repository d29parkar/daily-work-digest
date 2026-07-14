from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import write_config
from digest.config import load_config
from digest.corroborate import collect_git_facts, corroborate_units
from digest.store_v2 import SessionRecord, TurnRecord, WorkStore, WorkUnit


@pytest.fixture()
def store(tmp_path: Path) -> WorkStore:
    s = WorkStore(tmp_path / "digest.sqlite")
    yield s
    s.close()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-fastapi"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "widget.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fix widget crash")
    (repo / "dirty.py").write_text("wip\n", encoding="utf-8")
    return repo


def _store_session_with_turn(
    store: WorkStore, *, tools: list[dict] | None = None, files: list[str] | None = None
) -> None:
    session = SessionRecord(
        session_id="claude:abc", agent="claude", path="x", content_hash="h",
        title=None, cwd=None, git_branch=None,
        started_at="2026-07-12T09:00:00-04:00", ended_at="2026-07-12T10:00:00-04:00",
        turn_count=1, parse_errors=0,
    )
    turn = TurnRecord(
        turn_id="claude:abc:1:p1", session_id="claude:abc", seq=1,
        started_at="2026-07-12T09:00:00-04:00", ended_at=None,
        user_text="u", assistant_text="a",
        tools=tools or [], files=files or [],
    )
    store.upsert_session(session, [turn])


def _unit(
    *, files: list[str], status_claim: str = "done", claims: list[dict] | None = None
) -> WorkUnit:
    unit = WorkUnit(
        unit_key="claude:abc:2026-07-12:1", work_date="2026-07-12", project_id=None,
        turn_ids=["claude:abc:1:p1"], intent="i", kind="debugging",
        outcome_claim="o", status_claim=status_claim, files=files,
        entities=[], claims=claims or [], open_questions=[],
    )
    return unit


def _prepare(store: WorkStore, tmp_path: Path):
    repo = _make_repo(tmp_path)
    config = load_config(write_config(tmp_path, repos={"my-api-repo": repo}))
    summary = collect_git_facts(store, config, lookback_hours=48)
    assert summary["repos"]["my-api-repo"]["commits"] == 1
    assert summary["repos"]["my-api-repo"]["dirty_files"] == 1
    return config


def test_commit_overlap_corroborates(store: WorkStore, tmp_path: Path):
    _prepare(store, tmp_path)
    _store_session_with_turn(store)
    unit = _unit(files=["C:\\somewhere\\repo-fastapi\\widget.py"])
    store.replace_work_units("claude:abc", "2026-07-12", [unit])
    corroborate_units(store, [unit])
    stored = store.list_units_for_date("2026-07-12")[0]
    assert stored.verification["verdict"] == "corroborated_by_commit"
    assert stored.verification["evidence"][0].startswith("commit:")


def test_dirty_overlap_is_uncommitted(store: WorkStore, tmp_path: Path):
    _prepare(store, tmp_path)
    _store_session_with_turn(store)
    unit = _unit(files=["dirty.py"], status_claim="in_progress")
    store.replace_work_units("claude:abc", "2026-07-12", [unit])
    corroborate_units(store, [unit])
    stored = store.list_units_for_date("2026-07-12")[0]
    assert stored.verification["verdict"] == "uncommitted"


def test_done_claim_without_repo_evidence_is_contradicted(store: WorkStore, tmp_path: Path):
    _prepare(store, tmp_path)
    _store_session_with_turn(store)
    unit = _unit(
        files=["ghost.py"],
        claims=[{"type": "tests_pass", "text": "all green"}],
    )
    store.replace_work_units("claude:abc", "2026-07-12", [unit])
    corroborate_units(store, [unit])
    stored = store.list_units_for_date("2026-07-12")[0]
    assert stored.verification["verdict"] == "contradicted"
    assert any("tests pass" in n for n in stored.verification["notes"])


def test_harness_verified_patch_beats_dirty(store: WorkStore, tmp_path: Path):
    _prepare(store, tmp_path)
    _store_session_with_turn(
        store,
        tools=[{"name": "apply_patch", "ok": True, "harness_verified": True}],
        files=["parser.py"],
    )
    unit = _unit(files=["parser.py"], status_claim="done")
    store.replace_work_units("claude:abc", "2026-07-12", [unit])
    corroborate_units(store, [unit])
    stored = store.list_units_for_date("2026-07-12")[0]
    assert stored.verification["verdict"] == "applied_by_harness"


def test_discovery_unit_with_no_files(store: WorkStore, tmp_path: Path):
    _prepare(store, tmp_path)
    _store_session_with_turn(store)
    unit = _unit(files=[], status_claim="done")
    store.replace_work_units("claude:abc", "2026-07-12", [unit])
    corroborate_units(store, [unit])
    stored = store.list_units_for_date("2026-07-12")[0]
    assert stored.verification["verdict"] == "no_files"
