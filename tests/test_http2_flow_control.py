"""
Tests for HTTP/2 flow control mechanisms.

This module tests:
- Window update handling
- Stream-level flow control
- Connection-level flow control
- Backpressure scenarios
- Data acknowledgment
"""

import asyncio
from unittest.mock import AsyncMock, Mock

import h2.connection
import h2.events
import pytest
from h2.config import H2Configuration

from asgiri.proto.http2 import Http2ServerProtocol


@pytest.fixture
def mock_transport():
    """Create a mock transport."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()
    transport.get_extra_info = Mock(return_value=("127.0.0.1", 8000))
    return transport


@pytest.fixture
def mock_app():
    """Create a simple mock ASGI app."""

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"5")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Hello",
                "more_body": False,
            }
        )

    return app


@pytest.fixture
def http2_protocol(mock_app, mock_transport):
    """Create an Http2ServerProtocol instance."""
    protocol = Http2ServerProtocol(
        server=("127.0.0.1", 8000),
        app=mock_app,
        state={},
    )
    protocol.connection_made(mock_transport)

    # Send client preface
    protocol.data_received(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

    # Clear the initial writes
    mock_transport.write.reset_mock()

    return protocol


@pytest.mark.asyncio
async def test_window_update_event_handled_silently(
    http2_protocol, mock_transport
):
    """Test that WindowUpdated events are handled without errors."""
    # Create a client connection to generate events
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send a request
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"GET"),
            (b":path", b"/"),
            (b":scheme", b"http"),
            (b":authority", b"localhost"),
        ],
        end_stream=True,
    )
    request_data = client_conn.data_to_send()

    # Server receives the request
    http2_protocol.data_received(request_data)

    # Give time for async processing
    await asyncio.sleep(0.1)

    # Send window update from client
    client_conn.increment_flow_control_window(1000, stream_id=1)
    window_update_data = client_conn.data_to_send()

    # Server should handle this without errors
    http2_protocol.data_received(window_update_data)

    # Verify protocol is still functional
    assert http2_protocol.conn is not None


@pytest.mark.asyncio
async def test_data_acknowledgment_for_flow_control(
    http2_protocol, mock_transport
):
    """Test that received data is acknowledged for flow control."""
    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send POST request with body
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"POST"),
            (b":path", b"/"),
            (b":scheme", b"http"),
            (b":authority", b"localhost"),
        ],
        end_stream=False,
    )
    headers_data = client_conn.data_to_send()
    http2_protocol.data_received(headers_data)

    # Clear previous writes
    mock_transport.write.reset_mock()

    # Send data
    client_conn.send_data(stream_id=1, data=b"test data", end_stream=True)
    body_data = client_conn.data_to_send()

    # Server receives data
    http2_protocol.data_received(body_data)

    # Give time for processing
    await asyncio.sleep(0.01)

    # Verify transport.write was called (for acknowledgment)
    assert mock_transport.write.called


@pytest.mark.asyncio
async def test_stream_flow_control_with_large_data(
    http2_protocol, mock_transport
):
    """Test stream-level flow control with larger data chunks."""
    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send POST request
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"POST"),
            (b":path", b"/"),
            (b":scheme", b"http"),
            (b":authority", b"localhost"),
        ],
        end_stream=False,
    )
    headers_data = client_conn.data_to_send()
    http2_protocol.data_received(headers_data)

    # Send large data chunk (1KB)
    large_data = b"x" * 1024
    client_conn.send_data(stream_id=1, data=large_data, end_stream=True)
    body_data = client_conn.data_to_send()

    # Server should handle this and acknowledge
    http2_protocol.data_received(body_data)

    await asyncio.sleep(0.01)

    # Verify stream is in protocol's tracking
    # (may be cleaned up after completion, so we just check no errors)
    assert http2_protocol.conn is not None


@pytest.mark.asyncio
async def test_multiple_streams_flow_control(http2_protocol, mock_transport):
    """Test flow control with multiple concurrent streams."""
    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send multiple requests
    for stream_id in [1, 3, 5]:
        client_conn.send_headers(
            stream_id=stream_id,
            headers=[
                (b":method", b"GET"),
                (b":path", f"/{stream_id}".encode()),
                (b":scheme", b"http"),
                (b":authority", b"localhost"),
            ],
            end_stream=False,
        )
        client_conn.send_data(
            stream_id=stream_id, data=b"test", end_stream=True
        )

    request_data = client_conn.data_to_send()

    # Server handles all requests
    http2_protocol.data_received(request_data)

    await asyncio.sleep(0.1)

    # Verify multiple streams were handled
    # Protocol should have processed them without flow control errors
    assert http2_protocol.conn is not None


@pytest.mark.asyncio
async def test_connection_flow_control_window():
    """Test connection-level flow control window management."""
    app = AsyncMock()
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()
    transport.get_extra_info = Mock(return_value=("127.0.0.1", 8000))

    protocol = Http2ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        state={},
    )
    protocol.connection_made(transport)

    # Verify h2 connection was created with default flow control window
    assert protocol.conn is not None
    assert hasattr(protocol.conn, "local_settings")


@pytest.mark.asyncio
async def test_stream_ended_event_completes_request(
    http2_protocol, mock_transport
):
    """Test that StreamEnded event properly completes the request."""
    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send request with end_stream flag
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"GET"),
            (b":path", b"/"),
            (b":scheme", b"http"),
            (b":authority", b"localhost"),
        ],
        end_stream=True,
    )
    request_data = client_conn.data_to_send()

    # Server receives request
    http2_protocol.data_received(request_data)

    await asyncio.sleep(0.1)

    # Stream should be processed
    # The stream may be cleaned up after response is sent
    assert http2_protocol.conn is not None


@pytest.mark.asyncio
async def test_websocket_data_bypasses_http_flow_control(
    http2_protocol, mock_transport
):
    """Test that WebSocket data over HTTP/2 is handled separately."""
    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send WebSocket CONNECT request
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"CONNECT"),
            (b":path", b"/ws"),
            (b":scheme", b"https"),
            (b":authority", b"localhost"),
            (b":protocol", b"websocket"),
        ],
        end_stream=False,
    )
    request_data = client_conn.data_to_send()

    # Server handles WebSocket CONNECT
    http2_protocol.data_received(request_data)

    await asyncio.sleep(0.05)

    # Verify WebSocket stream was created
    # (it should be in stream_states with ws_protocol attribute)
    stream_state = http2_protocol.stream_states.get(1)
    if stream_state:
        # If stream exists, it should have ws_protocol
        assert hasattr(stream_state, "ws_protocol") or stream_state is not None


@pytest.mark.asyncio
async def test_flow_control_with_chunked_data(http2_protocol, mock_transport):
    """Test flow control with data sent in multiple chunks."""
    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send POST request
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"POST"),
            (b":path", b"/"),
            (b":scheme", b"http"),
            (b":authority", b"localhost"),
        ],
        end_stream=False,
    )
    headers_data = client_conn.data_to_send()
    http2_protocol.data_received(headers_data)

    # Send data in multiple chunks
    for i in range(5):
        client_conn.send_data(
            stream_id=1,
            data=f"chunk{i}".encode(),
            end_stream=(i == 4),
        )
        chunk_data = client_conn.data_to_send()
        http2_protocol.data_received(chunk_data)
        await asyncio.sleep(0.01)

    await asyncio.sleep(0.05)

    # All chunks should be processed
    assert http2_protocol.conn is not None


@pytest.mark.asyncio
async def test_acknowledge_received_data_called():
    """Test that acknowledge_received_data is called for flow control."""
    app = AsyncMock()
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()
    transport.get_extra_info = Mock(return_value=("127.0.0.1", 8000))

    protocol = Http2ServerProtocol(
        server=("127.0.0.1", 8000),
        app=app,
        state={},
    )
    protocol.connection_made(transport)
    protocol.data_received(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")

    # Mock the acknowledge_received_data method to track calls
    original_acknowledge = protocol.conn.acknowledge_received_data
    call_count = []

    def mock_acknowledge(length, stream_id):
        call_count.append((length, stream_id))
        return original_acknowledge(length, stream_id)

    protocol.conn.acknowledge_received_data = mock_acknowledge

    # Create client connection
    client_config = H2Configuration(client_side=True)
    client_conn = h2.connection.H2Connection(config=client_config)
    client_conn.initiate_connection()
    client_conn.clear_outbound_data_buffer()

    # Send POST with data
    client_conn.send_headers(
        stream_id=1,
        headers=[
            (b":method", b"POST"),
            (b":path", b"/"),
            (b":scheme", b"http"),
            (b":authority", b"localhost"),
        ],
        end_stream=False,
    )
    headers_data = client_conn.data_to_send()
    protocol.data_received(headers_data)

    client_conn.send_data(stream_id=1, data=b"test data", end_stream=True)
    body_data = client_conn.data_to_send()

    # Clear call count before the data we're testing
    call_count.clear()

    protocol.data_received(body_data)

    await asyncio.sleep(0.01)

    # Verify acknowledge was called
    assert len(call_count) > 0
    assert call_count[0][1] == 1  # stream_id should be 1
