"""The headline extractability guarantee: LoopForge is import-clean.

LoopForge must ship with **no** Flask, **no** SQLAlchemy, **no** thread-local
session, **no** ``vbwd`` core import and **no** ``plugins.*`` import — that is
what makes it extractable later as a pip dependency or git submodule. This
oracle proves it two ways: an AST scan of every LoopForge source file for
banned import statements, and a runtime check that importing the package does
not pull the banned top-level modules into ``sys.modules``.
"""

import ast
import importlib
import os
import sys

import loopforge

_PACKAGE_DIR = os.path.dirname(os.path.abspath(loopforge.__file__))

# Top-level module names LoopForge must never import (directly or transitively
# at import time). ``session`` and ``sqlalchemy`` cover the loopai coupling we
# deliberately left behind.
_BANNED_TOP_LEVEL_MODULES = {
    "flask",
    "sqlalchemy",
    "vbwd",
    "plugins",
}
# Substrings that, if they appear as an imported module root, signal a leak.
_BANNED_NAME_FRAGMENTS = (
    "session_manager",
    "session_dependent",
    "app_global_config",
)


def _loopforge_source_files():
    for root, _dirs, files in os.walk(_PACKAGE_DIR):
        if os.path.basename(root) == "tests":
            continue
        for file_name in files:
            if file_name.endswith(".py"):
                yield os.path.join(root, file_name)


def _imported_module_roots(source_path):
    with open(source_path, "r", encoding="utf-8") as source_file:
        tree = ast.parse(source_file.read(), filename=source_path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import within the package — allowed
            if node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def test_no_banned_imports_in_any_source_file():
    offenders = {}
    for source_path in _loopforge_source_files():
        roots = _imported_module_roots(source_path)
        banned = roots & _BANNED_TOP_LEVEL_MODULES
        fragment_hits = {
            root
            for root in roots
            for fragment in _BANNED_NAME_FRAGMENTS
            if fragment in root
        }
        if banned or fragment_hits:
            offenders[source_path] = sorted(banned | fragment_hits)
    assert offenders == {}, f"LoopForge has banned imports: {offenders}"


def test_importing_package_does_not_pull_banned_modules():
    # Another test suite (or the repo-root conftest) may already have imported
    # Flask/SQLAlchemy/vbwd. We assert that *LoopForge's own import* does not
    # add any banned module that wasn't already present — so snapshot first.
    already_present = {
        name for name in _BANNED_TOP_LEVEL_MODULES if name in sys.modules
    }

    for module_name in list(sys.modules):
        if module_name == "loopforge" or module_name.startswith("loopforge."):
            del sys.modules[module_name]

    importlib.import_module("loopforge")

    newly_pulled = {
        name
        for name in _BANNED_TOP_LEVEL_MODULES
        if name in sys.modules and name not in already_present
    }
    assert (
        newly_pulled == set()
    ), f"Importing LoopForge pulled banned module(s): {sorted(newly_pulled)}"
