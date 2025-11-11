"""
Asyncio utilities.
"""

import asyncio

uvloop_ = None
try:
    import uvloop  # type: ignore

    uvloop_ = uvloop
except ImportError:
    pass


def install_event_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # no event loop, create one
        if uvloop_:
            loop = uvloop_.new_event_loop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop
