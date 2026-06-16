"""cms-ai plugin API routes — the editor text-generate proxy.

``POST /api/v1/plugins/cms-ai/generate`` takes an admin's prompt + page context,
runs the configured LLM through LoopForge and returns a validated patch of CMS
fields. The browser never holds the LLM key: endpoint/key/model live in the
server-side plugin config. Errors surface a safe message only — the key is
never echoed.
"""

import logging

from flask import Blueprint, current_app, jsonify, request

from vbwd.middleware.auth import require_admin, require_auth, require_permission

logger = logging.getLogger(__name__)

cms_ai_bp = Blueprint("cms_ai_plugin", __name__)

PLUGIN_NAME = "cms-ai"
MANAGE_PERMISSION = "cms.manage"

# Fallbacks for the deliberate private-connection escape hatch (D-EscapeHatch).
_DEFAULT_OVERRIDE_MODEL = "gpt-4o-mini"
_DEFAULT_OVERRIDE_MAX_TOKENS = 4000


def _check_enabled():
    """Return ``(config, None)`` when enabled, else ``(None, error_response)``."""
    config_store = getattr(current_app, "config_store", None)
    if not config_store:
        return None, (jsonify({"error": "Plugin system not available"}), 503)

    entry = config_store.get_by_name(PLUGIN_NAME)
    if not entry or entry.status != "enabled":
        return None, (jsonify({"error": "cms-ai plugin not enabled"}), 404)

    return config_store.get_config(PLUGIN_NAME), None


def _service_module():
    """Import the generate-service module (hyphenated package -> importlib)."""
    from importlib import import_module

    return import_module("plugins.cms-ai.cms-ai.services.cms_ai_generate_service")


def _build_generate_service(config):
    """Build the generate service with the asset loader + S77 port (if present).

    The LLM adapter + model come from the CORE LLM connection (S97.4): cms-ai
    resolves the connection named by ``llm_connection_slug`` (empty ⇒ the active
    default), builds the core adapter for it, and stamps ``last_active_at`` so
    the admin can see which connection served the request. cms-ai itself no
    longer holds an API key / endpoint / provider SDK.
    """
    from importlib import import_module

    loader_module = import_module("plugins.cms-ai.cms-ai.services.prompt_asset_loader")

    field_defs_provider = _resolve_field_defs_provider()
    asset_loader = loader_module.PromptAssetLoader(config.get("prompts_dir", ""))
    llm_adapter, model = _resolve_llm_adapter(config)
    return _service_module().CmsAiGenerateService(
        config=config,
        asset_loader=asset_loader,
        field_defs_provider=field_defs_provider,
        llm_adapter=llm_adapter,
        model=model,
    )


def _resolve_llm_adapter(config):
    """Resolve ``(adapter, model)`` for the generate call.

    Default path (S97.4): the core ``llm_connection_service`` resolves the
    connection (by slug, else the active default), the core ``select_adapter``
    builds the provider adapter from it, and ``last_active_at`` is stamped so
    the admin sees which connection was used.

    Escape hatch (D-EscapeHatch): if the operator still supplies an explicit
    private ``llm_api_key`` (+ optional endpoint/model) in plugin config, a
    private adapter is built from those instead of the central connection.
    """
    from vbwd.llm.adapter import select_adapter

    override_key = config.get("llm_api_key")
    if override_key:
        model = str(config.get("llm_model") or _DEFAULT_OVERRIDE_MODEL)
        adapter = select_adapter(
            model=model,
            api_key=override_key,
            endpoint=config.get("llm_api_endpoint", ""),
            max_tokens=int(config.get("max_tokens", _DEFAULT_OVERRIDE_MAX_TOKENS)),
        )
        return adapter, model

    connection_service = current_app.container.llm_connection_service()
    slug = config.get("llm_connection_slug") or None
    connection = (
        connection_service.get_by_slug(slug)
        if slug
        else connection_service.get_default()
    )
    if connection is None or not getattr(connection, "is_active", False):
        target = f"slug '{slug}'" if slug else "default"
        raise _service_module().CmsAiGenerateError(
            f"No active LLM connection for {target}"
        )

    adapter = select_adapter(
        model=connection.model,
        api_key=connection.api_key,
        endpoint=connection.api_endpoint or "",
        max_tokens=connection.max_tokens or _DEFAULT_OVERRIDE_MAX_TOKENS,
    )
    connection_service.stamp_last_active(connection.id)
    return adapter, connection.model


def _resolve_field_defs_provider():
    """Resolve the S77 custom-field port; ``None`` when unavailable."""
    container = getattr(current_app, "container", None)
    provider_factory = getattr(container, "tags_and_custom_fields", None)
    if provider_factory is None:
        # S77 not wired into this container: degrade to core fields only.
        return None
    return provider_factory()


def _image_service_module():
    """Import the image-service module (hyphenated package -> importlib)."""
    from importlib import import_module

    return import_module("plugins.cms-ai.cms-ai.services.cms_ai_image_service")


def _build_image_service(config):
    """Build the image service: LoopForge Replicate adapter + cms gallery service.

    The gallery upload reuses the cms plugin's own ``CmsImageService`` (built by
    the cms route's factory from ``filesystem_manager`` + ``db.session``) — a
    declared ``cms`` plugin dependency, so there is no duplicate upload logic.
    """
    from importlib import import_module

    from loopforge import ReplicateImageAdapter

    image_adapter = ReplicateImageAdapter(
        api_token=config.get("replicate_api_token", "")
    )
    cms_routes = import_module("plugins.cms.src.routes")
    gallery_service = cms_routes._image_service()
    return _image_service_module().CmsAiImageService(
        config=config,
        image_adapter=image_adapter,
        gallery_service=gallery_service,
    )


@cms_ai_bp.route("/generate", methods=["POST"])
@require_auth
@require_admin
@require_permission(MANAGE_PERMISSION)
def generate():
    """POST /api/v1/plugins/cms-ai/generate -> {patch, provider, model}."""
    config, error = _check_enabled()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    action = data.get("action", "freeform")
    prompt = data.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "Missing 'prompt' field"}), 400

    read_excerpt = bool(data.get("read_excerpt", False))
    context = data.get("context") or {}
    if not isinstance(context, dict):
        return jsonify({"error": "'context' must be an object"}), 400

    service = _build_generate_service(config)
    try:
        result = service.generate(
            action=action,
            prompt=prompt,
            read_excerpt=read_excerpt,
            context=context,
        )
    except _service_module().CmsAiGenerateError as generation_error:
        # exc_info logs the chained cause (the underlying LoopForgeError) to the
        # server log; the response body stays a safe, key-free message.
        logger.error("cms-ai generation failed: %s", generation_error, exc_info=True)
        return jsonify({"error": "AI generation failed"}), 500

    return jsonify(result), 200


@cms_ai_bp.route("/generate-image", methods=["POST"])
@require_auth
@require_admin
@require_permission(MANAGE_PERMISSION)
def generate_image():
    """POST /api/v1/plugins/cms-ai/generate-image -> {patch, image}.

    Generates an image from the prompt (Replicate / Black Forest FLUX), uploads
    it to the global CMS image gallery immediately, and returns a patch that
    appends the gallery ``<img>`` to the current ``content_html``. The Replicate
    token is server-side only — it never appears in the response or error body.
    """
    config, error = _check_enabled()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "Missing 'prompt' field"}), 400

    context = data.get("context") or {}
    if not isinstance(context, dict):
        return jsonify({"error": "'context' must be an object"}), 400
    content_html = context.get("content_html", "")

    service = _build_image_service(config)
    try:
        result = service.generate(prompt=prompt, content_html=content_html)
    except _image_service_module().CmsAiImageError as image_error:
        logger.error("cms-ai image generation failed: %s", image_error, exc_info=True)
        return jsonify({"error": "Image generation failed"}), 400

    return jsonify(result), 200
