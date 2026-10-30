"""RS-WFIREX4 Remote platform that has a remotes."""

import asyncio
import logging
import re
from base64 import b64decode
from collections import defaultdict
from itertools import product
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.persistent_notification import async_create, async_dismiss
from homeassistant.components.remote import (
    ATTR_ALTERNATIVE,
    ATTR_DELAY_SECS,
    ATTR_DEVICE,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.const import ATTR_COMMAND, CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store

from .client import Wfirex4Client
from .codes import CodeStore
from .const import DEFAULT_NAME, DOMAIN
from .helpers import build_default_name_with_mac, build_device_info
from .protocol import build_send_frame, parse_send_response

FLAG_STORAGE_VERSION = 1
FLAG_SAVE_DELAY = 15

COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_COMMAND): vol.All(
            cv.ensure_list, [vol.All(cv.string, vol.Length(min=1))], vol.Length(min=1)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

SERVICE_SEND_SCHEMA = COMMAND_SCHEMA.extend(
    {
        vol.Optional(ATTR_DEVICE): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional(ATTR_DELAY_SECS, default=DEFAULT_DELAY_SECS): vol.Coerce(float),
    }
)

SERVICE_DELETE_SCHEMA = COMMAND_SCHEMA.extend(
    {
        vol.Required(ATTR_DEVICE): vol.All(cv.string, vol.Length(min=1)),
    }
)

SERVICE_LEARN_SCHEMA = COMMAND_SCHEMA.extend(
    {
        vol.Required(ATTR_DEVICE): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional(ATTR_ALTERNATIVE, default=False): cv.boolean,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Set up remote from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]

    mac = format_mac(data.get(CONF_MAC))
    name = data.get(CONF_NAME, build_default_name_with_mac(mac))

    remote_entity = Wfirex4Remote(
        hass.data[DOMAIN]["clients"][mac],
        mac,
        name,
        hass.data[DOMAIN]["codes"][mac],
        Store(hass, FLAG_STORAGE_VERSION, f"rs_wfirex4_{mac.replace(':', '')}_flags"),
    )
    await remote_entity.async_load_flags()
    # The buttons send their commands through this entity.
    hass.data[DOMAIN]["remotes"][mac] = remote_entity
    async_add_entities([remote_entity], update_before_add=False)


class Wfirex4Remote(RemoteEntity):
    """Representation of a RS-WFIREX4 remote."""

    def __init__(self, client: Wfirex4Client, mac: str, name: str, codes: CodeStore, flag):
        """Initialize the RS-WFIREX4 Remote."""
        self._name = name or DEFAULT_NAME
        self._client = client
        self._codes = codes
        self._flag_storage: Store[Any] = flag
        self._flags = defaultdict(int)
        self._attr_is_on = True
        self._codeRegx = re.compile(r"^[0-9a-f]{32,}$")

        self._attr_name = "{} {}".format(self._name, "Remote")
        self._attr_unique_id = "wfirex4_{}_remote".format(mac)
        self._attr_icon = "mdi:remote"
        self._attr_should_poll = False
        self._attr_supported_features = (
            RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND
        )
        self._attr_extra_state_attributes = {}

        self._attr_device_info = build_device_info(mac, name)

    def turn_on(self, **kwargs):
        """Turn the remote on."""
        self._attr_is_on = True
        self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the remote off."""
        self._attr_is_on = False
        self.schedule_update_ha_state()

    def get_code(self, command, device):
        """Get Hex code"""

        def data_packet(value):
            """Decode a data packet given for a Broadlink remote."""
            value = cv.string(value)
            extra = len(value) % 4
            if extra > 0:
                value = value + ("=" * (4 - extra))
            return b64decode(value)

        if command.startswith("b64:"):
            try:
                code, is_toggle_cmd = data_packet(command[4:]).hex(), False
            except ValueError as err:
                raise ValueError("Invalid code") from err

        elif self._codeRegx.match(command):
            code, is_toggle_cmd = command, False

        else:
            if device is None:
                raise KeyError("You need to specify a device")

            try:
                code = self._codes.codes[device][command]
            except KeyError as err:
                raise KeyError("Command not found") from err

            # For toggle commands, alternate between codes in a list.
            if isinstance(code, list):
                code = code[self._flags[device]]
                is_toggle_cmd = True
            else:
                is_toggle_cmd = False

        return code, is_toggle_cmd

    @callback
    def get_flags(self):
        """Return a dictionary of toggle flags.

        A toggle flag indicates whether the remote should send an
        alternative code.
        """
        return self._flags

    async def async_load_flags(self):
        """Load the toggle flags from the storage file."""
        self._flags.update(await self._flag_storage.async_load() or {})

    async def async_send_learned(self, device: str, command: str) -> None:
        """Send one learned command, raising when it fails."""
        if not self._attr_is_on:
            raise HomeAssistantError(f"{self.entity_id} is turned off")
        code, is_toggle_cmd = self.get_code(command, device)
        await self.set_wfirex(code)
        if is_toggle_cmd:
            self._flags[device] ^= 1
            self._flag_storage.async_delay_save(self.get_flags, FLAG_SAVE_DELAY)
        self._attr_extra_state_attributes["last_command_sent"] = code
        self.async_write_ha_state()

    async def async_delete_command(self, **kwargs):
        """Delete learned commands of a device."""
        kwargs = SERVICE_DELETE_SCHEMA(kwargs)
        device = kwargs[ATTR_DEVICE]
        for command in kwargs[ATTR_COMMAND]:
            if command not in self._codes.codes.get(device, {}):
                raise HomeAssistantError(f"Command not found: {device} {command}")
            await self._codes.async_delete(device, command)

    async def async_send_command(self, command, **kwargs):
        """Send a list of commands to a device."""
        kwargs[ATTR_COMMAND] = command
        kwargs = SERVICE_SEND_SCHEMA(kwargs)
        commands = kwargs[ATTR_COMMAND]
        device = kwargs.get(ATTR_DEVICE)
        repeat = kwargs[ATTR_NUM_REPEATS]
        delay = kwargs[ATTR_DELAY_SECS]

        last_code = ""

        if not self._attr_is_on:
            _LOGGER.warning(
                "remote.send_command canceled: %s entity is turned off", self.entity_id
            )
            return

        should_delay = False

        for _, cmd in product(range(repeat), commands):
            if should_delay:
                await asyncio.sleep(delay)

            try:
                code, is_toggle_cmd = self.get_code(cmd, device)

            except (KeyError, ValueError) as err:
                _LOGGER.error("Failed to send '%s' to %s: %s", cmd, device, err)
                should_delay = False
                continue

            try:
                await self.set_wfirex(code)
                last_code = code

            except Exception as err:
                _LOGGER.error("Failed to send '%s' to %s: %s", cmd, device, err)
                continue

            should_delay = True
            if is_toggle_cmd:
                self._flags[device] ^= 1

        self._flag_storage.async_delay_save(self.get_flags, FLAG_SAVE_DELAY)

        if last_code:
            self._attr_extra_state_attributes["last_command_sent"] = last_code
            self.schedule_update_ha_state()

    async def async_learn_command(self, **kwargs):
        """Learn a command to a device."""
        if await self.learn_wfirex(**kwargs):
            self.schedule_update_ha_state()

    async def set_wfirex(self, wave_data_str):
        self._attr_extra_state_attributes["last_command_result"] = "Pending..."

        data = await self._client.async_request(
            build_send_frame(bytes.fromhex(wave_data_str))
        )

        if data:
            self._attr_extra_state_attributes["last_command_result"] = data.hex()
        parse_send_response(data)

    async def learn_wfirex(self, **kwargs):
        """Learn a list of commands from a remote."""
        kwargs = SERVICE_LEARN_SCHEMA(kwargs)
        commands = kwargs[ATTR_COMMAND]
        device = kwargs[ATTR_DEVICE]
        toggle = kwargs[ATTR_ALTERNATIVE]

        async def learn_command(command):
            notify_id = f"{DOMAIN}_learn_command"
            async_create(
                self.hass,
                f"Press the '{command}' button.",
                title="Learn command",
                notification_id=notify_id,
            )

            try:
                code = (await self._client.async_learn()).hex()
            finally:
                async_dismiss(self.hass, notification_id=notify_id)

            self._attr_extra_state_attributes["last_learn"] = code
            return code

        if not self._attr_is_on:
            _LOGGER.warning(
                "remote.learn_command canceled: %s entity is turned off", self.entity_id
            )
            return False

        for command in commands:
            try:
                code = await learn_command(command)
                if toggle:
                    code = [code, await learn_command(command)]

                await self._codes.async_set(device, command, code)
            except Exception as err:
                _LOGGER.error("Failed to learn '%s': %s", command, err)
                continue

        return True
