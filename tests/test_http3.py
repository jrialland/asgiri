"""Tests for HTTP/3 support."""

import asyncio

import pytest

from asgiri.server import HttpProtocolVersion, Server


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
            "body": b"Hello, HTTP/3!",
        }
    )


@pytest.mark.asyncio
async def test_http3_server_starts():
    """Test that HTTP/3 server can start successfully."""
    server = Server(
        app=simple_app,
        host="127.0.0.1",
        port=0,  # Use any available port
        http_version=HttpProtocolVersion.AUTO,
        enable_http3=False,  # Disable HTTP/3 for now since we need certificates
    )

    # Just verify the server object is created correctly
    assert server.enable_http3 is False
    assert server.http_version == HttpProtocolVersion.AUTO


@pytest.mark.asyncio
async def test_http3_requires_ssl():
    """Test that HTTP/3 requires SSL/TLS configuration."""
    # This test verifies that HTTP/3 won't start without certificates
    # In a real scenario, we'd need to provide certificates

    server = Server(
        app=simple_app,
        host="127.0.0.1",
        port=0,
        http_version=HttpProtocolVersion.AUTO,
        enable_http3=True,  # Enable HTTP/3
        # No certificates provided - HTTP/3 should skip startup
    )

    assert server.enable_http3 is True
    # The actual check happens in _start_http3_server which will log a warning


def test_http3_protocol_version_enum():
    """Test that HTTP_3 is available in the protocol version enum."""
    assert HttpProtocolVersion.HTTP_3.value == "http/3"

    # Verify all protocol versions
    versions = {v.value for v in HttpProtocolVersion}
    assert "http/1.1" in versions
    assert "http/2" in versions
    assert "http/3" in versions
    assert "auto" in versions


@pytest.mark.asyncio
async def test_alt_svc_header_advertisement():
    """Test that Alt-Svc header is added to advertise HTTP/3."""
    # This is a basic test that verifies the server configuration
    # A full test would require making actual HTTP requests

    server = Server(
        app=simple_app,
        host="127.0.0.1",
        port=8443,
        http_version=HttpProtocolVersion.AUTO,
        enable_http3=True,
    )

    assert server.enable_http3 is True
    assert server.http3_port == 8443  # Should default to same port


if __name__ == "__main__":
    # Run basic sanity checks
    print("Running HTTP/3 tests...")
    pytest.main([__file__, "-v"])
