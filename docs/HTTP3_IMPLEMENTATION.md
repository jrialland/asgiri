# HTTP/3 Implementation Guide

## Overview

Asgiri now supports **HTTP/3** (HTTP over QUIC), the latest version of the HTTP protocol. HTTP/3 brings significant improvements over HTTP/2:

- **Better performance over lossy networks**: QUIC handles packet loss more efficiently than TCP
- **Reduced latency**: 0-RTT connection resumption for returning clients
- **Improved multiplexing**: True stream independence without head-of-line blocking
- **Built-in encryption**: TLS 1.3 is mandatory and integrated into QUIC

### Related Documentation

- **[WebSocket over HTTP/3](WEBSOCKETS_HTTP3.md)** - RFC 9220 implementation for WebSocket support over HTTP/3
- **[WebTransport](WEBTRANSPORT.md)** - WebTransport protocol support over HTTP/3

## Architecture

### Dual Transport Design

Asgiri runs **two servers simultaneously** when HTTP/3 is enabled:

```
┌─────────────────────────────────────────┐
│         Asgiri Server                   │
├─────────────────────────────────────────┤
│                                         │
│  TCP Server (Port 8443)                 │
│  ├─ HTTP/1.1 Protocol                   │
│  ├─ HTTP/2 Protocol                     │
│  └─ Auto Protocol Detection             │
│                                         │
│  UDP Server (Port 8443)                 │
│  └─ HTTP/3 Protocol (QUIC)              │
│                                         │
└─────────────────────────────────────────┘
```

Both servers:
- Can use the **same port number** (TCP port 8443 and UDP port 8443)
- Share the same **ASGI application**
- Are managed by a single `Server` instance

### Protocol Advertisement

All HTTP protocols automatically advertise HTTP/3 availability via the `Alt-Svc` header:

```http
Alt-Svc: h3=":8443"; ma=86400
```

This tells clients:
- HTTP/3 is available on the same port
- The information is valid for 86400 seconds (24 hours)

## Usage

### Basic Server Setup

```python
from asgiri.server import Server, HttpProtocolVersion

async def app(scope, receive, send):
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({
        "type": "http.response.body",
        "body": b"Hello from HTTP/3!",
    })

# Create server with HTTP/3 enabled
server = Server(
    app=app,
    host="0.0.0.0",
    port=8443,
    http_version=HttpProtocolVersion.AUTO,
    certfile="cert.pem",      # Required for HTTP/3
    keyfile="key.pem",        # Required for HTTP/3
    enable_http3=True,        # Enable HTTP/3 (default: True)
)

await server.a_run()
```

### Configuration Options

```python
Server(
    app=app,
    
    # Network configuration
    host="0.0.0.0",
    port=8443,
    
    # Protocol selection
    http_version=HttpProtocolVersion.AUTO,  # AUTO, HTTP_1_1, HTTP_2, HTTP_3
    
    # HTTP/3 configuration
    enable_http3=True,         # Enable/disable HTTP/3
    http3_port=None,           # Use different port for QUIC (default: same as port)
    
    # TLS configuration (required for HTTP/3)
    certfile="cert.pem",
    keyfile="key.pem",
    # OR use in-memory certificates
    cert_data=cert_bytes,
    key_data=key_bytes,
)
```

### Protocol Version Options

```python
# Auto-detect HTTP/1.1 and HTTP/2, enable HTTP/3
HttpProtocolVersion.AUTO

# HTTP/1.1 only (no HTTP/3)
HttpProtocolVersion.HTTP_1_1

# HTTP/2 only (no HTTP/3)
HttpProtocolVersion.HTTP_2

# HTTP/3 only (no TCP server)
HttpProtocolVersion.HTTP_3
```

## Requirements

### Dependencies

HTTP/3 support requires the `aioquic` library:

```bash
pip install aioquic
# or
uv add aioquic
```

### TLS Certificates

HTTP/3 **requires** TLS certificates. You have three options:

#### 1. Production Certificates (Recommended)

```python
Server(
    app=app,
    certfile="/etc/letsencrypt/live/example.com/fullchain.pem",
    keyfile="/etc/letsencrypt/live/example.com/privkey.pem",
    enable_http3=True,
)
```

#### 2. Self-Signed Certificates (Development)

```python
from generate_cert import generate_self_signed_cert

cert_data, key_data = generate_self_signed_cert()

Server(
    app=app,
    cert_data=cert_data,
    key_data=key_data,
    enable_http3=True,
)
```

#### 3. File-based Self-Signed (Development)

```bash
python generate_cert.py
```

```python
Server(
    app=app,
    certfile="localhost.pem",
    keyfile="localhost-key.pem",
    enable_http3=True,
)
```

## Testing HTTP/3

### With curl (HTTP/3 enabled)

```bash
# Install curl with HTTP/3 support
# See: https://github.com/curl/curl/blob/master/docs/HTTP3.md

# Test HTTP/3 endpoint
curl --http3 https://localhost:8443/ -k

# Verify Alt-Svc header
curl -I https://localhost:8443/ -k | grep -i alt-svc
```

### With Modern Browsers

1. Chrome/Edge (HTTP/3 enabled by default)
2. Firefox (HTTP/3 enabled by default)
3. Safari (HTTP/3 support varies by version)

Open developer tools → Network tab → check Protocol column

### With aioquic Client

```python
from aioquic.asyncio.client import connect
from aioquic.quic.configuration import QuicConfiguration
from aioquic.h3.connection import H3Connection

# Configure client
configuration = QuicConfiguration(
    alpn_protocols=["h3"],
    is_client=True,
    verify_mode=ssl.CERT_NONE,  # For self-signed certs
)

# Connect to HTTP/3 server
async with connect(
    "127.0.0.1",
    8443,
    configuration=configuration,
) as protocol:
    # Send HTTP/3 request
    # ...
```

## Implementation Details

### HTTP/3 Protocol Handler

The `HTTP3ServerProtocol` class implements the ASGI interface over QUIC:

```python
from asgiri.proto.http3 import HTTP3ServerProtocol
```

Key features:
- **QUIC event handling**: Translates QUIC events to HTTP/3 events
- **Stream management**: Manages multiple concurrent HTTP/3 streams
- **ASGI integration**: Converts HTTP/3 requests to ASGI scope/receive/send
- **Error handling**: Graceful error responses for protocol violations

### ASGI Scope for HTTP/3

```python
{
    "type": "http",
    "asgi": {"version": "3.0", "spec_version": "2.3"},
    "http_version": "3",  # Note: "3" for HTTP/3
    "method": "GET",
    "scheme": "https",    # Always HTTPS for HTTP/3
    "path": "/",
    "query_string": b"",
    "headers": [...],
    "server": ("127.0.0.1", 8443),
    "client": None,       # QUIC client address
    "extensions": {},
}
```

### Alt-Svc Header Format

The server automatically adds Alt-Svc headers to responses:

```python
# HTTP/1.1 responses
Alt-Svc: h2c=":8443", h3=":8443"; ma=86400

# HTTP/2 responses
Alt-Svc: h3=":8443"; ma=86400

# Parameters:
# - h3: HTTP/3 protocol identifier
# - ":8443": Port (":port" uses same host)
# - ma=86400: Max age in seconds (24 hours)
```

## Performance Considerations

### Connection Establishment

| Protocol | Handshakes Required | RTTs to First Byte |
|----------|--------------------|--------------------|
| HTTP/1.1 | TCP + TLS          | ~2-3 RTTs          |
| HTTP/2   | TCP + TLS + ALPN   | ~2-3 RTTs          |
| HTTP/3   | QUIC (0-RTT*)      | ~1 RTT*            |

*0-RTT for returning clients with valid tokens

### When to Use HTTP/3

**Best for:**
- Mobile networks with packet loss
- Long-distance connections
- Applications requiring low latency
- Scenarios with frequent connection changes (WiFi → Cellular)

**May not help:**
- Local network traffic (very low packet loss)
- Large file transfers on stable connections
- Legacy clients without HTTP/3 support

## Troubleshooting

### HTTP/3 Not Starting

**Symptom**: Server starts but HTTP/3 is not available

**Possible causes:**
1. No TLS certificates provided
2. `aioquic` library not installed
3. `enable_http3=False` in configuration

**Solution:**
```python
# Check logs for warnings
# Ensure certificates are provided
Server(
    app=app,
    certfile="cert.pem",
    keyfile="key.pem",
    enable_http3=True,
)
```

### UDP Port Blocked

**Symptom**: TCP works but HTTP/3 connections fail

**Possible causes:**
- Firewall blocking UDP traffic
- NAT not configured for UDP

**Solution:**
```bash
# Check firewall rules
sudo ufw allow 8443/udp

# Test UDP connectivity
nc -u localhost 8443
```

### Client Not Upgrading to HTTP/3

**Symptom**: Clients always use HTTP/2

**Possible causes:**
1. Client doesn't support HTTP/3
2. Alt-Svc header not being sent
3. Client caching old Alt-Svc information

**Solution:**
```bash
# Verify Alt-Svc header
curl -I https://localhost:8443/ -k | grep -i alt-svc

# Clear browser cache
# Wait for Alt-Svc max-age to expire
# Check browser's HTTP/3 support (chrome://flags)
```

## Future Enhancements

### Planned Features

- [x] **WebSocket over HTTP/3**: RFC 9220 implementation - [See Documentation](WEBSOCKETS_HTTP3.md)
- [x] **WebTransport**: Bidirectional streams over HTTP/3 - [See Documentation](WEBTRANSPORT.md)
- [ ] **0-RTT Resumption**: Connection resumption for returning clients
- [ ] **Connection Migration**: Seamless connection handoff between networks
- [ ] **QPACK Dictionary**: Custom compression dictionaries
- [ ] **Priority Hints**: Request prioritization for optimal performance

### Migration Path

The current implementation provides a solid foundation for HTTP/3 support. Future updates will be backward-compatible with the current API.

## References

- [RFC 9114 - HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html)
- [RFC 9000 - QUIC](https://www.rfc-editor.org/rfc/rfc9000.html)
- [RFC 9220 - WebSocket over HTTP/3](https://www.rfc-editor.org/rfc/rfc9220.html)
- [aioquic Documentation](https://aioquic.readthedocs.io/)
- [Alt-Svc Header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Alt-Svc)
- [WebSocket over HTTP/3 Guide](WEBSOCKETS_HTTP3.md)
- [WebTransport Guide](WEBTRANSPORT.md)

## License

HTTP/3 implementation in asgiri follows the same license as the main project.
