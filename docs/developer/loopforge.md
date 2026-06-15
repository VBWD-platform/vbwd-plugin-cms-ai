# LoopForge — the clean pipeline engine

> Developer documentation for the `cms-ai` plugin (Sprint S41).
> Package: `plugins/cms-ai/loopforge/`.

## What LoopForge is

LoopForge is a self-contained, **import-clean** multi-step LLM pipeline engine.
A step template renders a prompt from a variable scope, a Liskov-substitutable
LLM adapter (OpenAI- or Anthropic-protocol, chosen by model name) returns a
**validated JSON object**, an image adapter (Replicate / Black Forest Labs FLUX)
turns a prompt into JPEG bytes, and a flow runner threads an ordered list of
steps so each step's output feeds the next.

It depends only on the standard library plus the provider SDKs (`openai`,
`anthropic`, `replicate`) and `Pillow`/`requests` for image handling. It imports
**no** Flask, **no** SQLAlchemy, **no** thread-local session, **no** `vbwd` core
module and **no** other plugin. That import-clean boundary is what makes
LoopForge extractable later as a pip dependency or a git submodule.

> **Conceptual origin only.** The `loopai` project (vendored under
> `plugins/cms-ai/cms-ai/loopai`) inspired the step-template / dual-protocol /
> JSON-repair / multi-step *concepts*. LoopForge is a fresh, SOLID rebuild — it
> does **not** import from `loopai`.

## Package layout

```
plugins/cms-ai/loopforge/
├── __init__.py     # public surface + run_flow() convenience entry
├── template.py     # StepTemplate / RenderedStep: render a prompt from a scope
├── flow.py         # Flow / FlowStep / FlowRunner: ordered multi-step pipeline
├── adapter.py      # LlmAdapter (ABC) + OpenAiAdapter + AnthropicAdapter + select_adapter
├── image.py        # ImageAdapter (ABC) + ReplicateImageAdapter
├── json_io.py      # clean/parse/validate JSON + the repair/retry loop
├── errors.py       # LoopForgeError (+ InvalidJsonError, AdapterError)
└── tests/          # pure unit tests (SDK clients mocked, no network)
```

### Public surface (`loopforge/__init__.py`)

```python
from loopforge import (
    StepTemplate, RenderedStep,
    Flow, FlowStep, FlowRunner,
    LlmAdapter, OpenAiAdapter, AnthropicAdapter, select_adapter,
    ImageAdapter, ReplicateImageAdapter,
    is_valid_json,
    LoopForgeError, AdapterError, InvalidJsonError,
    run_flow,
)
```

`run_flow` is a convenience entry for the common single-call case:

```python
def run_flow(
    flow: Flow,
    scope: Optional[Dict[str, Any]] = None,
    *,
    llm_adapter: Optional[LlmAdapter] = None,
    image_adapter: Optional[ImageAdapter] = None,
) -> dict
```

It builds a `FlowRunner(llm_adapter=..., image_adapter=...)` and returns
`runner.run(flow, scope)`. Callers that already hold a runner should use it
directly.

## The StepTemplate / Flow / FlowRunner model

### StepTemplate (`template.py`)

A `StepTemplate` is plain data — `system_content`, `prompt`, `model`,
`temperature` — where each string may carry `{{ variable }}` placeholders and
single-level `{% if flag %}...{% endif %}` blocks. `render(scope)` substitutes
the scope and returns an immutable `RenderedStep`:

```python
@dataclass(frozen=True)
class RenderedStep:
    system_content: str
    user_content: str
    model: str
    temperature: float
```

```python
class StepTemplate:
    def __init__(self, *, system_content="", prompt="", model="", temperature=0.7) -> None
    @classmethod
    def from_dict(cls, template_data: Mapping[str, Any]) -> "StepTemplate"
    def render(self, scope: Optional[Mapping[str, Any]] = None) -> RenderedStep
```

Rendering is **pure**: unknown or missing variables render as empty strings and
never raise. Jinja2 is preferred when available (it is already a backend
dependency and gives the `{% if %}` blocks the shipped templates use); a minimal
regex-based safe substituter is the fallback so LoopForge stays usable even
without Jinja2 — **no new heavy dependency is introduced.** `from_dict` accepts
the loopai-native `template-N.json` shape (`{model, temperature, system_content,
prompt}`).

### FlowStep / Flow (`flow.py`)

```python
@dataclass
class FlowStep:
    template: StepTemplate
    kind: str = "llm"               # "llm" or "image"
    json_schema: Optional[dict] = None
    json_retry_max: int = 3
    output_key: Optional[str] = None    # image steps: where bytes are merged
    image_model: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None

@dataclass
class Flow:
    name: str
    steps: List[FlowStep] = field(default_factory=list)
```

### FlowRunner (`flow.py`)

```python
class FlowRunner:
    def __init__(self, *, llm_adapter=None, image_adapter=None) -> None
    def run(self, flow: Flow, scope: Optional[Dict[str, Any]] = None) -> dict
```

`run` threads the scope through the steps: it starts from a copy of the caller's
scope, and for each `FlowStep` it renders the template against the **accumulating
scope**, calls the step's adapter, then `update()`s both the merged output and
the accumulating scope with that step's result. So a later step can read an
earlier step's output by key.

- An **`llm`** step calls `self._llm_adapter.generate(...)` with the rendered
  system/user content, model, temperature, `json_schema` and `json_retry_max`,
  and returns the validated JSON dict.
- An **`image`** step calls `self._image_adapter.generate(prompt, model, width,
  height)` (defaults `1024×1024`) and returns `{output_key: image_bytes}`
  (`output_key` defaults to `"image_bytes"`).
- A missing adapter for the step kind raises `ValueError`.

**A single-step flow is just a flow of length 1.** The editor actions are
single-step today; the multi-step `article → seo → image` pipeline is the same
runner with more steps appended — forward-compatible, not yet exercised in
production.

## The LlmAdapter surface (`adapter.py`)

One narrow, Liskov-substitutable contract every provider honours:

```python
class LlmAdapter(ABC):
    def __init__(self, *, api_key: str, endpoint: str = "", max_tokens: int = 4096) -> None
    @abstractmethod
    def generate(
        self,
        system_content: str,
        user_content: str,
        *,
        model: str,
        temperature: float,
        json_schema: Optional[dict] = None,
        json_retry_max: int = 3,
    ) -> dict
```

`generate` returns the model's reply parsed into a JSON object, raising
`AdapterError` on SDK/transport failure and `InvalidJsonError` when no valid
JSON is produced within `json_retry_max` attempts.

### `select_adapter` — model-name routing

```python
def select_adapter(model: str, **adapter_kwargs: Any) -> LlmAdapter:
    if model.startswith("claude-"):
        return AnthropicAdapter(**adapter_kwargs)
    return OpenAiAdapter(**adapter_kwargs)
```

**The model name is the sole discriminator** — `claude-*` → Anthropic,
everything else → OpenAI. Callers never branch on provider; endpoint/key/
max-tokens still come from config and are passed through `adapter_kwargs`.

### The two SDK implementations

**`OpenAiAdapter`** drives the `openai` SDK. It builds
`OpenAI(api_key=..., base_url=endpoint?)` and calls
`client.chat.completions.create(...)` with `response_format={"type":
"json_object"}` to force a JSON object, reading `choices[0].message.content`. A
non-empty `endpoint` lets it also drive any OpenAI-compatible endpoint. Any SDK
exception becomes `AdapterError("OpenAI request failed")`.

**`AnthropicAdapter`** drives the `anthropic` SDK. It builds
`anthropic.Anthropic(api_key=..., base_url=endpoint?)` and calls
`client.messages.create(...)` with `system=system_content`, a single tool
(`emit_json`) and `tool_choice={"type": "tool", "name": "emit_json"}` to force
a JSON object. The reply is extracted from the `tool_use` block's `input` (JSON-
dumped) or, failing that, the first text block. Any SDK exception becomes
`AdapterError("Anthropic request failed")`.

The lightweight `{field: "type|null"}` manifest schema is translated into the
Anthropic tool's `input_schema` via `_build_json_tool` / `_schema_to_properties`
/ `_normalise_schema_type` (the type token before the first `|` is mapped to a
JSON-schema type, defaulting to `string`).

> **The API key is never echoed** into an error message or return value — every
> failure normalises to a safe `AdapterError` / `LoopForgeError`.

## The JSON validate / repair / retry loop (`json_io.py`)

Both adapters route every call through one shared loop (DRY):

```python
def request_valid_json(
    generate_text: Callable[[Optional[str]], str],
    *,
    json_schema: Optional[dict],
    max_attempts: int,
) -> dict
```

`generate_text` is invoked with an optional repair instruction — `None` on the
first attempt, and a short re-prompt string on later attempts when the previous
output failed to parse. Each raw reply is run through:

- `clean_json_text` — strips Markdown ```` ```json ```` fences and surrounding
  whitespace.
- `parse_json_object` — parses and returns the value **only if it is a JSON
  object** (`dict`); a bare array/number/string is treated as invalid.
- `validate_against_schema` — with the lightweight manifest schema, enforces
  only that the value is an object (`None` schema means "any object");
  business-level field filtering is the plugin service's job.

After `max_attempts` invalid responses it raises
`InvalidJsonError("Model did not return valid JSON after N attempt(s)")`.
`is_valid_json(text)` and `dump_compact(value)` are also exported helpers.

In `adapter.py`, `_run_repair_loop` wraps `request_valid_json` so any
`LoopForgeError` propagates unchanged while any other unexpected exception
becomes `AdapterError("LLM generation failed")`.

## Error contract (`errors.py`)

```python
class LoopForgeError(Exception): ...          # base for every recoverable failure
class InvalidJsonError(LoopForgeError): ...    # output not parseable/repairable
class AdapterError(LoopForgeError): ...        # provider SDK call failed / empty
```

Every failure that crosses the LoopForge boundary surfaces as a
`LoopForgeError` (or subtype), so callers depend on a single narrow error
contract and never catch a provider SDK's transport exceptions directly. **No
provider key is ever placed into an error message.**

## How to extend LoopForge

### Add a new step to a flow

Append another `FlowStep` to a `Flow`. Because `FlowRunner.run` merges each
step's output back into the scope, a later step's template can read an earlier
step's keys with `{{ ... }}`:

```python
from loopforge import Flow, FlowStep, StepTemplate, FlowRunner

flow = Flow(
    name="article_then_seo",
    steps=[
        FlowStep(template=StepTemplate.from_dict(article_tpl),
                 json_schema={"content_html": "string", "title": "string"}),
        FlowStep(template=StepTemplate.from_dict(seo_tpl),       # reads content_html
                 json_schema={"meta_title": "string", "meta_description": "string"}),
    ],
)
result = FlowRunner(llm_adapter=adapter).run(flow, {"user_prompt": "..."})
```

For an **image** step set `kind="image"` and (optionally) `output_key`,
`image_model`, `image_width`, `image_height`; provide an `image_adapter` to the
runner.

### Add a new LLM provider

1. Subclass the `LlmAdapter` ABC and implement `generate(...)` honouring the
   exact signature and return contract (a JSON `dict`).
2. Route the call through `request_valid_json` (or `_run_repair_loop`) so it
   gets the shared repair/retry loop for free, and raise only `AdapterError` /
   `InvalidJsonError` — never leak the SDK exception or the key.
3. Wire the discriminator: extend `select_adapter(model)` so the new model
   prefix maps to your class (keep the model name as the only discriminator).

### Add a new image provider

1. Subclass the `ImageAdapter` ABC and implement
   `generate(prompt, *, model, width, height) -> bytes` returning **JPEG bytes
   only** — no gallery, no DB, no filesystem (that keeps the adapter
   extractable).
2. Raise `AdapterError` (a `LoopForgeError`) on any SDK or download failure.
3. The plugin selects the adapter; LoopForge does not auto-discover image
   providers (there is no `select_*` for images today).

## The import-clean rule and its oracle

The extractability guarantee is enforced by
`loopforge/tests/test_import_clean.py`, which proves the boundary two ways:

- **`test_no_banned_imports_in_any_source_file`** — AST-scans every LoopForge
  source file (excluding `tests/`) for banned top-level imports
  (`flask`, `sqlalchemy`, `vbwd`, `plugins`) and banned name fragments
  (`session_manager`, `session_dependent`, `app_global_config`). Relative
  imports within the package are allowed.
- **`test_importing_package_does_not_pull_banned_modules`** — re-imports
  `loopforge` and asserts that doing so pulls **no** banned top-level module into
  `sys.modules` that was not already present.

Keep both green: if you add a dependency, it must not be Flask / SQLAlchemy /
`vbwd` / another plugin, directly or transitively at import time.

## Runtime dependencies

LoopForge's plugin-local runtime deps are declared in
`plugins/cms-ai/requirements.txt` (Pillow + requests come from core):

```
openai==2.41.1
anthropic==0.109.1
replicate==1.0.7
PyYAML==6.0.2
```

(`PyYAML` is used by the plugin's asset loader to read the flow files, not by
LoopForge itself.)
