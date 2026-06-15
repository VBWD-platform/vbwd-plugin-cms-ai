"""Unit tests for LoopForge JSON parsing, cleaning and the repair/retry loop."""

import pytest

from loopforge.errors import InvalidJsonError
from loopforge.json_io import (
    clean_json_text,
    is_valid_json,
    parse_json_object,
    request_valid_json,
)


def test_clean_strips_markdown_fences():
    fenced = '```json\n{"a": 1}\n```'
    assert clean_json_text(fenced) == '{"a": 1}'


def test_parse_returns_object_for_valid_json():
    assert parse_json_object('{"content_html": "<p>x</p>"}') == {
        "content_html": "<p>x</p>"
    }


def test_parse_rejects_non_object_json():
    assert parse_json_object("[1, 2, 3]") is None
    assert parse_json_object('"just a string"') is None
    assert parse_json_object("42") is None


def test_is_valid_json_true_and_false():
    assert is_valid_json('{"k": "v"}') is True
    assert is_valid_json("not json at all") is False


def test_repair_loop_returns_first_valid_object():
    def _generate(_repair_instruction):
        return '{"title": "ok"}'

    result = request_valid_json(_generate, json_schema=None, max_attempts=3)
    assert result == {"title": "ok"}


def test_repair_loop_reprompts_until_valid():
    attempts = {"count": 0}

    def _generate(repair_instruction):
        attempts["count"] += 1
        if attempts["count"] == 1:
            assert repair_instruction is None
            return "garbage, not json"
        assert repair_instruction is not None  # repair instruction passed on retry
        return '{"title": "recovered"}'

    result = request_valid_json(_generate, json_schema=None, max_attempts=3)
    assert result == {"title": "recovered"}
    assert attempts["count"] == 2


def test_repair_loop_raises_after_max_attempts():
    calls = {"count": 0}

    def _generate(_repair_instruction):
        calls["count"] += 1
        return "never valid"

    with pytest.raises(InvalidJsonError):
        request_valid_json(_generate, json_schema=None, max_attempts=3)
    assert calls["count"] == 3


def test_repair_loop_rejects_zero_attempts():
    with pytest.raises(ValueError):
        request_valid_json(lambda _instruction: "{}", json_schema=None, max_attempts=0)
