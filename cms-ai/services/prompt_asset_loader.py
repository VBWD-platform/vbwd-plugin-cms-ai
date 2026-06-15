"""Resolve the LoopForge prompt triple (flow + template + vars) for an action.

The triple lives on the unified core filesystem under
``${VBWD_VAR_DIR}/assets/cms-ai/prompts/`` (admin-editable, host-mounted) and
falls back to the copies shipped inside the plugin at
``plugins/cms-ai/templates/prompts/``. A per-instance file under the var dir
ALWAYS wins over the shipped default, so prompts/flows/field-manifests are
tunable without a code change.

The loader resolves a named ``action`` (article / seo / restyle / freeform) to
its flow file, then reads the template and vars-manifest the flow references.
An unknown action falls back to the default flow shipped as
``loopforge-flow-1.yaml``. The loader reads plain data only; it builds no
LoopForge objects (the service does that) and touches no network or DB.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import yaml

from vbwd.services.asset_storage import asset_dir

ASSET_OWNER = "cms-ai"
PROMPTS_SUBDIR = "prompts"
DEFAULT_FLOW_FILE = "loopforge-flow-1.yaml"

# The plugin-shipped defaults live next to this package, under
# ``plugins/cms-ai/templates/prompts/``.
_SHIPPED_PROMPTS_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "templates",
        "prompts",
    )
)


@dataclass(frozen=True)
class PromptTriple:
    """The resolved data for one action: flow + step template + field manifest."""

    flow: Dict[str, Any]
    template: Dict[str, Any]
    manifest: Dict[str, Any]


class PromptAssetLoader:
    """Loads the prompt triple for an action, var-dir override winning."""

    def __init__(self, prompts_dir_override: str = "") -> None:
        # An explicit override (plugin config ``prompts_dir``) replaces the
        # default var-dir location; otherwise the unified asset dir is used.
        self._var_prompts_dir = prompts_dir_override or asset_dir(
            ASSET_OWNER, PROMPTS_SUBDIR
        )

    def load(self, action: str) -> PromptTriple:
        """Resolve the flow/template/manifest triple for ``action``."""
        flow_file = self._flow_file_for_action(action)
        flow = self._read_yaml(flow_file) or self._read_yaml(DEFAULT_FLOW_FILE) or {}
        flow_body = flow.get("flow", flow)
        steps = flow_body.get("steps") or []
        first_step = steps[0] if steps else {}

        template_file = first_step.get("template", "template-1.json")
        manifest_file = first_step.get("vars", "template-1-vars.json")

        template = self._read_json(template_file) or {}
        manifest = self._read_json(manifest_file) or {}
        return PromptTriple(flow=flow_body, template=template, manifest=manifest)

    def _flow_file_for_action(self, action: str) -> str:
        """The flow filename for ``action`` (``loopforge-flow-<action>.yaml``)."""
        safe_action = (action or "").strip().lower()
        if not safe_action:
            return DEFAULT_FLOW_FILE
        return f"loopforge-flow-{safe_action}.yaml"

    def _resolve_path(self, file_name: str) -> Optional[str]:
        """Return the override path if it exists, else the shipped default path."""
        override_path = os.path.join(self._var_prompts_dir, file_name)
        if os.path.isfile(override_path):
            return override_path
        shipped_path = os.path.join(_SHIPPED_PROMPTS_DIR, file_name)
        if os.path.isfile(shipped_path):
            return shipped_path
        return None

    def _read_json(self, file_name: str) -> Optional[Dict[str, Any]]:
        path = self._resolve_path(file_name)
        if path is None:
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _read_yaml(self, file_name: str) -> Optional[Dict[str, Any]]:
        path = self._resolve_path(file_name)
        if path is None:
            return None
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)
