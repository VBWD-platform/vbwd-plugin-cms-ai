"""Unit tests for ReplicateImageAdapter (replicate client + HTTP mocked)."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from loopforge.errors import AdapterError
from loopforge.image import ReplicateImageAdapter


def _png_bytes() -> bytes:
    """A tiny in-memory PNG to stand in for a downloaded image."""
    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=(10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _patch_download(monkeyable_bytes: bytes):
    response = SimpleNamespace(
        content=monkeyable_bytes,
        raise_for_status=lambda: None,
    )
    return patch("loopforge.image.requests.get", return_value=response)


def _run_with_response(replicate_output):
    fake_client = MagicMock()
    fake_client.run.return_value = replicate_output
    with patch("loopforge.image.replicate.Client", return_value=fake_client):
        with _patch_download(_png_bytes()):
            adapter = ReplicateImageAdapter(api_token="rep-token")
            result = adapter.generate("a cat", model="black-forest-labs/flux-schnell")
    return result, fake_client


def _assert_is_jpeg(image_bytes: bytes):
    assert isinstance(image_bytes, bytes)
    decoded = Image.open(BytesIO(image_bytes))
    assert decoded.format == "JPEG"


def test_generate_passes_prompt_width_height_to_client_run():
    _, fake_client = _run_with_response("https://cdn.example.com/x.png")
    call_args = fake_client.run.call_args
    assert call_args.args[0] == "black-forest-labs/flux-schnell"
    assert call_args.kwargs["input"] == {
        "prompt": "a cat",
        "width": 1024,
        "height": 1024,
    }


def test_normalises_url_string_response():
    result, _ = _run_with_response("https://cdn.example.com/x.png")
    _assert_is_jpeg(result)


def test_normalises_file_output_object_response():
    file_output = SimpleNamespace(url="https://cdn.example.com/y.png")
    result, _ = _run_with_response(file_output)
    _assert_is_jpeg(result)


def test_normalises_list_response():
    result, _ = _run_with_response(["https://cdn.example.com/z.png"])
    _assert_is_jpeg(result)


def test_normalises_dict_response():
    result, _ = _run_with_response({"url": "https://cdn.example.com/d.png"})
    _assert_is_jpeg(result)


def test_normalises_dict_output_list_response():
    result, _ = _run_with_response({"output": ["https://cdn.example.com/o.png"]})
    _assert_is_jpeg(result)


def test_unresolvable_response_raises_loopforge_error():
    fake_client = MagicMock()
    fake_client.run.return_value = None
    with patch("loopforge.image.replicate.Client", return_value=fake_client):
        adapter = ReplicateImageAdapter(api_token="rep-token")
        with pytest.raises(AdapterError):
            adapter.generate("a cat")


def test_sdk_error_raises_loopforge_error():
    fake_client = MagicMock()
    fake_client.run.side_effect = RuntimeError("replicate down")
    with patch("loopforge.image.replicate.Client", return_value=fake_client):
        adapter = ReplicateImageAdapter(api_token="rep-token")
        with pytest.raises(AdapterError):
            adapter.generate("a cat")


def test_download_failure_raises_loopforge_error():
    import requests

    fake_client = MagicMock()
    fake_client.run.return_value = "https://cdn.example.com/x.png"
    with patch("loopforge.image.replicate.Client", return_value=fake_client):
        with patch(
            "loopforge.image.requests.get",
            side_effect=requests.RequestException("timeout"),
        ):
            adapter = ReplicateImageAdapter(api_token="rep-token")
            with pytest.raises(AdapterError):
                adapter.generate("a cat")
