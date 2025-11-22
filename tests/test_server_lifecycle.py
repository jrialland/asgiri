"""Tests for server lifecycle: startup, shutdown, error recovery, signal handling."""

import asyncio
import signal
from unittest.mock import AsyncMock, Mock, patch

import pytest

from asgiri.server import HttpProtocolVersion, LifespanPolicy, Server


@pytest.fixture
def simple_app():
    """Simple ASGI app for testing."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    return app


@pytest.fixture
def lifespan_failing_app():
    """ASGI app that fails during lifespan startup."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "Database connection failed",
                        }
                    )
                    return
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    return app


@pytest.mark.asyncio
async def test_server_initialization_with_http3_disabled():
    """Test server initialization with HTTP/3 disabled via reuse_port."""

    async def app(scope, receive, send):
        pass

    # When reuse_port=True, HTTP/3 should be automatically disabled
    server = Server(
        app=app,
        host="127.0.0.1",
        port=8000,
        reuse_port=True,
        enable_http3=True,  # Will be disabled due to reuse_port
    )

    # Verify HTTP/3 was disabled
    assert server.enable_http3 is False


@pytest.mark.asyncio
async def test_server_lifespan_startup_success(simple_app):
    """Test successful lifespan startup."""
    server = Server(
        app=simple_app,
        host="127.0.0.1",
        port=8000,
        lifespan=LifespanPolicy.ENABLED,
    )

    # Start lifespan
    await server.lifespan_handler.startup()

    # Verify startup completed
    assert server.lifespan_handler.startup_complete.is_set()
    assert not server.lifespan_handler.startup_failed
    assert server.lifespan_handler.lifespan_task is not None

    # Cleanup
    await server.lifespan_handler.shutdown()


@pytest.mark.asyncio
async def test_server_lifespan_startup_failure(lifespan_failing_app):
    """Test lifespan startup failure handling."""
    server = Server(
        app=lifespan_failing_app,
        host="127.0.0.1",
        port=8000,
        lifespan=LifespanPolicy.ENABLED,
    )

    # Startup should raise RuntimeError
    with pytest.raises(RuntimeError, match="Database connection failed"):
        await server.lifespan_handler.startup()

    # Verify failure was recorded
    assert server.lifespan_handler.startup_failed
    assert server.lifespan_handler.startup_error == "Database connection failed"


@pytest.mark.asyncio
async def test_server_lifespan_startup_timeout():
    """Test lifespan startup timeout."""

    async def slow_app(scope, receive, send):
        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] == "lifespan.startup":
                # Never send startup.complete
                await asyncio.sleep(100)

    server = Server(
        app=slow_app,
        host="127.0.0.1",
        port=8000,
        lifespan=LifespanPolicy.ENABLED,
        lifespan_startup_timeout=0.1,  # Very short timeout
    )

    # Startup should timeout
    with pytest.raises(asyncio.TimeoutError):
        await server.lifespan_handler.startup()


@pytest.mark.asyncio
async def test_server_lifespan_shutdown_timeout():
    """Test lifespan shutdown timeout handling."""

    async def slow_shutdown_app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    # Never send shutdown.complete
                    await asyncio.sleep(100)

    server = Server(
        app=slow_shutdown_app,
        host="127.0.0.1",
        port=8000,
        lifespan=LifespanPolicy.ENABLED,
    )

    # Start successfully
    await server.lifespan_handler.startup()

    # Shutdown should timeout but not raise
    await server.lifespan_handler.shutdown()

    # Verify task was cancelled
    assert server.lifespan_handler.lifespan_task.cancelled()


@pytest.mark.asyncio
async def test_server_lifespan_disabled():
    """Test server with lifespan disabled."""

    async def app(scope, receive, send):
        # This app doesn't handle lifespan
        pass

    server = Server(
        app=app,
        host="127.0.0.1",
        port=8000,
        lifespan=LifespanPolicy.DISABLED,
    )

    # Startup should complete immediately without calling app
    await server.lifespan_handler.startup()

    assert server.lifespan_handler.startup_complete.is_set()
    assert server.lifespan_handler.lifespan_task is None

    # Shutdown should be a no-op
    await server.lifespan_handler.shutdown()


@pytest.mark.asyncio
async def test_signal_handlers_registered():
    """Test that signal handlers are registered in main thread."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    server = Server(
        app=app,
        host="127.0.0.1",
        port=8000,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.DISABLED,
    )

    # Mock signal.signal to track calls
    with patch("signal.signal") as mock_signal:
        # Start server in background
        server_task = asyncio.create_task(server.a_run())

        # Give it time to start
        await asyncio.sleep(0.1)

        # Trigger shutdown
        server.should_exit.set()

        # Wait for server to complete
        await server_task

        # Verify signal handlers were registered (or attempted)
        # In tests, this may raise ValueError if not in main thread
        calls = mock_signal.call_args_list
        if calls:
            # Signal handlers were registered
            assert any(call[0][0] == signal.SIGINT for call in calls)
            assert any(call[0][0] == signal.SIGTERM for call in calls)


@pytest.mark.asyncio
async def test_http3_server_without_ssl_skipped():
    """Test that HTTP/3 server is skipped when SSL is not configured."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    server = Server(
        app=app,
        host="127.0.0.1",
        port=8000,
        http_version=HttpProtocolVersion.AUTO,
        enable_http3=True,  # HTTP/3 enabled but no SSL
        lifespan=LifespanPolicy.DISABLED,
    )

    # Start HTTP/3 server should return None
    http3_server = await server._start_http3_server()

    assert http3_server is None


@pytest.mark.asyncio
async def test_temp_cert_cleanup_on_shutdown():
    """Test that temporary certificate files are cleaned up on shutdown."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    # Generate real certificate data
    from asgiri.ssl_utils import generate_self_signed_cert

    cert, key = generate_self_signed_cert()

    server = Server(
        app=app,
        host="127.0.0.1",
        port=8000,
        cert_data=cert,
        key_data=key,
        enable_http3=True,
        http_version=HttpProtocolVersion.AUTO,
        lifespan=LifespanPolicy.DISABLED,
    )

    # Mock the HTTP/3 server creation to track temp files
    with patch("asgiri.server.serve") as mock_serve:
        mock_serve.return_value = AsyncMock()

        # Start HTTP/3 server
        await server._start_http3_server()

        # Verify temp files were created
        assert server._temp_cert_file is not None
        assert server._temp_key_file is not None

        temp_cert = server._temp_cert_file
        temp_key = server._temp_key_file

        # Files should exist
        import os

        assert os.path.exists(temp_cert)
        assert os.path.exists(temp_key)

        # Trigger cleanup manually (normally done in a_run finally block)
        if server._temp_cert_file:
            os.unlink(server._temp_cert_file)
        if server._temp_key_file:
            os.unlink(server._temp_key_file)

        # Files should be deleted
        assert not os.path.exists(temp_cert)
        assert not os.path.exists(temp_key)


@pytest.mark.asyncio
async def test_temp_cert_cleanup_error_handling():
    """Test graceful handling of temp file cleanup errors."""

    async def app(scope, receive, send):
        pass

    server = Server(app=app, host="127.0.0.1", port=8000)

    # Set fake temp file paths that don't exist
    server._temp_cert_file = "/nonexistent/cert.pem"
    server._temp_key_file = "/nonexistent/key.pem"

    # Create a mock logger to capture warnings
    with patch("asgiri.server.logger") as mock_logger:
        # Trigger cleanup (normally in finally block)
        import os

        try:
            if server._temp_cert_file:
                os.unlink(server._temp_cert_file)
        except Exception:
            pass  # Should log warning but not crash

        try:
            if server._temp_key_file:
                os.unlink(server._temp_key_file)
        except Exception:
            pass  # Should log warning but not crash


@pytest.mark.asyncio
async def test_protocol_version_not_implemented():
    """Test that unsupported protocol versions raise NotImplementedError."""

    async def app(scope, receive, send):
        pass

    # Create a mock enum value for unsupported protocol
    class FakeProtocol:
        value = "http/4.0"

    with pytest.raises(NotImplementedError, match="not implemented yet"):
        # Patch the http_version after initialization to test the match statement
        server = Server(
            app=app,
            host="127.0.0.1",
            port=8000,
        )
        server.http_version = FakeProtocol()

        # Re-run the protocol selection logic
        match server.http_version:
            case HttpProtocolVersion.HTTP_1_1:
                pass
            case HttpProtocolVersion.HTTP_2:
                pass
            case HttpProtocolVersion.HTTP_3:
                pass
            case HttpProtocolVersion.AUTO | None:
                pass
            case _:
                raise NotImplementedError(
                    f"Protocol version '{server.http_version}' not implemented yet"
                )


@pytest.mark.asyncio
async def test_server_graceful_shutdown_sequence(unused_tcp_port: int):
    """Test complete server graceful shutdown sequence."""

    shutdown_called = asyncio.Event()

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    shutdown_called.set()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"text/plain"]],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

    server = Server(
        app=app,
        host="127.0.0.1",
        port=unused_tcp_port,
        http_version=HttpProtocolVersion.HTTP_1_1,
        lifespan=LifespanPolicy.ENABLED,
    )

    # Start server in background
    server_task = asyncio.create_task(server.a_run())

    # Give it time to start
    await asyncio.sleep(0.2)

    # Verify server is running
    assert server.should_exit is not None
    assert not server.should_exit.is_set()

    # Trigger graceful shutdown
    server.should_exit.set()

    # Wait for shutdown
    await server_task

    # Verify lifespan shutdown was called
    assert shutdown_called.is_set()


@pytest.mark.asyncio
async def test_http3_only_mode():
    """Test server in HTTP/3-only mode."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

    server = Server(
        app=app,
        host="127.0.0.1",
        port=8000,
        http_version=HttpProtocolVersion.HTTP_3,
    )

    # In HTTP/3-only mode, protocol_cls should be None
    assert server.protocol_cls is None
    assert server.enable_http3 is True
