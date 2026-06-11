"""Sprint 58.6 — the file adapter's hardcoded dev log sink is gone.

``write_to_fs_log`` used to ``open('/app/var/tmp/log-dev/fs_out.log', 'a')`` and
append directly, bypassing the unified router (no scope routing, no redaction,
no rotation) and creating a stray hardcoded path. After 58.6 it must not open
that file at all — diagnostics flow through the unified ``logging`` layer.
"""
import builtins
import inspect


def test_write_to_fs_log_does_not_open_hardcoded_path():
    """A call must not touch the hardcoded /app/var/tmp/log-dev sink."""
    from core.adapters.file_adapter_interface import write_to_fs_log

    opened_paths = []
    real_open = builtins.open

    def tracking_open(path, *args, **kwargs):
        opened_paths.append(str(path))
        return real_open(path, *args, **kwargs)

    builtins.open = tracking_open
    try:
        write_to_fs_log({"any": "payload"})
    finally:
        builtins.open = real_open

    assert not any("log-dev" in path for path in opened_paths)
    assert not any("fs_out.log" in path for path in opened_paths)


def test_module_source_has_no_hardcoded_log_path():
    """Belt-and-braces: the hardcoded path string is gone from the source."""
    from core.adapters import file_adapter_interface

    source = inspect.getsource(file_adapter_interface)
    assert "/app/var/tmp/log-dev" not in source
    assert "fs_out.log" not in source
