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
3. Settings > Devices & Services > Add Integration > **Crestron PAC2M Lighting**. It scans Home Assistant's local network (each network capped at a /24) for a telnet console with a Crestron 2-series prompt (`PAC2M>`) and pre-fills the address it finds (otherwise `192.168.104.111`); type another one if needed. The scan only reads the console banner and sends nothing over UDP.

To change the processor address later (for example after it got a new IP): the integration's menu (three dots) > **Reconfigure**. This only changes where Home Assistant sends to; nothing is sent to the processor and the mode stays the same.

Adding the integration **never changes the processor**. It starts in `control4` mode: passive, nothing sent, 40 lights shown as unavailable.

## Switch to stand-alone (Home Assistant owns the lights)
Settings > Devices & Services > Crestron PAC2M > **Configure** > *Go stand-alone*.
Tick **Preview only** first - it lists exactly what would happen. Untick it to do it for real. It then:
1. finds Home Assistant's own IP,
2. edits the processor's IP table (`REMSlave 6 <control4>` / `ADDSlave 6 <home assistant>`) over the telnet console,
3. **reboots the processor** (2-4 minutes; lights keep their levels, wall keypads keep working),
4. waits until the processor reports ONLINE and Home Assistant receives its hello,
5. moves the existing entity ids to this integration (old Control4 lights are renamed `<id>_control4_old` and disabled; name, icon, area, labels and voice exposure are copied),
6. sends a persistent notification and a phone notification (Configure > *Settings* > phone notification service; default `notify.mobile_app_iphone_rg`, empty = none).

If Home Assistant never comes online (firewall, network, wrong IP), the switch **undoes itself** (restores the Control4 address and reboots again).

## Return to Control4 (and disable this integration)
Configure > *Return to Control4* (also with a *Preview only* checkbox). It finds the Control4 server address from the Control4
integration already in Home Assistant (or the override under *Settings*; overrides must be IPv4 addresses), points the processor back at it, reboots,
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
| Label check | After every link-up the processor's name dump is compared with the table; a mismatch raises a Repairs issue (Settings > System > Repairs) and a phone message and shows on the Link sensor (`label_mismatches`, `label_check`). After a Home Assistant restart there is no name dump, so `label_check` is `no_dump` (nothing compared) |
| Lost packets | A light command with no ack within 1 s is sent once more; a frame the processor retransmits is acked but handled once (Link sensor: `commands_resent`, `duplicates_ignored`). Simulator only |
| Refresh from processor | Service `crestron_pac2m.refresh_from_processor` (stand-alone, dry run by default): Home Assistant goes silent for `pause_seconds` (default 180) so the processor drops the session, then reconnects; a new session brings a hello + full state dump, so light levels changed while HA was down become current. Link sensor attribute `refresh`: `paused`, `waiting`, `dump_received` or `no_dump`. Simulator only: how long the real processor needs is unknown |
| Keypads | Event entity **Digital join** fires `high`/`low` (attributes `join`, `join_hex`) for every digital join. Configure > *Learn a keypad button* waits for the next press and asks for a name; that button gets its own event entity with `press`/`release` (no reload, no code change). Configure > *Remove keypad buttons* removes them. No real keypad press captured yet |
| Refresh after a Home Assistant restart | Off by default (Configure > Settings). When ticked: in stand-alone mode, if the link comes up without a hello (HA restarted, the processor kept the session), *Refresh from processor* runs once after 30 s. Simulator only |
| Areas | At setup each light without an area goes into the Home Assistant area named like its room (name or alias, any case). Done once per light: areas you change or clear later are never touched. Create the areas first; rooms without an area are listed in the diagnostics |
| Light table file | Optional `crestron_pac2m_lights.yaml` in the Home Assistant config folder replaces the built-in table (see [docs/crestron_pac2m_lights.example.yaml](docs/crestron_pac2m_lights.example.yaml)). A wrong file stops the integration from loading with the reason shown; no file = built-in table |
| Diagnostics | Integration menu > Download diagnostics: link state, counters, levels, labels, last 100 frames and the IP table entry (read only) |
| Home Assistant IP changed | Stand-alone with the link down for 60 s: the IP table is read (read only) and a Repairs issue says if it points at another address; its *Fix* button runs Go stand-alone to re-point the processor (reboots it). Simulator only |
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
  lights.py     light table (built-in or crestron_pac2m_lights.yaml) repairs.py    Repairs fix for the address issue
  light.py binary_sensor.py event.py diagnostics.py discovery.py config_flow.py __init__.py const.py (every tunable value is in const.py)
  brand/        integration icon
tests/          pytest (real HA core + fake processor) and standalone_client_check.py
tools/          reference scripts used during discovery (probe4, theater restore, rollback, pcap parser, join JSON)
docs/           lessons learned, protocol, runbook, join map
```

## Run the tests
```
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python homeassistant pytest-homeassistant-custom-component
.venv/bin/python -m pytest
.venv/bin/ruff check .                          # also run by GitHub Actions with hassfest and the HACS check
python3 tests/standalone_client_check.py      # protocol + client only, no Home Assistant needed
```

## Safety rules learned the hard way
* Only the IP in the processor's IP table can control it. Two devices must never share the Control4 IP.
* IP table edits do nothing until the processor reboots.
* The processor ignores commands without the 30 s keep-alive (ping + digital join 4000 high).
* Test scripts change real lights: note the levels first, restore afterwards.
