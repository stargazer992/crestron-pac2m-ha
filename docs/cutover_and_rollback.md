# Cutover and rollback runbook

## Before the cutover (all safe, nothing changes on the processor)
1. Install the integration and add it (mode `control4`, passive). Check the 40 lights exist and are unavailable.
2. Run *Configure > Go stand-alone* with **Preview only** ticked. Read the plan: the Control4 address, Home Assistant's address, the entity-id moves.
3. Confirm Home Assistant listens on UDP 41794 and the VM is on bridged networking (HA IP 192.168.104.120 must be reachable from the processor).
4. Pick a time when nobody needs the lights for ~5 minutes. Lights keep their levels during the reboot and wall keypads keep working.

## Cutover
Configure > Go stand-alone (Preview only unticked). Expect 2-4 minutes. Watch `binary_sensor.crestron_pac2m_link` turn on.
Then spot-check one light per room. Rooms other than Theater were never verified physically.

## Rollback levels (use the first that works)
1. **UI**: Configure > Return to Control4. Does everything, including re-enabling the Control4 lights and disabling the integration.
2. **Service**: `crestron_pac2m.return_to_control4` with `dry_run: false` (Developer tools > Actions).
3. **Home Assistant is down**: from any computer on the LAN: `python3 tools/crestron_rollback.py` (use `--check` to look first).
   Afterwards, in Home Assistant: run `crestron_pac2m.release_entity_ids` (dry run first) or rename the `_control4_old` entities back and enable them, then reload the Control4 integration.
4. **No scripts at all**: telnet to the processor (port 23, no password, CRLF line endings) and type:
   ```
   ipt
   remslave 6 192.168.104.120
   addslave 6 192.168.104.25
   reboot
   ```
   Wait 2-3 minutes, then `ipt` must show `6 CIP ONLINE 41794 192.168.104.025`.

## Console gotchas
* A leftover/suspended `nc` or telnet makes the console ignore commands ("another Telnet port is open"): `pkill -x nc`, wait 10 s, reconnect.
* `NOT REG` and a blank type after an edit is normal until the reboot.
* `ipt` prints addresses zero-padded (`192.168.104.025`); the commands accept `192.168.104.25`.
