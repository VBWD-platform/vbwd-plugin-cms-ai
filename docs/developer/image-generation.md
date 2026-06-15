# Image generation — prompt → global gallery → content_html

> Developer documentation for the `cms-ai` plugin (Sprint S41).
> Covers the Replicate image adapter, the gallery upload path, and the
> `POST /generate-image` route.

## Overview

The image path is a first-class action driven by the editor's **"Generate only
image from the prompt"** checkbox. Unlike text generation (stateless, returns a
patch only), the image path **does persist** — it writes the generated asset to
the global CMS image gallery **immediately**, by the operator's explicit intent.

The split of responsibilities is deliberate:

- **LoopForge `ReplicateImageAdapter`** turns a prompt into JPEG **bytes** — no
  DB, no gallery, no filesystem (keeps it extractable).
- **`CmsAiImageService`** composes the adapter with the gallery and the plugin
  config: it generates bytes, uploads them to the global gallery, and appends the
  canonical gallery `<img>` to the post body.
- **CMS's own `CmsImageService`** owns the actual upload — there is exactly one
  home for the gallery-upload logic (reuse, not duplication).

## The Replicate image adapter (`loopforge/image.py`)

`ImageAdapter` is the Liskov-substitutable contract; `ReplicateImageAdapter` is
the Replicate / Black Forest Labs FLUX implementation:

```python
class ImageAdapter(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, model="black-forest-labs/flux-schnell",
                 width=1024, height=1024) -> bytes: ...

class ReplicateImageAdapter(ImageAdapter):
    def __init__(self, *, api_token: str) -> None
    def generate(self, prompt, *, model="black-forest-labs/flux-schnell",
                 width=1024, height=1024) -> bytes
```

`generate` builds `replicate.Client(api_token=...)`, calls
`client.run(model, input={"prompt": prompt, "width": width, "height": height})`,
then normalises whatever Replicate returns into a single image URL, downloads it
and re-encodes it as JPEG. It returns **raw JPEG bytes only.**

- **Response normalisation** (`_resolve_image_url` / `_resolve_url_from_dict`)
  handles the shapes the SDK can emit: a list (first element), a `FileOutput`
  object exposing `.url`, a dict (`url` or `output[0]`), a plain URL string, or
  anything whose `str()` begins with `http`.
- **Download** (`_download`) uses `requests.get(..., timeout=30)` and raises on a
  bad status.
- **Re-encode** (`_to_jpeg_bytes`) opens the bytes with Pillow, converts to RGB
  if needed and saves as JPEG at quality 95.

Any SDK error, missing URL, download failure or decode failure becomes an
`AdapterError` (a `LoopForgeError`). **The token never appears in an error
message.**

## Config keys (`DEFAULT_CONFIG`, `plugins/cms-ai/__init__.py`)

| Key                   | Default                          | Role                                          |
|-----------------------|----------------------------------|-----------------------------------------------|
| `image_enabled`       | `True`                           | master switch; off → endpoint 4xx's           |
| `replicate_api_token` | `""`                             | Replicate token — **server-side only**        |
| `image_model`         | `black-forest-labs/flux-schnell` | Replicate model id                            |
| `image_width`         | `1024`                           | generated width (px)                          |
| `image_height`        | `1024`                           | generated height (px)                         |

All are exposed in `admin-config.json` under the **Image Generation** tab;
`replicate_api_token` uses the `password` component.

## The service flow (`cms-ai/services/cms_ai_image_service.py`)

```python
class CmsAiImageService:
    def __init__(self, *, config, image_adapter, gallery_service) -> None
    def generate(self, *, prompt: str, content_html: str) -> Dict[str, Any]
```

`generate` runs four stages:

1. **Guard** — `_guard_enabled` raises `CmsAiImageError("Image generation is
   disabled")` when `image_enabled` is falsy, and
   `CmsAiImageError("Image generation is not configured")` when
   `replicate_api_token` is blank. `_require_prompt` rejects an empty prompt.
2. **Generate bytes** — `_generate_bytes` calls
   `self._image_adapter.generate(prompt, model=image_model, width=image_width,
   height=image_height)`. A `LoopForgeError` is caught and re-raised as
   `CmsAiImageError("Image generation failed")` (**token never echoed**).
3. **Upload to the global gallery** — `_upload_to_gallery` hands the bytes to
   CMS's own `CmsImageService.upload_image`:

   ```python
   gallery_service.upload_image(
       file_data=image_bytes,
       filename="ai-generated.jpg",
       mime_type="image/jpeg",
       caption=prompt,
   )
   ```

   This is the **same global media gallery** as
   `POST /api/v1/admin/cms/images/upload`. It returns the gallery image dict
   (`id`, `slug`, `url_path`, `caption`). Reuse here is enforced by the declared
   `cms` plugin dependency (`PluginMetadata.dependencies=["cms"]`), so there is no
   duplicate upload logic.
4. **Append + return a patch** — `_append_image` appends the canonical
   global-gallery `<img>` to the **current** `content_html`:

   ```html
   <img src="{url_path}" alt="{caption}" data-cms-image="{slug}">
   ```

   (attribute values are HTML-escaped via `_escape_attribute`). The
   `data-cms-image="{slug}"` marker is the canonical "image from the global
   gallery" markup the renderer already understands. The method returns:

   ```jsonc
   {
     "patch": { "content_html": "<existing body><img ...>" },
     "image": { "id": "...", "slug": "...", "url_path": "..." }
   }
   ```

### Persistence timing

> The **image asset is persisted in the gallery immediately** (stage 3). Only the
> **post content reference** waits for the operator to click **Save** — generate
> never auto-saves the post. If the operator discards the post, the gallery image
> simply remains in the gallery (reusable), which is the intended global-gallery
> behaviour.

## The route — `POST /api/v1/plugins/cms-ai/generate-image`

Defined in `cms-ai/routes.py`, gated `@require_auth @require_admin
@require_permission("cms.manage")` (URL prefix `/api/v1/plugins/cms-ai`).

**Request:**

```jsonc
{
  "prompt": "a watercolour mountain at dawn",
  "context": { "content_html": "<p>existing body</p>" }
}
```

**Response (200):**

```jsonc
{
  "patch": { "content_html": "<p>existing body</p><img src=\"...\" alt=\"...\" data-cms-image=\"...\">" },
  "image": { "id": "...", "slug": "...", "url_path": "..." }
}
```

The route:

1. `_check_enabled()` — returns 503 if the plugin system is unavailable, 404 if
   the `cms-ai` plugin entry is missing or not `enabled`, else the server-side
   config.
2. Validates `prompt` (non-empty string → else 400) and `context` (must be an
   object → else 400); reads `content_html` from `context`.
3. `_build_image_service(config)` builds the service with a
   `ReplicateImageAdapter(api_token=config["replicate_api_token"])` and the CMS
   gallery service obtained from the cms plugin's own factory
   (`plugins.cms.src.routes._image_service()`).
4. On `CmsAiImageError` (disabled / unconfigured / empty prompt / generation
   failure) it logs and returns **`{"error": "Image generation failed"}` with
   HTTP 400** — a single safe message.

### Gating and token safety

- `image_enabled = false` **or** an empty `replicate_api_token` → the service's
  `_guard_enabled` raises and the route returns a **4xx** with a safe message.
  (In the editor UI the checkbox is correspondingly hidden/disabled.)
- The Replicate **token is server-side only** — it lives in the plugin config and
  is never sent to the browser, never returned in the response body, and never
  placed into an error message (the adapter and service both normalise failures
  to token-free messages).

## Image steps inside a multi-step flow

The same `ReplicateImageAdapter` is also the `image` **step** referenced by the
forward-compatible multi-step `article → seo → image` flow (see `loopforge.md` /
`field-manifest.md`). In a flow, an image step is a `FlowStep(kind="image",
output_key=..., image_model=..., image_width=..., image_height=...)` and the
`FlowRunner` calls the adapter and merges `{output_key: image_bytes}` into the
scope — the same adapter reached programmatically instead of via the editor
checkbox. The editor action wires only the single-shot path described above.
