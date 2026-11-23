"""Unit tests for Wave 2b ZigBee platforms, grouping, event-only guards, covers, and buttons."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNAutomationEvent, OWNLightingEvent, OWNMessage
from OWNd.profiles import F454Profile, Legrand3578Profile
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import button, cover, light
from custom_components.myhome.button import (
    CalibrateCoverButtonEntity,
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
    _valid_device_address,
)
from custom_components.myhome.const import (
    CONF_MEMBERS,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.cover_scope import CoverScope
from custom_components.myhome.gateway_events import GatewayEventDispatcher
from custom_components.myhome.light import MyHOMELight, _ForeignAddresses
from custom_components.myhome.light_group import MyHOMELightGroup
from custom_components.myhome.myhome_device import (
    MyHOMEEntity,
    extra_where_attributes,
    gateway_address_family,
)
from custom_components.myhome.typing_compat import device_info_to_dict
from tests.conftest import attach_runtime

EUI64_MAC = "00:03:50:00:00:01:02:03"


def _make_zigbee_gateway(
    mac: str = EUI64_MAC,
    *,
    inventory: object | None = None,
    device_registry_id: str | None = "gw-dev-1",
) -> MagicMock:
    """Create a gateway stand-in carrying Legrand3578Profile without mocking address_family."""
    profile = Legrand3578Profile()
    gw = MagicMock()
    gw.mac = mac
    gw.unique_id = mac
    gw.log_id = "[3578]"
    gw.profile = profile
    gw.is_zigbee_address_family = True
    gw.is_serial_transport = True
    gw.gateway = SimpleNamespace(profile=profile)
    gw.zigbee_inventory = inventory
    gw.device_registry_id = device_registry_id
    gw.availability_signal = f"myhome_{mac}_availability"
    gw.available = True
    gw.is_who_available.return_value = True
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw.sweep_active = False
    gw.initial_discovery_pending = False
    return gw


def test_gateway_address_family_and_extra_where_attributes() -> None:
    """Resolve ZigBee vs SCS address families safely across gateway shapes."""
    assert gateway_address_family(None) is None
    assert gateway_address_family(SimpleNamespace(is_zigbee_address_family=True)) == "zigbee"
    assert gateway_address_family(SimpleNamespace(profile=Legrand3578Profile())) == "zigbee"
    assert gateway_address_family(
        SimpleNamespace(gateway=SimpleNamespace(profile=Legrand3578Profile()))
    ) == "zigbee"
    assert gateway_address_family(SimpleNamespace(profile=F454Profile())) is None
    assert gateway_address_family(MagicMock()) is None

    zb_gw = _make_zigbee_gateway()
    assert extra_where_attributes("32818401#9", None, zb_gw) == {
        "zigbee_device_id": "328184",
        "zigbee_unit": "01",
    }
    assert extra_where_attributes("32818402#9", "01", zb_gw) == {
        "zigbee_device_id": "328184",
        "zigbee_unit": "02",
        "Int": "01",
    }
    scs_gw = SimpleNamespace(profile=F454Profile())
    assert extra_where_attributes("012#9", None, scs_gw) == {"A": "01", "PL": "2#9"}
    assert extra_where_attributes("12", "01", scs_gw) == {"A": "1", "PL": "2", "Int": "01"}


def test_myhome_device_groups_zigbee_units_and_reads_inventory_model(hass: HomeAssistant) -> None:
    """Multiple units on one ZigBee node share one HA device and unit translation."""
    inventory = SimpleNamespace(
        get_node_model=lambda addr: "Dimmer Switch" if addr == "328184" else None
    )
    gw = _make_zigbee_gateway(inventory=inventory, device_registry_id="gw-reg-id")

    unit1 = MyHOMEEntity(
        hass=hass,
        name="Light 32818401#9",
        platform="light",
        device_id="32818401#9",
        who="1",
        where="32818401#9",
        manufacturer="Legrand",
        model="Lighting Device",
        gateway=gw,
    )
    unit2 = MyHOMEEntity(
        hass=hass,
        name="Light 32818402#9",
        platform="light",
        device_id="32818402#9",
        who="1",
        where="32818402#9",
        manufacturer="Legrand",
        model="Lighting Device",
        gateway=gw,
    )

    assert unit1.unique_id == f"{EUI64_MAC}-1-32818401#9"
    assert unit2.unique_id == f"{EUI64_MAC}-1-32818402#9"
    assert unit1.device_info is not None
    assert unit2.device_info is not None
    unit1_info = device_info_to_dict(unit1.device_info)
    unit2_info = device_info_to_dict(unit2.device_info)
    assert unit1_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-328184")}
    assert unit2_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-328184")}
    assert unit1_info["name"] == "ZigBee 328184"
    assert unit1_info["model"] == "Dimmer Switch"
    assert unit1_info["via_device_id"] == "gw-reg-id"
    assert unit1.translation_key == "zigbee_unit"
    assert unit1._attr_translation_placeholders == {"unit": "1"}
    assert unit2.translation_key == "zigbee_unit"
    assert unit2._attr_translation_placeholders == {"unit": "2"}

    # Absent inventory or unknown node falls back safely to passed model;
    # explicit translation_key (e.g. companion button) is preserved.
    gw_no_inv = _make_zigbee_gateway(inventory=None, device_registry_id=None)
    btn_like = MyHOMEEntity(
        hass=hass,
        name="ZigBee 700001",
        platform="button",
        device_id="70000101#9",
        who="2",
        where="70000101#9",
        manufacturer="Legrand",
        model="Shutter / Cover",
        gateway=gw_no_inv,
        translation_key="calibrate_travel_time",
    )
    assert btn_like.device_info is not None
    btn_like_info = device_info_to_dict(btn_like.device_info)
    assert btn_like_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-700001")}
    assert btn_like_info["model"] == "Shutter / Cover"
    assert btn_like.translation_key == "calibrate_travel_time"

    # SCS gateway never treats 012#9 as ZigBee.
    scs_gw = SimpleNamespace(
        mac="00:03:50:00:11:22",
        profile=F454Profile(),
        device_registry_id=None,
    )
    scs_ent = MyHOMEEntity(
        hass=hass,
        name="Actuator 012#9",
        platform="climate",
        device_id="012#9",
        who="4",
        where="012#9",
        manufacturer=None,
        model=None,
        gateway=scs_gw,  # type: ignore[arg-type]
    )
    assert scs_ent.device_info is not None
    scs_info = device_info_to_dict(scs_ent.device_info)
    assert scs_info["identifiers"] == {(DOMAIN, "00:03:50:00:11:22-4-012#9")}
    assert scs_info["name"] == "Actuator 012#9"


async def test_zigbee_light_group_and_point_attributes(hass: HomeAssistant) -> None:
    """ZigBee group #25#9 builds *1*1*#25#9## cleanly and point light emits zigbee attrs."""
    gw = _make_zigbee_gateway()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {
        EUI64_MAC: {
            "entity": gw,
            CONF_PLATFORMS: {
                "light": {
                    "#25#9": {
                        CONF_WHERE: "#25#9",
                        CONF_NAME: "Living ZigBee Group",
                        CONF_MEMBERS: [],
                    },
                    "32818401#9": {
                        CONF_WHERE: "32818401#9",
                    },
                }
            },
        }
    }
    attach_runtime(hass, entry, EUI64_MAC, gw)

    added: list[object] = []
    await light.async_setup_entry(hass, entry, added.extend)

    groups = [e for e in added if isinstance(e, MyHOMELightGroup)]
    lights = [e for e in added if isinstance(e, MyHOMELight)]
    assert len(groups) == 1
    assert len(lights) == 1

    grp = groups[0]
    assert grp._group == 25
    assert grp._full_where == "#25#9"
    grp.hass = hass
    grp.entity_id = "light.living_zigbee_group"
    await grp.async_turn_on()
    assert str(gw.send.await_args_list[-1].args[0]) == "*1*1*#25#9##"
    await grp.async_turn_off()
    assert str(gw.send.await_args_list[-1].args[0]) == "*1*0*#25#9##"

    pt = lights[0]
    assert pt.extra_state_attributes == {
        "zigbee_device_id": "328184",
        "zigbee_unit": "01",
    }
    assert "A" not in pt.extra_state_attributes
    assert "PL" not in pt.extra_state_attributes
    assert pt.device_info is not None
    assert device_info_to_dict(pt.device_info)["name"] == "ZigBee 328184"


async def test_zigbee_what_32_34_39_event_only_in_light_and_dispatcher(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WHAT 32/34/39 never auto-discover lights, flip/warn in handle_event, or call handle_ptp_echo."""
    gw = _make_zigbee_gateway()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {
        EUI64_MAC: {
            "entity": gw,
            CONF_PLATFORMS: {
                "light": {
                    "32818401#9": {CONF_WHERE: "32818401#9"},
                }
            },
        }
    }
    attach_runtime(hass, entry, EUI64_MAC, gw)

    added: list[object] = []
    with patch("custom_components.myhome.discovery.PlatformDiscovery.listen"):
        await light.async_setup_entry(hass, entry, added.extend)

    existing_light = next(e for e in added if isinstance(e, MyHOMELight))
    existing_light.hass = hass
    existing_light.entity_id = "light.zigbee_328184_unit_1"
    existing_light._attr_is_on = False

    # Capture dispatcher calls by exercising PlatformDiscovery directly via hass bus
    toggle_unknown = OWNMessage.parse("*1*32*702053501#9##", gateway=gw.profile)
    motion_unknown = OWNMessage.parse("*1*34*702053501#9##", gateway=gw.profile)
    motion_end_unknown = OWNMessage.parse("*1*39*702053501#9##", gateway=gw.profile)
    assert isinstance(toggle_unknown, OWNLightingEvent)
    assert isinstance(motion_unknown, OWNLightingEvent)
    assert isinstance(motion_end_unknown, OWNLightingEvent)

    assert _ForeignAddresses.is_sensor_frame(SimpleNamespace(what=34)) is True
    assert _ForeignAddresses.is_sensor_frame(SimpleNamespace(what=39)) is True
    assert _ForeignAddresses.is_sensor_frame(toggle_unknown) is False

    # Build a second setup with real listen() to test route_foreign -> zero auto-discovery
    added2: list[object] = []
    entry2 = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry2.add_to_hass(hass)
    attach_runtime(hass, entry2, EUI64_MAC, gw)
    await light.async_setup_entry(hass, entry2, added2.extend)
    count_before = len(added2)

    from homeassistant.helpers.dispatcher import async_dispatcher_send

    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", toggle_unknown)
    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", motion_unknown)
    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", motion_end_unknown)
    await hass.async_block_till_done()
    assert len(added2) == count_before

    # Existing light handle_event on WHAT 32/34/39 must not flip, warn, or set unknown_state
    toggle_known = OWNMessage.parse("*1*32*32818401#9##", gateway=gw.profile)
    motion_known = OWNMessage.parse("*1*34*32818401#9##", gateway=gw.profile)
    motion_end_known = OWNMessage.parse("*1*39*32818401#9##", gateway=gw.profile)
    assert isinstance(toggle_known, OWNLightingEvent)
    assert isinstance(motion_known, OWNLightingEvent)
    assert isinstance(motion_end_known, OWNLightingEvent)

    caplog.clear()
    with caplog.at_level("WARNING"):
        existing_light.handle_event(toggle_known)
        existing_light.handle_event(motion_known)
        existing_light.handle_event(motion_end_known)
    assert existing_light._attr_is_on is False
    assert "unknown_state" not in existing_light._attr_extra_state_attributes
    assert [r for r in caplog.records if r.levelno >= 30] == []

    # GatewayEventDispatcher: WHAT 32 fires myhome_zigbee_light_event and skips handle_ptp_echo;
    # WHAT 34/39 also skip handle_ptp_echo; real actuator *1*1*32818401#9## calls handle_ptp_echo.
    events: list[dict[str, object]] = []
    hass.bus.async_listen("myhome_zigbee_light_event", lambda ev: events.append(dict(ev.data)))

    dispatcher_gw = MagicMock()
    dispatcher_gw.hass = hass
    dispatcher_gw.mac = EUI64_MAC
    dispatcher_gw.log_id = "[3578]"
    dispatcher_gw.gateway = SimpleNamespace(host="", profile=gw.profile)
    dispatcher_gw.generate_events = False
    dispatcher_gw.is_standby = False
    dispatcher_gw.is_secondary = False
    dispatcher_gw.delegated_away_whos = set()
    dispatcher_gw._resync_manager = MagicMock()
    dispatcher = GatewayEventDispatcher(dispatcher_gw)

    await dispatcher.process_message(toggle_known)
    await dispatcher.process_message(motion_known)
    await dispatcher.process_message(motion_end_known)
    await hass.async_block_till_done()

    dispatcher_gw._resync_manager.handle_ptp_echo.assert_not_called()
    assert events == [
        {
            "message": "*1*32*32818401#9##",
            "where": "32818401#9",
            "event": "toggle",
        }
    ]

    actuator_on = OWNMessage.parse("*1*1*32818401#9##", gateway=gw.profile)
    await dispatcher.process_message(actuator_on)
    dispatcher_gw._resync_manager.handle_ptp_echo.assert_called_once_with(actuator_on)


async def test_zigbee_cover_attributes_point_matching_and_scope(hass: HomeAssistant) -> None:
    """ZigBee cover emits zigbee attrs, matches zigbee_where, and treats 0#9/0#00#9 as general."""
    gw = _make_zigbee_gateway()
    cov = MyHOMECover(
        hass=hass,
        name="Cover 32818401#9",
        entity_name="",
        device_id="32818401#9",
        who="2",
        where="32818401#9",
        interface=None,  # type: ignore[arg-type]
        advanced=False,
        manufacturer="Legrand",
        model="Shutter Switch",
        gateway=gw,
    )
    assert cov._attr_extra_state_attributes["zigbee_device_id"] == "328184"
    assert cov._attr_extra_state_attributes["zigbee_unit"] == "01"
    assert "A" not in cov._attr_extra_state_attributes
    assert "PL" not in cov._attr_extra_state_attributes
    assert cov.device_info is not None
    assert device_info_to_dict(cov.device_info)["name"] == "ZigBee 328184"

    own_msg = OWNMessage.parse("*2*1*32818401#9##", gateway=gw.profile)
    other_msg = OWNMessage.parse("*2*1*32818402#9##", gateway=gw.profile)
    gen_msg = OWNMessage.parse("*2*1*0#00#9##", gateway=gw.profile)
    assert isinstance(own_msg, OWNAutomationEvent)
    assert isinstance(other_msg, OWNAutomationEvent)
    assert isinstance(gen_msg, OWNAutomationEvent)

    assert cov._is_own_point_message(own_msg) is True
    assert cov._is_own_point_message(other_msg) is False
    assert cov._is_own_point_message(gen_msg) is False
    assert cov._is_own_point_message(SimpleNamespace(where=True)) is False  # type: ignore[arg-type]

    # Scope cover stop echo matching via effective_message_where
    scope_grp = CoverScope.of("#25#9", members=["32818401#9"])
    assert scope_grp is not None
    assert scope_grp.kind == "group"
    assert scope_grp.contains(cov) is True

    scope_gen_bare = CoverScope.of("0#9")
    scope_gen_unit = CoverScope.of("0#00#9")
    assert scope_gen_bare is not None and scope_gen_bare.kind == "general"
    assert scope_gen_unit is not None and scope_gen_unit.kind == "general"
    assert scope_gen_unit.contains(cov) is True
    assert CoverScope.of("32818401#9") is None

    # Exercise _handle_echo pending stop path with ZigBee scope echo
    from custom_components.myhome.cover_scope import MyHOMEScopeCover

    scov = MyHOMEScopeCover(
        hass=hass,
        name="Group 25",
        entity_name="",
        device_id="#25#9",
        who="2",
        where="#25#9",
        interface=None,
        advanced=False,
        manufacturer="Legrand",
        model="Shutter",
        gateway=gw,
        scope=scope_grp,
    )
    scov._pending_cmd = "stop"
    scov._echo_until = 9e12
    grp_stop = OWNMessage.parse("*2*0*#25#9##", gateway=gw.profile)
    assert isinstance(grp_stop, OWNMessage)
    assert scov._handle_echo(grp_stop, 100.0) is True

    # Ensure cover async_setup_entry builds a ZigBee cover cleanly too
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry.add_to_hass(hass)
    hass.data[DOMAIN] = {
        EUI64_MAC: {
            "entity": gw,
            CONF_PLATFORMS: {"cover": {"32818401#9": {CONF_WHERE: "32818401#9"}}},
        }
    }
    attach_runtime(hass, entry, EUI64_MAC, gw)
    added_covers: list[object] = []
    await cover.async_setup_entry(hass, entry, added_covers.extend)
    assert any(isinstance(c, MyHOMECover) and c._where == "32818401#9" for c in added_covers)


async def test_zigbee_buttons_keep_cover_calibration_and_skip_who14_locks(
    hass: HomeAssistant,
) -> None:
    """ZigBee covers retain calibration buttons across restart, while WHO 14 locks are omitted."""
    assert _valid_device_address("32818401#9") is True
    assert _valid_device_address("32818401#9", address_family="zigbee") is True
    assert _valid_device_address("32818401#9", address_family="scs") is False
    assert _valid_device_address("12#4#01", address_family="scs") is True
    assert _valid_device_address("1-06") is False

    gw = _make_zigbee_gateway()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry.add_to_hass(hass)

    reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    zb_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{EUI64_MAC}-zigbee-328184")},
        name="ZigBee 328184",
    )
    reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{EUI64_MAC}-2-32818401#9",
        config_entry=entry,
        original_name="Unit 1",
        device_id=zb_dev.id,
    )
    reg.async_get_or_create(
        "light",
        DOMAIN,
        f"{EUI64_MAC}-1-32818402#9",
        config_entry=entry,
        original_name="Unit 2",
        device_id=zb_dev.id,
    )
    reg.async_get_or_create(
        "switch",
        DOMAIN,
        f"{EUI64_MAC}-1-70000101#9",
        config_entry=entry,
        original_name="Unit 1",
    )

    hass.data[DOMAIN] = {
        EUI64_MAC: {
            "entity": gw,
            CONF_PLATFORMS: {
                "button": {
                    "zb_light": {CONF_WHO: "1", CONF_WHERE: "32818402#9"},
                },
                "switch": {
                    "70000201#9": {CONF_WHO: "1", CONF_WHERE: "70000201#9"},
                },
            },
        }
    }
    attach_runtime(hass, entry, EUI64_MAC, gw)

    added: list[object] = []
    await button.async_setup_entry(hass, entry, added.extend)

    cal_btns = [e for e in added if isinstance(e, CalibrateCoverButtonEntity)]
    lock_btns = [
        e
        for e in added
        if isinstance(e, (DisableCommandButtonEntity, EnableCommandButtonEntity))
    ]
    assert len(cal_btns) == 1
    assert cal_btns[0].unique_id == f"{EUI64_MAC}-2-32818401#9-calibrate"
    assert cal_btns[0].device_info is not None
    assert device_info_to_dict(cal_btns[0].device_info)["identifiers"] == {
        (DOMAIN, f"{EUI64_MAC}-zigbee-328184")
    }
    assert cal_btns[0].translation_key == "calibrate_travel_time"
    assert lock_btns == []


async def test_zigbee_dimmer_curve_900ms_debounce_1pct_status_and_dynamic_promotion(
    hass: HomeAssistant,
    freezer,
) -> None:
    """ZigBee dimmers use 1/5..100% curve, debounce + / - taps (900ms), decode status *1*1* as 1%, and promote dynamically."""
    from datetime import timedelta

    from homeassistant.components.light import (
        ATTR_BRIGHTNESS,
        ATTR_BRIGHTNESS_PCT,
        ATTR_TRANSITION,
        ColorMode,
        LightEntityFeature,
    )
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    is_dim_flag = False
    reachable_map: dict[str, dict[str, object]] = {
        "7009209": {"reachable": True, "units": {}},
        "7000535": {"reachable": True, "units": {}},
    }
    inv = SimpleNamespace(
        products=reachable_map,
        get_node_model=lambda addr: "Dimmer Switch" if addr == "7009209" else "On/Off Switch",
        is_dimmer_where=lambda w: w == "700920901#9" and is_dim_flag,
        is_non_actuator_where=lambda w: w == "700888801#9",
    )
    gw = _make_zigbee_gateway(inventory=inv)
    gw.sweep_active = True
    gw.initial_discovery_pending = True

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry.add_to_hass(hass)

    # Seed stale non-actuator registry entries in both light & cover to verify ghost pruning
    reg = er.async_get(hass)
    stale_l = reg.async_get_or_create(
        "light", DOMAIN, f"{EUI64_MAC}-1-700888801#9", config_entry=entry
    )
    stale_c = reg.async_get_or_create(
        "cover", DOMAIN, f"{EUI64_MAC}-2-700888801#9", config_entry=entry
    )

    hass.data[DOMAIN] = {
        EUI64_MAC: {
            "entity": gw,
            CONF_PLATFORMS: {
                "light": {
                    "#25#9": {
                        CONF_WHERE: "#25#9",
                        CONF_NAME: "Dimmer Group",
                        "dimmable": True,
                        CONF_MEMBERS: [],
                    },
                },
                "cover": {},
            },
        }
    }
    attach_runtime(hass, entry, EUI64_MAC, gw)

    added_lights: list[object] = []
    added_covers: list[object] = []
    await light.async_setup_entry(hass, entry, added_lights.extend)
    await cover.async_setup_entry(hass, entry, added_covers.extend)

    # Stale wireless battery control registry entries were ghost-pruned on startup!
    assert reg.async_get(stale_l.entity_id) is None
    assert reg.async_get(stale_c.entity_id) is None

    # Bus frames from wireless battery control 700888801#9 are blocked in both light & cover
    ctrl_l = OWNMessage.parse("*1*1*700888801#9##", gateway=gw.profile)
    ctrl_c = OWNMessage.parse("*2*1*700888801#9##", gateway=gw.profile)
    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", ctrl_l)
    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", ctrl_c)
    await hass.async_block_till_done()
    assert not any(
        isinstance(e, MyHOMELight) and e._where == "700888801#9" for e in added_lights
    )
    assert not any(
        isinstance(e, MyHOMECover) and e._where == "700888801#9" for e in added_covers
    )

    # Discover On/Off switch 700053501#9 and initially-untyped dimmer 700920901#9 via *1*1*...
    sw_on = OWNMessage.parse("*1*1*700053501#9##", gateway=gw.profile)
    dim_on = OWNMessage.parse("*1*1*700920901#9##", gateway=gw.profile)
    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", sw_on)
    async_dispatcher_send(hass, f"myhome_message_{EUI64_MAC}", dim_on)
    await hass.async_block_till_done()

    sw_ent = next(
        e for e in added_lights if isinstance(e, MyHOMELight) and e._where == "700053501#9"
    )
    dim_ent = next(
        e for e in added_lights if isinstance(e, MyHOMELight) and e._where == "700920901#9"
    )
    grp_ent = next(e for e in added_lights if isinstance(e, MyHOMELightGroup))

    # Startup deduplication & no Dimension 1 dimmer probe on ZigBee!
    assert sw_ent._poll_on_add is False
    assert dim_ent._poll_on_add is False
    assert sw_ent._probe_dimmer_on_add is False
    assert dim_ent._probe_dimmer_on_add is False
    assert sw_ent.supported_color_modes == {ColorMode.ONOFF}
    assert dim_ent.supported_color_modes == {ColorMode.ONOFF}

    for ent, eid in (
        (sw_ent, "light.zigbee_7000535_unit_1"),
        (dim_ent, "light.zigbee_7009209_unit_1"),
        (grp_ent, "light.dimmer_group"),
    ):
        ent.hass = hass
        ent.entity_id = eid
        ent.async_write_ha_state = MagicMock()

    sw_ent._register_availability_listener()
    dim_ent._register_availability_listener()

    # On/Off switch receiving status-reply *1*1*700053501#9## MUST stay ColorMode.ONOFF!
    sw_status_reply = OWNMessage.parse(
        "*1*1*700053501#9##", gateway=gw.profile, is_status_reply=True
    )
    assert isinstance(sw_status_reply, OWNLightingEvent)
    sw_ent.handle_event(sw_status_reply)
    assert sw_ent.supported_color_modes == {ColorMode.ONOFF}
    assert sw_ent._attr_brightness is None
    assert sw_ent._zigbee_what1_debounce_unsub is None

    # Dynamic promotion when DIM 66 reveals 700920901#9 is a dimmer (DEV_ID 258)
    is_dim_flag = True
    dim_ent._handle_availability_update()
    assert dim_ent.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert not (dim_ent.supported_features & LightEntityFeature.TRANSITION)
    assert dim_ent._should_use_software_stepped(2.0) is False

    # Entity availability tracks reachable_map[addr]["reachable"]
    assert dim_ent.available is True
    reachable_map["7009209"]["reachable"] = False
    assert dim_ent.available is False
    reachable_map["7009209"]["reachable"] = True
    assert dim_ent.available is True

    # Actuation across ZigBee dimmer presets: non-optimistic TX & coarse WHAT frames!
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()
    dim_ent._attr_is_on = False
    dim_ent._attr_brightness_pct = None
    for pct, expected_what in (
        (1, 2),
        (5, 2),
        (10, 3),
        (20, 4),
        (30, 5),
        (40, 6),
        (50, 7),
        (60, 8),
        (75, 9),
        (100, 10),
    ):
        await dim_ent.async_turn_on(**{ATTR_BRIGHTNESS_PCT: pct, ATTR_TRANSITION: 3})
        assert str(gw.send.await_args_list[-1].args[0]) == f"*1*{expected_what}*700920901#9##"
    # Non-optimistic: still False / None until RF echo arrives!
    assert dim_ent._attr_is_on is False
    assert dim_ent._attr_brightness_pct is None

    # Also test _set_brightness_instant, async_turn_on_timed, plain async_turn_on, async_update
    await dim_ent._set_brightness_instant(30)
    assert str(gw.send.await_args_list[-1].args[0]) == "*1*5*700920901#9##"
    await dim_ent.async_turn_on_timed(seconds=30, brightness_pct=50)
    assert [str(c.args[0]) for c in gw.send.await_args_list[-2:]] == [
        "*1*7*700920901#9##",
        "*1*17*700920901#9##",
    ]
    assert dim_ent._attr_is_on is False  # non-optimistic on ZigBee!

    gw.send_status_request.reset_mock()
    await dim_ent.async_turn_on()
    assert str(gw.send.await_args_list[-1].args[0]) == "*1*1*700920901#9##"
    gw.send_status_request.assert_not_awaited()  # no 50ms Dimension 1 poll!

    await dim_ent.async_update()
    assert str(gw.send_status_request.await_args_list[-1].args[0]) == "*#1*700920901#9##"

    # RX preset curve verification (WHAT 2..10 -> 5/10/20/30/40/50/60/75/100%)
    gw.send_status_request.reset_mock()
    for what_code, expected_pct in (
        (2, 5),
        (3, 10),
        (4, 20),
        (5, 30),
        (6, 40),
        (7, 50),
        (8, 60),
        (9, 75),
        (10, 100),
    ):
        ev = OWNMessage.parse(f"*1*{what_code}*700920901#9##", gateway=gw.profile)
        assert isinstance(ev, OWNLightingEvent)
        dim_ent.handle_event(ev)
        assert dim_ent._attr_brightness_pct == expected_pct
        assert dim_ent._bus_state_complete is True
    # Coarse ZigBee dimmer never asks Dimension 1 (_request_brightness_refresh)!
    gw.send_status_request.assert_not_awaited()

    # Rapid + / - button tap debounce: 5 taps of *1*1*... -> 1 status poll after 900 ms!
    gw.send_status_request.reset_mock()
    tap_ev = OWNMessage.parse("*1*1*700920901#9##", gateway=gw.profile)
    assert isinstance(tap_ev, OWNLightingEvent)
    for _step in range(5):
        dim_ent.handle_event(tap_ev)
        freezer.tick(timedelta(milliseconds=150))
        async_fire_time_changed(hass)
    gw.send_status_request.assert_not_awaited()

    # Fire past 900 ms after last tap -> exactly 1 *#1*700920901#9## poll!
    freezer.tick(timedelta(milliseconds=900))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert gw.send_status_request.await_count == 1
    assert str(gw.send_status_request.await_args_list[-1].args[0]) == "*#1*700920901#9##"

    # Status reply *1*1*700920901#9## (is_status_reply=True) -> applies 1% with ZERO follow-up polls!
    gw.send_status_request.reset_mock()
    reply_1pct = OWNMessage.parse(
        "*1*1*700920901#9##", gateway=gw.profile, is_status_reply=True
    )
    assert isinstance(reply_1pct, OWNLightingEvent)
    dim_ent.handle_event(reply_1pct)
    assert dim_ent._attr_brightness_pct == 1
    assert dim_ent._attr_is_on is True
    assert dim_ent._zigbee_what1_debounce_unsub is None
    freezer.tick(timedelta(seconds=2.0))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    gw.send_status_request.assert_not_awaited()

    # Mock gateway fallback: untagged *1*1*... emitted synchronously DURING send_status_request
    # is consumed as 1%, and finally: clears _zigbee_awaiting_status_until so a subsequent
    # physical wall + tap (*1*1*..., is_status_reply=False) is NOT misclassified as 1%!
    dim_ent._apply_brightness_state(50, is_on=True)

    async def _emit_untagged_during_poll(_cmd: object) -> bool:
        dim_ent.handle_event(tap_ev)
        return True

    gw.send_status_request.side_effect = _emit_untagged_during_poll
    await dim_ent.async_update()
    assert dim_ent._attr_brightness_pct == 1
    assert dim_ent._zigbee_awaiting_status_until == 0.0
    gw.send_status_request.side_effect = None
    gw.send_status_request.reset_mock()

    # After async_update() returns (even without emitting a status frame), a physical wall tap
    # (*1*1*..., is_status_reply=False) must NOT be consumed as 1%; it must arm the 900ms poll!
    dim_ent._apply_brightness_state(60, is_on=True)
    await dim_ent.async_update()
    assert dim_ent._zigbee_awaiting_status_until == 0.0
    dim_ent.handle_event(tap_ev)
    assert dim_ent._attr_brightness_pct == 60
    assert dim_ent._zigbee_what1_debounce_unsub is not None
    dim_ent._cancel_zigbee_what1_debounce()

    # Real AsyncSerialTransport on serial_runtime: unsolicited *1*1*... (is_status_reply=False)
    # arriving while send_status_request is queued ahead of wire TX is NOT misclassified as 1%!
    from OWNd.transport import AsyncSerialTransport

    gw.serial_runtime = SimpleNamespace(
        transport=AsyncSerialTransport("/dev/ttyUSB0", handshake=False, reconnect=False)
    )

    async def _tap_while_queued(_cmd: object) -> bool:
        dim_ent.handle_event(tap_ev)
        return True

    gw.send_status_request.side_effect = _tap_while_queued
    await dim_ent.async_update()
    assert dim_ent._attr_brightness_pct == 60
    assert dim_ent._zigbee_what1_debounce_unsub is not None
    dim_ent._cancel_zigbee_what1_debounce()
    gw.send_status_request.side_effect = None
    gw.serial_runtime = None

    # Cancellation of pending debounce on *1*0*... and async_will_remove_from_hass
    dim_ent.handle_event(tap_ev)
    assert dim_ent._zigbee_what1_debounce_unsub is not None
    off_ev = OWNMessage.parse("*1*0*700920901#9##", gateway=gw.profile)
    assert isinstance(off_ev, OWNLightingEvent)
    dim_ent.handle_event(off_ev)
    assert dim_ent._zigbee_what1_debounce_unsub is None

    dim_ent.handle_event(tap_ev)
    await dim_ent.async_turn_off()
    assert dim_ent._zigbee_what1_debounce_unsub is None

    dim_ent.handle_event(tap_ev)
    await dim_ent.async_will_remove_from_hass()
    assert dim_ent._zigbee_what1_debounce_unsub is None

    # Also cover build() when inv.is_dimmer_where is already True & _schedule_zigbee_what1_debounce_poll when hass=None
    detached_dim = MyHOMELight(
        hass=None,
        name="Detached",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="700920901#9",
        who="1",
        where="700920901#9",
        interface=None,
        dimmable=True,
        manufacturer="Legrand / BTicino",
        model="Dimmer",
        gateway=gw,
    )
    detached_dim._schedule_zigbee_what1_debounce_poll()
    assert detached_dim._zigbee_what1_debounce_unsub is None

    entry2 = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: EUI64_MAC})
    entry2.add_to_hass(hass)
    reg.async_get_or_create(
        "light", DOMAIN, f"{EUI64_MAC}-1-700920901#9", config_entry=entry2
    )
    reg.async_get_or_create(
        "cover", DOMAIN, f"{EUI64_MAC}-2-700888801#9", config_entry=entry2
    )
    hass.data[DOMAIN][EUI64_MAC][CONF_PLATFORMS]["light"]["700888801#9"] = {
        CONF_WHERE: "700888801#9"
    }
    hass.data[DOMAIN][EUI64_MAC][CONF_PLATFORMS]["cover"]["700888801#9"] = {
        CONF_WHERE: "700888801#9",
        CONF_WHO: "2",
    }
    added_lights2: list[object] = []
    added_covers2: list[object] = []
    attach_runtime(hass, entry2, EUI64_MAC, gw)
    await light.async_setup_entry(hass, entry2, added_lights2.extend)
    await cover.async_setup_entry(hass, entry2, added_covers2.extend)
    assert any(
        isinstance(e, MyHOMELight)
        and e._where == "700920901#9"
        and ColorMode.BRIGHTNESS in (e.supported_color_modes or set())
        for e in added_lights2
    )

    # ZigBee light group brightness TX, RX preset, and async_update skipping DIM 1
    gw.send.reset_mock()
    gw.send_status_request.reset_mock()
    await grp_ent.async_turn_on(**{ATTR_BRIGHTNESS: 77})
    assert str(gw.send.await_args_list[-1].args[0]) == "*1*5*#25#9##"
    grp_preset = OWNMessage.parse("*1*8*#25#9##", gateway=gw.profile)
    grp_ent.handle_event(grp_preset)
    assert grp_ent._last_brightness_pct == 60
    await grp_ent.async_update()
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list] == [
        "*#1*#25#9##"
    ]

    # Cover echo window: 4.0 s on ZigBee vs 1.5 s on SCS
    from custom_components.myhome.cover import ECHO_WINDOW, ZIGBEE_ECHO_WINDOW

    zb_cov = MyHOMECover(
        hass=hass,
        name="ZB Cover",
        entity_name="",
        device_id="700053501#9",
        who="2",
        where="700053501#9",
        interface=None,  # type: ignore[arg-type]
        advanced=False,
        manufacturer="Legrand / BTicino",
        model="Shutter",
        gateway=gw,
    )
    scs_gw = SimpleNamespace(
        mac="00:03:50:00:11:22",
        unique_id="00:03:50:00:11:22",
        log_id="[F454]",
        profile=F454Profile(),
        device_registry_id=None,
        availability_signal="avail",
        is_who_available=lambda _w: True,
    )
    scs_cov = MyHOMECover(
        hass=hass,
        name="SCS Cover",
        entity_name="",
        device_id="12",
        who="2",
        where="12",
        interface=None,  # type: ignore[arg-type]
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=scs_gw,  # type: ignore[arg-type]
    )
    assert zb_cov._echo_window == ZIGBEE_ECHO_WINDOW == 4.0
    assert scs_cov._echo_window == ECHO_WINDOW == 1.5

