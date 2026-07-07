from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from .text_utils import dedupe_keep_order, normalize_snippet


REQUEST_RE = re.compile(
    r"\b(user:|my request|asked|please|can you|could you|need to|build|implement|fix|add|create)\b",
    re.IGNORECASE,
)
ACTION_RE = re.compile(
    r"\b(updated|created|added|fixed|implemented|changed|patched|generated|completed|wrote|refactored|renamed)\b",
    re.IGNORECASE,
)
COMMAND_RE = re.compile(
    r"\b(pytest|ruff|mypy|npm|pnpm|yarn|git|uvicorn|make|python -m|alembic|yoyo)\b",
    re.IGNORECASE,
)
ISSUE_RE = re.compile(
    r"\b(error|failed|failure|failing|blocked|blocker|unresolved|todo|warning|exception|not run|skipped|permission denied)\b",
    re.IGNORECASE,
)
REVIEWER_RE = re.compile(r"\b(reviewer|review comment|feedback|pr comment)\b", re.IGNORECASE)


def summarize_text_source(
    *,
    text: str,
    source_type: str,
    path: Path,
    repo_names: list[str],
    max_items: int,
) -> dict[str, Any]:
    lines = _signal_lines(text)
    return {
        "title": _title_for_source(source_type, path),
        "repo_names": repo_names,
        "user_requests": _matching(lines, REQUEST_RE, max_items),
        "actions": _matching(lines, ACTION_RE, max_items),
        "commands": _matching(lines, COMMAND_RE, max_items),
        "issues": _matching(lines, ISSUE_RE, max_items),
        "reviewer_comments": _matching(lines, REVIEWER_RE, max_items),
        "keywords": _keywords(text, repo_names),
    }


def summarize_git_state(git_state: dict[str, Any]) -> dict[str, Any]:
    repo_name = str(git_state.get("repo_name", "repo"))
    changed = git_state.get("changed_files", [])
    commits = git_state.get("recent_commits", [])
    errors = git_state.get("errors", [])
    actions = []
    if commits:
        actions.append(f"{repo_name}: {len(commits)} recent commit(s) found.")
    if changed:
        actions.append(f"{repo_name}: {len(changed)} changed file(s) in git status.")
    if not commits and not changed and not errors:
        actions.append(f"{repo_name}: no recent commits or local changes found.")

    return {
        "title": f"Git state for {repo_name}",
        "repo_names": [repo_name],
        "user_requests": [],
        "actions": actions,
        "commands": ["git status", "git log", "git diff --stat"],
        "issues": errors,
        "reviewer_comments": [],
        "keywords": [],
    }


def _signal_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        clean = normalize_snippet(raw_line)
        if not clean:
            continue
        if len(clean) < 5:
            continue
        if _is_low_signal_line(clean):
            continue
        lines.append(clean)
    return lines


def _matching(lines: list[str], pattern: re.Pattern[str], limit: int) -> list[str]:
    matches = [line for line in lines if pattern.search(line)]
    return dedupe_keep_order(matches, limit)


def _is_low_signal_line(line: str) -> bool:
    lowered = line.lower()
    prefixes = (
        "you are codex",
        "knowledge cutoff",
        "current date",
        "when making a hero page",
        "- when making a hero page",
        "websites and games must use",
        "- websites and games must use",
        "use lucide icons",
        "never use destructive",
        "do not use visible",
        "avoid nested bullets",
        "source_id:",
        "skipped:",
        "skipped=true",
        "skipped=false",
        "if generated.",
        'f"skipped;',
        "throw ",
        "print(f",
        "st.warning(",
        "logger.warning(",
        "else ",
        "def ",
        "class ",
        "return ",
        "from .",
        "import ",
        "@dataclass",
    )
    if lowered.startswith(prefixes):
        return True
    if lowered.startswith(("+ ", "+", "- ")) and any(
        token in lowered
        for token in (
            "def ",
            "class ",
            "return ",
            "print(",
            "warning(",
            "skipped",
            "logger.warning",
            "throw ",
            "else ",
        )
    ):
        return True
    if "skill's standing warning" in lowered:
        return True
    if "developer instructions" in lowered:
        return True
    if "nothing blocked" in lowered:
        return True
    if lowered in {'"error",', '"error"', "success/error", "* failure modes"}:
        return True
    if lowered.startswith(("## ", "i'll ", "- simplify:")):
        return True
    if "pr-review-and-ship:" in lowered:
        return True
    if "use when handling pr review comments" in lowered:
        return True
    return False


def _keywords(text: str, repo_names: list[str]) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", text.lower())
    stop = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "were",
        "will",
        "your",
        "codex",
        "claude",
        "assistant",
        "user",
        "daily",
        "digest",
        "daily-work-digest",
        "desktop",
        "users",
        "onedrive",
        "projects",
        "config",
        "scripts",
    }
    stop.update(repo.lower() for repo in repo_names)
    # Filter the user's own home-directory parts (e.g. their username) out of
    # the keyword themes.
    stop.update(part.lower().strip("\\/") for part in Path.home().parts)
    counts = Counter(word for word in words if word not in stop)
    return [word for word, _ in counts.most_common(8)]


def _title_for_source(source_type: str, path: Path) -> str:
    name = path.name or str(path)
    return f"{source_type}: {name}"
