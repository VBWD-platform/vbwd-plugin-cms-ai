"""Test harness for cms-ai gate-visible unit tests (Sprint 58.6).

The cms-ai source lives under the ``loopai`` import namespace
(``plugins/cms-ai/cms-ai/loopai/``) and depends on a couple of modules that
only exist inside the loopai web container (``app_global_config`` and
``web.services.socket_io_service``). To exercise the migrated ``LoggerService``
from the vbwd ``--plugin cms-ai`` gate we put the loopai root on ``sys.path``
and stub those two host-only modules. Nothing here touches real files: the
LoggerService now routes through the unified ``logging`` layer, which the
TESTING-guarded boot path leaves un-attached during pytest.
"""
import os
import sys
import types

LOOPAI_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "cms-ai", "loopai")
)
if LOOPAI_ROOT not in sys.path:
    sys.path.insert(0, LOOPAI_ROOT)

# The loopai source (which provides the ``core`` namespace these tests import)
# is vendored separately and gitignored, so it is ABSENT in CI / fresh clones.
# Skip the loopai-dependent specs when it is missing instead of failing
# collection with "No module named 'core'"; they still run in local dev where
# loopai is vendored in.
if not os.path.isdir(os.path.join(LOOPAI_ROOT, "core")):
    collect_ignore = [
        "test_logger_service_unified.py",
        "test_file_adapter_no_hardcoded_log.py",
    ]


def _install_host_module_stubs() -> None:
    """Provide the two loopai-container-only modules LoggerService imports."""
    if "app_global_config" not in sys.modules:
        config_stub = types.ModuleType("app_global_config")
        config_stub.WEB_OR_CLI = "cli"
        sys.modules["app_global_config"] = config_stub

    web_module = sys.modules.get("web")
    if web_module is None or not getattr(web_module, "__path__", None):
        # Make ``web`` a *package* so submodule stubs resolve cleanly.
        web_module = types.ModuleType("web")
        web_module.__path__ = []  # marks it as a package
        sys.modules["web"] = web_module

    if "web.services.socket_io_service" not in sys.modules:
        services_module = types.ModuleType("web.services")
        socket_io_module = types.ModuleType("web.services.socket_io_service")
        socket_io_module.send_message_to_user = lambda *args, **kwargs: None
        web_module.services = services_module
        services_module.socket_io_service = socket_io_module
        sys.modules["web.services"] = services_module
        sys.modules["web.services.socket_io_service"] = socket_io_module

    if "web.admin_models" not in sys.modules:
        admin_models_module = types.ModuleType("web.admin_models")

        class _LoopStub:  # minimal stand-in for the loopai ORM model
            pass

        admin_models_module.Loop = _LoopStub
        web_module.admin_models = admin_models_module
        sys.modules["web.admin_models"] = admin_models_module


_install_host_module_stubs()
