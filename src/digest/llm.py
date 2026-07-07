"""Summarizer abstraction.

Providers:
- ``openai``:      calls the OpenAI Chat Completions API (stdlib HTTP, no SDK
                   dependency) using the model named in config.
- ``local_rules``: deterministic rule-based renderer; needs no network or key.
- ``fixture``:     returns the content of a file; used by tests and demos.
- ``auto``:        openai when the API key is set, otherwise local_rules.

Every provider returns the *body* of the report (the mode's sections). The
header and the locally-computed "Source coverage and missing inputs" section
are added by ``report.render_report`` so coverage facts never depend on the LLM.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DigestConfig
from .context import EvidencePack
from .store import SourceRecord

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

MORNING_SECTIONS = [
    "Executive summary",
    "Yesterday's completed work",
    "Open bugs / unresolved questions",
    "Decisions made",
    "What needs testing",
    "Trello-ready updates",
    "Prompts to send to coding agents",
    "Top 3 tasks for today",
    "People to follow up with",
]

NIGHT_SECTIONS = [
    "What changed today",
    "Work completed",
    "Work in progress",
    "Blockers",
    "Risky or untested changes",
    "Notes for tomorrow morning",
    "Suggested coding-agent prompts",
]

TRELLO_SECTIONS = [
    "Bigger picture",
    "Today's Update",
]

MODE_SECTIONS = {
    "morning": MORNING_SECTIONS,
    "night": NIGHT_SECTIONS,
    "trello": TRELLO_SECTIONS,
}

MODE_TEMPLATES = {
    "morning": "morning_brief.md",
    "night": "night_digest.md",
    "trello": "trello_update.md",
}


class SummarizerError(RuntimeError):
    pass


@dataclass
class ReportInputs:
    mode: str
    digest_date: str
    pack: EvidencePack
    sources: list[SourceRecord]
    git_states: list[dict[str, Any]]


class BaseSummarizer:
    name = "base"

    @property
    def model_label(self) -> str:
        return self.name

    def render_body(self, inputs: ReportInputs) -> str:
        raise NotImplementedError


class OpenAISummarizer(BaseSummarizer):
    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_seconds: int = 120,
        max_output_tokens: int = 2000,
    ):
        if not api_key:
            raise SummarizerError("OpenAI API key is empty.")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens

    @property
    def model_label(self) -> str:
        return f"openai:{self.model}"

    def render_body(self, inputs: ReportInputs) -> str:
        system = _load_prompt("system.md").format(
            repo_names=", ".join(inputs.pack.repo_names) or "(none configured)"
        )
        template = MODE_TEMPLATES.get(inputs.mode, "night_digest.md")
        user = _load_prompt(template).format(
            date=inputs.digest_date, evidence=inputs.pack.evidence_text
        )
        body = self._chat(system=system, user=user).strip()
        expected = MODE_SECTIONS.get(inputs.mode, NIGHT_SECTIONS)
        _validate_sections(body, expected)
        return body

    def _chat(self, *, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": self.max_output_tokens,
        }
        request = urllib.request.Request(
            OPENAI_CHAT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace"))
                detail = detail.get("error", {}).get("message", "")
            except Exception:
                pass
            raise SummarizerError(
                f"OpenAI API returned HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SummarizerError(f"Could not reach the OpenAI API: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SummarizerError(f"Unexpected OpenAI response shape: {exc}") from exc
        if not content or not content.strip():
            raise SummarizerError("OpenAI returned an empty completion.")
        return content


class FixtureSummarizer(BaseSummarizer):
    """Returns canned report bodies from a file; keeps tests offline."""

    name = "fixture"

    def __init__(self, fixture_path: Path):
        self.fixture_path = fixture_path

    @property
    def model_label(self) -> str:
        return f"fixture:{self.fixture_path.name}"

    def render_body(self, inputs: ReportInputs) -> str:
        if not self.fixture_path.exists():
            raise SummarizerError(f"Fixture file not found: {self.fixture_path}")
        return self.fixture_path.read_text(encoding="utf-8", errors="replace").strip()


class LocalRulesSummarizer(BaseSummarizer):
    """Deterministic fallback. Conservative by design: it only reports what the
    rule-based extraction observed, tags everything (observed), and leaves
    judgment-heavy sections explicitly empty rather than guessing."""

    name = "local_rules"

    @property
    def model_label(self) -> str:
        return "local_rules (no LLM)"

    def render_body(self, inputs: ReportInputs) -> str:
        if inputs.mode == "morning":
            return self._morning(inputs)
        if inputs.mode == "trello":
            return self._trello(inputs)
        return self._night(inputs)

    # -- shared extraction helpers -------------------------------------------------

    def _tagged_items(self, inputs: ReportInputs, key: str, limit: int) -> list[str]:
        items: list[str] = []
        for index, source in enumerate(inputs.sources, start=1):
            for item in (source.summary or {}).get(key, [])[:4]:
                text = str(item).strip()
                if text:
                    items.append(f"{text} [S{index}] (observed)")
            if len(items) >= limit:
                break
        return items[:limit]

    def _commits(self, inputs: ReportInputs, limit: int = 10) -> list[str]:
        items: list[str] = []
        for index, state in enumerate(inputs.git_states, start=1):
            for commit in (state.get("recent_commits") or [])[:5]:
                items.append(
                    f"{state.get('repo_name', 'repo')}: `{commit}` [G{index}] (observed)"
                )
        return items[:limit]

    def _dirty_repos(self, inputs: ReportInputs) -> list[tuple[int, dict[str, Any]]]:
        primary = inputs.pack.primary_repo
        dirty = [
            (index, state)
            for index, state in enumerate(inputs.git_states, start=1)
            if state.get("changed_files")
        ]
        return sorted(dirty, key=lambda item: item[1].get("repo_name") != primary)

    def _has_command(self, inputs: ReportInputs, command: str) -> bool:
        for source in inputs.sources:
            for item in (source.summary or {}).get("commands", []):
                if command in str(item).lower():
                    return True
        return False

    # -- section renderers ----------------------------------------------------------

    def _morning(self, inputs: ReportInputs) -> str:
        sections: list[tuple[str, list[str]]] = []

        summary_bits = []
        for index, state in enumerate(inputs.git_states, start=1):
            summary_bits.append(
                f"{state.get('repo_name', 'repo')} is on `{state.get('branch') or 'unknown'}` "
                f"with {len(state.get('changed_files') or [])} uncommitted file(s) and "
                f"{len(state.get('recent_commits') or [])} recent commit(s) [G{index}] (observed)"
            )
        if inputs.sources:
            summary_bits.append(
                f"{len(inputs.sources)} coding-agent session(s)/note(s) were ingested (observed)"
            )
        sections.append(("Executive summary", summary_bits))

        sections.append(
            ("Yesterday's completed work", self._commits(inputs) + self._tagged_items(inputs, "actions", 8))
        )
        sections.append(("Open bugs / unresolved questions", self._tagged_items(inputs, "issues", 10)))
        sections.append(
            (
                "Decisions made",
                [
                    "The local rule-based summarizer cannot reliably extract decisions; "
                    "set OPENAI_API_KEY to enable this section. (inferred)"
                ]
                if inputs.sources
                else [],
            )
        )

        testing = []
        for index, state in self._dirty_repos(inputs):
            testing.append(
                f"Uncommitted changes in {state.get('repo_name')} have no recorded test run "
                f"[G{index}] (observed)"
            )
        if inputs.sources and not self._has_command(inputs, "pytest"):
            testing.append("No pytest run found in ingested sessions (observed)")
        sections.append(("What needs testing", testing))

        trello = [
            f"Today's Update: I'm continuing work on `{state.get('branch') or 'unknown'}` "
            f"in {state.get('repo_name')}, {len(state.get('changed_files') or [])} file(s) "
            f"uncommitted. Next step is to review and either commit or park them. [G{index}]"
            for index, state in self._dirty_repos(inputs)
        ]
        sections.append(("Trello-ready updates", trello))

        prompts = [
            f'"There are uncommitted changes in {state.get("repo_name")} on '
            f'`{state.get("branch") or "unknown"}`. Your job is to review them. '
            f"Inspect `git status` and the diff first. Come back with: "
            f"A. what is safe to commit B. what still needs tests C. anything risky. "
            f'Do not propose broad rewrites. Be concrete and codebase-specific." [G{index}]'
            for index, state in self._dirty_repos(inputs)
        ]
        issues = self._tagged_items(inputs, "issues", 2)
        prompts.extend(
            f'"Your job is to investigate and fix: {item.rsplit(" [", 1)[0]} '
            f"Inspect the relevant code first and come back with the root cause, "
            f'exact file paths and line numbers, and the smallest reliable fix."'
            for item in issues
        )
        sections.append(("Prompts to send to coding agents", prompts))

        tasks = []
        for index, state in self._dirty_repos(inputs)[:2]:
            tasks.append(
                f"Commit or park the uncommitted work in {state.get('repo_name')} [G{index}]"
            )
        if issues:
            tasks.append(f"Resolve: {issues[0].rsplit(' [', 1)[0]}")
        if not self._has_command(inputs, "pytest") and inputs.sources:
            tasks.append("Run the test suite for the repos touched yesterday")
        sections.append(("Top 3 tasks for today", tasks[:3]))

        people = self._tagged_items(inputs, "reviewer_comments", 5)
        sections.append(("People to follow up with", people))

        needs = []
        if inputs.sources:
            needs.append(
                "Rule-based extraction can miss context; skim the cited sessions for anything "
                "the pattern matching missed. (inferred)"
            )
        sections.append(("Needs verification", needs))

        return _render_sections(sections)

    def _trello(self, inputs: ReportInputs) -> str:
        primary = inputs.pack.primary_repo
        primary_state = next(
            (s for s in inputs.git_states if s.get("repo_name") == primary),
            inputs.git_states[0] if inputs.git_states else {},
        )
        branch = primary_state.get("branch") or "unknown"
        commits = primary_state.get("recent_commits") or []
        changed = primary_state.get("changed_files") or []

        bigger = [
            f"Work continues on `{branch}` in {primary or 'the main repo'}: "
            f"{len(commits)} recent commit(s), {len(changed)} file(s) currently "
            f"uncommitted. (observed)"
        ]
        if inputs.sources:
            bigger.append(
                f"{len(inputs.sources)} coding-agent session(s) today fed this update; "
                "the rule-based summarizer cannot judge the overall direction, so skim "
                "the newest session before pasting. (inferred)"
            )

        card = []
        if commits:
            subject = commits[0].split(" ", 1)[-1] if commits else ""
            card.append(f"I completed work landed as `{subject}` on `{branch}`.")
        if changed:
            card.append(
                f"I'm now moving toward finishing the {len(changed)} uncommitted "
                f"file(s) on `{branch}`."
            )
        issues = self._tagged_items(inputs, "issues", 1)
        if issues:
            card.append(f"Blocker / open question: {issues[0].rsplit(' [', 1)[0]}")

        return _render_sections(
            [("Bigger picture", bigger), ("Today's Update", card)]
        )

    def _night(self, inputs: ReportInputs) -> str:
        sections: list[tuple[str, list[str]]] = []

        changed = []
        for index, state in enumerate(inputs.git_states, start=1):
            changed.append(
                f"{state.get('repo_name', 'repo')}: branch `{state.get('branch') or 'unknown'}`, "
                f"{len(state.get('changed_files') or [])} uncommitted file(s), "
                f"{len(state.get('recent_commits') or [])} recent commit(s) [G{index}] (observed)"
            )
        sections.append(("What changed today", changed))

        sections.append(("Work completed", self._commits(inputs)))

        wip = []
        for index, state in self._dirty_repos(inputs):
            files = state.get("changed_files") or []
            preview = ", ".join(f"`{f}`" for f in files[:5])
            more = f" and {len(files) - 5} more" if len(files) > 5 else ""
            wip.append(
                f"{state.get('repo_name')}: uncommitted: {preview}{more} [G{index}] (observed)"
            )
        wip.extend(self._tagged_items(inputs, "actions", 5))
        sections.append(("Work in progress", wip))

        sections.append(("Blockers", self._tagged_items(inputs, "issues", 10)))

        risky = []
        for index, state in self._dirty_repos(inputs):
            risky.append(
                f"Uncommitted changes in {state.get('repo_name')} with no recorded test run "
                f"[G{index}] (observed)"
            )
        sections.append(("Risky or untested changes", risky))

        notes = [
            f"Resume from `{state.get('branch') or 'unknown'}` in {state.get('repo_name')}; "
            f"start with `git status` [G{index}]"
            for index, state in self._dirty_repos(inputs)
        ]
        sections.append(("Notes for tomorrow morning", notes))

        prompts = [
            f'"Your job is to summarize the current diff in {state.get("repo_name")} '
            f'(branch `{state.get("branch") or "unknown"}`) and propose a commit plan. '
            f"Inspect `git status` and `git diff` first. Come back with: "
            f"A. scoped commits with messages B. what needs tests before committing. "
            f'Be concrete and codebase-specific." [G{index}]'
            for index, state in self._dirty_repos(inputs)
        ]
        sections.append(("Suggested coding-agent prompts", prompts))

        needs = []
        if inputs.sources:
            needs.append(
                "Rule-based extraction can miss context; skim the cited sessions for anything "
                "the pattern matching missed. (inferred)"
            )
        sections.append(("Needs verification", needs))

        return _render_sections(sections)


def _render_sections(sections: list[tuple[str, list[str]]]) -> str:
    lines: list[str] = []
    for title, items in sections:
        if title == "Needs verification" and not items:
            continue
        lines.append(f"## {title}")
        lines.append("")
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- Nothing found in available sources.")
        lines.append("")
    return "\n".join(lines).strip()


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def _validate_sections(body: str, expected: list[str]) -> None:
    lowered = body.lower()
    found = sum(1 for section in expected if f"## {section.lower()}" in lowered)
    # Short section lists (e.g. trello) must be complete; long ones tolerate a
    # few merged/missing sections before we call the output ungrounded.
    minimum = min(len(expected), max(3, len(expected) - 3))
    if found < minimum:
        raise SummarizerError(
            f"LLM response only contained {found}/{len(expected)} expected sections; "
            "treating it as ungrounded and falling back."
        )


def resolve_summarizer(config: DigestConfig) -> tuple[BaseSummarizer, list[str]]:
    """Pick the summarizer for this run. Returns (summarizer, notes) where notes
    explain any downgrade and are surfaced in the report header."""
    notes: list[str] = []
    provider = config.model.provider
    api_key = os.environ.get(config.model.api_key_env, "")

    if provider == "fixture":
        fixture = Path(config.model.fixture_path)
        if not fixture.is_absolute():
            fixture = config.project_root / fixture
        return FixtureSummarizer(fixture), notes

    if provider == "local_rules":
        return LocalRulesSummarizer(), notes

    if provider in {"openai", "auto"}:
        if api_key:
            return (
                OpenAISummarizer(
                    model=config.model.name,
                    api_key=api_key,
                    timeout_seconds=config.model.timeout_seconds,
                    max_output_tokens=config.model.max_output_tokens,
                ),
                notes,
            )
        message = (
            f"{config.model.api_key_env} is not set; used the local rule-based summarizer "
            "instead of OpenAI."
        )
        if provider == "openai":
            logger.warning(message)
        notes.append(message)
        return LocalRulesSummarizer(), notes

    notes.append(f"Unknown model.provider {provider!r}; used local_rules.")
    return LocalRulesSummarizer(), notes


def summarize_with_fallback(
    summarizer: BaseSummarizer, inputs: ReportInputs
) -> tuple[str, str, list[str]]:
    """Run the summarizer; on failure fall back to local rules.

    Returns (body_markdown, provider_label, notes).
    """
    notes: list[str] = []
    try:
        return summarizer.render_body(inputs), summarizer.model_label, notes
    except SummarizerError as exc:
        logger.warning("Summarizer %s failed: %s", summarizer.name, exc)
        if isinstance(summarizer, LocalRulesSummarizer):
            raise
        notes.append(
            f"Primary summarizer ({summarizer.model_label}) failed: {exc} "
            "Fell back to the local rule-based summarizer."
        )
        fallback = LocalRulesSummarizer()
        return fallback.render_body(inputs), fallback.model_label, notes
