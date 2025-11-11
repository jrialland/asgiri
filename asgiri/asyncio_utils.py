"""
Asyncio utilities.
"""

import asyncio

# Install uvloop if available (must be done before creating event loops)
try:
    import uvloop  # type: ignore
    uvloop.install()
except ImportError:
    pass


def install_event_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # no event loop, create one
        # If uvloop was installed above, this will automatically use uvloop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop
