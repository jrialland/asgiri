"""WebSocket frame parser/serializer for HTTP/2 and HTTP/3.

This module provides a lightweight WebSocket frame implementation that works
without requiring an HTTP/1.1 upgrade handshake. It's designed for use with
HTTP/2 CONNECT (RFC 8441) and HTTP/3 Extended CONNECT (RFC 9220).

Unlike wsproto which expects an HTTP/1.1 Upgrade handshake, this module
handles only the WebSocket framing layer (RFC 6455) and assumes the
handshake has already been completed via Extended CONNECT.
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass


class Opcode(enum.IntEnum):
    """WebSocket opcodes per RFC 6455."""

    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


class CloseCode(enum.IntEnum):
    """WebSocket close codes per RFC 6455."""

    NORMAL = 1000
    GOING_AWAY = 1001
    PROTOCOL_ERROR = 1002
    UNSUPPORTED_DATA = 1003
    NO_STATUS_RCVD = 1005
    ABNORMAL_CLOSURE = 1006
    INVALID_FRAME_PAYLOAD = 1007
    POLICY_VIOLATION = 1008
    MESSAGE_TOO_BIG = 1009
    MANDATORY_EXT = 1010
    INTERNAL_ERROR = 1011
    SERVICE_RESTART = 1012
    TRY_AGAIN_LATER = 1013
    BAD_GATEWAY = 1014
    TLS_HANDSHAKE = 1015


@dataclass
class WebSocketFrame:
    """A WebSocket frame.

    Attributes:
        opcode: The frame opcode (text, binary, close, ping, pong)
        payload: The frame payload bytes
        fin: Whether this is the final frame in a message
    """

    opcode: Opcode
    payload: bytes
    fin: bool = True

    @property
    def is_text(self) -> bool:
        """Check if this is a text frame."""
        return self.opcode == Opcode.TEXT

    @property
    def is_binary(self) -> bool:
        """Check if this is a binary frame."""
        return self.opcode == Opcode.BINARY

    @property
    def is_close(self) -> bool:
        """Check if this is a close frame."""
        return self.opcode == Opcode.CLOSE

    @property
    def is_ping(self) -> bool:
        """Check if this is a ping frame."""
        return self.opcode == Opcode.PING

    @property
    def is_pong(self) -> bool:
        """Check if this is a pong frame."""
        return self.opcode == Opcode.PONG


class WebSocketProtocolError(Exception):
    """Raised when a protocol error is detected."""

    pass


class WebSocketParser:
    """Parse WebSocket frames from a byte stream.

    This parser handles the WebSocket framing protocol per RFC 6455.
    It assumes the handshake has already been completed (via HTTP/2 CONNECT
    or HTTP/3 Extended CONNECT), so it only parses frame data.
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        self._buffer = bytearray()
        self._frames: list[WebSocketFrame] = []

    def receive_data(self, data: bytes) -> list[WebSocketFrame]:
        """Parse frames from incoming data.

        Args:
            data: Raw bytes received from the network.

        Returns:
            List of complete frames parsed from the data.

        Raises:
            WebSocketProtocolError: If a malformed frame is detected.
        """
        self._buffer.extend(data)
        frames: list[WebSocketFrame] = []

        while True:
            frame = self._try_parse_frame()
            if frame is None:
                break
            frames.append(frame)

        return frames

    def _try_parse_frame(self) -> WebSocketFrame | None:
        """Try to parse a single frame from the buffer.

        Returns:
            A WebSocketFrame if a complete frame is available, None otherwise.

        Raises:
            WebSocketProtocolError: If the frame is malformed.
        """
        buffer = self._buffer
        min_header_size = 2

        if len(buffer) < min_header_size:
            return None

        # Parse first byte: FIN, RSV, OPCODE
        first_byte = buffer[0]
        fin = bool(first_byte & 0x80)
        rsv = (first_byte >> 4) & 0x07
        opcode = first_byte & 0x0F

        # RSV must be 0 unless extensions are negotiated
        if rsv != 0:
            raise WebSocketProtocolError(f"RSV must be 0, got {rsv}")

        # Parse second byte: MASK, PAYLOAD_LEN
        second_byte = buffer[1]
        masked = bool(second_byte & 0x80)
        payload_len = second_byte & 0x7F

        # Calculate header size
        header_size = min_header_size

        # Extended payload length
        if payload_len == 126:
            header_size += 2
            if len(buffer) < header_size:
                return None
            payload_len = struct.unpack("!H", buffer[2:4])[0]
        elif payload_len == 127:
            header_size += 8
            if len(buffer) < header_size:
                return None
            payload_len = struct.unpack("!Q", buffer[2:10])[0]

        # Masking key (always present from client, must be absent from server)
        # For HTTP/2/3 WebSocket, the peer is the client, so we expect masking
        if masked:
            header_size += 4
            if len(buffer) < header_size:
                return None

        # Check if we have the full frame
        if len(buffer) < header_size + payload_len:
            return None

        # Extract payload
        payload_start = header_size
        payload_end = payload_start + payload_len
        payload = bytes(buffer[payload_start:payload_end])

        # Unmask payload if needed
        if masked:
            mask_key = bytes(buffer[header_size - 4 : header_size])
            payload = self._unmask(payload, mask_key)

        # Validate opcode
        try:
            opcode_enum = Opcode(opcode)
        except ValueError:
            raise WebSocketProtocolError(f"Unknown opcode: {opcode}")

        # Control frames must not be fragmented
        if opcode_enum in (Opcode.CLOSE, Opcode.PING, Opcode.PONG) and not fin:
            raise WebSocketProtocolError(
                f"Control frame {opcode_enum.name} must not be fragmented"
            )

        # Control frames must have small payload
        if opcode_enum in (Opcode.CLOSE, Opcode.PING, Opcode.PONG) and payload_len > 125:
            raise WebSocketProtocolError(
                f"Control frame {opcode_enum.name} payload too large: {payload_len}"
            )

        # Consume bytes from buffer
        del buffer[:payload_end]

        return WebSocketFrame(opcode=opcode_enum, payload=payload, fin=fin)

    @staticmethod
    def _unmask(payload: bytes, mask_key: bytes) -> bytes:
        """Unmask payload data.

        Args:
            payload: The masked payload.
            mask_key: The 4-byte masking key.

        Returns:
            The unmasked payload.
        """
        return bytes(
            payload[i] ^ mask_key[i % 4] for i in range(len(payload))
        )


def encode_frame(
    opcode: Opcode,
    payload: bytes = b"",
    fin: bool = True,
) -> bytes:
    """Encode a WebSocket frame.

    This creates a server-to-client frame (no masking).

    Args:
        opcode: The frame opcode.
        payload: The frame payload.
        fin: Whether this is the final frame.

    Returns:
        The encoded frame as bytes.
    """
    # First byte: FIN, RSV, OPCODE
    first_byte = (0x80 if fin else 0x00) | (opcode & 0x0F)

    # Payload length encoding
    payload_len = len(payload)
    if payload_len < 126:
        header = struct.pack("!BB", first_byte, payload_len)
    elif payload_len < 65536:
        header = struct.pack("!BBH", first_byte, 126, payload_len)
    else:
        header = struct.pack("!BBQ", first_byte, 127, payload_len)

    return header + payload


def encode_text_frame(text: str, fin: bool = True) -> bytes:
    """Encode a text frame.

    Args:
        text: The text to send.
        fin: Whether this is the final frame.

    Returns:
        The encoded frame as bytes.
    """
    return encode_frame(Opcode.TEXT, text.encode("utf-8"), fin)


def encode_binary_frame(data: bytes, fin: bool = True) -> bytes:
    """Encode a binary frame.

    Args:
        data: The binary data to send.
        fin: Whether this is the final frame.

    Returns:
        The encoded frame as bytes.
    """
    return encode_frame(Opcode.BINARY, data, fin)


def encode_close_frame(code: int = 1000, reason: str = "") -> bytes:
    """Encode a close frame.

    Args:
        code: The close code.
        reason: The close reason string.

    Returns:
        The encoded close frame as bytes.
    """
    payload = struct.pack("!H", code) + reason.encode("utf-8")
    return encode_frame(Opcode.CLOSE, payload)


def encode_ping_frame(data: bytes = b"") -> bytes:
    """Encode a ping frame.

    Args:
        data: Optional ping payload (max 125 bytes).

    Returns:
        The encoded ping frame as bytes.
    """
    return encode_frame(Opcode.PING, data)


def encode_pong_frame(data: bytes = b"") -> bytes:
    """Encode a pong frame.

    Args:
        data: Optional pong payload (max 125 bytes).

    Returns:
        The encoded pong frame as bytes.
    """
    return encode_frame(Opcode.PONG, data)


def parse_close_frame(payload: bytes) -> tuple[int, str]:
    """Parse a close frame payload.

    Args:
        payload: The close frame payload.

    Returns:
        Tuple of (code, reason).
    """
    if len(payload) >= 2:
        code = struct.unpack("!H", payload[:2])[0]
        reason = payload[2:].decode("utf-8", errors="replace")
    else:
        code = CloseCode.NO_STATUS_RCVD
        reason = ""
    return code, reason
