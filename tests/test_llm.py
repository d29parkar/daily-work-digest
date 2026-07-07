from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from conftest import write_config
from digest.config import load_config
from digest.context import EvidencePack
from digest.llm import (
    LocalRulesSummarizer,
    MORNING_SECTIONS,
    NIGHT_SECTIONS,
    OpenAISummarizer,
    ReportInputs,
    SummarizerError,
    resolve_summarizer,
    summarize_with_fallback,
)


def make_inputs(mode: str = "morning") -> ReportInputs:
    pack = EvidencePack(
        mode=mode,
        digest_date="2026-07-06",
        repo_names=["my-api-repo"],
        primary_repo="my-api-repo",
        evidence_text="### [S1] Claude Code session: s.jsonl\n- fixed the parser",
        tags=["S1"],
        coverage_lines=["- Claude Code sessions: 1 included"],
        appendix_lines=["[S1] Claude Code session `s.jsonl` (2026-07-05 10:00)"],
    )
    sources = [
        SimpleNamespace(
            source_id="s1",
            source_type="claude",
            path="c:/s.jsonl",
            modified_at="2026-07-05T10:00:00",
            content_hash="x",
            repo_names=["my-api-repo"],
            summary={
                "actions": ["fixed the parser"],
                "issues": ["pytest failed on test_upload"],
                "commands": ["git status"],
                "reviewer_comments": [],
                "user_requests": [],
                "keywords": [],
            },
            included_at=None,
        )
    ]
    git_states = [
        {
            "repo_name": "my-api-repo",
            "repo_path": "c:/repo",
            "branch": "main",
            "status_summary": "## main",
            "changed_files": ["app/main.py"],
            "recent_commits": ["abc1234 fix parser"],
            "diff_stat": "1 file changed",
            "cached_diff_stat": "",
            "errors": [],
        }
    ]
    return ReportInputs(
        mode=mode, digest_date="2026-07-06", pack=pack, sources=sources, git_states=git_states
    )


def _openai_response(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


class FakeHTTPResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_openai_summarizer_returns_valid_body(monkeypatch):
    body = "\n\n".join(f"## {s}\n- item [S1] (observed)" for s in MORNING_SECTIONS)
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.get_header("Authorization")
        return FakeHTTPResponse(_openai_response(body))

    monkeypatch.setattr("digest.llm.urllib.request.urlopen", fake_urlopen)
    summarizer = OpenAISummarizer(model="gpt-4o-mini", api_key="sk-test")
    result = summarizer.render_body(make_inputs("morning"))

    assert result == body
    assert captured["auth"] == "Bearer sk-test"
    assert captured["payload"]["model"] == "gpt-4o-mini"
    # Evidence must be embedded in the prompt (grounding).
    assert "fixed the parser" in captured["payload"]["messages"][1]["content"]
    # Grounding rules travel in the system prompt.
    assert "Never invent" in captured["payload"]["messages"][0]["content"]


def test_openai_summarizer_rejects_bodies_missing_sections(monkeypatch):
    def fake_urlopen(request, timeout=None):
        return FakeHTTPResponse(_openai_response("Great job! Everything is done."))

    monkeypatch.setattr("digest.llm.urllib.request.urlopen", fake_urlopen)
    summarizer = OpenAISummarizer(model="gpt-4o-mini", api_key="sk-test")
    with pytest.raises(SummarizerError, match="expected sections"):
        summarizer.render_body(make_inputs("morning"))


def test_fallback_to_local_rules_on_api_failure(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr("digest.llm.urllib.request.urlopen", fake_urlopen)
    summarizer = OpenAISummarizer(model="gpt-4o-mini", api_key="sk-test")
    body, label, notes = summarize_with_fallback(summarizer, make_inputs("morning"))
    assert label == "local_rules (no LLM)"
    assert notes and "failed" in notes[0].lower()
    assert "## Executive summary" in body


@pytest.mark.parametrize("mode,sections", [("morning", MORNING_SECTIONS), ("night", NIGHT_SECTIONS)])
def test_local_rules_produces_all_sections(mode, sections):
    body = LocalRulesSummarizer().render_body(make_inputs(mode))
    for section in sections:
        assert f"## {section}" in body
    # Grounding markers present.
    assert "(observed)" in body
    assert "[G1]" in body


def test_local_rules_trello_leads_with_bigger_picture():
    body = LocalRulesSummarizer().render_body(make_inputs("trello"))
    assert body.startswith("## Bigger picture")
    assert "## Today's Update" in body
    assert "`main`" in body  # branch from git evidence


def test_local_rules_reports_evidence_not_invention():
    body = LocalRulesSummarizer().render_body(make_inputs("night"))
    assert "abc1234 fix parser" in body  # from git evidence
    assert "pytest failed on test_upload" in body  # from session issues


def test_resolve_summarizer_auto_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = load_config(write_config(tmp_path, provider="auto"))
    summarizer, notes = resolve_summarizer(config)
    assert isinstance(summarizer, LocalRulesSummarizer)
    assert notes and "OPENAI_API_KEY" in notes[0]


def test_resolve_summarizer_auto_with_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    config = load_config(write_config(tmp_path, provider="auto"))
    summarizer, notes = resolve_summarizer(config)
    assert isinstance(summarizer, OpenAISummarizer)
    assert notes == []


def test_resolve_summarizer_fixture(tmp_path):
    fixture = tmp_path / "canned.md"
    fixture.write_text("## Executive summary\n- canned", encoding="utf-8")
    config = load_config(write_config(tmp_path, provider="fixture", fixture_path=str(fixture)))
    summarizer, _ = resolve_summarizer(config)
    assert summarizer.render_body(make_inputs()) == "## Executive summary\n- canned"
