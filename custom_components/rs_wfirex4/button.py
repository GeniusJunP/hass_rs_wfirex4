"""A button for each learned command, under a device for each remote."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo, format_mac

from .codes import CodeStore
from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities):
    """Add the buttons of the learned codes, and follow learning and deletion."""
    mac = format_mac(hass.data[DOMAIN][entry.entry_id][CONF_MAC])
    store: CodeStore = hass.data[DOMAIN]["codes"][mac]

    # The device of the RS-WFIREX4 itself, registered in async_setup_entry.
    dev_reg = dr.async_get(hass)
    via_device_id = dev_reg.async_get_device_by_identifier((DOMAIN, mac), entry.entry_id).id

    async_add_entities(
        LearnedButton(mac, via_device_id, device, command)
        for device, commands in store.codes.items()
        for command in commands
    )

    @callback
    def on_change(device: str, command: str, added: bool) -> None:
        if added:
            async_add_entities([LearnedButton(mac, via_device_id, device, command)])
            return
        ent_reg = er.async_get(hass)
        if entity_id := ent_reg.async_get_entity_id(
            "button", DOMAIN, button_unique_id(mac, device, command)
        ):
            ent_reg.async_remove(entity_id)
        if device not in store.codes:
            if remote_device := dev_reg.async_get_device_by_identifier(
                remote_identifier(mac, device), entry.entry_id
            ):
                dev_reg.async_remove_device(remote_device.id)

    entry.async_on_unload(store.async_add_listener(on_change))


def remote_identifier(mac: str, device: str) -> tuple[str, str]:
    return (DOMAIN, f"{mac}_{device}")


def button_unique_id(mac: str, device: str, command: str) -> str:
    return f"wfirex4_{mac}_{device}_{command}"


class LearnedButton(ButtonEntity):
    """Sends one learned command through the remote entity of the RS-WFIREX4."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, mac: str, via_device_id: str, device: str, command: str) -> None:
        self._mac = mac
        self._device = device
        self._command = command
        self._attr_name = command
        self._attr_unique_id = button_unique_id(mac, device, command)
        self._attr_device_info = DeviceInfo(
            identifiers={remote_identifier(mac, device)},
            name=device,
            via_device_id=via_device_id,
        )

    async def async_press(self) -> None:
        remote = self.hass.data[DOMAIN]["remotes"][self._mac]
        await remote.async_send_learned(self._device, self._command)
