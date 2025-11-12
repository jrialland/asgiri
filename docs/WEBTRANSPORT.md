# WebTransport Support

## Overview

WebTransport is a protocol that provides low-latency, bidirectional communication between clients and servers over HTTP/3. It's designed as a modern replacement for WebSockets, leveraging QUIC's built-in multiplexing and improved connection migration.

**Key Feature**: asgiri implements WebTransport in **per-stream mode**, where each WebTransport stream triggers a new call to your ASGI application with a dedicated scope. This makes it simple and intuitive to use, similar to handling individual HTTP requests.

> ⚠️ **Experimental Feature**: The `"webtransport.stream"` scope type is a custom implementation not yet part of the official ASGI specification. This API may change in the future if/when WebTransport support is standardized in ASGI.

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

In asgiri, each WebTransport stream is handled independently by calling your ASGI application with a new scope. This design provides:

- ✅ **Simplicity**: Each stream triggers an independent app call
- ✅ **Familiar pattern**: Similar to handling HTTP requests
- ✅ **No manual multiplexing**: The server handles stream routing
- ✅ **Automatic cleanup**: Per-stream lifecycle management

### Why a Separate Scope Type?

WebTransport uses a custom `"webtransport.stream"` scope instead of reusing the existing `"websocket"` scope, even though both provide bidirectional binary communication. This design choice deserves explanation, as ASGI's purpose is normally to abstract away protocol differences when semantics are similar.

#### The Client API Perspective

When developers write client-side code, they make explicit choices about which API to use, and these choices reflect fundamentally different architectural patterns:

**WebSocket Client Code** - Single persistent connection model:
```javascript
// Client expects ONE connection that persists
const ws = new WebSocket('wss://server.com/chat');

ws.onopen = () => console.log('Connected');
ws.onmessage = (event) => {
    // All messages flow through this single handler
    console.log('Received:', event.data);
};

// All messages sent through the same connection
ws.send('message 1');
ws.send('message 2');  // Queued on same connection
ws.send('message 3');  // Sequential, ordered delivery
```

**WebTransport Client Code** - Multiple independent streams model:
```javascript
// Client expects MULTIPLE independent streams
const wt = new WebTransport('https://server.com/chat');
await wt.ready;

// Create separate streams for different purposes
const stream1 = await wt.createBidirectionalStream();
const stream2 = await wt.createBidirectionalStream();

// Each stream is completely independent
const writer1 = stream1.writable.getWriter();
const writer2 = stream2.writable.getWriter();

await writer1.write(encoder.encode('stream 1 data'));
await writer2.write(encoder.encode('stream 2 data'));  // Parallel, no ordering
```

When a client developer chooses `new WebTransport()` instead of `new WebSocket()`, they're making an **intentional architectural decision** to use a multiplexed, stream-based communication pattern. The server-side ASGI scope should reflect this intent.

#### Server Handler Expectations

The different client APIs lead to different server-side programming patterns:

**WebSocket Handler** - Expects single connection lifecycle:
```python
async def websocket_handler(scope, receive, send):
    if scope["type"] == "websocket":
        # Handler expects to manage ONE connection
        connection_state = {
            "user_id": None,
            "authenticated": False,
            "message_count": 0
        }
        
        await send({"type": "websocket.accept"})
        
        # Single connection loop
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                break
            
            # Maintain state across messages
            connection_state["message_count"] += 1
            # Process message using connection_state...
```

**WebTransport Stream Handler** - Expects independent stream lifecycle:
```python
async def webtransport_handler(scope, receive, send):
    if scope["type"] == "webtransport.stream":
        # Handler is called once PER STREAM
        stream_id = scope["stream_id"]
        session_id = scope["session_id"]
        
        # Each stream is independent and ephemeral
        stream_data = b""
        
        # Per-stream loop
        while True:
            message = await receive()
            if message["type"] == "webtransport.stream.disconnect":
                break
            
            # Process this stream's data independently
            stream_data += message["data"]
            # No shared state with other streams...
```

#### Why Not Reuse "websocket" Scope?

Initially, it seems appealing to map WebTransport streams to the existing `"websocket"` scope - after all, they both provide bidirectional communication, and ASGI's purpose is to abstract protocols. However, this would create serious problems:

**Problem 1: Violated Expectations**
```python
# Developer writes a WebSocket handler expecting ONE connection:
async def app(scope, receive, send):
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        
        # They expect this to run ONCE per connection
        user_session = initialize_user_session()
        
        while True:
            message = await receive()
            # Process with user_session state...

# If client uses WebTransport with 10 concurrent streams,
# this handler gets called 10 TIMES with scope["type"] == "websocket"
# Each call creates a separate user_session - NOT what was intended!
```

**Problem 2: Conflicting Semantics**
```python
# WebSocket: "One scope call = one persistent connection"
# Expected: Handler runs for minutes/hours, manages connection state

# WebTransport: "One scope call = one ephemeral stream"  
# Expected: Handler runs for seconds, processes stream data

# These are fundamentally different lifecycle expectations!
```

**Problem 3: Implicit Behavior**
Using the same scope type would hide the protocol difference, making it impossible for developers to handle the two patterns appropriately. With separate scopes, the intent is explicit:

- Client sends `new WebSocket()` → Server gets `"websocket"` scope → Handler implements connection-oriented pattern
- Client sends `new WebTransport()` → Server gets `"webtransport.stream"` scope → Handler implements stream-oriented pattern

#### The Justification

**Protocol differences matter when they change application architecture:**

1. **Client Intent**: When a developer chooses WebTransport over WebSocket, they're explicitly requesting multiplexed streams, not a single connection. The server should honor this architectural choice.

2. **Handler Design**: WebSocket handlers are designed around connection lifecycle and state management. WebTransport stream handlers are designed around independent, parallel stream processing. These patterns don't mix well.

3. **Explicit vs Implicit**: Using different scope types makes the protocol choice visible and intentional. Developers can write handlers specifically for the pattern their clients use.

4. **Future Compatibility**: When ASGI eventually standardizes WebTransport (if it does), it will likely introduce a dedicated scope type anyway. Our implementation anticipates this.

5. **No False Abstraction**: While ASGI should abstract protocol details, WebSocket and WebTransport represent genuinely different communication paradigms. Forcing them into the same scope would be a false abstraction that hides important architectural differences.

**Comparison with HTTP/1.1 vs HTTP/2:**
ASGI uses the same `"http"` scope for both HTTP/1.1 and HTTP/2 because the application semantics are identical - one request, one response. But WebSocket vs WebTransport have different semantics - one persistent connection vs many ephemeral streams. This difference justifies separate scope types.

#### Alternative Considered: Extensions Dictionary

We considered using the ASGI `extensions` mechanism:
```python
scope = {
    "type": "websocket",  # Reuse existing type
    "extensions": {
        "webtransport": {
            "session_id": 12345,
            "stream_id": 67890,
        }
    }
}
```

However, this approach has problems:
- Existing WebSocket handlers would unknowingly handle WebTransport streams incorrectly
- No way to distinguish handler intent (connection-oriented vs stream-oriented)
- Confusion about which message types are valid
- Extensions are meant for optional features, not fundamentally different protocols

#### Conclusion

The separate `"webtransport.stream"` scope type is justified because:

✅ **Reflects client architecture choice**: WebSocket API vs WebTransport API  
✅ **Matches handler expectations**: Connection state vs stream independence  
✅ **Makes intent explicit**: Developers know what pattern to implement  
✅ **Prevents incorrect usage**: WebSocket handlers won't accidentally handle streams  
✅ **Future-proof**: Aligns with likely ASGI standardization path  

While this creates a custom, experimental scope type outside the ASGI spec, it's the right design choice that respects the fundamental differences between these protocols and the architectural patterns they enable.

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
        # This handler is called once per stream
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
// Each stream triggers a new app call on the server
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
3. Server calls your ASGI application with a `"webtransport.stream"` scope
4. Stream data flows through dedicated `receive`/`send` callables
5. Stream ends when:
   - Client closes stream
   - Server sends `end_stream=True`
   - WebTransport session closes
6. ASGI application call completes and cleans up

### Session Management

- WebTransport session is automatically accepted by the server
- Each stream within a session triggers a new call to your ASGI app
- Streams are independent - no shared state between them
- Session is tracked but applications interact only with individual streams

### Performance

- **One app call per stream**: Simple and isolated
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

**⚠️ Important**: WebTransport is not yet part of the official ASGI specification. The `"webtransport.stream"` scope type and associated message formats used in asgiri are a custom experimental implementation. This API may change when/if WebTransport support becomes standardized in ASGI.

If you build applications using this feature, be prepared for potential API changes in future versions.

## Limitations

1. **Experimental ASGI Scope**: Not part of the official ASGI spec - API may change
2. **Browser Support**: Limited to modern Chromium-based browsers
3. **TLS Required**: WebTransport requires valid TLS certificates (same as HTTP/3)
4. **No Datagrams**: Current implementation only supports streams (no unreliable datagrams)

## References

- [WebTransport Specification](https://w3c.github.io/webtransport/)
- [HTTP/3 WebTransport Draft](https://datatracker.ietf.org/doc/html/draft-ietf-webtrans-http3)
- [aioquic Library](https://github.com/aiortc/aioquic)
- [MDN WebTransport API](https://developer.mozilla.org/en-US/docs/Web/API/WebTransport)
- [HTTP/3 Implementation Guide](HTTP3_IMPLEMENTATION.md)
