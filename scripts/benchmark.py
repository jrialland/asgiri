"""
Benchmarking utilities that uses locust to run performance tests.

Usage:
    uv run python scripts/benchmark.py --list
    uv run python scripts/benchmark.py --benchmark asgiri
    uv run python scripts/benchmark.py --all
"""

import subprocess
import sys
import time
from argparse import ArgumentParser
from contextlib import contextmanager
from pathlib import Path

# Try to import psutil, will be installed if missing
try:
    import psutil  # noqa: F401

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


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


project_root = find_root()


@contextmanager
def server_process(server_cmd: list[str]):
    """
    Context manager to start and stop a server process.

    Args:
        server_cmd (list[str]): Command to start the server.
    """
    server_proc = subprocess.Popen(
        server_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Give the server time to start up
        time.sleep(2)
        yield
    finally:
        # Kill the process and all its children
        if HAS_PSUTIL:
            # Use psutil for robust cleanup
            try:
                import psutil  # noqa: F401, F811

                parent = psutil.Process(server_proc.pid)
                children = parent.children(recursive=True)

                # Terminate children first
                for child in children:
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass

                # Terminate parent
                parent.terminate()

                # Wait for processes to terminate gracefully
                gone, alive = psutil.wait_procs(children + [parent], timeout=3)

                # Force kill any remaining processes
                for p in alive:
                    try:
                        p.kill()
                    except psutil.NoSuchProcess:
                        pass
            except psutil.NoSuchProcess:
                pass
        else:
            # Fallback: basic termination
            server_proc.terminate()

        # Ensure the original process is cleaned up
        try:
            server_proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait()

        # Wait a bit to ensure port is released
        time.sleep(2)


def run_benchmark(
    name: str,
    server_cmd: list[str],
    locustfile: Path,
    users: int,
    spawn_rate: int,
    run_time: str,
):
    """
    Run a locust benchmark test with the specified parameters.

    Args:
        name (str): Name of the benchmark.
        server_cmd (list[str]): Command to start the server.
        locustfile (Path): Path to the locustfile.
        users (int): Number of users to simulate.
        spawn_rate (int): Rate at which users are spawned.
        run_time (str): Duration to run the test (e.g., '1m', '10s').
    """
    # Create .benchmark directory if it doesn't exist
    benchmarks_dir = project_root / ".benchmarks"
    benchmarks_dir.mkdir(exist_ok=True)

    locust_command = [
        "uv",
        "run",
        "locust",
        "-f",
        locustfile.absolute().as_posix(),
        "--host",
        "http://localhost:8000",
        "--headless",
        "-u",
        str(users),
        "-r",
        str(spawn_rate),
        "--run-time",
        run_time,
        "--csv",
        (benchmarks_dir / f"benchmark_{name}").as_posix(),
    ]

    with server_process(server_cmd):
        subprocess.run(
            locust_command,
            cwd=project_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def install_package(package: str):
    """
    we are using uv to manage the virtual environment and dependencies. This function ensures that the specified package is installed in the uv environment.
    Args:
        package (str): The package to install.
    """
    subprocess.run(
        ["uv", "run", "pip", "install", package], cwd=project_root, check=True
    )


benchmark_template = {
    "name": "",
    "need_install": [],
    "server_cmd": [],
    "locustfile": project_root / "scripts" / "locustfile.py",
    "users": 10,
    "spawn_rate": 10,
    "run_time": "1m",
}

benchmarks = [
    benchmark_template
    | {
        "name": "uvicorn",
        "need_install": ["uvicorn[standard]"],
        "server_cmd": [
            "uv",
            "run",
            "uvicorn",
            "tests.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
    },
    benchmark_template
    | {
        "name": "hypercorn",
        "need_install": ["hypercorn"],
        "server_cmd": [
            "uv",
            "run",
            "hypercorn",
            "tests.app:app",
            "--bind",
            "127.0.0.1:8000",
        ],
    },
    benchmark_template
    | {
        "name": "daphne",
        "need_install": ["daphne"],
        "server_cmd": [
            "uv",
            "run",
            "daphne",
            "-b",
            "127.0.0.1",
            "-p",
            "8000",
            "tests.app:app",
        ],
    },
    benchmark_template
    | {
        "name": "granian",
        "need_install": ["granian"],
        "server_cmd": [
            "uv",
            "run",
            "granian",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "8000" "tests.app:app",
        ],
    },
    benchmark_template
    | {
        "name": "asgiri",
        "need_install": ["asgiri"],
        "server_cmd": [
            "uv",
            "run",
            "asgiri",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "tests.app:app",
        ],
    },
]

if __name__ == "__main__":
    # Ensure psutil is installed first for process management
    if not HAS_PSUTIL:
        print("Installing psutil...")
        subprocess.run(
            ["uv", "pip", "install", "psutil"],
            cwd=project_root,
            check=True,
        )
        print(
            "psutil installed. Please re-run this script with: "
            "uv run python scripts/benchmark.py [options]"
        )
        sys.exit(0)

    parser = ArgumentParser(description="Run benchmark tests using locust.")
    # the "--list" argument to list available benchmarks
    parser.add_argument(
        "--list", action="store_true", help="List available benchmarks"
    )
    # the "--benchmark" argument to specify which benchmark to run
    parser.add_argument(
        "--benchmark", type=str, help="Name of the benchmark to run"
    )
    # the "--all" argument to run all benchmarks sequentially
    parser.add_argument(
        "--all", action="store_true", help="Run all benchmarks sequentially"
    )

    args = parser.parse_args()
    if args.list:
        print("Available benchmarks:")
        for bm in benchmarks:
            print(f" - {bm['name']}")
        sys.exit(0)

    benchmark_names = (
        [args.benchmark]
        if args.benchmark
        else [bm["name"] for bm in benchmarks]
    )

    if benchmark_names:
        for bm_name in benchmark_names:
            benchmark = next(
                (bm for bm in benchmarks if bm["name"] == bm_name), None
            )
            if not benchmark:
                print(f"Benchmark '{args.benchmark}' not found.")
                sys.exit(1)

            for package in benchmark["need_install"]:
                install_package(package)

            print(f"Running benchmark: {benchmark['name']}")
            run_benchmark(
                name=benchmark["name"],
                server_cmd=benchmark["server_cmd"],
                locustfile=benchmark["locustfile"],
                users=benchmark["users"],
                spawn_rate=benchmark["spawn_rate"],
                run_time=benchmark["run_time"],
            )
            print(f"Completed benchmark: {benchmark['name']}\n")
            print(f"Completed benchmark: {benchmark['name']}\n")
