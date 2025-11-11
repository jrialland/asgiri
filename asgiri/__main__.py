"""
Command-line interface for asgiri ASGI server.

Usage:
    python -m asgiri [options] mymodule:app
    asgiri [options] mymodule:app

Examples:
    asgiri --http11 --host=0.0.0.0 --port=8000 mymodule:app
    asgiri --http2 --port=8080 tests.app:app
    asgiri --wsgi mymodule:wsgi_app
"""

import argparse
import importlib
import logging
import sys

from asgiref.typing import ASGIApplication
from asgiref.wsgi import WsgiToAsgi

from .server import HttpProtocolVersion, LifespanPolicy, Server
from .ssl_utils import generate_self_signed_cert


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

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Could not import module '{module_path}': {e}") from e

    try:
        app = getattr(module, attribute_name)
    except AttributeError as e:
        raise AttributeError(
            f"Module '{module_path}' has no attribute '{attribute_name}'"
        ) from e

    if wsgi:
        app = WsgiToAsgi(app)

    return app


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="asgiri",
        description="ASGI HTTP server with HTTP/1.1, HTTP/2, and HTTP/3 support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
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
        "--port", type=int, default=8000, help="Port to bind to (default: 8000)"
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

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, parsed_args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger = logging.getLogger("asgiri.cli")

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
        cert_data, key_data = generate_self_signed_cert(
            hostname=parsed_args.host,
            ip_addresses=[parsed_args.host] if parsed_args.host != "0.0.0.0" else None,
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
        )

        protocol_str = parsed_args.protocol.value
        tls_str = "with TLS" if (parsed_args.selfcert or certfile) else "without TLS"
        logger.info(
            f"Starting server on {parsed_args.host}:{parsed_args.port} "
            f"({protocol_str}, {tls_str}, lifespan: {parsed_args.lifespan_policy})"
        )

        server.run()
        return 0

    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        return 0
    except Exception as e:
        logger.exception(f"Server error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
