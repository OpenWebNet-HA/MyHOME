"""Tests for #866: BTicino F454 FW 2.0 standalone thermostat trace replay & *#4*Z## zone status poll.

Verifies from the live F454 FW 2.0 trace captured on @xduss's 13-zone standalone plant
(tests/fixtures/traces/issue_866/myhome_trace_F454_all_2026-11-04T19-00-04.json):
1. F454 drops the general heating status sweep (*#4*0##) on live hardware (both manual
   sweeps at 18:51:53 and 18:52:42 returned zero zone frames), matching own-firmware-oracle
   F454 020051 (trace-corpus.tsv).
2. Standalone thermostats (no #0 central unit) answer per-zone status queries (*#4*1##..*#4*4##)
   in ~1.31-1.33 s with all five frames: Dimension 0 temperature, Dimension 12 effective
   setpoint, *4*1*Z## heating mode, Dimension 14 manual setpoint, and Dimension 13 local offset.
3. F454 and its Legrand catalogue code 003598 set supports_zone_status = True and
   zone_temperature_poll_needed = False, polling *#4*Z## on add so cold-start standalone
   zones initialize hvac_mode and target_temperature (preserving Dimension 12 over trailing
   Dimension 14) without a #0 central unit, and dynamically discovered zones (zone 13) also
   poll *#4*13## immediately on add.
4. CommandWorkerPool harvests partial OWNNackError.responses when a status request ends with
   *#*0## after partial replies, hydrating any answered dimensions and counting the zone as
   answered in PollHealth.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from OWNd.connection import OWNGateway
from OWNd.exceptions import OWNNackError
from OWNd.message import OWNHeatingCommand, OWNMessage

from custom_components.myhome.bus_monitor import BusMonitor
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.climate import async_setup_entry as climate_setup
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_PLATFORMS,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.gateway import (
    MyHOMEGatewayHandler,
    command_session_default,
    command_session_limit,
)
from custom_components.myhome.gateway_sessions import (
    COMMAND_SESSION_IDLE_TIMEOUT,
    CommandWorkerPool,
)
from tests.conftest import attach_platform, attach_runtime
from tests.test_climate_actuator_status import _replay, _trace_gateway, _trace_plant

MAC = "00:03:50:00:08:66"
TRACE_FILE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_866"
    / "myhome_trace_F454_all_2026-11-04T19-00-04.json"
)


@pytest.fixture(scope="module")
def issue_866_archive() -> dict[str, Any]:
    """Load the authentic 129-frame F454 FW 2.0 trace archive from issue #866."""
    assert TRACE_FILE.is_file(), f"Missing trace fixture: {TRACE_FILE}"
    return json.loads(TRACE_FILE.read_text(encoding="utf-8"))


def _entry(model: str, firmware: str = "2.0") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = f"entry-866-{model}"
    entry.data = {
        CONF_HOST: "192.0.2.86",
        CONF_PORT: 20000,
        CONF_PASSWORD: "x",
        CONF_NAME: model,
        CONF_FIRMWARE: firmware,
        CONF_MAC: MAC,
    }
    entry.runtime_data = None
    return entry


def _handler(hass, model: str = "F454") -> MyHOMEGatewayHandler:
    handler = MyHOMEGatewayHandler(hass, _entry(model))
    handler.send_status_request = AsyncMock()
    return handler


def _live_f454_gateway(hass, mac: str = MAC) -> MagicMock:
    gw = _trace_gateway("F454", mac)
    real = _handler(hass, "F454")
    gw.supports_zone_status = real.supports_zone_status
    gw.zone_temperature_poll_needed = real.zone_temperature_poll_needed
    gw.humidity_request_refused = real.humidity_request_refused
    gw.interface_zone_request_refused = real.interface_zone_request_refused
    return gw


def _status_requests(gw: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gw.send_status_request.await_args_list]


def test_issue_866_trace_archive_metadata_and_who4_invariants(
    issue_866_archive: dict[str, Any],
) -> None:
    """Verify live F454 FW 2.0 trace structure, *#4*0## silence, and 5-frame *#4*Z## replies."""
    gw = issue_866_archive["gateway"]
    assert gw["model"] == "F454"
    assert gw["firmware"] == "2.0"
    assert gw["identification"]["profile"] == "F454Profile"

    frames = issue_866_archive["frames"]
    assert len(frames) == 129
    assert issue_866_archive["telemetry"]["total_rx"] == 86
    assert issue_866_archive["telemetry"]["total_tx"] == 43

    # Pure standalone thermostat plant: zero #0 central-unit frames anywhere in the capture
    assert not any(f.get("where") == "#0" or "*#0#" in f["raw"] for f in frames)

    # Both manual *#4*0## sweeps produced zero immediate replies (dropped by F454 firmware)
    sweep_indices = [
        idx for idx, f in enumerate(frames) if f["direction"] == "tx" and f["raw"] == "*#4*0##"
    ]
    assert len(sweep_indices) == 2
    for idx in sweep_indices:
        next_frame = frames[idx + 1]
        assert next_frame["timestamp"] - frames[idx]["timestamp"] > 4.0

    # Manual *#4*Z## status polls for zones 2, 1, 3, 4 each answered all 5 frames in ~1.31-1.34 s
    expected_replies = {
        "2": ["*#4*2*0*0238##", "*#4*2*12*0230*3##", "*4*1*2##", "*#4*2*14*0200*3##", "*#4*2*13*00##"],
        "1": ["*#4*1*0*0227##", "*#4*1*12*0220*3##", "*4*1*1##", "*#4*1*14*0200*3##", "*#4*1*13*00##"],
        "3": ["*#4*3*0*0227##", "*#4*3*12*0220*3##", "*4*1*3##", "*#4*3*14*0200*3##", "*#4*3*13*00##"],
        "4": ["*#4*4*0*0219##", "*#4*4*12*0215*3##", "*4*1*4##", "*#4*4*14*0200*3##", "*#4*4*13*00##"],
    }
    for zone, expected in expected_replies.items():
        tx_idx = next(
            i for i, f in enumerate(frames) if f["direction"] == "tx" and f["raw"] == f"*#4*{zone}##"
        )
        replies = frames[tx_idx + 1 : tx_idx + 6]
        assert [r["raw"] for r in replies] == expected
        elapsed = replies[-1]["timestamp"] - frames[tx_idx]["timestamp"]
        assert 1.2 < elapsed < 1.5, (zone, elapsed)


@pytest.mark.parametrize("model", ["F454", "BTicino F454", "003598"])
async def test_f454_and_003598_enable_zone_status_and_skip_general_and_temp_polls(
    hass, model: str
) -> None:
    """F454 (up to 4 command sessions) polls *#4*Z## per zone instead of *#4*0## or *#4*Z*0##."""
    handler = _handler(hass, model)
    assert handler._model_key == "F454"
    assert handler.supports_zone_status is True
    assert handler.general_zone_request_refused is True
    assert "*#4*0##" not in handler._discovery_frames()
    assert handler.zone_temperature_poll_needed is False
    assert handler.actuator_request_refused is False
    assert handler.humidity_request_refused is True
    assert command_session_default("F454") == 1
    assert command_session_limit("F454") == 4

    zone = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="4-1",
        who="4",
        where="1",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="F454",
        gateway=handler,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_1"
    zone.async_write_ha_state = MagicMock()

    assert zone._poll_on_add is True
    assert zone._polls_temperature is False
    await zone.async_update()
    assert _status_requests(handler) == ["*#4*1##"]


async def test_issue_866_cold_start_replay_hydrates_standalone_zones_and_preserves_dim12_setpoint(
    hass, issue_866_archive: dict[str, Any]
) -> None:
    """Replay all 86 RX frames from a cold start (no RestoreEntity, no #0 central unit)."""
    gw = _live_f454_gateway(hass)
    # Match @xduss's startup configuration: zones 1..12 + unpopulated 14 configured;
    # zone 13 was unconfigured and discovered dynamically from *#4*13*0*0216## at 18:24:11.
    configured_zones = [*(str(z) for z in range(1, 13)), "14"]
    platforms_cfg = {
        "climate": {
            f"climate_zone_{z}": {
                CONF_WHERE: z,
                "who": "4",
                "zone": z,
                "name": f"Climate Zone {z}",
                "heat": True,
                "cool": True,
                "standalone": True,
                "central": False,
                "manufacturer": "BTicino",
                "model": gw.model,
            }
            for z in configured_zones
        },
    }
    hass.data = {DOMAIN: {gw.mac: {CONF_ENTITY: gw, CONF_PLATFORMS: platforms_cfg}}}
    entry = MagicMock()
    entry.data = {"mac": gw.mac}
    entry.entry_id = "trace_F454_866"
    entry.options = {}
    attach_runtime(hass, entry, gw.mac, gw)

    entities: dict[str, MyHOMEClimate] = {}
    added: list[MyHOMEClimate] = []
    pending_tasks: list[asyncio.Task[None]] = []

    def _add_entities(new_entities: list[MyHOMEClimate], update_before_add: bool = False) -> None:
        for ent in new_entities:
            ent.hass = hass
            ent.entity_id = f"climate.{ent._device_id}"
            ent.async_write_ha_state = MagicMock()
            ent.async_schedule_update_ha_state = MagicMock()
            attach_platform(ent, entry)
            added.append(ent)
            entities[ent._where] = ent
            pending_tasks.append(hass.async_create_task(ent.async_added_to_hass()))

    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await climate_setup(hass, entry, _add_entities)
        await asyncio.gather(*pending_tasks)
        pending_tasks.clear()

    # Every configured zone immediately polls *#4*Z## (never *#4*Z*0##) after setup
    initial_polls = _status_requests(gw)
    assert initial_polls == [f"*#4*{z}##" for z in configured_zones]
    gw.send_status_request.reset_mock()

    rx_frames = [f["raw"] for f in issue_866_archive["frames"] if f["direction"] == "rx"]
    assert len(rx_frames) == 86
    for raw in rx_frames:
        _replay(hass, gw.mac, [raw])
        if pending_tasks:
            await asyncio.gather(*pending_tasks)
            pending_tasks.clear()
    await hass.async_block_till_done()

    # Zone 13 was dynamically discovered from *#4*13*0*0216## and immediately polled *#4*13## on add
    assert len(added) == len(configured_zones) + 1
    zone13 = entities["13"]
    assert _status_requests(gw) == ["*#4*13##"]
    assert zone13.current_temperature == 21.5
    assert zone13.extra_state_attributes["actuators"] == {"1": "off"}

    # Zones 1..4 received full 5-frame *#4*Z## replies: hvac_mode is HEAT without any #0 central
    # unit, and Dimension 12 effective setpoint wins over trailing Dimension 14 (20.0 °C manual).
    expected_hydrated = {
        "1": (22.7, 22.0),
        "2": (23.8, 23.0),
        "3": (22.7, 22.0),
        "4": (21.9, 21.5),
    }
    for z, (curr_temp, target_temp) in expected_hydrated.items():
        entity = entities[z]
        assert entity.hvac_mode == HVACMode.HEAT, z
        assert entity.current_temperature == curr_temp, z
        assert entity.target_temperature == target_temp, z
        assert entity.hvac_action == HVACAction.IDLE, z
        assert entity.extra_state_attributes["actuators"] == {"1": "off"}, z
        assert entity.extra_state_attributes["knob_pos"] == "0", z
        assert entity.extra_state_attributes["local_offset"] == 0.0, z

    # Zone 6 reported active heating actuator (*#4*6#1*20*1##); Zone 8 reported dual actuators
    assert entities["6"].current_temperature == 22.3
    assert entities["6"].extra_state_attributes["actuators"] == {"1": "on"}
    assert entities["8"].current_temperature == 21.8
    assert entities["8"].extra_state_attributes["actuators"] == {"1": "off", "2": "off"}

    # Unpopulated Zone 14 never answered any bus query
    assert entities["14"].current_temperature is None
    assert entities["14"].target_temperature is None
    assert entities["14"].hvac_mode is None


async def test_partial_nack_status_request_harvests_ownd_responses_and_counts_zone_answered(
    hass,
) -> None:
    """When *#4*Z## ends with *#*0## after partial frames, OWNNackError.responses still hydrate the zone."""
    handler = MagicMock(spec=MyHOMEGatewayHandler)
    handler.log_id = "[F454:00:03:50:00:08:66]"
    handler.mac = MAC
    handler.unique_id = MAC
    handler.bus_group = MAC
    handler.primary_gateway_mac = None
    handler.delegated_whos = set()
    handler.delegated_away_whos = set()
    handler.bus_monitor = BusMonitor()
    handler.gateway = MagicMock(spec=OWNGateway)
    handler.gateway.profile = MagicMock()
    handler.gateway.profile.max_queue_size = 250
    handler.gateway.profile.command_queue_delay = 0
    handler.command_session_idle_timeout = COMMAND_SESSION_IDLE_TIMEOUT
    handler.hass = hass
    handler.is_connected = True
    handler.initial_discovery_pending = False
    handler.sweep_active = False
    handler.has_central_unit = False
    handler.supports_zone_status = True
    handler.zone_temperature_poll_needed = False
    handler.humidity_request_refused = True
    handler.interface_zone_request_refused = True
    handler.watch_zone_actuators = MagicMock(return_value=MagicMock())
    handler.request_zone_dimension = MagicMock()
    handler._record_tx = MagicMock()
    handler._bridge_to_primary = MagicMock()
    handler._exposed_wheres = {}

    handler.model = "F454"
    pool = CommandWorkerPool(handler)
    pool._event_session_ready.set()
    handler.send_status_request = AsyncMock()
    entities, _ = await _trace_plant(hass, handler, ["5"])
    zone5 = entities["5"]
    handler.send_status_request = pool.send_status_request

    already_seen = OWNMessage.parse("*#4*5*0*0220##")
    mode_reply = OWNMessage.parse("*4*1*5##")
    setpoint_reply = OWNMessage.parse("*#4*5*12*0215*3##")
    assert already_seen is not None and mode_reply is not None and setpoint_reply is not None

    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    session.close = AsyncMock()
    session._stream_reader = object()
    session._stream_writer = object()

    async def partial_nack_send(message: Any, is_status_request: bool = False) -> None:
        # Simulate *#4*5*0*0220## already seen on the event session during the request window,
        # while *4*1*5## and *#4*5*12*0215*3## arrived only on the command session before *#*0##.
        handler.bus_monitor.record_frame(direction="rx", raw="*#4*5*0*0220##", parsed=already_seen)
        session.last_error = OWNNackError(
            f"Command failed with NACK: {message}",
            frame=str(message),
            responses=[already_seen, mode_reply, setpoint_reply],
        )
        return None

    session.send = AsyncMock(side_effect=partial_nack_send)

    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        worker = asyncio.create_task(pool.sending_loop(0))
        pool.sending_workers = [worker]
        try:
            frames_before = zone5._poll_health.frames
            written = await pool.send_status_request(OWNHeatingCommand.status("5"))
            assert isinstance(written, asyncio.Future)
            written.add_done_callback(lambda f: zone5._poll_answered(f, frames_before))
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()

            # Write future is cancelled on NACK, yet partial frames hydrated mode & setpoint
            assert written.cancelled() is True
            assert zone5.hvac_mode == HVACMode.HEAT
            assert zone5.target_temperature == 21.5
            assert zone5._poll_health.frames > frames_before
            assert zone5._poll_health.should_skip(time.time()) is False
            handler._bridge_to_primary.assert_called()
        finally:
            pool.close()
            await worker
