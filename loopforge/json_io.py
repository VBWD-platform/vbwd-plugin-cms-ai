"""JSON parsing, cleaning, schema-validation and the repair/retry loop.

Language models that are asked for JSON frequently wrap the object in a
```json fenced block or emit a leading prose sentence. This module isolates
the "coax a clean JSON object out of a model" behaviour so both LLM adapters
share one home for it (DRY), and exposes the retry loop that re-prompts the
model up to a configured number of attempts before giving up with a
:class:`InvalidJsonError`.

Pure standard-library; no provider SDK, no network, no filesystem.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from .errors import InvalidJsonError

_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def clean_json_text(raw_text: str) -> str:
    """Strip Markdown ```json fences and surrounding whitespace from ``raw_text``."""
    if raw_text is None:
        return ""
    return _FENCE_PATTERN.sub("", raw_text).strip()


def parse_json_object(raw_text: str) -> Optional[dict]:
    """Return the parsed JSON object, or ``None`` if it is not a valid object.

    A valid result is always a JSON *object* (``dict``); a bare array, number
    or string is treated as invalid for LoopForge's purposes.
    """
    cleaned_text = clean_json_text(raw_text)
    if not cleaned_text:
        return None
    try:
        parsed_value = json.loads(cleaned_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed_value, dict):
        return None
    return parsed_value


def is_valid_json(raw_text: str) -> bool:
    """True when ``raw_text`` cleans up to a parseable JSON object."""
    return parse_json_object(raw_text) is not None


def validate_against_schema(parsed_object: dict, json_schema: Optional[dict]) -> dict:
    """Return ``parsed_object`` unchanged when it satisfies ``json_schema``.

    The schema is the lightweight ``{field_name: "type|null"}`` shape LoopForge
    uses (the field manifest's response schema). Validation here only enforces
    JSON-shape correctness (the value is an object); the calling plugin service
    owns business-level field filtering. A ``None`` schema means "any object".
    """
    if json_schema is None:
        return parsed_object
    if not isinstance(parsed_object, dict):
        raise InvalidJsonError("Model output is not a JSON object")
    return parsed_object


def request_valid_json(
    generate_text: Callable[[Optional[str]], str],
    *,
    json_schema: Optional[dict],
    max_attempts: int,
) -> dict:
    """Call ``generate_text`` until it yields a valid JSON object.

    ``generate_text`` is invoked with an optional repair instruction: ``None``
    on the first attempt, and a short re-prompt string on later attempts when
    the previous output failed to parse. After ``max_attempts`` invalid
    responses an :class:`InvalidJsonError` is raised.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    repair_instruction: Optional[str] = None
    for _attempt_index in range(max_attempts):
        raw_text = generate_text(repair_instruction)
        parsed_object = parse_json_object(raw_text)
        if parsed_object is not None:
            return validate_against_schema(parsed_object, json_schema)
        repair_instruction = (
            "Your previous reply was not valid JSON. Reply with ONE valid "
            "JSON object only, no prose and no Markdown fences."
        )

    raise InvalidJsonError(
        f"Model did not return valid JSON after {max_attempts} attempt(s)"
    )


def dump_compact(value: Any) -> str:
    """Serialise ``value`` to a compact JSON string for embedding in prompts."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
