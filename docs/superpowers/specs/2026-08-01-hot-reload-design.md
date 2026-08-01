# Hot Reload Mode Design

Date: 2026-08-01
Status: Draft — pending implementation planning

## Goal

Add a development-time hot-reload mode to `asgiri`. When enabled, the server detects changes in the user’s source code (with debounce) and restarts the application automatically. The implementation must avoid the Windows-specific problem seen in uvicorn, where the reloader signals the parent process and can terminate the controlling process unexpectedly.

## Non-goals

- Production-grade process supervision (restarts are best-effort, capped).
- Hot reload for multi-worker mode.
- Reloading of arbitrary non-Python files (e.g., templates, static assets). The feature focuses on Python source code.

## Constraints

1. **Single-process only** — `--reload` is incompatible with `--workers > 1` and errors out.
2. **No parent-directed signals** — the reloader must never send a signal to the child or parent to trigger a reload. Communication is via `multiprocessing.Pipe` only.
3. **Cross-platform** — must work on Windows, Linux, and macOS.
4. **Minimal intrusion** — existing `Server` behavior is unchanged when `reload=False`.

## User-facing interface

### CLI options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--reload` | flag | False | Enable hot-reload mode. |
| `--reload-dir` | repeatable | derived | Directory or file path to watch. If omitted, the directory of the application module is watched; if that cannot be determined, the current working directory is used. |
| `--reload-ignore` | repeatable | built-in list | Extra ignore pattern. Patterns are matched against the file/directory base name or the full relative path. |
| `--reload-delay-ms` | int | 200 | Debounce delay in milliseconds. Changes within this window are coalesced into a single reload. |

Example:

```bash
asgiri --reload --reload-dir ./src --reload-ignore "*.log" --reload-delay-ms 300 myapp:app
```

### Server constructor additions

```python
Server(
    app,
    host="127.0.0.1",
    port=8000,
    reload=False,                       # bool
    reload_dirs=None,                   # Sequence[str | Path] | None
    reload_delay_ms=200,                # float | int
    reload_ignore_patterns=None,        # Sequence[str] | None
)
```

These arguments are metadata for the reload supervisor. When `reload=False`, the new arguments have no effect.

### New public method on `Server`

```python
def request_shutdown(self) -> None:
    """Request a graceful shutdown, safe to call from any thread."""
```

This method sets the internal `should_exit` event via `loop.call_soon_threadsafe`, allowing the reload supervisor’s pipe-listener thread (running outside the asyncio event loop) to stop the server cleanly. If called before `a_run()` has started, the shutdown request is stored and applied as soon as the event loop is available, so a reload command received very early still terminates the server.

## Architecture

### Components

```text
parent CLI process
├── Reloader (asgiri.reload.Reloader)
│   ├── starts child process
│   ├── starts watchfiles watcher thread
│   ├── on file change -> Pipe.send("reload")
│   ├── on SIGINT/SIGTERM -> Pipe.send("shutdown")
│   └── restarts child when it exits
└── Pipe

child process
├── serve process (asgiri.cli.reloadable_worker_process)
│   ├── builds Server
│   ├── starts Pipe listener thread
│   │   └── on "reload"/"shutdown" -> server.request_shutdown()
│   └── runs server.run()
└── Pipe
```

### Module structure

- `asgiri/server.py`: add `reload*` constructor args and `request_shutdown()`.
- `asgiri/reload.py`: new module containing the supervisor logic.
- `asgiri/cli.py`: add CLI flags and route to the reloader when `--reload` is set.

### Data flow

1. The CLI parses `--reload` and collects directories, ignore patterns, and delay.
2. If `--reload` is set, `main()` calls `run_with_reloader(config)` instead of the normal `spawn_workers(..., worker_process, ...)` path.
3. `Reloader` creates a `multiprocessing.Pipe` and starts a `Process` that runs `reloadable_worker_process(config, child_conn)`.
4. The watcher thread calls `watchfiles.watch(*dirs, debounce=delay_ms/1000, stop_event=stop_event)`.
5. When the watcher detects a non-ignored change, it sets a `multiprocessing.Event` (`reload_event`).
6. The main loop in `Reloader` notices the event, sends `"reload"` through the pipe, and waits for the child to exit.
7. The child’s listener thread receives `"reload"`, calls `server.request_shutdown()`, and the child exits with code `0`.
8. The parent sees exit code `0`, resets the failure counter, and starts a new child with a fresh watcher thread.
9. If the child exits with a non-zero code, the parent increments a consecutive-failure counter. After **3** consecutive non-zero exits, the parent logs the last exit code and exits with that code.
10. On `SIGINT`/`SIGTERM`, the parent sends `"shutdown"` through the pipe, waits for the child to exit (with timeout), then terminates/kills if necessary, and finally exits cleanly.

### Communication protocol over Pipe

Only two commands are sent from parent to child:

- `"reload"` — child should gracefully shut down and exit with code `0`. The parent will start a new child.
- `"shutdown"` — child should gracefully shut down and exit with code `0`. The parent will exit.

The pipe is never used in the reverse direction. The child’s exit code is the only outcome signal.

## Default ignore patterns

The following entries are always ignored, plus any user-provided `--reload-ignore` patterns:

- `*.pyc`
- `__pycache__/`
- `.git/`
- `.venv/`, `venv/`, `.env/`
- `node_modules/`
- `.pytest_cache/`
- `.ruff_cache/`
- `.mypy_cache/`
- `*.egg-info/`
- `.tox/`
- `.coverage`
- `*.pyo`
- Hidden files and directories (names starting with `.`)
- Paths inside Python standard library / installed site-packages (we only watch user project files)

Pattern matching is done on the relative path from the closest watched root. Both glob-style wildcards (`*`, `?`) and directory trailing slash are supported. A change is ignored if **any** of its paths matches an ignore pattern.

## Directory resolution

1. If `--reload-dir` is provided, use those paths (files are accepted; their parent directory is watched).
2. Otherwise, resolve the directory of the ASGI application module (`config.application`). If the module is a package, watch the package directory; if it is a module inside a package, watch the package directory. This is done by importing the module via `asgiri.app_loader.load_application` and inspecting `app.__module__`.
3. If resolution fails, fall back to the current working directory.

## Windows safety details

- The parent never calls `os.kill`, `signal.raise_signal`, or `process.send_signal` to communicate with the child.
- `multiprocessing.Pipe` uses named pipes on Windows and Unix sockets/anonymous pipes on POSIX, so it is cross-platform and signal-free.
- `Server` signal handlers (`SIGINT`, `SIGTERM`, `SIGBREAK`) are installed only in the child process, exactly as today. The parent installs its own lightweight signal handlers that forward `"shutdown"` to the child.
- The watcher thread is a daemon and receives a `stop_event`, so it does not block process exit.

## Dependency management

`watchfiles` is added as an optional dependency under the `reload` extra:

```toml
[project.optional-dependencies]
reload = ["watchfiles>=1.0.0"]
```

CLI code imports `watchfiles` lazily inside the reloader path. If `--reload` is requested and `watchfiles` is not installed, the CLI exits with a clear message:

```text
Hot reload requires the 'watchfiles' package. Install it with: pip install asgiri[reload]
```

## Error handling

| Scenario | Behavior |
| --- | --- |
| `--reload` with `--workers > 1` | CLI error: `Cannot use --reload with --workers > 1`. |
| `--reload` with missing `watchfiles` | CLI error with installation hint. |
| Child crashes on startup | Parent logs the traceback (printed by the child), counts as a failure, and restarts up to 3 times. After 3 consecutive failures, parent exits with the child’s exit code. |
| Child exits 0 after reload | Parent starts a new child immediately. |
| User presses Ctrl+C | Parent sends `"shutdown"`, waits for child, then exits 0. |
| Child refuses to exit | Parent waits `shutdown_timeout` (reuse `Server.shutdown_timeout`), then `terminate()` + `join(5s)`, then `kill()` if still alive. |

## Logging

- Parent logs: `Starting hot-reload supervisor`, `Detected file changes, requesting reload`, `Child process exited with code N`, `Restarting child process`, `Giving up after N consecutive failures`.
- Child logs: `Reload requested by supervisor`, `Shutdown requested by supervisor`.
- Reload events are logged at `INFO` level, not as errors or exceptions, so users do not mistake a normal reload for a crash.

## Testing

Unit / integration tests to add:

1. **CLI parsing** — `--reload` with `--workers 2` raises a `SystemExit` / error.
2. **Ignore matching** — verify each built-in ignore pattern matches and non-matching paths do not.
3. **Directory resolution** — given `tests.app:app`, resolves to the `tests/` directory.
4. **Reloader child lifecycle** — mock the child process and pipe to verify `"reload"` is sent and a new child is spawned after exit code 0.
5. **Reloader crash backoff** — simulate three consecutive child crashes and assert the parent gives up and exits with the last code.
6. **`request_shutdown()` thread safety** — call `request_shutdown()` from a thread while the server loop is running and assert the event loop stops.
7. **Signal forwarding** — send `SIGINT` to the parent (or call the signal handler directly) and assert `"shutdown"` is sent and the parent exits cleanly.
8. **Actual hot reload** — start the CLI with `--reload` in a subprocess, modify the app file, and verify the server responds with the updated content. Mark as slow / integration.

## Backward compatibility

- Existing `Server` behavior is unchanged when `reload=False` (the default).
- Existing CLI behavior is unchanged when `--reload` is absent.
- No new required dependencies.

## Open questions resolved

- Single-process only: yes.
- Watch backend: `watchfiles` optional extra.
- Reload strategy: supervisor child process, no signals.
- Parent-child communication: `multiprocessing.Pipe` with `"reload"` / `"shutdown"` strings.
- `Server` involvement: `reload*` metadata args + `request_shutdown()` method.
- Default debounce: 200 ms.
- Max consecutive child crashes: 3.
- Ignore patterns: built-in list + user extras.
