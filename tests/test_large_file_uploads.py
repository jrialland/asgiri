"""
Test large file uploads for HTTP/1.1 and HTTP/2 protocols.

This test module verifies that the server can handle file uploads
of various sizes (from 10KB to 1MB) without truncation or errors.

Tests both HTTP/1.1 (using httpx) and HTTP/2 (using raw h2 library).
"""

import socket
import threading
import time

import h2.config
import h2.connection
import h2.events
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


@pytest.mark.parametrize(
    "size_kb,protocol",
    [
        # HTTP/1.1 tests
        (10, HttpProtocolVersion.HTTP_1_1),
        (64, HttpProtocolVersion.HTTP_1_1),
        (100, HttpProtocolVersion.HTTP_1_1),
        (500, HttpProtocolVersion.HTTP_1_1),
        (1024, HttpProtocolVersion.HTTP_1_1),
        # HTTP/2 tests
        (10, HttpProtocolVersion.HTTP_2),
        (100, HttpProtocolVersion.HTTP_2),
        (1024, HttpProtocolVersion.HTTP_2),
    ],
)
@pytest.mark.timeout(15)
def test_file_upload_sizes(unused_port: int, size_kb: int, protocol):
    """Test file uploads at various sizes to find the limit."""
    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=protocol,
    )

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
    time.sleep(0.5)

    try:
        # Create file of specified size
        content = b"X" * (size_kb * 1024)
        expected_size = len(content)

        sep = "=" * 60
        msg = f"Testing {size_kb}KB upload with {protocol.value}"
        print(f"\n{sep}\n{msg}\n{sep}")

        if protocol == HttpProtocolVersion.HTTP_2:
            # Use raw h2 library for HTTP/2
            received_size = _test_http2_upload(
                unused_port, content, expected_size
            )
        else:
            # Use httpx for HTTP/1.1
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    f"http://127.0.0.1:{unused_port}/uploadfile/rawpost",
                    content=content,
                    headers={"Content-Type": "application/octet-stream"},
                )

                print(f"Response status: {response.status_code}")

                if response.status_code == 200:
                    data = response.json()
                    received_size = data["content_size"]
                    print(f"Expected size: {expected_size}")
                    print(f"Received size: {received_size}")
                    print(
                        f"Match: {'✓' if received_size == expected_size else '✗'}"  # noqa: E501
                    )

                    assert received_size == expected_size, (
                        f"Size mismatch: expected {expected_size}, "
                        f"got {received_size}"
                    )
                else:
                    print(f"Failed with status {response.status_code}")
                    print(f"Response: {response.text[:200]}")
                    msg = f"Upload failed with status {response.status_code}"
                    assert False, msg

        if protocol == HttpProtocolVersion.HTTP_2:
            print(f"Expected size: {expected_size}")
            print(f"Received size: {received_size}")
            print(f"Match: {'✓' if received_size == expected_size else '✗'}")
            assert received_size == expected_size, (
                f"Size mismatch: expected {expected_size}, "
                f"got {received_size}"
            )

    finally:
        if hasattr(server, "should_exit") and server.should_exit:
            server.should_exit.set()


def _test_http2_upload(port: int, content: bytes, expected_size: int) -> int:
    """Test HTTP/2 file upload using raw h2 library."""
    import json

    # Create a raw socket connection
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.connect(("127.0.0.1", port))

    # Create HTTP/2 connection
    config = h2.config.H2Configuration(client_side=True)
    conn = h2.connection.H2Connection(config=config)
    conn.initiate_connection()
    sock.sendall(conn.data_to_send())

    # Send a POST request for /uploadfile/rawpost
    stream_id = conn.get_next_available_stream_id()
    headers = [
        (":method", "POST"),
        (":path", "/uploadfile/rawpost"),
        (":scheme", "http"),
        (":authority", f"127.0.0.1:{port}"),
        ("content-type", "application/octet-stream"),
        ("content-length", str(len(content))),
    ]
    conn.send_headers(stream_id, headers, end_stream=False)
    sock.sendall(conn.data_to_send())

    # Send body data in chunks (simulate real upload)
    # Respect HTTP/2 flow control window
    offset = 0
    while offset < len(content):
        # Process any incoming events (like window updates) BEFORE checking window
        # This ensures we have the most up-to-date flow control window
        sock.settimeout(0.001)
        try:
            data = sock.recv(65536)
            if data:
                conn.receive_data(data)
                sock.sendall(conn.data_to_send())
        except socket.timeout:
            pass
        sock.settimeout(None)

        # Check available window using the actual current window, not initial settings
        window_size = conn.local_flow_control_window(stream_id)
        max_frame_size = conn.max_outbound_frame_size

        # Determine chunk size based on actual available window
        available = min(window_size, max_frame_size)
        chunk_size = min(available, len(content) - offset)

        if chunk_size > 0:
            chunk = content[offset : offset + chunk_size]
            is_last = offset + chunk_size >= len(content)
            conn.send_data(stream_id, chunk, end_stream=is_last)
            sock.sendall(conn.data_to_send())
            offset += chunk_size
        else:
            # Wait for flow control window to open
            time.sleep(0.01)

    # Receive response
    response_data = b""
    response_headers = None

    for _ in range(100):  # Try up to 100 times for large uploads
        data = sock.recv(65536)
        if not data:
            break

        events = conn.receive_data(data)
        for event in events:
            if isinstance(event, h2.events.ResponseReceived):
                response_headers = dict(event.headers)
            elif isinstance(event, h2.events.DataReceived):
                response_data += event.data
                conn.acknowledge_received_data(
                    event.flow_controlled_length, event.stream_id
                )
            elif isinstance(event, h2.events.StreamEnded):
                sock.close()
                # Parse JSON response
                response_json = json.loads(response_data.decode())
                return response_json["content_size"]

        sock.sendall(conn.data_to_send())

        time.sleep(0.01)

    sock.close()

    # Verify response
    assert response_headers is not None
    assert response_headers.get(b":status") == b"200"

    # Parse JSON response
    response_json = json.loads(response_data.decode())
    return response_json["content_size"]
