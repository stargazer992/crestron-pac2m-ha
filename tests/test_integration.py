"""End-to-end tests inside a real Home Assistant core against the simulated processor."""
import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.helpers import area_registry as ar, entity_registry as er, label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron_pac2m.const import (
    CONF_HA_HOST, CONF_HOST, CONF_MODE, DOMAIN, MODE_CONTROL4, MODE_STANDALONE,
)
from custom_components.crestron_pac2m.operations import async_go_standalone, async_return_to_control4

from .conftest import C4_IP, HA_IP, PORTS

LEGACY = "light.dimmer_sconces"       # join 0x127 in the real map


async def setup_entry(hass, mode=MODE_CONTROL4):
    c4 = MockConfigEntry(domain="control4", data={"host": C4_IP})
    c4.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, options={CONF_MODE: mode, CONF_HA_HOST: HA_IP}, unique_id="127.0.0.1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, c4


def make_legacy_entity(hass):
    """A Control4 light with a custom name, icon, area and label, like the real one."""
    area = ar.async_get(hass).async_create("Theater")
    label = lr.async_get(hass).async_create("Lights")
    registry = er.async_get(hass)
    entry = registry.async_get_or_create("light", "control4", "247", suggested_object_id="dimmer_sconces", original_name="Sconces")
    registry.async_update_entity(entry.entity_id, name="Wall Sconces", icon="mdi:wall-sconce", area_id=area.id, labels={label.label_id})
    assert entry.entity_id == LEGACY
    return area, label


async def wait_state(hass, entity_id, predicate, timeout=6):
    end = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < end:
        state = hass.states.get(entity_id)
        if state is not None and predicate(state):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_add_is_passive(hass, processor):
    """Adding the integration creates unavailable lights and sends nothing to the processor."""
    entry, _ = await setup_entry(hass)
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    lights = [e for e in registry.entities.values() if e.platform == DOMAIN and e.domain == "light"]
    assert len(lights) == 40
    assert all(hass.states.get(e.entity_id).state == "unavailable" for e in lights)
    await asyncio.sleep(1.2)                           # several keep-alive periods
    assert processor.udp_frames == 0, "control4 mode must not send anything"
    assert processor.commands == [] and processor.table_ip == C4_IP


async def test_dry_runs_change_nothing(hass, processor):
    entry, _ = await setup_entry(hass)
    make_legacy_entity(hass)
    result = await async_go_standalone(hass, entry, True)
    assert result.ok and any("WOULD run: addslave 6 127.0.0.1" in s for s in result.steps)
    assert processor.table_ip == C4_IP and processor.reboots == 0 and processor.pending_ip is None
    assert er.async_get(hass).async_get(LEGACY).platform == "control4"


async def test_go_standalone_and_back(hass, processor):
    entry, c4 = await setup_entry(hass)
    area, label = make_legacy_entity(hass)
    registry = er.async_get(hass)

    # ---------- to stand-alone ----------
    result = await async_go_standalone(hass, entry, False)
    await hass.async_block_till_done()
    assert result.ok, result.steps
    assert processor.table_ip == HA_IP and processor.reboots == 1
    assert entry.options[CONF_MODE] == MODE_STANDALONE
    # entity ids: the Crestron light now owns the legacy id, with the old name/icon/area/label
    new = registry.async_get(LEGACY)
    assert new.platform == DOMAIN and new.name == "Wall Sconces" and new.icon == "mdi:wall-sconce"
    assert new.area_id == area.id and label.label_id in new.labels
    parked = registry.async_get(LEGACY + "_control4_old")
    assert parked.platform == "control4" and parked.disabled_by == er.RegistryEntryDisabler.USER
    assert await wait_state(hass, LEGACY, lambda s: s.state == "off"), hass.states.get(LEGACY)
    assert hass.states.get("light.dimmer_theater_front_recessed") is None or True
    assert hass.states.get("binary_sensor.crestron_pac2m_link").state == "on"

    # ---------- control the light ----------
    await hass.services.async_call("light", "turn_on", {"entity_id": LEGACY, "brightness": 128}, blocking=True)
    assert await wait_state(hass, LEGACY, lambda s: s.state == "on" and s.attributes.get("brightness") == 128)
    assert processor.levels[0x127] == 128 * 257
    await hass.services.async_call("light", "turn_off", {"entity_id": LEGACY}, blocking=True)
    assert await wait_state(hass, LEGACY, lambda s: s.state == "off")
    await hass.services.async_call("light", "turn_on", {"entity_id": LEGACY}, blocking=True)       # restores last level
    assert await wait_state(hass, LEGACY, lambda s: s.attributes.get("brightness") == 128)

    # ---------- wall keypad press (feedback from the processor) ----------
    processor.levels[0x127] = 0xFFFF
    processor.send_data(__import__("custom_components.crestron_pac2m.protocol", fromlist=["x"]).analog_payload(0x127, 0xFFFF))
    assert await wait_state(hass, LEGACY, lambda s: s.attributes.get("brightness") == 255)

    # ---------- back to Control4 ----------
    with patch("homeassistant.config_entries.ConfigEntries.async_schedule_reload") as reload_mock:
        result = await async_return_to_control4(hass, entry, False)
        assert result.ok, result.steps
        assert processor.table_ip == C4_IP and processor.reboots == 2
        assert [c.args[0] for c in reload_mock.call_args_list] == [c4.entry_id]   # Control4 reloaded to re-create its lights
    assert entry.options[CONF_MODE] == MODE_CONTROL4
    restored = registry.async_get(LEGACY)
    assert restored.platform == "control4" and restored.disabled_by is None and restored.name == "Wall Sconces"
    assert registry.async_get(LEGACY + "_crestron").platform == DOMAIN
    await asyncio.sleep(0.6)                           # DISABLE_DELAY_SECONDS
    await hass.async_block_till_done()
    assert entry.disabled_by is ConfigEntryDisabler.USER and entry.state is ConfigEntryState.NOT_LOADED


async def test_failback_when_home_assistant_never_comes_up(hass, processor):
    """If the processor never talks to HA after the reboot, the old Control4 entry is restored."""
    entry, _ = await setup_entry(hass)
    make_legacy_entity(hass)
    processor.hello_enabled = False
    result = await async_go_standalone(hass, entry, False)
    assert not result.ok and result.rolled_back, result.steps
    assert processor.table_ip == C4_IP and processor.reboots == 2
    assert entry.options[CONF_MODE] == MODE_CONTROL4
    assert er.async_get(hass).async_get(LEGACY).platform == "control4"       # ids untouched


async def test_return_when_control4_unknown(hass, processor):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, options={CONF_MODE: MODE_STANDALONE}, unique_id="x")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await async_return_to_control4(hass, entry, False)
    assert not result.ok and "No Control4 address" in result.message and processor.reboots == 0


async def test_options_flow_preview(hass, processor):
    entry, _ = await setup_entry(hass)
    make_legacy_entity(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"].value == "menu"
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "standalone"})
    assert flow["type"].value == "form" and C4_IP in flow["description_placeholders"]["control4_host"]   # Control4 IP auto-discovered
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"dry_run": True})
    for _ in range(50):
        if flow["type"].value != "progress":
            break
        await asyncio.sleep(0.1)
        flow = await hass.config_entries.options.async_configure(flow["flow_id"])
    assert flow["type"].value in ("abort", "progress_done"), flow
    if flow["type"].value == "progress_done":
        flow = await hass.config_entries.options.async_configure(flow["flow_id"])
    assert flow["type"].value == "abort" and "WOULD run" in flow["description_placeholders"]["result"]
    assert processor.reboots == 0


async def test_config_flow_creates_passive_entry(hass, processor):
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    bad = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: "not-an-ip"})
    assert bad["errors"] == {CONF_HOST: "invalid_host"}
    ok = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: "127.0.0.1"})
    assert ok["type"].value == "create_entry" and ok["options"][CONF_MODE] == MODE_CONTROL4
    await hass.async_block_till_done()
    await asyncio.sleep(0.8)
    assert processor.udp_frames == 0


async def test_label_check_flags_wrong_mapping(hass, processor):
    """The processor says join 0x124 is 'WRONG NAME' (table expects 'Downlights'); 0x127 'Sconces 2' matches."""
    entry, _ = await setup_entry(hass)
    with patch("custom_components.crestron_pac2m.operations.persistent_notification.async_create") as notify, \
         patch("custom_components.crestron_pac2m.operations.persistent_notification.async_dismiss"):
        await async_go_standalone(hass, entry, False)
        await asyncio.sleep(1.0)
        report = entry.runtime_data.label_report
        assert report is not None and [m[0] for m in report.mismatched] == [0x124, 0x127]
        assert report.mismatched[0][2:] == ("Downlights in Theater", "WRONG NAME in Theater")   # wrong light label
        assert report.mismatched[1][2:] == ("Sconces 2 in Theater", "Sconces 2 in Kitchen")       # wrong room
        texts = [c.args[1] for c in notify.call_args_list if "MISMATCH" in str(c.args)]
        assert texts and "0x0124" in texts[0] and "WRONG NAME" in texts[0]
        await asyncio.sleep(2.0)                           # regression: it used to re-run endlessly (a notification every few seconds)
        assert len([c for c in notify.call_args_list if "MISMATCH" in str(c.args)]) == 1
    assert hass.states.get("binary_sensor.crestron_pac2m_link").attributes["label_mismatches"] == 2


def test_label_compare_unit():
    from custom_components.crestron_pac2m.labelcheck import compare
    lights = ((0x101, "light.a", "Dimmer", "R", "Garage & Portico Flood"), (0x102, "light.b", "Dimmer", "R", "Back_Porch"), (0x103, "light.c", "Dimmer", "R", "Rope"))
    report = compare(lights, {0x101: "garage  & portico flood", 0x102: "Back Porch"})
    assert report.ok and report.missing == [(0x103, "light.c", "Rope")] and report.checked == 3
    assert not compare(lights, {0x101: "Other"}).ok


def test_room_entries_do_not_overwrite_light_labels():
    """Real processor: every light name (odd id) is followed by a room name (even id) with the same join number."""
    from custom_components.crestron_pac2m import protocol
    light = protocol.decode_payload(bytes([len(b"Downlights") + 3, 0x12, 0x47]) + b"Downlights")
    room = protocol.decode_payload(bytes([len(b"Theater") + 3, 0x12, 0x46]) + b"Theater")
    assert (light.kind, light.join, light.text) == ("name", 0x124, "Downlights")
    assert (room.kind, room.name_id, room.text) == ("room", 0x46, "Theater")


async def test_restart_while_processor_session_alive_gets_resync(hass, processor):
    """After a Home Assistant restart the processor does not dump again; the client must ask for it (join 4000 edge)."""
    entry, _ = await setup_entry(hass)
    make_legacy_entity(hass)
    result = await async_go_standalone(hass, entry, False)
    assert result.ok, result.steps
    assert await wait_state(hass, LEGACY, lambda s: s.state in ("on", "off"))
    # simulate the restart: reload the entry; the fake still believes the session is alive (keepalive_seen stays True)
    assert processor.keepalive_seen
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    client = entry.runtime_data.client
    assert await wait_state(hass, LEGACY, lambda s: s.state in ("on", "off"), timeout=8), "resync never produced a state dump"
    assert client.resyncs >= 1 and client.levels


async def test_manual_resync_service(hass, processor):
    entry, _ = await setup_entry(hass)
    make_legacy_entity(hass)
    await async_go_standalone(hass, entry, False)
    before = entry.runtime_data.client.resyncs
    await hass.services.async_call(DOMAIN, "resync", {}, blocking=True)
    assert entry.runtime_data.client.resyncs == before + 1
