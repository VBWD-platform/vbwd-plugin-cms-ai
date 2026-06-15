"""Unit tests for LoopForge StepTemplate rendering."""

from loopforge.template import RenderedStep, StepTemplate


def _article_template() -> StepTemplate:
    return StepTemplate.from_dict(
        {
            "model": "{{ llm_model }}",
            "temperature": "{{ temperature }}",
            "system_content": "Reply with JSON. Fields: {{ requested_fields }}.",
            "prompt": (
                "{{ user_prompt }}\n"
                "{% if read_excerpt %}Source excerpt: {{ excerpt }}{% endif %}\n"
                "Page title: {{ title }}"
            ),
        }
    )


def test_render_returns_all_payload_fields():
    rendered = _article_template().render(
        {
            "llm_model": "gpt-4o-mini",
            "temperature": "0.4",
            "requested_fields": "content_html",
            "user_prompt": "Write about astronomy",
            "read_excerpt": True,
            "excerpt": "A short excerpt",
            "title": "Stars",
        }
    )

    assert isinstance(rendered, RenderedStep)
    assert rendered.model == "gpt-4o-mini"
    assert rendered.temperature == 0.4
    assert "content_html" in rendered.system_content
    assert "Write about astronomy" in rendered.user_content
    assert "Source excerpt: A short excerpt" in rendered.user_content
    assert "Page title: Stars" in rendered.user_content


def test_read_excerpt_false_omits_the_excerpt_block():
    rendered = _article_template().render(
        {
            "llm_model": "gpt-4o-mini",
            "temperature": "0.7",
            "requested_fields": "content_html",
            "user_prompt": "Write about astronomy",
            "read_excerpt": False,
            "excerpt": "SHOULD NOT APPEAR",
            "title": "Stars",
        }
    )

    assert "SHOULD NOT APPEAR" not in rendered.user_content
    assert "Source excerpt" not in rendered.user_content
    assert "Write about astronomy" in rendered.user_content


def test_missing_variable_renders_empty_and_never_raises():
    rendered = _article_template().render(
        {
            "llm_model": "gpt-4o-mini",
            "temperature": "0.7",
            "user_prompt": "Hello",
            "read_excerpt": False,
            # 'title' and 'requested_fields' deliberately omitted.
        }
    )

    assert "Page title:" in rendered.user_content
    assert "Page title: " in rendered.user_content  # value rendered empty
    assert rendered.model == "gpt-4o-mini"


def test_temperature_falls_back_to_default_when_unrenderable():
    template = StepTemplate.from_dict(
        {"model": "gpt-4o-mini", "temperature": "not-a-number", "prompt": "hi"}
    )
    rendered = template.render({})
    assert rendered.temperature == 0.7


def test_empty_scope_does_not_raise():
    rendered = _article_template().render()
    assert rendered.model == ""
    assert "Page title:" in rendered.user_content
