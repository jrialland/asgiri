"""Utility middlewares that are used by the server"""

from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGISendCallable,
    Scope,
)


class HeadersInjectingMiddleware:
    """
    Middleware that injects custom headers in http responses
    """

    def __init__(self, app: ASGI3Application, headers: dict[str, str]) -> None:
        self.app = app
        self.headers_keys = set(
            key.lower().encode("latin-1") for key in headers.keys()
        )
        self.headers_list = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in headers.items()
        ]

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(
            message: dict,  # type: ignore[type-arg]
        ) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key not in self.headers_keys
                ] + self.headers_list
            await send(message)  # type: ignore[arg-type]

        await self.app(scope, receive, send_wrapper)  # type: ignore[arg-type]


def wrap_with_advertisements(
    app: ASGI3Application,
    server: tuple[str, int],
    advertise_http2: bool = True,
    advertise_http3: bool = False,
) -> ASGI3Application:
    """
    Wrap the given ASGI app with headers advertising HTTP/2 and/or
      HTTP/3 support.

    Args:
        app: The original ASGI application.
        server: A tuple containing the server's host and port.
        advertise_http2: Whether to advertise HTTP/2 support.
        advertise_http3: Whether to advertise HTTP/3 support.

    Returns:
        The wrapped ASGI application with the appropriate headers injected.
    """
    alt_svc = []
    if advertise_http2:
        alt_svc.append(f'h2c=":{server[1]}"')
    if advertise_http3:
        alt_svc.append(f'h3=":{server[1]}"; ma=86400')

    if advertise_http3 or advertise_http2:
        return HeadersInjectingMiddleware(
            app,
            headers={"Alt-Svc": ", ".join(part for part in alt_svc)},
        )
    else:
        return app
