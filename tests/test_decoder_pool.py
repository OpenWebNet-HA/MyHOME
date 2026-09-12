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

from custom_components.myhome.decoder_pool import DecoderPool

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
