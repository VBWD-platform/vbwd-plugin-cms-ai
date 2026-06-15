"""Flow + FlowRunner — the ordered, multi-step pipeline.

A :class:`Flow` is an ordered list of :class:`FlowStep`. ``FlowRunner.run``
renders each step against the accumulating scope, calls the step's adapter
(LLM or image), validates the output and merges it back into the scope so
downstream steps can read it. A single-step flow is just a flow of length 1 —
the editor actions are single-step today; ``article -> seo -> image`` is the
same runner with more steps appended.

The runner depends only on the LoopForge adapter contracts and the template;
no Flask, no SQLAlchemy, no filesystem. Config and templates arrive as plain
data from the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .adapter import LlmAdapter
from .image import ImageAdapter
from .template import StepTemplate

_LLM_STEP = "llm"
_IMAGE_STEP = "image"


@dataclass
class FlowStep:
    """One step in a flow: a template, the adapter to run it, and an output key."""

    template: StepTemplate
    kind: str = _LLM_STEP
    json_schema: Optional[dict] = None
    json_retry_max: int = 3
    # For image steps: the key under which the produced bytes are merged.
    output_key: Optional[str] = None
    # For image steps: rendering uses the user_content as the image prompt.
    image_model: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None


@dataclass
class Flow:
    """An ordered, named pipeline of steps."""

    name: str
    steps: List[FlowStep] = field(default_factory=list)


class FlowRunner:
    """Executes a flow, threading each step's output into the next step's scope."""

    def __init__(
        self,
        *,
        llm_adapter: Optional[LlmAdapter] = None,
        image_adapter: Optional[ImageAdapter] = None,
    ) -> None:
        self._llm_adapter = llm_adapter
        self._image_adapter = image_adapter

    def run(self, flow: Flow, scope: Optional[Dict[str, Any]] = None) -> dict:
        """Run ``flow`` against ``scope``; return the merged output of all steps."""
        accumulated_scope: Dict[str, Any] = dict(scope or {})
        merged_output: Dict[str, Any] = {}

        for flow_step in flow.steps:
            step_output = self._run_step(flow_step, accumulated_scope)
            merged_output.update(step_output)
            accumulated_scope.update(step_output)

        return merged_output

    def _run_step(self, flow_step: FlowStep, scope: Dict[str, Any]) -> Dict[str, Any]:
        rendered_step = flow_step.template.render(scope)
        if flow_step.kind == _IMAGE_STEP:
            return self._run_image_step(flow_step, rendered_step)
        return self._run_llm_step(flow_step, rendered_step)

    def _run_llm_step(self, flow_step: FlowStep, rendered_step) -> Dict[str, Any]:
        if self._llm_adapter is None:
            raise ValueError("FlowRunner has no LLM adapter for an LLM step")
        return self._llm_adapter.generate(
            rendered_step.system_content,
            rendered_step.user_content,
            model=rendered_step.model,
            temperature=rendered_step.temperature,
            json_schema=flow_step.json_schema,
            json_retry_max=flow_step.json_retry_max,
        )

    def _run_image_step(self, flow_step: FlowStep, rendered_step) -> Dict[str, Any]:
        if self._image_adapter is None:
            raise ValueError("FlowRunner has no image adapter for an image step")
        image_bytes = self._image_adapter.generate(
            rendered_step.user_content,
            model=flow_step.image_model or rendered_step.model,
            width=flow_step.image_width or 1024,
            height=flow_step.image_height or 1024,
        )
        output_key = flow_step.output_key or "image_bytes"
        return {output_key: image_bytes}
