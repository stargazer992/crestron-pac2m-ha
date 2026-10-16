"""Crestron PAC2M lighting (2-Series) over CIP/UDP, with a built-in switch between Control4 and stand-alone.

Modes
  control4   : the processor's IP table points at the Control4 server. This integration is passive
               (listens only, sends nothing) and its lights are unavailable.
  standalone : the processor points at Home Assistant. The lights are controlled from here.
Switching is done from Settings > Devices & Services > Crestron > Configure, or with the services
crestron_pac2m.go_standalone / crestron_pac2m.return_to_control4 (dry run by default).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .adopt import async_adopt, async_release
from .client import CrestronClient
from .const import (
    ADOPT_DRY_RUN_DEFAULT,
    CONF_HOST,
    CONF_MODE,
    CRESTRON_PORT,
    DEFAULT_MODE,
    DOMAIN,
    LISTEN_PORT,
    MODE_STANDALONE,
    SOURCE_PORT,
)
from .operations import async_go_standalone, async_return_to_control4

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LIGHT, Platform.BINARY_SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SERVICE_SCHEMA = vol.Schema({vol.Optional("dry_run", default=ADOPT_DRY_RUN_DEFAULT): cv.boolean})


@dataclass
class CrestronData:
    """Runtime data of one config entry."""

    client: CrestronClient


type CrestronConfigEntry = ConfigEntry[CrestronData]


def _loaded_entry(hass: HomeAssistant) -> CrestronConfigEntry:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        return entry
    raise ServiceValidationError("The Crestron PAC2M integration is not loaded (enable it first)")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the services once."""

    async def _go_standalone(call: ServiceCall) -> dict:
        result = await async_go_standalone(hass, _loaded_entry(hass), call.data["dry_run"])
        return {"ok": result.ok, "message": result.message, "steps": result.steps}

    async def _return_to_control4(call: ServiceCall) -> dict:
        result = await async_return_to_control4(hass, _loaded_entry(hass), call.data["dry_run"])
        return {"ok": result.ok, "message": result.message, "steps": result.steps}

    async def _adopt(call: ServiceCall) -> dict:
        messages, _ = await async_adopt(hass, call.data["dry_run"])
        return {"messages": messages}

    async def _release(call: ServiceCall) -> dict:
        messages, needs_reload = await async_release(hass, call.data["dry_run"])
        if needs_reload and not call.data["dry_run"]:
            for c4 in hass.config_entries.async_entries("control4"):
                hass.config_entries.async_schedule_reload(c4.entry_id)
        return {"messages": messages}

    for name, handler in (
        ("go_standalone", _go_standalone),
        ("return_to_control4", _return_to_control4),
        ("adopt_entity_ids", _adopt),
        ("release_entity_ids", _release),
    ):
        hass.services.async_register(DOMAIN, name, handler, schema=SERVICE_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> bool:
    """Start the client. In control4 mode it stays passive so nothing is ever sent to the processor."""
    mode = entry.options.get(CONF_MODE, DEFAULT_MODE)
    client = CrestronClient(entry.data[CONF_HOST], CRESTRON_PORT, LISTEN_PORT, SOURCE_PORT, active=(mode == MODE_STANDALONE))
    try:
        await client.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(f"Cannot open UDP port for the Crestron feedback: {err}") from err
    entry.runtime_data = CrestronData(client)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await client.async_stop()                    # never leave sockets or tasks behind
        raise
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: CrestronConfigEntry) -> None:
    """Apply a mode change in place (no reload: the sockets and the session stay up)."""
    entry.runtime_data.client.active = entry.options.get(CONF_MODE, DEFAULT_MODE) == MODE_STANDALONE


async def async_unload_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> bool:
    """Stop the client and unload the platforms."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_stop()
    return unloaded
