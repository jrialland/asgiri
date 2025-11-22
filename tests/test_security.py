"""Security-focused tests for Asgiri.

These tests are marked as 'slow' and 'security' and are excluded from
pre-commit checks by default. Run them explicitly with:

    pytest tests/test_security.py
    pytest -m security
    pytest -m slow

To run all tests including slow ones:
    pytest tests -m ""
"""

import asyncio
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta

import h2.config
import h2.connection
import h2.settings
import httpx
import pytest
import websockets
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from asgiri.server import HttpProtocolVersion, LifespanPolicy, Server

from .app import app


@pytest.fixture(scope="function")
def unused_port():
    """Find an unused port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="function")
def temp_ssl_cert(tmp_path):
    """Generate a temporary self-signed SSL certificate for testing.

    Returns:
        Tuple of (cert_path, key_path) as Path objects.
    """
    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend()
    )

    # Generate certificate
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Test"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Test"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256(), backend=default_backend())
    )

    # Write certificate and key to temporary files
    cert_path = tmp_path / "test_cert.pem"
    key_path = tmp_path / "test_key.pem"

    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    return cert_path, key_path


def wait_for_server(host: str, port: int, timeout: float = 5.0) -> bool:
    """Wait for server to be ready to accept connections."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.connect((host, port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


# ============================================================================
# HTTP/1.1 Security Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_http11_header_injection_crlf(unused_port: int):
    """Test CRLF injection in HTTP/1.1 headers is prevented.

    Attack vector: Inject CRLF characters to add malicious headers
    or split the response.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Test CRLF injection in various headers
    malicious_headers = [
        # Try to inject a new header
        {"X-Test": "value\r\nX-Injected: evil"},
        # Try to inject response splitting
        {"User-Agent": "test\r\n\r\nHTTP/1.1 200 OK\r\nX-Evil: true"},
        # URL-encoded CRLF
        {"X-Custom": "value%0d%0aX-Injected: evil"},
    ]

    async with httpx.AsyncClient(timeout=3.0) as client:
        for headers in malicious_headers:
            try:
                # Server should either reject or sanitize these headers
                response = await client.get(
                    f"http://127.0.0.1:{unused_port}/", headers=headers
                )
                # Check that injected headers didn't make it through
                assert "X-Injected" not in response.headers
                assert "X-Evil" not in response.headers
            except httpx.HTTPError:
                # Connection errors are acceptable (server rejected request)
                pass


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_http11_request_smuggling_cl_te(unused_port: int):
    """Test protection against CL.TE request smuggling.

    Attack: Send conflicting Content-Length and Transfer-Encoding headers
    to cause desynchronization between proxy and backend.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Attempt CL.TE attack
    malicious_request = (
        b"POST / HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Length: 13\r\n"
        b"Transfer-Encoding: chunked\r\n"
        b"\r\n"
        b"0\r\n"
        b"\r\n"
        b"GET /admin HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"\r\n"
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(3.0)
        sock.connect(("127.0.0.1", unused_port))
        sock.sendall(malicious_request)

        # Server should reject this or handle safely
        response = sock.recv(4096)
        # Should not see a successful smuggled request
        assert b"admin" not in response.lower() or b"400" in response
    except (ConnectionResetError, BrokenPipeError):
        # Server rejecting the connection is acceptable
        pass
    finally:
        sock.close()


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(20)
@pytest.mark.asyncio
async def test_http11_slowloris_attack(unused_port: int):
    """Test protection against Slowloris (slow headers) DoS attack.

    Attack: Send headers very slowly to keep connections open and
    exhaust server resources.

    Note: This is a basic test. Real protection requires connection
    timeouts at the server level.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Open multiple slow connections
    socks = []
    try:
        for _ in range(10):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15.0)
            sock.connect(("127.0.0.1", unused_port))

            # Send partial request very slowly
            sock.sendall(b"GET / HTTP/1.1\r\n")
            time.sleep(0.5)
            sock.sendall(b"Host: localhost\r\n")
            time.sleep(0.5)

            socks.append(sock)

        # Try to make a normal request while slow connections are open
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"http://127.0.0.1:{unused_port}/")
            # Server should still respond to legitimate requests
            assert response.status_code == 200

    finally:
        for sock in socks:
            try:
                sock.close()
            except Exception:
                pass


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_http11_large_header_attack(unused_port: int):
    """Test handling of excessively large headers (DoS vector)."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Create a very large header value (1 MB)
    large_value = "A" * (1024 * 1024)

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(
                f"http://127.0.0.1:{unused_port}/",
                headers={"X-Large-Header": large_value},
            )
            # Server should either accept with limits or reject
            # If accepted, ensure it doesn't crash
            assert response.status_code in [200, 400, 413, 431]
        except (httpx.HTTPError, httpx.RemoteProtocolError):
            # Connection errors are acceptable (server rejected)
            pass


# ============================================================================
# HTTP/2 Security Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_http2_rapid_stream_creation(unused_port: int):
    """Test protection against rapid stream creation DoS.

    Attack: Create many streams rapidly to exhaust server resources.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_2,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Create an HTTP/2 connection
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect(("127.0.0.1", unused_port))

    # Send HTTP/2 connection preface
    sock.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

    # Create h2 connection
    config = h2.config.H2Configuration(client_side=True)
    conn = h2.connection.H2Connection(config=config)
    conn.initiate_connection()
    sock.sendall(conn.data_to_send())

    try:
        # Rapidly create many streams
        stream_ids = []
        for i in range(100):
            stream_id = 1 + (i * 2)  # Client streams are odd
            conn.send_headers(
                stream_id,
                [
                    (":method", "GET"),
                    (":path", "/"),
                    (":scheme", "http"),
                    (":authority", "localhost"),
                ],
            )
            stream_ids.append(stream_id)

        sock.sendall(conn.data_to_send())

        # Server should handle this gracefully (may limit streams)
        # Read response
        data = sock.recv(65536)
        assert len(data) > 0  # Server should respond

    finally:
        sock.close()


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_http2_settings_flood(unused_port: int):
    """Test protection against SETTINGS frame flood DoS.

    Attack: Send many SETTINGS frames to exhaust server resources.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_2,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect(("127.0.0.1", unused_port))

    # Send HTTP/2 connection preface
    sock.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

    config = h2.config.H2Configuration(client_side=True)
    conn = h2.connection.H2Connection(config=config)
    conn.initiate_connection()
    sock.sendall(conn.data_to_send())

    try:
        # Send many SETTINGS frames
        for _ in range(100):
            conn.update_settings(
                {h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 65536}
            )

        sock.sendall(conn.data_to_send())

        # Server should handle this gracefully
        data = sock.recv(4096)
        # May receive GOAWAY or RST_STREAM if server detects abuse
        assert len(data) > 0

    except (ConnectionResetError, BrokenPipeError):
        # Server may close connection - acceptable
        pass
    finally:
        sock.close()


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_http2_priority_tree_manipulation(unused_port: int):
    """Test handling of complex priority tree (potential CPU DoS).

    Attack: Create complex stream dependencies to exhaust CPU.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_2,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect(("127.0.0.1", unused_port))

    sock.sendall(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

    config = h2.config.H2Configuration(client_side=True)
    conn = h2.connection.H2Connection(config=config)
    conn.initiate_connection()
    sock.sendall(conn.data_to_send())

    try:
        # Create a chain of dependent streams
        for i in range(50):
            stream_id = 1 + (i * 2)
            # Stream 0 is connection stream, avoid self-dependency
            depends_on = 0 if i == 0 else stream_id - 2

            conn.send_headers(
                stream_id,
                [
                    (":method", "GET"),
                    (":path", "/"),
                    (":scheme", "http"),
                    (":authority", "localhost"),
                ],
                priority_weight=16,
                priority_depends_on=depends_on,
                priority_exclusive=False,
            )

        sock.sendall(conn.data_to_send())

        # Server should handle complex priority trees
        data = sock.recv(4096)
        assert len(data) > 0

    finally:
        sock.close()


# ============================================================================
# TLS/SSL Security Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_tls_cipher_suite_security(unused_port: int, temp_ssl_cert):
    """Test that only secure cipher suites are allowed.

    Note: This test uses a temporary self-signed certificate to verify
    cipher configuration.
    """
    from asgiri.ssl_utils import create_ssl_context

    cert_path, key_path = temp_ssl_cert

    # Create SSL context with temporary certificate
    ssl_context = create_ssl_context(
        certfile=str(cert_path), keyfile=str(key_path)
    )

    # Get configured ciphers
    ciphers = ssl_context.get_ciphers()

    # Check that weak ciphers are not included
    weak_algorithms = [
        "DES",
        "3DES",
        "RC4",
        "MD5",
        "NULL",
        "EXPORT",
        "anon",
    ]

    for cipher in ciphers:
        cipher_name = cipher["name"].upper()
        for weak in weak_algorithms:
            assert (
                weak not in cipher_name
            ), f"Weak cipher detected: {cipher_name}"

    # Verify TLS 1.2+ is required
    if hasattr(ssl_context, "minimum_version"):
        assert ssl_context.minimum_version >= ssl.TLSVersion.TLSv1_2


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_tls_version_enforcement(unused_port: int, temp_ssl_cert):
    """Test that old TLS versions are rejected.

    This test verifies SSL/TLS version configuration using a temporary
    self-signed certificate.
    """
    from asgiri.ssl_utils import create_ssl_context

    cert_path, key_path = temp_ssl_cert

    # Create SSL context with temporary certificate
    ssl_context = create_ssl_context(
        certfile=str(cert_path), keyfile=str(key_path)
    )

    # Check minimum TLS version
    # In Python 3.7+, we can check the minimum version
    if hasattr(ssl_context, "minimum_version"):
        # Should be at least TLS 1.2
        assert ssl_context.minimum_version >= ssl.TLSVersion.TLSv1_2


# ============================================================================
# Input Validation Security Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_null_byte_injection(unused_port: int):
    """Test handling of null bytes in request paths."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Raw socket to send null bytes
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(("127.0.0.1", unused_port))

    try:
        # Send request with null byte
        malicious_request = (
            b"GET /safe.txt\x00../../etc/passwd HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n"
        )
        sock.sendall(malicious_request)

        response = sock.recv(4096)
        # Should either reject (400) or safely handle
        assert b"HTTP/1.1" in response
        assert b"etc/passwd" not in response

    finally:
        sock.close()


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_unicode_normalization_attack(unused_port: int):
    """Test handling of Unicode normalization attacks in paths."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Unicode slash: U+2044 (fraction slash)
    # URL encoded: %E2%81%84
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            # Should not allow directory traversal via Unicode
            response = await client.get(
                f"http://127.0.0.1:{unused_port}/%E2%81%84admin"
            )
            # Path should be treated safely
            assert response.status_code in [200, 404]
        except httpx.HTTPError:
            # Connection errors acceptable
            pass


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_excessive_url_length(unused_port: int):
    """Test handling of excessively long URLs (DoS vector)."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Create a very long URL (100KB)
    long_path = "/" + "a" * (100 * 1024)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(("127.0.0.1", unused_port))

    try:
        request = (
            f"GET {long_path} HTTP/1.1\r\n" f"Host: localhost\r\n" f"\r\n"
        ).encode()
        sock.sendall(request)

        response = sock.recv(4096)
        # Server should reject or handle safely
        assert b"HTTP/1.1" in response
        # Should be 414 URI Too Long, 431 Header Too Large, or 400 Bad Request
        assert b"414" in response or b"431" in response or b"400" in response

    except (ConnectionResetError, BrokenPipeError):
        # Server may close connection - acceptable
        pass
    finally:
        sock.close()


# ============================================================================
# Information Disclosure Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_error_message_information_disclosure(unused_port: int):
    """Test that error messages don't leak sensitive information."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Request non-existent endpoint
        response = await client.get(
            f"http://127.0.0.1:{unused_port}/nonexistent"
        )

        # Check that error doesn't expose internal details
        body = response.text.lower()

        # Should not contain file paths
        assert "c:\\" not in body and "/home/" not in body
        assert "traceback" not in body
        assert "exception" not in body

        # Should not expose server version details
        server_header = response.headers.get("server", "").lower()
        # It's OK to have "asgiri" but not version numbers
        if "asgiri" in server_header:
            # Check it doesn't have detailed version
            import re

            assert not re.search(r"\d+\.\d+\.\d+", server_header)


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_server_header_minimal_disclosure(unused_port: int):
    """Test that Server header doesn't expose too much information."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"http://127.0.0.1:{unused_port}/")

        # Check Server header if present
        server_header = response.headers.get("server", "").lower()

        # Server header should not contain version numbers or detailed info
        import re

        # Should not have detailed version numbers like 1.2.3
        assert not re.search(
            r"\d+\.\d+\.\d+", server_header
        ), f"Server header contains version number: {server_header}"

        # If it says "asgiri", that's OK, but no implementation details
        if server_header:
            # Should be simple like "asgiri" not "asgiri/1.2.3 (python 3.11)"
            parts = server_header.split()
            assert (
                len(parts) <= 2
            ), f"Server header too detailed: {server_header}"


# ============================================================================
# WebSocket Security Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_websocket_message_size_limit(unused_port: int):
    """Test handling of extremely large WebSocket messages (DoS).

    This test attempts to send a very large message to check
    if the server has size limits or handles large messages gracefully.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    try:
        # Connect to WebSocket endpoint
        uri = f"ws://127.0.0.1:{unused_port}/ws"
        async with asyncio.timeout(10.0):
            async with websockets.connect(uri) as websocket:
                # Try to send a very large message (10 MB)
                large_message = "A" * (10 * 1024 * 1024)

                try:
                    await websocket.send(large_message)
                    # Server should either accept and handle it, or reject it
                    # If it accepts, try to receive response
                    response = await asyncio.wait_for(
                        websocket.recv(), timeout=5.0
                    )
                    # If we get here, server handled large message
                    # Just verify it didn't crash
                    assert response is not None
                except (
                    websockets.exceptions.ConnectionClosed,
                    asyncio.TimeoutError,
                ):
                    # Server may have closed connection or timed out
                    # This is acceptable behavior
                    pass

    except (
        websockets.exceptions.WebSocketException,
        ConnectionRefusedError,
        OSError,
        asyncio.TimeoutError,
    ):
        # Connection errors are acceptable (server may reject large messages)
        pass


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_origin_validation(unused_port: int):
    """Test WebSocket Origin header validation.

    The server should validate Origin headers to prevent
    cross-site WebSocket hijacking (CSWSH).
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Test with malicious origin
    malicious_origins = [
        "http://evil.com",
        "https://attacker.example.com",
        "http://localhost.evil.com",
    ]

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    for origin in malicious_origins:
        try:
            # Try to connect with malicious origin header
            async with asyncio.timeout(5.0):
                async with websockets.connect(
                    uri,
                    additional_headers={"Origin": origin},
                ) as websocket:
                    # If connection succeeds, server accepted the origin
                    # Send a test message
                    await websocket.send("test")
                    response = await asyncio.wait_for(
                        websocket.recv(), timeout=2.0
                    )

                    # Note: This test currently just checks that the server
                    # doesn't crash. Real origin validation would be
                    # implemented at the application level in most cases.
                    assert response is not None

        except (
            websockets.exceptions.WebSocketException,
            asyncio.TimeoutError,
        ):
            # Server rejected the connection - this is acceptable
            pass


# ============================================================================
# Denial of Service Tests
# ============================================================================


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(20)
@pytest.mark.asyncio
async def test_connection_exhaustion(unused_port: int):
    """Test server behavior under connection exhaustion."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Open many connections
    connections = []
    try:
        # Try to open 100 connections
        for _ in range(100):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15.0)
            sock.connect(("127.0.0.1", unused_port))
            connections.append(sock)

        # Server should still accept new connections
        # (or have a reasonable limit)
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"http://127.0.0.1:{unused_port}/")
            # Should either succeed or get a reasonable error
            assert response.status_code in [200, 503, 429]

    finally:
        for sock in connections:
            try:
                sock.close()
            except Exception:
                pass


@pytest.mark.slow
@pytest.mark.security
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_request_rate_limiting(unused_port: int):
    """Test if server handles request floods gracefully.

    Note: This doesn't test rate limiting implementation
    (which is typically app-level), but server stability
    under high request volume.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
        lifespan=LifespanPolicy.DISABLED,
    )

    def run_server():
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port)

    # Send many requests rapidly
    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = []
        for _ in range(100):
            task = client.get(f"http://127.0.0.1:{unused_port}/")
            tasks.append(task)

        # Execute all requests concurrently
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Server should handle all requests (though some may fail)
        successful = sum(
            1
            for r in responses
            if not isinstance(r, Exception) and r.status_code == 200
        )

        # At least some should succeed
        assert successful > 50, f"Only {successful}/100 requests succeeded"
