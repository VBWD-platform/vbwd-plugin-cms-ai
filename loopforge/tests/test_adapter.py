"""Unit tests for the LoopForge LLM adapters (SDK clients mocked, no network)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from loopforge.adapter import (
    AnthropicAdapter,
    OpenAiAdapter,
    select_adapter,
)
from loopforge.errors import AdapterError, InvalidJsonError


# --- select_adapter routing -------------------------------------------------


def test_select_adapter_routes_claude_to_anthropic():
    adapter = select_adapter("claude-3-5-sonnet-latest", api_key="k")
    assert isinstance(adapter, AnthropicAdapter)


def test_select_adapter_routes_everything_else_to_openai():
    assert isinstance(select_adapter("gpt-4o-mini", api_key="k"), OpenAiAdapter)
    assert isinstance(select_adapter("mistral-large", api_key="k"), OpenAiAdapter)


# --- OpenAI payload build + parse -------------------------------------------


def _openai_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def test_openai_builds_json_payload_and_parses_content():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _openai_response(
        '{"title": "Hi"}'
    )

    with patch("loopforge.adapter.OpenAI", return_value=fake_client):
        adapter = OpenAiAdapter(
            api_key="secret-key", endpoint="https://api.example.com"
        )
        result = adapter.generate(
            "system text",
            "user text",
            model="gpt-4o-mini",
            temperature=0.5,
            json_schema={"title": "string|null"},
            json_retry_max=3,
        )

    assert result == {"title": "Hi"}
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["response_format"] == {"type": "json_object"}
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["messages"][0]["role"] == "system"
    assert call_kwargs["messages"][1]["role"] == "user"


def test_openai_sdk_error_becomes_loopforge_error():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("boom")

    with patch("loopforge.adapter.OpenAI", return_value=fake_client):
        adapter = OpenAiAdapter(api_key="secret-key")
        with pytest.raises(AdapterError):
            adapter.generate("s", "u", model="gpt-4o-mini", temperature=0.5)


def test_openai_repair_loop_reprompts_then_raises():
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _openai_response("not json")

    with patch("loopforge.adapter.OpenAI", return_value=fake_client):
        adapter = OpenAiAdapter(api_key="secret-key")
        with pytest.raises(InvalidJsonError):
            adapter.generate(
                "s", "u", model="gpt-4o-mini", temperature=0.5, json_retry_max=2
            )

    assert fake_client.chat.completions.create.call_count == 2


def test_openai_never_echoes_key_in_error():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("transport down")

    with patch("loopforge.adapter.OpenAI", return_value=fake_client):
        adapter = OpenAiAdapter(api_key="super-secret-key-123")
        try:
            adapter.generate("s", "u", model="gpt-4o-mini", temperature=0.5)
        except AdapterError as error:
            assert "super-secret-key-123" not in str(error)
            assert "super-secret-key-123" not in repr(error)


# --- Anthropic payload build + parse ----------------------------------------


def _anthropic_tool_use_response(tool_input: dict):
    block = SimpleNamespace(type="tool_use", input=tool_input)
    return SimpleNamespace(content=[block])


def _anthropic_text_response(text: str):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


def test_anthropic_builds_tool_payload_and_parses_tool_input():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_tool_use_response(
        {"meta_title": "SEO Title"}
    )

    with patch("loopforge.adapter.anthropic.Anthropic", return_value=fake_client):
        adapter = AnthropicAdapter(api_key="secret-key")
        result = adapter.generate(
            "system text",
            "user text",
            model="claude-3-5-sonnet-latest",
            temperature=0.3,
            json_schema={"meta_title": "string|null"},
        )

    assert result == {"meta_title": "SEO Title"}
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"]["type"] == "tool"
    assert call_kwargs["tools"][0]["name"] == call_kwargs["tool_choice"]["name"]
    assert call_kwargs["system"] == "system text"


def test_anthropic_parses_text_block_when_no_tool_use():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _anthropic_text_response(
        '{"excerpt": "from text"}'
    )

    with patch("loopforge.adapter.anthropic.Anthropic", return_value=fake_client):
        adapter = AnthropicAdapter(api_key="secret-key")
        result = adapter.generate("s", "u", model="claude-3-haiku", temperature=0.3)

    assert result == {"excerpt": "from text"}


def test_anthropic_sdk_error_becomes_loopforge_error():
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("api down")

    with patch("loopforge.adapter.anthropic.Anthropic", return_value=fake_client):
        adapter = AnthropicAdapter(api_key="secret-key")
        with pytest.raises(AdapterError):
            adapter.generate("s", "u", model="claude-3-haiku", temperature=0.3)
