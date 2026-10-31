# Crestron PAC2M Lighting for Home Assistant

**Repository:** https://github.com/stargazer992/crestron-pac2m-ha (HACS custom repository; updates need a GitHub release)

Controls a Crestron **PAC2M (2-Series) lighting processor** directly from Home Assistant over its CIP/UDP protocol,
so the Control4 server is no longer needed for lighting. Built for the Fleur de Lis house (40 dimmers/relays),
but the protocol notes and tools apply to any PAC2M.

* Reuses the **existing Home Assistant light entity ids** (dashboards, scenes and automations keep working).
* Checks at every connect that the processor's labels match the mapping table, and warns if a join looks mapped to the wrong light.
* Lights are **available** whenever the processor session is alive - independent of the Control4 server.
* One setting switches between two modes - and back - without touching a console by hand.

| Mode | Processor IP table points to | This integration | Control4 |
|---|---|---|---|
| `control4` (default after adding) | Control4 server | passive: sends **nothing**, lights unavailable | controls the lights |
| `standalone` | Home Assistant | controls the lights | lights disabled (audio untouched) |

## Install
1. Copy `custom_components/crestron_pac2m` into `/config/custom_components/` (or add this repo to HACS as a custom repository).
2. Restart Home Assistant.
3. Settings > Devices & Services > Add Integration > **Crestron PAC2M Lighting** > enter the processor IP (default `192.168.104.111`).

Adding the integration **never changes the processor**. It starts in `control4` mode: passive, nothing sent, 40 lights shown as unavailable.

## Switch to stand-alone (Home Assistant owns the lights)
Settings > Devices & Services > Crestron PAC2M > **Configure** > *Go stand-alone*.
Tick **Preview only** first - it lists exactly what would happen. Untick it to do it for real. It then:
1. finds Home Assistant's own IP,
2. edits the processor's IP table (`REMSlave 6 <control4>` / `ADDSlave 6 <home assistant>`) over the telnet console,
3. **reboots the processor** (2-4 minutes; lights keep their levels, wall keypads keep working),
4. waits until the processor reports ONLINE and Home Assistant receives its hello,
5. moves the existing entity ids to this integration (old Control4 lights are renamed `<id>_control4_old` and disabled; name, icon, area, labels and voice exposure are copied),
6. sends a persistent notification and a phone notification (`notify.mobile_app_iphone_rg`).

If Home Assistant never comes online (firewall, network, wrong IP), the switch **undoes itself** (restores the Control4 address and reboots again).

## Return to Control4 (and disable this integration)
Configure > *Return to Control4* (also with a *Preview only* checkbox). It finds the Control4 server address from the Control4
integration already in Home Assistant (or the override under *Address overrides*), points the processor back at it, reboots,
waits for ONLINE, gives the entity ids back to the Control4 lights (re-enabled, Control4 reloaded), and then **disables this integration**
(re-enable it under Devices & Services if you want to go stand-alone again).

The same operations exist as services (dry run by default): `crestron_pac2m.go_standalone`, `crestron_pac2m.return_to_control4`,
`crestron_pac2m.adopt_entity_ids`, `crestron_pac2m.release_entity_ids`.

## If Home Assistant itself is down
Run `python3 tools/crestron_rollback.py` from any computer on the network. It needs only the processor's console.
Step-by-step, including manual console commands: [docs/cutover_and_rollback.md](docs/cutover_and_rollback.md).

## What is verified, what is not
| Item | Status |
|---|---|
| Protocol, keep-alive, session start, feedback | Verified on the real processor |
| Theater joins 0x124-0x12A | Verified by watching the lights |
| IP table edits + reboot + Control4 reconnect | Verified (rollback test, Oct 2026) |
| All other joins (33 lights) | Derived from the Control4 project; spot-check after cutover |
| Family Room fan (0x106) | Works on/off from Home Assistant (never worked under Control4); speed control untested |
| Label check | After every link-up the processor's name dump is compared with the table; a mismatch raises a notification (`crestron_pac2m_labels`) and shows on the Link sensor (`label_mismatches`) |
| Integration inside Home Assistant 2026.10 | Automated tests against a simulated processor + console (`tests/`) |
| Integration against the real processor | Verified: cutover done 2026-10-16, all 40 lights respond (v1.5.0) |
| `device_id` for the new IP table entry | Left to the default (the Control4 entry also has none) |

## Documentation
* [docs/lessons_learned.md](docs/lessons_learned.md) - everything learned about controlling the Crestron (read this first)
* [docs/udp_protocol.md](docs/udp_protocol.md) - frame and payload formats
* [docs/cutover_and_rollback.md](docs/cutover_and_rollback.md) - runbook
* [docs/join_map.md](docs/join_map.md) - entity <-> join table

## Repository layout
```
custom_components/crestron_pac2m/   the integration
  protocol.py   frame encode/decode (no HA imports)      client.py     UDP session, keep-alive, availability (no HA imports)
  console.py    telnet console client (no HA imports)    switcher.py   IP table re-pointing with reboot + failback (no HA imports)
  adopt.py      entity-id takeover / release             operations.py the two switch operations
  light.py binary_sensor.py config_flow.py __init__.py const.py (every tunable value is in const.py)
tests/          pytest (real HA core + fake processor) and standalone_client_check.py
tools/          reference scripts used during discovery (probe4, theater restore, rollback, pcap parser, join JSON)
docs/           lessons learned, protocol, runbook, join map
```

## Run the tests
```
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python homeassistant pytest-homeassistant-custom-component
.venv/bin/python -m pytest
python3 tests/standalone_client_check.py      # protocol + client only, no Home Assistant needed
```

## Safety rules learned the hard way
* Only the IP in the processor's IP table can control it. Two devices must never share the Control4 IP.
* IP table edits do nothing until the processor reboots.
* The processor ignores commands without the 30 s keep-alive (ping + digital join 4000 high).
* Test scripts change real lights: note the levels first, restore afterwards.
