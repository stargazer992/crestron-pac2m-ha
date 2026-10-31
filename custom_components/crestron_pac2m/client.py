"""Async UDP client that behaves like the Control4 server toward the Crestron PAC2M.

No Home Assistant imports, so it can be tested on its own against a fake processor.

How the session works (verified against the real processor):
  * we send a ping (01 00) and digital join 4000 high every 30 s - without them the processor
    acks commands but silently ignores them
  * the processor answers with hello + a full state dump and feedback on every level change
  * every data frame from the processor must be acked with the same sequence number
  * the processor only talks to the IP in its IP table, so before the cutover nothing answers
    and the link simply stays "down" (entities unavailable)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from . import protocol
from .const import (
    COMMAND_SPACING_SECONDS,
    CRESTRON_PORT,
    KEEPALIVE_DIGITAL_JOIN,
    DUMP_SETTLE_SECONDS,
    KEEPALIVE_SECONDS,
    LISTEN_PORT,
    LOOP_TICK_SECONDS,
    RETRY_SECONDS,
    SESSION_TIMEOUT_SECONDS,
    SOURCE_PORT,
)

_LOGGER = logging.getLogger(__name__)

LinkCallback = Callable[[], None]
JoinCallback = Callable[[int], None]


class _Endpoint(asyncio.DatagramProtocol):
    """Datagram endpoint that hands everything to the client."""

    def __init__(self, client: CrestronClient, label: str) -> None:
        self._client = client
        self._label = label

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        try:
            self._client.handle_datagram(data, addr, self._label)
        except Exception:  # noqa: BLE001 - one bad packet must never stop the session
            _LOGGER.exception("Error handling a datagram from %s", addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error on %s socket: %s", self._label, exc)


class CrestronClient:
    """One session with one processor."""

    def __init__(
        self,
        host: str,
        port: int = CRESTRON_PORT,
        listen_port: int = LISTEN_PORT,
        source_port: int = SOURCE_PORT,
        active: bool = True,
    ) -> None:
        self.host = host
        self.active = active                       # False = passive: listen and ack only, never send unsolicited traffic
        self.port = port
        self.listen_port = listen_port
        self.source_port = source_port
        self.levels: dict[int, int] = {}           # analog join -> 16-bit level
        self.labels: dict[int, str] = {}           # analog join -> Crestron light label (from the name dump)
        self.room_names: dict[int, str] = {}       # name id (even) -> room name; the room of a light is the entry at (its id - 1)
        self.connected = False
        self.dump_complete = False                 # True a few seconds after a hello: joins still without a level are at 0 (the dump omits them)
        self.dump_in_progress = False              # True from a hello until the dump has settled (digital events are held back)
        self._dump_timer: asyncio.TimerHandle | None = None
        self._digital_listeners: set[Callable[[int, int], None]] = set()   # callback(join, 0/1) for every digital join change
        self.last_rx = 0.0                         # monotonic time of the last frame from the processor
        self.frames_rx = 0
        self.frames_tx = 0
        self._last_keepalive = -1e9
        self._seq = 0
        self._rx: asyncio.DatagramTransport | None = None
        self._tx: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()
        self._link_listeners: set[LinkCallback] = set()
        self._join_listeners: dict[int, set[JoinCallback]] = {}

    def room_for_join(self, join: int) -> str | None:
        """Room name the processor reports for a light: the even-id entry just before the light's own (odd) id."""
        return self.room_names.get(2 * (join - protocol.JOIN_BASE) - 2)

    # ---------- listeners ----------
    def add_link_listener(self, callback: LinkCallback) -> Callable[[], None]:
        """Call callback whenever the link goes up or down. Returns the remove function."""
        self._link_listeners.add(callback)
        return lambda: self._link_listeners.discard(callback)

    def add_digital_listener(self, callback: Callable[[int, int], None]) -> Callable[[], None]:
        """Call callback(join, level) for every digital join the processor sends (keypad presses, feedback pairs). Returns the remove function."""
        self._digital_listeners.add(callback)
        return lambda: self._digital_listeners.discard(callback)

    def add_join_listener(self, join: int, callback: JoinCallback) -> Callable[[], None]:
        """Call callback(join) whenever the level of join changes. Returns the remove function."""
        self._join_listeners.setdefault(join, set()).add(callback)
        return lambda: self._join_listeners.get(join, set()).discard(callback)

    def notify_link(self) -> None:
        for callback in list(self._link_listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - keep notifying the others
                _LOGGER.exception("Link listener failed")

    def _notify_join(self, join: int) -> None:
        for callback in list(self._join_listeners.get(join, ())):
            try:
                callback(join)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Join listener failed for join 0x%04X", join)

    # ---------- lifecycle ----------
    async def async_start(self) -> None:
        """Open both sockets and start the keep-alive loop. Raises OSError if the listen port is taken."""
        loop = asyncio.get_running_loop()
        self._rx, _ = await loop.create_datagram_endpoint(
            lambda: _Endpoint(self, "feedback"), local_addr=("0.0.0.0", self.listen_port)
        )
        try:
            self._tx, _ = await loop.create_datagram_endpoint(
                lambda: _Endpoint(self, "command"), local_addr=("0.0.0.0", self.source_port)
            )
        except OSError:
            _LOGGER.debug("Source port %s busy, using a random one", self.source_port)
            self._tx, _ = await loop.create_datagram_endpoint(
                lambda: _Endpoint(self, "command"), local_addr=("0.0.0.0", 0)
            )
        self._task = asyncio.create_task(self._run(), name="crestron_pac2m_keepalive")

    @property
    def listening(self) -> bool:
        """True when the feedback socket is open (required before the processor is pointed at us)."""
        return self._rx is not None

    async def async_stop(self) -> None:
        """Stop the loop and close the sockets."""
        if self._dump_timer is not None:
            self._dump_timer.cancel()
            self._dump_timer = None
        self.dump_in_progress = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        for transport in (self._rx, self._tx):
            if transport is not None:
                transport.close()
        self._rx = self._tx = None
        await asyncio.sleep(0)                     # let the loop finish closing the sockets so a reload can rebind the port
        await asyncio.sleep(0)
        self._set_connected(False)

    # ---------- keep-alive loop ----------
    async def _run(self) -> None:
        """Session loop: keep-alive and silence timeout."""
        while True:
            try:
                now = time.monotonic()
                if self.active:
                    interval = KEEPALIVE_SECONDS if self.connected else RETRY_SECONDS
                    if now - self._last_keepalive >= interval:
                        self._last_keepalive = now
                        await self._send_keepalive()
                if self.connected and now - self.last_rx > SESSION_TIMEOUT_SECONDS:
                    _LOGGER.warning("No frame from the Crestron for %s s, marking lights unavailable", SESSION_TIMEOUT_SECONDS)
                    self._set_connected(False)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the loop die
                _LOGGER.exception("Session loop error, continuing")
            await asyncio.sleep(LOOP_TICK_SECONDS)

    async def _send_keepalive(self) -> None:
        await self._send_payload(protocol.PAYLOAD_PING)
        await self._send_payload(protocol.digital_payload(KEEPALIVE_DIGITAL_JOIN, True))

    # ---------- sending ----------
    def _send_raw(self, packet: bytes) -> None:
        if self._tx is None:
            raise ConnectionError("Crestron client is not started")
        self._tx.sendto(packet, (self.host, self.port))   # unconnected socket: accept replies from any port
        self.frames_tx += 1

    async def _send_payload(self, payload: bytes) -> None:
        async with self._send_lock:
            packet = protocol.build_data(self._seq, payload)
            self._seq = (self._seq + 1) & 0xFF
            try:
                self._send_raw(packet)
            except OSError as err:
                _LOGGER.debug("Send failed: %s", err)
                return
            await asyncio.sleep(COMMAND_SPACING_SECONDS)

    async def async_set_level(self, join: int, level: int) -> None:
        """Set an analog join (0 = off, 65535 = full). Raises ConnectionError when the link is down."""
        if not self.connected:
            raise ConnectionError("Crestron link is down")
        await self._send_payload(protocol.analog_payload(join, level))

    # ---------- receiving ----------
    def handle_datagram(self, data: bytes, addr: tuple, label: str = "") -> None:
        """Process one datagram. Only the processor's IP is trusted."""
        if addr[0] != self.host:
            return
        frames = protocol.parse_datagram(data)
        if not frames:
            return
        self.last_rx = time.monotonic()
        self.frames_rx += len(frames)
        self._set_connected(True)
        for frame in frames:
            if frame.kind != "data":
                continue
            try:
                self._send_raw(protocol.build_ack(frame.seq))
            except OSError as err:
                _LOGGER.debug("Ack send failed: %s", err)
            self._handle_event(protocol.decode_payload(frame.payload))

    def _handle_event(self, event: protocol.Event) -> None:
        if event.kind == "analog":
            if self.levels.get(event.join) != event.level:
                self.levels[event.join] = event.level
                self._notify_join(event.join)
        elif event.kind == "digital":
            # Keypad button presses probably arrive here. Held back while a state dump is arriving so it does not
            # flood the event bus; the keep-alive echo is ignored. After a Home Assistant restart no hello (and no
            # dump) arrives, so events must flow without one.
            if not self.dump_in_progress and event.join != KEEPALIVE_DIGITAL_JOIN:
                for listener in list(self._digital_listeners):
                    try:
                        listener(event.join, event.level)
                    except Exception:  # noqa: BLE001 - a bad listener must never stop the session
                        _LOGGER.exception("Digital listener failed")
        elif event.kind == "name":
            self.labels[event.join] = event.text
        elif event.kind == "room":
            self.room_names[event.name_id] = event.text
        elif event.kind == "hello":
            _LOGGER.info("Crestron hello received, session established")
            self._schedule_dump_complete()

    def _schedule_dump_complete(self) -> None:
        """After the state dump has had time to arrive, lights without a reported level are treated as off."""
        if self._dump_timer is not None:
            self._dump_timer.cancel()
        self.dump_complete = False
        self.dump_in_progress = True
        self._dump_timer = asyncio.get_running_loop().call_later(DUMP_SETTLE_SECONDS, self._dump_settled)

    def _dump_settled(self) -> None:
        self._dump_timer = None
        self.dump_complete = True
        self.dump_in_progress = False
        _LOGGER.info("Crestron state dump settled (%d levels known); lights without a level are treated as off", len(self.levels))
        self.notify_link()
        for join in range(protocol.JOIN_BASE + 1, protocol.JOIN_BASE + 0x40):
            if join not in self.levels:
                self._notify_join(join)

    def _set_connected(self, value: bool) -> None:
        if value == self.connected:
            return
        self.connected = value
        _LOGGER.info("Crestron link is %s", "UP" if value else "DOWN")
        self.notify_link()
        if value:
            # entities that were unavailable must refresh with the (possibly already known) levels
            for join in list(self.levels):
                self._notify_join(join)
