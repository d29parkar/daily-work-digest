from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool
    recipient: str
    subject_prefix: str
    sender_env: str
    smtp_host: str
    smtp_port: int
    smtp_username_env: str
    smtp_password_env: str
    smtp_use_tls: bool


@dataclass(frozen=True)
class ModelConfig:
    provider: str  # auto | openai | local_rules | fixture
    name: str
    api_key_env: str
    max_prompt_chars: int
    max_output_tokens: int
    timeout_seconds: int
    fixture_path: str


@dataclass(frozen=True)
class IntegrationsConfig:
    gmail_enabled: bool
    calendar_enabled: bool


@dataclass(frozen=True)
class ModelsV2Config:
    """Per-role model selection for the v2 pipeline (DESIGN_V2.md 3.2/3.5)."""

    provider: str  # auto | openai | anthropic | fixture | none
    extract: str
    state: str
    render: str
    openai_api_key_env: str
    anthropic_api_key_env: str
    fixture_dir: str
    timeout_seconds: int


@dataclass(frozen=True)
class PipelineConfig:
    engine: str  # v1 | v2
    retire_after_days: int
    trello_skill_paths: list[Path]
    registry_export_path: Path


@dataclass(frozen=True)
class DigestConfig:
    config_path: Path
    project_root: Path
    parent_dir: Path
    repos: dict[str, Path]
    primary_repo: str
    claude_paths: list[Path]
    claude_lookback_hours: int
    codex_paths: list[Path]
    codex_lookback_hours: int
    notes_path: Path
    notes_lookback_hours: int
    output_path: Path
    sqlite_path: Path
    max_transcript_chars: int
    max_items_per_source: int
    max_sources: int
    exclude_session_markers: list[str]
    email: EmailConfig
    model: ModelConfig
    integrations: IntegrationsConfig
    models_v2: ModelsV2Config
    pipeline: PipelineConfig

    # Backwards-compatible accessors used by older call sites.
    @property
    def model_provider(self) -> str:
        return self.model.provider

    @property
    def model_name(self) -> str:
        return self.model.name


class ConfigError(RuntimeError):
    pass


def load_config(config_path: str | Path | None = None) -> DigestConfig:
    path = _resolve_config_path(config_path)
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}. "
            "Copy config.example.yaml to config.yaml or pass --config."
        )
    try:
        raw = _load_yaml_subset(path)
    except Exception as exc:
        raise ConfigError(f"Could not parse config file {path}: {exc}") from exc

    parent_dir = _resolve_path(raw.get("parent_dir", PROJECT_ROOT.parent), PROJECT_ROOT)
    repos = {
        name: _resolve_path(repo_path, parent_dir)
        for name, repo_path in dict(raw.get("repos", {})).items()
    }

    claude = dict(raw.get("claude", {}))
    codex = dict(raw.get("codex", {}))
    notes = dict(raw.get("notes", {}))
    output = dict(raw.get("output", {}))
    data = dict(raw.get("data", {}))
    summary = dict(raw.get("summary", {}))
    filters = dict(raw.get("filters", {}))
    email = dict(raw.get("email", {}))
    smtp = dict(email.get("smtp", {}))
    model = dict(raw.get("model", {}))
    models_v2 = dict(raw.get("models", {}))
    pipeline = dict(raw.get("pipeline", {}))
    integrations = dict(raw.get("integrations", {}))
    gmail = dict(integrations.get("gmail", {}))
    calendar = dict(integrations.get("calendar", {}))

    return DigestConfig(
        config_path=path,
        project_root=PROJECT_ROOT,
        parent_dir=parent_dir,
        repos=repos,
        primary_repo=str(raw.get("primary_repo", next(iter(repos), ""))),
        claude_paths=[
            _resolve_path(p, PROJECT_ROOT) for p in list(claude.get("paths", []))
        ],
        claude_lookback_hours=int(claude.get("lookback_hours", 48)),
        codex_paths=[_resolve_path(p, PROJECT_ROOT) for p in list(codex.get("paths", []))],
        codex_lookback_hours=int(codex.get("lookback_hours", 48)),
        notes_path=_resolve_path(notes.get("path", "notes"), PROJECT_ROOT),
        notes_lookback_hours=int(notes.get("lookback_hours", 168)),
        output_path=_resolve_path(output.get("path", "outputs/digests"), PROJECT_ROOT),
        sqlite_path=_resolve_path(data.get("sqlite_path", "data/digest.sqlite"), PROJECT_ROOT),
        max_transcript_chars=int(summary.get("max_transcript_chars", 60000)),
        max_items_per_source=int(summary.get("max_items_per_source", 12)),
        max_sources=int(summary.get("max_sources", 20)),
        exclude_session_markers=[
            str(item).lower()
            for item in list(
                filters.get("exclude_session_markers", ["daily-work-digest"])
            )
        ],
        email=EmailConfig(
            enabled=bool(email.get("enabled", False)),
            recipient=str(email.get("recipient", "")),
            subject_prefix=str(email.get("subject_prefix", "Daily Work Digest")),
            sender_env=str(email.get("sender_env", "DIGEST_EMAIL_FROM")),
            smtp_host=str(smtp.get("host", "smtp.gmail.com")),
            smtp_port=int(smtp.get("port", 587)),
            smtp_username_env=str(smtp.get("username_env", "DIGEST_SMTP_USERNAME")),
            smtp_password_env=str(smtp.get("password_env", "DIGEST_SMTP_PASSWORD")),
            smtp_use_tls=bool(smtp.get("use_tls", True)),
        ),
        model=ModelConfig(
            provider=str(model.get("provider", "auto")).lower(),
            name=str(model.get("name", "gpt-4o-mini")),
            api_key_env=str(model.get("api_key_env", "OPENAI_API_KEY")),
            max_prompt_chars=int(model.get("max_prompt_chars", 24000)),
            max_output_tokens=int(model.get("max_output_tokens", 2000)),
            timeout_seconds=int(model.get("timeout_seconds", 120)),
            fixture_path=str(model.get("fixture_path", "")),
        ),
        integrations=IntegrationsConfig(
            gmail_enabled=bool(gmail.get("enabled", False)),
            calendar_enabled=bool(calendar.get("enabled", False)),
        ),
        models_v2=ModelsV2Config(
            provider=str(models_v2.get("provider", "auto")).lower(),
            extract=str(models_v2.get("extract", "gpt-4o-mini")),
            state=str(models_v2.get("state", "gpt-4o")),
            render=str(models_v2.get("render", "gpt-4o-mini")),
            openai_api_key_env=str(
                models_v2.get("openai_api_key_env", "OPENAI_API_KEY")
            ),
            anthropic_api_key_env=str(
                models_v2.get("anthropic_api_key_env", "ANTHROPIC_API_KEY")
            ),
            fixture_dir=str(models_v2.get("fixture_dir", "")),
            timeout_seconds=int(models_v2.get("timeout_seconds", 120)),
        ),
        pipeline=PipelineConfig(
            engine=str(pipeline.get("engine", "v1")).lower(),
            retire_after_days=int(pipeline.get("retire_after_days", 21)),
            trello_skill_paths=[
                _resolve_path(p, PROJECT_ROOT)
                for p in list(pipeline.get("trello_skill_paths", []))
            ],
            registry_export_path=_resolve_path(
                pipeline.get("registry_export_path", "data/registry.md"), PROJECT_ROOT
            ),
        ),
    )


VALID_PROVIDERS = {"auto", "openai", "local_rules", "fixture"}


def validate_config(config: DigestConfig) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) describing config problems.

    Errors block generation; warnings mean the digest still runs but with
    degraded coverage, and each one is reported in the digest itself.
    """
    import os as _os

    errors: list[str] = []
    warnings: list[str] = []

    if config.model.provider not in VALID_PROVIDERS:
        errors.append(
            f"model.provider must be one of {sorted(VALID_PROVIDERS)}, "
            f"got {config.model.provider!r}."
        )
    if config.model.provider == "fixture" and not config.model.fixture_path:
        errors.append("model.provider is 'fixture' but model.fixture_path is empty.")
    if config.model.provider == "openai" and not _os.environ.get(config.model.api_key_env):
        warnings.append(
            f"model.provider is 'openai' but {config.model.api_key_env} is not set; "
            "the local rule-based fallback will be used."
        )

    if not config.repos:
        errors.append("No repos configured; add at least one under 'repos:'.")
    if config.primary_repo and config.repos and config.primary_repo not in config.repos:
        warnings.append(
            f"primary_repo '{config.primary_repo}' is not in repos; "
            "the first listed repo will still be treated as primary by default."
        )
    for name, path in config.repos.items():
        if not path.exists():
            warnings.append(f"Repo path for '{name}' does not exist: {path}")

    for label, paths in (("claude", config.claude_paths), ("codex", config.codex_paths)):
        if not paths:
            warnings.append(f"No {label} paths configured; {label} sessions will be skipped.")
        elif not any(p.exists() for p in paths):
            warnings.append(f"None of the configured {label} paths exist; {label} sessions will be skipped.")

    if config.email.enabled and not config.email.recipient:
        errors.append("email.enabled is true but email.recipient is empty.")

    if config.claude_lookback_hours <= 0 or config.codex_lookback_hours <= 0:
        errors.append("lookback_hours values must be positive.")

    return errors, warnings


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path:
        return Path(config_path).expanduser().resolve()

    local_config = PROJECT_ROOT / "config.yaml"
    if local_config.exists():
        return local_config

    return PROJECT_ROOT / "config.example.yaml"


def _resolve_path(value: Any, base: Path) -> Path:
    raw = os.path.expandvars(os.path.expanduser(str(value)))
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _load_yaml_subset(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except Exception:
        yaml = None

    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        loaded = yaml.safe_load(text)
        return dict(loaded or {})

    lines = _preprocess_yaml_lines(text)
    parsed, _ = _parse_yaml_block(lines, 0, 0)
    return dict(parsed or {})


def _preprocess_yaml_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        raw = _strip_inline_comment(raw)
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))
    return lines


def _strip_inline_comment(line: str) -> str:
    """Drop ' # ...' comments that are not inside a quoted value."""
    in_single = in_double = False
    for index, char in enumerate(line):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif (
            char == "#"
            and not in_single
            and not in_double
            and index > 0
            and line[index - 1] in " \t"
        ):
            return line[:index]
    return line


def _parse_yaml_block(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[Any, int]:
    if index >= len(lines) or lines[index][0] < indent:
        return {}, index

    if lines[index][0] == indent and lines[index][1].startswith("- "):
        return _parse_yaml_list(lines, index, indent)

    return _parse_yaml_dict(lines, index, indent)


def _parse_yaml_dict(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, stripped = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            break
        if stripped.startswith("- "):
            break
        if ":" not in stripped:
            index += 1
            continue

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not raw_value:
            value, index = _parse_yaml_block(lines, index + 1, indent + 2)
            result[key] = value
        else:
            result[key] = _parse_yaml_scalar(raw_value)
            index += 1

    return result, index


def _parse_yaml_list(
    lines: list[tuple[int, str]], index: int, indent: int
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, stripped = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent or not stripped.startswith("- "):
            break

        raw_value = stripped[2:].strip()
        if not raw_value:
            value, index = _parse_yaml_block(lines, index + 1, indent + 2)
            result.append(value)
        else:
            result.append(_parse_yaml_scalar(raw_value))
            index += 1

    return result, index


def _parse_yaml_scalar(value: str) -> Any:
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(item.strip()) for item in inner.split(",")]
    if value == "{}":
        return {}
    lowered = value.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value
