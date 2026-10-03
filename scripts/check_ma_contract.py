"""Check that Music Assistant's HA player provider still makes the calls MyHOME relies on.

MyHOME implements HA ``media_player`` grouping and Music Assistant's ``hass_players``
provider drives it (see ``docs/configuration/media_player.md``). The group entity's
behaviour rests on three facts about that provider, each pinned by a replay test in
``tests/test_audio_audit_hardening.py``:

* ``set_members`` joins with only the *added* entities and unjoins removed ones one by one;
* a player is unjoined and may then get ``play_media`` as a new leader;
* volume is set through ``volume_set`` (0 is a volume, not a mute).

This script downloads the upstream file and fails when those anchors disappear, so a
change on their side is noticed by CI before a tester reports it. It does not import
Home Assistant. Usage: ``python scripts/check_ma_contract.py [path-or-url]``.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

UPSTREAM = (
    "https://raw.githubusercontent.com/music-assistant/server/dev/"
    "music_assistant/providers/hass_players/player.py"
)

# (anchor in the upstream source, why MyHOME depends on it)
ANCHORS: tuple[tuple[str, str], ...] = (
    ("async def set_members", "join/unjoin arrive as one set_members call"),
    ("player_ids_to_add", "the call carries only the added members (additive join)"),
    ("player_ids_to_remove", "removed members are unjoined one by one, not re-sent as a snapshot"),
    ('service="unjoin"', "removal is HA's unjoin on the removed player (async_unjoin_player)"),
    ('service="join"', "addition is HA's join on the leader (async_join_players)"),
    ('"group_members": player_ids_to_add', "join carries only the new members"),
    ('service="play_media"', "a freshly unjoined member may get play_media as a new leader"),
    ('service="volume_set"', "volume goes through volume_set, so 0 is never a mute"),
)


def load(source: str) -> str:
    """Return the text of ``source``: a URL or a local path."""
    if source.startswith(("http://", "https://")):
        ctx = None
        try:
            import ssl

            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
        with urllib.request.urlopen(source, timeout=30, context=ctx) as response:  # noqa: S310 - fixed https upstream
            return str(response.read().decode("utf-8"))
    return Path(source).read_text(encoding="utf-8")


def missing_anchors(text: str) -> list[str]:
    """Return the reasons of the anchors that ``text`` no longer contains."""
    return [f"{anchor!r}: {why}" for anchor, why in ANCHORS if anchor not in text]


def main(argv: list[str]) -> int:
    """Fail (exit 1) when an anchor is gone; a download error is a warning, not a failure."""
    source = argv[1] if len(argv) > 1 else UPSTREAM
    try:
        text = load(source)
    except OSError as err:
        print(f"::warning::could not read {source}: {err}")
        return 0
    lost = missing_anchors(text)
    for line in lost:
        print(f"::error::Music Assistant contract changed, missing {line}")
    if not lost:
        print(f"Music Assistant contract intact ({len(ANCHORS)} anchors in {source}).")
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
