"""Tests for Issue #733: sweep gating, dimmer capability probe, and legacy YAML migration."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant

from custom_components.myhome.const import CONF_PLATFORMS
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.legacy_yaml import (
    CONF_MIGRATED_YAML_PLATFORMS,
    load_legacy_myhome_yaml,
    migrate_legacy_yaml_to_entry_options,
)
from custom_components.myhome.light import MyHOMELight


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


async def test_entity_added_mid_sweep_defers_status_poll(hass: HomeAssistant) -> None:
    """Entities added while gateway.sweep_active is True defer status polls to background task."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()
    gw._begin_sweep()

    entity = MyHOMELight(
        hass=hass,
        name="Sweep Light 21",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="1-21",
        who="1",
        where="21",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )
    entity.entity_id = "light.sweep_light_21"
    entity.platform = MagicMock()

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


async def test_light_async_probe_dimmer_capability(hass: HomeAssistant) -> None:
    """async_probe_dimmer_capability queries Dimension 1 after active sweeps for ON/OFF lights."""
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()

    entity = MyHOMELight(
        hass=hass,
        name="Probe Light 25",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="1-25",
        who="1",
        where="25",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="F411",
        gateway=gw,
    )

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

    # Already-dimmable or feature-locked light does not need probing
    dimmer_entity = MyHOMELight(
        hass=hass,
        name="Dimmer Light 26",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="1-26",
        who="1",
        where="26",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="F418",
        gateway=gw,
    )
    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        await dimmer_entity.async_probe_dimmer_capability()
        mock_send.assert_not_called()

    # Post-sweep recheck: if promoted to dimmable during the sweep, skip probe
    promoted_entity = MyHOMELight(
        hass=hass,
        name="Promoted Light 27",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="1-27",
        who="1",
        where="27",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="F418",
        gateway=gw,
        probe_dimmer_on_add=True,
    )
    promoted_entity.entity_id = "light.promoted_light_27"
    promoted_entity.platform = MagicMock()
    promoted_entity._poll_on_add = False

    gw._begin_sweep()
    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        with patch.object(
            promoted_entity, "async_get_last_state", new_callable=AsyncMock, return_value=None
        ):
            await promoted_entity.async_added_to_hass()
        await asyncio.sleep(0)
        # Promote while sweep is still active
        promoted_entity._lock_features = True
        gw._end_sweep()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_send.assert_not_called()

    for cb in list(getattr(promoted_entity, "_on_remove", None) or []):
        cb()


async def test_legacy_yaml_migration_and_fallback(hass: HomeAssistant, tmp_path) -> None:
    """Validated myhome.yaml platforms are migrated to entry.options once and used as fallback."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.myhome.const import DOMAIN
    from custom_components.myhome.repairs import ISSUE_LEGACY_YAML_IMPORTED

    mock_hass = MagicMock(spec=HomeAssistant)
    mock_hass.data = hass.data
    mock_hass.bus = hass.bus
    mock_hass.config = MagicMock()
    mock_hass.config.path = MagicMock(side_effect=lambda p: str(tmp_path / p))
    mock_hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *args: f(*args))
    mock_hass.config_entries = MagicMock()

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "entry_1"
    entry.data = {CONF_MAC: "00:03:50:81:22:33"}
    entry.options = {}

    mock_hass.config_entries.async_entries = MagicMock(return_value=[entry])
    mock_hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    def _update_entry(target_entry, *, options=None, **kwargs):
        if options is not None:
            target_entry.options = options

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
        "00:03:50:81:22:33:\n  light:\n    hall:\n      where: '14'\n      name: 'Hall Light'\n",
        encoding="utf-8",
    )

    configured_platforms: dict[str, dict[str, dict]] = {"light": {}, "climate": {}}
    await load_legacy_myhome_yaml(mock_hass, entry, configured_platforms)

    assert "14" in configured_platforms["light"]
    assert CONF_MIGRATED_YAML_PLATFORMS in entry.options
    assert mock_hass.config_entries.async_update_entry.call_count == 1
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, f"{ISSUE_LEGACY_YAML_IMPORTED}_entry_1") is not None

    # Second call does not re-migrate when already present in entry.options
    assert (
        migrate_legacy_yaml_to_entry_options(
            mock_hass, entry, entry.options[CONF_MIGRATED_YAML_PLATFORMS]
        )
        is False
    )

    # Empty yaml_platforms or unregistered entry returns False
    unregistered = MagicMock(spec=ConfigEntry)
    unregistered.entry_id = "missing"
    unregistered.options = {}
    mock_hass.config_entries.async_get_entry.return_value = None
    assert (
        migrate_legacy_yaml_to_entry_options(
            mock_hass, unregistered, {"light": {"14": {"where": "14"}}}
        )
        is False
    )

    # Remove myhome.yaml from disk: fallback to entry.options[CONF_MIGRATED_YAML_PLATFORMS]
    yaml_file.unlink()
    configured_platforms_after_removal: dict[str, dict[str, dict]] = {
        "light": {},
        "climate": {},
    }
    with patch("os.path.isfile", return_value=False):
        await load_legacy_myhome_yaml(mock_hass, entry, configured_platforms_after_removal)

    assert "14" in configured_platforms_after_removal["light"]
    assert configured_platforms_after_removal["light"]["14"]["name"] == "Hall Light"
    assert CONF_PLATFORMS != CONF_MIGRATED_YAML_PLATFORMS


async def test_f461_live_2026_10_18_yaml_migration_and_sweep_probe(
    hass: HomeAssistant, tmp_path
) -> None:
    """Verify F461 live trace plant myhome.yaml migration and sweep-gated *#1*06*1## dimmer probe (#733, #742)."""
    import json
    from pathlib import Path

    from custom_components.myhome.light import MyHOMELight

    trace_path = (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "f461_live_2026-10-18"
        / "myhome_trace_F461_live_2026-10-18.json"
    )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    f461_mac = trace["gateway"]["mac"]

    mock_hass = MagicMock(spec=HomeAssistant)
    mock_hass.data = hass.data
    mock_hass.bus = hass.bus
    mock_hass.config = MagicMock()
    mock_hass.config.path = MagicMock(side_effect=lambda p: str(tmp_path / p))
    mock_hass.async_add_executor_job = AsyncMock(side_effect=lambda f, *args: f(*args))
    mock_hass.config_entries = MagicMock()

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "entry_f461"
    entry.data = {CONF_MAC: f461_mac}
    entry.options = {}
    mock_hass.config_entries.async_entries = MagicMock(return_value=[entry])
    mock_hass.config_entries.async_get_entry = MagicMock(return_value=entry)

    def _update_entry(target_entry, *, options=None, **kwargs):
        if options is not None:
            target_entry.options = options

    mock_hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(
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

    configured_platforms: dict[str, dict[str, dict]] = {
        "light": {},
        "cover": {},
        "climate": {},
    }
    await load_legacy_myhome_yaml(mock_hass, entry, configured_platforms)
    assert "06" in configured_platforms["light"]
    assert "07" in configured_platforms["light"]
    assert "05" in configured_platforms["cover"]
    assert "2" in configured_platforms["climate"]

    # Verify sweep-gated *#1*06*1## probe for F461 light 06 discovered during startup sweep
    gw = _make_gateway(hass)
    gw._initial_discovery_done.set()
    gw._begin_sweep()
    light_06 = MyHOMELight(
        hass=hass,
        name="Office Light",
        entity_name="Office Light",
        icon=None,
        icon_on=None,
        device_id="1-06",
        who="1",
        where="06",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="BT-F411/4",
        gateway=gw,
        probe_dimmer_on_add=True,
    )
    light_06.entity_id = "light.office_light"
    light_06.platform = MagicMock()
    light_06._poll_on_add = False

    with patch.object(gw, "send_status_request", new_callable=AsyncMock) as mock_send:
        with patch.object(
            light_06, "async_get_last_state", new_callable=AsyncMock, return_value=None
        ):
            await light_06.async_added_to_hass()
        await asyncio.sleep(0)
        # While startup sweep (*#1*0##, *#2*0##) is active, *#1*06*1## has not been sent yet
        mock_send.assert_not_called()
        gw._end_sweep()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        mock_send.assert_awaited_once()
        assert str(mock_send.call_args[0][0]) == "*#1*06*1##"

    for cb in list(getattr(light_06, "_on_remove", None) or []):
        cb()


