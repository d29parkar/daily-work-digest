from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .config import DigestConfig
from .store import DigestStore
from .summarize import summarize_text_source
from .text_utils import (
    SESSION_EXTENSIONS,
    file_hash,
    is_related_to_repos,
    read_session_text,
)


MAX_SESSION_BYTES = 50 * 1024 * 1024


def ingest_claude(config: DigestConfig, store: DigestStore) -> int:
    return _ingest_sessions(
        source_type="claude",
        paths=config.claude_paths,
        lookback_hours=config.claude_lookback_hours,
        config=config,
        store=store,
    )


def ingest_codex(config: DigestConfig, store: DigestStore) -> int:
    return _ingest_sessions(
        source_type="codex",
        paths=config.codex_paths,
        lookback_hours=config.codex_lookback_hours,
        config=config,
        store=store,
    )


def _ingest_sessions(
    *,
    source_type: str,
    paths: list[Path],
    lookback_hours: int,
    config: DigestConfig,
    store: DigestStore,
) -> int:
    cutoff = datetime.now().astimezone() - timedelta(hours=lookback_hours)
    changed_count = 0
    repo_names = list(config.repos.keys())
    repo_paths = list(config.repos.values())

    for path in _discover_files(paths, cutoff):
        try:
            stat = path.stat()
            path_text = str(path)
            text = read_session_text(path, config.max_transcript_chars)
            haystack = f"{path_text}\n{text}"
            if _is_excluded(haystack, config.exclude_session_markers):
                continue
            if not is_related_to_repos(haystack, repo_names, repo_paths):
                continue
            related_repos = [
                repo_name
                for repo_name, repo_path in config.repos.items()
                if is_related_to_repos(haystack, [repo_name], [repo_path])
            ]
            summary = summarize_text_source(
                text=text,
                source_type=source_type,
                path=path,
                repo_names=related_repos or repo_names,
                max_items=config.max_items_per_source,
            )
            changed = store.upsert_source(
                source_type=source_type,
                path=str(path),
                modified_at=datetime.fromtimestamp(stat.st_mtime)
                .astimezone()
                .isoformat(timespec="seconds"),
                content_hash=file_hash(path),
                repo_names=related_repos or repo_names,
                summary=summary,
            )
            if changed:
                changed_count += 1
        except OSError:
            continue

    return changed_count


def _discover_files(paths: list[Path], cutoff: datetime) -> list[Path]:
    files: list[Path] = []
    for base in paths:
        if not base.exists():
            continue
        if base.is_file():
            candidates = [base]
        else:
            candidates = [
                path
                for path in base.rglob("*")
                if path.is_file() and path.suffix.lower() in SESSION_EXTENSIONS
            ]
        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_SESSION_BYTES:
                continue
            modified = datetime.fromtimestamp(stat.st_mtime).astimezone()
            if modified >= cutoff:
                files.append(path)
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def _is_excluded(text: str, markers: list[str]) -> bool:
    lowered = text.lower()
    return any(marker and marker in lowered for marker in markers)
