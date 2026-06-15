"""Unit tests for CmsAiGenerateService (LoopForge + S77 mocked).

Engineering requirements (binding, restated): TDD-first (RED before the
service); DevOps-first (no DB/network — a fake FlowRunner stands in for the LLM,
the S77 port is a MagicMock); SOLID/DI/DRY (the service is pure orchestration;
LoopForge stays the single home of the LLM call); Liskov (the fake runner
honours ``run(flow, scope)``); clean code; no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin cms-ai --full``.
"""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

service_module = import_module("plugins.cms-ai.cms-ai.services.cms_ai_generate_service")
loader_module = import_module("plugins.cms-ai.cms-ai.services.prompt_asset_loader")

CmsAiGenerateService = service_module.CmsAiGenerateService
CmsAiGenerateError = service_module.CmsAiGenerateError
PromptTriple = loader_module.PromptTriple


def _article_triple():
    return PromptTriple(
        flow={"name": "article", "steps": [{}]},
        template={
            "model": "{{ llm_model }}",
            "system_content": "Schema: {{ json_schema }}. Fields: {{ requested_fields }}.",
            "prompt": "{{ user_prompt }}\n"
            "{% if read_excerpt %}Excerpt: {{ excerpt }}{% endif %}",
        },
        manifest={
            "request_context": ["title", "excerpt", "content_html", "type"],
            "response_fields": {
                "content_html": {"type": "string"},
                "title": {"type": "string"},
                "excerpt": {"type": "string"},
            },
        },
    )


def _loader_for(triple):
    loader = MagicMock()
    loader.load.return_value = triple
    return loader


class _FakeRunner:
    """Liskov-honest FlowRunner fake: records the scope, returns canned output."""

    def __init__(self, output):
        self._output = output
        self.last_scope = None
        self.last_flow = None

    def run(self, flow, scope):
        self.last_flow = flow
        self.last_scope = dict(scope)
        return self._output


def _service(*, triple, output, config=None, field_defs_provider=None):
    runner = _FakeRunner(output)
    service = CmsAiGenerateService(
        config=config or {"llm_model": "gpt-4o-mini"},
        asset_loader=_loader_for(triple),
        field_defs_provider=field_defs_provider,
        flow_runner_factory=lambda model: runner,
    )
    return service, runner


def test_builds_scope_with_excerpt_only_when_read_excerpt_true():
    service, runner = _service(
        triple=_article_triple(), output={"content_html": "<p>x</p>"}
    )

    service.generate(
        action="article",
        prompt="Write about astronomy",
        read_excerpt=True,
        context={"title": "Stars", "excerpt": "About stars", "type": "post"},
    )

    assert runner.last_scope["excerpt"] == "About stars"
    assert runner.last_scope["read_excerpt"] is True


def test_excerpt_omitted_from_scope_when_read_excerpt_false():
    service, runner = _service(
        triple=_article_triple(), output={"content_html": "<p>x</p>"}
    )

    service.generate(
        action="article",
        prompt="Write",
        read_excerpt=False,
        context={"title": "Stars", "excerpt": "About stars", "type": "post"},
    )

    assert "excerpt" not in runner.last_scope


def test_derives_json_schema_and_requested_fields_from_manifest():
    service, runner = _service(
        triple=_article_triple(), output={"content_html": "<p>x</p>"}
    )

    service.generate(action="article", prompt="Write", read_excerpt=False, context={})

    # requested_fields render lists every response field name.
    assert "content_html" in runner.last_scope["requested_fields"]
    # json_schema is the serialised {field: type} map.
    assert '"content_html"' in runner.last_scope["json_schema"]


def test_patch_drops_unknown_keys_and_null_values():
    service, _runner = _service(
        triple=_article_triple(),
        output={
            "content_html": "<p>kept</p>",
            "title": None,  # null -> dropped
            "not_a_field": "ignored",  # unknown -> dropped
        },
    )

    result = service.generate(
        action="article", prompt="Write", read_excerpt=False, context={}
    )

    assert result["patch"] == {"content_html": "<p>kept</p>"}


def test_schema_json_must_be_an_object():
    triple = PromptTriple(
        flow={"name": "seo", "steps": [{}]},
        template={"prompt": "{{ user_prompt }}"},
        manifest={
            "request_context": ["title"],
            "response_fields": {
                "meta_title": {"type": "string"},
                "schema_json": {"type": "object"},
            },
        },
    )
    service, _runner = _service(
        triple=triple,
        output={"meta_title": "T", "schema_json": "not-an-object"},
    )

    result = service.generate(
        action="seo", prompt="SEO", read_excerpt=False, context={"title": "X"}
    )

    assert "schema_json" not in result["patch"]
    assert result["patch"]["meta_title"] == "T"


def test_object_schema_json_is_kept():
    triple = PromptTriple(
        flow={"name": "seo", "steps": [{}]},
        template={"prompt": "{{ user_prompt }}"},
        manifest={
            "request_context": ["title"],
            "response_fields": {"schema_json": {"type": "object"}},
        },
    )
    service, _runner = _service(
        triple=triple, output={"schema_json": {"@type": "Article"}}
    )

    result = service.generate(
        action="seo", prompt="SEO", read_excerpt=False, context={"title": "X"}
    )

    assert result["patch"]["schema_json"] == {"@type": "Article"}


def test_source_css_with_script_tag_is_dropped():
    triple = PromptTriple(
        flow={"name": "restyle", "steps": [{}]},
        template={"prompt": "{{ user_prompt }}"},
        manifest={
            "request_context": ["title"],
            "response_fields": {"source_css": {"type": "string"}},
        },
    )
    service, _runner = _service(
        triple=triple,
        output={"source_css": "body{color:red}<script>alert(1)</script>"},
    )

    result = service.generate(
        action="restyle", prompt="Restyle", read_excerpt=False, context={}
    )

    assert "source_css" not in result["patch"]


def test_clean_source_css_is_kept():
    triple = PromptTriple(
        flow={"name": "restyle", "steps": [{}]},
        template={"prompt": "{{ user_prompt }}"},
        manifest={
            "request_context": ["title"],
            "response_fields": {"source_css": {"type": "string"}},
        },
    )
    service, _runner = _service(
        triple=triple, output={"source_css": "body { color: red; }"}
    )

    result = service.generate(
        action="restyle", prompt="Restyle", read_excerpt=False, context={}
    )

    assert result["patch"]["source_css"] == "body { color: red; }"


def test_custom_field_def_type_and_options_injected_into_instruction():
    triple = PromptTriple(
        flow={"name": "article", "steps": [{}]},
        template={
            "system_content": "{{ requested_fields }}",
            "prompt": "{{ user_prompt }}",
        },
        manifest={
            "request_context": ["title"],
            "response_fields": {
                "content_html": {"type": "string"},
                "reading_level": {"type": "string", "custom_field": True},
            },
        },
    )
    field_defs = MagicMock()
    field_defs.get_field_defs.return_value = [
        {
            "key": "reading_level",
            "label": "Reading level",
            "type": "select",
            "options": ["easy", "hard"],
        }
    ]
    service, runner = _service(
        triple=triple,
        output={"content_html": "<p>x</p>", "reading_level": "easy"},
        field_defs_provider=field_defs,
    )

    result = service.generate(
        action="article", prompt="Write", read_excerpt=False, context={"title": "X"}
    )

    rendered = runner.last_scope["requested_fields"]
    assert "reading_level" in rendered
    assert "select" in rendered
    assert "easy" in rendered and "hard" in rendered
    # The custom-field value still rides back in the patch.
    assert result["patch"]["reading_level"] == "easy"
    field_defs.get_field_defs.assert_called_once_with("cms_post")


def test_loopforge_error_surfaces_as_safe_generate_error():
    loopforge_error_module = import_module("loopforge")

    class _RaisingRunner:
        def run(self, flow, scope):
            raise loopforge_error_module.LoopForgeError("secret-key-12345 leaked")

    service = CmsAiGenerateService(
        config={"llm_model": "gpt-4o-mini", "llm_api_key": "secret-key-12345"},
        asset_loader=_loader_for(_article_triple()),
        flow_runner_factory=lambda model: _RaisingRunner(),
    )

    with pytest.raises(CmsAiGenerateError) as exc_info:
        service.generate(
            action="article", prompt="Write", read_excerpt=False, context={}
        )

    assert "secret-key-12345" not in str(exc_info.value)


def test_provider_reported_from_model_name():
    service, _runner = _service(
        triple=_article_triple(),
        output={"content_html": "<p>x</p>"},
        config={"llm_model": "claude-3-5-sonnet"},
    )

    result = service.generate(
        action="article", prompt="Write", read_excerpt=False, context={}
    )

    assert result["provider"] == "anthropic"
    assert result["model"] == "claude-3-5-sonnet"
