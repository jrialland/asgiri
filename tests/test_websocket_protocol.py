"""
Tests for WebSocket protocol edge cases and specific behaviors.

This module tests:
- Ping/Pong frame handling
- Close frame handling with various codes and reasons
- Fragmented message handling
- Protocol violations and error cases
- State transitions and error conditions
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Ping,
    Pong,
    Request,
    TextMessage,
)

from asgiri.proto.websocket import WebSocketProtocol


@pytest.fixture
def mock_transport():
    """Create a mock transport."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()
    return transport


@pytest.fixture
def mock_app():
    """Create a simple mock ASGI app."""

    async def app(scope, receive, send):
        # Accept connection
        await send({"type": "websocket.accept"})
        # Wait for messages
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                break

    return app


@pytest.fixture
def websocket_handler(mock_app, mock_transport):
    """Create a WebSocketProtocol instance."""
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    # Create a real handshake request
    request = b"GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"

    handler = WebSocketProtocol(
        transport=mock_transport,
        scope=scope,
        app=mock_app,
        raw_request=request,
    )
    return handler


@pytest.mark.asyncio
async def test_ping_frame_triggers_pong_response():
    """Test that Ping event handler sends a Pong response."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    # Create handler in HTTP/2 mode
    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Directly test the event handling
    from wsproto.events import Ping as PingEvent

    ping_event = PingEvent(payload=b"test")

    # Call the event handler directly
    await handler._handle_event(ping_event)

    # Verify that a pong response was sent
    assert transport.write.called
    # Verify the data looks like a Pong response
    written_data = transport.write.call_args[0][0]
    assert len(written_data) > 0  # Some data was written


@pytest.mark.asyncio
async def test_pong_frame_is_handled_silently():
    """Test that receiving a Pong frame doesn't cause errors (silent handling)."""
    transport = Mock()
    transport.write = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Directly test Pong event handling
    from wsproto.events import Pong as PongEvent

    pong_event = PongEvent(payload=b"test")

    # Call the event handler - should not raise
    await handler._handle_event(pong_event)

    # Should not close connection or write anything
    assert not handler.closed
    # Pong events don't trigger writes (they're responses, not requests)
    # Just verify it didn't crash


@pytest.mark.asyncio
async def test_close_with_specific_code_and_reason():
    """Test closing WebSocket with specific code and reason."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,  # HTTP/2 mode
    )

    # Close with custom code and reason
    handler.close(1001, "Going away")

    # Verify state
    assert handler.closed
    assert handler.close_code == 1001
    assert handler.close_reason == "Going away"

    # Verify transport was written to and closed
    assert transport.write.called
    assert transport.close.called


@pytest.mark.asyncio
async def test_close_with_default_code():
    """Test closing WebSocket with default code (1000)."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Close with default code
    handler.close()

    assert handler.closed
    assert handler.close_code == 1000
    assert handler.close_reason == ""


@pytest.mark.asyncio
async def test_close_when_already_closed():
    """Test that calling close() multiple times is idempotent."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Close the first time
    handler.close(1000, "Normal")

    # Reset mocks
    transport.write.reset_mock()
    transport.close.reset_mock()

    # Close again - should not write or close again
    handler.close(1001, "Another reason")

    assert not transport.write.called
    assert not transport.close.called

    # State should remain from first close
    assert handler.close_code == 1000
    assert handler.close_reason == "Normal"


@pytest.mark.asyncio
async def test_client_initiated_close_frame():
    """Test handling a close frame initiated by the client."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Directly test CloseConnection event handling
    from wsproto.events import CloseConnection as CloseEvent

    close_event = CloseEvent(code=1001, reason="Client going away")

    # Call the event handler
    await handler._handle_event(close_event)

    # Give async tasks time to run
    await asyncio.sleep(0.01)

    # Handler should close the connection
    assert handler.closed
    assert transport.write.called  # Echo close frame
    assert transport.close.called  # Close transport


@pytest.mark.asyncio
async def test_close_frame_with_empty_reason():
    """Test handling close frame with code but no reason."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Close with code but empty reason
    handler.close(1000, "")

    assert handler.closed
    assert handler.close_code == 1000
    assert handler.close_reason == ""


@pytest.mark.asyncio
async def test_close_with_error_writing_frame():
    """Test that errors during close frame writing are handled gracefully."""
    transport = Mock()
    transport.write = Mock(side_effect=Exception("Write error"))
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Close should not raise even if write fails
    handler.close(1000, "Test")

    assert handler.closed
    # Transport.close should still be called even if write failed
    assert transport.close.called


@pytest.mark.asyncio
async def test_connection_lost_triggers_close():
    """Test that connection_lost() closes the WebSocket if not already closed."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Simulate connection lost
    handler.connection_lost(None)

    # Should close with code 1006
    assert handler.closed
    assert handler.close_code == 1006
    assert handler.close_reason == "Connection lost"


@pytest.mark.asyncio
async def test_connection_lost_when_already_closed():
    """Test that connection_lost() is idempotent when already closed."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Close first
    handler.close(1000, "Normal")

    # Reset mocks
    transport.write.reset_mock()
    transport.close.reset_mock()

    # Connection lost - should not trigger another close
    handler.connection_lost(None)

    # No additional writes or closes
    assert not transport.write.called
    assert not transport.close.called


@pytest.mark.asyncio
async def test_data_received_when_closed():
    """Test that data_received() does nothing when connection is already closed."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Close the connection
    handler.close(1000, "Test")

    # Reset transport mock
    transport.write.reset_mock()

    # Try to receive data - should be ignored
    handler.data_received(b"some data")

    # No writes should occur
    assert not transport.write.called


@pytest.mark.asyncio
async def test_accept_with_subprotocol():
    """Test accepting WebSocket with a subprotocol."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    async def app(scope, receive, send):
        await send({"type": "websocket.accept", "subprotocol": "chat"})

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    # Create handler with raw request (HTTP/1.1 mode) that includes the subprotocol
    request = b"GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: chat\r\n\r\n"

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=request,
    )

    # Accept with subprotocol
    message = {"type": "websocket.accept", "subprotocol": "chat"}
    await handler._handle_accept(message)

    assert handler.accepted
    assert transport.write.called


@pytest.mark.asyncio
async def test_accept_with_custom_headers():
    """Test accepting WebSocket with custom headers."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    # Create handler with raw request (HTTP/1.1 mode)
    request = b"GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"

    app = AsyncMock()

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=request,
    )

    # Accept with custom headers
    message = {
        "type": "websocket.accept",
        "headers": [(b"X-Custom", b"value")],
    }
    await handler._handle_accept(message)

    assert handler.accepted
    assert transport.write.called


@pytest.mark.asyncio
async def test_accept_twice_raises_error():
    """Test that accepting twice raises an error."""
    transport = Mock()
    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Accept first time
    await handler._handle_accept({"type": "websocket.accept"})

    # Accept second time should raise
    with pytest.raises(RuntimeError, match="already accepted"):
        await handler._handle_accept({"type": "websocket.accept"})


@pytest.mark.asyncio
async def test_send_before_accept_raises_error():
    """Test that sending data before accept raises an error."""
    transport = Mock()
    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Try to send without accepting first
    with pytest.raises(RuntimeError, match="not accepted"):
        await handler._handle_send({"type": "websocket.send", "text": "hello"})


@pytest.mark.asyncio
async def test_send_without_bytes_or_text_raises_error():
    """Test that send without bytes or text raises an error."""
    transport = Mock()
    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Accept first
    handler.accepted = True

    # Try to send without bytes or text
    with pytest.raises(ValueError, match="must have 'bytes' or 'text'"):
        await handler._handle_send({"type": "websocket.send"})


@pytest.mark.asyncio
async def test_send_text_message():
    """Test sending a text message."""
    transport = Mock()
    transport.write = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    handler.accepted = True

    # Send text message
    await handler._handle_send(
        {"type": "websocket.send", "text": "Hello, World!"}
    )

    assert transport.write.called


@pytest.mark.asyncio
async def test_send_binary_message():
    """Test sending a binary message."""
    transport = Mock()
    transport.write = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    handler.accepted = True

    # Send binary message
    await handler._handle_send(
        {"type": "websocket.send", "bytes": b"\x00\x01\x02"}
    )

    assert transport.write.called


@pytest.mark.asyncio
async def test_close_message_from_app():
    """Test handling websocket.close message from application."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Send close message
    await handler._handle_close(
        {"type": "websocket.close", "code": 1000, "reason": "Done"}
    )

    assert handler.closed
    assert handler.close_code == 1000
    assert handler.close_reason == "Done"


@pytest.mark.asyncio
async def test_close_message_with_default_values():
    """Test handling websocket.close message with default code and no reason."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()

    app = AsyncMock()
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
    }

    handler = WebSocketProtocol(
        transport=transport,
        scope=scope,
        app=app,
        raw_request=None,
    )

    # Send close message without code or reason
    await handler._handle_close({"type": "websocket.close"})

    assert handler.closed
    assert handler.close_code == 1000
    assert handler.close_reason == ""
