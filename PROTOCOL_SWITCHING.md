# HTTP Protocol Auto-Detection and Switching

## Overview

The `AutoProtocol` class in `asgiri.proto.auto` enables a server to automatically detect and handle both HTTP/1.1 and HTTP/2 connections on the same port. This solves the common architecture challenge of supporting multiple HTTP versions simultaneously.

## How It Works

### 1. Protocol Detection

When a client connects, the server buffers initial data and analyzes it to determine which protocol to use:

- **HTTP/2 Prior Knowledge (h2c)**: Detects the HTTP/2 connection preface: `PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`
- **HTTP/1.1**: Detects standard HTTP/1.1 request methods (GET, POST, etc.)

### 2. Protocol Delegation

Once detected, the connection is delegated to the appropriate protocol handler:
- `Http2ServerProtocol` for HTTP/2 connections
- `HTTP11ServerProtocol` for HTTP/1.1 connections

### 3. HTTP/2 Advertisement

For HTTP/1.1 connections, the server automatically adds an `Alt-Svc` header to responses:
```
Alt-Svc: h2c=":8000"
```

This advertises to clients that the server supports HTTP/2 on the same port, allowing clients to upgrade to HTTP/2 for future requests.

## Usage

### Basic Usage (Default)

By default, the server uses auto-detection:

```python
from asgiri.server import Server

server = Server(app=app, host="127.0.0.1", port=8000)
server.run()
```

### Explicit Auto Protocol

You can explicitly specify the AUTO protocol:

```python
from asgiri.server import Server, HttpProtocolVersion

server = Server(
    app=app,
    host="127.0.0.1",
    port=8000,
    http_version=HttpProtocolVersion.AUTO
)
server.run()
```

### Force Specific Protocol

You can still force a specific protocol if needed:

```python
# Force HTTP/1.1 only
server = Server(
    app=app,
    host="127.0.0.1",
    port=8000,
    http_version=HttpProtocolVersion.HTTP_1_1
)

# Force HTTP/2 only
server = Server(
    app=app,
    host="127.0.0.1",
    port=8000,
    http_version=HttpProtocolVersion.HTTP_2
)
```

## HTTP/2 Negotiation Methods

There are three standard ways clients can negotiate HTTP/2:

### 1. HTTP/2 Prior Knowledge (h2c)

The client knows the server supports HTTP/2 and sends the HTTP/2 connection preface immediately:

```bash
curl --http2-prior-knowledge http://localhost:8000/
```

**Flow:**
```
Client -> Server: PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n [HTTP/2 preface]
Server: [Detects HTTP/2, delegates to Http2ServerProtocol]
Server -> Client: [HTTP/2 response]
```

### 2. HTTP/1.1 Upgrade (Not Yet Implemented)

The client sends an HTTP/1.1 request with an `Upgrade: h2c` header:

```
GET / HTTP/1.1
Host: localhost:8000
Connection: Upgrade, HTTP2-Settings
Upgrade: h2c
HTTP2-Settings: <base64-encoded-settings>
```

**Flow:**
```
Client -> Server: GET / HTTP/1.1\r\nUpgrade: h2c\r\n...
Server -> Client: HTTP/1.1 101 Switching Protocols\r\n...
[Connection switches to HTTP/2]
```

> **Note:** This method is not yet implemented in the current version. The server will handle the request as HTTP/1.1 and advertise HTTP/2 via Alt-Svc for future connections.

### 3. ALPN (Application-Layer Protocol Negotiation)

For TLS connections, the protocol is negotiated during the TLS handshake using ALPN:

```python
# With TLS (future enhancement)
server = Server(
    app=app,
    host="127.0.0.1",
    port=8443,
    http_version=HttpProtocolVersion.AUTO,
    certificate=cert_bytes,
    private_key=key_bytes
)
```

The server would advertise `['h2', 'http/1.1']` during TLS negotiation.

## Testing

### Test HTTP/1.1 Connection

```bash
curl -v http://localhost:8000/
```

Look for the `Alt-Svc` header in the response:
```
< HTTP/1.1 200 OK
< alt-svc: h2c=":8000"
< content-type: text/plain
< content-length: 30
< 
Hello! You're using HTTP/1.1
```

### Test HTTP/2 Connection (Prior Knowledge)

```bash
curl --http2-prior-knowledge http://localhost:8000/
```

Response:
```
Hello! You're using HTTP/2
```

### Using Python's httpx

```python
import httpx

# HTTP/1.1
with httpx.Client() as client:
    response = client.get("http://localhost:8000/")
    print(f"Protocol: HTTP/{response.http_version}")
    print(f"Alt-Svc: {response.headers.get('alt-svc', 'Not present')}")

# HTTP/2
with httpx.Client(http2=True) as client:
    response = client.get("http://localhost:8000/")
    print(f"Protocol: HTTP/{response.http_version}")
```

## Architecture Details

### Buffer Management

The `AutoProtocol` buffers incoming data until it can determine the protocol:

- Minimum buffer: 4 bytes (enough to distinguish "PRI " vs other HTTP methods)
- Maximum buffer: 24 bytes (length of HTTP/2 preface)

Once the protocol is detected, all buffered data is passed to the delegated protocol handler.

### State Transitions

```
[Initial State]
     |
     v
[Buffering Data]
     |
     v
[Protocol Detection]
     |
     +-- HTTP/2 detected --> [Delegate to Http2ServerProtocol]
     |
     +-- HTTP/1.1 detected --> [Delegate to HTTP11ServerProtocol + Alt-Svc wrapper]
```

### ASGI Application Wrapping

For HTTP/1.1 connections, the ASGI application is wrapped to inject the `Alt-Svc` header:

```python
async def wrapped_send(message):
    if message["type"] == "http.response.start":
        headers = list(message.get("headers", []))
        headers.append((b"alt-svc", b'h2c=":8000"'))
        message = dict(message)
        message["headers"] = headers
    await original_send(message)
```

This ensures all HTTP/1.1 responses advertise HTTP/2 capability without modifying the application code.

## Limitations

1. **HTTP/1.1 Upgrade**: The `Upgrade: h2c` mechanism is not yet implemented. Clients must use "prior knowledge" or make a new connection for HTTP/2.

2. **ALPN**: TLS/ALPN negotiation is not yet implemented. When TLS support is added, ALPN will be the preferred method for protocol negotiation.

3. **HTTP/3**: HTTP/3 (QUIC) requires UDP and is not supported by this TCP-based implementation.

## Future Enhancements

- [ ] Implement HTTP/1.1 Upgrade mechanism (101 Switching Protocols)
- [ ] Add ALPN support for TLS connections
- [ ] Optimize buffer management for high-throughput scenarios
- [ ] Add metrics/logging for protocol distribution
- [ ] Support graceful protocol fallback

## References

- [RFC 7540 - HTTP/2](https://tools.ietf.org/html/rfc7540)
- [RFC 7230 - HTTP/1.1 Message Syntax and Routing](https://tools.ietf.org/html/rfc7230)
- [RFC 7838 - HTTP Alternative Services](https://tools.ietf.org/html/rfc7838)
- [RFC 7301 - TLS Application-Layer Protocol Negotiation Extension](https://tools.ietf.org/html/rfc7301)
