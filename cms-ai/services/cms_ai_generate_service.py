"""CmsAiGenerateService — orchestrate one editor text-generation request.

The service is the only layer that knows about vbwd config, asset storage and
the S77 custom-field defs. It picks the prompt triple for the requested action,
builds the LoopForge variable scope from the request + plugin config + the
manifest's ``request_context``, derives the JSON schema and requested-field
list from the manifest's ``response_fields`` (injecting the S77 def type/options
for any custom-field key so the model emits valid values), runs the flow
through a :class:`FlowRunner`, validates the model output against the manifest
and returns the **patch** — only the model-filled, recognised fields.

LoopForge stays pure: it receives endpoint/key/model/params and the rendered
template as plain data and returns a dict. The service never persists anything
(stateless generate; the operator owns persistence).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# LoopForge is imported as the top-level ``loopforge`` package; the source
# package ``__init__`` places the plugin root on ``sys.path`` so this resolves.
from loopforge import (
    Flow,
    FlowRunner,
    FlowStep,
    LoopForgeError,
    StepTemplate,
    select_adapter,
)

from .prompt_asset_loader import PromptAssetLoader, PromptTriple

# Entity type S77 registers for CMS posts (custom-field defs are scoped to it).
CMS_POST_ENTITY_TYPE = "cms_post"

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_MAX_TOKENS = 4000
_DEFAULT_RETRY_MAX = 3


class CmsAiGenerateError(Exception):
    """Raised when generation fails; carries a safe (key-free) message."""


class CmsAiGenerateService:
    """Turn a prompt + page context into a validated CMS-field patch."""

    def __init__(
        self,
        *,
        config: Dict[str, Any],
        asset_loader: PromptAssetLoader,
        field_defs_provider: Optional[Any] = None,
        flow_runner_factory: Optional[Any] = None,
    ) -> None:
        self._config = config or {}
        self._asset_loader = asset_loader
        # The S77 port (``ITagsAndCustomFields``); optional so the service runs
        # with core fields only when S77 is absent.
        self._field_defs_provider = field_defs_provider
        # Injected for tests (a fake FlowRunner); production builds one bound to
        # the model-selected LLM adapter.
        self._flow_runner_factory = flow_runner_factory or self._build_flow_runner

    def generate(
        self,
        *,
        action: str,
        prompt: str,
        read_excerpt: bool,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run the action's flow and return ``{patch, provider, model}``."""
        triple = self._asset_loader.load(action)
        manifest = triple.manifest or {}

        response_fields = manifest.get("response_fields") or {}
        json_schema = self._derive_json_schema(response_fields)
        requested_fields = self._derive_requested_fields(response_fields)

        scope = self._build_scope(
            prompt=prompt,
            read_excerpt=read_excerpt,
            context=context,
            manifest=manifest,
            json_schema=json_schema,
            requested_fields=requested_fields,
        )

        model = self._resolve_model(triple)
        flow = self._build_flow(action, triple.template, json_schema)
        runner = self._flow_runner_factory(model)

        try:
            raw_output = runner.run(flow, scope)
        except LoopForgeError as generation_error:
            # Never echo the key; surface a safe message only.
            raise CmsAiGenerateError("AI generation failed") from generation_error

        patch = self._validate_output(raw_output, response_fields)
        return {"patch": patch, "provider": _provider_for_model(model), "model": model}

    # -- scope -------------------------------------------------------------

    def _build_scope(
        self,
        *,
        prompt: str,
        read_excerpt: bool,
        context: Dict[str, Any],
        manifest: Dict[str, Any],
        json_schema: Dict[str, str],
        requested_fields: List[str],
    ) -> Dict[str, Any]:
        """Compose the LoopForge variable scope (request + config + manifest)."""
        request_context_keys = manifest.get("request_context") or []
        scope: Dict[str, Any] = {
            "user_prompt": prompt or "",
            "read_excerpt": bool(read_excerpt),
            "llm_model": self._resolve_model_string(),
            "temperature": self._config.get("temperature", _DEFAULT_TEMPERATURE),
            "json_schema": json.dumps(json_schema),
            "requested_fields": self._render_requested_fields(
                requested_fields, manifest.get("response_fields") or {}
            ),
        }

        for key in request_context_keys:
            # Excerpt only rides along when the operator asked for it.
            if key == "excerpt" and not read_excerpt:
                continue
            scope[key] = context.get(key, "")

        # The template gates the "existing content" block on this flag.
        scope["existing_content"] = bool(context.get("content_html"))
        return scope

    def _render_requested_fields(
        self, requested_fields: List[str], response_fields: Dict[str, Any]
    ) -> str:
        """Render the requested-field list, enriching custom fields with S77 defs.

        For a manifest key flagged ``custom_field`` the service looks up the S77
        def (type + options) so the model is told exactly what valid values look
        like. Core fields render as a plain name.
        """
        custom_field_defs = self._custom_field_defs()
        parts: List[str] = []
        for field_name in requested_fields:
            spec = response_fields.get(field_name, {})
            if isinstance(spec, dict) and spec.get("custom_field"):
                definition = custom_field_defs.get(field_name)
                if definition is not None:
                    parts.append(self._describe_custom_field(field_name, definition))
                    continue
            parts.append(field_name)
        return ", ".join(parts)

    def _describe_custom_field(
        self, field_name: str, definition: Dict[str, Any]
    ) -> str:
        """Build the instruction fragment for one S77 custom field."""
        field_type = definition.get("type", "string")
        description = f"{field_name} (custom field, type {field_type}"
        options = definition.get("options")
        if options:
            description += f", one of: {', '.join(str(option) for option in options)}"
        return description + ")"

    def _custom_field_defs(self) -> Dict[str, Dict[str, Any]]:
        """Map S77 custom-field key -> def, or empty when S77 is absent."""
        if self._field_defs_provider is None:
            return {}
        defs = self._field_defs_provider.get_field_defs(CMS_POST_ENTITY_TYPE)
        return {definition["key"]: definition for definition in defs or []}

    # -- schema derivation --------------------------------------------------

    def _derive_json_schema(self, response_fields: Dict[str, Any]) -> Dict[str, str]:
        """``{field: 'type'}`` map the adapter forces the model to emit."""
        schema: Dict[str, str] = {}
        for field_name, spec in response_fields.items():
            field_type = "string"
            if isinstance(spec, dict):
                field_type = str(spec.get("type", "string"))
            schema[field_name] = field_type
        return schema

    def _derive_requested_fields(self, response_fields: Dict[str, Any]) -> List[str]:
        """The ordered list of field names the action asks the model to fill."""
        return list(response_fields.keys())

    # -- output validation --------------------------------------------------

    def _validate_output(
        self, raw_output: Dict[str, Any], response_fields: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Drop unknown/null keys, type-check ``schema_json``, sanitise CSS."""
        patch: Dict[str, Any] = {}
        if not isinstance(raw_output, dict):
            return patch

        for field_name, value in raw_output.items():
            if field_name not in response_fields:
                continue  # unknown key the manifest never requested
            if value is None:
                continue  # null -> leave the field untouched
            if _is_placeholder_value(value):
                continue  # placeholder/empty -> treat as not filled
            if field_name == "schema_json" and not isinstance(value, dict):
                continue  # JSON-LD must be an object
            if field_name == "source_css":
                value = _sanitise_css(value)
                if value is None:
                    continue
            patch[field_name] = value
        return patch

    # -- LoopForge wiring ---------------------------------------------------

    def _build_flow(
        self, action: str, template_data: Dict[str, Any], json_schema: Dict[str, str]
    ) -> Flow:
        """Build a single-step flow from the resolved template data."""
        step = FlowStep(
            template=StepTemplate.from_dict(template_data),
            json_schema=json_schema,
            json_retry_max=int(self._config.get("json_retry_max", _DEFAULT_RETRY_MAX)),
        )
        return Flow(name=action or "generate", steps=[step])

    def _build_flow_runner(self, model: str) -> FlowRunner:
        """Build the production runner bound to the model-selected LLM adapter."""
        adapter = select_adapter(
            model,
            api_key=self._config.get("llm_api_key", ""),
            endpoint=self._config.get("llm_api_endpoint", ""),
            max_tokens=int(self._config.get("max_tokens", _DEFAULT_MAX_TOKENS)),
        )
        return FlowRunner(llm_adapter=adapter)

    def _resolve_model(self, triple: PromptTriple) -> str:
        """The concrete model name (config overrides any template placeholder)."""
        return self._resolve_model_string()

    def _resolve_model_string(self) -> str:
        return str(self._config.get("llm_model") or _DEFAULT_MODEL)


def _provider_for_model(model: str) -> str:
    """Report which provider the model name selects (for the response)."""
    return "anthropic" if model.startswith("claude-") else "openai"


def _sanitise_css(value: Any) -> Optional[str]:
    """Return plain stylesheet text, or ``None`` if it carries a script tag."""
    if not isinstance(value, str):
        return None
    if "<script" in value.lower():
        return None
    return value


# Tokens a model may emit instead of real content. These are treated as
# "not filled" (dropped from the patch) so the editor is never populated with a
# placeholder — a defensive backstop on top of the prompt, which already tells
# the model never to produce them.
_PLACEHOLDER_VALUES = frozenset(
    {
        "<unknown>",
        "unknown",
        "todo",
        "tbd",
        "n/a",
        "na",
        "none",
        "null",
        "...",
        "placeholder",
        "[placeholder]",
        "<placeholder>",
    }
)


def _is_placeholder_value(value: Any) -> bool:
    """True when a string value is empty/whitespace or a known placeholder token."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return True
    return stripped.lower() in _PLACEHOLDER_VALUES
