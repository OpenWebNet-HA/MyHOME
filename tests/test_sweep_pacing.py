"""Tests for Issue #733: sweep gating, dimmer capability probe, and legacy YAML migration."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.light import ColorMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.legacy_yaml import (
    CONF_MIGRATED_YAML_PLATFORMS,
    load_legacy_myhome_yaml,
    migrate_legacy_yaml_to_entry_options,
)
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.repairs import ISSUE_LEGACY_YAML_IMPORTED
from tests.mock_gateway_harness import MockGatewayHarness

TRACES = Path(__file__).parent / "fixtures" / "traces"


def _make_gateway(hass: HomeAssistant) -> MyHOMEGatewayHandler:
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_MAC: "00:03:50:aa:bb:cc",
        CONF_NAME: "MH202",
        CONF_HOST: "192.168.1.50",
        CONF_PORT: 20000,
        CONF_PASSWORD: "12345",
    }
    entry.options = {}
    return MyHOMEGatewayHandler(hass=hass, config_entry=entry)


def _light(hass: HomeAssistant, gateway: Any, where: str, **kwargs: Any) -> MyHOMELight:
    kwargs.setdefault("dimmable", False)
    light = MyHOMELight(
        hass=hass,
        name=f"Light {where}",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id=f"1-{where}",
        who="1",
        where=where,
        interface=None,
        manufacturer="BTicino",
        model="F411",
        gateway=gateway,
        **kwargs,
    )
    light.entity_id = f"light.light_{where}"
    light.platform = MagicMock()
    light.async_schedule_update_ha_state = MagicMock()
    return light


# ── Sweep gate ─────────────────────────────────────────────────────────────


async def test_gateway_sweep_active_tracks_send_paced(hass: HomeAssistant) -> None:
    """gateway.sweep_active is True during send_paced and False once all sweeps finish."""
    gw = _make_gateway(hass)
    assert gw.sweep_active is False

    observed_during_send: list[bool] = []

    async def fake_send_status_request(msg: Any) -> None:
        observed_during_send.append(gw.sweep_active)

    with (
        patch.object(gw, "send_status_request", side_effect=fake_send_status_request),
        patch.object(gw, "_wait_for_bus_quiet", new_callable=AsyncMock),
    ):
        await gw.send_paced(["*#1*11##", "*#1*12##"])

    assert observed_during_send == [True, True]
    assert gw.sweep_active is False


async def test_initial_discovery_counts_as_a_sweep(hass: HomeAssistant) -> None:
    """The startup sweep goes through the same gate as myhome.sweep_bus."""
    gw = _make_gateway(hass)
    observed: list[tuple[bool, bool]] = []

    async def fake_send_status_request(msg: Any) -> None:
        observed.append((gw.sweep_active, gw.initial_discovery_pending))

    with (
        patch.object(gw, "send_status_request", side_effect=fake_send_status_request),
        patch.object(gw, "_wait_for_bus_quiet", new_callable=AsyncMock),
        patch.object(gw, "_discovery_frames", return_value=["*#1*0##"]),
    ):
        await gw.initial_discovery()

    assert observed == [(True, True)]
    assert gw.sweep_active is False
    assert gw.initial_discovery_pending is False


async def test_gateway_wait_for_initial_discovery_waits_for_active_sweep(
    hass: HomeAssistant,
) -> None:
    """wait_for_initial_discovery waits for active sweeps even after initial discovery completes."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()

    gw._begin_sweep()
    assert gw.sweep_active is True

    wait_task = asyncio.create_task(gw.wait_for_initial_discovery())
    await asyncio.sleep(0)
    assert not wait_task.done()

    gw._end_sweep()
    assert gw.sweep_active is False
    await wait_task


async def test_sweep_cut_short_by_a_reconnect_cannot_end_the_next_sweep(
    hass: HomeAssistant,
) -> None:
    """A sweep still running when the listener closes must not open the gate of a newer sweep."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()
    replies = {"*#1*0##": asyncio.Event(), "*#2*0##": asyncio.Event()}

    async def answered_later(msg: Any) -> None:
        await replies[str(msg)].wait()

    with (
        patch.object(gw, "send_status_request", side_effect=answered_later),
        patch.object(gw, "_wait_for_bus_quiet", new_callable=AsyncMock),
    ):
        before_reconnect = asyncio.create_task(gw.send_paced(["*#1*0##"]))
        await asyncio.sleep(0)
        assert gw.sweep_active is True

        await gw.close_listener()
        assert gw.sweep_active is False

        after_reconnect = asyncio.create_task(gw.send_paced(["*#2*0##"]))
        await asyncio.sleep(0)

        replies["*#1*0##"].set()
        await before_reconnect
        assert gw.sweep_active is True

        replies["*#2*0##"].set()
        await after_reconnect
        assert gw.sweep_active is False


async def test_entity_added_mid_sweep_defers_status_poll(hass: HomeAssistant) -> None:
    """Entities added while gateway.sweep_active is True defer status polls to background task."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()
    gw._begin_sweep()

    entity = _light(hass, gw, "21")

    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        with patch.object(
            entity, "async_get_last_state", new_callable=AsyncMock, return_value=None
        ):
            await entity.async_added_to_hass()
        await asyncio.sleep(0)
        mock_send.assert_not_called()

        gw._end_sweep()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_bus_holds_back_polls_of_the_cover_and_zone_it_discovers(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cover and a heating zone revealed during myhome.sweep_bus poll only once it is done (#733)."""
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_PERIOD", 0.02)
    harness = MockGatewayHarness()
    port = await harness.start()

    mac = "00:03:50:00:00:01"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: mac,
            CONF_NAME: "F461",  # polls heating zones point-to-point (#714)
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:IP scenario module:1",
            CONF_FRIENDLY_NAME: "F461 Gateway",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "3.6.27",
        },
        options={},
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    release = asyncio.Event()

    try:
        with (
            patch(
                "custom_components.myhome.gateway.OWNSession.test_connection",
                return_value={"Success": True, "Message": None},
            ),
            patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        ):
            setup_task = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
            for _ in range(50):
                if hass.data.get(DOMAIN, {}).get(mac):
                    break
                await asyncio.sleep(0.05)
            handler = hass.data[DOMAIN][mac][CONF_ENTITY]
            handler._on_event_connection_state_change(True)
            await setup_task
            await handler.wait_for_initial_discovery(timeout=10.0)
            assert handler.sweep_active is False

            def point_requests() -> list[str]:
                return [m for m in harness.received_messages if m.startswith(("*#2*19", "*#4*2"))] + [
                    str(item["message"])
                    for item in list(handler.send_buffer._queue)
                    if str(item["message"]).startswith(("*#2*19", "*#4*2"))
                ]

            # Hold the sweep on its first general request, the way a large plant keeps answering
            sweep_held = asyncio.Event()
            real_quiet = handler._wait_for_bus_quiet

            async def held_quiet(since: float) -> None:
                sweep_held.set()
                await release.wait()
                await real_quiet(since)

            monkeypatch.setattr(handler, "_wait_for_bus_quiet", held_quiet)
            sweep = asyncio.create_task(
                hass.services.async_call(DOMAIN, "sweep_bus", {}, blocking=True)
            )
            await asyncio.wait_for(sweep_held.wait(), 5.0)
            assert handler.sweep_active is True

            # Frames that reveal new devices without their full state
            async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse("*2*1*19##"))
            async_dispatcher_send(hass, f"myhome_message_{mac}", OWNMessage.parse("*#4*2*0*0215##"))
            for _ in range(50):
                if hass.states.get("cover.cover_19") and hass.states.async_entity_ids("climate"):
                    break
                await asyncio.sleep(0.05)
            assert hass.states.get("cover.cover_19") is not None
            assert hass.states.async_entity_ids("climate")
            await asyncio.sleep(0.2)
            assert point_requests() == []

            release.set()
            await sweep
            for _ in range(50):
                sent = point_requests()
                if "*#2*19##" in sent and any(m.startswith("*#4*2") for m in sent):
                    break
                await asyncio.sleep(0.05)
            sent = point_requests()
            assert "*#2*19##" in sent
            assert any(m.startswith("*#4*2") for m in sent)
    finally:
        release.set()
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await harness.stop()


# ── Dimmer capability probe ────────────────────────────────────────────────


async def test_light_async_probe_dimmer_capability(hass: HomeAssistant) -> None:
    """async_probe_dimmer_capability queries Dimension 1 after active sweeps for ON/OFF lights."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()

    entity = _light(hass, gw, "25")
    with (
        patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send,
        patch.object(gw, "wait_for_initial_discovery", new_callable=AsyncMock) as mock_wait,
    ):
        gw._begin_sweep()
        await entity.async_probe_dimmer_capability()
        mock_wait.assert_awaited_once()
        mock_send.assert_awaited_once()
        assert str(mock_send.await_args_list[0].args[0]) == "*#1*25*1##"
        gw._end_sweep()

    # Already-dimmable light does not need probing
    dimmer_entity = _light(hass, gw, "26", dimmable=True)
    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        await dimmer_entity.async_probe_dimmer_capability()
        mock_send.assert_not_called()


async def test_probe_skipped_when_the_sweep_itself_reveals_the_dimmer(hass: HomeAssistant) -> None:
    """An ON dimmer first answers *1*1*W## and only later in the sweep with its level (MH200 2026-10-20)."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()
    gw._begin_sweep()
    light = _light(hass, gw, "29", probe_dimmer_on_add=True)
    light._poll_on_add = False

    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        with patch.object(light, "async_get_last_state", new_callable=AsyncMock, return_value=None):
            await light.async_added_to_hass()
        await asyncio.sleep(0)

        light.handle_event(OWNEvent.parse("*1*10*29##"))
        assert ColorMode.BRIGHTNESS in light.supported_color_modes

        gw._end_sweep()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_send.assert_not_called()


# ── Legacy myhome.yaml copy in the entry options ───────────────────────────

MAC = "00:03:50:81:22:33"
HALL_YAML = f"{MAC}:\n  light:\n    hall:\n      where: '14'\n      name: 'Hall Light'\n"


def _yaml_hass(hass: HomeAssistant, tmp_path: Path, *entries: Any) -> MagicMock:
    mock_hass = MagicMock(spec=HomeAssistant)
    mock_hass.data = hass.data
    mock_hass.bus = hass.bus
    mock_hass.config = MagicMock()
    mock_hass.config.path = MagicMock(side_effect=lambda p: str(tmp_path / p))
    mock_hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *args: f(*args))
    mock_hass.config_entries = MagicMock()
    mock_hass.config_entries.async_entries = MagicMock(return_value=list(entries))

    def _update_entry(target_entry: Any, *, options: Any = None, **kwargs: Any) -> None:
        if options is not None:
            target_entry.options = options

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
    return mock_hass


def _yaml_entry(entry_id: str = "entry_1", mac: str = MAC) -> MagicMock:
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = entry_id
    entry.data = {CONF_MAC: mac}
    entry.options = {}
    return entry


def _platforms(*names: str) -> dict[str, dict[str, Any]]:
    return {name: {} for name in names or ("light", "climate")}


def _issue(hass: HomeAssistant, entry_id: str) -> Any:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_LEGACY_YAML_IMPORTED}_{entry_id}")


async def test_legacy_yaml_migration_and_fallback(hass: HomeAssistant, tmp_path: Path) -> None:
    """Validated myhome.yaml platforms are copied to entry.options once and used as fallback."""
    entry = _yaml_entry()
    mock_hass = _yaml_hass(hass, tmp_path, entry)
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(HALL_YAML, encoding="utf-8")

    configured_platforms = _platforms()
    await load_legacy_myhome_yaml(mock_hass, entry, configured_platforms)

    assert "14" in configured_platforms["light"]
    assert CONF_MIGRATED_YAML_PLATFORMS in entry.options
    assert mock_hass.config_entries.async_update_entry.call_count == 1
    assert _issue(hass, "entry_1") is not None

    # An unchanged myhome.yaml does not rewrite the options
    await load_legacy_myhome_yaml(mock_hass, entry, _platforms())
    assert mock_hass.config_entries.async_update_entry.call_count == 1
    assert (
        migrate_legacy_yaml_to_entry_options(
            mock_hass, entry, entry.options[CONF_MIGRATED_YAML_PLATFORMS]
        )
        is False
    )

    # Nothing to import: no options, no notice
    empty_entry = _yaml_entry("entry_empty")
    assert migrate_legacy_yaml_to_entry_options(mock_hass, empty_entry, {"light": {}}) is False
    assert CONF_MIGRATED_YAML_PLATFORMS not in empty_entry.options
    assert _issue(hass, "entry_empty") is None

    # Remove myhome.yaml from disk: fallback to the copy in the entry options
    yaml_file.unlink()
    configured_platforms_after_removal = _platforms()
    await load_legacy_myhome_yaml(mock_hass, entry, configured_platforms_after_removal)

    assert configured_platforms_after_removal["light"]["14"]["name"] == "Hall Light"


async def test_imported_copy_follows_yaml_changes(hass: HomeAssistant, tmp_path: Path) -> None:
    """Editing myhome.yaml after the upgrade refreshes the copy; removing it later keeps the edit."""
    entry = _yaml_entry("entry_edit")
    mock_hass = _yaml_hass(hass, tmp_path, entry)
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(HALL_YAML, encoding="utf-8")
    await load_legacy_myhome_yaml(mock_hass, entry, _platforms())
    ir.async_get(hass).async_delete(DOMAIN, f"{ISSUE_LEGACY_YAML_IMPORTED}_entry_edit")

    yaml_file.write_text(
        HALL_YAML + "    porch:\n      where: '15'\n      name: 'Porch'\n", encoding="utf-8"
    )
    await load_legacy_myhome_yaml(mock_hass, entry, _platforms())
    assert mock_hass.config_entries.async_update_entry.call_count == 2
    # The notice is raised by the first import only
    assert _issue(hass, "entry_edit") is None

    yaml_file.unlink()
    restored = _platforms()
    await load_legacy_myhome_yaml(mock_hass, entry, restored)
    assert restored["light"]["15"]["name"] == "Porch"

    # An empty section for the gateway empties the copy (an empty `light: {}` fails validation)
    yaml_file.write_text(f"{MAC}: {{}}\n", encoding="utf-8")
    await load_legacy_myhome_yaml(mock_hass, entry, _platforms())
    yaml_file.unlink()
    cleared = _platforms()
    await load_legacy_myhome_yaml(mock_hass, entry, cleared)
    assert cleared["light"] == {}


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(f"{MAC}:\n  light: [unclosed\n", id="unparsable"),
        pytest.param(f"{MAC}:\n  light:\n    hall:\n      where: 'not-a-where'\n", id="invalid"),
        pytest.param("00:03:50:99:99:99:\n  light:\n    x:\n      where: '21'\n", id="other_gateway"),
        pytest.param("[]\n", id="not_a_mapping"),
    ],
)
async def test_imported_copy_used_when_yaml_is_unusable(
    hass: HomeAssistant, tmp_path: Path, content: str
) -> None:
    """A myhome.yaml without a usable section for the gateway falls back to the imported copy."""
    entry = _yaml_entry("entry_bad")
    mock_hass = _yaml_hass(hass, tmp_path, entry)
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(HALL_YAML, encoding="utf-8")
    await load_legacy_myhome_yaml(mock_hass, entry, _platforms())
    imported = entry.options[CONF_MIGRATED_YAML_PLATFORMS]

    yaml_file.write_text(content, encoding="utf-8")
    configured = _platforms()
    await load_legacy_myhome_yaml(mock_hass, entry, configured)

    assert configured["light"]["14"]["name"] == "Hall Light"
    assert entry.options[CONF_MIGRATED_YAML_PLATFORMS] == imported


async def test_imported_copy_used_when_top_level_yaml_is_ambiguous(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Top-level platforms without a MAC are refused with two gateways; the copy still loads."""
    entry = _yaml_entry("entry_a")
    entry.options = {
        CONF_MIGRATED_YAML_PLATFORMS: {"light": {"1-14": {"where": "14", "name": "Hall Light"}}}
    }
    mock_hass = _yaml_hass(hass, tmp_path, entry, _yaml_entry("entry_b", "00:03:50:00:00:02"))
    (tmp_path / "myhome.yaml").write_text("light:\n  hall:\n    where: '14'\n", encoding="utf-8")

    configured = _platforms()
    await load_legacy_myhome_yaml(mock_hass, entry, configured)
    assert configured["light"]["14"]["name"] == "Hall Light"


async def test_one_device_stays_one_object_live_and_from_the_copy(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """Aliases of a device (zone keys, the button platform) share one dict, also after JSON storage."""
    entry = _yaml_entry("entry_alias")
    mock_hass = _yaml_hass(hass, tmp_path, entry)
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        f"{MAC}:\n"
        "  light:\n    hall:\n      where: '14'\n      name: 'Hall Light'\n"
        "  climate:\n    office:\n      zone: '2'\n      name: 'Office'\n      heat: true\n",
        encoding="utf-8",
    )

    def assert_shared(configured: dict[str, dict[str, Any]]) -> None:
        assert configured["climate"]["2"] is configured["climate"]["zone_2"]
        assert configured["climate"]["2"] is configured["climate"]["office"]
        assert configured["button"]["1-14"] is configured["light"]["1-14"]

    live = _platforms("light", "climate", "button")
    await load_legacy_myhome_yaml(mock_hass, entry, live)
    assert_shared(live)

    yaml_file.unlink()
    restored = _platforms("light", "climate", "button")
    await load_legacy_myhome_yaml(mock_hass, entry, restored)
    assert_shared(restored)


async def test_f461_plant_yaml_migration_and_sweep_probe(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """The F461 test plant of the 2026-10-18 capture: its myhome.yaml imports, and relay 06 is probed after the sweep.

    Only the gateway MAC is read from the capture; the YAML mirrors the plant described in its README.
    """
    trace_path = TRACES / "f461_live_2026-10-18" / "myhome_trace_F461_live_2026-10-18.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    f461_mac = trace["gateway"]["mac"]

    entry = _yaml_entry("entry_f461", f461_mac)
    mock_hass = _yaml_hass(hass, tmp_path, entry)
    (tmp_path / "myhome.yaml").write_text(
        f"{f461_mac}:\n"
        "  light:\n"
        "    office_light:\n"
        "      where: '06'\n"
        "      name: 'Office Light'\n"
        "      model: 'BT-F411/4'\n"
        "    bath_light:\n"
        "      where: '07'\n"
        "      name: 'Bath Light'\n"
        "      model: 'BT-F411/4'\n"
        "  cover:\n"
        "    shutter_05:\n"
        "      where: '05'\n"
        "      name: 'Shutter 05'\n"
        "      travel_time: 5\n"
        "  climate:\n"
        "    thermo_office:\n"
        "      zone: '2'\n"
        "      name: 'Thermo Office'\n"
        "      heat: true\n"
        "      cool: true\n"
        "      standalone: true\n",
        encoding="utf-8",
    )

    configured_platforms = _platforms("light", "cover", "climate")
    await load_legacy_myhome_yaml(mock_hass, entry, configured_platforms)
    assert "06" in configured_platforms["light"]
    assert "07" in configured_platforms["light"]
    assert "05" in configured_platforms["cover"]
    assert "2" in configured_platforms["climate"]

    # Relay 06 seen ON during a sweep is probed on Dimension 1 only once the sweep is done
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()
    gw._begin_sweep()
    light_06 = _light(hass, gw, "06", probe_dimmer_on_add=True)
    light_06._poll_on_add = False

    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        with patch.object(
            light_06, "async_get_last_state", new_callable=AsyncMock, return_value=None
        ):
            await light_06.async_added_to_hass()
        await asyncio.sleep(0)
        mock_send.assert_not_called()
        gw._end_sweep()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_send.assert_awaited_once()
        assert str(mock_send.call_args[0][0]) == "*#1*06*1##"
