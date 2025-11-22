"""
ASGI Middleware for serving the proxied location.


When activated, this middleware starts the proxied location using npm and proxies requests to it :
    - Any request leading to a 404 (i.e not handled by other routes) is proxied to the proxied location.
    - Websocket requests are also proxied to the proxied location.

This allows to serve the proxied location during development without needing to build it and serve static files.
"""

import asyncio
import re
import httpx
import websockets
from typing import cast
from asgiref.typing import (
    ASGI3Application,
    Scope,
    HTTPScope,
    WebSocketScope,
    ASGIReceiveCallable,
    ASGISendCallable,
)
from loguru import logger


class ProxyMiddleware:
    """
    ASGI Middleware for proxying requests to a proxied location.
    When a request matches the given pattern, it is proxied to the proxied location.
    Otherwise, it is passed to the wrapped application.
    the match() method can be overridden to customize the matching logic.
    """

    def __init__(
        self,
        app: ASGI3Application,
        target_url: str,
        pattern: str = ".*",
    ):
        """
        Initialize the middleware.

        Example patterns:
            - Everything: ".*"
            - Only paths starting with /api: "^/api/.*"
            - Everything except /static: "^(?!/static/).*"

        :param app: The ASGI application to wrap.
        :param target_url: The target URL to proxy requests to.
        :param pattern: The regex pattern to match request paths.
        """
        self.app = app
        assert target_url.startswith("http://") or target_url.startswith(
            "https://"
        ), "target_url must start with http:// or https://"
        self.target_url = target_url
        self.is_https = target_url.startswith("https://")
        self.pattern = re.compile(pattern)
        self.http_transport = httpx.AsyncHTTPTransport()

    def match(self, scope: Scope, ws: bool = False) -> str | None:
        """
        Check if the request matches the pattern.
        If it matches, return the target URL to proxy to.
        :param scope: The ASGI scope.
        :param ws: Whether the request is a websocket request.
        :return: The target URL to proxy to, or None if it doesn't match.
        """
        m = self.pattern.match(scope["path"][len(scope.get("root_path", "")) :])
        if m is None:
            return None
        scheme = (
            "wss"
            if ws and self.is_https
            else "ws" if ws else "https" if self.is_https else "http"
        )
        return f"{scheme}://{self.target_url[len('https://' if self.is_https else 'http://'):]}{scope['path']}"

    def _forwarded_headers(self, scope: Scope) -> list[tuple[bytes, bytes]]:
        """
        Generate forwarded headers for the proxied request.
        :param scope: The ASGI scope.
        :return: A list of headers to add to the proxied request.
        """
        headers = []
        has_forwarded = False
        client_host, client_port = scope.get("client", ("", 0))
        for key, value in scope["headers"]:  # type: ignore
            if key[0] == b":":
                # Skip pseudo-headers
                continue
            match key:
                case (
                    b"x-forwarded-for"
                    | b"x-forwarded-proto"
                    | b"x-forwarded-port"
                ):
                    # Skip existing forwarded headers to avoid duplication
                    continue
                case b"forwarded":
                    # Append to existing Forwarded header
                    has_forwarded = True
                    value += b", "
                    value += f'for="{client_host}:{client_port}"; proto={scope.get("scheme", "http")}'.encode()
                    headers.append((key, value))
                case _:
                    headers.append((key, value))

        headers.append(
            (b"x-forwarded-proto", scope.get("scheme", "http").encode())
        )
        headers.append((b"x-real-ip", client_host.encode()))
        headers.append((b"x-forwarded-for", client_host.encode()))
        headers.append((b"x-forwarded-port", str(client_port).encode()))
        headers.append((b"x-forwarded-path", scope["path"].encode()))
        headers.append((b"x-forwarded-uri", scope["raw_path"]))
        headers.append(
            (
                b"via",
                f"HTTP/{scope.get('http_version', '1.1')} ASGIRI_PROXY".encode(),
            )
        )
        if not has_forwarded:
            headers.append(
                (
                    b"forwarded",
                    f"for={client_host}; proto={scope.get('scheme', 'http')}".encode(),
                )
            )
        return headers

    async def websocket_proxy(
        self,
        scope: WebSocketScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Proxy websocket requests to the proxied location.

        :param scope: The ASGI scope.
        :param receive: The ASGI receive callable.
        :param send: The ASGI send callable.
        :return: None
        """
        if (target_url := self.match(scope, ws=True)) is None:
            await self.app(scope, receive, send)
            return

        logger.debug(f"Proxying WebSocket request to {target_url}")

        # extract subprotocols from the headers
        headers = self._forwarded_headers(scope)
        subprotocols = []
        for header, value in headers:  # type: ignore
            if header == b"sec-websocket-protocol":
                subprotocols = [v.strip() for v in value.decode().split(",")]

        # remove headers that websockets.connect will set automatically
        headers = [
            (key, value)
            for key, value in headers
            if key
            not in {
                b"connection",
                b"upgrade",
                b"sec-websocket-key",
                b"sec-websocket-version",
                b"sec-websocket-protocol",
            }
        ]

        # connect to the proxied location websocket server
        async with websockets.connect(
            target_url, subprotocols=subprotocols, extra_headers=headers
        ) as websocket:

            await send(
                {
                    "type": "websocket.accept",
                    "subprotocol": websocket.subprotocol,
                }
            )

            async def forward_to_client():
                """for each message received from the proxied location, send it to the client"""
                while True:
                    msg = await websocket.recv()
                    client_msg: dict = {"type": "websocket.send"}
                    if isinstance(msg, str):
                        client_msg["text"] = msg
                    else:
                        client_msg["bytes"] = msg
                    await send(client_msg)

            async def forward_to_server():
                """for each message received from the client, send it to the proxied location"""
                while True:
                    message = await receive()
                    match message["type"]:
                        case "websocket.receive":
                            if "bytes" in message:
                                await websocket.send(
                                    message["bytes"], text=False
                                )
                            else:
                                await websocket.send(
                                    message.get("text", ""), text=True
                                )
                        case "websocket.close":
                            await websocket.close()
                            return

            await asyncio.gather(forward_to_client(), forward_to_server())

    async def http_proxy(
        self,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """
        Proxy HTTP requests to the proxied location by first letting the wrapped
        application handle the request. If it returns a 404, forward the request to the
        proxied location instead.

        :param scope: The ASGI scope.
        :param receive: The ASGI receive callable.
        :param send: The ASGI send callable.
        :return: None
        """

        if (target_url := self.match(scope, ws=False)) is None:
            await self.app(scope, receive, send)
            return

        logger.debug(f"Proxying HTTP request to {target_url}")

        async with httpx.AsyncClient(transport=self.http_transport) as client:

            # extract method and headers from the scope
            method = scope["method"]
            headers = {
                key.decode(): value.decode()
                for key, value in self._forwarded_headers(scope)
            }

            # read the body from the receive callable
            body = b""
            more_body = True
            while more_body:
                message = await receive()
                if message["type"] == "http.request":
                    body += message.get("body", b"")
                    more_body = message.get("more_body", False)

            # send the request to the proxied location
            response = await client.request(
                method, target_url, headers=headers, content=body
            )

            # send the response back to the client
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": [
                        (key.encode(), value.encode())
                        for key, value in response.headers.items()
                        if key.lower()
                        not in (
                            "transfer-encoding",
                            "content-encoding",
                        )  # httpx handles these
                    ],
                }
            )

            async for chunk in response.aiter_bytes():
                await send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": True,
                    }
                )

            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                }
            )

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        """
        ASGI entry point.
        Dispatch to the appropriate handler based on the scope type.
        :param scope: The ASGI scope.
        :param receive: The ASGI receive callable.
        :param send: The ASGI send callable.
        :return: None
        """
        match scope["type"]:
            case "websocket":
                await self.websocket_proxy(
                    cast(WebSocketScope, scope), receive, send
                )
            case "http":
                await self.http_proxy(cast(HTTPScope, scope), receive, send)
            case _:
                await self.app(scope, receive, send)
