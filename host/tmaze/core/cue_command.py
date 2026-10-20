"""
cue_command.py - which cue STIMULUS a Phase 3 session plays, and the firmware
command + display label for one cue (pure core).

A session plays either the pure tones (HIGH / LOW frequency, the original task)
or sinusoidally amplitude-modulated white noise (AM noise; firmware protocol
v10/v11, docs/decisions/0221), chosen per session and fixed while Phase 3 is
armed (docs/decisions/0222). The trial schedule, the CSV and each rat's
``high_tone_is_left`` keep the abstract HIGH / LOW labels in both cases; in an AM
session HIGH means the FASTER modulation rate and LOW the slower one. Only the
command sent and the label SHOWN to the operator change:

  - tone:     ``CUE_ON,<kind>,<commanded_hz>,<gain_pct>``   shown as HIGH / LOW
  - AM noise: ``CUE_ON,<kind>,<rate_hz>,<gain_pct>,AM``     shown as FAST 32 Hz /
                                                            SLOW 2 Hz

Rates may carry up to three decimals (firmware v11 holds them in milli-Hz), so
the string sent here is the same trimmed form the firmware echoes back.

Pure and stdlib-only. Headless tests: python -m unittest tests.test_cue_command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tmaze.core.phase3.trial_sequence import HIGH, LOW

STIM_TONE = "tone"
STIM_AM_NOISE = "am_noise"
STIMULI = (STIM_TONE, STIM_AM_NOISE)

# Modulation-rate window, mirroring CUE_AM_HZ_MIN / CUE_AM_HZ_MAX in
# firmware/common/rig_config.h (the firmware clamps to the same range).
AM_HZ_MIN = 1.0
AM_HZ_MAX = 128.0
# The firmware keeps the rate in milli-Hz (protocol v11), so at most 3 decimals.
AM_RATE_DECIMALS = 3
# First firmware protocol that accepts a fractional AM rate. Phase 3 refuses to
# arm an AM session against older (or unidentified) firmware. see docs/decisions/0222
MIN_FIRMWARE_PROTO_FOR_AM = 11

FAST_LABEL = "FAST"
SLOW_LABEL = "SLOW"


@dataclass(frozen=True)
class CueStimulus:
    """The session's cue stimulus. ``am_fast_hz`` / ``am_slow_hz`` only matter
    when ``kind`` is AM noise; the fast rate stands in for HIGH."""
    kind: str = STIM_TONE
    am_fast_hz: float = 32.0
    am_slow_hz: float = 2.0

    @property
    def is_am(self) -> bool:
        return self.kind == STIM_AM_NOISE

    def rate_for(self, label: str) -> float:
        """The modulation rate (Hz) that plays for cue identity ``label``."""
        return self.am_fast_hz if label == HIGH else self.am_slow_hz


def _check_rate(name: str, hz: float) -> float:
    if not AM_HZ_MIN <= hz <= AM_HZ_MAX:
        raise ValueError(f"cue.{name} must be in [{AM_HZ_MIN:g}, {AM_HZ_MAX:g}] Hz")
    scaled = hz * 10 ** AM_RATE_DECIMALS
    if abs(scaled - round(scaled)) > 1e-6:
        raise ValueError(f"cue.{name} may have at most {AM_RATE_DECIMALS} decimals")
    return round(hz, AM_RATE_DECIMALS)


def make_stimulus(kind: str, am_fast_hz: float, am_slow_hz: float) -> CueStimulus:
    """Build + validate a `CueStimulus`. Rates are checked even for a tone
    session, so a bad value is caught on Save rather than the day AM is used."""
    if kind not in STIMULI:
        raise ValueError(f"cue.stimulus must be one of {', '.join(STIMULI)}")
    fast = _check_rate("am_fast_hz", float(am_fast_hz))
    slow = _check_rate("am_slow_hz", float(am_slow_hz))
    if fast <= slow:
        raise ValueError("cue.am_fast_hz must be greater than cue.am_slow_hz")
    return CueStimulus(kind=kind, am_fast_hz=fast, am_slow_hz=slow)


def format_rate(hz: float) -> str:
    """A rate as the firmware echoes it: up to 3 decimals, trailing zeros
    trimmed ("32", "2.5", "2.125")."""
    text = f"{hz:.{AM_RATE_DECIMALS}f}".rstrip("0").rstrip(".")
    return text or "0"


def cue_on_command(kind: str, label: str, stimulus: CueStimulus, *,
                   tone_commanded_hz: Optional[int], tone_gain_pct: int,
                   noise_gain_pct: int) -> Optional[str]:
    """The ``CUE_ON`` line for one cue, or None when there is nothing valid to
    play (an unknown label, or a tone with no frequency - the caller then sends
    no cue rather than the firmware's bare-command fallback; docs/decisions/0150).

    ``kind`` is CHOICE / RETURN, ``label`` the trial's HIGH / LOW identity. The
    tone arguments are already resolved through the cue calibration (commanded
    Hz + volume %, ADR-0125/0148); the noise gain is the shared AM level."""
    if label not in (HIGH, LOW):
        return None
    if stimulus.is_am:
        return (f"CUE_ON,{kind},{format_rate(stimulus.rate_for(label))},"
                f"{int(noise_gain_pct)},AM")
    if tone_commanded_hz is None:
        return None
    return f"CUE_ON,{kind},{int(tone_commanded_hz)},{int(tone_gain_pct)}"


def cue_label(label: Optional[str], stimulus: CueStimulus) -> str:
    """What the operator sees for a cue identity: HIGH / LOW in a tone session,
    ``FAST 32 Hz`` / ``SLOW 2 Hz`` in an AM session ('' for no identity)."""
    if label not in (HIGH, LOW):
        return ""
    if not stimulus.is_am:
        return label
    word = FAST_LABEL if label == HIGH else SLOW_LABEL
    return f"{word} {format_rate(stimulus.rate_for(label))} Hz"


def short_cue_label(label: Optional[str], stimulus: CueStimulus) -> str:
    """Compact form for tight cells (the trial ribbon): HIGH / LOW, or FAST / SLOW."""
    if label not in (HIGH, LOW):
        return ""
    if not stimulus.is_am:
        return label
    return FAST_LABEL if label == HIGH else SLOW_LABEL


def letter_cue_label(label: Optional[str], stimulus: CueStimulus) -> str:
    """One letter for the tightest cells: H / L, or F / S ('?' for no identity)."""
    return short_cue_label(label, stimulus)[:1] or "?"


def describe(stimulus: CueStimulus) -> str:
    """One-line description for the tab / summary, e.g. ``AM noise (fast 32 Hz /
    slow 2 Hz)`` or ``tone``."""
    if not stimulus.is_am:
        return "tone"
    return (f"AM noise (fast {format_rate(stimulus.am_fast_hz)} Hz / "
            f"slow {format_rate(stimulus.am_slow_hz)} Hz)")


def arm_block_reason(stimulus: CueStimulus,
                     firmware_proto: Optional[int]) -> Optional[str]:
    """Why Phase 3 must NOT arm with this stimulus, or None when it may.

    An AM session needs firmware protocol >= MIN_FIRMWARE_PROTO_FOR_AM; an
    unidentified firmware (no ``0,PROTO`` handshake seen) is refused too, since a
    pre-v10 board would play a silent tone instead of the noise cue."""
    if not stimulus.is_am:
        return None
    if firmware_proto is None or firmware_proto < MIN_FIRMWARE_PROTO_FOR_AM:
        seen = "unknown" if firmware_proto is None else str(firmware_proto)
        return (f"AM noise needs firmware protocol {MIN_FIRMWARE_PROTO_FOR_AM}+ "
                f"(board reports {seen}): reflash the Due or choose Tone")
    return None
