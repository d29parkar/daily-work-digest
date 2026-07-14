from __future__ import annotations

from pathlib import Path

import pytest

from conftest import write_config
from digest.config import load_config
from digest.llm_v2 import (
    AnthropicLLM,
    FixtureLLM,
    LLMError,
    LLMUnavailable,
    NoneLLM,
    OpenAILLM,
    parse_json_response,
    resolve_llm,
)


def test_parse_json_response_handles_fences_and_prose():
    assert parse_json_response('{"a": 1}') == {"a": 1}
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_response('Here you go:\n{"a": {"b": 2}}\nDone.') == {"a": {"b": 2}}
    with pytest.raises(LLMError):
        parse_json_response("no json here")
    with pytest.raises(LLMError):
        parse_json_response('[1, 2]')


def test_fixture_llm_reads_stage_files(tmp_path: Path):
    (tmp_path / "extract.json").write_text('{"work_units": []}', encoding="utf-8")
    llm = FixtureLLM(tmp_path)
    assert llm.complete_json(
        system="s", user="u", max_output_tokens=10, stage="extract"
    ) == {"work_units": []}
    with pytest.raises(LLMError):
        llm.complete_text(system="s", user="u", max_output_tokens=10, stage="missing")


def test_none_llm_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        NoneLLM().complete_text(system="s", user="u", max_output_tokens=10, stage="x")


def _config(tmp_path: Path, extra: str):
    return load_config(write_config(tmp_path, extra=extra))


def test_resolve_llm_auto_prefers_openai(tmp_path: Path, monkeypatch):
    config = _config(tmp_path, "models:\n  provider: auto\n  extract: gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
    llm = resolve_llm(config, "extract")
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "gpt-4o-mini"


def test_resolve_llm_auto_falls_to_anthropic_with_model_swap(tmp_path: Path, monkeypatch):
    config = _config(tmp_path, "models:\n  provider: auto\n  state: gpt-4o")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-test")
    llm = resolve_llm(config, "state")
    assert isinstance(llm, AnthropicLLM)
    assert llm.model == "claude-sonnet-5"  # gpt name would 404 on Anthropic


def test_resolve_llm_auto_without_keys_is_unavailable(tmp_path: Path, monkeypatch):
    config = _config(tmp_path, "models:\n  provider: auto")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable):
        resolve_llm(config, "extract")


def test_resolve_llm_roles_pick_their_models(tmp_path: Path, monkeypatch):
    config = _config(
        tmp_path,
        "models:\n  provider: openai\n  extract: gpt-4o-mini\n  state: gpt-4o\n  render: gpt-4o-mini",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert resolve_llm(config, "state").model == "gpt-4o"
    assert resolve_llm(config, "render").model == "gpt-4o-mini"


def test_config_defaults_keep_v1_engine(tmp_path: Path):
    config = load_config(write_config(tmp_path))
    assert config.pipeline.engine == "v1"
    assert config.models_v2.provider == "auto"
