"""
Tests for WebSocket protocol implementation.

This module tests:
- Ping/Pong frame handling
- Close frame handling with various codes and reasons
- Fragmented message handling
- Protocol violations and error cases
- State transitions and error conditions
"""

import asyncio
from unittest.mock import Mock

import pytest

from asgiri.proto.websocket_frames import (
    Opcode,
    WebSocketParser,
    encode_binary_frame,
    encode_close_frame,
    encode_ping_frame,
    encode_pong_frame,
    encode_text_frame,
    parse_close_frame,
)
from asgiri.proto.websocket_http11 import (
    WebSocketHTTP11Handler,
    generate_accept_key,
)


class TestWebSocketFrames:
    """Tests for the WebSocket frame encoder/decoder."""

    def test_encode_text_frame(self):
        """Test encoding a text frame."""
        text = "Hello, World!"
        frame = encode_text_frame(text)
        
        # Parse it back
        parser = WebSocketParser()
        frames = parser.receive_data(frame)
        
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.TEXT
        assert frames[0].payload == text.encode("utf-8")
        assert frames[0].fin is True

    def test_encode_binary_frame(self):
        """Test encoding a binary frame."""
        data = b"\x00\x01\x02\x03"
        frame = encode_binary_frame(data)
        
        parser = WebSocketParser()
        frames = parser.receive_data(frame)
        
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.BINARY
        assert frames[0].payload == data

    def test_encode_close_frame(self):
        """Test encoding a close frame."""
        code = 1000
        reason = "Normal closure"
        frame = encode_close_frame(code, reason)
        
        parser = WebSocketParser()
        frames = parser.receive_data(frame)
        
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.CLOSE
        
        # Parse the close payload
        parsed_code, parsed_reason = parse_close_frame(frames[0].payload)
        assert parsed_code == code
        assert parsed_reason == reason

    def test_encode_ping_frame(self):
        """Test encoding a ping frame."""
        payload = b"ping"
        frame = encode_ping_frame(payload)
        
        parser = WebSocketParser()
        frames = parser.receive_data(frame)
        
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.PING
        assert frames[0].payload == payload

    def test_encode_pong_frame(self):
        """Test encoding a pong frame."""
        payload = b"pong"
        frame = encode_pong_frame(payload)
        
        parser = WebSocketParser()
        frames = parser.receive_data(frame)
        
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.PONG
        assert frames[0].payload == payload

    def test_parse_fragmented_message(self):
        """Test parsing a fragmented text message."""
        # First frame (not final)
        frame1 = encode_text_frame("Hello, ", fin=False)
        # Continuation frame (final)
        from asgiri.proto.websocket_frames import encode_frame
        frame2 = encode_frame(Opcode.CONTINUATION, b"World!", fin=True)
        
        parser = WebSocketParser()
        frames = parser.receive_data(frame1 + frame2)
        
        assert len(frames) == 2
        assert frames[0].opcode == Opcode.TEXT
        assert frames[0].fin is False
        assert frames[1].opcode == Opcode.CONTINUATION
        assert frames[1].fin is True

    def test_parse_multiple_frames(self):
        """Test parsing multiple frames at once."""
        text_frame = encode_text_frame("Hello")
        binary_frame = encode_binary_frame(b"\x00\x01")
        
        parser = WebSocketParser()
        frames = parser.receive_data(text_frame + binary_frame)
        
        assert len(frames) == 2
        assert frames[0].opcode == Opcode.TEXT
        assert frames[1].opcode == Opcode.BINARY

    def test_parse_empty_data(self):
        """Test parsing empty data returns no frames."""
        parser = WebSocketParser()
        frames = parser.receive_data(b"")
        assert len(frames) == 0

    def test_parse_incomplete_frame(self):
        """Test parsing incomplete frame data."""
        # Only send partial frame header
        parser = WebSocketParser()
        frames = parser.receive_data(b"\x81")  # Just first byte
        assert len(frames) == 0  # No complete frames
        
        # Now send the rest
        frames = parser.receive_data(b"\x05Hello")  # Length + payload
        assert len(frames) == 1
        assert frames[0].payload == b"Hello"


class TestGenerateAcceptKey:
    """Tests for WebSocket key generation."""

    def test_generate_accept_key(self):
        """Test generating accept key from client key."""
        # Test vector from RFC 6455
        client_key = "dGhlIHNhbXBsZSBub25jZQ=="
        expected_accept = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        
        result = generate_accept_key(client_key)
        assert result == expected_accept

    def test_generate_accept_key_different_keys(self):
        """Test that different keys produce different accept keys."""
        key1 = "dGhlIHNhbXBsZSBub25jZQ=="
        key2 = "Y2xpZW50IGtleQ=="
        
        accept1 = generate_accept_key(key1)
        accept2 = generate_accept_key(key2)
        
        assert accept1 != accept2


@pytest.fixture
def mock_transport():
    """Create a mock transport."""
    transport = Mock()
    transport.write = Mock()
    transport.close = Mock()
    transport.is_closing = Mock(return_value=False)
    return transport


@pytest.fixture
def mock_app():
    """Create a simple mock ASGI app."""
    async def app(scope, receive, send):
        # Accept connection
        await send({"type": "websocket.accept"})
        # Wait for messages
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                break
    return app


@pytest.fixture
def websocket_handler(mock_app, mock_transport):
    """Create a WebSocketHTTP11Handler instance."""
    scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "query_string": b"",
        "http_version": "1.1",
        "scheme": "ws",
        "root_path": "",
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
        "subprotocols": [],
        "state": {},
        "extensions": {},
        "asgi": {"version": "3.0", "spec_version": "3.0"},
    }
    
    websocket_key = "dGhlIHNhbXBsZSBub25jZQ=="
    
    handler = WebSocketHTTP11Handler(
        scope=scope,
        app=mock_app,
        send_data=mock_transport.write,
        close_connection=mock_transport.close,
        websocket_key=websocket_key,
    )
    return handler


class TestWebSocketHTTP11Handler:
    """Tests for the HTTP/1.1 WebSocket handler."""

    @pytest.mark.asyncio
    async def test_accept_with_subprotocol(self, websocket_handler, mock_transport):
        """Test accepting WebSocket with a subprotocol."""
        await websocket_handler._handle_accept({
            "type": "websocket.accept",
            "subprotocol": "chat",
        })
        
        assert websocket_handler._accepted
        # Check that handshake response was sent
        assert mock_transport.write.called
        written_data = mock_transport.write.call_args[0][0]
        assert b"101 Switching Protocols" in written_data
        assert b"Sec-WebSocket-Accept:" in written_data
        assert b"Sec-WebSocket-Protocol: chat" in written_data

    @pytest.mark.asyncio
    async def test_accept_without_subprotocol(self, websocket_handler, mock_transport):
        """Test accepting WebSocket without a subprotocol."""
        await websocket_handler._handle_accept({
            "type": "websocket.accept",
        })
        
        assert websocket_handler._accepted
        written_data = mock_transport.write.call_args[0][0]
        assert b"101 Switching Protocols" in written_data
        assert b"Sec-WebSocket-Protocol" not in written_data

    @pytest.mark.asyncio
    async def test_accept_twice_raises_error(self, websocket_handler):
        """Test that accepting twice raises an error."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        
        with pytest.raises(RuntimeError, match="already accepted"):
            await websocket_handler._handle_accept({"type": "websocket.accept"})

    @pytest.mark.asyncio
    async def test_send_text_message(self, websocket_handler, mock_transport):
        """Test sending a text message."""
        # Accept first
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        mock_transport.write.reset_mock()
        
        # Send text message
        await websocket_handler._handle_send({
            "type": "websocket.send",
            "text": "Hello, World!",
        })
        
        assert mock_transport.write.called
        written_data = mock_transport.write.call_args[0][0]
        
        # Verify it's a valid WebSocket text frame
        parser = WebSocketParser()
        frames = parser.receive_data(written_data)
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.TEXT
        assert frames[0].payload == b"Hello, World!"

    @pytest.mark.asyncio
    async def test_send_binary_message(self, websocket_handler, mock_transport):
        """Test sending a binary message."""
        # Accept first
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        mock_transport.write.reset_mock()
        
        # Send binary message
        await websocket_handler._handle_send({
            "type": "websocket.send",
            "bytes": b"\x00\x01\x02",
        })
        
        assert mock_transport.write.called
        written_data = mock_transport.write.call_args[0][0]
        
        # Verify it's a valid WebSocket binary frame
        parser = WebSocketParser()
        frames = parser.receive_data(written_data)
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.BINARY
        assert frames[0].payload == b"\x00\x01\x02"

    @pytest.mark.asyncio
    async def test_send_before_accept_raises_error(self, websocket_handler):
        """Test that sending data before accept raises an error."""
        with pytest.raises(RuntimeError, match="not accepted"):
            await websocket_handler._handle_send({
                "type": "websocket.send",
                "text": "Hello",
            })

    @pytest.mark.asyncio
    async def test_send_without_bytes_or_text_raises_error(self, websocket_handler):
        """Test that send without bytes or text raises an error."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        
        with pytest.raises(ValueError, match="must have 'bytes' or 'text'"):
            await websocket_handler._handle_send({"type": "websocket.send"})

    @pytest.mark.asyncio
    async def test_close_with_code_and_reason(self, websocket_handler, mock_transport):
        """Test closing with specific code and reason."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        mock_transport.write.reset_mock()
        
        await websocket_handler._close(1001, "Going away")
        
        assert websocket_handler._closed
        assert websocket_handler._close_code == 1001
        assert websocket_handler._close_reason == "Going away"
        assert mock_transport.write.called
        assert mock_transport.close.called
        
        # Verify close frame
        written_data = mock_transport.write.call_args[0][0]
        parser = WebSocketParser()
        frames = parser.receive_data(written_data)
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.CLOSE

    @pytest.mark.asyncio
    async def test_close_when_already_closed(self, websocket_handler, mock_transport):
        """Test that calling close() multiple times is idempotent."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        await websocket_handler._close(1000, "Normal")
        
        # Reset mocks
        mock_transport.write.reset_mock()
        mock_transport.close.reset_mock()
        
        # Close again
        await websocket_handler._close(1001, "Another reason")
        
        # No additional writes or closes
        assert not mock_transport.write.called
        assert not mock_transport.close.called

    @pytest.mark.asyncio
    async def test_close_message_from_app(self, websocket_handler, mock_transport):
        """Test handling websocket.close message from application."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        mock_transport.write.reset_mock()
        
        await websocket_handler._handle_close({
            "type": "websocket.close",
            "code": 1000,
            "reason": "Done",
        })
        
        assert websocket_handler._closed
        assert websocket_handler._close_code == 1000
        assert websocket_handler._close_reason == "Done"

    @pytest.mark.asyncio
    async def test_data_received_text_frame(self, websocket_handler):
        """Test receiving a text frame from client."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        
        # Create a masked text frame (as client would send)
        import struct
        payload = b"Hello from client"
        mask_key = b"\x12\x34\x56\x78"
        masked_payload = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))
        
        frame_header = struct.pack("!BB", 0x81, 0x80 | len(payload))  # FIN + TEXT, MASKED
        frame = frame_header + mask_key + masked_payload
        
        websocket_handler.data_received(frame)
        
        # Give async tasks time to run
        await asyncio.sleep(0.01)
        
        # Check that message was queued
        assert not websocket_handler._receive_queue.empty()
        message = websocket_handler._receive_queue.get_nowait()
        assert message["type"] == "websocket.receive"
        assert message["text"] == "Hello from client"

    @pytest.mark.asyncio
    async def test_data_received_ping_frame(self, websocket_handler, mock_transport):
        """Test that ping frame triggers pong response."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        mock_transport.write.reset_mock()
        
        # Create a masked ping frame
        import struct
        payload = b"ping"
        mask_key = b"\xAB\xCD\xEF\x01"
        masked_payload = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))
        
        frame_header = struct.pack("!BB", 0x89, 0x80 | len(payload))  # FIN + PING, MASKED
        frame = frame_header + mask_key + masked_payload
        
        websocket_handler.data_received(frame)
        await asyncio.sleep(0.01)
        
        # Verify pong was sent
        assert mock_transport.write.called
        written_data = mock_transport.write.call_args[0][0]
        
        # Parse and verify it's a pong
        parser = WebSocketParser()
        frames = parser.receive_data(written_data)
        assert len(frames) == 1
        assert frames[0].opcode == Opcode.PONG
        assert frames[0].payload == payload

    @pytest.mark.asyncio
    async def test_data_received_close_frame(self, websocket_handler, mock_transport):
        """Test receiving close frame from client."""
        await websocket_handler._handle_accept({"type": "websocket.accept"})
        mock_transport.write.reset_mock()
        
        # Create a masked close frame with code 1001
        import struct
        code = 1001
        payload = struct.pack("!H", code) + b"Client closing"
        mask_key = b"\x11\x22\x33\x44"
        masked_payload = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))
        
        frame_header = struct.pack("!BB", 0x88, 0x80 | len(payload))  # FIN + CLOSE, MASKED
        frame = frame_header + mask_key + masked_payload
        
        websocket_handler.data_received(frame)
        await asyncio.sleep(0.01)
        
        # Verify disconnect message was queued
        message = websocket_handler._receive_queue.get_nowait()
        assert message["type"] == "websocket.disconnect"
        assert message["code"] == 1001
        
        # Verify close frame was echoed
        assert mock_transport.write.called
        assert mock_transport.close.called

    def test_data_received_when_closed(self, websocket_handler, mock_transport):
        """Test that data_received() does nothing when connection is already closed."""
        # Close the connection
        asyncio.run(websocket_handler._close(1000, "Test"))
        
        # Reset transport mock
        mock_transport.write.reset_mock()
        
        # Try to receive data
        websocket_handler.data_received(b"some data")
        
        # No writes should occur
        assert not mock_transport.write.called
