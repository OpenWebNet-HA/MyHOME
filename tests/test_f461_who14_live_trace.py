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

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import CONF_NAME
from OWNd.message import OWNCommand, OWNEvent, OWNMessage

from custom_components.myhome.const import (
    CONF_ADVANCED_SHUTTER,
    CONF_ENTITY,
    CONF_PLATFORMS,
    CONF_TRAVEL_TIME,
    CONF_WHERE,
    DOMAIN,
)
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
