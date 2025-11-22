"""Protocol auto-detection and switching for HTTP/1.1 and HTTP/2.

This module implements a protocol switcher that can:
1. Use ALPN (Application-Layer Protocol Negotiation) for TLS connections
2. Detect HTTP/2 "prior knowledge" connections (h2c - HTTP/2 over cleartext)
3. Handle HTTP/1.1 requests with automatic protocol detection
4. Automatically advertise HTTP/2 and HTTP/3 capability
5. Delegate to appropriate protocol handler
"""

import asyncio
from typing import Any, cast, override

from asgiref.typing import ASGI3Application
from loguru import logger

from asgiri.middleware import wrap_with_advertisements
from asgiri.proto.http2 import Http2ServerProtocol
from asgiri.proto.http11 import HTTP11ServerProtocol

# HTTP/2 connection preface (client magic)
HTTP2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"


class AutoProtocol(asyncio.Protocol):
    """Protocol that auto-detects HTTP/1.1 vs HTTP/2 and delegates accordingly.

    This protocol handles four scenarios:
    1. ALPN Negotiation (TLS): Uses ALPN result to immediately select protocol
    2. HTTP/2 Prior Knowledge (h2c): Detects the HTTP/2 connection preface
    3. HTTP/1.1 Upgrade: Responds to Upgrade: h2c headers (future)
    4. Plain HTTP/1.1: Falls back to HTTP/1.1 when no upgrade is requested

    For TLS connections, ALPN is the preferred and fastest method as it avoids
    waiting for application data. For cleartext connections, the server detects
    the HTTP/2 preface or falls back to HTTP/1.1.

    The protocol advertises HTTP/2 and HTTP/3 capability via Alt-Svc headers.
    """

    def __init__(
        self,
        server: tuple[str, int],
        app: ASGI3Application,
        state: dict[str, Any] | None = None,
        ssl: bool = False,
    ):
        """Initialize the auto-detecting protocol.

        Args:
            server: A tuple containing the server's host and port.
            app: The ASGI application to handle requests.
            state: A copy of the namespace passed into the lifespan corresponding to this request.
            ssl: Whether the connection is over SSL (for ALPN negotiation).
        """
        super().__init__()
        self.server = server
        self.app = app
        self.state = state or {}
        self.ssl = ssl

        self.transport: asyncio.Transport | None = None
        self.buffer = bytearray()
        self.delegated_protocol: asyncio.Protocol | None = None
        self.protocol_detected = False

    @override
    def connection_made(self, transport: asyncio.BaseTransport):
        """Handle a new connection.

        For TLS connections, checks ALPN negotiation to immediately delegate
        to the appropriate protocol without waiting for data.

        Args:
            transport: The transport representing the connection.
        """

        self.transport = cast(asyncio.Transport, transport)

        # For SSL/TLS connections, check ALPN negotiation result
        ssl_object = transport.get_extra_info("ssl_object")
        if ssl_object:
            try:
                alpn_protocol = ssl_object.selected_alpn_protocol()
                if alpn_protocol:
                    logger.debug(f"ALPN negotiated protocol: {alpn_protocol}")

                    # Delegate immediately based on ALPN result
                    if alpn_protocol == "h2":
                        self._delegate_to_http2()
                        return
                    elif alpn_protocol == "http/1.1":
                        self._delegate_to_http11()
                        return
                    else:
                        logger.warning(
                            f"Unknown ALPN protocol: {alpn_protocol}, "
                            "falling back to detection"
                        )
            except Exception as e:
                logger.debug(f"Could not retrieve ALPN protocol: {e}")

        logger.debug("Connection made, waiting for protocol detection")

    @override
    def data_received(self, data: bytes):
        """Receive data and detect which protocol to use.

        Args:
            data: The received data bytes.
        """
        if self.delegated_protocol:
            # Already delegated, pass through
            self.delegated_protocol.data_received(data)
            return

        # Buffer incoming data for protocol detection
        self.buffer.extend(data)

        if not self.protocol_detected:
            # Check if we have enough data to detect the protocol
            if len(self.buffer) >= len(HTTP2_PREFACE):
                if self.buffer.startswith(HTTP2_PREFACE):
                    # HTTP/2 Prior Knowledge (h2c)
                    self._delegate_to_http2()
                else:
                    # HTTP/1.1 (possibly with Upgrade header)
                    self._delegate_to_http11()
            elif len(self.buffer) >= 4:
                # Quick check: HTTP/1.x requests start with method names
                # If it doesn't start with "PRI ", it's likely HTTP/1.1
                if not self.buffer.startswith(b"PRI "):
                    self._delegate_to_http11()
                # else: wait for more data to confirm HTTP/2 preface

    def _delegate_to_http2(self):
        """Delegate to HTTP/2 protocol handler."""
        logger.debug("Detected HTTP/2 connection (prior knowledge)")
        self.protocol_detected = True

        # Create HTTP/2 protocol handler
        self.delegated_protocol = Http2ServerProtocol(
            server=self.server,
            app=self.app,
        )

        # Initialize the delegated protocol
        if self.transport is None:
            raise RuntimeError("Transport is None during HTTP/2 delegation")
        self.delegated_protocol.connection_made(self.transport)

        # Pass buffered data to the delegated protocol
        if self.buffer:
            self.delegated_protocol.data_received(bytes(self.buffer))
            self.buffer.clear()

    def _delegate_to_http11(self):
        """Delegate to HTTP/1.1 protocol handler with HTTP/2 upgrade support."""
        logger.debug("Detected HTTP/1.1 connection")
        self.protocol_detected = True

        # Wrap the ASGI app to advertise HTTP/2 capability
        wrapped_app = wrap_with_advertisements(
            app=self.app,
            server=self.server,
            advertise_http2=True,
            advertise_http3=False,
        )

        # Create HTTP/1.1 protocol handler
        self.delegated_protocol = HTTP11ServerProtocol(
            server=self.server,
            app=wrapped_app,
            state=self.state,
            ssl=self.ssl,
        )

        # Initialize the delegated protocol
        if self.transport is None:
            raise RuntimeError("Transport is None during HTTP/1.1 delegation")
        self.delegated_protocol.connection_made(self.transport)

        # Pass buffered data to the delegated protocol
        if self.buffer:
            self.delegated_protocol.data_received(bytes(self.buffer))
            self.buffer.clear()

    @override
    def connection_lost(self, exc):
        """Handle connection loss.

        Args:
            exc: The exception that caused the connection to be lost, if any.
        """
        if self.delegated_protocol:
            self.delegated_protocol.connection_lost(exc)
        elif self.transport:
            self.transport.close()
        logger.debug(f"Connection lost: {exc}")

    @override
    def eof_received(self):
        """Handle EOF reception."""
        if self.delegated_protocol:
            return self.delegated_protocol.eof_received()
        return super().eof_received()
