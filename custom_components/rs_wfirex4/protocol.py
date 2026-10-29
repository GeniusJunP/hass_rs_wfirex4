"""RS-WFIREX4 wire format: frames, CRC and IR waveforms.

A frame is ``0xAA``, a 2-byte big-endian payload length, the payload and a CRC-8
(polynomial 0x85, MSB first, initial value 0) over the payload only. The first
payload byte is the command.

An IR waveform is a sequence of bytes, each a duration in units of 0.1 ms, that
alternate between carrier on and carrier off, starting with on. A byte of 0xFF is
25.5 ms and keeps the current level, so the next byte extends the same interval;
any other byte, 0 included, ends the interval and flips the level. The device writes
learned waveforms in the same form, so a learned code can be sent back as is.

This module has no Home Assistant dependency so that it can be unit tested alone.
"""

from __future__ import annotations

FRAME_START = 0xAA

CMD_SEND = 0x11
CMD_LEARN = 0x12
CMD_SENSOR = 0x18

STATUS_OK = 0x00

WAVE_UNIT_US = 100
WAVE_CONTINUE = 0xFF


class Wfirex4ProtocolError(Exception):
    """Raised when a response from the device is malformed or reports a failure."""


def _crc8_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc << 1) ^ 0x85) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


_CRC8_TABLE = _crc8_table()


def crc8(payload: bytes) -> int:
    """Return the CRC-8 the device expects over a frame payload."""
    crc = 0
    for byte in payload:
        crc = _CRC8_TABLE[crc ^ byte]
    return crc


def build_frame(payload: bytes) -> bytes:
    """Wrap a payload in a frame."""
    return (
        bytes([FRAME_START])
        + len(payload).to_bytes(2, "big")
        + payload
        + bytes([crc8(payload)])
    )


def frame_size(data: bytes) -> int | None:
    """Return the size of the frame that ``data`` starts with, if the header is in."""
    if len(data) < 3:
        return None
    return 3 + int.from_bytes(data[1:3], "big") + 1


def parse_frame(data: bytes, command: int) -> bytes:
    """Validate a response frame for ``command`` and return its payload."""
    size = frame_size(data)
    if data[:1] != bytes([FRAME_START]) or size is None or len(data) < size:
        raise Wfirex4ProtocolError(f"Incomplete response: {data.hex()}")
    payload = data[3 : size - 1]
    if crc8(payload) != data[size - 1]:
        raise Wfirex4ProtocolError(f"CRC mismatch in response: {data.hex()}")
    if payload[:1] != bytes([command]):
        raise Wfirex4ProtocolError(
            f"Response to command {payload[:1].hex()}, expected {command:02x}"
        )
    return payload


def build_send_frame(wave: bytes) -> bytes:
    """Return the frame that sends an IR waveform."""
    return build_frame(bytes([CMD_SEND, 0x00]) + len(wave).to_bytes(2, "big") + wave)


def parse_send_response(data: bytes) -> None:
    """Raise unless the device accepted a send command."""
    payload = parse_frame(data, CMD_SEND)
    if payload[1:2] != bytes([STATUS_OK]):
        raise Wfirex4ProtocolError(f"Send failed with status {payload[1:2].hex()}")


def parse_learn_response(data: bytes) -> bytes:
    """Return the waveform from a learn response.

    The payload is the command, a status byte, a reserved byte, a 2-byte waveform
    length and the waveform. A status other than 0 means nothing was learned; the
    device answers so when it times out or another command arrives meanwhile.
    """
    payload = parse_frame(data, CMD_LEARN)
    if payload[1:2] != bytes([STATUS_OK]):
        raise Wfirex4ProtocolError(f"Learning failed with status {payload[1:2].hex()}")
    length = int.from_bytes(payload[3:5], "big")
    wave = payload[5 : 5 + length]
    if len(wave) != length:
        raise Wfirex4ProtocolError(
            f"Truncated waveform in learn response: {data.hex()}"
        )
    return wave
