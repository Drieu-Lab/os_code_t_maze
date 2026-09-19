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
  - leading_ms(line) -> int | None
        Just the "<ms>," prefix, for a caller that wants the firmware clock
        without a full split.
  - Per-type helpers (lick_edge, sensor_edge, sol_pulse, mode_name, animal_id,
        reward_info, cue_info)
        Each returns the decoded payload for exactly one line type, or None if
        the line is not (a well-formed instance of) that type. Use these when a
        call site cares about one or two specific types.

A call site that fans one line out to many consumers can instead parse ONCE via
classify(line) -> LineInfo, which runs parse() a single time and pre-computes
every typed field (plus the raw text), so downstream modules read fields instead
of re-parsing (see docs/decisions/0056, docs/future-work.md FW-0021). Each
per-type helper is a thin wrapper over a "*_from(ParsedLine)" core so classify
can reuse the same decoders without re-splitting the line.

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


def leading_ms(line: str) -> Optional[int]:
    """The firmware clock from a line's "<ms>," prefix, or None.

    That clock is the firmware's session time (``millis() - session.startMs``),
    not the board's uptime: it starts when the host attaches and restarts on a
    MODE switch.

    A cheaper cousin of parse() for the one caller that wants nothing but the
    timestamp: the broker relays lines verbatim and must not pay for a full
    split on every line just to remember when the Due last spoke.
    see docs/decisions/0064

    isascii() guards the isdigit() fast path, which is also true of unicode
    digits that int() then rejects - and this module never raises.
    """
    head, sep, _ = line.partition(",")
    if not sep or not head.isascii() or not head.isdigit():
        return None
    return int(head)


# ------------------------------ edge lines (S / L) ------------------------------

def _edge_from(p: Optional[ParsedLine], wanted_type: str) -> Optional[Tuple[int, bool]]:
    """Decode a pre-parsed "<ms>,<S|L>,<i>,<state>" -> (index, active), state in {0,1}."""
    if p is None or p.type != wanted_type or len(p.fields) != 2:
        return None
    idx_s, state_s = p.fields[0].strip(), p.fields[1].strip()
    if not idx_s.isdigit() or state_s not in ("0", "1"):
        return None
    return int(idx_s), state_s == "1"


def lick_edge(line: str) -> Optional[Tuple[int, bool]]:
    """(index, active) for a lick edge "<ms>,L,<i>,<state>", else None."""
    return _edge_from(parse(line), "L")


def sensor_edge(line: str) -> Optional[Tuple[int, bool]]:
    """(index, active) for a body-beam edge "<ms>,S,<i>,<state>", else None."""
    return _edge_from(parse(line), "S")


# --------------------------------- SOL lines ---------------------------------

def sol_pulse_from(p: Optional[ParsedLine]) -> Optional[Tuple[int, int]]:
    """(port, sol) for a pre-parsed solenoid ack, else None (see sol_pulse)."""
    if p is None or p.type != "SOL" or len(p.fields) < 2:
        return None
    port_s, sol_s = p.fields[0].strip(), p.fields[1].strip()
    if not port_s.isdigit() or not sol_s.isdigit():
        return None
    return int(port_s), int(sol_s)


def sol_pulse(line: str) -> Optional[Tuple[int, int]]:
    """(port, sol) for a solenoid ack "<ms>,SOL,<port>,<sol>[,<dur_ms>]".

    The trailing <dur_ms> (protocol v4, cue-guided-task only) is tolerated and
    ignored, per the append-only field rule in PROTOCOL.md.
    """
    return sol_pulse_from(parse(line))


# ------------------------------ handshake lines ------------------------------

def mode_name_from(p: Optional[ParsedLine]) -> Optional[str]:
    """The mode token of a pre-parsed "<ms>,MODE,<name>" line, else None."""
    if p is None or p.type != "MODE" or not p.fields:
        return None
    name = p.fields[0].strip()
    return name or None


def mode_name(line: str) -> Optional[str]:
    """The mode token of a "<ms>,MODE,<name>" line (stripped), else None."""
    return mode_name_from(parse(line))


def animal_id_from(p: Optional[ParsedLine]) -> Optional[str]:
    """The id of a pre-parsed "<ms>,ANIMAL,<id>" handshake line, else None."""
    if p is None or p.type != "ANIMAL" or not p.fields:
        return None
    aid = p.fields[0].strip()
    return aid or None


def animal_id(line: str) -> Optional[str]:
    """The id of a "<ms>,ANIMAL,<id>" handshake line (stripped), else None."""
    return animal_id_from(parse(line))


def proto_version_from(p: Optional[ParsedLine]) -> Optional[int]:
    """The integer version of a pre-parsed "<ms>,PROTO,<n>" handshake, else None."""
    if p is None or p.type != "PROTO" or not p.fields:
        return None
    n = p.fields[0].strip()
    return int(n) if n.isdigit() else None


def proto_version(line: str) -> Optional[int]:
    """The firmware serial protocol version from a "<ms>,PROTO,<n>" line, else None.

    Emitted once at firmware startup (``0,PROTO,<n>``; see firmware/PROTOCOL.md).
    The host records it as the session's firmware provenance - in particular it
    fixes the S/L state polarity convention (proto >= 8 corrected, else the
    legacy inverted meaning; see docs/decisions/0164)."""
    return proto_version_from(parse(line))


def is_boot_handshake(line: str) -> bool:
    """Whether ``line`` is the firmware's startup ``PROTO`` handshake.

    ``0,PROTO,<n>`` is printed once by ``setup()`` at every boot and NOWHERE else,
    so seeing it again mid-session is the unambiguous signal that the Due rebooted
    (power glitch, watchdog, a stray reset) - a firmware reset (review finding S8,
    docs/decisions/0184). A MODE switch also restarts the firmware SESSION clock to
    ~0 but does NOT re-emit ``PROTO``, so keying the reset detector on ``PROTO``
    avoids a false positive on an operator mode change; the caller decides it is a
    RESET (rather than the initial handshake) by tracking that a session was already
    running. Pure/total, like the rest of this module."""
    p = parse(line)
    return p is not None and p.type == "PROTO"


def sanitize_animal_id(name: str) -> str:
    """Filename-safe slug of an animal id: keep alphanumerics plus '-'/'_',
    drop everything else, and fall back to "unknown" if nothing remains. The
    single host-side helper shared by rig_logger, the Session panel, and the
    Task tab so an id maps to the same slug everywhere (see docs/decisions/0053,
    docs/future-work.md FW-0005)."""
    slug = "".join(ch for ch in name.strip() if ch.isalnum() or ch in "-_")
    return slug or "unknown"


# -------------------------------- REWARD lines --------------------------------

def reward_info_from(p: Optional[ParsedLine]) -> Optional[Tuple[bool, Optional[int]]]:
    """(manual, dur_ms) for a pre-parsed "<ms>,REWARD,..." line, else None
    (see reward_info)."""
    if p is None or p.type != "REWARD" or not p.fields:
        return None
    manual = any(f.strip() == "MANUAL" for f in p.fields)
    last = p.fields[-1].strip()
    dur = int(last) if last.lstrip("+-").isdigit() else None
    return manual, dur


def reward_info(line: str) -> Optional[Tuple[bool, Optional[int]]]:
    """(manual, dur_ms) for a "<ms>,REWARD,..." line, else None.

    manual is True for "REWARD,<port>,MANUAL,<dur_ms>" (experimenter fire) and
    False for the automatic "REWARD,CENTER,<dur_ms>". dur_ms is None when the
    trailing duration field is absent (pre-v4 firmware / old logs); the caller
    picks the fallback pulse width. see docs/decisions/0019
    """
    return reward_info_from(parse(line))


# --------------------------------- CUE lines ---------------------------------

def cue_info_from(p: Optional[ParsedLine]) -> Optional[Tuple[str, Optional[str], Optional[int]]]:
    """(state, kind, freq_hz) for a pre-parsed "<ms>,CUE,..." line, else None
    (see cue_info)."""
    if p is None or p.type != "CUE" or not p.fields:
        return None
    state = p.fields[0].strip().upper()
    if state == "OFF":
        return ("OFF", None, None)
    if state == "ON":
        if len(p.fields) < 3:
            return None
        kind = p.fields[1].strip().upper()
        freq_s = p.fields[2].strip()
        if kind not in ("CHOICE", "RETURN") or not freq_s.lstrip("+-").isdigit():
            return None
        return ("ON", kind, int(freq_s))
    return None


def cue_info(line: str) -> Optional[Tuple[str, Optional[str], Optional[int]]]:
    """(state, kind, freq_hz) for a "<ms>,CUE,..." line (protocol v5), else None.

    The firmware emits this for the host-driven positional cue, reflecting the
    ACTUAL acoustic state (see docs/decisions/0044):

      - "<ms>,CUE,ON,<CHOICE|RETURN>,<freq_hz>" -> ("ON", "CHOICE"|"RETURN", freq)
      - "<ms>,CUE,OFF"                          -> ("OFF", None, None)

    The TONE_ON/TONE_OFF TTLs (not serial) carry the hardware-synchronous onset
    mark; this line is the behavioral-record companion (which cue, what tone).
    """
    return cue_info_from(parse(line))


# ----------------------------- single-parse fan-out -----------------------------

@dataclass(frozen=True)
class LineInfo:
    """One serial line parsed ONCE into every field a consumer might want.

    Built by classify(); handed to modules at the GUI fan-out point so each
    reads pre-decoded fields instead of re-running the parsers on the raw string
    (see docs/decisions/0056, docs/future-work.md FW-0021). ``raw`` is the
    original line (kept so modules that re-parse on another thread - e.g. the
    Rules/Phase 3 actuation threads - can still pass it along), ``parsed`` is the
    generic split (or None if malformed), and the remaining fields mirror the
    per-type helpers (each None when the line is not that type).
    """
    raw: str
    parsed: Optional[ParsedLine]
    mode: Optional[str]
    lick: Optional[Tuple[int, bool]]
    sensor: Optional[Tuple[int, bool]]
    sol: Optional[Tuple[int, int]]
    reward: Optional[Tuple[bool, Optional[int]]]
    animal: Optional[str]
    cue: Optional[Tuple[str, Optional[str], Optional[int]]]
    # Host-side receipt stamps (docs/decisions/0175), attached at the GUI fan-out
    # point via dataclasses.replace when the line arrived through the stamped
    # reader callback (RigClient.on_line_stamped). ``recv_wall`` is time.time()
    # and ``recv_mono`` is time.monotonic() captured on the reader thread the
    # instant the bytes arrived - before the Tk hop. Both stay None when the line
    # did not carry a stamp (e.g. re-classified on another thread), so consumers
    # must fall back (the serial capture uses datetime.now() then). classify()
    # never sets them; only the fan-out does.
    recv_wall: Optional[float] = None
    recv_mono: Optional[float] = None


def classify(line: str) -> LineInfo:
    """Parse ``line`` ONCE and pre-compute every typed field (see LineInfo).

    Never raises: a malformed line yields a LineInfo whose ``parsed`` and all
    typed fields are None but whose ``raw`` is preserved.
    """
    p = parse(line)
    return LineInfo(
        raw=line,
        parsed=p,
        mode=mode_name_from(p),
        lick=_edge_from(p, "L"),
        sensor=_edge_from(p, "S"),
        sol=sol_pulse_from(p),
        reward=reward_info_from(p),
        animal=animal_id_from(p),
        cue=cue_info_from(p),
    )
