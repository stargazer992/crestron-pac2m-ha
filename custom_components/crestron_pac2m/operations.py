"""The two switch operations used by the options flow and the services."""
from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components import persistent_notification
from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .adopt import async_adopt, async_release
from .const import (
    CONF_CONTROL4_HOST,
    CONF_HA_HOST,
    CONF_HOST,
    CONF_MODE,
    CONF_NOTIFY_SERVICE,
    CONTROL4_DOMAIN,
    DISABLE_DELAY_SECONDS,
    MODE_CONTROL4,
    MODE_STANDALONE,
    NOTIFICATION_ID,
    NOTIFY_SERVICE,
    REFRESH_DUMP_RECEIVED,
    REFRESH_NO_DUMP,
    REFRESH_NOTIFICATION_ID,
    REFRESH_PAUSED,
    REFRESH_WAIT_SECONDS,
    REFRESH_WAITING,
    SIGNAL_LABELS_UPDATED,
)
from .switcher import SwitchResult, repoint

_LOGGER = logging.getLogger(__name__)


def find_control4_host(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Control4 server address: the host of the Control4 integration, else the manual override."""
    for c4 in hass.config_entries.async_entries(CONTROL4_DOMAIN):
        host = c4.data.get(CONF_HOST)
        if host:
            return str(host)
    return entry.options.get(CONF_CONTROL4_HOST) or None


def notify_service_for(entry: ConfigEntry) -> str:
    """The phone notify service from the options ("notify." prefix optional); empty = persistent notification only."""
    service = str(entry.options.get(CONF_NOTIFY_SERVICE, NOTIFY_SERVICE)).strip()
    return service.removeprefix("notify.")


async def async_notify(
    hass: HomeAssistant, entry: ConfigEntry, title: str, text: str, short: str, notification_id: str = NOTIFICATION_ID
) -> None:
    """Persistent notification plus the phone (project rule). Never raises."""
    try:
        persistent_notification.async_create(hass, text, title=title, notification_id=notification_id)
        service = notify_service_for(entry)
        if service and hass.services.has_service("notify", service):
            await hass.services.async_call("notify", service, {"title": title, "message": short})
    except Exception:
        _LOGGER.exception("Could not send the notification")


async def _notify(hass: HomeAssistant, entry: ConfigEntry, title: str, result: SwitchResult) -> None:
    await async_notify(hass, entry, title, result.message + "\n\n" + "\n".join(result.steps), result.message)


async def _repoint(entry: ConfigEntry, *args) -> SwitchResult:
    """repoint() with the switching flag set, so the address check never opens the console at the same time."""
    entry.runtime_data.switching = True
    try:
        return await repoint(*args)
    finally:
        entry.runtime_data.switching = False


def _persist_mode(hass: HomeAssistant, entry: ConfigEntry, mode: str) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_MODE: mode})


async def async_go_standalone(hass: HomeAssistant, entry: ConfigEntry, dry_run: bool) -> SwitchResult:
    """Point the processor at Home Assistant, then take over the Control4 light entity ids."""
    client = entry.runtime_data.client
    host = entry.data[CONF_HOST]
    try:
        ha_ip = entry.options.get(CONF_HA_HOST) or await async_get_source_ip(hass, target_ip=host)
    except Exception as err:  # noqa: BLE001
        return SwitchResult(False, message=f"Cannot determine Home Assistant's IP address: {err}")
    if not client.listening:
        return SwitchResult(False, message="Home Assistant is not listening on the Crestron port; not touching the processor.")

    async def ready() -> bool:
        return client.connected

    async def failback() -> None:
        client.active = False

    if not dry_run:
        client.active = True
    result = await _repoint(entry, host, ha_ip, dry_run, ready, failback)
    result.steps.insert(0, f"Home Assistant address used: {ha_ip}")
    if result.ok:
        messages, _ = await async_adopt(hass, dry_run)
        result.steps.append("--- entity ids ---")
        result.steps.extend(messages)
        if not dry_run:
            _persist_mode(hass, entry, MODE_STANDALONE)
    elif not dry_run:
        client.active = False
    if not dry_run:
        await _notify(hass, entry, "Crestron: stand-alone", result)
    return result


async def async_return_to_control4(hass: HomeAssistant, entry: ConfigEntry, dry_run: bool) -> SwitchResult:
    """Point the processor back at the Control4 server, restore its entities and disable this integration."""
    client = entry.runtime_data.client
    host = entry.data[CONF_HOST]
    c4_ip = find_control4_host(hass, entry)
    if not c4_ip:
        return SwitchResult(False, message="No Control4 address found: set it under Configure > Settings.")

    async def failback() -> None:
        client.active = True

    result = await _repoint(entry, host, c4_ip, dry_run, None, failback)
    result.steps.insert(0, f"Control4 address used: {c4_ip}")
    if result.ok:
        messages, needs_reload = await async_release(hass, dry_run)
        result.steps.append("--- entity ids ---")
        result.steps.extend(messages)
        if not dry_run:
            client.active = False
            if needs_reload:
                for c4 in hass.config_entries.async_entries(CONTROL4_DOMAIN):
                    hass.config_entries.async_schedule_reload(c4.entry_id)
            _persist_mode(hass, entry, MODE_CONTROL4)
            result.steps.append(f"This integration will be disabled in {DISABLE_DELAY_SECONDS} s (re-enable it under Devices & Services).")

            def _disable() -> None:
                hass.async_create_task(hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER))

            hass.loop.call_later(DISABLE_DELAY_SECONDS, _disable)
    if not dry_run:
        await _notify(hass, entry, "Crestron: back to Control4", result)
    return result


async def async_refresh_from_processor(hass: HomeAssistant, entry: ConfigEntry, dry_run: bool, seconds: int) -> dict:
    """Get a fresh state dump without a reboot: go silent until the processor drops the session, then reconnect.

    UNVERIFIED on the real processor (lessons learned section 19). Starts in the background and returns at once.
    """
    data = entry.runtime_data
    client = data.client
    plan = (
        f"Home Assistant goes silent for {seconds} s (no keep-alive, no acks). The Crestron lights are unavailable from "
        f"Home Assistant meanwhile; wall keypads keep working. Then it reconnects and waits up to {REFRESH_WAIT_SECONDS} s "
        "for the processor's hello and state dump."
    )
    if entry.options.get(CONF_MODE) != MODE_STANDALONE:
        return {"ok": False, "message": "Only possible in stand-alone mode (in Control4 mode the processor talks to Control4)."}
    if data.switching or data.refresh_status in (REFRESH_PAUSED, REFRESH_WAITING):
        return {"ok": False, "message": "A switch or a refresh is already running."}
    if dry_run:
        return {"ok": True, "message": "Dry run: " + plan}

    def _status(value: str) -> None:
        data.refresh_status = value
        async_dispatcher_send(hass, SIGNAL_LABELS_UPDATED)

    async def _run() -> None:
        hellos = client.hellos
        try:
            client.pause(seconds)
            _status(REFRESH_PAUSED)
            await asyncio.sleep(seconds)
            client.resume()
            _status(REFRESH_WAITING)
            deadline = time.monotonic() + REFRESH_WAIT_SECONDS
            while client.hellos == hellos and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
        finally:
            client.resume()
        if client.hellos != hellos:
            _status(REFRESH_DUMP_RECEIVED)
            await async_notify(hass, entry, "Crestron: refreshed", "The processor sent a fresh state dump; the light levels are current.",
                               "Crestron lights refreshed from the processor", REFRESH_NOTIFICATION_ID)
        else:
            _status(REFRESH_NO_DUMP)
            text = (f"No state dump after a {seconds} s pause: the processor kept the session. Try a longer pause; "
                    "the lights are back under Home Assistant control with their previous levels.")
            await async_notify(hass, entry, "Crestron: no fresh state", text, "Crestron refresh: no state dump, try a longer pause",
                               REFRESH_NOTIFICATION_ID)

    _status(REFRESH_PAUSED)
    data.refresh_task = entry.async_create_background_task(hass, _run(), "crestron_pac2m_refresh")
    return {"ok": True, "message": "Started: " + plan}
