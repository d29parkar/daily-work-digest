"""SQLite storage for the v2 pipeline (DESIGN_V2.md section 4).

Lives in the same database file as the v1 ``DigestStore`` tables but owns its
own tables, so v1 keeps working during and after the migration. Every write
is keyed deterministically (session ids, unit keys, (project, date) state
versions), which is what makes pipeline reruns idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class SessionRecord:
    session_id: str  # 'claude:<uuid>' | 'codex:<uuid>'
    agent: str
    path: str
    content_hash: str
    title: str | None
    cwd: str | None
    git_branch: str | None
    started_at: str | None
    ended_at: str | None
    turn_count: int
    parse_errors: int
    status: str = "ok"  # ok | corrupt | excluded


@dataclass(frozen=True)
class TurnRecord:
    turn_id: str  # '<session_id>:<promptId|turn_id>'
    session_id: str
    seq: int
    started_at: str | None
    ended_at: str | None
    user_text: str
    assistant_text: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    cwd: str | None = None
    git_branch: str | None = None
    model: str | None = None
    flags: list[str] = field(default_factory=list)
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class WorkUnit:
    unit_key: str  # '<session_id>:<n>'
    work_date: str
    project_id: str | None
    turn_ids: list[str]
    intent: str
    kind: str
    outcome_claim: str
    status_claim: str
    files: list[str]
    entities: list[str]
    claims: list[dict[str, Any]]
    open_questions: list[str]
    user_corrections: list[str] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    extraction: str = "llm"  # llm | stub
    # Incidental = environment/tooling troubleshooting (venv breakage,
    # dependency installs, permission errors) that is not project work.
    # Excluded from state updates and the Trello card; footnoted in the
    # night digest.
    incidental: bool = False


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    status: str  # active | provisional | paused | retired
    created_at: str
    head_version: int
    trello_scope: bool


@dataclass(frozen=True)
class ProjectState:
    project_id: str
    version: int
    as_of_date: str
    goal: str
    system_state: str
    narrative_delta: str
    open_threads: list[dict[str, Any]]
    evidence: dict[str, Any]
    written_by: str


class WorkStore:
    def __init__(self, sqlite_path: Path):
        self.sqlite_path = sqlite_path
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(sqlite_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    # -- sessions / turns ---------------------------------------------------

    def get_session_hash(self, session_id: str) -> str | None:
        row = self.conn.execute(
            "select content_hash from sessions_v2 where session_id = ?",
            (session_id,),
        ).fetchone()
        return row["content_hash"] if row else None

    def upsert_session(self, session: SessionRecord, turns: list[TurnRecord]) -> bool:
        """Replace a session and all of its turns atomically.

        Returns True when the stored content changed (new or different hash).
        """
        changed = self.get_session_hash(session.session_id) != session.content_hash
        with self.conn:
            self.conn.execute(
                """
                insert into sessions_v2 (
                    session_id, agent, path, content_hash, title, cwd,
                    git_branch, started_at, ended_at, turn_count,
                    parse_errors, status, harvested_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(session_id) do update set
                    path = excluded.path,
                    content_hash = excluded.content_hash,
                    title = excluded.title,
                    cwd = excluded.cwd,
                    git_branch = excluded.git_branch,
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    turn_count = excluded.turn_count,
                    parse_errors = excluded.parse_errors,
                    status = excluded.status,
                    harvested_at = excluded.harvested_at
                """,
                (
                    session.session_id,
                    session.agent,
                    session.path,
                    session.content_hash,
                    session.title,
                    session.cwd,
                    session.git_branch,
                    session.started_at,
                    session.ended_at,
                    session.turn_count,
                    session.parse_errors,
                    session.status,
                    now_iso(),
                ),
            )
            self.conn.execute(
                "delete from turns_v2 where session_id = ?", (session.session_id,)
            )
            self.conn.executemany(
                """
                insert into turns_v2 (
                    turn_id, session_id, seq, started_at, ended_at, user_text,
                    assistant_text, tools_json, files_json, cwd, git_branch,
                    model, flags_json, line_start, line_end
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        t.turn_id,
                        t.session_id,
                        t.seq,
                        t.started_at,
                        t.ended_at,
                        t.user_text,
                        t.assistant_text,
                        json.dumps(t.tools),
                        json.dumps(t.files),
                        t.cwd,
                        t.git_branch,
                        t.model,
                        json.dumps(t.flags),
                        t.line_start,
                        t.line_end,
                    )
                    for t in turns
                ],
            )
        return changed

    def list_sessions_between(self, start_iso: str, end_iso: str) -> list[SessionRecord]:
        """Sessions whose activity window overlaps [start, end)."""
        rows = self.conn.execute(
            """
            select * from sessions_v2
            where status != 'excluded'
              and ended_at >= ? and started_at < ?
            order by started_at
            """,
            (start_iso, end_iso),
        ).fetchall()
        return [_row_to_session(row) for row in rows]

    def list_turns(self, session_id: str) -> list[TurnRecord]:
        rows = self.conn.execute(
            "select * from turns_v2 where session_id = ? order by seq",
            (session_id,),
        ).fetchall()
        return [_row_to_turn(row) for row in rows]

    def list_turns_between(
        self, session_id: str, start_iso: str, end_iso: str
    ) -> list[TurnRecord]:
        rows = self.conn.execute(
            """
            select * from turns_v2
            where session_id = ? and started_at >= ? and started_at < ?
            order by seq
            """,
            (session_id, start_iso, end_iso),
        ).fetchall()
        return [_row_to_turn(row) for row in rows]

    def prune_turn_text(self, keep_after_iso: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                update turns_v2
                set user_text = '', assistant_text = ''
                where started_at < ? and (user_text != '' or assistant_text != '')
                """,
                (keep_after_iso,),
            )
        return cursor.rowcount

    # -- work units ----------------------------------------------------------

    def replace_work_units(
        self, session_id: str, work_date: str, units: list[WorkUnit]
    ) -> None:
        """Idempotent per (session, day): rerunning extraction replaces."""
        with self.conn:
            self.conn.execute(
                "delete from work_units_v2 where unit_key like ? and work_date = ?",
                (f"{session_id}:%", work_date),
            )
            self.conn.executemany(
                """
                insert into work_units_v2 (
                    unit_key, work_date, project_id, turn_ids_json, intent,
                    kind, outcome_claim, status_claim, files_json,
                    entities_json, claims_json, open_questions_json,
                    user_corrections_json, verification_json, extraction,
                    incidental, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        u.unit_key,
                        u.work_date,
                        u.project_id,
                        json.dumps(u.turn_ids),
                        u.intent,
                        u.kind,
                        u.outcome_claim,
                        u.status_claim,
                        json.dumps(u.files),
                        json.dumps(u.entities),
                        json.dumps(u.claims),
                        json.dumps(u.open_questions),
                        json.dumps(u.user_corrections),
                        json.dumps(u.verification),
                        u.extraction,
                        int(u.incidental),
                        now_iso(),
                    )
                    for u in units
                ],
            )

    def list_units_for_date(
        self, work_date: str, project_id: str | None = None
    ) -> list[WorkUnit]:
        query = "select * from work_units_v2 where work_date = ?"
        params: list[Any] = [work_date]
        if project_id is not None:
            query += " and project_id = ?"
            params.append(project_id)
        rows = self.conn.execute(query + " order by unit_key", params).fetchall()
        return [_row_to_unit(row) for row in rows]

    def set_unit_project(self, unit_key: str, project_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "update work_units_v2 set project_id = ? where unit_key = ?",
                (project_id, unit_key),
            )

    def set_unit_verification(self, unit_key: str, verification: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute(
                "update work_units_v2 set verification_json = ? where unit_key = ?",
                (json.dumps(verification), unit_key),
            )

    # -- projects ------------------------------------------------------------

    def create_project(
        self,
        project_id: str,
        name: str,
        status: str = "active",
        trello_scope: bool = False,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into projects_v2 (
                    project_id, name, status, created_at, head_version, trello_scope
                ) values (?, ?, ?, ?, 0, ?)
                on conflict(project_id) do nothing
                """,
                (project_id, name, status, now_iso(), int(trello_scope)),
            )

    def get_project(self, project_id: str) -> Project | None:
        row = self.conn.execute(
            "select * from projects_v2 where project_id = ?", (project_id,)
        ).fetchone()
        return _row_to_project(row) if row else None

    def list_projects(self, include_retired: bool = False) -> list[Project]:
        query = "select * from projects_v2"
        if not include_retired:
            query += " where status != 'retired'"
        rows = self.conn.execute(query + " order by project_id").fetchall()
        return [_row_to_project(row) for row in rows]

    def set_project_status(self, project_id: str, status: str) -> None:
        with self.conn:
            self.conn.execute(
                "update projects_v2 set status = ? where project_id = ?",
                (status, project_id),
            )

    def rename_project(self, project_id: str, name: str) -> None:
        with self.conn:
            self.conn.execute(
                "update projects_v2 set name = ? where project_id = ?",
                (name, project_id),
            )

    def set_trello_scope(self, project_id: str, in_scope: bool) -> None:
        with self.conn:
            self.conn.execute(
                "update projects_v2 set trello_scope = ? where project_id = ?",
                (int(in_scope), project_id),
            )

    def add_matcher(
        self, project_id: str, kind: str, pattern: str, source: str = "seed"
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into project_matchers_v2 (
                    project_id, kind, pattern, source, created_at
                ) values (?, ?, ?, ?, ?)
                on conflict(project_id, kind, pattern) do nothing
                """,
                (project_id, kind, pattern.lower(), source, now_iso()),
            )

    def list_matchers(self, project_id: str | None = None) -> list[dict[str, Any]]:
        query = "select * from project_matchers_v2"
        params: list[Any] = []
        if project_id is not None:
            query += " where project_id = ?"
            params.append(project_id)
        rows = self.conn.execute(query + " order by project_id, kind", params).fetchall()
        return [dict(row) for row in rows]

    # -- project state versions -----------------------------------------------

    def get_head_state(self, project_id: str) -> ProjectState | None:
        row = self.conn.execute(
            """
            select s.* from project_state_versions_v2 s
            join projects_v2 p on p.project_id = s.project_id
            where s.project_id = ? and s.version = p.head_version
            """,
            (project_id,),
        ).fetchone()
        return _row_to_state(row) if row else None

    def list_recent_states(self, project_id: str, limit: int = 7) -> list[ProjectState]:
        rows = self.conn.execute(
            """
            select * from project_state_versions_v2
            where project_id = ? order by version desc limit ?
            """,
            (project_id, limit),
        ).fetchall()
        return [_row_to_state(row) for row in rows]

    def write_state_version(
        self,
        project_id: str,
        as_of_date: str,
        *,
        goal: str,
        system_state: str,
        narrative_delta: str,
        open_threads: list[dict[str, Any]],
        evidence: dict[str, Any],
        written_by: str,
    ) -> int:
        """Write today's state. A rerun for the same date replaces in place,
        so state cannot compound within one day. Returns the version number."""
        with self.conn:
            existing = self.conn.execute(
                """
                select version from project_state_versions_v2
                where project_id = ? and as_of_date = ?
                """,
                (project_id, as_of_date),
            ).fetchone()
            if existing:
                version = int(existing["version"])
            else:
                row = self.conn.execute(
                    "select coalesce(max(version), 0) + 1 as v "
                    "from project_state_versions_v2 where project_id = ?",
                    (project_id,),
                ).fetchone()
                version = int(row["v"])
            self.conn.execute(
                """
                insert into project_state_versions_v2 (
                    project_id, version, as_of_date, goal, system_state,
                    narrative_delta, open_threads_json, evidence_json,
                    written_by, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(project_id, version) do update set
                    goal = excluded.goal,
                    system_state = excluded.system_state,
                    narrative_delta = excluded.narrative_delta,
                    open_threads_json = excluded.open_threads_json,
                    evidence_json = excluded.evidence_json,
                    written_by = excluded.written_by,
                    created_at = excluded.created_at
                """,
                (
                    project_id,
                    version,
                    as_of_date,
                    goal,
                    system_state,
                    narrative_delta,
                    json.dumps(open_threads),
                    json.dumps(evidence),
                    written_by,
                    now_iso(),
                ),
            )
            self.conn.execute(
                "update projects_v2 set head_version = max(head_version, ?) "
                "where project_id = ?",
                (version, project_id),
            )
        return version

    def rollback_state(self, project_id: str, to_date: str) -> int | None:
        """Move head back to the newest version at or before to_date."""
        row = self.conn.execute(
            """
            select version from project_state_versions_v2
            where project_id = ? and as_of_date <= ?
            order by version desc limit 1
            """,
            (project_id, to_date),
        ).fetchone()
        if not row:
            return None
        with self.conn:
            self.conn.execute(
                "update projects_v2 set head_version = ? where project_id = ?",
                (int(row["version"]), project_id),
            )
        return int(row["version"])

    def last_activity_date(self, project_id: str) -> str | None:
        row = self.conn.execute(
            "select max(work_date) as d from work_units_v2 where project_id = ?",
            (project_id,),
        ).fetchone()
        return row["d"] if row and row["d"] else None

    # -- facts -----------------------------------------------------------------

    def upsert_fact(
        self, table: str, fact_id: str, columns: dict[str, Any]
    ) -> None:
        if table not in {"git_facts_v2", "pr_facts_v2"}:
            raise ValueError(f"Unknown fact table: {table}")
        keys = ["fact_id", *columns.keys()]
        placeholders = ", ".join("?" for _ in keys)
        updates = ", ".join(f"{k} = excluded.{k}" for k in columns)
        with self.conn:
            self.conn.execute(
                f"insert into {table} ({', '.join(keys)}) values ({placeholders}) "
                f"on conflict(fact_id) do update set {updates}",
                [fact_id, *columns.values()],
            )

    def list_git_facts(self, kind: str | None = None) -> list[dict[str, Any]]:
        query = "select * from git_facts_v2"
        params: list[Any] = []
        if kind:
            query += " where kind = ?"
            params.append(kind)
        rows = self.conn.execute(query + " order by observed_at desc", params).fetchall()
        return [_fact_to_dict(row) for row in rows]

    def list_pr_facts(self, kind: str | None = None) -> list[dict[str, Any]]:
        query = "select * from pr_facts_v2"
        params: list[Any] = []
        if kind:
            query += " where kind = ?"
            params.append(kind)
        rows = self.conn.execute(query + " order by observed_at desc", params).fetchall()
        return [_fact_to_dict(row) for row in rows]

    # -- overrides ---------------------------------------------------------------

    def set_override(self, unit_key: str, project_id: str, note: str = "") -> None:
        with self.conn:
            self.conn.execute(
                """
                insert into unit_overrides_v2 (unit_key, project_id, note, created_at)
                values (?, ?, ?, ?)
                on conflict(unit_key) do update set
                    project_id = excluded.project_id,
                    note = excluded.note,
                    created_at = excluded.created_at
                """,
                (unit_key, project_id, note, now_iso()),
            )

    def get_override(self, unit_key: str) -> str | None:
        row = self.conn.execute(
            "select project_id from unit_overrides_v2 where unit_key = ?",
            (unit_key,),
        ).fetchone()
        return row["project_id"] if row else None

    # -- pipeline runs -------------------------------------------------------------

    def record_stage(self, run_date: str, mode: str, stage: str, status: str) -> None:
        run_id = f"{run_date}:{mode}"
        with self.conn:
            row = self.conn.execute(
                "select stage_status_json from pipeline_runs_v2 where run_id = ?",
                (run_id,),
            ).fetchone()
            stages = json.loads(row["stage_status_json"]) if row else {}
            stages[stage] = status
            self.conn.execute(
                """
                insert into pipeline_runs_v2 (
                    run_id, run_date, mode, stage_status_json, started_at, finished_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(run_id) do update set
                    stage_status_json = excluded.stage_status_json,
                    finished_at = excluded.finished_at
                """,
                (run_id, run_date, mode, json.dumps(stages), now_iso(), now_iso()),
            )

    def get_stage_status(self, run_date: str, mode: str) -> dict[str, str]:
        row = self.conn.execute(
            "select stage_status_json from pipeline_runs_v2 where run_id = ?",
            (f"{run_date}:{mode}",),
        ).fetchone()
        return json.loads(row["stage_status_json"]) if row else {}

    # -- schema ---------------------------------------------------------------------

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists sessions_v2 (
                session_id    text primary key,
                agent         text not null,
                path          text not null,
                content_hash  text not null,
                title         text,
                cwd           text,
                git_branch    text,
                started_at    text,
                ended_at      text,
                turn_count    integer not null default 0,
                parse_errors  integer not null default 0,
                status        text not null default 'ok',
                harvested_at  text not null
            );

            create table if not exists turns_v2 (
                turn_id        text primary key,
                session_id     text not null references sessions_v2(session_id),
                seq            integer not null,
                started_at     text,
                ended_at       text,
                user_text      text not null default '',
                assistant_text text not null default '',
                tools_json     text not null default '[]',
                files_json     text not null default '[]',
                cwd            text,
                git_branch     text,
                model          text,
                flags_json     text not null default '[]',
                line_start     integer,
                line_end       integer
            );
            create index if not exists idx_turns_v2_session on turns_v2(session_id, seq);
            create index if not exists idx_turns_v2_time on turns_v2(started_at);

            create table if not exists projects_v2 (
                project_id    text primary key,
                name          text not null,
                status        text not null default 'active',
                created_at    text not null,
                head_version  integer not null default 0,
                trello_scope  integer not null default 0
            );

            create table if not exists project_matchers_v2 (
                project_id    text not null references projects_v2(project_id),
                kind          text not null,
                pattern       text not null,
                source        text not null default 'seed',
                created_at    text not null,
                primary key (project_id, kind, pattern)
            );

            create table if not exists project_state_versions_v2 (
                project_id      text not null references projects_v2(project_id),
                version         integer not null,
                as_of_date      text not null,
                goal            text not null default '',
                system_state    text not null default '',
                narrative_delta text not null default '',
                open_threads_json text not null default '[]',
                evidence_json   text not null default '{}',
                written_by      text not null,
                created_at      text not null,
                primary key (project_id, version),
                unique (project_id, as_of_date)
            );

            create table if not exists work_units_v2 (
                unit_key      text primary key,
                work_date     text not null,
                project_id    text,
                turn_ids_json text not null,
                intent        text not null default '',
                kind          text not null default 'other',
                outcome_claim text not null default '',
                status_claim  text not null default 'unknown',
                files_json    text not null default '[]',
                entities_json text not null default '[]',
                claims_json   text not null default '[]',
                open_questions_json text not null default '[]',
                user_corrections_json text not null default '[]',
                verification_json text not null default '{}',
                extraction    text not null default 'llm',
                incidental    integer not null default 0,
                created_at    text not null
            );
            create index if not exists idx_units_v2_date
            on work_units_v2(work_date, project_id);

            create table if not exists git_facts_v2 (
                fact_id       text primary key,
                repo_path     text not null,
                kind          text not null,
                ref           text,
                observed_at   text not null,
                data_json     text not null
            );

            create table if not exists pr_facts_v2 (
                fact_id       text primary key,
                repo          text not null,
                pr_number     integer not null,
                author        text,
                my_role       text,
                kind          text not null,
                observed_at   text not null,
                data_json     text not null
            );

            create table if not exists unit_overrides_v2 (
                unit_key      text primary key,
                project_id    text,
                note          text,
                created_at    text not null
            );

            create table if not exists pipeline_runs_v2 (
                run_id        text primary key,
                run_date      text not null,
                mode          text not null,
                stage_status_json text not null,
                started_at    text,
                finished_at   text
            );
            """
        )
        self.conn.commit()


def _row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        agent=row["agent"],
        path=row["path"],
        content_hash=row["content_hash"],
        title=row["title"],
        cwd=row["cwd"],
        git_branch=row["git_branch"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        turn_count=row["turn_count"],
        parse_errors=row["parse_errors"],
        status=row["status"],
    )


def _row_to_turn(row: sqlite3.Row) -> TurnRecord:
    return TurnRecord(
        turn_id=row["turn_id"],
        session_id=row["session_id"],
        seq=row["seq"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        user_text=row["user_text"],
        assistant_text=row["assistant_text"],
        tools=json.loads(row["tools_json"]),
        files=json.loads(row["files_json"]),
        cwd=row["cwd"],
        git_branch=row["git_branch"],
        model=row["model"],
        flags=json.loads(row["flags_json"]),
        line_start=row["line_start"],
        line_end=row["line_end"],
    )


def _row_to_unit(row: sqlite3.Row) -> WorkUnit:
    return WorkUnit(
        unit_key=row["unit_key"],
        work_date=row["work_date"],
        project_id=row["project_id"],
        turn_ids=json.loads(row["turn_ids_json"]),
        intent=row["intent"],
        kind=row["kind"],
        outcome_claim=row["outcome_claim"],
        status_claim=row["status_claim"],
        files=json.loads(row["files_json"]),
        entities=json.loads(row["entities_json"]),
        claims=json.loads(row["claims_json"]),
        open_questions=json.loads(row["open_questions_json"]),
        user_corrections=json.loads(row["user_corrections_json"]),
        verification=json.loads(row["verification_json"]),
        extraction=row["extraction"],
        incidental=bool(row["incidental"]),
    )


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        project_id=row["project_id"],
        name=row["name"],
        status=row["status"],
        created_at=row["created_at"],
        head_version=row["head_version"],
        trello_scope=bool(row["trello_scope"]),
    )


def _row_to_state(row: sqlite3.Row) -> ProjectState:
    return ProjectState(
        project_id=row["project_id"],
        version=row["version"],
        as_of_date=row["as_of_date"],
        goal=row["goal"],
        system_state=row["system_state"],
        narrative_delta=row["narrative_delta"],
        open_threads=json.loads(row["open_threads_json"]),
        evidence=json.loads(row["evidence_json"]),
        written_by=row["written_by"],
    )


def _fact_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["data"] = json.loads(result.pop("data_json"))
    return result
