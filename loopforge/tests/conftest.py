"""Pytest harness for the LoopForge engine tests.

LoopForge lives at ``plugins/cms-ai/loopforge`` but ``cms-ai`` is not a valid
Python package name (the hyphen), so the package cannot be reached as
``plugins.cms-ai.loopforge``. We put the ``plugins/cms-ai`` directory on
``sys.path`` so the tests import the engine as a top-level ``loopforge``
package — exactly how it will be imported once extracted to its own
distribution. Nothing here touches the network or the database.
"""

import os
import sys

_CMS_AI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _CMS_AI_ROOT not in sys.path:
    sys.path.insert(0, _CMS_AI_ROOT)
