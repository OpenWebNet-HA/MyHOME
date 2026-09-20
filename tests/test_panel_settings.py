"""Native options navigation and persistent, installation-wide panel preferences."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import voluptuous as vol
from homeassistant.components import frontend
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.panel import (
    PANEL_STORAGE_KEY,
    PANEL_URL,
    PanelPreferences,
    async_get_panel_sidebar,
    async_set_panel_sidebar,
    async_setup_panel,
)


async def test_sidebar_visibility_is_persistent_and_does_not_remove_panel(hass):
    hass.config.components.add("frontend")
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    await async_setup_panel(hass, "/card.js")
    original = hass.data[frontend.DATA_PANELS][PANEL_URL]
    assert original.sidebar_title == "MyHOME"
    await async_set_panel_sidebar(hass, False)
    hidden = hass.data[frontend.DATA_PANELS][PANEL_URL]
    assert hidden.sidebar_title is None
    assert hidden.require_admin is True
    assert hidden.config == original.config
    # A fresh preference object reads HA storage, not the previous runtime cache.
    restored = PanelPreferences(hass)
    await restored.async_load()
    assert restored.show_sidebar is False
    await async_setup_panel(hass, "/card.js")
    assert hass.data[frontend.DATA_PANELS][PANEL_URL].sidebar_title is None
    await async_set_panel_sidebar(hass, True)
    assert hass.data[frontend.DATA_PANELS][PANEL_URL].sidebar_title == "MyHOME"
    hass.http.async_register_static_paths.assert_awaited_once()


async def test_sidebar_can_be_hidden_before_frontend_setup(hass):
    await async_set_panel_sidebar(hass, False)
    assert await async_get_panel_sidebar(hass) is False
    hass.config.components.add("frontend")
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    await async_setup_panel(hass, "/card.js")
    assert hass.data[frontend.DATA_PANELS][PANEL_URL].sidebar_title is None
    with pytest.raises(vol.Invalid):
        await async_set_panel_sidebar(hass, "false")


async def test_invalid_stored_preference_keeps_compatible_default(hass):
    await Store(hass, 1, PANEL_STORAGE_KEY).async_save({"show_sidebar": "false"})
    assert await async_get_panel_sidebar(hass) is True


async def test_configure_panel_link_and_shared_setting_preserve_gateway_options(hass):
    entries = []
    for index in range(2):
        entry = MockConfigEntry(
            domain=DOMAIN, data={"host": f"192.0.2.{index + 1}"},
            options={"command_worker_count": 3, "decoder_1_entity": "media_player.radio"},
        )
        entry.add_to_hass(hass)
        entries.append(entry)
    original_data, original_options = dict(entries[0].data), dict(entries[0].options)
    menu = await hass.config_entries.options.async_init(entries[0].entry_id)
    assert menu["type"] == "menu"
    assert menu["menu_options"] == ["panel", "user"]
    form = await hass.config_entries.options.async_configure(menu["flow_id"], {"next_step_id": "panel"})
    url = urlsplit(form["description_placeholders"]["panel_url"])
    assert url.path == "/myhome"
    assert parse_qs(url.query) == {"entry_id": [entries[0].entry_id]}
    assert form["data_schema"]({}) == {"show_sidebar": True}
    # An I/O failure leaves the form open and does not change the active preference.
    with patch("custom_components.myhome.panel.Store.async_save", side_effect=OSError("disk full")):
        failed = await hass.config_entries.options.async_configure(form["flow_id"], {"show_sidebar": False})
    assert failed["errors"] == {"base": "panel_save_failed"}
    assert await async_get_panel_sidebar(hass) is True
    with patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as reload_entry:
        saved = await hass.config_entries.options.async_configure(form["flow_id"], {"show_sidebar": False})
        await hass.async_block_till_done()
    assert saved["type"] == "create_entry"
    assert entries[0].data == original_data
    assert entries[0].options == original_options
    reload_entry.assert_not_awaited()
    other_menu = await hass.config_entries.options.async_init(entries[1].entry_id)
    other = await hass.config_entries.options.async_configure(other_menu["flow_id"], {"next_step_id": "panel"})
    assert other["data_schema"]({}) == {"show_sidebar": False}
    hass.config_entries.options.async_abort(other["flow_id"])
