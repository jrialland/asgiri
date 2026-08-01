"""Hot-reload support helpers and supervisor."""

import fnmatch
import importlib
import multiprocessing
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from watchfiles import watch
except ImportError:  # pragma: no cover
    watch = None  # type: ignore[misc,assignment]

DEFAULT_RELOAD_IGNORE_PATTERNS = (
    "*.pyc",
    "__pycache__/",
    ".git/",
    ".venv/",
    "venv/",
    ".env/",
    "node_modules/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    "*.egg-info/",
    ".tox/",
    ".coverage",
    "*.pyo",
)


def _relative_to_root(path: Path, roots: Sequence[Path]) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return path.name


def is_ignored(
    path: Path,
    watch_roots: Sequence[Path],
    extra_patterns: Sequence[str] | None = None,
) -> bool:
    """Return True if a path should be ignored during reload watching."""
    patterns = list(DEFAULT_RELOAD_IGNORE_PATTERNS)
    if extra_patterns:
        patterns.extend(extra_patterns)

    # Never watch files inside installed site-packages / dist-packages.
    abs_path = path.resolve()
    parts = abs_path.parts
    if "site-packages" in parts or "dist-packages" in parts:
        return True

    # Hidden files / directories anywhere in the path.
    for part in parts:
        if part.startswith(".") and part not in {".", ".."}:
            return True

    rel = _relative_to_root(abs_path, watch_roots)

    for pattern in patterns:
        # Directory pattern with trailing slash.
        if pattern.endswith("/"):
            dir_name = pattern[:-1]
            if dir_name in parts:
                return True
        # Match against full relative path and base name.
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
            abs_path.name, pattern
        ):
            return True

    return False


def _module_path_from_app_spec(app_spec: str) -> Path | None:
    """Import the module named in app_spec and return its file path."""
    if ":" not in app_spec:
        return None
    module_path_str = app_spec.split(":", 1)[0]
    if not module_path_str:
        return None
    try:
        module = importlib.import_module(module_path_str)
        if hasattr(module, "__file__") and module.__file__:
            return Path(module.__file__).resolve().parent
    except Exception:
        pass
    return None


def resolve_reload_dirs(
    app_spec: str,
    provided_dirs: Sequence[str | Path] | None = None,
) -> list[Path]:
    """Return the list of directories to watch for reload."""
    if provided_dirs:
        return [Path(d).resolve() for d in provided_dirs]

    module_dir = _module_path_from_app_spec(app_spec)
    if module_dir is not None:
        return [module_dir]

    return [Path.cwd()]


class Reloader:
    """Supervises a child process and restarts it when source files change."""

    def __init__(
        self,
        watch_dirs: Sequence[Path],
        debounce_ms: float | int = 200,
        ignore_patterns: Sequence[str] | None = None,
        max_restarts: int = 3,
        shutdown_timeout: float = 30.0,
    ):
        self.watch_dirs = [d.resolve() for d in watch_dirs]
        self.debounce_ms = debounce_ms
        self.ignore_patterns = list(ignore_patterns or [])
        self.max_restarts = max_restarts
        self.shutdown_timeout = shutdown_timeout
        self._shutdown_requested = threading.Event()

    def _start_child(
        self,
        target: Callable[..., Any],
        target_args: tuple[Any, ...],
        child_conn,
    ) -> multiprocessing.Process:
        process = multiprocessing.Process(
            target=target,
            args=target_args + (child_conn,),
            name="asgiri-reload-child",
            daemon=False,
        )
        process.start()
        return process

    def _watcher(self, reload_event: multiprocessing.Event) -> None:
        """Run in a thread; signal reload_event when changes are detected."""
        if watch is None:
            logger.error(
                "Hot reload requires the 'watchfiles' package. "
                "Install it with: pip install asgiri[reload]"
            )
            return

        dirs = [str(d) for d in self.watch_dirs if d.exists()]
        if not dirs:
            logger.warning(
                "No reload directories exist, watching current directory"
            )
            dirs = [str(Path.cwd())]

        try:
            for changes in watch(
                *dirs,
                debounce=self.debounce_ms,
                stop_event=self._shutdown_requested,
            ):
                for _, raw_path in changes:
                    path = Path(raw_path).resolve()
                    if is_ignored(path, self.watch_dirs, self.ignore_patterns):
                        continue
                    logger.info(f"Detected file change: {path}")
                    reload_event.set()
                    return
        except Exception as e:
            logger.error(f"Watcher failed: {e}")

    def _send(self, conn, message: str) -> None:
        try:
            conn.send(message)
        except Exception as e:
            logger.warning(f"Failed to send '{message}' to child: {e}")

    def _stop_child(self, process: multiprocessing.Process, conn) -> None:
        self._send(conn, "shutdown")
        process.join(timeout=self.shutdown_timeout)
        if process.is_alive():
            logger.warning("Child did not exit gracefully, terminating")
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                logger.warning("Child did not terminate, killing")
                process.kill()
                process.join()

    def run(
        self, target: Callable[..., Any], target_args: tuple[Any, ...] = ()
    ) -> int:
        """Run the supervisor loop. Returns the final exit code."""
        logger.info("Starting hot-reload supervisor")

        parent_conn, child_conn = multiprocessing.Pipe()
        process = self._start_child(target, target_args, child_conn)

        reload_event = multiprocessing.Event()
        watcher_thread = threading.Thread(
            target=self._watcher,
            args=(reload_event,),
            daemon=True,
        )
        watcher_thread.start()

        consecutive_failures = 0

        def handle_signal(signum, _frame):
            logger.info(f"Received signal {signum}, shutting down reloader")
            self._shutdown_requested.set()
            self._stop_child(process, parent_conn)
            sys.exit(0)

        # Replace signal handlers only in the parent process.
        old_sigint = signal.signal(signal.SIGINT, handle_signal)
        old_sigterm = signal.signal(signal.SIGTERM, handle_signal)
        old_sigbreak: Any = None
        if hasattr(signal, "SIGBREAK"):
            old_sigbreak = signal.signal(signal.SIGBREAK, handle_signal)

        try:
            while True:
                # Poll child exit / reload event.
                process.join(timeout=0.5)

                if not process.is_alive():
                    exit_code = process.exitcode
                    logger.info(f"Child process exited with code {exit_code}")
                    if exit_code == 0:
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        logger.warning(
                            f"Child failure {consecutive_failures}/"
                            f"{self.max_restarts}"
                        )
                        if consecutive_failures >= self.max_restarts:
                            logger.error(
                                f"Giving up after {consecutive_failures} "
                                f"consecutive failures (exit code {exit_code})"
                            )
                            return exit_code

                    if self._shutdown_requested.is_set():
                        return exit_code

                    # Start a new child and watcher.
                    parent_conn, child_conn = multiprocessing.Pipe()
                    process = self._start_child(target, target_args, child_conn)
                    reload_event.clear()
                    watcher_thread = threading.Thread(
                        target=self._watcher,
                        args=(reload_event,),
                        daemon=True,
                    )
                    watcher_thread.start()
                    continue

                if self._shutdown_requested.is_set():
                    logger.info("Shutdown requested, stopping child process")
                    self._stop_child(process, parent_conn)
                    return 0

                if reload_event.is_set():
                    logger.info("Reload requested, restarting child process")
                    self._send(parent_conn, "reload")
                    process.join(timeout=self.shutdown_timeout)
                    if process.is_alive():
                        logger.warning(
                            "Child did not exit after reload request, "
                            "terminating"
                        )
                        process.terminate()
                        process.join(timeout=5)
                        if process.is_alive():
                            process.kill()
                            process.join()
                    reload_event.clear()
                    # Loop will restart child below.
                    continue

        except KeyboardInterrupt:
            handle_signal(signal.SIGINT, None)
        finally:
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)
            if old_sigbreak is not None:
                signal.signal(signal.SIGBREAK, old_sigbreak)
            if process.is_alive():
                self._stop_child(process, parent_conn)

        return 0
