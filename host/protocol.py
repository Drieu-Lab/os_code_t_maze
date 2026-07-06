"""
protocol.py - the ONE host-side parser for the rig's serial protocol.

Every line the firmware emits is "<ms>,<TYPE>,<payload...>" (see
firmware/PROTOCOL.md for the full per-type field layouts). Before this module
existed, each host tool hand-rolled its own line.split(",") checks, so protocol
knowledge was duplicated in ~10 places and could drift per file. All host tools
now parse through here, so a protocol change is a change in ONE place (plus the
consumers that care about the new field). See docs/decisions/0024.

Two layers:

  - parse(line) -> ParsedLine | None
        The generic "<ms>,<TYPE>,fields..." split, validating the uniform
        prefix. Use this when dispatching on several types.
  - Per-type helpers (lick_edge, sensor_edge, sol_pulse, mode_name, animal_id,
        reward_info)
        Each returns the decoded payload for exactly one line type, or None if
        the line is not (a well-formed instance of) that type. Use these when a
        call site cares about one or two specific types.

All functions are pure and total: they never raise on malformed input, they
just return None. Pure stdlib (no Tk/numpy), so headless modules such as
rules_engine.py may import this too.

Headless tests: python -m unittest test_protocol   (from host/)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ParsedLine:
    """A validated "<ms>,<TYPE>,<payload...>" line.

    ``fields`` holds the payload fields AFTER the TYPE token (possibly empty),
    unstripped, exactly as split on commas.
    """
    ms: int
    type: str
    fields: Tuple[str, ...]


def parse(line: str) -> Optional[ParsedLine]:
    """Split a firmware line into (ms, TYPE, payload fields), or None.

    Validates only the uniform prefix (integer ms + non-empty TYPE); per-type
    payload validation lives in the helpers below / at the call site.
    """
    parts = line.split(",")
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    typ = parts[1].strip()
    if not typ:
        return None
    return ParsedLine(ms=int(parts[0]), type=typ, fields=tuple(parts[2:]))


# ------------------------------ edge lines (S / L) ------------------------------

def _edge(line: str, wanted_type: str) -> Optional[Tuple[int, bool]]:
    """Decode "<ms>,<S|L>,<i>,<state>" -> (index, active) with state in {0, 1}."""
    p = parse(line)
    if p is None or p.type != wanted_type or len(p.fields) != 2:
        return None
    idx_s, state_s = p.fields[0].strip(), p.fields[1].strip()
    if not idx_s.isdigit() or state_s not in ("0", "1"):
        return None
    return int(idx_s), state_s == "1"


def lick_edge(line: str) -> Optional[Tuple[int, bool]]:
    """(index, active) for a lick edge "<ms>,L,<i>,<state>", else None."""
    return _edge(line, "L")


def sensor_edge(line: str) -> Optional[Tuple[int, bool]]:
    """(index, active) for a body-beam edge "<ms>,S,<i>,<state>", else None."""
    return _edge(line, "S")


# --------------------------------- SOL lines ---------------------------------

def sol_pulse(line: str) -> Optional[Tuple[int, int]]:
    """(port, sol) for a solenoid ack "<ms>,SOL,<port>,<sol>[,<dur_ms>]".

    The trailing <dur_ms> (protocol v4, cue-guided-task only) is tolerated and
    ignored, per the append-only field rule in PROTOCOL.md.
    """
    p = parse(line)
    if p is None or p.type != "SOL" or len(p.fields) < 2:
        return None
    port_s, sol_s = p.fields[0].strip(), p.fields[1].strip()
    if not port_s.isdigit() or not sol_s.isdigit():
        return None
    return int(port_s), int(sol_s)


# ------------------------------ handshake lines ------------------------------

def mode_name(line: str) -> Optional[str]:
    """The mode token of a "<ms>,MODE,<name>" line (stripped), else None."""
    p = parse(line)
    if p is None or p.type != "MODE" or not p.fields:
        return None
    name = p.fields[0].strip()
    return name or None


def animal_id(line: str) -> Optional[str]:
    """The id of a "<ms>,ANIMAL,<id>" handshake line (stripped), else None."""
    p = parse(line)
    if p is None or p.type != "ANIMAL" or not p.fields:
        return None
    aid = p.fields[0].strip()
    return aid or None


# -------------------------------- REWARD lines --------------------------------

def reward_info(line: str) -> Optional[Tuple[bool, Optional[int]]]:
    """(manual, dur_ms) for a "<ms>,REWARD,..." line, else None.

    manual is True for "REWARD,<port>,MANUAL,<dur_ms>" (experimenter fire) and
    False for the automatic "REWARD,CENTER,<dur_ms>". dur_ms is None when the
    trailing duration field is absent (pre-v4 firmware / old logs); the caller
    picks the fallback pulse width. see docs/decisions/0019
    """
    p = parse(line)
    if p is None or p.type != "REWARD" or not p.fields:
        return None
    manual = any(f.strip() == "MANUAL" for f in p.fields)
    last = p.fields[-1].strip()
    dur = int(last) if last.lstrip("+-").isdigit() else None
    return manual, dur
