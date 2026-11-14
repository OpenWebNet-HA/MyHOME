"""A probe frame (WHERE ``PZZ`` >= 100) is not a frame of heating zone ``ZZ``.

OWNd decodes ``105`` correctly as probe 1 of zone 5 (``sensor`` 1, ``zone`` 5), but
the climate platform then treated it as zone 5's own frame: it delivered the probe's
frames to that zone and, where no heating zone 5 exists (an external probe 105 beside
zones 1-4), discovered a phantom one. Found in the #466 MyHomeServer1 capture from
@gdluck, whose plant has zones 35-70 and a probe 169.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import CONF_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent

from custom_components.myhome.climate import (
    MyHOMEClimate,
    _calling_zones,
    _zone_address,
    _zone_route_keys,
    async_setup_entry,
)
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from custom_components.myhome.where_grammar import is_probe
from tests.conftest import attach_runtime

MAC = "00:03:50:00:04:66"
TRACE = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/myhome_trace_MyHomeServer1_all_2026-09-29T17-57-37.json"
)


def _setup(hass, wheres):
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.log_id = "[probe routing]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    devices = {
        w: {"where": w, "name": f"Zone {w}", "heat": True, "cool": False, "standalone": True}
        for w in wheres
    }
    hass.data[DOMAIN] = {MAC: {CONF_PLATFORMS: {"climate": devices}, CONF_ENTITY: gateway}}
    entry = MagicMock()
    entry.entry_id = "probe_routing"
    entry.data = {CONF_MAC: MAC}
    attach_runtime(hass, entry)
    return entry


@pytest.fixture
async def zones(hass):
    """Zones 5 and 69, the two a probe's last two digits can hit (probes 105 and 169)."""
    entry = _setup(hass, ("5", "69"))
    added: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, added.extend)
    for entity in added:
        entity.hass = hass
        entity._attr_hvac_mode = HVACMode.HEAT
        await entity.async_added_to_hass()
    return {e._where: e for e in added}


def _send(hass, frame):
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))


@pytest.mark.parametrize(
    ("frame", "zone"),
    [
        ("*#4*105*0*0296##", "5"),
        ("*#4*169*0*0250##", "69"),
        ("*#4*100*0*0210##", "0"),
        ("*#4*199*0*0210##", "99"),
        ("*#4*0105*0*0296##", "5"),
    ],
)
def test_probe_frame_names_no_zone(frame, zone):
    """OWNd's ``zone`` of a probe frame is the probe's zone; the platform must not treat it as the zone's own frame."""
    message = OWNEvent.parse(frame)
    assert message.zone == int(zone)  # the premise: OWNd reports the probe's zone
    assert _calling_zones(message)[0] == []
    assert _zone_address(message) is None
    assert (
        _zone_route_keys(message, None) == []
    )  # neither the zone nor the probe's own WHERE targets a climate entity


@pytest.mark.parametrize(
    "frame",
    ["*4*1*105##", "*4*0*169##", "*#4*105*14*0210*3##", "*#4*169*12*0210*3##"],
)
def test_probe_command_and_setpoint_frames_name_no_zone(frame):
    """Not only temperature reports: any frame addressed to a probe WHERE is the probe's."""
    message = OWNEvent.parse(frame)
    assert _zone_address(message) is None
    assert _zone_route_keys(message, None) == []


@pytest.mark.parametrize(
    ("where", "probe"),
    [
        ("105", True),
        ("0105", True),
        ("#105", True),
        ("4-105", True),
        ("99", False),
        ("5", False),
        ("#0", False),
        ("12#1", False),
        ("99#3", False),
    ],
)
def test_one_definition_of_a_probe_where(where, probe):
    """``12#1`` is actuator 1 of zone 12, not probe 1 of zone 21 - only a bare number is ``PZZ``."""
    assert is_probe(where) is probe


@pytest.mark.parametrize(
    "frame", ["*#4*5*0*0210##", "*#4*99*0*0210##", "*#4*69*0*0230##", "*#4*1#4#01*0*0210##"]
)
def test_zone_frame_still_names_its_zone(frame):
    """The guard is WHERE >= 100: zone 99 is the last zone, not a probe."""
    message = OWNEvent.parse(frame)
    assert _calling_zones(message)[0] == [str(message.zone)]
    assert _zone_address(message) is not None


async def test_probe_temperature_does_not_reach_the_zone(hass, zones):
    """External probe 105 (29.6 C outside) must not become zone 5's room temperature.

    OWNd files a probe's reading as ``secondary_temperature``, which a zone ignores, so this
    holds today; it pins the routing so a future OWNd typing the frame differently cannot leak it.
    """
    _send(hass, "*#4*5*0*0210##")
    await hass.async_block_till_done()
    assert zones["5"].current_temperature == 21.0

    _send(hass, "*#4*105*0*0296##")
    _send(hass, "*#4*169*0*0250##")
    await hass.async_block_till_done()
    assert zones["5"].current_temperature == 21.0
    assert zones["69"].current_temperature is None


async def test_probe_frame_discovers_no_phantom_zone(hass):
    """With no zone 5 known, a frame of probe 105 must not create a "Climate Zone 5"."""
    entry = _setup(hass, ())
    added: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, added.extend)
    _send(hass, "*#4*105*0*0296##")
    _send(hass, "*#4*169*5*0##")
    await hass.async_block_till_done()
    assert added == []


def test_gdluck_trace_probe_frames_are_not_zone_frames():
    """Replay of the #466 capture: probe 169's frame, and no other, must not name a zone."""
    frames = [
        f["raw"]
        for f in json.loads(TRACE.read_text(encoding="utf-8"))["frames"]
        if f["raw"].startswith("*#4*")
    ]
    probe_frames = [f for f in frames if f.startswith("*#4*169*")]
    assert probe_frames, "the capture is expected to hold probe 169 traffic"
    for frame in probe_frames:
        message = OWNEvent.parse(frame)
        assert _zone_address(message) is None, frame
    # every other heating frame of the capture still names its zone (35-70)
    for frame in set(frames) - set(probe_frames):
        message = OWNEvent.parse(frame)
        if str(message.where).startswith(("0", "#0")):
            continue  # pump / central unit
        assert _zone_address(message) is not None, frame
