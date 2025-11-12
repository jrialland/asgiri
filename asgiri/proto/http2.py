"""HTTP/2 protocol implementation for ASGIRI server."""

import asyncio
import logging
from typing import Any, cast, override

import h2.connection
import h2.events
import rfc3986  # type: ignore
from asgiref.typing import (ASGI3Application, ASGIApplication,
                            ASGIReceiveCallable, ASGISendCallable,
                            HTTPRequestEvent, HTTPResponseBodyEvent,
                            HTTPResponseStartEvent,
                            HTTPResponseTrailersEvent, HTTPScope,
                            WebSocketScope)

from ..exceptions import ConnectionAbortedError
from .websocket import WebSocketProtocol


class Receiver:
    """ASGI receive callable for HTTP/2 requests."""

    def __init__(self) -> None:
        """Initialize the Receiver."""
        self.messages: asyncio.Queue[HTTPRequestEvent] = asyncio.Queue(maxsize=32)

    async def __call__(self) -> HTTPRequestEvent:
        message = await self.messages.get()
        return message


class Sender:
    """ASGI send callable for HTTP/2 responses."""

    def __init__(
        self,
        conn: h2.connection.H2Connection,
        transport: asyncio.Transport,
        stream_id: int,
    ):
        """Initialize the Sender with an h2 Connection, transport, and stream ID.
        Args:
            conn: The h2 Connection object.
            transport: The transport to write data to.
            stream_id: The HTTP/2 stream ID.
        """
        self.conn = conn
        self.transport = transport
        self.stream_id = stream_id
        self.ended = False

    async def __call__(
        self,
        message: (
            HTTPResponseStartEvent | HTTPResponseBodyEvent | HTTPResponseTrailersEvent
        ),
    ) -> None:
        if self.ended:
            raise RuntimeError("Cannot send messages after response has ended.")

        try:
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.get("headers", [])
                response_headers = [(b":status", str(status_code).encode())] + list(
                    headers
                )
                self.conn.send_headers(
                    stream_id=self.stream_id,
                    headers=response_headers,
                    end_stream=False,
                )
                self.transport.write(self.conn.data_to_send())
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                self.conn.send_data(
                    stream_id=self.stream_id,
                    data=body,
                    end_stream=not more_body,
                )
                self.transport.write(self.conn.data_to_send())
                if not more_body:
                    self.ended = True
            elif message["type"] == "http.response.trailers":
                trailers = message.get("headers", [])
                self.conn.send_headers(
                    stream_id=self.stream_id,
                    headers=list(trailers),
                    end_stream=True,
                )
                self.transport.write(self.conn.data_to_send())
                self.ended = True
        except Exception as e:
            # Stream may have been reset by client or connection closed
            logging.getLogger(__name__).warning(
                f"Error sending on stream {self.stream_id}: {e}"
            )
            self.ended = True
            raise


class UpgradeStreamSender:
    """ASGI send callable for HTTP/2 upgrade stream (stream 1).

    This sender handles stream 1 specially since it's created from an HTTP/1.1
    upgrade and needs to work around h2 library's stream creation constraints.
    """

    def __init__(
        self,
        conn: h2.connection.H2Connection,
        transport: asyncio.Transport,
        stream_id: int,
    ):
        """Initialize the UpgradeStreamSender.
        Args:
            conn: The h2 Connection object.
            transport: The transport to write data to.
            stream_id: The HTTP/2 stream ID (must be 1).
        """
        assert stream_id == 1, "UpgradeStreamSender is only for stream 1"
        self.conn = conn
        self.transport = transport
        self.stream_id = stream_id
        self.ended = False
        self._headers_sent = False

    async def __call__(
        self,
        message: (
            HTTPResponseStartEvent | HTTPResponseBodyEvent | HTTPResponseTrailersEvent
        ),
    ) -> None:
        if self.ended:
            raise RuntimeError("Cannot send messages after response has ended.")

        try:
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = message.get("headers", [])
                response_headers = [(b":status", str(status_code).encode())] + list(
                    headers
                )

                # Manually create stream 1 if it doesn't exist
                if self.stream_id not in self.conn.streams:
                    # We need to manually add stream 1 to the connection
                    # This is a workaround for the h2 library's stream management
                    from h2.stream import H2Stream, StreamInputs

                    stream = H2Stream(
                        stream_id=self.stream_id,
                        config=self.conn.config,
                        inbound_window_size=self.conn.local_settings.initial_window_size,
                        outbound_window_size=self.conn.remote_settings.initial_window_size,
                    )
                    # Don't transition state - let send_headers do it
                    self.conn.streams[self.stream_id] = stream

                self.conn.send_headers(
                    stream_id=self.stream_id,
                    headers=response_headers,
                    end_stream=False,
                )
                self.transport.write(self.conn.data_to_send())
                self._headers_sent = True

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                more_body = message.get("more_body", False)
                self.conn.send_data(
                    stream_id=self.stream_id,
                    data=body,
                    end_stream=not more_body,
                )
                self.transport.write(self.conn.data_to_send())
                if not more_body:
                    self.ended = True
            elif message["type"] == "http.response.trailers":
                trailers = message.get("headers", [])
                self.conn.send_headers(
                    stream_id=self.stream_id,
                    headers=list(trailers),
                    end_stream=True,
                )
                self.transport.write(self.conn.data_to_send())
                self.ended = True
        except Exception as e:
            # Stream may have been reset by client or connection closed
            logging.getLogger(__name__).warning(
                f"Error sending on upgrade stream {self.stream_id}: {e}"
            )
            self.ended = True
            raise


class StreamState:
    """Holds state for an individual HTTP/2 stream."""

    def __init__(self, stream_id: int):
        self.stream_id = stream_id
        self.receiver = Receiver()
        self.sender: Sender | None = None  # Will be set later


class Http2ServerProtocol(asyncio.Protocol):

    def __init__(
        self,
        server: tuple[str, int],
        app: ASGIApplication,
        state: dict[str, Any] | None = None,
        advertise_http3: bool = True,
    ):
        super().__init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.server = server
        self.conn: h2.connection.H2Connection | None = None
        self.transport: asyncio.Transport | None = None
        self.client: tuple[str, int] | None = None
        self.advertise_http3 = advertise_http3
        self.state = state or {}

        # Wrap app to add HTTP/3 advertisement if enabled
        if advertise_http3:
            self.app = self._wrap_with_http3_advertisement(app)
        else:
            self.app = app

        self.stream_states: dict[int, StreamState] = {}

    def _write_to_transport(self) -> None:
        """Safely write data to transport with checks."""
        if self.transport and self.conn and not self.transport.is_closing():
            data = self.conn.data_to_send()
            if data:
                self.transport.write(data)

    @override
    def connection_made(self, transport):
        self.transport = transport
        config = h2.config.H2Configuration(client_side=False)
        self.conn = h2.connection.H2Connection(config=config)
        self.conn.initiate_connection()
        self._write_to_transport()
        peername = transport.get_extra_info("peername")
        if peername and len(peername) > 1:
            self.client = (peername[0], peername[1])
        else:
            self.client = None

    @override
    def connection_lost(self, exc):
        self.transport = None
        if exc:
            raise ConnectionAbortedError("Connection lost") from exc

    @override
    def data_received(self, data: bytes):
        if not self.conn:
            return
        try:
            events = self.conn.receive_data(data)
        except Exception as e:
            self.logger.error(f"Error receiving data: {e}")
            return

        self._write_to_transport()

        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                # Check if this is a WebSocket CONNECT request (RFC 8441)
                is_websocket = self._is_websocket_connect(event)

                if is_websocket:
                    # Handle WebSocket CONNECT
                    self._handle_websocket_connect(event)
                    continue

                scope = self._build_scope(event)
                stream_state = self.stream_states[event.stream_id] = StreamState(
                    event.stream_id
                )
                if self.conn and self.transport:
                    stream_state.sender = Sender(
                        conn=self.conn,
                        transport=self.transport,
                        stream_id=event.stream_id,
                    )
                # Send initial empty body message to start the request
                asyncio.create_task(
                    stream_state.receiver.messages.put(
                        {
                            "type": "http.request",
                            "body": b"",
                            "more_body": True,
                        }
                    )
                )
                asyncio.create_task(self._handle_request(event, scope, stream_state))
            elif isinstance(event, h2.events.DataReceived):
                stream_state_temp2 = self.stream_states.get(event.stream_id)
                if stream_state_temp2:
                    # Acknowledge the data for flow control
                    if self.conn:
                        self.conn.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                        self._write_to_transport()

                    # Check if this is a WebSocket stream
                    ws_protocol = getattr(stream_state_temp2, "ws_protocol", None)
                    if ws_protocol:
                        # Pass data to WebSocket handler
                        ws_protocol.data_received(event.data)
                    else:
                        # Regular HTTP/2 data
                        asyncio.create_task(
                            stream_state_temp2.receiver.messages.put(
                                {
                                    "type": "http.request",
                                    "body": event.data,
                                    "more_body": True,
                                }
                            )
                        )
            elif isinstance(event, h2.events.StreamEnded):
                stream_state_temp = self.stream_states.get(event.stream_id)
                if stream_state_temp:
                    asyncio.create_task(
                        stream_state_temp.receiver.messages.put(
                            {
                                "type": "http.request",
                                "body": b"",
                                "more_body": False,
                            }
                        )
                    )
            elif isinstance(event, h2.events.StreamReset):
                # Clean up stream state on reset
                self.stream_states.pop(event.stream_id, None)
            elif isinstance(event, h2.events.WindowUpdated):
                # Flow control window updated - no action needed
                # The h2 library handles this automatically
                pass
            elif isinstance(event, h2.events.ConnectionTerminated):
                # Clean up all stream states
                self.stream_states.clear()
                if self.transport:
                    self.transport.close()
            else:
                self.logger.debug(f"Unhandled event: {event}")

    @override
    def eof_received(self):
        # Handle end of file
        pass

    def _build_scope(self, event: h2.events.RequestReceived) -> HTTPScope:
        # Build and return the ASGI scope from the event
        # Extract pseudo-headers and regular headers
        raw_path = b"/"
        method = "GET"
        scheme = "https"
        authority = None
        headers = []

        for name, value in event.headers:
            if name == b":path":
                raw_path = value
            elif name == b":method":
                method = value.decode()
            elif name == b":scheme":
                scheme = value.decode()
            elif name == b":authority":
                authority = value.decode()
            else:
                # Only include non-pseudo-headers in the headers list
                headers.append((name, value))

        url: rfc3986.ParseResult = rfc3986.urlparse(raw_path.decode())
        scope: HTTPScope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.2"},
            "http_version": "2",
            "method": method,
            "path": url.path or "/",
            "raw_path": raw_path,
            "query_string": url.query.encode() if url.query else b"",
            "scheme": scheme,
            "root_path": "",
            "headers": headers,
            "client": self.client,
            "server": self.server,
            "state": {},
            "extensions": {},
        }
        return scope

    def _handle_upgrade_stream_1(
        self,
        headers: list[tuple[bytes, bytes]],
        client: tuple[str, int] | None,
        server: tuple[str, int],
        ssl: bool,
    ):
        """Handle the HTTP/1.1 request that was upgraded to HTTP/2 as stream 1.

        Per RFC 7540 Section 3.2, the HTTP/1.1 request sent prior to upgrade
        is assigned stream identifier 1 with default priority values.

        Since we cannot use the h2 library to create server-initiated stream 1,
        we manually create the stream state and handle it specially.

        Args:
            headers: The HTTP/2 headers converted from HTTP/1.1 request.
            client: The client address.
            server: The server address.
            ssl: Whether the connection uses SSL.
        """
        stream_id = 1

        # Build scope from headers
        raw_path = b"/"
        method = "GET"
        scheme = "https" if ssl else "http"
        regular_headers = []

        for name, value in headers:
            if name == b":path":
                raw_path = value
            elif name == b":method":
                method = value.decode()
            elif name == b":scheme":
                scheme = value.decode()
            elif name == b":authority":
                pass  # Already in headers if needed
            else:
                regular_headers.append((name, value))

        url: rfc3986.ParseResult = rfc3986.urlparse(raw_path.decode())
        scope: HTTPScope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.2"},
            "http_version": "2",
            "method": method,
            "path": url.path or "/",
            "raw_path": raw_path,
            "query_string": url.query.encode() if url.query else b"",
            "scheme": scheme,
            "root_path": "",
            "headers": regular_headers,
            "client": client,
            "server": server,
            "state": {},
            "extensions": {},
        }

        # Create stream state for stream 1
        # We use a special sender that bypasses h2's stream creation
        stream_state = self.stream_states[stream_id] = StreamState(stream_id)

        # Create the stream manually if it doesn't exist
        if self.conn and stream_id not in self.conn.streams:
            from h2.stream import H2Stream, StreamInputs

            stream = H2Stream(
                stream_id=stream_id,
                config=self.conn.config,
                inbound_window_size=self.conn.local_settings.initial_window_size,
                outbound_window_size=self.conn.remote_settings.initial_window_size,
            )
            # Mark that the stream has received headers (from the HTTP/1.1 upgrade request)
            stream.state_machine.process_input(StreamInputs.RECV_HEADERS)
            self.conn.streams[stream_id] = stream

        if self.conn and self.transport:
            stream_state.sender = Sender(
                conn=self.conn, transport=self.transport, stream_id=stream_id
            )

        # Send initial empty body message (upgrade request has no body)
        asyncio.create_task(
            stream_state.receiver.messages.put(
                {
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,  # Upgrade request is complete
                }
            )
        )

        # Handle the request
        asyncio.create_task(self._handle_stream_1_request(scope, stream_state))

    async def _handle_stream_1_request(
        self,
        scope: HTTPScope,
        stream_state: StreamState,
    ):
        """Handle the upgraded stream 1 request.

        Args:
            scope: The ASGI scope.
            stream_state: The stream state.
        """
        try:
            # App is already guaranteed to be single callable by server.py
            assert stream_state.sender is not None, "Sender must be set before calling app"
            app = cast(ASGI3Application, self.app)
            await app(
                scope,
                cast(ASGIReceiveCallable, stream_state.receiver),
                cast(ASGISendCallable, stream_state.sender),
            )
        except Exception as e:
            self.logger.exception(f"Error handling stream 1 request: {e}")
            # Send error response if not already sent
            if stream_state.sender and not stream_state.sender.ended:
                try:
                    await stream_state.sender(
                        HTTPResponseStartEvent(
                            type="http.response.start",
                            status=500,
                            headers=[(b"content-length", b"0")],
                            trailers=False,
                        )
                    )
                    await stream_state.sender(
                        HTTPResponseBodyEvent(
                            type="http.response.body",
                            body=b"",
                            more_body=False,
                        )
                    )
                except:
                    pass
        finally:
            # Clean up stream state
            self.stream_states.pop(stream_state.stream_id, None)

    async def _handle_request(
        self,
        event: h2.events.RequestReceived,
        scope: HTTPScope,
        stream_state: StreamState,
    ):
        try:
            # App is already guaranteed to be single callable by server.py
            assert stream_state.sender is not None, "Sender must be set before calling app"
            app = cast(ASGI3Application, self.app)
            await app(
                scope,
                cast(ASGIReceiveCallable, stream_state.receiver),
                cast(ASGISendCallable, stream_state.sender),
            )
        except Exception as e:
            self.logger.exception(f"Error handling request on stream {event.stream_id}")
        finally:
            # Clean up stream state after request is handled
            self.stream_states.pop(event.stream_id, None)

    def _is_websocket_connect(self, event: h2.events.RequestReceived) -> bool:
        """Check if this is a WebSocket CONNECT request (RFC 8441).

        Args:
            event: The RequestReceived event.

        Returns:
            True if this is a WebSocket CONNECT request.
        """
        headers_dict = {name: value for name, value in event.headers}

        # Check for CONNECT method with :protocol = websocket
        method = headers_dict.get(b":method", b"")
        protocol = headers_dict.get(b":protocol", b"")

        return method == b"CONNECT" and protocol == b"websocket"

    def _handle_websocket_connect(self, event: h2.events.RequestReceived):
        """Handle a WebSocket CONNECT request (RFC 8441).

        Args:
            event: The RequestReceived event.
        """
        assert self.transport is not None

        # Extract headers
        headers_dict = {name: value for name, value in event.headers}
        raw_path = headers_dict.get(b":path", b"/")
        scheme = headers_dict.get(b":scheme", b"https").decode()
        authority = headers_dict.get(b":authority", b"")

        # Regular headers (non-pseudo)
        headers = [
            (name, value) for name, value in event.headers if not name.startswith(b":")
        ]

        # Extract subprotocols if present
        subprotocols = []
        ws_protocol_header = headers_dict.get(b"sec-websocket-protocol")
        if ws_protocol_header:
            subprotocols = [p.strip().decode() for p in ws_protocol_header.split(b",")]

        # Parse URL
        url: rfc3986.ParseResult = rfc3986.urlparse(raw_path.decode())

        # Create WebSocket scope
        scope: WebSocketScope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "2",
            "scheme": "wss" if scheme == "https" else "ws",
            "path": url.path or "/",
            "raw_path": raw_path,
            "query_string": url.query.encode() if url.query else b"",
            "root_path": "",
            "headers": headers,
            "client": self.client,
            "server": self.server,
            "subprotocols": subprotocols,
            "state": self.state,
            "extensions": {"websocket.http.response": {}},
        }

        # Send 200 response to accept the CONNECT
        if self.conn:
            self.conn.send_headers(
                stream_id=event.stream_id,
                headers=[(b":status", b"200")],
            )
            self._write_to_transport()

        # Create a custom transport wrapper for this stream
        if self.conn and self.transport:
            stream_transport = HTTP2StreamTransport(
                self.conn, self.transport, event.stream_id, self
            )

            # Create WebSocket protocol handler
            ws_protocol = WebSocketProtocol(stream_transport, scope, self.app)  # type: ignore[arg-type]

            # Store stream as WebSocket
            stream_state = StreamState(event.stream_id)
            setattr(stream_state, "ws_protocol", ws_protocol)
            self.stream_states[event.stream_id] = stream_state

            # Start WebSocket handling
            asyncio.create_task(ws_protocol.handle())

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


class HTTP2StreamTransport:
    """Transport wrapper for HTTP/2 streams to work with WebSocket protocol.

    This provides a transport-like interface for a single HTTP/2 stream,
    allowing the WebSocket protocol handler to send data through the stream.
    """

    def __init__(
        self,
        conn: h2.connection.H2Connection,
        transport: asyncio.Transport,
        stream_id: int,
        protocol: "Http2ServerProtocol",
    ):
        """Initialize the stream transport.

        Args:
            conn: The h2 connection.
            transport: The underlying transport.
            stream_id: The stream ID.
            protocol: The HTTP/2 protocol instance.
        """
        self.conn = conn
        self.transport = transport
        self.stream_id = stream_id
        self.protocol = protocol
        self.closed = False

    def write(self, data: bytes):
        """Write data to the stream.

        Args:
            data: The data to write.
        """
        if self.closed:
            return

        # Send data through HTTP/2 stream
        self.conn.send_data(self.stream_id, data)
        self.protocol._write_to_transport()

    def close(self):
        """Close the stream."""
        if self.closed:
            return

        self.closed = True

        # End the stream
        self.conn.end_stream(self.stream_id)
        self.protocol._write_to_transport()

        # Clean up stream state
        self.protocol.stream_states.pop(self.stream_id, None)

    def get_extra_info(self, name: str, default=None):
        """Get extra info from the underlying transport.

        Args:
            name: The info name.
            default: The default value if not found.

        Returns:
            The info value.
        """
        return self.transport.get_extra_info(name, default)
