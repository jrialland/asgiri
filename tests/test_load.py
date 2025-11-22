import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest


def find_root() -> Path:
    """
    Find the project root directory.

    Returns: Path
    """
    current = Path(__file__).parent
    while current != current.parent:
        if "pyproject.toml" in [p.name for p in current.iterdir()]:
            return current
        current = current.parent
    raise RuntimeError("Project root not found.")


@contextmanager
def with_process(cmd: list[str]):
    """
    Context manager to run the Asgiri server for load testing.

    Args:
        server_cmd (list[str]): Command to start the Asgiri server.
    """
    server_proc = subprocess.Popen(cmd)
    try:
        yield
    finally:
        server_proc.terminate()
        server_proc.wait()


def run_locustfile(
    locustfile: Path,
    users: int = 10,
    spawn_rate: int = 10,
    run_time: str = "10s",
    host: str = "http://localhost:8000",
):
    """
    Run a locust load test using the specified locustfile.

    Args:
        locustfile (Path): Path to the locustfile.
        users (int): Number of users to simulate.
        spawn_rate (int): Rate at which users are spawned.
        run_time (str): Duration to run the test (e.g., '1m', '10s').
        host (str): The host to target for the load test.
    """
    locust_command = [
        "uv",
        "run",
        "locust",
        "-f",
        locustfile.absolute().as_posix(),
        "--headless",
        "--host",
        host,
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "--run-time",
        run_time,
    ]

    subprocess.run(locust_command, check=True)


@pytest.mark.slow
def test_load():

    python = sys.executable
    server_cmd = [
        python,
        "-m",
        "asgiri",
        "--host",
        "localhost",
        "--port",
        "8000",
        "tests.app:app",
    ]
    locustfile = find_root() / "scripts" / "locustfile.py"

    with with_process(server_cmd):
        run_locustfile(locustfile)
