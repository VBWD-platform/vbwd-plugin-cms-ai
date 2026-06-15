"""CmsAiImageService — prompt -> generated image -> global gallery -> content_html.

Unlike text generation (stateless, returns a patch only), the image path DOES
persist: it writes the generated asset to the global CMS image gallery
immediately, by the operator's explicit intent. The service is the only layer
that knows about the gallery and the plugin config; LoopForge's
``ReplicateImageAdapter`` only turns a prompt into JPEG bytes (no DB, no
gallery, no filesystem), and CMS's own ``CmsImageService`` owns the upload —
this service composes them and appends the canonical gallery-image ``<img>`` to
the post body, returning a patch.

Reuse, not duplication: the upload goes through CMS's ``CmsImageService``
(declared ``cms`` plugin dependency), so there is exactly one home for the
gallery upload logic. The Replicate token lives in the server-side plugin config
and never rides back in the returned patch or in any raised error message.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# LoopForge is imported as the top-level ``loopforge`` package; the source
# package ``__init__`` places the plugin root on ``sys.path`` so this resolves.
from loopforge import LoopForgeError

_DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-schnell"
_DEFAULT_WIDTH = 1024
_DEFAULT_HEIGHT = 1024
_GENERATED_FILENAME = "ai-generated.jpg"
_JPEG_MIME_TYPE = "image/jpeg"


class CmsAiImageError(Exception):
    """Raised when image generation fails; carries a safe (token-free) message."""


class CmsAiImageService:
    """Generate an image, upload it to the gallery, append it to ``content_html``."""

    def __init__(
        self,
        *,
        config: Dict[str, Any],
        image_adapter: Any,
        gallery_service: Any,
    ) -> None:
        self._config = config or {}
        # LoopForge ``ImageAdapter`` (Replicate impl) — prompt -> JPEG bytes.
        self._image_adapter = image_adapter
        # CMS's own ``CmsImageService`` — the single home of the gallery upload.
        self._gallery_service = gallery_service

    def generate(self, *, prompt: str, content_html: str) -> Dict[str, Any]:
        """Generate an image and return ``{patch: {content_html}, image: {...}}``."""
        self._guard_enabled()
        clean_prompt = self._require_prompt(prompt)

        image_bytes = self._generate_bytes(clean_prompt)
        gallery_image = self._upload_to_gallery(image_bytes, clean_prompt)

        appended_html = self._append_image(content_html or "", gallery_image)
        return {
            "patch": {"content_html": appended_html},
            "image": {
                "id": gallery_image.get("id"),
                "slug": gallery_image.get("slug"),
                "url_path": gallery_image.get("url_path"),
            },
        }

    # -- guards -------------------------------------------------------------

    def _guard_enabled(self) -> None:
        if not self._config.get("image_enabled"):
            raise CmsAiImageError("Image generation is disabled")
        if not str(self._config.get("replicate_api_token") or "").strip():
            raise CmsAiImageError("Image generation is not configured")

    @staticmethod
    def _require_prompt(prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise CmsAiImageError("Missing image prompt")
        return prompt.strip()

    # -- steps --------------------------------------------------------------

    def _generate_bytes(self, prompt: str) -> bytes:
        try:
            return self._image_adapter.generate(
                prompt,
                model=str(self._config.get("image_model") or _DEFAULT_IMAGE_MODEL),
                width=int(self._config.get("image_width", _DEFAULT_WIDTH)),
                height=int(self._config.get("image_height", _DEFAULT_HEIGHT)),
            )
        except LoopForgeError as adapter_error:
            # Never echo the token; surface a safe message only.
            raise CmsAiImageError("Image generation failed") from adapter_error

    def _upload_to_gallery(self, image_bytes: bytes, caption: str) -> Dict[str, Any]:
        return self._gallery_service.upload_image(
            file_data=image_bytes,
            filename=_GENERATED_FILENAME,
            mime_type=_JPEG_MIME_TYPE,
            caption=caption,
        )

    @staticmethod
    def _append_image(content_html: str, gallery_image: Dict[str, Any]) -> str:
        """Append the canonical global-gallery ``<img>`` to the current body."""
        url_path = _escape_attribute(gallery_image.get("url_path") or "")
        caption = _escape_attribute(gallery_image.get("caption") or "")
        slug = _escape_attribute(gallery_image.get("slug") or "")
        img_tag = f'<img src="{url_path}" alt="{caption}" data-cms-image="{slug}">'
        return f"{content_html}{img_tag}"


def _escape_attribute(value: Optional[str]) -> str:
    """Minimal HTML-attribute escaping for the gallery ``<img>`` markup."""
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
