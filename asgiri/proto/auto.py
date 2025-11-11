"""Protocol auto-detection and switching for HTTP/1.1 and HTTP/2.

This module implements a protocol switcher that can:
1. Use ALPN (Application-Layer Protocol Negotiation) for TLS connections
2. Detect HTTP/2 "prior knowledge" connections (h2c - HTTP/2 over cleartext)
3. Handle HTTP/1.1 requests with automatic protocol detection
4. Automatically advertise HTTP/2 and HTTP/3 capability
5. Delegate to appropriate protocol handler
"""

import asyncio
import logging
from typing import Any, override

from asgiref.typing import ASGIApplication

from .http2 import Http2ServerProtocol
from .http11 import HTTP11ServerProtocol

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
        app: ASGIApplication,
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
        self.logger = logging.getLogger(self.__class__.__name__)
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
        assert isinstance(transport, asyncio.Transport)
        self.transport = transport

        # For SSL/TLS connections, check ALPN negotiation result
        ssl_object = transport.get_extra_info("ssl_object")
        if ssl_object:
            try:
                alpn_protocol = ssl_object.selected_alpn_protocol()
                if alpn_protocol:
                    self.logger.info(f"ALPN negotiated protocol: {alpn_protocol}")

                    # Delegate immediately based on ALPN result
                    if alpn_protocol == "h2":
                        self._delegate_to_http2()
                        return
                    elif alpn_protocol == "http/1.1":
                        self._delegate_to_http11()
                        return
                    else:
                        self.logger.warning(
                            f"Unknown ALPN protocol: {alpn_protocol}, falling back to detection"
                        )
            except Exception as e:
                self.logger.debug(f"Could not retrieve ALPN protocol: {e}")

        self.logger.debug(f"Connection made, waiting for protocol detection")

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
        self.logger.info("Detected HTTP/2 connection (prior knowledge)")
        self.protocol_detected = True

        # Create HTTP/2 protocol handler
        self.delegated_protocol = Http2ServerProtocol(
            server=self.server,
            app=self.app,
        )

        # Initialize the delegated protocol
        assert self.transport is not None
        self.delegated_protocol.connection_made(self.transport)

        # Pass buffered data to the delegated protocol
        if self.buffer:
            self.delegated_protocol.data_received(bytes(self.buffer))
            self.buffer.clear()

    def _delegate_to_http11(self):
        """Delegate to HTTP/1.1 protocol handler with HTTP/2 upgrade support."""
        self.logger.info("Detected HTTP/1.1 connection")
        self.protocol_detected = True

        # Wrap the ASGI app to advertise HTTP/2 capability
        wrapped_app = self._create_http2_advertising_wrapper(self.app)

        # Create HTTP/1.1 protocol handler
        self.delegated_protocol = HTTP11ServerProtocol(
            server=self.server,
            app=wrapped_app,
            state=self.state,
            ssl=self.ssl,
        )

        # Initialize the delegated protocol
        assert self.transport is not None
        self.delegated_protocol.connection_made(self.transport)

        # Pass buffered data to the delegated protocol
        if self.buffer:
            self.delegated_protocol.data_received(bytes(self.buffer))
            self.buffer.clear()

    def _create_http2_advertising_wrapper(
        self, app: ASGIApplication
    ) -> ASGIApplication:
        """Wrap the ASGI app to advertise HTTP/2 and HTTP/3 capability via Alt-Svc header.

        Args:
            app: The original ASGI application.

        Returns:
            A wrapped ASGI application that adds Alt-Svc header to responses.
        """

        async def wrapped_app(scope, receive, send):
            """Wrapper that adds Alt-Svc header to advertise HTTP/2 and HTTP/3 support."""

            async def wrapped_send(message):
                if message["type"] == "http.response.start":
                    # Add Alt-Svc header to advertise h2c and h3 support
                    headers = list(message.get("headers", []))

                    # Check if Alt-Svc header already exists
                    has_alt_svc = any(name.lower() == b"alt-svc" for name, _ in headers)

                    if not has_alt_svc:
                        # Advertise both h2c (HTTP/2 cleartext) and h3 (HTTP/3) on the same port
                        port = self.server[1]
                        # Combine h2c and h3 advertisements
                        alt_svc_value = (
                            f'h2c=":{port}", h3=":{port}"; ma=86400'.encode()
                        )
                        headers.append((b"alt-svc", alt_svc_value))

                    # Create modified message with updated headers
                    message = dict(message)
                    message["headers"] = headers

                await send(message)

            await app(scope, receive, wrapped_send)

        return wrapped_app

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
        self.logger.debug(f"Connection lost: {exc}")

    @override
    def eof_received(self):
        """Handle EOF reception."""
        if self.delegated_protocol:
            return self.delegated_protocol.eof_received()
        return super().eof_received()
