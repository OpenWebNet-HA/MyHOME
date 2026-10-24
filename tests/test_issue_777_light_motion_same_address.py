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

Firmware evidence (OpenWebNet-HA/own-firmware-oracle#56, merged; suite ``who1-motion-detector``,
findings/who1-motion-detector.md,
12 emulated gateways; F454 2.0.51, F459, F460, F461, MH200N, MH202 and MyHomeServer1
agree on every row below):

- the SCS presence status frame ``B8 52 1D 00`` / ``B8 52 1D 01`` comes out as
  ``*1*34*52##`` / ``*1*39*52##`` (F453 / F453AV: WHAT 34 only);
- the reporter's dimension replies ``*#1*0*6*0##``, ``*#1*0*5*2##`` and
  ``*#1*0*7*0*0*45##`` come out byte-exact from ``D5 00 21 44 {10,15,16} ..``
  frames, whose address byte is 00. The same replies addressed to 52
  (``D1 52 01 44 ..``) come out as ``*#1*52*..##``: the gateway does not drop the
  address, the WHERE 0 is on the bus.
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


@pytest.mark.asyncio
async def test_configured_light_behind_an_interface_does_not_vouch_for_another_bus(hass):
    """A yaml light at 52 behind interface 01 leaves the ghost light at plain 52 a ghost."""
    lights = {"spots_wc": {CONF_WHERE: "52", "interface": "01", CONF_NAME: "Spots WC"}}
    entries = _registry(("light", f"{MAC}-1-52"), ("binary_sensor", f"{MAC}-52-motion"))
    config_entry, mock_er, patches = _setup(hass, lights, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        mock_er.async_remove.assert_any_call(entries[0].entity_id)
        assert [e._device_id for e in added] == ["52#4#01"]


@pytest.mark.asyncio
async def test_removing_a_light_at_a_sensor_address_warns_how_to_keep_it(hass, caplog):
    """The reporter saw only an INFO line; a light pruned for sharing a sensor address warns."""
    entries = _registry(("light", f"{MAC}-1-23"), ("binary_sensor", f"{MAC}-1-23-motion"))
    config_entry, mock_er, patches = _setup(hass, {}, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        mock_er.async_remove.assert_any_call(entries[0].entity_id)
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and entries[0].entity_id in r.getMessage()]
    assert len(warnings) == 1
    assert "myhome.yaml" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_on_off_at_an_unconfigured_sensor_address_hints_once(hass, caplog):
    entries = _registry(("binary_sensor", f"{MAC}-1-23-motion"))
    config_entry, _mock_er, patches = _setup(hass, {}, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        for frame in ("*1*1*23##", "*1*0*23##", "*1*1*23##"):
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))
            await hass.async_block_till_done()
    assert added == []
    hints = [r for r in caplog.records if "do not create a light" in r.getMessage()]
    assert len(hints) == 1


def test_motion_end_detection_is_what_39_only():
    from custom_components.myhome.discovery import is_motion_end

    assert is_motion_end(OWNEvent.parse("*1*39*52##")) is True
    for frame in ("*1*34*52##", "*1*0*52##", "*1*1*52##", "*1*19*52##", "*#1*52*7*0*0*45##"):
        assert is_motion_end(OWNEvent.parse(frame)) is False, frame

    # An OWNd that knows WHAT 39 flags it as motion_end (no unknown_state).
    newer = MagicMock(spec=[])
    newer.motion_end = True
    assert is_motion_end(newer) is True
    # A motion message with motion False is not an end of movement by itself.
    other = MagicMock(spec=[])
    other.message_type = "motion_detected"
    other.motion = False
    assert is_motion_end(other) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("inverted", [False, True])
async def test_motion_sensor_clears_on_what_39(hass, inverted):
    from unittest.mock import AsyncMock

    from homeassistant.components.binary_sensor import BinarySensorDeviceClass

    from custom_components.myhome.binary_sensor import MyHOMEMotionSensor

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.send_status_request = AsyncMock()
    sensor = MyHOMEMotionSensor(
        hass=hass, name="WC", entity_name="Mouvement", device_id="52",
        who="1", where="52", inverted=inverted, device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="Legrand", model="067226", gateway=gateway,
    )
    sensor.async_write_ha_state = MagicMock()

    sensor.handle_event(OWNEvent.parse("*1*34*52##"))
    assert sensor.is_on is (not inverted)
    sensor.handle_event(OWNEvent.parse("*1*39*52##"))
    assert sensor.is_on is inverted
    assert sensor._attr_force_update is False


@pytest.mark.asyncio
async def test_sensor_frames_routed_to_the_shared_key_never_reach_the_light(hass, caplog):
    """Reporter retest on b16: the light still logged 'unknown lighting WHAT 39' once per cycle.

    The binary_sensor's motion discovery publishes WHAT 34 / 39 to WHO 1 key 52, the key
    the light at 52 listens on, so route_foreign never sees them; the light drops them.
    """
    lights = {"spots_wc": {CONF_WHERE: "52", CONF_NAME: "Spots WC"}}
    entries = _registry(("light", f"{MAC}-1-52"), ("binary_sensor", f"{MAC}-52-motion"))
    config_entry, _mock_er, patches = _setup(hass, lights, entries)
    added: list = []
    with patches[0], patches[1], patches[2], patches[3]:
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
        await hass.async_block_till_done()
        light = added[0]
        light.hass = hass
        light.async_write_ha_state = MagicMock()
        router = config_entry.runtime_data.router

        for cycle in range(2):
            for frame in ("*1*34*52##", "*1*1000#1*52##", "*1*1*52##"):
                router.publish("1", ("52",), OWNEvent.parse(frame))
            assert light._attr_is_on is True
            for frame in ("*1*39*52##", "*#1*0*7*0*0*45##", "*#1*52*6*0##"):
                router.publish("1", ("52",), OWNEvent.parse(frame))
            assert light._attr_is_on is True, cycle
            for frame in ("*1*1000#0*52##", "*1*0*52##"):
                router.publish("1", ("52",), OWNEvent.parse(frame))
            assert light._attr_is_on is False

    assert "unknown lighting WHAT" not in caplog.text
    assert "unknown_state" not in light._attr_extra_state_attributes


def _make_pool_gateway(hass):
    """Build a gateway mock wired to a real CommandWorkerPool."""
    import asyncio

    from custom_components.myhome.bus_monitor import BusMonitor
    from custom_components.myhome.gateway_sessions import CommandWorkerPool

    gw = MagicMock()
    gw.hass = hass
    gw.mac = MAC
    gw.log_id = "[F454]"
    gw.bus_monitor = BusMonitor()
    gw.command_session_idle_timeout = 5.0
    gw.gateway = MagicMock()
    gw.gateway.profile.max_queue_size = 250
    gw.gateway.profile.command_queue_delay = 0
    gw.config_entry = None
    event_ready = asyncio.Event()
    event_ready.set()
    pool = CommandWorkerPool(gw, event_session_ready=event_ready)
    gw._command_pool = pool
    gw.send_status_request = pool.send_status_request
    gw.claim_in_flight_dimension_where = pool.claim_in_flight_dimension_where
    return gw, pool


@pytest.mark.asyncio
async def test_anonymous_where_0_dimension_replies_correlated_with_in_flight_request_before_nack(hass):
    """Replay @eC0y0te's #777 trace (#issuecomment-6375223714): WHERE=0 reply arrives ~140 ms before ~2 s NACK.

    Trace from F454 2.0.51 + Legrand 067226 at WHERE=52:
      23:11:59.564  Worker 0 unqueues `*#1*52*5##`
      23:11:59.702  Event session receives `*#1*0*5*2##` (+138 ms, while send() is still awaiting NACK)
      23:12:01.673  Command session receives `*#*0##` NACK (+2.11 s, send() returns None)
      23:12:01.724  Worker 0 unqueues `*#1*52*7##`
      23:12:01.863  Event session receives `*#1*0*7*0*0*45##` (+139 ms, while send() is still awaiting NACK)
      23:12:03.742  Command session receives `*#*0##` NACK (+2.02 s, send() returns None)
    """
    import asyncio
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from custom_components.myhome.binary_sensor import async_setup_entry as setup_binary_sensor

    gw, pool = _make_pool_gateway(hass)
    lights = {"spots_wc": {CONF_WHERE: "52", CONF_NAME: "Spots WC"}}
    entries = _registry(("light", f"{MAC}-1-52"), ("binary_sensor", f"{MAC}-52-motion"))
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        "entity": gw,
        CONF_PLATFORMS: {"light": lights, "binary_sensor": {}},
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "entry_777"
    mock_er = MagicMock()

    replies_for_request = {
        "*#1*52*5##": "*#1*0*5*2##",
        "*#1*52*7##": "*#1*0*7*0*0*45##",
    }

    async def fake_command_send(message, is_status_request=False):
        raw = str(message)
        reply = replies_for_request.get(raw)
        if reply is not None:
            # Event session receives the WHERE=0 reply ~140 ms after request is sent,
            # while the command session is still waiting for the ~2 s NACK.
            await asyncio.sleep(0)
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(reply))
            await asyncio.sleep(0)
        # Command session times out with NACK (*#*0##) -> OWNCommandSession.send returns None.
        return None

    fake_session = MagicMock()
    fake_session.connect = AsyncMock(return_value={"Success": True})
    fake_session.close = AsyncMock()
    fake_session.send = AsyncMock(side_effect=fake_command_send)

    added_lights: list = []
    added_sensors: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=entries),
        patch("custom_components.myhome.discovery.er.async_get", return_value=mock_er),
        patch("custom_components.myhome.light.er.async_entries_for_config_entry", return_value=entries),
        patch("custom_components.myhome.light.er.async_get", return_value=mock_er),
        patch("custom_components.myhome.gateway.OWNCommandSession", return_value=fake_session),
        patch("custom_components.myhome.gateway._session_is_open", return_value=True),
    ):
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added_lights.extend)
        await setup_binary_sensor(hass, config_entry, added_sensors.extend)
        assert len(added_lights) == 1
        assert len(added_sensors) == 1
        motion = added_sensors[0]
        motion.hass = hass
        motion.async_write_ha_state = MagicMock()

        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            await motion.async_added_to_hass()
            await pool.send_buffer.join()
        finally:
            pool.close()
            await asyncio.wait_for(worker, timeout=2.0)

    assert [str(c.kwargs["message"]) for c in fake_session.send.call_args_list] == [
        "*#1*52*5##",
        "*#1*52*7##",
    ]
    assert motion._attr_extra_state_attributes["Sensitivity"] == "high"
    assert motion._attr_extra_state_attributes["Timeout"] == 60.0
    assert motion._timeout == timedelta(seconds=60)
    # No WHERE=0 entity was discovered on either platform.
    assert [e._device_id for e in added_sensors] == ["52"]
    assert [e._device_id for e in added_lights] == ["52"]
    assert "unknown_state" not in added_lights[0]._attr_extra_state_attributes


@pytest.mark.asyncio
async def test_anonymous_where_0_replies_sequential_across_two_motion_sensors(hass):
    """With 1 command worker, two motion sensors (52 and 61) each receive their own WHERE=0 replies."""
    import asyncio
    from unittest.mock import AsyncMock

    from custom_components.myhome.binary_sensor import async_setup_entry as setup_binary_sensor

    gw, pool = _make_pool_gateway(hass)
    entries = _registry(("binary_sensor", f"{MAC}-52-motion"), ("binary_sensor", f"{MAC}-61-motion"))
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        "entity": gw,
        CONF_PLATFORMS: {"binary_sensor": {}},
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "entry_777_two"
    mock_er = MagicMock()

    replies_for_request = {
        "*#1*52*5##": "*#1*0*5*2##",       # high
        "*#1*52*7##": "*#1*0*7*0*0*45##",  # 45s + 15s = 60s
        "*#1*61*5##": "*#1*0*5*3##",       # very high
        "*#1*61*7##": "*#1*0*7*0*2*0##",   # 120s + 15s = 135s
    }

    async def fake_command_send(message, is_status_request=False):
        reply = replies_for_request.get(str(message))
        if reply is not None:
            await asyncio.sleep(0)
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(reply))
            await asyncio.sleep(0)
        return None

    fake_session = MagicMock()
    fake_session.connect = AsyncMock(return_value={"Success": True})
    fake_session.close = AsyncMock()
    fake_session.send = AsyncMock(side_effect=fake_command_send)

    added_sensors: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=entries),
        patch("custom_components.myhome.discovery.er.async_get", return_value=mock_er),
        patch("custom_components.myhome.gateway.OWNCommandSession", return_value=fake_session),
        patch("custom_components.myhome.gateway._session_is_open", return_value=True),
    ):
        attach_runtime(hass, config_entry)
        await setup_binary_sensor(hass, config_entry, added_sensors.extend)
        by_where = {s._where: s for s in added_sensors}
        assert set(by_where) == {"52", "61"}
        for s in added_sensors:
            s.hass = hass
            s.async_write_ha_state = MagicMock()

        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            for s in added_sensors:
                await s.async_added_to_hass()
            await pool.send_buffer.join()
        finally:
            pool.close()
            await asyncio.wait_for(worker, timeout=2.0)

    assert by_where["52"]._attr_extra_state_attributes["Sensitivity"] == "high"
    assert by_where["52"]._attr_extra_state_attributes["Timeout"] == 60.0
    assert by_where["61"]._attr_extra_state_attributes["Sensitivity"] == "very high"
    assert by_where["61"]._attr_extra_state_attributes["Timeout"] == 135.0


@pytest.mark.asyncio
async def test_anonymous_where_0_replies_dropped_when_unclaimed_duplicate_or_ambiguous(hass):
    """Stray, duplicate, broadcast, or multi-worker-ambiguous WHERE=0 replies are dropped."""
    from OWNd.message import OWNCommand

    from custom_components.myhome.binary_sensor import async_setup_entry as setup_binary_sensor

    gw, pool = _make_pool_gateway(hass)
    entries = _registry(("binary_sensor", f"{MAC}-52-motion"), ("binary_sensor", f"{MAC}-61-motion"))
    hass.data.setdefault(DOMAIN, {})[MAC] = {
        "entity": gw,
        CONF_PLATFORMS: {"binary_sensor": {}},
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": MAC}
    config_entry.entry_id = "entry_777_ambig"
    mock_er = MagicMock()

    added_sensors: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=entries),
        patch("custom_components.myhome.discovery.er.async_get", return_value=mock_er),
    ):
        attach_runtime(hass, config_entry)
        await setup_binary_sensor(hass, config_entry, added_sensors.extend)
        by_where = {s._where: s for s in added_sensors}
        for s in added_sensors:
            s.hass = hass
            s.async_write_ha_state = MagicMock()

        # 1. No request in flight -> WHERE=0 replies and non-5/7 general frames are ignored.
        for frame in ("*#1*0*5*2##", "*#1*0*7*0*0*45##", "*#1*0*6*500##"):
            async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(frame))
        await hass.async_block_till_done()
        assert by_where["52"]._attr_extra_state_attributes["Sensitivity"] == "medium"
        assert by_where["52"]._attr_extra_state_attributes["Timeout"] == 315.0

        # 2. Broadcast or group dimension requests in flight are never claimed as point-to-point targets.
        pool._in_flight_messages.extend([
            OWNCommand.parse("*#1*0*5##"),
            OWNCommand.parse("*#1*#1*5##"),
            "*1*1*52##",  # non-dimension command
        ])
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#1*0*5*2##"))
        await hass.async_block_till_done()
        assert by_where["52"]._attr_extra_state_attributes["Sensitivity"] == "medium"
        pool._in_flight_messages.clear()

        # 3. Two workers simultaneously in flight for the same (WHO=1, dim=5) on 52 and 61:
        # BOTH replies arriving while both requests remain in flight must be dropped.
        pool._in_flight_messages.extend([
            OWNCommand.parse("*#1*52*5##"),
            OWNCommand.parse("*#1*61*5##"),
        ])
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#1*0*5*2##"))
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#1*0*5*3##"))
        await hass.async_block_till_done()
        assert by_where["52"]._attr_extra_state_attributes["Sensitivity"] == "medium"
        assert by_where["61"]._attr_extra_state_attributes["Sensitivity"] == "medium"
        pool._in_flight_messages.clear()

        # 4. Single in-flight request is consumed by the first reply (+140 ms), so a second
        # stray reply arriving before the ~2 s NACK completes is dropped.
        pool._in_flight_messages.append(OWNCommand.parse("*#1*52*5##"))
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#1*0*5*2##"))
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#1*0*5*0##"))
        await hass.async_block_till_done()
        assert by_where["52"]._attr_extra_state_attributes["Sensitivity"] == "high"
        assert pool._in_flight_messages == []
        assert len(added_sensors) == 2


def test_claim_in_flight_dimension_where_delegation_and_failover():
    """MyHOMEGatewayHandler routes claim_in_flight_dimension_where through secondary or standby."""
    from custom_components.myhome.gateway import MyHOMEGatewayHandler

    handler = MagicMock(spec=MyHOMEGatewayHandler)
    handler._command_pool = MagicMock()
    handler._command_pool.claim_in_flight_dimension_where.return_value = "52"
    handler.delegated_away_whos = set()
    handler.is_connected = True

    # Direct to primary command pool.
    assert MyHOMEGatewayHandler.claim_in_flight_dimension_where(handler, 1, 5) == "52"

    # Delegated WHO routes to connected secondary's command pool.
    sec = MagicMock()
    sec.is_connected = True
    sec._command_pool.claim_in_flight_dimension_where.return_value = "52#4#01"
    handler.delegated_away_whos = {1}
    handler._get_secondary_for_who.return_value = sec
    assert MyHOMEGatewayHandler.claim_in_flight_dimension_where(handler, 1, 5) == "52#4#01"

    # Disconnected primary routes to connected warm standby supporting the WHO.
    handler.delegated_away_whos = set()
    handler.is_connected = False
    standby = MagicMock()
    standby.is_connected = True
    standby._profile_supports_who.return_value = True
    standby._command_pool.claim_in_flight_dimension_where.return_value = "61"
    handler._get_standby_gateway.return_value = standby
    assert MyHOMEGatewayHandler.claim_in_flight_dimension_where(handler, 1, 7) == "61"

