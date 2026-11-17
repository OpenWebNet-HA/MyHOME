"""Audio matrix, tuner, and decoder mapping helpers for MyHOME options flow."""

from __future__ import annotations

import typing

import voluptuous as vol
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_DECODER_COMPANION,
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    CONF_SOURCE_DEFAULT_FIELD,
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SOURCE_TUNER,
    CONF_TUNER_PICTURES,
)
from .decoder_companion import async_get_excluded_decoders


class OptionsFlowAudioMixin:
    """Audio environment, matrix source, and decoder pool options helper mixin."""

    hass: typing.Any
    config_entry: typing.Any
    options: dict[str, typing.Any] | None

    def _audio_environments(self) -> list[str]:
        """Return the environments that have audio zones, from the registry.

        Amplifier addresses are ``EA`` (environment, amplifier), and the F441M
        routes per environment, so defaults are offered per environment rather
        than per zone: two amplifiers in one room physically cannot sit on
        different inputs.  Environment 0 is left out: its routing address would
        be ``10S``, which is the source device itself, so it cannot be routed.
        So is any zone that is not a two-digit amplifier address.
        """
        environments: set[str] = set()
        try:
            registry = er.async_get(self.hass)
            entries = er.async_entries_for_config_entry(
                registry, self.config_entry.entry_id
            )
        except Exception:  # pylint: disable=broad-except
            return []
        for entry in entries:
            if entry.domain != "media_player" or "#16" not in (entry.unique_id or ""):
                continue
            zone = (entry.unique_id or "").rsplit("-", 1)[-1].split("#")[0]
            # Only two-digit amplifiers (01-99) have an environment digit
            if len(zone) == 2 and zone.isdigit():
                environments.add(zone[0])
        environments.discard("0")
        return sorted(environments)

    def _validate_audio_decoders(
        self, user_input: dict[str, typing.Any], errors: dict[str, str]
    ) -> None:
        """Validate decoder and companion media_player entity IDs and source uniqueness."""
        registry = er.async_get(self.hass)
        seen_sources: dict[int, str] = {}
        for i in range(1, CONF_DECODER_SLOTS + 1):
            entity_key = CONF_DECODER_ENTITY.format(i)
            source_key = CONF_DECODER_SOURCE.format(i)
            companion_key = CONF_DECODER_COMPANION.format(i)
            entity_val = str(user_input.get(entity_key) or "").strip()
            companion_val = str(user_input.get(companion_key) or "").strip()

            if entity_val:
                if not entity_val.startswith("media_player."):
                    errors[entity_key] = "not_a_media_player"
                else:
                    entry = registry.async_get(entity_val)
                    if entry and entry.platform == "mass":
                        # Prevent infinite loops by rejecting MA clones
                        errors[entity_key] = "mass_entity_not_allowed"
                    elif entry and entry.platform == "myhome":
                        errors[entity_key] = "myhome_entity_not_allowed"

                src_val = int(user_input.get(source_key, i) or i)
                if src_val in seen_sources:
                    errors[source_key] = "duplicate_decoder_source"
                else:
                    seen_sources[src_val] = source_key

            if companion_val:
                if not entity_val:
                    errors[companion_key] = "companion_without_decoder"
                elif not companion_val.startswith("media_player."):
                    errors[companion_key] = "not_a_media_player"
                else:
                    comp_entry = registry.async_get(companion_val)
                    if companion_val == entity_val:
                        errors[companion_key] = "companion_same_as_decoder"
                    elif comp_entry and comp_entry.platform == "mass":
                        errors[companion_key] = "mass_entity_not_allowed"
                    elif comp_entry and comp_entry.platform == "myhome":
                        errors[companion_key] = "myhome_entity_not_allowed"

    def _persist_audio_options(self, user_input: dict[str, typing.Any]) -> None:
        """Persist environment default sources, matrix input labels, and decoder slots."""
        assert self.options is not None
        # Persist the per-environment default source ("" = leave routing alone)
        _defaults: dict[str, int] = {}
        for env in self._audio_environments():
            raw = user_input.get(CONF_SOURCE_DEFAULT_FIELD.format(env), "")
            if raw not in ("", None, "none"):
                _defaults[env] = int(raw)
        self.options[CONF_SOURCE_DEFAULTS] = _defaults

        # Persist matrix source names (blank = nothing wired to that input)
        for i in range(1, CONF_SOURCE_SLOTS + 1):
            name_key = CONF_SOURCE_NAME.format(i)
            self.options[name_key] = str(user_input.get(name_key, "") or "").strip()
            tuner_key = CONF_SOURCE_TUNER.format(i)
            self.options[tuner_key] = bool(user_input.get(tuner_key, False))
        # Written only once set (or already stored), so an untouched form leaves the
        # options identical and a connection-only change still triggers its reload.
        pictures = str(user_input.get(CONF_TUNER_PICTURES) or "").strip()
        if pictures or CONF_TUNER_PICTURES in self.options:
            self.options[CONF_TUNER_PICTURES] = pictures

        for i in range(1, CONF_DECODER_SLOTS + 1):
            entity_key = CONF_DECODER_ENTITY.format(i)
            source_key = CONF_DECODER_SOURCE.format(i)
            gain_key = CONF_DECODER_PRE_GAIN.format(i)
            entity_val = str(user_input.get(entity_key) or "").strip()
            self.options[entity_key] = entity_val
            companion_key = CONF_DECODER_COMPANION.format(i)
            companion = (
                str(user_input.get(companion_key) or "").strip() if entity_val else ""
            )
            if companion or companion_key in self.options:
                self.options[companion_key] = companion
            # Selectors hand back strings/floats; the decoder pool and the
            # source labels both index on plain ints.
            self.options[source_key] = int(user_input.get(source_key, i) or i)
            self.options[gain_key] = int(float(user_input.get(gain_key, 0) or 0))

    def _append_audio_schema(
        self, schema_dict: dict[typing.Any, typing.Any]
    ) -> None:
        """Append matrix source, environment routing, and decoder slot fields to schema."""
        assert self.options is not None
        # Matrix source names 1–4 (F441M inputs S1–S4)
        _source_names: dict[int, str] = {}
        for i in range(1, CONF_SOURCE_SLOTS + 1):
            name_key = CONF_SOURCE_NAME.format(i)
            _name = str(self.options.get(name_key, "") or "").strip()
            if _name:
                _source_names[i] = _name
            schema_dict[
                vol.Optional(
                    name_key,
                    description={"suggested_value": _name},
                )
            ] = selector.TextSelector()
            # A tuner accepts frequency, station and RDS messages that a line
            # interface does not, and nothing on the bus tells them apart until
            # the device speaks, so the user declares it.
            tuner_key = CONF_SOURCE_TUNER.format(i)
            schema_dict[
                vol.Required(
                    tuner_key,
                    default=bool(self.options.get(tuner_key, False)),
                )
            ] = selector.BooleanSelector()

        # Default source per environment — only for environments that have zones.
        _stored_defaults = self.options.get(CONF_SOURCE_DEFAULTS) or {}
        for env in self._audio_environments():
            field = CONF_SOURCE_DEFAULT_FIELD.format(env)
            _current = (
                _stored_defaults.get(env)
                if isinstance(_stored_defaults, dict)
                else None
            )
            schema_dict[
                vol.Required(
                    field,
                    default=str(_current) if _current else "none",
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value="none", label="Leave routing as it is"
                        ),
                        *(
                            selector.SelectOptionDict(
                                value=str(i),
                                label=(
                                    f"S{i} — {_source_names[i]}"
                                    if i in _source_names
                                    else f"S{i} (unnamed)"
                                ),
                            )
                            for i in range(1, CONF_SOURCE_SLOTS + 1)
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        # Decoders are wired to one of those inputs: offer them by name, so the
        # mapping reads "which source is this decoder plugged into" rather than
        # asking the user to remember input numbers.
        _source_options = [
            selector.SelectOptionDict(
                value=str(i),
                label=(
                    f"S{i} — {_source_names[i]}"
                    if i in _source_names
                    else f"S{i} (unnamed)"
                ),
            )
            for i in range(1, CONF_SOURCE_SLOTS + 1)
        ]

        # Exclude internal MyHOME zones and Music Assistant clones from decoder choices
        _decoder_selector_cfg = selector.EntitySelectorConfig(
            domain=["media_player"],
            exclude_entities=async_get_excluded_decoders(self.hass),
        )

        # Decoder slots 1–4
        for i in range(1, CONF_DECODER_SLOTS + 1):
            entity_key = CONF_DECODER_ENTITY.format(i)
            source_key = CONF_DECODER_SOURCE.format(i)
            gain_key = CONF_DECODER_PRE_GAIN.format(i)

            _entity_val = self.options.get(entity_key, "")
            if _entity_val:
                schema_dict[
                    vol.Optional(
                        entity_key,
                        description={"suggested_value": _entity_val},
                    )
                ] = selector.EntitySelector(_decoder_selector_cfg)
            else:
                schema_dict[vol.Optional(entity_key)] = selector.EntitySelector(
                    _decoder_selector_cfg
                )

            companion_key = CONF_DECODER_COMPANION.format(i)
            _companion_val = self.options.get(companion_key, "")
            schema_dict[
                vol.Optional(
                    companion_key,
                    description={"suggested_value": _companion_val}
                    if _companion_val
                    else None,
                )
            ] = selector.EntitySelector(_decoder_selector_cfg)

            _source_val = int(self.options.get(source_key, i) or i)
            schema_dict[
                vol.Required(
                    source_key,
                    default=str(min(max(_source_val, 1), CONF_SOURCE_SLOTS)),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_source_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[
                vol.Required(
                    gain_key,
                    default=int(self.options.get(gain_key, 0) or 0),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=100,
                    step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
