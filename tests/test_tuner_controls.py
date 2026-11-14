"""Tests for F500 / F500N FM tuner UI entities, actions, Media Browser, and registry hygiene."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.components.media_player import MediaClass, MediaPlayerEntityFeature, MediaType
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    UnitOfFrequency,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNSoundEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.button import (
    TunerSeekDownButtonEntity,
    TunerSeekUpButtonEntity,
)
from custom_components.myhome.button import (
    async_setup_entry as async_setup_buttons,
)
from custom_components.myhome.const import (
    CONF_SOURCE_NAME,
    CONF_SOURCE_TUNER,
    CONF_TUNER_PICTURES,
    DOMAIN,
    SERVICE_TUNER_SEEK_DOWN,
    SERVICE_TUNER_SEEK_UP,
    SERVICE_TUNER_SET_FREQUENCY,
    SERVICE_TUNER_STORE_STATION,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.media_player import (
    MyHOMEMediaPlayer,
    _prune_unchecked_tuner_entities,
)
from custom_components.myhome.number import (
    MyHOMETunerFrequencyNumber,
)
from custom_components.myhome.number import (
    async_setup_entry as async_setup_numbers,
)
from custom_components.myhome.number import (
    async_unload_entry as async_unload_number,
)
from custom_components.myhome.sound_source import MyHOMESoundSource, parse_station_slot
from tests.conftest import attach_platform


@pytest.fixture
def mock_gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:aa:bb:cc"
    gw.unique_id = "00:03:50:aa:bb:cc"
    gw.log_id = "[MyHOME]"
    gw.available = True
    gw.availability_signal = "myhome_avail_00:03:50:aa:bb:cc"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.profile_supports_who = lambda who: who in (16, 22)
    return gw


@pytest.mark.asyncio
async def test_tuner_store_station_and_browse_media(
    hass: HomeAssistant, mock_gateway: MagicMock
) -> None:
    """MyHOMESoundSource supports store_station (1-15) and BROWSE_MEDIA with presets + pictures."""
    tuner = MyHOMESoundSource(
        hass=hass,
        name="FM Radio",
        device_id="101#16",
        who="16",
        where="101",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=mock_gateway,
        pictures={105300: "/local/dimsoft.png", 96200: "https://example.com/oe3.png"},
    )
    tuner.hass = hass
    tuner.entity_id = "media_player.fm_radio"
    tuner.async_schedule_update_ha_state = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=mock_gateway)
    attach_platform(tuner, entry)

    assert bool(tuner.supported_features & MediaPlayerEntityFeature.BROWSE_MEDIA)

    # Dual-stack gateway (WHO 16 + WHO 22): slots 1..5 use WHO 16 *#16*101*#10*<N>##,
    # while extended F500N slots 6..15 delegate to WHO 22 *22*33#<N>*2#1## (bt_difson caps #10 at 5).
    await tuner.async_store_station(5)
    assert str(mock_gateway.send.call_args[0][0]) == "*#16*101*#10*5##"
    assert tuner.source == "Station 5"
    assert tuner.extra_state_attributes["station"] == 5

    await tuner.async_store_station(6)
    assert str(mock_gateway.send.call_args[0][0]) == "*22*33#6*2#1##"
    assert tuner.source == "Station 6"

    await tuner.async_store_station(12)
    assert str(mock_gateway.send.call_args[0][0]) == "*22*33#12*2#1##"
    assert len(tuner.source_list) == 12
    assert tuner.source == "Station 12"

    # Whole-number float and integer string are accepted cleanly
    await tuner.async_store_station(4.0)  # type: ignore[arg-type]
    assert str(mock_gateway.send.call_args[0][0]) == "*#16*101*#10*4##"
    await tuner.async_store_station(" 5 ")  # type: ignore[arg-type]
    assert str(mock_gateway.send.call_args[0][0]) == "*#16*101*#10*5##"
    await tuner.async_select_station(3.0)  # type: ignore[arg-type]
    assert str(mock_gateway.send.call_args[0][0]) == "*#16*101*#7*3##"
    await tuner.async_select_station("2")  # type: ignore[arg-type]
    assert str(mock_gateway.send.call_args[0][0]) == "*#16*101*#7*2##"

    # parse_station_slot covers all scalar/non-scalar branches
    assert parse_station_slot(5) == 5
    assert parse_station_slot(5.0) == 5
    assert parse_station_slot(" 5 ") == 5
    for bad_slot in (True, False, 1.9, float("inf"), float("nan"), "1.9", "abc", None, object()):
        assert parse_station_slot(bad_slot) is None

    # Out-of-range, boolean, fractional float, or non-integer station raises HomeAssistantError
    mock_gateway.send.reset_mock()
    for bad in (0, 16, -1, True, False, 1.9, "1.9", "abc", float("nan"), float("inf"), None):
        with pytest.raises(HomeAssistantError) as exc_info:
            await tuner.async_store_station(bad)  # type: ignore[arg-type]
        assert exc_info.value.translation_key == "unknown_station"
        with pytest.raises(HomeAssistantError) as sel_exc:
            await tuner.async_select_station(bad)  # type: ignore[arg-type]
        assert sel_exc.value.translation_key == "unknown_station"
    for bad_freq in (True, False, "abc", float("nan"), float("inf"), None):
        with pytest.raises(HomeAssistantError) as freq_exc:
            await tuner.async_set_frequency(bad_freq)  # type: ignore[arg-type]
        assert freq_exc.value.translation_key == "frequency_out_of_range"
    mock_gateway.send.assert_not_called()

    # Dimension 7 report from bus updates active station; write-only Dimension 10 is ignored
    tuner.handle_event(OWNSoundEvent("*#16*101*7*0*3##"))
    assert tuner.source == "Station 3"
    tuner.handle_event(OWNSoundEvent("*#16*101*10*4##"))
    assert tuner.source == "Station 3"

    # Browse media lists all 12 stations + 2 sorted picture frequencies
    browse = await tuner.async_browse_media()
    assert browse.media_class == MediaClass.DIRECTORY
    assert browse.can_expand is True
    assert browse.children is not None
    assert len(browse.children) == 14
    assert browse.children[0].title == "Station 1"
    assert browse.children[0].media_content_id == "1"
    assert browse.children[0].media_content_type == MediaType.CHANNEL
    assert browse.children[12].title == "96.20 MHz"
    assert browse.children[12].media_content_id == "96.20"
    assert browse.children[12].thumbnail == "https://example.com/oe3.png"
    assert browse.children[13].title == "105.30 MHz"
    assert browse.children[13].media_content_id == "105.30"
    assert browse.children[13].thumbnail == "/local/dimsoft.png"


@pytest.mark.asyncio
async def test_who16_only_gateway_caps_store_station_at_five(
    hass: HomeAssistant, mock_gateway: MagicMock
) -> None:
    """WHO 16-only gateways (MH200, H4684 FW < 6.0) cap store_station at 1..5 without emitting NACKed frames."""
    mock_gateway.profile_supports_who = lambda who: who == 16
    tuner = MyHOMESoundSource(
        hass=hass,
        name="FM Radio",
        device_id="101#16",
        who="16",
        where="101",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=mock_gateway,
    )
    tuner.hass = hass
    tuner.entity_id = "media_player.fm_radio"
    tuner.async_schedule_update_ha_state = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_MAC: mock_gateway.mac}
    entry.runtime_data = MyHOMERuntimeData(gateway=mock_gateway)
    attach_platform(tuner, entry)

    await tuner.async_store_station(1)
    await tuner.async_store_station(5)
    assert [str(c.args[0]) for c in mock_gateway.send.call_args_list] == [
        "*#16*101*#10*1##",
        "*#16*101*#10*5##",
    ]
    assert tuner.source == "Station 5"

    mock_gateway.send.reset_mock()
    for bad in (0, -1, 6, 12, 15, 16, True, False):
        with pytest.raises(HomeAssistantError) as exc_info:
            await tuner.async_store_station(bad)
        assert exc_info.value.translation_key == "unknown_station"
        assert "(1-5)" in str(exc_info.value)
    mock_gateway.send.assert_not_called()

    # Also verify non-callable profile_supports_who defaults safely to the 1..5 WHO 16 ceiling
    mock_gateway.profile_supports_who = None
    with pytest.raises(HomeAssistantError) as exc_unprofiled:
        await tuner.async_store_station(6)
    assert exc_unprofiled.value.translation_key == "unknown_station"
    mock_gateway.send.assert_not_called()


@pytest.mark.asyncio
async def test_zone_player_rejects_tuner_only_services(
    hass: HomeAssistant, mock_gateway: MagicMock
) -> None:
    """Room amplifier zones reject tuner_set_frequency and tuner_store_station with tuner_only_operation."""
    player = MyHOMEMediaPlayer(
        hass=hass,
        name="Audio Zone 42",
        entity_name=None,
        device_id="42#16",
        who="16",
        where="42",
        manufacturer="BTicino",
        model="Audio System",
        gateway=mock_gateway,
    )
    player.entity_id = "media_player.audio_zone_42"

    with pytest.raises(HomeAssistantError) as err_freq:
        await player.async_set_frequency(105.3)
    assert err_freq.value.translation_key == "tuner_only_operation"

    with pytest.raises(HomeAssistantError) as err_store:
        await player.async_store_station(1)
    assert err_store.value.translation_key == "tuner_only_operation"


@pytest.mark.asyncio
async def test_tuner_number_and_seek_buttons_delegate_to_sound_source(
    hass: HomeAssistant, mock_gateway: MagicMock
) -> None:
    """The frequency number and seek buttons drive their MyHOMESoundSource on WHO 16 and WHO 22 gateways."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: mock_gateway.mac},
        options={
            CONF_SOURCE_NAME.format(1): "FM Radio",
            CONF_SOURCE_TUNER.format(1): True,
            CONF_SOURCE_TUNER.format(2): False,
        },
    )
    entry.add_to_hass(hass)
    runtime = MyHOMERuntimeData(gateway=mock_gateway, platforms={"button": {}, "number": {}})
    entry.runtime_data = runtime

    added_numbers: list[MyHOMETunerFrequencyNumber] = []
    assert await async_setup_numbers(hass, entry, lambda ents: added_numbers.extend(ents))
    assert len(added_numbers) == 1
    num = added_numbers[0]
    num.hass = hass
    num.entity_id = "number.fm_radio_frequency"
    num.async_write_ha_state = MagicMock()
    attach_platform(num, entry)

    assert num.device_class == NumberDeviceClass.FREQUENCY
    assert num.native_unit_of_measurement == UnitOfFrequency.MEGAHERTZ
    assert num.native_min_value == 87.5
    assert num.native_max_value == 108.0
    assert num.native_step == 0.05
    assert num.mode == NumberMode.BOX
    assert num.unique_id == f"{mock_gateway.mac}-16-101#16-frequency"

    # Restore valid vs invalid state
    await num.async_restore_last_state(State("number.fm_radio_frequency", "unknown"))
    assert num.native_value is None
    await num.async_restore_last_state(State("number.fm_radio_frequency", "nan"))
    assert num.native_value is None
    await num.async_restore_last_state(State("number.fm_radio_frequency", "105.3"))
    assert num.native_value == 105.3

    await num.async_added_to_hass()
    await num.async_update()

    added_buttons: list = []
    assert await async_setup_buttons(hass, entry, lambda ents: added_buttons.extend(ents))
    seek_up = next(b for b in added_buttons if isinstance(b, TunerSeekUpButtonEntity))
    seek_down = next(b for b in added_buttons if isinstance(b, TunerSeekDownButtonEntity))
    assert seek_up.unique_id == f"{mock_gateway.mac}-16-101#16-seek-up"
    assert seek_down.unique_id == f"{mock_gateway.mac}-16-101#16-seek-down"
    await seek_up.async_update()

    # Before the media player platform registers the tuner, commands fail cleanly.
    mock_gateway.send.reset_mock()
    for action in (num.async_set_native_value(99.5), seek_up.async_press(), seek_down.async_press()):
        with pytest.raises(HomeAssistantError) as not_ready:
            await action
        assert not_ready.value.translation_key == "tuner_not_ready"
    mock_gateway.send.assert_not_called()

    tuner = MyHOMESoundSource(
        hass=hass,
        name="FM Radio",
        device_id="101#16",
        who="16",
        where="101",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=mock_gateway,
    )
    tuner.hass = hass
    tuner.entity_id = "media_player.fm_radio"
    tuner.async_write_ha_state = MagicMock()
    tuner.async_schedule_update_ha_state = MagicMock()
    attach_platform(tuner, entry)
    runtime.sound_sources["101"] = tuner

    # Bus reports reach the number through the tuner (WHO 16 dim 6, WHO 22 dim 5).
    tuner.handle_event(OWNSoundEvent("*#16*101*6*0*98500##"))
    assert num.native_value == 98.5
    tuner.handle_who22_event(
        SimpleNamespace(who="22", where="5", where_param=["2", "1"], dimension=5, dimension_value=["1", "10150"])
    )
    assert num.native_value == 101.5

    # WHO 16 gateway: the tuner sends bt_difson frames.
    await num.async_set_native_value(107.25)
    await seek_up.async_press()
    await seek_down.async_press()
    assert [str(c.args[0]) for c in mock_gateway.send.call_args_list] == [
        "*#16*101*#6*0*107250##",
        "*16*5000*101##",
        "*16*5100*101##",
    ]
    assert num.native_value == 107.25

    with pytest.raises(HomeAssistantError) as out_of_band:
        await num.async_set_native_value(70.0)
    assert out_of_band.value.translation_key == "frequency_out_of_range"

    # WHO 22-only gateway: the same entities send bt_multi frames.
    mock_gateway.send.reset_mock()
    mock_gateway.profile_supports_who = lambda who: who == 22
    await num.async_set_native_value(99.5)
    await seek_up.async_press()
    await seek_down.async_press()
    assert [str(c.args[0]) for c in mock_gateway.send.call_args_list] == [
        "*#22*2#1*#5*1*9950##",
        "*22*5*2#1##",
        "*22*6*2#1##",
    ]
    assert num.native_value == 99.5

    assert await async_unload_number(hass, entry) is True

@pytest.mark.asyncio
async def test_full_config_entry_tuner_device_grouping_reload_and_uncheck_pruning(
    hass: HomeAssistant,
) -> None:
    """Full HA entry lifecycle: 4 entities share Audio Source device, reload never duplicates 101#16, uncheck prunes."""
    mac = "00:03:50:aa:bb:dd"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="H/L4684 Gateway",
        data={
            CONF_HOST: "192.0.2.20",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: mac,
            CONF_NAME: "H/L4684",
        },
        options={
            CONF_SOURCE_NAME.format(1): "FM Radio",
            CONF_SOURCE_TUNER.format(1): True,
            CONF_TUNER_PICTURES: "105.3=/local/dimsoft.png",
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
        patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send",
            new_callable=AsyncMock,
        ) as mock_send,
        patch(
            "custom_components.myhome.gateway.MyHOMEGatewayHandler.send_status_request",
            new_callable=AsyncMock,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entry.runtime_data.gateway._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)

        mp_eid = ent_reg.async_get_entity_id("media_player", DOMAIN, f"{mac}-16-101#16")
        num_eid = ent_reg.async_get_entity_id("number", DOMAIN, f"{mac}-16-101#16-frequency")
        up_eid = ent_reg.async_get_entity_id("button", DOMAIN, f"{mac}-16-101#16-seek-up")
        dn_eid = ent_reg.async_get_entity_id("button", DOMAIN, f"{mac}-16-101#16-seek-down")
        assert mp_eid is not None
        assert num_eid is not None
        assert up_eid is not None
        assert dn_eid is not None

        # All 4 entities attach to the exact same Audio Source device!
        dev_ids = {
            ent_reg.async_get(eid).device_id
            for eid in (mp_eid, num_eid, up_eid, dn_eid)
        }
        assert len(dev_ids) == 1
        tuner_dev_id = next(iter(dev_ids))
        assert tuner_dev_id is not None
        tuner_dev = dev_reg.async_get(tuner_dev_id)
        assert tuner_dev is not None
        assert tuner_dev.model == "Audio Source"

        # Setting number entity updates MyHOMESoundSource frequency and sends *#16*101*#6*0*105300##
        mock_send.reset_mock()
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": num_eid, "value": 105.3},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert [str(c.args[0]) for c in mock_send.call_args_list] == ["*#16*101*#6*0*105300##"]
        assert float(hass.states.get(num_eid).state) == 105.3
        assert hass.states.get(mp_eid).attributes["frequency"] == 105.3

        # Entity services: tuner_set_frequency, tuner_store_station, tuner_seek_up, tuner_seek_down + buttons
        mock_send.reset_mock()
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_SET_FREQUENCY,
            {"entity_id": mp_eid, "frequency": 98.75},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_STORE_STATION,
            {"entity_id": mp_eid, "station": 5},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_SEEK_UP,
            {"entity_id": mp_eid},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_SEEK_DOWN,
            {"entity_id": mp_eid},
            blocking=True,
        )
        await hass.services.async_call("button", "press", {"entity_id": up_eid}, blocking=True)
        await hass.services.async_call("button", "press", {"entity_id": dn_eid}, blocking=True)
        await hass.async_block_till_done()

        assert [str(c.args[0]) for c in mock_send.call_args_list] == [
            "*#16*101*#6*0*98750##",
            "*#16*101*#10*5##",
            "*16*5000*101##",
            "*16*5100*101##",
            "*16*5000*101##",
            "*16*5100*101##",
        ]
        assert float(hass.states.get(num_eid).state) == 98.75
        assert hass.states.get(mp_eid).attributes["station"] == 5

        # Service-layer schemas reject booleans, fractional floats, NaN/Inf, and non-numeric strings
        # BEFORE reaching the entity (preventing vol.Coerce(int)(True/1.9) == 1 from overwriting Station 1).
        mock_send.reset_mock()
        for bad_station in (True, False, 1.9, "1.9", "abc", float("nan"), None):
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_TUNER_STORE_STATION,
                    {"entity_id": mp_eid, "station": bad_station},
                    blocking=True,
                )
        for bad_frequency in (True, False, "abc", "nan", float("inf"), None):
            with pytest.raises(vol.Invalid):
                await hass.services.async_call(
                    DOMAIN,
                    SERVICE_TUNER_SET_FREQUENCY,
                    {"entity_id": mp_eid, "frequency": bad_frequency},
                    blocking=True,
                )
        mock_send.assert_not_called()

        # Whole-number float and integer string still work through the HA service schema
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_STORE_STATION,
            {"entity_id": mp_eid, "station": "4"},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_STORE_STATION,
            {"entity_id": mp_eid, "station": 5.0},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TUNER_SET_FREQUENCY,
            {"entity_id": mp_eid, "frequency": "101.20"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert [str(c.args[0]) for c in mock_send.call_args_list] == [
            "*#16*101*#10*4##",
            "*#16*101*#10*5##",
            "*#16*101*#6*0*101200##",
        ]

        # Reload entry: PlatformDiscovery MUST NOT restore 101#16 as a duplicate MyHOMEMediaPlayer!
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        mp_entries = [
            e
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            if e.domain == "media_player"
        ]
        assert len(mp_entries) == 1
        assert mp_entries[0].entity_id == mp_eid

        # Uncheck CONF_SOURCE_TUNER and reload: all 4 tuner entities and the empty Audio Source device are pruned!
        hass.config_entries.async_update_entry(
            entry,
            options={
                CONF_SOURCE_NAME.format(1): "Line In",
                CONF_SOURCE_TUNER.format(1): False,
            },
        )
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

        for uid_tail in ("16-101#16", "16-101#16-frequency", "16-101#16-seek-up", "16-101#16-seek-down"):
            for dom in ("media_player", "number", "button"):
                assert ent_reg.async_get_entity_id(dom, DOMAIN, f"{mac}-{uid_tail}") is None
        assert dev_reg.async_get(tuner_dev_id) is None


@pytest.mark.asyncio
async def test_tuner_guard_branches_and_edge_cases(hass: HomeAssistant) -> None:
    """Cover guard branches across number, button, sound_source, and media_player pruning."""
    ent_reg = er.async_get(hass)

    # 1. _prune_unchecked_tuner_entities: non-Mapping options and all-checked slots
    non_mapping_entry = MagicMock()
    non_mapping_entry.options = MagicMock()
    _prune_unchecked_tuner_entities(hass, ent_reg, non_mapping_entry, ("00:11-",))

    all_checked_entry = MockConfigEntry(
        domain=DOMAIN,
        options={CONF_SOURCE_TUNER.format(i): True for i in range(1, 5)},
    )
    all_checked_entry.add_to_hass(hass)
    _prune_unchecked_tuner_entities(hass, ent_reg, all_checked_entry, ("00:11-",))

    # 2. number.async_setup_entry: None runtime, follower without WHO 16/22, and non-Mapping options
    no_rt_entry = MockConfigEntry(domain=DOMAIN)
    no_rt_entry.add_to_hass(hass)
    assert await async_setup_numbers(hass, no_rt_entry, lambda _e: None) is True

    # A number whose entry lost its runtime data adds without following any tuner
    orphan_gw = MagicMock()
    orphan_gw.mac = "00:11:22:33:44:98"
    orphan_num = MyHOMETunerFrequencyNumber(
        hass=hass, config_entry=no_rt_entry, where="101", name="Radio", gateway=orphan_gw
    )
    orphan_num.hass = hass
    orphan_num.entity_id = "number.radio_frequency"
    attach_platform(orphan_num, no_rt_entry)
    await orphan_num.async_added_to_hass()
    assert orphan_num.native_value is None

    follower_gw = MagicMock()
    follower_gw.mac = "00:11:22:33:44:99"
    follower_gw.is_follower = True
    follower_gw.delegated_whos = {1, 2}
    follower_gw.profile_supports_who = None
    follower_gw.is_who_available = MagicMock(return_value=True)
    follower_entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: follower_gw.mac})
    follower_entry.add_to_hass(hass)
    follower_entry.runtime_data = MyHOMERuntimeData(gateway=follower_gw)
    assert await async_setup_numbers(hass, follower_entry, lambda _e: None) is True

    follower_gw.is_follower = False
    non_mapping_entry.runtime_data = MyHOMERuntimeData(gateway=follower_gw)
    assert await async_setup_numbers(hass, non_mapping_entry, lambda _e: None) is True

    # 3. Gateway with non-callable profile_supports_who + standalone sound_source _notify_frequency
    standalone_source = MyHOMESoundSource(
        hass=hass,
        name="Radio 1",
        device_id="101#16",
        who="16",
        where="101",
        manufacturer="BTicino",
        model="Audio Source",
        gateway=follower_gw,
    )
    assert standalone_source._uses_who22_commands() is False
    assert standalone_source.available is True
    standalone_source._notify_frequency()  # _frequency_khz is None
    standalone_source._frequency_khz = 105300
    standalone_source._notify_frequency()  # platform / runtime is None

    # 4. Number seeded from pre-existing runtime.sound_sources + out-of-band _apply_khz + non-callable supports
    follower_gw.is_follower = False
    seed_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: follower_gw.mac},
        options={CONF_SOURCE_TUNER.format(1): True},
    )
    seed_entry.add_to_hass(hass)
    seed_runtime = MyHOMERuntimeData(gateway=follower_gw)
    seed_runtime.sound_sources["101"] = standalone_source
    seed_entry.runtime_data = seed_runtime

    seeded_nums: list[MyHOMETunerFrequencyNumber] = []
    assert await async_setup_numbers(hass, seed_entry, lambda ents: seeded_nums.extend(ents))
    seeded_num = seeded_nums[0]
    seeded_num.hass = hass
    seeded_num.entity_id = "number.radio_1_frequency"
    seeded_num.async_write_ha_state = MagicMock()
    attach_platform(seeded_num, seed_entry)
    await seeded_num.async_added_to_hass()
    assert seeded_num.native_value == 105.3
    assert seeded_num.available is True
    seeded_num._apply_khz(None)
    seeded_num._apply_khz(50000)
    assert seeded_num.native_value == 105.3

    # 5. Button availability with non-callable profile_supports_who
    btn = TunerSeekUpButtonEntity(
        hass=hass,
        config_entry=seed_entry,
        where="101",
        name="Radio 1",
        gateway=follower_gw,
    )
    assert btn.available is True


def test_typing_compat_undefined_avoids_ha_2026_12_deprecation() -> None:
    """Verify typing_compat resolves Undefined on HA 2026.12+ without touching deprecated UNDEFINED."""
    import types

    import homeassistant.helpers.typing as ha_typing

    from custom_components.myhome.typing_compat import UNDEFINED, _resolve_undefined

    sentinel = object()
    fake_dev_typing = types.ModuleType("homeassistant.helpers.typing")
    fake_dev_typing.Undefined = sentinel  # type: ignore[attr-defined]

    def _forbid_deprecated_undefined(name: str) -> object:
        if name == "UNDEFINED":
            raise AssertionError("Deprecated UNDEFINED alias must not be accessed when Undefined exists")
        raise AttributeError(name)

    fake_dev_typing.__getattr__ = _forbid_deprecated_undefined  # type: ignore[method-assign]
    assert _resolve_undefined(fake_dev_typing) is sentinel

    fake_stable_typing = types.ModuleType("homeassistant.helpers.typing")
    fake_stable_typing.UNDEFINED = sentinel  # type: ignore[attr-defined]
    assert _resolve_undefined(fake_stable_typing) is sentinel
    assert UNDEFINED is _resolve_undefined(ha_typing)


