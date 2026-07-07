from __future__ import annotations

from pathlib import Path

import pytest

from conftest import PROJECT_ROOT, write_config
from digest.config import ConfigError, load_config, validate_config


def test_example_config_loads():
    config = load_config(PROJECT_ROOT / "config.example.yaml")
    assert "my-api-repo" in config.repos
    assert config.model.provider == "auto"
    assert config.model.name == "gpt-4o-mini"
    assert config.integrations.gmail_enabled is False
    assert config.integrations.calendar_enabled is False
    assert config.email.smtp_host == "smtp.gmail.com"


def test_missing_config_file_raises():
    with pytest.raises(ConfigError):
        load_config(Path("does/not/exist.yaml"))


def test_defaults_applied(tmp_path):
    config = load_config(write_config(tmp_path))
    assert config.model.api_key_env == "OPENAI_API_KEY"
    assert config.model.max_prompt_chars == 24000
    assert config.claude_lookback_hours == 48


def test_validate_flags_missing_repo(tmp_path):
    config = load_config(write_config(tmp_path))
    errors, warnings = validate_config(config)
    assert not errors
    assert any("does not exist" in w for w in warnings)


def test_validate_fixture_without_path_is_error(tmp_path):
    config = load_config(write_config(tmp_path, provider="fixture"))
    errors, _ = validate_config(config)
    assert any("fixture_path" in e for e in errors)


def test_validate_bad_provider_is_error(tmp_path):
    config = load_config(write_config(tmp_path, provider="not-a-provider"))
    errors, _ = validate_config(config)
    assert any("model.provider" in e for e in errors)


def test_inline_empty_list_parses(tmp_path):
    config = load_config(write_config(tmp_path, claude_paths=[], codex_paths=[]))
    assert config.claude_paths == []
    assert config.codex_paths == []
