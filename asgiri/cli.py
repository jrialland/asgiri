"""Command-line interface for asgiri ASGI server."""

import argparse
import os
import sys

from loguru import logger

from .app_loader import load_application
from .reload import Reloader, resolve_reload_dirs
from .server import HttpProtocolVersion, LifespanPolicy, Server
from .ssl_utils import generate_self_signed_cert
from .workers import compute_workers_count, spawn_workers


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="asgiri",
        description="ASGI HTTP server with HTTP/1.1, HTTP/2, and HTTP/3 support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    asgiri --http11 --host=0.0.0.0 --port=8000 mymodule:app
    asgiri --http2 --port=8080 tests.app:app
    asgiri --wsgi mymodule:wsgi_app
""",
    )

    # Protocol selection (mutually exclusive)
    protocol_group = parser.add_mutually_exclusive_group()
    protocol_group.add_argument(
        "--http11",
        action="store_const",
        const=HttpProtocolVersion.HTTP_1_1,
        dest="protocol",
        help="Use only HTTP/1.1 protocol",
    )
    protocol_group.add_argument(
        "--http2",
        action="store_const",
        const=HttpProtocolVersion.AUTO,
        dest="protocol",
        help="Handle both HTTP/1.1 and HTTP/2 protocols (default)",
    )
    protocol_group.add_argument(
        "--http3",
        action="store_const",
        const=HttpProtocolVersion.HTTP_3,
        dest="protocol",
        help="Use HTTP/3 (QUIC) protocol only - requires TLS certificates",
    )

    # Server configuration
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=-1, help="Port to bind to (default: 8000)"
    )
    parser.add_argument(
        "--workers",
        type=str,
        default="1",
        help=(
            "Number of worker processes. "
            "Use 1 for single process (default), "
            "an integer > 1 for multiple workers, "
            "or 'auto' to use CPU count"
        ),
    )

    # TLS/SSL configuration
    tls_group = parser.add_mutually_exclusive_group()
    tls_group.add_argument(
        "--selfcert",
        action="store_true",
        help="Generate and use a self-signed certificate for HTTPS",
    )
    parser.add_argument(
        "--cert", type=str, help="Path to SSL certificate file (PEM format)"
    )
    parser.add_argument(
        "--key", type=str, help="Path to SSL private key file (PEM format)"
    )

    # Application type
    parser.add_argument(
        "--wsgi",
        action="store_true",
        help="Treat application as WSGI (will be wrapped with asgiref.wsgi)",
    )

    # Lifespan policy
    parser.add_argument(
        "--lifespan-policy",
        type=str,
        choices=["enabled", "disabled", "auto"],
        default="auto",
        help="Lifespan event handling policy (default: auto)",
    )

    # Logging
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set logging level (default: INFO)",
    )

    # WebSocket ping/pong keep-alive
    parser.add_argument(
        "--ws-ping-interval",
        type=float,
        default=20.0,
        help="WebSocket ping interval in seconds (0 to disable, default: 20.0)",
    )
    parser.add_argument(
        "--ws-ping-timeout",
        type=float,
        default=20.0,
        help="WebSocket pong timeout in seconds (default: 20.0)",
    )

    # Hot reload options (development only)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable hot-reload mode in development (single-process only)",
    )
    parser.add_argument(
        "--reload-dir",
        action="append",
        dest="reload_dirs",
        help="Directory or file path to watch for changes (repeatable). "
        "Defaults to the app module's directory.",
    )
    parser.add_argument(
        "--reload-ignore",
        action="append",
        dest="reload_ignore_patterns",
        help="Extra ignore pattern for reload watcher (repeatable)",
    )
    parser.add_argument(
        "--reload-delay-ms",
        type=int,
        default=200,
        help="Reload debounce delay in milliseconds (default: 200)",
    )

    # Application specification
    parser.add_argument(
        "application",
        type=str,
        help='ASGI application in format "module.path:attribute"',
    )

    # Default protocol to AUTO (http2 flag)
    parser.set_defaults(protocol=HttpProtocolVersion.AUTO)

    return parser


class CliConfiguration:
    """Holds the configuration parsed from CLI arguments."""

    def __init__(
        self,
        host: str,
        port: int,
        protocol: HttpProtocolVersion,
        workers: str,
        selfcert: bool,
        cert: str | None,
        key: str | None,
        wsgi: bool,
        lifespan_policy: LifespanPolicy,
        log_level: str,
        application: str,
        ws_ping_interval: float,
        ws_ping_timeout: float,
        reload: bool,
        reload_dirs: list[str] | None,
        reload_ignore_patterns: list[str] | None,
        reload_delay_ms: int,
    ):

        self.host = host
        self.port = port
        self.protocol = protocol
        self.workers = workers
        self.selfcert = selfcert
        self.cert = cert
        self.key = key
        self.wsgi = wsgi
        self.lifespan_policy = lifespan_policy
        self.log_level = log_level
        self.application = application
        self.ws_ping_interval = ws_ping_interval
        self.ws_ping_timeout = ws_ping_timeout
        self.reload = reload
        self.reload_dirs = reload_dirs
        self.reload_ignore_patterns = reload_ignore_patterns
        self.reload_delay_ms = reload_delay_ms


def parse_args(args: list[str] | None = None) -> CliConfiguration:
    """Parse command-line arguments."""
    parser = create_parser()
    parsed_args = parser.parse_args(args)
    config = CliConfiguration(
        host=parsed_args.host,
        port=parsed_args.port,
        protocol=parsed_args.protocol,
        workers=parsed_args.workers,
        selfcert=parsed_args.selfcert,
        cert=parsed_args.cert,
        key=parsed_args.key,
        wsgi=parsed_args.wsgi,
        lifespan_policy=parsed_args.lifespan_policy,
        log_level=parsed_args.log_level,
        application=parsed_args.application,
        ws_ping_interval=parsed_args.ws_ping_interval,
        ws_ping_timeout=parsed_args.ws_ping_timeout,
        reload=parsed_args.reload,
        reload_dirs=parsed_args.reload_dirs,
        reload_ignore_patterns=parsed_args.reload_ignore_patterns,
        reload_delay_ms=parsed_args.reload_delay_ms,
    )

    is_tls = (
        config.protocol == HttpProtocolVersion.HTTP_3
        or config.cert is not None
        or config.key is not None
        or config.selfcert
    )
    # default values for http port
    if config.port == -1:
        config.port = 8443 if is_tls else 8000

    if config.reload and parsed_args.workers != "1":
        logger.error("Cannot use --reload with --workers > 1")
        sys.exit(1)

    # The --workers argument does not work for Windows
    if os.name == "nt":
        if config.workers != "1":
            logger.warning(
                "The --workers option is not supported on Windows. "
                "Defaulting to 1 worker."
            )
            config.workers = "1"

    return config


def _prepare_server(CliConfig: CliConfiguration):
    """Load app, configure TLS, and return (create_server_func, run_server_func)."""
    # Configure logging
    logger.remove()  # Remove default handler
    logger.add(
        sys.stderr,
        level=CliConfig.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    )

    # Load the application
    try:
        app = load_application(CliConfig.application, wsgi=CliConfig.wsgi)
        logger.info(f"Loaded application: {CliConfig.application}")
    except (ValueError, ImportError, AttributeError) as e:
        logger.error(f"Failed to load application: {e}")
        return 1

    # Handle TLS/SSL configuration
    cert_data = None
    key_data = None
    certfile = None
    keyfile = None

    if CliConfig.selfcert:
        logger.info("Generating self-signed certificate...")
        # Intentional bind to all interfaces when using 0.0.0.0
        ip_addrs = (
            [CliConfig.host]
            if CliConfig.host != "0.0.0.0"  # nosec B104
            else None
        )
        cert_data, key_data = generate_self_signed_cert(
            hostname=CliConfig.host,
            ip_addresses=ip_addrs,
        )
        logger.info("Self-signed certificate generated")
    elif CliConfig.cert or CliConfig.key:
        if not (CliConfig.cert and CliConfig.key):
            logger.error("Both --cert and --key must be provided together")
            return 1
        certfile = CliConfig.cert
        keyfile = CliConfig.key
        logger.info(f"Using certificate: {certfile} and key: {keyfile}")

    # Convert lifespan policy string to enum
    lifespan_policy_map: dict[str, LifespanPolicy] = {
        "enabled": LifespanPolicy.ENABLED,
        "disabled": LifespanPolicy.DISABLED,
        "auto": LifespanPolicy.AUTO,
    }
    lifespan_policy = lifespan_policy_map[str(CliConfig.lifespan_policy)]

    # Determine number of workers
    try:
        num_workers = compute_workers_count(CliConfig.workers)
    except ValueError as e:
        logger.error(f"Invalid --workers value: {e}")
        return 1

    def create_server() -> Server:
        """Create a server instance (without running it)."""
        # A --ws-ping-interval of 0 means "disable pings"
        ws_ping_interval = (
            CliConfig.ws_ping_interval
            if CliConfig.ws_ping_interval > 0
            else None
        )

        return Server(
            app=app,
            host=CliConfig.host,
            port=CliConfig.port,
            http_version=CliConfig.protocol,
            certfile=certfile,
            keyfile=keyfile,
            cert_data=cert_data,
            key_data=key_data,
            lifespan=lifespan_policy,
            reuse_port=(num_workers > 1),
            ws_ping_interval=ws_ping_interval,
            ws_ping_timeout=CliConfig.ws_ping_timeout,
            reload=CliConfig.reload,
            reload_dirs=CliConfig.reload_dirs,
            reload_delay_ms=CliConfig.reload_delay_ms,
            reload_ignore_patterns=CliConfig.reload_ignore_patterns,
        )

    def create_and_run_server() -> Server:
        """Create and run a server instance (used by workers)."""
        server = create_server()
        try:
            server.run()
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except Exception as e:
            logger.exception(f"Server error: {e}")
            raise
        return server

    protocol_str = CliConfig.protocol.value
    tls_str = "with TLS" if (CliConfig.selfcert or certfile) else "without TLS"

    if num_workers == 1:
        logger.info(
            f"Starting server on {CliConfig.host}:{CliConfig.port} "
            f"({protocol_str}, {tls_str}, lifespan: {CliConfig.lifespan_policy})"
        )
    else:
        logger.info(
            f"Starting server with {num_workers} workers on {CliConfig.host}:{CliConfig.port} "
            f"({protocol_str}, {tls_str}, lifespan: {CliConfig.lifespan_policy})"
        )

    return create_server, create_and_run_server


def worker_process(CliConfig: CliConfiguration) -> int:
    """Function to run in each worker process."""
    result = _prepare_server(CliConfig)
    if isinstance(result, int):
        return result
    _, create_and_run_server = result
    spawn_workers(CliConfig.workers, create_and_run_server)
    return 0


def reloadable_worker_process(CliConfig: CliConfiguration, conn) -> int:
    """Worker process that serves and listens for reload/shutdown commands."""
    import threading as _threading

    result = _prepare_server(CliConfig)
    if isinstance(result, int):
        return result
    create_server, create_and_run_server = result

    server = create_server()

    def pipe_listener():
        try:
            message = conn.recv()
            if message in ("reload", "shutdown"):
                logger.info(f"{message.capitalize()} requested by supervisor")
                server.request_shutdown()
        except EOFError:
            pass
        except Exception as e:
            logger.debug(f"Pipe listener error: {e}")

    listener = _threading.Thread(target=pipe_listener, daemon=True)
    listener.start()

    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("Server interrupted by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        raise
    return 0


def run_with_reloader(config: CliConfiguration) -> int:
    """Run the server under the hot-reload supervisor."""
    try:
        import watchfiles  # noqa: F401
    except ImportError:
        logger.error(
            "Hot reload requires the 'watchfiles' package. "
            "Install it with: pip install asgiri[reload]"
        )
        return 1

    watch_dirs = resolve_reload_dirs(
        config.application,
        config.reload_dirs,
    )
    logger.info(f"Watching directories for reload: {watch_dirs}")

    reloader = Reloader(
        watch_dirs=watch_dirs,
        debounce_ms=config.reload_delay_ms,
        ignore_patterns=config.reload_ignore_patterns,
        max_restarts=3,
        shutdown_timeout=30.0,
    )
    return reloader.run(reloadable_worker_process, (config,))


def main(args: list[str] | None = None) -> int:
    """Main entry point for the asgiri CLI."""
    # Parse command-line arguments
    config: CliConfiguration = parse_args(args)
    if config.reload:
        return run_with_reloader(config)
    spawn_workers(config.workers, worker_process, [config])
    return 0
