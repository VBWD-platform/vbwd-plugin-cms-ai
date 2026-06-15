"""LoopForge — a clean, import-clean multi-step LLM pipeline engine.

LoopForge is a self-contained pipeline engine: a step template renders a
prompt from a variable scope, a Liskov-substitutable LLM adapter (OpenAI or
Anthropic, chosen by model name) returns a validated JSON object, an image
adapter (Replicate / Black Forest Labs FLUX) turns a prompt into JPEG bytes,
and a flow runner threads an ordered list of steps so each step's output feeds
the next.

It depends only on the standard library plus the provider SDKs (``openai``,
``anthropic``, ``replicate``) and ``Pillow``/``requests`` for image handling.
It imports **no** Flask, **no** SQLAlchemy, **no** thread-local session, **no**
``vbwd`` core module and **no** other plugin — that import-clean boundary is
what makes LoopForge extractable later as a pip dependency or git submodule.

Conceptual origin: the loopai project vendored under
``plugins/cms-ai/cms-ai/loopai`` inspired the step-template / dual-protocol /
JSON-repair / multi-step concepts. LoopForge is a fresh, SOLID rebuild — it
does **not** import from loopai.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .adapter import (
    AnthropicAdapter,
    LlmAdapter,
    OpenAiAdapter,
    select_adapter,
)
from .errors import AdapterError, InvalidJsonError, LoopForgeError
from .flow import Flow, FlowRunner, FlowStep
from .image import ImageAdapter, ReplicateImageAdapter
from .json_io import is_valid_json
from .template import RenderedStep, StepTemplate

__all__ = [
    "StepTemplate",
    "RenderedStep",
    "Flow",
    "FlowStep",
    "FlowRunner",
    "LlmAdapter",
    "OpenAiAdapter",
    "AnthropicAdapter",
    "select_adapter",
    "ImageAdapter",
    "ReplicateImageAdapter",
    "is_valid_json",
    "LoopForgeError",
    "AdapterError",
    "InvalidJsonError",
    "run_flow",
]


def run_flow(
    flow: Flow,
    scope: Optional[Dict[str, Any]] = None,
    *,
    llm_adapter: Optional[LlmAdapter] = None,
    image_adapter: Optional[ImageAdapter] = None,
) -> dict:
    """Convenience entry: build a :class:`FlowRunner` and run ``flow``.

    Callers that already hold a runner should use it directly; this helper is
    for the common single-call case.
    """
    runner = FlowRunner(llm_adapter=llm_adapter, image_adapter=image_adapter)
    return runner.run(flow, scope)
