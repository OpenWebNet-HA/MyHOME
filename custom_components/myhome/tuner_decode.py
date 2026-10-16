"""Pure decoding of FM tuner reports, shared by WHO=16 and WHO=22.

On the SCS bus an F500 / F500N tuner broadcasts a single ``D2`` telegram for
each tuning or RDS update. Inside BTicino gateways, ``bt_difson`` translates
those telegrams to WHO=16 (source ``10S``) and ``bt_multi`` translates them to
WHO=22 (compound source ``5#2#S``). Legacy gateways (MH200, H4684, MH202) run
``bt_difson`` only; dual-stack gateways (MH200N, F454, F453AV) run both daemons
and emit mirrored WHO=16 and WHO=22 reports; modern gateways (MyHomeServer1,
F459, F460, F461) ship ``bt_multi`` only and report exclusively on WHO=22:

======================  ====================  ==========================
Fact                    WHO=16 (source 10S)   WHO=22 (source 5#2#S)
======================  ====================  ==========================
Frequency               dim 6, ``0*<KHZ>``    dim 5, ``1*<10/100 kHz>``
Frequency and preset    -                     dim 11, ``1*<units>*<N>``
Stored station          dim 7, ``0*<N>``      dim 6, ``<N>``
RDS programme service   dim 8, 8 ASCII codes  dim 10, 1..8 ASCII codes
======================  ====================  ==========================

The leading ``0`` / ``1`` token of the frequency reports is the band identifier
(``bt_difson`` formats the 0-based SCS band index ``0`` = FM; ``bt_multi``
hardcodes the 1-based literal ``1`` in its format strings). Everything in this
module is a function of dimension values alone, so it is testable against
recorded frames without a gateway or Home Assistant.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Lowest and highest FM frequency accepted, in kHz. Outside this a value is
#: almost certainly a mistake (a preset number, or MHz passed as kHz).
FM_MIN_KHZ = 87500
FM_MAX_KHZ = 108000

#: ``bt_multi`` firmware computes WHO=22 FM frequencies in 10 kHz units
#: (``steps * 5 + 8700``, so ``9620`` is 96.20 MHz), while the hand-transcribed
#: MyHomeServer1 log excerpt (OWNd#103) records 100 kHz units (``1015`` is
#: 101.5 MHz). The two FM ranges (8750..10800 and 875..1080) do not overlap, so
#: the value itself selects the unit.
WHO22_FREQUENCY_UNITS_KHZ = (10, 100)

#: ``bt_multi`` and ``bt_difson`` reassemble 3-frame SCS RDS sequences
#: (1 start + 4k middle + 3 EOT bytes = 4(k+1) ASCII codes): 8 codes normally
#: (k=1), 4 codes when a middle SCS frame is lost (k=0), and 12 codes when a
#: missed EOT concatenates a second middle frame (k=2); hand-transcribed log
#: excerpts (OWNd#103) also trim trailing space padding (``RADIO`` is 5 codes).
RDS_LENGTH = 8


@dataclass(frozen=True)
class TunerReport:
    """What one tuner dimension frame says; unset fields were not in the frame."""

    frequency_khz: int | None = None
    preset: int | None = None
    rds: str | None = None
    #: True for an RDS frame that carried no printable text (the tuner blanks
    #: the text with spaces while it retunes), as opposed to no RDS frame.
    rds_blank: bool = False


def rds_text(values: Sequence[str]) -> str | None:
    """Decode an RDS payload of decimal ASCII codes into readable text.

    Codes outside the printable range are dropped rather than rendering
    control characters into a media title. Returns ``None`` for a payload that
    holds no visible character.
    """
    chars = [
        chr(int(value))
        for value in values
        if str(value).isdigit() and 32 <= int(value) <= 126
    ]
    text = "".join(chars).strip()
    return text or None


def _is_fm(kilohertz: int) -> bool:
    return FM_MIN_KHZ <= kilohertz <= FM_MAX_KHZ


def frequency_khz_from_who22(raw: str) -> int | None:
    """Convert a WHO=22 frequency (10 or 100 kHz units) to kHz, ``None`` outside FM."""
    if not str(raw).isdigit():
        return None
    for unit in WHO22_FREQUENCY_UNITS_KHZ:
        kilohertz = int(raw) * unit
        if _is_fm(kilohertz):
            return kilohertz
    return None


def frequency_khz_from_who16(raw: str) -> int | None:
    """Validate a WHO=16 frequency (already kHz), ``None`` outside FM."""
    if not str(raw).isdigit():
        return None
    kilohertz = int(raw)
    return kilohertz if _is_fm(kilohertz) else None


def decode_rds_frame(values: Sequence[str]) -> TunerReport | None:
    """Decode a dimension-8 / dimension-10 RDS frame.

    A frame of no codes or of more than eight is a bus glitch (traces show
    12-code frames) and yields ``None`` so it cannot corrupt state.
    """
    if not 0 < len(values) <= RDS_LENGTH:
        return None
    text = rds_text(values)
    return TunerReport(rds=text, rds_blank=text is None)


def decode_who22_tuner(dimension: int | None, values: Sequence[str]) -> TunerReport | None:
    """Decode one WHO=22 tuner frame (source address ``5#2#S``).

    Args:
        dimension: The frame's dimension.
        values: The dimension values, as strings.

    Returns:
        The facts the frame carries, or ``None`` when it carries none this
        module knows (an unknown dimension, or a malformed payload).
    """
    values = [str(v) for v in values]
    if dimension == 5 and len(values) == 2:
        kilohertz = frequency_khz_from_who22(values[1])
        return TunerReport(frequency_khz=kilohertz) if kilohertz else None
    if dimension == 11 and len(values) == 3:
        kilohertz = frequency_khz_from_who22(values[1])
        if kilohertz is None:
            return None
        preset = int(values[2]) if values[2].isdigit() else 0
        # Preset 0 is "not a stored station": a frequency reached by seeking.
        return TunerReport(frequency_khz=kilohertz, preset=preset or None)
    if dimension == 6 and len(values) == 1 and values[0].isdigit():
        preset = int(values[0])
        return TunerReport(preset=preset) if preset >= 1 else None
    if dimension == 10:
        return decode_rds_frame(values)
    return None


def preset_for_frequency(
    frequency_khz: int, presets: dict[int, int]
) -> int | None:
    """Return the stored preset whose frequency is ``frequency_khz``, if any.

    The live frequency is the truth and the preset only a label for it, so an
    unsaved station never inherits the name of a previously tuned preset.
    """
    for number, stored in sorted(presets.items()):
        if stored == frequency_khz:
            return number
    return None

def parse_tuner_pictures(text: str | None) -> dict[int, str]:
    """Parse the user's frequency-to-picture list into ``{kHz: url}``.

    One ``<MHz>=<url>`` entry per line, for example ``96.2=/local/radio/one.png``.
    Lines that are not an FM frequency in MHz followed by a local path or an
    ``http(s)`` URL are skipped rather than failing the whole list, so a typo
    costs one picture, not all of them. The user supplies the images; none are
    bundled.
    """
    pictures: dict[int, str] = {}
    for line in (text or "").splitlines():
        frequency, separator, url = line.partition("=")
        frequency, url = frequency.strip(), url.strip()
        if not separator or not url.startswith(("/", "http://", "https://")):
            continue
        try:
            kilohertz = int(round(float(frequency) * 1000))
        except ValueError:
            continue
        if _is_fm(kilohertz):
            pictures[kilohertz] = url
    return pictures
