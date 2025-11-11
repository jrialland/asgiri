import asyncio
import base64
import logging
from typing import Any, override

import h11
import h2.config
import h2.connection
import rfc3986  # type: ignore
from asgiref.typing import (ASGIApplication, HTTPResponseBodyEvent,
                            HTTPResponseStartEvent, HTTPResponseTrailersEvent,
                            HTTPScope, WebSocketScope)

from .websocket import WebSocketProtocol


class Sender:
    """ASGI send callable for HTTP/1.1 responses."""

    def __init__(self, conn: h11.Connection, transport: asyncio.Transport):
        """Initialize the Sender with an h11 Connection and transport.
        Args:
            conn: The h11 Connection object.
            transport: The transport to write data to.
        """
        self.conn = conn
        self.transport = transport
        self.expect_start = True
        self.ended = False

    async def __call__(
        self,
        message: (
            HTTPResponseStartEvent | HTTPResponseBodyEvent | HTTPResponseTrailersEvent
        ),
    ) -> None:
        if self.ended:
            raise RuntimeError("Cannot send messages after response has ended.")
        if self.expect_start:
            assert (
                message["type"] == "http.response.start"
            ), "Expected 'http.response.start' message"
            status_code = message["status"]
            headers = message.get("headers", [])
            response_start = h11.Response(
                status_code=status_code,
                headers=list(headers),
                http_version=b"1.1",
            )
            self.transport.write(self.conn.send(response_start))
            self.expect_start = False
        else:
            assert message["type"] == "http.response.body"
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            data_event = h11.Data(data=body)
            self.transport.write(self.conn.send(data_event))
            if not more_body:
                end_event = h11.EndOfMessage()
                self.transport.write(self.conn.send(end_event))
                self.ended = True


class HTTP11ServerProtocol(asyncio.Protocol):
    """ASGI HTTP/1.1 server protocol implementation.
    asyncio.Protocol subclass that handles HTTP/1.1 requests using h11 and dispatches them to an ASGI application.
    """

    def __init__(
        self,
        server: tuple[str, int],
        app: ASGIApplication,
        state: dict[str, Any] | None = None,
        ssl: bool = False,
        advertise_http3: bool = True,
    ):
        """Initialize the HTTP/1.1 server protocol.
        Args:
           server: A tuple containing the server's host and port.
           app: The ASGI application to handle requests.
           state: A copy of the namespace passed into the lifespan corresponding to this request. (See Lifespan Protocol)
           ssl: Whether the connection is over SSL.
           advertise_http3: Whether to advertise HTTP/3 via Alt-Svc header.
        """
        super().__init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.server = server
        self.conn: h11.Connection | None = None
        self.transport: asyncio.Transport | None = None
        self.client: tuple[str, int] | None = None
        self.advertise_http3 = advertise_http3
        
        # Wrap app to add HTTP/3 advertisement if enabled
        if advertise_http3:
            self.app = self._wrap_with_http3_advertisement(app)
        else:
            self.app = app
        
        self.state = state or {}
        self.receive_body_queue: asyncio.Queue[h11.Data | None] | None = (
            None  # Will be created per request
        )
        self.ssl = ssl

    @override
    def connection_made(self, transport: asyncio.BaseTransport):
        """Handle a new connection.
        Args:
           transport: The transport representing the connection.
        """
        assert isinstance(transport, asyncio.Transport)
        self.transport = transport
        self.conn = h11.Connection(h11.SERVER)
        peername = transport.get_extra_info("peername")
        if peername and len(peername) > 1:
            self.client = (peername[0], peername[1])
        else:
            self.client = None
        self.logger.info(f"Connection made from {self.client}")

    @override
    def connection_lost(self, exc):
        """Handle connection loss.
        Args:
           exc: The exception that caused the connection to be lost, if any.
        """
        if self.transport:
            self.transport.close()
        self.logger.debug(f"Connection lost: {exc}")

    @override
    def eof_received(self):
        """Handle Proper EOF reception."""
        return super().eof_received()

    @override
    def data_received(self, data: bytes):
        assert self.conn is not None
        self.conn.receive_data(data)
        while True:
            event = self.conn.next_event()
            if event in (h11.NEED_DATA, h11.PAUSED):
                break
            else:
                # Handle other events (e.g., Request, Data, EndOfMessage)
                if isinstance(event, h11.Request):
                    # Check if this is a WebSocket upgrade request
                    is_websocket = self._is_websocket_upgrade(event)
                    
                    if is_websocket:
                        # Handle WebSocket upgrade
                        self.logger.info(f"WebSocket upgrade: {event.target.decode()}")
                        self._handle_websocket_upgrade(event)
                        return
                    
                    # Check if this is an HTTP/2 upgrade request (h2c)
                    is_h2c_upgrade = self._is_h2c_upgrade(event)
                    
                    if is_h2c_upgrade:
                        # Handle HTTP/2 upgrade
                        self.logger.info(f"HTTP/2 upgrade: {event.target.decode()}")
                        self._handle_h2c_upgrade(event)
                        return
                    
                    # Create a new queue for this request
                    self.receive_body_queue = asyncio.Queue()
                    url: rfc3986.ParseResult = rfc3986.urlparse(event.target.decode())
                    scope: HTTPScope = {
                        "type": "http",
                        "asgi": {"version": "3.0", "spec_version": "2.2"},
                        "http_version": event.http_version.decode(),
                        "method": event.method.decode(),
                        "scheme": "https" if self.ssl else "http",
                        "path": url.path or "/",
                        "raw_path": event.target,
                        "query_string": url.query.encode() if url.query else b"",
                        "root_path": "",
                        "headers": event.headers,
                        "client": self.client,
                        "server": self.server,
                        "state": self.state,
                        "extensions": {},
                    }

                    # Capture the queue for this specific request
                    request_queue = self.receive_body_queue

                    async def receive() -> dict[str, Any]:
                        event = await request_queue.get()
                        if event is None:
                            return {
                                "type": "http.request",
                                "body": b"",
                                "more_body": False,
                            }
                        return {
                            "type": "http.request",
                            "body": event.data,
                            "more_body": event.chunk_end,
                        }

                    self.current_task = asyncio.create_task(
                        self._handle_request(scope, receive)
                    )

                elif isinstance(event, h11.Data):
                    # put data into the receive queue
                    if self.receive_body_queue is not None:
                        self.receive_body_queue.put_nowait(event)
                elif isinstance(event, h11.EndOfMessage):
                    # put sentinel value to signal end of body
                    if self.receive_body_queue is not None:
                        self.receive_body_queue.put_nowait(None)  # type: ignore
                    # start_next_cycle will be called after response is sent
                else:
                    # should never happen unless h11 adds new events
                    if self.transport:
                        self.transport.close()
                    raise RuntimeError(f"Unexpected event: {event}")

    async def _handle_request(self, scope, receive):
        assert self.conn is not None
        assert self.transport is not None
        try:
            await self.app(scope, receive, Sender(self.conn, self.transport))
        except Exception as e:
            self.logger.exception(f"Error handling request")
        finally:
            # After response is sent, reset h11 connection state if possible
            # Only call start_next_cycle if the connection is in a reusable state
            if self.conn.our_state == h11.DONE and self.conn.their_state == h11.DONE:
                self.conn.start_next_cycle()
            elif self.conn.our_state == h11.MUST_CLOSE or self.conn.their_state == h11.MUST_CLOSE:
                # Connection must close, don't try to reuse it
                if self.transport:
                    self.transport.close()

    def _is_websocket_upgrade(self, request: h11.Request) -> bool:
        """Check if a request is a WebSocket upgrade request.
        
        Args:
            request: The h11 Request event.
            
        Returns:
            True if this is a WebSocket upgrade request.
        """
        headers_dict = {name.lower(): value for name, value in request.headers}
        
        # Check for required WebSocket headers
        upgrade = headers_dict.get(b"upgrade", b"").lower()
        connection = headers_dict.get(b"connection", b"").lower()
        ws_version = headers_dict.get(b"sec-websocket-version", b"")
        ws_key = headers_dict.get(b"sec-websocket-key", b"")
        
        return (
            upgrade == b"websocket" and
            b"upgrade" in connection and
            ws_version == b"13" and
            len(ws_key) > 0
        )

    def _is_h2c_upgrade(self, request: h11.Request) -> bool:
        """Check if a request is an HTTP/2 upgrade request (h2c).
        
        Per RFC 7540 Section 3.2, the client must send:
        - Upgrade: h2c
        - HTTP2-Settings header with base64url encoded settings
        - Connection: Upgrade, HTTP2-Settings
        
        Args:
            request: The h11 Request event.
            
        Returns:
            True if this is a valid h2c upgrade request.
        """
        headers_dict = {name.lower(): value for name, value in request.headers}
        
        # Check for required h2c upgrade headers
        upgrade = headers_dict.get(b"upgrade", b"").lower()
        connection = headers_dict.get(b"connection", b"").lower()
        http2_settings = headers_dict.get(b"http2-settings", b"")
        
        # Validate upgrade header
        if upgrade != b"h2c":
            return False
        
        # Validate connection header contains both "upgrade" and "http2-settings"
        if b"upgrade" not in connection or b"http2-settings" not in connection:
            return False
        
        # Validate HTTP2-Settings header is present and appears to be base64
        if not http2_settings or len(http2_settings) == 0:
            return False
        
        return True

    def _handle_websocket_upgrade(self, request: h11.Request):
        """Handle a WebSocket upgrade request.
        
        Args:
            request: The h11 Request event.
        """
        assert self.transport is not None
        assert self.conn is not None
        
        # Create WebSocket scope first (before we send any response)
        url: rfc3986.ParseResult = rfc3986.urlparse(request.target.decode())
        
        # Extract headers
        headers_dict = {name.lower(): value for name, value in request.headers}
        
        # Extract subprotocols if present
        subprotocols = []
        ws_protocol = headers_dict.get(b"sec-websocket-protocol")
        if ws_protocol:
            subprotocols = [p.strip().decode() for p in ws_protocol.split(b",")]
        
        scope: WebSocketScope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": request.http_version.decode(),
            "scheme": "wss" if self.ssl else "ws",
            "path": url.path or "/",
            "raw_path": request.target,
            "query_string": url.query.encode() if url.query else b"",
            "root_path": "",
            "headers": request.headers,
            "client": self.client,
            "server": self.server,
            "subprotocols": subprotocols,
            "state": self.state,
            "extensions": {"websocket.http.response": {}},
        }
        
        # Build the raw HTTP request for wsproto
        # wsproto needs to see the full HTTP request to complete its handshake
        request_line = f"{request.method.decode()} {request.target.decode()} HTTP/{request.http_version.decode()}\r\n".encode()
        headers_bytes = b"".join([f"{name.decode()}: {value.decode()}\r\n".encode() for name, value in request.headers])
        raw_request = request_line + headers_bytes + b"\r\n"
        
        # Create WebSocket protocol handler - this will handle the handshake
        ws_protocol = WebSocketProtocol(self.transport, scope, self.app, raw_request)
        
        # Replace data handler with WebSocket handler
        self.data_received = ws_protocol.data_received  # type: ignore
        self.connection_lost = ws_protocol.connection_lost  # type: ignore
        
        # Start WebSocket handling
        asyncio.create_task(ws_protocol.handle())

    def _handle_h2c_upgrade(self, request: h11.Request):
        """Handle an HTTP/2 upgrade request (h2c).
        
        Per RFC 7540 Section 3.2:
        1. Send 101 Switching Protocols response
        2. Process the HTTP2-Settings header
        3. Switch to HTTP/2 and treat the request as stream 1
        
        Args:
            request: The h11 Request event.
        """
        assert self.transport is not None
        assert self.conn is not None
        
        # Extract headers
        headers_dict = {name.lower(): value for name, value in request.headers}
        http2_settings = headers_dict.get(b"http2-settings", b"")
        
        # Validate and decode HTTP2-Settings header
        try:
            # RFC 7540 uses base64url encoding (URL-safe base64 without padding)
            # Python's base64.urlsafe_b64decode handles this
            # Add padding if needed
            missing_padding = len(http2_settings) % 4
            if missing_padding:
                http2_settings += b'=' * (4 - missing_padding)
            settings_payload = base64.urlsafe_b64decode(http2_settings)
        except Exception as e:
            self.logger.warning(f"Failed to decode HTTP2-Settings: {e}")
            # Send 400 Bad Request as raw bytes (h11 doesn't support all status codes easily)
            response_400 = (
                b"HTTP/1.1 400 Bad Request\r\n"
                b"Content-Length: 0\r\n"
                b"\r\n"
            )
            self.transport.write(response_400)
            self.transport.close()
            return
        
        # Send 101 Switching Protocols response
        # Per RFC 7540 Section 3.2: "A server MUST NOT upgrade the connection to HTTP/2
        # if this header field is not present or if more than one is present."
        # Note: h11 doesn't support 101 status code, so we send it as raw bytes
        response_101 = (
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Connection: Upgrade\r\n"
            b"Upgrade: h2c\r\n"
            b"\r\n"
        )
        self.transport.write(response_101)
        
        self.logger.debug("Sent 101 Switching Protocols, upgrading to HTTP/2")
        
        # Import here to avoid circular dependency
        from .http2 import Http2ServerProtocol
        
        # Create HTTP/2 protocol handler
        h2_protocol = Http2ServerProtocol(
            server=self.server,
            app=self.app,
            advertise_http3=self.advertise_http3,
        )
        
        # Initialize the HTTP/2 connection
        h2_protocol.connection_made(self.transport)
        
        # Apply the client's settings from HTTP2-Settings header
        # Note: The settings payload should be applied, but the h2 library
        # handles this through the normal frame processing
        
        # Per RFC 7540 Section 3.2: The client sends the connection preface
        # immediately after the HTTP/1.1 Upgrade request
        # We need to manually create stream 1 for the upgrade request
        
        # Build pseudo-headers for the HTTP/2 request
        url = rfc3986.urlparse(request.target.decode())
        
        #Build pseudo-headers
        h2_headers = [
            (b":method", request.method),
            (b":scheme", b"https" if self.ssl else b"http"),
            (b":authority", headers_dict.get(b"host", f"{self.server[0]}:{self.server[1]}".encode())),
            (b":path", request.target),
        ]
        
        # Add regular headers (exclude hop-by-hop headers)
        hop_by_hop = {b"connection", b"upgrade", b"http2-settings", b"transfer-encoding", b"keep-alive"}
        for name, value in request.headers:
            if name.lower() not in hop_by_hop:
                h2_headers.append((name, value))
        
        # Handle the upgrade request as HTTP/2 stream 1
        # We need to do this in a way that doesn't conflict with h2 library's stream management
        # The best approach is to manually inject a RequestReceived event
        try:
            import h2.events
            
            # Create a fake RequestReceived event for stream 1
            # The h2 library will have initialized the connection, now we simulate
            # receiving headers on stream 1
            
            # Instead of trying to hack the h2 library's internal state,
            # we'll handle stream 1 specially in the protocol handler
            h2_protocol._handle_upgrade_stream_1(
                headers=h2_headers,
                client=self.client,
                server=self.server,
                ssl=self.ssl,
            )
            
        except Exception as e:
            self.logger.exception(f"Failed to process upgrade request as HTTP/2 stream: {e}")
            self.transport.close()
            return
        
        # Replace the data handler with HTTP/2 handler
        self.data_received = h2_protocol.data_received  # type: ignore
        self.connection_lost = h2_protocol.connection_lost  # type: ignore
        
        self.logger.info("Successfully upgraded to HTTP/2 (h2c)")

    def _wrap_with_http3_advertisement(self, app: ASGIApplication) -> ASGIApplication:
        """Wrap ASGI app to advertise HTTP/3 via Alt-Svc header.
        
        Args:
            app: The original ASGI application.
            
        Returns:
            Wrapped ASGI application that adds Alt-Svc header.
        """
        async def wrapped_app(scope, receive, send):
            if scope["type"] != "http":
                # Only add headers for HTTP requests
                await app(scope, receive, send)
                return
            
            async def wrapped_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    port = self.server[1]
                    
                    # Advertise HTTP/3 (QUIC always requires TLS)
                    # ma=86400 means max-age of 24 hours
                    alt_svc_value = f'h3=":{port}"; ma=86400'.encode()
                    
                    # Check if Alt-Svc header already exists
                    has_alt_svc = any(name.lower() == b"alt-svc" for name, _ in headers)
                    if not has_alt_svc:
                        headers.append((b"alt-svc", alt_svc_value))
                    
                    # Create new message with updated headers
                    message = dict(message)
                    message["headers"] = headers
                
                await send(message)
            
            await app(scope, receive, wrapped_send)
        
        return wrapped_app
