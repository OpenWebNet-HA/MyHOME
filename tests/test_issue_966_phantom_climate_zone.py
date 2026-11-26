"""Tests for Issue #966: confirming unconfigured WHO 4 climate zones before entity creation.

Covers the live MyHomeServer1 2.87.13 single-bit SCS corruption capture (Zone 1
``*#4*1*0*0190##`` at ``23:09:20.755578Z`` -> phantom Zone 65 ``*#4*65*0*0190##``
12.9 ms later at ``23:09:20.768495Z``), passive echo suppression (``< 5.0 s``) vs
confirmation (``>= 5.0 s`` or distinct frame within ``CANDIDATE_MAX_AGE``), active
``read_zone_temperature`` confirmation (answer, NACK, MH202 empty ACK, busy retry),
``dim19`` / ``dim20`` actuator cross-confirmation and expiry, F422 routed zones,
and immediate creation for configured/registry/central-unit zones.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import climate_discovery
from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.climate_discovery import (
    CANDIDATE_ECHO_WINDOW,
    CANDIDATE_MAX_AGE,
    ZoneDiscovery,
    _thermo_hold_slot,
)
from custom_components.myhome.const import DOMAIN, PLATFORMS
from custom_components.myhome.discovery import Address, DeviceContext
from tests.conftest import attach_runtime
from tests.test_f455_zone_polling import MAC as HANDLER_MAC
from tests.test_f455_zone_polling import _handler

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_966"
    / "myhome_trace_MyHomeServer1_bitflip_zone65_2026-11-19.json"
)
TRACE_MAC = "00:03:50:00:09:08"

pytestmark = pytest.mark.usefixtures("fast_bus_pacing")


async def _plant(
    hass: HomeAssistant,
    *,
    model: str = "MyHomeServer1",
    read: Any = None,
    configured_climate: dict[str, Any] | None = None,
    configured_switch: dict[str, Any] | None = None,
    subentries: list[dict[str, Any]] | None = None,
    mac: str = HANDLER_MAC,
) -> tuple[Any, list[MyHOMEClimate], MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: mac},
        unique_id=mac,
        subentries_data=subentries or [],
    )
    entry.add_to_hass(hass)
    handler = _handler(hass, model)
    handler.send = AsyncMock()
    handler.send_status_request = AsyncMock()
    if read is not None:
        handler.read_zone_temperature = read
    runtime = attach_runtime(hass, entry, mac, handler)
    runtime.platforms["climate"] = dict(configured_climate or {})
    if configured_switch is not None:
        runtime.platforms["switch"] = dict(configured_switch)
    zones: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, zones.extend)
    return handler, zones, entry


async def _receive(hass: HomeAssistant, handler: Any, frames: list[str]) -> None:
    for raw in frames:
        message = OWNMessage.parse(raw)
        if isinstance(message, OWNHeatingEvent):
            await handler._event_dispatcher.process_message(message)
    await hass.async_block_till_done()


async def _settle(hass: HomeAssistant, handler: Any) -> None:
    handler._initial_discovery_done.set()
    await hass.async_block_till_done(wait_background_tasks=True)


# ---------------------------------------------------------------------------
# 1. Live 57-frame bit-flip trace replay (MyHomeServer1 2.87.13, Issue #966)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "probe65_result"),
    [
        ("MyHomeServer1", False),  # 12 of 13 gateways NACK *#4*65*0##
        ("MH202", True),  # MH202 returns ACK with zero bus frames
    ],
)
async def test_issue_966_57_frame_trace_replay_blocks_bitflip_zone65(
    hass: HomeAssistant,
    model: str,
    probe65_result: bool,
) -> None:
    """Replay all 57 verbatim UTC frames: Zones 1, 2, 4, 5 confirm; phantom Zone 65 is blocked."""
    trace = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    assert trace["synthetic"] is False
    assert trace["capture"]["kind"] == "bus_listener_log"
    assert trace["gateway"]["model"] == "MyHomeServer1"
    assert trace["gateway"]["firmware"] == "2.87.13"
    assert trace["gateway"]["mac"] == TRACE_MAC
    assert trace["gateway"]["mac_redacted"] is True
    assert trace["gateway"]["host"] == "192.0.2.98"
    assert (
        trace["capture"]["source_discussion"]
        == "https://github.com/orgs/OpenWebNet-HA/discussions/963#discussioncomment-19773009"
    )
    assert trace["capture"]["source_issue"] == "https://github.com/OpenWebNet-HA/MyHOME/issues/966"

    frames = trace["frames"]
    assert len(frames) == 57

    # Verify the exact 12.9 ms bit-flip pair (Zone 1 -> Zone 65) in the trace.
    z1_before = next(
        f for f in frames if f["iso_time"] == "2026-11-19T23:09:20.755578+00:00"
    )
    z65 = next(
        f for f in frames if f["iso_time"] == "2026-11-19T23:09:20.768495+00:00"
    )
    assert z1_before["raw"] == "*#4*1*0*0190##"
    assert z65["raw"] == "*#4*65*0*0190##"
    delta_ms = (
        datetime.fromisoformat(z65["iso_time"])
        - datetime.fromisoformat(z1_before["iso_time"])
    ).total_seconds() * 1000
    assert 12.8 <= delta_ms <= 13.0
    assert sum(1 for f in frames if f["where"] == "65") == 1

    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        return probe65_result if where == "65" else False

    handler, zones, _entry = await _plant(hass, model=model, read=read)
    handler._initial_discovery_done.set()

    for item in frames:
        msg = OWNMessage.parse(item["raw"])
        if isinstance(msg, OWNHeatingEvent):
            await handler._event_dispatcher.process_message(msg)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert sorted(z._where for z in zones) == ["1", "2", "4", "5"]
    assert "65" not in {z._where for z in zones}
    assert "65" in asked
    by_where = {z._where: z for z in zones}
    assert by_where["1"].current_temperature == 19.0
    assert by_where["2"].current_temperature == 19.1
    assert by_where["4"].current_temperature == 19.6
    assert by_where["5"].current_temperature == 19.5


# ---------------------------------------------------------------------------
# 2. Hold slots, passive confirmation, echo suppression & active reads
# ---------------------------------------------------------------------------


def test_thermo_hold_slot_categories_avoid_act_prefix() -> None:
    """Verify _thermo_hold_slot keys across dim 20, other dim, message_type, and fallback."""
    fancoil = OWNMessage.parse("*#4*2#3*20*120##")
    assert _thermo_hold_slot(fancoil) == "fancoil:3"
    assert not _thermo_hold_slot(fancoil).startswith(("act:", "dim19"))

    temp = OWNMessage.parse("*#4*2*0*0190##")
    assert _thermo_hold_slot(temp) == "dim:0"

    dim19 = OWNMessage.parse("*#4*2*19*5*0##")
    assert _thermo_hold_slot(dim19) == "dim:19"
    assert not _thermo_hold_slot(dim19).startswith(("act:", "dim19"))

    mode = OWNMessage.parse("*4*1*2##")
    assert _thermo_hold_slot(mode) == f"type:{mode.message_type}"

    typeless = SimpleNamespace(dimension=None, message_type=None)
    assert str(typeless) == _thermo_hold_slot(typeless).removeprefix("raw:")


async def test_echo_suppression_under_5s_and_passive_confirm_after_5s_or_distinct_frame(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical raw repeats < 5.0 s are suppressed; >= 5.0 s or distinct frames confirm passively."""
    now = 1000.0
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: now))

    read = AsyncMock(return_value=False)
    handler, zones, _entry = await _plant(hass, read=read)

    # Frame 1 for Zone 12 held as candidate; identical echo at +1.5 s must not confirm.
    await _receive(hass, handler, ["*#4*12*0*0205##"])
    assert zones == []

    now += 1.5
    await _receive(hass, handler, ["*#4*12*0*0205##"])
    assert zones == []

    # Identical frame at >= 5.0 s confirms passively and replays the held frame.
    now += CANDIDATE_ECHO_WINDOW
    await _receive(hass, handler, ["*#4*12*0*0205##"])
    assert [z._where for z in zones] == ["12"]
    assert zones[0].current_temperature == 20.5

    # Distinct frame within < 5.0 s confirms immediately in the same turn (no active read needed).
    await _receive(hass, handler, ["*#4*14*0*0210##", "*#4*14*12*0220*3##"])
    assert sorted(z._where for z in zones) == ["12", "14"]
    z14 = next(z for z in zones if z._where == "14")
    assert z14.current_temperature == 21.0
    assert z14.target_temperature == 22.0

    await _settle(hass, handler)
    read.assert_not_awaited()


async def test_active_confirmation_answers_with_identical_frame_within_5s(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Active read sets _confirming_pair so even an identical *#4*Z*0*TTTT## reply < 5 s confirms."""
    now = 500.0
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: now))
    handler: Any = None
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        # Same raw frame as the triggering one within < 5 s while _confirming_pair == ("15", None).
        await _receive(hass, handler, ["*#4*15*0*0215##"])
        return True

    handler, zones, _entry = await _plant(hass, read=read)
    await _receive(hass, handler, ["*#4*15*0*0215##"])
    assert zones == []

    now += 0.2
    await _settle(hass, handler)
    assert asked == ["15"]
    assert [z._where for z in zones] == ["15"]
    assert zones[0].current_temperature == 21.5


async def test_active_confirmation_nack_mh202_empty_ack_and_busy_retry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cover the unanswered read (NACK, empty ACK: kept, not read again), busy retry, and double-busy fallback."""
    now = 2000.0
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: now))
    caplog.set_level("DEBUG", logger="custom_components.myhome")

    calls: list[str] = []
    handler: Any = None

    async def read(where: str) -> bool | None:
        calls.append(where)
        if where == "65":
            return False  # NACK -> stays a candidate
        if where == "66":
            return True  # empty ACK with zero reply -> stays a candidate
        if where == "67":
            if calls.count("67") == 1:
                return None  # busy on attempt 1 -> retry
            await _receive(hass, handler, ["*#4*67*0*0220##"])
            return True
        if where == "68":
            return None  # busy twice -> leave for passive confirmation
        return False

    handler, zones, _entry = await _plant(hass, read=read)
    await _receive(
        hass,
        handler,
        [
            "*#4*65*0*0190##",
            "*#4*66*0*0190##",
            "*#4*67*0*0220##",
            "*#4*68*0*0190##",
        ],
    )
    await _settle(hass, handler)

    assert calls == ["65", "66", "67", "67", "68", "68"]
    assert [z._where for z in zones] == ["67"]
    assert "leaving it to confirm on its next frame" in caplog.text

    # Zones 65 and 66 are still candidates: an identical frame < 5 s later confirms nothing.
    await _receive(hass, handler, ["*#4*65*0*0190##", "*#4*66*0*0190##"])
    assert [z._where for z in zones] == ["67"]

    # Zone 68 was left in _candidates after busy gateway; a 2nd independent frame confirms it passively.
    await _receive(hass, handler, ["*#4*68*12*0210*3##"])
    assert sorted(z._where for z in zones) == ["67", "68"]

    # Nothing is read again for 65 and 66.
    await _settle(hass, handler)
    assert sorted(z._where for z in zones) == ["67", "68"]
    assert calls == ["65", "66", "67", "67", "68", "68"]


async def test_confirm_candidates_passive_confirm_during_wait_for_bus_quiet(
    hass: HomeAssistant,
) -> None:
    """Passive confirmation during _wait_for_bus_quiet skips read_zone_temperature."""
    handler: Any = None
    read = AsyncMock(return_value=False)

    async def quiet_confirms(_since: float) -> None:
        await _receive(hass, handler, ["*#4*19*12*0210*3##"])

    handler, zones, _ = await _plant(hass, read=read)
    handler._wait_for_bus_quiet = quiet_confirms
    await _receive(hass, handler, ["*#4*19*0*0200##"])
    await _settle(hass, handler)
    read.assert_not_awaited()
    assert [z._where for z in zones] == ["19"]


async def test_confirm_candidates_passive_confirm_while_waiting_on_lock(
    hass: HomeAssistant,
) -> None:
    """Passive confirmation while awaiting _who4_startup_poll_lock skips read_zone_temperature."""
    read = AsyncMock(return_value=False)
    handler, zones, _ = await _plant(hass, read=read)
    handler._wait_for_bus_quiet = None  # exercise missing _wait_for_bus_quiet branch
    await handler._who4_startup_poll_lock.acquire()
    await _receive(hass, handler, ["*#4*21*0*0200##"])
    handler._initial_discovery_done.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Candidate is now blocked on _who4_startup_poll_lock; confirm it passively before releasing lock.
    await _receive(hass, handler, ["*#4*21*12*0215*3##"])
    handler._who4_startup_poll_lock.release()
    await hass.async_block_till_done(wait_background_tasks=True)
    read.assert_not_awaited()
    assert [z._where for z in zones] == ["21"]


async def test_confirm_candidates_without_lock_and_sync_wait_for_initial_discovery(
    hass: HomeAssistant,
) -> None:
    """Bare gateway double without _who4_startup_poll_lock and non-awaitable wait_for_initial_discovery."""
    read = AsyncMock(return_value=False)
    handler, zones, _ = await _plant(hass, read=read)
    handler._who4_startup_poll_lock = None
    handler.wait_for_initial_discovery = lambda: None
    await _receive(hass, handler, ["*#4*22*0*0200##"])
    await hass.async_block_till_done(wait_background_tasks=True)
    read.assert_awaited_once_with("22")
    assert zones == []


# ---------------------------------------------------------------------------
# 3. dim19 & dim20 cross-confirmation + expiry + candidate pruning
# ---------------------------------------------------------------------------


async def test_dim20_cross_confirmation_both_orders_and_expiry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dim20 valve + thermostat confirm in either order; expired candidate drops non-actuator slots."""
    now = 100.0
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: now))

    read = AsyncMock(return_value=False)
    handler, zones, _entry = await _plant(hass, read=read)

    # Order 1: dim20 plain valve first -> thermostat frame second.
    await _receive(hass, handler, ["*#4*31#1*20*0##"])
    assert zones == []
    await _receive(hass, handler, ["*#4*31*0*0210##"])
    assert [z._where for z in zones] == ["31"]
    assert zones[0].current_temperature == 21.0
    assert zones[0].extra_state_attributes["actuators"] == {"1": "off"}

    # Order 2: thermostat frame first -> dim20 plain valve second within CANDIDATE_MAX_AGE.
    await _receive(hass, handler, ["*#4*32*0*0220##", "*#4*32#1*20*1##"])
    assert sorted(z._where for z in zones) == ["31", "32"]
    z32 = next(z for z in zones if z._where == "32")
    assert z32.current_temperature == 22.0
    assert z32.extra_state_attributes["actuators"] == {"1": "on"}

    # Expiry before dim20: expired candidate drops held thermostat slot and keeps only valve slot.
    await _receive(hass, handler, ["*#4*33*0*0190##"])
    now += CANDIDATE_MAX_AGE + 10.0
    await _receive(hass, handler, ["*#4*33#1*20*0##"])
    assert sorted(z._where for z in zones) == ["31", "32"]

    # Confirming Zone 33 via a fresh thermostat frame replays ONLY the kept valve slot (not expired 19.0).
    await _receive(hass, handler, ["*#4*33*12*0230*3##"])
    assert sorted(z._where for z in zones) == ["31", "32", "33"]
    z33 = next(z for z in zones if z._where == "33")
    assert z33.current_temperature is None
    assert z33.target_temperature == 23.0
    assert z33.extra_state_attributes["actuators"] == {"1": "off"}

    # Expiry on non-actuator frame: expired candidate is pruned and new frame becomes fresh candidate.
    await _receive(hass, handler, ["*#4*34*0*0180##"])
    now += CANDIDATE_MAX_AGE + 10.0
    await _receive(hass, handler, ["*#4*34*12*0210*3##"])
    assert "34" not in {z._where for z in zones}

    await _settle(hass, handler)


async def test_dim19_switch_actuator_cross_confirmation_both_orders_and_expiry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dim19 valve-only (thermo switch zone) + thermostat frame confirm in both orders and honour expiry."""
    now = 300.0
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: now))

    read = AsyncMock(return_value=False)
    handler, zones, _entry = await _plant(
        hass,
        read=read,
        configured_switch={
            "sw41": {"who": "4", "where": "41#1"},
            "sw42": {"who": "4", "where": "42#1"},
            "sw43": {"who": "4", "where": "43#1"},
        },
    )

    # Order 1: dim19 valve-only first -> thermostat frame second.
    await _receive(hass, handler, ["*#4*41*19*0*0##"])
    assert zones == []
    await _receive(hass, handler, ["*#4*41*0*0200##"])
    assert [z._where for z in zones] == ["41"]
    assert zones[0].current_temperature == 20.0

    # Order 2: thermostat frame first -> dim19 valve-only second within CANDIDATE_MAX_AGE.
    await _receive(hass, handler, ["*#4*42*0*0210##", "*#4*42*19*0*1##"])
    assert sorted(z._where for z in zones) == ["41", "42"]
    z42 = next(z for z in zones if z._where == "42")
    assert z42.current_temperature == 21.0

    # Expiry before dim19 valve-only: drops expired candidate and holds valve-only frame.
    await _receive(hass, handler, ["*#4*43*0*0190##"])
    now += CANDIDATE_MAX_AGE + 10.0
    await _receive(hass, handler, ["*#4*43*19*0*0##"])
    assert sorted(z._where for z in zones) == ["41", "42"]

    await _settle(hass, handler)


# ---------------------------------------------------------------------------
# 4. Immediate creation, F422 routed passive confirm, exposed & unit edge cases
# ---------------------------------------------------------------------------


async def test_configured_registry_central_and_f422_routed_zones(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configured, registry, and #0 create immediately; F422 routed zones confirm passively."""
    now = 400.0
    monkeypatch.setattr(climate_discovery, "time", SimpleNamespace(monotonic=lambda: now))

    read = AsyncMock(return_value=False)
    handler, zones, _entry = await _plant(
        hass,
        read=read,
        configured_climate={"zone_1": {"who": "4", "zone": "1", "name": "Configured Zone 1"}},
    )
    # Zone 1 created at setup from myhome.yaml.
    assert [z._where for z in zones] == ["1"]

    # Central unit (#0) creates immediately on a single frame.
    await _receive(hass, handler, ["*#4*#0*0*0215##"])
    assert sorted(z._where for z in zones) == ["#0", "1"]

    # F422 routed zone (2#4#01) is never actively queried; 1st frame held, <5s echo ignored, 2nd confirms.
    await _receive(hass, handler, ["*#4*2#4#01*0*0230##"])
    assert sorted(z._where for z in zones) == ["#0", "1"]
    await _receive(hass, handler, ["*#4*2#4#01*0*0230##"])
    assert sorted(z._where for z in zones) == ["#0", "1"]
    await _receive(hass, handler, ["*#4*2#4#01*12*0210*3##"])
    assert sorted(z._where for z in zones) == ["#0", "1", "2"]
    z2 = next(z for z in zones if z._where == "2")
    assert z2._interface == "01"
    assert z2.current_temperature == 23.0
    assert z2.target_temperature == 21.0

    await _settle(hass, handler)
    read.assert_not_awaited()


async def test_exposed_virtual_probe_and_direct_zone_discovery_unit_edges(
    hass: HomeAssistant,
) -> None:
    """Cover exposed-zone candidate guard, non-Future task close, and direct _drop_candidate_held."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: HANDLER_MAC}, unique_id=HANDLER_MAC)
    entry.add_to_hass(hass)
    gw = _handler(hass, "MyHomeServer1")
    gw.read_zone_temperature = AsyncMock(return_value=False)
    runtime = attach_runtime(hass, entry, HANDLER_MAC, gw)
    runtime.platforms["climate"] = {}

    zones: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, zones.extend)

    with patch(
        "custom_components.myhome.climate_discovery.is_exposed_on_bus",
        side_effect=lambda _hass, _entry, _who, where: str(where) in {"9", "10"},
    ):
        # Order A (Zone 9): non-dim0 frame held without active read; exposed dim0 alone does not confirm; 2nd non-dim0 confirms.
        await _receive(hass, gw, ["*#4*9*12*0210*3##"])
        assert zones == []
        await _settle(hass, gw)
        gw.read_zone_temperature.assert_not_awaited()

        await _receive(hass, gw, ["*#4*9*0*0205##"])
        assert zones == []
        await _receive(hass, gw, ["*#4*9*13*00##"])
        assert [z._where for z in zones] == ["9"]
        assert zones[0].current_temperature == 20.5
        assert zones[0].target_temperature == 21.0

        # Order B (Zone 10): exposed dim0 held first; 1st non-dim0 frame stays held without active read; 2nd non-dim0 confirms.
        await _receive(hass, gw, ["*#4*10*0*0215##"])
        assert [z._where for z in zones] == ["9"]
        await _receive(hass, gw, ["*#4*10*12*0220*3##"])
        assert [z._where for z in zones] == ["9"]
        await _settle(hass, gw)
        gw.read_zone_temperature.assert_not_awaited()

        await _receive(hass, gw, ["*#4*10*13*00##"])
        assert sorted(z._where for z in zones) == ["10", "9"]
        z10 = next(z for z in zones if z._where == "10")
        assert z10.current_temperature == 21.5
        assert z10.target_temperature == 22.0

    # Direct ZoneDiscovery unit edges: non-bus accept, _zone_config match, non-Future task, _drop_candidate_held.
    mock_entry = MagicMock()
    mock_entry.entry_id = "e1"
    mock_entry.subentries = {}
    mock_entry.async_create_background_task = MagicMock(return_value="not_a_future")
    mock_gw = MagicMock()
    mock_gw.mac = HANDLER_MAC
    mock_gw.read_zone_temperature = AsyncMock(return_value=False)
    mock_rt = SimpleNamespace(
        gateway=mock_gw,
        platforms={p: {} for p in PLATFORMS},
    )
    mock_rt.platforms["climate"] = {"4-50": {"who": "4", "where": "50"}}
    zd = ZoneDiscovery(hass, mock_entry, mock_rt, lambda ctx, cfg: MagicMock())

    # Registry source & configured zone via _zone_config return True immediately.
    assert zd.accept(DeviceContext(Address("55"), "4", source="registry")) is True
    assert zd.accept(DeviceContext(Address("50"), "4", source="bus", message=OWNMessage.parse("*#4*50*0*0200##"))) is True

    # Non-Future task from async_create_background_task closes the coroutine cleanly.
    msg_51 = OWNMessage.parse("*#4*51*0*0200##")
    assert zd.accept(DeviceContext(Address("51"), "4", source="bus", message=msg_51)) is False
    assert zd._candidate_task is None

    # Direct _drop_candidate_held: empty/missing pair, and preserving act:/dim19/dim0 while dropping candidate slot.
    zd._drop_candidate_held(("99", None))
    zd._held[("52", None)] = {
        "act:1": OWNMessage.parse("*#4*52#1*20*0##"),
        "dim0": OWNMessage.parse("*#4*52*0*0215##"),
        "dim:0": OWNMessage.parse("*#4*52*0*0200##"),
    }
    zd._drop_candidate_held(("52", None))
    assert list(zd._held[("52", None)]) == ["act:1", "dim0"]

    # build() with central #0 (pair is None).
    built_central = zd.build(DeviceContext(Address("#0"), "4", source="bus", message=OWNMessage.parse("*#4*#0*0*0215##")))
    assert built_central is not None


async def test_f422_registry_restore_immediate(hass: HomeAssistant) -> None:
    """Registry entries create immediately without candidate delay."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: HANDLER_MAC}, unique_id=HANDLER_MAC)
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{HANDLER_MAC}-4-17", config_entry=entry)

    gw = _handler(hass, "MyHomeServer1")
    runtime = attach_runtime(hass, entry, HANDLER_MAC, gw)
    runtime.platforms["climate"] = {}
    zones: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, zones.extend)
    assert [z._where for z in zones] == ["17"]
    async_dispatcher_send(hass, f"myhome_message_{HANDLER_MAC}", OWNMessage.parse("*#4*17*0*0205##"))
    await hass.async_block_till_done()
    assert zones[0].current_temperature == 20.5
