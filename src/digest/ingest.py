from __future__ import annotations

from dataclasses import dataclass

from .config import DigestConfig
from .ingest_git import ingest_git
from .ingest_notes import ingest_notes
from .ingest_sessions import ingest_claude, ingest_codex
from .store import DigestStore


@dataclass(frozen=True)
class IngestResult:
    claude_changed: int
    codex_changed: int
    notes_changed: int
    git_changed: int

    @property
    def total_changed(self) -> int:
        return (
            self.claude_changed
            + self.codex_changed
            + self.notes_changed
            + self.git_changed
        )


def run_ingest(config: DigestConfig, store: DigestStore) -> IngestResult:
    return IngestResult(
        claude_changed=ingest_claude(config, store),
        codex_changed=ingest_codex(config, store),
        notes_changed=ingest_notes(config, store),
        git_changed=ingest_git(
            config,
            store,
            lookback_hours=max(
                config.claude_lookback_hours,
                config.codex_lookback_hours,
                config.notes_lookback_hours,
            ),
        ),
    )
