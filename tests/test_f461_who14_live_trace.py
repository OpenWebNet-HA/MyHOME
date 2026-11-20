"""Live BTicino F461 (firmware 2.0.11) + F411/4 + F411/2 WHO 14 trace verification.

Validates the real hardware capture in
``tests/fixtures/traces/f461_who14_live_2026-10-18/myhome_trace_F461_who14_live_2026-10-18.json``:
- Plant: F461 gateway (fw 2.0.11), F411/4 lighting actuator at WHERE 06 (ON) and 07 (OFF),
  F411/2 shutter actuator at WHERE 05 (5 s travel time, STOP), KW4691 thermostat at zone 2.
- Empirically establishes how physical actuators respond to WHO 14 lock (``*14*0*WHERE##``)
  and unlock (``*14*1*WHERE##``) commands and why ``own-firmware-oracle`` observed
  ``verdict: "out", reply: "nack"`` on unpopulated addresses.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.const import CONF_NAME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from OWNd.message import OWNCommand, OWNEvent, OWNLightingCommand, OWNMessage

from custom_components.myhome.const import (
    CONF_ADVANCED_SHUTTER,
    CONF_ENTITY,
    CONF_PLATFORMS,
    CONF_TRAVEL_TIME,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.cover import (
    CalibrationInterrupted,
    MyHOMECover,
    MyHOMEImpulseCover,
)
from custom_components.myhome.cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:04:97"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_who14_live_2026-10-18"
    / "myhome_trace_F461_who14_live_2026-10-18.json"
)
PRIVATE_IP = re.compile(r"\b(10\.\d+|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d+\.\d+\b")


@pytest.fixture(scope="module")
def archive() -> dict:
    assert FIXTURE.is_file(), FIXTURE
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(archive: dict) -> list[dict]:
    return archive["rows"]


def _result_by_step(rows: list[dict], step: str) -> dict:
    tx = next(r for r in rows if r["dir"] == "tx" and r.get("step") == step)
    return tx


def _gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = MAC
    gw.unique_id = MAC
    gw.log_id = "[F461 who14 live]"
    gw.model = "F461"
    gw.has_central_unit = False
    gw.supports_zone_status = True
    gw.initial_discovery_pending = False
    gw.is_connected = True
    gw.sent: list[str] = []

    async def _send(cmd, *a, **k):
        gw.sent.append(str(cmd))
        return True

    gw.send = AsyncMock(side_effect=_send)
    gw.send_status_request = AsyncMock(side_effect=_send)
    return gw


async def _plant(hass, gw):
    from custom_components.myhome.climate import async_setup_entry as climate_setup
    from custom_components.myhome.cover import async_setup_entry as cover_setup
    from custom_components.myhome.light import async_setup_entry as light_setup

    hass.data = {
        DOMAIN: {
            MAC: {
                CONF_ENTITY: gw,
                CONF_PLATFORMS: {
                    "light": {
                        "light_06": {CONF_WHERE: "06", CONF_NAME: "Light 06", "model": "F411/4"},
                        "light_07": {CONF_WHERE: "07", CONF_NAME: "Light 07", "model": "F411/4"},
                    },
                    "cover": {
                        "shutter_05": {
                            CONF_WHERE: "05",
                            CONF_NAME: "Shutter 05",
                            CONF_ADVANCED_SHUTTER: False,
                            CONF_TRAVEL_TIME: 5,
                            "model": "F411/2",
                        },
                    },
                    "climate": {
                        "thermo_zone_2": {
                            "who": "4",
                            "zone": "2",
                            "name": "KW4691 Zone 2",
                            "heat": True,
                            "cool": True,
                            "standalone": True,
                            "central": False,
                            "manufacturer": "BTicino",
                            "model": "KW4691",
                        },
                    },
                },
            }
        }
    }
    entry = MagicMock()
    entry.data = {"mac": MAC}
    entry.entry_id = "f461_who14_live"
    entry.options = {}
    attach_runtime(hass, entry, MAC, gw)
    lights, covers, climates = [], [], []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await light_setup(hass, entry, lights.extend)
        await cover_setup(hass, entry, covers.extend)
        await climate_setup(hass, entry, climates.extend)
    for e in lights + covers + climates:
        e.hass = hass
        domain = "climate" if e in climates else "cover" if e in covers else "light"
        e.entity_id = f"{domain}.{e._device_id}"
        e.async_write_ha_state = MagicMock()
        e.async_schedule_update_ha_state = MagicMock()
        attach_platform(e, entry)
    return {e._where: e for e in lights + covers + climates}


def test_archive_metadata_and_privacy(archive: dict, rows: list[dict]) -> None:
    raw_text = FIXTURE.read_text(encoding="utf-8")
    assert PRIVATE_IP.search(raw_text) is None
    assert archive["gateway"]["ip"] == "192.0.2.92"
    assert archive["gateway"]["mac"] == MAC
    assert archive["gateway"]["model"] == "F461"
    assert archive["gateway"]["firmware"] == "2.0.11"
    assert len(archive["frames"]) == 58
    assert len(rows) == 236
    for f in archive["frames"]:
        assert f["direction"] == "rx"
        assert OWNMessage.parse(f["raw"]) is not None
        assert OWNEvent.parse(f["raw"]) is not None


def test_run1_gateway_telemetry_and_baseline_sweeps(rows: list[dict]) -> None:
    assert _result_by_step(rows, "gateway_model")["replies"] == ["*#13**15*200##"]
    assert _result_by_step(rows, "gateway_firmware")["replies"] == ["*#13**16*2*0*11##"]
    assert _result_by_step(rows, "gateway_kernel")["replies"] == ["*#13**23*5*10*35##"]
    assert _result_by_step(rows, "gateway_distribution")["replies"] == ["*#13**24*2*26*4##"]
    assert _result_by_step(rows, "status_light_06")["replies"] == ["*1*1*06##"]
    assert _result_by_step(rows, "status_light_07")["replies"] == ["*1*0*07##"]
    assert _result_by_step(rows, "status_shutter_05")["replies"] == ["*2*0*05##"]
    assert len(_result_by_step(rows, "status_thermo_zone_2")["replies"]) == 5


def test_run2_who14_lighting_f411_4_lock_and_unlock(rows: list[dict]) -> None:
    # 1. Unlocked 07 switches ON and OFF normally
    on_07 = _result_by_step(rows, "unlocked_07_on")
    assert on_07["ack"] is True and on_07["events_after"] == ["*1*1*07##"]
    off_07 = _result_by_step(rows, "unlocked_07_off")
    assert off_07["ack"] is True and off_07["events_after"] == ["*1*0*07##"]

    # 2. Lock 07 (*14*0*07##) -> ACK in 90ms + monitor broadcast *14*0*07##; *1*1*07## ignored
    lock_07 = _result_by_step(rows, "lock_07_while_off")
    assert lock_07["ack"] is True and lock_07["reply_time_s"] < 0.15
    assert lock_07["events_after"] == ["*14*0*07##"]

    ignored_on = _result_by_step(rows, "locked_07_ignore_on")
    assert ignored_on["ack"] is True
    assert ignored_on["events_after"] == [], "locked F411/4 channel 07 ignores *1*1*07##"
    assert _result_by_step(rows, "locked_07_confirm_still_off")["replies"] == ["*1*0*07##"]

    unlock_07 = _result_by_step(rows, "unlock_07")
    assert unlock_07["ack"] is True and unlock_07["events_after"] == ["*14*1*07##"]

    # 3. Lock 06 while ON (*14*0*06##) -> *1*0*06## ignored; remains ON
    lock_06 = _result_by_step(rows, "lock_06_while_on")
    assert lock_06["ack"] is True and lock_06["events_after"] == ["*14*0*06##"]

    ignored_off = _result_by_step(rows, "locked_06_ignore_off")
    assert ignored_off["ack"] is True
    assert ignored_off["events_after"] == [], "locked F411/4 channel 06 ignores *1*0*06##"
    assert _result_by_step(rows, "locked_06_confirm_still_on")["replies"] == ["*1*1*06##"]

    unlock_06 = _result_by_step(rows, "unlock_06")
    assert unlock_06["ack"] is True and unlock_06["events_after"] == ["*14*1*06##"]


def test_run3_who14_shutter_f411_2_lock_and_unlock(rows: list[dict]) -> None:
    # 1. Locking shutter 05 (*14*0*05##) broadcasts *14*0*05## followed 54ms later by *2*0*05## STOP
    lock_05 = _result_by_step(rows, "lock_shutter_05")
    assert lock_05["ack"] is True and lock_05["reply_time_s"] < 0.15
    assert lock_05["events_after"] == ["*14*0*05##", "*2*0*05##"]

    # 2. UP (*2*1*05##) and DOWN (*2*2*05##) while locked emit command translations only; no movement
    locked_up = _result_by_step(rows, "locked_shutter_05_ignore_up")
    assert locked_up["ack"] is True
    assert locked_up["events_after"] == ["*2*1000#0*05##", "*2*1000#1*05##"]
    assert _result_by_step(rows, "locked_shutter_05_confirm_stopped_after_up")["replies"] == ["*2*0*05##"]

    locked_down = _result_by_step(rows, "locked_shutter_05_ignore_down")
    assert locked_down["ack"] is True
    assert locked_down["events_after"] == ["*2*1000#0*05##", "*2*1000#2*05##"]
    assert _result_by_step(rows, "locked_shutter_05_confirm_stopped_after_down")["replies"] == ["*2*0*05##"]

    # 3. Unlock shutter 05 (*14*1*05##) -> ACK + *14*1*05## broadcast
    unlock_05 = _result_by_step(rows, "unlock_shutter_05")
    assert unlock_05["ack"] is True and unlock_05["events_after"] == ["*14*1*05##"]

    # 4. Unlocked UP and DOWN actuate and auto-stop after 5s travel time
    up_run = _result_by_step(rows, "unlocked_shutter_05_up_5s_auto_stop")
    assert up_run["ack"] is True
    assert "*2*1*05##" in up_run["events_after"]
    assert up_run["events_after"][-1] == "*2*0*05##"

    down_run = _result_by_step(rows, "unlocked_shutter_05_down_5s_auto_stop")
    assert down_run["ack"] is True
    assert "*2*2*05##" in down_run["events_after"]
    assert down_run["events_after"][-1] == "*2*0*05##"


def test_run4_who14_parser_rejections_vs_unpopulated_bus_timeout(rows: list[dict]) -> None:
    # Status queries (*#14*...) and invalid WHAT=2 are rejected by bt_luci parser in ~50ms
    for step in (
        "status_request_07_nack",
        "status_request_06_nack",
        "status_request_05_nack",
        "status_request_general_nack",
        "invalid_what_2_nack",
    ):
        res = _result_by_step(rows, step)
        assert res["ack"] is False
        assert res["reply_time_s"] < 0.08
        assert res["events_after"] == []

    # Unpopulated WHERE 99 (*14*0*99##, *14*1*99##) times out after ~2.05s waiting for SCS bus ACK
    for step in (
        "unpopulated_where_99_lock_timeout_nack",
        "unpopulated_where_99_unlock_timeout_nack",
    ):
        res = _result_by_step(rows, step)
        assert res["ack"] is False
        assert res["reply_time_s"] >= 2.0
        assert res["events_after"] == []
        assert OWNCommand.parse(res["frame"]) is not None

    # Private riser #4#01 (*14*0*07#4#01##, *14*1*07#4#01##) is ACKed in ~110ms
    for step in ("interface_01_lock_07", "interface_01_unlock_07"):
        res = _result_by_step(rows, step)
        assert res["ack"] is True
        assert res["reply_time_s"] < 0.2


async def test_replay_trace_through_myhome_entities(hass, rows: list[dict]) -> None:
    ents = await _plant(hass, _gateway())
    light06, light07, shutter05, zone2 = ents["06"], ents["07"], ents["05"], ents["2"]

    for r in rows:
        if r["dir"] != "rx" or r.get("session") != "evt":
            continue
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None
        who, where = getattr(msg, "who", None), getattr(msg, "where", None)
        if who in (1, 2) and where in ents:
            ents[where].handle_event(msg)
        elif who == 4 and getattr(msg, "zone", None) == 2:
            zone2.handle_event(msg)

    # Verify final restored plant state after all 4 runs
    assert light06.is_on is True
    assert light07.is_on is False
    assert shutter05.is_opening is False and shutter05.is_closing is False
    assert zone2.current_temperature == 24.9
    assert zone2.target_temperature == 24.5
    assert zone2.hvac_mode == HVACMode.HEAT


def _make_cover(
    hass,
    gw,
    where: str,
    *,
    advanced: bool = False,
    allow_auto_advanced: bool = False,
    slat_tilt: bool = False,
    travel_time: int = 5,
    slat_time: float = 2.0,
    strategy_name: str | None = None,
) -> MyHOMECover:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name=f"Cover {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=advanced,
            allow_auto_advanced=allow_auto_advanced,
            slat_tilt=slat_tilt,
            slat_time=slat_time,
            strategy_name=strategy_name,
            manufacturer="BTicino",
            model="F411/2",
            gateway=gw,
            travel_time=travel_time,
        )
    cover.hass = hass
    cover.entity_id = f"cover.cover_{where}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    return cover


async def test_who14_shutter05_locked_commands_translations_and_unlock(
    hass, rows: list[dict]
) -> None:
    gw = _gateway()
    cleared: list[tuple[str, str | None]] = []
    gw._actuator_locks = SimpleNamespace(
        clear_lock_from_actuation=lambda where, iface: cleared.append((where, iface))
    )
    ents = await _plant(hass, gw)
    shutter05: MyHOMECover = ents["05"]

    try:
        assert shutter05._actuator_locked is None
        assert "actuator_locked" not in shutter05.extra_state_attributes

        shutter05.handle_actuator_lock(True)
        assert shutter05._actuator_locked is True
        assert shutter05.extra_state_attributes["actuator_locked"] is True

        gw.sent.clear()
        await shutter05.async_open_cover()
        await shutter05.async_close_cover()
        await shutter05.async_stop_cover()
        assert gw.sent == ["*2*1*05##", "*2*2*05##", "*2*0*05##"]
        assert shutter05.is_opening is False
        assert shutter05.is_closing is False
        assert shutter05.current_cover_position is None
        assert shutter05.is_closed is None
        assert shutter05._stop_task is None

        with pytest.raises(ServiceValidationError):
            await shutter05.async_set_cover_position(position=60)

        gw.sent.clear()
        await shutter05.async_set_cover_position(position=100)
        await shutter05.async_set_cover_position(position=0)
        assert gw.sent == ["*2*1*05##", "*2*2*05##"]
        assert shutter05._stop_task is None

        shutter05._attr_current_cover_position = 0
        shutter05._start_position = 0
        shutter05._attr_is_closed = True
        gw.sent.clear()
        await shutter05.async_set_cover_position(position=60)
        assert gw.sent == ["*2*1*05##"]
        shutter05._attr_current_cover_position = 60
        shutter05._start_position = 60
        gw.sent.clear()
        await shutter05.async_set_cover_position(position=60)
        assert gw.sent == []
        shutter05._attr_current_cover_position = 0
        shutter05._start_position = 0
        assert shutter05.is_opening is False
        assert shutter05.is_closing is False
        assert shutter05.current_cover_position == 0
        assert shutter05.is_closed is True
        assert shutter05._stop_task is None

        with pytest.raises(
            HomeAssistantError,
            match=r"cannot calibrate while actuator is locked \(WHO 14\)",
        ):
            await shutter05.async_calibrate()

        # Replay run3_who14_shutter_f411_2 locked frames (*2*0*, *2*1000#0*, *2*1000#1/2*)
        locked_frames = [
            r["frame"]
            for r in rows
            if r["dir"] == "rx"
            and r.get("session") == "evt"
            and r.get("run") == "run3_who14_shutter_f411_2"
            and 25.8 <= r.get("t", 0.0) <= 29.5
        ]
        assert "*2*1000#0*05##" in locked_frames
        assert "*2*1000#1*05##" in locked_frames
        assert "*2*1000#2*05##" in locked_frames
        for raw in locked_frames:
            msg = OWNMessage.parse(raw)
            assert msg is not None
            shutter05.handle_event(msg)
        assert shutter05._actuator_locked is True
        assert shutter05.extra_state_attributes["actuator_locked"] is True
        assert shutter05.is_opening is False
        assert shutter05.is_closing is False
        assert shutter05.current_cover_position == 0

        # Explicit unlock restores normal 5 s travel
        shutter05.handle_actuator_lock(False)
        assert shutter05._actuator_locked is False
        assert shutter05.extra_state_attributes["actuator_locked"] is False
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await shutter05.async_open_cover()
            assert shutter05.is_opening is True
            shutter05.handle_event(OWNMessage.parse("*2*1*05##"))
            assert shutter05._stop_task is not None
            await shutter05._stop_task
        assert shutter05.is_opening is False
        assert shutter05.current_cover_position == 100
        assert shutter05.is_closed is False
        assert cleared == []
    finally:
        await shutter05.async_will_remove_from_hass()


async def test_who14_shutter05_mid_run_lock_and_calibration_interrupt(
    hass, monkeypatch
) -> None:
    gw = _gateway()
    gw._actuator_locks = None
    ents = await _plant(hass, gw)
    shutter05: MyHOMECover = ents["05"]

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    try:
        shutter05._attr_current_cover_position = 0
        shutter05._start_position = 0
        shutter05._attr_is_closed = True

        await shutter05.async_open_cover()
        assert shutter05.is_opening is True
        assert shutter05._stop_task is not None
        shutter05.handle_event(OWNMessage.parse("*2*1*05##"))

        clock[0] += 2.0
        shutter05.handle_actuator_lock(True)
        assert shutter05._actuator_locked is True
        assert shutter05.is_opening is False
        assert shutter05.is_closing is False
        assert shutter05._stop_task is None
        assert shutter05.current_cover_position == 40

        # Unlock, start calibration, and interrupt mid-run with WHO 14 lock
        shutter05.handle_actuator_lock(False)
        cal_task = hass.async_create_task(shutter05.async_calibrate())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert shutter05._calibrating is True
        shutter05.handle_event(OWNMessage.parse("*2*1*05##"))
        await asyncio.sleep(0)
        shutter05.handle_actuator_lock(True)
        with pytest.raises(CalibrationInterrupted, match="actuator locked via WHO 14"):
            await cal_task
        assert shutter05._calibrating is False
        assert shutter05.is_opening is False
    finally:
        await shutter05.async_will_remove_from_hass()


async def test_who14_shutter05_self_healing_unlock_vs_stop_and_translations(hass) -> None:
    gw = _gateway()
    cleared: list[tuple[str, str | None]] = []
    gw._actuator_locks = SimpleNamespace(
        clear_lock_from_actuation=lambda where, iface: cleared.append((where, iface))
    )
    ents = await _plant(hass, gw)
    shutter05: MyHOMECover = ents["05"]

    try:
        shutter05.handle_actuator_lock(True)
        for frame in ("*2*0*05##", "*2*1000#0*05##", "*2*1000#1*05##", "*2*1000#2*05##"):
            shutter05.handle_event(OWNMessage.parse(frame))
        assert shutter05._actuator_locked is True
        assert shutter05.extra_state_attributes["actuator_locked"] is True
        assert cleared == []

        # Real non-translation motion start self-heals lock
        shutter05.handle_event(OWNMessage.parse("*2*1*05##"))
        assert shutter05._actuator_locked is False
        assert shutter05.extra_state_attributes["actuator_locked"] is False
        assert cleared == [("05", None)]
        assert shutter05.is_opening is True

        # Also verify self-healing works cleanly when no manager is attached
        shutter05.handle_actuator_lock(True)
        gw._actuator_locks = None
        shutter05.handle_event(OWNMessage.parse("*2*2*05##"))
        assert shutter05._actuator_locked is False
        assert shutter05.is_closing is True
    finally:
        await shutter05.async_will_remove_from_hass()


async def test_who14_cover_variants_tilt_scope_advanced_and_simple(hass) -> None:
    gw = _gateway()
    gw._actuator_locks = None
    family = CoverFamily()

    locked_shutter = _make_cover(hass, gw, "05", travel_time=5)
    unlocked_shutter = _make_cover(hass, gw, "08", travel_time=5)
    blind = _make_cover(hass, gw, "09", slat_tilt=True, travel_time=5, slat_time=2.0)
    adv_cover = _make_cover(hass, gw, "22", advanced=True)
    simple_cover = _make_cover(hass, gw, "23", travel_time=0, strategy_name="simple")
    scope_cover = MyHOMEScopeCover(
        hass=hass,
        name="All Shutters",
        entity_name=None,
        device_id="0",
        who="2",
        where="0",
        interface=None,
        advanced=False,
        slat_tilt=True,
        slat_time=2.0,
        manufacturer="BTicino",
        model="Scope",
        gateway=gw,
        travel_time=5,
        scope=CoverScope.of("0"),
    )
    scope_cover.hass = hass
    scope_cover.entity_id = "cover.all_shutters"
    scope_cover.async_write_ha_state = MagicMock()
    scope_cover.async_schedule_update_ha_state = MagicMock()
    for c in (locked_shutter, unlocked_shutter, blind, scope_cover):
        family.add(c)

    try:
        # 1. Venetian blind: mid-tilt lock cancels tilt, and locked tilt commands send without mutating
        await blind.async_open_cover_tilt()
        assert blind._is_tilting is True
        assert blind._stop_task is not None
        blind.handle_actuator_lock(True)
        assert blind._is_tilting is False
        assert blind._stop_task is None
        assert blind.is_opening is False

        blind._attr_current_cover_tilt_position = 0
        gw.sent.clear()
        await blind.async_open_cover_tilt()
        await blind.async_set_cover_tilt_position(tilt_position=80)
        blind._attr_current_cover_tilt_position = 100
        await blind.async_close_cover_tilt()
        assert gw.sent == ["*2*1*09##", "*2*1*09##", "*2*2*09##"]
        assert blind._is_tilting is False
        assert blind._stop_task is None
        assert blind.current_cover_tilt_position == 100

        # 2. Scope cover: locked members skip _begin_run, _follow_scope_run_to_end,
        # _begin_stop, _follow_scope_stop, and _follow_pulse
        locked_shutter.handle_actuator_lock(True)
        gw.sent.clear()
        await scope_cover.async_open_cover()
        assert unlocked_shutter.is_opening is True
        assert unlocked_shutter._stop_task is not None
        assert locked_shutter.is_opening is False
        assert locked_shutter._stop_task is None

        await scope_cover.async_stop_cover()
        assert locked_shutter._pending_cmd is None
        assert locked_shutter._echo_until is None

        gw.sent.clear()
        await scope_cover.async_close_cover_tilt()
        assert gw.sent == ["*2*2*0##"]
        assert blind._is_tilting is False
        assert blind.is_closing is False
        await scope_cover.async_stop_cover()

        # 3. Advanced (Dimension 10/11) cover: set_cover_position sends Dim 11 without _echo_until;
        # stationary Dim 10 keeps lock, moving Dim 10 unlocks
        adv_cover.handle_actuator_lock(True)
        adv_cover._attr_current_cover_position = 40
        adv_cover._echo_until = None
        gw.sent.clear()
        await adv_cover.async_set_cover_position(position=60)
        assert gw.sent == ["*#2*22*#11#001*60##"]
        assert adv_cover._echo_until is None
        assert adv_cover.current_cover_position == 40

        adv_cover.handle_event(OWNMessage.parse("*#2*22*10*10*40*001*0##"))
        assert adv_cover._actuator_locked is True
        adv_cover.handle_event(OWNMessage.parse("*#2*22*10*11*40*001*0##"))
        assert adv_cover._actuator_locked is False

        # 4. Simple (assumed_state=True) cover: set_cover_position(50) sends stop without mutating
        simple_cover.handle_actuator_lock(True)
        simple_cover._attr_is_closed = True
        gw.sent.clear()
        await simple_cover.async_set_cover_position(position=50)
        assert gw.sent == ["*2*0*23##"]
        assert simple_cover.is_closed is True
    finally:
        for c in (locked_shutter, unlocked_shutter, blind, adv_cover, simple_cover, scope_cover):
            await c.async_will_remove_from_hass()


async def test_who14_advanced_and_promoted_cover_mid_run_lock_preserves_dim10_position(
    hass, monkeypatch
) -> None:
    """Mid-run WHO=14 lock on native/auto-promoted covers must not snap Dim 10 position to end stop."""
    gw = _gateway()
    gw._actuator_locks = None

    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    adv_cover = _make_cover(hass, gw, "22", advanced=True, travel_time=5)
    promo_cover = _make_cover(
        hass, gw, "24", advanced=False, allow_auto_advanced=True, travel_time=5
    )

    try:
        # Case A: Configured advanced=True cover (opening & closing past travel_time)
        adv_cover.handle_event(OWNMessage.parse("*#2*22*10*11*40*001*0##"))
        assert adv_cover.is_opening is True
        assert adv_cover.current_cover_position == 40
        assert adv_cover._run_started_at == 100.0

        clock[0] += 35.0
        adv_cover.handle_actuator_lock(True)
        assert adv_cover._actuator_locked is True
        assert adv_cover.is_opening is False
        assert adv_cover.is_closing is False
        assert adv_cover.current_cover_position == 40
        assert adv_cover.is_closed is False
        assert adv_cover.extra_state_attributes["last_run_seconds"] == 35.0

        adv_cover.handle_event(OWNMessage.parse("*#2*22*10*12*40*001*0##"))
        assert adv_cover._actuator_locked is False
        assert adv_cover.is_closing is True
        assert adv_cover.current_cover_position == 40

        clock[0] += 35.0
        adv_cover.handle_actuator_lock(True)
        assert adv_cover._actuator_locked is True
        assert adv_cover.is_closing is False
        assert adv_cover.current_cover_position == 40
        assert adv_cover.is_closed is False

        # Case B: Auto-promoted Dimension 10 cover (_promote_to_native_strategy mid-run)
        promo_cover._attr_current_cover_position = 20
        promo_cover._start_position = 20
        await promo_cover.async_open_cover()
        assert promo_cover.is_opening is True
        assert promo_cover._stop_task is not None
        promo_cover.handle_event(OWNMessage.parse("*2*1*24##"))

        promo_cover.handle_event(OWNMessage.parse("*#2*24*10*11*35*001*0##"))
        assert promo_cover._advanced is True
        assert promo_cover._start_position is None
        assert promo_cover._move_start_time is None
        assert promo_cover._stop_task is None
        assert promo_cover.is_opening is True
        assert promo_cover.current_cover_position == 35

        clock[0] += 35.0
        promo_cover.handle_actuator_lock(True)
        assert promo_cover._actuator_locked is True
        assert promo_cover.is_opening is False
        assert promo_cover.is_closing is False
        assert promo_cover.current_cover_position == 35
        assert promo_cover.is_closed is False
    finally:
        for c in (adv_cover, promo_cover):
            await c.async_will_remove_from_hass()


async def test_who14_impulse_cover_locked_and_self_healing(hass) -> None:
    gw = _gateway()
    cleared: list[tuple[str, str | None]] = []
    gw._actuator_locks = SimpleNamespace(
        clear_lock_from_actuation=lambda where, iface: cleared.append((where, iface))
    )
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        gate = MyHOMEImpulseCover(
            hass=hass,
            name="Locked Gate",
            entity_name=None,
            device_id="22",
            who="1",
            where="22",
            interface=None,
            device_class=CoverDeviceClass.GATE,
            travel_time=5.0,
            pulse_duration=0.5,
            min_cycle_time=1.0,
            pin_code=None,
            state_sensor=None,
            manufacturer="BTicino",
            model="Impulse Cover",
            gateway=gw,
        )
    gate.hass = hass
    gate.entity_id = "cover.locked_gate"
    gate.async_write_ha_state = MagicMock()
    expected_pulse = (
        "*1*18*22##" if hasattr(OWNLightingCommand, "switch_on_timed") else "*1*1*22##"
    )

    try:
        assert gate._actuator_locked is None
        assert "actuator_locked" not in gate.extra_state_attributes

        gate.handle_actuator_lock(True)
        assert gate._actuator_locked is True
        assert gate.extra_state_attributes["actuator_locked"] is True

        gw.sent.clear()
        await gate.async_open_cover()
        gate._last_pulse_time = -9999.0
        await gate.async_close_cover()
        gate._last_pulse_time = -9999.0
        await gate.async_stop_cover()
        assert gw.sent == [expected_pulse, expected_pulse, expected_pulse]
        assert gate.is_opening is False
        assert gate.is_closing is False
        assert gate.is_closed is None
        assert gate._travel_timer_cancel is None

        # *1*0*, translation, and echo-window frames keep lock True
        gate.handle_event(OWNMessage.parse("*1*0*22##"))
        assert gate._actuator_locked is True
        trans = MagicMock(is_translation=True, is_on=True, what=1000)
        gate.handle_event(trans)
        assert gate._actuator_locked is True
        gate._last_pulse_time = time.monotonic()
        gate.handle_event(OWNMessage.parse("*1*1*22##"))
        assert gate._actuator_locked is True

        # External non-translation is_on outside echo window self-heals lock
        gate._last_pulse_time = -9999.0
        gate._attr_is_closed = True
        gate.handle_event(OWNMessage.parse("*1*1*22##"))
        assert gate._actuator_locked is False
        assert gate.extra_state_attributes["actuator_locked"] is False
        assert cleared == [("22", None)]
        assert gate.is_opening is True

        # Explicit unlock restores optimistic motion
        gate.handle_actuator_lock(False)
        assert gate._actuator_locked is False
        gate._attr_is_opening = False
        gate._cancel_travel_timer()
        gate._last_pulse_time = -9999.0
        await gate.async_open_cover()
        assert gate.is_opening is True
        assert gate._travel_timer_cancel is not None
    finally:
        await gate.async_will_remove_from_hass()
