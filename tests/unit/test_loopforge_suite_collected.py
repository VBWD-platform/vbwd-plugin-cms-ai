"""Run the LoopForge engine suite under the cms-ai gate.

The LoopForge tests live at ``plugins/cms-ai/loopforge/tests`` so that the
engine stays import-clean and extractable. The repo gate's ``--plugin cms-ai``
flow only collects ``plugins/cms-ai/tests/unit``, so this thin collector runs
the LoopForge suite in-process (without editing the core ``bin/`` script) and
fails loudly if any LoopForge test regresses. The two suites therefore go green
together under one ``bin/pre-commit-check.sh --plugin cms-ai`` invocation.
"""

import os

import pytest

_LOOPFORGE_TESTS_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "loopforge",
        "tests",
    )
)


def test_loopforge_engine_suite_passes():
    exit_code = pytest.main(["-q", _LOOPFORGE_TESTS_DIR])
    assert exit_code == 0, f"LoopForge suite failed (pytest exit {exit_code})"
