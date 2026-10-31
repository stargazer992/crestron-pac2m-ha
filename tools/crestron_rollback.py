#!/usr/bin/env python3
# crestron_rollback.py
# Re-points the Crestron lighting processor (PAC2M) back to the Control4 server.
# It only talks to the Crestron's telnet console, so it works even if Home Assistant is down.
#
# IMPORTANT (learned on the real processor): REMSlave/ADDSlave only take effect after the processor
# REBOOTS ("Master List set. Reboot to take effect."). Until then the entry shows type blank / NOT REG
# and nothing talks to it. The reboot takes about 2 minutes; keypads and Control4 do not respond meanwhile,
# lights that are already on/off stay as they are.
#
# What it does:
#   1. Reads the IP table (ipt) and shows it.
#   2. If CIP_ID 6 does not point at the Control4 server: REMSlave the old entry, ADDSlave the Control4 entry.
#   3. Reboots the processor (skip with --no-reboot), waits for the console to return,
#      then waits until the entry shows ONLINE (Control4 has reconnected).
#
# Usage:
#   python3 crestron_rollback.py              roll back (re-point to Control4, reboot, verify)
#   python3 crestron_rollback.py --check      only show the IP table, change nothing
#   python3 crestron_rollback.py --no-reboot  change the table but do not reboot (reboot it yourself later)
import re
import socket
import sys
import time

# ---- settings (all values here) ----
CRESTRON_IP = "192.168.104.111"      # Crestron processor
CONSOLE_PORT = 23                    # Crestron telnet console
CIP_ID = "6"                         # IP table entry used by Control4 (hex id as shown by ipt)
CONTROL4_IP = "192.168.104.25"       # Control4 server (where the entry must point)
CIP_PORT = "41794"                   # expected CIP port in the table
REBOOT_SETTLE_SECONDS = 20           # pause after sending reboot before reconnecting
CONSOLE_BACK_SECONDS = 240           # how long to wait for the console to come back after the reboot
WAIT_ONLINE_SECONDS = 180            # how long to wait for ONLINE after the reboot
POLL_SECONDS = 10                    # how often to re-check while waiting
PROMPT = "PAC2M>"                    # console prompt
# -------------------------------------


def say(text=""):
    print(text, flush=True)


def norm_ip(text):
    # the console prints 192.168.104.025; turn that into 192.168.104.25
    try:
        return ".".join(str(int(p)) for p in text.split("."))
    except ValueError:
        return text


class Console:
    def __init__(self):
        self.sock = socket.create_connection((CRESTRON_IP, CONSOLE_PORT), timeout=5)
        self.sock.settimeout(0.5)
        self.read_until_quiet(1.5)          # banner (may contain the "another Telnet port is open" warning)

    def read_until_quiet(self, seconds, stop_at_prompt=False):
        end = time.time() + seconds
        out = b""
        while time.time() < end:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                out += chunk
                if stop_at_prompt and PROMPT.encode() in out:
                    break
            except TimeoutError:
                pass
        return re.sub(rb"\xff[\xfb-\xfe].", b"", out).decode("latin-1").replace("\r", "")

    def run(self, command, wait=3.0):
        self.sock.sendall(command.encode() + b"\r\n")
        return self.read_until_quiet(wait, stop_at_prompt=True)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def read_entry(console):
    # returns (raw_text, entry or None) where entry = dict(status, port, ip)
    # a row looks like:  "6  CIP  ONLINE  41794  192.168.104.025"  or  "6      NOT REG   41794  192.168.104.025"
    raw = console.run("ipt")
    for line in raw.split("\n"):
        m = re.match(rf"^\s*{re.escape(CIP_ID)}\s+(.*?)\s+(\d+)\s+([\d.]+)\s*$", line)
        if not m:
            continue
        middle = m.group(1)
        status = "UNKNOWN"
        for word in ("NOT REG", "ONLINE", "OFFLINE", "UNKNOWN"):
            if word in middle:
                status = word
                break
        return raw, {"status": status, "port": m.group(2), "ip": norm_ip(m.group(3))}
    return raw, None


def show(raw):
    for line in raw.split("\n"):
        if line.strip() and PROMPT not in line:
            say("  " + line.rstrip())


def wait_for_console():
    say(f"Waiting up to {CONSOLE_BACK_SECONDS} s for the processor to come back after the reboot...")
    end = time.time() + CONSOLE_BACK_SECONDS
    while time.time() < end:
        try:
            console = Console()
            say("  console is back.")
            return console
        except OSError:
            time.sleep(POLL_SECONDS)
    return None


def wait_online(console):
    say(f"Waiting up to {WAIT_ONLINE_SECONDS} s for the entry to show ONLINE (Control4 reconnecting)...")
    end = time.time() + WAIT_ONLINE_SECONDS
    last = None
    while time.time() < end:
        _raw, entry = read_entry(console)
        status = entry["status"] if entry else "MISSING"
        if status != last:
            say(f"  status: {status}")
            last = status
        if status == "ONLINE":
            return True
        time.sleep(POLL_SECONDS)
    return False


def main():
    check_only = "--check" in sys.argv
    do_reboot = "--no-reboot" not in sys.argv

    try:
        console = Console()
    except OSError as exc:
        say(f"Could not reach the Crestron console: {exc}")
        say("Check that the Mac is on the same network, then close other telnet/nc sessions (pkill -x nc) and retry.")
        return 1

    try:
        say("IP table now:")
        raw, entry = read_entry(console)
        show(raw)
        if check_only:
            return 0

        if entry is not None and entry["ip"] == CONTROL4_IP and entry["status"] == "ONLINE":
            say(f"Entry already points to Control4 ({CONTROL4_IP}) and is ONLINE. Nothing to change.")
            return 0

        if entry is None or entry["ip"] != CONTROL4_IP:
            if entry is not None:
                say(f"Removing entry {CIP_ID} -> {entry['ip']}")
                say("  " + console.run(f"remslave {CIP_ID} {entry['ip']}").strip().replace("\n", "\n  "))
            say(f"Adding entry {CIP_ID} -> {CONTROL4_IP}")
            say("  " + console.run(f"addslave {CIP_ID} {CONTROL4_IP}").strip().replace("\n", "\n  "))
            raw, entry = read_entry(console)
            say("IP table after the change (NOT REG is expected until the reboot):")
            show(raw)
            if entry is None or entry["ip"] != CONTROL4_IP:
                say("PROBLEM: the entry does not point to Control4. Send this output to Claude before doing anything else.")
                return 2
        else:
            say("Entry already points to Control4 but is not ONLINE; a reboot may be all that is needed.")

        if not do_reboot:
            say("Reboot skipped (--no-reboot). The change only takes effect after the processor reboots.")
            return 0

        say("Rebooting the processor now (about 2 minutes).")
        say("  " + console.run("reboot", wait=3.0).strip())
        console.close()
        time.sleep(REBOOT_SETTLE_SECONDS)
        console = wait_for_console()
        if console is None:
            say("The console did not come back in time. Wait a few minutes and run: python3 crestron_rollback.py --check")
            return 3
        if wait_online(console):
            raw, entry = read_entry(console)
            show(raw)
            say("DONE: the Crestron is pointed at Control4 and shows ONLINE.")
            return 0
        say("The entry is correct but not ONLINE yet. Control4 may need a minute, or a driver refresh in Composer.")
        say("Run 'python3 crestron_rollback.py --check' again in a minute.")
        return 4
    except Exception as exc:  # noqa: BLE001 - report and stop cleanly, never leave a half-open session
        say(f"Unexpected error: {exc}")
        return 5
    finally:
        if console is not None:
            console.close()


if __name__ == "__main__":
    sys.exit(main())
