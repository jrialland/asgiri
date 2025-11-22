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
from asgiri.ssl_utils import generate_self_signed_cert

cert_data, key_data = generate_self_signed_cert()

Server(
    app=app,
    cert_data=cert_data,
    key_data=key_data,
    enable_http3=True,
)
```

#### 3. File-based Self-Signed (Development)

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

## Multi-Worker Limitations

### HTTP/3 and Multiprocessing

> **⚠️ Critical Limitation:** HTTP/3 is **not compatible** with multi-worker mode (`--workers > 1` or `reuse_port=True`). When multiprocessing is enabled, HTTP/3 will be automatically disabled.

#### Why UDP Multiprocessing Doesn't Work for QUIC

Unlike TCP, where `SO_REUSEPORT` works well with multiple worker processes, UDP presents fundamental challenges for stateful protocols like QUIC:

**TCP with SO_REUSEPORT (works well):**
```
Client connects → Kernel routes to Worker 1
└─ All subsequent packets for this connection → Worker 1 (sticky)
   └─ Connection state maintained in Worker 1
```

**UDP with SO_REUSEPORT (breaks QUIC):**
```
Client sends packet 1 (Connection ID: ABC123) → Kernel routes to Worker 1
Client sends packet 2 (Connection ID: ABC123) → Kernel routes to Worker 2
                                                 └─ Worker 2 has no state for ABC123!
                                                    └─ Connection fails ❌
```

#### Technical Details

**The Core Problem:**

1. **QUIC is stateful**: Each connection maintains complex state (encryption keys, stream data, flow control)
2. **UDP is stateless**: The kernel's `SO_REUSEPORT` distributes individual UDP packets across workers
3. **No connection affinity**: Without special handling, packets from the same QUIC connection can arrive at different workers
4. **Isolated worker state**: Each worker process has its own memory space; they don't share QUIC connection state

**What happens when packets are misrouted:**

```python
# Worker 1 state
quic_connections = {
    "ABC123": QuicConnection(keys=..., streams=..., flow_control=...)
}

# Worker 2 receives packet for "ABC123"
# Worker 2 state: {} (empty - no connection!)
# Worker 2 sends CONNECTION_CLOSE (unknown connection ID)
# Client connection breaks
```

#### Modern Kernel Improvements

Linux 3.9+ introduced `SO_REUSEPORT` with hash-based load balancing that attempts to maintain **source IP:port affinity**:

```c
// Kernel pseudo-code
worker_id = hash(src_ip, src_port, dst_ip, dst_port) % num_workers
```

**Why this still doesn't work for QUIC:**

1. **Connection Migration**: QUIC clients can change IP/port mid-connection (WiFi → Cellular)
   - Hash changes → packet routed to different worker → connection breaks

2. **NAT Rebinding**: Network address translation can change source ports
   - Hash changes → different worker → connection breaks

3. **Load Balancing**: Hash distribution may not be even
   - Some workers get overloaded while others idle

4. **Kernel Variation**: Not all kernels implement affinity the same way
   - Behavior differs between Linux, BSD, Windows

#### How Production QUIC Servers Handle Multi-Worker

Advanced implementations use sophisticated techniques:

**1. Connection ID Encoding (nginx QUIC approach)**
```python
# Encode worker ID in connection ID
connection_id = encode_worker_id(worker_num=2) + random_bytes()
# Example: "02ABC123" where "02" = Worker 2

# Custom BPF program examines connection ID and routes to correct worker
```

**2. eBPF/XDP Packet Steering**
```c
// eBPF program in kernel
int route_quic_packet(struct xdp_md *ctx) {
    // Parse QUIC header
    uint8_t *conn_id = parse_connection_id(ctx);
    
    // Extract worker ID from connection ID
    int worker = decode_worker_id(conn_id);
    
    // Route to specific CPU/worker
    return bpf_redirect_cpu(worker);
}
```

**3. Shared Connection State (Complex)**
```python
# All workers share state via Redis/shared memory
class SharedQuicState:
    def __init__(self):
        self.redis = Redis()
    
    def get_connection(self, conn_id):
        # Fetch from shared store
        return self.redis.get(f"quic:{conn_id}")
```

**Challenges:**
- High latency (network/IPC overhead)
- Complex synchronization
- Encryption key sharing security concerns
- Difficult error handling

**4. Thread-Based Concurrency (Limited)**
```python
# Single process, multiple threads
# All threads share same UDP socket and connection state
# Python GIL limits effectiveness for CPU-bound work
```

#### Why Asgiri Takes the Conservative Approach

Asgiri uses `aioquic`, which:
- ✅ Provides excellent QUIC/HTTP/3 implementation
- ❌ Does not implement connection ID routing
- ❌ Does not support shared connection state
- ❌ Assumes single-process architecture

**The code's safety check:**
```python
# asgiri/server.py
if reuse_port and enable_http3:
    logger.warning(
        "HTTP/3 is not compatible with multi-worker mode. "
        "Disabling HTTP/3. Use a reverse proxy for HTTP/3 load balancing."
    )
    enable_http3 = False
```

This prevents broken connections and confusing errors.

#### Recommended Architecture for HTTP/3 at Scale

Use a reverse proxy that handles HTTP/3, forwarding to multiple Asgiri workers via HTTP/2:

```
Internet (HTTP/3)
    ↓
┌─────────────────────┐
│   nginx/HAProxy     │  ← Handles HTTP/3, single process
│   (HTTP/3 → HTTP/2) │
└──────────┬──────────┘
           │ HTTP/2
    ┌──────┼──────┬──────┐
    ↓      ↓      ↓      ↓
┌────────┐┌────────┐┌────────┐
│Asgiri  ││Asgiri  ││Asgiri  │  ← Multiple workers, HTTP/2
│Worker 1││Worker 2││Worker 3││     SO_REUSEPORT works here!
└────────┘└────────┘└────────┘
```

**Benefits:**
- ✅ HTTP/3 for clients (low latency, better mobile performance)
- ✅ Multi-worker backend (CPU parallelism)
- ✅ Proven architecture (nginx QUIC is battle-tested)
- ✅ No complex connection state management

**Example nginx configuration:**
```nginx
http {
    upstream asgiri_backend {
        server 127.0.0.1:8001;
        server 127.0.0.1:8002;
        server 127.0.0.1:8003;
        server 127.0.0.1:8004;
    }
    
    server {
        listen 443 quic reuseport;  # HTTP/3
        listen 443 ssl;              # HTTP/2 fallback
        
        http3 on;
        
        location / {
            proxy_pass http://asgiri_backend;
            proxy_http_version 2.0;
        }
    }
}
```

#### Future Possibilities

Implementing multi-worker HTTP/3 in Asgiri would require:

1. **Connection ID Management**
   - Encode worker ID in connection IDs
   - Implement connection ID negotiation

2. **Packet Steering**
   - eBPF/BPF program to route by connection ID
   - Or custom socket routing logic

3. **State Synchronization**
   - Shared memory for connection state
   - Or hand-off protocol between workers

4. **Migration Handling**
   - Handle connection migration across workers
   - Update routing tables dynamically

This is a significant undertaking that would require deep integration with aioquic internals or a custom QUIC implementation. The reverse proxy approach is simpler and more maintainable.

### Summary

| Feature | Single Worker | Multi-Worker | Reverse Proxy + Workers |
|---------|---------------|--------------|-------------------------|
| HTTP/3 Support | ✅ Yes | ❌ No | ✅ Yes |
| CPU Parallelism | ❌ Limited | ✅ Yes | ✅ Yes |
| Complexity | ✅ Simple | ⚠️ Broken | ⚠️ Moderate |
| **Recommended** | Development | - | **Production** |

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
