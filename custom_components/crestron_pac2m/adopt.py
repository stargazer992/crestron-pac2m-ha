"""Hands the existing Home Assistant light entity ids to this integration (and back).

adopt  : old Control4 light  light.x  -> light.x_control4_old (disabled)
         new Crestron light            -> light.x  (copies name, icon, area, labels, voice exposure)
release: the reverse - the Crestron lights get light.x_crestron, the Control4 lights are renamed back and enabled.

Dashboards, scenes and automations that use light.x keep working in both directions.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LEGACY_PLATFORM, LEGACY_SUFFIX, LIGHTS, RELEASED_SUFFIX
from .lights import LightRow

_LOGGER = logging.getLogger(__name__)


def unique_id_for(join: int) -> str:
    """Unique id of the Crestron light entity for an analog join."""
    return f"{DOMAIN}_{join:04x}"


def _copy_attributes(hass: HomeAssistant, old: er.RegistryEntry) -> dict:
    """Name, icon, area and labels of the old entity (area falls back to its device's area)."""
    area_id = old.area_id
    if area_id is None and old.device_id:
        device = dr.async_get(hass).async_get(old.device_id)
        area_id = device.area_id if device else None
    attrs: dict = {}
    if old.name:
        attrs["name"] = old.name
    if old.icon:
        attrs["icon"] = old.icon
    if area_id:
        attrs["area_id"] = area_id
    if old.labels:
        attrs["labels"] = set(old.labels)
    return attrs


async def async_adopt(hass: HomeAssistant, dry_run: bool, lights: tuple[LightRow, ...] = LIGHTS) -> tuple[list[str], bool]:
    """Take over the legacy entity ids. Returns (messages, any_change)."""
    registry = er.async_get(hass)
    messages: list[str] = []
    changed = False
    for join, legacy_id, _kind, _room, _label in lights:
        try:
            new_id = registry.async_get_entity_id("light", DOMAIN, unique_id_for(join))
            if new_id is None:
                messages.append(f"{legacy_id}: Crestron entity not found (is the integration loaded?)")
                continue
            if new_id == legacy_id:
                messages.append(f"{legacy_id}: already adopted")
                continue
            old = registry.async_get(legacy_id)
            attrs: dict = {}
            if old is not None:
                if old.platform != LEGACY_PLATFORM:
                    messages.append(f"{legacy_id}: held by platform '{old.platform}', skipped")
                    continue
                parked_id = legacy_id + LEGACY_SUFFIX
                if registry.async_get(parked_id) is not None:
                    messages.append(f"{legacy_id}: {parked_id} already exists, skipped")
                    continue
                attrs = _copy_attributes(hass, old)
                messages.append(f"{legacy_id}: Control4 entity -> {parked_id} (disabled); Crestron {new_id} -> {legacy_id}")
                if not dry_run:
                    update: dict = {"new_entity_id": parked_id}
                    if old.disabled_by is None:
                        update["disabled_by"] = er.RegistryEntryDisabler.USER
                    registry.async_update_entity(legacy_id, **update)
            else:
                messages.append(f"{legacy_id}: free, Crestron {new_id} -> {legacy_id}")
            if not dry_run:
                registry.async_update_entity(new_id, new_entity_id=legacy_id, **attrs)
                if old is not None:
                    for domain_key, value in old.options.items():       # voice assistant exposure etc.
                        registry.async_update_entity_options(legacy_id, domain_key, dict(value))
            changed = True
        except Exception as err:  # one bad entity must not stop the rest
            _LOGGER.exception("Adopt failed for %s", legacy_id)
            messages.append(f"{legacy_id}: ERROR {err}")
    return messages, changed


async def async_release(hass: HomeAssistant, dry_run: bool, lights: tuple[LightRow, ...] = LIGHTS) -> tuple[list[str], bool]:
    """Give the entity ids back to Control4. Returns (messages, needs_control4_reload)."""
    registry = er.async_get(hass)
    messages: list[str] = []
    needs_reload = False
    for join, legacy_id, _kind, _room, _label in lights:
        try:
            new_id = registry.async_get_entity_id("light", DOMAIN, unique_id_for(join))
            parked_id = legacy_id + LEGACY_SUFFIX
            parked = registry.async_get(parked_id)
            if parked is None or parked.platform != LEGACY_PLATFORM:
                messages.append(f"{legacy_id}: no parked Control4 entity ({parked_id}), nothing to restore")
                continue
            released_id = legacy_id + RELEASED_SUFFIX
            messages.append(f"{legacy_id}: Crestron {new_id} -> {released_id}; Control4 {parked_id} -> {legacy_id} (enabled)")
            if not dry_run:
                if new_id is not None and new_id == legacy_id:
                    registry.async_update_entity(new_id, new_entity_id=released_id)
                update: dict = {"new_entity_id": legacy_id}
                if parked.disabled_by == er.RegistryEntryDisabler.USER:
                    update["disabled_by"] = None
                registry.async_update_entity(parked_id, **update)
            needs_reload = True
        except Exception as err:
            _LOGGER.exception("Release failed for %s", legacy_id)
            messages.append(f"{legacy_id}: ERROR {err}")
    return messages, needs_reload
