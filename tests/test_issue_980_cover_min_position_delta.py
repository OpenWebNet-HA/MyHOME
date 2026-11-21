"""Tests for Issue #980: native cover min_position_delta UI number and YAML migration cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    PERCENTAGE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_ADVANCED_COVERS,
    CONF_ADVANCED_SHUTTER,
    CONF_COVER_MIN_POSITION_DELTAS,
    CONF_DEVICE_MODEL,
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_EXPLICIT_KINEMATICS,
    CONF_EXPLICIT_TRAVEL_TIME,
    CONF_FILE_PATH,
    CONF_FIRMWARE,
    CONF_IGNORED_ADDRESSES,
    CONF_MANUFACTURER,
    CONF_MIN_MOTOR_PULSE,
    CONF_MIN_POSITION_DELTA,
    CONF_SLAT_TILT,
    CONF_SLAT_TIME,
    CONF_SOURCE_TUNER,
    CONF_STRATEGY,
    CONF_TRAVEL_TIME,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.cover import (
    MyHOMECover,
    is_native_point_cover,
    resolve_cover_min_position_delta,
)
from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics
from custom_components.myhome.discovery import prune_entity, prune_orphaned_companions
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.number import (
    MyHOMECoverMinPositionDeltaNumber,
)
from custom_components.myhome.number import (
    async_setup_entry as async_setup_numbers,
)
from custom_components.myhome.validate import cover_schema
from tests.mock_gateway_harness import MockGatewayHarness
from tests.test_multi_gateway import _create_mock_gateway

MAC = "00:03:50:00:09:80"


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
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:MyHomeServer1:1",
            CONF_FRIENDLY_NAME: "MyHomeServer1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.87.13",
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


def test_cover_schema_clean_native_migration() -> None:
    """Legacy 0.9.x `advanced: true` omits contradictory `advanced_shutter: false` and unused timed defaults (#980)."""
    # 1. Exact reporter snippet from Issue #980
    res = cover_schema(
        {
            "study_room_shutter": {
                CONF_WHERE: "31",
                CONF_NAME: "Frangisole porta studio",
                CONF_ADVANCED_SHUTTER: True,
                CONF_MANUFACTURER: "Legrand",
                CONF_DEVICE_MODEL: "K4672M2S",
            }
        }
    )
    cfg = res["2-31"]
    assert cfg[CONF_ADVANCED_SHUTTER] is True
    assert "advanced_shutter" not in cfg
    assert CONF_TRAVEL_TIME not in cfg
    assert CONF_MIN_MOTOR_PULSE not in cfg
    assert CONF_SLAT_TIME not in cfg
    assert cfg[CONF_EXPLICIT_KINEMATICS] is True
    assert cfg[CONF_EXPLICIT_TRAVEL_TIME] is False

    # 2. strategy: native produces the same clean dict
    res_native = cover_schema(
        {
            "native_shutter": {
                CONF_WHERE: "32",
                CONF_NAME: "Native Shutter",
                CONF_STRATEGY: "native",
            }
        }
    )
    cfg_native = res_native["2-32"]
    assert cfg_native[CONF_ADVANCED_SHUTTER] is True
    assert "advanced_shutter" not in cfg_native
    assert CONF_TRAVEL_TIME not in cfg_native
    assert CONF_MIN_MOTOR_PULSE not in cfg_native
    assert CONF_SLAT_TIME not in cfg_native

    # 3. Alias advanced_shutter: true still populates both keys without unused timed defaults
    res_alias = cover_schema(
        {
            "alias_shutter": {
                CONF_WHERE: "33",
                CONF_NAME: "Alias Shutter",
                "advanced_shutter": True,
            }
        }
    )
    cfg_alias = res_alias["2-33"]
    assert cfg_alias[CONF_ADVANCED_SHUTTER] is True
    assert cfg_alias["advanced_shutter"] is True
    assert CONF_TRAVEL_TIME not in cfg_alias
    assert CONF_MIN_MOTOR_PULSE not in cfg_alias
    assert CONF_SLAT_TIME not in cfg_alias

    # 4. Native cover with slat_tilt: true preserves slat_time default and min_motor_pulse
    res_venetian_native = cover_schema(
        {
            "native_blind": {
                CONF_WHERE: "34",
                CONF_NAME: "Native Venetian",
                CONF_ADVANCED_SHUTTER: True,
                CONF_SLAT_TILT: True,
            }
        }
    )
    cfg_vn = res_venetian_native["2-34"]
    assert cfg_vn[CONF_ADVANCED_SHUTTER] is True
    assert CONF_TRAVEL_TIME not in cfg_vn
    assert cfg_vn[CONF_MIN_MOTOR_PULSE] == 0.4
    assert cfg_vn[CONF_SLAT_TIME] == 2.0

    # 5. Native cover with explicit travel_time and non-default min_motor_pulse keeps them
    res_explicit = cover_schema(
        {
            "explicit_native": {
                CONF_WHERE: "35",
                CONF_NAME: "Explicit Native",
                CONF_ADVANCED_SHUTTER: True,
                CONF_TRAVEL_TIME: 40,
                CONF_MIN_MOTOR_PULSE: 0.2,
            }
        }
    )
    cfg_exp = res_explicit["2-35"]
    assert cfg_exp[CONF_TRAVEL_TIME] == 40
    assert cfg_exp[CONF_MIN_MOTOR_PULSE] == 0.2
    assert CONF_SLAT_TIME not in cfg_exp

    # 6. Timed cover keeps travel_time, min_motor_pulse, and slat_time defaults
    res_timed = cover_schema(
        {
            "timed_shutter": {
                CONF_WHERE: "36",
                CONF_NAME: "Timed Shutter",
            }
        }
    )
    cfg_timed = res_timed["2-36"]
    assert cfg_timed[CONF_ADVANCED_SHUTTER] is False
    assert "advanced_shutter" not in cfg_timed
    assert cfg_timed[CONF_TRAVEL_TIME] == 25
    assert cfg_timed[CONF_MIN_MOTOR_PULSE] == 0.4
    assert cfg_timed[CONF_SLAT_TIME] == 2.0


@pytest.mark.asyncio
async def test_diagnostics_strips_stale_contradictory_advanced_shutter_false(
    hass: HomeAssistant,
) -> None:
    """Pre-2.0.1 cached migrated_yaml_platforms drops contradictory advanced_shutter: false in diagnostics."""
    cover_cfg = {
        "where": "31",
        "who": "2",
        "name": "Frangisole porta studio",
        "advanced": True,
        "advanced_shutter": False,
        "model": "K4672M2S",
    }
    raw_options = {
        "migrated_yaml_platforms": {
            "cover": {
                "2-31": cover_cfg,
            }
        }
    }
    mock_entry = MagicMock()
    mock_entry.entry_id = "diag_issue_980"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "MHS1"
    mock_entry.data = {CONF_MAC: MAC, CONF_NAME: "MyHomeServer1"}
    mock_entry.options = raw_options
    hass.data[DOMAIN] = {}

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)
    cov_diag = diag["config_entry"]["options"]["migrated_yaml_platforms"]["cover"]["2-31"]
    assert cov_diag["advanced"] is True
    assert "advanced_shutter" not in cov_diag
    assert cov_diag["name"] == "**REDACTED**"
    # Original config entry options remain unmutated
    assert cover_cfg["advanced_shutter"] is False


@pytest.mark.asyncio
async def test_native_cover_min_position_delta_number_end_to_end(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """UI Minimum position step number controls 1% steps live, persists across reload, and overrides YAML (#980)."""
    yaml_path = tmp_path / "myhome.yaml"
    yaml_path.write_text(
        f"""
{MAC}:
  cover:
    study_room_shutter:
      where: '31'
      name: Frangisole porta studio
      advanced: True
      manufacturer: Legrand
      model: K4672M2S
    living_shutter:
      where: '37'
      name: Living Native
      advanced: True
      min_position_delta: 4
    timed_shutter:
      where: '32'
      name: Timed Shutter
      travel_time: 25
    simple_shutter:
      where: '33'
      name: Simple Shutter
      strategy: simple
    group_shutters:
      where: '#5'
      name: Group 5
      members:
        - '31'
        - '32'
    garage_gate:
      who: '1'
      where: '14'
      name: Garage Gate
    ignored_native:
      where: '34'
      name: Ignored Native
      advanced: True
""",
        encoding="utf-8",
    )

    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(
        port,
        options={
            CONF_FILE_PATH: str(yaml_path),
            CONF_IGNORED_ADDRESSES: ["2/34"],
        },
    )
    ent_reg = er.async_get(hass)

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
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()

            # Only native point-to-point non-ignored WHO=2 covers (31 and 37) get the config number
            num_31_id = ent_reg.async_get_entity_id(
                "number", DOMAIN, f"{MAC}-2-31-min_position_delta"
            )
            num_37_id = ent_reg.async_get_entity_id(
                "number", DOMAIN, f"{MAC}-2-37-min_position_delta"
            )
            assert num_31_id == "number.frangisole_porta_studio_minimum_position_step"
            assert num_37_id == "number.living_native_minimum_position_step"
            for excluded in ("32", "33", "#5", "14", "34"):
                assert (
                    ent_reg.async_get_entity_id(
                        "number", DOMAIN, f"{MAC}-2-{excluded}-min_position_delta"
                    )
                    is None
                )

            # Verify entity metadata & initial values (default 2 for 31, YAML 4 for 37)
            num_31_entry = ent_reg.async_get(num_31_id)
            assert num_31_entry is not None
            assert num_31_entry.entity_category == EntityCategory.CONFIG
            num_31_state = hass.states.get(num_31_id)
            assert num_31_state is not None
            assert float(num_31_state.state) == 2.0
            assert num_31_state.attributes["unit_of_measurement"] == PERCENTAGE
            assert num_31_state.attributes["min"] == 1.0
            assert num_31_state.attributes["max"] == 5.0
            assert num_31_state.attributes["step"] == 1.0
            assert num_31_state.attributes["mode"] == NumberMode.BOX
            assert num_31_state.attributes.get("device_class") is None

            num_37_state = hass.states.get(num_37_id)
            assert num_37_state is not None
            assert float(num_37_state.state) == 4.0

            # Cover 31 is native and at default min_position_delta=2:
            # 0% -> 1% widens to 2%, and 2% -> 1% widens to 0% (full close).
            cover_31_id = ent_reg.async_get_entity_id("cover", DOMAIN, f"{MAC}-2-31")
            assert cover_31_id is not None
            cov_31 = hass.states.get(cover_31_id)
            assert cov_31 is not None
            assert cov_31.attributes["min_position_delta"] == 2
            assert "travel_time" not in cov_31.attributes

            await _feed(hass, "*#2*31*10*10*0*000*0##")
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": cover_31_id, "position": 1},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert "*#2*31*#11#001*2##" in _drain_sent(handler, harness)

            await _feed(hass, "*#2*31*10*10*2*000*0##")
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": cover_31_id, "position": 1},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert "*2*2*31##" in _drain_sent(handler, harness)

            # Set Minimum position step to 1 via UI number service -> updates live without reload!
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": num_31_id, "value": 1},
                blocking=True,
            )
            await hass.async_block_till_done()
            assert float(hass.states.get(num_31_id).state) == 1.0
            assert hass.states.get(cover_31_id).attributes["min_position_delta"] == 1
            assert entry.options.get(CONF_COVER_MIN_POSITION_DELTAS) == {"31": 1}

            # Now both 0% -> 1% and 2% -> 1% send exact Dim 11 level 1!
            await _feed(hass, "*#2*31*10*10*0*000*0##")
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": cover_31_id, "position": 1},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            assert "*#2*31*#11#001*1##" in _drain_sent(handler, harness)

            await _feed(hass, "*#2*31*10*10*2*000*0##")
            harness.received_messages.clear()
            await hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": cover_31_id, "position": 1},
                blocking=True,
            )
            await asyncio.sleep(0.05)
            await hass.async_block_till_done()
            sent_2_to_1 = _drain_sent(handler, harness)
            assert "*#2*31*#11#001*1##" in sent_2_to_1
            assert "*2*2*31##" not in sent_2_to_1

            # Update YAML to put min_position_delta: 3 on cover 31, and reload:
            # UI override (1) MUST win over YAML (3); cover 37 keeps YAML 4.
            yaml_path.write_text(
                f"""
{MAC}:
  cover:
    study_room_shutter:
      where: '31'
      name: Frangisole porta studio
      advanced: True
      min_position_delta: 3
      manufacturer: Legrand
      model: K4672M2S
    living_shutter:
      where: '37'
      name: Living Native
      advanced: True
      min_position_delta: 4
""",
                encoding="utf-8",
            )
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            hass.data[DOMAIN][MAC][CONF_ENTITY]._on_event_connection_state_change(True)
            await hass.async_block_till_done()
            assert float(hass.states.get(num_31_id).state) == 1.0
            assert hass.states.get(cover_31_id).attributes["min_position_delta"] == 1
            assert float(hass.states.get(num_37_id).state) == 4.0

            # Switch cover 37 from native to timed in YAML and reload: stale number entity is pruned!
            yaml_path.write_text(
                f"""
{MAC}:
  cover:
    study_room_shutter:
      where: '31'
      name: Frangisole porta studio
      advanced: True
      manufacturer: Legrand
      model: K4672M2S
    living_shutter:
      where: '37'
      name: Living Timed
      strategy: timed
      travel_time: 20
""",
                encoding="utf-8",
            )
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert (
                ent_reg.async_get_entity_id(
                    "number", DOMAIN, f"{MAC}-2-37-min_position_delta"
                )
                is None
            )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_auto_detected_dim10_creates_min_position_delta_number_and_cleans_on_removal(
    hass: HomeAssistant,
) -> None:
    """Auto-detected Dimension 10 covers (runtime promotion and first-frame) get the config number immediately."""
    harness = MockGatewayHarness()
    port = await harness.start()
    entry = _make_entry(port)
    ent_reg = er.async_get(hass)

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

            # 1. Runtime promotion: discover timed cover 0110 -> no delta number yet
            await _feed(hass, "*2*0*0110##")
            assert (
                ent_reg.async_get_entity_id(
                    "number", DOMAIN, f"{MAC}-2-0110-min_position_delta"
                )
                is None
            )
            cal_btn_id = ent_reg.async_get_entity_id(
                "button", DOMAIN, f"{MAC}-2-0110-calibrate"
            )
            assert cal_btn_id is not None
            assert hass.states.get(cal_btn_id).state != "unavailable"

            # Calibrated Dim 10 promotes 0110 -> calibrate button turns unavailable AND delta number appears!
            await _feed(hass, "*#2*0110*10*10*75*001*0##")
            assert hass.states.get(cal_btn_id).state == "unavailable"
            num_110_id = ent_reg.async_get_entity_id(
                "number", DOMAIN, f"{MAC}-2-0110-min_position_delta"
            )
            assert num_110_id is not None
            assert float(hass.states.get(num_110_id).state) == 2.0

            # Change promoted cover's delta to 1 live
            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": num_110_id, "value": 1},
                blocking=True,
            )
            await hass.async_block_till_done()
            assert hass.states.get("cover.cover_0110").attributes["min_position_delta"] == 1
            assert entry.options.get(CONF_COVER_MIN_POSITION_DELTAS) == {"0110": 1}

            # 2. First-frame Dim 10 discovery (detected_on_build + myhome_new_device)
            await _feed(hass, "*#2*0715*10*10*100*000*0##")
            num_715_id = ent_reg.async_get_entity_id(
                "number", DOMAIN, f"{MAC}-2-0715-min_position_delta"
            )
            assert num_715_id is not None
            assert float(hass.states.get(num_715_id).state) == 2.0

            await hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": num_715_id, "value": 3},
                blocking=True,
            )
            await hass.async_block_till_done()
            assert entry.options.get(CONF_COVER_MIN_POSITION_DELTAS) == {
                "0110": 1,
                "0715": 3,
            }

            # 3. Removing cover 0110 cleans "0110" while keeping "0715"; removing 0715 pops the option key
            ent_reg.async_remove("cover.cover_0110")
            await hass.async_block_till_done()
            assert entry.options.get(CONF_COVER_MIN_POSITION_DELTAS) == {"0715": 3}

            ent_reg.async_remove("cover.cover_0715")
            await hass.async_block_till_done()
            assert CONF_COVER_MIN_POSITION_DELTAS not in entry.options
            assert CONF_ADVANCED_COVERS not in entry.options
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


@pytest.mark.asyncio
async def test_prune_entity_and_follower_gating_for_cover_delta_numbers(
    hass: HomeAssistant,
) -> None:
    """prune_entity, prune_orphaned_companions, and shared-bus follower WHO gating handle -min_position_delta."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    pri_mac = "00:03:50:aa:bb:01"
    sec_mac = "00:03:50:aa:bb:02"

    # 1. prune_entity removes companion -min_position_delta number and empty device
    entry_p, _gw_p = _create_mock_gateway(
        hass, pri_mac, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    dev_p = dev_reg.async_get_or_create(
        config_entry_id=entry_p.entry_id,
        identifiers={(DOMAIN, f"{pri_mac}-2-41")},
        name="Cover 41",
    )
    cov_p = ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{pri_mac}-2-41",
        config_entry=entry_p,
        device_id=dev_p.id,
    )
    num_p = ent_reg.async_get_or_create(
        "number",
        DOMAIN,
        f"{pri_mac}-2-41-min_position_delta",
        config_entry=entry_p,
        device_id=dev_p.id,
    )
    prune_entity(hass, ent_reg, cov_p, entry_p.entry_id)
    assert ent_reg.async_get(num_p.entity_id) is None
    assert dev_reg.async_get(dev_p.id) is None

    # 2. prune_orphaned_companions removes orphaned follower -min_position_delta number
    dev_pri_21 = dev_reg.async_get_or_create(
        config_entry_id=entry_p.entry_id,
        identifiers={(DOMAIN, f"{pri_mac}-2-21")},
        name="Primary Cover 21",
    )
    ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{pri_mac}-2-21",
        config_entry=entry_p,
        device_id=dev_pri_21.id,
    )
    entry_s, _gw_s = _create_mock_gateway(
        hass,
        sec_mac,
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[16, 22],
    )
    orphan_dev = dev_reg.async_get_or_create(
        config_entry_id=entry_s.entry_id,
        identifiers={(DOMAIN, f"{sec_mac}-2-21")},
        name="Orphan Cover 21",
    )
    orphan_num = ent_reg.async_get_or_create(
        "number",
        DOMAIN,
        f"{sec_mac}-2-21-min_position_delta",
        config_entry=entry_s,
        device_id=orphan_dev.id,
    )
    unrelated_num = ent_reg.async_get_or_create(
        "number",
        DOMAIN,
        f"{sec_mac}-16-101#16-frequency",
        config_entry=entry_s,
    )
    prune_orphaned_companions(hass, entry_s.entry_id, sec_mac, pri_mac)
    assert ent_reg.async_get(orphan_num.entity_id) is None
    assert dev_reg.async_get(orphan_dev.id) is None
    assert ent_reg.async_get(unrelated_num.entity_id) is not None

    # 3. Follower with delegated WHO 16/22 (not WHO 2) gets tuner numbers only, not cover numbers
    hass.config_entries.async_update_entry(
        entry_s,
        options={**entry_s.options, CONF_SOURCE_TUNER.format(1): True},
    )
    entry_s.runtime_data.platforms["cover"] = {
        "2-22": {CONF_WHERE: "22", CONF_NAME: "Sec Cover", CONF_ADVANCED_SHUTTER: True}
    }
    added_sec: list = []
    assert await async_setup_numbers(hass, entry_s, added_sec.extend) is True
    assert len(added_sec) == 1
    assert added_sec[0].unique_id == f"{sec_mac}-16-101#16-frequency"

    # 4. Follower with delegated WHO 2 skips primary-owned cover 21, creates own cover 22
    entry_s2, _gw_s2 = _create_mock_gateway(
        hass,
        "00:03:50:aa:bb:03",
        topology=TOPOLOGY_SHARED,
        role=ROLE_SECONDARY,
        primary_gateway=pri_mac,
        delegated_whos=[2],
    )
    entry_s2.runtime_data.platforms["cover"] = {
        "2-21": {CONF_WHERE: "21", CONF_NAME: "Dup Cover", CONF_ADVANCED_SHUTTER: True},
        "2-22": {CONF_WHERE: "22", CONF_NAME: "Own Cover", CONF_ADVANCED_SHUTTER: True},
    }
    added_s2: list = []
    assert await async_setup_numbers(hass, entry_s2, added_s2.extend) is True
    assert [e.unique_id for e in added_s2] == [
        "00:03:50:aa:bb:03-2-22-min_position_delta"
    ]


@pytest.mark.asyncio
async def test_helpers_and_edge_branches_full_coverage(hass: HomeAssistant) -> None:
    """Exercise helper fallbacks, edge branches, and entity methods for 100% coverage."""
    # 1. resolve_cover_min_position_delta edge cases
    bad_entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_COVER_MIN_POSITION_DELTAS: {
                "31": True,  # bool must be ignored
                "32": 99,  # out of range must be ignored
                "33": 4,
            }
        },
    )
    assert resolve_cover_min_position_delta(bad_entry, "33") == 4
    assert resolve_cover_min_position_delta(bad_entry, "31", {CONF_MIN_POSITION_DELTA: "bad"}) == 2
    assert resolve_cover_min_position_delta(bad_entry, "32", {CONF_MIN_POSITION_DELTA: 9}) == 5
    assert resolve_cover_min_position_delta(bad_entry, "32", {CONF_MIN_POSITION_DELTA: 0}) == 1
    assert resolve_cover_min_position_delta(None, "31", None) == 2

    # 2. is_native_point_cover branches
    entry, gw = _create_mock_gateway(hass, MAC)
    entry.runtime_data.platforms["cover"] = {
        "2-11": {CONF_WHERE: "11", CONF_STRATEGY: "native"},
        "2-12": {CONF_WHERE: "12", CONF_STRATEGY: "timed"},
        "2-13": {CONF_WHERE: "13", CONF_ADVANCED_SHUTTER: True},
        "2-14": {CONF_WHERE: "14"},
    }
    assert is_native_point_cover(hass, entry, MAC, "#5") is False  # scope
    assert is_native_point_cover(hass, entry, MAC, "11") is True  # strategy: native
    assert is_native_point_cover(hass, entry, MAC, "12") is False  # strategy: timed
    assert is_native_point_cover(hass, entry, MAC, "13") is True  # advanced: True
    assert is_native_point_cover(hass, entry, MAC, "14") is False  # plain timed
    assert is_native_point_cover(None, entry, MAC, "11") is True  # hass=None

    # Live HA state short-circuit
    hass.states.async_set(
        "cover.live_native",
        "open",
        {"min_position_delta": 2},
    )
    assert is_native_point_cover(hass, entry, MAC, "99", entity_id="cover.live_native") is True

    # 3. _handle_min_position_delta_update mismatch and non-native cover
    timed_cover = MyHOMECover(
        hass=hass,
        name="Timed",
        entity_name=None,
        device_id="50",
        who="2",
        where="50",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )
    timed_cover._handle_min_position_delta_update("99", 1)
    assert timed_cover._min_position_delta == 2
    timed_cover._handle_min_position_delta_update("50", 1)
    assert timed_cover._min_position_delta == 1
    assert "min_position_delta" not in timed_cover.extra_state_attributes

    # 4. MyHOMECoverMinPositionDeltaNumber methods (available, async_update, F422 interface, hass=None)
    gw.is_who_available = MagicMock(return_value=True)
    num_f422 = MyHOMECoverMinPositionDeltaNumber(
        hass=hass,
        config_entry=entry,
        device_id="13#4#02",
        where="13",
        interface="02",
        name="Routed Native",
        gateway=gw,
    )
    assert num_f422._full_where == "13#4#02"
    assert num_f422.available is False  # 13#4#02 is not configured as native
    await num_f422.async_update()

    num_13 = MyHOMECoverMinPositionDeltaNumber(
        hass=hass,
        config_entry=entry,
        device_id="13",
        where="13",
        interface=None,
        name="Native 13",
        gateway=gw,
    )
    assert num_13.available is True
    # Also cover async_set_native_value when hass is None
    num_13._hass = None
    num_13.hass = None
    await num_13.async_set_native_value(3.0)
    assert num_13.native_value == 3.0

    # 5. number.async_setup_entry edge paths: exposed cover, non-WHO=2 new device, duplicate/invalid cfg,
    #    registry entry without device_id, and promotion listener with no registry entry.
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-11", config_entry=entry)
    ent_reg.async_get_or_create("cover", DOMAIN, f"{MAC}-1-14", config_entry=entry)  # impulse cover
    ent_reg.async_get_or_create("cover", DOMAIN, f"{MAC}-2-11", config_entry=entry)  # no device_id
    shared_cfg = {CONF_WHERE: "13", CONF_NAME: "Shared 13", CONF_ADVANCED_SHUTTER: True}
    entry.runtime_data.platforms["cover"] = {
        "2-11": {CONF_WHERE: "11", CONF_NAME: "Native 11", CONF_STRATEGY: "native"},
        "2-13": shared_cfg,
        "13": shared_cfg,  # duplicate dict reference
        "bad": "not-a-mapping",  # type: ignore[dict-item]
        "1-14": {CONF_WHO: "1", CONF_WHERE: "14", CONF_NAME: "Gate"},
        "2-15": {CONF_WHERE: "15", CONF_NAME: "Exposed 15", CONF_ADVANCED_SHUTTER: True},
    }
    added: list = []
    with patch(
        "custom_components.myhome.number.is_exposed_on_bus",
        side_effect=lambda _h, _e, _w, d: str(d) == "15",
    ):
        assert await async_setup_numbers(hass, entry, added.extend) is True
        assert {e.unique_id for e in added} == {
            f"{MAC}-2-11-min_position_delta",
            f"{MAC}-2-13-min_position_delta",
        }

        # Non-WHO=2 announcement ignored; empty device_id ignored; duplicate device_id ignored
        async_dispatcher_send(hass, f"myhome_new_device_{MAC}", {"who": "1", "device_id": "11"})
        async_dispatcher_send(hass, f"myhome_new_device_{MAC}", {"who": "2", "device_id": ""})
        async_dispatcher_send(hass, f"myhome_new_device_{MAC}", {"who": "2", "device_id": "11"})

        # Promotion listener for a cover absent from registry (no reg_entry) but stored in advanced_covers
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_ADVANCED_COVERS: {"88": True}},
        )
        async_dispatcher_send(hass, f"myhome_cover_promoted_{MAC}", "2-88")
        await hass.async_block_till_done()
        assert any(e.unique_id == f"{MAC}-2-88-min_position_delta" for e in added)
