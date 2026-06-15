"""Source package for the cms-ai plugin (text-generate path).

The directory is named after the plugin id (``cms-ai``); the hyphen means it
cannot be reached with a plain ``import plugins.cms-ai.cms-ai`` statement, so
the plugin imports its submodules with :func:`importlib.import_module`. The
LoopForge engine (``plugins/cms-ai/loopforge``) is imported as the top-level
``loopforge`` package; importing this package places the plugin root on
``sys.path`` so ``from loopforge import ...`` resolves in the service modules —
exactly how it will be imported once LoopForge is extracted to its own
distribution.
"""

import os
import sys

_PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)
