"""Number entities for declared MyHOME FM radio tuners (F500 / F500N).

Exposes ``number.<source>_frequency`` (87.5–108.0 MHz, step 0.05 MHz, box mode)
on each declared tuner's Audio Source device so users can type or step to any
FM frequency directly from the Home Assistant UI without scripting
``media_player.play_media`` calls.

The entity is a companion of the tuner's ``MyHOMESoundSource``: commands go
through that source (which picks WHO=16 or WHO=22 for the gateway), and bus
reports reach it through ``runtime.tuner_frequency_callbacks``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import Platform, UnitOfFrequency
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SOURCE_TUNER,
)
from .data import MyHOMEConfigEntry, get_runtime_data
from .myhome_device import MyHOMEEntity
from .sound_source import source_address, tuner_source, uses_who22_commands
from .tuner_decode import FM_MAX_KHZ, FM_MIN_KHZ

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

PARALLEL_UPDATES = 0
PLATFORM = Platform.NUMBER


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> bool:
    """Set up FM frequency number entities for declared tuner sources."""
    runtime = get_runtime_data(config_entry)
    if runtime is None:
        return True
    if runtime.is_follower and 16 not in runtime.delegated_whos and 22 not in runtime.delegated_whos:
        return True

    options = getattr(config_entry, "options", None)
    if not isinstance(options, Mapping):
        return True
    entities: list[MyHOMETunerFrequencyNumber] = []
    for slot in range(1, CONF_SOURCE_SLOTS + 1):
        if options.get(CONF_SOURCE_TUNER.format(slot)) is not True:
            continue
        where = source_address(slot)
        name = str(options.get(CONF_SOURCE_NAME.format(slot), "") or "").strip()
        entities.append(
            MyHOMETunerFrequencyNumber(
                hass=hass,
                config_entry=config_entry,
                where=where,
                name=name or f"Audio Source {slot}",
                gateway=runtime.gateway,
            )
        )

    if entities:
        async_add_entities(entities)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    """Unload the MyHOME number platform."""
    return True


class MyHOMETunerFrequencyNumber(NumberEntity, MyHOMEEntity):
    """Direct FM frequency control (87.50–108.00 MHz) for a declared F500 / F500N tuner."""

    _attr_device_class = NumberDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.MEGAHERTZ
    _attr_native_min_value = 87.5
    _attr_native_max_value = 108.0
    _attr_native_step = 0.05
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyHOMEConfigEntry,
        where: str,
        name: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
        """Initialise the tuner FM frequency number entity."""
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=f"{where}#16",
            who="16",
            where=where,
            manufacturer="BTicino",
            model="Audio Source",
            gateway=gateway,
            translation_key="tuner_frequency",
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{gateway.mac}-16-{where}#16-frequency"
        self._attr_native_value: float | None = None
        self._poll_on_add = False

    @property
    def available(self) -> bool:
        """Return True if this tuner's active WHO subsystem is available."""
        who = "22" if uses_who22_commands(self._gateway_handler) else self._who
        return self._gateway_handler.is_who_available(who)

    async def async_added_to_hass(self) -> None:
        """Restore last frequency and follow the tuner's reported frequency."""
        await super().async_added_to_hass()
        runtime = get_runtime_data(self._config_entry)
        if runtime is None:
            return
        callbacks = runtime.tuner_frequency_callbacks.setdefault(self._where, [])
        callbacks.append(self._apply_khz)

        @callback
        def _unsubscribe_freq() -> None:
            if self._apply_khz in callbacks:
                callbacks.remove(self._apply_khz)

        self.async_on_remove(_unsubscribe_freq)

        source = runtime.sound_sources.get(self._where)
        known_khz = getattr(source, "_frequency_khz", None) if source is not None else None
        if isinstance(known_khz, int) and FM_MIN_KHZ <= known_khz <= FM_MAX_KHZ:
            self._attr_native_value = round(known_khz / 1000.0, 2)

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore the last valid FM frequency across restarts."""
        try:
            mhz = float(last_state.state)
        except (ValueError, TypeError):
            return
        if not math.isfinite(mhz):
            return
        khz = int(round(mhz * 1000))
        if FM_MIN_KHZ <= khz <= FM_MAX_KHZ:
            self._attr_native_value = round(khz / 1000.0, 2)

    async def async_update(self) -> None:
        """Polling is handled by the companion MyHOMESoundSource entity."""

    @callback
    def _apply_khz(self, kilohertz: int | None) -> None:
        if kilohertz is None or not FM_MIN_KHZ <= kilohertz <= FM_MAX_KHZ:
            return
        new_val = round(kilohertz / 1000.0, 2)
        if self._attr_native_value != new_val:
            self._attr_native_value = new_val
            self._publish_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the tuner's FM frequency in MHz."""
        await tuner_source(self._config_entry, self._where, self.entity_id).async_set_frequency(value)
