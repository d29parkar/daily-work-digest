"""Build the evidence pack handed to the summarizer.

Every fact in the final report should be traceable back to a tagged evidence
item ([S1], [S2], ... for sessions/notes, [G1], ... for git state). The tags
are generated here and cited by the LLM, which is the backbone of the
hallucination safeguards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import DigestConfig
from .store import SourceRecord
from .text_utils import normalize_snippet


SOURCE_TYPE_LABELS = {
    "claude": "Claude Code session",
    "codex": "Codex session",
    "chatgpt": "ChatGPT export",
    "reviewer_note": "Reviewer note",
    "manual_note": "Manual note",
    "git_state": "Git state",
}


@dataclass
class EvidencePack:
    mode: str
    digest_date: str
    repo_names: list[str]
    primary_repo: str
    evidence_text: str
    tags: list[str] = field(default_factory=list)
    coverage_lines: list[str] = field(default_factory=list)
    appendix_lines: list[str] = field(default_factory=list)


def build_evidence_pack(
    *,
    config: DigestConfig,
    mode: str,
    digest_date: str,
    sources: list[SourceRecord],
    git_states: list[dict[str, Any]],
) -> EvidencePack:
    primary = config.primary_repo
    # Primary repo evidence first: the summarizer reads (and weights) top-down.
    sources = sorted(
        sources, key=lambda s: (primary not in (s.repo_names or []),)
    )
    git_states = sorted(
        git_states, key=lambda g: (g.get("repo_name") != primary,)
    )

    blocks: list[str] = []
    tags: list[str] = []
    appendix: list[str] = []

    for index, source in enumerate(sources, start=1):
        tag = f"S{index}"
        tags.append(tag)
        blocks.append(_render_source_block(tag, source, primary))
        appendix.append(_appendix_line_for_source(tag, source))

    for index, state in enumerate(git_states, start=1):
        tag = f"G{index}"
        tags.append(tag)
        blocks.append(_render_git_block(tag, state, primary))
        appendix.append(
            f"[{tag}] Git state of {state.get('repo_name', 'repo')} "
            f"(branch `{state.get('branch') or 'unknown'}`)"
        )

    evidence_text = "\n\n".join(blocks) if blocks else "(no evidence collected)"
    if len(evidence_text) > config.model.max_prompt_chars:
        evidence_text = (
            evidence_text[: config.model.max_prompt_chars]
            + "\n\n[...evidence truncated to fit the prompt budget...]"
        )

    return EvidencePack(
        mode=mode,
        digest_date=digest_date,
        repo_names=list(config.repos.keys()),
        primary_repo=primary,
        evidence_text=evidence_text,
        tags=tags,
        coverage_lines=build_coverage_lines(config, sources, git_states),
        appendix_lines=appendix,
    )


def build_coverage_lines(
    config: DigestConfig,
    sources: list[SourceRecord],
    git_states: list[dict[str, Any]],
) -> list[str]:
    """Factual source-coverage lines, always computed locally (never by the LLM)."""
    from collections import Counter

    counts = Counter(source.source_type for source in sources)
    lines = [
        f"- Claude Code sessions: {counts.get('claude', 0)} included "
        f"(lookback {config.claude_lookback_hours}h)",
        f"- Codex sessions: {counts.get('codex', 0)} included "
        f"(lookback {config.codex_lookback_hours}h)",
        f"- Notes: {counts.get('reviewer_note', 0) + counts.get('manual_note', 0)} included "
        f"(lookback {config.notes_lookback_hours}h)",
    ]
    for state in git_states:
        errors = state.get("errors") or []
        status = "ok" if not errors else f"issues: {'; '.join(errors[:2])}"
        lines.append(
            f"- Git ({state.get('repo_name', 'repo')}): "
            f"branch `{state.get('branch') or 'unknown'}`, {status}"
        )
    lines.append(
        "- Gmail: "
        + ("enabled (read-only stub; see docs/GOOGLE_INTEGRATIONS.md)"
           if config.integrations.gmail_enabled
           else "disabled (integrations.gmail.enabled: false)")
    )
    lines.append(
        "- Google Calendar: "
        + ("enabled (read-only stub; see docs/GOOGLE_INTEGRATIONS.md)"
           if config.integrations.calendar_enabled
           else "disabled (integrations.calendar.enabled: false)")
    )
    lines.append("- Trello: not integrated; markdown task files under notes/ are ingested instead")

    missing = [p for p in config.claude_paths + config.codex_paths if not p.exists()]
    for path in missing:
        lines.append(f"- Missing input path: {path}")
    for name, path in config.repos.items():
        if not path.exists():
            lines.append(f"- Missing repo path ({name}): {path}")
    return lines


def _appendix_line_for_source(tag: str, source: SourceRecord) -> str:
    label = SOURCE_TYPE_LABELS.get(source.source_type, source.source_type)
    name = source.path.replace("\\", "/").rsplit("/", 1)[-1]
    modified = str(source.modified_at)[:16].replace("T", " ")
    return f"[{tag}] {label} `{name}` ({modified}, {', '.join(source.repo_names)})"


def _render_source_block(tag: str, source: SourceRecord, primary_repo: str = "") -> str:
    summary = source.summary or {}
    label = SOURCE_TYPE_LABELS.get(source.source_type, source.source_type)
    name = source.path.replace("\\", "/").rsplit("/", 1)[-1]
    repo_bits = [
        f"{repo} (PRIMARY)" if repo == primary_repo else repo
        for repo in source.repo_names
    ]
    lines = [
        f"### [{tag}] {label}: {name}",
        f"Modified: {source.modified_at} | Repos: {', '.join(repo_bits)}",
    ]
    for key, heading in (
        ("user_requests", "User requests"),
        ("actions", "Actions taken"),
        ("commands", "Commands run"),
        ("issues", "Errors / issues mentioned"),
        ("reviewer_comments", "Reviewer comments"),
    ):
        items = [normalize_snippet(str(item)) for item in summary.get(key, []) if str(item).strip()]
        if items:
            lines.append(f"{heading}:")
            lines.extend(f"- {item}" for item in items)
    keywords = summary.get("keywords") or []
    if keywords:
        lines.append("Keywords: " + ", ".join(str(k) for k in keywords))
    return "\n".join(lines)


def _render_git_block(tag: str, state: dict[str, Any], primary_repo: str = "") -> str:
    repo_name = state.get("repo_name", "repo")
    role = "PRIMARY working repo" if repo_name == primary_repo else "secondary repo"
    lines = [
        f"### [{tag}] Git state: {repo_name} ({role})",
        f"Branch: {state.get('branch') or 'unknown'} | Status: {state.get('status_summary') or 'clean'}",
    ]
    commits = state.get("recent_commits") or []
    if commits:
        lines.append("Recent commits:")
        lines.extend(f"- {commit}" for commit in commits[:10])
    changed = state.get("changed_files") or []
    if changed:
        lines.append("Uncommitted changed files:")
        lines.extend(f"- {path}" for path in changed[:25])
        if len(changed) > 25:
            lines.append(f"- ...and {len(changed) - 25} more")
    if state.get("diff_stat"):
        lines.append("Diff stat (working tree):")
        lines.extend(f"- {normalize_snippet(l)}" for l in str(state["diff_stat"]).splitlines()[:10])
    if state.get("cached_diff_stat"):
        lines.append("Diff stat (staged):")
        lines.extend(
            f"- {normalize_snippet(l)}" for l in str(state["cached_diff_stat"]).splitlines()[:10]
        )
    for error in state.get("errors") or []:
        lines.append(f"Collection issue: {error}")
    return "\n".join(lines)
