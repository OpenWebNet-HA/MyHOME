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
    assert unit1.device_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-328184")}
    assert unit2.device_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-328184")}
    assert unit1.device_info["name"] == "ZigBee 328184"
    assert unit1.device_info["model"] == "Dimmer Switch"
    assert unit1.device_info["via_device_id"] == "gw-reg-id"
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
    assert btn_like.device_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-700001")}
    assert btn_like.device_info["model"] == "Shutter / Cover"
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
    assert scs_ent.device_info["identifiers"] == {(DOMAIN, "00:03:50:00:11:22-4-012#9")}
    assert scs_ent.device_info["name"] == "Actuator 012#9"


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
    assert pt.device_info["name"] == "ZigBee 328184"


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
    assert cov.device_info["name"] == "ZigBee 328184"

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
    assert cal_btns[0].device_info["identifiers"] == {(DOMAIN, f"{EUI64_MAC}-zigbee-328184")}
    assert cal_btns[0].translation_key == "calibrate_travel_time"
    assert lock_btns == []
