import os
import sys
import importlib
from asgiref.wsgi import WsgiToAsgi
from asgiref.typing import ASGI3Application
from asgiref.compatibility import guarantee_single_callable


def load_application(app_spec: str, wsgi: bool = False) -> ASGI3Application:
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

    return guarantee_single_callable(app)
