"""LLM client layer for the v2 pipeline.

Small, role-based abstraction (DESIGN_V2.md 3.2/3.5): each pipeline stage asks
for a client by role (``extract`` / ``state`` / ``render``) and gets whichever
provider the config resolves to. Stdlib HTTP only, no SDK dependency, same as
v1's llm.py.

Providers:
- ``openai``:    Chat Completions with JSON response_format for JSON calls.
- ``anthropic``: Messages API; JSON enforced by instruction and parsed.
- ``fixture``:   canned responses from a directory, keyed by stage name; used
                 by tests and offline demos.
- ``none``:      always raises LLMUnavailable (degraded mode: the pipeline
                 falls back to deterministic behavior per stage).
- ``auto``:      openai if its key is set, else anthropic if set, else none.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import DigestConfig

logger = logging.getLogger(__name__)

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"

JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """No provider is configured/reachable; callers degrade deterministically."""


class BaseLLM:
    name = "base"

    def __init__(self, model: str):
        self.model = model

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    def complete_text(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> str:
        raise NotImplementedError

    def complete_json(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> dict[str, Any]:
        text = self.complete_text(
            system=system, user=user, max_output_tokens=max_output_tokens, stage=stage
        )
        return parse_json_response(text)


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = JSON_FENCE_RE.sub("", text.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"Response contains no JSON object: {cleaned[:200]!r}")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"Response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("Response JSON is not an object.")
    return parsed


class OpenAILLM(BaseLLM):
    name = "openai"

    def __init__(self, model: str, api_key: str, timeout_seconds: int = 120):
        super().__init__(model)
        if not api_key:
            raise LLMUnavailable("OpenAI API key is empty.")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def complete_text(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> str:
        return self._chat(system, user, max_output_tokens, json_mode=False)

    def complete_json(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> dict[str, Any]:
        return parse_json_response(
            self._chat(system, user, max_output_tokens, json_mode=True)
        )

    def _chat(
        self, system: str, user: str, max_output_tokens: int, *, json_mode: bool
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = _post_json(
            OPENAI_CHAT_URL,
            payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI response shape: {exc}") from exc
        if not content or not content.strip():
            raise LLMError("OpenAI returned an empty completion.")
        return content


class AnthropicLLM(BaseLLM):
    name = "anthropic"

    def __init__(self, model: str, api_key: str, timeout_seconds: int = 120):
        super().__init__(model)
        if not api_key:
            raise LLMUnavailable("Anthropic API key is empty.")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def complete_text(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> str:
        payload = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        data = _post_json(
            ANTHROPIC_MESSAGES_URL,
            payload,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=self.timeout_seconds,
        )
        try:
            blocks = data["content"]
            text = "".join(
                block.get("text", "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response shape: {exc}") from exc
        if not text.strip():
            raise LLMError("Anthropic returned an empty completion.")
        return text


class FixtureLLM(BaseLLM):
    """Reads <stage>.json / <stage>.md files from a directory. Offline tests."""

    name = "fixture"

    def __init__(self, fixture_dir: Path):
        super().__init__("fixture")
        self.fixture_dir = fixture_dir

    def complete_text(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> str:
        for suffix in (".md", ".txt", ".json"):
            path = self.fixture_dir / f"{stage}{suffix}"
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")
        raise LLMError(f"No fixture for stage {stage!r} in {self.fixture_dir}")


class NoneLLM(BaseLLM):
    name = "none"

    def __init__(self):
        super().__init__("none")

    def complete_text(
        self, *, system: str, user: str, max_output_tokens: int, stage: str
    ) -> str:
        raise LLMUnavailable("No LLM provider configured (models.provider: none).")


def _post_json(
    url: str, payload: dict[str, Any], *, headers: dict[str, str], timeout: int
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            detail = str(
                body.get("error", {}).get("message", "") or body.get("message", "")
            )
        except Exception:
            pass
        raise LLMError(f"API returned HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"Could not reach the API: {exc}") from exc


def resolve_llm(config: DigestConfig, role: str) -> BaseLLM:
    """Pick the client for a pipeline role: 'extract', 'state', or 'render'.

    Raises LLMUnavailable when no provider can serve the role; stages catch it
    and degrade deterministically.
    """
    models = config.models_v2
    model_name = {
        "extract": models.extract,
        "state": models.state,
        "render": models.render,
    }.get(role, models.extract)

    provider = models.provider
    openai_key = os.environ.get(models.openai_api_key_env, "")
    anthropic_key = os.environ.get(models.anthropic_api_key_env, "")

    if provider == "fixture":
        fixture_dir = Path(models.fixture_dir)
        if not fixture_dir.is_absolute():
            fixture_dir = config.project_root / fixture_dir
        return FixtureLLM(fixture_dir)
    if provider == "none":
        return NoneLLM()
    if provider == "openai":
        return OpenAILLM(model_name, openai_key, models.timeout_seconds)
    if provider == "anthropic":
        return AnthropicLLM(model_name, anthropic_key, models.timeout_seconds)
    if provider == "auto":
        if openai_key:
            return OpenAILLM(model_name, openai_key, models.timeout_seconds)
        if anthropic_key:
            # Model names are provider-specific; when auto falls through to
            # Anthropic, a GPT-name would 404, so use a sensible default.
            if model_name.startswith("gpt"):
                model_name = "claude-sonnet-5"
            return AnthropicLLM(model_name, anthropic_key, models.timeout_seconds)
        raise LLMUnavailable(
            f"models.provider is 'auto' but neither {models.openai_api_key_env} "
            f"nor {models.anthropic_api_key_env} is set."
        )
    raise LLMUnavailable(f"Unknown models.provider {provider!r}.")
