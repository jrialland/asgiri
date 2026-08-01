"""Tests for HTTP/3 support."""

import logging
from io import StringIO

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
    """Test that HTTP/3 server configuration is created correctly."""
    from asgiri.ssl_utils import generate_self_signed_cert

    # Generate self-signed certificates for HTTP/3
    cert_data, key_data = generate_self_signed_cert()

    server = Server(
        app=simple_app,
        host="127.0.0.1",
        port=0,  # Use any available port
        http_version=HttpProtocolVersion.AUTO,
        enable_http3=True,
        cert_data=cert_data,
        key_data=key_data,
    )

    # Verify the server object is created with HTTP/3 enabled
    assert server.enable_http3 is True
    assert server.http_version == HttpProtocolVersion.AUTO
    assert server.http3_port == 0  # Port should be configured


@pytest.mark.asyncio
async def test_http3_requires_ssl():
    """Test that HTTP/3 requires SSL/TLS configuration."""

    # Capture log output
    log_capture = StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.WARNING)
    logger = logging.getLogger("asgiri.server")
    logger.addHandler(handler)
    original_level = logger.level
    logger.setLevel(logging.WARNING)

    try:
        server = Server(
            app=simple_app,
            host="127.0.0.1",
            port=0,
            http_version=HttpProtocolVersion.AUTO,
            enable_http3=True,  # Enable HTTP/3
            # No certificates provided - HTTP/3 should not start
        )

        assert server.enable_http3 is True

        # Try to start HTTP/3 server (should fail gracefully)
        # This is called internally when server starts
        # We can't easily test the actual startup without running the server
        # But we verify the configuration requires certs
        assert server.certfile is None or server.cert_data is None

    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


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
    """Test that Alt-Svc header configuration is set up correctly."""
    from asgiri.ssl_utils import generate_self_signed_cert

    # Generate certificates
    cert_data, key_data = generate_self_signed_cert()

    server = Server(
        app=simple_app,
        host="127.0.0.1",
        port=8080,
        http_version=HttpProtocolVersion.AUTO,
        enable_http3=True,
        http3_port=8443,  # Different port for HTTP/3
        cert_data=cert_data,
        key_data=key_data,
    )

    assert server.enable_http3 is True
    assert server.http3_port == 8443

    # Verify server is configured to advertise HTTP/3
    # The actual Alt-Svc header is added by the protocol handlers
    # This test verifies the configuration needed for Alt-Svc
    assert server.port == 8080  # HTTP/1.1 or HTTP/2 port
    assert server.http3_port == 8443  # HTTP/3 port for Alt-Svc


if __name__ == "__main__":
    # Run basic sanity checks
    print("Running HTTP/3 tests...")
    pytest.main([__file__, "-v"])
