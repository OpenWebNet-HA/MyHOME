"""Regression tests for the runtime defects collected in #566."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant
from OWNd.message import OWNMessage

from custom_components.myhome.alarm_control_panel import MyHOMEAlarmControlPanel
from custom_components.myhome.sensor import MyHOMETemperatureSensor


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:55:55"
    gw.unique_id = gw.mac
    gw.log_id = "[Test]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


# --- alarm: the central unit takes no code over the bus --------------------


def test_alarm_arm_needs_no_code(hass: HomeAssistant, gateway) -> None:
    alarm = MyHOMEAlarmControlPanel(
        hass=hass, name="Alarm", entity_name="Alarm", device_id="0", who="5", where="0",
        manufacturer="BTicino", model="3486", gateway=gateway,
    )
    alarm.entity_id = "alarm_control_panel.alarm"
    assert alarm.code_arm_required is False
    assert alarm.check_code_arm_required(None) is None


# --- WHO 4: temperatures below zero ----------------------------------------


@pytest.mark.parametrize(
    ("frame", "expected"),
    [("*#4*1*0*0055##", 5.5), ("*#4*1*0*1055##", -5.5), ("*#4*1*0*1005##", -0.5)],
)
def test_temperature_sensor_reads_the_sign(hass: HomeAssistant, gateway, frame, expected) -> None:
    sensor = MyHOMETemperatureSensor(
        hass=hass, name="Zone 1", device_id="4-1", who="4", where="1",
        device_class=SensorDeviceClass.TEMPERATURE, manufacturer="BTicino", model="Probe", gateway=gateway,
    )
    sensor.async_schedule_update_ha_state = MagicMock()
    sensor.handle_event(OWNMessage.parse(frame))
    assert sensor._attr_native_value == expected


def test_climate_reads_the_sign_of_the_zone_temperature(hass: HomeAssistant, gateway) -> None:
    from custom_components.myhome.climate import MyHOMEClimate

    climate = MyHOMEClimate(
        hass=hass, name="Zone", device_id="1", who="4", where="1", heating=True, cooling=False,
        fan=False, standalone=True, central=False, manufacturer="B", model="M", gateway=gateway,
    )
    climate.entity_id = "climate.zone"
    climate.async_schedule_update_ha_state = MagicMock()
    climate.handle_event(OWNMessage.parse("*#4*1*0*1055##"))
    assert climate.current_temperature == -5.5
    climate.handle_event(OWNMessage.parse("*#4*1*0*0215##"))
    assert climate.current_temperature == 21.5


# --- lights ----------------------------------------------------------------


def _light(hass: HomeAssistant, gateway):
    from custom_components.myhome.light import MyHOMELight

    gateway.config_entry = MagicMock(options={"transition_mode": "native"})
    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon=None, icon_on=None, device_id="12", who="1",
        where="12", interface=None, dimmable=True, manufacturer="B", model="M", gateway=gateway,
    )
    light.async_schedule_update_ha_state = MagicMock()
    return light


async def test_brightness_one_of_255_is_on_at_minimum_not_off(hass: HomeAssistant, gateway) -> None:
    light = _light(hass, gateway)
    await light.async_turn_on(brightness=1)
    frames = [str(call.args[0]) for call in gateway.send.await_args_list]
    assert frames, "nothing was sent"
    assert "*1*0*12##" not in frames
    assert light._attr_is_on is not False


@pytest.mark.parametrize(("what", "percent"), [(2, 20), (5, 50), (10, 100)])
def test_wall_dimmer_preset_is_the_level(hass: HomeAssistant, gateway, what, percent) -> None:
    light = _light(hass, gateway)
    light.handle_event(OWNMessage.parse(f"*1*{what}*12##"))
    assert light._attr_is_on is True
    assert light._attr_brightness_pct == percent
    assert light._attr_brightness == round(percent * 255 / 100)


# --- light group -----------------------------------------------------------


def _group(hass: HomeAssistant, gateway):
    from custom_components.myhome.light_group import MyHOMELightGroup

    group = MyHOMELightGroup(
        hass, "Group 6", "dev1", 6, gateway, [], dimmable=True, color_temp=True, rgb=True, hs=False,
    )
    group.hass = hass
    group.entity_id = "light.group_6"
    group.async_schedule_update_ha_state = MagicMock()
    return group


@pytest.mark.parametrize(("value", "brightness"), [(150, 128), (200, 255), (101, 3)])
def test_group_dimension_1_carries_level_plus_100(hass: HomeAssistant, gateway, value, brightness) -> None:
    group = _group(hass, gateway)
    group.handle_event(OWNMessage.parse(f"*#1*#6*1*{value}*0##"))
    assert group.brightness == brightness


def test_group_ignores_out_of_range_dimension_1(hass: HomeAssistant, gateway) -> None:
    group = _group(hass, gateway)
    group.handle_event(OWNMessage.parse("*#1*#6*1*150*0##"))
    group.handle_event(OWNMessage.parse("*#1*#6*1*50*0##"))
    assert group.brightness == 128


def test_group_ignores_the_unsupported_colour_sentinels(hass: HomeAssistant, gateway) -> None:
    group = _group(hass, gateway)
    group.handle_event(OWNMessage.parse("*#1*#6*14*1##"))
    assert group.color_temp_kelvin is None
    group.handle_event(OWNMessage.parse("*#1*#6*12*511*127*255##"))
    assert group.hs_color is None


# --- media player: repeated mute -------------------------------------------


async def test_repeated_mute_keeps_the_volume_from_before_the_first(hass: HomeAssistant, gateway) -> None:
    from custom_components.myhome.const import DOMAIN  # noqa: F401
    from custom_components.myhome.media_player import MyHOMEMediaPlayer

    player = MyHOMEMediaPlayer(
        hass=hass, name="Zone", entity_name=None, device_id="1#16", who="16", where="1",
        manufacturer="BTicino", model="Audio", gateway=gateway,
    )
    player.hass = hass
    player.entity_id = "media_player.zone"
    player.async_schedule_update_ha_state = MagicMock()
    player.async_set_volume_level = AsyncMock(side_effect=lambda v: setattr(player, "_attr_volume_level", v))
    player._attr_volume_level = 0.4

    await player.async_mute_volume(True)
    await player.async_mute_volume(True)
    await player.async_mute_volume(False)

    assert player.async_set_volume_level.await_args_list[-1].args[0] == 0.4


# --- switch: a frame without a state ---------------------------------------


def test_switch_keeps_its_state_on_a_frame_without_one(hass: HomeAssistant, gateway) -> None:
    from custom_components.myhome.switch import MyHOMESwitch

    switch = MyHOMESwitch(
        hass=hass, name="S", entity_name="S", icon=None, icon_on=None, device_id="31", who="1",
        where="31", interface=None, device_class=None, manufacturer="B", model="M", gateway=gateway,
    )
    switch.hass = hass
    switch.entity_id = "switch.s"
    switch.async_schedule_update_ha_state = MagicMock()
    switch.handle_event(OWNMessage.parse("*1*1*31##"))
    assert switch.is_on is True
    switch.handle_event(OWNMessage.parse("*#1*31*5*1*0##"))  # a dimension frame: no on/off
    assert switch.is_on is True


# --- configuration.yaml ----------------------------------------------------


async def test_a_myhome_key_in_configuration_yaml_does_not_abort_the_integration(hass: HomeAssistant) -> None:
    from custom_components.myhome import async_setup
    from custom_components.myhome.const import DOMAIN

    assert await async_setup(hass, {DOMAIN: {}}) is True


# --- removal ---------------------------------------------------------------


async def test_removing_an_entry_removes_its_repair_issues(hass: HomeAssistant) -> None:
    from homeassistant.helpers import issue_registry as ir
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome import async_remove_entry
    from custom_components.myhome.const import DOMAIN
    from custom_components.myhome.repairs import async_create_primary_missing_issue

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:00:12:34"}, entry_id="gone")
    other = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:00:12:35"}, entry_id="stays")
    entry.add_to_hass(hass)
    other.add_to_hass(hass)
    async_create_primary_missing_issue(hass, "gone", "Gateway", "aa")
    async_create_primary_missing_issue(hass, "stays", "Other", "aa")
    ir.async_create_issue(
        hass, DOMAIN, "incompatible_decoder_platform_gone_media_player_x", is_fixable=False,
        severity=ir.IssueSeverity.WARNING, translation_key="incompatible_decoder_platform",
        translation_placeholders={"decoder": "x", "platform": "y"},
    )

    await async_remove_entry(hass, entry)

    remaining = {issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN}
    assert not [issue_id for issue_id in remaining if "gone" in issue_id]


# --- resync echo -----------------------------------------------------------


@pytest.mark.parametrize(
    ("frame", "is_echo"),
    [("*1*1*12##", True), ("*1*34*12##", False), ("*#1*12*5*1*0##", False)],
)
async def test_only_an_actuator_status_counts_as_a_resync_echo(hass: HomeAssistant, frame, is_echo) -> None:
    from custom_components.myhome.gateway_events import GatewayEventDispatcher

    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:00:12:34"
    handler.log_id = "[T]"
    dispatcher = GatewayEventDispatcher(handler)
    dispatcher._is_active_for_who = lambda who: True  # type: ignore[method-assign]

    await dispatcher.process_message(OWNMessage.parse(frame))

    assert handler._resync_manager.handle_ptp_echo.called is is_echo


# --- diagnostics -----------------------------------------------------------


async def test_diagnostics_do_not_show_the_primary_gateway_mac(hass: HomeAssistant) -> None:
    from custom_components.myhome.const import DOMAIN
    from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics
    from tests.conftest import attach_runtime

    mac = "00:03:50:aa:bb:cc"
    entry = MagicMock(entry_id="e", version=1, domain=DOMAIN, title="T", data={"mac": mac}, options={})
    handler = MagicMock()
    handler.gateway = MagicMock(model_name="MH200N")
    handler.bus_topology = "shared"
    handler.primary_gateway_mac = "00:03:50:11:22:33"
    hass.data[DOMAIN] = {}
    attach_runtime(hass, entry, mac, handler)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "00:03:50:11:22:33" not in str(diag)
    assert diag["gateway"]["primary_gateway"] == "**REDACTED**"


# --- SSDP rediscovery ------------------------------------------------------


async def test_ssdp_rediscovery_only_updates_the_host(hass: HomeAssistant) -> None:
    from homeassistant import config_entries
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.0.2.10", "port": 20001, "mac": "00:03:50:00:12:34", "name": "My own name"},
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    class Info:
        ssdp_usn = "usn"
        ssdp_st = "st"
        ssdp_location = "http://192.0.2.11:49153/description.xml"
        upnp = {
            "modelName": "F454", "serialNumber": "00:03:50:00:12:34", "friendlyName": "Gateway",
            "UDN": "uuid", "modelNumber": "2.0",
        }
        ssdp_headers = {"_host": "192.0.2.11"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=Info()
    )

    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "192.0.2.11"  # the point of rediscovery
    assert entry.data["port"] == 20001  # 20000 is only assumed by the flow, never discovered
    assert entry.data["name"] == "My own name"


# --- legacy myhome.yaml ----------------------------------------------------


async def test_legacy_yaml_climate_without_a_zone_is_accepted(tmp_path) -> None:
    from homeassistant.const import CONF_MAC

    from custom_components.myhome.legacy_yaml import load_legacy_myhome_yaml

    hass = MagicMock(spec=HomeAssistant)
    hass.config = MagicMock()
    hass.config.path = MagicMock(side_effect=lambda p: str(tmp_path / p))
    hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *args: f(*args))
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    entry = MagicMock(entry_id="e", data={CONF_MAC: "00:03:50:81:22:33"}, options={})
    (tmp_path / "myhome.yaml").write_text(
        "00:03:50:81:22:33:\n  climate:\n    central:\n      name: Central unit\n      central: true\n",
        encoding="utf-8",
    )
    configured: dict = {"climate": {}}

    await load_legacy_myhome_yaml(hass, entry, configured)

    assert configured["climate"], "the whole file was rejected because of an injected `where`"


# --- event watchdog --------------------------------------------------------


async def test_closing_the_runner_after_the_watchdog_fired_does_not_raise() -> None:
    import asyncio

    from custom_components.myhome.gateway_sessions import EventSessionRunner

    runner = EventSessionRunner(MagicMock())
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01) as watchdog:
            runner._event_watchdog = watchdog
            await asyncio.sleep(1)

    runner.close()

    assert runner._event_watchdog is None


# --- illuminance: the registry id survives a restore -----------------------


@pytest.mark.parametrize("old_id", ["-1-14-illuminance", "-14-illuminance"])
async def test_a_restored_illuminance_sensor_keeps_its_registry_unique_id(hass: HomeAssistant, old_id) -> None:
    from homeassistant.const import CONF_MAC

    from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
    from custom_components.myhome.sensor import async_setup_entry
    from tests.conftest import attach_runtime

    mac = "00:11:22:33:44:55"
    config_entry = MagicMock(data={CONF_MAC: mac})
    gw = MagicMock(mac=mac, log_id="[T]", send_status_request=AsyncMock())
    hass.data = {DOMAIN: {mac: {CONF_PLATFORMS: {"sensor": {}}, CONF_ENTITY: gw}}}
    registry_entry = MagicMock(domain="sensor", unique_id=f"{mac}{old_id}", entity_id="sensor.illuminance_14")
    registry_entry.original_device_class = "illuminance"

    added: list = []
    with patch(
        "custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[registry_entry]
    ), patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()):
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)

    assert [e._attr_unique_id for e in added] == [registry_entry.unique_id]
