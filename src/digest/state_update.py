"""Stage S5: update each active project's persistent state (DESIGN_V2.md 3.5).

The memory backbone. One small LLM call per project that had work today,
running on the strongest configured model. Written as a dated state version:
a rerun of the same date replaces in place, and any failure leaves the
registry untouched (stale-but-true beats fresh-but-fabricated).
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_type
from pathlib import Path
from typing import Any

from .config import DigestConfig
from .llm_v2 import BaseLLM, LLMError, LLMUnavailable
from .store_v2 import ProjectState, WorkStore, WorkUnit

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
MAX_OUTPUT_TOKENS = 1200


def update_project_states(
    store: WorkStore,
    config: DigestConfig,
    llm: BaseLLM,
    work_date: str,
) -> dict[str, str]:
    """Returns {project_id: 'updated' | 'skipped: <reason>'} per active project."""
    results: dict[str, str] = {}
    units = store.list_units_for_date(work_date)
    by_project: dict[str, list[WorkUnit]] = {}
    for unit in units:
        # Incidental troubleshooting never feeds project memory: a broken
        # venv is not progress toward (or away from) an engineering goal.
        if unit.project_id and not unit.incidental:
            by_project.setdefault(unit.project_id, []).append(unit)

    for project_id, project_units in sorted(by_project.items()):
        project = store.get_project(project_id)
        if project is None or project.status == "retired":
            continue
        try:
            payload = _call_state_llm(
                store, llm, project_id, project_units, work_date
            )
            store.write_state_version(
                project_id,
                work_date,
                goal=str(payload.get("goal") or ""),
                system_state=str(payload.get("system_state") or ""),
                narrative_delta=str(payload.get("narrative_delta") or ""),
                open_threads=payload.get("open_threads", []),
                evidence=payload.get("evidence", {}),
                written_by=llm.label,
            )
            results[project_id] = "updated"
        except LLMUnavailable:
            results[project_id] = "skipped: no LLM provider"
        except LLMError as exc:
            logger.warning("State update failed for %s: %s", project_id, exc)
            results[project_id] = f"skipped: {exc}"
    export_registry(store, config.pipeline.registry_export_path)
    return results


def _call_state_llm(
    store: WorkStore,
    llm: BaseLLM,
    project_id: str,
    units: list[WorkUnit],
    work_date: str,
) -> dict[str, Any]:
    system = (PROMPTS_DIR / "v2_state_system.md").read_text(encoding="utf-8")
    user = _render_state_input(store, project_id, units, work_date)
    payload = llm.complete_json(
        system=system, user=user, max_output_tokens=MAX_OUTPUT_TOKENS, stage="state"
    )
    _validate_state_payload(payload, units, work_date)
    return payload


def _render_state_input(
    store: WorkStore,
    project_id: str,
    units: list[WorkUnit],
    work_date: str,
) -> str:
    project = store.get_project(project_id)
    head = store.get_head_state(project_id)
    recent = [
        state
        for state in store.list_recent_states(project_id, limit=7)
        if state.as_of_date != work_date  # a rerun must not read its own draft
    ]

    lines: list[str] = [f"PROJECT: {project.name} ({project_id})", f"DATE: {work_date}"]
    if head and head.as_of_date != work_date:
        lines += [
            "",
            "PREVIOUS STATE:",
            f"goal: {head.goal}",
            f"system_state: {head.system_state}",
            "open_threads:",
        ]
        threads = [t for t in head.open_threads if t.get("status") != "resolved_today"]
        lines += [
            f"- {t.get('text')} (since {t.get('since')})" for t in threads
        ] or ["- (none)"]
    else:
        lines += ["", "PREVIOUS STATE: (none, first day for this project)"]

    if recent:
        lines += ["", "RECENT DAILY DELTAS (newest first):"]
        lines += [f"- {s.as_of_date}: {s.narrative_delta}" for s in recent[:5]]

    lines += ["", f"TODAY'S WORK UNITS ({len(units)}):"]
    for unit in units:
        verdict = unit.verification.get("verdict", "unknown")
        evidence = ", ".join(unit.verification.get("evidence", [])[:4])
        lines += [
            f"### unit:{unit.unit_key}",
            f"intent: {unit.intent}",
            f"kind: {unit.kind} | status_claim: {unit.status_claim} "
            f"| verification: {verdict}" + (f" ({evidence})" if evidence else ""),
        ]
        if unit.outcome_claim:
            lines.append(f"outcome_claim: {unit.outcome_claim}")
        if unit.files:
            lines.append(f"files: {', '.join(unit.files[:10])}")
        if unit.open_questions:
            lines.append(f"open_questions: {'; '.join(unit.open_questions)}")
        if unit.user_corrections:
            lines.append(f"user_corrections: {'; '.join(unit.user_corrections)}")
        for note in unit.verification.get("notes", []):
            lines.append(f"verification_note: {note}")
    return "\n".join(lines)


def _validate_state_payload(
    payload: dict[str, Any], units: list[WorkUnit], work_date: str
) -> None:
    for key in ("goal", "system_state", "narrative_delta"):
        if not str(payload.get(key) or "").strip():
            raise LLMError(f"State payload is missing {key!r}.")
    threads = payload.get("open_threads")
    if not isinstance(threads, list):
        raise LLMError("open_threads must be a list.")
    for thread in threads:
        if not isinstance(thread, dict) or not str(thread.get("text") or "").strip():
            raise LLMError("Every open thread needs a text field.")
        thread.setdefault("since", work_date)
        if thread.get("status") not in {"open", "resolved_today"}:
            thread["status"] = "open"
    # Citation check: unknown unit references are dropped, not trusted.
    valid_ids = {f"unit:{u.unit_key}" for u in units}
    valid_ids.update(
        e for u in units for e in u.verification.get("evidence", [])
    )
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        payload["evidence"] = {}
        return
    for key, refs in list(evidence.items()):
        if isinstance(refs, list):
            evidence[key] = [r for r in refs if r in valid_ids]
        else:
            evidence[key] = []


def export_registry(store: WorkStore, path: Path) -> None:
    """Human-readable, read-only mirror of what the system believes."""
    lines = [
        "# Project registry (generated mirror; edit via `digest projects ...`)",
        "",
    ]
    for project in store.list_projects(include_retired=True):
        head = store.get_head_state(project.project_id)
        badges = [project.status]
        if project.trello_scope:
            badges.append("trello")
        lines.append(f"## {project.name} (`{project.project_id}`, {', '.join(badges)})")
        if head:
            lines += [
                "",
                f"- Goal: {head.goal or '(none recorded)'}",
                f"- State: {head.system_state or '(none recorded)'}",
                f"- As of: {head.as_of_date} (v{head.version}, by {head.written_by})",
            ]
            open_threads = [
                t for t in head.open_threads if t.get("status") != "resolved_today"
            ]
            if open_threads:
                lines.append("- Open threads:")
                lines += [
                    f"  - {t.get('text')} (since {t.get('since')})"
                    for t in open_threads
                ]
        else:
            lines += ["", "- No state recorded yet."]
        matchers = store.list_matchers(project.project_id)
        if matchers:
            patterns = ", ".join(f"{m['kind']}={m['pattern']}" for m in matchers[:6])
            lines.append(f"- Matchers: {patterns}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def bootstrap_state_if_missing(
    store: WorkStore, project_id: str, goal_hint: str, work_date: str | None = None
) -> None:
    """Seed a project's first state row from a human-provided goal."""
    if store.get_head_state(project_id) is not None:
        return
    store.write_state_version(
        project_id,
        work_date or date_type.today().isoformat(),
        goal=goal_hint,
        system_state="",
        narrative_delta="Seeded manually.",
        open_threads=[],
        evidence={},
        written_by="human",
    )
