"""Tests for automatic detection and promotion of WHO=2 Dimension 10 position-reporting shutters (#926)."""

from __future__ import annotations

import asyncio
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
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNAutomationEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
from custom_components.myhome.gateway import MyHOMEGatewayHandler
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
