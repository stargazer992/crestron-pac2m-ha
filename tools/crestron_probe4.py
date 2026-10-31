#!/usr/bin/env python3
# crestron_probe4.py
# Acts like the Control4 server toward the Crestron PAC2M, now including the "keep-alive" that the
# Control4 capture revealed: every 30 seconds Control4 sends a ping (01 00) followed by digital
# join 4000 set HIGH (03 00 a0 0f). Probe 3 never sent these, so the processor ignored our commands.
#   1. Reads the Crestron IP table (ipt) before and after.
#   2. Sends the ping + join 4000 (starts the session), acks the device list, repeats every 30 s.
#   3. Cycles the 7 Theater joins on/off and reports whether the Crestron answered each command.
# Run only while the Mac's Wi-Fi is 192.168.104.25 and the Control4 cable is unplugged.
# Usage: python3 crestron_probe4.py
import os
import re
import socket
import threading
import time

# ---- settings (all values here; the environment overrides exist only for local testing) ----
CRESTRON_IP = os.environ.get("CRESTRON_IP", "192.168.104.111")
CRESTRON_PORT = int(os.environ.get("CRESTRON_PORT", "41794"))   # Crestron listens here for commands/acks
FEEDBACK_PORT = int(os.environ.get("FEEDBACK_PORT", "41794"))   # Crestron sends feedback to this port on us
SRC_PORT = int(os.environ.get("SRC_PORT", "49638"))             # source port Control4 uses for commands/acks
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "23"))        # Crestron telnet console; 0 = skip ipt
PRIME_JOIN = 0x012A                                             # Stair Lights, currently off
THEATER_JOINS = [0x0124, 0x0125, 0x0126, 0x0127, 0x0128, 0x0129, 0x012A]
COLLECT_SECONDS = float(os.environ.get("COLLECT_SECONDS", "8"))  # time to collect the device list
HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "30"))  # Control4 repeats the keep-alive this often
ON_SECONDS = float(os.environ.get("ON_SECONDS", "3"))
OFF_SECONDS = float(os.environ.get("OFF_SECONDS", "2"))
LOG_FILE = os.path.expanduser("~/crestron_probe4_log.txt")
# -------------------------------------------------------------------------------------------

DEST = (CRESTRON_IP, CRESTRON_PORT)
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
tx.bind(("0.0.0.0", SRC_PORT))
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
rx.bind(("0.0.0.0", FEEDBACK_PORT))

lock = threading.Lock()
seq = 0x00                      # next command sequence number
names = {}                      # id -> text
analog = {}                     # join -> level
fb_flag = threading.Event()     # set when the Crestron reports a level for watch_join
watch_join = PRIME_JOIN
log = open(LOG_FILE, "w")  # noqa: SIM115 - stays open for the whole run


def stamp():
    return time.strftime("%H:%M:%S")


def logline(text):
    # every line is also written to the log file so it can be pasted back
    log.write(text + "\n")
    log.flush()


def say(text):
    print(text, flush=True)
    logline(f"{stamp()} {text}")


def send_ack(acked_seq):
    # same 9-byte ack Control4 sends; last byte = sequence number of the message being acknowledged
    pkt = bytes([0x0C, 0xA7, 0xA2, 0xCC, 0x80, 0xA7, 0x00, 0x06, acked_seq & 0xFF])
    try:
        with lock:
            tx.sendto(pkt, DEST)
    except OSError as exc:
        logline(f"ack send error: {exc}")


def decode(data):
    # message = 0b <seq> <flags> 00 00 <len> | body(len) ; body = 00 06 + payload
    try:
        length = data[5]
        body = data[6:6 + length]
        if len(body) < 4:
            return
        kind = body[2]
        if kind == 0x04 and len(body) >= 7:                    # analog level
            join = (body[3] << 8) | body[4]
            analog[join] = (body[5] << 8) | body[6]
            if join == watch_join:
                fb_flag.set()                                   # processor applied / reported our command
        elif body[3] == 0x12 and len(body) >= 5:               # device name
            names[body[4]] = body[5:3 + body[2]].decode("latin-1")
    except Exception as exc:  # noqa: BLE001 - keep going on any odd packet
        logline(f"decode error: {exc}")


def listen(sock, name):
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            return
        logline(f"{stamp()} <- {addr[0]}:{addr[1]} {name}: {len(data)}B {data.hex(' ')}")
        if addr[0] != CRESTRON_IP or not data:
            continue
        if data[0] == 0x0B and len(data) >= 6:
            decode(data)
            send_ack(data[1])


def send_raw(payload, watch_analog=None):
    # message = 0b <seq> 00 00 00 <len> 00 06 + payload ; returns the sequence number used
    global seq, watch_join
    with lock:
        used = seq & 0xFF
        pkt = bytes([0x0B, used, 0, 0, 0, len(payload) + 2, 0, 6]) + payload
        seq += 1
        if watch_analog is not None:
            watch_join = watch_analog
            fb_flag.clear()
        try:
            tx.sendto(pkt, DEST)
        except OSError as exc:
            logline(f"send error: {exc}")
    logline(f"{stamp()} -> {pkt.hex(' ')}")
    return used


def send_cmd(join, level):
    # analog command: 04 <join hi> <join lo> <level hi> <level lo>
    return send_raw(bytes([0x04, (join >> 8) & 0xFF, join & 0xFF, (level >> 8) & 0xFF, level & 0xFF]),
                    watch_analog=join)


def send_keepalive():
    # what Control4 sends every 30 s: ping (01 00) and digital join 4000 high (03 00 a0 0f)
    send_raw(bytes([0x01, 0x00]))
    send_raw(bytes([0x03, 0x00, 0xA0, 0x0F]))


def heartbeat():
    while True:
        time.sleep(HEARTBEAT_SECONDS)
        send_keepalive()


def console_ipt(label):
    # read the IP table from the Crestron telnet console and print the CIP_ID 6 line plus the header
    if not CONSOLE_PORT:
        return
    say(f"--- IP table ({label}) ---")
    try:
        sock = socket.create_connection((CRESTRON_IP, CONSOLE_PORT), timeout=4)
        sock.settimeout(0.5)

        def read_for(seconds):
            end = time.time() + seconds
            out = b""
            while time.time() < end:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    out += chunk
                except TimeoutError:
                    pass
            return out

        read_for(1.0)                                           # banner / prompt
        sock.sendall(b"ipt\r\n")
        raw = read_for(2.0)
        sock.close()
        raw = re.sub(rb"\xff[\xfb-\xfe].", b"", raw)            # strip telnet negotiation bytes
        for line in raw.decode("latin-1").replace("\r", "").split("\n"):
            if line.strip():
                say("  " + line.rstrip())
    except Exception as exc:  # noqa: BLE001 - a probe script reports every error and carries on
        say(f"  could not read the console: {exc}")


threading.Thread(target=listen, args=(rx, "feedback"), daemon=True).start()
threading.Thread(target=listen, args=(tx, "command"), daemon=True).start()

console_ipt("before")

say("Starting the session like Control4 (ping + join 4000) and collecting the device list...")
send_keepalive()
time.sleep(COLLECT_SECONDS)
say(f"Device list received: {len(names)} names, {len(analog)} levels.")
console_ipt("after the device list")
threading.Thread(target=heartbeat, daemon=True).start()

# ---- Theater cycle ----
say("Now cycling the Theater joins. Watch which light comes ON for each.")
for join in THEATER_JOINS:
    dev_id = (join - 0x100) * 2 - 1
    say(f"=== join 0x{join:04X} ({names.get(dev_id, '?')}) ===")
    send_cmd(join, 0xFFFF)
    time.sleep(ON_SECONDS)
    say(f"    feedback after ON: {'yes' if fb_flag.is_set() else 'no'}")
    send_cmd(join, 0x0000)
    time.sleep(OFF_SECONDS)
    say(f"    feedback after OFF: {'yes' if fb_flag.is_set() else 'no'}")
console_ipt("at the end")
say(f"Done. Full log saved to {LOG_FILE}")
time.sleep(2)
