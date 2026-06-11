"""Sprint 58.6 — cms-ai's ``LoggerService`` rides the unified log router.

These are the oracle tests for the migration. cms-ai's modules live under the
``loopai.*`` import namespace, *not* ``plugins.cms-ai.*``, so a naive
``getLogger(__name__)`` would scope every cms-ai line to ``core``. The contract
here is:

  * ``LoggerService`` obtains a stdlib logger whose name starts
    ``plugins.cms-ai`` so the unified router derives scope ``cms-ai``;
  * it attaches **no** bespoke file handler (no ``RotatingFileHandler`` /
    ``FileHandler``) and writes no stray relative ``core.log``;
  * the old file-path machinery (``get_log_file`` / ``ensure_log_directory`` /
    ``LOOPAI_LOG_PATH`` …) is gone — the unified router owns files + rotation;
  * driving a real :class:`VbwdLogRouter` with the service's logger name routes
    an ERROR to ``logs/cms-ai/error.log``, never ``logs/core/error.log``.
"""
import json
import logging
from logging.handlers import RotatingFileHandler

import pytest

from vbwd.services.filesystem import LocalFilesystemManager
from vbwd.services.logging import VbwdLogRouter
from vbwd.services.logging.router import derive_scope

from core.services.logger_service import LoggerService, get_logger_service


@pytest.fixture
def local_manager(tmp_path, monkeypatch):
    var_root = tmp_path / "var"
    uploads_root = tmp_path / "uploads"
    var_root.mkdir()
    uploads_root.mkdir()
    monkeypatch.setenv("VBWD_VAR_DIR", str(var_root))
    monkeypatch.setenv("UPLOADS_BASE_PATH", str(uploads_root))
    monkeypatch.setenv("UPLOADS_BASE_URL", "/uploads")
    return LocalFilesystemManager(), var_root


def test_logger_name_scopes_to_cms_ai():
    """The service's underlying logger must derive scope ``cms-ai``."""
    service = get_logger_service("core")

    logger_name = service.get_logger_name()

    assert logger_name.startswith("plugins.cms-ai")
    assert derive_scope(logger_name) == "cms-ai"


def test_component_loggers_all_scope_to_cms_ai():
    for component in ("core", "web", "system"):
        service = LoggerService(component=component)
        assert derive_scope(service.get_logger_name()) == "cms-ai"


def test_no_bespoke_file_handler_attached():
    """No RotatingFileHandler/FileHandler — the unified router owns files."""
    service = LoggerService(component="core")
    underlying = logging.getLogger(service.get_logger_name())

    file_handlers = [
        handler
        for handler in underlying.handlers
        if isinstance(handler, (RotatingFileHandler, logging.FileHandler))
    ]

    assert file_handlers == []


def test_no_relative_core_log_written(tmp_path, monkeypatch):
    """Logging must not drop a stray cwd-relative ``core.log``."""
    monkeypatch.chdir(tmp_path)

    service = LoggerService(component="core")
    service.info("hello from cms-ai")
    service.error("boom from cms-ai")

    assert not (tmp_path / "core.log").exists()


def test_old_file_path_machinery_removed():
    """The bespoke file-path/env config surface is retired."""
    service = LoggerService(component="core")
    for removed in (
        "get_log_file",
        "get_log_path",
        "ensure_log_directory",
        "_parse_file_size",
    ):
        assert not hasattr(service, removed), f"{removed} should be gone"


def test_public_api_still_works():
    """Delegation keeps the call-site facing API intact."""
    service = LoggerService(component="core")
    service.info("info line")
    service.warning("warning line")
    service.debug("debug line")
    service.error("error line")

    recent = service.get_logs()
    assert any("error line" in entry for entry in recent)


def test_error_routes_to_cms_ai_scope_not_core(local_manager):
    """An ERROR on the service's logger lands in logs/cms-ai/error.log."""
    manager, var_root = local_manager
    router = VbwdLogRouter(manager)

    logger_name = LoggerService(component="core").get_logger_name()
    record = logging.LogRecord(
        name=logger_name,
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="cms-ai failure",
        args=(),
        exc_info=None,
    )
    router.emit(record)

    cms_ai_error = var_root / "logs" / "cms-ai" / "error.log"
    core_error = var_root / "logs" / "core" / "error.log"
    assert cms_ai_error.exists()
    assert not core_error.exists()

    line = json.loads(cms_ai_error.read_text().strip())
    assert line["scope"] == "cms-ai"
    assert line["logger"].startswith("plugins.cms-ai")
