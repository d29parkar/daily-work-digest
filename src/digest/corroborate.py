"""Stage S4: collect ground truth and grade transcript claims against it.

All deterministic code (DESIGN_V2.md 3.4): commit/file set intersection, not
LLM judgment. The model never grades its own claims; later stages receive
these verdicts as input.

Verdicts, strongest first:
- corroborated_by_commit: the unit's files appear in a commit in the window.
- applied_by_harness:     Codex patch_apply_end recorded the change (harness
                          ground truth, carried on the unit's turn tools).
- uncommitted:            the unit's files overlap the current dirty tree.
- unverified:             claims exist but nothing in the repo confirms them.
- contradicted:           the unit claims done, names files, and the repo
                          shows neither commits nor local changes to them.
- no_files:               nothing to check (discovery/review units).
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import DigestConfig
from .store_v2 import TurnRecord, WorkStore, WorkUnit
from .store_v2 import now_iso

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 20


# -- collectors ---------------------------------------------------------------


def collect_git_facts(
    store: WorkStore, config: DigestConfig, lookback_hours: int
) -> dict[str, Any]:
    """Per configured repo: commits (with file lists) and dirty status."""
    summary: dict[str, Any] = {"repos": {}, "errors": []}
    for repo_name, repo_path in config.repos.items():
        if not repo_path.exists():
            summary["errors"].append(f"{repo_name}: path missing ({repo_path})")
            continue
        commits = _collect_commits(repo_path, lookback_hours)
        status = _collect_status(repo_path)
        for commit in commits:
            fact_id = hashlib.sha256(
                f"commit|{repo_path}|{commit['sha']}".encode()
            ).hexdigest()[:32]
            store.upsert_fact(
                "git_facts_v2",
                fact_id,
                {
                    "repo_path": str(repo_path),
                    "kind": "commit",
                    "ref": commit["sha"],
                    "observed_at": now_iso(),
                    "data_json": json.dumps(commit),
                },
            )
        fact_id = hashlib.sha256(f"status|{repo_path}".encode()).hexdigest()[:32]
        store.upsert_fact(
            "git_facts_v2",
            fact_id,
            {
                "repo_path": str(repo_path),
                "kind": "status",
                "ref": status.get("branch"),
                "observed_at": now_iso(),
                "data_json": json.dumps(status),
            },
        )
        summary["repos"][repo_name] = {
            "commits": len(commits),
            "branch": status.get("branch"),
            "dirty_files": len(status.get("dirty_files", [])),
        }
        summary["errors"].extend(status.get("errors", []))
    return summary


def _collect_commits(repo_path: Path, lookback_hours: int) -> list[dict[str, Any]]:
    out = _run_git(
        repo_path,
        [
            "log",
            f"--since={lookback_hours} hours ago",
            "--name-only",
            "--format=%x1e%H%x1f%an%x1f%aI%x1f%s",
        ],
    )
    if out is None:
        return []
    commits: list[dict[str, Any]] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        header, _, files_blob = chunk.partition("\n")
        parts = header.split("\x1f")
        if len(parts) != 4:
            continue
        sha, author, authored_at, subject = parts
        files = [line.strip() for line in files_blob.splitlines() if line.strip()]
        commits.append(
            {
                "sha": sha,
                "author": author,
                "authored_at": authored_at,
                "subject": subject,
                "files": files,
            }
        )
    return commits


def _collect_status(repo_path: Path) -> dict[str, Any]:
    status: dict[str, Any] = {"branch": None, "dirty_files": [], "errors": []}
    branch = _run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch is not None:
        status["branch"] = branch.strip()
    else:
        status["errors"].append(f"{repo_path}: could not read branch")
    porcelain = _run_git(repo_path, ["status", "--porcelain"])
    if porcelain is not None:
        status["dirty_files"] = [
            line[3:].strip() for line in porcelain.splitlines() if len(line) > 3
        ]
    return status


def _run_git(repo_path: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("git %s failed in %s: %s", args[0], repo_path, exc)
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def collect_pr_facts(store: WorkStore, config: DigestConfig) -> dict[str, Any]:
    """Optional: PR facts via the gh CLI. The review-direction rule lives here
    in code: my review on someone else's PR is review_done; others' comments
    on my PR are inbound_feedback. Absent/unauthed gh -> source unavailable."""
    if shutil.which("gh") is None:
        return {"available": False, "reason": "gh CLI not installed"}
    login = _run_gh(["api", "user", "--jq", ".login"], cwd=None)
    if not login:
        return {"available": False, "reason": "gh CLI not authenticated"}
    login = login.strip()

    counts = {"review_done": 0, "inbound_feedback": 0}
    for repo_name, repo_path in config.repos.items():
        if not repo_path.exists():
            continue
        reviewed = _run_gh(
            [
                "search", "prs",
                "--reviewed-by", login,
                "--repo", repo_name,
                "--json", "number,title,author,updatedAt",
                "--limit", "20",
            ],
            cwd=repo_path,
        )
        for pr in _parse_json_list(reviewed):
            author = str((pr.get("author") or {}).get("login", ""))
            if author and author != login:
                _store_pr_fact(store, repo_name, pr, login, "review_done")
                counts["review_done"] += 1
        mine = _run_gh(
            [
                "pr", "list",
                "--author", login,
                "--json", "number,title,reviews,comments,updatedAt",
                "--limit", "20",
            ],
            cwd=repo_path,
        )
        for pr in _parse_json_list(mine):
            others = [
                r
                for r in (pr.get("reviews") or []) + (pr.get("comments") or [])
                if str((r.get("author") or {}).get("login", "")) not in ("", login)
            ]
            if others:
                _store_pr_fact(store, repo_name, pr, login, "inbound_feedback")
                counts["inbound_feedback"] += 1
    return {"available": True, "login": login, **counts}


def _store_pr_fact(
    store: WorkStore, repo: str, pr: dict[str, Any], login: str, kind: str
) -> None:
    number = int(pr.get("number") or 0)
    fact_id = hashlib.sha256(f"pr|{repo}|{number}|{kind}".encode()).hexdigest()[:32]
    store.upsert_fact(
        "pr_facts_v2",
        fact_id,
        {
            "repo": repo,
            "pr_number": number,
            "author": str((pr.get("author") or {}).get("login", "")) or login,
            "my_role": "reviewer" if kind == "review_done" else "author",
            "kind": kind,
            "observed_at": now_iso(),
            "data_json": json.dumps(pr),
        },
    )


def _run_gh(args: list[str], cwd: Path | None) -> str | None:
    try:
        completed = subprocess.run(
            ["gh", *args],
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _parse_json_list(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


# -- verdicts -------------------------------------------------------------------


def corroborate_units(store: WorkStore, units: list[WorkUnit]) -> None:
    commit_facts = store.list_git_facts("commit")
    status_facts = store.list_git_facts("status")
    commit_files: list[tuple[str, str]] = []  # (normalized file, sha)
    for fact in commit_facts:
        for file in fact["data"].get("files", []):
            commit_files.append((_norm(file), fact["data"].get("sha", "")[:12]))
    dirty_files = {
        _norm(file)
        for fact in status_facts
        for file in fact["data"].get("dirty_files", [])
    }

    for unit in units:
        turns = _unit_turns(store, unit)
        verification = _verdict_for(unit, turns, commit_files, dirty_files)
        store.set_unit_verification(unit.unit_key, verification)


def _unit_turns(store: WorkStore, unit: WorkUnit) -> list[TurnRecord]:
    session_id = unit.unit_key.rsplit(":", 1)[0]
    wanted = set(unit.turn_ids)
    return [t for t in store.list_turns(session_id) if t.turn_id in wanted]


def _verdict_for(
    unit: WorkUnit,
    turns: list[TurnRecord],
    commit_files: list[tuple[str, str]],
    dirty_files: set[str],
) -> dict[str, Any]:
    evidence: list[str] = []
    notes: list[str] = []
    unit_files = [_norm(f) for f in unit.files]

    harness_files = {
        _norm(f)
        for turn in turns
        if any(t.get("harness_verified") and t.get("ok") for t in turn.tools)
        for f in turn.files
    }

    matched_commits = {
        sha
        for unit_file in unit_files
        for commit_file, sha in commit_files
        if _same_file(unit_file, commit_file)
    }
    matched_dirty = {
        unit_file
        for unit_file in unit_files
        if any(_same_file(unit_file, dirty) for dirty in dirty_files)
    }

    ran_tests = any(
        "pytest" in str(tool.get("detail", "")).lower()
        or "test" in str(tool.get("detail", "")).lower().split("/")[-1]
        for turn in turns
        for tool in turn.tools
        if tool.get("name") in {"Bash", "shell_command", "PowerShell"}
    )
    for claim in unit.claims:
        if claim.get("type") == "tests_pass" and not ran_tests:
            notes.append("claims tests pass but no test command appears in the turns")

    if matched_commits:
        verdict = "corroborated_by_commit"
        evidence.extend(f"commit:{sha}" for sha in sorted(matched_commits))
    elif harness_files:
        verdict = "applied_by_harness"
        evidence.extend(f"patch:{Path(f).name}" for f in sorted(harness_files)[:5])
    elif matched_dirty:
        verdict = "uncommitted"
        evidence.extend(f"git_status:dirty:{Path(f).name}" for f in sorted(matched_dirty)[:5])
    elif not unit_files:
        verdict = "no_files"
    elif unit.status_claim == "done":
        verdict = "contradicted"
        notes.append("claims done, but named files show no commits or local changes")
    else:
        verdict = "unverified"

    return {"verdict": verdict, "evidence": evidence, "notes": notes}


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower().lstrip("./")


def _same_file(a: str, b: str) -> bool:
    """Compare a possibly-absolute path with a repo-relative one."""
    if a == b:
        return True
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    return longer.endswith("/" + shorter)
