# WebSocket Implementation for asgiri

## Overview

Full WebSocket support has been successfully implemented for the asgiri ASGI server, supporting both HTTP/1.1 and HTTP/2 protocols.

## Implementation Summary

### Files Created/Modified

#### New Files
- **`asgiri/proto/websocket.py`** - Complete WebSocket protocol handler
  - Uses `wsproto` library for frame handling
  - Implements ASGI WebSocket specification
  - Handles text/binary messages, ping/pong, close frames
  - Manages connection lifecycle with proper state tracking

#### Modified Files
- **`asgiri/proto/http11.py`** - HTTP/1.1 WebSocket support
  - Detects `Upgrade: websocket` header
  - Validates WebSocket handshake (RFC 6455)
  - Delegates to WebSocket protocol handler after upgrade

- **`asgiri/proto/http2.py`** - HTTP/2 WebSocket support (RFC 8441)
  - Handles `CONNECT` method with `:protocol = websocket` pseudo-header
  - Created `HTTP2StreamTransport` wrapper for stream-based WebSocket
  - Routes DATA frames to WebSocket handler

- **`tests/test_websocket.py`** - Comprehensive test suite
  - Removed `pytest.mark.xfail` markers
  - All 14 tests now pass

## Features

### HTTP/1.1 WebSocket (RFC 6455)
- ✅ WebSocket upgrade handshake
- ✅ Sec-WebSocket-Key validation
- ✅ 101 Switching Protocols response
- ✅ Frame-based communication (text, binary, ping, pong, close)
- ✅ Proper connection lifecycle management

### HTTP/2 WebSocket (RFC 8441)
- ✅ Extended CONNECT method support
- ✅ `:protocol = websocket` pseudo-header handling
- ✅ Per-stream WebSocket connections
- ✅ Stream multiplexing support
- ✅ Proper stream state management

### ASGI WebSocket Protocol
- ✅ `websocket.connect` event
- ✅ `websocket.accept` message
- ✅ `websocket.receive` event (text/bytes)
- ✅ `websocket.send` message (text/bytes)
- ✅ `websocket.close` message
- ✅ `websocket.disconnect` event

## Test Results

All tests passing: ✅ **27/27 tests**

### WebSocket Tests (14 tests)
- ✅ Basic echo over HTTP/1.1
- ✅ Multiple messages over HTTP/1.1
- ✅ Bidirectional communication over HTTP/1.1
- ✅ Large messages over HTTP/1.1
- ✅ Connection close over HTTP/1.1
- ✅ Basic echo over HTTP/2
- ✅ Multiple messages over HTTP/2
- ✅ Bidirectional communication over HTTP/2
- ✅ Large messages over HTTP/2
- ✅ Concurrent connections over HTTP/2
- ✅ Protocol comparison (HTTP/1.1 vs HTTP/2)
- ✅ Rapid message exchange
- ✅ Empty message handling
- ✅ Special character handling

### HTTP Tests (13 tests)
- ✅ All existing HTTP tests still pass
- ✅ No regressions introduced

## Technical Details

### WebSocket Protocol Handler (`websocket.py`)

The `WebSocketProtocol` class provides:

1. **Handshake Management**
   - HTTP/1.1: Processes raw HTTP request via wsproto
   - HTTP/2: Simulates handshake internally (already done via CONNECT)

2. **Frame Handling**
   - Text messages (`TextMessage`)
   - Binary messages (`BytesMessage`)
   - Ping/Pong frames
   - Close frames with proper close codes

3. **ASGI Integration**
   - Async receive/send queues
   - Proper event flow (connect → accept → send/receive → close)
   - Exception handling for WebSocket disconnects

4. **Transport Abstraction**
   - Works with standard asyncio.Transport (HTTP/1.1)
   - Works with HTTP2StreamTransport (HTTP/2)

### HTTP/1.1 Integration

```python
# Detects WebSocket upgrade
if self._is_websocket_upgrade(request):
    self._handle_websocket_upgrade(request)
    return
```

### HTTP/2 Integration

```python
# Detects WebSocket CONNECT
if self._is_websocket_connect(event):
    self._handle_websocket_connect(event)
    continue
```

## Usage Example

```python
from fastapi import FastAPI, WebSocket
from asgiri.server import Server, HttpProtocolVersion

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Echo: {data}")

# HTTP/1.1 WebSocket
server = Server(app, port=8000, http_version=HttpProtocolVersion.HTTP_1_1)

# HTTP/2 WebSocket (requires TLS)
server = Server(app, port=8443, http_version=HttpProtocolVersion.HTTP_2,
                certfile="cert.pem", keyfile="key.pem")

# Auto-detect (supports both)
server = Server(app, port=8000, http_version=HttpProtocolVersion.AUTO)
server.run()
```

## Known Issues

Minor warnings (non-blocking):
- Deprecation warning from `websockets.client.connect` (test library)
- Port binding race condition in one test (doesn't affect functionality)

## References

- RFC 6455: The WebSocket Protocol
- RFC 8441: Bootstrapping WebSockets with HTTP/2
- ASGI WebSocket Specification 2.3
- wsproto library documentation
