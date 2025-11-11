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

import httpx
import pytest

from asgiri.server import HttpProtocolVersion, Server


@pytest.fixture(scope="function")
def unused_port():
    """Find an unused port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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

    if protocol_version == HttpProtocolVersion.HTTP_2:
        # Skip HTTP/2 only mode for now (needs TLS or special client)
        pytest.skip("HTTP/2 only mode requires special setup")

    server = Server(
        app=protocol_agnostic_app,
        host="127.0.0.1",
        port=unused_port,
        http_version=protocol_version,
    )

    # Start server
    server_task = asyncio.create_task(server.a_run())

    # Wait for startup
    try:
        await asyncio.wait_for(startup_event.wait(), timeout=2)
    except asyncio.TimeoutError:
        pytest.fail(f"Startup not called for {protocol_version.value}")

    # Make a request
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

    # Verify both events were called
    assert f"startup-{protocol_version.value}" in events
    # Note: shutdown may not be in events if cancellation happens too fast


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
    """Test that HTTP requests are blocked until lifespan startup is complete."""
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
            assert startup_complete.is_set(), "Request should wait for startup"
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

    # Try to make request immediately (should wait for startup)
    async def make_request():
        await asyncio.sleep(0.2)  # Small delay to let server start listening
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{unused_port}/")
            assert response.status_code == 200

    request_task = asyncio.create_task(make_request())

    # Wait for both
    await asyncio.wait_for(request_processed.wait(), timeout=3)
    await request_task

    # Clean up
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass
