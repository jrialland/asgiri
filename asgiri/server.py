"""ASGIRI server implementation."""

import asyncio
import logging
import signal
import ssl
from enum import Enum
from pathlib import Path

from asgiref.typing import ASGIApplication

from .proto.auto import AutoProtocol
from .proto.http2 import Http2ServerProtocol
from .proto.http11 import HTTP11ServerProtocol
from .ssl_utils import create_ssl_context
from .asyncio_utils import install_event_loop
from .extensions.tls import TLSExtensionMiddleware

class HttpProtocolVersion(Enum):
    HTTP_1_1 = "http/1.1"
    HTTP_2 = "http/2"
    HTTP_3 = "http/3"
    AUTO = "auto"  # Auto-detect and switch between HTTP/1.1 and HTTP/2


class LifespanPolicy(Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    AUTO = "auto"

class LifespanHandler:

    def __init__(self, app: ASGIApplication, policy: LifespanPolicy):
        self.app = app
        self.policy = policy
        self.queue: asyncio.Queue = asyncio.Queue()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.startup_complete = asyncio.Event()
        self.shutdown_complete = asyncio.Event()
        self.lifespan_task: asyncio.Task | None = None
        self.startup_failed = False
        self.startup_error: str | None = None

    def should_handle_lifespan(self) -> bool:
        match self.policy:
            case LifespanPolicy.ENABLED:
                return True
            case LifespanPolicy.DISABLED:
                return False
            case LifespanPolicy.AUTO:
                # Auto-detect based on whether the app implements lifespan
                return True  # Simplified for this example
            case _:
                return False

    async def startup(self):
        """Start the lifespan handler."""
        if not self.should_handle_lifespan():
            self.logger.info("Lifespan handling disabled")
            self.startup_complete.set()
            return

        self.logger.info("Starting lifespan handler")

        # Create the lifespan task
        self.lifespan_task = asyncio.create_task(
            self.app(
                {"type": "lifespan", "asgi": {"version": "3.0"}},
                self.receive,
                self.send,
            )
        )

        # Send startup event
        await self.queue.put({"type": "lifespan.startup"})

        # Wait for startup to complete (with timeout)
        try:
            await asyncio.wait_for(self.startup_complete.wait(), timeout=10.0)

            # Check if startup failed
            if self.startup_failed:
                self.logger.error(f"Lifespan startup failed: {self.startup_error}")
                if self.lifespan_task:
                    self.lifespan_task.cancel()
                raise RuntimeError(f"Lifespan startup failed: {self.startup_error}")

            self.logger.info("Lifespan startup completed successfully")
        except asyncio.TimeoutError:
            self.logger.error("Lifespan startup timed out")
            if self.lifespan_task:
                self.lifespan_task.cancel()
            raise

    async def shutdown(self):
        """Shutdown the lifespan handler."""
        if not self.should_handle_lifespan() or not self.lifespan_task:
            self.logger.info("Lifespan shutdown skipped (not running)")
            return

        self.logger.info("Shutting down lifespan handler")

        # Send shutdown event
        await self.queue.put({"type": "lifespan.shutdown"})

        # Wait for shutdown to complete (with timeout)
        try:
            await asyncio.wait_for(self.shutdown_complete.wait(), timeout=10.0)
            self.logger.info("Lifespan shutdown completed successfully")
        except asyncio.TimeoutError:
            self.logger.error("Lifespan shutdown timed out")
        finally:
            # Ensure the task is cancelled if it's still running
            if self.lifespan_task and not self.lifespan_task.done():
                self.lifespan_task.cancel()
                try:
                    await self.lifespan_task
                except asyncio.CancelledError:
                    pass

    async def receive(self):
        return await self.queue.get()

    async def send(self, message):
        if message["type"] == "lifespan.startup.complete":
            self.logger.info("Lifespan startup complete")
            self.startup_complete.set()
        elif message["type"] == "lifespan.startup.failed":
            error_msg = message.get("message", "Unknown error")
            self.logger.error(f"Lifespan startup failed: {error_msg}")
            # Store the error and unblock startup
            self.startup_failed = True
            self.startup_error = error_msg
            self.startup_complete.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self.logger.info("Lifespan shutdown complete")
            self.shutdown_complete.set()
        elif message["type"] == "lifespan.shutdown.failed":
            self.logger.error(
                f"Lifespan shutdown failed: {message.get('message', 'Unknown error')}"
            )
            self.shutdown_complete.set()  # Unblock even on failure


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
        lifespan: LifespanPolicy | None = None,
        enable_http3: bool = True,
        http3_port: int | None = None,
    ):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.app = app
        self.host = host
        self.port = port
        self.http_version = http_version or HttpProtocolVersion.AUTO
        self.enable_http3 = enable_http3
        self.http3_port = http3_port or port  # Default to same port as TCP
        self.certfile = certfile
        self.keyfile = keyfile
        self.cert_data = cert_data
        self.key_data = key_data
        self.lifespan_handler = LifespanHandler(app, lifespan or LifespanPolicy.AUTO)

        match self.http_version:
            case HttpProtocolVersion.HTTP_1_1:
                self.protocol_cls: (
                    type[HTTP11ServerProtocol]
                    | type[Http2ServerProtocol]
                    | type[AutoProtocol]
                    | None
                ) = HTTP11ServerProtocol
            case HttpProtocolVersion.HTTP_2:
                self.protocol_cls = Http2ServerProtocol
            case HttpProtocolVersion.HTTP_3:
                # HTTP/3 only mode - will only start QUIC server
                self.protocol_cls = None
                self.enable_http3 = True
            case HttpProtocolVersion.AUTO | None:
                # Default to auto-detection for best compatibility
                self.protocol_cls = AutoProtocol
            case _:
                raise NotImplementedError(
                    f"Protocol version '{self.http_version}' not implemented yet"
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
            self.app = TLSExtensionMiddleware(self.app, ssl_context = self.ssl_context)
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

        # Setup signal handlers for graceful shutdown
        shutdown_event = asyncio.Event()

        def signal_handler(sig, frame):
            self.logger.info(f"Received signal {sig}, shutting down...")
            shutdown_event.set()

        # Register signal handlers (works on both Unix and Windows)
        # Only register if we're in the main thread
        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except ValueError:
            # signal only works in main thread - this is expected in tests
            self.logger.debug("Signal handlers not registered (not in main thread)")

        try:
            # Start lifespan
            await self.lifespan_handler.startup()

            # Create TCP server (HTTP/1.1 and HTTP/2) unless HTTP/3 only
            tcp_server = None
            if self.protocol_cls is not None:
                tcp_server = await loop.create_server(
                    protocol_factory,
                    self.host,
                    self.port,
                    ssl=self.ssl_context,
                    reuse_address=True,
                    start_serving=True,
                )

                addr = tcp_server.sockets[0].getsockname()
                scheme = "https" if self.ssl_context else "http"
                self.logger.info(f"Serving on {scheme}://{addr[0]}:{addr[1]}")

            # Start HTTP/3 (QUIC) server if enabled
            http3_server = None
            if self.enable_http3 and self.http_version in (
                HttpProtocolVersion.HTTP_3,
                HttpProtocolVersion.AUTO,
            ):
                http3_server = await self._start_http3_server()

            # Wait for shutdown signal
            await shutdown_event.wait()
            self.logger.info("Shutting down server...")

            # Close servers
            if tcp_server:
                tcp_server.close()
                await tcp_server.wait_closed()

            if http3_server:
                http3_server.close()

        finally:
            # Always shutdown lifespan
            await self.lifespan_handler.shutdown()
            self.logger.info("Server shutdown complete")

    async def _start_http3_server(self):
        """Start HTTP/3 (QUIC) server."""
        try:
            from aioquic.asyncio import serve
            from aioquic.quic.configuration import QuicConfiguration

            from .proto.http3 import HTTP3ServerProtocol
        except ImportError:
            self.logger.error(
                "aioquic not installed. HTTP/3 support requires 'pip install aioquic'"
            )
            return None

        # HTTP/3 requires TLS
        if not self.ssl_context and not (self.certfile or self.cert_data):
            self.logger.warning(
                "HTTP/3 requires TLS. Either provide certificates or disable HTTP/3. "
                "Skipping HTTP/3 server startup."
            )
            return None

        # Create QUIC configuration
        configuration = QuicConfiguration(
            alpn_protocols=["h3"],  # HTTP/3
            is_client=False,
            max_datagram_frame_size=65536,
        )

        # Load certificates
        if self.certfile:
            configuration.load_cert_chain(self.certfile, self.keyfile)
        elif self.cert_data:
            # For in-memory certificates, we need to write to temp files
            # This is a limitation of aioquic's configuration
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, suffix=".pem"
            ) as cert_f:
                cert_f.write(self.cert_data)
                cert_path = cert_f.name

            if self.key_data:
                with tempfile.NamedTemporaryFile(
                    mode="wb", delete=False, suffix=".pem"
                ) as key_f:
                    key_f.write(self.key_data)
                    key_path = key_f.name
            else:
                key_path = None

            configuration.load_cert_chain(cert_path, key_path)

        def create_protocol(*args, **kwargs):
            return HTTP3ServerProtocol(
                *args,
                app=self.app,
                server=(self.host or "127.0.0.1", self.http3_port),
                **kwargs,
            )

        # Start QUIC server
        server = await serve(
            self.host or "::",
            self.http3_port,
            configuration=configuration,
            create_protocol=create_protocol,
        )

        self.logger.info(f"HTTP/3 (QUIC) listening on UDP port {self.http3_port}")

        return server
