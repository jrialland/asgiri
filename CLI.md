# Commandline usage

# Example

Http 1.1 only
```
    asgiri --http11 --host=0.0.0.0 --port=8000 mymodule:app
```


Http/2 with self-signed certificate
```
asgiri --host=0.0.0.0 --port 8443 --selfcert mymodule:app
```

with real private key : 
```
asgiri --host=0.0.0.0 --port 443 --key=myserver.pem --cert=myserver.crt mymodule:app
```

# Options:

## Protocol Options
 * --http11 : only http 1.1
 * --http2 : handle both http 1.1 and and http/2 protocols (default option)
 * --http3 : (WIP) also listen to udp connections on the given port  (not implemented yet)

## Server Configuration
 * --host=<ip4 or ip6> : serve on this interface (default: 127.0.0.1)
 * --port=<port> : serve on this port (default: 8000)

## TLS/SSL Options
 * --selfcert : generate and use a self-signed certificate for HTTPS (ideal for development/testing)
 * --cert=<path> : path to SSL certificate file (PEM format) - must be used with --key
 * --key=<path> : path to SSL private key file (PEM format) - must be used with --cert

## Application Type
 * --wsgi : consider that the last argument is an old-fashioned wgsi application. in this case an adapter from `asgiref.wsgi` is used.

## Other Options
 * --log-level={DEBUG,INFO,WARNING,ERROR,CRITICAL} : set logging level (default: INFO)

*mymodule:app => name of the variable that corresponds to the asgi application to run

# Generating Certificates

To generate and save a self-signed certificate for reuse:
```python
python generate_cert.py
```

This will create `server.crt` and `server.key` files that you can use with the `--cert` and `--key` options.

