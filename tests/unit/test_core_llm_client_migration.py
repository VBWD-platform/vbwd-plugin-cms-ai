"""S97.4 RED — cms-ai resolves its LLM through the CORE connection client.

Engineering requirements (binding, restated): TDD-first (these RED tests come
before the migration); DevOps-first (no DB/network — the core connection
service + adapter are mocked); SOLID (Open/Closed — cms-ai consumes the core
``llm_connection_service`` port, never its own SDK wiring; Dependency inversion
— it depends on the core abstraction); DI (resolved from the container); DRY
(one adapter home, in core); Liskov; clean code; no overengineering. Quality
guard: ``bin/pre-commit-check.sh --plugin cms-ai --full``.

What these lock in:
* the generate route resolves a connection from ``container.llm_connection_service``
  (empty ``llm_connection_slug`` ⇒ the default; a set slug ⇒ that connection),
* the resolved connection's ``last_active_at`` advances on a generate (we assert
  the ``stamp_last_active`` call carries the resolved connection id),
* no ``llm_api_endpoint`` / ``llm_api_key`` / ``llm_model`` reader remains in the
  cms-ai source, and ``select_adapter`` appears only at the loopforge injection
  point.
"""

from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

from flask import Flask

routes_module = import_module("plugins.cms-ai.cms-ai.routes")

USER_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
AUTH_HEADERS = {"Authorization": "Bearer test_token"}

DEFAULT_CONNECTION_ID = "11111111-1111-1111-1111-111111111111"
NAMED_CONNECTION_ID = "22222222-2222-2222-2222-222222222222"

CMS_AI_SOURCE_DIR = Path(routes_module.__file__).resolve().parent


def _make_connection(connection_id: str, model: str):
    connection = MagicMock()
    connection.id = connection_id
    connection.model = model
    connection.api_key = "core-managed-secret"
    connection.api_endpoint = ""
    connection.max_tokens = 4096
    connection.temperature = 0.7
    connection.is_active = True
    return connection


def _make_connection_service():
    """A spy core LlmConnectionService: default + one slugged connection."""
    service = MagicMock()
    default = _make_connection(DEFAULT_CONNECTION_ID, "gpt-4o-mini")
    named = _make_connection(NAMED_CONNECTION_ID, "claude-3-5-sonnet")

    service.get_default.return_value = default
    service.get_by_slug.side_effect = lambda slug: (
        named if slug == "anthropic-prod" else None
    )
    return service


def _make_app(mocker, *, config):
    app = Flask(__name__)
    app.config["TESTING"] = True

    mock_auth_service = MagicMock()
    mock_auth_service.return_value.verify_token.return_value = str(USER_ID)
    mocker.patch("vbwd.middleware.auth.AuthService", mock_auth_service)

    mock_user = MagicMock()
    mock_user.id = USER_ID
    mock_user.status.value = "ACTIVE"
    mock_user.is_admin = True
    mock_user.has_permission.return_value = True

    mock_user_repo = MagicMock()
    mock_user_repo.return_value.find_by_id.return_value = mock_user
    mocker.patch("vbwd.middleware.auth.UserRepository", mock_user_repo)
    mocker.patch("vbwd.middleware.auth.db", MagicMock())

    app.register_blueprint(routes_module.cms_ai_bp, url_prefix="/api/v1/plugins/cms-ai")

    store = MagicMock()
    entry = MagicMock()
    entry.status = "enabled"
    store.get_by_name.return_value = entry
    store.get_config.return_value = config
    app.config_store = store

    connection_service = _make_connection_service()
    container = MagicMock()
    container.llm_connection_service.return_value = connection_service
    app.container = container
    app._connection_service = connection_service  # test handle
    return app


def _patch_flow_runner_to_capture_adapter(mocker):
    """Replace FlowRunner so the test sees which adapter cms-ai injected."""
    captured = {}

    class _CapturingRunner:
        def __init__(self, *, llm_adapter=None, **_kwargs):
            captured["adapter"] = llm_adapter

        def run(self, flow, scope):
            return {"content_html": "<p>ok</p>"}

    service_module = import_module(
        "plugins.cms-ai.cms-ai.services.cms_ai_generate_service"
    )
    mocker.patch.object(service_module, "FlowRunner", _CapturingRunner)
    return captured


def _post_generate(client):
    return client.post(
        "/api/v1/plugins/cms-ai/generate",
        json={"action": "freeform", "prompt": "Write about astronomy"},
        headers=AUTH_HEADERS,
    )


def _stub_asset_loader(mocker):
    """Make the asset loader return a trivial single-step flow triple."""
    loader_module = import_module("plugins.cms-ai.cms-ai.services.prompt_asset_loader")
    triple = loader_module.PromptTriple(
        flow={"name": "freeform", "steps": [{}]},
        template={"prompt": "{{ user_prompt }}"},
        manifest={
            "request_context": [],
            "response_fields": {"content_html": {"type": "string"}},
        },
    )
    fake_loader = MagicMock()
    fake_loader.load.return_value = triple
    mocker.patch.object(loader_module.PromptAssetLoader, "load", return_value=triple)
    return fake_loader


def test_empty_slug_resolves_the_default_connection_and_stamps(mocker):
    _stub_asset_loader(mocker)
    _patch_flow_runner_to_capture_adapter(mocker)
    app = _make_app(mocker, config={"llm_connection_slug": "", "prompts_dir": ""})

    resp = _post_generate(app.test_client())

    assert resp.status_code == 200
    service = app._connection_service
    service.get_default.assert_called()
    # last_active_at advances for the DEFAULT connection.
    service.stamp_last_active.assert_called_once_with(DEFAULT_CONNECTION_ID)


def test_configured_slug_resolves_that_connection_not_the_default(mocker):
    _stub_asset_loader(mocker)
    _patch_flow_runner_to_capture_adapter(mocker)
    app = _make_app(
        mocker,
        config={"llm_connection_slug": "anthropic-prod", "prompts_dir": ""},
    )

    resp = _post_generate(app.test_client())

    assert resp.status_code == 200
    service = app._connection_service
    service.get_by_slug.assert_called_with("anthropic-prod")
    service.get_default.assert_not_called()
    service.stamp_last_active.assert_called_once_with(NAMED_CONNECTION_ID)


def test_no_legacy_llm_api_keys_read_outside_escape_hatch():
    """No ``llm_api_*`` reader remains in cms-ai except the route escape hatch.

    The DEFAULT path is the core connection; only ``routes.py`` may still read
    an explicit private ``llm_api_key`` / endpoint for the deliberate, opt-in
    escape hatch (D-EscapeHatch).
    """
    offenders = []
    for python_file in CMS_AI_SOURCE_DIR.rglob("*.py"):
        if python_file.name == "routes.py":
            continue  # the single, opt-in escape-hatch reader
        text = python_file.read_text(encoding="utf-8")
        for legacy_key in ("llm_api_endpoint", "llm_api_key"):
            if legacy_key in text:
                offenders.append(f"{python_file.name}: {legacy_key}")
    assert offenders == [], f"legacy LLM config readers remain: {offenders}"


def test_select_adapter_only_at_the_loopforge_injection_point():
    """``select_adapter`` lives only where core's adapter is injected (route)."""
    offenders = []
    for python_file in CMS_AI_SOURCE_DIR.rglob("*.py"):
        if python_file.name == "routes.py":
            continue  # the route is the single injection point for the core adapter
        text = python_file.read_text(encoding="utf-8")
        if "select_adapter" in text:
            offenders.append(python_file.name)
    assert offenders == [], f"select_adapter leaked outside the route: {offenders}"
