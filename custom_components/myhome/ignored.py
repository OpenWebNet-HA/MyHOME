"""Ignored bus addresses: skip discovery, entities, and health tracking."""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

from .const import CONF_IGNORED_ADDRESSES, CONF_WHERE, CONF_WHO

_PAIR_PATTERN = re.compile(
    r"^(?:who\s*[:=]?\s*)?(\d+)\s*(?:[/:\-,]|\s+(?:where\s*[:=]?\s*)?)\s*(?:where\s*[:=]?\s*)?([0-9A-Za-z]+(?:#4#[0-9A-Za-z]+)?)$",
    re.IGNORECASE,
)


def parse_ignored_address(value: Any) -> tuple[int, str] | None:
    """Parse a single WHO/WHERE specification into ``(who, where)``.

    Accepts:
    * String formats: ``"1/74"``, ``"1:74"``, ``"1-74"``, ``"WHO 1 WHERE 74"``,
      ``"1/74#4#01"``
    * Dict formats: ``{"who": 1, "where": "74"}``
    * Tuple/list formats: ``(1, "74")``, ``[1, "74"]``

    Returns ``None`` when the format cannot be parsed or ``who`` is invalid.
    """
    if isinstance(value, str):
        val = value.strip()
        if not val:
            return None
        match = _PAIR_PATTERN.match(val)
        if match:
            who = int(match.group(1))
            where = match.group(2).strip()
            if where:
                return who, where
        return None

    if isinstance(value, dict):
        who_val = None
        where_val = None
        for k, v in value.items():
            k_lower = str(k).lower()
            if k_lower in ("who", CONF_WHO):
                who_val = v
            elif k_lower in ("where", CONF_WHERE):
                where_val = v
        if who_val is not None and where_val is not None:
            try:
                who = int(who_val)
                where = str(where_val).strip()
                if where and re.fullmatch(r"[0-9A-Za-z]+(?:#4#[0-9A-Za-z]+)?", where):
                    return who, where
            except (ValueError, TypeError):
                return None
        return None

    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            who = int(value[0])
            where = str(value[1]).strip()
            if where and re.fullmatch(r"[0-9A-Za-z]+(?:#4#[0-9A-Za-z]+)?", where):
                return who, where
        except (ValueError, TypeError):
            return None

    return None


def parse_ignored_addresses(raw: Any) -> list[str]:
    """Parse and normalize raw ignored addresses into a canonical list of ``"who/where"`` strings.

    Accepts multiline/comma-delimited strings or iterables. Invalid entries are ignored.
    """
    results: set[tuple[int, str]] = set()
    if isinstance(raw, str):
        items = [item.strip() for item in re.split(r"[\n,]+", raw) if item.strip()]
        for item in items:
            parsed = parse_ignored_address(item)
            if parsed is not None:
                results.add(parsed)
    elif isinstance(raw, Iterable) and not isinstance(raw, (bytes, bytearray)):
        for item in raw:
            parsed = parse_ignored_address(item)
            if parsed is not None:
                results.add(parsed)

    return [f"{who}/{where}" for who, where in sorted(results, key=lambda p: (p[0], p[1]))]


def validate_ignored_addresses(raw: Any) -> tuple[list[str], bool]:
    """Parse and validate raw ignored addresses.

    Returns ``(canonical_list, is_valid)``. ``is_valid`` is ``False`` if any non-empty item
    failed to parse.
    """
    results: set[tuple[int, str]] = set()
    if isinstance(raw, str):
        items = [item.strip() for item in re.split(r"[\n,]+", raw) if item.strip()]
        for item in items:
            parsed = parse_ignored_address(item)
            if parsed is None:
                return [], False
            results.add(parsed)
    elif isinstance(raw, Iterable) and not isinstance(raw, (bytes, bytearray)):
        for item in raw:
            if isinstance(item, str) and not item.strip():
                continue
            parsed = parse_ignored_address(item)
            if parsed is None:
                return [], False
            results.add(parsed)
    elif raw is None:
        return [], True
    else:
        return [], False

    return [f"{who}/{where}" for who, where in sorted(results, key=lambda p: (p[0], p[1]))], True


class IgnoredAddresses:
    """Fast lookup for ignored (who, where) device addresses."""

    def __init__(self, raw: Any = None) -> None:
        """Initialize with raw address entries."""
        self._entries: set[tuple[int, str]] = set()
        self._normalized: set[tuple[int, str]] = set()

        if raw is not None:
            self.update(raw)

    @classmethod
    def from_config_entry(cls, entry: ConfigEntry | None) -> IgnoredAddresses:
        """Create from a config entry's options or data."""
        if entry is None:
            return cls()
        raw = getattr(entry, "options", {}).get(CONF_IGNORED_ADDRESSES)
        if raw is None:
            raw = getattr(entry, "data", {}).get(CONF_IGNORED_ADDRESSES)
        return cls(raw)

    def update(self, raw: Any) -> None:
        """Add addresses from raw input."""
        parsed_list = parse_ignored_addresses(raw)
        for s in parsed_list:
            who_str, _, where = s.partition("/")
            who = int(who_str)
            self._entries.add((who, where))
            self._normalized.add((who, self._norm_where(where)))

    @staticmethod
    def _norm_where(where: str) -> str:
        """Normalize WHERE: strip leading zeros from device number if numeric."""
        base, sep, iface = where.partition("#4#")
        clean_base = base.lstrip("0") or "0" if base.isdigit() else base
        clean_iface = iface.zfill(2) if iface.isdigit() else iface
        return f"{clean_base}#4#{clean_iface}" if sep else clean_base

    def is_ignored(self, who: int | str, where: str, interface: str | None = None) -> bool:
        """Return True if the address is configured to be ignored.

        Matches exact address, interface-qualified address, clean where, or
        zero-normalized where.
        """
        if not self._entries:
            return False

        if not isinstance(where, str) or not where:
            return False

        try:
            who_int = int(who)
        except (ValueError, TypeError):
            return False

        candidates = {where}
        if interface:
            iface_str = interface.zfill(2) if interface.isdigit() else interface
            candidates.add(f"{where}#4#{interface}")
            candidates.add(f"{where}#4#{iface_str}")

        if "#4#" in where:
            bare = where.partition("#4#")[0]
            candidates.add(bare)

        # Also strip who- prefix if any (e.g. clean WHERE)
        for c in list(candidates):
            if "-" in c:
                candidates.add(c.split("-")[-1])

        for c in candidates:
            if (who_int, c) in self._entries:
                return True
            if (who_int, self._norm_where(c)) in self._normalized:
                return True

        return False

    def __contains__(self, item: object) -> bool:
        """Containment check."""
        if not isinstance(item, tuple) or len(item) != 2:
            return False
        return self.is_ignored(item[0], item[1])

    def __iter__(self) -> Iterator[tuple[int, str]]:
        """Iterate over canonical (who, where) tuples."""
        return iter(self._entries)

    def __len__(self) -> int:
        """Count of ignored addresses."""
        return len(self._entries)

    def __bool__(self) -> bool:
        """True if any address is ignored."""
        return bool(self._entries)
