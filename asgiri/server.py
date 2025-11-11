import asyncio
import logging
import ssl
from enum import Enum
from pathlib import Path

from asgiref.typing import ASGIApplication
from .util import install_event_loop
from .proto.http11 import HTTP11ServerProtocol
from .proto.http2 import Http2ServerProtocol
from .proto.auto import AutoProtocol
from .ssl_utils import create_ssl_context

class HttpProtocolVersion(Enum):
    HTTP_1_1 = "http/1.1"
    HTTP_2 = "http/2"
    HTTP_3 = "http/3"
    AUTO = "auto"  # Auto-detect and switch between HTTP/1.1 and HTTP/2


class Server:

    def __init__(
        self,
        app: ASGIApplication,
        host: str | None = None,
        port: int | None = None,
        http_version: HttpProtocolVersion | None = None,
        certfile: str | Path | None = None,
        keyfile: str | Path | None = None,
        cert_data: bytes | None = None,
        key_data: bytes | None = None,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.app = app
        self.host = host
        self.port = port
        match http_version:
            case HttpProtocolVersion.HTTP_1_1:
                self.protocol_cls = HTTP11ServerProtocol
            case HttpProtocolVersion.HTTP_2:
                self.protocol_cls = Http2ServerProtocol
            case HttpProtocolVersion.AUTO | None:
                # Default to auto-detection for best compatibility
                self.protocol_cls = AutoProtocol
            case _:
                raise NotImplementedError(
                    f"Protocol version '{http_version}' not implemented yet"
                )
        
        # SSL/TLS configuration
        self.ssl_context: ssl.SSLContext | None = None
        if certfile or cert_data:
            self.ssl_context = create_ssl_context(
                certfile=certfile,
                keyfile=keyfile,
                cert_data=cert_data,
                key_data=key_data,
            )
            self.logger.info("SSL context created successfully")

    def run(self):
        """Run the server (blocking). Creates an event loop if necessary."""
        # install an event loop if necessary
        loop = install_event_loop()
        loop.run_until_complete(self.a_run())

    async def a_run(self):
        def protocol_factory():
            return self.protocol_cls(server=(self.host or "", self.port), app=self.app)

        loop = asyncio.get_running_loop()
        server = await loop.create_server(
            protocol_factory, 
            self.host, 
            self.port, 
            ssl=self.ssl_context,
            start_serving=True
        )
        addr = server.sockets[0].getsockname()
        scheme = "https" if self.ssl_context else "http"
        self.logger.info(f"Serving on {scheme}://{addr[0]}:{addr[1]}")
        async with server:
            await server.serve_forever()
