"""Repairs: the fix for "processor points at another address" runs Go stand-alone again."""
from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import ISSUE_ADDRESS
from .operations import async_go_standalone


class AddressFixFlow(RepairsFlow):
    """Confirm, then re-point the processor at Home Assistant's current address (reboots the processor)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._task: asyncio.Task | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        entry = self.hass.config_entries.async_get_entry(str(self._data.get("entry_id", "")))
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        if self._task is None:
            if user_input is None:
                placeholders = {key: str(self._data.get(key, "?")) for key in ("cip_id", "table_ip", "ha_ip")}
                return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}), description_placeholders=placeholders)
            self._task = self.hass.async_create_task(async_go_standalone(self.hass, entry, False))
        if not self._task.done():
            return self.async_show_progress(step_id="confirm", progress_action="switching", progress_task=self._task)
        return self.async_show_progress_done(next_step_id="done")

    async def async_step_done(self, user_input: dict[str, Any] | None = None) -> data_entry_flow.FlowResult:
        result = self._task.result() if self._task is not None else None
        if result is not None and result.ok:
            return self.async_create_entry(data={})
        return self.async_abort(reason="failed", description_placeholders={"result": "nothing happened" if result is None else result.message})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    if issue_id == ISSUE_ADDRESS:
        return AddressFixFlow(data or {})
    return ConfirmRepairFlow()
