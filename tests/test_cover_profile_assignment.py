"""Whole-selection confirmation, eligibility, persistence and runtime application."""
import copy
from unittest.mock import patch

import pytest
from aiohttp.resolver import ThreadedResolver
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_profile_catalogue import WS_MANAGE, manage_profile
from custom_components.myhome.cover_profiles import ProfileError, register_api, write_profile
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_profile_catalogue import catalogue as catalogue_fixture
from tests.test_cover_profile_catalogue import plant as plant_fixture
from tests.test_cover_profile_catalogue import request
from tests.test_cover_profiles import message

plant = plant_fixture
catalogue = catalogue_fixture


async def proposal(hass, cat, **extra):
    result = await manage_profile(hass, request(cat, 'duplicate', name='Destination'))
    return request(cat, 'preview_assign', profile_id=result['profile_id'],
                   entity_ids=[cover.entity_id for cover in cat.plant.covers[:2]], **extra)


async def test_atomic_assignment_preserves_overrides_evidence_and_other_gateway(hass, catalogue):
    cat = catalogue
    await write_profile(hass, message(cat.plant, 2, index=1, action='overrides', overrides={'opening': 12}))
    msg = await proposal(hass, cat)
    before = copy.deepcopy(cat.store.data)
    preview = await manage_profile(hass, msg)
    assert cat.store.data == before
    assert len(preview['targets']) == 2
    assert preview['targets'][1]['changes']['opening'] == {'before': 12, 'after': 12, 'overridden': True}
    assert preview['targets'][0]['previous_profile_id'] == cat.profile_id
    for cover in cat.plant.covers:
        cover.async_write_ha_state.reset_mock()
    with patch.object(cat.store.store, 'async_save', wraps=cat.store.store.async_save) as save:
        result = await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})
    save.assert_awaited_once()
    assert result['revision'] == before['revision'] + 1
    assert cat.store.data['profiles'] == before['profiles']
    assert cat.store.data['covers'] == before['covers']
    assert cat.store.data['native_fallbacks'] == before['native_fallbacks']
    assert all(cat.store.data['assignments'][cover.unique_id] == msg['profile_id'] for cover in cat.plant.covers[:2])
    assert cat.plant.covers[1]._travel_time_up == 12
    cat.plant.covers[2].async_write_ha_state.assert_not_called()
    for gateway in cat.plant.gateways:
        gateway.send.assert_not_called()
    with pytest.raises(ProfileError, match='revision_conflict'):
        await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})


async def test_default_and_native_before_values_and_unselected_associations(hass, catalogue):
    cat = catalogue
    await write_profile(hass, message(cat.plant, 2, index=0, action='assign', profile_id=None))
    msg = await proposal(hass, cat)
    msg['entity_ids'] = [cat.plant.covers[0].entity_id]
    preview = await manage_profile(hass, msg)
    assert preview['targets'][0]['previous_profile_id'] is None
    assert preview['targets'][0]['previous_profile_name'] is None
    assert preview['targets'][0]['changes']['closing']['before'] == 30
    assert preview['targets'][0]['changes']['closing']['after'] == 42.5
    await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})
    assert cat.store.data['assignments'][cat.plant.covers[1].unique_id] == cat.profile_id


@pytest.mark.parametrize('selection', [[], ['duplicate', 'duplicate'], ['one'] * 201, 'cover.invalid', [42]])
async def test_invalid_selections_never_mutate(hass, catalogue, selection):
    cat = catalogue
    before = copy.deepcopy(cat.store.data)
    with pytest.raises(ProfileError, match='invalid_selection'):
        await manage_profile(hass, request(cat, 'preview_assign', entity_ids=selection))
    assert cat.store.data == before


@pytest.mark.parametrize('condition,code', [
    ('foreign', 'target_not_found'), ('missing', 'target_not_found'),
    ('offline', 'cover_unavailable'), ('unloaded', 'cover_unavailable'),
    ('disabled', 'cover_unavailable'), ('advanced', 'advanced_cover'),
    ('calibrating', 'calibration_busy'), ('removed_runtime', 'cover_unavailable'),
])
async def test_invalid_member_rejects_entire_confirmation(hass, catalogue, condition, code):
    cat = catalogue
    msg = await proposal(hass, cat)
    preview = await manage_profile(hass, msg)
    before = copy.deepcopy(cat.store.data)
    cover = cat.plant.covers[1]
    if condition == 'foreign':
        msg['entity_ids'][1] = cat.plant.covers[2].entity_id
    elif condition == 'missing':
        er.async_get(hass).async_remove(cover.entity_id)
    elif condition == 'offline':
        cat.plant.gateways[0].available = False
    elif condition == 'unloaded':
        object.__setattr__(cat.plant.entries[0], 'state', ConfigEntryState.NOT_LOADED)
    elif condition == 'disabled':
        er.async_get(hass).async_update_entity(cover.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    elif condition == 'advanced':
        cover._advanced = True
    elif condition == 'calibrating':
        cover.native_calibration_busy = lambda: True
    else:
        cat.store.covers.pop(cover.unique_id)
    with pytest.raises(ProfileError, match=code):
        await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})
    assert cat.store.data == before
    if condition not in ('foreign', 'missing'):
        data = await overview(hass, cat.store.entry_id)
        assert next(item for item in data['covers'] if item['entity_id'] == cover.entity_id)['assignment_reason'] == code


async def test_confirmation_binds_selection_identity_and_current_resolved_values(hass, catalogue):
    cat = catalogue
    msg = await proposal(hass, cat)
    preview = await manage_profile(hass, msg)
    before = copy.deepcopy(cat.store.data)
    for selection in [msg['entity_ids'][:1], msg['entity_ids'][1:]]:
        with pytest.raises(ProfileError, match='preview_required'):
            await manage_profile(hass, {**msg, 'action': 'assign', 'entity_ids': selection, 'confirmation': preview['confirmation']})
    er.async_get(hass).async_update_entity(cat.plant.covers[1].entity_id, new_unique_id='replacement')
    cat.store.covers['replacement'] = cat.plant.covers[1]
    with pytest.raises(ProfileError, match='preview_required'):
        await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})
    assert cat.store.data == before


async def test_disk_failure_does_not_publish_partial_assignments(hass, catalogue):
    cat = catalogue
    msg = await proposal(hass, cat)
    preview = await manage_profile(hass, msg)
    before = copy.deepcopy(cat.store.data)
    for cover in cat.plant.covers:
        cover.async_write_ha_state.reset_mock()
    with patch.object(cat.store.store, 'async_save', side_effect=OSError('full')):
        with pytest.raises(OSError):
            await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})
    assert cat.store.data == before
    for cover in cat.plant.covers:
        cover.async_write_ha_state.assert_not_called()


async def test_moving_cover_defers_runtime_until_stopped(hass, catalogue):
    cat = catalogue
    await write_profile(hass, message(cat.plant, 2, action='assign', profile_id=None))
    msg = await proposal(hass, cat)
    cover = cat.plant.covers[0]
    cover._attr_is_opening = True
    preview = await manage_profile(hass, msg)
    await manage_profile(hass, {**msg, 'action': 'assign', 'confirmation': preview['confirmation']})
    assert cover._travel_time_up == 30 and cover._pending_profile
    cover._attr_is_opening = False
    cover._apply_pending_cover_profile()
    assert cover._travel_time_up == 42.5


async def test_real_websocket_preview_then_confirm_and_reject_duplicate_selection(hass, catalogue, hass_ws_client):
    cat = catalogue
    msg = await proposal(hass, cat)
    register_api(hass)
    with patch('aiohttp.connector.DefaultResolver', ThreadedResolver):
        client = await hass_ws_client(hass)
    await client.send_json({'id': 1, 'type': WS_MANAGE, **msg})
    result = await client.receive_json()
    assert result['success']
    await client.send_json({'id': 2, 'type': WS_MANAGE, **msg, 'action': 'assign', 'confirmation': result['result']['confirmation']})
    assert (await client.receive_json())['success']
    await client.send_json({'id': 3, 'type': WS_MANAGE, **msg, 'entity_ids': [msg['entity_ids'][0]] * 2})
    assert (await client.receive_json())['error']['code'] == 'invalid_format'
    await client.close()
