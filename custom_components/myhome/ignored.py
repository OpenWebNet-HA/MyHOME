"""Ignored bus addresses: skip discovery, entities, and health tracking."""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

from .const import CONF_IGNORED_ADDRESSES, CONF_WHERE, CONF_WHO
from .where_grammar import canonical_contact_where, canonical_point_spelling, is_apl_address

_PAIR_PATTERN = re.compile(
    r"^(?:who\s*[:=]?\s*)?(\d+)\s*(?:[/:\-,]|\s+(?:where\s*[:=]?\s*)?)\s*(?:where\s*[:=]?\s*)?(#?[0-9A-Za-z#]+)$",
    re.ASCII | re.IGNORECASE,
)


def _is_ascii_digits(value: str) -> bool:
    """Return True when ``value`` is a non-empty ASCII decimal digit string."""
    return bool(value) and value.isascii() and value.isdigit()


def _split_f422_suffix(where: str) -> tuple[str, str | None, bool]:
    """Split optional trailing ``#4#II`` (``00``..``15``) from ``where``.

    Returns ``(base, normalized_iface, is_valid)``. When ``#4#`` is present with
    an empty base or an invalid interface token (e.g. ``#4#20``), ``is_valid`` is
    ``False``.
    """
    base, sep, iface = where.rpartition("#4#")
    if not sep:
        return where, None, True
    if not base or not _is_ascii_digits(iface) or len(iface) > 2 or int(iface) > 15:
        return "", None, False
    return base, iface.zfill(2), True


def _normalize_parsed_where(who: int, raw_where: str) -> str | None:
    """Validate and canonicalize a parsed WHERE token for ``who``."""
    where = raw_where.strip()
    if not where or not where.isascii():
        return None
    base, iface, ok = _split_f422_suffix(where)
    if not ok:
        return None

    if who == 4:
        if base.startswith("#"):
            body = base[1:]
            if _is_ascii_digits(body):
                clean_base = body.lstrip("0") or "0"
            elif body.startswith("0#"):
                zone_str = body[2:]
                if not _is_ascii_digits(zone_str) or not (1 <= int(zone_str) <= 99):
                    return None
                clean_base = f"#0#{int(zone_str)}"
            else:
                return None
        elif "#" in base:
            parts = base.split("#")
            if (
                len(parts) != 2
                or not _is_ascii_digits(parts[0])
                or not _is_ascii_digits(parts[1])
                or not (0 <= int(parts[0]) <= 99)
                or not (1 <= int(parts[1]) <= 9)
            ):
                return None
            clean_base = f"{int(parts[0])}#{int(parts[1])}"
        elif _is_ascii_digits(base):
            clean_base = base.lstrip("0") or "0"
        else:
            return None
    elif base.startswith("#"):
        body = base[1:]
        if not _is_ascii_digits(body):
            return None
        clean_base = "#" + (body.lstrip("0") or "0")
    elif base.isalnum():
        clean_base = base
    else:
        return None

    return f"{clean_base}#4#{iface}" if iface is not None else clean_base


def _parse_who_and_where(who_raw: Any, where_raw: Any) -> tuple[int, str] | None:
    """Convert raw ``(who, where)`` values into canonical ``(who, where)`` or ``None``."""
    if isinstance(who_raw, bool) or isinstance(where_raw, bool) or where_raw is None:
        return None
    if not isinstance(where_raw, (str, int)):
        return None
    try:
        who = int(who_raw)
    except (ValueError, TypeError):
        return None
    if who < 0:
        return None
    norm_where = _normalize_parsed_where(who, str(where_raw))
    if norm_where is None:
        return None
    return who, norm_where


def parse_ignored_address(value: Any) -> tuple[int, str] | None:
    """Parse a single WHO/WHERE specification into ``(who, where)``.

    Accepts:
    * String formats: ``"1/74"``, ``"1/#14"``, ``"1:74"``, ``"1-74"``,
      ``"WHO 1 WHERE 74"``, ``"1/74#4#01"``, ``"4/#0#1"``, ``"4/0#1"``
    * Dict formats: ``{"who": 1, "where": "74"}``
    * Tuple/list formats: ``(1, "74")``, ``[1, "74"]``

    Returns ``None`` when the format cannot be parsed or ``who``/``where`` is invalid.
    """
    if isinstance(value, str):
        val = value.strip()
        if not val:
            return None
        match = _PAIR_PATTERN.match(val)
        if match:
            return _parse_who_and_where(match.group(1), match.group(2))
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
            return _parse_who_and_where(who_val, where_val)
        return None

    if isinstance(value, (list, tuple)) and len(value) == 2:
        return _parse_who_and_where(value[0], value[1])

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
            norm = self._norm_where(where, who)
            self._normalized.add((who, norm))
            if who == 25:
                # A dry contact typed as an F455 spells it (301) is the 31 of the other gateways
                self._normalized.add((who, canonical_contact_where(norm)))

    @staticmethod
    def _norm_where(where: str, who: int | None = None) -> str:
        """Normalize WHERE, preserving canonical A/PL, group ``#G``, and WHO=4 ``#0#N`` / ``Z#A`` forms."""
        base, sep, iface = where.rpartition("#4#")
        if not sep or not base:
            base, sep, iface = where, "", ""
        if _is_ascii_digits(base):
            if who in (0, 1, 2, 14, 15):
                if is_apl_address(base):
                    clean_base = canonical_point_spelling(base)
                elif base in ("0", "00", "100"):
                    clean_base = base
                else:
                    stripped = base.lstrip("0")
                    clean_base = stripped if len(stripped) == 2 and int(stripped) > 15 else base
            else:
                clean_base = base.lstrip("0") or "0"
        elif base.startswith("#"):
            body = base[1:]
            if _is_ascii_digits(body):
                raw_digits = body.lstrip("0") or "0"
                clean_base = raw_digits if who == 4 else f"#{raw_digits}"
            elif who == 4 and body.startswith("0#") and _is_ascii_digits(body[2:]) and 1 <= int(body[2:]) <= 99:
                clean_base = f"#0#{int(body[2:])}"
            else:
                clean_base = base
        elif who == 4 and "#" in base:
            parts = base.split("#")
            if (
                len(parts) == 2
                and _is_ascii_digits(parts[0])
                and _is_ascii_digits(parts[1])
                and 0 <= int(parts[0]) <= 99
                and 1 <= int(parts[1]) <= 9
            ):
                clean_base = f"{int(parts[0])}#{int(parts[1])}"
            else:
                clean_base = base
        else:
            clean_base = base
        clean_iface = (
            iface.zfill(2)
            if _is_ascii_digits(iface) and len(iface) <= 2 and int(iface) <= 15
            else iface
        )
        return f"{clean_base}#4#{clean_iface}" if sep else clean_base

    def is_ignored(self, who: int | str, where: str, interface: str | None = None) -> bool:
        """Return True if the address is configured to be ignored.

        Matches exact address, interface-qualified address, clean where, or
        zero-normalized where without crossing F422 ``#4#II`` sub-bus boundaries
        or conflating ``WHO != 4`` ``#G`` groups with ``G`` points/areas or
        ``WHO == 4`` ``#0#N`` 4-zone central units with ``0#N`` pumps.
        """
        if not self._entries:
            return False

        if not isinstance(where, str) or not where:
            return False

        try:
            who_int = int(who)
        except (ValueError, TypeError):
            return False

        if interface:
            iface_str = interface.zfill(2) if interface.isascii() and interface.isdigit() else interface
            base_where, sep, inline_iface = where.rpartition("#4#")
            inline_norm = (
                inline_iface.zfill(2)
                if sep and inline_iface.isascii() and inline_iface.isdigit()
                else inline_iface
            )
            if sep and inline_norm == iface_str:
                candidates = {where, f"{base_where}#4#{interface}", f"{base_where}#4#{iface_str}"}
            else:
                candidates = {f"{where}#4#{interface}", f"{where}#4#{iface_str}"}
        else:
            candidates = {where}

        # Strip legacy who- prefix if present; single-token WHO=4 '#' aliasing is handled by _norm_where
        for c in list(candidates):
            if "-" in c:
                candidates.add(c.split("-")[-1])
        if who_int == 25:
            candidates.update([canonical_contact_where(self._norm_where(c, who_int)) for c in candidates])

        for c in candidates:
            if (who_int, c) in self._entries:
                return True
            if (who_int, self._norm_where(c, who_int)) in self._normalized:
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
