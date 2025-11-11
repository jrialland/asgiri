# Commandline usage

# Examples

## Basic Usage

Http 1.1 only
```bash
asgiri --http11 --host=0.0.0.0 --port=8000 mymodule:app
```

Http/2 with self-signed certificate
```bash
asgiri --host=0.0.0.0 --port 8443 --selfcert mymodule:app
```

With real private key:
```bash
asgiri --host=0.0.0.0 --port 443 --key=myserver.pem --cert=myserver.crt mymodule:app
```

## Lifespan Control

Disable lifespan events (for apps that don't support it):
```bash
asgiri --lifespan-policy disabled mymodule:app
```

Force lifespan events (will fail if app doesn't support it):
```bash
asgiri --lifespan-policy enabled mymodule:app
```

Auto-detect lifespan support (default):
```bash
asgiri --lifespan-policy auto mymodule:app
# or simply:
asgiri mymodule:app
```

# Options:

## Protocol Options
 * `--http11` : only http 1.1
 * `--http2` : handle both http 1.1 and and http/2 protocols (default option)
 * `--http3` : use HTTP/3 (QUIC) protocol only - requires TLS certificates (use with --selfcert, --cert/--key)

## Server Configuration
 * `--host=<ip4 or ip6>` : serve on this interface (default: 127.0.0.1)
 * `--port=<port>` : serve on this port (default: 8000)

## TLS/SSL Options
 * `--selfcert` : generate and use a self-signed certificate for HTTPS (ideal for development/testing)
 * `--cert=<path>` : path to SSL certificate file (PEM format) - must be used with --key
 * `--key=<path>` : path to SSL private key file (PEM format) - must be used with --cert

## Application Type
 * `--wsgi` : consider that the last argument is an old-fashioned WSGI application. In this case an adapter from `asgiref.wsgi` is used.

## Lifespan Options
 * `--lifespan-policy={enabled,disabled,auto}` : control ASGI lifespan event handling (default: auto)
   - `enabled` : Force lifespan events. Server will fail to start if the app doesn't support lifespan.
   - `disabled` : Disable lifespan events entirely. Use this for apps that don't implement the lifespan protocol.
   - `auto` : Automatically detect and handle lifespan if the app supports it (recommended).

## Other Options
 * `--log-level={DEBUG,INFO,WARNING,ERROR,CRITICAL}` : set logging level (default: INFO)

**Note:** `mymodule:app` refers to the name of the variable that corresponds to the ASGI application to run

# Generating Certificates

To generate and save a self-signed certificate for reuse:
```bash
python generate_cert.py
```

This will create `server.crt` and `server.key` files that you can use with the `--cert` and `--key` options.

