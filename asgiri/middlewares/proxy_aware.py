"""
ASGI middleware to make the application proxy-aware by adjusting request headers.

This middleware rewrites the ASGI scope to reflect the original client information
as provided by trusted proxy headers (e.g., X-Forwarded-For, Forwarded, etc.).
It should only be used behind a trusted proxy, never when directly exposed to the internet.

If no expected proxy headers are present, the middleware rejects the request with 400 Bad Request.
"""

from typing import cast
from asgiref.typing import (
    ASGI3Application,
    Scope,
    HTTPScope,
    WebSocketScope,
    ASGIReceiveCallable,
    ASGISendCallable,
)

class ProxyAwareMiddleware:
    """
    ASGI middleware to adjust the ASGI scope based on trusted proxy headers.
    Ensures the app sees the original client info, not the proxy's.
    Rejects requests missing proxy headers with 400 Bad Request.
    """

    def __init__(self, app: ASGI3Application) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        """Entrypoint for the middleware."""
        if scope["type"] == "http":
            await self._handle_proxy_scope(
                cast(HTTPScope, scope), receive, send
            )
        elif scope["type"] == "websocket":
            await self._handle_proxy_scope(
                cast(WebSocketScope, scope), receive, send
            )
        else:
            await self.app(scope, receive, send)

    async def _handle_proxy_scope(
        self,
        scope: HTTPScope | WebSocketScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Adjust the ASGI scope based on proxy headers. Reject if none found.
        """
        has_proxy_header = False
        filtered_headers: list[tuple[bytes, bytes]] = []
        client_host, client_port = scope.get("client", ("", 0))
        scheme = scope.get("scheme", "http")
        server_host, server_port = scope.get("server", ("", 0))

        for key, value in scope["headers"]:
            key_lower = key.lower()
            val_str = value.decode()
            if key_lower == b"x-real-ip":
                has_proxy_header = True
                client_host = val_str
            elif key_lower == b"x-forwarded-for":
                has_proxy_header = True
                client_host, port = self._parse_x_forwarded_for(val_str)
                if port:
                    client_port = port
            elif key_lower == b"x-forwarded-proto":
                has_proxy_header = True
                if val_str:
                    scheme = val_str
            elif key_lower == b"x-forwarded-host":
                has_proxy_header = True
                server_host = val_str
            elif key_lower == b"x-forwarded-port":
                has_proxy_header = True
                try:
                    server_port = int(val_str)
                except ValueError:
                    server_port = 0
            elif key_lower == b"x-forwarded-path":
                has_proxy_header = True
                scope["root_path"] = val_str
            elif key_lower == b"forwarded":
                has_proxy_header = True
                (client_host, port), proto = self._parse_forwarded(val_str)
                if port:
                    client_port = port
                if proto:
                    scheme = proto
            else:
                filtered_headers.append((key, value))

        if not has_proxy_header:
            await self._respond_with_400(send)
            return

        scope["client"] = (client_host, client_port)
        scope["scheme"] = scheme
        scope["server"] = (server_host, server_port)
        scope["headers"] = filtered_headers
        await self.app(scope, receive, send)

    @staticmethod
    async def _respond_with_400(send: ASGISendCallable) -> None:
        """Send a 400 Bad Request response."""
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": f"❌ 400 Bad Request: expected proxy headers are missing.".encode("utf-8"),
            }
        )

    @staticmethod
    def _parse_x_forwarded_for(value: str) -> tuple[str, int]:
        """
        Parse the X-Forwarded-For header to extract the client IP and optional port.
        Takes the first IP in the list, strips quotes, and parses port if present.
        """
        first = value.split(",")[0].strip().strip('"')
        if ":" in first:
            ip, port_str = first.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                port = 0
        else:
            ip = first
            port = 0
        return ip, port

    @staticmethod
    def _parse_forwarded(value: str) -> tuple[tuple[str, int],str]:
        """
        Parse the Forwarded header to extract client IP/port and protocol.
        Only the last Forwarded entry is used.
        Args:
            value: The value of the Forwarded header.
        Returns:
            A tuple containing:
                - A tuple with the client IP and port. (for=)
                - The protocol string. (proto=)
        """
        client_ip = ""
        client_port = 0
        proto = ""
        # Take the last Forwarded entry
        parts = value.split(",")[-1].strip().split(";")
        for part in parts:
            part = part.strip().lower()
            if part.startswith("for="):
                client_ip, client_port = (
                    ProxyAwareMiddleware._parse_x_forwarded_for(
                        part[4:].strip()
                    )
                )
            elif part.startswith("proto="):
                proto = part[6:].strip()
        return (client_ip, client_port), proto
