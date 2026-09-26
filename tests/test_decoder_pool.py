"""Unit tests for DecoderPool -- the thread-safe decoder allocation manager.

Tests cover:
- Basic claim / release lifecycle
- Idempotent re-claim (same zone gets same decoder back)
- Pool exhaustion (all decoders busy)
- Concurrent claims via asyncio (lock validation)
- release_all for options reload
- Pre-gain lookup
- UNAVAILABLE state treated as busy (not idle)
"""
import asyncio
import platform
from unittest.mock import MagicMock

import pytest

# Fix: pytest-homeassistant-custom-component overrides the event loop policy
# to IocpProactor on Windows, which requires socket.socketpair() at creation.
# pytest-socket blocks this call and causes SocketBlockedError.
# Switching to SelectorEventLoopPolicy avoids the socketpair call.
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from homeassistant.components.media_player import MediaPlayerState

from custom_components.myhome.decoder_pool import DecoderPool, EnvironmentBusyError

# -- Overrides ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Override conftest autouse to suppress Windows SocketBlockedError.

    DecoderPool tests are pure unit tests requiring no HA platform setup.
    Overriding prevents pytest-homeassistant-custom-component from
    initialising the IocpProactor event loop on Windows.
    """
    yield


# -- Helpers ------------------------------------------------------------------

def _make_hass(state_map: dict) -> MagicMock:
    """Build a minimal hass mock with pre-configured entity states.

    Args:
        state_map: {entity_id: state_string_or_None}

    Returns:
        A MagicMock where hass.states.get(entity_id) returns a state object
        whose .state attribute matches the mapping.
    """
    hass = MagicMock()

    def _get_state(entity_id: str):
        val = state_map.get(entity_id)
        if val is None:
            return None
        state_obj = MagicMock()
        state_obj.state = val
        return state_obj

    hass.states.get.side_effect = _get_state
    return hass


# -- Fixtures -----------------------------------------------------------------

DECODER_1 = "media_player.cambridge_audio_cxn"
DECODER_2 = "media_player.hifiberry_zone"
ZONE_A = "media_player.audio_zone_3"
ZONE_B = "media_player.audio_zone_4"
ZONE_C = "media_player.audio_zone_5"


@pytest.fixture
def pool_one_decoder():
    """Single decoder pool with Cambridge at source 1."""
    hass = _make_hass({DECODER_1: MediaPlayerState.IDLE})
    return DecoderPool(
        hass,
        decoder_map={DECODER_1: 1},
        pre_gain_map={DECODER_1: 0},
    )


@pytest.fixture
def pool_two_decoders():
    """Two-decoder pool -- Cambridge at source 1, HiFiBerry at source 2."""
    hass = _make_hass({
        DECODER_1: MediaPlayerState.IDLE,
        DECODER_2: MediaPlayerState.IDLE,
    })
    return DecoderPool(
        hass,
        decoder_map={DECODER_1: 1, DECODER_2: 2},
        pre_gain_map={DECODER_1: 0, DECODER_2: 15},
    )


# -- Tests: claim / release ---------------------------------------------------

@pytest.mark.asyncio
async def test_claim_idle_decoder(pool_one_decoder):
    """First claim on an idle decoder returns (entity_id, source_num: int)."""
    result = await pool_one_decoder.claim(ZONE_A)
    assert result is not None
    decoder_id, source_num = result
    assert decoder_id == DECODER_1
    assert source_num == 1
    assert isinstance(source_num, int), "source_num must be int, not string"


@pytest.mark.asyncio
async def test_claim_reuse_existing(pool_one_decoder):
    """Claiming again from the same zone returns the existing assignment."""
    await pool_one_decoder.claim(ZONE_A)
    result = await pool_one_decoder.claim(ZONE_A)  # second call -- idempotent
    assert result is not None
    decoder_id, source_num = result
    assert decoder_id == DECODER_1
    assert source_num == 1


@pytest.mark.asyncio
async def test_claim_all_busy(pool_one_decoder):
    """Returns None when all decoders are already claimed."""
    await pool_one_decoder.claim(ZONE_A)       # zone A takes the only decoder
    result = await pool_one_decoder.claim(ZONE_B)  # zone B should get None
    assert result is None


@pytest.mark.asyncio
async def test_release_frees_decoder(pool_one_decoder):
    """A released decoder can immediately be claimed by another zone."""
    await pool_one_decoder.claim(ZONE_A)
    await pool_one_decoder.release(ZONE_A)

    result = await pool_one_decoder.claim(ZONE_B)
    assert result is not None
    decoder_id, _ = result
    assert decoder_id == DECODER_1


@pytest.mark.asyncio
async def test_release_returns_decoder_id(pool_one_decoder):
    """release() returns the freed decoder entity_id."""
    await pool_one_decoder.claim(ZONE_A)
    freed = await pool_one_decoder.release(ZONE_A)
    assert freed == DECODER_1


@pytest.mark.asyncio
async def test_release_no_assignment_returns_none(pool_one_decoder):
    """release() on a zone with no assignment returns None gracefully."""
    freed = await pool_one_decoder.release(ZONE_C)  # never claimed
    assert freed is None


# -- Tests: multi-decoder -----------------------------------------------------

@pytest.mark.asyncio
async def test_two_zones_get_different_decoders(pool_two_decoders):
    """Two simultaneous zones each claim a different decoder."""
    result_a = await pool_two_decoders.claim(ZONE_A)
    result_b = await pool_two_decoders.claim(ZONE_B)

    assert result_a is not None
    assert result_b is not None
    assert result_a[0] != result_b[0], "Both zones claimed the same decoder!"


@pytest.mark.asyncio
async def test_concurrent_claims_no_race(pool_two_decoders):
    """asyncio.gather cannot cause two zones to claim the same decoder.

    The asyncio.Lock inside DecoderPool ensures only one claim succeeds per
    decoder even under concurrent await calls.
    """
    results = await asyncio.gather(
        pool_two_decoders.claim(ZONE_A),
        pool_two_decoders.claim(ZONE_B),
        pool_two_decoders.claim(ZONE_C),  # third zone -- should get None
    )
    claimed = [r[0] for r in results if r is not None]
    # No decoder should appear more than once
    assert len(claimed) == len(set(claimed)), "Race condition: same decoder claimed twice!"
    assert len(claimed) == 2  # only 2 decoders available


# -- Tests: release_all -------------------------------------------------------

@pytest.mark.asyncio
async def test_release_all(pool_two_decoders):
    """release_all() clears all assignments so both decoders become available."""
    await pool_two_decoders.claim(ZONE_A)
    await pool_two_decoders.claim(ZONE_B)

    await pool_two_decoders.release_all()

    # Both decoders should now be claimable again
    result_a = await pool_two_decoders.claim(ZONE_A)
    result_b = await pool_two_decoders.claim(ZONE_B)
    assert result_a is not None
    assert result_b is not None


# -- Tests: pre-gain ----------------------------------------------------------

def test_get_pre_gain_configured(pool_two_decoders):
    """get_pre_gain returns the configured value."""
    assert pool_two_decoders.get_pre_gain(DECODER_1) == 0
    assert pool_two_decoders.get_pre_gain(DECODER_2) == 15


def test_get_pre_gain_unconfigured():
    """get_pre_gain returns 0 for decoders without explicit gain config."""
    hass = _make_hass({DECODER_1: MediaPlayerState.IDLE})
    pool = DecoderPool(hass, decoder_map={DECODER_1: 1})  # no pre_gain_map
    assert pool.get_pre_gain(DECODER_1) == 0


def test_get_pre_gain_unknown_entity(pool_two_decoders):
    """get_pre_gain returns 0 for unknown entity IDs (defensive default)."""
    assert pool_two_decoders.get_pre_gain("media_player.unknown") == 0


# -- Tests: UNAVAILABLE treated as busy ---------------------------------------

@pytest.mark.asyncio
async def test_unavailable_decoder_not_claimed():
    """UNAVAILABLE state is NOT idle -- the decoder must not be claimed.

    Prevents routing audio to a Cambridge that lost network connectivity.
    """
    hass = _make_hass({DECODER_1: "unavailable"})
    pool = DecoderPool(hass, decoder_map={DECODER_1: 1})
    result = await pool.claim(ZONE_A)
    assert result is None, "UNAVAILABLE decoder should not be claimable"


@pytest.mark.asyncio
async def test_playing_decoder_not_claimed():
    """A decoder in PLAYING state is not idle and must not be claimed."""
    hass = _make_hass({DECODER_1: MediaPlayerState.PLAYING})
    pool = DecoderPool(hass, decoder_map={DECODER_1: 1})
    result = await pool.claim(ZONE_A)
    assert result is None


# -- Tests: is_configured / decoder_entity_ids --------------------------------

def test_is_configured_true(pool_one_decoder):
    """is_configured is True when at least one decoder is mapped."""
    assert pool_one_decoder.is_configured is True


def test_is_configured_false():
    """is_configured is False when no decoders are configured."""
    hass = _make_hass({})
    pool = DecoderPool(hass, decoder_map={})
    assert pool.is_configured is False


def test_decoder_entity_ids(pool_two_decoders):
    """decoder_entity_ids returns all configured decoder entity IDs."""
    ids = pool_two_decoders.decoder_entity_ids
    assert DECODER_1 in ids
    assert DECODER_2 in ids
    assert len(ids) == 2


# -- Tests: get_assignment ----------------------------------------------------

@pytest.mark.asyncio
async def test_get_assignment_returns_decoder(pool_one_decoder):
    """get_assignment returns the claimed decoder for an active zone."""
    await pool_one_decoder.claim(ZONE_A)
    assert pool_one_decoder.get_assignment(ZONE_A) == DECODER_1


@pytest.mark.asyncio
async def test_get_assignment_none_when_not_claimed(pool_one_decoder):
    """get_assignment returns None for zones with no active decoder."""
    assert pool_one_decoder.get_assignment(ZONE_A) is None


@pytest.mark.asyncio
async def test_claim_prefers_a_decoder_on_the_requested_source(hass):
    """A zone gets the decoder wired to its preferred input when it is idle.

    Routing the matrix to the input the room already defaults to avoids an
    audible source switch, so slot order yields to the preference.
    """
    pool = DecoderPool(
        hass,
        {
            "media_player.slot_one": 1,
            "media_player.slot_two": 2,
        },
    )
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "idle")

    assert await pool.claim("media_player.zone", preferred_source=2) == (
        "media_player.slot_two",
        2,
    )


@pytest.mark.asyncio
async def test_claim_falls_back_when_the_preferred_decoder_is_busy(hass):
    """A busy preference does not block playback; slot order takes over."""
    pool = DecoderPool(
        hass,
        {
            "media_player.slot_one": 1,
            "media_player.slot_two": 2,
        },
    )
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "playing")

    assert await pool.claim("media_player.zone", preferred_source=2) == (
        "media_player.slot_one",
        1,
    )


@pytest.mark.asyncio
async def test_claim_is_refused_while_the_environment_streams(hass):
    """One environment listens to one matrix input, so it holds one decoder.

    Releasing the first zone frees the environment again, and a zone in a
    different environment is never affected.
    """
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "idle")

    assert await pool.claim("media_player.zone_22", environment="2") is not None
    # Re-claiming by the same zone stays idempotent
    assert await pool.claim("media_player.zone_22", environment="2") == ("media_player.slot_one", 1)
    assert pool.environment_owner("2") == "media_player.zone_22"
    assert pool.environment_owner("2", exclude="media_player.zone_22") is None

    with pytest.raises(EnvironmentBusyError) as err:
        await pool.claim("media_player.zone_23", environment="2")
    assert err.value.owner == "media_player.zone_22"
    assert err.value.environment == "2"
    assert pool.get_assignment("media_player.zone_23") is None

    assert await pool.claim("media_player.zone_31", environment="3") == ("media_player.slot_two", 2)

    await pool.release("media_player.zone_22")
    assert pool.environment_owner("2") is None

    await pool.release_all()
    assert pool.environment_owner("3") is None


@pytest.mark.asyncio
async def test_claim_without_environment_keeps_the_old_behaviour(hass):
    """Callers that do not pass an environment are never refused for one."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "idle")

    assert await pool.claim("media_player.zone_22") is not None
    assert await pool.claim("media_player.zone_23") is not None
    assert pool.environment_owner("2") is None


# -- Tests: Grouping & Members ------------------------------------------------

@pytest.mark.asyncio
async def test_add_member_when_leader_not_assigned(hass):
    """add_member returns None if the leader has no active decoder."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    assert await pool.add_member("media_player.leader", "media_player.member") is None


@pytest.mark.asyncio
async def test_add_member_success_and_idempotent(hass):
    """add_member successfully adds member and is idempotent when called again."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")

    # Leader claims slot_one
    claimed = await pool.claim("media_player.leader", environment="1")
    assert claimed == ("media_player.slot_one", 1)

    # Member joins
    joined = await pool.add_member("media_player.leader", "media_player.member", environment="2")
    assert joined == ("media_player.slot_one", 1)
    assert pool.get_members("media_player.leader") == ["media_player.member"]
    assert pool.environment_owner("2") == "media_player.member"

    # Idempotent call
    joined_again = await pool.add_member("media_player.leader", "media_player.member", environment="2")
    assert joined_again == ("media_player.slot_one", 1)


@pytest.mark.asyncio
async def test_add_member_environment_busy_conflict(hass):
    """add_member raises EnvironmentBusyError if member environment is locked by another decoder."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "idle")

    # Leader 1 in env 1 gets slot 1
    await pool.claim("media_player.leader1", environment="1")
    # Zone in env 2 gets slot 2
    await pool.claim("media_player.zone_env2", environment="2")

    # Leader 1 tries to add a member in env 2 (which is already on slot 2)
    with pytest.raises(EnvironmentBusyError) as err:
        await pool.add_member("media_player.leader1", "media_player.member_env2", environment="2")
    assert err.value.owner == "media_player.zone_env2"
    assert err.value.environment == "2"


@pytest.mark.asyncio
async def test_release_member_directly(hass):
    """Calling release() on a member removes the member and frees its environment."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")

    await pool.claim("media_player.leader", environment="1")
    await pool.add_member("media_player.leader", "media_player.member", environment="2")

    # Releasing member zone directly
    freed = await pool.release("media_player.member")
    assert freed == "media_player.slot_one"
    assert pool.get_members("media_player.leader") == []
    assert pool.environment_owner("2") is None


@pytest.mark.asyncio
async def test_release_leader_disbands_members(hass):
    """Calling release() on the leader disbands all members and clears their environments."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")

    await pool.claim("media_player.leader", environment="1")
    await pool.add_member("media_player.leader", "media_player.member", environment="2")

    freed = await pool.release("media_player.leader")
    assert freed == "media_player.slot_one"
    assert pool.get_members("media_player.leader") == []
    assert pool.environment_owner("1") is None
    assert pool.environment_owner("2") is None


@pytest.mark.asyncio
async def test_remove_member_explicit(hass):
    """Calling remove_member() directly removes the member and returns the decoder."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")

    await pool.claim("media_player.leader")
    await pool.add_member("media_player.leader", "media_player.member")
    assert pool.get_assignment("media_player.member") == "media_player.slot_one"

    freed = await pool.remove_member("media_player.member")
    assert freed == "media_player.slot_one"
    assert pool.get_assignment("media_player.member") is None

    # Removing nonexistent member returns None
    assert await pool.remove_member("media_player.nonexistent") is None


def test_decoder_source_lookups(hass):
    """Test get_members, get_decoder_for_source and decoder_source lookups."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})

    assert pool.get_members("media_player.nonexistent") == []
    assert pool.decoder_source("media_player.slot_one") == 1
    assert pool.decoder_source("media_player.unknown") is None
    assert pool.get_decoder_for_source(1) == "media_player.slot_one"
    assert pool.get_decoder_for_source(99) is None


@pytest.mark.asyncio
async def test_get_group_members_and_leader(hass):
    """Test get_group_members and get_leader for standalone and grouped zones."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")

    # Standalone zone
    assert pool.get_group_members("media_player.standalone") is None
    assert pool.get_leader("media_player.standalone") is None

    # Group zones
    await pool.add_member("media_player.leader", "media_player.member1")
    await pool.add_member("media_player.leader", "media_player.member2")

    expected = ["media_player.leader", "media_player.member1", "media_player.member2"]
    assert pool.get_group_members("media_player.leader") == expected
    assert pool.get_group_members("media_player.member1") == expected
    assert pool.get_group_members("media_player.member2") == expected

    assert pool.get_leader("media_player.leader") is None
    assert pool.get_leader("media_player.member1") == "media_player.leader"
    assert pool.get_leader("media_player.member2") == "media_player.leader"


@pytest.mark.asyncio
async def test_add_member_steals_and_releases_decoder(hass):
    """Adding a member that already leads a group or holds a decoder cleanly reassigns."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "idle")

    # Leader A claims slot 1
    await pool.claim("media_player.leaderA")
    # Zone X claims slot 2 and leads a group with member Y
    await pool.claim("media_player.zoneX")
    await pool.add_member("media_player.zoneX", "media_player.memberY")

    # Leader A now adds zoneX as a member
    await pool.add_member("media_player.leaderA", "media_player.zoneX")

    # zoneX's previous group with memberY was disbanded, and zoneX's decoder was released
    assert pool.get_assignment("media_player.memberY") is None
    assert pool.get_members("media_player.zoneX") == []
    # slot 2 is now free
    assert pool.get_assignment("media_player.zoneX") == "media_player.slot_one"
    assert pool.get_members("media_player.leaderA") == ["media_player.zoneX"]


@pytest.mark.asyncio
async def test_disband_group_explicit_and_release_all(hass):
    """Test disband_group and release_all clear all groups and environments."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")

    await pool.claim("media_player.leader", environment="1")
    await pool.add_member("media_player.leader", "media_player.member", environment="2")

    disbanded = await pool.disband_group("media_player.leader")
    assert disbanded == ["media_player.member"]
    assert pool.get_members("media_player.leader") == []
    assert pool.environment_owner("2") is None

    # Re-group and release_all
    await pool.add_member("media_player.leader", "media_player.member", environment="2")
    await pool.release_all()
    assert pool.get_members("media_player.leader") == []
    assert pool.environment_owner("1") is None
    assert pool.environment_owner("2") is None


@pytest.mark.asyncio
async def test_add_member_idempotent_without_leader_decoder(hass):
    """Calling add_member twice when leader has no decoder assigned returns None on second call."""
    pool = DecoderPool(hass, {})
    await pool.add_member("media_player.leader", "media_player.member")
    # Second call hits dec_id is None -> return None
    result = await pool.add_member("media_player.leader", "media_player.member")
    assert result is None


@pytest.mark.asyncio
async def test_environment_without_a_stream_does_not_block(hass):
    """A zone booked in an environment but listening to no decoder is not streaming.

    Members of a group whose leader has not claimed a decoder yet are in that
    state; they must not make other zones of their environment report
    ``environment_busy`` while nothing plays.
    """
    pool = DecoderPool(hass, {"media_player.slot_one": 1})
    hass.states.async_set("media_player.slot_one", "idle")
    await pool.claim("media_player.leader", environment="1")

    # A passive group: its member is booked in environment 2, nothing streams.
    await pool.add_member("media_player.passive_leader", "media_player.passive", environment="2")
    assert pool.environment_owner("2") is None

    assert await pool.add_member(
        "media_player.leader", "media_player.member", environment="2"
    ) == ("media_player.slot_one", 1)
    assert pool.environment_owner("2", exclude="media_player.passive") == "media_player.member"


@pytest.mark.asyncio
async def test_refused_join_changes_nothing(hass):
    """An environment conflict is raised before the pool is touched.

    The joining zone keeps its own decoder and group, and the leader stays in
    the group it belonged to: a refused ``media_player.join`` is a no-op.
    """
    pool = DecoderPool(
        hass,
        {"media_player.slot_one": 1, "media_player.slot_two": 2, "media_player.slot_three": 3},
    )
    for dec in ("media_player.slot_one", "media_player.slot_two", "media_player.slot_three"):
        hass.states.async_set(dec, "idle")

    await pool.add_member("media_player.outer", "media_player.leader", environment="1")
    await pool.claim("media_player.joiner", environment="3")
    await pool.add_member("media_player.joiner", "media_player.joiner_member", environment="3")
    await pool.claim("media_player.other", environment="2")
    before = (dict(pool._assignments), {k: set(v) for k, v in pool._groups.items()}, dict(pool._environments))

    with pytest.raises(EnvironmentBusyError) as err:
        await pool.set_group(
            "media_player.leader",
            {"media_player.joiner": "3", "media_player.blocked": "2"},
        )
    assert err.value.owner == "media_player.other"
    after = (dict(pool._assignments), {k: set(v) for k, v in pool._groups.items()}, dict(pool._environments))
    assert after == before


@pytest.mark.asyncio
async def test_set_group_reports_what_changed(hass):
    """set_group applies a snapshot and returns the zones and decoders to act on."""
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "idle")

    await pool.claim("media_player.leader", environment="1")
    await pool.set_group("media_player.leader", {"media_player.stays": "2", "media_player.goes": "3"})
    # A zone that leads its own streaming group joins: it gives up both.
    await pool.claim("media_player.joiner", environment="4")
    await pool.add_member("media_player.joiner", "media_player.orphan", environment="5")

    change = await pool.set_group(
        "media_player.leader",
        {"media_player.stays": "2", "media_player.joiner": "4", "media_player.no_env": None},
    )

    assert sorted(change.joined) == ["media_player.joiner", "media_player.no_env"]
    assert change.left == ["media_player.goes"]
    assert change.orphaned == ["media_player.orphan"]
    assert change.released == ["media_player.slot_two"]
    assert pool.get_members("media_player.leader") == [
        "media_player.joiner", "media_player.no_env", "media_player.stays",
    ]
    assert pool.get_assignment("media_player.joiner") == "media_player.slot_one"
    assert pool.get_assignment("media_player.orphan") is None
    assert pool.environment_owner("5") is None

    # An empty snapshot dissolves the group.
    change = await pool.set_group("media_player.leader", {})
    assert sorted(change.left) == ["media_player.joiner", "media_player.no_env", "media_player.stays"]
    assert pool.get_group_members("media_player.leader") is None


@pytest.mark.asyncio
async def test_claim_skips_excluded_decoders(hass):
    """A decoder that cannot play the media is passed over, not claimed and failed."""
    pool = DecoderPool(
        hass,
        {"media_player.cxn": 1, "media_player.dlna": 2},
        stream_incompatible={"media_player.cxn"},
    )
    hass.states.async_set("media_player.cxn", "idle")
    hass.states.async_set("media_player.dlna", "idle")

    assert pool.stream_incompatible == frozenset({"media_player.cxn"})
    assert await pool.claim(
        "media_player.zone", preferred_source=1, exclude={"media_player.cxn"}
    ) == ("media_player.dlna", 2)


@pytest.mark.asyncio
async def test_failed_claim_keeps_the_member_in_its_group(hass):
    """A member leaves its group only once it has a decoder of its own.

    Both ways a claim fails (no idle decoder, environment already streaming)
    must leave the group exactly as it was; a successful claim detaches.
    """
    pool = DecoderPool(hass, {"media_player.slot_one": 1, "media_player.slot_two": 2})
    hass.states.async_set("media_player.slot_one", "idle")
    hass.states.async_set("media_player.slot_two", "playing")  # busy elsewhere

    await pool.claim("media_player.leader", environment="1")
    await pool.add_member("media_player.leader", "media_player.member", environment="2")
    await pool.add_member("media_player.leader", "media_player.neighbour", environment="3")
    group = ["media_player.leader", "media_player.member", "media_player.neighbour"]

    # No idle decoder left.
    assert await pool.claim("media_player.member", environment="2") is None
    assert pool.get_group_members("media_player.leader") == group

    # A decoder is free, but a second member in environment 3 cannot take it:
    # switching the environment would take the neighbour off the group stream.
    hass.states.async_set("media_player.slot_two", "idle")
    await pool.add_member("media_player.leader", "media_player.second", environment="3")
    with pytest.raises(EnvironmentBusyError):
        await pool.claim("media_player.second", environment="3")
    assert pool.get_members("media_player.leader") == [
        "media_player.member", "media_player.neighbour", "media_player.second",
    ]
    await pool.remove_member("media_player.second")

    # Success: the member takes the free decoder and leaves.
    assert await pool.claim("media_player.member", environment="2") == ("media_player.slot_two", 2)
    assert pool.get_members("media_player.leader") == ["media_player.neighbour"]
    assert pool.environment_owner("2") == "media_player.member"


@pytest.mark.asyncio
async def test_claim_detaches_member_role_even_if_already_assigned(hass):
    """Calling claim() removes any lingering group membership even if the zone already has a claim."""
    pool = DecoderPool(hass, {"media_player.dec1": 1})
    hass.states.async_set("media_player.dec1", "idle")

    # Zone claims dec1
    await pool.claim("media_player.zone1")
    assert pool.get_assignment("media_player.zone1") == "media_player.dec1"

    # Simulate an abnormal dual-role state where zone1 is also listed as a member in another group
    pool._groups["media_player.other_leader"] = {"media_player.zone1"}

    # Calling claim again reuses the claim, but detaches member role
    claimed = await pool.claim("media_player.zone1")
    assert claimed == ("media_player.dec1", 1)
    assert "media_player.zone1" not in pool.get_members("media_player.other_leader")



