# WebTransport Support

## Overview

WebTransport is a protocol that provides low-latency, bidirectional communication between clients and servers over HTTP/3. It's designed as a modern replacement for WebSockets, leveraging QUIC's built-in multiplexing and improved connection migration.

**Key Feature**: asgiri implements WebTransport in **per-stream mode**, where each WebTransport stream gets its own ASGI application instance. This makes it simple and intuitive to use, similar to handling individual HTTP requests.

## Enabling WebTransport

WebTransport is **always enabled** when HTTP/3 is enabled. No additional configuration needed:

```python
from asgiri.server import Server

server = Server(
    app,
    host="127.0.0.1",
    port=8443,
    enable_http3=True,           # This enables both HTTP/3 and WebTransport
    certfile="server.crt",       # TLS is required for HTTP/3
    keyfile="server.key",
)
```

## Per-Stream Mode

In asgiri, each WebTransport stream is handled independently by its own ASGI application instance. This design provides:

- ✅ **Simplicity**: Each stream is like an individual connection
- ✅ **Familiar pattern**: Similar to handling HTTP requests
- ✅ **No manual multiplexing**: The server handles stream routing
- ✅ **Automatic cleanup**: Per-stream lifecycle management

### ASGI Scope

Each WebTransport stream receives a scope with type `"webtransport.stream"`:

```python
{
    "type": "webtransport.stream",
    "asgi": {"version": "3.0", "spec_version": "2.3"},
    "http_version": "3",
    "session_id": 12345,  # Parent WebTransport session ID
    "stream_id": 67890,   # This specific stream ID
    "server": ("127.0.0.1", 8443),
}
```

### Message Format

#### Receive Messages

Your application receives messages via the `receive` callable:

```python
# Stream data received
{
    "type": "webtransport.stream.receive",
    "data": b"hello",
    "stream_ended": False,
}

# Stream disconnected (stream ended or session closed)
{
    "type": "webtransport.stream.disconnect",
}
```

#### Send Messages

Your application sends messages via the `send` callable:

```python
# Send data on this stream
await send({
    "type": "webtransport.stream.send",
    "data": b"response data",
    "end_stream": False,  # Set True to close the stream
})

# Close this stream
await send({
    "type": "webtransport.stream.close",
})
```

## Example Application

Simple echo server that handles each stream independently:

```python
async def app(scope, receive, send):
    if scope["type"] == "webtransport.stream":
        # This handler runs once per stream
        session_id = scope["session_id"]
        stream_id = scope["stream_id"]
        
        print(f"New stream {stream_id} in session {session_id}")
        
        # Echo server - read and respond
        while True:
            message = await receive()
            
            if message["type"] == "webtransport.stream.disconnect":
                print(f"Stream {stream_id} disconnected")
                break
            
            elif message["type"] == "webtransport.stream.receive":
                data = message["data"]
                stream_ended = message.get("stream_ended", False)
                
                # Echo back the data
                await send({
                    "type": "webtransport.stream.send",
                    "data": b"Echo: " + data,
                    "end_stream": stream_ended,
                })
                
                if stream_ended:
                    break
    
    elif scope["type"] == "http":
        # Serve HTML client page
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"text/html"]],
        })
        
        html = b"<!DOCTYPE html><html>...</html>"
        await send({
            "type": "http.response.body",
            "body": html,
        })
```

## Use Cases

WebTransport per-stream mode is perfect for:

### 🎮 Gaming
Each player connection is an independent stream:
```python
async def app(scope, receive, send):
    if scope["type"] == "webtransport.stream":
        player_id = scope["stream_id"]
        
        # Handle player messages
        while True:
            message = await receive()
            if message["type"] == "webtransport.stream.receive":
                game_action = parse_action(message["data"])
                response = process_player_action(player_id, game_action)
                
                await send({
                    "type": "webtransport.stream.send",
                    "data": response,
                })
```

### 💬 Chat
Each chat thread or conversation as a stream:
```python
async def app(scope, receive, send):
    if scope["type"] == "webtransport.stream":
        thread_id = scope["stream_id"]
        
        while True:
            message = await receive()
            if message["type"] == "webtransport.stream.receive":
                chat_message = message["data"].decode()
                
                # Broadcast to other clients in this thread
                await broadcast_to_thread(thread_id, chat_message)
                
                await send({
                    "type": "webtransport.stream.send",
                    "data": b"Message delivered",
                })
```

### 📁 File Transfer
Each file upload/download is a separate stream:
```python
async def app(scope, receive, send):
    if scope["type"] == "webtransport.stream":
        stream_id = scope["stream_id"]
        file_data = b""
        
        while True:
            message = await receive()
            
            if message["type"] == "webtransport.stream.receive":
                file_data += message["data"]
                
                if message.get("stream_ended"):
                    # Save complete file
                    filename = f"upload_{stream_id}.dat"
                    with open(filename, "wb") as f:
                        f.write(file_data)
                    
                    await send({
                        "type": "webtransport.stream.send",
                        "data": f"Saved {filename} ({len(file_data)} bytes)".encode(),
                        "end_stream": True,
                    })
                    break
```

### 🔄 RPC (Remote Procedure Calls)
Each request/response is a stream:
```python
async def app(scope, receive, send):
    if scope["type"] == "webtransport.stream":
        # Receive RPC request
        message = await receive()
        if message["type"] == "webtransport.stream.receive":
            request = json.loads(message["data"])
            
            # Process RPC
            result = await handle_rpc(request["method"], request["params"])
            
            # Send response
            await send({
                "type": "webtransport.stream.send",
                "data": json.dumps(result).encode(),
                "end_stream": True,
            })
```

## Client Example

JavaScript client code for WebTransport:

```javascript
// Connect to WebTransport endpoint
const url = 'https://localhost:8443/webtransport';
const transport = new WebTransport(url);

await transport.ready;
console.log('Connected to WebTransport server');

// Create a bidirectional stream
const stream = await transport.createBidirectionalStream();

// Write data to the stream
const writer = stream.writable.getWriter();
await writer.write(new TextEncoder().encode('Hello from client!'));
await writer.close();  // End stream

// Read response from the stream
const reader = stream.readable.getReader();
const { value, done } = await reader.read();
console.log('Received:', new TextDecoder().decode(value));

// Each createBidirectionalStream() call creates a new stream
// Each stream gets its own ASGI app instance on the server
const stream2 = await transport.createBidirectionalStream();
// ... stream2 is completely independent from stream
```

### Multiple Concurrent Streams

```javascript
// Create multiple streams in parallel
const streams = await Promise.all([
    transport.createBidirectionalStream(),
    transport.createBidirectionalStream(),
    transport.createBidirectionalStream(),
]);

// Each stream operates independently - no head-of-line blocking!
for (const stream of streams) {
    const writer = stream.writable.getWriter();
    await writer.write(new TextEncoder().encode('Data'));
}
```

## Implementation Details

### Stream Lifecycle

1. Client creates a bidirectional stream
2. Server receives `WebTransportStreamDataReceived` event
3. Server creates a new ASGI task for this stream
4. Stream data flows through dedicated `receive`/`send` callables
5. Stream ends when:
   - Client closes stream
   - Server sends `end_stream=True`
   - WebTransport session closes
6. ASGI task completes and cleans up

### Session Management

- WebTransport session is automatically accepted by the server
- Each stream within a session gets its own ASGI app instance
- Streams are independent - no shared state between them
- Session is tracked but applications interact only with individual streams

### Performance

- **One ASGI task per stream**: Simple and isolated
- **No manual routing**: Server handles stream multiplexing
- **Independent streams**: No head-of-line blocking between streams
- **Clean lifecycle**: Automatic cleanup when stream ends

## Browser Support

WebTransport browser support (as of 2025):

| Browser | Support |
|---------|---------|
| Chrome 97+ | ✅ Full support |
| Edge 97+ | ✅ Full support |
| Firefox | 🔶 Experimental (behind flag) |
| Safari | ❌ Not yet supported |

Check current support: [caniuse.com/webtransport](https://caniuse.com/webtransport)

## Comparison with WebSocket

| Feature | WebSocket | WebTransport (Per-Stream) |
|---------|-----------|---------------------------|
| Protocol | HTTP/1.1 Upgrade | HTTP/3 CONNECT |
| Multiplexing | Single channel | Multiple independent streams |
| Head-of-line blocking | Yes | No (per-stream) |
| Browser support | Universal | Chrome/Edge 97+ |
| ASGI complexity | Simple | Simple (per-stream mode) |
| Connection migration | No | Yes (QUIC feature) |
| Use case | General bidirectional | High-performance, multiplexed |

## ASGI Specification Status

**Note**: WebTransport is not yet part of the official ASGI specification. The `"webtransport.stream"` scope type and message formats used in asgiri are a custom implementation that may change when/if WebTransport is standardized in ASGI.

## Limitations

1. **Browser Support**: Limited to modern Chromium-based browsers
2. **TLS Required**: WebTransport requires valid TLS certificates (same as HTTP/3)
3. **No Datagrams**: Current implementation only supports streams (no unreliable datagrams)
4. **Not ASGI Standard**: Custom scope type subject to change

## References

- [WebTransport Specification](https://w3c.github.io/webtransport/)
- [HTTP/3 WebTransport Draft](https://datatracker.ietf.org/doc/html/draft-ietf-webtrans-http3)
- [aioquic Library](https://github.com/aiortc/aioquic)
- [MDN WebTransport API](https://developer.mozilla.org/en-US/docs/Web/API/WebTransport)
- [HTTP/3 Implementation Guide](HTTP3_IMPLEMENTATION.md)

## Example: Complete WebTransport Server

See `examples/webtransport_per_stream_example.py` for a complete working example with an HTML client.
