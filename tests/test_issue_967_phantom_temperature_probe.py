"""Tests for Issue #967: passive second-reading confirmation of unconfigured WHO 4 probes."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.discovery import Address, DeviceContext, PlatformDiscovery
from custom_components.myhome.sensor import (
    PROBE_DISCOVERY_ECHO_WINDOW,
    PROBE_DISCOVERY_MAX_AGE,
    MyHOMETemperatureSensor,
    async_setup_entry,
)
from tests.conftest import attach_runtime

MAC = "00:03:50:a6:02:a0"


class _FakeClock:
    """Deterministic monotonic clock for testing echo and expiry windows."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def _gateway() -> MagicMock:
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[Test Gateway]"
    gateway.device_registry_id = None
    gateway.is_connected = True
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


async def _setup_sensor_platform(
    hass: HomeAssistant,
    sensors: dict | None = None,
) -> tuple[MockConfigEntry, MagicMock, list[MyHOMETemperatureSensor]]:
    gateway = _gateway()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        CONF_PLATFORMS: {"sensor": sensors or {}},
        CONF_ENTITY: gateway,
    }
    attach_runtime(hass, entry)
    added: list[MyHOMETemperatureSensor] = []
    return entry, gateway, added


def test_probe_discovery_constants() -> None:
    """Verify module-level echo and expiry windows match Issue #967 specification."""
    assert PROBE_DISCOVERY_ECHO_WINDOW == 5.0
    assert PROBE_DISCOVERY_MAX_AGE == 3600.0


@pytest.mark.asyncio
async def test_bit7_flip_and_echo_do_not_create_phantom_probe(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A single bit-7 corrupted frame (*#4*129*0*0187##) and < 5s echo create no sensor and send zero reads."""
    caplog.set_level(logging.DEBUG, logger="custom_components.myhome")
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        # First corrupted frame: zone 1 (00000001) bit-7 flip -> probe 129 at 18.7 C
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0187##")
        )
        await hass.async_block_till_done()
        assert added == []
        assert (
            "Deferring WHO=4 temperature sensor 129 creation until confirmed by a second reading (*#4*129*0*0187##)"
            in caplog.text
        )

        # Identical MyHomeServer1 echo < 5.0 s later (at 0.2 s and 4.9 s) stays ignored
        clock.advance(0.2)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0187##")
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(4.7)  # total elapsed: 4.9 s < 5.0 s
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0187##")
        )
        await hass.async_block_till_done()
        assert added == []

    gateway.send_status_request.assert_not_awaited()
    gateway.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_candidate_expires_after_max_age_and_requires_fresh_pair(
    hass: HomeAssistant,
) -> None:
    """A candidate older than PROBE_DISCOVERY_MAX_AGE (3600 s) expires and requires a new pair."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        # 1st corrupted frame at t=1000.0 and immediate echo at t=1000.2 (must not extend expiry)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0187##")
        )
        clock.advance(0.2)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0187##")
        )
        await hass.async_block_till_done()
        assert added == []

        # Advance past 3600.0 s from initial arrival (total 3600.1 s): candidate is pruned,
        # so a different frame resets the candidate rather than confirming it.
        clock.advance(PROBE_DISCOVERY_MAX_AGE - 0.1)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0188##")
        )
        await hass.async_block_till_done()
        assert added == []

        # Confirming reading within the fresh window creates the entity.
        clock.advance(10.0)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*129*0*0188##")
        )
        await hass.async_block_till_done()
        assert len(added) == 1
        assert added[0].unique_id == f"{MAC}-4-129-temperature"
        assert added[0].native_value == 18.8

    gateway.send_status_request.assert_not_awaited()
    gateway.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [601.3, PROBE_DISCOVERY_ECHO_WINDOW])
async def test_periodic_constant_probe_confirms_on_second_report(
    hass: HomeAssistant, interval: float
) -> None:
    """Real probe 303 pushing constant 22.9 C confirms on its 2nd report (>= 5.0 s, e.g. 601.3 s)."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        # 1st periodic report: deferred
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*303*0*0229##")
        )
        await hass.async_block_till_done()
        assert added == []

        # 2nd periodic report (identical raw frame, >= 5.0 s later): creates entity
        clock.advance(interval)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*303*0*0229##")
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        sensor = added[0]
        assert sensor._where == "303"
        assert sensor.native_value == 22.9
        assert sensor.unique_id == f"{MAC}-4-303-temperature"
        assert sensor._poll_on_add is False

        # 3rd report updates existing entity without duplicating
        clock.advance(interval)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*303*0*0230##")
        )
        await hass.async_block_till_done()
        assert len(added) == 1
        assert sensor.native_value == 23.0

    gateway.send_status_request.assert_not_awaited()
    gateway.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_reading_within_echo_window_creates_immediately_on_second_frame(
    hass: HomeAssistant,
) -> None:
    """Distinct frame/value within < 5.0 s (0200 -> 0196) confirms the probe on the 2nd frame."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*100*15*1*0200*0001##")
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*100*15*1*0196*0001##")
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].unique_id == f"{MAC}-4-100-temperature"
        assert added[0].native_value == 19.6

    gateway.send_status_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_frame", "second_frame"),
    [
        ("*#4*0105*0*0215##", "*#4*105*0*0215##"),
        ("*#4*105*0*0215##", "*#4*0105*0*0215##"),
    ],
)
async def test_canonical_probe_spelling_change_within_echo_window_confirms(
    hass: HomeAssistant, first_frame: str, second_frame: str
) -> None:
    """0105 <-> 105 canonical spelling within < 5.0 s shares one candidate and creates one entity."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse(first_frame)
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse(second_frame)
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].unique_id == f"{MAC}-4-105-temperature"
        assert added[0].native_value == 21.5

        # Subsequent frame in padded spelling routes to the same entity with no duplicate
        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*0105*0*0220##")
        )
        await hass.async_block_till_done()
        assert len(added) == 1
        assert added[0].native_value == 22.0

    gateway.send_status_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_dimension_15_on_zone_where_requires_second_reading(
    hass: HomeAssistant,
) -> None:
    """Unconfigured Dimension 15 frame on WHERE < 100 also defers until a 2nd independent reading."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*5*15*1*0200*0001##")
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*5*15*1*0205*0001##")
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].unique_id == f"{MAC}-4-5-temperature"
        assert added[0].native_value == 20.5

    gateway.send_status_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_yaml_and_registry_and_configured_bus_probes_remain_immediate(
    hass: HomeAssistant,
) -> None:
    """YAML-configured and entity-registry restored probes stay immediate without waiting."""
    # 1. myhome.yaml probe creates immediately at setup
    yaml_cfg = {
        "4-303": {
            CONF_WHO: "4",
            CONF_WHERE: "303",
            CONF_NAME: "Garden Probe",
            CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            CONF_MANUFACTURER: "BTicino",
            CONF_DEVICE_MODEL: "3455",
        }
    }
    entry_yaml, _gw_yaml, added_yaml = await _setup_sensor_platform(hass, yaml_cfg)
    assert await async_setup_entry(hass, entry_yaml, added_yaml.extend) is True
    await hass.async_block_till_done()
    assert [e.unique_id for e in added_yaml] == [f"{MAC}-4-303-temperature"]

    # 2. Entity-registry restored probe creates immediately at setup
    mac_reg = "00:03:50:a6:02:b1"
    gw_reg = _gateway()
    gw_reg.mac = mac_reg
    entry_reg = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac_reg}, unique_id=mac_reg)
    entry_reg.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mac_reg}-4-304-temperature",
        suggested_object_id="probe_304_temperature",
        config_entry=entry_reg,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    hass.data.setdefault(DOMAIN, {})[mac_reg] = {
        CONF_PLATFORMS: {"sensor": {}},
        CONF_ENTITY: gw_reg,
    }
    attach_runtime(hass, entry_reg)
    added_reg: list[MyHOMETemperatureSensor] = []
    assert await async_setup_entry(hass, entry_reg, added_reg.extend) is True
    await hass.async_block_till_done()
    assert [e.unique_id for e in added_reg] == [f"{mac_reg}-4-304-temperature"]

    # 3. Direct accept_temperature hook check for ctx.source == "bus" with ctx.cfg truthy
    mac_bus = "00:03:50:a6:02:c2"
    gw_bus = _gateway()
    gw_bus.mac = mac_bus
    entry_bus = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac_bus}, unique_id=mac_bus)
    entry_bus.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[mac_bus] = {
        CONF_PLATFORMS: {"sensor": {}},
        CONF_ENTITY: gw_bus,
    }
    attach_runtime(hass, entry_bus)

    with patch("custom_components.myhome.sensor.PlatformDiscovery", wraps=PlatformDiscovery) as mock_pd:
        assert await async_setup_entry(hass, entry_bus, lambda _entities: None) is True

    pd_4 = next(call.kwargs for call in mock_pd.call_args_list if call.kwargs.get("who") == "4")
    accept_temp = pd_4["accept"]
    ctx_cfg_bus = DeviceContext(
        address=Address("305"),
        who="4",
        cfg={CONF_WHO: "4", CONF_WHERE: "305", CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE},
        source="bus",
        message=OWNEvent.parse("*#4*305*0*0210##"),
    )
    assert accept_temp(ctx_cfg_bus) is True

    # Routed interface key isolation (0105#4#01 vs 105#4#02)
    ctx_routed_1 = DeviceContext(
        address=Address("0105", "01"),
        who="4",
        source="bus",
        message=OWNEvent.parse("*#4*0105#4#01*0*0210##"),
    )
    ctx_routed_2 = DeviceContext(
        address=Address("105", "02"),
        who="4",
        source="bus",
        message=OWNEvent.parse("*#4*105#4#02*0*0215##"),
    )
    ctx_routed_1_confirm = DeviceContext(
        address=Address("105", "01"),
        who="4",
        source="bus",
        message=OWNEvent.parse("*#4*105#4#01*0*0215##"),
    )
    assert accept_temp(ctx_routed_1) is False
    assert accept_temp(ctx_routed_2) is False
    assert accept_temp(ctx_routed_1_confirm) is True
