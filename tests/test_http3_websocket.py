"""
Tests for WebSocket over HTTP/3 (RFC 9220).

These tests verify the Extended CONNECT mechanism for WebSocket over HTTP/3,
which uses :method = CONNECT and :protocol = websocket.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from aioquic.h3.events import HeadersReceived

from asgiri.proto.http3 import HTTP3ServerProtocol


def create_mock_protocol(app):
    """Create a mocked HTTP3ServerProtocol for testing."""
    # Create mock QUIC connection
    mock_quic = MagicMock()
    mock_quic.configuration = MagicMock()
    mock_quic.configuration.is_client = False

    # Create protocol with mocked parent __init__
    protocol = object.__new__(HTTP3ServerProtocol)
    protocol._quic = mock_quic
    protocol.app = app
    protocol.server = ("localhost", 443)

    # Initialize H3Connection manually (it expects a QuicConnection)
    from aioquic.h3.connection import H3Connection

    protocol.h3 = H3Connection(mock_quic, enable_webtransport=True)

    # Initialize state tracking dictionaries
    protocol.stream_handlers = {}
    protocol.stream_receivers = {}
    protocol.stream_ended = {}
    protocol.webtransport_sessions = {}
    protocol.webtransport_stream_handlers = {}
    protocol.webtransport_stream_receivers = {}
    protocol.webtransport_stream_ended = {}
    protocol.websocket_handlers = {}

    return protocol


@pytest.mark.asyncio
async def test_websocket_connect_request_detection():
    """Test that WebSocket CONNECT requests are properly detected."""

    # WebSocket CONNECT headers per RFC 9220
    websocket_headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    # Regular CONNECT (not WebSocket)
    regular_connect_headers = [
        (b":method", b"CONNECT"),
        (b":scheme", b"https"),
        (b":path", b"/"),
        (b":authority", b"example.com"),
    ]

    # Regular HTTP request
    http_headers = [
        (b":method", b"GET"),
        (b":scheme", b"https"),
        (b":path", b"/"),
        (b":authority", b"example.com"),
    ]

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    # Test WebSocket CONNECT detection
    assert protocol._check_websocket_request(websocket_headers) is True
    assert protocol._check_websocket_request(regular_connect_headers) is False
    assert protocol._check_websocket_request(http_headers) is False


@pytest.mark.asyncio
async def test_websocket_connect_missing_protocol_header():
    """Test that CONNECT without :protocol is not treated as WebSocket."""

    headers = [
        (b":method", b"CONNECT"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    assert protocol._check_websocket_request(headers) is False


@pytest.mark.asyncio
async def test_websocket_scope_building():
    """Test building WebSocket scope from HTTP/3 CONNECT headers."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/chat?room=general"),
        (b":authority", b"example.com"),
        (b"sec-websocket-protocol", b"chat, superchat"),
        (b"user-agent", b"test-client"),
    ]

    event = HeadersReceived(stream_id=4, headers=headers, stream_ended=False)

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    scope = protocol._build_websocket_scope(event)

    assert scope["type"] == "websocket"
    assert scope["scheme"] == "wss"
    assert scope["path"] == "/chat"
    assert scope["query_string"] == b"room=general"
    assert scope["http_version"] == "3"
    assert scope["subprotocols"] == ["chat", "superchat"]
    assert (b"user-agent", b"test-client") in scope["headers"]
    # Pseudo-headers should not be in regular headers
    assert not any(name.startswith(b":") for name, _ in scope["headers"])


@pytest.mark.asyncio
async def test_websocket_scope_https_to_wss_scheme():
    """Test that https scheme is converted to wss for WebSocket."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    event = HeadersReceived(stream_id=4, headers=headers, stream_ended=False)

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    scope = protocol._build_websocket_scope(event)

    assert scope["scheme"] == "wss"


@pytest.mark.asyncio
async def test_websocket_scope_http_to_ws_scheme():
    """Test that http scheme is converted to ws for WebSocket."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"http"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    event = HeadersReceived(stream_id=4, headers=headers, stream_ended=False)

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    scope = protocol._build_websocket_scope(event)

    assert scope["scheme"] == "ws"


@pytest.mark.asyncio
async def test_websocket_scope_without_query_string():
    """Test WebSocket scope building without query string."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    event = HeadersReceived(stream_id=4, headers=headers, stream_ended=False)

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    scope = protocol._build_websocket_scope(event)

    assert scope["path"] == "/ws"
    assert scope["query_string"] == b""


@pytest.mark.asyncio
async def test_websocket_scope_without_subprotocols():
    """Test WebSocket scope building without subprotocols."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    event = HeadersReceived(stream_id=4, headers=headers, stream_ended=False)

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    scope = protocol._build_websocket_scope(event)

    assert scope["subprotocols"] == []


@pytest.mark.asyncio
async def test_websocket_handler_creation():
    """Test that WebSocket handler is created for CONNECT requests."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    event = HeadersReceived(stream_id=4, headers=headers, stream_ended=False)

    app_called = asyncio.Event()

    async def websocket_app(scope, receive, send):
        # Mark that app was called
        app_called.set()
        # Simple accept and close
        message = await receive()
        if message["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
            # Wait a bit then close
            await asyncio.sleep(0.1)
            await send({"type": "websocket.close", "code": 1000})

    protocol = create_mock_protocol(websocket_app)

    # Simulate handling the WebSocket CONNECT request
    protocol._handle_websocket_connect(event)

    # Give the handler task a moment to start
    await asyncio.sleep(0.05)

    # Verify handler was registered
    assert 4 in protocol.websocket_handlers
    assert 4 in protocol.stream_handlers

    # Clean up
    if 4 in protocol.stream_handlers:
        protocol.stream_handlers[4].cancel()
        try:
            await protocol.stream_handlers[4]
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_websocket_data_routing():
    """Test that data is routed to WebSocket handler."""
    from aioquic.h3.events import DataReceived

    received_data = []

    # Mock WebSocket handler
    class MockWebSocketHandler:
        def __init__(self, *args, **kwargs):
            pass

        def data_received(self, data):
            received_data.append(data)

        async def handle(self):
            # Keep running
            await asyncio.sleep(100)

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    # Register mock WebSocket handler
    mock_handler = MockWebSocketHandler()
    protocol.websocket_handlers[4] = mock_handler

    # Simulate data event
    data_event = DataReceived(
        stream_id=4, data=b"test websocket data", stream_ended=False
    )

    # Handle the event
    protocol._handle_h3_event(data_event)

    # Verify data was routed to handler
    assert received_data == [b"test websocket data"]


@pytest.mark.asyncio
async def test_websocket_invalid_protocol_header():
    """Test handling of CONNECT with invalid :protocol value."""

    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"invalid-protocol"),
        (b":scheme", b"https"),
        (b":path", b"/ws"),
        (b":authority", b"example.com"),
    ]

    async def simple_app(scope, receive, send):
        pass

    protocol = create_mock_protocol(simple_app)

    # Should not be detected as WebSocket
    assert protocol._check_websocket_request(headers) is False
