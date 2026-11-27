"""Regression tests for Group 3 audit fixes (advanced Venetian tilt restore, ignored.py, WHO=1 motion 0004/04, manual-delete orphans)."""
from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.restore_state import RestoredExtraData
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import binary_sensor as binary_sensor_mod
from custom_components.myhome import number as number_mod
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.const import CONF_IGNORED_ADDRESSES, DOMAIN
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.ignored import (
    IgnoredAddresses,
    parse_ignored_address,
    validate_ignored_addresses,
)

MAC = "00:03:50:aa:33:01"


def _make_gateway(
    hass: HomeAssistant,
    *,
    model: str = "F454",
    options: dict | None = None,
) -> tuple[MockConfigEntry, MyHOMEGatewayHandler]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.33",
            CONF_PORT: 20000,
            CONF_PASSWORD: "secret",
            CONF_MAC: MAC,
            CONF_NAME: model,
        },
        options=options or {},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.send = AsyncMock()
    handler.send_status_request = AsyncMock()
    entry.runtime_data = MyHOMERuntimeData(gateway=handler)
    return entry, handler


async def test_group3a_advanced_venetian_persists_and_restores_slat_tilt(
    hass: HomeAssistant,
) -> None:
    """Advanced (Dimension 10) Venetian blinds persist and restore slat tilt across restarts (available & unavailable)."""
    _, handler = _make_gateway(hass, model="F461")

    cov = MyHOMECover(
        hass=hass,
        name="Venetian 21",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
        slat_tilt=True,
        slat_time=2.0,
    )
    cov._attr_current_cover_position = 60
    cov._attr_current_cover_tilt_position = 50
    extra = cov.extra_restore_state_data
    assert extra is not None
    assert extra.as_dict().get("tilt") == 50

    # 1. Restore from normal state attributes (even if Dim 10 height already set _live_position_seen)
    cov_norm = MyHOMECover(
        hass=hass,
        name="Venetian 21",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
        slat_tilt=True,
        slat_time=2.0,
    )
    cov_norm._live_position_seen = True
    cov_norm.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(extra.as_dict()))  # type: ignore[method-assign]
    await cov_norm.async_restore_last_state(
        State("cover.venetian_21", "open", {"current_position": 60, "current_tilt_position": 50})
    )
    assert cov_norm.current_cover_tilt_position == 50

    # 2. Restore from STATE_UNAVAILABLE via extra_restore_state_data record["tilt"]
    cov_unavail = MyHOMECover(
        hass=hass,
        name="Venetian 21",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
        slat_tilt=True,
        slat_time=2.0,
    )
    cov_unavail.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(extra.as_dict()))  # type: ignore[method-assign]
    await cov_unavail.async_restore_last_state(State("cover.venetian_21", STATE_UNAVAILABLE, {}))
    assert cov_unavail.current_cover_tilt_position == 50


async def test_group3b_ignored_4001_param_wheres_and_hyphenated_yaml_key(
    hass: HomeAssistant,
) -> None:
    """Verify 4/001..099 distinct from 4/1..99, #-parameterized WHO!=4 accepted, and gate-31 not pruned."""
    # 3b-1: 4/001..4/099 ("all probes of zone Z") stays distinct from climate zone 1..99
    assert parse_ignored_address("4/001") == (4, "001")
    assert parse_ignored_address("4/099") == (4, "099")
    assert parse_ignored_address("4/01") == (4, "1")
    ign_probes = IgnoredAddresses(["4/001", "4/005#4#01"])
    assert ign_probes.is_ignored(4, "001") is True
    assert ign_probes.is_ignored(4, "1") is False
    assert ign_probes.is_ignored(4, "01") is False
    assert ign_probes.is_ignored(4, "005#4#01") is True
    assert ign_probes.is_ignored(4, "5#4#01") is False

    # 3b-2: #-parameterized WHEREs on WHO != 4 are accepted and matched cleanly
    for addr, expected in (
        ("18/71#0", (18, "71#0")),
        ("6/1#2", (6, "1#2")),
        ("1/701234501#9", (1, "701234501#9")),
        ("22/5#2#1", (22, "5#2#1")),
    ):
        assert parse_ignored_address(addr) == expected
        canon, ok = validate_ignored_addresses(addr)
        assert ok is True
        assert canon == [addr]
        who_i, where_s = expected
        assert IgnoredAddresses([addr]).is_ignored(who_i, where_s) is True

    # Invalid # forms remain rejected
    for bad in ("1/abc#xyz", "1/#1#2", "18/71#", "18/71##0", "1/74#4#20"):
        assert validate_ignored_addresses(bad) == ([], False)

    # 3b-3: Hyphenated YAML key "gate-31" (where="32") is NOT pruned when 25/31 is ignored
    entry, _ = _make_gateway(hass, options={CONF_IGNORED_ADDRESSES: ["25/31"]})
    assert entry.runtime_data is not None
    entry.runtime_data.platforms["binary_sensor"] = {
        "gate-31": {
            "who": "25",
            "where": "32",
            "name": "Gate 31",
            "device_class": "opening",
        }
    }
    ent_reg = er.async_get(hass)
    existing = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{MAC}-gate-31-opening",
        config_entry=entry,
        original_device_class="opening",
    )
    added: list = []
    assert await binary_sensor_mod.async_setup_entry(hass, entry, added.extend) is True
    assert ent_reg.async_get(existing.entity_id) is not None
    assert len(added) == 1


async def test_group3c_who1_motion_area00_0004_vs_04_anonymous_reply_and_dedup(
    hass: HomeAssistant,
) -> None:
    """Area-00 motion sensor (0004) receives anonymous WHERE=0 Dim 5 reply claimed as '04' and does not duplicate on *1*34*04##."""
    entry, handler = _make_gateway(hass, model="F454")
    assert entry.runtime_data is not None
    entry.runtime_data.platforms["binary_sensor"] = {}
    handler.claim_in_flight_dimension_where = lambda who, dim: "04" if (who == 1 and dim == 5) else None  # type: ignore[method-assign]

    added: list = []
    assert await binary_sensor_mod.async_setup_entry(hass, entry, added.extend) is True

    # 1. Discover motion sensor via 4-digit Area-00 spelling *1*34*0004##
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*34*0004##"))
    await hass.async_block_till_done()
    assert len(added) == 1
    sensor = added[0]
    sensor.hass = hass
    sensor.entity_id = "binary_sensor.motion_sensor_0004"
    sensor.async_write_ha_state = lambda: None

    # 2. Anonymous WHERE=0 PIR sensitivity reply (*#1*0*5*3## -> "very high") claimed as "04" routes cleanly!
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#1*0*5*3##"))
    await hass.async_block_till_done()
    assert sensor.extra_state_attributes.get("Sensitivity") == "very high"

    # 3. 2-digit spelling *1*34*04## routes to the same entity without creating a duplicate!
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*34*04##"))
    await hass.async_block_till_done()
    assert len(added) == 1


async def test_group3d_manual_delete_and_reload_prune_climate_and_cover_orphans(
    hass: HomeAssistant,
) -> None:
    """Deleting a climate or cover prunes companion sensors/numbers cleanly without breaking Dormant Skip."""
    entry, handler = _make_gateway(hass, model="F461")
    assert entry.runtime_data is not None
    ent_reg = er.async_get(hass)

    # 1. Manual UI deletion of MyHOMEClimate prunes both -temperature and -humidity companions immediately
    entry.runtime_data.platforms["sensor"] = {}
    entry.runtime_data.platforms["climate"] = {}
    clim_temp = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-3-temperature", config_entry=entry)
    clim_hum = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-3-humidity", config_entry=entry)
    clim = MyHOMEClimate(
        hass=hass,
        name="Climate Zone 3",
        device_id="3",
        who="4",
        where="3",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=handler,
    )
    clim.entity_id = "climate.climate_zone_3"
    await clim.async_removed_from_registry()
    assert ent_reg.async_get(clim_temp.entity_id) is None
    assert ent_reg.async_get(clim_hum.entity_id) is None

    # 2. Manual UI deletion of MyHOMEClimate preserves a YAML-owned zone temperature sensor, still prunes -humidity
    entry.runtime_data.platforms["sensor"] = {
        "zone2_temp": {
            "who": "4",
            "where": "2",
            "name": "Zone 2 YAML Temp",
            "device_class": "temperature",
            "manufacturer": "BTicino",
            "model": "Probe",
        },
    }
    yaml_temp = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-2-temperature", config_entry=entry)
    z2_hum = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-2-humidity", config_entry=entry)
    clim2 = MyHOMEClimate(
        hass=hass,
        name="Climate Zone 2",
        device_id="2",
        who="4",
        where="2",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=handler,
    )
    clim2.entity_id = "climate.climate_zone_2"
    await clim2.async_removed_from_registry()
    assert ent_reg.async_get(yaml_temp.entity_id) is not None
    assert ent_reg.async_get(z2_hum.entity_id) is None

    # 3. Reload cleanup when cover is absent for -min_position_delta number
    orphan_num = ent_reg.async_get_or_create("number", DOMAIN, f"{MAC}-2-31-min_position_delta", config_entry=entry)
    entry.runtime_data.platforms["cover"] = {}
    entry.runtime_data.platforms["number"] = {}
    added_nums: list = []
    assert await number_mod.async_setup_entry(hass, entry, added_nums.extend) is True
    assert ent_reg.async_get(orphan_num.entity_id) is None

    # 4. Immediate cleanup on MyHOMECover.async_removed_from_registry()
    cov_num = ent_reg.async_get_or_create("number", DOMAIN, f"{MAC}-2-32-min_position_delta", config_entry=entry)
    cov = MyHOMECover(
        hass=hass,
        name="Cover 32",
        entity_name=None,
        device_id="32",
        who="2",
        where="32",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
    )
    cov.entity_id = "cover.cover_32"
    await cov.async_removed_from_registry()
    assert ent_reg.async_get(cov_num.entity_id) is None
