"""Async client for the Crestron telnet console (port 23, no password). No Home Assistant imports.

Learned on the real processor:
  * commands need CRLF line endings
  * only one console session works at a time (a second one is ignored)
  * REMSlave / ADDSlave only take effect after the processor REBOOTS ("Master List set. Reboot to take effect.")
  * until the reboot the entry shows a blank type and NOT REG
  * ipt prints addresses zero padded (192.168.104.025)
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from .const import (
    CIP_ID,
    CONSOLE_COMMAND_WAIT,
    CONSOLE_CONNECT_TIMEOUT,
    CONSOLE_PORT,
    CONSOLE_PROMPT_REGEX,
)

_LOGGER = logging.getLogger(__name__)
_TELNET_NEGOTIATION = re.compile(rb"\xff[\xfb-\xfe].")
_STATUS_WORDS = ("NOT REG", "ONLINE", "OFFLINE", "UNKNOWN")


class ConsoleError(Exception):
    """The console could not be used."""


@dataclass(frozen=True)
class IptEntry:
    """The CIP_ID row of the IP table."""

    status: str            # ONLINE | OFFLINE | NOT REG | UNKNOWN
    port: str
    ip: str                # normalised (192.168.104.25)
    raw: str               # full ipt output for the log


def normalise_ip(text: str) -> str:
    """192.168.104.025 -> 192.168.104.25"""
    try:
        return ".".join(str(int(part)) for part in text.split("."))
    except ValueError:
        return text


def parse_ipt(raw: str, cip_id: str = CIP_ID) -> IptEntry | None:
    """Find the CIP_ID row. Handles ONLINE, OFFLINE and the blank-type NOT REG row."""
    for line in raw.replace("\r", "").split("\n"):
        match = re.match(rf"^\s*{re.escape(cip_id)}\s+(.*?)\s+(\d+)\s+([\d.]+)\s*$", line)
        if not match:
            continue
        status = "UNKNOWN"
        for word in _STATUS_WORDS:
            if word in match.group(1):
                status = word
                break
        return IptEntry(status, match.group(2), normalise_ip(match.group(3)), raw)
    return None


class CrestronConsole:
    """One telnet console session."""

    def __init__(self, host: str, port: int = CONSOLE_PORT) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def async_connect(self) -> None:
        """Open the console and swallow the banner."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), CONSOLE_CONNECT_TIMEOUT
            )
        except (TimeoutError, OSError) as err:
            raise ConsoleError(f"Cannot reach the Crestron console at {self._host}:{self._port}: {err}") from err
        banner = await self._read(1.5)
        if not banner and self._reader.at_eof():
            await self.async_close()
            raise ConsoleError("The console closed the connection immediately (processor busy or rebooting)")

    async def _read(self, seconds: float, stop_at_prompt: bool = False) -> str:
        assert self._reader is not None
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        out = b""
        while loop.time() < end:
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout=min(0.5, max(0.05, end - loop.time())))
            except TimeoutError:
                chunk = b""
            except OSError as err:
                raise ConsoleError(str(err)) from err
            if chunk:
                out += chunk
            elif self._reader.at_eof():
                break
            text = _TELNET_NEGOTIATION.sub(b"", out).decode("latin-1").replace("\r", "")
            if stop_at_prompt and out and re.search(CONSOLE_PROMPT_REGEX, text):
                break
        return _TELNET_NEGOTIATION.sub(b"", out).decode("latin-1").replace("\r", "")

    async def async_run(self, command: str, wait: float = CONSOLE_COMMAND_WAIT) -> str:
        """Send one command and return its output."""
        if self._writer is None:
            raise ConsoleError("Console is not connected")
        try:
            self._writer.write(command.encode() + b"\r\n")
            await self._writer.drain()
        except OSError as err:
            raise ConsoleError(str(err)) from err
        return await self._read(wait, stop_at_prompt=True)

    async def async_read_entry(self) -> IptEntry | None:
        """Return the CIP_ID row of the IP table."""
        return parse_ipt(await self.async_run("ipt"))

    async def async_close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = self._writer = None
