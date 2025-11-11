# Asgiri

A high-performance ASGI server implementation supporting both HTTP/1.1 and HTTP/2 protocols with automatic protocol detection.

## Lore

Deep in the hills of Kandy, Sri Lanka, stands the [Asgiri Maha Viharaya](https://en.wikipedia.org/wiki/Asgiri_Maha_Viharaya) — a sacred monastery that has been a beacon of wisdom and preservation for centuries. As the headquarters of the Asgiriya Chapter of Siyam Nikaya, this ancient temple holds the solemn duty of safeguarding one of Buddhism's most precious relics: the sacred tooth relic of Buddha.

Just as the monastery has faithfully served countless pilgrims across the ages, handling their requests with grace and precision, so too does this server aim to serve your web traffic. Like the monks who maintain both ancient traditions (the sacred tooth relic) and adapt to modern times, Asgiri the server bridges the old (HTTP/1.1) and the new (HTTP/2, QUIC), automatically detecting which protocol each client speaks and responding with wisdom.

The name `asgiri` reminds us that good software, like good monasteries, should be:
- **Reliable** — Standing strong through the centuries
- **Adaptable** — Serving all who come, regardless of their background
- **Efficient** — Handling many requests with minimal resources
- **Graceful** — Switching protocols as smoothly as monks switch between prayer and teaching

May your servers run as peacefully as the chants in Asgiri's halls. 🙏



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

For more details, see the [Protocol Switching Guide](docs/PROTOCOL_SWITCHING.md).

## Testing

```bash
# Test HTTP/1.1
curl -v http://localhost:8000/

# Test HTTP/2 (with prior knowledge)
curl --http2-prior-knowledge http://localhost:8000/
```

## Documentation

Want to learn more? Browse our documentation:

- **[Quick Reference](docs/QUICK_REFERENCE.md)** — Essential commands and usage patterns at a glance
- **[CLI Guide](docs/CLI.md)** — Command-line interface options and examples
- **[Protocol Switching](docs/PROTOCOL_SWITCHING.md)** — Deep dive into HTTP/2 negotiation and auto-detection
- **[Architecture](docs/ARCHITECTURE.md)** — Internal design and implementation details
- **[WebSocket Implementation](WEBSOCKET_IMPLEMENTATION.md)** — WebSocket protocol support and usage

## License

MIT
