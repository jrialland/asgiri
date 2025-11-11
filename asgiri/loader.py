"""
This module for loading asgi application by name.
"""

from importlib import import_module
from inspect import iscoroutinefunction


def load_asgi_app(app_path: str) -> object:
    """Load an ASGI application from a given import path.

    Args:
        app_path: The import path to the ASGI application, e.g. "myapp.module:app".

    Returns:
        The ASGI application callable.

    Raises:
        ImportError: If the module or application cannot be imported.
        AttributeError: If the application is not found in the module.
    """
    module_path, app_name = app_path.split(":", 1)
    module = import_module(module_path)
    app = getattr(module, app_name)

    # Basic validation to check if it's an ASGI application
    if not callable(app):
        raise TypeError(f"The ASGI application '{app_name}' is not callable.")
    if not (iscoroutinefunction(app) or hasattr(app, "__call__")):
        raise TypeError(f"The ASGI application '{app_name}' is not an async callable.")
    return app
