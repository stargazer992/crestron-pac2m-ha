"""Config flow (add the integration) and options flow (switch between Control4 and stand-alone)."""
from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    ADOPT_DRY_RUN_DEFAULT,
    CONF_CONTROL4_HOST,
    CONF_HA_HOST,
    CONF_HOST,
    CONF_MODE,
    CONF_NOTIFY_SERVICE,
    DEFAULT_HOST,
    DEFAULT_MODE,
    DOMAIN,
    NOTIFY_SERVICE,
)
from .operations import async_go_standalone, async_return_to_control4, find_control4_host


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


class CrestronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adding the integration never changes the processor (it starts in control4 mode, passive)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not _valid_ip(host):
                errors[CONF_HOST] = "invalid_host"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Crestron PAC2M ({host})", data={CONF_HOST: host}, options={CONF_MODE: DEFAULT_MODE})
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_HOST, default=DEFAULT_HOST): str}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CrestronOptionsFlow()


class CrestronOptionsFlow(OptionsFlow):
    """Menu: go stand-alone / return to Control4 / settings."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._action = ""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["standalone", "control4", "settings"],
            description_placeholders={"mode": self.config_entry.options.get(CONF_MODE, DEFAULT_MODE)},
        )

    # ---- the two switches share one form + progress + result pattern ----
    async def _run(self, step_id: str, user_input: dict[str, Any] | None, operation) -> ConfigFlowResult:
        if self._task is None:
            if user_input is None:
                return self.async_show_form(
                    step_id=step_id,
                    data_schema=vol.Schema({vol.Required("dry_run", default=ADOPT_DRY_RUN_DEFAULT): bool}),
                    description_placeholders={
                        "control4_host": find_control4_host(self.hass, self.config_entry) or "not found",
                        "mode": self.config_entry.options.get(CONF_MODE, DEFAULT_MODE),
                    },
                )
            self._task = self.hass.async_create_task(operation(self.hass, self.config_entry, user_input["dry_run"]))
        if not self._task.done():
            return self.async_show_progress(step_id=step_id, progress_action="switching", progress_task=self._task)
        return self.async_show_progress_done(next_step_id="result")

    async def async_step_standalone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._run("standalone", user_input, async_go_standalone)

    async def async_step_control4(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._run("control4", user_input, async_return_to_control4)

    async def async_step_result(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        result = self._task.result() if self._task is not None else None
        text = "nothing happened" if result is None else result.message + "\n\n" + "\n".join(result.steps)
        return self.async_abort(reason="result", description_placeholders={"result": text})

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        options = self.config_entry.options
        current = {
            CONF_CONTROL4_HOST: options.get(CONF_CONTROL4_HOST, ""),
            CONF_HA_HOST: options.get(CONF_HA_HOST, ""),
            CONF_NOTIFY_SERVICE: options.get(CONF_NOTIFY_SERVICE, NOTIFY_SERVICE),
        }
        if user_input is not None:
            # a cleared field is left out of user_input, so every key is rebuilt (missing = empty)
            current = {key: str(user_input.get(key, "")).strip() for key in current}
            current[CONF_NOTIFY_SERVICE] = current[CONF_NOTIFY_SERVICE].removeprefix("notify.")
            for key in (CONF_CONTROL4_HOST, CONF_HA_HOST):
                if current[key] and not _valid_ip(current[key]):
                    errors[key] = "invalid_host"
            if not errors:
                return self.async_create_entry(title="", data={**options, **current})
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {vol.Optional(key, description={"suggested_value": value}): str for key, value in current.items()}
            ),
            errors=errors,
        )
