from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import write_config
from digest.config import load_config
from digest.llm_v2 import NoneLLM
from digest.state_update import export_registry, update_project_states
from digest.store_v2 import WorkStore, WorkUnit
from test_extract import ScriptedLLM


@pytest.fixture()
def store(tmp_path: Path) -> WorkStore:
    s = WorkStore(tmp_path / "digest.sqlite")
    yield s
    s.close()


def _unit(project_id: str, verdict: str = "corroborated_by_commit") -> WorkUnit:
    return WorkUnit(
        unit_key="claude:abc:1", work_date="2026-07-12", project_id=project_id,
        turn_ids=["claude:abc:1:p1"], intent="Fix widget crash", kind="debugging",
        outcome_claim="Fixed", status_claim="done", files=["widget.py"],
        entities=[], claims=[], open_questions=[],
        verification={"verdict": verdict, "evidence": ["commit:abc123def456"], "notes": []},
    )


def _state_payload(**overrides) -> str:
    payload = {
        "goal": "Ship the widget",
        "goal_changed": False,
        "system_state": "Widget crash fixed and committed.",
        "narrative_delta": "Today moved the widget from crashing to fixed.",
        "open_threads": [{"text": "add regression test", "since": "2026-07-12", "status": "open"}],
        "evidence": {
            "narrative_delta": ["unit:claude:abc:1", "commit:abc123def456", "unit:HALLUCINATED"]
        },
    }
    payload.update(overrides)
    return json.dumps(payload)


def _setup(store: WorkStore, tmp_path: Path):
    config = load_config(write_config(tmp_path))
    store.create_project("widget", "Widget")
    unit = _unit("widget")
    store.replace_work_units("claude:abc", "2026-07-12", [unit])
    store.set_unit_project(unit.unit_key, "widget")
    return config


def test_state_update_writes_version_and_drops_bad_citations(store, tmp_path):
    config = _setup(store, tmp_path)
    llm = ScriptedLLM([_state_payload()])
    results = update_project_states(store, config, llm, "2026-07-12")
    assert results == {"widget": "updated"}
    head = store.get_head_state("widget")
    assert head.goal == "Ship the widget"
    assert head.open_threads[0]["text"] == "add regression test"
    # hallucinated citation was stripped, real ones kept
    assert head.evidence["narrative_delta"] == [
        "unit:claude:abc:1",
        "commit:abc123def456",
    ]
    # registry mirror exported
    mirror = config.pipeline.registry_export_path.read_text(encoding="utf-8")
    assert "Ship the widget" in mirror


def test_state_update_failure_leaves_registry_untouched(store, tmp_path):
    config = _setup(store, tmp_path)
    llm = ScriptedLLM(["not json", "still bad"])
    results = update_project_states(store, config, llm, "2026-07-12")
    assert results["widget"].startswith("skipped:")
    assert store.get_head_state("widget") is None


def test_state_update_no_provider_skips(store, tmp_path):
    config = _setup(store, tmp_path)
    results = update_project_states(store, config, NoneLLM(), "2026-07-12")
    assert results == {"widget": "skipped: no LLM provider"}


def test_state_prompt_carries_previous_state_and_verdicts(store, tmp_path):
    config = _setup(store, tmp_path)
    store.write_state_version(
        "widget", "2026-07-11",
        goal="Ship the widget", system_state="crashing",
        narrative_delta="found the bug",
        open_threads=[{"text": "fix crash", "since": "2026-07-10", "status": "open"}],
        evidence={}, written_by="test",
    )
    llm = ScriptedLLM([_state_payload()])
    update_project_states(store, config, llm, "2026-07-12")
    prompt = llm.prompts[0]
    assert "PREVIOUS STATE:" in prompt
    assert "fix crash (since 2026-07-10)" in prompt
    assert "verification: corroborated_by_commit" in prompt


def test_export_registry_lists_projects_without_state(store, tmp_path):
    store.create_project("empty", "Empty Project", status="provisional")
    path = tmp_path / "registry.md"
    export_registry(store, path)
    text = path.read_text(encoding="utf-8")
    assert "Empty Project" in text
    assert "provisional" in text
    assert "No state recorded yet" in text
