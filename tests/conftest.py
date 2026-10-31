"""Shared fixtures: custom integration loading + a simulated PAC2M (console + UDP) for end-to-end tests."""
import asyncio
import re
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from custom_components.crestron_pac2m import protocol  # noqa: E402

pytest_plugins = "pytest_homeassistant_custom_component"

C4_IP = "10.1.1.25"          # pretend Control4 server
HA_IP = "127.0.0.1"          # pretend Home Assistant (the test client is on loopback)
PORTS = {"console": 42301, "crestron": 42302, "listen": 42303, "source": 42304}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, socket_enabled):
    """Load the custom integration and allow real localhost sockets (the fake processor uses UDP + TCP)."""
    yield


class FakeProcessor:
    """Console + UDP side of a PAC2M. Edits to the IP table apply only after `reboot`, like the real one."""

    def __init__(self):
        self.table_ip = C4_IP              # active entry
        self.pending_ip = None             # edited, waiting for a reboot
        self.online = True
        self.down = False                  # console unreachable (rebooting)
        self.hello_enabled = True          # False = HA never comes up
        self.reboots = 0
        self.commands = []                 # console commands received
        self.udp_frames = 0
        self.keepalive_seen = False
        self.drop_analog = 0               # drop this many analog commands (no ack, not applied), like lost UDP packets
        self.resync_dumps = True          # False = like the real processor: no new dump while the session is alive
        self.levels = {0x127: 0x0000, 0x124: 0xFFFF}
        self.names = {0x124: "WRONG NAME", 0x127: "Sconces 2"}   # join -> label sent in the dump
        self.rooms = {0x124: "Theater", 0x127: "Kitchen"}   # join -> room sent in the dump (0x127 is deliberately wrong)
        self.seq = 0
        self.client_addr = ("127.0.0.1", PORTS["listen"])
        self._servers = []
        self._tasks = []

    # ---------- IP table ----------
    def ipt(self):
        if self.pending_ip is not None:
            row = f"  6                NOT REG        41794  {self._pad(self.pending_ip)}"
        else:
            status = "ONLINE" if self.online else "OFFLINE"
            row = f"  6      CIP      {status:<8}       41794  {self._pad(self.table_ip)}"
        return "CIP_ID Type  Status   DevID Port   IP Address/SiteName\r\n" + row + "\r\n"

    @staticmethod
    def _pad(ip):
        return ".".join(f"{int(p):03d}" for p in ip.split("."))

    # ---------- console ----------
    async def _console(self, reader, writer):
        if self.down:
            writer.close()
            return
        writer.write(b"\xff\xfd\x18Connected to Host: PAC2\r\nPAC2M>")
        await writer.drain()
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                cmd = line.decode().strip()
                if not cmd:
                    writer.write(b"PAC2M>")
                    continue
                self.commands.append(cmd)
                out = ""
                low = cmd.lower().split()
                if low[0] == "ipt":
                    out = self.ipt()
                elif low[0] == "remslave":
                    out = "Master List set. Reboot to take effect.\r\n"
                    self.pending_ip = self.pending_ip or "0.0.0.0"
                elif low[0] == "addslave":
                    self.pending_ip = ".".join(str(int(p)) for p in low[2].split("."))
                    out = "Master List set. Reboot to take effect.\r\n"
                elif low[0] == "reboot":
                    writer.write(b"Rebooting system. Please wait.\r\n")
                    await writer.drain()
                    self._tasks.append(asyncio.get_running_loop().create_task(self._reboot()))
                    writer.close()
                    return
                writer.write(out.encode() + b"PAC2M>")
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def _reboot(self):
        self.reboots += 1
        self.down = True
        self.online = False
        self.keepalive_seen = False
        await asyncio.sleep(0.4)
        if self.pending_ip is not None:
            self.table_ip = self.pending_ip
            self.pending_ip = None
        self.down = False
        if self.table_ip == C4_IP:
            await asyncio.sleep(0.3)
            self.online = True                 # Control4 reconnects by itself
        elif self.table_ip == HA_IP and self.hello_enabled:
            self.send_data(bytes([2, 3, 0]))   # the processor initiates the hello toward the new peer

    # ---------- UDP ----------
    def send_data(self, payload, seq=None):
        """Send a data frame; seq=<an earlier seq> repeats it like a retransmit."""
        if seq is None:
            seq = self.seq
            self.seq = (self.seq + 1) & 0xFF
        pkt = protocol.build_data(seq, payload)
        self.udp.sendto(pkt, self.client_addr)

    def dump(self):
        for join, text in self.names.items():
            t = text.encode()
            self.send_data(bytes([len(t) + 3, 0x12, 2 * (join - 0x100) - 1]) + t)
            r = self.rooms.get(join, "Theater").encode()      # room entry: even id = the light's id - 1
            self.send_data(bytes([len(r) + 3, 0x12, 2 * (join - 0x100) - 2]) + r)
        for join, level in self.levels.items():
            self.send_data(protocol.analog_payload(join, level))

    def connection_made(self, transport):
        self.udp = transport

    def datagram_received(self, data, addr):
        if self.table_ip != HA_IP or self.down or not self.hello_enabled:
            return                             # only the peer in the IP table is answered
        for frame in protocol.parse_datagram(data):
            self.udp_frames += 1
            if frame.kind != "data":
                continue
            ev = protocol.decode_payload(frame.payload)
            if ev.kind == "analog" and self.drop_analog:
                self.drop_analog -= 1
                continue
            self.udp.sendto(protocol.build_ack(frame.seq), addr)
            if ev.kind == "digital" and ev.join == 4000 and ev.level == 1 and not self.keepalive_seen:
                self.keepalive_seen = True
                self.online = True
                self.dump()
            elif ev.kind == "digital" and ev.join == 4000 and ev.level == 0 and self.resync_dumps:
                self.keepalive_seen = False             # a falling edge ends the "session"; the next rising edge dumps again
            elif ev.kind == "analog" and self.keepalive_seen:
                if self.levels.get(ev.join) != ev.level:
                    self.levels[ev.join] = ev.level
                    self.send_data(protocol.analog_payload(ev.join, ev.level))

    def error_received(self, exc):
        pass

    def connection_lost(self, exc):
        pass

    async def start(self):
        loop = asyncio.get_running_loop()
        self._console_server = await asyncio.start_server(self._console, "127.0.0.1", PORTS["console"])
        await loop.create_datagram_endpoint(lambda: self, local_addr=("127.0.0.1", PORTS["crestron"]))

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._console_server.close()
        await self._console_server.wait_closed()
        self.udp.close()


@pytest.fixture
async def processor(monkeypatch):
    """A running fake processor with all module ports and timings pointed at it and shortened."""
    import custom_components.crestron_pac2m as pkg
    from custom_components.crestron_pac2m import client, console, operations, switcher

    monkeypatch.setattr(pkg, "CRESTRON_PORT", PORTS["crestron"])
    monkeypatch.setattr(pkg, "LISTEN_PORT", PORTS["listen"])
    monkeypatch.setattr(pkg, "SOURCE_PORT", PORTS["source"])
    monkeypatch.setattr(switcher, "CONSOLE_PORT", PORTS["console"])
    monkeypatch.setattr(console.CrestronConsole.__init__, "__defaults__", (PORTS["console"],))
    monkeypatch.setattr(switcher, "REBOOT_SETTLE_SECONDS", 0.1)
    monkeypatch.setattr(switcher, "CONSOLE_BACK_SECONDS", 5)
    monkeypatch.setattr(switcher, "WAIT_READY_SECONDS", 3)
    monkeypatch.setattr(switcher, "POLL_SECONDS", 0.2)
    monkeypatch.setattr(client, "KEEPALIVE_SECONDS", 0.3)
    monkeypatch.setattr(client, "RETRY_SECONDS", 0.2)
    monkeypatch.setattr(client, "SESSION_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(client, "COMMAND_SPACING_SECONDS", 0)
    monkeypatch.setattr(client, "COMMAND_ACK_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(client, "LOOP_TICK_SECONDS", 0.05)
    monkeypatch.setattr(operations, "DISABLE_DELAY_SECONDS", 0.2)
    monkeypatch.setattr(pkg, "LABEL_CHECK_DELAY_SECONDS", 0.3)
    monkeypatch.setattr(pkg, "ADDRESS_CHECK_DELAY_SECONDS", 0.5)
    proc = FakeProcessor()
    await proc.start()
    yield proc
    await proc.stop()
