"""WebSocket handler for HTTP/2 and HTTP/3 connections.

This module provides WebSocket handling for HTTP/2 CONNECT (RFC 8441) and
HTTP/3 Extended CONNECT (RFC 9220). Unlike the HTTP/1.1 implementation,
this handler assumes the handshake has already been completed via Extended
CONNECT, so it only handles the WebSocket framing layer.

The implementation uses a lightweight frame parser instead of wsproto,
avoiding the need to simulate fake HTTP/1.1 handshakes.
"""

import asyncio
from typing import Callable

from asgiref.typing import (
    ASGI3Application,
    WebSocketAcceptEvent,
    WebSocketCloseEvent,
    WebSocketDisconnectEvent,
    WebSocketReceiveEvent,
    WebSocketScope,
    WebSocketSendEvent,
)
from loguru import logger

from .websocket_frames import (
    CloseCode,
    Opcode,
    WebSocketFrame,
    WebSocketParser,
    WebSocketProtocolError,
    encode_binary_frame,
    encode_close_frame,
    encode_pong_frame,
    encode_text_frame,
    parse_close_frame,
)


class WebSocketHandler:
    """Handles WebSocket connections over HTTP/2 or HTTP/3.

    This handler implements the WebSocket protocol for connections established
    via HTTP/2 CONNECT (RFC 8441) or HTTP/3 Extended CONNECT (RFC 9220).

    Unlike HTTP/1.1 WebSocket which uses the Upgrade mechanism, HTTP/2/3
    WebSocket uses Extended CONNECT which completes the handshake before
    this handler is created.
    """

    def __init__(
        self,
        scope: WebSocketScope,
        app: ASGI3Application,
        send_frame: Callable[[bytes], None],
        close_stream: Callable[[], None],
    ):
        """Initialize the WebSocket handler.

        Args:
            scope: The ASGI WebSocket scope dictionary.
            app: The ASGI application to handle the WebSocket connection.
            send_frame: Callback to send raw frame bytes to the client.
            close_stream: Callback to close the underlying stream.
        """
        self.scope = scope
        self.app = app
        self._send_frame = send_frame
        self._close_stream = close_stream

        # Frame parser for incoming data
        self._parser = WebSocketParser()

        # Queues for ASGI communication
        self._receive_queue: asyncio.Queue[
            WebSocketReceiveEvent | WebSocketDisconnectEvent
        ] = asyncio.Queue()
        self._send_queue: asyncio.Queue[
            WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent
        ] = asyncio.Queue()

        # State tracking
        self._accepted = False
        self._closed = False
        self._close_code: int | None = None
        self._close_reason: str = ""

        # Task tracking
        self._app_task: asyncio.Task | None = None
        self._sender_task: asyncio.Task | None = None

        # Message reassembly for fragmented messages
        self._message_buffer: list[bytes] = []
        self._message_type: Opcode | None = None

    async def handle(self) -> None:
        """Start handling the WebSocket connection.

        This method:
        1. Sends the websocket.connect event to the application
        2. Starts the application task
        3. Starts the sender task
        4. Waits for both tasks to complete
        """
        logger.debug("WebSocket handler starting")

        # Send initial connect event
        connect_event: WebSocketReceiveEvent = {"type": "websocket.connect", "text": None, "bytes": None}
        await self._receive_queue.put(connect_event)

        # Start application task
        self._app_task = asyncio.create_task(self._run_app())

        # Start sender task
        self._sender_task = asyncio.create_task(self._sender())

        # Wait for both to complete
        try:
            await asyncio.gather(self._app_task, self._sender_task)
        except Exception as e:
            logger.exception(f"Error in WebSocket handler: {e}")
        finally:
            await self._cleanup()

    async def _run_app(self) -> None:
        """Run the ASGI application with WebSocket scope."""
        logger.debug("Running ASGI app")
        try:
            await self.app(self.scope, self._receive, self._send)
            logger.debug("ASGI app completed normally")
        except Exception as e:
            # Check if it's a normal WebSocket disconnect
            if "WebSocketDisconnect" in type(e).__name__:
                logger.debug(f"WebSocket disconnected: {e}")
            else:
                logger.exception(f"Error in WebSocket application: {e}")
                if not self._closed:
                    await self._close(CloseCode.INTERNAL_ERROR, "Internal server error")

    async def _receive(self) -> WebSocketReceiveEvent | WebSocketDisconnectEvent:
        """ASGI receive callable."""
        return await self._receive_queue.get()

    async def _send(self, message: WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent) -> None:
        """ASGI send callable."""
        await self._send_queue.put(message)

    async def _sender(self) -> None:
        """Process outgoing messages from the application."""
        while not self._closed:
            try:
                # Wait for message from application with timeout
                try:
                    message = await asyncio.wait_for(
                        self._send_queue.get(), timeout=0.1
                    )
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

    async def _handle_accept(self, message: WebSocketAcceptEvent) -> None:
        """Handle websocket.accept message from application.

        For HTTP/2/3 WebSocket, the handshake is already complete when we
        reach this point (Extended CONNECT was accepted). We just mark
        the connection as accepted and ready for data frames.
        """
        if self._accepted:
            raise RuntimeError("WebSocket already accepted")

        self._accepted = True
        subprotocol = message.get("subprotocol")

        logger.debug(
            f"WebSocket accepted (subprotocol: {subprotocol})"
        )

    async def _handle_send(self, message: WebSocketSendEvent) -> None:
        """Handle websocket.send message from application."""
        if not self._accepted:
            raise RuntimeError("WebSocket not accepted yet")

        if self._closed:
            logger.warning("Attempted to send on closed WebSocket")
            return

        try:
            if "bytes" in message and message["bytes"] is not None:
                # Send binary message
                frame_data = encode_binary_frame(message["bytes"])
                self._send_frame(frame_data)
            elif "text" in message and message["text"] is not None:
                # Send text message
                frame_data = encode_text_frame(message["text"])
                self._send_frame(frame_data)
            else:
                raise ValueError(
                    "WebSocket send message must have 'bytes' or 'text'"
                )
        except Exception as e:
            logger.error(f"Error sending WebSocket frame: {e}")
            raise

    async def _handle_close(self, message: WebSocketCloseEvent) -> None:
        """Handle websocket.close message from application."""
        code = message.get("code", CloseCode.NORMAL)
        reason = message.get("reason") or ""
        await self._close(code, reason)

    def data_received(self, data: bytes) -> None:
        """Handle incoming WebSocket data.

        Args:
            data: Raw bytes received from the network.
        """
        if self._closed:
            return

        try:
            frames = self._parser.receive_data(data)
            for frame in frames:
                asyncio.create_task(self._handle_frame(frame))
        except WebSocketProtocolError as e:
            logger.error(f"WebSocket protocol error: {e}")
            asyncio.create_task(
                self._close(CloseCode.PROTOCOL_ERROR, str(e))
            )

    async def _handle_frame(self, frame: WebSocketFrame) -> None:
        """Handle a parsed WebSocket frame."""
        if self._closed:
            return

        try:
            if frame.opcode == Opcode.TEXT:
                if frame.fin:
                    # Complete text message
                    await self._receive_queue.put({
                        "type": "websocket.receive",
                        "text": frame.payload.decode("utf-8", errors="replace"),
                        "bytes": None,
                    })
                else:
                    # Start of fragmented text message
                    self._message_type = Opcode.TEXT
                    self._message_buffer = [frame.payload]

            elif frame.opcode == Opcode.BINARY:
                if frame.fin:
                    # Complete binary message
                    await self._receive_queue.put({
                        "type": "websocket.receive",
                        "bytes": frame.payload,
                        "text": None,
                    })
                else:
                    # Start of fragmented binary message
                    self._message_type = Opcode.BINARY
                    self._message_buffer = [frame.payload]

            elif frame.opcode == Opcode.CONTINUATION:
                # Continuation frame
                if self._message_type is None:
                    raise WebSocketProtocolError(
                        "Unexpected continuation frame"
                    )

                self._message_buffer.append(frame.payload)

                if frame.fin:
                    # End of fragmented message
                    complete_payload = b"".join(self._message_buffer)

                    if self._message_type == Opcode.TEXT:
                        await self._receive_queue.put({
                            "type": "websocket.receive",
                            "text": complete_payload.decode("utf-8", errors="replace"),
                            "bytes": None,
                        })
                    else:  # BINARY
                        await self._receive_queue.put({
                            "type": "websocket.receive",
                            "bytes": complete_payload,
                            "text": None,
                        })

                    self._message_type = None
                    self._message_buffer = []

            elif frame.opcode == Opcode.CLOSE:
                # Client initiated close
                code, reason = parse_close_frame(frame.payload)
                logger.debug(f"WebSocket close from client: {code} {reason}")

                # Send disconnect event to application
                await self._receive_queue.put({
                    "type": "websocket.disconnect",
                    "code": code,
                    "reason": reason,
                })

                # Echo the close frame
                if not self._closed:
                    await self._close(code, reason, echo=True)

            elif frame.opcode == Opcode.PING:
                # Respond to ping with pong
                pong_frame = encode_pong_frame(frame.payload)
                self._send_frame(pong_frame)

            elif frame.opcode == Opcode.PONG:
                # Pong received (we don't currently send pings, but handle anyway)
                logger.debug("Received pong frame")

        except Exception as e:
            logger.exception(f"Error handling WebSocket frame: {e}")

    async def _close(
        self,
        code: int = CloseCode.NORMAL,
        reason: str = "",
        echo: bool = False,
    ) -> None:
        """Close the WebSocket connection.

        Args:
            code: The WebSocket close code.
            reason: The close reason string.
            echo: If True, this is an echo close (don't send disconnect event).
        """
        if self._closed:
            return

        self._closed = True
        self._close_code = code
        self._close_reason = reason

        try:
            # Send close frame
            close_frame = encode_close_frame(code, reason)
            self._send_frame(close_frame)
        except Exception as e:
            logger.debug(f"Error sending close frame: {e}")

        # Close underlying stream
        try:
            self._close_stream()
        except Exception as e:
            logger.debug(f"Error closing stream: {e}")

        # Send disconnect event to application if not already sent
        if not echo:
            await self._receive_queue.put({
                "type": "websocket.disconnect",
                "code": code,
                "reason": reason,
            })

        logger.debug(f"WebSocket closed: {code} {reason}")

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Cancel tasks if they're still running
        if self._app_task and not self._app_task.done():
            self._app_task.cancel()
            try:
                await self._app_task
            except asyncio.CancelledError:
                pass

        if self._sender_task and not self._sender_task.done():
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass

        # Ensure connection is closed
        if not self._closed:
            await self._close(CloseCode.ABNORMAL_CLOSURE, "Connection lost")

    @property
    def closed(self) -> bool:
        """Check if the connection is closed."""
        return self._closed
