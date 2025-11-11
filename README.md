# Asgiri

A high-performance ASGI server implementation supporting both HTTP/1.1 and HTTP/2 protocols with automatic protocol detection.

## Features

- **Multi-Protocol Support**: Handles both HTTP/1.1 and HTTP/2 on the same port
- **Auto-Detection**: Automatically detects client protocol preference
- **Protocol Advertisement**: Advertises HTTP/2 capability via Alt-Svc headers
- **ASGI 3.0 Compatible**: Works with any ASGI 3.0 application
- **Async/Await**: Built on Python's asyncio for high concurrency

## Quick Start

```python
from asgiri.server import Server

async def app(scope, receive, send):
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({
        "type": "http.response.body",
        "body": b"Hello, World!",
    })

server = Server(app=app, host="127.0.0.1", port=8000)
server.run()
```

## Protocol Support

### Auto-Detection (Default)

By default, the server automatically detects whether the client is using HTTP/1.1 or HTTP/2:

```python
server = Server(app=app, host="127.0.0.1", port=8000)
# Automatically handles both HTTP/1.1 and HTTP/2
```

### Explicit Protocol Selection

You can force a specific protocol if needed:

```python
from asgiri.server import HttpProtocolVersion

# HTTP/1.1 only
server = Server(app=app, http_version=HttpProtocolVersion.HTTP_1_1)

# HTTP/2 only
server = Server(app=app, http_version=HttpProtocolVersion.HTTP_2)

# Auto-detection (explicit)
server = Server(app=app, http_version=HttpProtocolVersion.AUTO)
```

## How It Works

The server uses a smart protocol detection mechanism:

1. **HTTP/2 Prior Knowledge**: Detects the HTTP/2 connection preface (`PRI * HTTP/2.0...`)
2. **HTTP/1.1 Fallback**: Falls back to HTTP/1.1 for standard HTTP requests
3. **Advertisement**: Automatically adds `Alt-Svc` headers to HTTP/1.1 responses to advertise HTTP/2 capability

See [PROTOCOL_SWITCHING.md](PROTOCOL_SWITCHING.md) for detailed information about protocol negotiation.

## Testing

```bash
# Test HTTP/1.1
curl -v http://localhost:8000/

# Test HTTP/2 (with prior knowledge)
curl --http2-prior-knowledge http://localhost:8000/
```

## Examples

See `example_auto_protocol.py` for a complete example demonstrating auto-protocol switching.

## Documentation

- [Protocol Switching Guide](PROTOCOL_SWITCHING.md) - Detailed guide on HTTP/2 negotiation and auto-detection
- [Simple Example](simple_main.py) - Basic usage example
- [Auto Protocol Example](example_auto_protocol.py) - Multi-protocol demonstration

## Requirements

- Python 3.10+
- h11 (HTTP/1.1 support)
- h2 (HTTP/2 support)
- asgiref (ASGI typing)
- rfc3986 (URL parsing)

## License

MIT
