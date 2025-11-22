"""
Tests for HTTP header size limits.

This test verifies that the server correctly handles:
1. Requests with normal-sized headers (should succeed)
2. Requests with legitimately large headers (should get 431)
3. Requests with large bodies but normal headers (should succeed)
"""

import io
import socket
import threading
import time

import httpx
import pytest

from asgiri.server import HttpProtocolVersion, Server

from .app import app


@pytest.fixture(scope="function")
def unused_port():
    """Find an unused port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        return s.getsockname()[1]


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


@pytest.mark.timeout(10)
def test_normal_headers_with_large_body(unused_port: int):
    """
    Test that requests with normal-sized headers but large bodies work.

    This is the issue identified in the load tests - file uploads were
    failing with 431 errors because the body data was being counted as
    header data.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
    time.sleep(0.5)

    try:
        # Create a large file (100KB) to upload
        large_content = b"X" * (100 * 1024)

        # Test raw POST upload (this works reliably)
        with httpx.Client() as client:
            response = client.post(
                f"http://127.0.0.1:{unused_port}/uploadfile/rawpost",
                content=large_content,
                headers={"Content-Type": "application/octet-stream"},
                timeout=10.0,
            )

            # Should succeed with 200, not 431
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}. "
                f"Response: {response.text}"
            )

            data = response.json()
            assert data["content_size"] == len(large_content)
            assert "sha256" in data

    finally:
        # Shutdown server
        if hasattr(server, "should_exit") and server.should_exit:
            server.should_exit.set()


@pytest.mark.timeout(10)
def test_multipart_form_upload_with_large_body(unused_port: int):
    """
    Test multipart form uploads with large bodies.

    Separate test for multipart to isolate any multipart-specific issues.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
    time.sleep(0.5)

    try:
        # Create a large file (100KB) to upload
        large_content = b"X" * (100 * 1024)

        # Test multipart form upload
        with httpx.Client() as client:
            files = {
                "file": (
                    "test_large.bin",
                    io.BytesIO(large_content),
                    "application/octet-stream",
                )
            }
            response = client.post(
                f"http://127.0.0.1:{unused_port}/uploadfile/formpost",
                files=files,
                timeout=10.0,
            )

            # Should succeed with 200, not 431
            assert response.status_code == 200, (
                f"Expected 200, got {response.status_code}. "
                f"Response: {response.text}"
            )

            data = response.json()
            assert data["content_size"] == len(large_content)
            assert "sha256" in data

    finally:
        # Shutdown server
        if hasattr(server, "should_exit") and server.should_exit:
            server.should_exit.set()


@pytest.mark.timeout(10)
def test_very_large_body_succeeds(unused_port: int):
    """
    Test that even very large request bodies (1MB+) work correctly.

    This ensures we can handle realistic file uploads without 431 errors.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
    time.sleep(0.5)

    try:
        # Create a 1MB file to upload
        large_content = b"Y" * (1024 * 1024)

        # Test raw POST upload
        with httpx.Client() as client:
            response = client.post(
                f"http://127.0.0.1:{unused_port}/uploadfile/rawpost",
                content=large_content,
                headers={"Content-Type": "application/octet-stream"},
                timeout=15.0,
            )

            # Should succeed with 200, not 431
            assert (
                response.status_code == 200
            ), f"Expected 200, got {response.status_code}"

            data = response.json()
            assert data["content_size"] == len(large_content)

    finally:
        # Shutdown server
        if hasattr(server, "should_exit") and server.should_exit:
            server.should_exit.set()


@pytest.mark.timeout(10)
def test_legitimately_large_headers_rejected(unused_port: int):
    """
    Test that requests with legitimately huge headers are rejected with 431.

    This ensures our security measure still works - we reject requests with
    headers that are actually too large.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
    time.sleep(0.5)

    try:
        # Create a request with legitimately large headers (> 16KB)
        # Each header can be quite large
        large_header_value = "X" * (20 * 1024)  # 20KB header value

        with httpx.Client() as client:
            response = client.get(
                f"http://127.0.0.1:{unused_port}/",
                headers={"X-Large-Header": large_header_value},
                timeout=10.0,
            )

            # Should be rejected with 431
            assert (
                response.status_code == 431
            ), f"Expected 431 for large headers, got {response.status_code}"

    finally:
        # Shutdown server
        if hasattr(server, "should_exit") and server.should_exit:
            server.should_exit.set()


@pytest.mark.timeout(10)
def test_normal_request_succeeds(unused_port: int):
    """
    Test that normal requests with reasonable headers work fine.
    """
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for server to be ready
    assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
    time.sleep(0.5)

    try:
        with httpx.Client() as client:
            response = client.get(
                f"http://127.0.0.1:{unused_port}/",
                timeout=10.0,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["message"] == "Welcome to the test ASGI application!"

    finally:
        # Shutdown server
        if hasattr(server, "should_exit") and server.should_exit:
            server.should_exit.set()
