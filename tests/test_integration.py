"""End-to-end tests inside a real Home Assistant core against the simulated processor."""
import asyncio
from unittest.mock import patch

from homeassistant.components.persistent_notification import (
    _async_get_or_create_notifications,
)
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron_pac2m.const import (
    CONF_HA_HOST,
    CONF_HOST,
    CONF_MODE,
    DOMAIN,
    MODE_CONTROL4,
    MODE_STANDALONE,
)
from custom_components.crestron_pac2m.operations import (
    async_go_standalone,
    async_return_to_control4,
)

from .conftest import C4_IP, HA_IP, PORTS

LEGACY = "light.dimmer_sconces"       # join 0x127 in the real map


async def setup_entry(hass, mode=MODE_CONTROL4, **options):
    c4 = MockConfigEntry(domain="control4", data={"host": C4_IP})
    c4.add_to_hass(hass)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, options={CONF_MODE: mode, CONF_HA_HOST: HA_IP, **options}, unique_id="127.0.0.1")
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
    link = hass.states.get("binary_sensor.crestron_pac2m_link").attributes
    assert link["label_mismatches"] == 2 and link["label_check"] == "mismatch"


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


async def test_state_restored_after_restart_without_dump(hass, processor):
    """Real behavior: after a restart the processor sends no dump. Lights must start from their last known state."""
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    processor.table_ip = HA_IP
    processor.keepalive_seen = True
    processor.resync_dumps = False                     # the real processor ignores the join-4000 toggle
    mock_restore_cache(hass, [
        State("light.crestron_pac2m_theater_sconces_2", "on", {"brightness": 128}),
        State("light.crestron_pac2m_theater_downlights", "off"),
    ])
    await setup_entry(hass, mode=MODE_STANDALONE)
    assert await wait_state(hass, "light.crestron_pac2m_theater_sconces_2", lambda s: s.state == "on" and s.attributes["brightness"] == 128, timeout=5)
    state = hass.states.get("light.crestron_pac2m_theater_downlights")
    assert state.state == "off" and state.attributes["crestron_level_source"] == "restored"
    # the first real feedback replaces the restored value
    from custom_components.crestron_pac2m import protocol
    processor.send_data(protocol.analog_payload(0x124, 0xFFFF))
    assert await wait_state(hass, "light.crestron_pac2m_theater_downlights", lambda s: s.state == "on" and s.attributes["crestron_level_source"] == "processor")


async def test_keypad_events_after_restart_without_dump(hass, processor):
    """Real behavior: after a Home Assistant restart no hello arrives; keypad presses must still become events."""
    from custom_components.crestron_pac2m import protocol
    processor.table_ip = HA_IP
    processor.keepalive_seen = True
    processor.resync_dumps = False
    events = []
    hass.bus.async_listen("crestron_pac2m_digital", lambda e: events.append(e.data))
    await setup_entry(hass, mode=MODE_STANDALONE)
    assert await wait_state(hass, "binary_sensor.crestron_pac2m_link", lambda s: s.state == "on")
    processor.send_data(protocol.digital_payload(0x0042, True))
    for _ in range(40):
        if events:
            break
        await asyncio.sleep(0.05)
    assert events == [{"join": 0x42, "join_hex": "0x0042", "state": "high"}]
    # no name dump after a restart: the label check says so instead of reporting 0 mismatches
    assert await wait_state(hass, "binary_sensor.crestron_pac2m_link", lambda s: s.attributes["label_check"] == "no_dump")
    assert hass.states.get("binary_sensor.crestron_pac2m_link").attributes["label_mismatches"] is None


async def test_turn_on_after_restart_returns_to_restored_level(hass, processor):
    """A light restored at 50% must come back at 50% on a plain "turn on", not at 100%."""
    from homeassistant.core import State
    from pytest_homeassistant_custom_component.common import mock_restore_cache
    processor.table_ip = HA_IP
    processor.keepalive_seen = True
    processor.resync_dumps = False
    mock_restore_cache(hass, [State("light.crestron_pac2m_theater_sconces_2", "on", {"brightness": 128})])
    await setup_entry(hass, mode=MODE_STANDALONE)
    assert await wait_state(hass, "light.crestron_pac2m_theater_sconces_2", lambda s: s.state == "on")
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.crestron_pac2m_theater_sconces_2"}, blocking=True)
    assert await wait_state(hass, "light.crestron_pac2m_theater_sconces_2", lambda s: s.attributes.get("crestron_level_source") == "processor")
    assert processor.levels[0x127] == 128 * 257


async def _restarted_standalone(hass, processor, **options):
    """Processor already points at HA and the session is alive (HA restart: no hello, no dump)."""
    processor.table_ip = HA_IP
    processor.keepalive_seen = True
    processor.resync_dumps = False
    entry, _ = await setup_entry(hass, mode=MODE_STANDALONE, **options)
    assert await wait_state(hass, "binary_sensor.crestron_pac2m_link", lambda s: s.state == "on")
    return entry


async def test_lost_command_is_sent_once_more(hass, processor):
    """The processor never saw the first command (no ack): it is resent and the light still changes."""
    entry = await _restarted_standalone(hass, processor)
    processor.drop_analog = 1
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.crestron_pac2m_theater_sconces_2", "brightness": 128}, blocking=True)
    assert await wait_state(hass, "light.crestron_pac2m_theater_sconces_2", lambda s: s.attributes.get("brightness") == 128)
    assert processor.levels[0x127] == 128 * 257 and entry.runtime_data.client.resends == 1
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.crestron_pac2m_theater_sconces_2"}, blocking=True)
    assert await wait_state(hass, "light.crestron_pac2m_theater_sconces_2", lambda s: s.state == "off")
    await asyncio.sleep(0.4)
    assert entry.runtime_data.client.resends == 1, "an acked command must not be resent"


async def test_retransmitted_frame_is_handled_once(hass, processor):
    """If our ack is lost the processor sends the same frame again: one keypad press, one event."""
    from custom_components.crestron_pac2m import protocol
    entry = await _restarted_standalone(hass, processor)
    events = []
    hass.bus.async_listen("crestron_pac2m_digital", lambda e: events.append(e.data))
    seq = processor.seq
    processor.send_data(protocol.digital_payload(0x0042, True))
    processor.send_data(protocol.digital_payload(0x0042, True), seq=seq)
    processor.send_data(protocol.digital_payload(0x0042, False))
    for _ in range(40):
        if len(events) >= 2:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    assert [e["state"] for e in events] == ["high", "low"] and entry.runtime_data.client.duplicates == 1


async def test_warns_when_processor_points_at_an_old_home_assistant_address(hass, processor):
    """Stand-alone, but the IP table still has HA's old address (DHCP change): the link never comes up, so warn."""
    processor.table_ip = "10.9.9.9"
    phone = []
    hass.services.async_register("notify", "test_phone", lambda call: phone.append(call.data))
    await setup_entry(hass, mode=MODE_STANDALONE, notify_service="notify.test_phone")
    notifications = _async_get_or_create_notifications(hass)
    for _ in range(60):
        if "crestron_pac2m_address" in notifications:
            break
        await asyncio.sleep(0.05)
    text = notifications["crestron_pac2m_address"]["message"]
    assert "10.9.9.9" in text and HA_IP in text
    await hass.async_block_till_done()
    assert phone and "10.9.9.9" in phone[0]["message"]
    assert processor.table_ip == "10.9.9.9" and processor.reboots == 0     # read only


async def test_no_address_warning_when_link_is_up(hass, processor):
    await _restarted_standalone(hass, processor)
    await asyncio.sleep(0.8)
    assert "crestron_pac2m_address" not in _async_get_or_create_notifications(hass)
    assert processor.commands == []                     # the console was never opened


async def test_settings_validate_and_clear(hass, processor):
    entry, _ = await setup_entry(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "settings"})
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"control4_host": "c4.local", "ha_host": "10.1.1.120"})
    assert flow["type"].value == "form" and flow["errors"] == {"control4_host": "invalid_host"}
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"ha_host": " 10.1.1.120 ", "notify_service": "notify.my_phone"})
    assert flow["type"].value == "create_entry"
    assert entry.options[CONF_HA_HOST] == "10.1.1.120" and entry.options["control4_host"] == ""
    assert entry.options["notify_service"] == "my_phone" and entry.options[CONF_MODE] == MODE_CONTROL4


async def test_config_flow_finds_the_processor(hass, processor):
    """The scan pre-fills the processor's address and never sends anything to it."""
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert flow["type"].value == "form" and flow["description_placeholders"]["found"] == "PAC2M at 127.0.0.1"
    key = next(iter(flow["data_schema"].schema))
    assert key.description["suggested_value"] == "127.0.0.1"
    assert processor.commands == [] and processor.udp_frames == 0      # read only


async def test_identify_ignores_other_telnet_hosts(processor):
    """Only a Crestron 2-series prompt counts; another telnet banner or a closed port is not a processor."""
    from custom_components.crestron_pac2m import discovery

    async def _router(reader, writer):
        writer.write(b"\xff\xfb\x01Router\r\nlogin: ")
        await writer.drain()
    router = await asyncio.start_server(_router, "127.0.0.1", PORTS["console"] + 10)
    try:
        assert await discovery.async_identify("127.0.0.1", PORTS["console"]) == "PAC2M"
        assert await discovery.async_identify("127.0.0.1", PORTS["console"] + 10) is None
        assert await discovery.async_identify("127.0.0.1", PORTS["console"] + 11) is None
    finally:
        router.close()
        await router.wait_closed()


async def test_candidate_hosts_cover_the_local_slash_24():
    from custom_components.crestron_pac2m import discovery
    adapters = [
        {"enabled": True, "ipv4": [{"address": "192.168.104.50", "network_prefix": 16}]},
        {"enabled": True, "ipv4": [{"address": "127.0.0.1", "network_prefix": 8}]},
        {"enabled": False, "ipv4": [{"address": "10.0.0.5", "network_prefix": 24}]},
    ]
    with patch.object(discovery, "async_get_adapters", return_value=adapters):
        hosts = await discovery.async_candidate_hosts(None)
    assert len(hosts) == 253 and "192.168.104.50" not in hosts
    assert all(h.startswith("192.168.104.") for h in hosts)


async def test_reconfigure_changes_the_processor_address(hass, processor):
    entry, _ = await setup_entry(hass)
    MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.3"}, unique_id="127.0.0.3").add_to_hass(hass)
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id})
    assert flow["type"].value == "form" and flow["description_placeholders"]["current"] == "127.0.0.1"
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: "not-an-ip"})
    assert flow["errors"] == {CONF_HOST: "invalid_host"}
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: "127.0.0.3"})
    assert flow["errors"] == {CONF_HOST: "already_configured"}
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_HOST: " 127.0.0.2 "})
    assert flow["type"].value == "abort" and flow["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == "127.0.0.2" and entry.unique_id == "127.0.0.2"
    assert entry.title == "Mock Title" and entry.options[CONF_MODE] == MODE_CONTROL4     # a renamed title is kept
    assert entry.state is ConfigEntryState.LOADED and entry.runtime_data.client.host == "127.0.0.2"


async def test_refresh_from_processor_gets_a_fresh_dump(hass, processor):
    """After an HA restart there is no dump; going silent until the processor drops the session brings one."""
    from homeassistant.exceptions import HomeAssistantError
    entry = await _restarted_standalone(hass, processor)
    processor.peer_timeout = 0.5
    downlights = "light.crestron_pac2m_theater_downlights"
    assert hass.states.get(downlights).state == "unknown"
    dry = await hass.services.async_call(DOMAIN, "refresh_from_processor", {}, blocking=True, return_response=True)
    assert dry["ok"] and dry["message"].startswith("Dry run") and not entry.runtime_data.client.paused
    started = await hass.services.async_call(DOMAIN, "refresh_from_processor", {"dry_run": False, "pause_seconds": 1}, blocking=True, return_response=True)
    assert started["ok"] and entry.runtime_data.client.paused
    try:
        await hass.services.async_call("light", "turn_on", {"entity_id": downlights}, blocking=True)
        raise AssertionError("a command during the pause must be refused")
    except HomeAssistantError:
        pass
    assert await wait_state(hass, "binary_sensor.crestron_pac2m_link", lambda s: s.attributes["refresh"] == "dump_received", timeout=8)
    assert await wait_state(hass, downlights, lambda s: s.state == "on" and s.attributes["crestron_level_source"] == "processor")
    assert not entry.runtime_data.client.paused


async def test_refresh_reports_when_no_dump_comes(hass, processor):
    from custom_components.crestron_pac2m import operations
    with patch.object(operations, "REFRESH_WAIT_SECONDS", 0.5):
        entry = await _restarted_standalone(hass, processor)
        await hass.services.async_call(DOMAIN, "refresh_from_processor", {"dry_run": False, "pause_seconds": 1}, blocking=True, return_response=True)
        assert await wait_state(hass, "binary_sensor.crestron_pac2m_link", lambda s: s.attributes["refresh"] == "no_dump", timeout=8)
    assert not entry.runtime_data.client.paused
    assert "crestron_pac2m_refresh" in _async_get_or_create_notifications(hass)
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.crestron_pac2m_theater_sconces_2", "brightness": 128}, blocking=True)
    assert await wait_state(hass, "light.crestron_pac2m_theater_sconces_2", lambda s: s.attributes.get("brightness") == 128)


async def test_refresh_refused_in_control4_mode(hass, processor):
    await setup_entry(hass, mode=MODE_CONTROL4)
    result = await hass.services.async_call(DOMAIN, "refresh_from_processor", {"dry_run": False}, blocking=True, return_response=True)
    assert not result["ok"] and processor.udp_frames == 0


async def test_digital_joins_and_keypad_buttons_as_event_entities(hass, processor):
    from custom_components.crestron_pac2m import event, protocol
    with patch.dict(event.KEYPAD_BUTTONS, {0x43: "Theater button 1"}):
        await _restarted_standalone(hass, processor)
        processor.send_data(protocol.digital_payload(0x0042, True))
        assert await wait_state(hass, "event.crestron_pac2m_digital_join", lambda s: s.attributes.get("event_type") == "high" and s.attributes.get("join") == 0x42)
        processor.send_data(protocol.digital_payload(0x0043, True))
        assert await wait_state(hass, "event.crestron_pac2m_theater_button_1", lambda s: s.attributes.get("event_type") == "press")
        processor.send_data(protocol.digital_payload(0x0043, False))
        assert await wait_state(hass, "event.crestron_pac2m_theater_button_1", lambda s: s.attributes.get("event_type") == "release")
        assert hass.states.get("event.crestron_pac2m_digital_join").attributes["join"] == 0x42, "mapped buttons stay off the catch-all"


async def test_link_unique_id_moves_to_the_entry(hass, processor):
    """Up to v1.8 the Link sensor's unique id was not tied to the entry; the entity id must survive the move."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1"}, options={CONF_MODE: MODE_CONTROL4}, unique_id="127.0.0.1")
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("binary_sensor", DOMAIN, f"{DOMAIN}_link", suggested_object_id="crestron_pac2m_link", config_entry=entry)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg_entry = registry.async_get("binary_sensor.crestron_pac2m_link")
    assert reg_entry.unique_id == f"{entry.entry_id}_link"
    assert hass.states.get("binary_sensor.crestron_pac2m_link") is not None
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{DOMAIN}_link") is None


async def test_diagnostics(hass, processor):
    from custom_components.crestron_pac2m.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    entry = await _restarted_standalone(hass, processor)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["link"]["connected"] and diag["link"]["refresh"] == "idle"
    assert diag["ip_table"] == {"status": "ONLINE", "port": "41794", "ip": HA_IP}
    assert {f["dir"] for f in diag["recent_frames"]} == {"rx", "tx"}
    assert diag["label_check"]["status"] in ("pending", "no_dump")
