"""
HTTP/3 protocol implementation using aioquic.

This module implements HTTP/3 (QUIC) support for the ASGI server.
HTTP/3 runs over UDP instead of TCP, providing multiplexed streams
with better handling of packet loss.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, cast

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import (
    DataReceived,
    H3Event,
    HeadersReceived,
    WebTransportStreamDataReceived,
)
from aioquic.quic.events import QuicEvent
from aioquic.quic.configuration import QuicConfiguration
from asgiref.typing import (
    ASGI3Application,
    ASGIApplication,
    ASGIReceiveCallable,
    ASGISendCallable,
    HTTPScope,
    WebSocketScope,
)

logger = logging.getLogger(__name__)


class HTTP3ServerProtocol(QuicConnectionProtocol):
    """
    ASGI HTTP/3 server protocol implementation using aioquic.

    This protocol handles QUIC connections and translates HTTP/3 requests
    into ASGI application calls.
    """

    def __init__(
        self,
        *args: Any,
        app: ASGIApplication,
        server: tuple[str, int],
        enable_webtransport: bool = False,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.app = app
        self.server = server
        self.enable_webtransport = enable_webtransport
        self.h3 = H3Connection(self._quic, enable_webtransport=enable_webtransport)

        # Track active streams and their state
        self.stream_handlers: dict[int, asyncio.Task] = {}
        self.stream_receivers: dict[int, asyncio.Queue] = {}
        self.stream_ended: dict[int, bool] = {}

        # Track WebTransport sessions
        self.webtransport_sessions: dict[int, asyncio.Task] = {}
        self.webtransport_stream_receivers: dict[int, asyncio.Queue] = {}
        self.webtransport_stream_ended: dict[int, bool] = {}

        # Track WebSocket connections
        self.websocket_handlers: dict[int, "HTTP3WebSocketHandler"] = {}

    def quic_event_received(self, event: QuicEvent) -> None:
        """
        Handle QUIC events and delegate to H3.

        Args:
            event: QUIC protocol event
        """
        # Process H3 events from QUIC
        for h3_event in self.h3.handle_event(event):
            self._handle_h3_event(h3_event)

    def _handle_h3_event(self, event: H3Event) -> None:
        """
        Handle HTTP/3 events.

        Args:
            event: HTTP/3 event (headers, data, etc.)
        """
        if isinstance(event, HeadersReceived):
            # New request on a stream
            self._handle_request(event)
        elif isinstance(event, DataReceived):
            stream_id = event.stream_id

            # Check if this is a WebSocket stream
            if stream_id in self.websocket_handlers:
                # Route data to WebSocket handler
                self.websocket_handlers[stream_id].data_received(event.data)
                return

            # Request body data
            if stream_id in self.stream_receivers:
                # Queue data for the receiver
                self.stream_receivers[stream_id].put_nowait(event.data)

                # Check if stream ended
                if event.stream_ended:
                    self.stream_ended[stream_id] = True
        elif isinstance(event, WebTransportStreamDataReceived):
            # WebTransport stream data
            self._handle_webtransport_stream_data(event)

    def _handle_request(self, event: HeadersReceived) -> None:
        """
        Handle a new HTTP/3 request.

        Args:
            event: HeadersReceived event containing request headers
        """
        try:
            # Check if this is a WebTransport CONNECT request
            if self.enable_webtransport and self._check_webtransport_request(
                event.headers
            ):
                self._handle_webtransport_connect(event)
                return

            # Check if this is a WebSocket CONNECT request
            if self._check_websocket_request(event.headers):
                self._handle_websocket_connect(event)
                return

            scope = self._build_scope(event)

            # Create receiver queue for this stream
            self.stream_receivers[event.stream_id] = asyncio.Queue()
            self.stream_ended[event.stream_id] = event.stream_ended

            # Create and track the handler task
            task = asyncio.create_task(self._run_asgi(scope, event.stream_id))
            self.stream_handlers[event.stream_id] = task

        except Exception as e:
            self.logger.exception(
                f"Error handling request on stream {event.stream_id}: {e}"
            )
            # Send error response
            self._send_error_response(event.stream_id, 500)

    def _check_websocket_request(self, headers: list[tuple[bytes, bytes]]) -> bool:
        """
        Check if request is a WebSocket CONNECT request.

        Per RFC 9220, WebSocket over HTTP/3 uses Extended CONNECT with:
        - :method = CONNECT
        - :protocol = websocket

        Args:
            headers: Request headers

        Returns:
            True if this is a WebSocket CONNECT request
        """
        method = None
        protocol = None

        for name, value in headers:
            if name == b":method":
                method = value
            elif name == b":protocol":
                protocol = value

        return method == b"CONNECT" and protocol == b"websocket"

    def _handle_websocket_connect(self, event: HeadersReceived) -> None:
        """
        Handle a WebSocket CONNECT request over HTTP/3.

        Per RFC 9220, this implements the Extended CONNECT handshake
        for WebSocket over HTTP/3.

        Args:
            event: HeadersReceived event for the CONNECT request
        """
        stream_id = event.stream_id

        self.logger.info(f"WebSocket connection requested on stream {stream_id}")

        try:
            # Build WebSocket scope from headers
            scope = self._build_websocket_scope(event)

            # Create WebSocket handler
            handler = HTTP3WebSocketHandler(
                stream_id=stream_id,
                scope=scope,
                app=self.app,
                h3_connection=self.h3,
                protocol=self,
            )

            self.websocket_handlers[stream_id] = handler

            # Start the handler task
            task = asyncio.create_task(handler.handle())
            self.stream_handlers[stream_id] = task

        except Exception as e:
            self.logger.exception(
                f"Error handling WebSocket connection {stream_id}: {e}"
            )
            # Send error response
            self._send_error_response(stream_id, 500)

    def _build_websocket_scope(self, event: HeadersReceived) -> WebSocketScope:
        """
        Build ASGI WebSocket scope from HTTP/3 CONNECT headers.

        Args:
            event: HeadersReceived event

        Returns:
            ASGI WebSocket scope dictionary
        """
        headers_list = []
        path = b"/"
        scheme = b"wss"  # WebSocket over HTTP/3 is always secure
        authority = b""
        subprotocols = []

        for name, value in event.headers:
            if name == b":path":
                path = value
            elif name == b":scheme":
                # Map https -> wss, http -> ws
                if value == b"https":
                    scheme = b"wss"
                elif value == b"http":
                    scheme = b"ws"
            elif name == b":authority":
                authority = value
            elif name == b"sec-websocket-protocol":
                # Parse subprotocols (comma-separated)
                subprotocols = [
                    proto.strip().decode("latin1")
                    for proto in value.split(b",")
                ]
            elif name not in (b":method", b":protocol"):
                # Regular header (skip pseudo-headers)
                headers_list.append((name, value))

        # Parse path and query string
        if b"?" in path:
            path_part, query_string = path.split(b"?", 1)
        else:
            path_part = path
            query_string = b""

        # Build ASGI WebSocket scope
        scope: WebSocketScope = {
            "type": "websocket",
            "asgi": {
                "version": "3.0",
                "spec_version": "2.3",
            },
            "http_version": "3",
            "scheme": scheme.decode("latin1"),
            "path": path_part.decode("latin1"),
            "raw_path": path_part,
            "query_string": query_string,
            "root_path": "",
            "headers": headers_list,
            "server": self.server,
            "client": None,
            "subprotocols": subprotocols,
        }

        return scope


    def _handle_webtransport_connect(self, event: HeadersReceived) -> None:
        """
        Handle a WebTransport CONNECT request.

        Args:
            event: HeadersReceived event for the CONNECT request
        """
        session_id = event.stream_id

        self.logger.info(f"WebTransport session requested on stream {session_id}")

        try:
            # Build a custom WebTransport scope
            scope = self._build_webtransport_scope(event)

            # Create and track the session handler task
            task = asyncio.create_task(
                self._run_webtransport_session(scope, session_id)
            )
            self.webtransport_sessions[session_id] = task

        except Exception as e:
            self.logger.exception(
                f"Error handling WebTransport session {session_id}: {e}"
            )
            # Send error response
            self._send_error_response(session_id, 500)

    def _build_webtransport_scope(self, event: HeadersReceived) -> dict[str, Any]:
        """
        Build ASGI scope for WebTransport session.

        Args:
            event: HeadersReceived event

        Returns:
            Custom WebTransport scope dictionary
        """
        headers_list = []
        path = b"/"
        scheme = b"https"
        authority = b""

        for name, value in event.headers:
            if name == b":path":
                path = value
            elif name == b":scheme":
                scheme = value
            elif name == b":authority":
                authority = value
            elif name not in (b":method", b":protocol"):
                # Regular header (skip pseudo-headers)
                headers_list.append((name, value))

        # Parse path and query string
        if b"?" in path:
            path_part, query_string = path.split(b"?", 1)
        else:
            path_part = path
            query_string = b""

        # Build custom WebTransport scope
        # Note: This is not standardized in ASGI yet
        scope = {
            "type": "webtransport",
            "asgi": {
                "version": "3.0",
                "spec_version": "2.3",
            },
            "http_version": "3",
            "scheme": scheme.decode("latin1"),
            "path": path_part.decode("latin1"),
            "raw_path": path_part,
            "query_string": query_string,
            "root_path": "",
            "headers": headers_list,
            "server": self.server,
            "client": None,
            "session_id": event.stream_id,
        }

        return scope


    def _build_scope(self, event: HeadersReceived) -> HTTPScope:
        """
        Build ASGI scope from HTTP/3 headers.

        Args:
            event: HeadersReceived event

        Returns:
            ASGI HTTP scope dictionary
        """
        headers_dict = {}
        headers_list = []

        # Parse headers and pseudo-headers
        method = b"GET"
        path = b"/"
        scheme = b"https"  # HTTP/3 always uses TLS
        authority = b""

        for name, value in event.headers:
            if name == b":method":
                method = value
            elif name == b":path":
                path = value
            elif name == b":scheme":
                scheme = value
            elif name == b":authority":
                authority = value
            else:
                # Regular header
                headers_list.append((name, value))
                headers_dict[name] = value

        # Parse path and query string
        if b"?" in path:
            path_part, query_string = path.split(b"?", 1)
        else:
            path_part = path
            query_string = b""

        # Build ASGI scope
        scope: HTTPScope = {
            "type": "http",
            "asgi": {
                "version": "3.0",
                "spec_version": "2.3",
            },
            "http_version": "3",
            "method": method.decode("latin1"),
            "scheme": scheme.decode("latin1"),
            "path": path_part.decode("latin1"),
            "raw_path": path_part,
            "query_string": query_string,
            "root_path": "",
            "headers": headers_list,
            "server": self.server,
            "client": None,  # QUIC client address would need to be extracted
            "extensions": {},
        }

        return scope

    async def _run_asgi(self, scope: HTTPScope, stream_id: int) -> None:
        """
        Run ASGI application for this stream.

        Args:
            scope: ASGI scope dictionary
            stream_id: HTTP/3 stream identifier
        """
        receiver = self._create_receiver(stream_id)
        sender = self._create_sender(stream_id)

        try:
            # App is already guaranteed to be single callable by server.py
            app = cast(ASGI3Application, self.app)
            await app(scope, receiver, sender)
        except Exception as e:
            self.logger.exception(f"Error in ASGI app for stream {stream_id}: {e}")
            # Try to send error response if headers not sent yet
            try:
                self._send_error_response(stream_id, 500)
            except Exception:
                pass
        finally:
            # Cleanup
            self.stream_handlers.pop(stream_id, None)
            self.stream_receivers.pop(stream_id, None)
            self.stream_ended.pop(stream_id, None)

    def _create_receiver(self, stream_id: int) -> ASGIReceiveCallable:
        """
        Create ASGI receive callable for this stream.

        Args:
            stream_id: HTTP/3 stream identifier

        Returns:
            ASGI receive callable
        """

        async def receive() -> dict[str, Any]:
            """
            Receive request body data.

            Returns:
                ASGI receive message
            """
            queue = self.stream_receivers.get(stream_id)
            if not queue:
                # Stream already closed
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                }

            # Check if stream has ended and queue is empty
            if self.stream_ended.get(stream_id, False) and queue.empty():
                return {
                    "type": "http.request",
                    "body": b"",
                    "more_body": False,
                }

            # Wait for data or timeout
            try:
                # Use a timeout to periodically check stream_ended
                data = await asyncio.wait_for(queue.get(), timeout=0.1)

                # Check if more data is coming
                more_body = (
                    not self.stream_ended.get(stream_id, False) or not queue.empty()
                )

                return {
                    "type": "http.request",
                    "body": data,
                    "more_body": more_body,
                }
            except asyncio.TimeoutError:
                # No data yet, check if stream ended
                if self.stream_ended.get(stream_id, False):
                    return {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                else:
                    # Keep waiting
                    return await receive()

        return cast(ASGIReceiveCallable, receive)

    def _create_sender(self, stream_id: int) -> ASGISendCallable:
        """
        Create ASGI send callable for this stream.

        Args:
            stream_id: HTTP/3 stream identifier

        Returns:
            ASGI send callable
        """
        headers_sent = False

        async def send(message: dict[str, Any]) -> None:
            """
            Send response to client.

            Args:
                message: ASGI send message
            """
            nonlocal headers_sent

            if message["type"] == "http.response.start":
                if headers_sent:
                    raise RuntimeError("Response already started")

                # Build HTTP/3 headers with :status pseudo-header
                status = message["status"]
                headers = [(b":status", str(status).encode("latin1"))]

                # Add application headers
                for name, value in message.get("headers", []):
                    headers.append((name, value))

                # Send headers
                self.h3.send_headers(stream_id, headers)
                headers_sent = True

                # Transmit to QUIC
                self.transmit()

            elif message["type"] == "http.response.body":
                if not headers_sent:
                    raise RuntimeError("Response not started")

                # Send body data
                body = message.get("body", b"")
                more_body = message.get("more_body", False)

                if body:
                    self.h3.send_data(stream_id, body, end_stream=not more_body)
                elif not more_body:
                    # End stream even if no body
                    self.h3.send_data(stream_id, b"", end_stream=True)

                # Transmit to QUIC
                self.transmit()

        return cast(ASGISendCallable, send)

    def _send_error_response(self, stream_id: int, status_code: int) -> None:
        """
        Send an error response.

        Args:
            stream_id: HTTP/3 stream identifier
            status_code: HTTP status code
        """
        try:
            headers = [
                (b":status", str(status_code).encode("latin1")),
                (b"content-type", b"text/plain"),
            ]
            self.h3.send_headers(stream_id, headers)

            body = f"{status_code} Error".encode("utf-8")
            self.h3.send_data(stream_id, body, end_stream=True)

            self.transmit()
        except Exception as e:
            self.logger.exception(f"Failed to send error response: {e}")

    def _handle_webtransport_stream_data(
        self, event: WebTransportStreamDataReceived
    ) -> None:
        """
        Handle WebTransport stream data.

        Args:
            event: WebTransportStreamDataReceived event
        """
        stream_id = event.stream_id
        session_id = event.session_id

        # Create receiver queue for this stream if it doesn't exist
        if stream_id not in self.webtransport_stream_receivers:
            self.webtransport_stream_receivers[stream_id] = asyncio.Queue()

        # Queue data for the receiver
        self.webtransport_stream_receivers[stream_id].put_nowait(event.data)

        # Mark if stream ended
        if event.stream_ended:
            self.webtransport_stream_ended[stream_id] = True

        self.logger.debug(
            f"WebTransport stream {stream_id} (session {session_id}) "
            f"received {len(event.data)} bytes, ended={event.stream_ended}"
        )

    def _check_webtransport_request(self, headers: list[tuple[bytes, bytes]]) -> bool:
        """
        Check if request is a WebTransport session request.

        Args:
            headers: Request headers

        Returns:
            True if this is a WebTransport CONNECT request
        """
        # Look for :method = CONNECT and :protocol = webtransport
        method = None
        protocol = None

        for name, value in headers:
            if name == b":method":
                method = value
            elif name == b":protocol":
                protocol = value

        return method == b"CONNECT" and protocol == b"webtransport"

    async def _run_webtransport_session(
        self, scope: dict[str, Any], session_id: int
    ) -> None:
        """
        Run a WebTransport session.

        Args:
            scope: ASGI scope dictionary (custom WebTransport scope)
            session_id: WebTransport session identifier
        """
        # Note: WebTransport is not yet standardized in ASGI.
        # This is a custom implementation that applications can use.
        # The scope type is "webtransport" with custom receive/send callables.

        receiver = self._create_webtransport_receiver(session_id)
        sender = self._create_webtransport_sender(session_id)

        try:
            app = cast(ASGI3Application, self.app)
            await app(scope, receiver, sender)
        except Exception as e:
            self.logger.exception(
                f"Error in ASGI app for WebTransport session {session_id}: {e}"
            )
        finally:
            # Cleanup
            self.webtransport_sessions.pop(session_id, None)

    def _create_webtransport_receiver(self, session_id: int) -> ASGIReceiveCallable:
        """
        Create ASGI receive callable for WebTransport session.

        Args:
            session_id: WebTransport session identifier

        Returns:
            ASGI receive callable
        """

        async def receive() -> dict[str, Any]:
            """
            Receive WebTransport events.

            Returns:
                ASGI receive message (custom format for WebTransport)
            """
            # This is a placeholder implementation
            # Real implementation would need to handle:
            # - New stream creation
            # - Stream data
            # - Stream closure
            # - Datagrams
            await asyncio.sleep(0.1)
            return {
                "type": "webtransport.disconnect",
            }

        return cast(ASGIReceiveCallable, receive)

    def _create_webtransport_sender(self, session_id: int) -> ASGISendCallable:
        """
        Create ASGI send callable for WebTransport session.

        Args:
            session_id: WebTransport session identifier

        Returns:
            ASGI send callable
        """

        async def send(message: dict[str, Any]) -> None:
            """
            Send WebTransport messages.

            Args:
                message: ASGI send message (custom format for WebTransport)
            """
            msg_type = message.get("type", "")

            if msg_type == "webtransport.accept":
                # Accept the WebTransport session
                # Send 200 OK response
                headers = [
                    (b":status", b"200"),
                    (b"sec-webtransport-http3-draft", b"draft02"),
                ]
                self.h3.send_headers(session_id, headers)
                self.transmit()

            elif msg_type == "webtransport.close":
                # Close the session
                # This would involve closing all streams and the session
                pass

            elif msg_type == "webtransport.stream.send":
                # Send data on a WebTransport stream
                stream_id = message.get("stream_id")
                data = message.get("data", b"")
                end_stream = message.get("end_stream", False)

                if stream_id is not None:
                    # Use H3Connection to send data on the stream
                    self.h3.send_data(stream_id, data, end_stream=end_stream)
                    self.transmit()

        return cast(ASGISendCallable, send)


class HTTP3WebSocketHandler:
    """
    Handles WebSocket connections over HTTP/3 streams.

    This implements RFC 9220 - Bootstrapping WebSockets with HTTP/3.
    The WebSocket protocol runs over a single HTTP/3 stream after an
    Extended CONNECT handshake.
    """

    def __init__(
        self,
        stream_id: int,
        scope: WebSocketScope,
        app: ASGIApplication,
        h3_connection: H3Connection,
        protocol: "HTTP3ServerProtocol",
    ):
        """
        Initialize WebSocket handler.

        Args:
            stream_id: HTTP/3 stream ID
            scope: ASGI WebSocket scope
            app: ASGI application
            h3_connection: H3Connection for sending data
            protocol: Parent HTTP3ServerProtocol for transmitting
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.stream_id = stream_id
        self.scope = scope
        self.app = app
        self.h3 = h3_connection
        self.protocol = protocol

        # WebSocket state
        self.accepted = False
        self.closed = False

        # Queues for ASGI communication
        self.receive_queue: asyncio.Queue = asyncio.Queue()
        self.send_queue: asyncio.Queue = asyncio.Queue()

        # Use wsproto for WebSocket frame handling
        from wsproto import ConnectionType, WSConnection

        self.ws = WSConnection(ConnectionType.SERVER)

        # Simulate handshake to get wsproto into OPEN state
        # (actual HTTP/3 handshake is done via Extended CONNECT)
        fake_request = b"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        self.ws.receive_data(fake_request)
        from wsproto.events import Request, AcceptConnection

        for event in self.ws.events():
            if isinstance(event, Request):
                break

        # Accept to transition wsproto to OPEN state
        self.ws.send(AcceptConnection())

    async def handle(self):
        """
        Handle the WebSocket connection lifecycle.
        """
        self.logger.debug(f"WebSocket handler starting for stream {self.stream_id}")

        # Send connect event to application
        await self.receive_queue.put({"type": "websocket.connect"})

        # Start application task
        app_task = asyncio.create_task(self._run_app())

        # Start sender task
        sender_task = asyncio.create_task(self._sender())

        # Wait for both to complete
        try:
            await asyncio.gather(app_task, sender_task)
        except Exception as e:
            self.logger.exception(f"Error in WebSocket handler: {e}")
        finally:
            if not self.closed:
                self._close(1006, "Connection lost")

    async def _run_app(self):
        """Run the ASGI application."""
        try:
            app = cast(ASGI3Application, self.app)
            await app(self.scope, self._receive, self._send)
            self.logger.debug("WebSocket app completed")
        except Exception as e:
            if "WebSocketDisconnect" in type(e).__name__:
                self.logger.debug(f"WebSocket disconnected: {e}")
            else:
                self.logger.exception(f"Error in WebSocket application: {e}")
                if not self.closed:
                    self._close(1011, "Internal server error")

    async def _receive(self) -> dict[str, Any]:
        """ASGI receive callable."""
        event = await self.receive_queue.get()
        self.logger.debug(f"App receiving: {event['type']}")
        return event

    async def _send(self, message: dict[str, Any]):
        """ASGI send callable."""
        self.logger.debug(f"App sending: {message['type']}")
        await self.send_queue.put(message)

    async def _sender(self):
        """Process outgoing messages from the application."""
        while not self.closed:
            try:
                message = await asyncio.wait_for(self.send_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                if message["type"] == "websocket.accept":
                    await self._handle_accept(message)
                elif message["type"] == "websocket.send":
                    await self._handle_send(message)
                elif message["type"] == "websocket.close":
                    await self._handle_close(message)
                    break
            except Exception as e:
                self.logger.exception(f"Error handling message: {e}")
                break

    async def _handle_accept(self, message: dict[str, Any]):
        """
        Handle websocket.accept from application.

        Sends 200 OK response to complete the Extended CONNECT handshake.
        """
        if self.accepted:
            raise RuntimeError("WebSocket already accepted")

        self.accepted = True

        # Build response headers
        headers = [(b":status", b"200")]

        # Add subprotocol if selected
        subprotocol = message.get("subprotocol")
        if subprotocol:
            headers.append((b"sec-websocket-protocol", subprotocol.encode("latin1")))

        # Add any additional headers
        for name, value in message.get("headers", []):
            headers.append((name, value))

        # Send 200 OK via H3
        self.h3.send_headers(self.stream_id, headers)
        self.protocol.transmit()

        self.logger.debug(f"WebSocket accepted on stream {self.stream_id}")

    async def _handle_send(self, message: dict[str, Any]):
        """Handle websocket.send from application."""
        if not self.accepted:
            raise RuntimeError("WebSocket not accepted yet")

        from wsproto.events import Message

        # Send WebSocket frame
        if "bytes" in message and message["bytes"] is not None:
            frame_data = self.ws.send(Message(data=message["bytes"]))
        elif "text" in message and message["text"] is not None:
            frame_data = self.ws.send(Message(data=message["text"]))
        else:
            raise ValueError("Message must have 'bytes' or 'text'")

        # Send via H3 stream
        self.h3.send_data(self.stream_id, frame_data, end_stream=False)
        self.protocol.transmit()

    async def _handle_close(self, message: dict[str, Any]):
        """Handle websocket.close from application."""
        code = message.get("code", 1000)
        reason = message.get("reason") or ""
        self._close(code, reason)

    def _close(self, code: int = 1000, reason: str = ""):
        """Close the WebSocket connection."""
        if self.closed:
            return

        self.closed = True

        try:
            from wsproto.events import CloseConnection

            # Send WebSocket close frame
            close_data = self.ws.send(CloseConnection(code=code, reason=reason))
            self.h3.send_data(self.stream_id, close_data, end_stream=True)
            self.protocol.transmit()
        except Exception as e:
            self.logger.warning(f"Error sending close frame: {e}")

        # Send disconnect event to app
        asyncio.create_task(
            self.receive_queue.put(
                {
                    "type": "websocket.disconnect",
                    "code": code,
                    "reason": reason,
                }
            )
        )

    def data_received(self, data: bytes):
        """
        Handle incoming WebSocket frame data.

        Args:
            data: Raw WebSocket frame bytes from the HTTP/3 stream
        """
        if self.closed:
            return

        # Feed data to wsproto
        self.ws.receive_data(data)

        # Process events
        from wsproto.events import (
            TextMessage,
            BytesMessage,
            CloseConnection,
            Ping,
            Pong,
        )

        for event in self.ws.events():
            if isinstance(event, TextMessage):
                asyncio.create_task(
                    self.receive_queue.put(
                        {
                            "type": "websocket.receive",
                            "text": event.data,
                            "bytes": None,
                        }
                    )
                )
            elif isinstance(event, BytesMessage):
                asyncio.create_task(
                    self.receive_queue.put(
                        {
                            "type": "websocket.receive",
                            "bytes": event.data,
                            "text": None,
                        }
                    )
                )
            elif isinstance(event, CloseConnection):
                self.logger.debug(f"WebSocket close from client: {event.code}")
                asyncio.create_task(
                    self.receive_queue.put(
                        {
                            "type": "websocket.disconnect",
                            "code": event.code,
                            "reason": event.reason or "",
                        }
                    )
                )
                if not self.closed:
                    # Echo close
                    close_data = self.ws.send(
                        CloseConnection(code=event.code, reason=event.reason)
                    )
                    self.h3.send_data(self.stream_id, close_data, end_stream=True)
                    self.protocol.transmit()
                    self.closed = True

            elif isinstance(event, Ping):
                # Respond to ping with pong
                pong_data = self.ws.send(event.response())
                self.h3.send_data(self.stream_id, pong_data, end_stream=False)
                self.protocol.transmit()
            elif isinstance(event, Pong):
                # Pong received - ignore for now
                pass

