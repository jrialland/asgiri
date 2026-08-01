"""Tests for hot-reload helpers."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from asgiri.reload import (
    DEFAULT_RELOAD_IGNORE_PATTERNS,
    Reloader,
    is_ignored,
    resolve_reload_dirs,
)


class TestIsIgnored:
    def test_ignores_pyc(self):
        root = Path("/project")
        assert is_ignored(Path("/project/foo.pyc"), [root])

    def test_ignores_pycache_dir(self):
        root = Path("/project")
        assert is_ignored(
            Path("/project/__pycache__/__init__.cpython-312.pyc"), [root]
        )

    def test_ignores_hidden_file(self):
        root = Path("/project")
        assert is_ignored(Path("/project/.env"), [root])

    def test_does_not_ignore_py_file(self):
        root = Path("/project")
        assert not is_ignored(Path("/project/main.py"), [root])

    def test_extra_patterns(self):
        root = Path("/project")
        assert is_ignored(
            Path("/project/tmp.log"), [root], extra_patterns=["*.log"]
        )

    def test_ignores_site_packages(self):
        root = Path("/project")
        assert is_ignored(Path("/some/where/site-packages/foo.py"), [root])


class TestResolveReloadDirs:
    def test_uses_provided_dirs(self):
        dirs = resolve_reload_dirs("tests.app:app", ["src"])
        assert dirs == [Path("src").resolve()]

    def test_resolves_from_app_module(self):
        dirs = resolve_reload_dirs("tests.app:app")
        assert any(str(d).endswith("tests") for d in dirs)

    def test_falls_back_to_cwd(self):
        # Use a module that does not exist to force fallback
        dirs = resolve_reload_dirs("nonexistent_xyz.module:app")
        assert dirs == [Path.cwd()]


class TestDefaultPatterns:
    def test_default_patterns_exist(self):
        assert "*.pyc" in DEFAULT_RELOAD_IGNORE_PATTERNS
        assert "__pycache__/" in DEFAULT_RELOAD_IGNORE_PATTERNS
        assert ".git/" in DEFAULT_RELOAD_IGNORE_PATTERNS


class TestReloader:
    def test_reloader_sends_reload_and_restarts_child(self):
        reloader = Reloader(
            watch_dirs=[Path("/fake")],
            debounce_ms=50,
            max_restarts=3,
        )

        parent_conn = MagicMock()
        child_conn = MagicMock()

        class FakeProcess:
            def __init__(self):
                self.alive = True
                self.exitcode = 0

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                pass

            def terminate(self):
                self.alive = False

            def kill(self):
                self.alive = False

            def start(self):
                self.alive = True

        first = FakeProcess()
        second = FakeProcess()
        process_iter = iter([first, second])

        def fake_process(*args, **kwargs):
            return next(process_iter)

        yielded = False

        def fake_watch(*args, **kwargs):
            nonlocal yielded
            if not yielded:
                yielded = True
                yield {(1, "/fake/app.py")}

        with patch(
            "multiprocessing.Pipe", return_value=(parent_conn, child_conn)
        ):
            with patch("multiprocessing.Process", side_effect=fake_process):
                with patch("asgiri.reload.watch", fake_watch):

                    def shutdown_after_reload():
                        # Wait until the reloader has sent the reload command.
                        deadline = time.time() + 2
                        while time.time() < deadline:
                            if parent_conn.send.call_count >= 1:
                                reloader._shutdown_requested.set()
                                return
                            time.sleep(0.05)

                    threading.Thread(
                        target=shutdown_after_reload, daemon=True
                    ).start()
                    result = reloader.run(lambda c: 0)

        assert result == 0
        assert parent_conn.send.call_count == 2
        assert parent_conn.send.call_args_list[0] == (("reload",),)
        assert parent_conn.send.call_args_list[1] == (("shutdown",),)
        assert not first.is_alive()
