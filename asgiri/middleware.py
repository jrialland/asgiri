"""Utility middleware for adding protocol information to responses."""

from typing import Any


def protocol_info_middleware(app):
    """ASGI middleware that adds X-Protocol header to responses.
    
    This middleware adds an X-Protocol header to all responses indicating
    which HTTP protocol version was used for the request.
    
    Args:
        app: The ASGI application to wrap.
        
    Returns:
        A wrapped ASGI application.
        
    Example:
        ```python
        from asgiri.middleware import protocol_info_middleware
        from asgiri.server import Server
        
        app = protocol_info_middleware(my_asgi_app)
        server = Server(app=app, host="127.0.0.1", port=8000)
        server.run()
        ```
        
        Response will include:
        ```
        X-Protocol: HTTP/1.1
        ```
        or
        ```
        X-Protocol: HTTP/2
        ```
    """
    async def wrapped_app(scope, receive, send):
        """Wrapped ASGI app that adds protocol info header."""
        if scope["type"] != "http":
            # Not an HTTP request, pass through unchanged
            await app(scope, receive, send)
            return
            
        http_version = scope.get("http_version", "unknown")
        protocol_name = f"HTTP/{http_version}"
        
        async def wrapped_send(message: dict[str, Any]):
            """Send wrapper that adds X-Protocol header."""
            if message["type"] == "http.response.start":
                # Add X-Protocol header
                headers = list(message.get("headers", []))
                headers.append((b"x-protocol", protocol_name.encode()))
                
                # Create modified message with updated headers
                message = dict(message)
                message["headers"] = headers
            
            await send(message)
        
        await app(scope, receive, wrapped_send)
    
    return wrapped_app


def cors_middleware(app, allowed_origins="*"):
    """Simple CORS middleware for development.
    
    Adds CORS headers to allow cross-origin requests.
    
    Args:
        app: The ASGI application to wrap.
        allowed_origins: Allowed origins (default: "*" for all).
        
    Returns:
        A wrapped ASGI application.
    """
    async def wrapped_app(scope, receive, send):
        """Wrapped ASGI app that adds CORS headers."""
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        
        async def wrapped_send(message: dict[str, Any]):
            """Send wrapper that adds CORS headers."""
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                
                # Add CORS headers
                headers.append((b"access-control-allow-origin", allowed_origins.encode()))
                headers.append((b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS"))
                headers.append((b"access-control-allow-headers", b"*"))
                
                message = dict(message)
                message["headers"] = headers
            
            await send(message)
        
        # Handle OPTIONS preflight requests
        if scope["method"] == "OPTIONS":
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"access-control-allow-origin", allowed_origins.encode()),
                    (b"access-control-allow-methods", b"GET, POST, PUT, DELETE, OPTIONS"),
                    (b"access-control-allow-headers", b"*"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": b"",
            })
        else:
            await app(scope, receive, wrapped_send)
    
    return wrapped_app
