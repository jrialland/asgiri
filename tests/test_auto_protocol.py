"""Tests for auto-protocol detection and switching."""

import asyncio
import pytest
from asgiri.proto.auto import AutoProtocol, HTTP2_PREFACE
from tests.app import app


class MockTransport(asyncio.Transport):
    """Mock transport for testing."""
    
    def __init__(self):
        self.written_data = bytearray()
        self.closed = False
        
    def write(self, data: bytes):
        self.written_data.extend(data)
        
    def close(self):
        self.closed = True
        
    def is_closing(self):
        return self.closed
        
    def get_extra_info(self, name):
        if name == "peername":
            return ("127.0.0.1", 12345)
        return None


@pytest.mark.asyncio
async def test_detects_http2_prior_knowledge():
    """Test that the protocol detects HTTP/2 connection preface."""
    protocol = AutoProtocol(
        server=("localhost", 8000),
        app=app,
    )
    
    transport = MockTransport()
    protocol.connection_made(transport)
    
    # Send HTTP/2 connection preface
    protocol.data_received(HTTP2_PREFACE)
    
    # Should have delegated to HTTP/2 protocol
    assert protocol.protocol_detected
    assert protocol.delegated_protocol is not None
    assert protocol.delegated_protocol.__class__.__name__ == "Http2ServerProtocol"


@pytest.mark.asyncio
async def test_detects_http11_request():
    """Test that the protocol detects HTTP/1.1 requests."""
    protocol = AutoProtocol(
        server=("localhost", 8000),
        app=app,
    )
    
    transport = MockTransport()
    protocol.connection_made(transport)
    
    # Send HTTP/1.1 request
    http11_request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    protocol.data_received(http11_request)
    
    # Should have delegated to HTTP/1.1 protocol
    assert protocol.protocol_detected
    assert protocol.delegated_protocol is not None
    assert protocol.delegated_protocol.__class__.__name__ == "HTTP11ServerProtocol"


@pytest.mark.asyncio
async def test_http11_response_includes_alt_svc_header():
    """Test that HTTP/1.1 responses advertise HTTP/2 via Alt-Svc header."""
    protocol = AutoProtocol(
        server=("localhost", 8000),
        app=app,
    )
    
    transport = MockTransport()
    protocol.connection_made(transport)
    
    # Send HTTP/1.1 request
    http11_request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    protocol.data_received(http11_request)
    
    # Wait a bit for async processing
    await asyncio.sleep(0.1)
    
    # Check that Alt-Svc header is in the response
    response_data = bytes(transport.written_data)
    assert b"alt-svc:" in response_data.lower() or b"Alt-Svc:" in response_data


@pytest.mark.asyncio
async def test_partial_http2_preface_waits_for_more_data():
    """Test that partial HTTP/2 preface waits for complete data."""
    protocol = AutoProtocol(
        server=("localhost", 8000),
        app=app,
    )
    
    transport = MockTransport()
    protocol.connection_made(transport)
    
    # Send partial HTTP/2 preface
    partial_preface = HTTP2_PREFACE[:10]
    protocol.data_received(partial_preface)
    
    # Should not have detected protocol yet
    assert not protocol.protocol_detected
    assert protocol.delegated_protocol is None
    
    # Send rest of preface
    rest_of_preface = HTTP2_PREFACE[10:]
    protocol.data_received(rest_of_preface)
    
    # Now should have detected HTTP/2
    assert protocol.protocol_detected
    assert protocol.delegated_protocol is not None


@pytest.mark.asyncio
async def test_delegates_subsequent_data():
    """Test that data after protocol detection is delegated properly."""
    protocol = AutoProtocol(
        server=("localhost", 8000),
        app=app,
    )
    
    transport = MockTransport()
    protocol.connection_made(transport)
    
    # Send HTTP/1.1 request to trigger delegation
    http11_request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    protocol.data_received(http11_request)
    
    # Verify delegation happened
    assert protocol.delegated_protocol is not None
    
    # Track calls to delegated protocol
    delegated_protocol = protocol.delegated_protocol
    original_data_received = delegated_protocol.data_received
    call_count = 0
    
    def tracked_data_received(data):
        nonlocal call_count
        call_count += 1
        return original_data_received(data)
    
    delegated_protocol.data_received = tracked_data_received
    
    # Send more data
    more_data = b"GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
    protocol.data_received(more_data)
    
    # Should have delegated the data
    assert call_count == 1
