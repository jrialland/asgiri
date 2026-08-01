"""End-to-end test for hot reload mode."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest


def _find_unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.slow
@pytest.mark.timeout(30)
def test_hot_reload_updates_response():
    """Start asgiri --reload, modify the app, and verify the new response."""
    port = _find_unused_port()
    with tempfile.TemporaryDirectory() as tmpdir:
        app_file = Path(tmpdir) / "myapp.py"
        app_file.write_text(
            "async def app(scope, receive, send):\n"
            "    await send({\n"
            "        'type': 'http.response.start',\n"
            "        'status': 200,\n"
            "        'headers': [(b'content-type', b'text/plain')],\n"
            "    })\n"
            "    await send({'type': 'http.response.body', 'body': b'v1'})\n"
        )

        env = os.environ.copy()
        env["PYTHONPATH"] = tmpdir
        # Use bare module:attribute spec with PYTHONPATH set.
        # Disable lifespan because the minimal test app does not implement it.
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "asgiri",
                "--reload",
                "--reload-dir",
                tmpdir,
                "--port",
                str(port),
                "--lifespan-policy",
                "disabled",
                "myapp:app",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            deadline = time.time() + 10
            ready = False
            while time.time() < deadline and not ready:
                line = proc.stderr.readline()
                if "Serving on" in line and f":{port}" in line:
                    ready = True
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            assert ready, f"Server did not start. stderr:\n{proc.stderr.read()}"

            response = httpx.get(f"http://127.0.0.1:{port}/")
            assert response.status_code == 200
            assert response.text == "v1"

            # Update the file.
            app_file.write_text(
                "async def app(scope, receive, send):\n"
                "    await send({\n"
                "        'type': 'http.response.start',\n"
                "        'status': 200,\n"
                "        'headers': [(b'content-type', b'text/plain')],\n"
                "    })\n"
                "    await send({'type': 'http.response.body', 'body': b'v2'})\n"
            )

            # Wait for reload to complete.
            deadline = time.time() + 10
            new_response = response
            while time.time() < deadline:
                try:
                    new_response = httpx.get(f"http://127.0.0.1:{port}/")
                    if new_response.text == "v2":
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.2)
            assert new_response.text == "v2"
        finally:
            proc.terminate()
            proc.wait(timeout=10)
