"""Device health: faults of bus devices, raised as self-clearing repair issues.

One tracker per gateway, the only code that creates or deletes these issues.
Faults reach it two ways:

* frames, classified as they arrive (:meth:`DeviceHealth.observe`), so an
  address with no entity - a light nobody configured, a switch - still raises
  its issue;
* entities, for what only they can tell (a heating zone that stops answering
  its status request): :meth:`DeviceHealth.report` and :meth:`DeviceHealth.clear`.

An issue is written once per change: a stuck actuator answering every poll the
same way does not rewrite the registry. Issue ids are
``device_fault_<entry_id>_<kind>_<who>_<where>``, so the ``_<entry_id>_`` sweep
in ``async_remove_entry`` finds them.

A fault is described only as far as the evidence goes. WHAT 19 from a lighting
actuator is outside the published WHO 1 table and has been seen together with a
WHO 1001 DIMENSION 11 mask (EVID-MH200-WHAT19-FAULT). No source documents the
bits of that mask, so it is attached to the issue as raw evidence; it is never
decoded and never raises an issue on its own.
"""
from __future__ import annotations

import time
from collections.abc import Hashable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.issue_registry import (
    async_get as async_get_issue_registry,
)

from .const import DOMAIN, LOGGER
from .ignored import IgnoredAddresses

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

ISSUE_DEVICE_FAULT = "device_fault"
DOCS_URL = "https://openwebnet-ha.github.io/MyHOME/beta/diagnostics/repair-issues/"
# How long a WHO 1001 autodiagnostic report and a status outside the table can be apart
# and still belong together, in either order. On the MH200 (2026-09-26 trace) the mask
# arrived 3.45 s before the status.
EVIDENCE_WINDOW = 10.0
# WHO 1001 dimensions carrying an autodiagnostic bitmask (OPEN.db: 7 on request, 11 pushed).
AUTODIAG_DIMENSIONS = (7, 11)
# Statuses outside the SCS WHO 1 table that are documented as events, not faults (ZigBee
# OpenWebNet spec 4.0: 32 Toggle, 34 movement detected, 39 end of movement detected).
# They say nothing about the on/off state, so they neither raise nor clear a fault.
DOCUMENTED_EVENTS = frozenset({32, 34, 39})


class FaultKind(StrEnum):
    """What is wrong with a device."""

    #: A status outside the published table of its WHO (lighting WHAT 19).
    UNMAPPED_STATUS = "unmapped_status"
    #: The device stopped answering its status request (see ``poll_health``).
    UNRESPONSIVE = "unresponsive"


@dataclass(frozen=True)
class _KindText:
    translation_key: str
    #: Used instead when raw autodiagnostic evidence is attached.
    evidence_translation_key: str | None
    anchor: str


_TEXTS: dict[FaultKind, _KindText] = {
    FaultKind.UNMAPPED_STATUS: _KindText(
        "unmapped_device_status", "unmapped_device_status_autodiag", "unmapped-device-status"
    ),
    FaultKind.UNRESPONSIVE: _KindText("unresponsive_zone", None, "heating-zone-no-longer-answers"),
}


@dataclass(frozen=True)
class Fault:
    """One fault of the device at ``who``/``where`` (``where`` as entities spell ``_full_where``)."""

    who: int
    where: str
    kind: FaultKind
    code: str = ""
    evidence: str = ""


def fault_issue_id(entry_id: str, kind: FaultKind, who: int | str, where: str) -> str:
    """Repair issue id of a fault; ``#`` and ``.`` of the address become ``_``."""
    slug = str(where).replace("#", "_").replace(".", "_")
    return f"{ISSUE_DEVICE_FAULT}_{entry_id}_{kind}_{who}_{slug}"


def message_where(message: Any) -> str | None:
    """WHERE of a frame with its F422 interface (``74#4#01``), or ``None``."""
    where = getattr(message, "where", None)
    if not isinstance(where, str) or not where:
        return None
    params = getattr(message, "_where_param", None)
    if isinstance(params, list) and len(params) > 1 and params[0] == "4":
        return f"{where}#4#{params[1]}"
    return where


class DeviceHealth:
    """Faults of the devices behind one gateway."""

    def __init__(self, handler: MyHOMEGatewayHandler) -> None:
        self._handler = handler
        self._active: dict[tuple[int, str, FaultKind], Fault] = {}
        self._names: dict[tuple[int, str], str] = {}
        self._owners: dict[tuple[int, str], set[Hashable]] = {}
        # Last WHO 1001 autodiagnostic frame per address: (frame, monotonic time).
        self._autodiag: dict[str, tuple[str, float]] = {}
        # When each address last sent a status outside the table (monotonic time).
        self._anomaly_seen: dict[str, float] = {}
        self._clean_ignored_issues()

    @property
    def ignored_addresses(self) -> IgnoredAddresses:
        """Addresses configured to be ignored on this gateway."""
        entry = getattr(self._handler, "config_entry", None)
        return IgnoredAddresses.from_config_entry(entry)

    def is_ignored(self, who: int | str, where: str) -> bool:
        """Whether (who, where) is configured to be ignored."""
        try:
            who_int = int(who)
        except (ValueError, TypeError):
            return False
        return self.ignored_addresses.is_ignored(who_int, where)

    def _clean_ignored_issues(self) -> None:
        """Withdraw issues for addresses that are ignored."""
        hass, entry_id = self._target()
        if hass is None or entry_id is None:
            return
        ignored = self.ignored_addresses
        if not ignored:
            return

        # Query issue registry to match routed and zero-normalized variants
        try:
            issue_registry = async_get_issue_registry(hass)
            prefix = f"{ISSUE_DEVICE_FAULT}_{entry_id}_"
            for domain, issue_id in list(issue_registry.issues):
                if domain == DOMAIN and issue_id.startswith(prefix):
                    issue = issue_registry.async_get_issue(domain, issue_id)
                    if issue and issue.translation_placeholders:
                        issue_who = issue.translation_placeholders.get("who")
                        issue_where = issue.translation_placeholders.get("where")
                        if issue_who and issue_where and self.is_ignored(issue_who, issue_where):
                            async_delete_issue(hass, DOMAIN, issue_id)
        except Exception:
            pass

        # Direct deletion fallback for simple mock test harnesses
        for who, where in ignored:
            for kind in FaultKind:
                async_delete_issue(hass, DOMAIN, fault_issue_id(entry_id, kind, who, where))

    @property
    def faults(self) -> list[dict[str, Any]]:
        """Active faults, for diagnostics (device names left out)."""
        return [
            {"who": f.who, "where": f.where, "kind": str(f.kind), "code": f.code, "evidence": f.evidence}
            for f in self._active.values()
        ]

    # ── entities ───────────────────────────────────────────────────────

    def name_address(self, who: int | str, where: str, name: str, owner: Hashable | None = None) -> None:
        """Name the device at an address in its issues (until then: ``WHO x WHERE y``).

        ``owner`` identifies the entity (its unique id) so that :meth:`forget_address`
        knows when the last entity of the address is gone.
        """
        if self.is_ignored(who, where):
            return
        key = (int(who), where)
        if owner is not None:
            self._owners.setdefault(key, set()).add(owner)
        if self._names.get(key) == name:
            return
        self._names[key] = name
        for fault in [f for f in self._active.values() if (f.who, f.where) == key]:
            self._raise(fault)

    def forget_address(self, who: int | str, where: str, owner: Hashable | None = None) -> None:
        """The owner removed an entity of the device: with the last one, drop its name and issues."""
        key = (int(who), where)
        owners = self._owners.get(key)
        if owners is not None:
            owners.discard(owner)
            if owners:
                return
            del self._owners[key]
        self._names.pop(key, None)
        for fault in [f for f in self._active.values() if (f.who, f.where) == key]:
            self.clear(fault.who, fault.where, fault.kind)

    def report(self, fault: Fault, device: str | None = None) -> None:
        """Raise ``fault``, or update its issue when its code or evidence changed."""
        if self.is_ignored(fault.who, fault.where):
            return
        if device:
            self._names[(fault.who, fault.where)] = device
        key = (fault.who, fault.where, fault.kind)
        if self._active.get(key) == fault:
            return
        self._active[key] = fault
        self._raise(fault)

    def clear(self, who: int | str, where: str, kind: FaultKind) -> None:
        """Withdraw the fault's issue.

        The registry is always asked, not only when this tracker holds the fault: an
        issue it does not know about (raised by a frame that arrived while the entry
        unloaded) must still clear. Deleting a missing issue is a cheap no-op.
        """
        was_active = self._active.pop((int(who), where, kind), None) is not None
        hass, entry_id = self._target()
        if hass is not None and entry_id is not None:
            if was_active:
                LOGGER.debug("%s %s at WHO %s WHERE %s cleared", self._log_id, kind, who, where)
            async_delete_issue(hass, DOMAIN, fault_issue_id(entry_id, kind, who, where))

    def clear_all(self) -> None:
        """Withdraw every issue (the entry unloads; a reload raises what is still wrong)."""
        for who, where, kind in list(self._active):
            self.clear(who, where, kind)

    # ── frames ─────────────────────────────────────────────────────────

    def observe(self, message: Any) -> None:
        """Classify a frame from the bus."""
        who = getattr(message, "who", None)
        if who == 1:
            self._observe_lighting(message)
        elif who == 1001:
            self._observe_lighting_autodiag(message)

    def _observe_lighting(self, message: Any) -> None:
        if getattr(message, "is_translation", False) is True or any(
            getattr(message, scope, False) is True for scope in ("is_general", "is_area", "is_group")
        ):
            return
        where = message_where(message)
        if where is None or self.is_ignored(1, where):
            return
        unknown = getattr(message, "unknown_state", None)
        if isinstance(unknown, int) and not isinstance(unknown, bool):
            if unknown in DOCUMENTED_EVENTS:
                return
            code = str(unknown)
            self._anomaly_seen[where] = time.monotonic()
            active = self._active.get((1, where, FaultKind.UNMAPPED_STATUS))
            evidence = self._recent_autodiag(where) or (
                active.evidence if active is not None and active.code == code else ""
            )
            self.report(Fault(1, where, FaultKind.UNMAPPED_STATUS, code, evidence))
        elif getattr(message, "is_on", None) is not None:
            self.clear(1, where, FaultKind.UNMAPPED_STATUS)

    def _observe_lighting_autodiag(self, message: Any) -> None:
        if getattr(message, "dimension", None) not in AUTODIAG_DIMENSIONS:
            return
        where = message_where(message)
        if where is None or self.is_ignored(1, where):
            return
        values = getattr(message, "_dimension_value", None)
        if not isinstance(values, list) or not values:
            return
        mask = str(values[0])
        if not mask or set(mask) - {"0", "1"}:
            return
        frame = str(message)
        self._autodiag[where] = (frame, time.monotonic())
        active = self._active.get((1, where, FaultKind.UNMAPPED_STATUS))
        # The window holds in both directions: a mask long after the last odd status is
        # not evidence for it.
        if active is not None and time.monotonic() - self._anomaly_seen.get(where, float("-inf")) <= EVIDENCE_WINDOW:
            self.report(replace(active, evidence=frame))

    def _recent_autodiag(self, where: str) -> str:
        seen = self._autodiag.get(where)
        if seen is None or time.monotonic() - seen[1] > EVIDENCE_WINDOW:
            return ""
        return seen[0]

    # ── issues ─────────────────────────────────────────────────────────

    @property
    def _log_id(self) -> str:
        return str(getattr(self._handler, "log_id", ""))

    def _target(self) -> tuple[Any, str | None]:
        entry_id = getattr(getattr(self._handler, "config_entry", None), "entry_id", None)
        return getattr(self._handler, "hass", None), entry_id if isinstance(entry_id, str) else None

    def _raise(self, fault: Fault) -> None:
        hass, entry_id = self._target()
        if hass is None or entry_id is None:
            return
        text = _TEXTS[fault.kind]
        translation_key = (
            text.evidence_translation_key if fault.evidence and text.evidence_translation_key else text.translation_key
        )
        device = self._names.get((fault.who, fault.where)) or f"WHO {fault.who} WHERE {fault.where}"
        LOGGER.debug(
            "%s %s at WHO %s WHERE %s (code %s, evidence %s)",
            self._log_id, fault.kind, fault.who, fault.where, fault.code or "-", fault.evidence or "-",
        )
        placeholders = {
            "device": device,
            "gateway": str(getattr(self._handler, "name", "")),
            "who": str(fault.who),
            "where": fault.where,
            "code": fault.code,
            "evidence": fault.evidence,
        }
        if fault.kind is FaultKind.UNRESPONSIVE:
            # The string used {zone} before it was shared; a translation that still has it must render.
            placeholders["zone"] = device
        async_create_issue(
            hass,
            DOMAIN,
            fault_issue_id(entry_id, fault.kind, fault.who, fault.where),
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key=translation_key,
            translation_placeholders=placeholders,
            learn_more_url=f"{DOCS_URL}#{text.anchor}",
        )
