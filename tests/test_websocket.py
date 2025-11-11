"""
WebSocket tests for asgiri server.

Tests WebSocket functionality over both HTTP/1.1 and HTTP/2 protocols.
"""

import asyncio
import socket
import threading
import time

import pytest
from websockets.asyncio.client import connect

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


@pytest.fixture
def server_factory():
    """Factory fixture to create and manage test servers."""
    servers = []
    threads = []

    def _create_server(port: int, protocol_version: HttpProtocolVersion):
        server = Server(
            app=app, host="127.0.0.1", port=port, http_version=protocol_version
        )
        servers.append(server)

        server_thread = threading.Thread(target=server.run, daemon=True)
        threads.append(server_thread)
        server_thread.start()

        # Wait for server to be ready
        assert wait_for_server("127.0.0.1", port), f"Server failed to start on port {port}"

        return server

    yield _create_server

    # Cleanup - threads are daemon so they'll exit with the test process


# ============================================================================
# WebSocket Tests over HTTP/1.1
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_basic_echo_http11(unused_port: int, server_factory):
    """Test basic WebSocket echo over HTTP/1.1."""
    server_factory(unused_port, HttpProtocolVersion.HTTP_1_1)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send a message
        test_message = "Hello WebSocket!"
        await websocket.send(test_message)

        # Receive echo response
        response = await websocket.recv()
        assert response == f"Message text was: {test_message}"


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_multiple_messages_http11(unused_port: int, server_factory):
    """Test multiple WebSocket messages over HTTP/1.1."""
    server_factory(unused_port, HttpProtocolVersion.HTTP_1_1)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        messages = ["First message", "Second message", "Third message"]

        for msg in messages:
            await websocket.send(msg)
            response = await websocket.recv()
            assert response == f"Message text was: {msg}"


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_bidirectional_http11(unused_port: int, server_factory):
    """Test bidirectional WebSocket communication over HTTP/1.1."""
    server_factory(unused_port, HttpProtocolVersion.HTTP_1_1)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send multiple messages without waiting for responses
        messages = ["msg1", "msg2", "msg3"]
        for msg in messages:
            await websocket.send(msg)

        # Now receive all responses
        responses = []
        for _ in messages:
            response = await websocket.recv()
            responses.append(response)

        expected = [f"Message text was: {msg}" for msg in messages]
        assert responses == expected


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_large_message_http11(unused_port: int, server_factory):
    """Test WebSocket with large message over HTTP/1.1."""
    server_factory(unused_port, HttpProtocolVersion.HTTP_1_1)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send a large message
        large_message = "A" * 10000  # 10KB message
        await websocket.send(large_message)

        response = await websocket.recv()
        assert response == f"Message text was: {large_message}"
        assert len(response) > 10000


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_close_http11(unused_port: int, server_factory):
    """Test WebSocket clean close over HTTP/1.1."""
    server_factory(unused_port, HttpProtocolVersion.HTTP_1_1)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        await websocket.send("Test message")
        response = await websocket.recv()
        assert "Test message" in response

        # Connection should close cleanly when exiting context manager


# ============================================================================
# WebSocket Tests over HTTP/2 (AUTO mode)
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_basic_echo_http2(unused_port: int, server_factory):
    """Test basic WebSocket echo over HTTP/2 (AUTO mode)."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send a message
        test_message = "Hello WebSocket over HTTP/2!"
        await websocket.send(test_message)

        # Receive echo response
        response = await websocket.recv()
        assert response == f"Message text was: {test_message}"


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_multiple_messages_http2(unused_port: int, server_factory):
    """Test multiple WebSocket messages over HTTP/2 (AUTO mode)."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        messages = [
            "First HTTP/2 message",
            "Second HTTP/2 message",
            "Third HTTP/2 message",
        ]

        for msg in messages:
            await websocket.send(msg)
            response = await websocket.recv()
            assert response == f"Message text was: {msg}"


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_bidirectional_http2(unused_port: int, server_factory):
    """Test bidirectional WebSocket communication over HTTP/2 (AUTO mode)."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send multiple messages without waiting for responses
        messages = ["h2_msg1", "h2_msg2", "h2_msg3", "h2_msg4"]
        for msg in messages:
            await websocket.send(msg)

        # Now receive all responses
        responses = []
        for _ in messages:
            response = await websocket.recv()
            responses.append(response)

        expected = [f"Message text was: {msg}" for msg in messages]
        assert responses == expected


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_large_message_http2(unused_port: int, server_factory):
    """Test WebSocket with large message over HTTP/2 (AUTO mode)."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send a large message
        large_message = "B" * 50000  # 50KB message
        await websocket.send(large_message)

        response = await websocket.recv()
        assert response == f"Message text was: {large_message}"
        assert len(response) > 50000


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_concurrent_connections_http2(unused_port: int, server_factory):
    """Test multiple concurrent WebSocket connections over HTTP/2."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async def client_task(client_id: int):
        """Individual client task."""
        async with connect(uri) as websocket:
            message = f"Client {client_id} message"
            await websocket.send(message)
            response = await websocket.recv()
            assert response == f"Message text was: {message}"
            return client_id

    # Create 5 concurrent WebSocket connections
    tasks = [client_task(i) for i in range(5)]
    results = await asyncio.gather(*tasks)

    # Verify all clients completed successfully
    assert len(results) == 5
    assert results == list(range(5))


# ============================================================================
# Protocol Comparison Tests
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_protocol_comparison():
    """Compare WebSocket behavior between HTTP/1.1 and AUTO modes."""
    test_message = "Protocol test message"

    # Get two different unused ports
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s1:
        s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s1.bind(("", 0))
        port1 = s1.getsockname()[1]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
        s2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s2.bind(("", 0))
        port2 = s2.getsockname()[1]

    # Test with HTTP/1.1
    server1 = Server(
        app=app, host="127.0.0.1", port=port1, http_version=HttpProtocolVersion.HTTP_1_1
    )
    thread1 = threading.Thread(target=server1.run, daemon=True)
    thread1.start()
    assert wait_for_server("127.0.0.1", port1), "Server 1 failed to start"

    uri1 = f"ws://127.0.0.1:{port1}/ws"
    async with connect(uri1) as websocket:
        await websocket.send(test_message)
        response1 = await websocket.recv()

    # Test with AUTO (HTTP/2 capable)
    server2 = Server(
        app=app, host="127.0.0.1", port=port2, http_version=HttpProtocolVersion.AUTO
    )
    thread2 = threading.Thread(target=server2.run, daemon=True)
    thread2.start()
    assert wait_for_server("127.0.0.1", port2), "Server 2 failed to start"

    uri2 = f"ws://127.0.0.1:{port2}/ws"
    async with connect(uri2) as websocket:
        await websocket.send(test_message)
        response2 = await websocket.recv()

    # Both should produce the same result
    assert response1 == response2
    assert response1 == f"Message text was: {test_message}"


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_rapid_messages(unused_port: int, server_factory):
    """Test rapid succession of WebSocket messages."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send many messages rapidly
        num_messages = 20
        for i in range(num_messages):
            await websocket.send(f"Message {i}")

        # Receive all responses
        for i in range(num_messages):
            response = await websocket.recv()
            assert response == f"Message text was: Message {i}"


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_empty_message(unused_port: int, server_factory):
    """Test sending empty WebSocket message."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Send empty message
        await websocket.send("")
        response = await websocket.recv()
        assert response == "Message text was: "


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_websocket_special_characters(unused_port: int, server_factory):
    """Test WebSocket with special characters."""
    server_factory(unused_port, HttpProtocolVersion.AUTO)

    uri = f"ws://127.0.0.1:{unused_port}/ws"

    async with connect(uri) as websocket:
        # Test various special characters
        special_messages = [
            "Hello 🌍 World!",
            "Special chars: @#$%^&*()",
            "Newline\nCharacters\nTest",
            "Tab\tSeparated\tValues",
            "Unicode: café, naïve, 日本語",
        ]

        for msg in special_messages:
            await websocket.send(msg)
            response = await websocket.recv()
            assert response == f"Message text was: {msg}"
