# Crestron lighting: lessons learned on controlling it from Home Assistant (Oct 2026)

Everything below was learned on Robert's network on 2026-10-16. "Verified" means it was observed against the real processor; "unverified" means inferred.

## 1. The setup

| Item | Value |
|---|---|
| Crestron processor | PAC2M (2-Series), host name PAC2, firmware v4.001.1012 (Feb 17 2009), IP 192.168.104.111 |
| Program | SIMPL Windows, compiled 10/30/2019 (D3 Pro lighting design + Extra Vegetables "RSD" export) |
| Hardware | CLX-2DIM8 x4, CLX-1DIM4, CLX-4HSW4, C2N-DB6 / C2N-DB12 keypads (36 dimmers, 4 relays, 1 keypad in Composer) |
| Control4 server | 192.168.104.25, UniFi Port 6, runs the Extra Vegetables Crestron Lighting driver v8.0.3 (encrypted Lua, UDP) |
| Home Assistant | VM at 192.168.104.120 (VMware Fusion on the Mac Mini 192.168.104.68) |
| Crestron IP table | CIP_ID 6, type CIP, port 41794, 192.168.104.025 (the Control4 server) |
| Open ports on the processor | 23 (telnet console), 41794 (CIP over UDP), 41795, 80. Refused: 22, 443, 41797 |

Dead ends: the HACS integration bwcon/homeassistant-crestron-home needs a Crestron Home OS web API token. A 2-Series PAC2M has no such API (`/cws/api/login` returns 404). It can never work here. Decrypting the driver is not needed and was not done: the protocol can be read from network captures.

## 2. How the protocol works (all verified)

Transport: UDP. The processor listens on port 41794. The client sends from any source port (Control4 used 49638 and later an ephemeral port such as 44991) and must listen on UDP 41794 for the processor's feedback. The processor sends its replies to the command's source port (acks) and to the client's port 41794 (feedback).

Framing:
```
data message:  0b <seq> 00 00 00 <len> 00 06 <payload>      len = payload length + 2
ack:           0c a7 a2 cc 80 a7 00 06 <seq of the message being acked>
```
- Every data message in either direction must be acked with the same sequence number.
- Sequence numbers are per sender and start at 0 on a new session. The processor does NOT enforce them: a full 0-255 sweep made no difference.

Payloads:
```
analog level (16-bit, 0000 off, ffff full):   04 <join hi> <join lo> <level hi> <level lo>
digital:                                      03 00 <join lo> <join hi>   (join hi bit 7 set = LOW, clear = HIGH)
ping:                                         01 00
hello (processor to client):                  02 03 00
time/date (processor to client):              08 08 ... (processor clock, not decoded)
name entry (processor to client):             <len> 12 <id> <text>
```

### The key discovery: the keep-alive
Control4 sends, every ~30 s (we saw 30-60 s gaps):
```
0b <seq> 00 00 00 04 00 06 01 00          ping
0b <seq+1> 00 00 00 06 00 06 03 00 a0 0f  digital join 4000 HIGH
```
Without these two messages the processor accepts and acks commands but silently ignores them (lights do not change, no feedback). With them, `ipt` shows ONLINE and commands are applied. This cost us four probe versions to find, because the captures all started mid-session. Only a capture of Control4 STARTING UP (cable plugged back in while mirroring) showed it.

### Session start
1. Client sends ping + join 4000 high.
2. Processor acks both, then sends hello (seq 1), time, all digital joins, all analog levels, and 41 name entries (~112 messages in under 3 s).
3. Client must ack every one of them with the same sequence number. Then `ipt` shows ONLINE.
4. Client repeats ping + join 4000 every 30 s.

### Feedback
After a command that CHANGES a level, the processor sends analog feedback `04 01 <join> <level>` plus an on/off digital pair. A command that does not change the level (e.g. "on" to a light already on) produces no feedback, which is normal. Verified digital pairs: analog 0x124 gives 0x17c/0x17d, then +2 per light (0x125: 0x17e/0x17f ... 0x12a: 0x188/0x189).

### Join map
The dump contains name entries with odd ids. Rule (verified for the Theater): `analog join = 0x100 + (id + 1) / 2`.

Theater (verified by watching the lights): 0x124 Downlights, 0x125 Downlights 2, 0x126 Sconces, 0x127 Sconces 2, 0x128 Mini, 0x129 Rope Lights, 0x12A Stair Lights.

The full list of 41 Crestron names is in the dump (see crestron_devices.csv for the Control4 room/device names). Duplicate labels (several "Downlights") mean the Crestron names alone do not identify a room, so a per-light mapping table is still needed.

## 3. The processor only talks to 192.168.104.25
- The IP table entry (CIP_ID 6) is an outbound CIP peer entry. The processor sends its hello to that IP on port 41794 and only accepts commands from it. Commands from any other IP are ignored with no reply.
- When the client does not ack the hello, the processor retries 5 times (~0.1, 0.5, 0.9, 1.7, 3.3 s), marks the entry OFFLINE, and retries later. `ipt` shows UNKNOWN, OFFLINE or ONLINE.
- To test from a Mac we set the Mac Wi-Fi manually to 192.168.104.25, mask 255.255.254.0, while the Control4 server's cable was unplugged from UniFi Port 6. Two devices must never have .25 at the same time.

## 4. The Crestron console (telnet 23, no password)
- Works with `\r\n` line endings. Plain `nc` often shows nothing after a typed command; a small Python socket script that sends `\r\n` works reliably (see the script in section 7).
- Only one console session at a time effectively works. A leftover/suspended `nc` makes the console print "Warning: another Telnet port is open" and ignore commands. Fix: `pkill -x nc` on the Mac, wait ~10 s, reconnect (or `KILLSOCKET Telnet1` on the console).
- Useful commands: `ver`, `ipt` (display the IP table), `help ethernet` (lists all network commands), `addslave ?`, `remslave ?`.
- Edit commands (not yet used):
  ```
  ADDSlave cip_id ip_address [device_id] [port]      cip_id is hex; port is decimal and must be > 256
  REMSlave cip_id ip_address [device_id] [port]
  ```

### IP table edits need a processor reboot (verified 2026-10-16)
- `REMSlave` / `ADDSlave` print "Master List set. Reboot to take effect." Until the processor reboots, the entry shows a BLANK type and status `NOT REG` and the Control4 link is DOWN.
- `reboot` on the console (reply "Rebooting system. Please wait.") takes about 2 minutes. Afterwards the entry shows `CIP OFFLINE`, then `CIP ONLINE` once the peer connects. The re-added entry (same IP, default device id, port 41794) persisted through the reboot.
- Rollback test result: REMSlave 6 192.168.104.25 + ADDSlave 6 192.168.104.25 + reboot returned to `6 CIP ONLINE 41794 192.168.104.025`. Control4 reconnected without any action on its side.
- `crestron_rollback.py` does: remove, add, reboot, wait for the console, wait for ONLINE. `--check` only reads; `--no-reboot` skips the reboot.
- Keypads still work locally while the processor reboots; light states are kept.

## 5. Capturing traffic (how we found everything)
- UniFi port mirroring: Port 30 = destination (Mac's USB LAN adapter, `en13`), "Mirror From" Port 6 (Control4 server) to see what Control4 sends, Port 14 (Crestron) to see what the Crestron sends. Each capture only showed one direction, so use both views.
- Command: `sudo tcpdump -i en13 -n -U -w ~/capture.pcap udp and host 192.168.104.111`. Use `-U` and stop with Ctrl+C or the file comes out empty.
- Capture Control4 STARTING UP (plug its cable back in while capturing). This is what revealed the keep-alive.
- Revert afterwards: Mirror setting on Port 30, Mac Wi-Fi back to DHCP, Control4 cable back into Port 6.

## 6. Things that did not work (so we do not repeat them)
| Attempt | Result |
|---|---|
| One `nc` command from the Mac's normal IP | Ignored (wrong source IP) |
| Same command from source port 49638 | Ignored (the IP is what matters) |
| Command from the Mac as .25 with no session (`nc -u`) | Worked only briefly, while the processor still believed Control4's session was alive |
| Shell loop of commands, probes 1-3 (acks and commands, no keep-alive) | Acked but never applied; `ipt` OFFLINE or ONLINE did not matter |
| Probe 3: ONLINE session plus a 0-255 sequence sweep | Still no effect, ruling out sequence gating |
| Probe 4: added ping + join 4000 | WORKED |

## 7. Working test scripts (scratchpad, run on the Mac while it is 192.168.104.25)
- `crestron_probe4.py`: session start, keep-alive thread, acks everything, Theater on/off cycle, feedback detection, `ipt` before/after.
- `crestron_theater_restore.py`: puts the Theater lights back (Downlights, Sconces, Sconces 2, Mini full; Rope Lights 0x70A3; Downlights 2 and Stair Lights off).
- Console reader (paste into Terminal):
```
python3 - <<'EOF'
import socket, time, re
s = socket.create_connection(("192.168.104.111", 23), timeout=4)
s.settimeout(0.5)
def rd(t):
    end = time.time() + t; out = b""
    while time.time() < end:
        try:
            c = s.recv(4096)
            if not c: break
            out += c
        except socket.timeout: pass
    return re.sub(rb"\xff[\xfb-\xfe].", b"", out).decode("latin-1")
rd(1.5)
for cmd in ["ipt"]:
    s.sendall(cmd.encode() + b"\r\n"); print(rd(3.0))
s.close()
EOF
```

## 8. Safety lessons
- Test scripts change real lights. Save the starting levels first (the dump lists them) and restore afterwards. The Theater test left the lights off until the restore script put them back.
- Keep Control4's cable unplugged only for as long as the test needs. When it is plugged back in, the processor returns to ONLINE for CIP_ID 6 within about a minute.

## 9. Plan for moving the lights to Home Assistant
1. Build a Home Assistant custom integration that speaks this protocol (session start, 30 s keep-alive, ack everything, analog commands, feedback decoding), with one light entity per dimmer and relay.
2. Because the Control4 server stays for audio, repoint the processor's IP table entry to Home Assistant (planned, NOT yet done and untested):
   ```
   REMSlave 6 192.168.104.25
   ADDSlave 6 192.168.104.120 <device_id> 41794
   ```
   Both commands need a processor REBOOT to take effect (see section 4). Rollback: `REMSlave 6 192.168.104.120`, `ADDSlave 6 192.168.104.25`, reboot (use `crestron_rollback.py`). Verified: a console edit survives a reboot (the re-added Control4 entry stayed). Still unverified: which `device_id` to use for the HA entry (the default worked for Control4).
3. Home Assistant must listen on UDP 41794 and be reachable from the processor (the VM must be on bridged networking).
4. Control4 stops controlling the lights after the switch; wall keypads keep working locally.
5. Map every Crestron join to an existing Home Assistant light entity so dashboards and automations keep working (existing names differ from the Crestron labels, e.g. Theater: light.dimmer_sconces, light.dimmer_bar_lights, light.dimmer_theater_front_recessed).

## 10. Open items
- Join numbers for the other rooms (only the Theater is verified); relays and keypad joins.
- Which Crestron label is which Home Assistant light entity.
- The `device_id` value for the Home Assistant entry (console edit persistence is now verified).
- Whether ping/join 4000 must be sent from a fixed source port (Control4 used both 49638 and an ephemeral port, so probably not).

## 11. Lessons from building the Home Assistant integration (Oct 2026)
- The processor must be re-pointed with a console edit **plus a reboot**; the integration automates it and undoes it if Home Assistant never comes online.
- Adding the integration must be harmless: in `control4` mode the client is passive (listens and acks, sends no unsolicited traffic).
- Changing the mode must not reload the config entry: the old sockets close asynchronously and the UDP port is still busy ("Address already in use"). The mode is applied in place.
- Renaming an entity id in the entity registry makes Home Assistant remove and re-add the entity; listeners are re-created in `async_added_to_hass`.
- Control4 disables/re-enables: a re-enabled entity only comes back after the Control4 config entry reloads (the release step schedules that reload).
- The Control4 server address is read from the Control4 config entry (`host`); an override exists under Configure > Address overrides.
- Tests: `pytest-homeassistant-custom-component` blocks sockets by default; use the `socket_enabled` fixture for the fake processor.
