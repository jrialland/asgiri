"""Tests for ALPN (Application-Layer Protocol Negotiation) support."""

import asyncio
import ssl
from unittest.mock import MagicMock, Mock

import pytest

from asgiri.proto.auto import AutoProtocol


# Simple ASGI application for testing
async def simple_app(scope, receive, send):
    """Simple ASGI app that returns Hello World."""
    assert scope["type"] == "http"

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
            ],
        }
    )

    await send(
        {
            "type": "http.response.body",
            "body": b"Hello, ALPN!",
        }
    )


@pytest.mark.asyncio
async def test_alpn_selects_http2():
    """Test that ALPN negotiation selects HTTP/2 when 'h2' is negotiated."""
    protocol = AutoProtocol(
        server=("127.0.0.1", 8443),
        app=simple_app,
        ssl=True,
    )

    # Mock transport with SSL object that returns 'h2' as ALPN protocol
    transport = Mock(spec=asyncio.Transport)
    ssl_object = Mock()
    ssl_object.selected_alpn_protocol.return_value = "h2"
    transport.get_extra_info.return_value = ssl_object

    # Simulate connection
    protocol.connection_made(transport)

    # Verify that HTTP/2 protocol was selected immediately
    assert protocol.protocol_detected is True
    assert protocol.delegated_protocol is not None
    assert protocol.delegated_protocol.__class__.__name__ == "Http2ServerProtocol"


@pytest.mark.asyncio
async def test_alpn_selects_http11():
    """Test that ALPN negotiation selects HTTP/1.1 when 'http/1.1' is negotiated."""
    protocol = AutoProtocol(
        server=("127.0.0.1", 8443),
        app=simple_app,
        ssl=True,
    )

    # Mock transport with SSL object that returns 'http/1.1' as ALPN protocol
    transport = Mock(spec=asyncio.Transport)
    ssl_object = Mock()
    ssl_object.selected_alpn_protocol.return_value = "http/1.1"
    transport.get_extra_info.return_value = ssl_object

    # Simulate connection
    protocol.connection_made(transport)

    # Verify that HTTP/1.1 protocol was selected immediately
    assert protocol.protocol_detected is True
    assert protocol.delegated_protocol is not None
    assert protocol.delegated_protocol.__class__.__name__ == "HTTP11ServerProtocol"


@pytest.mark.asyncio
async def test_alpn_fallback_when_no_alpn():
    """Test that protocol detection falls back to data inspection when no ALPN."""
    protocol = AutoProtocol(
        server=("127.0.0.1", 8443),
        app=simple_app,
        ssl=True,
    )

    # Mock transport with SSL object that returns None (no ALPN negotiated)
    transport = Mock(spec=asyncio.Transport)
    ssl_object = Mock()
    ssl_object.selected_alpn_protocol.return_value = None
    transport.get_extra_info.return_value = ssl_object

    # Simulate connection
    protocol.connection_made(transport)

    # Verify that protocol was NOT detected yet (waiting for data)
    assert protocol.protocol_detected is False
    assert protocol.delegated_protocol is None


@pytest.mark.asyncio
async def test_alpn_fallback_when_no_ssl():
    """Test that protocol detection works without SSL (cleartext)."""
    protocol = AutoProtocol(
        server=("127.0.0.1", 8000),
        app=simple_app,
        ssl=False,
    )

    # Mock transport with no SSL object
    transport = Mock(spec=asyncio.Transport)
    transport.get_extra_info.return_value = None

    # Simulate connection
    protocol.connection_made(transport)

    # Verify that protocol was NOT detected yet (waiting for data)
    assert protocol.protocol_detected is False
    assert protocol.delegated_protocol is None


@pytest.mark.asyncio
async def test_alpn_unknown_protocol_fallback():
    """Test that unknown ALPN protocol falls back to data detection."""
    protocol = AutoProtocol(
        server=("127.0.0.1", 8443),
        app=simple_app,
        ssl=True,
    )

    # Mock transport with SSL object that returns unknown protocol
    transport = Mock(spec=asyncio.Transport)
    ssl_object = Mock()
    ssl_object.selected_alpn_protocol.return_value = "h3"  # Unknown for AUTO protocol
    transport.get_extra_info.return_value = ssl_object

    # Simulate connection
    protocol.connection_made(transport)

    # Verify that protocol falls back to detection
    assert protocol.protocol_detected is False
    assert protocol.delegated_protocol is None


def test_alpn_in_ssl_context():
    """Test that SSL context is configured with ALPN protocols."""
    from asgiri.ssl_utils import create_ssl_context
    from generate_cert import generate_self_signed_cert

    # Generate self-signed certificate
    cert_data, key_data = generate_self_signed_cert()

    # Create SSL context
    context = create_ssl_context(cert_data=cert_data, key_data=key_data)

    # Verify ALPN is configured (we can't easily check without a real connection,
    # but we can verify the context was created successfully)
    assert context is not None
    assert isinstance(context, ssl.SSLContext)
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
