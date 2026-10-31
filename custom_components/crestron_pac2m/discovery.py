"""Find the processor on the local network: every host with a telnet console whose prompt looks like a Crestron 2-series.

Read only: it opens the console, reads the banner/prompt and closes it. No UDP is sent, so a processor that
Control4 owns is not disturbed (adding the integration must never touch the lights side).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re

from homeassistant.components.network import async_get_adapters
from homeassistant.core import HomeAssistant

from .const import CONSOLE_PORT

_LOGGER = logging.getLogger(__name__)
_TELNET_NEGOTIATION = re.compile(rb"\xff[\xfb-\xfe].")
_CRESTRON_PROMPT = re.compile(r"(?m)^\s*((?:PAC2|CP2|PRO2|AV2|MC2|RMC|DIN-AP)[A-Za-z0-9_-]*)>\s*$")
SCAN_CONNECT_TIMEOUT = 0.6       # per host; the real processor answers in well under 100 ms
SCAN_BANNER_SECONDS = 1.5        # wait this long for the prompt after connecting
SCAN_PARALLEL = 64
SCAN_MAX_PREFIX = 24             # bigger networks are scanned as the /24 around Home Assistant's address


async def async_candidate_hosts(hass: HomeAssistant) -> list[str]:
    """Every host on Home Assistant's enabled IPv4 networks (each capped at a /24)."""
    hosts: list[str] = []
    for adapter in await async_get_adapters(hass):
        if not adapter["enabled"]:
            continue
        for addr in adapter["ipv4"]:
            own = ipaddress.IPv4Address(addr["address"])
            if own.is_loopback or own.is_link_local:
                continue
            net = ipaddress.IPv4Network(f"{own}/{max(addr['network_prefix'], SCAN_MAX_PREFIX)}", strict=False)
            hosts += [str(ip) for ip in net.hosts() if ip != own and str(ip) not in hosts]
    return hosts


async def async_identify(host: str, port: int | None = None) -> str | None:
    """The model from the console prompt (e.g. "PAC2M") if host is a Crestron 2-series, else None."""
    port = CONSOLE_PORT if port is None else port
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), SCAN_CONNECT_TIMEOUT)
    except (OSError, TimeoutError):
        return None
    try:
        loop = asyncio.get_running_loop()
        end = loop.time() + SCAN_BANNER_SECONDS
        out = b""
        while (left := end - loop.time()) > 0:
            try:
                chunk = await asyncio.wait_for(reader.read(1024), left)
            except (OSError, TimeoutError):
                break
            if not chunk:
                break
            out += chunk
            if match := _CRESTRON_PROMPT.search(_TELNET_NEGOTIATION.sub(b"", out).decode("latin-1").replace("\r", "")):
                return match.group(1)
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def async_scan(hosts: list[str], port: int | None = None) -> dict[str, str]:
    """{ip: model} for every host that is a Crestron 2-series, in address order."""
    limit = asyncio.Semaphore(SCAN_PARALLEL)

    async def _one(host: str) -> tuple[str, str | None]:
        async with limit:
            return host, await async_identify(host, port)

    results = await asyncio.gather(*(_one(h) for h in hosts), return_exceptions=True)   # one odd host must not end the scan
    found = {r[0]: r[1] for r in results if isinstance(r, tuple) and r[1]}
    _LOGGER.debug("Crestron scan of %d hosts found %s", len(hosts), found or "nothing")
    return found
