from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DigestConfig
from .store import DigestStore
from .summarize import summarize_git_state


def collect_git_states(config: DigestConfig, lookback_hours: int = 48) -> list[dict[str, Any]]:
    return [
        collect_git_state(repo_name, repo_path, lookback_hours)
        for repo_name, repo_path in config.repos.items()
    ]


def ingest_git(config: DigestConfig, store: DigestStore, lookback_hours: int = 48) -> int:
    changed_count = 0
    for state in collect_git_states(config, lookback_hours):
        summary = summarize_git_state(state)
        content = json.dumps(state, sort_keys=True)
        changed = store.upsert_source(
            source_type="git_state",
            path=str(state.get("repo_path", state.get("repo_name", "repo"))),
            modified_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            content_hash=_hash_text(content),
            repo_names=[str(state.get("repo_name", "repo"))],
            summary=summary,
        )
        if changed:
            changed_count += 1
    return changed_count


def collect_git_state(
    repo_name: str, repo_path: Path, lookback_hours: int = 48
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "repo_name": repo_name,
        "repo_path": str(repo_path),
        "branch": None,
        "status_summary": "",
        "changed_files": [],
        "recent_commits": [],
        "diff_stat": "",
        "cached_diff_stat": "",
        "errors": [],
    }

    if not repo_path.exists():
        state["errors"].append(f"Repo path does not exist: {repo_path}")
        return state

    branch = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch["ok"]:
        state["branch"] = branch["stdout"].strip()
    else:
        state["errors"].append(branch["stderr"] or branch["stdout"])

    status = _run_git(repo_path, ["status", "--short", "--branch"])
    if status["ok"]:
        status_lines = [line for line in status["stdout"].splitlines() if line.strip()]
        state["status_summary"] = status_lines[0] if status_lines else "clean"
        state["changed_files"] = _parse_changed_files(status_lines[1:])
    else:
        state["errors"].append(status["stderr"] or status["stdout"])

    commits = _run_git(
        repo_path,
        [
            "log",
            f"--since={lookback_hours} hours ago",
            "--oneline",
            "--decorate",
            "--max-count=8",
        ],
    )
    if commits["ok"] and commits["stdout"].strip():
        state["recent_commits"] = commits["stdout"].splitlines()
    else:
        fallback = _run_git(
            repo_path, ["log", "-5", "--oneline", "--decorate", "--max-count=5"]
        )
        if fallback["ok"]:
            state["recent_commits"] = fallback["stdout"].splitlines()
        elif fallback["stderr"]:
            state["errors"].append(fallback["stderr"])

    diff_stat = _run_git(repo_path, ["diff", "--stat"])
    if diff_stat["ok"]:
        state["diff_stat"] = diff_stat["stdout"].strip()

    cached_diff_stat = _run_git(repo_path, ["diff", "--cached", "--stat"])
    if cached_diff_stat["ok"]:
        state["cached_diff_stat"] = cached_diff_stat["stdout"].strip()

    return state


def _run_git(repo_path: Path, args: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "stdout": "", "stderr": str(exc)}

    return {
        "ok": completed.returncode == 0,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _parse_changed_files(status_lines: list[str]) -> list[str]:
    changed: list[str] = []
    for line in status_lines:
        if not line.strip():
            continue
        path = line[3:] if len(line) > 3 else line
        changed.append(path.strip())
    return changed


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
