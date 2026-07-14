"""The v2 pipeline orchestrator (DESIGN_V2.md section 3).

Runs S1..S6 as checkpointed stages over SQLite. Every stage records its
status; a failed stage degrades that run (noted in the digest) without
corrupting state, and a rerun replaces its own outputs instead of stacking.

Mode behavior:
- night:   full run including project state updates (S5).
- morning: harvest/extract/corroborate the fresh delta, but read yesterday's
           head states; the registry is only written at night.
- trello:  same as morning but renders the midday card from today's units.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta

from .attribute import attribute_units, ensure_baseline_projects, retire_idle_projects
from .config import DigestConfig
from .corroborate import collect_git_facts, collect_pr_facts, corroborate_units
from .extract import extract_session_units
from .harvest import run_harvest
from .llm_v2 import LLMUnavailable, NoneLLM, resolve_llm
from .render_v2 import render_morning, render_night, render_trello
from .state_update import update_project_states
from .store_v2 import WorkStore, now_iso

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    mode: str
    work_date: str
    markdown: str
    notes: list[str] = field(default_factory=list)
    stage_status: dict[str, str] = field(default_factory=dict)


def run_pipeline(
    config: DigestConfig,
    mode: str,
    work_date: str | None = None,
) -> PipelineResult:
    work_date = work_date or date_type.today().isoformat()
    notes: list[str] = []
    store = WorkStore(config.sqlite_path)
    try:
        return _run(store, config, mode, work_date, notes)
    finally:
        store.close()


def _run(
    store: WorkStore,
    config: DigestConfig,
    mode: str,
    work_date: str,
    notes: list[str],
) -> PipelineResult:
    def stage(name: str, status: str) -> None:
        store.record_stage(work_date, mode, name, status)
        if not status.startswith("ok"):
            notes.append(f"stage {name}: {status}")

    ensure_baseline_projects(store, config)

    # S1 harvest ------------------------------------------------------------
    try:
        harvest = run_harvest(config, store)
        stage(
            "harvest",
            f"ok: {harvest.harvested} changed, {harvest.unchanged} unchanged, "
            f"{len(harvest.corrupt)} corrupt",
        )
    except Exception as exc:  # a harvest crash must not kill the digest
        logger.exception("Harvest failed")
        harvest = None
        stage("harvest", f"failed: {exc}")

    # S2 extract -------------------------------------------------------------
    day_start, day_end = _day_window(work_date)
    changed = set(harvest.changed_session_ids) if harvest else set()
    sessions = store.list_sessions_between(day_start, day_end)
    try:
        llm_extract = resolve_llm(config, "extract")
    except LLMUnavailable as exc:
        llm_extract = NoneLLM()
        notes.append(f"extract: {exc} Using stub units (activity only).")

    extracted = stubbed = skipped = 0
    all_units = []
    for session in sessions:
        existing = [
            u
            for u in store.list_units_for_date(work_date)
            if u.unit_key.startswith(f"{session.session_id}:")
        ]
        if existing and session.session_id not in changed:
            all_units.extend(existing)
            skipped += 1
            continue
        turns = store.list_turns_between(session.session_id, day_start, day_end)
        if not turns:
            continue
        try:
            units, method = extract_session_units(llm_extract, session, turns, work_date)
        except LLMUnavailable:
            from .extract import stub_unit

            units, method = [stub_unit(session, turns, work_date)], "stub"
        store.replace_work_units(session.session_id, work_date, units)
        all_units.extend(units)
        if method == "llm":
            extracted += 1
        else:
            stubbed += 1
    stage(
        "extract",
        ("ok" if stubbed == 0 else "degraded")
        + f": {extracted} extracted, {stubbed} stubbed, {skipped} reused",
    )

    # S3 attribute -----------------------------------------------------------
    sessions_by_id = {s.session_id: s for s in store.list_sessions_between(day_start, day_end)}
    counts = attribute_units(store, all_units, sessions_by_id)
    stage("attribute", f"ok: {counts}")
    paused = retire_idle_projects(store, work_date, config.pipeline.retire_after_days)
    if paused:
        notes.append(f"paused idle projects: {', '.join(paused)}")

    # S4 corroborate ---------------------------------------------------------
    try:
        lookback = max(config.claude_lookback_hours, config.codex_lookback_hours)
        git_summary = collect_git_facts(store, config, lookback)
        pr_summary = collect_pr_facts(store, config)
        fresh_units = store.list_units_for_date(work_date)
        corroborate_units(store, fresh_units)
        pr_note = (
            f"prs: {pr_summary.get('review_done', 0)} reviews by me, "
            f"{pr_summary.get('inbound_feedback', 0)} inbound"
            if pr_summary.get("available")
            else f"prs unavailable ({pr_summary.get('reason')})"
        )
        stage("corroborate", f"ok: {len(git_summary['repos'])} repos, {pr_note}")
        for error in git_summary.get("errors", []):
            notes.append(f"git: {error}")
    except Exception as exc:
        logger.exception("Corroboration failed")
        stage("corroborate", f"failed: {exc}")

    # S5 state (night only) ----------------------------------------------------
    if mode == "night":
        try:
            llm_state = resolve_llm(config, "state")
            results = update_project_states(store, config, llm_state, work_date)
            updated = sum(1 for v in results.values() if v == "updated")
            skipped_state = {k: v for k, v in results.items() if v != "updated"}
            status = f"ok: {updated} project(s) updated"
            if skipped_state:
                status = f"degraded: {updated} updated, skipped {skipped_state}"
            stage("state", status)
        except LLMUnavailable as exc:
            stage("state", f"skipped: {exc}")
        except Exception as exc:
            logger.exception("State update failed")
            stage("state", f"failed: {exc}")

    # S6 render ------------------------------------------------------------------
    generated_at = now_iso()
    if mode == "morning":
        markdown = render_morning(store, config, work_date, generated_at)
    elif mode == "trello":
        try:
            llm_render = resolve_llm(config, "render")
        except LLMUnavailable:
            llm_render = NoneLLM()
        markdown = render_trello(store, config, work_date, generated_at, llm_render)
    else:
        markdown = render_night(store, config, work_date, generated_at)
    stage("render", "ok")

    if notes:
        header, _, rest = markdown.partition("\n\n")
        note_block = "\n".join(["> **Run notes:**"] + [f"> - {n}" for n in notes])
        markdown = f"{header}\n\n{note_block}\n\n{rest}"

    return PipelineResult(
        mode=mode,
        work_date=work_date,
        markdown=markdown,
        notes=notes,
        stage_status=store.get_stage_status(work_date, mode),
    )


def _day_window(work_date: str) -> tuple[str, str]:
    start = datetime.fromisoformat(work_date).astimezone()
    end = start + timedelta(days=1)
    return (
        start.isoformat(timespec="seconds"),
        end.isoformat(timespec="seconds"),
    )


def generate_digest_v2(
    *,
    config: DigestConfig,
    store,  # v1 DigestStore, used for digest_runs idempotency + email compat
    mode: str,
    once_per_day: bool = False,
    force: bool = False,
    skip_ingest: bool = False,  # accepted for CLI compat; harvest is cheap
):
    """v2 twin of generate.generate_digest, returning the same GenerateResult
    so cli.run_send and the scheduler scripts work unchanged."""
    from .email_render import render_email_text
    from .generate import GenerateResult, _read_if_exists

    today = date_type.today()
    existing = store.get_digest_run(today.isoformat(), mode)
    if once_per_day and existing and not force:
        from pathlib import Path

        digest_path = Path(existing["digest_path"])
        email_path = Path(existing["email_path"])
        digest_markdown = _read_if_exists(digest_path)
        subject, body, preview, html = render_email_text(
            config=config, mode=mode, digest_markdown=digest_markdown, digest_date=today
        )
        return GenerateResult(
            digest_path=digest_path,
            email_path=email_path,
            digest_markdown=digest_markdown,
            email_body=body,
            email_preview=preview,
            email_html=html,
            subject=subject,
            digest_date=today,
            skipped=True,
            provider_label="v2",
            ingest_result=None,
        )

    result = run_pipeline(config, mode, today.isoformat())
    subject, body, preview, html = render_email_text(
        config=config, mode=mode, digest_markdown=result.markdown, digest_date=today
    )

    config.output_path.mkdir(parents=True, exist_ok=True)
    digest_path = config.output_path / f"{today.isoformat()}_{mode}_digest.md"
    email_path = config.output_path / f"{today.isoformat()}_{mode}_email.txt"
    html_path = config.output_path / f"{today.isoformat()}_{mode}_email.html"
    digest_path.write_text(result.markdown, encoding="utf-8")
    email_path.write_text(preview, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")

    store.record_digest_run(
        digest_date=today.isoformat(),
        mode=mode,
        digest_path=digest_path,
        email_path=email_path,
        source_ids=[],
        provider="v2",
    )
    return GenerateResult(
        digest_path=digest_path,
        email_path=email_path,
        digest_markdown=result.markdown,
        email_body=body,
        email_preview=preview,
        email_html=html,
        subject=subject,
        digest_date=today,
        skipped=False,
        provider_label="v2",
        ingest_result=None,
    )
