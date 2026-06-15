"""Unit tests for the LoopForge FlowRunner (single-step and multi-step)."""

from loopforge.flow import Flow, FlowRunner, FlowStep
from loopforge.template import StepTemplate
from loopforge import run_flow


class _RecordingLlmAdapter:
    """A Liskov-honest test fake: same generate() surface, no network."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = []

    def generate(
        self,
        system_content,
        user_content,
        *,
        model,
        temperature,
        json_schema=None,
        json_retry_max=3
    ):
        self.calls.append(
            {"system": system_content, "user": user_content, "model": model}
        )
        return self._outputs.pop(0)


def test_single_step_flow_returns_that_steps_output():
    adapter = _RecordingLlmAdapter([{"content_html": "<p>Body</p>"}])
    step = FlowStep(
        template=StepTemplate.from_dict(
            {"model": "gpt-4o-mini", "prompt": "{{ user_prompt }}"}
        )
    )
    flow = Flow(name="article", steps=[step])

    result = FlowRunner(llm_adapter=adapter).run(flow, {"user_prompt": "Write"})

    assert result == {"content_html": "<p>Body</p>"}
    assert adapter.calls[0]["user"] == "Write"


def test_two_step_flow_feeds_step1_output_into_step2_and_merges():
    adapter = _RecordingLlmAdapter(
        [
            {"content_html": "<p>Generated article</p>"},
            {"meta_title": "SEO title"},
        ]
    )
    article_step = FlowStep(
        template=StepTemplate.from_dict(
            {"model": "gpt-4o-mini", "prompt": "{{ user_prompt }}"}
        )
    )
    seo_step = FlowStep(
        template=StepTemplate.from_dict(
            {
                "model": "gpt-4o-mini",
                # reads the previous step's output from the accumulated scope
                "prompt": "Generate SEO for: {{ content_html }}",
            }
        )
    )
    flow = Flow(name="article_then_seo", steps=[article_step, seo_step])

    result = FlowRunner(llm_adapter=adapter).run(flow, {"user_prompt": "Write"})

    # Step 2 saw step 1's output in its rendered prompt.
    assert "Generated article" in adapter.calls[1]["user"]
    # Both steps' outputs are merged into the final result.
    assert result == {
        "content_html": "<p>Generated article</p>",
        "meta_title": "SEO title",
    }


def test_run_flow_convenience_entry_matches_runner():
    adapter = _RecordingLlmAdapter([{"excerpt": "short"}])
    step = FlowStep(
        template=StepTemplate.from_dict(
            {"model": "gpt-4o-mini", "prompt": "{{ user_prompt }}"}
        )
    )
    flow = Flow(name="excerpt", steps=[step])

    result = run_flow(flow, {"user_prompt": "Summarise"}, llm_adapter=adapter)
    assert result == {"excerpt": "short"}


class _RecordingImageAdapter:
    """Test fake honouring the ImageAdapter generate() surface."""

    def __init__(self, image_bytes):
        self._image_bytes = image_bytes
        self.calls = []

    def generate(self, prompt, *, model="m", width=1024, height=1024):
        self.calls.append({"prompt": prompt, "model": model})
        return self._image_bytes


def test_image_step_merges_bytes_under_output_key():
    image_adapter = _RecordingImageAdapter(b"jpeg-bytes")
    image_step = FlowStep(
        template=StepTemplate.from_dict({"prompt": "{{ user_prompt }}"}),
        kind="image",
        output_key="image_bytes",
        image_model="black-forest-labs/flux-schnell",
    )
    flow = Flow(name="image_only", steps=[image_step])

    result = FlowRunner(image_adapter=image_adapter).run(
        flow, {"user_prompt": "a sunset"}
    )

    assert result == {"image_bytes": b"jpeg-bytes"}
    assert image_adapter.calls[0]["prompt"] == "a sunset"
