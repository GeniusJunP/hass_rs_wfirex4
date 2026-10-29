"""RS-WFIREX4 infrared emitter platform."""

from __future__ import annotations

import logging

from homeassistant.components.infrared import InfraredCommand, InfraredEmitterEntity
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac

from .client import Wfirex4Client
from .const import DOMAIN
from .helpers import build_default_name_with_mac, build_device_info
from .protocol import Wfirex4ProtocolError, timings_to_wave

_LOGGER = logging.getLogger(__name__)

# The carrier of the device is fixed. Receivers usually accept the 36-40 kHz carriers
# other protocols use, so a command is sent whatever modulation it asks for.
CARRIER_FREQUENCY = 38000


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Set up the infrared emitter from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]

    mac = format_mac(data.get(CONF_MAC))
    name = data.get(CONF_NAME, build_default_name_with_mac(mac))

    async_add_entities(
        [Wfirex4InfraredEmitter(hass.data[DOMAIN]["clients"][mac], mac, name)]
    )


class Wfirex4InfraredEmitter(InfraredEmitterEntity):
    """Infrared emitter of a RS-WFIREX4."""

    def __init__(self, client: Wfirex4Client, mac: str, name: str) -> None:
        """Initialize the emitter."""
        self._client = client
        self._attr_name = f"{name} Infrared"
        self._attr_unique_id = f"wfirex4_{mac}_infrared"
        self._attr_device_info = build_device_info(mac, name)

    async def async_send_command(self, command: InfraredCommand) -> None:
        """Send an IR command.

        command.repeat_count is not applied here: the protocols that repeat already
        include the repeats in get_raw_timings().
        """
        if command.modulation != CARRIER_FREQUENCY:
            _LOGGER.debug(
                "Sending %s Hz command with the fixed %s Hz carrier",
                command.modulation,
                CARRIER_FREQUENCY,
            )
        wave = timings_to_wave(command.get_raw_timings())
        try:
            await self._client.async_send_wave(wave)
        except (OSError, TimeoutError, Wfirex4ProtocolError) as err:
            raise HomeAssistantError(
                f"Failed to send IR command to {self._client.host}: {err}"
            ) from err
