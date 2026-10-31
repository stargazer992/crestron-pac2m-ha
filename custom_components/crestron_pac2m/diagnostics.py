"""Download diagnostics: link state, counters, levels, labels, recent frames and the processor's IP table entry."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.core import HomeAssistant

from . import CrestronConfigEntry
from .console import CrestronConsole

CONSOLE_READ_TIMEOUT = 10


async def _ip_table(host: str) -> dict[str, Any] | str:
    con = CrestronConsole(host)
    try:
        async with asyncio.timeout(CONSOLE_READ_TIMEOUT):
            await con.async_connect()
            row = await con.async_read_entry()
    except Exception as err:  # noqa: BLE001 - diagnostics must always download
        return f"not read: {err!r}"
    finally:
        await con.async_close()
    return "entry not found" if row is None else {"status": row.status, "port": row.port, "ip": row.ip}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: CrestronConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data
    client = data.client
    now = time.monotonic()
    report = data.label_report
    return {
        "entry": {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)},
        "link": {
            "connected": client.connected,
            "active": client.active,
            "paused": client.paused,
            "dump_complete": client.dump_complete,
            "seconds_since_last_frame": round(now - client.last_rx, 1) if client.last_rx else None,
            "frames_received": client.frames_rx,
            "frames_sent": client.frames_tx,
            "commands_resent": client.resends,
            "duplicates_ignored": client.duplicates,
            "hellos_received": client.hellos,
            "ignored_while_paused": client.ignored_while_paused,
            "refresh": data.refresh_status,
            "switching": data.switching,
        },
        "label_check": {
            "status": data.label_status,
            "mismatched": None if report is None else [list(m) for m in report.mismatched],
            "missing": None if report is None else [list(m) for m in report.missing],
        },
        "levels": {f"0x{join:04X}": level for join, level in sorted(client.levels.items())},
        "labels": {f"0x{join:04X}": text for join, text in sorted(client.labels.items())},
        "rooms": {str(name_id): text for name_id, text in sorted(client.room_names.items())},
        "recent_frames": [{"age_s": round(now - t, 2), "dir": d, "hex": h} for t, d, h in client.history],
        # read only, and skipped while a switch is using the console (it accepts one session at a time)
        "ip_table": "skipped: a switch is running" if data.switching else await _ip_table(client.host),
    }
