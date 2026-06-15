"""Unit tests for the cms-ai PromptAssetLoader.

Engineering requirements (binding, restated): TDD-first (these land RED before
the loader); DevOps-first (pure tmp-dir fixtures, no DB/network); SOLID/DI/DRY
(the loader has one job — resolve the triple, var-dir override winning); Liskov
(a missing override falls back to the shipped default, same return shape); clean
code; no overengineering. Quality guard: ``bin/pre-commit-check.sh --plugin
cms-ai --full``.
"""

import json
import os
from importlib import import_module

loader_module = import_module("plugins.cms-ai.cms-ai.services.prompt_asset_loader")
PromptAssetLoader = loader_module.PromptAssetLoader


def test_loads_shipped_default_article_triple():
    loader = PromptAssetLoader()

    triple = loader.load("article")

    assert triple.template.get("system_content")
    assert "title" in triple.manifest["request_context"]
    assert "content_html" in triple.manifest["response_fields"]


def test_each_shipped_action_has_its_own_response_fields():
    loader = PromptAssetLoader()

    seo_fields = loader.load("seo").manifest["response_fields"]
    restyle_fields = loader.load("restyle").manifest["response_fields"]
    freeform_fields = loader.load("freeform").manifest["response_fields"]

    # seo -> SEO meta only; restyle -> source_css; freeform -> the full set.
    assert "meta_title" in seo_fields and "content_html" not in seo_fields
    assert "source_css" in restyle_fields
    assert {"content_html", "source_css", "schema_json"} <= set(freeform_fields)


def test_var_dir_override_wins_over_shipped_default(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    # Override the article flow + its referenced template/vars.
    (prompts_dir / "loopforge-flow-article.yaml").write_text(
        "flow:\n"
        "  name: article\n"
        "  steps:\n"
        "    - template: custom-template.json\n"
        "      vars: custom-vars.json\n",
        encoding="utf-8",
    )
    (prompts_dir / "custom-template.json").write_text(
        json.dumps(
            {"system_content": "OVERRIDDEN SYSTEM", "prompt": "{{ user_prompt }}"}
        ),
        encoding="utf-8",
    )
    (prompts_dir / "custom-vars.json").write_text(
        json.dumps(
            {
                "request_context": ["title"],
                "response_fields": {"content_html": {"type": "string"}},
            }
        ),
        encoding="utf-8",
    )

    loader = PromptAssetLoader(prompts_dir_override=str(prompts_dir))
    triple = loader.load("article")

    assert triple.template["system_content"] == "OVERRIDDEN SYSTEM"
    assert triple.manifest["request_context"] == ["title"]


def test_unknown_action_falls_back_to_default_flow():
    loader = PromptAssetLoader()

    triple = loader.load("does-not-exist")

    # Falls back to the shipped default flow (article), never raising.
    assert triple.manifest.get("response_fields")
    assert os.path.exists(loader_module._SHIPPED_PROMPTS_DIR)
