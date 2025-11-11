"""
HTTP/3 protocol implementation using aioquic.

This module implements HTTP/3 (QUIC) support for the ASGI server.
HTTP/3 runs over UDP instead of TCP, providing multiplexed streams
with better handling of packet loss.
"""

import asyncio
import logging
from typing import Any, Callable
from collections import defaultdict

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import (
    DataReceived,
    H3Event,
    HeadersReceived,
)
from aioquic.quic.events import QuicEvent
from asgiref.typing import ASGIApplication, HTTPScope, ASGIReceiveCallable, ASGISendCallable


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
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.app = app
        self.server = server
        self.h3 = H3Connection(self._quic)
        
        # Track active streams and their state
        self.stream_handlers: dict[int, asyncio.Task] = {}
        self.stream_receivers: dict[int, asyncio.Queue] = {}
        self.stream_ended: dict[int, bool] = {}

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
            # Request body data
            stream_id = event.stream_id
            if stream_id in self.stream_receivers:
                # Queue data for the receiver
                self.stream_receivers[stream_id].put_nowait(event.data)
                
                # Check if stream ended
                if event.stream_ended:
                    self.stream_ended[stream_id] = True

    def _handle_request(self, event: HeadersReceived) -> None:
        """
        Handle a new HTTP/3 request.
        
        Args:
            event: HeadersReceived event containing request headers
        """
        try:
            scope = self._build_scope(event)
            
            # Create receiver queue for this stream
            self.stream_receivers[event.stream_id] = asyncio.Queue()
            self.stream_ended[event.stream_id] = event.stream_ended
            
            # Create and track the handler task
            task = asyncio.create_task(
                self._run_asgi(scope, event.stream_id)
            )
            self.stream_handlers[event.stream_id] = task
            
        except Exception as e:
            self.logger.exception(f"Error handling request on stream {event.stream_id}: {e}")
            # Send error response
            self._send_error_response(event.stream_id, 500)

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
            await self.app(scope, receiver, sender)
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
                more_body = not self.stream_ended.get(stream_id, False) or not queue.empty()
                
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
        
        return receive

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
        
        return send

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
