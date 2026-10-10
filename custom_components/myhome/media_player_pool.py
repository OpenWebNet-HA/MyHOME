"""Build the shared decoder pool of a MyHOME gateway from its options."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DECODER_COMPANION,
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    DOMAIN,
    LOGGER,
)
from .data import MyHOMEConfigEntry
from .decoder_companion import async_decoder_platform_problem, async_resolve_streaming_companion
from .decoder_pool import DecoderPool, decoder_pool_store
from .repairs import (
    ISSUE_AMBIGUOUS_COMPANION,
    ISSUE_INVALID_DECODER,
    async_create_decoder_config_issue,
    async_create_incompatible_decoder_issue,
    async_delete_incompatible_decoder_issue,
    async_prune_incompatible_decoder_issues,
    async_sync_decoder_config_issues,
    async_sync_multiple_audio_gateways_issue,
)

# Integrations that cannot play a stream URL, and the media types they do take.
# ``cambridge_audio`` (StreamMagic) accepts presets, Airable and internet radio
# only; a Music Assistant stream is refused with ``unsupported_media_type``.
STREAM_INCOMPATIBLE_PLATFORMS: dict[str, frozenset[str]] = {
    "cambridge_audio": frozenset({"preset", "airable", "internet_radio"}),
}


def sync_multiple_audio_gateways(hass: HomeAssistant) -> None:
    """Raise or clear the repair for sound zones spread over several gateways (#426)."""
    ent_reg = er.async_get(hass)
    entry_ids = {
        entity.config_entry_id
        for entity in ent_reg.entities.values()
        if entity.platform == DOMAIN
        and entity.domain == "media_player"
        and entity.config_entry_id
        and "#16" in str(entity.unique_id)
    }
    titles = [
        cfg.title for entry_id in entry_ids if (cfg := hass.config_entries.async_get_entry(entry_id)) is not None
    ]
    async_sync_multiple_audio_gateways_issue(hass, titles)


def build_pool(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> DecoderPool:
    """Build a :class:`DecoderPool` from the current options entry.

    Called from :func:`~.media_player.async_setup_entry`, which an options change
    re-runs by reloading the entry.

    Args:
        hass: Home Assistant instance.
        config_entry: The active config entry for this MyHOME gateway.

    Returns:
        A fully configured :class:`DecoderPool` (may have zero decoders if
        nothing is configured yet).
    """
    options = config_entry.options
    decoder_map: dict[str, int] = {}
    pre_gain_map: dict[str, int] = {}
    stream_incompatible: set[str] = set()
    companion_map: dict[str, str] = {}
    ent_reg = er.async_get(hass)
    invalid: set[str] = set()
    ambiguous: set[str] = set()

    for i in range(1, CONF_DECODER_SLOTS + 1):
        entity_id = options.get(CONF_DECODER_ENTITY.format(i), "").strip()
        source_num = options.get(CONF_DECODER_SOURCE.format(i), i)  # int
        pre_gain = options.get(CONF_DECODER_PRE_GAIN.format(i), 0)  # int

        if entity_id and entity_id.startswith("media_player."):
            # The options flow refuses these, but a slot saved before that check,
            # imported, or pointed at an entity that later changed platform still
            # reaches here. Routing a zone back into itself loops the audio.
            bad_platform = async_decoder_platform_problem(hass, entity_id)
            if bad_platform:
                LOGGER.warning(
                    "MyHOME media player: decoder slot %s (%s) is a %s entity and is ignored; "
                    "a decoder must be the physical streamer wired to the matrix",
                    i,
                    entity_id,
                    bad_platform,
                )
                invalid.add(entity_id)
                async_create_decoder_config_issue(
                    hass, config_entry.entry_id, entity_id, ISSUE_INVALID_DECODER, {"platform": bad_platform}
                )
                continue
            decoder_map[entity_id] = int(source_num)  # always int — never f"Source N"
            pre_gain_map[entity_id] = int(pre_gain)
            reg_entry = ent_reg.async_get(entity_id)
            override = str(options.get(CONF_DECODER_COMPANION.format(i), "") or "").strip() or None
            wants_companion = bool(override) or (
                reg_entry is not None and reg_entry.platform in STREAM_INCOMPATIBLE_PLATFORMS
            )
            if not wants_companion:
                async_delete_incompatible_decoder_issue(hass, config_entry.entry_id, entity_id)
                continue
            # An explicit choice is authoritative for any platform: control and
            # volume stay on the decoder, stream URLs go to the companion.
            match = async_resolve_streaming_companion(hass, entity_id, override)
            if match.entity_id:
                LOGGER.info(
                    "MyHOME media player: decoder %s has streaming companion %s (matched by %s) — dynamic DLNA bridge enabled",
                    entity_id,
                    match.entity_id,
                    match.step,
                )
                companion_map[entity_id] = match.entity_id
                async_delete_incompatible_decoder_issue(hass, config_entry.entry_id, entity_id)
            elif match.ambiguous:
                LOGGER.warning(
                    "MyHOME media player: decoder %s matches several possible companions (%s); "
                    "none is used until one is chosen in the decoder options",
                    entity_id,
                    ", ".join(match.ambiguous),
                )
                ambiguous.add(entity_id)
                stream_incompatible.add(entity_id)
                async_delete_incompatible_decoder_issue(hass, config_entry.entry_id, entity_id)
                async_create_decoder_config_issue(
                    hass,
                    config_entry.entry_id,
                    entity_id,
                    ISSUE_AMBIGUOUS_COMPANION,
                    {"candidates": ", ".join(match.ambiguous)},
                )
            else:
                stream_incompatible.add(entity_id)
                async_create_incompatible_decoder_issue(
                    hass, config_entry.entry_id, entity_id, reg_entry.platform if reg_entry else "unknown"
                )

    # Clean up any previously flagged decoder issues that are no longer configured
    async_prune_incompatible_decoder_issues(hass, config_entry.entry_id, decoder_map)
    async_sync_decoder_config_issues(hass, config_entry.entry_id, ISSUE_INVALID_DECODER, invalid)
    async_sync_decoder_config_issues(hass, config_entry.entry_id, ISSUE_AMBIGUOUS_COMPANION, ambiguous)

    return DecoderPool(
        hass,
        decoder_map,
        pre_gain_map,
        stream_incompatible=stream_incompatible,
        companion_map=companion_map,
        store=decoder_pool_store(hass, config_entry.entry_id),
    )
