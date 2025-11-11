# WebSocket Tests for Asgiri

## Overview

This directory contains comprehensive WebSocket tests for the asgiri ASGI server. The tests are designed to validate WebSocket functionality over both **HTTP/1.1** and **HTTP/2 (AUTO mode)** protocols.

## Current Status

⚠️ **The tests are currently marked as XFAIL (expected failures)** because WebSocket support has not yet been implemented in the asgiri server protocol handlers.

## Test Coverage

The test suite (`test_websocket.py`) includes **14 comprehensive tests** covering:

### HTTP/1.1 WebSocket Tests (5 tests)
1. **Basic Echo** - Simple message echo functionality
2. **Multiple Messages** - Sequential message handling  
3. **Bidirectional Communication** - Send multiple messages before receiving responses
4. **Large Messages** - Handling of 10KB+ messages
5. **Clean Close** - Proper connection termination

### HTTP/2 (AUTO mode) WebSocket Tests (5 tests)
1. **Basic Echo** - Simple message echo over HTTP/2
2. **Multiple Messages** - Sequential messaging over HTTP/2
3. **Bidirectional Communication** - Batch send/receive over HTTP/2
4. **Large Messages** - 50KB+ message handling over HTTP/2
5. **Concurrent Connections** - 5 simultaneous WebSocket connections

### Protocol Comparison Tests (4 tests)
1. **Protocol Comparison** - Verify identical behavior across HTTP/1.1 and HTTP/2
2. **Rapid Messages** - 20 messages in rapid succession
3. **Empty Messages** - Edge case: empty string messages
4. **Special Characters** - Unicode, emojis, and special symbols

## Implementation Requirements

To make these tests pass, WebSocket support needs to be added to asgiri's protocol handlers:

### 1. WebSocket Handshake (RFC 6455)
- Detect `Upgrade: websocket` header in HTTP requests
- Validate `Sec-WebSocket-Key` and other required headers
- Generate `Sec-WebSocket-Accept` response header
- Send HTTP 101 Switching Protocols response

### 2. WebSocket Protocol Integration
- Integrate the `wsproto` library (already in `pyproject.toml` dependencies)
- Implement WebSocket frame parsing and generation
- Handle frame types: text, binary, close, ping, pong

### 3. Protocol Handler Updates
Files that need modifications:
- `asgiri/proto/http11.py` - Add WebSocket upgrade detection and handling
- `asgiri/proto/http2.py` - Add HTTP/2 WebSocket support (RFC 8441)
- `asgiri/proto/auto.py` - Ensure WebSocket works in auto-detection mode

### 4. ASGI WebSocket Spec
Implement proper ASGI WebSocket communication:
- `websocket.connect` event handling
- `websocket.accept` message processing
- `websocket.receive` message handling  
- `websocket.send` message handling
- `websocket.close` message processing

## Test Application

The tests use a FastAPI application (`tests/app.py`) with a WebSocket endpoint:

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")
```

This simple echo server is perfect for validating the WebSocket implementation.

## Running the Tests

```bash
# Run all WebSocket tests (currently all XFAIL)
pytest tests/test_websocket.py -v

# Run specific test
pytest tests/test_websocket.py::test_websocket_basic_echo_http11 -v

# Run only HTTP/1.1 tests
pytest tests/test_websocket.py -k "http11" -v

# Run only HTTP/2 tests  
pytest tests/test_websocket.py -k "http2" -v
```

## When WebSocket is Implemented

Once WebSocket support is added to asgiri:

1. Remove the `pytestmark = pytest.mark.xfail(...)` decorator from `test_websocket.py`
2. Run the tests - they should all pass ✅
3. The test suite will provide immediate validation that WebSocket works correctly
4. Tests cover edge cases, concurrency, and protocol compatibility

## References

- [RFC 6455 - The WebSocket Protocol](https://tools.ietf.org/html/rfc6455)
- [RFC 8441 - Bootstrapping WebSockets with HTTP/2](https://tools.ietf.org/html/rfc8441)  
- [ASGI WebSocket Specification](https://asgi.readthedocs.io/en/latest/specs/www.html#websocket)
- [wsproto Documentation](https://wsproto.readthedocs.io/)
- [websockets Library](https://websockets.readthedocs.io/)
