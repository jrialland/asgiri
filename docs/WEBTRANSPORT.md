# WebTransport Support

## Overview

WebTransport is a protocol that provides low-latency, bidirectional communication between clients and servers over HTTP/3. It's designed as a modern replacement for WebSockets, leveraging QUIC's built-in multiplexing and improved connection migration.

## Enabling WebTransport

WebTransport support is built on top of HTTP/3 using the aioquic library. To enable it:

```python
from asgiri.server import Server

server = Server(
    app,
    host="127.0.0.1",
    port=8443,
    enable_http3=True,           # HTTP/3 must be enabled
    enable_webtransport=True,    # Enable WebTransport
    certfile="server.crt",       # TLS is required for HTTP/3
    keyfile="server.key",
)
```

## ASGI Scope

**Note:** WebTransport is not yet standardized in the ASGI specification. This implementation uses a custom scope type.

When a WebTransport connection is established, your ASGI application receives a scope with:

```python
{
    "type": "webtransport",
    "asgi": {"version": "3.0", "spec_version": "2.3"},
    "http_version": "3",
    "scheme": "https",
    "path": "/webtransport",  # The path from the CONNECT request
    "raw_path": b"/webtransport",
    "query_string": b"",
    "root_path": "",
    "headers": [...],  # Request headers (excluding pseudo-headers)
    "server": ("127.0.0.1", 8443),
    "client": None,
    "session_id": 12345,  # QUIC stream ID for this session
}
```

## Message Format

### Receive Messages

Your application receives messages via the `receive` callable:

#### Connection Accept
After accepting a WebTransport session, you'll receive stream and datagram events.

#### Stream Data Received
```python
{
    "type": "webtransport.stream.receive",
    "stream_id": 123,
    "data": b"hello",
    "stream_ended": False,
}
```

#### Datagram Received
```python
{
    "type": "webtransport.datagram.receive",
    "data": b"datagram data",
}
```

#### Disconnect
```python
{
    "type": "webtransport.disconnect",
}
```

### Send Messages

Your application sends messages via the `send` callable:

#### Accept Session
```python
await send({"type": "webtransport.accept"})
```

#### Send Stream Data
```python
await send({
    "type": "webtransport.stream.send",
    "stream_id": 123,
    "data": b"response data",
    "end_stream": False,
})
```

#### Send Datagram
```python
await send({
    "type": "webtransport.datagram.send",
    "data": b"datagram data",
})
```

#### Close Session
```python
await send({"type": "webtransport.close"})
```

## Example Application

```python
async def app(scope, receive, send):
    if scope["type"] == "webtransport":
        # Accept the WebTransport session
        await send({"type": "webtransport.accept"})
        
        # Handle events
        while True:
            message = await receive()
            
            if message["type"] == "webtransport.disconnect":
                break
            
            elif message["type"] == "webtransport.stream.receive":
                stream_id = message["stream_id"]
                data = message["data"]
                
                # Echo back the data
                await send({
                    "type": "webtransport.stream.send",
                    "stream_id": stream_id,
                    "data": b"Echo: " + data,
                    "end_stream": message.get("stream_ended", False),
                })
```

## Client Example

WebTransport requires browser or client support. Example JavaScript client:

```javascript
// Connect to WebTransport endpoint
const url = 'https://localhost:8443/webtransport';
const transport = new WebTransport(url);

await transport.ready;
console.log('Connected!');

// Create a bidirectional stream
const stream = await transport.createBidirectionalStream();

// Write data
const writer = stream.writable.getWriter();
await writer.write(new TextEncoder().encode('Hello!'));

// Read response
const reader = stream.readable.getReader();
const { value, done } = await reader.read();
console.log('Received:', new TextDecoder().decode(value));
```

## Implementation Details

### H3Connection Configuration

The `H3Connection` from aioquic is initialized with `enable_webtransport=True`:

```python
self.h3 = H3Connection(self._quic, enable_webtransport=True)
```

### WebTransport CONNECT Request

WebTransport sessions are initiated via an HTTP/3 CONNECT request with:
- `:method` = `CONNECT`
- `:protocol` = `webtransport`

The server detects this and routes to WebTransport handling instead of regular HTTP.

### Stream Handling

WebTransport uses QUIC streams underneath. The implementation tracks:
- `webtransport_sessions`: Active WebTransport session tasks
- `webtransport_stream_receivers`: Queues for incoming stream data
- `webtransport_stream_ended`: Stream completion status

### Events

The aioquic library provides `WebTransportStreamDataReceived` events when data arrives on WebTransport streams. These are queued and delivered to the ASGI application via the `receive` callable.

## Current Limitations

1. **Not ASGI Standard**: WebTransport is not yet part of the official ASGI specification. This is a custom implementation that may change.

2. **Partial Implementation**: The current implementation provides basic stream handling. Additional features like datagrams and advanced stream management are partially implemented.

3. **Browser Support**: WebTransport requires modern browsers with HTTP/3 support. Not all browsers support it yet.

4. **TLS Required**: WebTransport requires TLS certificates, just like HTTP/3.

## Future Enhancements

- [ ] Complete datagram support
- [ ] Bidirectional stream creation from server
- [ ] Unidirectional streams
- [ ] Stream prioritization
- [ ] Connection migration
- [ ] ASGI spec proposal for WebTransport

## References

- [WebTransport Specification](https://w3c.github.io/webtransport/)
- [HTTP/3 WebTransport Draft](https://datatracker.ietf.org/doc/html/draft-ietf-webtrans-http3)
- [aioquic Library](https://github.com/aiortc/aioquic)
- [MDN WebTransport API](https://developer.mozilla.org/en-US/docs/Web/API/WebTransport)
