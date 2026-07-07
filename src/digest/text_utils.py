from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SESSION_EXTENSIONS = {".jsonl", ".json", ".md", ".txt", ".log"}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_session_text(path: Path, max_chars: int) -> str:
    if path.suffix.lower() == ".jsonl":
        text = _read_jsonl_text(path)
    elif path.suffix.lower() == ".json":
        text = _read_json_text(path)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return trim_middle(text, max_chars)


def trim_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 3
    tail_chars = max_chars - head_chars
    return (
        text[:head_chars]
        + "\n\n[...middle of long transcript omitted...]\n\n"
        + text[-tail_chars:]
    )


def is_related_to_repos(text: str, repo_names: list[str], repo_paths: list[Path]) -> bool:
    lowered = text.lower()
    compact = compact_key(text)
    for repo_name in repo_names:
        if repo_name.lower() in lowered or compact_key(repo_name) in compact:
            return True
    for repo_path in repo_paths:
        raw_path = str(repo_path).lower()
        if raw_path in lowered or compact_key(raw_path) in compact:
            return True
    return False


def compact_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def dedupe_keep_order(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = normalize_snippet(item)
        if not clean:
            continue
        key = re.sub(r"\s+", " ", clean.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= limit:
            break
    return result


def normalize_snippet(value: str, max_len: int = 260) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    clean = clean.replace("\\n", " ").replace("\\t", " ")
    if not clean:
        return ""
    if len(clean) > max_len:
        clean = clean[: max_len - 3].rstrip() + "..."
    return clean


def _read_jsonl_text(path: Path) -> str:
    chunks: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                chunks.append(stripped)
                continue
            chunks.extend(_flatten_json_strings(payload))
    return "\n".join(chunks)


def _read_json_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return "\n".join(_flatten_json_strings(payload))


def _flatten_json_strings(value: Any, key_hint: str = "") -> list[str]:
    strings: list[str] = []
    interesting_keys = {
        "content",
        "text",
        "summary",
        "cwd",
        "command",
        "output",
        "error",
        "message",
        "role",
        "path",
        "file",
        "title",
    }

    if isinstance(value, dict):
        role = value.get("role")
        message_type = value.get("type")
        if isinstance(role, str) and role in {"system", "developer", "tool"}:
            return []
        if isinstance(message_type, str) and message_type in {
            "system",
            "developer",
            "tool_result",
            "function_call_output",
        }:
            return []
        for key, nested in value.items():
            if key in {"system", "developer", "instructions"}:
                continue
            if key not in interesting_keys and not isinstance(nested, (dict, list)):
                continue
            flattened = _flatten_json_strings(nested, str(key))
            if role and key in {"content", "text", "message"}:
                flattened = [f"{role}: {item}" for item in flattened]
            strings.extend(flattened)
    elif isinstance(value, list):
        for nested in value:
            strings.extend(_flatten_json_strings(nested, key_hint))
    elif isinstance(value, str):
        if key_hint in interesting_keys:
            strings.append(value)
    elif value is not None and key_hint in interesting_keys:
        strings.append(str(value))

    return strings
