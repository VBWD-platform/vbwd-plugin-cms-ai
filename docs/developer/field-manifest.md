# The prompt + field manifest (authoring & extending)

> Developer documentation for the `cms-ai` plugin (Sprint S41).
> Covers the admin-editable prompt triple, how it drives the request and
> response, and how to add or remove fields and ship new template sets.

## The admin-editable triple

For each editor action the plugin resolves a **triple** of files — a flow, a
step template, and a vars-manifest. They live on the unified core filesystem
under:

```
${VBWD_VAR_DIR}/assets/cms-ai/prompts/
```

(host-mounted, admin-editable — the same convention as core email templates),
and fall back to the copies shipped inside the plugin at:

```
plugins/cms-ai/templates/prompts/
```

### Resolution: VAR_DIR override wins

`PromptAssetLoader` (`cms-ai/services/prompt_asset_loader.py`) resolves the var-
dir directory via `asset_dir("cms-ai", "prompts")` from
`vbwd.services.asset_storage`, i.e. `${VBWD_VAR_DIR}/assets/cms-ai/prompts/`
(`VBWD_VAR_DIR` defaults to `/app/var`). For every file it checks the var-dir
path first and **only falls back to the shipped default** if no per-instance
file exists:

```python
def _resolve_path(self, file_name: str) -> Optional[str]:
    override_path = os.path.join(self._var_prompts_dir, file_name)
    if os.path.isfile(override_path):
        return override_path
    shipped_path = os.path.join(_SHIPPED_PROMPTS_DIR, file_name)
    if os.path.isfile(shipped_path):
        return shipped_path
    return None
```

So **retuning prompts, flows, or the in/out field sets is a file edit — no code
change.** A per-instance file under the var dir always wins. The plugin config
key `prompts_dir` can override the var-dir location entirely (it is passed as
`PromptAssetLoader(prompts_dir_override)`).

### How the loader resolves an action

```python
loader = PromptAssetLoader(prompts_dir_override="")
triple = loader.load("article")   # -> PromptTriple(flow, template, manifest)
```

`load(action)`:

1. Maps the action to `loopforge-flow-<action>.yaml` (a blank action →
   `loopforge-flow-1.yaml`, the default flow).
2. Reads that flow; if missing, falls back to `loopforge-flow-1.yaml`; if still
   missing, `{}`.
3. Takes the flow's **first step**, reads the `template` file it names (default
   `template-1.json`) and the `vars` file it names (default
   `template-1-vars.json`).
4. Returns a frozen `PromptTriple(flow, template, manifest)`.

The loader reads **plain data only** — it builds no LoopForge objects (the
service does that) and touches no network or DB.

## The three file shapes

### 1. Flow — `loopforge-flow-<action>.yaml`

The ordered list of steps. P1 ships single-step flows per action; the format is
multi-step-ready for a later increment.

```yaml
flow:
  name: article
  steps:
    - template: template-1.json
      vars: vars-article.json
```

Shipped flows: `loopforge-flow-1.yaml` (default → `template-1-vars.json`),
`loopforge-flow-article.yaml`, `loopforge-flow-seo.yaml`,
`loopforge-flow-restyle.yaml`, `loopforge-flow-freeform.yaml`.

### 2. Step template — `template-1.json`

A LoopForge step template (`{model, temperature, system_content, prompt}`) with
`{{ variables }}` and `{% if flag %}` blocks. The operator's free-form text is
just the `{{ user_prompt }}` variable:

```jsonc
{
  "model": "{{ llm_model }}",
  "temperature": "{{ temperature }}",
  "system_content": "You are a CMS content writer. Reply with ONE JSON object matching this schema (omit or null any field you are told not to fill): {{ json_schema }}. Requested fields: {{ requested_fields }}.",
  "prompt": "{{ user_prompt }}\n{% if read_excerpt %}Source excerpt: {{ excerpt }}{% endif %}\nPage title: {{ title }}\n{% if existing_content %}Existing content (HTML): {{ content_html }}{% endif %}"
}
```

### 3. Field manifest — `vars-<action>.json` (a.k.a. `template-1-vars.json`)

Declares which fields go **into** the request as context, and which fields the
model **populates** in the response:

```jsonc
{
  "request_context": ["title", "excerpt", "content_html", "type"],
  "response_fields": {
    "content_html": { "type": "string" },
    "title":        { "type": "string" },
    "excerpt":      { "type": "string" }
  }
}
```

## How the manifest drives the request and the response

`CmsAiGenerateService` (`cms-ai/services/cms_ai_generate_service.py`) is the only
layer that knows about vbwd config, asset storage and the S77 custom-field defs.
Given the manifest it does the following.

### `request_context` → the request scope

`_build_scope` seeds a fixed set of variables — `user_prompt`, `read_excerpt`,
`llm_model`, `temperature`, `json_schema` (JSON-dumped), `requested_fields` —
and then copies **each key listed in `request_context`** from the editor's
`context` object into the scope:

```python
for key in request_context_keys:
    if key == "excerpt" and not read_excerpt:   # excerpt only when asked for
        continue
    scope[key] = context.get(key, "")
scope["existing_content"] = bool(context.get("content_html"))
```

So `request_context` is exactly the set of page-context fields the template may
reference. The `excerpt` block only rides along when the operator ticked **Read
excerpt**; `existing_content` is a derived boolean the template uses to gate the
"existing content" block.

### `response_fields` → the JSON schema and requested-field list

- `_derive_json_schema` turns `response_fields` into the lightweight
  `{field: "type"}` schema the adapter forces the model to emit (default type
  `string`).
- `_derive_requested_fields` is the ordered list of field names
  (`list(response_fields.keys())`).
- `_render_requested_fields` renders that list into the `{{ requested_fields }}`
  instruction string.

After generation, `_validate_output` builds the returned **patch** by keeping
only recognised, non-null fields from the model output:

- a key **not** in `response_fields` is dropped (the manifest never requested
  it);
- a `null` value is dropped (leave the field untouched);
- `schema_json` must be an object or it is dropped (JSON-LD);
- `source_css` is sanitised — a value containing `<script` is dropped, otherwise
  kept as plain stylesheet text.

The route returns `{"patch": ..., "provider": ..., "model": ...}`.

## The per-action requested-field sets

The shipped manifests give each action its own request/response shape:

| Action     | manifest file       | `request_context`                                   | `response_fields`                                                                                             |
|------------|---------------------|-----------------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| article    | `vars-article.json` | title, excerpt, content_html, type                  | content_html, title, excerpt                                                                                 |
| seo        | `vars-seo.json`     | title, content_html, type                           | meta_title, meta_description, meta_keywords, og_title, og_description, schema_json (object)                  |
| restyle    | `vars-restyle.json` | title, content_html, source_css, type               | source_css, content_html                                                                                     |
| freeform   | `vars-freeform.json`| title, excerpt, content_html, source_css, type      | content_html, source_css, excerpt, title, meta_title, meta_description, meta_keywords, og_title, og_description, schema_json |
| (default)  | `template-1-vars.json` | title, excerpt, content_html, type               | content_html, title, excerpt                                                                                 |

The default contribution is the core, AI-authorable `CmsPost` fields. URLs /
robots / canonical / type / layout / style are **not** model-invented — they are
simply absent from the response sets.

## How to add or remove a field

### A core CmsPost field

Edit the action's `vars-<action>.json` manifest. To **add** a core field, add it
under `response_fields` (and, if the template should feed it back in, under
`request_context`):

```jsonc
"response_fields": {
  "content_html":     { "type": "string" },
  "title":            { "type": "string" },
  "excerpt":          { "type": "string" },
  "meta_description": { "type": "string" }   // newly added
}
```

To **remove** a field, delete its `response_fields` entry — the service will drop
it from both the schema and the requested-field instruction, and `_validate_output`
will reject it if the model emits it anyway. No code change is needed in either
case.

### An S77 custom-field key

S77 custom-field defs are scoped to the `cms_post` entity type
(`CMS_POST_ENTITY_TYPE = "cms_post"`). Mark the manifest entry with
`"custom_field": true`:

```jsonc
"response_fields": {
  "reading_time": { "type": "number", "custom_field": true }
}
```

When a `response_fields` entry is flagged `custom_field`, the service enriches
the `{{ requested_fields }}` instruction with that field's S77 **type and
options** via `_render_requested_fields` / `_describe_custom_field`, e.g.:

```
reading_time (custom field, type number)
priority (custom field, type select, one of: low, medium, high)
```

The defs come from the S77 port resolved at runtime
(`field_defs_provider.get_field_defs("cms_post")`, keyed by each def's `key`).
S77 is a **soft** dependency: when the port is absent the service degrades to
core fields only (a custom-field key then renders as a plain name). The route
resolves the port from the DI container
(`current_app.container.tags_and_custom_fields`) and passes `None` when it is not
wired. The service **never persists** custom-field values — they ride back in the
patch and land in the S77 editor inputs.

## How to ship a new template set / action

To add a new action `myaction`:

1. Drop a flow file `loopforge-flow-myaction.yaml` naming its template + vars:

   ```yaml
   flow:
     name: myaction
     steps:
       - template: template-1.json
         vars: vars-myaction.json
   ```

2. Drop the manifest `vars-myaction.json` with the `request_context` /
   `response_fields` for that action (optionally a new `template-N.json` if the
   prompt wording differs).
3. Call the endpoint with `{"action": "myaction", ...}`. No backend code change
   — the loader resolves `loopforge-flow-myaction.yaml` by name and falls back to
   the default flow if it is missing.

Ship these under the plugin's `templates/prompts/` as defaults, or drop them
straight into `${VBWD_VAR_DIR}/assets/cms-ai/prompts/` per instance (the var-dir
copy wins).

## Plugin config keys that feed the scope

From `DEFAULT_CONFIG` (`plugins/cms-ai/__init__.py`) and exposed via
`config.json` / `admin-config.json`:

| Key                | Default        | Role in generation                                            |
|--------------------|----------------|---------------------------------------------------------------|
| `llm_api_endpoint` | `""`           | OpenAI-compatible base URL (blank = provider default)         |
| `llm_api_key`      | `""`           | provider key — server-side only, never sent to the browser    |
| `llm_model`        | `gpt-4o-mini`  | model name; provider inferred (`claude-*` → Anthropic)        |
| `temperature`      | `0.7`          | sampling temperature → `{{ temperature }}`                    |
| `max_tokens`       | `4000`         | adapter `max_tokens`                                          |
| `timeout`          | `60`           | request timeout (seconds)                                     |
| `json_retry_max`   | `3`            | LoopForge JSON-repair retries                                 |
| `prompts_dir`      | `""`           | optional override of the asset-storage prompts dir           |
