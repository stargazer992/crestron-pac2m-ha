"""Light platform: one entity per Crestron dimmer or relay."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import CrestronConfigEntry
from .adopt import unique_id_for
from .client import CrestronClient
from .const import DOMAIN, KIND_DIMMER, LIGHTS, TURN_ON_RESTORES_LAST_LEVEL
from .protocol import LEVEL_FULL, brightness_to_level, level_to_brightness

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0                    # push based; commands are serialised inside the client


async def async_setup_entry(hass: HomeAssistant, entry: CrestronConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """Create all 40 lights."""
    client = entry.runtime_data.client
    async_add_entities(
        [CrestronLight(client, entry, join, legacy_id, kind, room, label) for join, legacy_id, kind, room, label in LIGHTS]
    )


class CrestronLight(LightEntity, RestoreEntity):
    """A Crestron dimmer (brightness) or relay (on/off)."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, client: CrestronClient, entry: CrestronConfigEntry, join: int, legacy_id: str, kind: str, room: str, label: str) -> None:
        self._client = client
        self._join = join
        self._last_level = LEVEL_FULL
        self._restored_level: int | None = None       # last known level from before a restart, used until the processor reports
        self._label = label
        self._attr_unique_id = unique_id_for(join)
        self._attr_name = f"{room} {label}"
        if kind == KIND_DIMMER:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Crestron PAC2M", manufacturer="Crestron", model="PAC2M"
        )

    @property
    def available(self) -> bool:
        """Available while the processor session is alive."""
        return self._client.connected

    @property
    def _level(self) -> int | None:
        level = self._client.levels.get(self._join)
        if level is None:
            level = self._restored_level
        if level is None and self._client.dump_complete:
            level = 0                                # not in the dump = off
        return level

    @property
    def is_on(self) -> bool | None:
        level = self._level
        return None if level is None else level > 0

    @property
    def brightness(self) -> int | None:
        level = self._level
        return None if level is None or self._attr_color_mode != ColorMode.BRIGHTNESS else level_to_brightness(level)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"crestron_join": f"0x{self._join:04X}", "crestron_label": self._client.labels.get(self._join, self._label), "crestron_level": self._level, "crestron_level_source": "processor" if self._join in self._client.levels else ("restored" if self._restored_level is not None else ("assumed_off" if self._client.dump_complete else None))}

    async def async_added_to_hass(self) -> None:
        # After a Home Assistant restart the processor does not repeat its state dump, so start from the last known state;
        # the first feedback from the processor replaces it.
        if (last := await self.async_get_last_state()) is not None and self._join not in self._client.levels:
            if last.state == "off":
                self._restored_level = 0
            elif last.state == "on":
                brightness = last.attributes.get(ATTR_BRIGHTNESS)
                self._restored_level = brightness_to_level(brightness) if brightness else LEVEL_FULL
                self._last_level = self._restored_level      # "turn on" returns to the level from before the restart
        self.async_on_remove(self._client.add_join_listener(self._join, self._join_changed))
        self.async_on_remove(self._client.add_link_listener(self._link_changed))

    @callback
    def _join_changed(self, join: int) -> None:
        level = self._level
        if level:
            self._last_level = level                 # remember the last non-zero level for "turn on"
        self.async_write_ha_state()

    @callback
    def _link_changed(self) -> None:
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs and self._attr_color_mode == ColorMode.BRIGHTNESS:
            level = brightness_to_level(kwargs[ATTR_BRIGHTNESS])
        elif TURN_ON_RESTORES_LAST_LEVEL and self._attr_color_mode == ColorMode.BRIGHTNESS:
            level = self._last_level or LEVEL_FULL
        else:
            level = LEVEL_FULL
        await self._set(level)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(0)

    async def _set(self, level: int) -> None:
        try:
            await self._client.async_set_level(self._join, level)
        except ConnectionError as err:
            raise HomeAssistantError(f"Crestron link is down: {err}") from err
