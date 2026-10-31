"""Crestron PAC2M lighting (2-Series) over CIP/UDP, with a built-in switch between Control4 and stand-alone.

Modes
  control4   : the processor's IP table points at the Control4 server. This integration is passive
               (listens only, sends nothing) and its lights are unavailable.
  standalone : the processor points at Home Assistant. The lights are controlled from here.
Switching is done from Settings > Devices & Services > Crestron > Configure, or with the services
crestron_pac2m.go_standalone / crestron_pac2m.return_to_control4 (dry run by default).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    CALLBACK_TYPE,
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .adopt import async_adopt, async_release
from .client import CrestronClient
from .console import CrestronConsole
from .const import (
    ADDRESS_CHECK_DELAY_SECONDS,
    ADDRESS_NOTIFICATION_ID,
    ADOPT_DRY_RUN_DEFAULT,
    CIP_ID,
    CONF_HA_HOST,
    CONF_HOST,
    CONF_MODE,
    CRESTRON_PORT,
    DEFAULT_MODE,
    DOMAIN,
    EVENT_DIGITAL,
    LABEL_CHECK_DELAY_SECONDS,
    LABEL_NOTIFICATION_ID,
    LABEL_STATUS_MISMATCH,
    LABEL_STATUS_NO_DUMP,
    LABEL_STATUS_OK,
    LABEL_STATUS_PENDING,
    LIGHTS,
    LISTEN_PORT,
    MODE_STANDALONE,
    REFRESH_IDLE,
    REFRESH_PAUSE_MAX_SECONDS,
    REFRESH_PAUSE_SECONDS,
    SIGNAL_LABELS_UPDATED,
    SOURCE_PORT,
)
from .labelcheck import LabelReport, compare
from .operations import (
    async_go_standalone,
    async_notify,
    async_refresh_from_processor,
    async_return_to_control4,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.LIGHT, Platform.BINARY_SENSOR, Platform.EVENT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SERVICE_SCHEMA = vol.Schema({vol.Optional("dry_run", default=ADOPT_DRY_RUN_DEFAULT): cv.boolean})
REFRESH_SCHEMA = SERVICE_SCHEMA.extend(
    {vol.Optional("pause_seconds", default=REFRESH_PAUSE_SECONDS): vol.All(vol.Coerce(int), vol.Range(min=1, max=REFRESH_PAUSE_MAX_SECONDS))}
)


@dataclass
class CrestronData:
    """Runtime data of one config entry."""

    client: CrestronClient
    label_report: LabelReport | None = None          # result of the last label comparison
    label_summary_sent: str = ""                      # last text sent as a notification (no repeats)
    label_status: str = LABEL_STATUS_PENDING          # Link sensor attribute label_check
    address_summary_sent: str = ""                    # last address warning sent (no repeats)
    switching: bool = False                           # a go stand-alone / return to Control4 is using the console
    refresh_status: str = REFRESH_IDLE                # Link sensor attribute refresh
    refresh_task: asyncio.Task | None = None


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

    async def _refresh(call: ServiceCall) -> dict:
        return await async_refresh_from_processor(hass, _loaded_entry(hass), call.data["dry_run"], call.data["pause_seconds"])

    hass.services.async_register(DOMAIN, "refresh_from_processor", _refresh, schema=REFRESH_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
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
    data = CrestronData(client)
    entry.runtime_data = data
    _migrate_link_unique_id(hass, entry)
    _setup_label_check(hass, entry, data)
    _setup_address_check(hass, entry, data)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await client.async_stop()                    # never leave sockets or tasks behind
        raise
    entry.async_on_unload(entry.add_update_listener(_options_updated))

    @callback
    def _digital(join: int, level: int) -> None:
        # Groundwork for wall keypads: every digital join change becomes an HA event.
        # Developer Tools > Events > listen to "crestron_pac2m_digital", press a keypad button, note the join.
        hass.bus.async_fire(EVENT_DIGITAL, {"join": join, "join_hex": f"0x{join:04x}", "state": "high" if level else "low"})

    entry.async_on_unload(client.add_digital_listener(_digital))
    return True


def _migrate_link_unique_id(hass: HomeAssistant, entry: CrestronConfigEntry) -> None:
    """Up to v1.8 the Link sensor's unique id was not tied to the entry; move it, keeping its entity id."""
    registry = er.async_get(hass)
    old = registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, f"{DOMAIN}_link")
    if old is not None and (reg_entry := registry.async_get(old)) is not None and reg_entry.config_entry_id == entry.entry_id:
        registry.async_update_entity(old, new_unique_id=f"{entry.entry_id}_link")


def _setup_label_check(hass: HomeAssistant, entry: CrestronConfigEntry, data: CrestronData) -> None:
    """After every link-up, compare the processor's labels with the mapping table and report differences."""
    client = data.client
    timer: dict[str, CALLBACK_TYPE | None] = {"cancel": None}
    state = {"was_connected": False}

    async def _run(_now) -> None:
        timer["cancel"] = None
        try:
            if not client.labels:
                # no name dump since the processor's last reboot (normal after a Home Assistant restart): nothing to compare
                data.label_report = None
                data.label_status = LABEL_STATUS_NO_DUMP
                _LOGGER.info("Crestron label check skipped: no name dump received (the processor sends one only to a new session)")
                async_dispatcher_send(hass, SIGNAL_LABELS_UPDATED)
                return
            report = compare(LIGHTS, client.labels, client.room_for_join)
            data.label_report = report
            data.label_status = LABEL_STATUS_MISMATCH if report.mismatched else LABEL_STATUS_OK
            summary = report.summary()
            if report.mismatched:
                if summary != data.label_summary_sent:          # tell once per distinct result, never repeat
                    data.label_summary_sent = summary
                    _LOGGER.warning("Crestron label check:\n%s", summary)
                    await async_notify(hass, entry, "Crestron: label mismatch", summary,
                                       f"{len(report.mismatched)} Crestron light(s) may be mapped wrongly - see Home Assistant", LABEL_NOTIFICATION_ID)
            else:
                _LOGGER.info("Crestron label check: %s", summary.splitlines()[0])
                data.label_summary_sent = ""
                persistent_notification.async_dismiss(hass, LABEL_NOTIFICATION_ID)
            async_dispatcher_send(hass, SIGNAL_LABELS_UPDATED)   # refresh the Link sensor (NOT the link listeners: that looped)
        except Exception:  # a failed check must never affect the lights
            _LOGGER.exception("Crestron label check failed")

    @callback
    def _link_changed() -> None:
        """Run the check once per link-up (False -> True transition only)."""
        connected = client.connected
        if connected and not state["was_connected"]:
            if timer["cancel"] is not None:
                timer["cancel"]()
            timer["cancel"] = async_call_later(hass, LABEL_CHECK_DELAY_SECONDS, _run)
        elif not connected and timer["cancel"] is not None:
            timer["cancel"]()
            timer["cancel"] = None
        state["was_connected"] = connected

    @callback
    def _cancel() -> None:
        if timer["cancel"] is not None:
            timer["cancel"]()
            timer["cancel"] = None

    entry.async_on_unload(client.add_link_listener(_link_changed))
    entry.async_on_unload(_cancel)


def _setup_address_check(hass: HomeAssistant, entry: CrestronConfigEntry, data: CrestronData) -> None:
    """Stand-alone but the link stays down: read the IP table (read only) and warn if it points at an old HA address."""
    client = data.client
    timer: dict[str, CALLBACK_TYPE | None] = {"cancel": None}

    async def _run(_now) -> None:
        timer["cancel"] = None
        if client.connected or not client.active or data.switching:
            return
        try:
            ha_ip = entry.options.get(CONF_HA_HOST) or await async_get_source_ip(hass, target_ip=client.host)
            con = CrestronConsole(client.host)
            try:
                await con.async_connect()
                row = await con.async_read_entry()
            finally:
                await con.async_close()
        except Exception as err:  # noqa: BLE001 - the check is advisory; never affect the lights
            _LOGGER.debug("Crestron address check could not read the IP table: %s", err)
            return
        if row is None or row.ip == ha_ip:
            if data.address_summary_sent:
                data.address_summary_sent = ""
                persistent_notification.async_dismiss(hass, ADDRESS_NOTIFICATION_ID)
            return
        summary = (
            f"The processor's IP table (entry {CIP_ID}) points at {row.ip}, but Home Assistant is now {ha_ip}, "
            "so the Crestron lights stay unavailable. Fix: Configure > Go stand-alone (re-points the processor and "
            "reboots it, about 2-4 minutes). A DHCP reservation for Home Assistant prevents this."
        )
        if summary != data.address_summary_sent:
            data.address_summary_sent = summary
            _LOGGER.warning("Crestron address check: %s", summary)
            await async_notify(hass, entry, "Crestron: processor points at another address", summary,
                               f"Crestron points at {row.ip}, Home Assistant is {ha_ip} - see Home Assistant", ADDRESS_NOTIFICATION_ID)

    @callback
    def _schedule() -> None:
        if client.connected:
            if timer["cancel"] is not None:
                timer["cancel"]()
                timer["cancel"] = None
        elif timer["cancel"] is None:
            timer["cancel"] = async_call_later(hass, ADDRESS_CHECK_DELAY_SECONDS, _run)

    @callback
    def _cancel() -> None:
        if timer["cancel"] is not None:
            timer["cancel"]()
            timer["cancel"] = None

    _schedule()
    entry.async_on_unload(client.add_link_listener(_schedule))
    entry.async_on_unload(_cancel)


async def _options_updated(hass: HomeAssistant, entry: CrestronConfigEntry) -> None:
    """Apply a mode change in place (no reload: the sockets and the session stay up)."""
    entry.runtime_data.client.active = entry.options.get(CONF_MODE, DEFAULT_MODE) == MODE_STANDALONE


async def async_unload_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> bool:
    """Stop the client and unload the platforms."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.client.resume()
        await entry.runtime_data.client.async_stop()
    return unloaded
