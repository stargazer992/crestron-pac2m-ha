"""Wall keypads: digital joins from the processor as event entities."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CrestronConfigEntry
from .const import DOMAIN, KEYPAD_BUTTONS


async def async_setup_entry(hass: HomeAssistant, entry: CrestronConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    entities: list[EventEntity] = [CrestronDigitalJoin(entry)]
    entities += [CrestronKeypadButton(entry, join, name) for join, name in KEYPAD_BUTTONS.items()]
    async_add_entities(entities)


class _CrestronEvent(EventEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: CrestronConfigEntry) -> None:
        self._client = entry.runtime_data.client
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Crestron PAC2M", manufacturer="Crestron", model="PAC2M")

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_digital_listener(self._digital))

    @callback
    def _digital(self, join: int, level: int) -> None:
        raise NotImplementedError


class CrestronDigitalJoin(_CrestronEvent):
    """Every digital join (keypad presses and light feedback pairs) not mapped in KEYPAD_BUTTONS; the join is an attribute."""

    _attr_name = "Digital join"

    def __init__(self, entry: CrestronConfigEntry) -> None:
        super().__init__(entry)
        self._attr_event_types = ["high", "low"]
        self._attr_unique_id = f"{entry.entry_id}_digital"

    @callback
    def _digital(self, join: int, level: int) -> None:
        if join in KEYPAD_BUTTONS:
            return
        self._trigger_event("high" if level else "low", {"join": join, "join_hex": f"0x{join:04x}"})
        self.async_write_ha_state()


class CrestronKeypadButton(_CrestronEvent):
    """One mapped keypad button: press (join high) and release (join low)."""

    def __init__(self, entry: CrestronConfigEntry, join: int, name: str) -> None:
        super().__init__(entry)
        self._attr_event_types = ["press", "release"]
        self._join = join
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_button_{join:04x}"

    @callback
    def _digital(self, join: int, level: int) -> None:
        if join != self._join:
            return
        self._trigger_event("press" if level else "release", {"join_hex": f"0x{join:04x}"})
        self.async_write_ha_state()
