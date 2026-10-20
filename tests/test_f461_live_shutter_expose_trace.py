"""Replay and verification tests for the 2026-10-18 live BTicino F461 capture.

Fixture:
  tests/fixtures/traces/f461_live_2026-10-18/myhome_trace_F461_live_2026-10-18.json

Covers:
1. Expanded F461 physical plant:
   - BT-F411/4 lights at WHERE="06" and WHERE="07", Group #1 (*1*1*#1##, *1*0*#1##,
     *#1*#1##, and aggregate status *#1*#1*15*0## / *#1*#1*15*1##), and General 0
     (*1*1*0##, *1*0*0##) with post-broadcast resync polling.
   - BT-F411/2 5-second timed shutter at WHERE="05": partial 2.0 s UP + STOP,
     mid-flight direction reversal (UP -> DOWN) with gateway pre-move STOP
     translation (*2*1000#0*05##), and automatic 5.0 s hardware end-of-travel
     STOP (*2*0*05##).
   - Living Now KW4691 standalone thermostat + F430/2 + F430/4 at Zone 2.
2. Live F461 bus verification of PR #750 declarative Expose (WHO=1, 2, 4, 13, 18):
   - WHO=2 virtual cover at WHERE="25": gateway emits *2*1000#0*25## + *2*1000#1/2*25##
     on the event monitor session before returning NACK (~0.55 s) on the command
     session; ExposeManager's _pre_move_stop_guard and _tx_echo_guard absorb both
     frames without self-oscillation.
   - WHO=1 populated (06) vs unpopulated virtual (08) state push & Dim #1 write.
   - WHO=4 Dimension 0 read-vs-write (*#4*2*0*0999## parsed as read returning
     *#4*2*0*0249## vs *#4*50*#0*0215## write ACKed in 155 ms).
   - WHO=13 clock/date queries and *#13**#0*...## write vs *#13**0*...## report.
   - WHO=18 virtual energy meter at WHERE="51" (Dims 113, 51, 54, and 1200 streaming).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from OWNd.profiles import F461Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, CONF_WHERE, DOMAIN
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.router import FrameRouter
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:04:97"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_live_2026-10-18"
    / "myhome_trace_F461_live_2026-10-18.json"
)


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    """Load the 2026-10-18 F461 live trace archive."""
    assert FIXTURE.is_file(), FIXTURE
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(trace_payload: dict) -> list[dict]:
    """Return the chronological session log rows."""
    return trace_payload["rows"]


def _result_rows(rows: list[dict], run: str, frame: str | None = None) -> list[dict]:
    return [
        r
        for r in rows
        if r["run"] == run
        and r["dir"] == "result"
        and (frame is None or r["frame"] == frame)
    ]


def _gateway(model: str = "F461") -> MagicMock:
    gw = MagicMock()
    gw.mac = MAC
    gw.unique_id = MAC
    gw.bus_group = MAC
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.log_id = f"[{model} live 2026-10-18]"
    gw.model = model
    gw.has_central_unit = False
    gw.supports_zone_status = True
    gw.initial_discovery_pending = False
    gw.is_connected = True
    gw.profile = F461Profile()
    gw._gateway = SimpleNamespace(profile=gw.profile)
    gw.sent: list[str] = []

    async def _send(cmd, *a, **k):
        gw.sent.append(str(cmd))
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        fut.set_result(True)
        return fut

    gw.send = AsyncMock(side_effect=_send)
    gw.send_status_request = AsyncMock(side_effect=_send)
    return gw


async def _setup_plant(hass: HomeAssistant, gw: MagicMock) -> dict[str, object]:
    from custom_components.myhome.climate import async_setup_entry as climate_setup
    from custom_components.myhome.cover import async_setup_entry as cover_setup
    from custom_components.myhome.light import async_setup_entry as light_setup

    platforms_cfg = {
        "light": {
            "light_1": {CONF_WHERE: "06", CONF_NAME: "Light 1", "model": "F411"},
            "light_2": {CONF_WHERE: "07", CONF_NAME: "Light 2", "model": "F411"},
        },
        "cover": {
            "shutter": {
                CONF_WHERE: "05",
                CONF_NAME: "Shutter",
                "advanced": True,
                "open_Best_Time": 5,
                "close_Best_Time": 5,
            },
        },
        "climate": {
            "thermo_office": {
                "who": "4",
                "zone": "2",
                "name": "Thermo office",
                "heat": True,
                "cool": True,
                "standalone": True,
                "central": False,
                "manufacturer": "BTicino",
                "model": "F461",
            }
        },
    }
    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_ENTITY: gw,
                CONF_PLATFORMS: platforms_cfg,
            }
        }
    }
    entry = MagicMock()
    entry.data = {"mac": MAC}
    entry.entry_id = "f461_live_2026_10_18"
    entry.options = {}
    attach_runtime(hass, entry, MAC, gw)

    lights: list = []
    covers: list = []
    climates: list = []
    with (
        patch(
            "custom_components.myhome.discovery.er.async_entries_for_config_entry",
            return_value=[],
        ),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await light_setup(hass, entry, lights.extend)
        await cover_setup(hass, entry, covers.extend)
        await climate_setup(hass, entry, climates.extend)

    for ent in lights + covers + climates:
        ent.hass = hass
        domain_prefix = (
            "climate"
            if ent in climates
            else ("cover" if ent in covers else "light")
        )
        ent.entity_id = f"{domain_prefix}.{ent._device_id}"
        ent.async_write_ha_state = MagicMock()
        ent.async_schedule_update_ha_state = MagicMock()
        attach_platform(ent, entry)

    return {ent._where: ent for ent in lights + covers + climates}


def test_f461_2026_10_18_trace_schema_and_ownd_parsing(
    trace_payload: dict, rows: list[dict]
) -> None:
    """Verify fixture metadata, RFC 5737 scrubbing, and OWNd parser coverage across all frames."""
    assert trace_payload["gateway"]["model"] == "F461"
    assert trace_payload["gateway"]["firmware"] == "2.0.11"
    assert trace_payload["gateway"]["ip"] == "192.0.2.92"
    assert trace_payload["gateway"]["mac"] == MAC
    assert trace_payload["gateway"]["identification"]["who13_code"] == "200"
    assert trace_payload["gateway"]["identification"]["who1013_code"] == "134"

    frames = trace_payload["frames"]
    assert len(frames) == 73
    assert len(rows) == 294

    for item in frames:
        assert item["direction"] == "rx"
        assert item["session"] == "evt"
        parsed = OWNMessage.parse(item["raw"])
        assert parsed is not None, f"Failed to parse evt frame: {item['raw']}"

    for r in rows:
        if r["dir"] == "tx":
            assert OWNCommand.parse(r["frame"]) is not None or OWNMessage.parse(r["frame"]) is not None
        elif r["dir"] == "rx":
            assert OWNMessage.parse(r["frame"]) is not None


@pytest.mark.asyncio
async def test_run1_lights_06_07_group1_dim15_and_general0_resync(
    hass: HomeAssistant, rows: list[dict]
) -> None:
    """Replay run1_lights_group_and_general through light entities 06 and 07 and PlatformDiscovery."""
    gw = _gateway()
    entities = await _setup_plant(hass, gw)
    light_06 = entities["06"]
    light_07 = entities["07"]

    # Verify gateway identification frames at start of run1
    id_res = _result_rows(rows, "run1_lights_group_and_general", "*#13**15##")[0]
    assert id_res["ack"] is True
    assert id_res["replies"] == ["*#13**15*200##"]
    fw_res = _result_rows(rows, "run1_lights_group_and_general", "*#13**16##")[0]
    assert fw_res["ack"] is True
    assert fw_res["replies"] == ["*#13**16*2*0*11##"]

    # Unpopulated old light addresses 01 and 04 NACK
    for old_where in ("01", "04"):
        old_res = _result_rows(rows, "run1_lights_group_and_general", f"*#1*{old_where}##")[0]
        assert old_res["ack"] is False

    # Step-by-step replay of point switching, Group #1, and General 0
    r1_results = [
        r for r in rows if r["run"] == "run1_lights_group_and_general" and r["dir"] == "result"
    ]
    by_note = {r["note"]: r for r in r1_results}

    def _feed_events(ev_list: list[str]) -> None:
        for raw in ev_list:
            msg = OWNMessage.parse(raw)
            assert msg is not None
            if getattr(msg, "who", None) == 1:
                w = getattr(msg, "where", None)
                if w in ("06", "07"):
                    entities[w].handle_event(msg)

    # Initial polls: both 06 and 07 are OFF
    _feed_events(by_note["poll_light_06"]["events_after"])
    _feed_events(by_note["poll_light_07"]["events_after"])
    assert light_06.is_on is False
    assert light_07.is_on is False

    # Turn 06 ON -> emits *1*1*06## and Group #1 aggregate *#1*#1*15*1##
    assert by_note["light_06_on_emits_group1_dim15_1"]["events_after"] == [
        "*1*1*06##",
        "*#1*#1*15*1##",
    ]
    _feed_events(by_note["light_06_on_emits_group1_dim15_1"]["events_after"])
    assert light_06.is_on is True
    assert light_07.is_on is False

    # Turn 06 OFF -> emits *1*0*06## and Group #1 aggregate *#1*#1*15*0##
    assert by_note["light_06_off_emits_group1_dim15_0"]["events_after"] == [
        "*1*0*06##",
        "*#1*#1*15*0##",
    ]
    _feed_events(by_note["light_06_off_emits_group1_dim15_0"]["events_after"])
    assert light_06.is_on is False

    # Turn 07 ON and OFF -> emits *#1*#1*15*1## then *#1*#1*15*0##
    _feed_events(by_note["light_07_on_emits_group1_dim15_1"]["events_after"])
    assert light_07.is_on is True
    _feed_events(by_note["light_07_off_emits_group1_dim15_0"]["events_after"])
    assert light_07.is_on is False

    # Group #1 poll (*#1*#1##) returns individual states for both 06 and 07
    assert by_note["poll_group_1_returns_06_and_07"]["replies"] == [
        "*1*0*06##",
        "*1*0*07##",
    ]

    # Group #1 ON (*1*1*#1##) emits individual status frames for both 06 and 07
    assert set(by_note["group_1_on_emits_06_07_and_dim15_1"]["events_after"]) == {
        "*1*1*#1##",
        "*1*1*06##",
        "*1*1*07##",
        "*#1*#1*15*1##",
    }
    _feed_events(by_note["group_1_on_emits_06_07_and_dim15_1"]["events_after"])
    assert light_06.is_on is True
    assert light_07.is_on is True

    # Group #1 OFF (*1*0*#1##) turns both 06 and 07 OFF
    _feed_events(by_note["group_1_off_emits_06_07_and_dim15_0"]["events_after"])
    assert light_06.is_on is False
    assert light_07.is_on is False

    # General ON (*1*1*0##) emits only *1*1*0## and *#1*#1*15*1##; resync polls confirm 06 & 07 are ON
    assert by_note["general_on_emits_dim15_1_only"]["events_after"] == [
        "*1*1*0##",
        "*#1*#1*15*1##",
    ]
    _feed_events(by_note["resync_poll_06_after_general_on"]["events_after"])
    _feed_events(by_note["resync_poll_07_after_general_on"]["events_after"])
    assert light_06.is_on is True
    assert light_07.is_on is True

    # General OFF (*1*0*0##) followed by resync polls confirms 06 & 07 are OFF
    assert by_note["general_off_emits_dim15_0_only"]["events_after"] == [
        "*1*0*0##",
        "*#1*#1*15*0##",
    ]
    _feed_events(by_note["resync_poll_06_after_general_off"]["events_after"])
    _feed_events(by_note["resync_poll_07_after_general_off"]["events_after"])
    assert light_06.is_on is False
    assert light_07.is_on is False


@pytest.mark.asyncio
async def test_run2_shutter_05_partial_run_reversal_and_5s_hardware_autostop(
    hass: HomeAssistant, rows: list[dict]
) -> None:
    """Replay run2_shutter_05_travel_and_reversal through the F411/2 cover entity at WHERE=05."""
    gw = _gateway()
    entities = await _setup_plant(hass, gw)
    shutter = entities["05"]

    r2_results = [
        r
        for r in rows
        if r["run"] == "run2_shutter_05_travel_and_reversal" and r["dir"] == "result"
    ]
    by_note = {r["note"]: r for r in r2_results}

    # 1. Initial status is stopped (*2*0*05##)
    assert by_note["initial_shutter_05_status"]["replies"] == ["*2*0*05##"]

    # 2. Raising shutter (*2*1*05##) emits pre-move STOP translation, STOP status, UP translation, UP status
    up_events = by_note["raise_shutter_05_for_2s"]["events_after"]
    assert up_events == [
        "*2*1000#0*05##",
        "*2*0*05##",
        "*2*1000#1*05##",
        "*2*1*05##",
    ]
    for raw in up_events:
        msg = OWNMessage.parse(raw)
        if not getattr(msg, "is_translation", False):
            shutter.handle_event(msg)
    assert shutter.is_opening is True

    # 3. Manual STOP after 2s (*2*0*05##) emits *2*1000#0*05## and *2*0*05##
    stop_events = by_note["stop_shutter_05_after_2s"]["events_after"]
    assert stop_events == ["*2*1000#0*05##", "*2*0*05##"]
    for raw in stop_events:
        msg = OWNMessage.parse(raw)
        if not getattr(msg, "is_translation", False):
            shutter.handle_event(msg)
    assert shutter.is_opening is False
    assert shutter.is_closing is False

    # 4. Mid-flight reversal (UP -> DOWN) followed by 5s hardware auto-stop (*2*0*05##)
    rev_events = by_note["reverse_to_down_and_wait_for_5s_hardware_autostop"]["events_after"]
    assert rev_events == [
        "*2*1000#0*05##",
        "*2*0*05##",
        "*2*1000#2*05##",
        "*2*2*05##",
        "*2*0*05##",
    ]
    # Verify exact timing of the spontaneous 5s hardware auto-stop in the raw evt rows
    r2_evt = [
        r
        for r in rows
        if r["run"] == "run2_shutter_05_travel_and_reversal"
        and r["dir"] == "rx"
        and r["session"] == "evt"
        and r["step"] == "reversal_and_autostop"
    ]
    down_start_t = next(r["t"] for r in r2_evt if r["frame"] == "*2*2*05##")
    auto_stop_t = r2_evt[-2]["t"]
    assert r2_evt[-2]["frame"] == "*2*0*05##"
    assert 5.0 <= (auto_stop_t - down_start_t) <= 6.5

    for raw in rev_events[:-1]:
        msg = OWNMessage.parse(raw)
        if not getattr(msg, "is_translation", False):
            shutter.handle_event(msg)
    assert shutter.is_closing is True

    shutter.handle_event(OWNMessage.parse(rev_events[-1]))
    assert shutter.is_closing is False
    assert shutter.is_opening is False


@pytest.mark.asyncio
async def test_run3_climate_zone2_status_and_actuators(
    hass: HomeAssistant, rows: list[dict]
) -> None:
    """Replay run3_climate_zone2 through the standalone KW4691 climate entity at Zone 2."""
    gw = _gateway()
    entities = await _setup_plant(hass, gw)
    zone2 = entities["2"]

    r3_evt = [
        r
        for r in rows
        if r["run"] == "run3_climate_zone2"
        and r["dir"] == "rx"
        and r["session"] == "evt"
    ]
    for r in r3_evt:
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None
        if getattr(msg, "who", None) == 4 and getattr(msg, "zone", None) == 2:
            zone2.handle_event(msg)

    assert zone2.current_temperature == 24.9
    assert zone2.target_temperature == 24.5
    assert zone2.hvac_mode == HVACMode.HEAT
    assert zone2.extra_state_attributes["fan_coil_cooling"] is True
    assert zone2.extra_state_attributes["fan_coil_heating"] is False


@pytest.mark.asyncio
async def test_run4_expose_live_f461_protocol_and_echo_guard_replay(
    hass: HomeAssistant, rows: list[dict]
) -> None:
    """Verify the live F461 Expose run4 verdicts and replay them through ExposeManager."""
    r4_results = [
        r
        for r in rows
        if r["run"] == "run4_expose_virtual_bus_verification" and r["dir"] == "result"
    ]
    by_note = {r["note"]: r for r in r4_results}

    # 1. WHO=1 populated (06) ACKs and echoes on evt; unpopulated (08) NACKs without retry on s:, while Dim #1 write ACKs
    assert by_note["expose_who1_on_populated_06"]["ack"] is True
    assert "*1*1*06##" in by_note["expose_who1_on_populated_06"]["events_after"]
    assert by_note["expose_who1_on_virtual_08_no_retry"]["ack"] is False
    assert by_note["expose_who1_on_virtual_08_no_retry"]["reply_time_s"] < 2.5
    assert by_note["expose_who1_write_brightness_virtual_08_acked"]["ack"] is True

    # 2. WHO=2 unpopulated virtual cover (25) emits pre-move STOP (*2*1000#0*25##) + move (*2*1000#1/2*25##) on evt!
    assert by_note["expose_who2_raise_virtual_25_emits_premove_stop_and_up_on_evt"]["ack"] is False
    assert by_note["expose_who2_raise_virtual_25_emits_premove_stop_and_up_on_evt"]["events_after"] == [
        "*2*1000#0*25##",
        "*2*1000#1*25##",
    ]
    assert by_note["expose_who2_lower_virtual_25_emits_premove_stop_and_down_on_evt"]["events_after"] == [
        "*2*1000#0*25##",
        "*2*1000#2*25##",
    ]
    assert by_note["expose_who2_stop_virtual_25_emits_stop_translation_on_evt"]["events_after"] == [
        "*2*1000#0*25##",
    ]
    assert by_note["query_who2_dim10_virtual_25_acked"]["ack"] is True

    # 3. WHO=4: *#4*2*0*0999## (without #0) is parsed by bt_termo as a read request and returns *#4*2*0*0249##;
    #    *#4*50*#0*0215## (write_temperature with #0) is ACKed in ~155 ms!
    assert by_note["who4_dim0_without_hash_parsed_as_read_on_zone2"]["ack"] is True
    assert by_note["who4_dim0_without_hash_parsed_as_read_on_zone2"]["replies"] == [
        "*#4*2*0*0249##"
    ]
    assert by_note["expose_who4_report_temp_50_nack_timeout"]["ack"] is False
    assert by_note["expose_who4_write_temp_50_acked_156ms"]["ack"] is True
    assert by_note["expose_who4_write_temp_50_acked_156ms"]["reply_time_s"] < 0.5

    # 4. WHO=13: clock queries ACK and echo on evt; *#13**0*...## (without #0) is parsed as read
    assert by_note["query_who13_time"]["ack"] is True
    assert by_note["query_who13_date"]["ack"] is True
    assert by_note["query_who13_datetime"]["ack"] is True
    assert by_note["expose_who13_report_time_parsed_as_read"]["ack"] is True
    assert len(by_note["expose_who13_report_time_parsed_as_read"]["replies"]) == 1
    assert by_note["expose_who13_set_time_broadcasts_on_evt"]["ack"] is True

    # 5. WHO=18: active power (113), total energy (51), daily energy (54), and Dim 1200 streaming ACK
    assert by_note["expose_who18_active_power_dim113_acked"]["ack"] is True
    assert by_note["expose_who18_total_energy_dim51_acked"]["ack"] is True
    assert by_note["expose_who18_daily_energy_dim54_acked"]["ack"] is True
    assert by_note["expose_who18_stream_enable_dim1200_acked"]["ack"] is True
    assert by_note["expose_who18_stream_disable_dim1200_acked"]["ack"] is True

    # 6. Replay the captured WHO=2 virtual cover (25) and WHO=1 (06) event bursts through ExposeManager
    try:
        from custom_components.myhome.expose import ExposeManager
    except ImportError:
        return

    gw = _gateway()
    expose_cfg = [
        {"who": 1, "where": "06", "entity_id": "light.virtual_06", "cooldown": 0},
        {"who": 2, "where": "25", "entity_id": "cover.virtual_25", "cooldown": 0},
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": MAC, "host": "192.0.2.92", "port": 20000, "expose": expose_cfg},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        router=FrameRouter(),
        platforms={},
        entities={},
        expose=expose_cfg,
    )

    cover_calls: list[tuple[str, dict]] = []

    async def _record_cover_service(call) -> None:
        cover_calls.append((call.service, dict(call.data)))

    hass.services.async_register("cover", "open_cover", _record_cover_service)
    hass.services.async_register("cover", "close_cover", _record_cover_service)
    hass.services.async_register("cover", "stop_cover", _record_cover_service)

    hass.states.async_set("light.virtual_06", "off")
    hass.states.async_set("cover.virtual_25", "closed")
    mgr = ExposeManager(hass, entry, gw, expose_cfg)
    await mgr.async_start()

    # HA transitions cover.virtual_25 -> "opening", sending *2*1*25## onto the F461
    hass.states.async_set("cover.virtual_25", "opening")
    await hass.async_block_till_done()
    assert gw.sent[-1] == "*2*1*25##"

    # Feed the exact 2-frame translation burst emitted by the physical F461 on evt:
    # ['*2*1000#0*25##', '*2*1000#1*25##']
    for raw in by_note["expose_who2_raise_virtual_25_emits_premove_stop_and_up_on_evt"]["events_after"]:
        msg = OWNMessage.parse(raw)
        assert msg is not None
        await mgr.handle_bus_message(msg)
    await hass.async_block_till_done()

    # Both frames MUST be absorbed by _pre_move_stop_guard and _tx_echo_guard (0 service calls!)
    assert cover_calls == []

    # HA transitions cover.virtual_25 -> "closing", sending *2*2*25##
    hass.states.async_set("cover.virtual_25", "closing")
    await hass.async_block_till_done()
    assert gw.sent[-1] == "*2*2*25##"

    for raw in by_note["expose_who2_lower_virtual_25_emits_premove_stop_and_down_on_evt"]["events_after"]:
        msg = OWNMessage.parse(raw)
        assert msg is not None
        await mgr.handle_bus_message(msg)
    await hass.async_block_till_done()
    assert cover_calls == []

    # Now an external wall control sends the same F461 sequence (*2*1000#0*25## -> *2*1000#1*25##)
    # when no HA TX guard is active: pre-move STOP triggers stop_cover and UP triggers open_cover,
    # while ExposeManager's confirmation echoes are guarded!
    for raw in by_note["expose_who2_raise_virtual_25_emits_premove_stop_and_up_on_evt"]["events_after"]:
        msg = OWNMessage.parse(raw)
        assert msg is not None
        await mgr.handle_bus_message(msg)
    await hass.async_block_till_done()
    assert [c[0] for c in cover_calls] == ["stop_cover", "open_cover"]

    # 7. Verify 2026-10-20 F461 follow-up findings:
    #    a) Double virtual-cover translation burst (['*2*1000#0*25##', '*2*1000#1*25##'] x 2)
    #       arriving while the command-session write future is still pending (before 0.55 s NACK)
    cover_calls.clear()
    hass.services.async_register("cover", "set_cover_position", _record_cover_service)
    pending_fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    async def _send_pending(cmd, *a, **k):
        gw.sent.append(str(cmd))
        return pending_fut

    gw.send_status_request.side_effect = _send_pending
    mgr.bindings[1].last_sent_value = None
    hass.states.async_set("cover.virtual_25", "closed")
    await hass.async_block_till_done()
    hass.states.async_set("cover.virtual_25", "opening")
    await hass.async_block_till_done()
    assert gw.sent[-1] == "*2*1*25##"
    for raw in [
        "*2*1000#0*25##",
        "*2*1000#1*25##",
        "*2*1000#0*25##",
        "*2*1000#1*25##",
    ]:
        msg = OWNMessage.parse(raw)
        assert msg is not None
        await mgr.handle_bus_message(msg)
    pending_fut.set_result(False)
    await asyncio.sleep(0)
    assert cover_calls == []

    #    b) WHO=2 #11#001 level write (*#2*25*#11#001*50##) translates to *#2*25*#11#001#1*50##
    #       on the F461 monitor session and is absorbed by _tx_echo_guard without looping back
    gw.send_status_request.side_effect = gw.send.side_effect
    mgr.bindings[1].attribute = "current_position"
    hass.states.async_set("cover.virtual_25", "open", {"current_position": 50})
    await hass.async_block_till_done()
    assert "*#2*25*#11#001*50##" in gw.sent
    await mgr.handle_bus_message(OWNMessage.parse("*#2*25*#11#001#1*50##"))
    await hass.async_block_till_done()
    assert cover_calls == []

    await mgr.async_stop()
