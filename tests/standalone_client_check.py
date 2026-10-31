"""Tests the protocol + client modules against a simulated PAC2M (no Home Assistant needed).

Run:  python3 tests/test_client_vs_fake.py
The simulated processor mimics what was verified on the real one:
  * only applies commands after it has seen ping + digital join 4000
  * then sends hello, names, levels (the state dump) and feedback on every level change
  * stays completely silent when 'silent' is set (what the real one does to a non-table IP)
"""
import asyncio
import importlib
import pathlib
import sys
import types

PKG_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "crestron_pac2m"
# load the modules without running the package __init__ (which needs Home Assistant)
pkg = types.ModuleType("cpac"); pkg.__path__ = [str(PKG_DIR)]; sys.modules["cpac"] = pkg
protocol = importlib.import_module("cpac.protocol")
const = importlib.import_module("cpac.const")
client_mod = importlib.import_module("cpac.client")

# shorten the timings for the test
client_mod.KEEPALIVE_SECONDS = 0.3
client_mod.RETRY_SECONDS = 0.2
client_mod.SESSION_TIMEOUT_SECONDS = 1.0
client_mod.COMMAND_SPACING_SECONDS = 0.0
client_mod.LOOP_TICK_SECONDS = 0.05

FAKE_PORT, CLIENT_LISTEN, CLIENT_SRC = 41901, 41902, 41903
results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name, flush=True)


class FakeCrestron(asyncio.DatagramProtocol):
    def __init__(self):
        self.silent = True
        self.enabled = False
        self.seq = 0
        self.levels = {0x124: 0xFFFF, 0x125: 0x0000, 0x129: 0x70A3}
        self.names = {0x47: "Downlights", 0x49: "Downlights 2", 0x4F: "Mini"}   # ids -> labels (join = 0x100+(id+1)/2)
        self.acks_seen = []
        self.commands_applied = 0

    def connection_made(self, transport):
        self.transport = transport

    def send_data(self, payload, to=("127.0.0.1", CLIENT_LISTEN)):
        pkt = protocol.build_data(self.seq, payload)
        self.seq = (self.seq + 1) & 0xFF
        self.transport.sendto(pkt, to)

    def dump(self):
        self.send_data(bytes([2, 3, 0]))                                  # hello
        for nid, text in self.names.items():
            t = text.encode()
            self.send_data(bytes([len(t) + 3, 0x12, nid]) + t)            # name entry
        for join, level in self.levels.items():
            self.send_data(protocol.analog_payload(join, level))          # analog levels

    def set_level(self, join, level):
        """Used by the test to simulate a wall keypad change (feedback only on change)."""
        if self.levels.get(join) != level:
            self.levels[join] = level
            self.send_data(protocol.analog_payload(join, level))

    def datagram_received(self, data, addr):
        if self.silent:
            return
        for frame in protocol.parse_datagram(data):
            if frame.kind == "ack":
                self.acks_seen.append(frame.seq)
                continue
            self.transport.sendto(protocol.build_ack(frame.seq), addr)    # ack goes back to the command's source
            ev = protocol.decode_payload(frame.payload)
            if ev.kind == "digital" and ev.join == 4000 and ev.level == 1:
                if not self.enabled:
                    self.enabled = True
                    self.dump()
            elif ev.kind == "analog" and self.enabled:
                self.commands_applied += 1
                self.set_level(ev.join, ev.level)


async def wait_for(cond, timeout=3.0):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


async def main():
    loop = asyncio.get_running_loop()
    # ---- protocol unit checks against bytes captured from the real Control4 ----
    check("ping frame bytes", protocol.build_data(0, protocol.PAYLOAD_PING).hex(" ") == "0b 00 00 00 00 04 00 06 01 00")
    check("join 4000 high bytes", protocol.build_data(1, protocol.digital_payload(4000, True)).hex(" ") == "0b 01 00 00 00 06 00 06 03 00 a0 0f")
    check("ack bytes", protocol.build_ack(0x05).hex(" ") == "0c a7 a2 cc 80 a7 00 06 05")
    check("analog cmd bytes", protocol.analog_payload(0x124, 0xFFFF).hex(" ") == "04 01 24 ff ff")
    check("join rule id 0x47 -> 0x124", protocol.name_id_to_join(0x47) == 0x124)
    check("brightness round trip", all(protocol.level_to_brightness(protocol.brightness_to_level(b)) == b for b in range(256)))
    check("brightness 255 -> 0xFFFF", protocol.brightness_to_level(255) == 0xFFFF)
    two = protocol.build_data(3, bytes([4, 1, 0x24, 0, 0])) + protocol.build_ack(9)
    check("two frames in one datagram", [f.kind for f in protocol.parse_datagram(two)] == ["data", "ack"])
    check("garbage never raises", protocol.parse_datagram(b"\xff\x00\x01") == [] and protocol.parse_datagram(b"\x0b\x00") == [])
    check("truncated frame ignored", protocol.parse_datagram(protocol.build_data(0, b"\x04\x01\x24\xff\xff")[:-2]) == [])

    # ---- live client vs fake ----
    fake = FakeCrestron()
    await loop.create_datagram_endpoint(lambda: fake, local_addr=("127.0.0.1", FAKE_PORT))
    client = client_mod.CrestronClient("127.0.0.1", FAKE_PORT, CLIENT_LISTEN, CLIENT_SRC)
    link_events, join_events = [], []
    client.add_link_listener(lambda: link_events.append(client.connected))
    client.add_join_listener(0x124, lambda j: join_events.append(client.levels[j]))
    await client.async_start()

    await asyncio.sleep(1.5)
    check("silent processor -> link stays down (pre-cutover)", client.connected is False and client.frames_rx == 0)
    try:
        await client.async_set_level(0x124, 0)
        check("command refused while link down", False)
    except ConnectionError:
        check("command refused while link down", True)

    fake.silent = False
    check("link comes up after the processor answers", await wait_for(lambda: client.connected))
    check("state dump: levels received", await wait_for(lambda: client.levels.get(0x124) == 0xFFFF and client.levels.get(0x129) == 0x70A3))
    check("state dump: labels received", client.labels.get(0x124) == "Downlights" and client.labels.get(0x128) == "Mini")
    check("hello+dump frames were acked", await wait_for(lambda: len(fake.acks_seen) >= 5))

    await client.async_set_level(0x124, 0x8080)
    check("command applied and feedback received", await wait_for(lambda: client.levels.get(0x124) == 0x8080 and fake.commands_applied == 1))
    check("join listener fired", 0x8080 in join_events)

    fake.set_level(0x125, 0xFFFF)                                          # wall keypad press
    check("wall-keypad feedback updates level", await wait_for(lambda: client.levels.get(0x125) == 0xFFFF))

    # ---- processor goes silent, then returns ----
    fake.silent = True
    check("silence -> unavailable after timeout", await wait_for(lambda: client.connected is False, timeout=4.0))
    fake.enabled = False                                                   # a restarted processor needs a new session
    fake.silent = False
    check("reconnects and gets a fresh dump", await wait_for(lambda: client.connected and fake.enabled))
    check("link up/down/up events seen", link_events[:3] == [True, False, True] or link_events[-3:] == [True, False, True])

    # ---- processor-initiated hello before any keep-alive ack ----
    before = len(fake.acks_seen)
    fake.send_data(bytes([2, 3, 0]))
    check("processor-initiated frame is acked", await wait_for(lambda: len(fake.acks_seen) > before))

    # ---- frames from a foreign IP are ignored ----
    n = client.frames_rx
    client.handle_datagram(protocol.build_data(0, protocol.analog_payload(0x124, 5)), ("10.9.9.9", 41794))
    check("foreign IP ignored", client.frames_rx == n and client.levels[0x124] != 5)

    await client.async_stop()
    check("stop marks link down", client.connected is False)
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0

sys.exit(asyncio.run(main()))
