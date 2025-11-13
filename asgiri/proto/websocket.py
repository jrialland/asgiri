"""WebSocket protocol handler using wsproto.

This module provides WebSocket frame handling for both HTTP/1.1 and HTTP/2
based WebSocket connections, implementing the ASGI WebSocket specification.
"""

import asyncio
from typing import Any

from asgiref.typing import (ASGIApplication, WebSocketAcceptEvent,
                            WebSocketCloseEvent, WebSocketConnectEvent,
                            WebSocketDisconnectEvent, WebSocketReceiveEvent,
                            WebSocketScope, WebSocketSendEvent)
from loguru import logger
from wsproto import ConnectionType, WSConnection
from wsproto.events import (BytesMessage, CloseConnection, Event, Message,
                            Ping, Pong, Request, TextMessage)
from wsproto.frame_protocol import CloseReason


class WebSocketProtocol:
    """Handles WebSocket protocol after upgrade from HTTP/1.1 or HTTP/2.

    This class manages the WebSocket connection lifecycle using wsproto for
    frame handling and implements the ASGI WebSocket protocol for communication
    with the application.

    Note: The HTTP upgrade (101 Switching Protocols for HTTP/1.1 or 200 for
    HTTP/2 CONNECT) must be completed before this handler is initialized.
    """

    def __init__(
        self,
        transport: asyncio.Transport,
        scope: WebSocketScope,
        app: ASGIApplication,
        raw_request: bytes | None = None,
    ):
        """Initialize the WebSocket protocol handler.

        Args:
            transport: The network transport for sending/receiving data.
            scope: The ASGI WebSocket scope dictionary.
            app: The ASGI application to handle the WebSocket connection.
            raw_request: The raw HTTP request bytes (for HTTP/1.1 upgrade). If provided,
                        wsproto will handle the handshake. If None, assumes handshake is
                        already completed (HTTP/2 case).
        """
        self.transport = transport
        self.scope = scope
        self.app = app

        # Create wsproto connection in SERVER mode
        self.ws = WSConnection(ConnectionType.SERVER)

        # If raw_request is provided, process the handshake
        if raw_request:
            self.ws.receive_data(raw_request)
            # Process the Request event
            for event in self.ws.events():
                if isinstance(event, Request):
                    # The request has been processed, we'll send AcceptConnection
                    # when the app calls websocket.accept
                    logger.debug(f"WebSocket request received: {event.target}")
                    break
        else:
            # HTTP/2 case - handshake already done externally via CONNECT
            # We need to manually transition wsproto to OPEN state
            # Since we can't directly set state, we'll simulate a handshake
            fake_request = b"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
            self.ws.receive_data(fake_request)
            for event in self.ws.events():
                if isinstance(event, Request):
                    break
            # Send the accept to transition to OPEN state
            from wsproto.events import AcceptConnection

            self.ws.send(AcceptConnection())
            # Mark that we don't need to send this to the transport
            logger.debug(
                "HTTP/2 WebSocket - simulated handshake for wsproto state"
            )

        # Queues for ASGI communication
        self.receive_queue: asyncio.Queue[
            WebSocketReceiveEvent | WebSocketDisconnectEvent
        ] = asyncio.Queue()
        self.send_queue: asyncio.Queue[
            WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent
        ] = asyncio.Queue()

        # State tracking
        self.accepted = False
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

        # Track if we need to send the handshake response
        self.needs_handshake_response = raw_request is not None

        # Task for handling the application
        self.app_task: asyncio.Task | None = None

    async def handle(self):
        """Start handling the WebSocket connection.

        This method:
        1. Sends the connection event to the application
        2. Starts the application task
        3. Starts the sender task
        4. Waits for both tasks to complete
        """
        logger.debug("WebSocket handler starting")

        # Send initial connect event
        await self.receive_queue.put(
            {
                "type": "websocket.connect",
            }
        )

        # Start application task
        self.app_task = asyncio.create_task(self._run_app())

        # Start sender task
        sender_task = asyncio.create_task(self._sender())

        # Wait for both to complete
        try:
            await asyncio.gather(self.app_task, sender_task)
        except Exception as e:
            logger.exception(f"Error in WebSocket handler: {e}")
        finally:
            if not self.closed:
                self.close(1006, "Connection lost")

    async def _run_app(self):
        """Run the ASGI application with WebSocket scope."""
        logger.debug("Running ASGI app")
        try:
            await self.app(self.scope, self._receive, self._send)
            logger.debug("ASGI app completed normally")
        except Exception as e:
            # Check if it's a normal WebSocket disconnect (FastAPI/Starlette pattern)
            if "WebSocketDisconnect" in type(e).__name__:
                logger.debug(f"WebSocket disconnected: {e}")
            else:
                logger.exception(f"Error in WebSocket application: {e}")
                if not self.closed:
                    self.close(1011, "Internal server error")

    async def _receive(self) -> WebSocketReceiveEvent | WebSocketDisconnectEvent:
        """ASGI receive callable.

        Returns:
            WebSocket event from the client.
        """
        event = await self.receive_queue.get()
        logger.debug(f"App receiving event: {event['type']}")
        return event

    async def _send(
        self, message: WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent
    ):
        """ASGI send callable.

        Args:
            message: WebSocket message to send to the client.
        """
        logger.debug(f"App sending message: {message['type']}")
        await self.send_queue.put(message)

    async def _sender(self):
        """Process outgoing messages from the application."""
        while not self.closed:
            try:
                # Wait for message from application with timeout
                try:
                    message = await asyncio.wait_for(self.send_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if message["type"] == "websocket.accept":
                    await self._handle_accept(message)
                elif message["type"] == "websocket.send":
                    await self._handle_send(message)
                elif message["type"] == "websocket.close":
                    await self._handle_close(message)
                    break

            except Exception as e:
                logger.exception(f"Error in WebSocket sender: {e}")
                break

    async def _handle_accept(self, message: WebSocketAcceptEvent):
        """Handle websocket.accept message from application.

        Args:
            message: The accept message from the application.
        """
        if self.accepted:
            raise RuntimeError("WebSocket already accepted")

        self.accepted = True

        # If we need to send the handshake response (HTTP/1.1 case)
        if self.needs_handshake_response:
            from wsproto.events import AcceptConnection

            subprotocol = message.get("subprotocol")
            headers = list(message.get("headers", []))

            # Send accept via wsproto
            event = AcceptConnection(
                subprotocol=subprotocol,
                extra_headers=headers,
            )
            data = self.ws.send(event)
            self.transport.write(data)

            logger.debug(
                f"WebSocket handshake sent with subprotocol: {subprotocol}"
            )
            self.needs_handshake_response = False
        else:
            # HTTP/2 case - handshake was already completed
            logger.debug("WebSocket accepted (HTTP/2 - no handshake needed)")

    async def _handle_send(self, message: WebSocketSendEvent):
        """Handle websocket.send message from application.

        Args:
            message: The send message from the application.
        """
        if not self.accepted:
            raise RuntimeError("WebSocket not accepted yet")

        if "bytes" in message and message["bytes"] is not None:
            # Send binary message
            data = self.ws.send(Message(data=message["bytes"]))
            self.transport.write(data)
        elif "text" in message and message["text"] is not None:
            # Send text message
            data = self.ws.send(Message(data=message["text"]))
            self.transport.write(data)
        else:
            raise ValueError("WebSocket send message must have 'bytes' or 'text'")

    async def _handle_close(self, message: WebSocketCloseEvent):
        """Handle websocket.close message from application.

        Args:
            message: The close message from the application.
        """
        code = message.get("code", 1000)
        reason = message.get("reason") or ""

        self.close(code, reason)

    def data_received(self, data: bytes):
        """Handle incoming WebSocket data.

        Args:
            data: Raw bytes received from the network.
        """
        if self.closed:
            return

        # Feed data to wsproto
        self.ws.receive_data(data)

        # Process events
        for event in self.ws.events():
            asyncio.create_task(self._handle_event(event))

    async def _handle_event(self, event: Event):
        """Handle a wsproto event.

        Args:
            event: The wsproto event to handle.
        """
        if isinstance(event, Request):
            # This shouldn't happen as we've already handled the upgrade
            logger.warning("Received unexpected Request event in WebSocket")

        elif isinstance(event, TextMessage):
            # Text message from client
            await self.receive_queue.put(
                {
                    "type": "websocket.receive",
                    "text": event.data,
                    "bytes": None,
                }
            )

        elif isinstance(event, BytesMessage):
            # Binary message from client
            await self.receive_queue.put(
                {
                    "type": "websocket.receive",
                    "bytes": event.data,
                    "text": None,
                }
            )

        elif isinstance(event, CloseConnection):
            # Client initiated close
            logger.debug(
                f"WebSocket close from client: {event.code} {event.reason}"
            )

            # Send disconnect event to application
            await self.receive_queue.put(
                WebSocketDisconnectEvent(
                    type="websocket.disconnect",
                    code=event.code,
                    reason=event.reason or "",
                )
            )

            # Echo the close frame if we haven't closed yet
            if not self.closed:
                data = self.ws.send(
                    CloseConnection(code=event.code, reason=event.reason)
                )
                self.transport.write(data)
                self.closed = True
                self.transport.close()

        elif isinstance(event, Ping):
            # Respond to ping with pong
            data = self.ws.send(event.response())
            self.transport.write(data)

        elif isinstance(event, Pong):
            # Pong received (we don't currently send pings, but handle anyway)
            pass

        else:
            logger.warning(f"Unhandled WebSocket event: {type(event)}")

    def close(self, code: int = 1000, reason: str = ""):
        """Close the WebSocket connection.

        Args:
            code: The WebSocket close code.
            reason: The close reason string.
        """
        if self.closed:
            return

        self.closed = True
        self.close_code = code
        self.close_reason = reason

        # Send close frame
        try:
            data = self.ws.send(CloseConnection(code=code, reason=reason))
            self.transport.write(data)
        except Exception as e:
            logger.warning(f"Error sending close frame: {e}")

        # Close transport
        self.transport.close()

        # Send disconnect event to application if not already sent
        asyncio.create_task(
            self.receive_queue.put(
                WebSocketDisconnectEvent(
                    type="websocket.disconnect",
                    code=code,
                    reason=reason,
                )
            )
        )

    def connection_lost(self, exc: Exception | None):
        """Handle connection loss.

        Args:
            exc: The exception that caused the connection loss, if any.
        """
        if not self.closed:
            self.close(1006, "Connection lost")
