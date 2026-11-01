"""The light table: const.LIGHTS, or the optional crestron_pac2m_lights.yaml in the Home Assistant config folder."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.util.yaml import load_yaml

from .const import (
    CONF_KEYPAD_BUTTONS,
    KEYPAD_BUTTONS,
    KIND_DIMMER,
    KIND_RELAY,
    LIGHTS,
    LIGHTS_FILE,
)

type LightRow = tuple[int, str, str, str, str]      # (analog join, entity_id, kind, room, Crestron label)


def _join(value) -> int:
    join = int(value, 0) if isinstance(value, str) else int(value)
    if not 0 < join <= 0xFFFF:
        raise vol.Invalid(f"join {value} out of range")
    return join


def _entity_id(value) -> str:
    value = str(value)
    if not value.startswith("light.") or len(value) <= len("light."):
        raise vol.Invalid(f"{value} is not a light entity id")
    return value


def _kind(value) -> str:
    for kind in (KIND_DIMMER, KIND_RELAY):
        if str(value).strip().casefold() == kind.casefold():
            return kind
    raise vol.Invalid(f"kind must be {KIND_DIMMER} or {KIND_RELAY}")


ROW_SCHEMA = vol.Schema(
    {
        vol.Required("join"): _join,
        vol.Required("entity_id"): _entity_id,
        vol.Required("kind"): _kind,
        vol.Required("room"): str,
        vol.Required("label"): str,
    }
)


def parse_lights(rows) -> tuple[LightRow, ...]:
    """Validate the file's rows (vol.Invalid with the row number on errors)."""
    if not isinstance(rows, list) or not rows:
        raise vol.Invalid("the file must be a non-empty list of lights")
    table: list[LightRow] = []
    for number, row in enumerate(rows, 1):
        try:
            row = ROW_SCHEMA(row)
        except vol.Invalid as err:
            raise vol.Invalid(f"light {number}: {err}") from err
        table.append((row["join"], row["entity_id"], row["kind"], row["room"], row["label"]))
    for what, values in (("join", [r[0] for r in table]), ("entity_id", [r[1] for r in table])):
        if dupes := sorted({v for v in values if values.count(v) > 1}):
            raise vol.Invalid(f"duplicate {what}: " + ", ".join(f"0x{v:04X}" if isinstance(v, int) else v for v in dupes))
    return tuple(table)


def _load(path: Path) -> tuple[LightRow, ...] | None:
    if not path.is_file():
        return None
    return parse_lights(load_yaml(str(path)))


async def async_load_lights(hass: HomeAssistant) -> tuple[tuple[LightRow, ...], str]:
    """(table, source). Raises vol.Invalid or HomeAssistantError if the file exists but is wrong."""
    path = Path(hass.config.path(LIGHTS_FILE))
    table = await hass.async_add_executor_job(_load, path)
    return (LIGHTS, "built-in") if table is None else (table, LIGHTS_FILE)


def keypad_buttons(entry) -> dict[int, str]:
    """Named keypad buttons: const.KEYPAD_BUTTONS plus the ones learned under Configure (options win)."""
    learned = {int(join, 16): name for join, name in entry.options.get(CONF_KEYPAD_BUTTONS, {}).items()}
    return {**KEYPAD_BUTTONS, **learned}
