"""
Tests for HTTP/1.1 to HTTP/2 upgrade mechanism (h2c).

Tests the RFC 7540 Section 3.2 upgrade flow where a client can upgrade
from HTTP/1.1 to HTTP/2 using the Upgrade header.
"""

import asyncio
import base64

import h2.config
import h2.connection
import pytest


@pytest.mark.asyncio
async def test_h2c_upgrade_detection():
    """Test that h2c upgrade requests are properly detected."""
    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    # Create a mock app
    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Hello HTTP/2!",
            }
        )

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    # Create valid HTTP2-Settings - just use a simple base64 encoded value
    # In practice this would be the serialized settings, but for detection we just need valid base64
    http2_settings = base64.urlsafe_b64encode(b"\x00\x03\x00\x00\x00\x64").rstrip(b"=")

    # Create h11 request with upgrade headers
    request = h11.Request(
        method=b"GET",
        target=b"/",
        headers=[
            (b"host", b"localhost:8000"),
            (b"connection", b"Upgrade, HTTP2-Settings"),
            (b"upgrade", b"h2c"),
            (b"http2-settings", http2_settings),
        ],
        http_version=b"1.1",
    )

    # Test detection
    assert protocol._is_h2c_upgrade(request) is True


@pytest.mark.asyncio
async def test_h2c_upgrade_missing_settings():
    """Test that upgrade requests without HTTP2-Settings are rejected."""
    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    async def app(scope, receive, send):
        pass

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    # Request missing HTTP2-Settings header
    request = h11.Request(
        method=b"GET",
        target=b"/",
        headers=[
            (b"host", b"localhost:8000"),
            (b"connection", b"Upgrade"),
            (b"upgrade", b"h2c"),
        ],
        http_version=b"1.1",
    )

    assert protocol._is_h2c_upgrade(request) is False


@pytest.mark.asyncio
async def test_h2c_upgrade_wrong_protocol():
    """Test that upgrade to non-h2c protocols are not detected."""
    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    async def app(scope, receive, send):
        pass

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    # Request with wrong upgrade value
    request = h11.Request(
        method=b"GET",
        target=b"/",
        headers=[
            (b"host", b"localhost:8000"),
            (b"connection", b"Upgrade, HTTP2-Settings"),
            (b"upgrade", b"websocket"),  # Wrong protocol
            (b"http2-settings", b"AAMAAABkAARAAAAAAAIAAAAA"),
        ],
        http_version=b"1.1",
    )

    assert protocol._is_h2c_upgrade(request) is False


@pytest.mark.asyncio
async def test_h2c_upgrade_missing_connection_header():
    """Test that upgrade without proper Connection header is rejected."""
    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    async def app(scope, receive, send):
        pass

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    # Missing HTTP2-Settings in Connection header
    request = h11.Request(
        method=b"GET",
        target=b"/",
        headers=[
            (b"host", b"localhost:8000"),
            (b"connection", b"Upgrade"),  # Missing HTTP2-Settings
            (b"upgrade", b"h2c"),
            (b"http2-settings", b"AAMAAABkAARAAAAAAAIAAAAA"),
        ],
        http_version=b"1.1",
    )

    assert protocol._is_h2c_upgrade(request) is False


@pytest.mark.asyncio
async def test_h2c_upgrade_invalid_settings():
    """Test that invalid HTTP2-Settings are handled gracefully."""
    from unittest.mock import Mock

    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    sent_data = bytearray()

    mock_transport = Mock(spec=asyncio.Transport)
    mock_transport.write = lambda data: sent_data.extend(data)
    mock_transport.get_extra_info = lambda key, default=None: (
        ("127.0.0.1", 12345) if key == "peername" else default
    )
    mock_transport.close = Mock()

    async def app(scope, receive, send):
        pass

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    protocol.connection_made(mock_transport)

    # Create request with invalid base64 in HTTP2-Settings
    request_line = b"GET / HTTP/1.1\r\n"
    headers = (
        b"Host: localhost:8000\r\n"
        b"Connection: Upgrade, HTTP2-Settings\r\n"
        b"Upgrade: h2c\r\n"
        b"HTTP2-Settings: !!!INVALID_BASE64!!!\r\n"
        b"\r\n"
    )

    protocol.data_received(request_line + headers)

    # Give async tasks time to process
    await asyncio.sleep(0.1)

    # Should get 400 Bad Request
    response_data = bytes(sent_data)
    assert b"400" in response_data


@pytest.mark.asyncio
async def test_websocket_upgrade_still_works():
    """Ensure WebSocket upgrade still works after adding h2c upgrade."""
    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    async def app(scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.accept"})

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    # WebSocket upgrade request
    ws_request = h11.Request(
        method=b"GET",
        target=b"/ws",
        headers=[
            (b"host", b"localhost:8000"),
            (b"connection", b"Upgrade"),
            (b"upgrade", b"websocket"),
            (b"sec-websocket-version", b"13"),
            (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
        ],
        http_version=b"1.1",
    )

    # Should detect as WebSocket, not h2c
    assert protocol._is_websocket_upgrade(ws_request) is True
    assert protocol._is_h2c_upgrade(ws_request) is False


@pytest.mark.asyncio
async def test_h2c_upgrade_with_query_string():
    """Test h2c upgrade with query string in the URL."""
    from unittest.mock import Mock

    import h11

    from asgiri.proto.http11 import HTTP11ServerProtocol

    sent_data = bytearray()
    response_sent = asyncio.Event()

    mock_transport = Mock(spec=asyncio.Transport)
    mock_transport.write = lambda data: sent_data.extend(data)
    mock_transport.get_extra_info = lambda key, default=None: (
        ("127.0.0.1", 12345) if key == "peername" else default
    )
    mock_transport.is_closing = lambda: False

    async def app(scope, receive, send):
        # Verify query string is preserved
        assert scope["query_string"] == b"foo=bar&baz=qux"
        assert scope["path"] == "/test"
        response_sent.set()

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"OK",
            }
        )

    protocol = HTTP11ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        ssl=False,
    )

    protocol.connection_made(mock_transport)

    # Create valid HTTP2-Settings
    http2_settings = base64.urlsafe_b64encode(b"\x00\x03\x00\x00\x00\x64").rstrip(b"=")

    # Request with query string
    request_line = b"GET /test?foo=bar&baz=qux HTTP/1.1\r\n"
    headers = (
        b"Host: localhost:8000\r\n"
        b"Connection: Upgrade, HTTP2-Settings\r\n"
        b"Upgrade: h2c\r\n"
        b"HTTP2-Settings: " + http2_settings + b"\r\n"
        b"\r\n"
    )

    protocol.data_received(request_line + headers)

    await asyncio.sleep(0.1)

    # Verify 101 was sent
    assert b"HTTP/1.1 101" in bytes(sent_data)

    # Wait for app to verify
    await asyncio.wait_for(response_sent.wait(), timeout=1.0)
