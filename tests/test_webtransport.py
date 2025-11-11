"""
Test WebTransport and WebSocket over HTTP/3 functionality.

This test verifies that WebTransport and WebSocket support is properly enabled
and the protocol can handle CONNECT requests.
"""

import pytest
import asyncio


@pytest.mark.asyncio
async def test_webtransport_enabled():
    """Test that WebTransport can be enabled in HTTP3ServerProtocol."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from unittest.mock import Mock
    
    # Create a mock QUIC connection
    mock_quic = Mock()
    
    # Create protocol with WebTransport enabled
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8443),
        enable_webtransport=True,
    )
    
    assert protocol.enable_webtransport is True
    assert protocol.h3 is not None


@pytest.mark.asyncio
async def test_webtransport_disabled_by_default():
    """Test that WebTransport is disabled by default."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from unittest.mock import Mock
    
    # Create a mock QUIC connection
    mock_quic = Mock()
    
    # Create protocol without WebTransport
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8443),
    )
    
    assert protocol.enable_webtransport is False


@pytest.mark.asyncio
async def test_check_webtransport_request():
    """Test detection of WebTransport CONNECT requests."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from unittest.mock import Mock
    
    mock_quic = Mock()
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8443),
        enable_webtransport=True,
    )
    
    # Test WebTransport CONNECT request
    wt_headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"webtransport"),
        (b":path", b"/webtransport"),
        (b":authority", b"localhost:8443"),
    ]
    assert protocol._check_webtransport_request(wt_headers) is True
    
    # Test regular HTTP request
    http_headers = [
        (b":method", b"GET"),
        (b":path", b"/"),
        (b":authority", b"localhost:8443"),
    ]
    assert protocol._check_webtransport_request(http_headers) is False
    
    # Test CONNECT without webtransport protocol
    connect_headers = [
        (b":method", b"CONNECT"),
        (b":path", b"/proxy"),
        (b":authority", b"example.com:443"),
    ]
    assert protocol._check_webtransport_request(connect_headers) is False


def test_server_webtransport_parameter():
    """Test that Server class accepts enable_webtransport parameter."""
    from asgiri.server import Server
    
    async def app(scope, receive, send):
        pass
    
    server = Server(
        app,
        host="127.0.0.1",
        port=8443,
        enable_http3=True,
        enable_webtransport=True,
    )
    
    assert server.enable_webtransport is True


@pytest.mark.asyncio
async def test_build_webtransport_scope():
    """Test building WebTransport scope from headers."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from aioquic.h3.events import HeadersReceived
    from unittest.mock import Mock
    
    mock_quic = Mock()
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8443),
        enable_webtransport=True,
    )
    
    # Create a mock HeadersReceived event
    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"webtransport"),
        (b":scheme", b"https"),
        (b":authority", b"localhost:8443"),
        (b":path", b"/webtransport?test=1"),
        (b"user-agent", b"test-client"),
    ]
    
    event = HeadersReceived(
        stream_id=12345,
        headers=headers,
        stream_ended=False,
    )
    
    scope = protocol._build_webtransport_scope(event)
    
    assert scope["type"] == "webtransport"
    assert scope["path"] == "/webtransport"
    assert scope["query_string"] == b"test=1"
    assert scope["scheme"] == "https"
    assert scope["session_id"] == 12345
    assert scope["server"] == ("127.0.0.1", 8443)
    assert (b"user-agent", b"test-client") in scope["headers"]
    # Pseudo-headers should not be in headers list
    assert not any(name.startswith(b":") for name, _ in scope["headers"])


# WebSocket over HTTP/3 tests

@pytest.mark.asyncio
async def test_check_websocket_request():
    """Test detection of WebSocket CONNECT requests."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from unittest.mock import Mock
    
    mock_quic = Mock()
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8443),
    )
    
    # Test WebSocket CONNECT request (RFC 9220)
    ws_headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":path", b"/chat"),
        (b":authority", b"localhost:8443"),
    ]
    assert protocol._check_websocket_request(ws_headers) is True
    
    # Test regular HTTP request
    http_headers = [
        (b":method", b"GET"),
        (b":path", b"/"),
        (b":authority", b"localhost:8443"),
    ]
    assert protocol._check_websocket_request(http_headers) is False
    
    # Test CONNECT without websocket protocol
    connect_headers = [
        (b":method", b"CONNECT"),
        (b":path", b"/proxy"),
        (b":authority", b"example.com:443"),
    ]
    assert protocol._check_websocket_request(connect_headers) is False
    
    # Test WebTransport CONNECT (should not match WebSocket)
    wt_headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"webtransport"),
        (b":path", b"/webtransport"),
        (b":authority", b"localhost:8443"),
    ]
    assert protocol._check_websocket_request(wt_headers) is False


@pytest.mark.asyncio
async def test_build_websocket_scope():
    """Test building WebSocket scope from HTTP/3 CONNECT headers."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from aioquic.h3.events import HeadersReceived
    from unittest.mock import Mock
    
    mock_quic = Mock()
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8443),
    )
    
    # Create a mock WebSocket CONNECT request
    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"https"),
        (b":authority", b"localhost:8443"),
        (b":path", b"/chat?room=general"),
        (b"origin", b"https://example.com"),
        (b"sec-websocket-protocol", b"chat, superchat"),
        (b"sec-websocket-version", b"13"),
    ]
    
    event = HeadersReceived(
        stream_id=12345,
        headers=headers,
        stream_ended=False,
    )
    
    scope = protocol._build_websocket_scope(event)
    
    assert scope["type"] == "websocket"
    assert scope["http_version"] == "3"
    assert scope["scheme"] == "wss"  # https -> wss
    assert scope["path"] == "/chat"
    assert scope["query_string"] == b"room=general"
    assert scope["server"] == ("127.0.0.1", 8443)
    assert scope["subprotocols"] == ["chat", "superchat"]
    
    # Check headers (pseudo-headers should be excluded)
    headers_dict = dict(scope["headers"])
    assert headers_dict[b"origin"] == b"https://example.com"
    assert headers_dict[b"sec-websocket-version"] == b"13"
    # Pseudo-headers should not be in headers list
    assert not any(name.startswith(b":") for name, _ in scope["headers"])


@pytest.mark.asyncio
async def test_websocket_http_to_ws_scheme():
    """Test that http scheme maps to ws (not wss)."""
    from asgiri.proto.http3 import HTTP3ServerProtocol
    from aioquic.h3.events import HeadersReceived
    from unittest.mock import Mock
    
    mock_quic = Mock()
    protocol = HTTP3ServerProtocol(
        quic=mock_quic,
        app=lambda s, r, send: None,
        server=("127.0.0.1", 8080),
    )
    
    headers = [
        (b":method", b"CONNECT"),
        (b":protocol", b"websocket"),
        (b":scheme", b"http"),  # Plain HTTP
        (b":path", b"/"),
        (b":authority", b"localhost:8080"),
    ]
    
    event = HeadersReceived(stream_id=1, headers=headers, stream_ended=False)
    scope = protocol._build_websocket_scope(event)
    
    assert scope["scheme"] == "ws"  # http -> ws (not wss)



if __name__ == "__main__":
    pytest.main([__file__, "-v"])
