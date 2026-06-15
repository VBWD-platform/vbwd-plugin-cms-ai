"""StepTemplate — pure rendering of a step's prompt payload from a var scope.

A step template is plain data (model, temperature, system_content, prompt
strings carrying ``{{ variable }}`` placeholders and optional
``{% if flag %}...{% endif %}`` blocks). ``render(scope)`` substitutes the
scope and returns a :class:`RenderedStep`. Rendering is pure: unknown or
missing variables render as empty strings and never raise.

Jinja2 is already a backend dependency, so it is used when available (it gives
the ``{% if %}`` blocks the shipped templates use); a minimal safe substituter
is the fallback so LoopForge stays usable even without Jinja2. No new heavy
dependency is introduced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

try:  # Jinja2 ships with the backend; prefer it for {% if %} support.
    from jinja2 import Environment

    _JINJA_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on a Jinja-less install
    _JINJA_AVAILABLE = False

_DEFAULT_TEMPERATURE = 0.7

# Matches {{ variable_name }} with optional surrounding whitespace.
_VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
# Matches {% if flag %}...{% endif %} blocks (non-greedy, single-level).
_IF_BLOCK_PATTERN = re.compile(
    r"{%\s*if\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*%}(.*?){%\s*endif\s*%}",
    re.DOTALL,
)


@dataclass(frozen=True)
class RenderedStep:
    """The concrete prompt payload produced by rendering a StepTemplate."""

    system_content: str
    user_content: str
    model: str
    temperature: float


def _coerce_temperature(value: Any) -> float:
    """Best-effort conversion of a rendered temperature to a float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return _DEFAULT_TEMPERATURE


def _render_with_safe_substituter(template_text: str, scope: Mapping[str, Any]) -> str:
    """Substitute ``{{ var }}`` and resolve ``{% if flag %}`` without Jinja2.

    Missing variables and falsy flags collapse to empty output; nothing raises.
    """

    def _resolve_if_block(match: "re.Match[str]") -> str:
        flag_name = match.group(1)
        inner_text = match.group(2)
        flag_value = scope.get(flag_name)
        return inner_text if flag_value else ""

    resolved_text = _IF_BLOCK_PATTERN.sub(_resolve_if_block, template_text)

    def _resolve_variable(match: "re.Match[str]") -> str:
        variable_name = match.group(1)
        value = scope.get(variable_name, "")
        return "" if value is None else str(value)

    return _VARIABLE_PATTERN.sub(_resolve_variable, resolved_text)


def _render_text(template_text: str, scope: Mapping[str, Any]) -> str:
    """Render ``template_text`` against ``scope``; missing vars render empty."""
    if not template_text:
        return ""
    if _JINJA_AVAILABLE:
        # Default Undefined renders missing vars as empty and never raises,
        # which matches the "missing var -> empty" contract.
        environment = Environment(autoescape=False)
        return environment.from_string(template_text).render(**dict(scope))
    return _render_with_safe_substituter(template_text, scope)


class StepTemplate:
    """A renderable LLM step: system + user prompt, model and temperature."""

    def __init__(
        self,
        *,
        system_content: str = "",
        prompt: str = "",
        model: str = "",
        temperature: Any = _DEFAULT_TEMPERATURE,
    ) -> None:
        self._system_content_template = system_content
        self._prompt_template = prompt
        self._model_template = model
        self._temperature_template = temperature

    @classmethod
    def from_dict(cls, template_data: Mapping[str, Any]) -> "StepTemplate":
        """Build a StepTemplate from the loopai-native ``template-N.json`` shape."""
        return cls(
            system_content=str(template_data.get("system_content", "")),
            prompt=str(template_data.get("prompt", "")),
            model=str(template_data.get("model", "")),
            temperature=template_data.get("temperature", _DEFAULT_TEMPERATURE),
        )

    def render(self, scope: Optional[Mapping[str, Any]] = None) -> RenderedStep:
        """Render this template against ``scope`` into a :class:`RenderedStep`."""
        effective_scope: Mapping[str, Any] = scope or {}
        rendered_model = _render_text(self._model_template, effective_scope)
        rendered_temperature = _render_text(
            str(self._temperature_template), effective_scope
        )
        return RenderedStep(
            system_content=_render_text(self._system_content_template, effective_scope),
            user_content=_render_text(self._prompt_template, effective_scope),
            model=rendered_model,
            temperature=_coerce_temperature(rendered_temperature),
        )
