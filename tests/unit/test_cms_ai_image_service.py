"""Unit tests for CmsAiImageService (LoopForge adapter + CmsImageService mocked).

Engineering requirements (binding, restated): TDD-first (RED before the
service); DevOps-first (no network/DB — the Replicate image adapter and the cms
gallery service are both mocked); SOLID/DI/DRY (the service orchestrates only;
image generation lives once in LoopForge, the gallery upload reuses CMS's own
``CmsImageService`` — no duplicate upload logic); Liskov (the injected fakes
honour their contracts); clean code (the Replicate token never rides in the
returned patch); no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin cms-ai --full``.
"""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

service_module = import_module("plugins.cms-ai.cms-ai.services.cms_ai_image_service")

CmsAiImageService = service_module.CmsAiImageService
CmsAiImageError = service_module.CmsAiImageError

REPLICATE_TOKEN = "r8_super_secret_replicate_token"
GALLERY_IMAGE = {
    "id": "11111111-2222-3333-4444-555555555555",
    "slug": "an-astronomy-render",
    "url_path": "/var/assets/cms/images/an-astronomy-render.jpg",
    "caption": "an astronomy render",
}


def _config(**overrides):
    config = {
        "image_enabled": True,
        "replicate_api_token": REPLICATE_TOKEN,
        "image_model": "black-forest-labs/flux-schnell",
        "image_width": 1024,
        "image_height": 1024,
    }
    config.update(overrides)
    return config


def _build_service(config, *, image_adapter=None, gallery_service=None):
    adapter = image_adapter or MagicMock()
    adapter.generate.return_value = b"jpeg-bytes"
    gallery = gallery_service or MagicMock()
    gallery.upload_image.return_value = dict(GALLERY_IMAGE)
    return (
        CmsAiImageService(
            config=config,
            image_adapter=adapter,
            gallery_service=gallery,
        ),
        adapter,
        gallery,
    )


def test_generate_calls_replicate_adapter_with_config_model_and_size():
    service, adapter, _gallery = _build_service(_config())

    service.generate(prompt="an astronomy render", content_html="")

    adapter.generate.assert_called_once()
    _args, kwargs = adapter.generate.call_args
    assert kwargs["model"] == "black-forest-labs/flux-schnell"
    assert kwargs["width"] == 1024
    assert kwargs["height"] == 1024


def test_generate_uploads_bytes_to_global_gallery_with_prompt_caption():
    service, _adapter, gallery = _build_service(_config())

    service.generate(prompt="an astronomy render", content_html="")

    gallery.upload_image.assert_called_once()
    _args, kwargs = gallery.upload_image.call_args
    call = {**dict(zip(("file_data", "filename", "mime_type"), _args)), **kwargs}
    assert call["file_data"] == b"jpeg-bytes"
    assert call["mime_type"] == "image/jpeg"
    assert call["caption"] == "an astronomy render"


def test_generate_appends_img_to_existing_content_html():
    service, _adapter, _gallery = _build_service(_config())

    result = service.generate(
        prompt="an astronomy render",
        content_html="<p>Existing body</p>",
    )

    content_html = result["patch"]["content_html"]
    # The prior body is preserved and the gallery <img> appended after it.
    assert content_html.startswith("<p>Existing body</p>")
    assert '<img src="/var/assets/cms/images/an-astronomy-render.jpg"' in content_html
    assert 'alt="an astronomy render"' in content_html
    assert 'data-cms-image="an-astronomy-render"' in content_html


def test_generate_returns_image_descriptor():
    service, _adapter, _gallery = _build_service(_config())

    result = service.generate(prompt="render", content_html="")

    assert result["image"]["id"] == GALLERY_IMAGE["id"]
    assert result["image"]["slug"] == GALLERY_IMAGE["slug"]
    assert result["image"]["url_path"] == GALLERY_IMAGE["url_path"]


def test_generate_disabled_raises():
    service, _adapter, _gallery = _build_service(_config(image_enabled=False))

    with pytest.raises(CmsAiImageError):
        service.generate(prompt="render", content_html="")


def test_generate_empty_token_raises():
    service, _adapter, _gallery = _build_service(_config(replicate_api_token=""))

    with pytest.raises(CmsAiImageError):
        service.generate(prompt="render", content_html="")


def test_generate_empty_prompt_raises():
    service, _adapter, _gallery = _build_service(_config())

    with pytest.raises(CmsAiImageError):
        service.generate(prompt="   ", content_html="")


def test_adapter_failure_becomes_safe_error_without_token():
    from loopforge import AdapterError

    adapter = MagicMock()
    adapter.generate.side_effect = AdapterError(
        f"replicate failed using {REPLICATE_TOKEN}"
    )
    service, _adapter, _gallery = _build_service(_config(), image_adapter=adapter)

    with pytest.raises(CmsAiImageError) as excinfo:
        service.generate(prompt="render", content_html="")

    assert REPLICATE_TOKEN not in str(excinfo.value)
