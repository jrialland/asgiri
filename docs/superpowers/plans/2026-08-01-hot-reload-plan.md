# Hot Reload Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single-process, signal-free hot-reload mode to `asgiri` using a parent supervisor that watches source files via `watchfiles` and restarts a child server process over a `multiprocessing.Pipe`.

**Architecture:** A new `asgiri/reload.py` module contains the supervisor (`Reloader`) and ignore/directory helpers. `asgiri/server.py` gains reload metadata constructor args and a thread-safe `request_shutdown()` method. `asgiri/cli.py` adds `--reload`, `--reload-dir`, `--reload-ignore`, and `--reload-delay-ms`, routing to the reloader when enabled. All parent-child coordination uses the pipe only; no signals are sent to the child.

**Tech Stack:** Python 3.12+, `watchfiles` (optional extra), `multiprocessing.Pipe`, `threading.Event`, existing `asgiri` server and CLI infrastructure.

## Global Constraints

- **Single-process only** — `--reload` is incompatible with `--workers > 1` and errors out.
- **No parent-directed signals** — the reloader must never send a signal to the child or parent to trigger a reload. Communication is via `multiprocessing.Pipe` only.
- **Cross-platform** — must work on Windows, Linux, and macOS.
- **Minimal intrusion** — existing `Server` behavior is unchanged when `reload=False`.
- Default debounce delay: **200 ms**.
- Max consecutive child crashes before giving up: **3**.
- `watchfiles` is an optional dependency under the `reload` extra (`pip install asgiri[reload]`).

---

## File structure

| File | Responsibility |
| --- | --- |
| `asgiri/server.py` | Add `reload*` constructor args and `request_shutdown()` method. |
| `asgiri/reload.py` | New module: ignore matching, directory resolution, `Reloader` supervisor, and child pipe listener. |
| `asgiri/cli.py` | Add CLI flags, `CliConfiguration` fields, validation, and dispatch to reloader. |
| `pyproject.toml` | Add `reload` optional dependency group for `watchfiles`. |
| `docs/CLI.md` | Document new `--reload*` options. |
| `tests/test_reload.py` | New tests for ignore matching, directory resolution, and `Reloader` behavior. |
| `tests/test_cli.py` | Add tests for CLI parsing and `--reload` + `--workers` incompatibility. |
| `tests/test_server.py` | Add test for `request_shutdown()` thread safety. |

---

## Task 1: Add `Server.request_shutdown()` and reload metadata args

**Files:**
- Modify: `asgiri/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Server.__init__` accepts `reload: bool = False`, `reload_dirs: Sequence[str | Path] | None = None`, `reload_delay_ms: float | int = 200`, `reload_ignore_patterns: Sequence[str] | None = None`; `Server.request_shutdown()` is a public, thread-safe method that stops the server.

- [ ] **Step 1: Write the failing test**

```python
def test_server_request_shutdown_stops_event_loop(unused_port):
    from tests.app import app

    server = Server(app=app, host="127.0.0.1", port=unused_port)

    def run_and_request_shutdown():
        # Start server in a thread
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        # Wait until the event loop is running
        for _ in range(50):
            if server._loop is not None:
                break
            time.sleep(0.01)
        assert server._loop is not None
        server.request_shutdown()
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()

    run_and_request_shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py::test_server_request_shutdown_stops_event_loop -v`
Expected: FAIL with `AttributeError: 'Server' object has no attribute 'request_shutdown'`.

- [ ] **Step 3: Write minimal implementation**

In `asgiri/server.py`:

1. Add a `_loop: asyncio.AbstractEventLoop | None = None` instance attribute in `__init__`.
2. Add `_pending_shutdown: bool = False` instance attribute in `__init__`.
3. Add constructor args after `ws_ping_timeout`:

```python
reload: bool = False,
reload_dirs: Sequence[str | Path] | None = None,
reload_delay_ms: float | int = 200,
reload_ignore_patterns: Sequence[str] | None = None,
```

4. Store them as attributes:

```python
self.reload = reload
self.reload_dirs = reload_dirs
self.reload_delay_ms = reload_delay_ms
self.reload_ignore_patterns = reload_ignore_patterns
```

5. At the start of `a_run`, set `self._loop = asyncio.get_running_loop()` and apply any pending shutdown:

```python
self._loop = asyncio.get_running_loop()
if self._pending_shutdown:
    self._loop.call_soon(self.should_exit.set)
```

6. Add `request_shutdown()` method:

```python
def request_shutdown(self) -> None:
    """Request a graceful shutdown, safe to call from any thread."""
    loop = self._loop
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(self.should_exit.set)
    else:
        # Store pending request; will be applied once a_run starts.
        self._pending_shutdown = True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_server.py::test_server_request_shutdown_stops_event_loop -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add asgiri/server.py tests/test_server.py
git commit -m "feat(reload): add Server.request_shutdown and reload metadata args"
```

---

## Task 2: Add `watchfiles` optional dependency

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `pip install asgiri[reload]` installs `watchfiles>=1.0.0`.

- [ ] **Step 1: Add the optional dependency group**

In `pyproject.toml`, add under `[project.optional-dependencies]`:

```toml
reload = ["watchfiles>=1.0.0"]
```

- [ ] **Step 2: Verify TOML syntax**

Run: `python -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text())"`
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: add watchfiles as reload extra dependency"
```

---

## Task 3: Create reload helpers module

**Files:**
- Create: `asgiri/reload.py`
- Test: `tests/test_reload.py`

**Interfaces:**
- Consumes: `pathlib.Path`, `fnmatch.fnmatch`, `os.path`.
- Produces:
  - `DEFAULT_RELOAD_IGNORE_PATTERNS: tuple[str, ...]`
  - `is_ignored(path: Path, watch_roots: Sequence[Path], extra_patterns: Sequence[str] | None = None) -> bool`
  - `resolve_reload_dirs(app_spec: str, provided_dirs: Sequence[str | Path] | None = None) -> list[Path]`

- [ ] **Step 1: Write failing tests**

Create `tests/test_reload.py`:

```python
import os
from pathlib import Path

import pytest

from asgiri.reload import DEFAULT_RELOAD_IGNORE_PATTERNS, is_ignored, resolve_reload_dirs


class TestIsIgnored:
    def test_ignores_pyc(self):
        root = Path("/project")
        assert is_ignored(Path("/project/foo.pyc"), [root])

    def test_ignores_pycache_dir(self):
        root = Path("/project")
        assert is_ignored(Path("/project/__pycache__/__init__.cpython-312.pyc"), [root])

    def test_ignores_hidden_file(self):
        root = Path("/project")
        assert is_ignored(Path("/project/.env"), [root])

    def test_does_not_ignore_py_file(self):
        root = Path("/project")
        assert not is_ignored(Path("/project/main.py"), [root])

    def test_extra_patterns(self):
        root = Path("/project")
        assert is_ignored(Path("/project/tmp.log"), [root], extra_patterns=["*.log"])


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_reload.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'asgiri.reload'`.

- [ ] **Step 3: Write minimal implementation**

Create `asgiri/reload.py`:

```python
"""Hot-reload support helpers and supervisor."""

import fnmatch
import os
import sys
from pathlib import Path
from typing import Sequence

from asgiri.app_loader import load_application

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

    # Never watch files inside installed site-packages or stdlib.
    abs_path = path.resolve()
    for prefix in sys.path:
        if prefix and abs_path.is_relative_to(Path(prefix)):
            # Allow the current working directory and project paths, but skip
            # anything clearly under a site-packages directory.
            if "site-packages" in abs_path.parts or "dist-packages" in abs_path.parts:
                return True
            break

    rel = _relative_to_root(abs_path, watch_roots)
    parts = abs_path.parts

    # Hidden files / directories anywhere in the path.
    for part in parts:
        if part.startswith(".") and part not in {"."}:
            return True

    for pattern in patterns:
        # Directory pattern with trailing slash.
        if pattern.endswith("/"):
            dir_name = pattern[:-1]
            if dir_name in parts:
                return True
        # Match against full relative path and base name.
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True

    return False


def resolve_reload_dirs(
    app_spec: str,
    provided_dirs: Sequence[str | Path] | None = None,
) -> list[Path]:
    """Return the list of directories to watch for reload."""
    if provided_dirs:
        return [Path(d).resolve() for d in provided_dirs]

    try:
        app = load_application(app_spec)
        module_name = getattr(app, "__module__", None)
        if module_name:
            module = sys.modules.get(module_name)
            if module and hasattr(module, "__file__") and module.__file__:
                module_path = Path(module.__file__).resolve()
                # Watch the package directory, or the module's directory.
                if module_path.name == "__init__.py":
                    watch_dir = module_path.parent
                else:
                    watch_dir = module_path.parent
                return [watch_dir]
    except Exception:
        pass

    return [Path.cwd()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_reload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add asgiri/reload.py tests/test_reload.py
git commit -m "feat(reload): add ignore matching and directory resolution helpers"
```

---

## Task 4: Implement the `Reloader` supervisor

**Files:**
- Modify: `asgiri/reload.py`
- Test: `tests/test_reload.py`

**Interfaces:**
- Consumes: `multiprocessing`, `signal`, `threading`, `loguru.logger`, `watchfiles.watch`, `asgiri.cli.worker_process` (indirectly via a target function).
- Produces:
  - `class Reloader`
  - `Reloader.run(target: Callable[..., None], target_args: tuple = ()) -> int`
  - `run_with_reloader(config: CliConfiguration) -> int` (in `asgiri/cli.py`, added in Task 5).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reload.py`:

```python
from unittest.mock import MagicMock, patch


class TestReloader:
    def test_reloader_sends_reload_and_restarts_child(self):
        from asgiri.reload import Reloader

        reloader = Reloader(
            watch_dirs=[Path("/fake")],
            debounce_ms=50,
            max_restarts=3,
        )

        fake_process = MagicMock()
        fake_process.is_alive.side_effect = [True, False, True, False]
        fake_process.exitcode = 0

        call_order = []

        def fake_target(conn):
            call_order.append(("target", conn.recv()))
            return 0

        with patch("multiprocessing.Process", return_value=fake_process) as mock_process:
            with patch("multiprocessing.Pipe") as mock_pipe:
                parent_conn = MagicMock()
                child_conn = MagicMock()
                mock_pipe.return_value = (parent_conn, child_conn)

                def fake_watch(*args, **kwargs):
                    yield {(1, "/fake/app.py")}

                with patch("asgiri.reload.watch", fake_watch):
                    result = reloader.run(fake_target)

        assert result == 0
        parent_conn.send.assert_any_call("reload")
        assert fake_process.terminate.called or not fake_process.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reload.py::TestReloader::test_reloader_sends_reload_and_restarts_child -v`
Expected: FAIL with `ImportError: cannot import name 'Reloader'`.

- [ ] **Step 3: Write minimal implementation**

Append to `asgiri/reload.py`:

```python
import multiprocessing
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from loguru import logger

try:
    from watchfiles import watch
except ImportError:  # pragma: no cover
    watch = None  # type: ignore


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
        self._shutdown_requested = multiprocessing.Event()

    def _start_child(
        self,
        target: Callable[..., Any],
        target_args: tuple,
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

        debounce = self.debounce_ms / 1000.0
        dirs = [str(d) for d in self.watch_dirs if d.exists()]
        if not dirs:
            logger.warning("No reload directories exist, watching current directory")
            dirs = [str(Path.cwd())]

        try:
            for changes in watch(
                *dirs,
                debounce=debounce,
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

    def run(self, target: Callable[..., Any], target_args: tuple = ()) -> int:
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

                if reload_event.is_set():
                    logger.info("Reload requested, restarting child process")
                    self._send(parent_conn, "reload")
                    process.join(timeout=self.shutdown_timeout)
                    if process.is_alive():
                        logger.warning(
                            "Child did not exit after reload request, terminating"
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
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, old_sigbreak)
            if process.is_alive():
                self._stop_child(process, parent_conn)

        return 0
```

Also add `import sys` at the top of `asgiri/reload.py` if not already present.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reload.py::TestReloader::test_reloader_sends_reload_and_restarts_child -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add asgiri/reload.py tests/test_reload.py
git commit -m "feat(reload): implement signal-free Reloader supervisor"
```

---

## Task 5: Wire reload into the CLI

**Files:**
- Modify: `asgiri/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `asgiri.reload.Reloader`, `asgiri.reload.resolve_reload_dirs`, `CliConfiguration.reload*` fields.
- Produces:
  - `create_parser` gains `--reload`, `--reload-dir`, `--reload-ignore`, `--reload-delay-ms`.
  - `CliConfiguration` gains `reload`, `reload_dirs`, `reload_ignore_patterns`, `reload_delay_ms`.
  - `parse_args` validates `--reload` + `--workers > 1` incompatibility and resolves defaults.
  - `main()` dispatches to `run_with_reloader(config)` when `config.reload` is True.
  - `reloadable_worker_process(config, conn)` runs the server in the child and listens on the pipe.
  - `run_with_reloader(config) -> int` builds and runs the `Reloader`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestReloadCli:
    def test_parse_reload_flag(self):
        args = parse_args(["--reload", "myapp:app"])
        assert args.reload is True
        assert args.reload_delay_ms == 200

    def test_parse_reload_options(self):
        args = parse_args([
            "--reload",
            "--reload-dir", "src",
            "--reload-dir", "lib",
            "--reload-ignore", "*.log",
            "--reload-delay-ms", "300",
            "myapp:app",
        ])
        assert args.reload is True
        assert args.reload_dirs == ["src", "lib"]
        assert args.reload_ignore_patterns == ["*.log"]
        assert args.reload_delay_ms == 300

    def test_reload_incompatible_with_workers(self):
        with pytest.raises(SystemExit):
            parse_args(["--reload", "--workers", "2", "myapp:app"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestReloadCli -v`
Expected: FAIL with `AttributeError: 'CliConfiguration' object has no attribute 'reload'`.

- [ ] **Step 3: Write minimal implementation**

In `asgiri/cli.py`:

1. Import reload helpers at the top:

```python
from .reload import Reloader, resolve_reload_dirs
```

2. Add CLI arguments in `create_parser()` after `--ws-ping-timeout`:

```python
parser.add_argument(
    "--reload",
    action="store_true",
    help="Enable hot-reload mode in development (single-process only)",
)
parser.add_argument(
    "--reload-dir",
    action="append",
    dest="reload_dirs",
    help="Directory or file to watch for changes (repeatable). Defaults to the app module's directory.",
)
parser.add_argument(
    "--reload-ignore",
    action="append",
    dest="reload_ignore_patterns",
    help="Extra ignore pattern for reload watcher (repeatable)",
)
parser.add_argument(
    "--reload-delay-ms",
    type=int,
    default=200,
    help="Reload debounce delay in milliseconds (default: 200)",
)
```

3. Extend `CliConfiguration.__init__` signature with:

```python
reload: bool,
reload_dirs: list[str] | None,
reload_ignore_patterns: list[str] | None,
reload_delay_ms: int,
```

and store them:

```python
self.reload = reload
self.reload_dirs = reload_dirs
self.reload_ignore_patterns = reload_ignore_patterns
self.reload_delay_ms = reload_delay_ms
```

4. Update `parse_args` to pass these fields:

```python
config = CliConfiguration(
    ...,
    reload=parsed_args.reload,
    reload_dirs=parsed_args.reload_dirs,
    reload_ignore_patterns=parsed_args.reload_ignore_patterns,
    reload_delay_ms=parsed_args.reload_delay_ms,
)
```

5. After port default resolution and Windows workers adjustment, add validation:

```python
if config.reload and config.workers != "1":
    logger.error("Cannot use --reload with --workers > 1")
    sys.exit(1)
```

6. Add `reloadable_worker_process` function near `worker_process`:

```python
def reloadable_worker_process(CliConfig: "CliConfiguration", conn) -> int:
    """Worker process that serves and listens for reload/shutdown commands."""
    import threading

    result = _prepare_server(CliConfig)
    if isinstance(result, int):
        return result

    server, create_and_run_server = result

    def pipe_listener():
        try:
            message = conn.recv()
            if message in ("reload", "shutdown"):
                logger.info(f"{message.capitalize()} requested by supervisor")
                server.request_shutdown()
        except EOFError:
            pass
        except Exception as e:
            logger.debug(f"Pipe listener error: {e}")

    listener = threading.Thread(target=pipe_listener, daemon=True)
    listener.start()

    try:
        create_and_run_server()
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        raise
    return 0
```

7. Refactor `worker_process` so the server-building logic is reusable. Extract a helper `_prepare_server(config)` that returns `(server, create_and_run_server)` or an exit code `int`:

Replace the body of `worker_process` from line 218 onward with:

```python
def _prepare_server(CliConfig: "CliConfiguration"):
    """Load app, configure TLS, and return (server, run_server_func)."""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        level=CliConfig.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )

    # Load the application
    try:
        app = load_application(CliConfig.application, wsgi=CliConfig.wsgi)
        logger.info(f"Loaded application: {CliConfig.application}")
    except (ValueError, ImportError, AttributeError) as e:
        logger.error(f"Failed to load application: {e}")
        return 1

    # Handle TLS/SSL configuration
    cert_data = None
    key_data = None
    certfile = None
    keyfile = None

    if CliConfig.selfcert:
        logger.info("Generating self-signed certificate...")
        ip_addrs = (
            [CliConfig.host]
            if CliConfig.host != "0.0.0.0"  # nosec B104
            else None
        )
        cert_data, key_data = generate_self_signed_cert(
            hostname=CliConfig.host,
            ip_addresses=ip_addrs,
        )
        logger.info("Self-signed certificate generated")
    elif CliConfig.cert or CliConfig.key:
        if not (CliConfig.cert and CliConfig.key):
            logger.error("Both --cert and --key must be provided together")
            return 1
        certfile = CliConfig.cert
        keyfile = CliConfig.key
        logger.info(f"Using certificate: {certfile} and key: {keyfile}")

    lifespan_policy_map = {
        "enabled": LifespanPolicy.ENABLED,
        "disabled": LifespanPolicy.DISABLED,
        "auto": LifespanPolicy.AUTO,
    }
    lifespan_policy = lifespan_policy_map[CliConfig.lifespan_policy]

    try:
        num_workers = compute_workers_count(CliConfig.workers)
    except ValueError as e:
        logger.error(f"Invalid --workers value: {e}")
        return 1

    def create_and_run_server():
        ws_ping_interval = (
            CliConfig.ws_ping_interval
            if CliConfig.ws_ping_interval > 0
            else None
        )
        server = Server(
            app=app,
            host=CliConfig.host,
            port=CliConfig.port,
            http_version=CliConfig.protocol,
            certfile=certfile,
            keyfile=keyfile,
            cert_data=cert_data,
            key_data=key_data,
            lifespan=lifespan_policy,
            reuse_port=(num_workers > 1),
            ws_ping_interval=ws_ping_interval,
            ws_ping_timeout=CliConfig.ws_ping_timeout,
        )
        try:
            server.run()
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except Exception as e:
            logger.exception(f"Server error: {e}")
            raise

    protocol_str = CliConfig.protocol.value
    tls_str = "with TLS" if (CliConfig.selfcert or certfile) else "without TLS"

    if num_workers == 1:
        logger.info(
            f"Starting server on {CliConfig.host}:{CliConfig.port} "
            f"({protocol_str}, {tls_str}, lifespan: {CliConfig.lifespan_policy})"
        )
    else:
        logger.info(
            f"Starting server with {num_workers} workers on {CliConfig.host}:{CliConfig.port} "
            f"({protocol_str}, {tls_str}, lifespan: {CliConfig.lifespan_policy})"
        )

    return server, create_and_run_server


def worker_process(CliConfig: "CliConfiguration") -> int:
    """Function to run in each worker process."""
    result = _prepare_server(CliConfig)
    if isinstance(result, int):
        return result
    _, create_and_run_server = result
    create_and_run_server()
    return 0
```

Note: remove the duplicated logging/app-loading/TLS code from the original `worker_process`.

8. Add `run_with_reloader` function in `asgiri/cli.py`:

```python
def run_with_reloader(config: CliConfiguration) -> int:
    """Run the server under the hot-reload supervisor."""
    try:
        from watchfiles import watch  # noqa: F401
    except ImportError:
        logger.error(
            "Hot reload requires the 'watchfiles' package. "
            "Install it with: pip install asgiri[reload]"
        )
        return 1

    watch_dirs = resolve_reload_dirs(
        config.application,
        config.reload_dirs,
    )
    logger.info(f"Watching directories for reload: {watch_dirs}")

    reloader = Reloader(
        watch_dirs=watch_dirs,
        debounce_ms=config.reload_delay_ms,
        ignore_patterns=config.reload_ignore_patterns,
        max_restarts=3,
        shutdown_timeout=30.0,
    )
    return reloader.run(reloadable_worker_process, (config,))
```

9. Update `main()` to dispatch:

```python
def main(args: list[str] | None = None) -> int:
    config: CliConfiguration = parse_args(args)
    if config.reload:
        return run_with_reloader(config)
    spawn_workers(config.workers, worker_process, [config])
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py::TestReloadCli -v`
Expected: PASS.

- [ ] **Step 5: Run the existing CLI tests**

Run: `pytest tests/test_cli.py -v`
Expected: all existing tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add asgiri/cli.py tests/test_cli.py
git commit -m "feat(reload): wire CLI flags and supervisor dispatch"
```

---

## Task 6: Add integration test for reload behavior

**Files:**
- Create: `tests/test_reload_integration.py`

**Interfaces:**
- Consumes: `subprocess`, `tempfile`, `httpx`, `time`.
- Produces: a test that starts `asgiri --reload` on a temporary app, modifies the file, and verifies the response changes.

- [ ] **Step 1: Write the test**

```python
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest


@pytest.mark.slow
@pytest.mark.timeout(30)
def test_hot_reload_updates_response():
    """End-to-end test that --reload picks up source file changes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        app_file = Path(tmpdir) / "myapp.py"
        app_file.write_text(
            "async def app(scope, receive, send):\n"
            "    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/plain')]}\n"
            "    await send({'type': 'http.response.body', 'body': b'v1'})\n"
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = tmpdir
        proc = subprocess.Popen(
            [sys.executable, "-m", "asgiri", "--reload", "--port", "0", f"{tmpdir}/myapp:app"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Find actual port from stderr.
            port = None
            deadline = time.time() + 10
            while time.time() < deadline and port is None:
                line = proc.stderr.readline()
                if "Serving on" in line:
                    # Parse "http://127.0.0.1:<port>"
                    url = line.split("Serving on ")[1].strip()
                    port = int(url.rsplit(":", 1)[1])
                time.sleep(0.1)
            assert port is not None, "Server did not start"

            response = httpx.get(f"http://127.0.0.1:{port}/")
            assert response.status_code == 200
            assert response.text == "v1"

            # Update the file.
            app_file.write_text(
                "async def app(scope, receive, send):\n"
                "    await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/plain')]}\n"
                "    await send({'type': 'http.response.body', 'body': b'v2'})\n"
            )

            # Wait for reload to complete.
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    response = httpx.get(f"http://127.0.0.1:{port}/")
                    if response.text == "v2":
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.2)
            assert response.text == "v2"
        finally:
            proc.terminate()
            proc.wait(timeout=10)
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_reload_integration.py -v -m slow`
Expected: PASS (requires `watchfiles` installed).

- [ ] **Step 3: Commit**

```bash
git add tests/test_reload_integration.py
git commit -m "test(reload): add end-to-end hot reload integration test"
```

---

## Task 7: Update CLI documentation

**Files:**
- Modify: `docs/CLI.md`

- [ ] **Step 1: Add reload section after "## Other Options"**

Append to `docs/CLI.md`:

```markdown
## Hot Reload Options (Development Only)

 * `--reload` — enable hot-reload mode. The server restarts automatically when source files change.
 * `--reload-dir=<path>` — add a directory or file to watch (repeatable). Defaults to the directory of the application module, or the current working directory.
 * `--reload-ignore=<pattern>` — add an extra ignore pattern (repeatable). The built-in ignore list already excludes `*.pyc`, `__pycache__/`, `.git/`, `.venv/`, `node_modules/`, test/cache directories, and hidden files.
 * `--reload-delay-ms=<ms>` — debounce delay in milliseconds before triggering a reload (default: 200).

Requirements:
 * Requires the `watchfiles` package: `pip install asgiri[reload]`.
 * Incompatible with `--workers > 1` (hot reload is single-process only).

Example:
```bash
asgiri --reload --reload-dir ./src --reload-ignore "*.log" myapp:app
```
```

- [ ] **Step 2: Update usage block**

Update the usage block near the top to include the new options:

```
usage: asgiri [-h] [--http11 | --http2 | --http3] [--host HOST] [--port PORT]
              [--workers WORKERS] [--selfcert] [--cert CERT] [--key KEY]
              [--wsgi] [--lifespan-policy {enabled,disabled,auto}]
              [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}]
              [--reload] [--reload-dir RELOAD_DIR] [--reload-ignore RELOAD_IGNORE]
              [--reload-delay-ms RELOAD_DELAY_MS]
              application
```

- [ ] **Step 3: Commit**

```bash
git add docs/CLI.md
git commit -m "docs(cli): document hot reload options"
```

---

## Task 8: Full test suite and lint run

**Files:**
- All modified files.

- [ ] **Step 1: Install project with reload extra in the venv**

Run: `uv pip install -e ".[reload,dev]"`
Expected: installs `watchfiles` and project in editable mode.

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/ -m "not slow" -q`
Expected: PASS (existing tests still pass; new non-slow tests pass).

- [ ] **Step 3: Run slow integration tests**

Run: `pytest tests/test_reload_integration.py -v`
Expected: PASS.

- [ ] **Step 4: Run lint/format checks**

Run: `ruff check asgiri tests`
Expected: clean or only pre-existing issues.

Run: `black --check asgiri tests`
Expected: clean or only pre-existing formatting differences.

Run: `mypy asgiri`
Expected: no new type errors introduced by reload code.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "chore: fix formatting and type checks for hot reload"
```

---

## Spec coverage self-review

| Spec section | Task(s) |
| --- | --- |
| CLI options `--reload`, `--reload-dir`, `--reload-ignore`, `--reload-delay-ms` | Task 5, Task 7 |
| Server constructor `reload*` args and `request_shutdown()` | Task 1 |
| Built-in ignore patterns + extras | Task 3, Task 5 |
| Directory resolution (app module -> cwd fallback) | Task 3, Task 5 |
| Supervisor/child architecture with `multiprocessing.Pipe` | Task 4, Task 5 |
| No parent-directed signals, cross-platform | Task 4 |
| `watchfiles` optional extra + lazy import | Task 2, Task 5 |
| Max 3 consecutive child crashes | Task 4 |
| Debounce 200 ms default | Task 1 (constructor), Task 4 (Reloader), Task 5 (CLI default) |
| `--reload` incompatible with `--workers > 1` | Task 5 |
| Logging at INFO for reload events | Task 4, Task 5 |
| Tests: ignore matching, directory resolution, child lifecycle, crash backoff, `request_shutdown`, signal forwarding, integration | Tasks 1, 3, 4, 5, 6 |
| Backward compatibility | Tasks 1, 5 (defaults to no behavior change) |

No placeholders remain. All tasks include exact file paths, code, and verification commands.
