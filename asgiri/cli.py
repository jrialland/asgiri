"""Command-line interface for asgiri ASGI server."""

import argparse
import importlib
import os
import sys

from asgiref.typing import ASGIApplication
from asgiref.wsgi import WsgiToAsgi
from loguru import logger

from .server import HttpProtocolVersion, LifespanPolicy, Server
from .ssl_utils import generate_self_signed_cert
from .workers import get_worker_count, spawn_workers


def load_application(app_spec: str, wsgi: bool = False) -> ASGIApplication:
    """
    Load an ASGI (or WSGI) application from a module:attribute specification.

    Args:
        app_spec: String in the format "module.path:attribute"
        wsgi: If True, wrap the application using asgiref.wsgi

    Returns:
        The loaded ASGI application

    Raises:
        ValueError: If the app_spec format is invalid
        ImportError: If the module cannot be imported
        AttributeError: If the attribute doesn't exist in the module
    """
    if ":" not in app_spec:
        raise ValueError(
            f"Invalid application specification '{app_spec}'. "
            "Expected format: 'module.path:attribute'"
        )

    module_path, attribute_name = app_spec.rsplit(":", 1)

    # Add current directory to sys.path to allow importing local modules
    # When running as an installed command, sys.path[0] is the executable path,
    # not the current directory, so we need to explicitly add it.
    cwd = os.getcwd()

    # Remove cwd if it's already in sys.path (to re-insert at position 0)
    if cwd in sys.path:
        sys.path.remove(cwd)
    sys.path.insert(0, cwd)

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Could not import module '{module_path}': {e}"
        ) from e

    try:
        app = getattr(module, attribute_name)
    except AttributeError as e:
        raise AttributeError(
            f"Module '{module_path}' has no attribute '{attribute_name}'"
        ) from e

    if wsgi:
        app = WsgiToAsgi(app)

    return app


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

    # Application specification
    parser.add_argument(
        "application",
        type=str,
        help='ASGI application in format "module.path:attribute"',
    )

    # Default protocol to AUTO (http2 flag)
    parser.set_defaults(protocol=HttpProtocolVersion.AUTO)

    return parser


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = create_parser()
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """
    Main entry point for the CLI.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    try:
        parsed_args = parse_args(args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1

    # Set default port to 8443 when TLS is enabled and port not specified
    if parsed_args.port == -1:
        if parsed_args.selfcert or parsed_args.cert:
            parsed_args.port = 8443
        else:
            # Default to port 8000 if not specified and TLS is not enabled
            parsed_args.port = 8000

    # Configure logging
    # Configure loguru logger
    logger.remove()  # Remove default handler
    logger.add(
        sys.stderr,
        level=parsed_args.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # Load the application
    try:
        app = load_application(parsed_args.application, wsgi=parsed_args.wsgi)
        logger.info(f"Loaded application: {parsed_args.application}")
    except (ValueError, ImportError, AttributeError) as e:
        logger.error(f"Failed to load application: {e}")
        return 1

    # Handle TLS/SSL configuration
    cert_data = None
    key_data = None
    certfile = None
    keyfile = None

    if parsed_args.selfcert:
        # Generate self-signed certificate
        logger.info("Generating self-signed certificate...")
        # Intentional bind to all interfaces when using 0.0.0.0
        ip_addrs = (
            [parsed_args.host]
            if parsed_args.host != "0.0.0.0"  # nosec B104
            else None
        )
        cert_data, key_data = generate_self_signed_cert(
            hostname=parsed_args.host,
            ip_addresses=ip_addrs,
        )
        logger.info("Self-signed certificate generated")
    elif parsed_args.cert or parsed_args.key:
        # Validate that both cert and key are provided
        if not (parsed_args.cert and parsed_args.key):
            logger.error("Both --cert and --key must be provided together")
            return 1
        certfile = parsed_args.cert
        keyfile = parsed_args.key
        logger.info(f"Using certificate: {certfile} and key: {keyfile}")

    # Create and run server
    try:
        # Convert lifespan policy string to enum
        lifespan_policy_map = {
            "enabled": LifespanPolicy.ENABLED,
            "disabled": LifespanPolicy.DISABLED,
            "auto": LifespanPolicy.AUTO,
        }
        lifespan_policy = lifespan_policy_map[parsed_args.lifespan_policy]

        # Determine number of workers
        try:
            num_workers = get_worker_count(parsed_args.workers)
        except ValueError as e:
            logger.error(f"Invalid --workers value: {e}")
            return 1

        # Create server factory function for workers
        def create_and_run_server():
            """Create and run a server instance (used by workers)."""
            server = Server(
                app=app,
                host=parsed_args.host,
                port=parsed_args.port,
                http_version=parsed_args.protocol,
                certfile=certfile,
                keyfile=keyfile,
                cert_data=cert_data,
                key_data=key_data,
                lifespan=lifespan_policy,
                reuse_port=(
                    num_workers > 1
                ),  # Enable SO_REUSEPORT for multiprocessing
            )
            try:
                server.run()
            except KeyboardInterrupt:
                logger.info("Server interrupted by user")
            except Exception as e:
                logger.exception(f"Server error: {e}")
                raise

        protocol_str = parsed_args.protocol.value
        tls_str = (
            "with TLS" if (parsed_args.selfcert or certfile) else "without TLS"
        )

        if num_workers == 1:
            logger.info(
                f"Starting server on {parsed_args.host}:{parsed_args.port} "
                f"({protocol_str}, {tls_str}, lifespan: {parsed_args.lifespan_policy})"
            )
        else:
            logger.info(
                f"Starting server with {num_workers} workers on {parsed_args.host}:{parsed_args.port} "
                f"({protocol_str}, {tls_str}, lifespan: {parsed_args.lifespan_policy})"
            )

        # Run with workers
        spawn_workers(num_workers, create_and_run_server)
        return 0

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Server error: {e}")
        return 1
