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

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
