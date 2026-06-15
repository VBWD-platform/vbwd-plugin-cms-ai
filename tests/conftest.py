"""Gate bootstrap for the cms-ai suite — ensure the plugin's runtime deps.

The cms-ai plugin ships its own ``requirements.txt`` (the LoopForge provider
SDKs ``openai`` / ``anthropic`` / ``replicate`` plus ``PyYAML`` for the flow
files). These are PLUGIN-LOCAL deps, not part of the core image, so a cold
``bin/pre-commit-check.sh --plugin cms-ai`` container has them absent and the
LoopForge + service imports would fail at collection.

Rather than edit the core ``bin/`` script or the shared test image, this
conftest (collected for both the unit and integration suites because pytest
walks up to it from every cms-ai test) installs the plugin requirements once,
in-process, when any of them is missing. When they are already present (local
dev where the image was pre-seeded) it is a no-op. Nothing here touches the
network beyond the pip install, and that only runs when a dep is genuinely
absent.
"""

import importlib.util
import os
import subprocess
import sys

# Module name -> import check. ``yaml`` is the import name for PyYAML.
_REQUIRED_IMPORTS = ("openai", "anthropic", "replicate", "yaml")
_REQUIREMENTS_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "requirements.txt")
)


def _missing_any() -> bool:
    return any(importlib.util.find_spec(name) is None for name in _REQUIRED_IMPORTS)


def _ensure_plugin_requirements() -> None:
    if not _missing_any():
        return
    if not os.path.isfile(_REQUIREMENTS_FILE):
        return
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", "-r", _REQUIREMENTS_FILE]
    )


_ensure_plugin_requirements()
