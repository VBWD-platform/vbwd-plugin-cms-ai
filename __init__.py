"""cms-ai plugin — LoopForge-driven CMS content & SEO generation.

The plugin exposes an admin-only editor proxy: an operator's prompt plus the
page's own context (title/excerpt/content/CSS) is turned into a validated patch
of CMS fields by the configured LLM (OpenAI- or Anthropic-protocol, chosen by
model name), driven through the import-clean LoopForge engine. Generation is
stateless — the route returns a patch; nothing is persisted server-side.

The plugin class MUST be defined here (not re-exported): the plugin manager's
discovery skips classes whose ``__module__`` differs from the package module.
"""

from importlib import import_module
from typing import Any, Dict, Optional, TYPE_CHECKING

from vbwd.plugins.base import BasePlugin, PluginMetadata

if TYPE_CHECKING:
    from flask import Blueprint


DEFAULT_CONFIG: Dict[str, Any] = {
    "debug_mode": False,
    # --- LLM (text generation) ---
    # The model/endpoint/key now live in a CORE "LLM Connection" (S97). cms-ai
    # keeps only the optional slug of the connection to use; empty ⇒ the active
    # default connection.
    "llm_connection_slug": "",
    "json_retry_max": 3,  # LoopForge JSON-repair loop
    "prompts_dir": "",  # optional override of the asset-storage prompts dir
    # --- image generation (Replicate / Black Forest FLUX) — Slice 2 ---
    # Drives POST /generate-image: prompt -> gallery image -> content_html <img>.
    "image_enabled": True,
    "replicate_api_token": "",
    "image_model": "black-forest-labs/flux-schnell",
    "image_width": 1024,
    "image_height": 1024,
}


class CmsAiPlugin(BasePlugin):
    """Admin editor AI helper: prompt + page context -> validated field patch."""

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="cms-ai",
            version="26.6.1",
            author="VBWD Team",
            description="LoopForge-driven CMS content & SEO generation",
            # Plugin-to-plugin deps: the image path reuses the cms plugin's
            # global image gallery (CmsImageService) — a declared hard dep so
            # the manager resolves enable order. The custom-field path also has
            # a soft dependency on S77, resolved from the container at runtime
            # and degrading to core fields when absent (so it is not declared
            # here as a load-order requirement).
            dependencies=["cms"],
        )

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> None:
        merged: Dict[str, Any] = {**DEFAULT_CONFIG}
        if config:
            merged.update(config)
        super().initialize(merged)

    def get_blueprint(self) -> Optional["Blueprint"]:
        routes_module = import_module("plugins.cms-ai.cms-ai.routes")
        return routes_module.cms_ai_bp

    def get_url_prefix(self) -> Optional[str]:
        return "/api/v1/plugins/cms-ai"

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass
