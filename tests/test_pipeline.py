from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from conftest import write_config
from digest.config import load_config
from digest.pipeline import run_pipeline
from digest.store_v2 import WorkStore
from test_harvest import _claude_lines, _write_jsonl

TODAY = date.today().isoformat()


def _fresh_claude_lines() -> list[dict]:
    """The synthetic session, re-timestamped to today so it lands in today's
    work window regardless of when the test runs."""
    lines = _claude_lines()
    now = datetime.now().astimezone()
    for i, line in enumerate(lines):
        if "timestamp" in line:
            stamp = now.replace(hour=9, minute=0, second=0, microsecond=0)
            line["timestamp"] = stamp.isoformat().replace("+00:00", "Z")
    return lines


def _setup(tmp_path: Path, *, provider: str) -> tuple:
    claude_root = tmp_path / "claude"
    project_dir = claude_root / "c--projects-widget"
    project_dir.mkdir(parents=True)
    _write_jsonl(
        project_dir / "11111111-2222-3333-4444-555555555555.jsonl",
        _fresh_claude_lines(),
    )
    repo = tmp_path / "repo-fastapi"
    repo.mkdir()

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "extract.json").write_text(
        json.dumps(
            {
                "work_units": [
                    {
                        "turns": [1],
                        "intent": "Fix the widget crash on save",
                        "kind": "debugging",
                        "incidental": False,
                        "outcome_claim": "Edited src/widget.py; pytest failed once",
                        "status_claim": "in_progress",
                        "entities": ["save"],
                        "claims_to_verify": [],
                        "open_questions": [],
                        "user_corrections": [],
                    },
                    {
                        "turns": [2],
                        "intent": "Add a regression test for the crash",
                        "kind": "feature",
                        "incidental": False,
                        "outcome_claim": "",
                        "status_claim": "in_progress",
                        "entities": [],
                        "claims_to_verify": [],
                        "open_questions": [],
                        "user_corrections": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (fixtures / "state.json").write_text(
        json.dumps(
            {
                "goal": "Ship a crash-free widget",
                "goal_changed": False,
                "system_state": "Crash fix in progress, regression test pending.",
                "narrative_delta": "Today moved the widget crash from unreproduced to a guarded save() with a failing test still open.",
                "open_threads": [
                    {"text": "make the failing pytest pass", "since": TODAY, "status": "open"}
                ],
                "evidence": {"narrative_delta": ["unit:claude:11111111-2222-3333-4444-555555555555:1"]},
            }
        ),
        encoding="utf-8",
    )
    (fixtures / "trello.md").write_text(
        "Bigger picture: the widget save path is being hardened.\n\n"
        "Today's Update\n- I completed a first pass on the `save()` crash fix.",
        encoding="utf-8",
    )

    extra = f"models:\n  provider: {provider}\n  fixture_dir: {fixtures}"
    config = load_config(write_config(tmp_path, extra=extra, repos={"my-api-repo": repo}))
    return config, tmp_path / "digest.sqlite"


def test_night_pipeline_end_to_end_with_fixture_llm(tmp_path: Path):
    config, db = _setup(tmp_path, provider="fixture")
    result = run_pipeline(config, "night")

    assert result.stage_status["harvest"].startswith("ok")
    assert result.stage_status["extract"].startswith("ok: 1 extracted")
    assert result.stage_status["state"].startswith("ok: 1 project(s) updated")
    assert result.stage_status["render"] == "ok"

    text = result.markdown
    assert "# End-of-Day Work Digest" in text
    assert "Ship a crash-free widget" in text
    assert "Fix the widget crash on save" in text
    # provisional project detected from unknown cwd, surfaced for confirmation
    assert "digest projects confirm" in text

    store = WorkStore(db)
    try:
        units = store.list_units_for_date(TODAY)
        assert len(units) == 2
        assert all(u.project_id == "widget" for u in units)
        assert store.get_project("widget").status == "provisional"
        head = store.get_head_state("widget")
        assert head.goal == "Ship a crash-free widget"
    finally:
        store.close()

    # Rerun: idempotent (units replaced, not duplicated; same state version).
    result2 = run_pipeline(config, "night")
    store = WorkStore(db)
    try:
        assert len(store.list_units_for_date(TODAY)) == 2
        assert store.get_head_state("widget").version == 1
    finally:
        store.close()
    assert "reused" in result2.stage_status["extract"]


def test_pipeline_degrades_without_any_llm(tmp_path: Path):
    config, db = _setup(tmp_path, provider="none")
    result = run_pipeline(config, "night")

    assert "stubbed" in result.stage_status["extract"]
    assert result.stage_status["render"] == "ok"
    text = result.markdown
    assert "Run notes" in text
    assert "activity only" in text.lower()
    # No state was invented without an LLM.
    store = WorkStore(db)
    try:
        assert store.get_head_state("widget") is None
    finally:
        store.close()


def test_morning_and_trello_render_from_night_state(tmp_path: Path):
    config, db = _setup(tmp_path, provider="fixture")
    run_pipeline(config, "night")

    morning = run_pipeline(config, "morning")
    assert "# Daily Work Brief" in morning.markdown
    assert "Ship a crash-free widget" in morning.markdown
    assert "make the failing pytest pass" in morning.markdown

    trello = run_pipeline(config, "trello")
    assert "Today's Update" in trello.markdown
    assert "RAW FALLBACK" not in trello.markdown


def test_quiet_day_pipeline(tmp_path: Path):
    config, _ = _setup(tmp_path, provider="fixture")
    # Point claude at an empty directory: no sessions at all.
    empty = tmp_path / "empty"
    empty.mkdir()
    config2 = load_config(
        write_config(
            tmp_path,
            claude_paths=[empty],
            codex_paths=[empty],
            extra="models:\n  provider: none",
        )
    )
    result = run_pipeline(config2, "night")
    assert "Quiet day" in result.markdown
