"""Moves the processor's IP table entry between Control4 and Home Assistant. No Home Assistant imports.

repoint() is the one routine used by both directions:
  1. read the IP table, 2. REMSlave the old entry, 3. ADDSlave the new one, 4. verify the table,
  5. reboot (mandatory), 6. wait for the console, 7. wait for ONLINE (+ an optional extra readiness check),
  8. if the new peer never comes up, undo everything (AUTO_FAILBACK) so the lights are never stranded.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import console as console_mod
from .console import ConsoleError, CrestronConsole, IptEntry
from .const import (
    AUTO_FAILBACK,
    CIP_ID,
    CONSOLE_BACK_SECONDS,
    CONSOLE_PORT,
    POLL_SECONDS,
    REBOOT_SETTLE_SECONDS,
    WAIT_READY_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SwitchResult:
    """Outcome of a switch. ok=True means the processor now points at the target and is ONLINE."""

    ok: bool
    changed: bool = False                  # the table was (or would be) changed
    rolled_back: bool = False
    message: str = ""
    steps: list[str] = field(default_factory=list)


async def _wait_console(host: str) -> CrestronConsole | None:
    """Wait for the console to return after a reboot."""
    loop = asyncio.get_running_loop()
    end = loop.time() + CONSOLE_BACK_SECONDS
    while loop.time() < end:
        con = CrestronConsole(host, CONSOLE_PORT)
        try:
            await con.async_connect()
            return con
        except ConsoleError:
            await con.async_close()
            await asyncio.sleep(POLL_SECONDS)
    return None


async def _wait_online(
    host: str, con: CrestronConsole, ready: Callable[[], Awaitable[bool]] | None
) -> tuple[bool, CrestronConsole | None]:
    """Wait for ONLINE in ipt and for the extra readiness check; reconnects if the console drops."""
    loop = asyncio.get_running_loop()
    end = loop.time() + WAIT_READY_SECONDS
    while loop.time() < end:
        try:
            entry = await con.async_read_entry()
        except ConsoleError:
            await con.async_close()
            con = CrestronConsole(host, CONSOLE_PORT)
            try:
                await con.async_connect()
            except ConsoleError:
                await asyncio.sleep(POLL_SECONDS)
                continue
            entry = await con.async_read_entry()
        if entry is not None and entry.status == "ONLINE" and (ready is None or await ready()):
            return True, con
        await asyncio.sleep(POLL_SECONDS)
    return False, con


async def _apply(con: CrestronConsole, old: IptEntry | None, target_ip: str, steps: list[str]) -> IptEntry | None:
    """REMSlave the old entry and ADDSlave the target; return the table row afterwards."""
    if old is not None and old.ip != target_ip:
        steps.append(f"remslave {CIP_ID} {old.ip}")
        steps.append("   " + (await con.async_run(f"remslave {CIP_ID} {old.ip}")).strip().replace("\n", " | "))
    steps.append(f"addslave {CIP_ID} {target_ip}")
    steps.append("   " + (await con.async_run(f"addslave {CIP_ID} {target_ip}")).strip().replace("\n", " | "))
    return await con.async_read_entry()


async def _reboot_and_wait(
    host: str, con: CrestronConsole, ready: Callable[[], Awaitable[bool]] | None, steps: list[str]
) -> tuple[bool, CrestronConsole | None]:
    """Reboot, then wait for console and ONLINE. Returns (online, console or None)."""
    steps.append("reboot (about 2 minutes; lights keep their levels, wall keypads keep working)")
    try:
        await con.async_run("reboot", wait=3.0)
    except ConsoleError:
        pass                                           # the console may drop while rebooting
    await con.async_close()
    await asyncio.sleep(REBOOT_SETTLE_SECONDS)
    con2 = await _wait_console(host)
    if con2 is None:
        steps.append("console did not come back in time")
        return False, None
    steps.append("console is back")
    online, con2 = await _wait_online(host, con2, ready)
    steps.append("entry is ONLINE" if online else "entry did not reach ONLINE in time")
    return online, con2


async def repoint(
    host: str,
    target_ip: str,
    dry_run: bool,
    ready: Callable[[], Awaitable[bool]] | None = None,
    on_failback: Callable[[], Awaitable[None]] | None = None,
) -> SwitchResult:
    """Point the processor's CIP entry at target_ip. See the module docstring."""
    steps: list[str] = []
    con = CrestronConsole(host, CONSOLE_PORT)
    try:
        await con.async_connect()
        old = await con.async_read_entry()
        steps.append(f"IP table now: {old.status + ' ' + old.ip if old else 'no CIP_ID ' + CIP_ID + ' row'}")
        if old is not None and old.ip == target_ip and old.status == "ONLINE" and (ready is None or await ready()):
            return SwitchResult(True, False, False, f"Already pointing at {target_ip} and ONLINE; nothing to do.", steps)

        if dry_run:
            if old is not None and old.ip != target_ip:
                steps.append(f"WOULD run: remslave {CIP_ID} {old.ip}")
            steps.append(f"WOULD run: addslave {CIP_ID} {target_ip}")
            steps.append("WOULD reboot the processor and wait for ONLINE (about 2-4 minutes)")
            return SwitchResult(True, True, False, "Dry run only: nothing was changed.", steps)

        new_row = await _apply(con, old, target_ip, steps)
        if new_row is None or new_row.ip != target_ip:
            # restore what was there so we never leave the table half edited
            if old is not None:
                await _apply(con, new_row, old.ip, steps)
            return SwitchResult(False, False, False, "The IP table did not accept the new address; the old entry was restored.", steps)

        online, con = await _reboot_and_wait(host, con, ready, steps)
        if online:
            return SwitchResult(True, True, False, f"Processor now points at {target_ip} and is ONLINE.", steps)

        # ---- the new peer never came up: undo ----
        if AUTO_FAILBACK and old is not None and old.ip != target_ip:
            steps.append(f"FAILBACK: restoring {old.ip}")
            if on_failback is not None:
                await on_failback()
            if con is None:
                con = await _wait_console(host)
            if con is not None:
                row = await con.async_read_entry()
                await _apply(con, row, old.ip, steps)
                back, con = await _reboot_and_wait(host, con, None, steps)
                return SwitchResult(False, True, True, f"{target_ip} never came up; restored {old.ip} ({'ONLINE' if back else 'not yet ONLINE - check the processor'}).", steps)
        return SwitchResult(False, True, False, f"{target_ip} did not come up and no failback was possible. Run crestron_rollback.py.", steps)
    except ConsoleError as err:
        steps.append(f"console error: {err}")
        return SwitchResult(False, False, False, str(err), steps)
    except Exception as err:  # noqa: BLE001 - report instead of crashing the flow
        _LOGGER.exception("Unexpected error while re-pointing the processor")
        steps.append(f"unexpected error: {err}")
        return SwitchResult(False, False, False, f"Unexpected error: {err}", steps)
    finally:
        if con is not None:
            await con.async_close()
