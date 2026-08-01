import asyncio
from unittest.mock import Mock

import pytest

from asgiri.proto.websocket_heartbeat import WebSocketHeartbeat


@pytest.mark.asyncio
async def test_ping_sent_after_interval():
    send_ping = Mock()
    close = Mock()
    hb = WebSocketHeartbeat(
        ping_interval=0.05, ping_timeout=0.5, send_ping=send_ping, close=close
    )
    hb.start()
    await asyncio.sleep(0.07)
    send_ping.assert_called_once()
    hb.stop()


@pytest.mark.asyncio
async def test_missing_pong_triggers_close():
    send_ping = Mock()
    close = Mock()
    hb = WebSocketHeartbeat(
        ping_interval=0.05, ping_timeout=0.05, send_ping=send_ping, close=close
    )
    hb.start()
    await asyncio.sleep(0.12)
    close.assert_called_once()
    hb.stop()


@pytest.mark.asyncio
async def test_pong_resets_timeout():
    send_ping = Mock()
    close = Mock()
    hb = WebSocketHeartbeat(
        ping_interval=0.05, ping_timeout=0.5, send_ping=send_ping, close=close
    )
    hb.start()
    await asyncio.sleep(0.07)
    send_ping.assert_called_once()
    hb.record_activity()
    await asyncio.sleep(0.07)
    close.assert_not_called()
    hb.stop()


@pytest.mark.asyncio
async def test_disabled_heartbeat_does_nothing():
    send_ping = Mock()
    close = Mock()
    hb = WebSocketHeartbeat(
        ping_interval=None, ping_timeout=0.5, send_ping=send_ping, close=close
    )
    hb.start()
    await asyncio.sleep(0.05)
    send_ping.assert_not_called()
    close.assert_not_called()
    hb.stop()


@pytest.mark.asyncio
async def test_activity_before_timeout_prevents_close():
    send_ping = Mock()
    close = Mock()
    hb = WebSocketHeartbeat(
        ping_interval=0.05, ping_timeout=0.08, send_ping=send_ping, close=close
    )
    hb.start()
    await asyncio.sleep(0.07)
    send_ping.assert_called_once()
    await asyncio.sleep(0.05)
    hb.record_activity()
    await asyncio.sleep(0.05)
    close.assert_not_called()
    hb.stop()


@pytest.mark.asyncio
async def test_stop_cancels_tasks():
    send_ping = Mock()
    close = Mock()
    hb = WebSocketHeartbeat(
        ping_interval=0.01, ping_timeout=0.5, send_ping=send_ping, close=close
    )
    hb.start()
    await asyncio.sleep(0.03)
    hb.stop()
    # No exception should be raised; test passes if we reach here.


@pytest.mark.asyncio
async def test_negative_interval_raises():
    with pytest.raises(ValueError, match="ping_interval must be non-negative"):
        WebSocketHeartbeat(
            ping_interval=-1.0, ping_timeout=0.5, send_ping=Mock(), close=Mock()
        )


def test_negative_timeout_raises():
    with pytest.raises(ValueError, match="ping_timeout must be non-negative"):
        WebSocketHeartbeat(
            ping_interval=1.0, ping_timeout=-1.0, send_ping=Mock(), close=Mock()
        )
