"""rs_wfirex4 integration entrypoints."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import Wfirex4Client
from .codes import CodeStore
from .const import DOMAIN
from .helpers import build_default_name_with_mac, build_device_info, test_connection
from .sensor import Wfirex4Fetcher

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "remote", "button", "infrared"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the integration via YAML (import flow)."""
    if DOMAIN not in config:
        return True

    for entry_conf in config[DOMAIN]:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=entry_conf,
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up config entry with connection check BEFORE forwarding platforms."""

    hass.data.setdefault(
        DOMAIN,
        {
            "clients": {},
            "codes": {},
            "coordinators": {},
            "fetchers": {},
            "remotes": {},
        },
    )

    host = entry.data.get(CONF_HOST, "")
    mac = format_mac(entry.data.get(CONF_MAC, ""))

    # -----------------------
    # 1. Check connection
    # -----------------------
    try:
        reachable_host = await test_connection(hass, host, mac)
    except Exception as err:
        _LOGGER.exception("Unexpected error during pre-setup connection test")
        raise ConfigEntryNotReady from err

    if not reachable_host:
        # Home Assistant は自動リトライするので OK
        raise ConfigEntryNotReady("Device not reachable during setup")

    # -----------------------
    # 2. Save if IP changed
    # -----------------------
    if reachable_host != host:
        new_data = {**entry.data, CONF_HOST: reachable_host}
        hass.config_entries.async_update_entry(entry, data=new_data)
        host = reachable_host
        _LOGGER.info("Updated host for %s to %s", entry.entry_id, host)

    # Save entry data for platforms (remote/sensor).
    # Use the latest host value so platforms (especially remote) don't use stale IP.
    hass.data[DOMAIN][entry.entry_id] = {**entry.data, CONF_HOST: host}

    # All platforms of a device share one client, which serializes the commands.
    client = hass.data[DOMAIN]["clients"].get(mac)
    if client:
        client.host = host
    else:
        client = hass.data[DOMAIN]["clients"][mac] = Wfirex4Client(host)

    # The buttons of the learned remotes refer to this device by its id.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **build_device_info(mac, entry.data.get(CONF_NAME, build_default_name_with_mac(mac))),
    )

    # The remote and the buttons share the learned codes.
    if mac not in hass.data[DOMAIN]["codes"]:
        codes = CodeStore(hass, mac)
        await codes.async_load()
        hass.data[DOMAIN]["codes"][mac] = codes

    # -----------------------
    # 3. Prepare sensor coordinator (BEFORE forwarding platforms)
    # -----------------------
    # NOTE: remote platform does not require fetcher/coordinator, but sensor does.
    if "sensor" in PLATFORMS:
        opts = entry.options

        scan_interval = opts.get("scan_interval", 60)
        temp_offset = opts.get("temp_offset", entry.data.get("temp_offset", 0.0))
        humi_offset = opts.get("humi_offset", entry.data.get("humi_offset", 0.0))

        fetcher = hass.data[DOMAIN]["fetchers"].get(mac)
        if not fetcher:
            fetcher = hass.data[DOMAIN]["fetchers"][mac] = Wfirex4Fetcher(
                client,
                mac,
                temp_offset,
                humi_offset,
                scan_interval,
                entry,
                hass,
            )
        else:
            # Update existing fetcher with latest config
            fetcher.apply_config(
                temp_offset=temp_offset,
                humi_offset=humi_offset,
                scan_interval=scan_interval,
                entry=entry,
                hass=hass,
            )

        coordinator = hass.data[DOMAIN]["coordinators"].get(mac)
        if not coordinator:
            coordinator = hass.data[DOMAIN]["coordinators"][mac] = (
                DataUpdateCoordinator(
                    hass,
                    _LOGGER,
                    name=entry.title or mac,
                    update_interval=timedelta(seconds=scan_interval),
                    update_method=fetcher.get_sensor_data,
                )
            )
        else:
            coordinator.update_interval = timedelta(seconds=scan_interval)

        # IMPORTANT: If first refresh fails, raise ConfigEntryNotReady HERE
        # (before async_forward_entry_setups), otherwise HA will warn.
        try:
            await coordinator.async_config_entry_first_refresh()
        except Exception as err:
            raise ConfigEntryNotReady from err

    # -----------------------
    # 4. Forward platforms
    # -----------------------
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry):
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
