"""Multiprocessing support for running multiple worker processes.

This module provides functionality to run the ASGI server with multiple
worker processes using SO_REUSEPORT to share the same port across processes.
This is similar to uvicorn's --workers option.
"""

import multiprocessing
import os
import signal
import sys
from typing import Callable

from loguru import logger


def compute_workers_count(workers: str | int) -> int:
    """Calculate the number of workers to use.

    Args:
        workers: Either an integer, or "auto" to detect CPU count.

    Returns:
        Number of worker processes to spawn.

    Raises:
        ValueError: If workers is invalid.
    """
    if isinstance(workers, int):
        if workers < 1:
            raise ValueError(f"workers must be >= 1, got {workers}")
        return workers

    if workers == "auto":
        # Use CPU count
        cpu_count = os.cpu_count() or 1
        logger.info(f"Auto-detected {cpu_count} CPUs")
        return cpu_count

    # Try to parse as integer
    try:
        count = int(workers)
        if count < 1:
            raise ValueError(f"workers must be >= 1, got {count}")
        return count
    except ValueError as e:
        raise ValueError(
            f"workers must be an integer >= 1 or 'auto', got '{workers}'"
        ) from e


def run_worker(
    worker_id: int,
    target_func: Callable[[], None],
    *args,
) -> None:
    """Run a single worker process.

    Args:
        worker_id: Unique identifier for this worker.
        target_func: The function to run in the worker process.
        restart_on_failure: Whether to restart the worker if it fails.
    """
    logger.info(f"Worker {worker_id} starting (PID: {os.getpid()})")

    try:
        target_func(*args)
    except KeyboardInterrupt:
        logger.info(f"Worker {worker_id} interrupted")
    except Exception as e:
        logger.exception(f"Worker {worker_id} failed with error: {e}")
        raise
    finally:
        logger.info(f"Worker {worker_id} shutting down")


def spawn_workers(
    workers: str,
    target_func: Callable[[], None],
    args: list = [],
) -> None:
    """Spawn multiple worker processes.

    Each worker will run the target_func. The workers share the same
    port using SO_REUSEPORT (which must be set up in the target_func).

    Args:
        workers: Number of worker processes to spawn, or "auto" to detect CPU count.
        target_func: The function each worker should execute.
        args: Additional arguments to pass to the target function.

    Raises:
        ValueError: If num_workers < 1.
    """
    num_workers = compute_workers_count(workers)
    
    # Special case: if only 1 worker, just run directly without forking
    if num_workers == 1:
        logger.info("Running with 1 worker (no multiprocessing)")
        target_func(*args)
        return

    logger.info(f"Starting {num_workers} worker processes")

    # Track worker processes
    workers: dict[int, multiprocessing.Process] = {}

    def start_worker(worker_id: int) -> multiprocessing.Process:
        """Start a single worker process."""
        process = multiprocessing.Process(
            target=run_worker,
            args=(worker_id, target_func, *args),
            name=f"asgiri-worker-{worker_id}",
        )
        process.start()
        return process

    # Start all workers
    for i in range(num_workers):
        workers[i] = start_worker(i)

    # Setup signal handlers for graceful shutdown
    shutdown_event = multiprocessing.Event()

    def signal_handler(signum, frame):
        logger.info(
            f"Received signal {signum}, shutting down {num_workers} workers"
        )
        shutdown_event.set()

        # Terminate all workers gracefully
        for worker_id, process in workers.items():
            if process.is_alive():
                logger.info(f"Terminating worker {worker_id}")
                process.terminate()

        # Wait for workers to finish
        for worker_id, process in workers.items():
            process.join(timeout=5)
            if process.is_alive():
                logger.warning(
                    f"Worker {worker_id} didn't terminate, killing it"
                )
                process.kill()
                process.join()

        logger.info("All workers shut down")
        sys.exit(0)

    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Monitor workers and restart if needed
    try:
        while not shutdown_event.is_set():
            for worker_id, process in list(workers.items()):
                if not process.is_alive():
                    exit_code = process.exitcode
                    logger.warning(
                        f"Worker {worker_id} died with exit code {exit_code}"
                    )

                    if not shutdown_event.is_set():
                        logger.info(f"Restarting worker {worker_id}")
                        workers[worker_id] = start_worker(worker_id)
                    else:
                        # If one worker dies and we're not restarting,
                        # shut down all workers
                        logger.error("Worker died, shutting down all workers")
                        signal_handler(signal.SIGTERM, None)

            # Sleep briefly before checking again
            shutdown_event.wait(timeout=1.0)

    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)
