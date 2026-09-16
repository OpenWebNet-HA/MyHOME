"""Regression tests for the September 2026 issue batch (#304, #307, #308, #310)."""
import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ColorMode
from homeassistant.core import State
from OWNd.message import OWNEvent, OWNHeatingEvent, OWNLightingEvent

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.sensor import SCAN_INTERVAL, MyHOMETemperatureSensor


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
    entry.entry_id = "entry_batch"
    entry.data = {
        "host": "192.168.1.5",
        "port": 20000,
        "password": "12345",
        "mac": "00:11:22:33:44:55",
        "name": "MH202",
        "manufacturer": "BTicino",
        "firmware": "1.0",
    }
    entry.options = {}
    entry.title = "MH202 Gateway"
    return entry


@pytest.fixture
def gateway_handler(mock_config_entry):
    mock_hass = MagicMock()
    mock_hass.data = {}
    return MyHOMEGatewayHandler(mock_hass, mock_config_entry)


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:11:22:33:44:55"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.log_id = "[test]"
    gw.device_registry_id = None
    return gw


# ── #304: OWNd yields None while reconnecting ────────────────────────────


async def test_listening_loop_skips_none_without_warning(gateway_handler, caplog):
    """A None cycle from OWNd.get_next is a reconnect, not a bad frame (#304)."""
    real_event = OWNEvent.parse("*2*1*21##")
    with patch("custom_components.myhome.gateway.OWNEventSession") as session_cls:
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.get_next = AsyncMock(side_effect=[None, real_event, asyncio.CancelledError()])
        session_cls.return_value = session
        gateway_handler._process_message = AsyncMock()

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(asyncio.CancelledError):
                await gateway_handler.listening_loop()

    # Only the real frame reaches the dispatcher and the ring buffer
    gateway_handler._process_message.assert_awaited_once_with(real_event)
    assert gateway_handler.bus_monitor.get_stats()["captured"] == 1
    assert "Data received is not a message" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "None" in r.getMessage()]


# ── #310: via_device -> via_device_id ────────────────────────────────────


def test_cen_device_links_to_the_gateway_with_via_device_id(gateway_handler):
    """CEN devices link to the gateway device by id; never the deprecated via_device tuple (#310)."""
    mock_dr = MagicMock()
    gateway_handler.device_registry_id = "gateway_dev_id"
    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "7")
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    assert kwargs["via_device_id"] == "gateway_dev_id"
    assert "via_device" not in kwargs
    assert kwargs["identifiers"] == {(DOMAIN, f"{gateway_handler.mac}-15-7")}

    # Before the parent gateway exists registration is deferred, rather than
    # creating an orphaned CEN device which cannot later be linked correctly.
    mock_dr.reset_mock()
    gateway_handler._cen_devices.clear()
    gateway_handler.device_registry_id = None
    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(25, "3")
    mock_dr.async_get_or_create.assert_not_called()


# ── #308: external probes are push-driven, poll only as a fallback ───────


def _make_temp_sensor(hass, gateway, where):
    return MyHOMETemperatureSensor(
        hass=hass,
        name=f"Probe {where}",
        device_id=f"temp_{where}",
        who="4",
        where=where,
        device_class="temperature",
        manufacturer="BTicino",
        model="3455",
        gateway=gateway,
    )


async def test_probe_sensor_starts_receive_only(hass, mock_gateway):
    """A ZPP >= 100 probe sends no *#4*ZPP*15## on add; a zone sensor still polls (#308)."""
    hass.data[DOMAIN] = {mock_gateway.mac: {"platforms": {"sensor": {}}}}

    probe = _make_temp_sensor(hass, mock_gateway, "101")
    probe.hass = hass
    probe.async_get_last_state = AsyncMock(return_value=None)
    await probe.async_added_to_hass()
    mock_gateway.send_status_request.assert_not_awaited()
    assert probe._is_probe is True

    zone = _make_temp_sensor(hass, mock_gateway, "1")
    zone.hass = hass
    zone.async_get_last_state = AsyncMock(return_value=None)
    await zone.async_added_to_hass()
    mock_gateway.send_status_request.assert_awaited_once()
    assert str(mock_gateway.send_status_request.call_args.args[0]) == "*#4*1*0##"
    assert zone._is_probe is False


async def test_probe_sensor_polls_only_when_push_stream_is_silent(hass, mock_gateway):
    """Fresh unsolicited readings suppress the periodic poll; silence re-enables it (#308)."""
    probe = _make_temp_sensor(hass, mock_gateway, "101")
    probe.hass = hass
    probe.async_schedule_update_ha_state = MagicMock()

    # Nothing received yet -> the periodic update polls the probe
    await probe.async_update()
    mock_gateway.send_status_request.assert_awaited_once()
    assert str(mock_gateway.send_status_request.call_args.args[0]) == "*#4*101*15##"

    # Unsolicited frame from the F454 / L4577 (dimension 0, secondary sensor)
    probe.handle_event(OWNHeatingEvent("*#4*101*0*0215*3##"))
    assert probe.native_value == 21.5
    assert probe._push_is_fresh() is True

    mock_gateway.send_status_request.reset_mock()
    await probe.async_update()
    mock_gateway.send_status_request.assert_not_awaited()

    # Push stream goes quiet for a full interval -> poll again
    probe._last_push_at = time.monotonic() - SCAN_INTERVAL.total_seconds() - 1
    assert probe._push_is_fresh() is False
    await probe.async_update()
    mock_gateway.send_status_request.assert_awaited_once()

# ── #307: DALI DT8 reports both HSV and tunable white ────────────────────


def _make_light(hass, gateway):
    return MyHOMELight(
        hass=hass,
        name="DALI DT8",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=False,
        manufacturer="BTicino",
        model="DALI Gateway",
        gateway=gateway,
    )


def test_color_modes_accumulate_instead_of_toggling(hass, mock_gateway):
    """Dimension 12 then 14 (and back) must leave both HS and COLOR_TEMP supported (#307)."""
    light = _make_light(hass, mock_gateway)
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    assert light.supported_color_modes == {ColorMode.ONOFF}

    light.handle_event(OWNLightingEvent("*#1*25#4#02*12*120*50*80##"))
    assert light.supported_color_modes == {ColorMode.HS}
    assert light.color_mode == ColorMode.HS

    light.handle_event(OWNLightingEvent("*#1*25#4#02*14*300##"))
    assert light.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.COLOR_TEMP

    light.handle_event(OWNLightingEvent("*#1*25#4#02*12*10*90*40##"))
    assert light.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.HS
    assert light.hs_color == (10.0, 90.0)


async def test_color_modes_restored_together(hass, mock_gateway):
    """Restoring a light that had both modes keeps both and the last active mode (#307)."""
    light = _make_light(hass, mock_gateway)
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    light.async_get_last_state = AsyncMock(
        return_value=State(
            "light.dali_dt8",
            "on",
            {
                "supported_color_modes": ["hs", "color_temp"],
                "color_mode": "color_temp",
                "brightness": 200,
                "color_temp_kelvin": 4000,
            },
        )
    )
    await light.async_added_to_hass()
    assert light.supported_color_modes == {ColorMode.HS, ColorMode.COLOR_TEMP}
    assert light.color_mode == ColorMode.COLOR_TEMP
    assert light.brightness == 200


async def test_brightness_restore_does_not_downgrade_color_light(hass, mock_gateway):
    """A stale brightness-only attribute never strips color capabilities."""
    light = _make_light(hass, mock_gateway)
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    light.async_get_last_state = AsyncMock(
        return_value=State(
            "light.dali_dt8", "on", {"supported_color_modes": ["hs", "brightness"], "color_mode": "hs"}
        )
    )
    await light.async_added_to_hass()
    assert light.supported_color_modes == {ColorMode.HS}
    assert light.color_mode == ColorMode.HS


# ── Startup discovery honours the gateway profile (MH200N NACKs *#16*0##) ──


async def test_discovery_skips_unsupported_who(gateway_handler):
    """An MH200N profile (no audio) must not be asked *#16*0## at startup."""
    from OWNd.profiles import get_gateway_profile

    gateway_handler.gateway.profile = get_gateway_profile("MH200N")
    await gateway_handler.initial_discovery()

    queued = []
    while not gateway_handler.send_buffer.empty():
        queued.append(str(gateway_handler.send_buffer.get_nowait()["message"]))
    assert queued == ["*#2*0##", "*#4*0##"]


def test_profile_supports_who_defaults_to_true_without_profile(gateway_handler):
    """Unknown or foreign profile objects never suppress discovery."""
    gateway_handler.gateway.profile = None
    assert gateway_handler._profile_supports_who(16) is True
    gateway_handler.gateway.profile = object()
    assert gateway_handler._profile_supports_who(16) is True


def test_gateway_info_normalises_none_firmware():
    """A stringified None firmware from OWNd/SSDP is reported as empty, not 'None'."""
    from custom_components.myhome.websocket import _extract_gateway_info

    gw = MagicMock()
    gw.gateway.model_name = "MH200N"
    gw.gateway.manufacturer = "BTicino S.p.A."
    gw.gateway.firmware = "None"
    gw.gateway.host = "192.0.2.40"
    gw.gateway.port = 20000
    gw.mac = "00:03:50:00:48:71"
    gw.sending_workers = []
    gw.send_buffer = None
    gw.config_entry.data = {"firmware": "null"}
    assert _extract_gateway_info(gw)["firmware"] == ""

    gw.config_entry.data = {"firmware": "1.0.42"}
    assert _extract_gateway_info(gw)["firmware"] == "1.0.42"

