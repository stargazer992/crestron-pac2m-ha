"""Crestron CIP-over-UDP framing for the PAC2M (pure Python, no Home Assistant imports).

Everything here was learned from packet captures against the real processor
(see crestron_lessons_learned.md / crestron_udp_protocol.md in the project).

Frame layout
    data message : 0b <seq> 00 00 00 <len> 00 06 <payload>      len = len(payload) + 2
    ack          : 0c a7 a2 cc 80 a7 00 06 <seq of the acked message>
Payloads
    analog level : 04 <join hi> <join lo> <level hi> <level lo>   (0000 off ... ffff full)
    digital      : 03 00 <join lo> <join hi>                      (join hi bit 7 set = LOW)
    ping         : 01 00
    hello        : 02 03 00                                       (processor -> client)
    name entry   : <len> 12 <id> <text>                           (processor -> client)
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- constants (kept at the top, no magic numbers further down) ----
FRAME_DATA = 0x0B
FRAME_ACK = 0x0C
ACK_PREFIX = bytes([0x0C, 0xA7, 0xA2, 0xCC, 0x80, 0xA7, 0x00, 0x06])  # + seq byte
DATA_HEADER_PAD = bytes([0x00, 0x00, 0x00])        # bytes 2-4 of a data frame
DATA_SUBHEADER = bytes([0x00, 0x06])               # first two bytes of every body
ACK_LENGTH = 9
DATA_HEADER_LENGTH = 6                             # 0b seq 00 00 00 len
PAYLOAD_PING = bytes([0x01, 0x00])
KIND_PING = 0x01
KIND_HELLO = 0x02
KIND_DIGITAL = 0x03
KIND_ANALOG = 0x04
NAME_MARKER = 0x12
LEVEL_FULL = 0xFFFF
JOIN_BASE = 0x100                                  # analog join = 0x100 + (name id + 1) / 2


@dataclass(frozen=True)
class Frame:
    """One decoded frame from the processor."""

    kind: str                  # "ack" or "data"
    seq: int
    payload: bytes = b""


@dataclass(frozen=True)
class Event:
    """One decoded payload. kind: analog | digital | hello | name (light) | room | other."""

    kind: str
    join: int = 0
    level: int = 0             # analog level (0-65535) or digital 0/1
    text: str = ""
    name_id: int = 0


def build_data(seq: int, payload: bytes) -> bytes:
    """Wrap a payload into a data frame."""
    return bytes([FRAME_DATA, seq & 0xFF]) + DATA_HEADER_PAD + bytes([len(payload) + 2]) + DATA_SUBHEADER + payload


def build_ack(seq: int) -> bytes:
    """Ack for a data frame received from the processor."""
    return ACK_PREFIX + bytes([seq & 0xFF])


def analog_payload(join: int, level: int) -> bytes:
    """Payload that sets an analog join to a 16-bit level."""
    level = max(0, min(LEVEL_FULL, int(level)))
    return bytes([KIND_ANALOG, (join >> 8) & 0xFF, join & 0xFF, (level >> 8) & 0xFF, level & 0xFF])


def digital_payload(join: int, high: bool) -> bytes:
    """Payload that sets a digital join high or low."""
    hi = (join >> 8) & 0x7F
    if not high:
        hi |= 0x80
    return bytes([KIND_DIGITAL, 0x00, join & 0xFF, hi])


def parse_datagram(data: bytes) -> list[Frame]:
    """Split a UDP datagram into frames. Unknown or truncated data is skipped, never raises."""
    frames: list[Frame] = []
    pos = 0
    while pos < len(data):
        marker = data[pos]
        if marker == FRAME_ACK:
            if pos + ACK_LENGTH > len(data):
                break
            frames.append(Frame("ack", data[pos + ACK_LENGTH - 1]))
            pos += ACK_LENGTH
        elif marker == FRAME_DATA:
            if pos + DATA_HEADER_LENGTH > len(data):
                break
            length = data[pos + 5]
            body = data[pos + DATA_HEADER_LENGTH: pos + DATA_HEADER_LENGTH + length]
            if len(body) < length:
                break                                  # truncated frame
            frames.append(Frame("data", data[pos + 1], bytes(body[2:])))
            pos += DATA_HEADER_LENGTH + length
        else:
            break                                      # unknown marker: stop, keep what we have
    return frames


def decode_payload(payload: bytes) -> Event:
    """Decode one payload into an Event."""
    if not payload:
        return Event("other")
    kind = payload[0]
    # name entries: <len> 12 <id> <text>. Checked first because a 1-letter name has len 4 like an analog frame.
    if len(payload) >= 4 and payload[1] == NAME_MARKER:
        text = payload[3: 1 + kind].decode("latin-1").rstrip("\x00").strip()
        # odd ids are light names; even ids are room names (seen on the real processor, Oct 2026) and must NOT
        # be used as light labels - they map onto the same join number and would overwrite the light name
        kind_name = "name" if payload[2] % 2 == 1 else "room"
        return Event(kind_name, name_id=payload[2], text=text, join=name_id_to_join(payload[2]))
    if kind == KIND_ANALOG and len(payload) >= 5:
        return Event("analog", join=(payload[1] << 8) | payload[2], level=(payload[3] << 8) | payload[4])
    if kind == KIND_DIGITAL and len(payload) >= 4:
        return Event("digital", join=((payload[3] & 0x7F) << 8) | payload[2], level=0 if payload[3] & 0x80 else 1)
    if kind == KIND_HELLO:
        return Event("hello")
    return Event("other")


def name_id_to_join(name_id: int) -> int:
    """Name entries have odd ids; the analog join is 0x100 + (id + 1) / 2 (verified for the Theater)."""
    return JOIN_BASE + (name_id + 1) // 2


def brightness_to_level(brightness: int) -> int:
    """Home Assistant brightness 0-255 to Crestron 16-bit level (255 * 257 = 65535 exactly)."""
    return max(0, min(255, int(brightness))) * 257


def level_to_brightness(level: int) -> int:
    """Crestron 16-bit level to Home Assistant brightness 0-255."""
    return max(0, min(255, round(level * 255 / LEVEL_FULL)))
