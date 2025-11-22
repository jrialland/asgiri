# HTTP Protocol Auto-Detection and Switching

## Overview

The `AutoProtocol` class in `asgiri.proto.auto` enables a server to automatically detect and handle both HTTP/1.1 and HTTP/2 connections on the same port. This solves the common architecture challenge of supporting multiple HTTP versions simultaneously.

## How It Works

### Protocol Selection Priority

The `AutoProtocol` uses a smart multi-tier approach to select the best protocol:

1. **ALPN Negotiation (TLS only)** - Fastest method, immediate selection during TLS handshake
2. **HTTP/2 Prior Knowledge Detection** - Detects HTTP/2 connection preface for cleartext
3. **HTTP/1.1 Fallback** - Default for standard HTTP requests

### 1. ALPN Protocol Selection (TLS Connections)

For TLS connections, ALPN (Application-Layer Protocol Negotiation) is the **preferred and fastest** method:

- **No buffering required**: Protocol is selected during TLS handshake
- **Zero latency**: Immediate delegation to the correct protocol handler
- **Supported protocols**: `['h2', 'http/1.1']`

**Flow:**
```
Client <-> Server: TLS Handshake
  Client: ClientHello (ALPN: h2, http/1.1)
  Server: ServerHello (ALPN: h2)
Server: [Immediately uses HTTP/2 - no data inspection needed]
```

### 2. HTTP/2 Prior Knowledge Detection (Cleartext)

For cleartext (non-TLS) connections, the server detects the HTTP/2 connection preface:

- **Preface**: `PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`
- **Detection**: Buffers initial bytes and checks for preface
- **Delegation**: Switches to `Http2ServerProtocol`

### 3. HTTP/1.1 Fallback

If neither ALPN nor HTTP/2 preface is detected:

- **Detection**: Checks for standard HTTP/1.1 request methods (GET, POST, etc.)
- **Delegation**: Switches to `HTTP11ServerProtocol`
- **Advertisement**: Adds `Alt-Svc` header to advertise HTTP/2 and HTTP/3

### 4. Protocol Delegation

Once detected, the connection is delegated to the appropriate protocol handler:
- `Http2ServerProtocol` for HTTP/2 connections
- `HTTP11ServerProtocol` for HTTP/1.1 connections

### 5. HTTP/2 and HTTP/3 Advertisement

For HTTP/1.1 connections, the server automatically adds an `Alt-Svc` header to responses:
```
Alt-Svc: h2c=":8000", h3=":8000"; ma=86400
```

This advertises to clients that the server supports HTTP/2 (h2c) and HTTP/3 (h3) on the same port, 
allowing clients to upgrade for future requests.

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

### 2. HTTP/1.1 Upgrade (✅ Implemented)

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

The server validates the upgrade request, sends a 101 Switching Protocols response,
and switches the connection to HTTP/2. The original HTTP/1.1 request is processed
as HTTP/2 stream 1 per RFC 7540 Section 3.2.

**Implementation notes:**
- Validates presence of `Upgrade: h2c` header
- Validates presence of `HTTP2-Settings` header with base64url encoded settings
- Verifies `Connection` header includes both `Upgrade` and `HTTP2-Settings`
- Sends raw 101 response (h11 library doesn't support 101 status code)
- Properly handles stream 1 as the upgraded request
- Supports query strings, custom headers, and all HTTP methods

### 3. ALPN (Application-Layer Protocol Negotiation) - ✅ Implemented

For TLS connections, the protocol is negotiated during the TLS handshake using ALPN.
This is the **fastest and most efficient** method as it avoids waiting for application data.

```python
from asgiri.server import Server

# ALPN is automatically enabled for TLS connections
server = Server(
    app=app,
    host="127.0.0.1",
    port=8443,
    certfile="cert.pem",
    keyfile="key.pem",
)
```

**Flow:**
```
Client <-> Server: TLS Handshake with ALPN
  Client: ClientHello (ALPN: h2, http/1.1)
  Server: ServerHello (ALPN: h2)
Server: [Immediately delegates to HTTP/2, no data inspection needed]
```

The SSL context is configured with ALPN protocols `['h2', 'http/1.1']` and the
`AutoProtocol` checks the negotiated protocol immediately on connection to delegate
to the appropriate handler without waiting for data.

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

### Test HTTP/2 Connection (h2c Upgrade)

```bash
# Using curl with HTTP/1.1 upgrade
curl -v --http2 http://localhost:8000/

# Note: curl will attempt upgrade if server advertises it via Alt-Svc
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

~~1. **HTTP/1.1 Upgrade**: The `Upgrade: h2c` mechanism is not yet implemented. Clients must use "prior knowledge" or make a new connection for HTTP/2.~~ **HTTP/1.1 Upgrade is now fully implemented!**

~~2. **ALPN**: TLS/ALPN negotiation is not yet implemented. When TLS support is added, ALPN will be the preferred method for protocol negotiation.~~ **ALPN is now implemented!** TLS connections use ALPN to immediately select the appropriate protocol (h2 or http/1.1).

## Future Enhancements

- [x] ~~Implement HTTP/1.1 Upgrade mechanism (101 Switching Protocols)~~ **Implemented!**
- [x] ~~Add ALPN support for TLS connections~~ **Implemented!**
- [ ] Optimize buffer management for high-throughput scenarios
- [ ] Add metrics/logging for protocol distribution
- [ ] Support graceful protocol fallback

## References

- [RFC 7540 - HTTP/2](https://tools.ietf.org/html/rfc7540)
- [RFC 7230 - HTTP/1.1 Message Syntax and Routing](https://tools.ietf.org/html/rfc7230)
- [RFC 7838 - HTTP Alternative Services](https://tools.ietf.org/html/rfc7838)
- [RFC 7301 - TLS Application-Layer Protocol Negotiation Extension](https://tools.ietf.org/html/rfc7301)
