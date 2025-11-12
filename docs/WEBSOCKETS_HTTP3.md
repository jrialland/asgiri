# WebSocket over HTTP/3

## Overview

This implementation follows **RFC 9220 - Bootstrapping WebSockets with HTTP/3**, which defines how to run the WebSocket Protocol over HTTP/3 streams using the Extended CONNECT method.

## How It Works

### Extended CONNECT Method

WebSocket over HTTP/3 uses an Extended CONNECT request with special pseudo-headers:

```
:method = CONNECT
:protocol = websocket
:scheme = https (or http for ws:// URIs)
:path = /websocket-endpoint
:authority = server.example.com
```

Additional headers like `sec-websocket-protocol`, `sec-websocket-extensions`, and `origin` are sent as regular headers (lowercase).

### SETTINGS_ENABLE_CONNECT_PROTOCOL

The server must advertise support for Extended CONNECT by sending the `SETTINGS_ENABLE_CONNECT_PROTOCOL` setting with a value of 1 (0x08 in the HTTP/3 settings registry).

In aioquic, this is handled automatically when creating an H3Connection.

### Handshake Flow

1. **Client sends CONNECT request** with `:protocol = websocket`
2. **Server validates** the request
3. **Server sends 200 OK** response (not 101 like HTTP/1.1)
4. **WebSocket protocol begins** over the HTTP/3 stream

The HTTP/3 stream becomes the "TCP connection" for the WebSocket Protocol (RFC 6455).

### Key Differences from HTTP/1.1 WebSocket

| Aspect | HTTP/1.1 | HTTP/3 |
|--------|----------|---------|
| Upgrade method | `Upgrade:` header | Extended CONNECT |
| Response code | 101 Switching Protocols | 200 OK |
| Connection field | Required | Not used (HTTP/2+ doesn't have it) |
| Host header | Required | Conveyed in `:authority` |
| Sec-WebSocket-Key | Required | NOT used with Extended CONNECT |
| Sec-WebSocket-Accept | Required | NOT used with Extended CONNECT |

The `Sec-WebSocket-Key` and `Sec-WebSocket-Accept` handshake mechanism is superseded by the `:protocol` pseudo-header field.

## ASGI Scope

The WebSocket scope for HTTP/3 is identical to HTTP/1.1 WebSockets:

```python
{
    "type": "websocket",
    "asgi": {"version": "3.0", "spec_version": "2.3"},
    "http_version": "3",  # Indicates HTTP/3
    "scheme": "wss",      # Always wss for HTTP/3
    "path": "/chat",
    "raw_path": b"/chat",
    "query_string": b"",
    "root_path": "",
    "headers": [...],
    "server": ("localhost", 8443),
    "client": None,
    "subprotocols": ["chat", "superchat"],  # From sec-websocket-protocol
}
```

## Implementation Details

### HTTP3ServerProtocol

The `HTTP3ServerProtocol` class detects WebSocket CONNECT requests:

```python
def _check_websocket_request(self, headers) -> bool:
    method = None
    protocol = None
    
    for name, value in headers:
        if name == b":method":
            method = value
        elif name == b":protocol":
            protocol = value
    
    return method == b"CONNECT" and protocol == b"websocket"
```

### HTTP3WebSocketHandler

A dedicated handler manages each WebSocket connection:

- Uses `wsproto` library for WebSocket frame handling
- Implements ASGI WebSocket protocol
- Routes data between HTTP/3 stream and ASGI application
- Handles ping/pong automatically

### Frame Transmission

WebSocket frames are sent as HTTP/3 DATA frames on the stream:

```python
# Application sends text message
await send({
    "type": "websocket.send",
    "text": "Hello!",
})

# Handler encodes as WebSocket frame using wsproto
frame_data = self.ws.send(Message(data="Hello!"))

# Sends frame via HTTP/3 stream
self.h3.send_data(stream_id, frame_data, end_stream=False)
```

### Stream Closure

WebSocket close frames are sent before ending the HTTP/3 stream:

```python
# Send WebSocket close frame
close_data = self.ws.send(CloseConnection(code=1000, reason="Goodbye"))
self.h3.send_data(stream_id, close_data, end_stream=True)
```

This is analogous to TCP FIN in RFC 6455. Stream errors (RST_STREAM in HTTP/2, stream errors in HTTP/3) are analogous to RST.

## Usage Example

### Server Setup

```python
from asgiri.server import Server

async def app(scope, receive, send):
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                break
            elif message["type"] == "websocket.receive":
                # Echo back
                if "text" in message:
                    await send({
                        "type": "websocket.send",
                        "text": message["text"],
                    })

server = Server(
    app,
    host="0.0.0.0",
    port=8443,
    enable_http3=True,  # Required
    certfile="cert.pem",
    keyfile="key.pem",
)

await server.run()
```

### Client Example (JavaScript)

```javascript
// Connect to WebSocket over HTTP/3
// The browser automatically uses HTTP/3 if available
const ws = new WebSocket('wss://localhost:8443/chat');

ws.onopen = () => {
    console.log('Connected via HTTP/3');
    ws.send('Hello!');
};

ws.onmessage = (event) => {
    console.log('Received:', event.data);
};
```

Note: The browser handles HTTP/3 negotiation automatically. From JavaScript's perspective, it's the same WebSocket API.

## Browser Support

WebSocket over HTTP/3 is supported in:
- **Chrome 97+** (with HTTP/3 enabled)
- **Edge 97+** (with HTTP/3 enabled)
- **Firefox** (experimental support)

Check browser settings to ensure HTTP/3 is enabled:
- Chrome: `chrome://flags/#enable-quic`
- Firefox: `about:config` → `network.http.http3.enabled`

## Benefits Over HTTP/1.1 WebSocket

1. **Multiplexing**: Multiple WebSocket connections share one QUIC connection
2. **No Head-of-Line Blocking**: Lost packets only affect individual streams
3. **Better Performance**: QUIC's improved congestion control
4. **Connection Migration**: Survive network changes (WiFi → cellular)
5. **Faster Handshake**: Combined TLS 1.3 and QUIC handshake

## Current Limitations

1. **No HTTP/1.1 fallback**: Application must handle both protocols if needed
2. **wsproto state simulation**: Uses a fake handshake to initialize wsproto
3. **Limited browser support**: Not all browsers support HTTP/3 WebSockets yet

## Testing

### Using curl-impersonate

```bash
# Install curl-impersonate with HTTP/3 support
curl-impersonate-chrome https://localhost:8443 --http3
```

### Using Python Client

For testing, you can use `aioquic` directly:

```python
import asyncio
from aioquic.asyncio.client import connect
from aioquic.h3.connection import H3Connection

async def test_websocket():
    async with connect("localhost", 8443) as (quic, h3):
        # Send CONNECT request
        stream_id = quic.get_next_available_stream_id()
        h3.send_headers(stream_id, [
            (b":method", b"CONNECT"),
            (b":protocol", b"websocket"),
            (b":scheme", b"https"),
            (b":path", b"/chat"),
            (b":authority", b"localhost:8443"),
        ])
        
        # Wait for 200 OK response
        # Then send/receive WebSocket frames
```

## References

- [RFC 9220 - Bootstrapping WebSockets with HTTP/3](https://www.rfc-editor.org/rfc/rfc9220.html)
- [RFC 8441 - Bootstrapping WebSockets with HTTP/2](https://www.rfc-editor.org/rfc/rfc8441.html)
- [RFC 6455 - The WebSocket Protocol](https://www.rfc-editor.org/rfc/rfc6455.html)
- [RFC 9114 - HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html)
