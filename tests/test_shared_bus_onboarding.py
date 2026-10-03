"""A gateway added next to a configured one: topology first, no orphaned buttons (#524).

Adding a second gateway used to set it up as a standalone primary: its startup
sweep discovered every device the other gateway already had, and once it was
made a follower only the duplicate actuators were pruned, leaving their lock /
unlock / calibrate buttons unavailable on devices without an actuator.
"""
import logging
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_platform import EntityPlatform
from OWNd.message import OWNAutomationEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import button
from custom_components.myhome.const import (
    CONF_BUS_TOPOLOGY,
    CONF_DELEGATED_WHOS,
    CONF_GATEWAY_ROLE,
    CONF_PRIMARY_GATEWAY,
    CONF_WORKER_COUNT,
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    SERVICE_CALIBRATE_COVER,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from custom_components.myhome.discovery import PlatformDiscovery, prune_orphaned_companions
from custom_components.myhome.topology import (
    _follower_delegation,
    gateway_supported_whos,
    recommend_follower,
)
from tests.test_multi_gateway import _create_mock_gateway

PRI = "00:03:50:aa:bb:01"
SB = "00:03:50:aa:bb:02"
NEW = "00:03:50:aa:bb:03"
SUFFIXES = ("disable", "enable", "calibrate")


# ── recommendation ────────────────────────────────────────────────────────


def test_follower_takes_what_the_primary_lacks_and_keeps_audio_together() -> None:
    assert _follower_delegation({1, 2}, {1, 2, 16, 22}) == (ROLE_SECONDARY, {16, 22}, False)
    # Only WHO 22 is missing on the primary: WHO 16 moves along with it
    assert _follower_delegation({1, 2, 16}, {1, 2, 16, 22}) == (ROLE_SECONDARY, {16, 22}, True)
    assert _follower_delegation({1, 2, 16}, {1, 2}) == (ROLE_STANDBY, set(), False)


def test_recommend_follower_keeps_the_configured_gateway_primary() -> None:
    """An MH202 next to a MyHomeServer1 (no audio) takes the audio; next to an F454 it is a standby."""
    mh202 = MagicMock(data={CONF_NAME: "MH202"}, options={})
    expected_mh202 = {5, 16, 22} if 5 in gateway_supported_whos("MH202") else {16, 22}
    assert recommend_follower(MagicMock(data={CONF_NAME: "MyHomeServer1"}, options={}), mh202) == (
        ROLE_SECONDARY, expected_mh202,
    )
    has_alarm_delta = 5 in gateway_supported_whos("MH202") and 5 not in gateway_supported_whos("F454")
    expected_f454 = (ROLE_SECONDARY, {5}) if has_alarm_delta else (ROLE_STANDBY, set())
    assert recommend_follower(MagicMock(data={CONF_NAME: "F454"}, options={}), mh202) == expected_f454


# ── config flow ───────────────────────────────────────────────────────────


def _existing(hass: HomeAssistant, mac: str, model: str, **options) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"{model} Gateway",
        data={CONF_HOST: "192.0.2.1", CONF_MAC: mac, CONF_NAME: model},
        options={CONF_WORKER_COUNT: 1, **options},
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    return entry


async def _add_mh202(hass: HomeAssistant, *steps: dict, before_answer=None):
    """Run the manual-IP flow for an MH202, then each ``steps`` answer; return every result.

    ``before_answer`` runs once the topology form is shown, before it is answered.
    """
    discovered = {
        "address": "192.0.2.20", "port": 20000, "serialNumber": NEW, "modelName": "MH202",
        "ssdp_location": None, "ssdp_st": None, "deviceType": None, "friendlyName": None,
        "manufacturer": "BTicino S.p.A.", "manufacturerURL": "http://www.bticino.it",
        "modelNumber": None, "UDN": None,
    }
    results = []
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=discovered),
        patch("custom_components.myhome.config_flow.OWNSession.test_connection", return_value={"Success": True}),
        patch("custom_components.myhome.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"serial": "00:00:00:00:00:00"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": "192.0.2.20", "port": 20000}
        )
        results.append(result)
        if before_answer is not None:
            await before_answer()
        for answer in steps:
            result = await hass.config_entries.flow.async_configure(result["flow_id"], answer)
            results.append(result)
        await hass.async_block_till_done()
    return results


def _default(result, key: str):
    for marker in result["data_schema"].schema:
        if str(marker) == key:
            if (description := getattr(marker, "description", None)) and "suggested_value" in description:
                return description["suggested_value"]
            return marker.default()
    raise KeyError(key)


async def test_first_gateway_skips_the_topology_step(hass: HomeAssistant) -> None:
    (result,) = await _add_mh202(hass)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_BUS_TOPOLOGY not in result["options"]


async def test_followers_and_serial_gateways_cannot_be_the_primary(hass: HomeAssistant) -> None:
    """With only a follower and a USB gateway configured there is nothing to share a bus with."""
    _existing(hass, PRI, "F454", **{CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY})
    _existing(hass, SB, "F454", **{
        CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_STANDBY, CONF_PRIMARY_GATEWAY: PRI,
    })
    serial = MockConfigEntry(
        domain=DOMAIN, data={CONF_MAC: "35:78:00:00:00:01", "transport_type": "serial"}, unique_id="35:78:00:00:00:01"
    )
    serial.add_to_hass(hass)
    (result,) = await _add_mh202(hass)
    assert result["step_id"] == "bus_topology"
    options = result["data_schema"].schema[CONF_PRIMARY_GATEWAY].config["options"]
    assert [o["value"] for o in options] == [PRI]


async def test_standalone_answer_creates_a_standalone_entry(hass: HomeAssistant) -> None:
    primary = _existing(hass, PRI, "MyHomeServer1")
    step, created = await _add_mh202(hass, {
        CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE, CONF_PRIMARY_GATEWAY: PRI, CONF_GATEWAY_ROLE: ROLE_SECONDARY,
    })
    assert step["type"] == FlowResultType.FORM
    assert step["step_id"] == "bus_topology"
    assert _default(step, CONF_BUS_TOPOLOGY) == TOPOLOGY_STANDALONE
    assert created["type"] == FlowResultType.CREATE_ENTRY
    assert created["options"] == {CONF_WORKER_COUNT: 1, CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE}
    assert CONF_BUS_TOPOLOGY not in primary.options


async def test_shared_answer_creates_a_secondary_and_promotes_the_primary(hass: HomeAssistant) -> None:
    primary = _existing(hass, PRI, "MyHomeServer1")
    step, created = await _add_mh202(hass, {
        CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_PRIMARY_GATEWAY: PRI,
        CONF_GATEWAY_ROLE: ROLE_SECONDARY, CONF_DELEGATED_WHOS: ["16", "22"],
    })
    # Suggested from the two models: the MH202 takes what the MyHomeServer1 lacks
    assert _default(step, CONF_GATEWAY_ROLE) == ROLE_SECONDARY
    expected_default_whos = ["5", "16", "22"] if 5 in gateway_supported_whos("MH202") else ["16", "22"]
    assert _default(step, CONF_DELEGATED_WHOS) == expected_default_whos
    assert step["description_placeholders"] == {CONF_NAME: "MH202"}

    assert created["type"] == FlowResultType.CREATE_ENTRY
    assert created["options"] == {
        CONF_WORKER_COUNT: 1, CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_SECONDARY,
        CONF_PRIMARY_GATEWAY: PRI, CONF_DELEGATED_WHOS: [16, 22],
    }
    assert primary.options == {
        CONF_WORKER_COUNT: 1, CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY,
    }


async def test_a_second_standby_is_refused_and_the_form_keeps_the_answers(hass: HomeAssistant) -> None:
    shared = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY}
    primary = _existing(hass, PRI, "F454", **shared)
    _existing(hass, SB, "F454", **{
        CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_STANDBY, CONF_PRIMARY_GATEWAY: PRI,
    })
    answer = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_PRIMARY_GATEWAY: PRI, CONF_GATEWAY_ROLE: ROLE_STANDBY}
    step, refused = await _add_mh202(hass, answer)
    has_alarm_delta = 5 in gateway_supported_whos("MH202") and 5 not in gateway_supported_whos("F454")
    expected_default_role = ROLE_SECONDARY if has_alarm_delta else ROLE_STANDBY
    assert _default(step, CONF_GATEWAY_ROLE) == expected_default_role
    assert refused["type"] == FlowResultType.FORM
    assert refused["errors"] == {CONF_GATEWAY_ROLE: "multiple_standbys"}
    assert _default(refused, CONF_BUS_TOPOLOGY) == TOPOLOGY_SHARED
    assert _default(refused, CONF_GATEWAY_ROLE) == ROLE_STANDBY
    assert primary.options == {CONF_WORKER_COUNT: 1, **shared}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_a_primary_removed_while_the_form_is_open(hass: HomeAssistant) -> None:
    """Refused while another candidate remains; with none left the gateway is simply added."""
    gone = _existing(hass, PRI, "MyHomeServer1")
    _existing(hass, SB, "MyHomeServer1")
    answer = {CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_PRIMARY_GATEWAY: PRI, CONF_GATEWAY_ROLE: ROLE_STANDBY}

    async def remove_primary() -> None:
        await hass.config_entries.async_remove(gone.entry_id)

    _, refused = await _add_mh202(hass, answer, before_answer=remove_primary)
    assert refused["errors"] == {CONF_PRIMARY_GATEWAY: "primary_gateway_not_found"}
    assert [o["value"] for o in refused["data_schema"].schema[CONF_PRIMARY_GATEWAY].config["options"]] == [SB]


async def test_the_only_primary_removed_while_the_form_is_open(hass: HomeAssistant) -> None:
    gone = _existing(hass, PRI, "MyHomeServer1")

    async def remove_primary() -> None:
        await hass.config_entries.async_remove(gone.entry_id)

    _, created = await _add_mh202(hass, {
        CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_PRIMARY_GATEWAY: PRI, CONF_GATEWAY_ROLE: ROLE_STANDBY,
    }, before_answer=remove_primary)
    assert created["type"] == FlowResultType.CREATE_ENTRY
    assert created["options"] == {CONF_WORKER_COUNT: 1}


# ── pruning ───────────────────────────────────────────────────────────────


def _actuator(hass: HomeAssistant, entry, mac: str, where: str, *, buttons: bool = True):
    """A cover with its device and (optionally) its lock / unlock / calibrate buttons."""
    registry = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{mac}-2-{where}")}, name=f"Cover {where}",
    )
    cover = registry.async_get_or_create("cover", DOMAIN, f"{mac}-2-{where}", config_entry=entry, device_id=device.id)
    button_ids = [
        registry.async_get_or_create(
            "button", DOMAIN, f"{mac}-2-{where}-{suffix}", config_entry=entry, device_id=device.id
        ).entity_id
        for suffix in (SUFFIXES if buttons else ())
    ]
    return device, cover, button_ids


def _follower(hass: HomeAssistant):
    entry_p, _ = _create_mock_gateway(hass, PRI, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY)
    entry_x, _ = _create_mock_gateway(hass, NEW, topology=TOPOLOGY_SHARED, role=ROLE_STANDBY, primary_gateway=PRI)
    return entry_p, entry_x


async def test_restore_prunes_the_duplicate_with_its_buttons_and_device(hass: HomeAssistant) -> None:
    registry, devices = er.async_get(hass), dr.async_get(hass)
    entry_p, entry_x = _follower(hass)
    _actuator(hass, entry_p, PRI, "21#4#02")
    device, cover, button_ids = _actuator(hass, entry_x, NEW, "21#4#02")
    kept_device, kept_cover, kept_buttons = _actuator(hass, entry_x, NEW, "85")  # the primary lacks it

    discovery = PlatformDiscovery(
        hass, entry_x, MagicMock(), platform="cover", who="2", event_type=OWNAutomationEvent,
        build=lambda ctx: None,
    )
    discovery.restore()

    assert registry.async_get(cover.entity_id) is None
    assert all(registry.async_get(b) is None for b in button_ids)
    assert devices.async_get(device.id) is None
    assert registry.async_get(kept_cover.entity_id) is not None
    assert all(registry.async_get(b) is not None for b in kept_buttons)
    assert devices.async_get(kept_device.id) is not None


async def test_pruning_keeps_a_device_that_still_has_other_entities(hass: HomeAssistant) -> None:
    registry, devices = er.async_get(hass), dr.async_get(hass)
    entry_p, entry_x = _follower(hass)
    _actuator(hass, entry_p, PRI, "21")
    device, cover, _ = _actuator(hass, entry_x, NEW, "21")
    other = registry.async_get_or_create("sensor", DOMAIN, f"{NEW}-2-21-extra", config_entry=entry_x, device_id=device.id)

    PlatformDiscovery(
        hass, entry_x, MagicMock(), platform="cover", who="2", event_type=OWNAutomationEvent, build=lambda ctx: None,
    ).restore()

    assert registry.async_get(cover.entity_id) is None
    assert registry.async_get(other.entity_id) is not None
    assert devices.async_get(device.id) is not None


async def test_orphaned_buttons_left_by_earlier_versions_are_pruned(hass: HomeAssistant) -> None:
    """Only buttons whose actuator is gone here but exists on the primary."""
    registry, devices = er.async_get(hass), dr.async_get(hass)
    entry_p, entry_x = _follower(hass)
    _actuator(hass, entry_p, PRI, "21")
    orphan_device, orphan_cover, orphans = _actuator(hass, entry_x, NEW, "21")
    registry.async_remove(orphan_cover.entity_id)  # what the old pruning did
    _, _, deleted_by_user = _actuator(hass, entry_x, NEW, "85")
    registry.async_remove(registry.async_get_entity_id("cover", DOMAIN, f"{NEW}-2-85"))
    _actuator(hass, entry_p, PRI, "12")
    _, _, still_here = _actuator(hass, entry_x, NEW, "12")  # restore prunes these, not this pass
    unrelated = registry.async_get_or_create("button", DOMAIN, f"{NEW}-other", config_entry=entry_x)

    prune_orphaned_companions(hass, entry_x.entry_id, NEW, PRI)

    assert all(registry.async_get(b) is None for b in orphans)
    assert devices.async_get(orphan_device.id) is None
    assert all(registry.async_get(b) is not None for b in deleted_by_user + still_here)
    assert registry.async_get(unrelated.entity_id) is not None


@pytest.mark.parametrize("follower", [True, False])
async def test_button_setup_prunes_orphans_on_followers_only(hass: HomeAssistant, follower: bool) -> None:
    registry = er.async_get(hass)
    if follower:
        entry_p, entry_x = _follower(hass)
    else:
        entry_p, _ = _create_mock_gateway(hass, PRI)
        entry_x, _ = _create_mock_gateway(hass, NEW)
    _actuator(hass, entry_p, PRI, "21")
    _, cover, orphans = _actuator(hass, entry_x, NEW, "21")
    registry.async_remove(cover.entity_id)
    entry_x.runtime_data.platforms["button"] = {}

    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="button", platform_name=DOMAIN,
        platform=button, scan_interval=timedelta(seconds=30), entity_namespace=None,
    )
    assert await platform.async_setup_entry(entry_x)
    await hass.async_block_till_done()
    try:
        assert all((registry.async_get(b) is None) is follower for b in orphans)
    finally:
        await platform.async_reset()


async def test_calibrate_all_button_reactive_availability_and_no_covers(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The global button dynamically updates availability across gateway drops and cover registry changes (#525, #565)."""
    from custom_components.myhome.button import CalibrateAllCoversButtonEntity

    entry_p, entry_x = _follower(hass)
    _actuator(hass, entry_p, PRI, "21")  # primary owns a cover; follower starts with 0 covers

    btn = CalibrateAllCoversButtonEntity(hass=hass, config_entry=entry_x, gateway=entry_x.runtime_data.gateway)
    entry_x.runtime_data.gateway._available = True

    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="button",
        platform_name=DOMAIN,
        platform=button,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    await platform.async_add_entities([btn])
    await hass.async_block_till_done()

    try:
        # 1. Created with empty registry on follower: unavailable in state machine (#525)
        assert btn.available is False
        state = hass.states.get(btn.entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE

        # 2. Pressing while unavailable logs a warning and does not call service
        await btn.async_press()
        assert "Cannot calibrate covers: gateway unavailable or no covers present." in caplog.text
        caplog.clear()

        # 3. Covers populate after creation (e.g. late discovery / startup race #565)
        _, cover, _ = _actuator(hass, entry_x, NEW, "21")
        await hass.async_block_till_done()

        # Registry listener fires -> button becomes available in state machine
        assert btn.available is True
        assert btn._cover_entity_ids() == [cover.entity_id]
        state = hass.states.get(btn.entity_id)
        assert state is not None
        assert state.state != STATE_UNAVAILABLE

        # 4. Pressing with covers dispatches calibration service
        calls = []
        hass.services.async_register(DOMAIN, SERVICE_CALIBRATE_COVER, lambda call: calls.append(call))
        await btn.async_press()
        # The non-blocking service call may still be queued on newer HA cores.
        await hass.async_block_till_done()
        assert len(calls) == 1
        assert calls[0].data == {"entity_id": [cover.entity_id]}

        # 5. Gateway drops -> availability listener fires -> state becomes unavailable
        entry_x.runtime_data.gateway._available = False
        async_dispatcher_send(hass, entry_x.runtime_data.gateway.availability_signal)
        await hass.async_block_till_done()
        assert btn.available is False
        state = hass.states.get(btn.entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE

        # 6. Gateway restores -> state becomes available again
        entry_x.runtime_data.gateway._available = True
        async_dispatcher_send(hass, entry_x.runtime_data.gateway.availability_signal)
        await hass.async_block_till_done()
        assert btn.available is True
        state = hass.states.get(btn.entity_id)
        assert state is not None
        assert state.state != STATE_UNAVAILABLE

        # 7. Covers pruned from follower -> registry listener fires -> state returns to unavailable (#525)
        er.async_get(hass).async_remove(cover.entity_id)
        await hass.async_block_till_done()
        assert btn.available is False
        state = hass.states.get(btn.entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE
    finally:
        await platform.async_reset()

