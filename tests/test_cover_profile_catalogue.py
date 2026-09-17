"""Gateway profile operations: no implicit assignments, exact previews and durability."""
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.core import CoreState
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.storage import Store
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_profile_catalogue import WS_MANAGE, manage_profile, ws_manage
from custom_components.myhome.cover_profile_export import export_profiles
from custom_components.myhome.cover_profile_provenance import EVIDENCE
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    ProfileError,
    bind_cover,
    get_store,
    register_api,
    remove_entry,
    write_profile,
)
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


@pytest.fixture
async def catalogue(hass, plant):
    first = await write_profile(hass, message(plant))
    profile_id = first['assigned_profile_id']
    await write_profile(hass, message(plant, 1, index=1, action='assign', profile_id=profile_id))
    store = get_store(hass, plant.entries[0].entry_id)
    return SimpleNamespace(store=store, plant=plant, profile_id=profile_id)


def request(cat, action, **extra):
    return {'entry_id': cat.store.entry_id, 'revision': cat.store.data['revision'],
            'profile_id': cat.profile_id, 'action': action, **extra}


async def test_duplicate_is_unassigned_independent_and_preserves_evidence(hass, catalogue):
    cat = catalogue
    before = copy.deepcopy(cat.store.data)
    result = await manage_profile(hass, request(cat, 'duplicate', name='Independent'))
    new_id = result['profile_id']
    assert new_id != cat.profile_id and result['revision'] == before['revision'] + 1
    assert cat.store.data['assignments'] == before['assignments']
    assert cat.store.data['profiles'][new_id] == {**before['profiles'][cat.profile_id], 'name': 'Independent'}
    assert cat.store.data['profiles'][new_id]['provenance'] is not cat.store.data['profiles'][cat.profile_id]['provenance']
    for gateway in cat.plant.gateways:
        gateway.send.assert_not_called()
    cat.store.covers.clear()  # Catalogue management does not require any online cover.
    await manage_profile(hass, request(cat, 'delete', profile_id=new_id))
    assert new_id not in cat.store.data['profiles']
    assert cat.store.data['assignments'] == before['assignments']


async def test_update_preview_preserves_overrides_and_only_changed_direction_becomes_manual(hass, catalogue):
    cat = catalogue
    await write_profile(hass, message(cat.plant, 2, index=1, action='overrides', overrides={'opening': 12}))
    old = copy.deepcopy(cat.store.data)
    proposed = {'name': 'Updated', 'opening_time': 44, 'closing_time': old['profiles'][cat.profile_id]['closing_time']}
    preview = await manage_profile(hass, request(cat, 'preview', profile=proposed))
    assert cat.store.data == old
    assert len(preview['followers']) == 2
    masked = preview['followers'][1]['changes']['opening']
    assert masked == {'before': 12, 'after': 12, 'overridden': True}
    await manage_profile(hass, request(cat, 'update', profile=proposed, confirmation=preview['confirmation']))
    current = cat.store.data['profiles'][cat.profile_id]
    assert current['provenance']['opening']['source'] == 'manual'
    assert current['provenance']['opening']['origin_unique_id'] is None
    assert current['provenance']['opening']['recorded_at']
    assert current['provenance']['closing'] == old['profiles'][cat.profile_id]['provenance']['closing']
    assert cat.store.data['covers'] == old['covers'] and cat.store.data['assignments'] == old['assignments']
    assert cat.plant.covers[0]._travel_time_up == 44
    assert cat.plant.covers[1]._travel_time_up == 12
    saved = copy.deepcopy(cat.store.data)
    hass.data[DATA_KEY].pop(cat.store.entry_id)
    await bind_cover(hass, cat.plant.covers[0])
    assert get_store(hass, cat.store.entry_id).data == saved
    exported = await export_profiles(hass, cat.store.entry_id)
    assert exported['profiles'][0]['provenance']['opening']['origin_cover_id'] is None
    assert len(exported['covers']) == 2  # No phantom cover for a manual catalogue edit.


async def test_rename_retains_evidence_and_unused_profile_can_be_edited(hass, catalogue):
    cat = catalogue
    result = await manage_profile(hass, request(cat, 'duplicate', name='Unused'))
    new_id = result['profile_id']
    previous = copy.deepcopy(cat.store.data['profiles'][new_id])
    profile = {key: previous[key] for key in ('name', 'opening_time', 'closing_time')}
    profile['name'] = 'Renamed'
    preview = await manage_profile(hass, request(cat, 'preview', profile_id=new_id, profile=profile))
    assert preview['followers'] == []
    await manage_profile(hass, request(cat, 'update', profile_id=new_id, profile=profile, confirmation=preview['confirmation']))
    assert cat.store.data['profiles'][new_id]['provenance'] == previous['provenance']


@pytest.mark.parametrize('orphan', [False, True])
async def test_delete_rejects_live_and_orphan_associations(hass, catalogue, orphan):
    cat = catalogue
    if orphan:
        cat.store.data['assignments'] = {'removed-cover': cat.profile_id}
    with pytest.raises(ProfileError, match='profile_in_use'):
        await manage_profile(hass, request(cat, 'delete'))


@pytest.mark.parametrize('action', ['duplicate', 'update', 'delete'])
async def test_disk_failure_does_not_publish_revision_or_runtime_changes(hass, catalogue, action):
    cat = catalogue
    if action == 'delete':
        result = await manage_profile(hass, request(cat, 'duplicate', name='Unused'))
        cat.profile_id = result['profile_id']
    extra = {'name': 'New'} if action == 'duplicate' else {}
    if action == 'update':
        extra['profile'] = {'name': 'Changed', 'opening_time': 42, 'closing_time': 43}
        extra['confirmation'] = (await manage_profile(hass, request(cat, 'preview', profile=extra['profile'])))['confirmation']
    before = copy.deepcopy(cat.store.data)
    with patch.object(cat.store.store, 'async_save', side_effect=OSError('full')):
        with pytest.raises(OSError):
            await manage_profile(hass, request(cat, action, **extra))
    assert cat.store.data == before


@pytest.mark.parametrize('extra,code', [
    ({'entry_id': 'missing'}, 'target_not_found'), ({'revision': -1}, 'revision_conflict'),
    ({'profile_id': 'missing'}, 'profile_not_found'), ({'action': 'unsafe'}, 'invalid_profile'),
    ({'profile': {}}, 'invalid_profile'),
])
async def test_invalid_scope_revision_profile_and_payload(hass, catalogue, extra, code):
    with pytest.raises(ProfileError, match=code):
        await manage_profile(hass, {**request(catalogue, 'duplicate', name='Copy'), **extra})


async def test_gateway_shutdown_ownership_native_busy_and_profile_limit(hass, catalogue):
    cat = catalogue
    with patch.object(hass, 'state', CoreState.stopping):
        with pytest.raises(ProfileError, match='cover_unavailable'):
            await manage_profile(hass, request(cat, 'duplicate', name='Copy'))
    cat.store.calibration = object()
    with pytest.raises(ProfileError, match='calibration_busy'):
        await manage_profile(hass, request(cat, 'duplicate', name='Copy'))
    cat.store.calibration = None
    with patch('custom_components.myhome.cover_profile_catalogue.MAX_PROFILES', 1):
        with pytest.raises(ProfileError, match='profile_limit'):
            await manage_profile(hass, request(cat, 'duplicate', name='Copy'))
    profile = {'name': 'Change', 'opening_time': 22, 'closing_time': 33}
    preview = await manage_profile(hass, request(cat, 'preview', profile=profile))
    with pytest.raises(ProfileError, match='preview_required'):
        await manage_profile(hass, request(cat, 'update', profile=profile, confirmation='wrong'))
    with pytest.raises(ProfileError, match='preview_required'):
        await manage_profile(hass, request(cat, 'update', profile={**profile, 'name': 'Other'}, confirmation=preview['confirmation']))
    with patch.object(cat.plant.covers[0], 'native_calibration_busy', return_value=True):
        with pytest.raises(ProfileError, match='calibration_busy'):
            await manage_profile(hass, request(cat, 'update', profile=profile, confirmation=preview['confirmation']))
    await manage_profile(hass, request(cat, 'duplicate', name='Concurrent'))
    with pytest.raises(ProfileError, match='preview_required'):
        await manage_profile(hass, request(cat, 'update', profile=profile, confirmation=preview['confirmation']))


async def test_entry_replaced_while_loading_is_rejected(hass, catalogue):
    cat = catalogue
    with patch('custom_components.myhome.cover_profile_catalogue.gateway', side_effect=[cat.plant.entries[0], object()]):
        with pytest.raises(ProfileError, match='target_not_found'):
            await manage_profile(hass, request(cat, 'duplicate', name='Copy'))


async def test_v5_migration_keeps_all_data_and_backup_and_removal_cleans_both(hass, catalogue):
    cat = catalogue
    original = copy.deepcopy(cat.store.data)
    key = cat.store.store.key
    await Store(hass, 5, key).async_save(original)
    hass.data[DATA_KEY].pop(cat.store.entry_id)
    await bind_cover(hass, cat.plant.covers[0])
    store = get_store(hass, cat.store.entry_id)
    assert store.data == original
    assert await Store(hass, 5, key + '.pre_catalogue').async_load() == original
    assert await Store(hass, 6, key).async_load() == original
    await remove_entry(hass, cat.store.entry_id)
    assert await Store(hass, 5, key + '.pre_catalogue').async_load() is None


def test_only_manual_evidence_allows_no_origin():
    value = {'source': 'manual', 'recorded_at': '2026-09-17T12:00:00+00:00', 'origin_unique_id': None}
    assert EVIDENCE(value) == value
    for source in ('guided', 'automatic'):
        with pytest.raises(vol.Invalid):
            EVIDENCE({**value, 'source': source})


def test_management_requires_admin(hass):
    with pytest.raises(Unauthorized):
        ws_manage(hass, MagicMock(user=SimpleNamespace(is_admin=False)), {'id': 1})


async def test_real_websocket_validation_and_persistence(hass, catalogue, hass_ws_client):
    register_api(hass)
    with patch('aiohttp.connector.DefaultResolver', ThreadedResolver):
        client = await hass_ws_client(hass)
    await client.send_json({'id': 1, 'type': WS_MANAGE, **request(catalogue, 'duplicate', name='Websocket copy')})
    response = await client.receive_json()
    assert response['success']
    copy_id = response['result']['profile_id']
    assert copy_id not in catalogue.store.data['assignments'].values()
    await client.send_json({'id': 2, 'type': WS_MANAGE, **request(catalogue, 'delete', profile_id=copy_id), 'entity_id': 'cover.injected'})
    assert (await client.receive_json())['error']['code'] == 'invalid_format'
    await client.send_json({'id': 3, 'type': WS_MANAGE, **request(catalogue, 'delete', profile_id=copy_id)})
    assert (await client.receive_json())['success']
    await client.close()


async def test_confirmed_update_defers_runtime_for_moving_followers(hass, catalogue):
    cat = catalogue
    cover = cat.plant.covers[0]
    previous = cover._travel_time_up
    profile = {'name': 'Deferred', 'opening_time': 22, 'closing_time': 33}
    preview = await manage_profile(hass, request(cat, 'preview', profile=profile))
    cover._attr_is_opening = True
    await manage_profile(hass, request(cat, 'update', profile=profile, confirmation=preview['confirmation']))
    assert cover._travel_time_up == previous and cover._pending_profile
    cover._attr_is_opening = False
    cover.async_apply_cover_profile(cat.store.profile(cover.unique_id))
    assert cover._travel_time_up == 22


async def test_v5_backup_failure_preserves_original_store(hass, catalogue):
    cat = catalogue
    before = copy.deepcopy(cat.store.data)
    key = cat.store.store.key
    await Store(hass, 5, key).async_save(before)
    hass.data[DATA_KEY].pop(cat.store.entry_id)
    from custom_components.myhome.cover_profiles import ProfileStorage
    save = ProfileStorage.async_save
    async def reject_backup(store, data):
        if store.key.endswith('.pre_catalogue'):
            raise OSError('backup disk full')
        await save(store, data)
    with patch.object(ProfileStorage, 'async_save', reject_backup):
        with pytest.raises(OSError):
            await bind_cover(hass, cat.plant.covers[0])
    assert not get_store(hass, cat.store.entry_id).loaded
    assert await Store(hass, 5, key).async_load() == before
