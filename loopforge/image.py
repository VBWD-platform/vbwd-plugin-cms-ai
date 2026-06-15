"""Image adapters — one ``generate()`` surface, a Replicate implementation.

``ImageAdapter`` is the Liskov-substitutable contract:
``generate(prompt, *, model, width, height) -> bytes``. The bytes are JPEG.

:class:`ReplicateImageAdapter` drives the ``replicate`` SDK
(``black-forest-labs/flux-schnell`` by default), normalises the assorted
response shapes Replicate can return (URL string / ``FileOutput`` object /
list / dict — the same variants loopai's executor handled), downloads the
image and re-encodes it as JPEG. It returns raw bytes only — no gallery, no
DB, no filesystem — which keeps it extractable. Any SDK or download failure
becomes a :class:`LoopForgeError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from io import BytesIO
from typing import Any, Optional

import replicate
import requests
from PIL import Image

from .errors import AdapterError

_DEFAULT_IMAGE_MODEL = "black-forest-labs/flux-schnell"
_DEFAULT_WIDTH = 1024
_DEFAULT_HEIGHT = 1024
_DOWNLOAD_TIMEOUT_SECONDS = 30
_JPEG_QUALITY = 95


class ImageAdapter(ABC):
    """One text-to-image surface; concrete providers implement ``generate``."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        model: str = _DEFAULT_IMAGE_MODEL,
        width: int = _DEFAULT_WIDTH,
        height: int = _DEFAULT_HEIGHT,
    ) -> bytes:
        """Return JPEG bytes for ``prompt``. Raises :class:`LoopForgeError` on failure."""
        raise NotImplementedError


class ReplicateImageAdapter(ImageAdapter):
    """Text-to-image via the ``replicate`` SDK (Black Forest Labs FLUX)."""

    def __init__(self, *, api_token: str) -> None:
        self._api_token = api_token

    def generate(
        self,
        prompt: str,
        *,
        model: str = _DEFAULT_IMAGE_MODEL,
        width: int = _DEFAULT_WIDTH,
        height: int = _DEFAULT_HEIGHT,
    ) -> bytes:
        client = replicate.Client(api_token=self._api_token)
        try:
            raw_output = client.run(
                model,
                input={"prompt": prompt, "width": width, "height": height},
            )
        except Exception as sdk_error:
            raise AdapterError("Replicate image generation failed") from sdk_error

        image_url = _resolve_image_url(raw_output)
        if not image_url:
            raise AdapterError("Replicate returned no usable image URL")

        image_bytes = _download(image_url)
        return _to_jpeg_bytes(image_bytes)


def _resolve_image_url(raw_output: Any) -> Optional[str]:
    """Normalise a Replicate response into a single image URL string.

    Handles the shapes the SDK can emit: a list (take the first element), a
    ``FileOutput`` object exposing ``.url``, a dict (``url`` / ``output``), a
    plain URL string, or anything whose ``str()`` begins with ``http``.
    """
    candidate = (
        raw_output[0] if isinstance(raw_output, list) and raw_output else raw_output
    )

    if candidate is None:
        return None
    if hasattr(candidate, "url"):
        return candidate.url
    if isinstance(candidate, dict):
        return _resolve_url_from_dict(candidate)
    if isinstance(candidate, str):
        return candidate

    candidate_text = str(candidate)
    return candidate_text if candidate_text.startswith("http") else None


def _resolve_url_from_dict(candidate: dict) -> Optional[str]:
    """Extract an image URL from the common dict response shapes."""
    url_value = candidate.get("url")
    if isinstance(url_value, str):
        return url_value

    output_list = candidate.get("output")
    if isinstance(output_list, list) and output_list:
        first_output = output_list[0]
        if isinstance(first_output, str):
            return first_output
        if hasattr(first_output, "url"):
            return first_output.url
    return None


def _download(image_url: str) -> bytes:
    """Download ``image_url`` and return the raw response bytes."""
    try:
        response = requests.get(image_url, timeout=_DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as download_error:
        raise AdapterError("Replicate image download failed") from download_error
    return response.content


def _to_jpeg_bytes(image_bytes: bytes) -> bytes:
    """Re-encode arbitrary image bytes as JPEG and return them."""
    try:
        source_image: Image.Image = Image.open(BytesIO(image_bytes))
        source_image.load()
        if source_image.mode != "RGB":
            source_image = source_image.convert("RGB")
        jpeg_buffer = BytesIO()
        source_image.save(jpeg_buffer, format="JPEG", quality=_JPEG_QUALITY)
        return jpeg_buffer.getvalue()
    except (OSError, ValueError) as decode_error:
        raise AdapterError("Could not decode the generated image") from decode_error
