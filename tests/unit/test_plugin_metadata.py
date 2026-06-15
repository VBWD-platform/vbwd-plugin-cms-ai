"""Unit test: the cms-ai plugin declares its plugin-to-plugin dependency.

The image-generation path (Slice 2) reuses the cms plugin's global image
gallery via ``CmsImageService``. Per the core⇄plugin separation rule a
plugin→plugin dependency is fine but MUST be declared in
``PluginMetadata.dependencies`` so the manager resolves enable order. This test
locks that the ``cms`` dependency is declared.
"""

from importlib import import_module

plugin_module = import_module("plugins.cms-ai.__init__")


def test_cms_ai_declares_cms_dependency():
    plugin = plugin_module.CmsAiPlugin()
    assert "cms" in plugin.metadata.dependencies
