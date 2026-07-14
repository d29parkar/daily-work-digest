from __future__ import annotations

from pathlib import Path

import pytest

from digest.store_v2 import SessionRecord, TurnRecord, WorkStore, WorkUnit


@pytest.fixture()
def store(tmp_path: Path) -> WorkStore:
    s = WorkStore(tmp_path / "digest.sqlite")
    yield s
    s.close()


def _session(session_id: str = "claude:abc", content_hash: str = "h1") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        agent="claude",
        path="C:/fake/abc.jsonl",
        content_hash=content_hash,
        title="Fix the widget",
        cwd="c:/projects/widget",
        git_branch="main",
        started_at="2026-07-12T09:00:00-04:00",
        ended_at="2026-07-12T10:00:00-04:00",
        turn_count=1,
        parse_errors=0,
    )


def _turn(turn_id: str = "claude:abc:p1", seq: int = 1) -> TurnRecord:
    return TurnRecord(
        turn_id=turn_id,
        session_id="claude:abc",
        seq=seq,
        started_at="2026-07-12T09:00:00-04:00",
        ended_at="2026-07-12T09:05:00-04:00",
        user_text="fix the widget",
        assistant_text="done",
        tools=[{"name": "Edit", "detail": "widget.py", "ok": True}],
        files=["widget.py"],
    )


def _unit(unit_key: str = "claude:abc:1") -> WorkUnit:
    return WorkUnit(
        unit_key=unit_key,
        work_date="2026-07-12",
        project_id=None,
        turn_ids=["claude:abc:p1"],
        intent="Fix the widget crash",
        kind="debugging",
        outcome_claim="Fixed",
        status_claim="done",
        files=["widget.py"],
        entities=["widget"],
        claims=[{"type": "tests_pass", "text": "pytest green"}],
        open_questions=[],
    )


def test_upsert_session_is_idempotent(store: WorkStore):
    assert store.upsert_session(_session(), [_turn()]) is True
    assert store.upsert_session(_session(), [_turn()]) is False  # same hash
    assert store.upsert_session(_session(content_hash="h2"), [_turn()]) is True
    turns = store.list_turns("claude:abc")
    assert len(turns) == 1  # replaced, not duplicated
    assert turns[0].files == ["widget.py"]


def test_sessions_between_uses_overlap(store: WorkStore):
    store.upsert_session(_session(), [_turn()])
    found = store.list_sessions_between("2026-07-12T00:00:00", "2026-07-13T00:00:00")
    assert [s.session_id for s in found] == ["claude:abc"]
    assert store.list_sessions_between("2026-07-13T00:00:00", "2026-07-14T00:00:00") == []


def test_replace_work_units_is_idempotent(store: WorkStore):
    store.replace_work_units("claude:abc", "2026-07-12", [_unit()])
    store.replace_work_units("claude:abc", "2026-07-12", [_unit()])
    units = store.list_units_for_date("2026-07-12")
    assert len(units) == 1
    assert units[0].claims[0]["type"] == "tests_pass"


def test_state_version_rerun_replaces_same_day(store: WorkStore):
    store.create_project("widget", "Widget")
    v1 = store.write_state_version(
        "widget", "2026-07-12",
        goal="ship widget", system_state="alpha", narrative_delta="d1",
        open_threads=[], evidence={}, written_by="test",
    )
    v1b = store.write_state_version(
        "widget", "2026-07-12",
        goal="ship widget", system_state="beta", narrative_delta="d2",
        open_threads=[], evidence={}, written_by="test",
    )
    assert v1 == v1b == 1
    v2 = store.write_state_version(
        "widget", "2026-07-13",
        goal="ship widget", system_state="rc", narrative_delta="d3",
        open_threads=[{"text": "t", "since": "2026-07-13", "status": "open"}],
        evidence={}, written_by="test",
    )
    assert v2 == 2
    head = store.get_head_state("widget")
    assert head.version == 2 and head.system_state == "rc"
    assert store.rollback_state("widget", "2026-07-12") == 1
    assert store.get_head_state("widget").system_state == "beta"


def test_matchers_and_overrides(store: WorkStore):
    store.create_project("widget", "Widget")
    store.add_matcher("widget", "cwd_prefix", "C:/Projects/Widget")
    store.add_matcher("widget", "cwd_prefix", "c:/projects/widget")  # dedupe (lowered)
    assert len(store.list_matchers("widget")) == 1
    store.set_override("claude:abc:1", "widget", "manual fix")
    assert store.get_override("claude:abc:1") == "widget"
    assert store.get_override("claude:abc:2") is None


def test_stage_status_accumulates(store: WorkStore):
    store.record_stage("2026-07-12", "night", "harvest", "ok")
    store.record_stage("2026-07-12", "night", "extract", "failed: boom")
    assert store.get_stage_status("2026-07-12", "night") == {
        "harvest": "ok",
        "extract": "failed: boom",
    }


def test_facts_upsert(store: WorkStore):
    store.upsert_fact(
        "git_facts_v2",
        "abc",
        {
            "repo_path": "C:/r",
            "kind": "commit",
            "ref": "deadbeef",
            "observed_at": "2026-07-12T09:00:00",
            "data_json": '{"files": ["a.py"]}',
        },
    )
    facts = store.list_git_facts("commit")
    assert facts[0]["data"] == {"files": ["a.py"]}


def test_coexists_with_v1_store(tmp_path: Path):
    from digest.store import DigestStore

    db = tmp_path / "digest.sqlite"
    v1 = DigestStore(db)
    v2 = WorkStore(db)
    v2.create_project("p", "P")
    assert v1.get_digest_run("2026-07-12", "night") is None
    assert v2.get_project("p").name == "P"
    v1.close()
    v2.close()
