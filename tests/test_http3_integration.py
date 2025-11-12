"""
Integration tests for HTTP/3 server with real client/server communication.
"""

import asyncio
import json as json_module
import pytest
import pytest_asyncio

from aioquic.asyncio.client import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import QuicEvent

from asgiri.server import Server
from asgiri.ssl_utils import generate_self_signed_cert
from tests.app import app


class SimpleH3Client(QuicConnectionProtocol):
    """Simple HTTP/3 client for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.h3 = H3Connection(self._quic)
        self.responses = {}
        self.response_waiter = {}

    def quic_event_received(self, event: QuicEvent):
        for h3_event in self.h3.handle_event(event):
            self._handle_h3_event(h3_event)

    def _handle_h3_event(self, event):
        if isinstance(event, HeadersReceived):
            stream_id = event.stream_id
            if stream_id not in self.responses:
                self.responses[stream_id] = {"headers": [], "data": b"", "ended": False}
            
            for name, value in event.headers:
                self.responses[stream_id]["headers"].append((name, value))
                if name == b":status":
                    self.responses[stream_id]["status"] = int(value)
                    
        elif isinstance(event, DataReceived):
            stream_id = event.stream_id
            if stream_id not in self.responses:
                self.responses[stream_id] = {"headers": [], "data": b"", "ended": False}
            
            self.responses[stream_id]["data"] += event.data
            if event.stream_ended:
                self.responses[stream_id]["ended"] = True
                # Signal completion
                if stream_id in self.response_waiter:
                    self.response_waiter[stream_id].set()

    async def request(self, method, path, headers=None, body=None):
        """Send HTTP/3 request."""
        stream_id = self._quic.get_next_available_stream_id()
        
        # Prepare response tracking
        self.responses[stream_id] = {"headers": [], "data": b"", "ended": False}
        self.response_waiter[stream_id] = asyncio.Event()
        
        # Build headers
        h3_headers = [
            (b":method", method.encode()),
            (b":scheme", b"https"),
            (b":authority", b"127.0.0.1:8443"),
            (b":path", path.encode()),
        ]
        
        if headers:
            for name, value in headers.items():
                h3_headers.append((name.encode(), value.encode()))
        
        # Add content-length if body is present
        if body:
            h3_headers.append((b"content-length", str(len(body)).encode()))
        
        # Send request
        self.h3.send_headers(stream_id, h3_headers)
        
        if body:
            self.h3.send_data(stream_id, body, end_stream=True)
        else:
            self.h3.send_data(stream_id, b"", end_stream=True)
        
        self.transmit()
        
        # Wait for response with timeout
        await asyncio.wait_for(self.response_waiter[stream_id].wait(), timeout=5.0)
        
        return self.responses[stream_id]


async def make_h3_request(method, path, port=8443, headers=None, body=None):
    """Helper to make HTTP/3 request."""
    configuration = QuicConfiguration(
        is_client=True,
        alpn_protocols=["h3"],
    )
    configuration.verify_mode = False  # Disable cert verification for testing
    
    async with connect(
        "127.0.0.1",
        port,
        configuration=configuration,
        create_protocol=SimpleH3Client,
    ) as client:
        response = await client.request(method, path, headers, body)
        return response


@pytest_asyncio.fixture(scope="function")
async def http3_server(request):
    """Create an HTTP/3 server for testing."""
    # Use different port for each test to avoid conflicts
    port = 8443 + hash(request.node.name) % 100
    
    # Create server with the FastAPI test app and self-signed certificates
    # Use the existing server.crt and server.key in the project root
    import os
    project_root = os.path.dirname(os.path.dirname(__file__))
    
    cert_data, key_data = generate_self_signed_cert()

    server = Server(
        app,
        host="127.0.0.1",
        port=port,
        enable_http3=True,
        cert_data=cert_data,
        key_data=key_data,
    )

    # Start server in background
    server_task = asyncio.create_task(server.a_run())

    # Give server time to start
    await asyncio.sleep(0.5)

    yield (server, port)

    # Cleanup
    server_task.cancel()
    try:
        await server_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_http3_get_request(http3_server):
    """Test simple HTTP/3 GET request."""
    server, port = http3_server
    response = await make_h3_request("GET", "/helloworld", port=port)
    
    assert response["status"] == 200
    data = json_module.loads(response["data"])
    assert data == {"Hello": "World"}


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_http3_post_echo(http3_server):
    """Test HTTP/3 POST request with JSON body."""
    server, port = http3_server
    test_data = {"message": "test", "value": 123}
    body = json_module.dumps(test_data).encode("utf-8")
    headers = {"content-type": "application/json"}
    
    response = await make_h3_request("POST", "/echo", port=port, headers=headers, body=body)
    
    assert response["status"] == 200
    data = json_module.loads(response["data"])
    assert data == test_data


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_http3_get_with_params(http3_server):
    """Test HTTP/3 GET request with query parameters."""
    server, port = http3_server
    response = await make_h3_request("GET", "/read_params?name=Alice&age=30&active=true", port=port)
    
    assert response["status"] == 200
    data = json_module.loads(response["data"])
    assert data == {"Name": "Alice", "Age": 30, "Active": True}


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_http3_multiple_requests(http3_server):
    """Test multiple concurrent HTTP/3 requests."""
    server, port = http3_server
    
    # Make requests sequentially
    r1 = await make_h3_request("GET", "/helloworld", port=port)
    assert r1["status"] == 200
    assert json_module.loads(r1["data"]) == {"Hello": "World"}
    
    r2 = await make_h3_request("GET", "/read_params?name=Bob&age=25&active=false", port=port)
    assert r2["status"] == 200
    assert json_module.loads(r2["data"]) == {"Name": "Bob", "Age": 25, "Active": False}
    
    test_data = {"test": "data1"}
    body = json_module.dumps(test_data).encode("utf-8")
    r3 = await make_h3_request("POST", "/echo", port=port, headers={"content-type": "application/json"}, body=body)
    assert r3["status"] == 200
    assert json_module.loads(r3["data"]) == test_data

