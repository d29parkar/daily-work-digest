from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import DigestConfig
from .store import DigestStore
from .summarize import summarize_text_source
from .text_utils import SESSION_EXTENSIONS, file_hash, is_related_to_repos


def ingest_notes(config: DigestConfig, store: DigestStore) -> int:
    cutoff = datetime.now().astimezone() - timedelta(hours=config.notes_lookback_hours)
    changed_count = 0
    if not config.notes_path.exists():
        return 0

    for path in _discover_note_files(config.notes_path, cutoff):
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        related_repos = [
            repo_name
            for repo_name, repo_path in config.repos.items()
            if is_related_to_repos(f"{path}\n{text}", [repo_name], [repo_path])
        ]
        # Notes under this project are intentional; include them even if they do not
        # spell out the repo name.
        if not related_repos:
            related_repos = list(config.repos.keys())

        source_type = "reviewer_note" if "reviewer" in path.parts else "manual_note"
        summary = summarize_text_source(
            text=text,
            source_type=source_type,
            path=path,
            repo_names=related_repos,
            max_items=config.max_items_per_source,
        )
        changed = store.upsert_source(
            source_type=source_type,
            path=str(path),
            modified_at=datetime.fromtimestamp(stat.st_mtime)
            .astimezone()
            .isoformat(timespec="seconds"),
            content_hash=file_hash(path),
            repo_names=related_repos,
            summary=summary,
        )
        if changed:
            changed_count += 1

    return changed_count


def _discover_note_files(notes_path: Path, cutoff: datetime) -> list[Path]:
    files: list[Path] = []
    for path in notes_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SESSION_EXTENSIONS:
            continue
        if path.name == ".gitkeep":
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        if modified >= cutoff:
            files.append(path)
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
