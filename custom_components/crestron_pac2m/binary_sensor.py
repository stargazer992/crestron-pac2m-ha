"""Link status: is the processor session alive?"""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CrestronConfigEntry
from .const import CONF_MODE, DEFAULT_MODE, DOMAIN, SIGNAL_LABELS_UPDATED


async def async_setup_entry(hass: HomeAssistant, entry: CrestronConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([CrestronLink(entry)])


class CrestronLink(BinarySensorEntity):
    """On while the Crestron session is alive."""

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_name = "Link"

    def __init__(self, entry: CrestronConfigEntry) -> None:
        self._entry = entry
        self._client = entry.runtime_data.client
        self._attr_unique_id = f"{DOMAIN}_link"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Crestron PAC2M", manufacturer="Crestron", model="PAC2M")

    @property
    def is_on(self) -> bool:
        return self._client.connected

    @property
    def extra_state_attributes(self) -> dict:
        report = self._entry.runtime_data.label_report
        return {
            "mode": self._entry.options.get(CONF_MODE, DEFAULT_MODE),
            "frames_received": self._client.frames_rx,
            "frames_sent": self._client.frames_tx,
            "labels_known": len(self._client.labels),
            "label_mismatches": None if report is None else len(report.mismatched),
            "labels_missing": None if report is None else len(report.missing),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._client.add_link_listener(self._changed))
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_LABELS_UPDATED, self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
