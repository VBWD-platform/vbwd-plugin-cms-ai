"""LLM adapters — one ``generate()`` surface, two provider implementations.

``LlmAdapter`` is the narrow, Liskov-substitutable contract every provider
honours: ``generate(system_content, user_content, *, model, temperature,
json_schema) -> dict``. Two concrete adapters drive the providers via their
official Python SDKs:

* :class:`OpenAiAdapter` — the ``openai`` SDK, JSON forced with
  ``response_format={"type": "json_object"}``.
* :class:`AnthropicAdapter` — the ``anthropic`` SDK, JSON forced with a single
  tool plus ``tool_choice``.

``select_adapter(model)`` is the only place that maps a model name to a
provider: ``claude-*`` -> Anthropic, everything else -> OpenAI. The model name
is the sole discriminator, so callers never branch on provider.

Both adapters route every call through the shared JSON validate/repair/retry
loop and surface any SDK/transport failure as :class:`AdapterError`. The API
key is never echoed into an error or return value.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Optional

import anthropic
from openai import OpenAI

from .errors import AdapterError, LoopForgeError
from .json_io import request_valid_json

_DEFAULT_MAX_TOKENS = 4096
_ANTHROPIC_MODEL_PREFIX = "claude-"
_JSON_TOOL_NAME = "emit_json"


class LlmAdapter(ABC):
    """One generate() surface; provider-specific subclasses implement it."""

    def __init__(
        self, *, api_key: str, endpoint: str = "", max_tokens: int = _DEFAULT_MAX_TOKENS
    ) -> None:
        self._api_key = api_key
        self._endpoint = endpoint
        self._max_tokens = max_tokens

    @abstractmethod
    def generate(
        self,
        system_content: str,
        user_content: str,
        *,
        model: str,
        temperature: float,
        json_schema: Optional[dict] = None,
        json_retry_max: int = 3,
    ) -> dict:
        """Return the model's reply parsed into a JSON object.

        Raises :class:`AdapterError` on SDK/transport failure and
        :class:`InvalidJsonError` when no valid JSON is produced in
        ``json_retry_max`` attempts.
        """
        raise NotImplementedError


class OpenAiAdapter(LlmAdapter):
    """OpenAI / OpenAI-compatible adapter using the ``openai`` SDK."""

    def _build_client(self) -> OpenAI:
        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._endpoint:
            client_kwargs["base_url"] = self._endpoint
        return OpenAI(**client_kwargs)

    def generate(
        self,
        system_content: str,
        user_content: str,
        *,
        model: str,
        temperature: float,
        json_schema: Optional[dict] = None,
        json_retry_max: int = 3,
    ) -> dict:
        client = self._build_client()

        def _call(repair_instruction: Optional[str]) -> str:
            user_text = user_content
            if repair_instruction:
                user_text = f"{user_content}\n\n{repair_instruction}"
            request_kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_text},
                ],
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            try:
                response = client.chat.completions.create(**request_kwargs)
            except Exception as sdk_error:
                raise AdapterError("OpenAI request failed") from sdk_error
            return _extract_openai_content(response)

        return _run_repair_loop(
            _call, json_schema=json_schema, max_attempts=json_retry_max
        )


class AnthropicAdapter(LlmAdapter):
    """Anthropic adapter using the ``anthropic`` SDK; JSON forced via tool-use."""

    def _build_client(self) -> "anthropic.Anthropic":
        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._endpoint:
            client_kwargs["base_url"] = self._endpoint
        return anthropic.Anthropic(**client_kwargs)

    def generate(
        self,
        system_content: str,
        user_content: str,
        *,
        model: str,
        temperature: float,
        json_schema: Optional[dict] = None,
        json_retry_max: int = 3,
    ) -> dict:
        client = self._build_client()
        tool_definition = _build_json_tool(json_schema)

        def _call(repair_instruction: Optional[str]) -> str:
            user_text = user_content
            if repair_instruction:
                user_text = f"{user_content}\n\n{repair_instruction}"
            request_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": self._max_tokens,
                "temperature": temperature,
                "system": system_content,
                "tools": [tool_definition],
                "tool_choice": {"type": "tool", "name": _JSON_TOOL_NAME},
                "messages": [{"role": "user", "content": user_text}],
            }
            try:
                response = client.messages.create(**request_kwargs)
            except Exception as sdk_error:
                raise AdapterError("Anthropic request failed") from sdk_error
            return _extract_anthropic_content(response)

        return _run_repair_loop(
            _call, json_schema=json_schema, max_attempts=json_retry_max
        )


def select_adapter(model: str, **adapter_kwargs: Any) -> LlmAdapter:
    """Return the adapter for ``model`` (model name is the only discriminator)."""
    if model.startswith(_ANTHROPIC_MODEL_PREFIX):
        return AnthropicAdapter(**adapter_kwargs)
    return OpenAiAdapter(**adapter_kwargs)


def _run_repair_loop(call, *, json_schema, max_attempts) -> dict:
    """Drive the shared JSON repair/retry loop, normalising any leak to LoopForgeError."""
    try:
        return request_valid_json(
            call, json_schema=json_schema, max_attempts=max_attempts
        )
    except LoopForgeError:
        raise
    except Exception as unexpected_error:
        raise AdapterError("LLM generation failed") from unexpected_error


def _build_json_tool(json_schema: Optional[dict]) -> dict:
    """Build the single Anthropic tool that forces a JSON-object reply."""
    input_schema: dict[str, Any] = {"type": "object"}
    if json_schema:
        input_schema = {
            "type": "object",
            "properties": _schema_to_properties(json_schema),
        }
    return {
        "name": _JSON_TOOL_NAME,
        "description": "Return the requested CMS fields as a single JSON object.",
        "input_schema": input_schema,
    }


def _schema_to_properties(json_schema: dict) -> dict:
    """Translate the lightweight ``{field: 'type|null'}`` schema to JSON-schema props."""
    properties: dict[str, Any] = {}
    for field_name, field_type in json_schema.items():
        properties[field_name] = {"type": _normalise_schema_type(field_type)}
    return properties


def _normalise_schema_type(field_type: Any) -> str:
    """Map a manifest type token (e.g. ``'string|null'``) to a JSON-schema type."""
    if isinstance(field_type, dict):
        field_type = field_type.get("type", "string")
    type_token = str(field_type).split("|", 1)[0].strip().lower()
    if type_token in {"object", "number", "integer", "boolean", "array", "string"}:
        return type_token
    return "string"


def _extract_openai_content(response: Any) -> str:
    """Pull the assistant text out of an OpenAI chat-completion response."""
    try:
        return response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError) as parse_error:
        raise AdapterError("OpenAI response had no content") from parse_error


def _extract_anthropic_content(response: Any) -> str:
    """Pull the JSON out of an Anthropic response (tool-use input or text)."""
    content_blocks = getattr(response, "content", None)
    if not content_blocks:
        raise AdapterError("Anthropic response had no content")
    for block in content_blocks:
        if getattr(block, "type", None) == "tool_use":
            return json.dumps(getattr(block, "input", {}))
    first_block = content_blocks[0]
    text_value = getattr(first_block, "text", None)
    if text_value is not None:
        return text_value
    raise AdapterError("Anthropic response had no usable content")


__all__ = [
    "LlmAdapter",
    "OpenAiAdapter",
    "AnthropicAdapter",
    "select_adapter",
]
