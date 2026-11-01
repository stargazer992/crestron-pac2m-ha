"""Config flow (add the integration) and options flow (switch between Control4 and stand-alone)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from . import discovery
from .const import (
    ADOPT_DRY_RUN_DEFAULT,
    CONF_CONTROL4_HOST,
    CONF_HA_HOST,
    CONF_HOST,
    CONF_KEYPAD_BUTTONS,
    CONF_MODE,
    CONF_NOTIFY_SERVICE,
    CONF_REFRESH_AFTER_RESTART,
    DEFAULT_HOST,
    DEFAULT_MODE,
    DOMAIN,
    KEEPALIVE_DIGITAL_JOIN,
    LEARN_TIMEOUT_SECONDS,
    NOTIFY_SERVICE,
)
from .lights import keypad_buttons
from .operations import (
    async_go_standalone,
    async_return_to_control4,
    find_control4_host,
)

_LOGGER = logging.getLogger(__name__)
SCAN_TOTAL_SECONDS = 15


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


class CrestronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Adding the integration never changes the processor (it starts in control4 mode, passive)."""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, str] | None = None      # {ip: model} from the network scan, done once per flow

    async def _async_found(self) -> dict[str, str]:
        if self._found is None:
            try:
                hosts = await discovery.async_candidate_hosts(self.hass)
                self._found = await asyncio.wait_for(discovery.async_scan(hosts), SCAN_TOTAL_SECONDS)
            except Exception:  # the scan is only a convenience; typing the address always works
                _LOGGER.exception("Crestron network scan failed")
                self._found = {}
        return self._found

    def _host_schema(self, suggested: str) -> vol.Schema:
        return vol.Schema({vol.Required(CONF_HOST, description={"suggested_value": suggested}): str})

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
        configured = {entry.data.get(CONF_HOST) for entry in self._async_current_entries()}
        found = {ip: model for ip, model in (await self._async_found()).items() if ip not in configured}
        suggested = (user_input or {}).get(CONF_HOST) or next(iter(found), DEFAULT_HOST)
        return self.async_show_form(
            step_id="user", data_schema=self._host_schema(suggested), errors=errors,
            description_placeholders={"found": _found_text(found)},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the processor address (e.g. it got a new IP). Nothing is sent to the processor."""
        entry = self._get_reconfigure_entry()
        current = entry.data[CONF_HOST]
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            if not _valid_ip(host):
                errors[CONF_HOST] = "invalid_host"
            elif any(other.entry_id != entry.entry_id and host in (other.unique_id, other.data.get(CONF_HOST))
                     for other in self._async_current_entries()):
                errors[CONF_HOST] = "already_configured"
            else:
                title = f"Crestron PAC2M ({host})" if entry.title == f"Crestron PAC2M ({current})" else entry.title   # keep a renamed title
                return self.async_update_reload_and_abort(entry, unique_id=host, title=title, data_updates={CONF_HOST: host})
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._host_schema((user_input or {}).get(CONF_HOST) or current), errors=errors,
            description_placeholders={"found": _found_text(await self._async_found()), "current": current},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CrestronOptionsFlow()


def _found_text(found: dict[str, str]) -> str:
    return ", ".join(f"{model} at {ip}" for ip, model in found.items()) or "nothing (type the address)"


class CrestronOptionsFlow(OptionsFlow):
    """Menu: go stand-alone / return to Control4 / learn or remove keypad buttons / settings."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._action = ""
        self._learn_task: asyncio.Task | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["standalone", "control4", "learn", "buttons", "settings"],
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

    # ---- keypad buttons ----
    async def _wait_for_press(self) -> int | None:
        """The next digital join that goes high and has no name yet (None after LEARN_TIMEOUT_SECONDS)."""
        client = self.config_entry.runtime_data.client
        known = keypad_buttons(self.config_entry)
        pressed: asyncio.Future[int] = self.hass.loop.create_future()

        @callback
        def _digital(join: int, level: int) -> None:
            if level and join != KEEPALIVE_DIGITAL_JOIN and join not in known and not pressed.done():
                pressed.set_result(join)

        remove = client.add_digital_listener(_digital)
        try:
            return await asyncio.wait_for(pressed, LEARN_TIMEOUT_SECONDS)
        except TimeoutError:
            return None
        finally:
            remove()

    async def async_step_learn(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._learn_task is None:
            if not self.config_entry.runtime_data.client.connected:
                return self.async_abort(reason="not_connected")
            self._learn_task = self.hass.async_create_task(self._wait_for_press())
        if not self._learn_task.done():
            return self.async_show_progress(
                step_id="learn",
                progress_action="learning",
                progress_task=self._learn_task,
                description_placeholders={"seconds": str(LEARN_TIMEOUT_SECONDS)},
            )
        return self.async_show_progress_done(next_step_id="learn_name")

    async def async_step_learn_name(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        join = self._learn_task.result() if self._learn_task is not None else None
        if join is None:
            return self.async_abort(reason="learn_timeout", description_placeholders={"seconds": str(LEARN_TIMEOUT_SECONDS)})
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get("name", "")).strip()
            if name:
                options = self.config_entry.options
                buttons = {**options.get(CONF_KEYPAD_BUTTONS, {}), f"0x{join:04x}": name}
                return self.async_create_entry(title="", data={**options, CONF_KEYPAD_BUTTONS: buttons})
            errors["name"] = "name_required"
        return self.async_show_form(
            step_id="learn_name",
            data_schema=vol.Schema({vol.Required("name", description={"suggested_value": f"Keypad button 0x{join:04X}"}): str}),
            description_placeholders={"join": f"0x{join:04X}"},
            errors=errors,
        )

    async def async_step_buttons(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        options = self.config_entry.options
        buttons: dict[str, str] = dict(options.get(CONF_KEYPAD_BUTTONS, {}))
        if not buttons:
            return self.async_abort(reason="no_buttons")
        if user_input is not None:
            remove = set(user_input.get("remove", []))
            kept = {join: name for join, name in buttons.items() if join not in remove}
            return self.async_create_entry(title="", data={**options, CONF_KEYPAD_BUTTONS: kept})
        choices = {join: f"{name} ({join})" for join, name in sorted(buttons.items())}
        return self.async_show_form(
            step_id="buttons",
            data_schema=vol.Schema({vol.Optional("remove", default=[]): cv.multi_select(choices)}),
        )

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
            refresh = bool(user_input.get(CONF_REFRESH_AFTER_RESTART, False))
            current = {key: str(user_input.get(key, "")).strip() for key in current}
            current[CONF_NOTIFY_SERVICE] = current[CONF_NOTIFY_SERVICE].removeprefix("notify.")
            for key in (CONF_CONTROL4_HOST, CONF_HA_HOST):
                if current[key] and not _valid_ip(current[key]):
                    errors[key] = "invalid_host"
            if not errors:
                return self.async_create_entry(title="", data={**options, **current, CONF_REFRESH_AFTER_RESTART: refresh})
        schema: dict = {vol.Optional(key, description={"suggested_value": value}): str for key, value in current.items()}
        schema[vol.Optional(CONF_REFRESH_AFTER_RESTART, default=bool(options.get(CONF_REFRESH_AFTER_RESTART, False)))] = bool
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
