"""Regression tests for HA 2026.12+ DeviceInfo dataclass compatibility (Issue #930, core#192323).

Home Assistant Core ``DeviceInfo`` / ``ChildDeviceInfo`` timeline:
- ``2026.8–2026.10``: ``DeviceInfo`` is a ``TypedDict`` (``dict`` at runtime without
  ``.as_dict()`` or attribute access).
- ``2026.11``: ``DeviceInfo`` and ``ChildDeviceInfo`` became ``@dataclass(slots=True)``
  with ``.as_dict()`` and a silent ``_DeviceInfoMapping`` compatibility shim.
- ``2026.12`` (``home-assistant/core#192323``): ``_DeviceInfoMapping`` dict-compatibility
  methods (``__getitem__``, ``__setitem__``, ``__contains__``, ``.get()``, ``.keys()``,
  ``.items()``, ``.update()``, ``__eq__`` against ``Mapping``) emit ``frame.report_usage``
  deprecations (removal scheduled for ``2027.12.0``).

Because MyHOME v2.0.0 supports Home Assistant 2026.8+, this module simulates the strict
HA 2026.12+ slotted dataclass contract even when the test suite runs on HA 2026.8–2026.10,
guaranteeing zero deprecated mapping calls and 100% branch coverage across both regimes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.myhome import button, myhome_device, switch
from custom_components.myhome.button import (
    CalibrateAllCoversButtonEntity,
    CalibrateCoverButtonEntity,
    DisableCommandButtonEntity,
)
from custom_components.myhome.const import (
    DOMAIN,
    ROLE_PRIMARY,
    ROLE_STANDBY,
    TOPOLOGY_SHARED,
)
from custom_components.myhome.myhome_device import MyHOMEEntity
from custom_components.myhome.typing_compat import device_info_to_dict
from tests.test_multi_gateway import _create_mock_gateway

_UNSET: Any = object()
_DEPRECATED_MSG = "Deprecated DeviceInfo mapping method called"


@dataclass(slots=True, kw_only=True)
class StrictDataclassDeviceInfo:
    """Simulator of HA 2026.12+ ``DeviceInfo`` that forbids all deprecated mapping calls."""

    configuration_url: str | Any | None = _UNSET
    connections: set[tuple[str, str]] = _UNSET
    created_at: Any = _UNSET
    default_manufacturer: str = _UNSET
    default_model: str = _UNSET
    default_name: str = _UNSET
    entry_type: Any = _UNSET
    hw_version: str | None = _UNSET
    identifiers: set[tuple[str, str]] = _UNSET
    manufacturer: str | None = _UNSET
    modified_at: Any = _UNSET
    model: str | None = _UNSET
    model_id: str | None = _UNSET
    name: str | None = _UNSET
    serial_number: str | None = _UNSET
    suggested_area: str | None = _UNSET
    sw_version: str | None = _UNSET
    translation_key: str | None = _UNSET
    translation_placeholders: Mapping[str, str] | None = _UNSET
    via_device_id: str = _UNSET

    __iter__: ClassVar[Any] = None

    def as_dict(self) -> dict[str, Any]:
        """Return explicitly set fields as a plain ``dict``, matching HA 2026.11+."""
        return {
            field.name: val
            for field in fields(self)
            if (val := getattr(self, field.name)) is not _UNSET
        }

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            raise AssertionError(_DEPRECATED_MSG)
        if isinstance(other, StrictDataclassDeviceInfo):
            return all(
                getattr(self, field.name) == getattr(other, field.name)
                for field in fields(self)
            )
        return NotImplemented

    def __getitem__(self, key: str) -> Any:
        raise AssertionError(_DEPRECATED_MSG)

    def __setitem__(self, key: str, value: Any) -> None:
        raise AssertionError(_DEPRECATED_MSG)

    def __contains__(self, key: object) -> bool:
        raise AssertionError(_DEPRECATED_MSG)

    def get(self, key: str, default: Any = None) -> Any:
        raise AssertionError(_DEPRECATED_MSG)

    def keys(self) -> Any:
        raise AssertionError(_DEPRECATED_MSG)

    def items(self) -> Any:
        raise AssertionError(_DEPRECATED_MSG)

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError(_DEPRECATED_MSG)


def test_strict_dataclass_simulator_forbids_mapping_and_enforces_slots() -> None:
    """Verify the simulator itself rejects all deprecated mapping methods and dynamic attrs."""
    sim = StrictDataclassDeviceInfo(
        identifiers={(DOMAIN, "00:03:50:00:00:01-1-21")},
        name="Living Light",
    )
    same_sim = StrictDataclassDeviceInfo(
        identifiers={(DOMAIN, "00:03:50:00:00:01-1-21")},
        name="Living Light",
    )
    other_sim = StrictDataclassDeviceInfo(
        identifiers={(DOMAIN, "00:03:50:00:00:01-1-21")},
        name="Kitchen Light",
    )
    assert sim.identifiers == {(DOMAIN, "00:03:50:00:00:01-1-21")}
    assert sim.name == "Living Light"
    assert sim == same_sim
    assert sim != other_sim
    assert (sim == object()) is False
    assert sim.as_dict() == {
        "identifiers": {(DOMAIN, "00:03:50:00:00:01-1-21")},
        "name": "Living Light",
    }

    with pytest.raises(AttributeError):
        sim.extra_attr = "nope"  # type: ignore[attr-defined]

    with pytest.raises(TypeError):
        iter(sim)
    with pytest.raises(TypeError):
        list(sim)

    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        _ = sim == {"name": "x"}
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        _ = sim != {"name": "x"}
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        _ = sim["name"]
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        sim["via_device_id"] = "gw"
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        _ = "name" in sim
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        sim.get("name")
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        sim.keys()
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        sim.items()
    with pytest.raises(AssertionError, match=_DEPRECATED_MSG):
        sim.update({"model": "F411"})


def test_device_info_to_dict_cross_version_branches() -> None:
    """Verify typing_compat.device_info_to_dict handles dataclass, TypedDict dict, and None."""
    # 1. HA 2026.11+ dataclass with .as_dict() (2026.12+ deprecates _DeviceInfoMapping)
    sim = StrictDataclassDeviceInfo(
        identifiers={(DOMAIN, "mac-1-11")},
        name="Kitchen",
        via_device_id="gw-123",
    )
    assert device_info_to_dict(sim) == {
        "identifiers": {(DOMAIN, "mac-1-11")},
        "name": "Kitchen",
        "via_device_id": "gw-123",
    }

    # Verify .as_dict() result is returned directly without a redundant second dict copy.
    fresh = {"identifiers": {(DOMAIN, "mac-1-99")}, "name": "Direct"}

    class _AsDictProvider:
        def as_dict(self) -> dict[str, Any]:
            return fresh

    assert device_info_to_dict(_AsDictProvider()) is fresh

    # 2. HA 2026.8–2026.10 TypedDict (plain dict at runtime without .as_dict())
    legacy_dict = {"identifiers": {(DOMAIN, "mac-1-12")}, "name": "Hallway"}
    converted = device_info_to_dict(legacy_dict)
    assert converted == legacy_dict
    assert converted is not legacy_dict

    # 3. None or non-mapping objects without a callable .as_dict
    assert device_info_to_dict(None) == {}
    assert device_info_to_dict(object()) == {}


@pytest.mark.parametrize("device_registry_id", ["gw-reg-930", None, ""])
def test_myhome_entity_constructs_dataclass_device_info_without_mapping_mutation(
    monkeypatch: pytest.MonkeyPatch,
    device_registry_id: str | None,
) -> None:
    """MyHOMEEntity passes via_device_id at construction and never mutates DeviceInfo via []."""
    monkeypatch.setattr(myhome_device, "DeviceInfo", StrictDataclassDeviceInfo)

    gateway = MagicMock()
    gateway.mac = "00:03:50:aa:bb:cc"
    gateway.unique_id = gateway.mac
    gateway.device_registry_id = device_registry_id

    entity = MyHOMEEntity(
        hass=None,
        name="Ceiling Spot",
        platform="light",
        device_id="1-21",
        who="1",
        where="21",
        manufacturer="BTicino",
        model="F411/4",
        gateway=gateway,
    )

    assert isinstance(entity.device_info, StrictDataclassDeviceInfo)
    as_dict = device_info_to_dict(entity.device_info)
    assert as_dict["identifiers"] == {(DOMAIN, "00:03:50:aa:bb:cc-1-21")}
    assert as_dict["name"] == "Ceiling Spot"
    assert as_dict["manufacturer"] == "BTicino"
    assert as_dict["model"] == "F411/4"
    if device_registry_id:
        assert as_dict["via_device_id"] == device_registry_id
    else:
        assert "via_device_id" not in as_dict


async def test_switch_shared_bus_follower_pruning_with_dataclass_device_info(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """switch._contact_only_on_primary reads identifiers without deprecated DeviceInfo.get()."""
    monkeypatch.setattr(myhome_device, "DeviceInfo", StrictDataclassDeviceInfo)

    pri_mac = "00:03:50:93:00:01"
    fol_mac = "00:03:50:93:00:02"
    entry_primary, _ = _create_mock_gateway(
        hass, pri_mac, topology=TOPOLOGY_SHARED, role=ROLE_PRIMARY
    )
    entry_follower, follower_gw = _create_mock_gateway(
        hass,
        fol_mac,
        topology=TOPOLOGY_SHARED,
        role=ROLE_STANDBY,
        primary_gateway=pri_mac,
    )
    follower_gw.device_registry_id = "follower-gw-reg"

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    # Contact 344 has a binary_sensor on the primary gateway ONLY (pruned on follower).
    pri_dev = dev_reg.async_get_or_create(
        config_entry_id=entry_primary.entry_id,
        identifiers={(DOMAIN, f"{pri_mac}-25-344")},
        name="Contact 344",
    )
    ent_reg.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{pri_mac}-25-344-motion",
        config_entry=entry_primary,
        device_id=pri_dev.id,
        original_device_class="motion",
    )

    entry_follower.runtime_data.platforms["binary_sensor"] = {
        "25-344": {
            "who": "25",
            "where": "344",
            "name": "Primary Only Contact",
            "class": "motion",
            "inverted": False,
        },
        "25-77": {
            "who": "25",
            "where": "77",
            "name": "Follower Local Contact",
            "class": "motion",
            "inverted": False,
        },
    }
    entry_follower.runtime_data.platforms["switch"] = {}

    added: list[Any] = []
    assert await switch.async_setup_entry(hass, entry_follower, added.extend) is True

    invert_uids = {ent.unique_id for ent in added}
    assert f"{fol_mac}-25-344-invert" not in invert_uids
    assert f"{fol_mac}-25-77-invert" in invert_uids
    for ent in added:
        assert isinstance(ent.device_info, StrictDataclassDeviceInfo)


def test_button_entities_construct_dataclass_device_info(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both gateway-level and device-level buttons construct dataclass DeviceInfo cleanly."""
    monkeypatch.setattr(button, "DeviceInfo", StrictDataclassDeviceInfo)
    monkeypatch.setattr(myhome_device, "DeviceInfo", StrictDataclassDeviceInfo)

    entry, gw = _create_mock_gateway(hass, "00:03:50:93:00:03")
    gw.device_registry_id = "gw-btn-930"

    cal_all = CalibrateAllCoversButtonEntity(hass, entry, gw)
    assert isinstance(cal_all.device_info, StrictDataclassDeviceInfo)
    assert device_info_to_dict(cal_all.device_info) == {
        "identifiers": {(DOMAIN, gw.unique_id)}
    }

    cal_one = CalibrateCoverButtonEntity(
        hass,
        platform="button",
        device_id="21",
        where="21",
        interface=None,
        name="Shutter 21",
        gateway=gw,
    )
    assert isinstance(cal_one.device_info, StrictDataclassDeviceInfo)
    assert device_info_to_dict(cal_one.device_info)["via_device_id"] == "gw-btn-930"

    disable_btn = DisableCommandButtonEntity(
        hass,
        platform="button",
        name="Light 21",
        device_id="21",
        who="1",
        where="21",
        interface=None,
        manufacturer="BTicino",
        model="F411/4",
        gateway=gw,
    )
    assert isinstance(disable_btn.device_info, StrictDataclassDeviceInfo)
    assert device_info_to_dict(disable_btn.device_info)["identifiers"] == {
        (DOMAIN, "00:03:50:93:00:03-1-21")
    }
