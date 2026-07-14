from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from digest.config import DigestConfig, load_config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_from_real_environment(monkeypatch):
    """Tests must never read the developer's real .env or send real email.

    - load_dotenv is a no-op inside tests.
    - Any attempt to open an SMTP connection fails the test. A test that needs
      to exercise a 'successful send' must monkeypatch digest.email_send itself.
    - Credentials are scrubbed from the ambient environment.
    """
    monkeypatch.setattr("digest.cli.load_dotenv", lambda root: [])

    class _ForbiddenSMTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "Test attempted a real SMTP connection. Patch digest.email_send instead."
            )

    monkeypatch.setattr("digest.email_send.smtplib.SMTP", _ForbiddenSMTP)
    for env in (
        "OPENAI_API_KEY",
        "DIGEST_EMAIL_FROM",
        "DIGEST_SMTP_USERNAME",
        "DIGEST_SMTP_PASSWORD",
    ):
        monkeypatch.delenv(env, raising=False)


def write_config(
    tmp_path: Path,
    *,
    provider: str = "local_rules",
    fixture_path: str = "",
    repos: dict[str, Path] | None = None,
    claude_paths: list[Path] | None = None,
    codex_paths: list[Path] | None = None,
    email_enabled: bool = True,
    extra: str = "",
) -> Path:
    """Write a minimal, fully-isolated config.yaml under tmp_path."""
    if repos is None:
        repos = {"my-api-repo": tmp_path / "repo-fastapi"}
    claude_paths = claude_paths if claude_paths is not None else [tmp_path / "claude"]
    codex_paths = codex_paths if codex_paths is not None else [tmp_path / "codex"]

    lines = [f"parent_dir: {tmp_path}", "repos:"]
    lines += [f"  {name}: {path}" for name, path in repos.items()]
    lines.append("claude:")
    if claude_paths:
        lines.append("  paths:")
        lines += [f"    - {p}" for p in claude_paths]
    else:
        lines.append("  paths: []")
    lines.append("  lookback_hours: 48")
    lines.append("codex:")
    if codex_paths:
        lines.append("  paths:")
        lines += [f"    - {p}" for p in codex_paths]
    else:
        lines.append("  paths: []")
    lines.append("  lookback_hours: 48")
    lines += [
        "notes:",
        f"  path: {tmp_path / 'notes'}",
        "  lookback_hours: 168",
        "output:",
        f"  path: {tmp_path / 'outputs'}",
        "data:",
        f"  sqlite_path: {tmp_path / 'digest.sqlite'}",
        "model:",
        f"  provider: {provider}",
        "  name: test-model",
    ]
    if fixture_path:
        lines.append(f"  fixture_path: {fixture_path}")
    lines += [
        "email:",
        f"  enabled: {'true' if email_enabled else 'false'}",
        "  recipient: test@example.com",
        "  subject_prefix: Test Digest",
    ]
    if extra:
        lines.append(extra)
    # Keep the registry mirror inside tmp_path unless the test overrides
    # pipeline: itself; the default would resolve into the real repo.
    if "registry_export_path" not in extra and "pipeline:" not in extra:
        lines += ["pipeline:", f"  registry_export_path: {tmp_path / 'registry.md'}"]
    config_path = tmp_path / "config.yaml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


@pytest.fixture
def temp_config(tmp_path: Path) -> DigestConfig:
    return load_config(write_config(tmp_path))
