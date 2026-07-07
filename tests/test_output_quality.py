from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from conftest import write_config
from digest.cli import resolve_mode
from digest.config import load_config
from digest.context import build_evidence_pack
from digest.email_render import markdown_to_html


def _source(repos: list[str], path: str = "c:/s.jsonl") -> SimpleNamespace:
    return SimpleNamespace(
        source_id="x",
        source_type="claude",
        path=path,
        modified_at="2026-07-05T10:00:00",
        content_hash="h",
        repo_names=repos,
        summary={"actions": ["did a thing"]},
        included_at=None,
    )


def _git_state(repo: str) -> dict:
    return {
        "repo_name": repo,
        "repo_path": f"c:/{repo}",
        "branch": "main",
        "status_summary": "## main",
        "changed_files": [],
        "recent_commits": [],
        "diff_stat": "",
        "cached_diff_stat": "",
        "errors": [],
    }


def test_primary_repo_defaults_to_first_and_is_configurable(tmp_path):
    config = load_config(write_config(tmp_path))
    assert config.primary_repo == "my-api-repo"
    config2 = load_config(
        write_config(tmp_path, extra="primary_repo: my-app-repo")
    )
    assert config2.primary_repo == "my-app-repo"


def test_evidence_pack_puts_primary_repo_first_and_labels_it(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            repos={
                "my-api-repo": tmp_path / "a",
                "my-app-repo": tmp_path / "b",
            },
        )
    )
    pack = build_evidence_pack(
        config=config,
        mode="morning",
        digest_date="2026-07-06",
        sources=[
            _source(["my-app-repo"], "c:/app.jsonl"),
            _source(["my-api-repo"], "c:/fastapi.jsonl"),
        ],
        git_states=[
            _git_state("my-app-repo"),
            _git_state("my-api-repo"),
        ],
    )
    # Primary-repo evidence gets the first tags.
    assert "[S1] Claude Code session: fastapi.jsonl" in pack.evidence_text.split("[S2]")[0]
    assert "Git state: my-api-repo (PRIMARY working repo)" in pack.evidence_text
    assert "Git state: my-app-repo (secondary repo)" in pack.evidence_text
    # Appendix maps tags to human-readable descriptions.
    assert any("fastapi.jsonl" in line and line.startswith("[S1]") for line in pack.appendix_lines)
    assert any(line.startswith("[G1] Git state of my-api-repo") for line in pack.appendix_lines)


def test_resolve_mode_auto_by_time():
    assert resolve_mode("auto", datetime(2026, 7, 6, 8, 30)) == "morning"
    assert resolve_mode("auto", datetime(2026, 7, 6, 13, 59)) == "morning"
    assert resolve_mode("auto", datetime(2026, 7, 6, 14, 0)) == "night"
    assert resolve_mode("auto", datetime(2026, 7, 6, 21, 30)) == "night"
    assert resolve_mode("morning", datetime(2026, 7, 6, 22, 0)) == "morning"


def test_markdown_to_html_renders_email_structure():
    md = "\n".join(
        [
            "# Daily Work Brief - 2026-07-06",
            "",
            "## Top 3 tasks for today",
            "",
            "- Fix `phase_used` in `budget_utils.py` [G1] (observed)",
            "1. ordered item",
            "",
            "> quoted agent prompt",
            "",
            "---",
            "_footer note_",
        ]
    )
    html = markdown_to_html(md)
    assert "<h1>Daily Work Brief - 2026-07-06</h1>" in html
    assert "<h2>Top 3 tasks for today</h2>" in html
    assert "<li>Fix <code>phase_used</code> in <code>budget_utils.py</code> [G1] (observed)</li>" in html
    assert "<ol>" in html and "<ul>" in html
    assert "<blockquote>quoted agent prompt</blockquote>" in html
    assert "<hr>" in html
    assert "<p class='footnote'>footer note</p>" in html
    # Raw markdown syntax must not leak into the HTML body.
    assert "## " not in html


def test_markdown_to_html_escapes_html():
    html = markdown_to_html("- error was `<None>` & more")
    assert "<code>&lt;None&gt;</code>" in html
    assert "&amp; more" in html
