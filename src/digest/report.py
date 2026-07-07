"""Assemble the final report markdown: header + summarizer body + coverage.

The "Source coverage and missing inputs" section is always computed locally
from what was actually ingested, so it stays factual even if the LLM body is
imperfect.
"""

from __future__ import annotations

from typing import Any

from .config import DigestConfig
from .context import EvidencePack, build_evidence_pack
from .llm import ReportInputs, resolve_summarizer, summarize_with_fallback
from .store import SourceRecord

MODE_TITLES = {
    "morning": "Daily Work Brief",
    "night": "End-of-Day Work Digest",
    "trello": "Trello Update",
}


def render_report(
    *,
    config: DigestConfig,
    mode: str,
    digest_date: str,
    generated_at: str,
    sources: list[SourceRecord],
    git_states: list[dict[str, Any]],
) -> tuple[str, str]:
    """Build the full report markdown. Returns (markdown, provider_label)."""
    # Keep source/git ordering identical between the evidence pack and the
    # fallback inputs so [S#]/[G#] tags always match the Sources appendix.
    primary = config.primary_repo
    sources = sorted(sources, key=lambda s: primary not in (s.repo_names or []))
    git_states = sorted(git_states, key=lambda g: g.get("repo_name") != primary)

    pack: EvidencePack = build_evidence_pack(
        config=config,
        mode=mode,
        digest_date=digest_date,
        sources=sources,
        git_states=git_states,
    )
    inputs = ReportInputs(
        mode=mode,
        digest_date=digest_date,
        pack=pack,
        sources=sources,
        git_states=git_states,
    )

    summarizer, notes = resolve_summarizer(config)
    body, provider_label, fallback_notes = summarize_with_fallback(summarizer, inputs)
    notes.extend(fallback_notes)

    title = MODE_TITLES.get(mode, "Work Digest")
    lines: list[str] = [
        f"# {title} - {digest_date}",
        "",
        f"Generated: {generated_at} | Mode: {mode} | Summarizer: {provider_label}",
        "",
    ]
    if notes:
        lines.append("> **Run notes:**")
        lines.extend(f"> - {note}" for note in notes)
        lines.append("")
    lines.append(body)
    if pack.appendix_lines:
        lines.extend(
            [
                "",
                "## Sources",
                "",
                *[f"- {line}" for line in pack.appendix_lines],
            ]
        )
    lines.extend(
        [
            "",
            "## Source coverage and missing inputs",
            "",
            *pack.coverage_lines,
            "",
            "---",
            "_(observed) = stated directly in a transcript, note, or git. "
            "(inferred) = a deduction, not a fact. Citations like [S1]/[G1] map to "
            "the Sources list above. Treat anything under 'Needs verification' as "
            "unconfirmed._",
            "",
        ]
    )
    return "\n".join(lines), provider_label
