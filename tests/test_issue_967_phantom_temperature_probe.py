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
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, interval: float
) -> None:
    """Real probe 303 pushing constant 22.9 C confirms on its 2nd report (>= 5.0 s, e.g. 601.3 s)."""
    caplog.set_level(logging.DEBUG, logger="custom_components.myhome")
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
        assert (
            "Confirmed WHO=4 temperature sensor 303 on second reading (*#4*303*0*0229##)"
            in caplog.text
        )

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
async def test_dimension_15_on_zone_where_ignored_and_probe_requires_second_reading(
    hass: HomeAssistant,
) -> None:
    """Dimension 15 on non-probe WHERE < 100 never collides with climate companions (#981), while probe Dimension 15 defers until a 2nd reading (#967)."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*5*15*1*0200*0001##")
        )
        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*5*15*1*0205*0001##")
        )
        await hass.async_block_till_done()
        assert added == []

        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105*15*1*0200*0001##")
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105*15*1*0205*0001##")
        )
        await hass.async_block_till_done()

        assert len(added) == 1
        assert added[0].unique_id == f"{MAC}-4-105-temperature"
        assert added[0].native_value == 20.5

        added[0].hass = hass
        added[0].entity_id = "sensor.probe_105_temperature"
        with patch.object(added[0], "async_get_last_state", AsyncMock(return_value=None)):
            await added[0].async_added_to_hass()
        assert added[0]._poll_on_add is False

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


@pytest.mark.asyncio
async def test_f422_routed_probe_interface_isolation_end_to_end(
    hass: HomeAssistant,
) -> None:
    """F422 #4#II probe discovery, routing, and registry restore isolate interfaces end-to-end."""
    clock = _FakeClock()
    entry, gateway, added = await _setup_sensor_platform(hass)

    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry, added.extend) is True
        await hass.async_block_till_done()

        # First reading on bus 01, then bus 02, then local bus: none cross-confirm each other
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*0105#4#01*0*0210##")
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105#4#02*0*0215##")
        )
        await hass.async_block_till_done()
        assert added == []

        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105*0*0195##")
        )
        await hass.async_block_till_done()
        assert added == []

        # Second reading on bus 01 confirms only probe 105#4#01
        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105#4#01*0*0212##")
        )
        await hass.async_block_till_done()
        assert len(added) == 1
        assert added[0].unique_id == f"{MAC}-4-105#4#01-temperature"
        assert added[0]._where == "105#4#01"
        assert added[0]._display_name == "Probe 105I01 Temperature"
        assert added[0].native_value == 21.2

        # Second reading on bus 02 confirms probe 105#4#02 without affecting bus 01
        clock.advance(0.5)
        async_dispatcher_send(
            hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*105#4#02*0*0220##")
        )
        await hass.async_block_till_done()
        assert len(added) == 2
        assert added[1].unique_id == f"{MAC}-4-105#4#02-temperature"
        assert added[1]._where == "105#4#02"
        assert added[1]._display_name == "Probe 105I02 Temperature"
        assert added[1].native_value == 22.0
        assert added[0].native_value == 21.2

    gateway.send_status_request.assert_not_awaited()

    # Restoring {mac}-4-105#4#01-temperature leaves bus 02 unconfirmed on its first frame
    mac_reg2 = "00:03:50:a6:02:d3"
    gw_reg2 = _gateway()
    gw_reg2.mac = mac_reg2
    entry_reg2 = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac_reg2}, unique_id=mac_reg2)
    entry_reg2.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mac_reg2}-4-105#4#01-temperature",
        suggested_object_id="probe_105_01_temperature",
        config_entry=entry_reg2,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    hass.data.setdefault(DOMAIN, {})[mac_reg2] = {
        CONF_PLATFORMS: {"sensor": {}},
        CONF_ENTITY: gw_reg2,
    }
    attach_runtime(hass, entry_reg2)
    added_reg2: list[MyHOMETemperatureSensor] = []
    with patch("custom_components.myhome.sensor.time.monotonic", side_effect=clock):
        assert await async_setup_entry(hass, entry_reg2, added_reg2.extend) is True
        await hass.async_block_till_done()
        assert [e.unique_id for e in added_reg2] == [f"{mac_reg2}-4-105#4#01-temperature"]

        async_dispatcher_send(
            hass, f"myhome_message_{mac_reg2}", OWNEvent.parse("*#4*105#4#02*0*0220##")
        )
        await hass.async_block_till_done()
        assert len(added_reg2) == 1


@pytest.mark.asyncio
async def test_zero_padded_probe_yaml_and_registry_canonicalization(
    hass: HomeAssistant,
) -> None:
    """Zero-padded probe 0105 in myhome.yaml and entity registry canonicalizes to 105 without duplicates."""
    # Sub-case A: myhome.yaml "4-0105" with existing canonical registry entry {mac}-4-105-temperature
    mac_a = "00:03:50:a6:02:e1"
    gw_a = _gateway()
    gw_a.mac = mac_a
    entry_a = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac_a}, unique_id=mac_a)
    entry_a.add_to_hass(hass)
    reg = er.async_get(hass)
    reg_a = reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mac_a}-4-105-temperature",
        suggested_object_id="garden_probe",
        config_entry=entry_a,
        original_device_class=SensorDeviceClass.TEMPERATURE,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    hass.data.setdefault(DOMAIN, {})[mac_a] = {
        CONF_PLATFORMS: {
            "sensor": {
                "4-0105": {
                    CONF_WHO: "4",
                    CONF_WHERE: "0105",
                    CONF_NAME: "Garden Probe",
                    CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
                    CONF_MANUFACTURER: "BTicino",
                    CONF_DEVICE_MODEL: "3455",
                }
            }
        },
        CONF_ENTITY: gw_a,
    }
    attach_runtime(hass, entry_a)
    added_a: list[MyHOMETemperatureSensor] = []
    assert await async_setup_entry(hass, entry_a, added_a.extend) is True
    await hass.async_block_till_done()
    assert [e.unique_id for e in added_a] == [f"{mac_a}-4-105-temperature"]
    assert added_a[0]._where == "105"
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{mac_a}-4-105-temperature") == reg_a.entity_id

    async_dispatcher_send(hass, f"myhome_message_{mac_a}", OWNEvent.parse("*#4*105*0*0210##"))
    await hass.async_block_till_done()
    assert len(added_a) == 1
    assert added_a[0].native_value == 21.0

    async_dispatcher_send(hass, f"myhome_message_{mac_a}", OWNEvent.parse("*#4*0105*0*0215##"))
    await hass.async_block_till_done()
    assert len(added_a) == 1
    assert added_a[0].native_value == 21.5

    # Sub-case B1: registry-only {mac}-4-0105-temperature migrates in place to {mac}-4-105-temperature
    mac_b1 = "00:03:50:a6:02:e2"
    gw_b1 = _gateway()
    gw_b1.mac = mac_b1
    entry_b1 = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac_b1}, unique_id=mac_b1)
    entry_b1.add_to_hass(hass)
    reg_b1 = reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mac_b1}-4-0105-temperature",
        suggested_object_id="probe_0105_temperature",
        config_entry=entry_b1,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    hass.data.setdefault(DOMAIN, {})[mac_b1] = {
        CONF_PLATFORMS: {"sensor": {}},
        CONF_ENTITY: gw_b1,
    }
    attach_runtime(hass, entry_b1)
    added_b1: list[MyHOMETemperatureSensor] = []
    assert await async_setup_entry(hass, entry_b1, added_b1.extend) is True
    await hass.async_block_till_done()
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{mac_b1}-4-0105-temperature") is None
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{mac_b1}-4-105-temperature") == reg_b1.entity_id
    assert [e.unique_id for e in added_b1] == [f"{mac_b1}-4-105-temperature"]

    # Sub-case B2: both {mac}-4-0105-temperature and {mac}-4-105-temperature in registry -> duplicate removed
    mac_b2 = "00:03:50:a6:02:e3"
    gw_b2 = _gateway()
    gw_b2.mac = mac_b2
    entry_b2 = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: mac_b2}, unique_id=mac_b2)
    entry_b2.add_to_hass(hass)
    reg_b2_canon = reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mac_b2}-4-105-temperature",
        suggested_object_id="probe_105_canon",
        config_entry=entry_b2,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    reg_b2_dup = reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{mac_b2}-4-0105-temperature",
        suggested_object_id="probe_0105_dup",
        config_entry=entry_b2,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    hass.data.setdefault(DOMAIN, {})[mac_b2] = {
        CONF_PLATFORMS: {"sensor": {}},
        CONF_ENTITY: gw_b2,
    }
    attach_runtime(hass, entry_b2)
    added_b2: list[MyHOMETemperatureSensor] = []
    assert await async_setup_entry(hass, entry_b2, added_b2.extend) is True
    await hass.async_block_till_done()
    assert reg.async_get(reg_b2_dup.entity_id) is None
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{mac_b2}-4-105-temperature") == reg_b2_canon.entity_id
    assert [e.unique_id for e in added_b2] == [f"{mac_b2}-4-105-temperature"]
