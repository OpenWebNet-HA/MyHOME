"""#777: a configured light shares its WHO 1 address with a motion detector.

A Legrand 067226 (Céliane BUS motion / luminosity detector with push button) has no
relay: it is configured at the A/PL of the F411 channel it switches and reports its
motion frames there. On the F454 of the reporter this produced, at address 52:

    *1*34*52##            movement detected (sensor)
    *1*39*52##            end of movement (sensor; "unknown lighting WHAT 39" before)
    *1*1*52## / *1*0*52## the F411 channel (light)

Before this fix the discovered ``binary_sensor`` at 52 made ``_ForeignAddresses``
own the address, ``ghost()`` removed ``light.myhome_spots_wc`` from the registry on
the next reload and ``accept()`` skipped the ``myhome.yaml`` entry, so the light was
gone for good.

Firmware evidence for WHAT 34 / 39: the F461 ``bt_luci`` translator decodes the SCS
presence-status frame (class 0x1D) value 00 as ``*1*34*WHERE##`` and value 01 as
``*1*39*WHERE##`` (``oracle2/hex2own_corpus.tsv`` rows ``B8311D00`` / ``B8311D01``).
"""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import CONF_NAME
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent

from custom_components.myhome.const import CONF_PLATFORMS, CONF_WHERE, DOMAIN
from custom_components.myhome.light import _ForeignAddresses, async_setup_entry
from tests.conftest import attach_runtime

MAC = "mac_777"


def _registry(*entries):
    out = []
    for domain, unique_id in entries:
        e = MagicMock()
        e.domain = domain
        e.unique_id = unique_id
        e.entity_id = f"{domain}.{unique_id.replace('-', '_')}"
        out.append(e)
    return out


def _setup(hass, lights, registry_entries):
    mock_gateway = MagicMock()
    mock_gateway.mac = MAC
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {"light": lights},
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "entry_777"
    mock_er = MagicMock()
    patches = (
        patch(
            "custom_components.myhome.discovery.er.async_entries_for_config_entry",
            return_value=registry_entries,
        ),
        patch("custom_components.myhome.discovery.er.async_get", return_value=mock_er),
        patch(
            "custom_components.myhome.light.er.async_entries_for_config_entry",
            return_value=registry_entries,
        ),
        patch("custom_components.myhome.light.er.async_get", return_value=mock_er),
    )
    return config_entry, mock_er, patches


@pytest.mark.asyncio
async def test_configured_light_survives_a_motion_sensor_at_its_address(hass):
    """The yaml light at 52 is restored, not removed, although binary_sensor 52-motion exists."""
    lights = {"spots_wc": {CONF_WHERE: "52", CONF_NAME: "Spots WC"}}
    entries = _registry(("light", f"{MAC}-1-52"), ("binary_sensor", f"{MAC}-52-motion"))
    config_entry, mock_er, patches = _setup(hass, lights, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()

        for call in mock_er.async_remove.call_args_list:
            assert "light" not in call.args[0], f"configured light removed: {call.args}"
        assert len(added) == 1
        light = added[0]
        assert light._device_id == "52"

        # Sensor frames on 52 never create another light and never reach the light.
        for frame in ("*1*34*52##", "*#1*52*6*500##", "*#1*52*5*2##", "*1*39*52##"):
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))
            await hass.async_block_till_done()
            assert len(added) == 1
            assert "unknown_state" not in light._attr_extra_state_attributes

        # The F411 channel's own frames still drive the light.
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*1*52##"))
        await hass.async_block_till_done()
        assert light._attr_is_on is True
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*0*52##"))
        await hass.async_block_till_done()
        assert light._attr_is_on is False
        assert len(added) == 1


@pytest.mark.asyncio
async def test_configured_light_is_created_from_yaml_next_to_a_motion_sensor(hass):
    """Fresh install: the registry only has the motion sensor, the yaml light is still created."""
    lights = {"spots_wc": {CONF_WHERE: "52", CONF_NAME: "Spots WC"}}
    entries = _registry(("binary_sensor", f"{MAC}-52-motion"))
    config_entry, mock_er, patches = _setup(hass, lights, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        assert [e._device_id for e in added] == ["52"]
        mock_er.async_remove.assert_not_called()


@pytest.mark.asyncio
async def test_unconfigured_ghost_light_at_a_sensor_address_is_still_pruned(hass):
    """#244 / #245 behaviour is kept: no yaml light at 23 -> the registry light at 23 is a ghost."""
    lights = {"25": {CONF_WHERE: "25", CONF_NAME: "Light 25"}}
    entries = _registry(
        ("light", f"{MAC}-1-23"), ("binary_sensor", f"{MAC}-1-23-motion"), ("light", f"{MAC}-1-25")
    )
    config_entry, mock_er, patches = _setup(hass, lights, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        mock_er.async_remove.assert_any_call(entries[0].entity_id)
        assert [e._device_id for e in added] == ["25"]

        # A later *1*1*23## does not create a light either (sensor-owned, unconfigured).
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*1*1*23##"))
        await hass.async_block_till_done()
        assert len(added) == 1


def test_sensor_frame_detection_covers_what_39():
    assert _ForeignAddresses.is_sensor_frame(OWNEvent.parse("*1*39*52##")) is True
    assert _ForeignAddresses.is_sensor_frame(OWNEvent.parse("*1*34*52##")) is True
    assert _ForeignAddresses.is_sensor_frame(OWNEvent.parse("*1*1*52##")) is False

    # Older OWNd releases leave WHAT 39 as unknown_state 39: still a sensor frame.
    legacy = MagicMock(spec=[])
    legacy.unknown_state = 39
    assert _ForeignAddresses.is_sensor_frame(legacy) is True


@pytest.mark.asyncio
async def test_reporter_trace_f454_067226_with_f411_at_52(hass):
    """Replay the F454 capture of the report: a 067226 and an F411 channel share 52.

    The detector's cycle is ``*1*34*52##`` (movement), ``*1*1000#1*52##`` + ``*1*1*52##``
    (F411 on); after ~45 s ``*1*39*52##`` (end of movement), ``*1*1000#0*52##``,
    ``*1*0*52##``. Dimension replies come back with WHERE 0 (``*#1*0*6*0##``), never with
    52, and must neither create a light nor change the configured one.
    """
    lights = {"spots_wc": {CONF_WHERE: "52", CONF_NAME: "Spots WC"}}
    entries = _registry(("light", f"{MAC}-1-52"), ("binary_sensor", f"{MAC}-52-motion"))
    config_entry, mock_er, patches = _setup(hass, lights, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        light = added[0]

        async def feed(frame: str) -> None:
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))
            await hass.async_block_till_done()

        await feed("*1*0*52##")  # status reply, light off
        assert light._attr_is_on is False
        for frame in ("*1*34*52##", "*1*1000#1*52##", "*1*1*52##"):
            await feed(frame)
        assert light._attr_is_on is True
        for frame in ("*#1*0*5*2##", "*#1*0*7*0*0*45##", "*#1*0*6*0##"):
            await feed(frame)
        assert light._attr_is_on is True
        for frame in ("*1*39*52##", "*1*1000#0*52##", "*1*0*52##"):
            await feed(frame)
        assert light._attr_is_on is False
        assert len(added) == 1
        for call in mock_er.async_remove.call_args_list:
            assert "light" not in call.args[0]
