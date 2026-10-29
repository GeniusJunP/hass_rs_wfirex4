"""TCP client for one RS-WFIREX4 device."""

from __future__ import annotations

import asyncio
import logging

from .const import PORT
from .protocol import (
    CMD_LEARN,
    CMD_SENSOR,
    build_frame,
    build_send_frame,
    frame_size,
    parse_learn_response,
    parse_send_response,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 4.0
READ_TIMEOUT = 4.0
# The device answers a learn command after about 10 s without a signal, and has been
# seen to take up to 30 s when another command arrived while it was learning.
LEARN_TIMEOUT = 60.0


class Wfirex4Client:
    """Send commands to one device, one at a time.

    The device does not handle concurrent commands well: a command that arrives while
    it is learning aborts the learning. Every platform of a device therefore goes
    through one client, whose lock serializes the commands.
    """

    def __init__(self, host: str, port: int = PORT) -> None:
        """Initialize the client."""
        self.host = host
        self.port = port
        self._lock = asyncio.Lock()

    async def async_request(
        self,
        frame: bytes,
        *,
        host: str | None = None,
        read_timeout: float = READ_TIMEOUT,
    ) -> bytes:
        """Send a frame and return the response frame.

        ``host`` overrides the stored host for this request only.
        """
        async with self._lock:
            return await self._async_request(frame, host or self.host, read_timeout)

    async def _async_request(
        self, frame: bytes, host: str, read_timeout: float
    ) -> bytes:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, self.port), timeout=CONNECT_TIMEOUT
        )
        try:
            writer.write(frame)
            await writer.drain()

            # Read one response frame; the device may keep the connection open.
            data = b""
            while (size := frame_size(data)) is None or len(data) < size:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=read_timeout)
                if not chunk:
                    break
                data += chunk
            return data
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError as err:
                _LOGGER.debug(
                    "Error closing connection to %s:%s: %s", host, self.port, err
                )

    async def async_fetch_sensor(self, host: str | None = None) -> bytes:
        """Return the raw sensor response frame."""
        return await self.async_request(build_frame(bytes([CMD_SENSOR])), host=host)

    async def async_send_wave(self, wave: bytes) -> None:
        """Send an IR waveform."""
        parse_send_response(await self.async_request(build_send_frame(wave)))

    async def async_learn(self) -> bytes:
        """Wait for an IR signal and return its waveform."""
        return parse_learn_response(
            await self.async_request(
                build_frame(bytes([CMD_LEARN])), read_timeout=LEARN_TIMEOUT
            )
        )
