"""Learned IR codes of one RS-WFIREX4, shared by the remote and the button platform."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
# Codes of all devices used to share this key, before they were stored per device.
LEGACY_STORAGE_KEY = "rs_wfirex4_codes"

# A code is a hex string, or a pair of them for a toggle command learned with `alternative`.
Code = str | list[str]
Listener = Callable[[str, str, bool], None]


class CodeStore:
    """Codes as {remote device: {command: code}}, notifying listeners of each change."""

    def __init__(self, hass: HomeAssistant, mac: str) -> None:
        self._hass = hass
        self._store: Store[dict[str, dict[str, Code]]] = Store(
            hass, STORAGE_VERSION, f"rs_wfirex4_{mac.replace(':', '')}_codes"
        )
        self.codes: dict[str, dict[str, Code]] = {}
        self._listeners: list[Listener] = []

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data is None:
            data = await Store[Any](self._hass, STORAGE_VERSION, LEGACY_STORAGE_KEY).async_load()
            if data:
                _LOGGER.warning(
                    "Copied the codes of %s to %s. With several RS-WFIREX4, each one now has "
                    "a copy; remove the ones it does not send with remote.delete_command",
                    LEGACY_STORAGE_KEY,
                    self._store.key,
                )
                await self._store.async_save(data)
        self.codes = data or {}

    @callback
    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call listener(device, command, added) on each change. Returns the remover."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def async_set(self, device: str, command: str, code: Code) -> None:
        added = command not in self.codes.get(device, {})
        self.codes.setdefault(device, {})[command] = code
        await self._store.async_save(self.codes)
        if added:
            for listener in self._listeners:
                listener(device, command, True)

    async def async_delete(self, device: str, command: str) -> None:
        commands = self.codes[device]
        del commands[command]
        if not commands:
            del self.codes[device]
        await self._store.async_save(self.codes)
        for listener in self._listeners:
            listener(device, command, False)
