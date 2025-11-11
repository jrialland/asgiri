# Protocol Switching Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         Client Connection                        │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                │ TCP Connection Established
                                │
                                ▼
                    ┌───────────────────────┐
                    │    AutoProtocol       │
                    │   (Protocol Router)   │
                    └───────────┬───────────┘
                                │
                    Buffer first few bytes
                    and analyze protocol
                                │
                ┌───────────────┴────────────────┐
                │                                │
                ▼                                ▼
    Starts with "PRI * HTTP/2.0"?    Starts with HTTP method?
         (HTTP/2 Preface)                 (GET, POST, etc.)
                │                                │
                │ YES                            │ YES
                ▼                                ▼
    ┌─────────────────────┐          ┌──────────────────────┐
    │ Http2ServerProtocol │          │ HTTP11ServerProtocol │
    │                     │          │  + Alt-Svc Wrapper   │
    └──────────┬──────────┘          └──────────┬───────────┘
               │                                 │
               │                                 │
               ▼                                 ▼
    ┌─────────────────────┐          ┌──────────────────────┐
    │   HTTP/2 Handling   │          │  HTTP/1.1 Handling   │
    │                     │          │                      │
    │ - Multiplexing      │          │ - Sequential Reqs    │
    │ - Server Push       │          │ - Keep-Alive         │
    │ - Header Compression│          │ - Chunked Transfer   │
    │ - Stream Priority   │          │                      │
    └──────────┬──────────┘          │  Response Headers:   │
               │                     │  Alt-Svc: h2c=":8000"│
               │                     └──────────┬───────────┘
               │                                │
               │                                │
               └────────────┬───────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │  ASGI App     │
                    │               │
                    │  scope        │
                    │  receive      │
                    │  send         │
                    └───────────────┘
```

## Data Flow

### HTTP/2 Connection (Prior Knowledge)

```
1. Client → Server: PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n
2. AutoProtocol detects HTTP/2 preface
3. Delegates to Http2ServerProtocol
4. All subsequent data flows through HTTP/2 handler
5. Multiple requests multiplexed on single connection
```

### HTTP/1.1 Connection

```
1. Client → Server: GET / HTTP/1.1\r\n...
2. AutoProtocol detects HTTP/1.1 request
3. Wraps ASGI app with Alt-Svc injector
4. Delegates to HTTP11ServerProtocol
5. Response includes: Alt-Svc: h2c=":8000"
6. Client learns about HTTP/2 support for future connections
```

## Component Responsibilities

### AutoProtocol
- Buffer initial bytes
- Detect protocol type
- Delegate to appropriate handler
- Pass through all subsequent data

### HTTP11ServerProtocol
- Parse HTTP/1.1 requests using h11
- Handle keep-alive connections
- Sequential request processing
- Standard HTTP/1.1 semantics

### Http2ServerProtocol
- Parse HTTP/2 frames using h2
- Handle multiplexed streams
- Flow control and prioritization
- Server push (if needed)

### Alt-Svc Wrapper
- Intercept http.response.start events
- Inject Alt-Svc header
- Advertise h2c capability
- Transparent to ASGI app

## Protocol Transition Scenarios

### Scenario 1: HTTP/1.1 Client (curl)
```bash
curl http://localhost:8000/

# Connection 1:
Request  → HTTP/1.1
Response ← HTTP/1.1 + Alt-Svc: h2c=":8000"

# Connection 2 (curl doesn't auto-upgrade):
Request  → HTTP/1.1
Response ← HTTP/1.1 + Alt-Svc: h2c=":8000"
```

### Scenario 2: HTTP/2 Client (curl with --http2-prior-knowledge)
```bash
curl --http2-prior-knowledge http://localhost:8000/

# Single Connection:
Request  → HTTP/2 (PRI * HTTP/2.0...)
Response ← HTTP/2 (binary frames)
Request  → HTTP/2 (stream 3)
Response ← HTTP/2 (stream 3)
# Multiple requests multiplexed
```

### Scenario 3: Smart Client (httpx, modern browsers)
```
# Connection 1:
Client → HTTP/1.1 request
Server → HTTP/1.1 response + Alt-Svc: h2c=":8000"

# Client notices Alt-Svc, opens new connection:
Client → HTTP/2 preface (PRI * HTTP/2.0...)
Server → HTTP/2 response
# Client uses HTTP/2 for all subsequent requests
```

## Key Benefits

1. **Backward Compatibility**: HTTP/1.1 clients work without changes
2. **Performance**: HTTP/2 clients get multiplexing and compression
3. **Simple API**: ASGI app doesn't need protocol-specific code
4. **Automatic Upgrade Path**: Clients discover HTTP/2 organically
5. **Single Port**: No need for separate ports per protocol
