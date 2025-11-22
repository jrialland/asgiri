"""
Asyncio utilities.
"""

import asyncio

try:
    import uvloop  # type: ignore
except ImportError:
    uvloop = None  # type: ignore

# Set uvloop event loop policy if available
if uvloop is not None:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


def install_event_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # no event loop, create one
        # If uvloop was installed above, this will automatically use uvloop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop
