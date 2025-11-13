[![build status](https://github.com/jrialland/asgiri/actions/workflows/test.yml/badge.svg)](https://github.com/jrialland/asgiri/actions)
[![coverage](https://coveralls.io/repos/github/jrialland/asgiri/badge.svg?branch=develop)](https://coveralls.io/github/jrialland/asgiri?branch=develop)
[![security: bandit](https://img.shields.io/badge/security-bandit-yellow.svg)](https://github.com/PyCQA/bandit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![pypi](https://badge.fury.io/py/asgiri.svg)](https://badge.fury.io/py/asgiri)
[![Python Versions](https://img.shields.io/pypi/pyversions/asgiri)](https://pypi.org/project/asgiri/)
[![Downloads](https://static.pepy.tech/badge/asgiri)](https://pepy.tech/project/asgiri)
[![License](https://img.shields.io/github/license/jrialland/asgiri)](https://github.com/jrialland/asgiri/blob/main/LICENSE)
[![forks](https://img.shields.io/github/forks/jrialland/asgiri)](https://github.com/jrialland/asgiri)

# Asgiri

A high-performance ASGI server implementation supporting HTTP/1.1, HTTP/2, and HTTP/3 protocols with automatic protocol detection and seamless switching.

> **⚠️ Early Stage Project**  
> Asgiri is a relatively new project and has not been extensively tested in production environments. While we strive for stability and correctness, we recommend thorough testing in your specific use case before deploying to production. Contributions, bug reports, and feedback are highly appreciated as we work toward production readiness.

## Lore

Deep in the hills of Kandy, Sri Lanka, stands the [Asgiri Maha Viharaya](https://en.wikipedia.org/wiki/Asgiri_Maha_Viharaya) — a sacred monastery that has been a beacon of wisdom and preservation for centuries. As the headquarters of the Asgiriya Chapter of Siyam Nikaya, this ancient temple holds the solemn duty of safeguarding one of Buddhism's most precious relics: the sacred tooth relic of Buddha.

Just as the monastery has faithfully served countless pilgrims across the ages, handling their requests with grace and precision, so too does this server aim to serve your web traffic. Like the monks who maintain both ancient traditions (the sacred tooth relic) and adapt to modern times, Asgiri the server bridges the old (HTTP/1.1), the new (HTTP/2), and the cutting edge (HTTP/3), automatically detecting which protocol each client speaks and responding with wisdom.

The name `asgiri` reminds us that good software, like good monasteries, should be:
- **Reliable** — Standing strong through the centuries
- **Adaptable** — Serving all who come, regardless of their background
- **Efficient** — Handling many requests with minimal resources
- **Graceful** — Switching protocols as smoothly as monks switch between prayer and teaching

May your servers run as peacefully as the chants in Asgiri's halls. 🙏



## Features

- **Multi-Protocol Support**: Handles HTTP/1.1, HTTP/2, and HTTP/3 on the same port
- **HTTP/3 (QUIC)**: Modern UDP-based protocol for improved performance over lossy networks
- **Auto-Detection**: Automatically detects client protocol preference
- **Dual Transport**: Runs TCP and UDP servers simultaneously for comprehensive protocol coverage
- **Protocol Advertisement**: Advertises HTTP/2 and HTTP/3 capability via Alt-Svc headers
- **WebSocket Support**: WebSocket protocol over HTTP/1.1 and HTTP/2
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

By default, the server automatically detects whether the client is using HTTP/1.1, HTTP/2, or HTTP/3:

```python
server = Server(app=app, host="127.0.0.1", port=8443, certfile="cert.pem", keyfile="key.pem")
# Automatically handles HTTP/1.1, HTTP/2 (TCP), and HTTP/3 (UDP)
```

### Explicit Protocol Selection

You can force a specific protocol if needed:

```python
from asgiri.server import HttpProtocolVersion

# HTTP/1.1 only
server = Server(app=app, http_version=HttpProtocolVersion.HTTP_1_1)

# HTTP/2 only
server = Server(app=app, http_version=HttpProtocolVersion.HTTP_2)

# HTTP/3 only (requires TLS)
server = Server(
    app=app, 
    http_version=HttpProtocolVersion.HTTP_3,
    certfile="cert.pem",
    keyfile="key.pem"
)

# Auto-detection (explicit)
server = Server(app=app, http_version=HttpProtocolVersion.AUTO)
```

### HTTP/3 Configuration

HTTP/3 requires TLS certificates:

```python
from asgiri.server import Server

server = Server(
    app=app,
    host="0.0.0.0",
    port=8443,
    certfile="cert.pem",
    keyfile="key.pem",
    enable_http3=True,  # Enable HTTP/3 (default: True)
)
```

For development, generate self-signed certificates:

```bash
python generate_cert.py
```

## How It Works

The server uses a smart dual-transport architecture with protocol detection:

### TCP Server (HTTP/1.1 and HTTP/2)

1. **HTTP/2 Prior Knowledge**: Detects the HTTP/2 connection preface (`PRI * HTTP/2.0...`)
2. **HTTP/1.1 Fallback**: Falls back to HTTP/1.1 for standard HTTP requests
3. **Advertisement**: Automatically adds `Alt-Svc` headers to advertise HTTP/2 and HTTP/3 capability

### UDP Server (HTTP/3)

1. **QUIC Protocol**: Handles HTTP/3 over QUIC (UDP-based transport)
2. **TLS Integration**: Built-in TLS 1.3 encryption (mandatory for HTTP/3)
3. **Multiplexing**: True stream independence without head-of-line blocking

For more details, see:
- [Protocol Switching Guide](docs/PROTOCOL_SWITCHING.md)
- [HTTP/3 Implementation](docs/HTTP3_IMPLEMENTATION.md)

## Testing

```bash
# Test HTTP/1.1
curl -v http://localhost:8000/

# Test HTTP/2 (with prior knowledge)
curl --http2-prior-knowledge http://localhost:8000/

# Test HTTP/3 (requires curl with HTTP/3 support)
curl --http3 https://localhost:8443/ -k
```

## Documentation

Want to learn more? Browse our documentation:

- **[Quick Reference](docs/QUICK_REFERENCE.md)** — Essential commands and usage patterns at a glance
- **[CLI Guide](docs/CLI.md)** — Command-line interface options and examples
- **[Protocol Switching](docs/PROTOCOL_SWITCHING.md)** — Deep dive into HTTP/2 negotiation and auto-detection
- **[HTTP/3 Implementation](docs/HTTP3_IMPLEMENTATION.md)** — Complete guide to HTTP/3 support and configuration
- **[Architecture](docs/ARCHITECTURE.md)** — Internal design and implementation details
- **[WebSocket Implementation](WEBSOCKET_IMPLEMENTATION.md)** — WebSocket protocol support and usage

## License

MIT
