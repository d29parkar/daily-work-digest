from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from digest.ingest_git import collect_git_state


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "a.txt").write_text("hello", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def test_collect_git_state_reads_branch_and_commits(git_repo):
    state = collect_git_state("test-repo", git_repo, lookback_hours=48)
    assert state["branch"] == "main"
    assert state["errors"] == []
    assert any("initial commit" in c for c in state["recent_commits"])
    assert state["changed_files"] == []


def test_collect_git_state_sees_uncommitted_changes(git_repo):
    (git_repo / "a.txt").write_text("changed", encoding="utf-8")
    (git_repo / "new.txt").write_text("new", encoding="utf-8")
    state = collect_git_state("test-repo", git_repo, lookback_hours=48)
    assert len(state["changed_files"]) == 2
    assert state["diff_stat"]


def test_missing_repo_path_reports_error_not_crash(tmp_path):
    state = collect_git_state("gone", tmp_path / "nope", lookback_hours=48)
    assert state["errors"]
    assert state["branch"] is None
