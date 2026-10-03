"""Regression test verifying that plant fixtures build all declared lights without drops (#402)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from homeassistant.core import HomeAssistant

from custom_components.myhome.const import CONF_PLATFORMS, DOMAIN
from custom_components.myhome.light import MyHOMELight, async_setup_entry
from custom_components.myhome.validate import config_schema
from tests.conftest import attach_runtime

FIXTURES_PLANTS = Path(__file__).parent / "fixtures" / "plants"


@pytest.mark.asyncio
async def test_plant_fixtures_lights_completeness(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Ensure all declared lights in real-world plant fixtures are built and not refused as broadcasts."""
    plant_dirs = [d for d in FIXTURES_PLANTS.iterdir() if d.is_dir() and (d / "myhome.yaml").is_file()]
    assert len(plant_dirs) >= 8

    for plant_dir in plant_dirs:
        yaml_path = plant_dir / "myhome.yaml"
        parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        validated = config_schema(parsed)

        for mac, mac_data in validated.items():
            platforms = mac_data.get(CONF_PLATFORMS, {})
            lights = platforms.get("light", {})
            if not lights:
                continue

            config_entry = MagicMock()
            config_entry.data = {"mac": mac}
            config_entry.entry_id = f"entry_{mac.replace(':', '_')}"
            gateway = MagicMock()
            gateway.mac = mac
            gateway.log_id = f"GATEWAY_{mac}"
            gateway.device_registry_id = None
            gateway.send = AsyncMock()
            gateway.send_status_request = AsyncMock()

            hass.data = {DOMAIN: {mac: {"entity": gateway, "platforms": {"light": lights}}}}

            with (
                patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
                patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
            ):
                async_add_entities = MagicMock()
                attach_runtime(hass, config_entry)
                await async_setup_entry(hass, config_entry, async_add_entities)

            # Every declared YAML light must be passed to async_add_entities
            async_add_entities.assert_called_once()
            added_entities = list(async_add_entities.call_args[0][0])
            assert len(added_entities) == len(lights), (
                f"Plant {plant_dir.name} declared {len(lights)} lights, but only {len(added_entities)} were built."
            )

            # Specifically ensure no light was rejected as a broadcast
            for entity in added_entities:
                assert isinstance(entity, MyHOMELight)
                assert f"Refusing to create a light entity for broadcast WHERE {entity._where}" not in caplog.text
