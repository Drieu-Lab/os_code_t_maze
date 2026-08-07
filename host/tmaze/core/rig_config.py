"""rig_config.py - single host-side reader of the firmware pin map.

The firmware's wiring lives in one place, ``firmware/common/rig_config.h``. The
Python tools used to each hard-code their own copy of the lick/beam/solenoid
mapping (``lick_tuner.py`` labels, ``rig_view.py`` markers, ...), and those
copies drifted out of sync with the header and with each other. This module
parses the header so every host tool labels ports from ONE source of truth.

IMPORTANT - assumption: flashed == source. This reads ``rig_config.h`` on disk,
so the mapping it reports is only correct if the Arduino Due is running that same
header. Reflash the Due after editing the header. (The alternative that removes
this assumption - having the firmware report its own compiled pin map over
serial - was considered and deferred in favour of this simpler design; if the
"edited but not reflashed" footgun ever bites, revisit that.)

The protocol only ever carries the channel *index* (``L,<i>``/``S,<i>``); the
pin number and the port role come from here.

Pure standard library. Every accessor degrades gracefully: if the header cannot
be found or parsed, ``load_rig_config()`` returns a ``RigConfig`` with
``ok == False`` and empty maps, and callers fall back to their own defaults.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from tmaze.paths import repo_root

# <repo>/firmware/common/rig_config.h (the ONE real header; the sketch folders
# only hold symlinks to it - see docs/decisions/0006). Resolved from the repo
# root by landmark search (tmaze.paths) rather than a fixed ".." hop, so it is
# unaffected by this module's depth inside the package (see docs/decisions/0083).
DEFAULT_HEADER_PATH = os.path.normpath(
    os.path.join(str(repo_root()), "firmware", "common", "rig_config.h")
)

# Body-beam roles, in the fixed order SENSOR_PINS is defined in (which must match
# the Beam enum in firmware/cue-guided-task/task_core.h:
#   0 ENTRY, 1 EXIT, 2 LCHOICE, 3 RCHOICE, 4 LRETURN, 5 RRETURN).
BEAM_ROLES = (
    "central_entry", "central_exit",
    "left_choice", "right_choice",
    "left_return", "right_return",
)

# Solenoid ports (rows of SOLENOID_TABLE) and columns.
SOLENOID_PORTS = ("left", "right", "center")

Scalar = Union[int, str]


# ------------------------------ Header parsing ------------------------------
_INT_RE = re.compile(r"^[+-]?\d+$")
_HEX_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
_DEFINE_RE = re.compile(r"^[ \t]*#define[ \t]+(\w+)[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_TOKEN_RE = re.compile(r"[{},]|0[xX][0-9a-fA-F]+|[+-]?\d+|[A-Za-z_]\w*")


def _strip_comments_and_continuations(text: str) -> str:
    """Remove C comments and join backslash-continued lines.

    Order matters: strip block/line comments first, then fold ``\\``-newline
    continuations so multi-line macros (SENSOR_PINS, SOLENOID_TABLE) become one
    logical line.
    """
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)  # block comments
    text = re.sub(r"//[^\n]*", "", text)                      # line comments
    text = re.sub(r"\\[ \t]*\n", " ", text)                   # line continuations
    return text


def _parse_defines(text: str) -> Dict[str, str]:
    """Map every ``#define NAME VALUE`` to its raw (comment-free) VALUE string."""
    return {m.group(1): m.group(2).strip() for m in _DEFINE_RE.finditer(text)}


def _resolve_scalar(token: str, defines: Dict[str, str],
                    _seen: Optional[set] = None) -> Optional[Scalar]:
    """Resolve a token to an int, following ``#define`` -> ``#define`` chains.

    Returns an int for numeric literals / numeric macro chains, the stripped
    string for non-numeric leaves (e.g. a quoted ANIMAL_ID), or None if the
    token is a brace list or an unresolvable/cyclic reference.
    """
    token = token.strip()
    if _INT_RE.match(token):
        return int(token)
    if _HEX_RE.match(token):
        return int(token, 16)
    if token in defines:
        value = defines[token]
        if value.startswith("{"):
            return None  # a list macro, not a scalar
        if _seen is None:
            _seen = set()
        if token in _seen:
            return None  # cycle guard
        _seen.add(token)
        return _resolve_scalar(value, defines, _seen)
    # Non-numeric leaf (string literal, unknown identifier): hand back as-is,
    # stripping surrounding quotes for convenience.
    return token.strip('"')


def _parse_brace_list(value: str, defines: Dict[str, str]) -> list:
    """Parse a (possibly nested) brace initializer into nested lists of ints."""
    tokens = _TOKEN_RE.findall(value)
    pos = 0

    def parse_seq() -> list:
        nonlocal pos
        out: list = []
        if pos >= len(tokens) or tokens[pos] != "{":
            return out
        pos += 1  # consume '{'
        while pos < len(tokens) and tokens[pos] != "}":
            tok = tokens[pos]
            if tok == ",":
                pos += 1
                continue
            if tok == "{":
                out.append(parse_seq())
            else:
                out.append(_resolve_scalar(tok, defines))
                pos += 1
        if pos < len(tokens):
            pos += 1  # consume '}'
        return out

    return parse_seq()


# ------------------------------- Public model -------------------------------
@dataclass
class RigConfig:
    """Resolved firmware pin map. Empty / ``ok == False`` if parsing failed."""

    ok: bool = False
    error: Optional[str] = None
    path: Optional[str] = None

    # Licks: index -> pin (from LICK_PINS); role -> index (from PIN_LICK_* aliases).
    lick_pins: List[int] = field(default_factory=list)
    lick_role_by_index: Dict[int, str] = field(default_factory=dict)
    lick_index_by_role: Dict[str, int] = field(default_factory=dict)

    # Body beams: index -> pin (SENSOR_PINS), with positional roles (BEAM_ROLES).
    beam_pins: List[int] = field(default_factory=list)

    # Solenoids: [port][col] -> pin (SOLENOID_TABLE) + the task column indices.
    solenoid_table: List[List[int]] = field(default_factory=list)
    valve_pins: List[int] = field(default_factory=list)
    reward_sol_index: Optional[int] = None
    drain_sol_index: Optional[int] = None
    rinse_sol_index: Optional[int] = None

    # Handy resolved scalars (REWARD_PULSE_MS, SENSOR_DEBOUNCE_MS, ANIMAL_ID, ...).
    scalars: Dict[str, Scalar] = field(default_factory=dict)

    def lick_role(self, index: int) -> Optional[str]:
        """Role of lick channel ``index`` ('center'/'left'/'right'), or None."""
        return self.lick_role_by_index.get(index)

    def lick_label(self, index: int) -> str:
        """Human label for lick channel ``index``, e.g. 'L2 (pin 35, center)'."""
        parts = [f"L{index}"]
        detail = []
        if 0 <= index < len(self.lick_pins):
            detail.append(f"pin {self.lick_pins[index]}")
        role = self.lick_role(index)
        if role:
            detail.append(role)
        if detail:
            parts.append(f"({', '.join(detail)})")
        return " ".join(parts)

    def beam_label(self, index: int) -> str:
        """Human label for beam channel ``index``, e.g. 'S0 (pin 39, central_entry)'."""
        parts = [f"S{index}"]
        detail = []
        if 0 <= index < len(self.beam_pins):
            detail.append(f"pin {self.beam_pins[index]}")
        if 0 <= index < len(BEAM_ROLES):
            detail.append(BEAM_ROLES[index])
        if detail:
            parts.append(f"({', '.join(detail)})")
        return " ".join(parts)


def load_rig_config(path: Optional[str] = None) -> RigConfig:
    """Parse ``rig_config.h`` (default: the repo's common header).

    Never raises: on any error returns ``RigConfig(ok=False, error=...)`` so GUIs
    can fall back to their own labels.
    """
    header_path = path or DEFAULT_HEADER_PATH
    try:
        with open(header_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return RigConfig(ok=False, error=f"cannot read {header_path}: {exc}",
                         path=header_path)

    try:
        text = _strip_comments_and_continuations(raw)
        defines = _parse_defines(text)

        cfg = RigConfig(ok=True, path=header_path)

        # --- Licks ---
        if "LICK_PINS" in defines:
            pins = [p for p in _parse_brace_list(defines["LICK_PINS"], defines)
                    if isinstance(p, int)]
            cfg.lick_pins = pins
            for role, alias in (("center", "PIN_LICK_CENTER"),
                                ("right", "PIN_LICK_RIGHT"),
                                ("left", "PIN_LICK_LEFT")):
                if alias in defines:
                    pin = _resolve_scalar(alias, defines)
                    if isinstance(pin, int) and pin in pins:
                        idx = pins.index(pin)
                        cfg.lick_index_by_role[role] = idx
                        cfg.lick_role_by_index[idx] = role

        # --- Body beams ---
        if "SENSOR_PINS" in defines:
            cfg.beam_pins = [p for p in _parse_brace_list(defines["SENSOR_PINS"], defines)
                             if isinstance(p, int)]

        # --- Solenoids ---
        if "SOLENOID_TABLE" in defines:
            table = _parse_brace_list(defines["SOLENOID_TABLE"], defines)
            cfg.solenoid_table = [[c for c in row if isinstance(c, int)]
                                  for row in table if isinstance(row, list)]
        if "VALVE_PINS" in defines:
            cfg.valve_pins = [p for p in _parse_brace_list(defines["VALVE_PINS"], defines)
                              if isinstance(p, int)]
        for attr, name in (("reward_sol_index", "REWARD_SOLENOID_INDEX"),
                           ("drain_sol_index", "DRAIN_SOLENOID_INDEX"),
                           ("rinse_sol_index", "RINSE_SOLENOID_INDEX")):
            val = _resolve_scalar(name, defines) if name in defines else None
            if isinstance(val, int):
                setattr(cfg, attr, val)

        # --- Handy scalars ---
        for name in ("REWARD_PULSE_MS", "SENSOR_DEBOUNCE_MS", "TTL_PULSE_MS",
                     "ANIMAL_ID", "PROTO_VERSION", "SOLENOIDS_PER_PORT"):
            if name in defines:
                val = _resolve_scalar(name, defines)
                if val is not None:
                    cfg.scalars[name] = val

        return cfg
    except Exception as exc:  # noqa: BLE001 - never let a parse bug crash a GUI
        return RigConfig(ok=False, error=f"parse error in {header_path}: {exc}",
                         path=header_path)


if __name__ == "__main__":
    # Quick manual check: `python rig_config.py` dumps the parsed mapping.
    cfg = load_rig_config()
    if not cfg.ok:
        print(f"[rig_config] FAILED: {cfg.error}")
        raise SystemExit(1)
    print(f"[rig_config] parsed {cfg.path}")
    print("  Licks:")
    for i in range(len(cfg.lick_pins)):
        print(f"    {cfg.lick_label(i)}")
    print(f"    role->index: {cfg.lick_index_by_role}")
    print("  Beams:")
    for i in range(len(cfg.beam_pins)):
        print(f"    {cfg.beam_label(i)}")
    print(f"  Solenoid table (port x col): {cfg.solenoid_table}")
    print(f"  Valve pins: {cfg.valve_pins}  "
          f"reward/drain/rinse col = "
          f"{cfg.reward_sol_index}/{cfg.drain_sol_index}/{cfg.rinse_sol_index}")
    print(f"  Scalars: {cfg.scalars}")
