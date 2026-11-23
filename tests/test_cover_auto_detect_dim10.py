"""Tests for automatic detection and promotion of WHO=2 Dimension 10 position-reporting shutters (#926)."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_component import DATA_INSTANCES
from OWNd.message import OWNAutomationEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.myhome.const import (
    CONF_ADVANCED_COVERS,
    CONF_ADVANCED_SHUTTER,
    CONF_COVER_TRAVEL_TIMES,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_STRATEGY,
    CONF_TRAVEL_TIME,
    DOMAIN,
)
from custom_components.myhome.cover import (
    MyHOMECover,
    _can_auto_promote_advanced,
    _is_stored_advanced_cover,
    _message_has_calibrated_dim10,
    _normalize_bus_interface,
)
from custom_components.myhome.cover_strategies import NativePositionStrategy
from custom_components.myhome.gateway import FrameScheduler, MyHOMEGatewayHandler
from tests.mock_gateway_harness import MockGatewayHarness

MAC = "00:03:50:00:07:15"


@pytest.fixture(autouse=True)
def _short_bus_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten gateway quiet periods for deterministic test runs."""
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_PERIOD", 0.02)
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_CAP", 0.05)


def _make_entry(
    port: int,
    *,
    options: dict | None = None,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: MAC,
            CONF_NAME: "MH201",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "MH201 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.27",
        },
        options=options or {},
        unique_id=MAC,
    )


async def _setup_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
) -> MyHOMEGatewayHandler:
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    handler: MyHOMEGatewayHandler = hass.data[DOMAIN][MAC][CONF_ENTITY]
    handler._on_event_connection_state_change(True)
    return handler


async def _feed(hass: HomeAssistant, raw: str) -> None:
    msg = OWNMessage.parse(raw)
    assert msg is not None, f"Failed to parse frame {raw!r}"
    async_dispatcher_send(hass, f"myhome_message_{MAC}", msg)
    await hass.async_block_till_done()


def _drain_sent(handler: MyHOMEGatewayHandler, harness: MockGatewayHarness) -> list[str]:
    frames = list(harness.received_messages)
    while not handler.send_buffer.empty():
        item = handler.send_buffer.get_nowait()
        if item and item.get("message"):
            frames.append(str(item["message"]))
    return frames


@pytest.mark.asyncio
async def test_mh201_discovery_calibrated_dim10_auto_promotes_and_sends_dim11(
    hass: HomeAssistant,
) -> None:
    """MH201 fw 3.3.6 (issue #466) Dimension 10 sequence auto-promotes on discovery and commands via Dimension 11."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            harness.received_messages.clear()

            # MH201 fw 3.3.6 sweep sequence from myhome_sweep_MH201_all_2026-09-25T19-11-35.json:
            # Dim 10 then plain stop
            await _feed(hass, "*#2*0715*10*10*100*000*0##")
            await _feed(hass, "*2*0*0715##")

            state = hass.states.get("cover.cover_0715")
            assert state is not None
            assert state.state == "open"
            assert state.attributes.get("current_position") == 100
            assert state.attributes.get("min_position_delta") == 2
            assert "travel_time" not in state.attributes
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0715": True}

            sent_during_discovery = _drain_sent(handler, harness)
            assert not any(
                m.startswith("*#2*0715") for m in sent_during_discovery
            ), f"Unexpected p2p poll during Dimension 10 discovery: {sent_during_discovery}"

            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0715", "position": 40},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()

            sent_cmds = _drain_sent(handler, harness)
            assert "*#2*0715*#11#001*40##" in sent_cmds
            assert "*2*2*0715##" not in sent_cmds
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_fedem95_mh201_ack_only_poll_promotes_and_updates_calibration_buttons(
    hass: HomeAssistant,
) -> None:
    """Reproduce @fedem95's MH201 fw 3.6.27 comment (#926): ACK-only poll -> event-session Dim 10 -> Dim 11 + buttons."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0110",
        config_entry=entry,
        suggested_object_id="cover_0110",
    )
    registry.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0110-calibrate",
        config_entry=entry,
        suggested_object_id="cover_0110_calibrate_travel_time",
    )

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            await asyncio.sleep(0.2)
            await hass.async_block_till_done()

            # Pre-existing v2.0.0 timed cover polls -> *#2*0110##; MH201 command session returns ACK only.
            sent_poll = _drain_sent(handler, harness)
            assert "*#2*0110##" in sent_poll

            cal_btn_id = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-0110-calibrate")
            cal_all_id = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-calibrate-all-covers")
            assert cal_btn_id is not None and cal_all_id is not None
            assert hass.states.get(cal_btn_id).state != "unavailable"
            assert hass.states.get(cal_all_id).state != "unavailable"

            # Event session may echo the request *#2*0110## before actuator replies: must not create a phantom entity.
            await _feed(hass, "*#2*0110##")
            assert hass.states.async_entity_ids("cover") == ["cover.cover_0110"]
            assert hass.states.get(cal_btn_id).state != "unavailable"

            # Exact MH201 fw 3.6.27 event-session replies from issue #926 comment #6910228975
            await _feed(hass, "*#2*0110*10*10*75*001*0##")
            await _feed(hass, "*2*0*0110##")

            state = hass.states.get("cover.cover_0110")
            assert state is not None
            assert state.state == "open"
            assert state.attributes.get("current_position") == 75
            assert state.attributes.get("min_position_delta") == 2
            assert "travel_time" not in state.attributes
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0110": True}

            # Calibration buttons immediately turn unavailable in the running session
            assert hass.states.get(cal_btn_id).state == "unavailable"
            assert hass.states.get(cal_all_id).state == "unavailable"

            # Subsequent position command uses native Dimension 11
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0110", "position": 40},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert "*#2*0110*#11#001*40##" in _drain_sent(handler, harness)

            # On reload, persisted advanced cover prunes the redundant per-cover calibrate button
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-0110-calibrate") is None
            assert hass.states.get(cal_all_id).state == "unavailable"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_runtime_promotion_on_stationary_and_moving_dim10_frames(
    hass: HomeAssistant,
) -> None:
    """Covers discovered via plain frames promote in-place on first calibrated Dimension 10."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)

            # 1. Stationary Dimension 10 frame after timed motion started
            await _feed(hass, "*2*0*0715##")
            state = hass.states.get("cover.cover_0715")
            assert state is not None
            assert state.attributes.get("travel_time") == 25
            assert "min_position_delta" not in state.attributes
            assert CONF_ADVANCED_COVERS not in entry.options

            # Start a timed opening run so live refresh + wall settle timers are active
            await _feed(hass, "*2*1*0715##")
            assert hass.states.get("cover.cover_0715").state == "opening"

            # First calibrated Dim 10 frame promotes cleanly in-place
            await _feed(hass, "*#2*0715*10*10*65*000*0##")
            state = hass.states.get("cover.cover_0715")
            assert state is not None
            assert state.state == "open"
            assert state.attributes.get("current_position") == 65
            assert state.attributes.get("min_position_delta") == 2
            for timed_attr in ("travel_time", "travel_time_down", "travel_time_up"):
                assert timed_attr not in state.attributes
            assert state.attributes.get("motion_started_at") is None
            assert entry.options.get(CONF_ADVANCED_COVERS, {}).get("0715") is True

            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0715", "position": 30},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert "*#2*0715*#11#001*30##" in _drain_sent(handler, harness)

            # 2. In-flight moving Dimension 10 frame (*#2*0714*10*11*60*000*0##) promotes before
            # moving-level handling so no timed settle / interpolation timer is armed.
            await _feed(hass, "*2*0*0714##")
            await _feed(hass, "*#2*0714*10*11*60*000*0##")
            state_714 = hass.states.get("cover.cover_0714")
            assert state_714 is not None
            assert state_714.state == "opening"
            assert state_714.attributes.get("current_position") == 60
            assert state_714.attributes.get("min_position_delta") == 2
            assert entry.options.get(CONF_ADVANCED_COVERS, {}).get("0714") is True
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_persisted_advanced_cover_survives_restart_and_defers_sweep_poll(
    hass: HomeAssistant,
) -> None:
    """Persisted advanced covers start native on restart and defer/skip sweep polls."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port, options={CONF_ADVANCED_COVERS: {"0715": True, "0714": True}})
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0715",
        config_entry=entry,
        suggested_object_id="cover_0715",
    )
    registry.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0714",
        config_entry=entry,
        suggested_object_id="cover_0714",
    )

    sweep_proceed = asyncio.Event()
    real_initial_discovery = MyHOMEGatewayHandler.initial_discovery

    async def controlled_initial_discovery(self: MyHOMEGatewayHandler) -> None:
        await sweep_proceed.wait()
        await real_initial_discovery(self)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
            patch.object(MyHOMEGatewayHandler, "initial_discovery", controlled_initial_discovery),
        ):
            setup_task = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
            for _ in range(50):
                if hass.data.get(DOMAIN, {}).get(MAC) and hass.states.get("cover.cover_0715"):
                    break
                await asyncio.sleep(0.05)

            handler: MyHOMEGatewayHandler = hass.data[DOMAIN][MAC][CONF_ENTITY]
            handler._on_event_connection_state_change(True)

            # Before any frame arrives, both restored covers already run NativePositionStrategy
            state_715 = hass.states.get("cover.cover_0715")
            state_714 = hass.states.get("cover.cover_0714")
            assert state_715 is not None and state_715.attributes.get("min_position_delta") == 2
            assert "travel_time" not in state_715.attributes
            assert state_714 is not None and state_714.attributes.get("min_position_delta") == 2

            # While sweep is active, neither cover has polled yet
            assert not any(
                m.startswith("*#2*0715") or m.startswith("*#2*0714")
                for m in harness.received_messages
            )

            # 0715 reports Dim 10 during the sweep -> marks _bus_state_complete and skips poll
            await _feed(hass, "*#2*0715*10*10*80*000*0##")
            assert hass.states.get("cover.cover_0715").attributes.get("current_position") == 80

            # Allow discovery sweep to finish; 0714 never answered, so only 0714 polls *10##
            sweep_proceed.set()
            await setup_task
            await handler.wait_for_initial_discovery(timeout=5.0)
            await asyncio.sleep(0.2)
            await hass.async_block_till_done()

            sent = _drain_sent(handler, harness)
            assert "*#2*0714*10##" in sent
            assert "*#2*0715*10##" not in sent
            assert "*#2*0714##" not in sent
    finally:
        sweep_proceed.set()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_guardrails_uncalibrated_standard_yaml_and_stored_calibration(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Verify LEVEL=255, standard relays, explicit YAML kinematics, and calibrations stay timed."""
    yaml_path = tmp_path / "myhome.yaml"
    yaml_path.write_text(
        f"""
mh201:
  mac: "{MAC}"
  cover:
    cosmetic_blind:
      where: "0715"
      name: "Cosmetic Blind"
      class: blind
    cosmetic_with_cal:
      where: "0711"
      name: "Cosmetic Calibrated"
      class: shutter
    strategy_native:
      where: "0712"
      name: "Explicit Native"
      strategy: native
    explicit_adv_true:
      where: "0801"
      name: "Explicit Adv True"
      advanced: true
    explicit_adv_false:
      where: "0701"
      name: "Explicit Adv False"
      advanced: false
    explicit_adv_shutter_false:
      where: "0702"
      name: "Explicit Adv Shutter False"
      advanced_shutter: false
    explicit_default_travel:
      where: "0703"
      name: "Explicit Travel 25"
      travel_time: 25
    explicit_custom_travel:
      where: "0704"
      name: "Explicit Travel 18"
      travel_time: 18
    explicit_strategy_timed:
      where: "0705"
      name: "Explicit Timed"
      strategy: timed
    explicit_strategy_simple:
      where: "0706"
      name: "Explicit Simple"
      strategy: simple
    explicit_slat_tilt:
      where: "0707"
      name: "Explicit Venetian"
      slat_tilt: true
    standard_f411_model:
      where: "0708"
      name: "Standard F411"
      model: "F411/2"
    area_scope_cover:
      where: "1"
      name: "Area 1 Scope"
    impulse_gate:
      where: "50"
      who: "1"
      name: "Impulse Gate"
""",
        encoding="utf-8",
    )

    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(
        port,
        options={
            CONF_FILE_PATH: str(yaml_path),
            CONF_COVER_TRAVEL_TIMES: {
                "0709": {"down": 18.0, "up": 19.0, "source": "measured"},
                "0711": {"down": 16.0, "up": 17.0, "source": "measured"},
            },
        },
    )

    # Pre-register stored calibrated discovered cover 0709 and a WHO=1 impulse cover
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0709",
        config_entry=entry,
        suggested_object_id="cover_0709",
    )
    reg.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id=f"{MAC}-1-50",
        config_entry=entry,
        suggested_object_id="impulse_gate",
    )

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)

            # 1. Cosmetic-only YAML blind starts with calibration_source="default" (not "yaml"),
            # then auto-promotes on calibrated Dim 10 frame.
            cosmetic_before = hass.states.get("cover.cosmetic_blind")
            assert cosmetic_before is not None
            assert cosmetic_before.attributes.get("calibration_source") == "default"
            await hass.services.async_call(
                DOMAIN,
                "reset_cover_travel_time",
                {"entity_id": "cover.cosmetic_blind"},
                blocking=True,
            )
            assert hass.states.get("cover.cosmetic_blind").attributes.get("calibration_source") == "default"

            await _feed(hass, "*#2*0715*10*10*75*000*0##")
            cosmetic = hass.states.get("cover.cosmetic_blind")
            assert cosmetic is not None
            assert cosmetic.attributes.get("min_position_delta") == 2
            assert cosmetic.attributes.get("current_position") == 75
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0715": True}

            # Cosmetic YAML cover with stored calibration preserves it and stays timed on Dim 10
            await _feed(hass, "*#2*0711*10*10*60*000*0##")
            st_cosmetic_cal = hass.states.get("cover.cosmetic_calibrated")
            assert st_cosmetic_cal is not None
            assert st_cosmetic_cal.attributes.get("travel_time_down") == 16.0
            assert "min_position_delta" not in st_cosmetic_cal.attributes

            # Non-calibratable YAML / scope / impulse covers have no calibration button and are excluded
            # from CalibrateAllCoversButtonEntity
            for dev_id in ("0712", "0801", "1"):
                assert reg.async_get_entity_id("button", DOMAIN, f"{MAC}-2-{dev_id}-calibrate") is None

            cal_all_btn = hass.states.get(
                reg.async_get_entity_id("button", DOMAIN, f"{MAC}-calibrate-all-covers")
            )
            assert cal_all_btn is not None and cal_all_btn.state != "unavailable"
            from custom_components.myhome.button import CalibrateAllCoversButtonEntity

            cal_all_helper = CalibrateAllCoversButtonEntity(hass=hass, config_entry=entry, gateway=handler)
            cal_targets = cal_all_helper._cover_entity_ids()
            assert "cover.cosmetic_blind" not in cal_targets
            assert "cover.explicit_native" not in cal_targets
            assert "cover.explicit_adv_true" not in cal_targets
            assert "cover.area_1_scope" not in cal_targets
            assert "cover.impulse_gate" not in cal_targets
            assert "cover.explicit_adv_false" in cal_targets

            # 2. Explicit YAML covers with kinematics / model / scope NEVER auto-promote
            for where, entity_id in (
                ("0701", "cover.explicit_adv_false"),
                ("0702", "cover.explicit_adv_shutter_false"),
                ("0703", "cover.explicit_travel_25"),
                ("0704", "cover.explicit_travel_18"),
                ("0705", "cover.explicit_timed"),
                ("0706", "cover.explicit_simple"),
                ("0707", "cover.explicit_venetian"),
                ("0708", "cover.standard_f411"),
            ):
                await _feed(hass, f"*#2*{where}*10*10*50*000*0##")
                st = hass.states.get(entity_id)
                assert st is not None
                assert "min_position_delta" not in st.attributes, (
                    f"{entity_id} unexpectedly promoted"
                )
            assert "min_position_delta" not in hass.states.get("cover.area_1_scope").attributes

            # 3. Pre-calibrated discovered cover 0709 stays timed with its measured travel times
            await _feed(hass, "*#2*0709*10*10*50*000*0##")
            st_cal = hass.states.get("cover.cover_0709")
            assert st_cal is not None
            assert "min_position_delta" not in st_cal.attributes
            assert st_cal.attributes.get("travel_time_down") == 18.0

            # 4. Uncalibrated actuator LEVEL=255 stays timed and never writes advanced_covers
            await _feed(hass, "*#2*0710*10*10*255*000*0##")
            st_uncal = hass.states.get("cover.cover_0710")
            assert st_uncal is not None
            assert st_uncal.attributes.get("travel_time") == 25
            assert "min_position_delta" not in st_uncal.attributes

            # 5. Standard plain timed relay frames stay timed
            await _feed(hass, "*2*1*21##")
            await _feed(hass, "*2*0*21##")
            st_std = hass.states.get("cover.cover_21")
            assert st_std is not None
            assert st_std.attributes.get("travel_time") == 25
            assert "min_position_delta" not in st_std.attributes

            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0715": True}
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_manual_and_measured_calibration_lock_out_future_auto_promotion(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calibrating a timed cover manually, via min_motor_pulse, or interactively disables future auto-promotion."""
    monkeypatch.setattr("custom_components.myhome.cover.CALIBRATION_SETTLE", 0.0)
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)

            # 1. Manual set_cover_travel_time locks out Dim 10 auto-promotion
            await _feed(hass, "*2*0*0715##")
            await hass.services.async_call(
                DOMAIN,
                "set_cover_travel_time",
                {"entity_id": "cover.cover_0715", "travel_time": 20},
                blocking=True,
            )
            await _feed(hass, "*#2*0715*10*10*55*000*0##")
            st_manual = hass.states.get("cover.cover_0715")
            assert st_manual is not None
            assert st_manual.attributes.get("travel_time") == 20
            assert "min_position_delta" not in st_manual.attributes
            assert CONF_ADVANCED_COVERS not in entry.options

            # 2. Interactive calibrate_cover also locks out Dim 10 auto-promotion
            await _feed(hass, "*2*0*0714##")
            with patch.object(
                MyHOMECover,
                "_calibration_run",
                AsyncMock(side_effect=[10.0, 14.2, 15.6]),
            ):
                await hass.services.async_call(
                    DOMAIN,
                    "calibrate_cover",
                    {"entity_id": "cover.cover_0714"},
                    blocking=True,
                )
            await _feed(hass, "*#2*0714*10*10*55*001*0##")
            st_cal = hass.states.get("cover.cover_0714")
            assert st_cal is not None
            assert st_cal.attributes.get("travel_time_down") == 14.2
            assert st_cal.attributes.get("travel_time_up") == 15.6
            assert "min_position_delta" not in st_cal.attributes
            assert CONF_ADVANCED_COVERS not in entry.options

            # 3. min_motor_pulse-only set_cover_travel_time also locks out Dim 10 auto-promotion
            await _feed(hass, "*2*0*0713##")
            await hass.services.async_call(
                DOMAIN,
                "set_cover_travel_time",
                {"entity_id": "cover.cover_0713", "min_motor_pulse": 0.3},
                blocking=True,
            )
            await _feed(hass, "*#2*0713*10*10*55*001*0##")
            st_pulse = hass.states.get("cover.cover_0713")
            assert st_pulse is not None
            assert st_pulse.attributes.get("min_motor_pulse") == 0.3
            assert "min_position_delta" not in st_pulse.attributes
            assert CONF_ADVANCED_COVERS not in entry.options
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_entity_removal_cleans_advanced_covers_and_button_promotion_edges(
    hass: HomeAssistant,
) -> None:
    """Deleting a cover cleans advanced_covers, and unrelated cover promotion keeps sibling calibrate buttons available."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    registry = er.async_get(hass)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)

            # Discover two timed covers 0110 and 0111
            await _feed(hass, "*2*0*0110##")
            await _feed(hass, "*2*0*0111##")
            btn_110 = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-0110-calibrate")
            btn_111 = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-0111-calibrate")
            cal_all = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-calibrate-all-covers")
            assert btn_110 and btn_111 and cal_all

            # Promote 0110 only -> 0110's calibrate button turns unavailable, 0111's and calibrate-all stay available
            await _feed(hass, "*#2*0110*10*10*75*001*0##")
            assert hass.states.get(btn_110).state == "unavailable"
            assert hass.states.get(btn_111).state != "unavailable"
            assert hass.states.get(cal_all).state != "unavailable"

            # Promote 0111 too -> both stored in advanced_covers, calibrate-all turns unavailable
            await _feed(hass, "*#2*0111*10*10*20*001*0##")
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0110": True, "0111": True}
            assert hass.states.get(btn_111).state == "unavailable"
            assert hass.states.get(cal_all).state == "unavailable"

            # Remove 0110 from entity registry -> removes "0110", preserves "0111"
            registry.async_remove("cover.cover_0110")
            await hass.async_block_till_done()
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0111": True}

            # Remove 0111 from entity registry -> pops CONF_ADVANCED_COVERS key cleanly
            registry.async_remove("cover.cover_0111")
            await hass.async_block_till_done()
            assert CONF_ADVANCED_COVERS not in entry.options
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_scope_dim10_frames_do_not_promote_members(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Scope Dimension 10 frames (area, general, group, F422-routed) never promote member covers (#927)."""
    yaml_path = tmp_path / "myhome.yaml"
    yaml_path.write_text(
        f"""
mh201:
  mac: "{MAC}"
  cover:
    group_5:
      where: "#5"
      name: "Group 5 Shutters"
      members:
        - "52"
        - "53"
""",
        encoding="utf-8",
    )

    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port, options={CONF_FILE_PATH: str(yaml_path)})
    registry = er.async_get(hass)

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)

            # Discover two main-bus timed covers (52, 53) and two F422-routed covers (52#4#01, 53#4#01)
            for raw in ("*2*0*52##", "*2*0*53##", "*2*0*52#4#01##", "*2*0*53#4#01##"):
                await _feed(hass, raw)

            btn_52 = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-52-calibrate")
            btn_53 = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-53-calibrate")
            btn_52_i01 = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-52#4#01-calibrate")
            btn_53_i01 = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-53#4#01-calibrate")
            cal_all = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-calibrate-all-covers")
            assert btn_52 and btn_53 and btn_52_i01 and btn_53_i01 and cal_all

            # 1. Area 5 (*#2*5*10*10*50*001*0##), General (*#2*0*10*10*30*001*0##),
            # and Group #5 (*#2*#5*10*10*40*001*0##) update position as before, but keep
            # 52 and 53 timed with calibration buttons active and advanced_covers untouched.
            for scope_frame, expected_pos in (
                ("*#2*5*10*10*50*001*0##", 50),
                ("*#2*0*10*10*30*001*0##", 30),
                ("*#2*#5*10*10*40*001*0##", 40),
            ):
                await _feed(hass, scope_frame)
                for eid in ("cover.cover_52", "cover.cover_53"):
                    st = hass.states.get(eid)
                    assert st is not None
                    assert st.state == "open"
                    assert st.attributes.get("current_position") == expected_pos
                    assert st.attributes.get("travel_time") == 25
                    assert "min_position_delta" not in st.attributes
                assert CONF_ADVANCED_COVERS not in entry.options
                assert hass.states.get(btn_52).state != "unavailable"
                assert hass.states.get(btn_53).state != "unavailable"
                assert hass.states.get(cal_all).state != "unavailable"

            # 2. F422-routed scope Dimension 10 frames (*#2*5#4#01*10*...##, *#2*0#4#01*10*...##)
            # also leave 52#4#01 and 53#4#01 timed.
            for routed_scope, expected_pos in (
                ("*#2*5#4#01*10*10*50*001*0##", 50),
                ("*#2*0#4#01*10*10*30*001*0##", 30),
            ):
                await _feed(hass, routed_scope)
                for eid in ("cover.cover_52i01", "cover.cover_53i01"):
                    st = hass.states.get(eid)
                    assert st is not None
                    assert st.attributes.get("current_position") == expected_pos
                    assert st.attributes.get("travel_time") == 25
                    assert "min_position_delta" not in st.attributes
                assert CONF_ADVANCED_COVERS not in entry.options
                assert hass.states.get(btn_52_i01).state != "unavailable"
                assert hass.states.get(btn_53_i01).state != "unavailable"

            # 3. Point-to-point Dimension 10 on 52#4#01 and 52 promotes ONLY that cover,
            # leaving sibling 53#4#01 and 53 timed.
            await _feed(hass, "*#2*52#4#01*10*10*75*001*0##")
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"52#4#01": True}
            assert hass.states.get("cover.cover_52i01").attributes.get("min_position_delta") == 2
            assert hass.states.get("cover.cover_53i01").attributes.get("travel_time") == 25
            assert hass.states.get(btn_52_i01).state == "unavailable"
            assert hass.states.get(btn_53_i01).state != "unavailable"

            await _feed(hass, "*#2*52*10*10*80*001*0##")
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"52#4#01": True, "52": True}
            assert hass.states.get("cover.cover_52").attributes.get("min_position_delta") == 2
            assert hass.states.get("cover.cover_53").attributes.get("travel_time") == 25
            assert hass.states.get(btn_52).state == "unavailable"
            assert hass.states.get(btn_53).state != "unavailable"
            assert hass.states.get(cal_all).state != "unavailable"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_direct_unit_edges_and_calibration_lockout(hass: HomeAssistant) -> None:
    """Cover helper edge cases, _is_tilting promotion guard, calibrate_cover lock, and poll fallback."""
    gw = MagicMock()
    gw.mac = MAC
    gw.log_id = "[test]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()

    # 1. Helper predicates: _is_stored_advanced_cover, _message_has_calibrated_dim10, _can_auto_promote_advanced
    assert _is_stored_advanced_cover(None, "0715") is False
    assert _is_stored_advanced_cover(SimpleNamespace(options=None), "0715") is False
    assert _is_stored_advanced_cover(SimpleNamespace(options={CONF_ADVANCED_COVERS: []}), "0715") is False
    assert _is_stored_advanced_cover(SimpleNamespace(options={CONF_ADVANCED_COVERS: {"0715": False}}), "0715") is False
    assert _is_stored_advanced_cover(SimpleNamespace(options={CONF_ADVANCED_COVERS: {"0715": True}}), "0715") is True

    assert _message_has_calibrated_dim10(None) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(is_translation=True, current_position=50)) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(_family="REQUEST", current_position=50)) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(_message_type="STATUS_REQUEST", current_position=50)) is False
    assert _message_has_calibrated_dim10(OWNAutomationEvent.parse("*#2*0*10*10*30*001*0##")) is False
    assert _message_has_calibrated_dim10(OWNAutomationEvent.parse("*#2*5*10*10*50*001*0##")) is False
    assert _message_has_calibrated_dim10(OWNAutomationEvent.parse("*#2*#5*10*10*50*001*0##")) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(is_position_unknown=True, current_position=50)) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(current_position=True)) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(current_position=-1)) is False
    assert _message_has_calibrated_dim10(SimpleNamespace(current_position=101)) is False
    assert _message_has_calibrated_dim10(OWNAutomationEvent.parse("*#2*0715*10*10*42*000*0##")) is True

    assert _normalize_bus_interface(None) is None
    assert _normalize_bus_interface(True) is None
    assert _normalize_bus_interface("") is None
    assert _normalize_bus_interface("1") == "01"
    assert _normalize_bus_interface(1) == "01"
    assert _normalize_bus_interface("01") == "01"
    assert _normalize_bus_interface("A") == "A"

    assert _can_auto_promote_advanced({}, None, None) is True
    assert _can_auto_promote_advanced({CONF_ADVANCED_SHUTTER: False}, None, None) is False
    assert _can_auto_promote_advanced({CONF_STRATEGY: "timed"}, None, None) is False
    assert _can_auto_promote_advanced({CONF_TRAVEL_TIME: 25}, None, None) is False
    assert _can_auto_promote_advanced({CONF_DEVICE_MODEL: "F411/2"}, None, None) is False
    assert _can_auto_promote_advanced({}, {"down": 15.0, "up": 15.0}, None) is False
    assert _can_auto_promote_advanced({}, {"min_motor_pulse": 0.3}, None) is False
    assert _can_auto_promote_advanced({}, None, object()) is False

    # 2. _persist_advanced_cover and async_removed_from_registry: missing entry, no-op, and cleanup
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Unit Cover",
            entity_name=None,
            device_id="0715",
            who="2",
            where="0715",
            interface=None,
            advanced=False,
            allow_auto_advanced=True,
            manufacturer="BTicino",
            model="Shutter / Cover",
            gateway=gw,
        )
    cover.hass = hass
    cover.entity_id = "cover.unit_cover"
    cover.async_schedule_update_ha_state = MagicMock()
    cover.async_write_ha_state = MagicMock()

    # No config_entry string entry_id -> no-op return
    cover._persist_advanced_cover()
    await cover.async_removed_from_registry()

    # _is_own_point_message edges: missing/bool where, scope entity, legacy "2-" prefix, 1-digit interface
    assert cover._is_own_point_message(SimpleNamespace(where=None)) is False  # type: ignore[arg-type]
    assert cover._is_own_point_message(SimpleNamespace(where=True)) is False  # type: ignore[arg-type]
    assert cover._is_own_point_message(OWNAutomationEvent.parse("*#2*0716*10*10*50*001*0##")) is False
    assert cover._is_own_point_message(OWNAutomationEvent.parse("*#2*0715*10*10*50*001*0##")) is True
    cover._where = "2-12"
    cover._interface = "01"
    assert cover._is_own_point_message(OWNAutomationEvent.parse("*#2*12#4#1*10*10*50*001*0##")) is True
    assert cover._is_own_point_message(OWNAutomationEvent.parse("*#2*12*10*10*50*001*0##")) is False
    cover.scope = object()  # type: ignore[assignment]
    assert cover._is_own_point_message(OWNAutomationEvent.parse("*#2*12#4#01*10*10*50*001*0##")) is False
    cover.scope = None
    cover._where = "0715"
    cover._interface = None

    # Real MockConfigEntry -> writes once, second call short-circuits when stored[key] is True
    mock_entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC}, unique_id=MAC, entry_id="e-unit")
    mock_entry.add_to_hass(hass)
    gw.config_entry = mock_entry
    # Non-stored key on async_removed_from_registry -> no-op
    await cover.async_removed_from_registry()
    cover._persist_advanced_cover()
    assert mock_entry.options.get(CONF_ADVANCED_COVERS) == {"0715": True}
    with patch.object(hass.config_entries, "async_update_entry") as update_spy:
        cover._persist_advanced_cover()
        update_spy.assert_not_called()

    # 3. async_calibrate sets _allow_auto_advanced = False
    cover._allow_auto_advanced = True
    cover._is_tilting = False
    with patch.object(cover, "_calibration_run", AsyncMock(side_effect=[10.0, 12.0, 13.0])):
        await cover.async_calibrate()
    assert cover._allow_auto_advanced is False

    # 4. _promote_to_native_strategy preserves active tilt stop task when _is_tilting is True
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover2 = MyHOMECover(
            hass=hass,
            name="Unit Cover 2",
            entity_name=None,
            device_id="0716",
            who="2",
            where="0716",
            interface=None,
            advanced=False,
            allow_auto_advanced=True,
            manufacturer="BTicino",
            model="Shutter / Cover",
            gateway=gw,
        )
    cover2.hass = hass
    cover2.entity_id = "cover.unit_cover_2"
    cover2.async_schedule_update_ha_state = MagicMock()
    cover2._is_tilting = True
    sentinel_task = asyncio.create_task(asyncio.sleep(10))
    cover2._stop_task = sentinel_task
    cover2._promote_to_native_strategy()
    assert isinstance(cover2.strategy, NativePositionStrategy)
    assert not sentinel_task.cancelled()
    sentinel_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sentinel_task

    # 5. _async_poll_if_needed fallback when sweep_active=True but target_hass is None
    gw.wait_for_initial_discovery = AsyncMock()
    gw.sweep_active = True
    gw.initial_discovery_pending = False
    gw.device_health = MagicMock()
    cover2.hass = None  # type: ignore[assignment]
    cover2._hass = None
    cover2._poll_on_add = True
    cover2.async_on_remove = MagicMock()
    gw.send_status_request.reset_mock()
    await cover2.async_added_to_hass()
    gw.send_status_request.assert_awaited_once()


def _get_cover_entity(hass: HomeAssistant, entity_id: str) -> MyHOMECover:
    """Return the live MyHOMECover instance registered in Home Assistant."""
    entity = hass.data[DATA_INSTANCES]["cover"].get_entity(entity_id)
    assert isinstance(entity, MyHOMECover), f"Expected MyHOMECover for {entity_id}, got {entity!r}"
    return entity


async def _wait_for_sent(
    harness: MockGatewayHarness,
    expected: str,
    *,
    timeout: float = 2.0,
) -> None:
    """Poll until expected frame reaches harness.received_messages."""
    deadline = time.monotonic() + timeout
    while expected not in harness.received_messages:
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"Timed out waiting for {expected!r} in {harness.received_messages!r}"
            )
        await asyncio.sleep(0.02)


async def _drain_startup_polls(
    handler: MyHOMEGatewayHandler,
    harness: MockGatewayHarness,
    *,
    timeout: float = 2.0,
) -> None:
    """Drain queued startup _TIER_STATUS polls and wait for any in-flight poll to finish."""
    _drain_sent(handler, harness)
    deadline = time.monotonic() + timeout
    while handler.send_buffer.in_flight_status or handler.send_buffer.in_flight_targets:
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.02)
    _drain_sent(handler, harness)
    harness.received_messages.clear()


@pytest.mark.asyncio
async def test_fedem95_midmove_moving_dim10_promotes_and_hands_off_dim11(
    hass: HomeAssistant,
) -> None:
    """@fedem95 (#927): moving Dim 10 mid-run promotes, cancels _auto_stop, hands off Dim 11, and settles at target."""
    mock_restore_cache(
        hass,
        [State("cover.cover_0110", "open", {"current_position": 100})],
    )
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="cover",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0110",
        config_entry=entry,
        suggested_object_id="cover_0110",
    )
    registry.async_get_or_create(
        domain="button",
        platform=DOMAIN,
        unique_id=f"{MAC}-2-0110-calibrate",
        config_entry=entry,
        suggested_object_id="cover_0110_calibrate_travel_time",
    )

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            await _drain_startup_polls(handler, harness)
            await hass.async_block_till_done()

            # Startup *#2*0110## returned ACK-only on MH201; cover is still timed with restored 100% position.
            st_init = hass.states.get("cover.cover_0110")
            assert st_init is not None
            assert st_init.attributes.get("current_position") == 100
            assert st_init.attributes.get("travel_time") == 25
            assert "min_position_delta" not in st_init.attributes
            cal_btn_id = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-2-0110-calibrate")
            assert cal_btn_id is not None and hass.states.get(cal_btn_id).state != "unavailable"

            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0110", "position": 40},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*2*0110##")
            await hass.async_block_till_done()

            cover = _get_cover_entity(hass, "cover.cover_0110")
            assert "*2*2*0110##" in harness.received_messages
            assert cover._stop_task is not None and not cover._stop_task.done()
            assert hass.states.get("cover.cover_0110").state == "closing"
            harness.received_messages.clear()

            # MH201 / MH202 actuator emits moving calibrated Dim 10 + plain direction frame at motor start.
            await _feed(hass, "*#2*0110*10*12*100*001*0##")
            await _feed(hass, "*2*2*0110##")
            await _wait_for_sent(harness, "*#2*0110*#11#001*40##")
            await hass.async_block_till_done()

            # Promoted in-place, _stop_task cancelled, Dim 11 40% handed off (no timed *2*0*).
            assert cover._stop_task is None
            assert isinstance(cover.strategy, NativePositionStrategy)
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0110": True}
            assert hass.states.get(cal_btn_id).state == "unavailable"

            st_mid = hass.states.get("cover.cover_0110")
            assert st_mid is not None
            assert st_mid.state == "closing"
            assert st_mid.attributes.get("current_position") == 100
            assert st_mid.attributes.get("min_position_delta") == 2
            assert "travel_time" not in st_mid.attributes

            mid_sent = _drain_sent(handler, harness)
            assert mid_sent == ["*#2*0110*#11#001*40##"]
            assert "*2*0*0110##" not in mid_sent

            # Actuator reaches 40% and reports stationary calibrated Dim 10 + plain stop.
            harness.received_messages.clear()
            await _feed(hass, "*#2*0110*10*10*40*001*0##")
            await _feed(hass, "*2*0*0110##")
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()

            st_settled = hass.states.get("cover.cover_0110")
            assert st_settled is not None
            assert st_settled.state == "open"
            assert st_settled.attributes.get("current_position") == 40
            assert _drain_sent(handler, harness) == []
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_stationary_dim10_midmove_drops_queued_move_and_handles_pre_stop(
    hass: HomeAssistant,
) -> None:
    """Stationary Dim 10 during queued move (MH201 poll race) or gateway pre-STOP (F454) hands off Dim 11 cleanly."""
    mock_restore_cache(
        hass,
        [
            State("cover.cover_0110", "open", {"current_position": 100}),
            State("cover.cover_21", "open", {"current_position": 60}),
        ],
    )
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    for dev_id, obj_id in (("0110", "cover_0110"), ("21", "cover_21")):
        registry.async_get_or_create(
            domain="cover",
            platform=DOMAIN,
            unique_id=f"{MAC}-2-{dev_id}",
            config_entry=entry,
            suggested_object_id=obj_id,
        )

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            await _drain_startup_polls(handler, harness)
            await hass.async_block_till_done()

            # 1. Delayed MH201 ACK-only poll race: (2, "0110") is locked in-flight on a worker
            # when set_cover_position(40) queues *2*2*0110##; stationary *#2*0110*10*10*75*001*0##
            # arrives -> promotes, drops queued *2*2*0110## via is_cancel=True, and sends Dim 11 40%.
            handler.send_buffer._in_flight_targets.add((2, "0110"))
            try:
                await hass.services.async_call(
                    "cover",
                    "set_cover_position",
                    {"entity_id": "cover.cover_0110", "position": 40},
                    blocking=True,
                )
                assert handler.send_buffer.command_qsize == 1
                assert harness.received_messages == []

                await _feed(hass, "*#2*0110*10*10*75*001*0##")
                assert handler.send_buffer.command_qsize == 1
            finally:
                handler.send_buffer.release_target((2, "0110"))

            await _wait_for_sent(harness, "*#2*0110*#11#001*40##")
            await hass.async_block_till_done()

            sent_0110 = _drain_sent(handler, harness)
            assert sent_0110 == ["*#2*0110*#11#001*40##"]
            assert "*2*2*0110##" not in sent_0110
            assert "*2*0*0110##" not in sent_0110
            cover_0110 = _get_cover_entity(hass, "cover.cover_0110")
            assert cover_0110._echo_until is not None and cover_0110._echo_until > time.monotonic()

            # 2. F454 / H4890 pre-STOP sequence (discussion_770_f454_f401): *2*2*21## already written,
            # gateway emits *2*1000#0*21## + stationary *#2*21*10*10*47*001*15## before motor starts.
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_21", "position": 30},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*2*21##")
            await hass.async_block_till_done()
            assert "*2*2*21##" in harness.received_messages
            harness.received_messages.clear()

            await _feed(hass, "*2*1000#0*21##")
            await _feed(hass, "*#2*21*10*10*47*001*15##")
            await _wait_for_sent(harness, "*#2*21*#11#001*30##")
            await hass.async_block_till_done()

            sent_21 = _drain_sent(handler, harness)
            assert sent_21 == ["*#2*21*#11#001*30##"]
            cover_21 = _get_cover_entity(hass, "cover.cover_21")
            assert cover_21._echo_until is not None and cover_21._echo_until > time.monotonic()

            # Moving + arrival frames complete cleanly at 30%.
            await _feed(hass, "*#2*21*10*12*47*001*15##")
            await _feed(hass, "*#2*21*10*10*30*001*15##")
            st_21 = hass.states.get("cover.cover_21")
            assert st_21 is not None and st_21.attributes.get("current_position") == 30
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_stale_restored_position_wrong_way_reversal_and_already_at_target_guards(
    hass: HomeAssistant,
) -> None:
    """Stale restored curr_pos reverses via Dim 11 or stops via *2*0* when already at target."""
    mock_restore_cache(
        hass,
        [
            State("cover.cover_0110", "open", {"current_position": 20}),
            State("cover.cover_0111", "open", {"current_position": 20}),
            State("cover.cover_0112", "open", {"current_position": 80}),
            State("cover.cover_0113", "open", {"current_position": 20}),
            State("cover.cover_0114", "open", {"current_position": 20}),
            State("cover.cover_0115", "open", {"current_position": 20}),
        ],
    )
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    for dev_id in ("0110", "0111", "0112", "0113", "0114", "0115"):
        registry.async_get_or_create(
            domain="cover",
            platform=DOMAIN,
            unique_id=f"{MAC}-2-{dev_id}",
            config_entry=entry,
            suggested_object_id=f"cover_{dev_id}",
        )

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            await _drain_startup_polls(handler, harness)
            await hass.async_block_till_done()

            # 1. Wrong-way UP move: restored curr_pos=20, target=50 sends *2*1*0110## (UP),
            # actuator reports moving UP from real 80% (*#2*0110*10*11*80*001*0##) -> reverses via Dim 11 50%.
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0110", "position": 50},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*1*0110##")
            await hass.async_block_till_done()
            assert "*2*1*0110##" in harness.received_messages
            harness.received_messages.clear()

            await _feed(hass, "*#2*0110*10*11*80*001*0##")
            await _wait_for_sent(harness, "*#2*0110*#11#001*50##")
            await hass.async_block_till_done()
            assert _drain_sent(handler, harness) == ["*#2*0110*#11#001*50##"]

            # 2. Opposite-direction moving Dim 10 on promotion (was_opening + status=12 closing,
            # and was_closing + status=11 opening) preserves hand_off_gen (`if not promoted:`).
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0111", "position": 50},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*1*0111##")
            await hass.async_block_till_done()
            harness.received_messages.clear()
            await _feed(hass, "*#2*0111*10*12*80*001*0##")
            await _wait_for_sent(harness, "*#2*0111*#11#001*50##")
            await hass.async_block_till_done()
            assert _drain_sent(handler, harness) == ["*#2*0111*#11#001*50##"]

            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0112", "position": 50},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*2*0112##")
            await hass.async_block_till_done()
            harness.received_messages.clear()
            await _feed(hass, "*#2*0112*10*11*20*001*0##")
            await _wait_for_sent(harness, "*#2*0112*#11#001*50##")
            await hass.async_block_till_done()
            assert _drain_sent(handler, harness) == ["*#2*0112*#11#001*50##"]

            # 3. Already-at-target on moving status=11 at 50%: sends *2*0*0113## stop (not Dim 11).
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0113", "position": 50},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*1*0113##")
            await hass.async_block_till_done()
            assert "*2*1*0113##" in harness.received_messages
            harness.received_messages.clear()

            await _feed(hass, "*#2*0113*10*11*50*001*0##")
            await _wait_for_sent(harness, "*2*0*0113##")
            await hass.async_block_till_done()
            assert _drain_sent(handler, harness) == ["*2*0*0113##"]

            # 4. Already-at-target on stationary status=10 at 50% while *2*1*0114## is queued:
            # drops queued *2*1*0114## via is_cancel=True and sends *2*0*0114##.
            harness.received_messages.clear()
            handler.send_buffer._in_flight_targets.add((2, "0114"))
            try:
                await hass.services.async_call(
                    "cover",
                    "set_cover_position",
                    {"entity_id": "cover.cover_0114", "position": 50},
                    blocking=True,
                )
                assert handler.send_buffer.command_qsize == 1
                await _feed(hass, "*#2*0114*10*10*50*001*0##")
                assert handler.send_buffer.command_qsize == 1
            finally:
                handler.send_buffer.release_target((2, "0114"))

            await _wait_for_sent(harness, "*2*0*0114##")
            await hass.async_block_till_done()
            assert _drain_sent(handler, harness) == ["*2*0*0114##"]

            # 5. Moving status=11 at 51% (1% delta inside min_position_delta=2): widens to Dim 11 49%.
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0115", "position": 50},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*1*0115##")
            await hass.async_block_till_done()
            harness.received_messages.clear()

            await _feed(hass, "*#2*0115*10*11*51*001*0##")
            await _wait_for_sent(harness, "*#2*0115*#11#001*49##")
            await hass.async_block_till_done()
            assert _drain_sent(handler, harness) == ["*#2*0115*#11#001*49##"]
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_deadband_zero_and_hundred_quantization_routes_to_plain_open_close(
    hass: HomeAssistant,
) -> None:
    """Deadband-clamped 0% and 100% targets route to *2*2* / *2*1* (never Dim 11 0% / 100%)."""
    mock_restore_cache(
        hass,
        [
            State("cover.cover_0110", "closed", {"current_position": 0}),
            State("cover.cover_0111", "open", {"current_position": 100}),
        ],
    )
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    for dev_id in ("0110", "0111"):
        registry.async_get_or_create(
            domain="cover",
            platform=DOMAIN,
            unique_id=f"{MAC}-2-{dev_id}",
            config_entry=entry,
            suggested_object_id=f"cover_{dev_id}",
        )

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            await _drain_startup_polls(handler, harness)
            await hass.async_block_till_done()

            # 1. target=1 from restored 0% starts UP (*2*1*0110##); actuator reports position=2
            # -> quantize_target(2, 1) == 0 -> routes to *2*2*0110## (never *#2*0110*#11#001*0##).
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0110", "position": 1},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*1*0110##")
            await hass.async_block_till_done()
            assert "*2*1*0110##" in harness.received_messages
            harness.received_messages.clear()

            await _feed(hass, "*#2*0110*10*11*2*001*0##")
            await _wait_for_sent(harness, "*2*2*0110##")
            await hass.async_block_till_done()
            sent_low = _drain_sent(handler, harness)
            assert sent_low == ["*2*2*0110##"]
            assert "*#2*0110*#11#001*0##" not in sent_low

            # 2. target=99 from restored 100% starts DOWN (*2*2*0111##); actuator reports position=98
            # -> quantize_target(98, 99) == 100 -> routes to *2*1*0111## (never *#2*0111*#11#001*100##).
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": "cover.cover_0111", "position": 99},
                blocking=True,
            )
            await _wait_for_sent(harness, "*2*2*0111##")
            await hass.async_block_till_done()
            assert "*2*2*0111##" in harness.received_messages
            harness.received_messages.clear()

            await _feed(hass, "*#2*0111*10*12*98*001*0##")
            await _wait_for_sent(harness, "*2*1*0111##")
            await hass.async_block_till_done()
            sent_high = _drain_sent(handler, harness)
            assert sent_high == ["*2*1*0111##"]
            assert "*#2*0111*#11#001*100##" not in sent_high
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


def _create_unit_cover(
    hass: HomeAssistant,
    where: str,
    gw: MagicMock,
    *,
    pos: int | None = 100,
) -> MyHOMECover:
    """Create an isolated timed MyHOMECover with entity scaffolding mocked."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cov = MyHOMECover(
            hass=hass,
            name=f"Cover {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=False,
            allow_auto_advanced=True,
            manufacturer="BTicino",
            model="Shutter / Cover",
            gateway=gw,
        )
    cov.hass = hass
    cov.entity_id = f"cover.cover_{where}"
    cov.async_schedule_update_ha_state = MagicMock()
    cov.async_write_ha_state = MagicMock()
    if pos is not None:
        cov._attr_current_cover_position = pos
        cov._start_position = pos
        cov._attr_is_closed = pos == 0
    return cov


@pytest.mark.asyncio
async def test_midmove_promotion_dangling_write_full_runs_and_user_supersede(
    hass: HomeAssistant,
) -> None:
    """Verify dangling _track_write isolation, _async_move promotion guard, full runs, and user supersede."""
    loop = asyncio.get_running_loop()

    def _build_unit_cover(where: str, gw: MagicMock, *, pos: int | None = 100) -> MyHOMECover:
        return _create_unit_cover(hass, where, gw, pos=pos)

    gw = MagicMock()
    gw.mac = MAC
    gw.log_id = "[test]"
    gw.config_entry = None
    gw.calibration_hub = None

    # 1. Dangling _track_write callbacks resolving AFTER mid-move promotion must no-op:
    # 1a) open/close write timestamp resolution does not re-anchor timed run or re-add timed attrs
    fut_move: asyncio.Future[float] = loop.create_future()
    gw.send = AsyncMock(side_effect=[fut_move, None])
    cov_a = _build_unit_cover("0720", gw, pos=100)
    await cov_a.async_set_cover_position(position=40)
    cov_a.handle_event(OWNAutomationEvent.parse("*#2*0720*10*12*100*001*0##"))
    await hass.async_block_till_done()
    assert cov_a._advanced is True
    assert "travel_time" not in cov_a.extra_state_attributes
    cov_a._motion_started_at = None
    cov_a._attr_extra_state_attributes.pop("motion_started_at", None)

    fut_move.set_result(time.monotonic())
    await hass.async_block_till_done()
    assert cov_a._move_start_time is None
    assert cov_a._live_refresh_unsub is None
    assert "travel_time" not in cov_a.extra_state_attributes
    assert cov_a.extra_state_attributes.get("motion_started_at") is None

    # 1b) stop write timestamp resolution does not call _freeze_position or clear motion prematurely
    fut_stop: asyncio.Future[float] = loop.create_future()
    gw.send = AsyncMock(return_value=fut_stop)
    cov_b = _build_unit_cover("0721", gw, pos=50)
    cov_b._attr_is_opening = True
    await cov_b.async_stop_cover()
    cov_b.handle_event(OWNAutomationEvent.parse("*#2*0721*10*11*50*001*0##"))
    assert cov_b._advanced is True
    assert cov_b.is_opening is True

    fut_stop.set_result(time.monotonic())
    await hass.async_block_till_done()
    assert cov_b.is_opening is True

    # 1c) cancelled/failed move write after promotion does not call _abort_motion
    fut_cancel: asyncio.Future[float] = loop.create_future()
    gw.send = AsyncMock(return_value=fut_cancel)
    cov_c = _build_unit_cover("0722", gw, pos=100)
    await cov_c.async_open_cover()
    cov_c.handle_event(OWNAutomationEvent.parse("*#2*0722*10*12*80*001*0##"))
    fut_cancel.cancel()
    await hass.async_block_till_done()
    assert cov_c.is_closing is True
    assert cov_c._start_position is None

    # 2. Promotion firing INSIDE `await self._async_move(direction)` (`if self._is_promoted_native(): return`)
    cov_inline = _build_unit_cover("0723", gw, pos=100)
    sent_inline: list[tuple[str, bool]] = []

    async def _send_and_promote_inline(cmd: object, *, is_cancel: bool = False) -> None:
        sent_inline.append((str(cmd), is_cancel))
        if str(cmd) == "*2*2*0723##":
            cov_inline.handle_event(OWNAutomationEvent.parse("*#2*0723*10*12*100*001*0##"))

    gw.send = AsyncMock(side_effect=_send_and_promote_inline)
    await cov_inline.async_set_cover_position(position=40)
    assert cov_inline._advanced is True
    assert cov_inline._stop_task is None
    await hass.async_block_till_done()
    assert sent_inline == [
        ("*2*2*0723##", False),
        ("*#2*0723*#11#001*40##", True),
    ]

    # 3. Full open_cover / close_cover / 0% / 100% promotion sends NO extra Dim 11 or *2*0*
    for idx, action in enumerate(("open", "close", "pos100", "pos0")):
        where = f"073{idx}"
        cov_full = _build_unit_cover(where, gw, pos=50)
        gw.send = AsyncMock(return_value=None)
        if action == "open":
            await cov_full.async_open_cover()
        elif action == "close":
            await cov_full.async_close_cover()
        elif action == "pos100":
            await cov_full.async_set_cover_position(position=100)
        else:
            await cov_full.async_set_cover_position(position=0)
        assert cov_full._stop_task is not None
        gw.send.reset_mock()

        cov_full.handle_event(OWNAutomationEvent.parse(f"*#2*{where}*10*11*50*001*0##"))
        await hass.async_block_till_done()
        assert cov_full._advanced is True
        assert cov_full._stop_task is None
        gw.send.assert_not_awaited()

    # 4. User commands issued right after promotion (before _handoff_timed_target runs) supersede hand-off
    for idx, user_cmd in enumerate(("stop", "open", "retarget", "tilting_stop")):
        where = f"074{idx}"
        cov_sup = _build_unit_cover(where, gw, pos=100)
        sent_sup: list[str] = []

        async def _record_send(cmd: object, *, is_cancel: bool = False, _log: list[str] = sent_sup) -> None:
            _log.append(str(cmd))

        gw.send = AsyncMock(side_effect=_record_send)
        await cov_sup.async_set_cover_position(position=40)
        sent_sup.clear()

        # Synchronous handle_event schedules _handoff_timed_target without running it yet.
        cov_sup.handle_event(OWNAutomationEvent.parse(f"*#2*{where}*10*12*100*001*0##"))
        if user_cmd == "stop":
            await cov_sup.async_stop_cover()
            await hass.async_block_till_done()
            assert sent_sup == [f"*2*0*{where}##"]
        elif user_cmd == "open":
            await cov_sup.async_open_cover()
            await hass.async_block_till_done()
            assert sent_sup == [f"*2*1*{where}##"]
        elif user_cmd == "retarget":
            await cov_sup.async_set_cover_position(position=70)
            await hass.async_block_till_done()
            assert sent_sup == [f"*#2*{where}*#11#001*70##"]
        else:
            # Also cover _begin_stop when _is_tilting is True on a promoted cover
            cov_sup._is_tilting = True
            await cov_sup.async_stop_cover()
            cov_sup._is_tilting = False
            await hass.async_block_till_done()
            assert f"*2*0*{where}##" in sent_sup

    # 5. Timed lifecycle endings (_abort_motion, _finish_end_of_run_settle, _auto_stop) clear
    # _timed_target_position so later promotion never fires a stale Dim 11 hand-off, and
    # already-promoted direction reversals still increment _run_generation (`if not promoted:`).
    fut_abort: asyncio.Future[float] = loop.create_future()
    gw.send = AsyncMock(return_value=fut_abort)
    cov_abort = _build_unit_cover("0750", gw, pos=100)
    await cov_abort.async_set_cover_position(position=40)
    assert cov_abort._timed_target_position == 40
    cov_abort._abort_motion()
    assert cov_abort._timed_target_position is None
    gw.send.reset_mock()
    cov_abort.handle_event(OWNAutomationEvent.parse("*#2*0750*10*10*80*001*0##"))
    await hass.async_block_till_done()
    gw.send.assert_not_awaited()

    cov_end = _build_unit_cover("0751", gw, pos=50)
    cov_end._timed_target_position = 40
    cov_end._finish_end_of_run_settle(True)
    assert cov_end._timed_target_position is None
    gw.send.reset_mock()
    cov_end.handle_event(OWNAutomationEvent.parse("*#2*0751*10*10*100*001*0##"))
    await hass.async_block_till_done()
    gw.send.assert_not_awaited()

    fut_past: asyncio.Future[float] = loop.create_future()
    fut_past.set_result(time.monotonic() - 100.0)
    gw.send = AsyncMock(side_effect=[fut_past, None])
    cov_autostop = _build_unit_cover("0752", gw, pos=100)
    await cov_autostop.async_set_cover_position(position=90)
    assert cov_autostop._stop_task is not None
    await asyncio.wait_for(cov_autostop._stop_task, timeout=2.0)
    assert cov_autostop._timed_target_position is None
    gw.send.reset_mock()
    cov_autostop.handle_event(OWNAutomationEvent.parse("*#2*0752*10*10*90*001*0##"))
    await hass.async_block_till_done()
    gw.send.assert_not_awaited()

    gen_before = cov_a._run_generation
    cov_a.handle_event(OWNAutomationEvent.parse("*2*2*0720##"))
    cov_a.handle_event(OWNAutomationEvent.parse("*2*1*0720##"))
    assert cov_a._run_generation == gen_before + 1
    cov_a.handle_event(OWNAutomationEvent.parse("*2*2*0720##"))
    assert cov_a._run_generation == gen_before + 2


@pytest.mark.asyncio
async def test_queued_calibration_promoted_while_waiting_on_lock_aborts_cleanly(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued async_calibrate promoted while waiting on calibration_lock aborts with cover_reports_position."""
    monkeypatch.setattr("custom_components.myhome.cover.CALIBRATION_SETTLE", 0.0)
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    registry = er.async_get(hass)

    cal_events: list[dict] = []
    unsub_events = hass.bus.async_listen(
        f"{DOMAIN}_cover_calibration",
        callback(lambda ev: cal_events.append(dict(ev.data))),
    )

    proceed_0110 = asyncio.Event()
    entered_0110 = asyncio.Event()
    swept_covers: list[tuple[str, str]] = []

    async def _controlled_calibration_run(self: MyHOMECover, direction: str) -> float:
        swept_covers.append((self._full_where, direction))
        if self._full_where == "0110" and not entered_0110.is_set():
            entered_0110.set()
            await proceed_0110.wait()
        return 14.0 if direction == "close" else 15.0

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
            patch.object(MyHOMECover, "_calibration_run", _controlled_calibration_run),
        ):
            handler = await _setup_entry(hass, entry)
            await handler.wait_for_initial_discovery(timeout=5.0)
            await _drain_startup_polls(handler, harness)

            for raw in ("*2*0*0110##", "*2*0*0111##", "*2*0*0112##"):
                await _feed(hass, raw)

            cover_0110 = _get_cover_entity(hass, "cover.cover_0110")
            cover_0111 = _get_cover_entity(hass, "cover.cover_0111")
            cover_0112 = _get_cover_entity(hass, "cover.cover_0112")
            cal_all_id = registry.async_get_entity_id("button", DOMAIN, f"{MAC}-calibrate-all-covers")
            assert cal_all_id is not None

            # 1. Concurrent calibrate_cover calls:
            # 0110 acquires lock first, then 0111 queues behind it; 0111 promotes via Dim 10 while waiting.
            task_0110 = asyncio.create_task(
                hass.services.async_call(
                    DOMAIN,
                    "calibrate_cover",
                    {"entity_id": "cover.cover_0110"},
                    blocking=True,
                )
            )
            await asyncio.wait_for(entered_0110.wait(), timeout=5.0)

            task_0111 = asyncio.create_task(
                hass.services.async_call(
                    DOMAIN,
                    "calibrate_cover",
                    {"entity_id": "cover.cover_0111"},
                    blocking=True,
                )
            )
            for _ in range(20):
                if cover_0111 in cover_0110.calibration_hub.queued_covers:
                    break
                await asyncio.sleep(0.01)
            assert cover_0111 in cover_0110.calibration_hub.queued_covers

            # Promote 0111 while it sits queued on calibration_hub.lock (do NOT call _feed / async_block_till_done
            # while task_0110 is parked on proceed_0110.wait()).
            msg_0111 = OWNMessage.parse("*#2*0111*10*10*60*001*0##")
            assert msg_0111 is not None
            async_dispatcher_send(hass, f"myhome_message_{MAC}", msg_0111)
            await asyncio.sleep(0)
            assert cover_0111._advanced is True
            assert entry.options.get(CONF_ADVANCED_COVERS) == {"0111": True}

            proceed_0110.set()
            await asyncio.wait_for(task_0110, timeout=5.0)
            with pytest.raises(HomeAssistantError) as batch_exc:
                await asyncio.wait_for(task_0111, timeout=5.0)
            assert batch_exc.value.translation_key == "cover_reports_position"
            await hass.async_block_till_done()

            # 0110 completed and saved travel times; 0111 NEVER swept its motor and NEVER wrote travel times.
            assert [w for w, _ in swept_covers] == ["0110", "0110", "0110"]
            assert set(cover_0110._profile_store.data["native_fallbacks"]) == {"0110"}
            assert cover_0110.calibration_hub.queued_covers == set()
            assert any(
                e.get("entity_id") == "cover.cover_0111" and e.get("phase") == "failed"
                for e in cal_events
            )

            # 2. Queued async_calibrate on 0112 via CalibrateAllCoversButtonEntity,
            # also verifying stale _calibration_interrupted is cleared and pre-lock check raises cleanly.
            swept_covers.clear()
            async with cover_0112.calibration_hub.lock:
                await hass.services.async_call(
                    "button",
                    "press",
                    {"entity_id": cal_all_id},
                    blocking=True,
                )
                for _ in range(20):
                    if cover_0112 in cover_0112.calibration_hub.queued_covers:
                        break
                    await asyncio.sleep(0.01)
                assert cover_0112 in cover_0112.calibration_hub.queued_covers
                cover_0112._calibration_interrupted = "stale_interrupt"
                msg_0112 = OWNMessage.parse("*#2*0112*10*10*45*001*0##")
                assert msg_0112 is not None
                async_dispatcher_send(hass, f"myhome_message_{MAC}", msg_0112)
                await asyncio.sleep(0)

            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert "0112" not in [w for w, _ in swept_covers]
            assert [w for w, _ in swept_covers] == ["0110", "0110", "0110"]
            assert "0112" not in entry.options.get(CONF_COVER_TRAVEL_TIMES, {})
            assert cover_0112._calibration_interrupted is None
            assert cover_0112.calibration_hub.queued_covers == set()
            assert any(
                e.get("entity_id") == "cover.cover_0112" and e.get("phase") == "failed"
                for e in cal_events
            )

            # Calling calibrate_cover on an already-promoted cover fails immediately at entry.
            with pytest.raises(HomeAssistantError) as pre_lock_exc:
                await hass.services.async_call(
                    DOMAIN,
                    "calibrate_cover",
                    {"entity_id": "cover.cover_0112"},
                    blocking=True,
                )
            assert pre_lock_exc.value.translation_key == "cover_reports_position"
    finally:
        proceed_0110.set()
        unsub_events()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_midmove_promotion_handoff_failure_and_delivery_paths(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify Stage 1 enqueue exceptions, Stage 2 written future outcomes, and shutdown/supersede guards."""
    loop = asyncio.get_running_loop()

    def _make_gw(*, is_shutdown: bool = False) -> MagicMock:
        gw = MagicMock()
        gw.mac = MAC
        gw.log_id = "[test]"
        gw.config_entry = None
        gw.calibration_hub = None
        gw.send_buffer = FrameScheduler()
        if is_shutdown:
            gw.send_buffer.shutdown()
        return gw

    def _relevant_logs() -> list[logging.LogRecord]:
        return [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.name.startswith(("custom_components.myhome", "asyncio"))
        ]

    # 1a. Stage 1: synchronous send() raising RuntimeError while not shut down
    # -> caught cleanly, _echo_until cleared, warning logged ("failed").
    gw_err = _make_gw(is_shutdown=False)
    gw_err.send = AsyncMock(side_effect=[None, RuntimeError("enqueue failed")])
    cov_err = _create_unit_cover(hass, "0760", gw_err, pos=100)
    await cov_err.async_set_cover_position(position=40)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cov_err.handle_event(OWNAutomationEvent.parse("*#2*0760*10*12*100*001*0##"))
        await hass.async_block_till_done()
    assert cov_err._echo_until is None
    err_logs = _relevant_logs()
    assert len(err_logs) == 1
    assert err_logs[0].levelno == logging.WARNING
    assert "Mid-move Dimension 10 promotion hand-off" in err_logs[0].message
    assert "failed" in err_logs[0].message
    assert "*#2*0760*#11#001*40##" in err_logs[0].message
    assert "enqueue failed" in err_logs[0].message

    # 1b. Stage 1 with arm_echo=False (already-at-target *2*0* stop_shutter) raising RuntimeError.
    gw_stop_err = _make_gw(is_shutdown=False)
    gw_stop_err.send = AsyncMock(side_effect=[None, RuntimeError("stop enqueue failed")])
    cov_stop_err = _create_unit_cover(hass, "0761", gw_stop_err, pos=100)
    await cov_stop_err.async_set_cover_position(position=40)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cov_stop_err.handle_event(OWNAutomationEvent.parse("*#2*0761*10*11*40*001*0##"))
        await hass.async_block_till_done()
    assert cov_stop_err._echo_until is None
    stop_err_logs = _relevant_logs()
    assert len(stop_err_logs) == 1
    assert "failed" in stop_err_logs[0].message
    assert "*2*0*0761##" in stop_err_logs[0].message

    # 1c. Stage 1 superseded while awaiting send() before raising -> newer _echo_until preserved.
    entered_send = asyncio.Event()
    release_send = asyncio.Event()

    async def _slow_failing_send(cmd: object, *, is_cancel: bool = False) -> None:
        if str(cmd) == "*#2*0762*#11#001*40##":
            entered_send.set()
            await release_send.wait()
            raise RuntimeError("delayed enqueue failed")

    gw_sup_err = _make_gw(is_shutdown=False)
    gw_sup_err.send = AsyncMock(side_effect=_slow_failing_send)
    cov_sup_err = _create_unit_cover(hass, "0762", gw_sup_err, pos=100)
    await cov_sup_err.async_set_cover_position(position=40)
    cov_sup_err.handle_event(OWNAutomationEvent.parse("*#2*0762*10*12*100*001*0##"))
    await asyncio.wait_for(entered_send.wait(), timeout=2.0)

    await cov_sup_err.async_set_cover_position(position=70)
    newer_echo_stage1 = cov_sup_err._echo_until
    assert newer_echo_stage1 is not None
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        release_send.set()
        await hass.async_block_till_done()
    assert cov_sup_err._echo_until == newer_echo_stage1
    assert any("delayed enqueue failed" in r.message for r in _relevant_logs())

    # 2. Stage 1: synchronous send() raising when send_buffer.is_shutdown = True
    # -> _echo_until cleared, NO warning logged.
    gw_shut_err = _make_gw(is_shutdown=True)
    gw_shut_err.send = AsyncMock(side_effect=[None, RuntimeError("queue shut down")])
    cov_shut_err = _create_unit_cover(hass, "0763", gw_shut_err, pos=100)
    await cov_shut_err.async_set_cover_position(position=40)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cov_shut_err.handle_event(OWNAutomationEvent.parse("*#2*0763*10*12*100*001*0##"))
        await hass.async_block_till_done()
    assert cov_shut_err._echo_until is None
    assert _relevant_logs() == []

    # 3a. Stage 2: written future cancelled while unsuperseded & _is_shutdown = False
    # -> _echo_until cleared, warning logged ("did not reach the bus").
    gw_fut_cancel = _make_gw(is_shutdown=False)
    fut_cancel: asyncio.Future[float] = loop.create_future()
    gw_fut_cancel.send = AsyncMock(side_effect=[None, fut_cancel])
    cov_fut_cancel = _create_unit_cover(hass, "0764", gw_fut_cancel, pos=100)
    await cov_fut_cancel.async_set_cover_position(position=40)
    cov_fut_cancel.handle_event(OWNAutomationEvent.parse("*#2*0764*10*12*100*001*0##"))
    await hass.async_block_till_done()
    assert cov_fut_cancel._echo_until is not None
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        fut_cancel.cancel()
        await hass.async_block_till_done()
    assert cov_fut_cancel._echo_until is None
    fut_cancel_logs = _relevant_logs()
    assert len(fut_cancel_logs) == 1
    assert "did not reach the bus" in fut_cancel_logs[0].message
    assert "*#2*0764*#11#001*40##" in fut_cancel_logs[0].message

    # 3b. Stage 2: written future failed via set_exception while unsuperseded & send_buffer=None
    # -> _echo_until cleared, warning logged ("did not reach the bus"), exception retrieved cleanly.
    gw_fut_exc = _make_gw(is_shutdown=False)
    gw_fut_exc.send_buffer = None
    fut_exc: asyncio.Future[float] = loop.create_future()
    gw_fut_exc.send = AsyncMock(side_effect=[None, fut_exc])
    cov_fut_exc = _create_unit_cover(hass, "0765", gw_fut_exc, pos=100)
    await cov_fut_exc.async_set_cover_position(position=40)
    cov_fut_exc.handle_event(OWNAutomationEvent.parse("*#2*0765*10*12*100*001*0##"))
    await hass.async_block_till_done()
    assert cov_fut_exc._echo_until is not None
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        fut_exc.set_exception(RuntimeError("bus write failed"))
        await hass.async_block_till_done()
    assert cov_fut_exc._echo_until is None
    fut_exc_logs = _relevant_logs()
    assert len(fut_exc_logs) == 1
    assert fut_exc_logs[0].levelno == logging.WARNING
    assert "did not reach the bus" in fut_exc_logs[0].message

    # 3c. Stage 2 with arm_echo=False (stop_shutter) cancelled -> warning logged without touching echo.
    gw_stop_fut = _make_gw(is_shutdown=False)
    fut_stop_cancel: asyncio.Future[float] = loop.create_future()
    gw_stop_fut.send = AsyncMock(side_effect=[None, fut_stop_cancel])
    cov_stop_fut = _create_unit_cover(hass, "0766", gw_stop_fut, pos=100)
    await cov_stop_fut.async_set_cover_position(position=40)
    cov_stop_fut.handle_event(OWNAutomationEvent.parse("*#2*0766*10*11*40*001*0##"))
    await hass.async_block_till_done()
    assert cov_stop_fut._echo_until is None
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        fut_stop_cancel.cancel()
        await hass.async_block_till_done()
    assert cov_stop_fut._echo_until is None
    stop_fut_logs = _relevant_logs()
    assert len(stop_fut_logs) == 1
    assert "did not reach the bus" in stop_fut_logs[0].message
    assert "*2*0*0766##" in stop_fut_logs[0].message

    # 3d. Stage 2: written future succeeds via set_result -> _echo_until preserved, no warning.
    gw_fut_ok = _make_gw(is_shutdown=False)
    fut_ok: asyncio.Future[float] = loop.create_future()
    gw_fut_ok.send = AsyncMock(side_effect=[None, fut_ok])
    cov_fut_ok = _create_unit_cover(hass, "0767", gw_fut_ok, pos=100)
    await cov_fut_ok.async_set_cover_position(position=40)
    cov_fut_ok.handle_event(OWNAutomationEvent.parse("*#2*0767*10*12*100*001*0##"))
    await hass.async_block_till_done()
    assert cov_fut_ok._echo_until is not None
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        fut_ok.set_result(time.monotonic())
        await hass.async_block_till_done()
    assert cov_fut_ok._echo_until is not None
    assert _relevant_logs() == []

    # 4a. Stage 2: written future cancelled when superseded by a newer user command
    # -> NO warning logged, and newer _echo_until preserved.
    gw_fut_sup = _make_gw(is_shutdown=False)
    fut_old: asyncio.Future[float] = loop.create_future()
    fut_new: asyncio.Future[float] = loop.create_future()
    gw_fut_sup.send = AsyncMock(side_effect=[None, fut_old, fut_new])
    cov_fut_sup = _create_unit_cover(hass, "0768", gw_fut_sup, pos=100)
    await cov_fut_sup.async_set_cover_position(position=40)
    cov_fut_sup.handle_event(OWNAutomationEvent.parse("*#2*0768*10*12*100*001*0##"))
    await hass.async_block_till_done()

    await cov_fut_sup.async_set_cover_position(position=70)
    newer_echo = cov_fut_sup._echo_until
    assert newer_echo is not None
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        fut_old.cancel()
        await hass.async_block_till_done()
    assert cov_fut_sup._echo_until == newer_echo
    assert _relevant_logs() == []

    # 4b. Stage 2: written future cancelled when send_buffer.is_shutdown = True
    # -> _echo_until cleared, NO warning logged.
    gw_fut_shut = _make_gw(is_shutdown=False)
    fut_shut: asyncio.Future[float] = loop.create_future()
    gw_fut_shut.send = AsyncMock(side_effect=[None, fut_shut])
    cov_fut_shut = _create_unit_cover(hass, "0769", gw_fut_shut, pos=100)
    await cov_fut_shut.async_set_cover_position(position=40)
    cov_fut_shut.handle_event(OWNAutomationEvent.parse("*#2*0769*10*12*100*001*0##"))
    await hass.async_block_till_done()
    assert cov_fut_shut._echo_until is not None
    gw_fut_shut.send_buffer.shutdown()
    assert gw_fut_shut.send_buffer.is_shutdown is True
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        fut_shut.cancel()
        await hass.async_block_till_done()
    assert cov_fut_shut._echo_until is None
    assert _relevant_logs() == []

    # 5. Non-Future return from send() (e.g. AsyncMock returning None) -> completes cleanly.
    gw_none = _make_gw(is_shutdown=False)
    gw_none.send = AsyncMock(return_value=None)
    cov_none = _create_unit_cover(hass, "0770", gw_none, pos=100)
    await cov_none.async_set_cover_position(position=40)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        cov_none.handle_event(OWNAutomationEvent.parse("*#2*0770*10*12*100*001*0##"))
        await hass.async_block_till_done()
    assert cov_none._advanced is True
    assert cov_none._echo_until is not None
    assert gw_none.send.await_count == 2
    assert str(gw_none.send.await_args_list[1].args[0]) == "*#2*0770*#11#001*40##"
    assert gw_none.send.await_args_list[1].kwargs == {"is_cancel": True}
    assert _relevant_logs() == []


