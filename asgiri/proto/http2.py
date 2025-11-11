import asyncio
from typing import Any, override
import h2.connection
import h2.events
import logging
import rfc3986  # type: ignore
from asgiref.typing import (
    ASGIApplication,
    HTTPRequestEvent,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    HTTPResponseTrailersEvent,
    HTTPScope,
    WebSocketScope,
)
from ..exceptions import ConnectionAbortedError
from .websocket import WebSocketProtocol


class Receiver:
    """ASGI receive callable for HTTP/2 requests."""

    def __init__(self):
        """Initialize the Receiver."""
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)

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


class StreamState:
    """Holds state for an individual HTTP/2 stream."""

    def __init__(self, stream_id: int):
        self.stream_id = stream_id
        self.receiver = Receiver()
        self.sender: Sender | None = None  # Will be set later


class Http2ServerProtocol(asyncio.Protocol):

    def __init__(self, server: tuple[str, int], app: ASGIApplication):
        super().__init__()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.server = server
        self.conn = None
        self.transport = None
        self.client = None
        self.app = app
        self.stream_states: dict[int, StreamState] = {}

    def _write_to_transport(self) -> None:
        """Safely write data to transport with checks."""
        if self.transport and not self.transport.is_closing():
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
                stream_state.sender = Sender(
                    conn=self.conn, transport=self.transport, stream_id=event.stream_id
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
                stream_state = self.stream_states.get(event.stream_id)
                if stream_state:
                    # Acknowledge the data for flow control
                    self.conn.acknowledge_received_data(
                        event.flow_controlled_length, event.stream_id
                    )
                    self._write_to_transport()

                    # Check if this is a WebSocket stream
                    if hasattr(stream_state, "ws_protocol"):
                        # Pass data to WebSocket handler
                        stream_state.ws_protocol.data_received(event.data)  # type: ignore
                    else:
                        # Regular HTTP/2 data
                        asyncio.create_task(
                            stream_state.receiver.messages.put(
                                {
                                    "type": "http.request",
                                    "body": event.data,
                                    "more_body": True,
                                }
                            )
                        )
            elif isinstance(event, h2.events.StreamEnded):
                stream_state = self.stream_states.get(event.stream_id)
                if stream_state:
                    asyncio.create_task(
                        stream_state.receiver.messages.put(
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
            "headers": headers,
            "client": self.client,
            "server": self.server,
            "state": {},
            "extensions": {},
        }
        return scope

    async def _handle_request(
        self,
        event: h2.events.RequestReceived,
        scope: HTTPScope,
        stream_state: StreamState,
    ):
        try:
            await self.app(scope, stream_state.receiver, stream_state.sender)
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
        headers = [(name, value) for name, value in event.headers 
                   if not name.startswith(b":")]
        
        # Extract subprotocols if present
        subprotocols = []
        ws_protocol = headers_dict.get(b"sec-websocket-protocol")
        if ws_protocol:
            subprotocols = [p.strip().decode() for p in ws_protocol.split(b",")]
        
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
        self.conn.send_headers(
            stream_id=event.stream_id,
            headers=[(b":status", b"200")],
        )
        self._write_to_transport()
        
        # Create a custom transport wrapper for this stream
        stream_transport = HTTP2StreamTransport(
            self.conn, self.transport, event.stream_id, self
        )
        
        # Create WebSocket protocol handler
        ws_protocol = WebSocketProtocol(stream_transport, scope, self.app)
        
        # Store stream as WebSocket
        self.stream_states[event.stream_id] = StreamState(event.stream_id)
        self.stream_states[event.stream_id].ws_protocol = ws_protocol  # type: ignore
        
        # Start WebSocket handling
        asyncio.create_task(ws_protocol.handle())


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
