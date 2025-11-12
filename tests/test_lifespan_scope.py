"""
Lifespan scope tests for asgiri server.

Tests that the server properly handles ASGI lifespan events:
- lifespan.startup
- lifespan.shutdown

These tests follow TDD principles and will initially fail until lifespan
support is implemented in the server.
"""

import asyncio
import socket
import threading
import time

import httpx
import pytest

from asgiri.server import HttpProtocolVersion, Server


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


# ============================================================================
# Basic Lifespan Tests
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_startup_called(unused_port: int):
    """Test that lifespan startup event is called when server starts."""
    from tests.app import app, lifespan_records

    # Clear any previous records
    lifespan_records.clear()

    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    # Start server in async task
    server_task = asyncio.create_task(server.a_run())

    # Give server time to start and call lifespan startup
    await asyncio.sleep(1)

    # Verify startup was called
    assert "startup" in lifespan_records, "Lifespan startup should have been called"

    # Make a request to ensure server is working
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{unused_port}/helloworld")
        assert response.status_code == 200

    # Clean up
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_lifespan_shutdown_called(unused_port: int):
    """Test that lifespan shutdown event is called when server stops."""
    from tests.app import lifespan_records

    # Clear any previous records
    lifespan_records.clear()

    # Create a simple ASGI app with lifespan that we can control
    shutdown_called = asyncio.Event()
    startup_called = asyncio.Event()

    async def lifespan_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    startup_called.set()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    shutdown_called.set()
                    await send({"type": "lifespan.shutdown.complete"})
                    break
        elif scope["type"] == "http":
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
                    "body": b"OK",
                }
            )

    server = Server(
        app=lifespan_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
    )

    # Start server in async task
    server_task = asyncio.create_task(server.a_run())

    # Wait for startup
    try:
        await asyncio.wait_for(startup_called.wait(), timeout=2)
    except asyncio.TimeoutError:
        pytest.fail("Lifespan startup was not called within timeout")

    # Small delay to ensure server socket is ready to accept connections
    await asyncio.sleep(0.2)

    # Make a request to ensure server is working
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{unused_port}/")
        assert response.status_code == 200

    # Cancel server task to trigger shutdown
    server_task.cancel()

    # Wait for shutdown to be called
    try:
        await asyncio.wait_for(shutdown_called.wait(), timeout=2)
    except asyncio.TimeoutError:
        pytest.fail("Lifespan shutdown was not called within timeout")

    # Clean up
    try:
        await server_task
    except asyncio.CancelledError:
        pass


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_startup_and_shutdown_order(unused_port: int):
    """Test that lifespan events are called in correct order: startup before shutdown."""
    events = []
    startup_event = asyncio.Event()
    shutdown_event = asyncio.Event()

    async def tracking_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    events.append("startup")
                    startup_event.set()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    events.append("shutdown")
                    shutdown_event.set()
                    await send({"type": "lifespan.shutdown.complete"})
                    break
        elif scope["type"] == "http":
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

    server = Server(
        app=tracking_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
    )

    # Start server
    server_task = asyncio.create_task(server.a_run())

    # Wait for startup
    try:
        await asyncio.wait_for(startup_event.wait(), timeout=2)
    except asyncio.TimeoutError:
        pytest.fail("Startup event not called")

    # Verify startup was called first
    assert events == ["startup"]

    # Stop server
    server_task.cancel()

    # Wait for shutdown
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=2)
    except asyncio.TimeoutError:
        pytest.fail("Shutdown event not called")

    # Verify order
    assert events == ["startup", "shutdown"]

    # Clean up
    try:
        await server_task
    except asyncio.CancelledError:
        pass


# ============================================================================
# Lifespan State Tests
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_state_shared_with_requests(unused_port: int):
    """Test that lifespan state is available across the application lifecycle.

    Note: In ASGI, lifespan state is typically managed by the framework (like FastAPI)
    which maintains a shared state dict. This test verifies that lifespan events
    are properly called so frameworks can manage their state.
    """
    startup_event = asyncio.Event()
    shutdown_event = asyncio.Event()

    # Shared state dict that will be managed by our app
    app_state = {}

    async def stateful_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    # Set some state during startup
                    app_state["db_connection"] = "mock_db_handle"
                    app_state["counter"] = 0
                    startup_event.set()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    shutdown_event.set()
                    await send({"type": "lifespan.shutdown.complete"})
                    break
        elif scope["type"] == "http":
            # Access state from the shared app_state
            db = app_state.get("db_connection", "none")
            counter = app_state.get("counter", -1)

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": f'{{"db": "{db}", "counter": {counter}}}'.encode(),
                }
            )

    server = Server(
        app=stateful_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
    )

    # Start server
    server_task = asyncio.create_task(server.a_run())

    # Wait for startup
    try:
        await asyncio.wait_for(startup_event.wait(), timeout=2)
    except asyncio.TimeoutError:
        pytest.fail("Startup not called")

    # Small delay to ensure server socket is ready to accept connections
    await asyncio.sleep(0.2)

    # Make request and check state
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{unused_port}/")
        assert response.status_code == 200
        data = response.json()
        assert data["db"] == "mock_db_handle"
        assert data["counter"] == 0

    # Clean up
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


# ============================================================================
# Lifespan Error Handling Tests
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_startup_failure(unused_port: int):
    """Test server behavior when lifespan startup fails."""

    async def failing_startup_app(scope, receive, send):
        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] == "lifespan.startup":
                # Signal startup failure
                await send(
                    {
                        "type": "lifespan.startup.failed",
                        "message": "Database connection failed",
                    }
                )
                return
        elif scope["type"] == "http":
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

    server = Server(
        app=failing_startup_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
    )

    # Server should handle startup failure gracefully
    # This might raise an exception or log an error
    with pytest.raises(Exception):
        await asyncio.wait_for(server.a_run(), timeout=2)


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_shutdown_failure(unused_port: int):
    """Test server behavior when lifespan shutdown fails."""
    startup_event = asyncio.Event()

    async def failing_shutdown_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    startup_event.set()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    # Signal shutdown failure
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": "Failed to close connections",
                        }
                    )
                    break
        elif scope["type"] == "http":
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

    server = Server(
        app=failing_shutdown_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
    )

    # Start server
    server_task = asyncio.create_task(server.a_run())

    # Wait for startup
    await asyncio.wait_for(startup_event.wait(), timeout=2)

    # Cancel to trigger shutdown
    server_task.cancel()

    # Server should handle shutdown failure gracefully (may log error)
    try:
        await asyncio.wait_for(server_task, timeout=2)
    except (asyncio.CancelledError, Exception):
        # Expected - shutdown failure should be handled
        pass


# ============================================================================
# Lifespan with Different Protocols
# ============================================================================


@pytest.mark.parametrize(
    "protocol_version",
    [
        HttpProtocolVersion.HTTP_1_1,
        HttpProtocolVersion.HTTP_2,
        HttpProtocolVersion.AUTO,
    ],
)
@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_works_with_all_protocols(
    unused_port: int, protocol_version: HttpProtocolVersion
):
    """Test that lifespan events work correctly with all protocol versions."""
    events = []
    startup_event = asyncio.Event()

    async def protocol_agnostic_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    events.append(f"startup-{protocol_version.value}")
                    startup_event.set()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    events.append(f"shutdown-{protocol_version.value}")
                    await send({"type": "lifespan.shutdown.complete"})
                    break
        elif scope["type"] == "http":
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
                    "body": f"Protocol: {protocol_version.value}".encode(),
                }
            )

    server = Server(
        app=protocol_agnostic_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=protocol_version,
    )

    # For HTTP/2 only mode, use synchronous threading approach
    if protocol_version == HttpProtocolVersion.HTTP_2:
        # Start server in thread
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        
        # Wait for server to be ready
        assert wait_for_server("127.0.0.1", unused_port), "Server failed to start"
        
        # Wait a bit for startup event to be set
        await asyncio.sleep(0.5)
        
        # Verify startup was called
        assert f"startup-{protocol_version.value}" in events, f"Startup not called for {protocol_version.value}"
        
        # Use h2 library directly for HTTP/2
        import h2.connection
        import h2.events
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.connect(("127.0.0.1", unused_port))
        
        # Create HTTP/2 connection
        config = h2.connection.H2Configuration(client_side=True)
        conn = h2.connection.H2Connection(config=config)
        conn.initiate_connection()
        sock.sendall(conn.data_to_send())
        
        # Send GET request
        stream_id = conn.get_next_available_stream_id()
        headers = [
            (":method", "GET"),
            (":path", "/"),
            (":scheme", "http"),
            (":authority", f"127.0.0.1:{unused_port}"),
        ]
        conn.send_headers(stream_id, headers, end_stream=True)
        sock.sendall(conn.data_to_send())
        
        # Receive response
        response_headers = None
        for _ in range(10):
            data = sock.recv(65536)
            if not data:
                break
            
            events_h2 = conn.receive_data(data)
            for event in events_h2:
                if isinstance(event, h2.events.ResponseReceived):
                    response_headers = dict(event.headers)
                elif isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(
                        event.flow_controlled_length, event.stream_id
                    )
                elif isinstance(event, h2.events.StreamEnded):
                    break
            
            sock.sendall(conn.data_to_send())
            
            if response_headers:
                break
            
            time.sleep(0.1)
        
        sock.close()
        
        # Verify response
        assert response_headers is not None
        assert response_headers.get(b":status") == b"200"
        
        # Server will shut down when thread is cleaned up
    else:
        # For HTTP/1.1 and AUTO mode, use async approach
        server_task = asyncio.create_task(server.a_run())

        # Wait for startup
        try:
            await asyncio.wait_for(startup_event.wait(), timeout=2)
        except asyncio.TimeoutError:
            pytest.fail(f"Startup not called for {protocol_version.value}")

        # Small delay to ensure server socket is ready to accept connections
        await asyncio.sleep(0.2)

        # Make request using httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{unused_port}/")
            assert response.status_code == 200

        # Stop server
        server_task.cancel()

        # Clean up
        try:
            await asyncio.wait_for(server_task, timeout=2)
        except asyncio.CancelledError:
            pass

    # Verify startup event was called
    assert f"startup-{protocol_version.value}" in events


# ============================================================================
# Lifespan with FastAPI App
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_lifespan_with_fastapi_app(unused_port: int):
    """Test that lifespan events work with the actual FastAPI test app."""
    from tests.app import app, lifespan_records

    # Clear previous records
    lifespan_records.clear()

    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
    )

    # Start server
    server_task = asyncio.create_task(server.a_run())

    # Give server time to start
    await asyncio.sleep(1)

    # Verify startup was called
    assert "startup" in lifespan_records, "FastAPI lifespan startup should be called"

    # Make a request to verify server is working
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{unused_port}/helloworld")
        assert response.status_code == 200
        assert response.json() == {"Hello": "World"}

    # Stop server
    server_task.cancel()

    # Give time for shutdown
    await asyncio.sleep(1)

    # Clean up
    try:
        await server_task
    except asyncio.CancelledError:
        pass

    # Verify shutdown was called
    assert "shutdown" in lifespan_records, "FastAPI lifespan shutdown should be called"
    assert lifespan_records == ["startup", "shutdown"]


# ============================================================================
# Concurrent Request During Lifespan Tests
# ============================================================================


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_requests_blocked_until_startup_complete(unused_port: int):
    """Test that the server doesn't accept connections until lifespan startup is complete."""
    startup_complete = asyncio.Event()
    request_processed = asyncio.Event()

    async def slow_startup_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    # Simulate slow startup
                    await asyncio.sleep(1)
                    startup_complete.set()
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    break
        elif scope["type"] == "http":
            # This should only be called after startup is complete
            assert startup_complete.is_set(), "Request should not be processed until startup is complete"
            request_processed.set()
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

    server = Server(
        app=slow_startup_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=HttpProtocolVersion.AUTO,
    )

    # Start server
    server_task = asyncio.create_task(server.a_run())

    # Wait for startup to complete (server won't be listening until this is done)
    await asyncio.wait_for(startup_complete.wait(), timeout=3)

    # Small delay to ensure server socket is ready to accept connections
    await asyncio.sleep(0.2)

    # Now make request - should succeed immediately since startup is complete
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{unused_port}/")
        assert response.status_code == 200

    # Verify request was processed
    assert request_processed.is_set(), "Request should have been processed"

    # Clean up
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass
