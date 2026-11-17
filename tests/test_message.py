import pytest
from OWNd.message import OWNEvent, OWNSoundCommand, OWNSoundEvent


def test_own_sound_event_parsing_baseband():
    """Test parsing of WHO=16 Audio Events with baseband WHAT values (0/10)."""

    # Test Sound ON baseband (WHAT=0, General)
    message = OWNEvent.parse("*16*0*0##")
    assert isinstance(message, OWNSoundEvent)
    assert message.who == 16
    assert message.is_on is True
    assert message.is_off is False
    assert message.zone == "0"
    assert message.is_source_event is False

    # Test Sound OFF baseband (WHAT=10, Zone 21)
    message = OWNEvent.parse("*16*10*21##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is False
    assert message.is_off is True
    assert message.zone == "21"
    assert message.is_source_event is False

    # Test Source ON baseband (WHAT=0, WHERE=102 → Source 2)
    message = OWNEvent.parse("*16*0*102##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is True
    assert message.is_source_event is True
    assert message.source_id == "2"


def test_own_sound_event_parsing_stereo():
    """Test parsing of WHO=16 Audio Events with stereo WHAT values (3/13).

    BTicino Sound System 2.0 uses WHAT=3 for stereo ON and WHAT=13 for
    stereo OFF.  These must be recognised as on/off state changes alongside
    the legacy baseband values (0/10).
    """

    # Stereo ON zone (WHAT=3, WHERE=22 — kitchen zone from live capture)
    message = OWNEvent.parse("*16*3*22##")
    assert isinstance(message, OWNSoundEvent)
    assert message.who == 16
    assert message.is_on is True
    assert message.is_off is False
    assert message.zone == "22"
    assert message.is_source_event is False
    assert "switched ON" in message.human_readable_log

    # Stereo OFF zone (WHAT=13, WHERE=22)
    message = OWNEvent.parse("*16*13*22##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is False
    assert message.is_off is True
    assert message.zone == "22"
    assert "switched OFF" in message.human_readable_log

    # Stereo ON source (WHAT=3, WHERE=101 — source 1 from live capture)
    message = OWNEvent.parse("*16*3*101##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is True
    assert message.is_source_event is True
    assert message.source_id == "1"
    assert "Source 1" in message.human_readable_log

    # Stereo OFF source (WHAT=13, WHERE=103)
    message = OWNEvent.parse("*16*13*103##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_off is True
    assert message.is_source_event is True
    assert message.source_id == "3"


def test_own_sound_event_volume():
    """Test volume dimension messages."""

    # Volume Level Dimension (Volume 19 on zone 22 — from live capture)
    message = OWNEvent.parse("*#16*22*1*19##")
    assert isinstance(message, OWNSoundEvent)
    assert message.who == 16
    assert message.zone == "22"
    assert message.volume == 19
    assert message.is_on is False   # volume events don't set state
    assert message.is_off is False

    # Volume Level Dimension (Volume 15 on zone 1)
    message = OWNEvent.parse("*#16*1*1*15##")
    assert isinstance(message, OWNSoundEvent)
    assert message.zone == "1"
    assert message.volume == 15


def test_own_sound_event_amplifier_zones():
    """Test that matrix routing addresses (121, 122, 132) are not source events.

    These are ``1ES`` routing frames (environment E to source S) on BTicino
    multi-amplifier systems, NOT sources (101-109).  The address is asserted on
    ``where``, which MyHOME reads, not on OWNd's ``zone``.
    """

    # *16*3*121## from live capture — routing, not a source
    message = OWNEvent.parse("*16*3*121##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is True
    assert message.is_source_event is False  # 121 does NOT start with "10"
    assert message.source_id is None
    assert message.where == "121"

    # *16*3*122## from live capture
    message = OWNEvent.parse("*16*3*122##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is True
    assert message.is_source_event is False
    assert message.where == "122"

    # *16*3*132## from live capture
    message = OWNEvent.parse("*16*3*132##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is True
    assert message.is_source_event is False
    assert message.where == "132"


def test_own_sound_event_unknown_command():
    """Test that unrecognised WHAT values fall through to generic log."""

    message = OWNEvent.parse("*16*30*22##")
    assert isinstance(message, OWNSoundEvent)
    assert message.is_on is False
    assert message.is_off is False
    assert "received command: 30" in message.human_readable_log


def test_own_sound_command_generation():
    """Test generating WHO=16 Audio Commands.

    Commands must use WHAT=3 (stereo ON) and WHAT=13 (stereo OFF)
    to match BTicino Sound System 2.0 protocol.
    """

    # Status Request: see test_own_sound_status_requests_dimension_5

    # Turn On — must send WHAT=3 (stereo)
    on_msg = OWNSoundCommand.turn_on("11")
    assert str(on_msg) == "*16*3*11##"

    # Turn Off — must send WHAT=13 (stereo)
    off_msg = OWNSoundCommand.turn_off("11")
    assert str(off_msg) == "*16*13*11##"

    # Select Source: see test_own_sound_select_source_routes_the_environment

    # Volume Up
    vol_up_msg = OWNSoundCommand.volume_up("0") # All zones
    assert str(vol_up_msg) == "*16*1001*0##"

    # Volume Down
    vol_down_msg = OWNSoundCommand.volume_down("21")
    # 1101 per the Encyclopedia; OWNd <= 2.0.0b8 still sends the undefined 1000.
    # Tighten to 1101 only once the manifest pins a release with the fix.
    assert str(vol_down_msg) in ("*16*1101*21##", "*16*1000*21##")

    # Set Volume
    set_vol_msg = OWNSoundCommand.set_volume("1", 15)
    assert str(set_vol_msg) == "*#16*1*#1*15##"


# OWNd releases up to and including 2.0.0b8 build the routing address from the
# source and the LAST amplifier digit (22 on source 3 -> 132).  OWNd#48 fixes it
# to environment-then-source (-> 123).  Detect which one is installed instead of
# pinning a version: this suite runs against released OWNd here and against the
# OWNd#48 checkout in that PR's "MyHOME test suite" job, and must be green in
# both.  strict=True turns an unexpected pass into a failure, so the marker
# cannot quietly outlive the old formula.
_OWND_ROUTES_BY_LAST_DIGIT = (
    str(OWNSoundCommand.select_source("22", "3")[1]) == "*16*3*132##"
)


@pytest.mark.xfail(
    _OWND_ROUTES_BY_LAST_DIGIT,
    reason="installed OWNd predates OWNd#48 (routing address built from the last amplifier digit)",
    strict=True,
)
def test_own_sound_select_source_routes_the_environment():
    """``select_source`` returns the two frames a wall panel sends.

    1. Activate the source device on the bus (WHERE = 100 + source).
    2. Route the matrix to that source: compound ``1ES`` address, where E is
       the environment, the FIRST digit of the ``EA`` amplifier address.
    """
    source_cmds = OWNSoundCommand.select_source("22", "3")
    assert isinstance(source_cmds, list)
    assert len(source_cmds) == 2
    assert str(source_cmds[0]) == "*16*3*103##"  # activate source 3 device
    assert str(source_cmds[1]) == "*16*3*123##"  # route environment 2 to source 3


# OWNd releases up to and including 2.0.0b8 build the WHO 16 status request as
# the bare `*#16*WHERE##`, which an MH201 NACKs for every address.  OWNd#51
# sends the specified dimension-5 form `*#16*WHERE*5##`.  Same detection as
# above, so the suite is green against released OWNd and against the OWNd#51
# checkout, and strict=True retires the marker once a release ships the fix.
_OWND_STATUS_IS_BARE = str(OWNSoundCommand.status("22")) == "*#16*22##"


@pytest.mark.xfail(
    _OWND_STATUS_IS_BARE,
    reason="installed OWNd predates OWNd#51 (bare `*#16*WHERE##` status request)",
    strict=True,
)
def test_own_sound_status_requests_dimension_5():
    """WHO 16 status is dimension 5 (``*#16*WHERE*5##``, spec section 1.5.2).

    The gateway answers with ordinary ``*16*WHAT*WHERE##`` state frames.
    """
    assert str(OWNSoundCommand.status("22")) == "*#16*22*5##"


def test_own_sound_live_capture_full_sequence():
    """End-to-end replay of a real SDomotica capture.

    Sequence: turn on kitchen → change volume → switch source → turn off.
    Verifies every message from the live capture is parsed correctly.
    """
    capture = [
        ("*16*13*22##",     {"is_on": False, "is_off": True,  "zone": "22"}),
        ("*#16*22*1*19##",  {"volume": 19, "zone": "22"}),
        ("*16*3*101##",     {"is_on": True,  "is_source_event": True, "source_id": "1"}),
        ("*16*3*121##",     {"is_on": True,  "is_source_event": False, "where": "121"}),
        ("*16*3*122##",     {"is_on": True,  "is_source_event": False, "where": "122"}),
        ("*#16*22*1*20##",  {"volume": 20, "zone": "22"}),
        ("*#16*22*1*21##",  {"volume": 21, "zone": "22"}),
        ("*#16*22*1*20##",  {"volume": 20, "zone": "22"}),
        ("*#16*22*1*19##",  {"volume": 19, "zone": "22"}),
        ("*16*3*22##",      {"is_on": True,  "is_off": False, "zone": "22"}),
        ("*16*3*132##",     {"is_on": True,  "is_source_event": False, "where": "132"}),
    ]

    for raw, expected in capture:
        msg = OWNEvent.parse(raw)
        assert isinstance(msg, OWNSoundEvent), f"Failed to parse {raw} as OWNSoundEvent"
        for attr, value in expected.items():
            actual = getattr(msg, attr)
            assert actual == value, (
                f"{raw}: expected {attr}={value!r}, got {actual!r}"
            )


def test_own_event_unspecialized_who_returns_own_event():
    """Test that valid event frames for unspecialized WHO families (e.g. WHO 6 Door Entry, WHO 8 Intercom)
    return an OWNEvent instance (satisfying isinstance(..., OWNMessage)) instead of raw string."""
    from OWNd.message import OWNMessage

    frames = [
        ("*8*1#1#4*11##", 8, "11"),
        ("*8*9#1#4*20##", 8, "20"),
        ("*6*9**##", 6, "*"),
    ]
    for raw, expected_who, expected_where in frames:
        msg = OWNEvent.parse(raw)
        assert isinstance(msg, OWNMessage), f"Frame {raw} was not parsed as OWNMessage"
        assert isinstance(msg, OWNEvent), f"Frame {raw} was not parsed as OWNEvent"
        assert msg.who == expected_who
        assert msg.where == expected_where
