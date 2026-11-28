"""Regression tests for Group 3 audit fixes (advanced Venetian tilt restore, ignored.py, WHO=1 motion 0004/04, manual-delete orphans)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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

    # 5. Legacy "2-" prefixed cover device_id also removes -min_position_delta and empty device
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    cov33_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-2-33")},
        name="Cover 33",
    )
    cov33_num = ent_reg.async_get_or_create(
        "number",
        DOMAIN,
        f"{MAC}-2-33-min_position_delta",
        config_entry=entry,
        device_id=cov33_dev.id,
    )
    cov33 = MyHOMECover(
        hass=hass,
        name="Cover 33",
        entity_name=None,
        device_id="2-33",
        who="2",
        where="33",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
    )
    cov33.entity_id = "cover.cover_33"
    await cov33.async_removed_from_registry()
    assert ent_reg.async_get(cov33_num.entity_id) is None
    assert dev_reg.async_get(cov33_dev.id) is None


async def test_group3_audit_fixes_who22_who18_motion_and_climate_safeguards(
    hass: HomeAssistant,
) -> None:
    """Verify WHO=22 Area-4 speakers, WHO=18 #0 meters, hyphenated motion keys, and climate companion safeguards."""
    from custom_components.myhome.binary_sensor import _motion_spellings
    from custom_components.myhome.ignored import IgnoredAddresses, parse_ignored_address

    # 1. WHO=22 Area-4 speakers (3#4#1, 3#4#16) must not be misparsed as F422 #4#II routing
    assert parse_ignored_address("22/3#4#1") == (22, "3#4#1")
    assert parse_ignored_address("22/3#4#16") == (22, "3#4#16")
    ign22 = IgnoredAddresses(["22/3#4#1", "22/3#4#16"])
    assert ign22.is_ignored(22, "3#4#1") is True
    assert ign22.is_ignored(22, "3#4#16") is True
    assert ign22.is_ignored(22, "3", interface="01") is False

    # 2. WHO=18 F522/F523 meter 18/71#0 matches discovered address 71 symmetrically
    assert IgnoredAddresses(["18/71#0"]).is_ignored(18, "71") is True
    assert IgnoredAddresses(["18/71"]).is_ignored(18, "71#0") is True

    # 3. Hyphenated YAML motion sensor keys must not leak numeric suffix into Area-00 spellings
    assert _motion_spellings("hall-04") == ["hall-04"]
    assert _motion_spellings("1-04") == ["1-04", "04", "0004"]

    # 4. Climate manual delete: sibling climate preserves companions; non-temp YAML sensor does not orphan -temperature;
    #    -output-level companion is pruned when the last climate entity for the zone is deleted.
    entry, handler = _make_gateway(hass, model="F461")
    assert entry.runtime_data is not None
    ent_reg = er.async_get(hass)

    # 4a. Sibling climate on zone 5 (e.g. zero-padded legacy unique_id) keeps all companions intact
    ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-05", config_entry=entry)
    z5_temp = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-5-temperature", config_entry=entry)
    z5_hum = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-5-humidity", config_entry=entry)
    z5_out = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-5#1-output-level", config_entry=entry)
    clim5 = MyHOMEClimate(
        hass=hass,
        name="Zone 5",
        device_id="5",
        who="4",
        where="5",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=handler,
    )
    clim5.entity_id = "climate.zone_5"
    await clim5.async_removed_from_registry()
    assert ent_reg.async_get(z5_temp.entity_id) is not None
    assert ent_reg.async_get(z5_hum.entity_id) is not None
    assert ent_reg.async_get(z5_out.entity_id) is not None

    # 4b. Non-temperature WHO=4 YAML sensor on zone 6 does not block -temperature or -output-level pruning
    entry.runtime_data.platforms["sensor"] = {
        "zone6_hum": {"who": "4", "where": "6", "device_class": "humidity", "name": "Zone 6 Hum"},
    }
    z6_temp = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-6-temperature", config_entry=entry)
    z6_out = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-6#1-output-level", config_entry=entry)
    clim6 = MyHOMEClimate(
        hass=hass,
        name="Zone 6",
        device_id="6",
        who="4",
        where="6",
        heating=True,
        cooling=False,
        fan=False,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=handler,
    )
    clim6.entity_id = "climate.zone_6"
    await clim6.async_removed_from_registry()
    assert ent_reg.async_get(z6_temp.entity_id) is None
    assert ent_reg.async_get(z6_out.entity_id) is None

    # 4c. Early-return guards in async_removed_from_registry: central, non-zone where, missing entry
    central = MyHOMEClimate(
        hass=hass,
        name="Central 0",
        device_id="#0",
        who="4",
        where="#0",
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=True,
        manufacturer="BTicino",
        model="3550",
        gateway=handler,
    )
    await central.async_removed_from_registry()

    bad_where = MyHOMEClimate(
        hass=hass,
        name="Zone bad",
        device_id="bad",
        who="4",
        where="bad",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=handler,
    )
    await bad_where.async_removed_from_registry()

    handler_no_entry = MyHOMEGatewayHandler(hass, entry)
    handler_no_entry.config_entry = None  # type: ignore[assignment]
    no_entry_clim = MyHOMEClimate(
        hass=hass,
        name="Zone 7",
        device_id="7",
        who="4",
        where="7",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=handler_no_entry,
    )
    await no_entry_clim.async_removed_from_registry()

    # 4d. Non-numeric restored tilt on an advanced Venetian cover is ignored cleanly
    cov_bad_tilt = MyHOMECover(
        hass=hass,
        name="Venetian 22",
        entity_name=None,
        device_id="22",
        who="2",
        where="22",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
        slat_tilt=True,
        slat_time=2.0,
    )
    cov_bad_tilt.async_get_last_extra_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await cov_bad_tilt.async_restore_last_state(
        State("cover.venetian_22", "open", {"current_position": 50, "current_tilt_position": "not-a-number"})
    )
    assert cov_bad_tilt.current_cover_tilt_position is None


async def test_group3_audit_dim10_end_tilt_who22_motion_and_companion_pruning(
    hass: HomeAssistant,
) -> None:
    """Verify #1033 audit fixes: Dim 10 pos=0/100 tilt preservation, WHO 22 interface guard, motion hall-04/0004/peer spellings, and companion pruning."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.myhome.binary_sensor import (
        MyHOMEAuxiliary,
        MyHOMEDryContact,
        _motion_peer_spellings,
        classify_binary_sensor_registry_entry,
    )
    from custom_components.myhome.ignored import IgnoredAddresses
    from custom_components.myhome.light import MyHOMELight
    from custom_components.myhome.switch import MyHOMESwitch

    entry, handler = _make_gateway(hass, model="F461")
    assert entry.runtime_data is not None
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # 1. A1 Path 1 (Dim 10 pos=0/100 AFTER restore preserves restored tilt 50 on advanced Venetian)
    cov_after = MyHOMECover(
        hass=hass,
        name="Venetian 23",
        entity_name=None,
        device_id="23",
        who="2",
        where="23",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
        slat_tilt=True,
        slat_time=2.0,
    )
    cov_after.async_get_last_extra_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cov_after.async_schedule_update_ha_state = MagicMock()  # type: ignore[method-assign]
    await cov_after.async_restore_last_state(
        State("cover.venetian_23", "closed", {"current_position": 0, "current_tilt_position": 50})
    )
    assert cov_after.current_cover_tilt_position == 50
    cov_after.handle_event(OWNEvent.parse("*#2*23*10*10*0*001*0##"))
    assert cov_after.current_cover_position == 0
    assert cov_after.current_cover_tilt_position == 50

    # 2. A1 Path 2 (Dim 10 pos=0 BEFORE restore still lets async_restore_last_state restore saved tilt 50)
    cov_before = MyHOMECover(
        hass=hass,
        name="Venetian 24",
        entity_name=None,
        device_id="24",
        who="2",
        where="24",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
        slat_tilt=True,
        slat_time=2.0,
    )
    cov_before.async_get_last_extra_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cov_before.async_schedule_update_ha_state = MagicMock()  # type: ignore[method-assign]
    cov_before.handle_event(OWNEvent.parse("*#2*24*10*10*0*001*0##"))
    assert cov_before.current_cover_tilt_position == 0
    await cov_before.async_restore_last_state(
        State("cover.venetian_24", "closed", {"current_position": 0, "current_tilt_position": 50})
    )
    assert cov_before.current_cover_tilt_position == 50

    # 3. A2 (Non-advanced Venetian receiving Dim 10 BEFORE restore still restores saved tilt 50)
    cov_timed = MyHOMECover(
        hass=hass,
        name="Venetian 25",
        entity_name=None,
        device_id="25",
        who="2",
        where="25",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=False,
        travel_time=20.0,
        slat_tilt=True,
        slat_time=2.0,
        allow_auto_advanced=False,
    )
    cov_timed.async_get_last_extra_data = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cov_timed.async_schedule_update_ha_state = MagicMock()  # type: ignore[method-assign]
    cov_timed.handle_event(OWNEvent.parse("*#2*25*10*10*60*001*0##"))
    assert cov_timed._live_position_seen is True
    await cov_timed.async_restore_last_state(
        State("cover.venetian_25", "open", {"current_position": 60, "current_tilt_position": 50})
    )
    assert cov_timed.current_cover_tilt_position == 50

    # 4. A3 (WHO=22 with explicit interface never false-matches 22/3#4#1)
    ign22 = IgnoredAddresses(["22/3#4#1"])
    assert ign22.is_ignored(22, "3", interface="1") is False
    assert ign22.is_ignored(22, "3#4#1", interface="01") is False
    assert ign22.is_ignored(22, "3#4#1") is True

    # 5. A5 + A6 + B4 (classify_binary_sensor_registry_entry preserves hall-04; build_motion matches 0004 <-> 04; _motion_peer_spellings)
    hall_entry = ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{MAC}-hall-04-motion",
        config_entry=entry,
        original_device_class="motion",
    )
    assert classify_binary_sensor_registry_entry(hall_entry, MAC, MAC) == ("1", "hall-04")
    assert _motion_peer_spellings(f"{MAC}-0004-motion") == [
        f"{MAC}-04-motion",
        f"{MAC}-1-0004-motion",
        f"{MAC}-1-04-motion",
    ]
    assert _motion_peer_spellings(f"{MAC}-1-04-motion") == [
        f"{MAC}-1-0004-motion",
        f"{MAC}-04-motion",
        f"{MAC}-0004-motion",
    ]
    assert _motion_peer_spellings(f"{MAC}-hall-04-motion") == []
    assert _motion_peer_spellings("badid") == []
    assert _motion_peer_spellings("a-b-1-04-motion") == []

    # Registry restore of {MAC}-0004-motion picks up YAML config keyed as "04"
    ent_reg.async_remove(hall_entry.entity_id)
    ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{MAC}-0004-motion",
        config_entry=entry,
        original_device_class="motion",
    )
    entry.runtime_data.platforms["binary_sensor"] = {
        "04": {"who": "1", "where": "04", "name": "Hallway PIR", "device_class": "motion"}
    }
    restored_motion: list = []
    assert await binary_sensor_mod.async_setup_entry(hass, entry, restored_motion.extend) is True
    assert len(restored_motion) == 1
    assert restored_motion[0]._device_name == "Hallway PIR"

    # 6. B1 (Manual cover delete prunes -min_position_delta, -calibrate, -disable, -enable AND empty device)
    cov_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-2-34")},
        name="Cover 34",
    )
    c_num = ent_reg.async_get_or_create("number", DOMAIN, f"{MAC}-2-34-min_position_delta", config_entry=entry, device_id=cov_dev.id)
    c_cal = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-2-34-calibrate", config_entry=entry, device_id=cov_dev.id)
    c_dis = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-2-34-disable", config_entry=entry, device_id=cov_dev.id)
    c_en = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-2-34-enable", config_entry=entry, device_id=cov_dev.id)
    cov34 = MyHOMECover(
        hass=hass,
        name="Cover 34",
        entity_name=None,
        device_id="34",
        who="2",
        where="34",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F401",
        gateway=handler,
        advanced=True,
    )
    cov34.entity_id = "cover.cover_34"
    await cov34.async_removed_from_registry()
    assert ent_reg.async_get(c_num.entity_id) is None
    assert ent_reg.async_get(c_cal.entity_id) is None
    assert ent_reg.async_get(c_dis.entity_id) is None
    assert ent_reg.async_get(c_en.entity_id) is None
    assert dev_reg.async_get(cov_dev.id) is None

    # 7. B2 (Manual light, switch, dry contact, and auxiliary delete prunes companions + empty devices)
    lt_dev = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-41")}, name="Light 41")
    lt_dis = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-1-41-disable", config_entry=entry, device_id=lt_dev.id)
    lt_en = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-1-41-enable", config_entry=entry, device_id=lt_dev.id)
    lt = MyHOMELight(
        hass=hass, name="Light 41", entity_name=None, icon=None, icon_on=None,
        device_id="41", who="1", where="41", interface=None, dimmable=False,
        manufacturer="BTicino", model="Relay", gateway=handler,
    )
    lt.entity_id = "light.light_41"
    await lt.async_removed_from_registry()
    assert ent_reg.async_get(lt_dis.entity_id) is None
    assert ent_reg.async_get(lt_en.entity_id) is None
    assert dev_reg.async_get(lt_dev.id) is None

    sw_dev = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-1-42")}, name="Switch 42")
    sw_dis = ent_reg.async_get_or_create("button", DOMAIN, f"{MAC}-1-42-disable", config_entry=entry, device_id=sw_dev.id)
    sw = MyHOMESwitch(
        hass=hass, name="Switch 42", entity_name=None, icon=None, icon_on=None,
        device_id="42", who="1", where="42", interface=None, device_class="switch",
        manufacturer="BTicino", model="F411", gateway=handler,
    )
    sw.entity_id = "switch.switch_42"
    await sw.async_removed_from_registry()
    assert ent_reg.async_get(sw_dis.entity_id) is None
    assert dev_reg.async_get(sw_dev.id) is None

    dc_dev = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-25-31")}, name="Contact 31")
    dc_reg = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-25-31-opening", config_entry=entry, device_id=dc_dev.id)
    dc_inv = ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-25-31-invert", config_entry=entry, device_id=dc_dev.id)
    dc = MyHOMEDryContact(
        hass=hass, device_id="31", who="25", where="31", name="Contact 31",
        entity_name=None, inverted=False, device_class="opening",
        manufacturer="BTicino", model="F428", gateway=handler,
    )
    dc.entity_id = dc_reg.entity_id
    dc.registry_entry = dc_reg
    ent_reg.async_remove(dc_reg.entity_id)
    await dc.async_removed_from_registry()
    assert ent_reg.async_get(dc_inv.entity_id) is None
    assert dev_reg.async_get(dc_dev.id) is None

    aux_dev = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-9-1")}, name="Aux 1")
    aux_reg = ent_reg.async_get_or_create("binary_sensor", DOMAIN, f"{MAC}-9-1-opening", config_entry=entry, device_id=aux_dev.id)
    aux_inv = ent_reg.async_get_or_create("switch", DOMAIN, f"{MAC}-9-1-invert", config_entry=entry, device_id=aux_dev.id)
    aux = MyHOMEAuxiliary(
        hass=hass, device_id="1", who="9", where="1", name="Aux 1",
        entity_name=None, inverted=False, device_class="opening",
        manufacturer="BTicino", model="Aux", gateway=handler,
    )
    aux.entity_id = aux_reg.entity_id
    aux.registry_entry = aux_reg
    ent_reg.async_remove(aux_reg.entity_id)
    await aux.async_removed_from_registry()
    assert ent_reg.async_get(aux_inv.entity_id) is None
    assert dev_reg.async_get(aux_dev.id) is None

    # 8. B3 (4-zone central unit #0#1 manual delete prunes -output-level sensor & empty device)
    cu_dev = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-4-#0#1")}, name="Central Unit 01")
    cu_reg = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-#0#1", config_entry=entry, device_id=cu_dev.id)
    cu_out = ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-1#1-output-level", config_entry=entry, device_id=cu_dev.id)
    cu = MyHOMEClimate(
        hass=hass, name="Central Unit 01", device_id="#0#1", who="4", where="#0#1",
        heating=True, cooling=True, fan=False, standalone=False, central=True,
        manufacturer="BTicino", model="4695", gateway=handler,
    )
    cu.entity_id = cu_reg.entity_id
    cu.registry_entry = cu_reg
    ent_reg.async_remove(cu_reg.entity_id)
    await cu.async_removed_from_registry()
    assert ent_reg.async_get(cu_out.entity_id) is None
    assert dev_reg.async_get(cu_dev.id) is None

    # 9. Early-return guards in prune_companions_for_removed_entity (no config_entry, missing unique_id)
    from types import SimpleNamespace

    from custom_components.myhome.discovery import prune_companions_for_removed_entity

    prune_companions_for_removed_entity(SimpleNamespace(hass=None, _hass=None, _gateway_handler=None))
    prune_companions_for_removed_entity(
        SimpleNamespace(
            hass=hass,
            _hass=hass,
            _gateway_handler=handler,
            registry_entry=None,
            _platform="sensor",
            _who="18",
            _device_id="51",
            unique_id=None,
        )
    )
    prune_companions_for_removed_entity(
        SimpleNamespace(
            hass=hass,
            _hass=hass,
            _gateway_handler=handler,
            registry_entry=None,
            _platform="binary_sensor",
            _who="25",
            _device_id="99",
            unique_id=f"{MAC}-25-99-opening",
        )
    )


