"""Regression tests for #1015 (zero-conflict pre-release batch from #1010).

Covers:
- [HIGH] #5 (`ignored.py`): preserve canonical 2-digit vs 4-digit A/PL addresses on
  WHO = 1 / 2 / 14 (`0010..0015` <-> `10..15`, `01..09` <-> `1..9`, `00` <-> `0`,
  `0100` <-> `100`) and never strip `#4#II` across F422 sub-bus boundaries.
- M-07 + M-15 (`discovery.py` registry side): include `"-output-level"` in
  `CLIMATE_COMPANION_SENSOR_SUFFIXES`, preserve explicit YAML WHO=4 zone temperature
  sensors in `prune_entity()`, and match canonical `-4-Z-temperature` / `-humidity` /
  `-output-level` suffixes when `climate` used `01` or `#1`.
- M-19 (`websocket.py`, `services.py`): enforce the ASCII frame guard in
  `ws_bus_monitor_send`, route status/dimension reads via `send_status_request()`,
  and refuse per-gateway crash frames (`MH202` `*#4*001##` / `*#4*001*0##` /
  `*#4*2#1##` / `*#4*2#4#01##`; `H4684`/`MH200` `*#13**10..12##`).
- M-20 (`pypi_standards.yml`, `ownd-smoke.yml`): include `"chore/release-*"` in
  `pull_request.branches` and avoid overwriting/failing git-installed `OWNd` pins.
- L-07 (`binary_sensor.py`): reject empty `where == ""` on malformed `*#1**5*2##`.
- L-08 (`config_flow_discovery.py`): copy `discovery_info.upnp` in `async_step_ssdp`
  and check `not _no_stored_firmware(gateway.firmware)` in `_ssdp_entry_updates`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant import config_entries
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.discovery_flow import DiscoveryKey
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.connection import get_gateway_profile
from OWNd.message import OWNLightingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import binary_sensor
from custom_components.myhome.binary_sensor import _motion_address
from custom_components.myhome.const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    CONF_BUS_TOPOLOGY,
    CONF_ENTITY,
    CONF_EXPOSE,
    CONF_FIRMWARE,
    CONF_GATEWAY_ROLE,
    CONF_IGNORED_ADDRESSES,
    CONF_PLATFORMS,
    CONF_PRIMARY_GATEWAY,
    CONF_SSDP_LOCATION,
    CONF_UDN,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    ROLE_STANDBY,
    SUBENTRY_TYPE_EXPOSE,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.discovery import (
    CLIMATE_COMPANION_SENSOR_SUFFIXES,
    PlatformDiscovery,
    _climate_zone_from_unique_id,
    _has_active_yaml_zone_temperature,
    prune_entity,
)
from custom_components.myhome.expose import ExposeManager
from custom_components.myhome.ignored import IgnoredAddresses
from custom_components.myhome.myhome_device import MyHOMEEntity
from custom_components.myhome.services import (
    SERVICE_SEND_MESSAGE,
    _handler_model_key,
    _is_mh202_who4_crash_frame,
    async_setup_services,
    gateway_crash_frame_reason,
)
from custom_components.myhome.websocket import ws_bus_monitor_send
from tests.conftest import attach_runtime

REPO_ROOT = Path(__file__).resolve().parent.parent
MAC = "00:03:50:aa:bb:cc"
PRIMARY_MAC = "00:03:50:11:22:33"


# ── [HIGH] #5: ignored.py A/PL leading zeros & #4#II sub-bus isolation ──────


def test_ignored_addresses_preserve_canonical_apl_and_f422_isolation() -> None:
    """WHO 1/2/14 canonical 2-digit vs 4-digit A/PL addresses and #4#II risers never collide."""
    # 1. A=0, PL=10..15 ('0010'..'0015') vs A=1, PL=0..5 ('10'..'15')
    for who in (1, 2, 14):
        for pl in range(10, 16):
            four_digit = f"00{pl}"
            two_digit = f"{pl}"
            ign_4 = IgnoredAddresses([f"{who}/{four_digit}"])
            assert ign_4.is_ignored(who, four_digit) is True
            assert ign_4.is_ignored(who, two_digit) is False
            assert ign_4.is_ignored(who, f"{four_digit}#4#01") is False
            assert ign_4.is_ignored(who, four_digit, interface="01") is False

            ign_2 = IgnoredAddresses([f"{who}/{two_digit}"])
            assert ign_2.is_ignored(who, two_digit) is True
            assert ign_2.is_ignored(who, four_digit) is False
            for bus in (*range(1, 10), *range(11, 16)):
                bus_str = f"{bus:02d}"
                assert ign_2.is_ignored(who, f"{two_digit}#4#{bus_str}") is False
                assert ign_2.is_ignored(who, two_digit, interface=bus_str) is False
                assert ign_2.is_ignored(who, two_digit, interface=str(bus)) is False

    # 2. A=0, PL=1..9 ('01'..'09' / '0001'..'0009') vs Area 1..9 ('1'..'9') & Group '#1'..'#9'
    for pl in range(1, 10):
        ign_pt = IgnoredAddresses([f"1/0{pl}"])
        assert ign_pt.is_ignored(1, f"0{pl}") is True
        assert ign_pt.is_ignored(1, f"000{pl}") is True
        assert ign_pt.is_ignored(1, str(pl)) is False
        assert ign_pt.is_ignored(1, f"#{pl}") is False

        ign_4pt = IgnoredAddresses([f"2/000{pl}"])
        assert ign_4pt.is_ignored(2, f"0{pl}") is True
        assert ign_4pt.is_ignored(2, str(pl)) is False

    # 3. General '0' vs Area 0 '00' vs Area 10 '100' vs invalid '0100' / '0000' / '014'
    assert IgnoredAddresses(["1/00"]).is_ignored(1, "00") is True
    assert IgnoredAddresses(["1/00"]).is_ignored(1, "0") is False
    assert IgnoredAddresses(["1/0"]).is_ignored(1, "00") is False
    assert IgnoredAddresses(["1/0"]).is_ignored(1, "0000") is False
    assert IgnoredAddresses(["1/100"]).is_ignored(1, "100") is True
    assert IgnoredAddresses(["1/100"]).is_ignored(1, "0100") is False
    assert IgnoredAddresses(["1/0100"]).is_ignored(1, "100") is False
    assert IgnoredAddresses(["1/014"]).is_ignored(1, "14") is False
    assert IgnoredAddresses(["1/014"]).is_ignored(1, "0014") is False

    # 4. Extended 4-digit A=1..9, PL=10..15 ('0114') stays distinct from '114'
    assert IgnoredAddresses(["1/0114"]).is_ignored(1, "0114") is True
    assert IgnoredAddresses(["1/0114"]).is_ignored(1, "114") is False

    # 5. Inline #4#II with mismatched vs matching interface argument
    ign_sub01 = IgnoredAddresses(["1/14#4#01"])
    assert ign_sub01.is_ignored(1, "14#4#01", interface="01") is True
    assert ign_sub01.is_ignored(1, "14#4#1", interface="01") is True
    assert ign_sub01.is_ignored(1, "14#4#01", interface="02") is False

    # 6. Non-WHO-1/2/14 subsystems continue stripping leading zeros
    assert IgnoredAddresses(["4/01"]).is_ignored(4, "1") is True
    assert IgnoredAddresses(["4/00"]).is_ignored(4, "0") is True
    assert IgnoredAddresses(["25/31"]).is_ignored(25, "31", interface="01") is False


async def test_platform_discovery_ignoring_main_bus_14_preserves_f422_and_0014(
    hass: HomeAssistant,
) -> None:
    """Ignoring 1/14 prunes main-bus 14 while keeping 0014 and 14#4#01 intact."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10", CONF_MAC: MAC, CONF_NAME: "F454"},
        options={CONF_IGNORED_ADDRESSES: ["1/14"]},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    gw = MagicMock(mac=MAC, unique_id=MAC)
    hass.data[DOMAIN] = {MAC: {CONF_ENTITY: gw, CONF_PLATFORMS: {"light": {}}}}
    attach_runtime(hass, entry)

    reg = er.async_get(hass)
    e_main = reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-14", config_entry=entry)
    e_0014 = reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-0014", config_entry=entry)
    e_f422 = reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-14#4#01", config_entry=entry)

    def _build(ctx: Any) -> MyHOMEEntity:
        ent = MyHOMEEntity(
            hass=hass,
            name=f"Light {ctx.suffix}",
            platform="light",
            device_id=ctx.key,
            who="1",
            where=ctx.address.key,
            manufacturer="BTicino",
            model="Light",
            gateway=gw,
        )
        ent._attr_unique_id = f"{MAC}-1-{ctx.key}"
        return ent

    added: list[Any] = []
    disc = PlatformDiscovery(
        hass,
        entry,
        added.extend,
        platform="light",
        who="1",
        event_type=OWNLightingEvent,
        build=_build,
    )
    restored = disc.restore()
    assert {e.unique_id for e in restored} == {f"{MAC}-1-0014", f"{MAC}-1-14#4#01"}
    assert reg.async_get(e_main.entity_id) is None
    assert reg.async_get(e_0014.entity_id) is not None
    assert reg.async_get(e_f422.entity_id) is not None


# ── M-07 + M-15: discovery.py climate companion cleanup & YAML preservation ──


def _setup_climate_entry(
    hass: HomeAssistant,
    *,
    mac: str = MAC,
    options: dict[str, Any] | None = None,
    sensor_yaml: dict[str, Any] | None = None,
    subentries: list[dict[str, Any]] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10", CONF_MAC: mac, CONF_NAME: "F461"},
        options=options or {},
        subentries_data=subentries or [],
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    opts = options or {}
    gw = MagicMock(mac=mac, unique_id=mac)
    gw.is_follower = bool(
        opts.get(CONF_BUS_TOPOLOGY) == TOPOLOGY_SHARED
        and opts.get(CONF_GATEWAY_ROLE) in ("follower", ROLE_STANDBY)
    )
    gw.primary_gateway_mac = opts.get(CONF_PRIMARY_GATEWAY)
    hass.data[DOMAIN] = {
        mac: {
            CONF_ENTITY: gw,
            CONF_PLATFORMS: {"sensor": dict(sensor_yaml or {}), "climate": {}},
        }
    }
    runtime = attach_runtime(hass, entry)
    runtime.expose_manager = ExposeManager(hass, entry, gw, [])
    return entry


def test_climate_companion_suffixes_include_output_level() -> None:
    """M-07: CLIMATE_COMPANION_SENSOR_SUFFIXES includes -output-level."""
    assert CLIMATE_COMPANION_SENSOR_SUFFIXES == (
        "-temperature",
        "-humidity",
        "-output-level",
    )


async def test_prune_climate_removes_actuator_output_sensor_and_empty_device(
    hass: HomeAssistant,
) -> None:
    """M-07: pruning a climate zone removes -temperature, -humidity, -output-level, and empty DeviceEntry."""
    entry = _setup_climate_entry(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-1")},
        name="Climate Zone 1",
    )
    clim = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-1", config_entry=entry, device_id=dev.id
    )
    temp = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1-temperature", config_entry=entry, device_id=dev.id
    )
    hum = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1-humidity", config_entry=entry, device_id=dev.id
    )
    out = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1#1-output-level", config_entry=entry, device_id=dev.id
    )

    prune_entity(hass, ent_reg, clim, entry.entry_id)

    assert ent_reg.async_get(clim.entity_id) is None
    assert ent_reg.async_get(temp.entity_id) is None
    assert ent_reg.async_get(hum.entity_id) is None
    assert ent_reg.async_get(out.entity_id) is None
    assert dev_reg.async_get(dev.id) is None


@pytest.mark.parametrize(
    ("climate_tail", "zone_key", "act_key", "other_act_key"),
    [
        ("01", "1", "1#1", "1#1#4#01"),
        ("#1", "1", "1#2", "2#1"),
        ("01#4#1", "1#4#01", "1#1#4#01", "1#1"),
    ],
)
async def test_prune_climate_noncanonical_01_and_hash1_matches_canonical_companions(
    hass: HomeAssistant,
    climate_tail: str,
    zone_key: str,
    act_key: str,
    other_act_key: str,
) -> None:
    """M-15: pruning climate {mac}-4-01 / #1 removes canonical -4-1-* companions across split devices."""
    entry = _setup_climate_entry(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    dev_clim = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-{climate_tail}")},
    )
    dev_canon = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-{zone_key}")},
    )
    dev_other = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-other")},
    )

    clim = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-{climate_tail}", config_entry=entry, device_id=dev_clim.id
    )
    temp = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-{zone_key}-temperature", config_entry=entry, device_id=dev_canon.id
    )
    hum = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-{zone_key}-humidity", config_entry=entry, device_id=dev_canon.id
    )
    out = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-{act_key}-output-level", config_entry=entry, device_id=dev_canon.id
    )
    unrelated_out = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-{other_act_key}-output-level", config_entry=entry, device_id=dev_other.id
    )
    unrelated_energy = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-18-51-power", config_entry=entry, device_id=dev_other.id
    )

    prune_entity(hass, ent_reg, clim, entry.entry_id)

    assert ent_reg.async_get(clim.entity_id) is None
    assert ent_reg.async_get(temp.entity_id) is None
    assert ent_reg.async_get(hum.entity_id) is None
    assert ent_reg.async_get(out.entity_id) is None
    assert dev_reg.async_get(dev_clim.id) is None
    assert dev_reg.async_get(dev_canon.id) is None
    # Unrelated bus/zone actuator sensor & energy sensor remain untouched
    assert ent_reg.async_get(unrelated_out.entity_id) is not None
    assert ent_reg.async_get(unrelated_energy.entity_id) is not None


async def test_prune_climate_preserves_explicit_yaml_zone_temperature_sensor(
    hass: HomeAssistant,
) -> None:
    """M-15: explicit YAML WHO=4 zone temperature sensor and its device survive climate pruning."""
    entry = _setup_climate_entry(
        hass,
        sensor_yaml={
            "ignored_non_mapping": "invalid",
            "power_51": {"who": "18", "where": "51", "class": "power"},
            "probe_105": {"who": "4", "where": "105", "class": "temperature"},
            "act_1_1": {"who": "4", "where": "1#1", "class": "temperature"},
            "4-01": {"who": "4", "where": "01", "class": "temperature", "name": "Living Temp"},
        },
    )
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-1")},
    )
    clim = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-1", config_entry=entry, device_id=dev.id
    )
    yaml_temp = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1-temperature", config_entry=entry, device_id=dev.id
    )
    hum = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1-humidity", config_entry=entry, device_id=dev.id
    )
    out = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1#1-output-level", config_entry=entry, device_id=dev.id
    )

    prune_entity(hass, ent_reg, clim, entry.entry_id)

    assert ent_reg.async_get(clim.entity_id) is None
    assert ent_reg.async_get(hum.entity_id) is None
    assert ent_reg.async_get(out.entity_id) is None
    # Explicit YAML temperature sensor and its device stay registered
    assert ent_reg.async_get(yaml_temp.entity_id) is not None
    assert dev_reg.async_get(dev.id) is not None


async def test_prune_climate_removes_yaml_temp_when_ignored_exposed_or_on_primary(
    hass: HomeAssistant,
) -> None:
    """YAML zone temperature sensor is NOT preserved when ignored, exposed, or owned by primary."""
    ent_reg = er.async_get(hass)

    # 1. Ignored zone 4/1
    ign_entry = _setup_climate_entry(
        hass,
        mac="00:03:50:00:00:11",
        options={CONF_IGNORED_ADDRESSES: ["4/1"]},
        sensor_yaml={"4-1": {"who": "4", "where": "1", "class": "temperature"}},
    )
    clim_ign = ent_reg.async_get_or_create(
        "climate", DOMAIN, "00:03:50:00:00:11-4-1", config_entry=ign_entry
    )
    temp_ign = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "00:03:50:00:00:11-4-1-temperature", config_entry=ign_entry
    )
    prune_entity(hass, ent_reg, clim_ign, ign_entry.entry_id)
    assert ent_reg.async_get(temp_ign.entity_id) is None

    # 2. Exposed WHO=4 WHERE=1
    exp_entry = _setup_climate_entry(
        hass,
        mac="00:03:50:00:00:22",
        sensor_yaml={"4-1": {"where": "1", "device_class": "temperature"}},
        subentries=[
            {
                "subentry_type": SUBENTRY_TYPE_EXPOSE,
                "title": "Exposed Temp",
                "unique_id": "exp-4-1",
                "data": {CONF_WHO: 4, CONF_WHERE: "1", CONF_EXPOSE: "sensor.room"},
            }
        ],
    )
    clim_exp = ent_reg.async_get_or_create(
        "climate", DOMAIN, "00:03:50:00:00:22-4-1", config_entry=exp_entry
    )
    temp_exp = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "00:03:50:00:00:22-4-1-temperature", config_entry=exp_entry
    )
    prune_entity(hass, ent_reg, clim_exp, exp_entry.entry_id)
    assert ent_reg.async_get(temp_exp.entity_id) is None

    # 3. Follower gateway whose primary already owns {primary_mac}-4-1-temperature
    pri_entry = _setup_climate_entry(hass, mac=PRIMARY_MAC)
    ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{PRIMARY_MAC}-4-1-temperature", config_entry=pri_entry
    )
    fol_mac = "00:03:50:00:00:33"
    fol_entry = _setup_climate_entry(
        hass,
        mac=fol_mac,
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: PRIMARY_MAC,
        },
        sensor_yaml={"4-1": {"who": "4", "where": "1", "class": "temperature"}},
    )
    clim_fol = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{fol_mac}-4-1", config_entry=fol_entry
    )
    temp_fol = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{fol_mac}-4-1-temperature", config_entry=fol_entry
    )
    prune_entity(hass, ent_reg, clim_fol, fol_entry.entry_id)
    assert ent_reg.async_get(temp_fol.entity_id) is None

    # Follower whose primary does NOT have the sensor keeps its active YAML temperature sensor
    fol_mac_2 = "00:03:50:00:00:44"
    fol_entry_2 = _setup_climate_entry(
        hass,
        mac=fol_mac_2,
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: PRIMARY_MAC,
        },
        sensor_yaml={"4-2": {"where": "2", "device_class": "temperature"}},
    )
    clim_fol_2 = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{fol_mac_2}-4-2", config_entry=fol_entry_2
    )
    temp_fol_2 = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{fol_mac_2}-4-2-temperature", config_entry=fol_entry_2
    )
    prune_entity(hass, ent_reg, clim_fol_2, fol_entry_2.entry_id)
    assert ent_reg.async_get(temp_fol_2.entity_id) is not None


async def test_prune_climate_preserves_companions_when_sibling_climate_exists_and_handles_edge_ids(
    hass: HomeAssistant,
) -> None:
    """Duplicate {mac}-4-01 prune keeps live {mac}-4-1 companions; helper edge cases are safe."""
    entry = _setup_climate_entry(hass)
    ent_reg = er.async_get(hass)

    ent_reg.async_get_or_create("light", DOMAIN, f"{MAC}-1-12", config_entry=entry)
    live_clim = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-1", config_entry=entry)
    dup_clim = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-01", config_entry=entry)
    temp = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1-temperature", config_entry=entry
    )
    out = ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}-4-1#1-output-level", config_entry=entry
    )

    prune_entity(hass, ent_reg, dup_clim, entry.entry_id)
    assert ent_reg.async_get(dup_clim.entity_id) is None
    assert ent_reg.async_get(live_clim.entity_id) is not None
    assert ent_reg.async_get(temp.entity_id) is not None
    assert ent_reg.async_get(out.entity_id) is not None

    # Fallback parsing without runtime_data and non-zone unique_ids (#0, probe, actuator, empty)
    assert _climate_zone_from_unique_id(None, "", "") == (None, None, set())
    assert _climate_zone_from_unique_id(f"{MAC}-4-01", "", "") == ("1", None, {MAC})
    assert _climate_zone_from_unique_id("-4-01", "", "") == (None, None, set())
    assert _climate_zone_from_unique_id(f"{MAC}-1-12", MAC, MAC) == (None, None, {MAC})
    assert _climate_zone_from_unique_id(f"{MAC}-4-#0", MAC, MAC) == (None, None, {MAC})
    assert _climate_zone_from_unique_id(f"{MAC}-4-105", MAC, MAC) == (None, None, {MAC})
    assert _climate_zone_from_unique_id(f"{MAC}-4-1#1", MAC, MAC) == (None, None, {MAC})
    assert _has_active_yaml_zone_temperature(hass, ent_reg, None, None, "1", None) is False

    # Entry without runtime_data still cleans canonical companions via fallback -4- parser
    bare_entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: "00:03:50:99:88:77"})
    bare_entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    dev_bare = dev_reg.async_get_or_create(
        config_entry_id=bare_entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:99:88:77-4-01")},
    )
    clim_bare = ent_reg.async_get_or_create(
        "climate",
        DOMAIN,
        "00:03:50:99:88:77-4-01",
        config_entry=bare_entry,
        device_id=dev_bare.id,
    )
    extra_dev_sensor = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "00:03:50:99:88:77-custom-humidity",
        config_entry=bare_entry,
        device_id=dev_bare.id,
    )
    keep_other_sensor = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "00:03:50:99:88:77-custom-power",
        config_entry=bare_entry,
        device_id=dev_bare.id,
    )
    temp_bare = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "00:03:50:99:88:77-4-1-temperature", config_entry=bare_entry
    )
    foreign_mac_out = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "ff:ee:dd:cc:bb:aa-4-1#1-output-level", config_entry=bare_entry
    )
    prune_entity(hass, ent_reg, clim_bare, bare_entry.entry_id)
    assert ent_reg.async_get(temp_bare.entity_id) is None
    assert ent_reg.async_get(extra_dev_sensor.entity_id) is None
    assert ent_reg.async_get(keep_other_sensor.entity_id) is not None
    assert ent_reg.async_get(foreign_mac_out.entity_id) is not None

    # Second climate entry remaining on the same device keeps device-attached companions
    dev_shared = dev_reg.async_get_or_create(
        config_entry_id=bare_entry.entry_id,
        identifiers={(DOMAIN, "00:03:50:99:88:77-shared")},
    )
    c_central_1 = ent_reg.async_get_or_create(
        "climate",
        DOMAIN,
        "00:03:50:99:88:77-4-#0",
        config_entry=bare_entry,
        device_id=dev_shared.id,
    )
    c_central_2 = ent_reg.async_get_or_create(
        "climate",
        DOMAIN,
        "00:03:50:99:88:77-4-#0#1",
        config_entry=bare_entry,
        device_id=dev_shared.id,
    )
    shared_hum = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "00:03:50:99:88:77-shared-humidity",
        config_entry=bare_entry,
        device_id=dev_shared.id,
    )
    prune_entity(hass, ent_reg, c_central_1, bare_entry.entry_id)
    assert ent_reg.async_get(c_central_2.entity_id) is not None
    assert ent_reg.async_get(shared_hum.entity_id) is not None


# ── M-19: ws_bus_monitor_send ASCII/read routing & per-gateway crash guards ──


@pytest.fixture
def mock_ws_conn() -> MagicMock:
    conn = MagicMock(spec=websocket_api.ActiveConnection)
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()
    conn.send_message = MagicMock()
    conn.subscriptions = {}
    return conn


async def test_ws_bus_monitor_send_ascii_guard_and_status_request_routing(
    hass: HomeAssistant, mock_ws_conn: MagicMock, attach_gateway: Any
) -> None:
    """M-19: ws_bus_monitor_send rejects non-ASCII digits and routes reads via send_status_request."""
    gw = MagicMock()
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    attach_gateway(MAC, gw)

    # 1. Non-ASCII digits & embedded newlines rejected before parsing/sending
    for idx, bad in enumerate(("*1*1*１２##", "*1*1*\u0661\u0662##", "*1*1*12##\n*1*0*12##"), 1):
        mock_ws_conn.send_error.reset_mock()
        ws_bus_monitor_send(hass, mock_ws_conn, {"id": idx, "type": "myhome/bus_monitor/send", "frame": bad})
        await hass.async_block_till_done()
        mock_ws_conn.send_error.assert_called_once_with(
            idx, websocket_api.ERR_INVALID_FORMAT, f"Invalid OpenWebNet frame format: {bad.strip()}"
        )
    gw.send.assert_not_awaited()
    gw.send_status_request.assert_not_awaited()

    # 2. Status & dimension reads route through send_status_request()
    for idx, read_frame in enumerate(("*#1*12##", "*#1*52*5##", "*#13**15##"), 10):
        gw.send_status_request.reset_mock()
        mock_ws_conn.send_result.reset_mock()
        ws_bus_monitor_send(
            hass, mock_ws_conn, {"id": idx, "type": "myhome/bus_monitor/send", "frame": read_frame}
        )
        await hass.async_block_till_done()
        mock_ws_conn.send_result.assert_called_once_with(idx, {"success": True, "frame": read_frame})
        gw.send_status_request.assert_awaited_once()
        assert str(gw.send_status_request.await_args.args[0]) == read_frame
    gw.send.assert_not_awaited()

    # 3. Commands and #DIM writes still route through send()
    for idx, write_frame in enumerate(("*1*1*12##", "*#1*12*#1*150*0##"), 20):
        gw.send.reset_mock()
        ws_bus_monitor_send(
            hass, mock_ws_conn, {"id": idx, "type": "myhome/bus_monitor/send", "frame": write_frame}
        )
        await hass.async_block_till_done()
        gw.send.assert_awaited_once()
        assert str(gw.send.await_args.args[0]) == write_frame


def test_gateway_crash_frame_classifier_and_model_resolution() -> None:
    """Unit-test _handler_model_key, _is_mh202_who4_crash_frame, and delegation resolution."""
    assert _is_mh202_who4_crash_frame("*#4*001##") is True
    assert _is_mh202_who4_crash_frame("*#4*001*0##") is True
    assert _is_mh202_who4_crash_frame("*#4*099*0##") is True
    assert _is_mh202_who4_crash_frame("*#4*2#1##") is True
    assert _is_mh202_who4_crash_frame("*#4*2#4#01##") is True
    assert _is_mh202_who4_crash_frame("*#4*2#1#4#01##") is True
    assert _is_mh202_who4_crash_frame("*#4*2##") is False
    assert _is_mh202_who4_crash_frame("*#4*#0##") is False
    assert _is_mh202_who4_crash_frame("*#4*2#abc##") is False
    assert _is_mh202_who4_crash_frame("*#4*1#2#3#4#5##") is False
    assert _is_mh202_who4_crash_frame("*#4*2#4#12##") is False
    assert _is_mh202_who4_crash_frame("*#4*2#1*20##") is False
    assert _is_mh202_who4_crash_frame("*#1*12##") is False

    # Model extraction fallbacks
    assert _handler_model_key(SimpleNamespace(_model_key="MH202")) == "MH202"
    assert _handler_model_key(SimpleNamespace(gateway=SimpleNamespace(model_name="BTicino MH202"))) == "MH202"
    assert _handler_model_key(SimpleNamespace(gateway=SimpleNamespace(model="MH200"))) == "MH200"
    assert _handler_model_key(SimpleNamespace(model="BTicino H/L4684")) == "H4684"
    assert _handler_model_key(SimpleNamespace(config_entry=SimpleNamespace(data={CONF_NAME: "L4684"}))) == "H4684"
    mh202_profile = get_gateway_profile("MH202")
    assert _handler_model_key(SimpleNamespace(gateway=SimpleNamespace(profile=mh202_profile))) == "MH202"
    assert _handler_model_key(SimpleNamespace()) == ""

    # Shared-bus WHO 4 delegation from F461 primary to MH202 secondary blocks MH202 crash frames
    mh202_peer = SimpleNamespace(_model_key="MH202")
    f461_with_mh202 = SimpleNamespace(_model_key="F461", _who4_target=lambda: mh202_peer)
    assert gateway_crash_frame_reason(f461_with_mh202, "*#4*2#1##") is not None
    assert gateway_crash_frame_reason(f461_with_mh202, "*#13**12##") is None

    # Self _who4_target or empty peer key does not block on F461
    f461_self = SimpleNamespace(_model_key="F461")
    f461_self._who4_target = lambda: f461_self
    assert gateway_crash_frame_reason(f461_self, "*#4*001##") is None
    f461_unknown_peer = SimpleNamespace(_model_key="F461", _who4_target=lambda: SimpleNamespace())
    assert gateway_crash_frame_reason(f461_unknown_peer, "*#4*001##") is None


@pytest.mark.parametrize(
    ("model", "blocked_frames", "allowed_frames"),
    [
        (
            "MH202",
            ("*#4*001##", "*#4*001*0##", "*#4*2#1##", "*#4*2#4#01##"),
            ("*#4*2##", "*#4*2*0##", "*#4*2#1*20##", "*#4*#0##", "*#13**12##"),
        ),
        (
            "H4684",
            ("*#13**10##", "*#13**11##", "*#13**12##"),
            ("*#13**15##", "*#13**16##", "*#4*001##"),
        ),
        (
            "MH200",
            ("*#13**10##", "*#13**11##", "*#13**12##"),
            ("*#13**15##", "*#13**16##", "*#4*001##"),
        ),
        (
            "MH200N",
            (),
            ("*#13**10##", "*#13**11##", "*#13**12##", "*#4*001##"),
        ),
        (
            "F461",
            (),
            ("*#13**10##", "*#13**11##", "*#13**12##", "*#4*001##", "*#4*2##"),
        ),
    ],
)
async def test_per_gateway_crash_frames_blocked_in_send_message_and_websocket(
    hass: HomeAssistant,
    mock_ws_conn: MagicMock,
    attach_gateway: Any,
    model: str,
    blocked_frames: tuple[str, ...],
    allowed_frames: tuple[str, ...],
) -> None:
    """M-19: per-gateway crash frames are blocked in both myhome.send_message and ws_bus_monitor_send."""
    await async_setup_services(hass)
    gw = MagicMock()
    gw._model_key = model
    gw.model = model
    gw.log_id = f"[{model}]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw._who4_target = lambda: gw
    mac = f"00:03:50:cc:{len(model):02x}:{ord(model[-1]):02x}"
    attach_gateway(mac, gw)

    for idx, bad in enumerate(blocked_frames, 1):
        with pytest.raises(ServiceValidationError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SEND_MESSAGE,
                {ATTR_GATEWAY: mac, ATTR_MESSAGE: bad},
                blocking=True,
            )
        assert err.value.translation_key == "send_message_refused_frame"

        mock_ws_conn.send_error.reset_mock()
        mock_ws_conn.send_result.reset_mock()
        ws_bus_monitor_send(
            hass,
            mock_ws_conn,
            {"id": idx, "type": "myhome/bus_monitor/send", "mac": mac, "frame": bad},
        )
        await hass.async_block_till_done()
        mock_ws_conn.send_result.assert_not_called()
        mock_ws_conn.send_error.assert_called_once()
        assert mock_ws_conn.send_error.call_args.args[1] == websocket_api.ERR_INVALID_FORMAT
        assert bad in mock_ws_conn.send_error.call_args.args[2]

    gw.send.assert_not_awaited()
    gw.send_status_request.assert_not_awaited()

    for idx, ok in enumerate(allowed_frames, 100):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            {ATTR_GATEWAY: mac, ATTR_MESSAGE: ok},
            blocking=True,
        )
        mock_ws_conn.send_result.reset_mock()
        ws_bus_monitor_send(
            hass,
            mock_ws_conn,
            {"id": idx, "type": "myhome/bus_monitor/send", "mac": mac, "frame": ok},
        )
        await hass.async_block_till_done()
        mock_ws_conn.send_result.assert_called_once_with(idx, {"success": True, "frame": ok})


# ── M-20: CI workflows (pypi_standards.yml & ownd-smoke.yml) ────────────────


def test_ci_workflows_cover_release_branches_and_strip_ownd_in_fallback() -> None:
    """M-20: both workflows trigger on chore/release-* PRs and pypi_standards strips OWNd on git fallback."""
    pypi_wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "pypi_standards.yml").read_text(encoding="utf-8")
    )
    smoke_wf = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "ownd-smoke.yml").read_text(encoding="utf-8")
    )
    pypi_on = pypi_wf.get("on") or pypi_wf[True]
    smoke_on = smoke_wf.get("on") or smoke_wf[True]
    assert "chore/release-*" in pypi_on["pull_request"]["branches"]
    assert "chore/release-*" in smoke_on["pull_request"]["branches"]

    install_step = next(
        step
        for step in pypi_wf["jobs"]["unit-tests-and-coverage"]["steps"]
        if step.get("name") == "Install test dependencies"
    )
    run_script = install_step["run"]
    assert run_script.count('pip install ".[test]"') == 1
    assert "pip install --no-deps ." in run_script

    # Verify the exact tomllib extraction logic strips OWNd while keeping test dependencies
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extracted = [
        d
        for d in pyproject["project"]["optional-dependencies"]["test"]
        if not d.lower().startswith("ownd")
    ]
    assert extracted
    assert all(not d.lower().startswith("ownd") for d in extracted)
    assert any(d.startswith("pytest") for d in extracted)


# ── L-07: binary_sensor.py empty WHERE on malformed *#1**5*2## ──────────────


async def test_binary_sensor_rejects_empty_where_on_malformed_pir_frames(
    hass: HomeAssistant,
) -> None:
    """L-07: malformed *#1**5*2## / *#1**7*0*1*0## frames with empty WHERE do not discover a motion sensor."""
    for raw in ("*#1**5*2##", "*#1**7*0*1*0##"):
        parsed = OWNMessage.parse(raw)
        assert parsed is not None
        assert getattr(parsed, "where", None) == ""
        assert _motion_address(parsed) is None

    assert _motion_address(SimpleNamespace(dimension=5, where="   ")) is None
    assert _motion_address(SimpleNamespace(dimension=5, where=None)) is None

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10", CONF_MAC: MAC, CONF_NAME: "F454"},
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    gw = MagicMock(mac=MAC, unique_id=MAC)
    hass.data[DOMAIN] = {MAC: {CONF_ENTITY: gw, CONF_PLATFORMS: {"binary_sensor": {}}}}
    attach_runtime(hass, entry)

    added: list[Any] = []
    await binary_sensor.async_setup_entry(hass, entry, added.extend)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNMessage.parse("*#1**5*2##"))
    await hass.async_block_till_done()
    assert added == []
    await entry.async_unload(hass)


# ── L-08: config_flow_discovery.py upnp copy & placeholder firmware guard ───


async def test_ssdp_does_not_mutate_upnp_dict_and_ignores_placeholder_firmware(
    hass: HomeAssistant,
) -> None:
    """L-08: async_step_ssdp copies discovery_info.upnp and skips placeholder gateway.firmware."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        source=config_entries.SOURCE_SSDP,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_MAC: MAC,
            CONF_NAME: "MH200N",
            CONF_FRIENDLY_NAME: "MH200N",
            CONF_SSDP_LOCATION: "http://192.0.2.10:49153/description.xml",
            CONF_UDN: "uuid:mock",
            CONF_FIRMWARE: None,
        },
        unique_id=MAC,
    )
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)

    original_upnp = {
        "modelName": "MH200N",
        "serialNumber": MAC,
        "friendlyName": "MH200N",
        "UDN": "uuid:mock",
        "modelNumber": "Unknown",
    }
    snapshot = dict(original_upnp)
    announcement = SimpleNamespace(
        ssdp_usn="mock_usn",
        ssdp_st="upnp:rootdevice",
        ssdp_location="http://192.0.2.10:49153/description.xml",
        ssdp_headers={"_host": "192.0.2.10"},
        upnp=original_upnp,
    )

    with patch.object(hass.config_entries, "async_schedule_reload") as reloads:
        for placeholder in ("Unknown", "unknown", "none", "null", "   ", ""):
            original_upnp["modelNumber"] = placeholder
            snapshot["modelNumber"] = placeholder
            key = DiscoveryKey(domain="ssdp", key="uuid:mock", version=1)
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_SSDP, "discovery_key": key},
                data=announcement,
            )
            await hass.async_block_till_done()
            # Caller's upnp dictionary was never mutated in-place
            assert original_upnp == snapshot
            assert entry.data[CONF_FIRMWARE] is None

        reloads.assert_not_called()
