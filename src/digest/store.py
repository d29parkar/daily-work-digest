from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    source_type: str
    path: str
    modified_at: str
    content_hash: str
    repo_names: list[str]
    summary: dict[str, Any]
    included_at: str | None


class DigestStore:
    def __init__(self, sqlite_path: Path):
        self.sqlite_path = sqlite_path
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(sqlite_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def upsert_source(
        self,
        *,
        source_type: str,
        path: str,
        modified_at: str,
        content_hash: str,
        repo_names: list[str],
        summary: dict[str, Any],
    ) -> bool:
        source_id = make_source_id(source_type, path)
        existing = self.conn.execute(
            "select content_hash from sources where source_id = ?", (source_id,)
        ).fetchone()
        changed = existing is None or existing["content_hash"] != content_hash
        self.conn.execute(
            """
            insert into sources (
                source_id, source_type, path, modified_at, content_hash,
                repo_names, summary_json, first_seen_at, last_seen_at
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(source_id) do update set
                modified_at = excluded.modified_at,
                content_hash = excluded.content_hash,
                repo_names = excluded.repo_names,
                summary_json = excluded.summary_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                source_id,
                source_type,
                path,
                modified_at,
                content_hash,
                json.dumps(repo_names, sort_keys=True),
                json.dumps(summary, sort_keys=True),
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()
        return changed

    def list_sources_since(self, since_iso: str) -> list[SourceRecord]:
        rows = self.conn.execute(
            """
            select *
            from sources
            where modified_at >= ?
            order by modified_at desc
            """,
            (since_iso,),
        ).fetchall()
        return [row_to_source(row) for row in rows]

    def get_digest_run(self, digest_date: str, mode: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            select *
            from digest_runs
            where digest_date = ? and mode = ?
            """,
            (digest_date, mode),
        ).fetchone()

    def record_digest_run(
        self,
        *,
        digest_date: str,
        mode: str,
        digest_path: Path,
        email_path: Path,
        source_ids: list[str],
        provider: str = "unknown",
    ) -> None:
        self.conn.execute(
            """
            insert into digest_runs (
                digest_date, mode, digest_path, email_path,
                source_ids_json, created_at, provider
            )
            values (?, ?, ?, ?, ?, ?, ?)
            on conflict(digest_date, mode) do update set
                digest_path = excluded.digest_path,
                email_path = excluded.email_path,
                source_ids_json = excluded.source_ids_json,
                created_at = excluded.created_at,
                provider = excluded.provider
            """,
            (
                digest_date,
                mode,
                str(digest_path),
                str(email_path),
                json.dumps(source_ids),
                now_iso(),
                provider,
            ),
        )
        self.conn.commit()

    def mark_sources_included(self, source_ids: list[str]) -> None:
        if not source_ids:
            return
        self.conn.executemany(
            "update sources set included_at = coalesce(included_at, ?) where source_id = ?",
            [(now_iso(), source_id) for source_id in source_ids],
        )
        self.conn.commit()

    def record_sent(self, digest_date: str, mode: str) -> None:
        self.conn.execute(
            """
            update digest_runs
            set sent_at = ?
            where digest_date = ? and mode = ?
            """,
            (now_iso(), digest_date, mode),
        )
        self.conn.commit()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists sources (
                source_id text primary key,
                source_type text not null,
                path text not null,
                modified_at text not null,
                content_hash text not null,
                repo_names text not null,
                summary_json text not null,
                included_at text,
                first_seen_at text not null,
                last_seen_at text not null
            );

            create index if not exists idx_sources_modified_at
            on sources(modified_at);

            create table if not exists digest_runs (
                digest_date text not null,
                mode text not null,
                digest_path text not null,
                email_path text not null,
                source_ids_json text not null,
                created_at text not null,
                sent_at text,
                provider text,
                primary key (digest_date, mode)
            );
            """
        )
        self.conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute("pragma table_info(digest_runs)").fetchall()
        }
        if "provider" not in columns:
            self.conn.execute("alter table digest_runs add column provider text")
            self.conn.commit()


def make_source_id(source_type: str, path: str) -> str:
    return hashlib.sha256(f"{source_type}|{path}".encode("utf-8")).hexdigest()[:32]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def row_to_source(row: sqlite3.Row) -> SourceRecord:
    return SourceRecord(
        source_id=row["source_id"],
        source_type=row["source_type"],
        path=row["path"],
        modified_at=row["modified_at"],
        content_hash=row["content_hash"],
        repo_names=json.loads(row["repo_names"]),
        summary=json.loads(row["summary_json"]),
        included_at=row["included_at"],
    )
