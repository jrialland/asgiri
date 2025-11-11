# Quick Reference: Protocol Switching

## TL;DR

**Yes, it's possible!** I've implemented automatic protocol switching that handles both HTTP/1.1 and HTTP/2 on the same port, with automatic HTTP/2 advertisement.

## What You Get

```python
from asgiri.server import Server

# This now works with BOTH HTTP/1.1 AND HTTP/2!
server = Server(app=my_asgi_app, host="127.0.0.1", port=8000)
server.run()
```

## How It Works

1. **Auto-Detection**: Server detects HTTP/2 preface or HTTP/1.1 requests
2. **Protocol Routing**: Delegates to appropriate handler
3. **Advertisement**: HTTP/1.1 responses include `Alt-Svc: h2c=":8000"`
4. **Transparent**: Your ASGI app requires zero changes

## Test Commands

```bash
# HTTP/1.1 (will see Alt-Svc header)
curl -v http://localhost:8000/

# HTTP/2 (prior knowledge)
curl --http2-prior-knowledge http://localhost:8000/
```

## Key Files

| File | Purpose |
|------|---------|
| `asgiri/proto/auto.py` | Auto-detection implementation |
| `example_auto_protocol.py` | Working example |
| `PROTOCOL_SWITCHING.md` | Detailed guide |
| `ARCHITECTURE.md` | Visual diagrams |

## Protocol Detection Logic

```
Data received → Check first few bytes
                      ↓
    "PRI * HTTP/2.0..." → HTTP/2 (delegate to Http2ServerProtocol)
    "GET / HTTP/1.1..." → HTTP/1.1 (delegate to HTTP11ServerProtocol + Alt-Svc)
```

## Response Headers

**HTTP/1.1 Response:**
```
HTTP/1.1 200 OK
alt-svc: h2c=":8000"          ← Advertises HTTP/2 support
x-protocol: HTTP/1.1          ← Shows protocol used (if using middleware)
content-type: text/plain
```

**HTTP/2 Response:**
```
HTTP/2 200
x-protocol: HTTP/2            ← Shows protocol used (if using middleware)
content-type: text/plain
```

## Configuration Options

```python
from asgiri.server import Server, HttpProtocolVersion

# Auto-detect (default) - RECOMMENDED
server = Server(app=app)

# Auto-detect (explicit)
server = Server(app=app, http_version=HttpProtocolVersion.AUTO)

# Force HTTP/1.1 only
server = Server(app=app, http_version=HttpProtocolVersion.HTTP_1_1)

# Force HTTP/2 only
server = Server(app=app, http_version=HttpProtocolVersion.HTTP_2)
```

## With Middleware

```python
from asgiri.middleware import protocol_info_middleware

# Add X-Protocol header to responses
app = protocol_info_middleware(my_asgi_app)
server = Server(app=app)
server.run()
```

## What's Advertised

Via the `Alt-Svc` header, your server advertises:

- **h2c**: HTTP/2 over cleartext (no TLS)
- **Same port**: Clients can use HTTP/2 on the same port

This follows RFC 7838 (HTTP Alternative Services).

## Browser Behavior

Most modern browsers will:
1. First request: Use HTTP/1.1
2. See `Alt-Svc` header
3. Subsequent requests: May use HTTP/2

## Common Scenarios

### Scenario 1: Legacy Client (HTTP/1.1 only)
- Uses HTTP/1.1 ✅
- Ignores Alt-Svc header
- Everything works fine

### Scenario 2: Modern Client (HTTP/2 capable)
- First request: HTTP/1.1
- Sees Alt-Svc header
- Next request: HTTP/2
- Performance improved! 🚀

### Scenario 3: Smart Client (prior knowledge)
- Directly uses HTTP/2
- No HTTP/1.1 request needed
- Optimal from the start 🎯

## Debugging

Enable debug logging:

```python
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
```

You'll see:
```
INFO - AutoProtocol - Detected HTTP/1.1 connection
INFO - AutoProtocol - Detected HTTP/2 connection (prior knowledge)
```

## HTTP/2 Benefits

When clients use HTTP/2:
- ✅ **Multiplexing**: Multiple requests on one connection
- ✅ **Header Compression**: Reduced bandwidth
- ✅ **Binary Protocol**: More efficient parsing
- ✅ **Flow Control**: Better resource management

## Next Steps

1. **Run the example**: `python example_auto_protocol.py`
2. **Test both protocols**: Use the curl commands above
3. **Integrate into your app**: Replace your server initialization
4. **Monitor**: Check logs to see which protocols clients use

## Need More Info?

- **How does it work?** → See `PROTOCOL_SWITCHING.md`
- **Architecture details?** → See `ARCHITECTURE.md`
- **Complete solution?** → See `SOLUTION_SUMMARY.md`
- **Quick start?** → See `README.md`

---

**That's it!** Your server now supports both HTTP/1.1 and HTTP/2 automatically. 🎉
