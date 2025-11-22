"""Command-line interface for asgiri ASGI server."""

import argparse
import os
import sys


from loguru import logger
from .server import HttpProtocolVersion, LifespanPolicy, Server
from .ssl_utils import generate_self_signed_cert
from .workers import spawn_workers
from .app_loader import load_application

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

    # Handle self-signed certificate generation
    ... # TODO: Implement self-signed cert generation if needed

    # The --workers argument does not work for Windows
    if os.name == "nt":
        if config.workers != "1":
            logger.warning(
                "The --workers option is not supported on Windows. "
                "Defaulting to 1 worker."
            )
            config.workers = "1"

    return config

def worker_process(CliConfig: CliConfiguration) -> None:
    """Function to run in each worker process."""

    
    # Configure logging
    # Configure loguru logger
    logger.remove()  # Remove default handler
    logger.add(
        sys.stderr,
        level=CliConfig.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # Load the application
    app = load_application(CliConfig.application, wsgi=CliConfig.wsgi)

    lifespan_policy_map = {
        "enabled": LifespanPolicy.ENABLED,
        "disabled": LifespanPolicy.DISABLED,
        "auto": LifespanPolicy.AUTO,
    }
    lifespan_policy = lifespan_policy_map[CliConfig.lifespan_policy]

    def is_multiprocessing_worker() -> bool:
        import multiprocessing
        return multiprocessing.current_process().name != "MainProcess"

    server = Server(
        app=app,
        host=CliConfig.host,
        port=CliConfig.port,
        http_version=CliConfig.protocol,
        certfile=CliConfig.cert,
        keyfile=CliConfig.key,
        lifespan=lifespan_policy,
        reuse_port=is_multiprocessing_worker(),
    )

    server.run()

def main(args: list[str] | None = None) -> int:
    """Main entry point for the asgiri CLI."""
    # Parse command-line arguments
    config: CliConfiguration = parse_args(args)
    spawn_workers(config.workers, worker_process, [config])
    return 0