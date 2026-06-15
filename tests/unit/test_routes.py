"""Unit tests for the cms-ai generate route (auth + happy path + error safety).

Engineering requirements (binding, restated): TDD-first (RED before the route);
DevOps-first (a minimal Flask app, no DB/network; auth + service mocked);
SOLID/DI/DRY (the route is thin — it delegates to the service); Liskov; clean
code (a safe error message, never the key); no overengineering. Quality guard:
``bin/pre-commit-check.sh --plugin cms-ai --full``.
"""

from importlib import import_module
from unittest.mock import MagicMock
from uuid import UUID

from flask import Flask

routes_module = import_module("plugins.cms-ai.cms-ai.routes")
service_module = import_module("plugins.cms-ai.cms-ai.services.cms_ai_generate_service")

USER_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
AUTH_HEADERS = {"Authorization": "Bearer test_token"}


def _mock_config_store():
    store = MagicMock()
    entry = MagicMock()
    entry.status = "enabled"
    store.get_by_name.return_value = entry
    store.get_config.return_value = {
        "llm_model": "gpt-4o-mini",
        "llm_api_key": "super-secret-key",
        "prompts_dir": "",
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


def test_generate_requires_admin(mocker):
    app = _make_app(mocker, is_admin=False)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={"action": "article", "prompt": "Write"},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 403


def test_generate_requires_cms_manage_permission(mocker):
    app = _make_app(mocker, is_admin=True, has_permission=False)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={"action": "article", "prompt": "Write"},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 403


def test_generate_happy_path_returns_patch(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.return_value = {
        "patch": {"content_html": "<p>Generated</p>"},
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    mocker.patch.object(
        routes_module, "_build_generate_service", return_value=fake_service
    )

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={
            "action": "article",
            "prompt": "Write about astronomy",
            "read_excerpt": True,
            "context": {"title": "Stars", "excerpt": "About stars", "type": "post"},
        },
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["patch"] == {"content_html": "<p>Generated</p>"}
    assert body["model"] == "gpt-4o-mini"


def test_generate_missing_prompt_is_400(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={"action": "article", "prompt": "   "},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 400


def test_generate_adapter_error_is_500_without_key_leak(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    fake_service = MagicMock()
    fake_service.generate.side_effect = service_module.CmsAiGenerateError(
        "super-secret-key blew up"
    )
    mocker.patch.object(
        routes_module, "_build_generate_service", return_value=fake_service
    )

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={"action": "article", "prompt": "Write"},
        headers=AUTH_HEADERS,
    )

    assert resp.status_code == 500
    assert "super-secret-key" not in resp.get_data(as_text=True)


def test_generate_requires_auth(mocker):
    app = _make_app(mocker)
    client = app.test_client()

    resp = client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={"action": "article", "prompt": "Write"},
    )

    assert resp.status_code == 401
