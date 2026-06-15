"""Unit tests for the cms-ai generate-image route.

Engineering requirements (binding, restated): TDD-first (RED before the route);
DevOps-first (a minimal Flask app, no DB/network; auth + service mocked);
SOLID/DI/DRY (the route is thin — it delegates to CmsAiImageService); Liskov;
clean code (a safe error message, the Replicate token never echoed); no
overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin cms-ai --full``.
"""

from importlib import import_module
from unittest.mock import MagicMock
from uuid import UUID

from flask import Flask

routes_module = import_module("plugins.cms-ai.cms-ai.routes")
image_service_module = import_module(
    "plugins.cms-ai.cms-ai.services.cms_ai_image_service"
)

USER_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
AUTH_HEADERS = {"Authorization": "Bearer test_token"}
REPLICATE_TOKEN = "r8_super_secret_replicate_token"


def _mock_config_store():
    store = MagicMock()
    entry = MagicMock()
    entry.status = "enabled"
    store.get_by_name.return_value = entry
    store.get_config.return_value = {
        "image_enabled": True,
        "replicate_api_token": REPLICATE_TOKEN,
        "image_model": "black-forest-labs/flux-schnell",
        "image_width": 1024,
        "image_height": 1024,
    }
    return store


def _make_app(mocker, *, is_admin=True, has_permission=True):
    app = Flask(__name__)
    app.config["TESTING"] = True

    mock_auth_service = MagicMock()
    mock_auth_service.return_value.verify_token.return_value = str(USER_ID)
    mocker.patch("vbwd.middleware.auth.AuthService", mock_auth_service)

    mock_user = MagicMock()
    mock_user.id = USER_ID
    mock_user.status.value = "ACTIVE"
    mock_user.is_admin = is_admin
    mock_user.has_permission.return_value = has_permission

    mock_user_repo = MagicMock()
    mock_user_repo.return_value.find_by_id.return_value = mock_user
    mocker.patch("vbwd.middleware.auth.UserRepository", mock_user_repo)
    mocker.patch("vbwd.middleware.auth.db", MagicMock())

    app.register_blueprint(routes_module.cms_ai_bp, url_prefix="/api/v1/plugins/cms-ai")
    app.config_store = _mock_config_store()
    app.container = MagicMock()
    return app


def test_generate_image_requires_admin(mocker):
    app = _make_app(mocker, is_admin=False)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": ""}},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 403


def test_generate_image_requires_cms_manage_permission(mocker):
    app = _make_app(mocker, has_permission=False)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": ""}},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 403


def test_generate_image_requires_auth(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": ""}},
    )

    assert resp.status_code == 401


def test_generate_image_happy_path_returns_patch_and_image(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.return_value = {
        "patch": {
            "content_html": '<p>Body</p><img src="/u.jpg" '
            'alt="a render" data-cms-image="a-render">'
        },
        "image": {"id": "img-1", "slug": "a-render", "url_path": "/u.jpg"},
    }
    mocker.patch.object(
        routes_module, "_build_image_service", return_value=fake_service
    )

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": "<p>Body</p>"}},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert 'data-cms-image="a-render"' in body["patch"]["content_html"]
    assert body["image"]["slug"] == "a-render"


def test_generate_image_passes_prior_content_html_to_service(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.return_value = {"patch": {"content_html": ""}, "image": {}}
    mocker.patch.object(
        routes_module, "_build_image_service", return_value=fake_service
    )

    client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": "<p>Prior</p>"}},
        headers=AUTH_HEADERS,
    )

    _args, kwargs = fake_service.generate.call_args
    assert kwargs["prompt"] == "a render"
    assert kwargs["content_html"] == "<p>Prior</p>"


def test_generate_image_missing_prompt_is_400(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "   ", "context": {"content_html": ""}},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 400


def test_generate_image_disabled_is_4xx(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.side_effect = image_service_module.CmsAiImageError(
        "image generation is disabled"
    )
    mocker.patch.object(
        routes_module, "_build_image_service", return_value=fake_service
    )

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": ""}},
        headers=AUTH_HEADERS,
    )

    assert 400 <= resp.status_code < 500


def test_generate_image_error_never_leaks_token(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.side_effect = image_service_module.CmsAiImageError(
        f"replicate exploded with {REPLICATE_TOKEN}"
    )
    mocker.patch.object(
        routes_module, "_build_image_service", return_value=fake_service
    )

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": ""}},
        headers=AUTH_HEADERS,
    )

    assert REPLICATE_TOKEN not in resp.get_data(as_text=True)


def test_generate_image_success_response_never_contains_token(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.return_value = {
        "patch": {"content_html": "<img src='/u.jpg'>"},
        "image": {"id": "img-1", "slug": "a-render", "url_path": "/u.jpg"},
    }
    mocker.patch.object(
        routes_module, "_build_image_service", return_value=fake_service
    )

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate-image",
        json={"prompt": "a render", "context": {"content_html": ""}},
        headers=AUTH_HEADERS,
    )

    assert REPLICATE_TOKEN not in resp.get_data(as_text=True)
