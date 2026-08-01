"""Shared WebSocket ping/pong keep-alive logic."""

import asyncio
from typing import Awaitable, Callable

from loguru import logger


class WebSocketHeartbeat:
    """Manage server-initiated WebSocket ping frames and pong timeouts.

    The heartbeat sends a ping frame every ``ping_interval`` seconds. After each
    ping, it starts a watchdog that closes the connection if no frame (any frame,
    including a pong) is received within ``ping_timeout`` seconds.
    """

    def __init__(
        self,
        ping_interval: float | None,
        ping_timeout: float,
        send_ping: Callable[[], None],
        close: Callable[[], None] | Callable[[], Awaitable[None]],
    ) -> None:
        """Initialize the heartbeat.

        Args:
            ping_interval: Seconds between outgoing pings. ``None`` disables pings.
            ping_timeout: Seconds to wait for any incoming frame after a ping.
            send_ping: Callback invoked to send a ping frame.
            close: Callback invoked when the pong timeout fires.
        """
        if ping_interval is not None and ping_interval < 0:
            raise ValueError("ping_interval must be non-negative")
        if ping_timeout < 0:
            raise ValueError("ping_timeout must be non-negative")

        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._send_ping = send_ping
        self._close: Callable[[], None] | Callable[[], Awaitable[None]] = close

        self._heartbeat_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._last_ping_sent_at: float = 0.0
        self._last_activity_at: float = 0.0
        self._closed = False

    def start(self) -> None:
        """Start the heartbeat task."""
        if self._ping_interval is None:
            return
        self._heartbeat_task = asyncio.create_task(self._run_heartbeat())
        logger.debug("WebSocket heartbeat started")

    def stop(self) -> None:
        """Stop the heartbeat task and any pending watchdog."""
        self._closed = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        logger.debug("WebSocket heartbeat stopped")

    def record_activity(self) -> None:
        """Notify the heartbeat that any frame was received from the peer."""
        self._last_activity_at = asyncio.get_running_loop().time()
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            self._watchdog_task = None

    async def _run_heartbeat(self) -> None:
        """Loop that sends periodic pings and starts pong watchdogs."""
        ping_interval = self._ping_interval
        while not self._closed and ping_interval is not None:
            try:
                await asyncio.sleep(ping_interval)
            except asyncio.CancelledError:
                break

            if self._closed:
                break

            self._last_ping_sent_at = asyncio.get_running_loop().time()
            try:
                self._send_ping()
            except Exception:
                logger.exception("Failed to send WebSocket ping")
                break

            self._watchdog_task = asyncio.create_task(self._run_watchdog())

    async def _run_watchdog(self) -> None:
        """Wait for pong timeout and close the connection if no activity."""
        deadline = self._last_ping_sent_at + self._ping_timeout
        try:
            await asyncio.sleep(
                max(0.0, deadline - asyncio.get_running_loop().time())
            )
        except asyncio.CancelledError:
            return

        if self._closed:
            return

        if self._last_activity_at < self._last_ping_sent_at:
            logger.warning("WebSocket ping timeout, closing connection")
            result = self._close()
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)
