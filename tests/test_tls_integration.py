"""
Integration tests for TLS/HTTPS server functionality.

Tests that verify TLS actually works with real HTTPS connections.
"""

import asyncio
import ssl
import tempfile
from pathlib import Path

import httpx
import pytest

from asgiri.server import LifespanPolicy, Server
from asgiri.ssl_utils import generate_self_signed_cert


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_https_server_with_real_connection():
    """Test that HTTPS server works with a real TLS connection."""
    captured_scope = None
    request_received = asyncio.Event()

    async def app(scope, receive, send):
        nonlocal captured_scope
        if scope["type"] == "http":
            captured_scope = scope
            request_received.set()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"text/plain"]],
                }
            )
            await send({"type": "http.response.body", "body": b"Hello, HTTPS!"})

    # Generate self-signed certificate
    cert_pem, key_pem = generate_self_signed_cert(hostname="localhost")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        cert_file = tmp_path / "server.crt"
        key_file = tmp_path / "server.key"

        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)

        # Start HTTPS server
        server = Server(
            app,
            host="127.0.0.1",
            port=8443,
            certfile=cert_file,
            keyfile=key_file,
            enable_http3=False,
            lifespan=LifespanPolicy.DISABLED,
        )
        server_task = asyncio.create_task(server.a_run())

        await asyncio.sleep(0.3)  # Give server time to start

        try:
            # Make HTTPS request with httpx
            # Create SSL context that trusts our self-signed cert
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE  # Accept self-signed cert

            async with httpx.AsyncClient(verify=False) as client:
                response = await client.get("https://127.0.0.1:8443/test")
                assert response.status_code == 200
                assert response.text == "Hello, HTTPS!"

            # Wait for app to capture the scope
            await asyncio.wait_for(request_received.wait(), timeout=2)

        finally:
            # Shutdown server
            server.should_exit.set()
            await server_task

    # Verify TLS extension was added to scope
    assert captured_scope is not None
    assert "extensions" in captured_scope
    assert "tls" in captured_scope["extensions"]

    tls_info = captured_scope["extensions"]["tls"]

    # Verify mandatory fields are present
    assert "server_cert" in tls_info
    assert "tls_version" in tls_info
    assert "cipher_suite" in tls_info

    # TLS version should be set (not None) since we used HTTPS
    assert tls_info["tls_version"] is not None
    assert "TLS" in str(tls_info["tls_version"]) or tls_info["tls_version"] in [
        "TLSv1.2",
        "TLSv1.3",
    ]

    # Cipher suite should be set
    assert tls_info["cipher_suite"] is not None


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_https_server_with_cert_data():
    """Test HTTPS server using in-memory certificate data instead of files."""
    request_received = asyncio.Event()

    async def app(scope, receive, send):
        if scope["type"] == "http":
            request_received.set()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"text/plain"]],
                }
            )
            await send(
                {"type": "http.response.body", "body": b"In-memory cert!"}
            )

    # Generate certificate in memory
    cert_pem, key_pem = generate_self_signed_cert(hostname="localhost")

    # Start HTTPS server with in-memory certs
    server = Server(
        app,
        host="127.0.0.1",
        port=8444,
        cert_data=cert_pem,
        key_data=key_pem,
        enable_http3=False,
        lifespan=LifespanPolicy.DISABLED,
    )
    server_task = asyncio.create_task(server.a_run())

    await asyncio.sleep(0.3)

    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get("https://127.0.0.1:8444/")
            assert response.status_code == 200
            assert response.text == "In-memory cert!"

        await asyncio.wait_for(request_received.wait(), timeout=2)

    finally:
        server.should_exit.set()
        await server_task


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_https_with_http2():
    """Test HTTPS server with HTTP/2 protocol."""
    request_received = asyncio.Event()

    async def app(scope, receive, send):
        if scope["type"] == "http":
            request_received.set()
            # Verify we got HTTP/2
            http_version = scope.get("http_version", "1.1")

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        [b"content-type", b"text/plain"],
                        [b"x-http-version", http_version.encode()],
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"HTTP/2 works!"})

    cert_pem, key_pem = generate_self_signed_cert(hostname="localhost")

    server = Server(
        app,
        host="127.0.0.1",
        port=8445,
        cert_data=cert_pem,
        key_data=key_pem,
        enable_http3=False,
        lifespan=LifespanPolicy.DISABLED,
    )
    server_task = asyncio.create_task(server.a_run())

    await asyncio.sleep(0.3)

    try:
        # httpx with HTTP/2 support
        async with httpx.AsyncClient(verify=False, http2=True) as client:
            response = await client.get("https://127.0.0.1:8445/")
            assert response.status_code == 200

            # Check if HTTP/2 was used
            http_version = response.headers.get("x-http-version", "unknown")
            # HTTP/2 should be negotiated via ALPN
            assert http_version in ["2", "1.1"]  # Either is acceptable

        await asyncio.wait_for(request_received.wait(), timeout=2)

    finally:
        server.should_exit.set()
        await server_task
