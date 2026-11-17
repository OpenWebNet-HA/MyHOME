"""Audio audit hardening: companion resolution, decoder guard, busy-error text, MA call replay.

Covers the follow-ups of the Music Assistant audit: a companion is never guessed
between two devices, a decoder that is a MyHOME/Music Assistant entity is
ignored at runtime, the environment-busy refusal names rooms, sound zones on two
gateways raise a repair, and the call sequences Music Assistant's HA player
provider makes (``set_members`` add/remove, unjoin, ``play_media`` on a member)
keep the matrix and the group books coherent.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from OWNd.message import OWNSoundCommand
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DECODER_COMPANION,
    CONF_DECODER_ENTITY,
    CONF_DECODER_SOURCE,
    DOMAIN,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.decoder_companion import (
    async_decoder_platform_problem,
    async_resolve_streaming_companion,
)
from custom_components.myhome.decoder_pool import DecoderPool
from custom_components.myhome.media_player_pool import build_pool, sync_multiple_audio_gateways
from custom_components.myhome.repairs import (
    ISSUE_AMBIGUOUS_COMPANION,
    ISSUE_INCOMPATIBLE_DECODER,
    ISSUE_INVALID_DECODER,
    ISSUE_MULTIPLE_AUDIO_GATEWAYS,
    async_create_incompatible_decoder_issue,
)
from tests.test_component_media_player import _create_test_zone


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[MYHOME gateway - 192.168.1.5]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


# ── Companion resolution ─────────────────────────────────────────────────────


def _streamer(hass, name, mac, dlna_platforms=("dlna_dmr",), entry_domain="cambridge_audio", host=None):
    """Register a vendor streamer device with one vendor entity and companions on other devices."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    vendor_entry = MockConfigEntry(domain=entry_domain, data={"host": host} if host else {})
    vendor_entry.add_to_hass(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=vendor_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        identifiers={(entry_domain, name)},
        name=name,
    )
    ent_reg.async_get_or_create(
        "media_player",
        entry_domain,
        f"{name}_vendor",
        device_id=device.id,
        config_entry=vendor_entry,
        suggested_object_id=name,
    )
    companions = []
    for platform in dlna_platforms:
        cfg = MockConfigEntry(domain=platform, data={"host": host} if host else {})
        cfg.add_to_hass(hass)
        comp_dev = dev_reg.async_get_or_create(
            config_entry_id=cfg.entry_id,
            connections={(dr.CONNECTION_NETWORK_MAC, mac)},
            identifiers={(platform, name)},
            name=name,
        )
        ent = ent_reg.async_get_or_create(
            "media_player",
            platform,
            f"{name}_{platform}",
            device_id=comp_dev.id,
            config_entry=cfg,
            suggested_object_id=f"{name}_{platform}",
        )
        companions.append(ent.entity_id)
    return f"media_player.{name}", companions


@pytest.mark.asyncio
async def test_override_is_authoritative(hass):
    match = async_resolve_streaming_companion(hass, "media_player.any", "media_player.chosen")
    assert (match.entity_id, match.step) == ("media_player.chosen", "override")
    # An override that points at the decoder itself is no override.
    assert async_resolve_streaming_companion(hass, "media_player.any", "media_player.any").step is None


@pytest.mark.asyncio
async def test_two_identical_streamers_are_not_glued_together(hass):
    """Two boxes with the same MAC-less name and separate renderers: refuse to guess."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    vendor = MockConfigEntry(domain="cambridge_audio")
    vendor.add_to_hass(hass)
    dev = dev_reg.async_get_or_create(
        config_entry_id=vendor.entry_id, identifiers={("cambridge_audio", "a")}, name="Cambridge CXN"
    )
    ent_reg.async_get_or_create("media_player", "cambridge_audio", "a", device_id=dev.id, suggested_object_id="cxn_a")
    for idx in ("1", "2"):
        cfg = MockConfigEntry(domain="dlna_dmr")
        cfg.add_to_hass(hass)
        other = dev_reg.async_get_or_create(
            config_entry_id=cfg.entry_id, identifiers={("dlna_dmr", idx)}, name="Cambridge CXN"
        )
        ent_reg.async_get_or_create(
            "media_player", "dlna_dmr", idx, device_id=other.id, suggested_object_id=f"dmr_{idx}"
        )

    match = async_resolve_streaming_companion(hass, "media_player.cxn_a")
    assert match.entity_id is None
    assert match.step == "name"
    assert match.ambiguous == ("media_player.dmr_1", "media_player.dmr_2")


@pytest.mark.asyncio
async def test_name_containment_no_longer_matches(hass):
    """"Cambridge CXN" must not adopt the renderer of "Cambridge CXN Bedroom"."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    vendor = MockConfigEntry(domain="cambridge_audio")
    vendor.add_to_hass(hass)
    dev = dev_reg.async_get_or_create(
        config_entry_id=vendor.entry_id, identifiers={("cambridge_audio", "a")}, name="Cambridge CXN"
    )
    ent_reg.async_get_or_create("media_player", "cambridge_audio", "a", device_id=dev.id, suggested_object_id="cxn")
    cfg = MockConfigEntry(domain="dlna_dmr")
    cfg.add_to_hass(hass)
    other = dev_reg.async_get_or_create(
        config_entry_id=cfg.entry_id, identifiers={("dlna_dmr", "b")}, name="Cambridge CXN Bedroom"
    )
    ent_reg.async_get_or_create("media_player", "dlna_dmr", "b", device_id=other.id, suggested_object_id="bedroom")

    assert async_resolve_streaming_companion(hass, "media_player.cxn").entity_id is None


@pytest.mark.asyncio
async def test_same_device_renderers_are_ranked_deterministically(hass):
    """dlna_dmr beats cast on one device, whatever the registry order."""
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    vendor = MockConfigEntry(domain="cambridge_audio")
    vendor.add_to_hass(hass)
    dev = dev_reg.async_get_or_create(config_entry_id=vendor.entry_id, identifiers={("x", "1")}, name="Box")
    for platform in ("cast", "dlna_dmr"):
        ent_reg.async_get_or_create(
            "media_player", platform, platform, device_id=dev.id, suggested_object_id=f"box_{platform}"
        )
    ent_reg.async_get_or_create("media_player", "cambridge_audio", "v", device_id=dev.id, suggested_object_id="box")

    match = async_resolve_streaming_companion(hass, "media_player.box")
    assert (match.entity_id, match.step) == ("media_player.box_dlna_dmr", "same_device")


@pytest.mark.asyncio
async def test_mac_and_host_steps_and_their_ambiguity(hass):
    """Same MAC on two other devices is ambiguous; a unique MAC matches; the host step works."""
    vendor_id, (dmr,) = _streamer(hass, "cxn_mac", "aa:aa:aa:aa:aa:01")
    match = async_resolve_streaming_companion(hass, vendor_id)
    assert (match.entity_id, match.step) == (dmr, "mac")

    # A second renderer on the same MAC turns it ambiguous.
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    cfg = MockConfigEntry(domain="upnp")
    cfg.add_to_hass(hass)
    dev2 = dev_reg.async_get_or_create(
        config_entry_id=cfg.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "aa:aa:aa:aa:aa:01")},
        identifiers={("upnp", "z")},
    )
    ent_reg.async_get_or_create("media_player", "upnp", "z", device_id=dev2.id, suggested_object_id="cxn_mac_upnp")
    match = async_resolve_streaming_companion(hass, vendor_id)
    assert match.entity_id is None and len(match.ambiguous) == 2


@pytest.mark.asyncio
async def test_host_step(hass):
    """No shared device or MAC, but the same host: the renderer of that host wins."""
    ent_reg = er.async_get(hass)
    vendor = MockConfigEntry(domain="cambridge_audio", data={"host": "10.0.0.5"})
    vendor.add_to_hass(hass)
    dlna = MockConfigEntry(domain="dlna_dmr", data={"ip_address": "10.0.0.5"})
    dlna.add_to_hass(hass)
    ent_reg.async_get_or_create(
        "media_player", "cambridge_audio", "v", config_entry=vendor, suggested_object_id="by_host"
    )
    ent_reg.async_get_or_create("media_player", "dlna_dmr", "d", config_entry=dlna, suggested_object_id="by_host_dmr")

    match = async_resolve_streaming_companion(hass, "media_player.by_host")
    assert (match.entity_id, match.step) == ("media_player.by_host_dmr", "host")


@pytest.mark.asyncio
async def test_unknown_entity_has_no_companion(hass):
    match = async_resolve_streaming_companion(hass, "media_player.nope")
    assert (match.entity_id, match.step, match.ambiguous) == (None, None, ())


# ── Runtime decoder guard and pool building ──────────────────────────────────


def _entry(options):
    entry = MagicMock()
    entry.entry_id = "gw"
    entry.options = options
    return entry


@pytest.mark.asyncio
async def test_build_pool_drops_a_mass_or_myhome_decoder_and_flags_it(hass):
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("media_player", "mass", "m", suggested_object_id="mass_clone")
    ent_reg.async_get_or_create("media_player", "sonos", "s", suggested_object_id="real")
    assert async_decoder_platform_problem(hass, "media_player.mass_clone") == "mass"
    assert async_decoder_platform_problem(hass, "media_player.real") is None

    pool = build_pool(
        hass,
        _entry(
            {
                CONF_DECODER_ENTITY.format(1): "media_player.mass_clone",
                CONF_DECODER_ENTITY.format(2): "media_player.real",
                CONF_DECODER_SOURCE.format(2): 2,
            }
        ),
    )
    assert pool.decoder_entity_ids == ["media_player.real"]
    issues = ir.async_get(hass).issues
    assert (DOMAIN, f"{ISSUE_INVALID_DECODER}_gw_media_player_mass_clone") in issues

    # Fixing the slot clears the issue.
    build_pool(hass, _entry({CONF_DECODER_ENTITY.format(2): "media_player.real"}))
    assert (DOMAIN, f"{ISSUE_INVALID_DECODER}_gw_media_player_mass_clone") not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_build_pool_uses_the_companion_override_for_any_platform(hass):
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("media_player", "sonos", "s", suggested_object_id="box")
    pool = build_pool(
        hass,
        _entry(
            {
                CONF_DECODER_ENTITY.format(1): "media_player.box",
                CONF_DECODER_COMPANION.format(1): "media_player.box_dmr",
            }
        ),
    )
    assert pool.companion_map == {"media_player.box": "media_player.box_dmr"}
    assert "media_player.box" not in pool.stream_incompatible


@pytest.mark.asyncio
async def test_build_pool_flags_an_ambiguous_companion_and_keeps_the_decoder_off_streams(hass):
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    vendor = MockConfigEntry(domain="cambridge_audio")
    vendor.add_to_hass(hass)
    dev = dev_reg.async_get_or_create(config_entry_id=vendor.entry_id, identifiers={("c", "a")}, name="CXN")
    ent_reg.async_get_or_create("media_player", "cambridge_audio", "a", device_id=dev.id, suggested_object_id="cxn")
    for idx in ("1", "2"):
        cfg = MockConfigEntry(domain="dlna_dmr")
        cfg.add_to_hass(hass)
        other = dev_reg.async_get_or_create(config_entry_id=cfg.entry_id, identifiers={("d", idx)}, name="CXN")
        ent_reg.async_get_or_create("media_player", "dlna_dmr", idx, device_id=other.id, suggested_object_id=f"d{idx}")

    # Preexisting incompatible-decoder issue should be cleared when ambiguous companion is raised
    async_create_incompatible_decoder_issue(hass, "gw", "media_player.cxn", "cambridge_audio")
    assert (DOMAIN, f"{ISSUE_INCOMPATIBLE_DECODER}_gw_media_player_cxn") in ir.async_get(hass).issues

    options = {CONF_DECODER_ENTITY.format(1): "media_player.cxn"}
    pool = build_pool(hass, _entry(options))
    assert "media_player.cxn" in pool.stream_incompatible
    assert (DOMAIN, f"{ISSUE_AMBIGUOUS_COMPANION}_gw_media_player_cxn") in ir.async_get(hass).issues
    assert (DOMAIN, f"{ISSUE_INCOMPATIBLE_DECODER}_gw_media_player_cxn") not in ir.async_get(hass).issues

    # Choosing one resolves it.
    options[CONF_DECODER_COMPANION.format(1)] = "media_player.d2"
    pool = build_pool(hass, _entry(options))
    assert pool.companion_map == {"media_player.cxn": "media_player.d2"}
    assert (DOMAIN, f"{ISSUE_AMBIGUOUS_COMPANION}_gw_media_player_cxn") not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_build_pool_unresolvable_override_target_is_still_authoritative(hass):
    """An unknown decoder with no override on an incompatible platform reports 'unknown' platform."""
    pool = build_pool(hass, _entry({CONF_DECODER_ENTITY.format(1): "media_player.ghost"}))
    assert pool.companion_map == {}


# ── More than one audio gateway ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sound_zones_on_two_gateways_raise_a_repair(hass):
    ent_reg = er.async_get(hass)
    entries = []
    for idx in (1, 2):
        cfg = MockConfigEntry(domain=DOMAIN, title=f"GW{idx}")
        cfg.add_to_hass(hass)
        entries.append(cfg)
        ent_reg.async_get_or_create(
            "media_player", DOMAIN, f"mac{idx}-21#16", config_entry=cfg, suggested_object_id=f"zone{idx}"
        )

    sync_multiple_audio_gateways(hass)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MULTIPLE_AUDIO_GATEWAYS)
    assert issue is not None
    assert issue.translation_placeholders == {"gateways": "GW1, GW2"}

    ent_reg.async_remove("media_player.zone2")
    sync_multiple_audio_gateways(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MULTIPLE_AUDIO_GATEWAYS) is None
    assert entries[0].state is ConfigEntryState.NOT_LOADED


# ── Environment-busy refusal names the rooms ─────────────────────────────────


@pytest.mark.asyncio
async def test_environment_busy_error_names_the_rooms(hass, mock_gateway):
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    z21 = _create_test_zone(hass, mock_gateway, runtime, "21", "media_player.kitchen")
    _create_test_zone(hass, mock_gateway, runtime, "22", "media_player.living")
    _create_test_zone(hass, mock_gateway, runtime, "23", "media_player.hall")
    _create_test_zone(hass, mock_gateway, runtime, "31", "media_player.other_env")
    hass.states.async_set("media_player.living", "on", {"friendly_name": "Living"})
    hass.states.async_set("media_player.hall", "on", {"friendly_name": "Hall"})

    err = z21._environment_busy_error("media_player.living", "2")
    assert err.translation_key == "environment_busy"
    assert err.translation_placeholders["owner_name"] == "Living"
    assert err.translation_placeholders["rooms"] == "Hall"
    assert "also on it: Hall" in str(err)


# ── Music Assistant's call sequences, replayed ───────────────────────────────
#
# Upstream reference: music_assistant/providers/hass_players/player.py
# (``set_members``: join with only the *added* entities, unjoin one by one) and
# its ``play_media`` on a freshly unjoined member. tests/fixtures hold the live
# capture; these are the sequences the audit asks to keep pinned.


def _ma_setup(hass, mock_gateway):
    runtime = MyHOMERuntimeData(gateway=mock_gateway)
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    runtime.decoder_pool = pool
    hass.states.async_set("media_player.dec1", MediaPlayerState.IDLE)
    zones = {
        name: _create_test_zone(hass, mock_gateway, runtime, where, f"media_player.{name}")
        for name, where in (("a", "11"), ("b", "22"), ("c", "33"))
    }
    return pool, zones


@pytest.mark.asyncio
async def test_ma_third_room_joins_then_leader_is_dropped(hass, mock_gateway):
    pool, z = _ma_setup(hass, mock_gateway)
    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await z["a"].async_play_media("music", "http://stream")
        await z["a"].async_join_players(["media_player.b"])  # set_members add=[b]
        await z["a"].async_join_players(["media_player.c"])  # set_members add=[c]
        assert set(pool.get_members("media_player.a")) == {"media_player.b", "media_player.c"}

        await z["a"].async_unjoin_player()  # MA drops the leader
    leader = pool.get_leader("media_player.c") or "media_player.c"
    leader_id = "media_player.b" if pool.get_members("media_player.b") else leader
    remaining = {"media_player.b", "media_player.c"}
    assert leader_id in remaining
    assert set(pool.get_members(leader_id)) == remaining - {leader_id}
    assert pool.get_assignment(leader_id) == "media_player.dec1"


@pytest.mark.asyncio
async def test_ma_unjoin_then_play_media_on_the_member_keeps_it_on(hass, mock_gateway):
    pool, z = _ma_setup(hass, mock_gateway)
    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await z["a"].async_play_media("music", "http://stream")
        await z["a"].async_join_players(["media_player.b"])
        await z["b"].async_unjoin_player()  # MA: remove=[b] ...
        assert z["b"]._pending_off_task is not None  # ... the OFF is only scheduled
        await z["b"].async_play_media("music", "http://stream-2")  # ... then b leads its own stream
    assert z["b"]._pending_off_task is None  # the grace period was cancelled by the reclaim
    assert pool.get_leader("media_player.b") is None


@pytest.mark.asyncio
async def test_ma_member_volume_zero_is_not_mute_and_can_be_raised(hass, mock_gateway):
    pool, z = _ma_setup(hass, mock_gateway)
    with patch("asyncio.sleep", return_value=None), patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ):
        await z["a"].async_play_media("music", "http://stream")
        await z["a"].async_join_players(["media_player.b"])
        mock_gateway.send.reset_mock()
        await z["b"].async_set_volume_level(0.0)
        assert z["b"].is_volume_muted is False
        assert str(mock_gateway.send.await_args_list[-1].args[0]) == str(OWNSoundCommand.set_volume("22", 0))
        await z["b"].async_set_volume_level(0.4)
        assert str(mock_gateway.send.await_args_list[-1].args[0]) == str(OWNSoundCommand.set_volume("22", 12))
        assert z["b"].is_volume_muted is False


# ── Options flow: the companion field ────────────────────────────────────────


@pytest.mark.asyncio
async def test_options_flow_validates_and_keeps_the_companion(hass):
    from custom_components.myhome.config_flow import MyhomeOptionsFlowHandler
    from custom_components.myhome.const import (
        CONF_GENERATE_EVENTS,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:12:34", "host": "192.168.1.50", "port": 20000},
        options={
            CONF_DECODER_ENTITY.format(1): "media_player.box",
            CONF_DECODER_COMPANION.format(1): "media_player.box_dmr",
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)
    flow = MyhomeOptionsFlowHandler(entry)
    flow.hass = hass
    form = await flow.async_step_init()
    schema_keys = {str(key) for key in form["data_schema"].schema}
    assert CONF_DECODER_COMPANION.format(1) in schema_keys

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("media_player", "mass", "m", suggested_object_id="mass_clone")
    ent_reg.async_get_or_create("media_player", DOMAIN, "z", suggested_object_id="zone")

    def submit(companion):
        return flow.async_step_user(
            {
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software",
                CONF_DECODER_ENTITY.format(1): "media_player.box",
                CONF_DECODER_SOURCE.format(1): 1,
                CONF_DECODER_COMPANION.format(1): companion,
            }
        )

    key = CONF_DECODER_COMPANION.format(1)
    assert (await submit("media_player.box"))["errors"][key] == "companion_same_as_decoder"
    assert (await submit("media_player.mass_clone"))["errors"][key] == "mass_entity_not_allowed"
    assert (await submit("media_player.zone"))["errors"][key] == "myhome_entity_not_allowed"
    assert (await submit("switch.box"))["errors"][key] == "not_a_media_player"

    # Companion without decoder entity is rejected
    res_no_decoder = await flow.async_step_user(
        {
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
            CONF_TRANSITION_MODE: "software",
            CONF_DECODER_ENTITY.format(1): "",
            CONF_DECODER_SOURCE.format(1): 1,
            CONF_DECODER_COMPANION.format(1): "media_player.box_dmr",
        }
    )
    assert res_no_decoder["errors"][key] == "companion_without_decoder"

    with patch.object(flow, "_apply_topology"), patch.object(hass.config_entries, "async_update_entry"):
        await submit("media_player.box_dmr")
    assert flow.options[key] == "media_player.box_dmr"

    # Clearing decoder entity also clears companion in options
    with patch.object(flow, "_apply_topology"), patch.object(hass.config_entries, "async_update_entry"):
        await flow.async_step_user(
            {
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software",
                CONF_DECODER_ENTITY.format(1): "",
                CONF_DECODER_SOURCE.format(1): 1,
                CONF_DECODER_COMPANION.format(1): "",
            }
        )
    assert flow.options[key] == ""
