# ADR 0024: Centralize host-side serial-protocol parsing in protocol.py

- **Status:** Accepted
- **Date:** 2026-07-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/protocol.py` (new), `host/test_protocol.py` (new),
  `host/experiment_control.py`, `host/camera_view.py`, `host/tracker_view.py`,
  `host/rules_view.py`, `host/rules_engine.py`, `host/rig_view.py`,
  `host/maze_gui.py`, `host/lick_tuner.py`, `host/rig_logger.py`
- **Supersedes / Superseded by:** —

## Context

The serial protocol ("<ms>,<TYPE>,<payload...>", see `firmware/PROTOCOL.md`) is
documented in one place, but the host-side *parsing* of it was not: ten host
files each hand-rolled their own `line.split(",")` plus ad-hoc field checks
(`fields[1] == "MODE"`, `len(parts) == 4 and parts[1] == "L"`, an `ANIMAL`
regex in the logger, ...). Each copy encoded slightly different validation, and
a protocol change (e.g. the planned v5 `ANIMAL` command, `docs/future-work.md`
FW-0005) would require finding and correctly updating every copy - the classic
protocol-drift failure mode for a lab codebase that people rotate in and out of.

## Decision

Add `host/protocol.py`, the single host-side parser, and route every host tool
through it. Two layers:

- `parse(line) -> ParsedLine(ms, type, fields) | None` - validates the uniform
  `<ms>,<TYPE>` prefix and splits the payload; for call sites that dispatch on
  several types (e.g. `rig_view.parse_line`).
- Per-type helpers - `lick_edge`, `sensor_edge`, `sol_pulse`, `mode_name`,
  `animal_id`, `reward_info` - each decoding exactly one line type (returning
  `None` otherwise); for call sites that care about one or two types.

All functions are pure and total (never raise on malformed input; return
`None`), pure stdlib (no Tk/numpy) so the headless `rules_engine.py` can import
it too, and covered by the headless `host/test_protocol.py` unittest suite.
Per-v4's append-only field rule, helpers tolerate and ignore trailing fields
they don't need (e.g. `SOL,...,<dur_ms>`); `reward_info` surfaces the trailing
duration as `None` when absent so callers pick the pre-v4 fallback (ADR-0019).

## Alternatives considered

- **Keep the status quo (parse at each call site)** - rejected: ten divergent
  copies of the same knowledge, each a place for a protocol change to be missed.
- **Have the parser dispatch to registered per-type handlers (mini framework)**
  - rejected: more indirection than a lab-experience reader needs; plain
  functions returning tuples keep call sites obvious.
- **Firmware self-describing protocol (version-negotiated schemas)** - rejected
  as far out of proportion; `PROTOCOL.md` + one parser module is enough.

## Consequences

- A protocol change now touches `firmware/PROTOCOL.md`, `protocol.py`, and its
  tests, plus only the consumers that care about the new field.
- Call sites got shorter and behavior slightly *stricter* in corner cases the
  firmware never emits (e.g. a non-numeric `<ms>` or an `S`/`L` line with extra
  fields is now uniformly rejected/ignored instead of per-file behavior).
- `rules_engine.py` (pure core) now imports `protocol` - acceptable because
  protocol.py is itself pure stdlib and headlessly tested.
- New host tools must use `protocol` instead of `line.split(",")`.

## Evidence / links

- `firmware/PROTOCOL.md` - the wire format this module implements.
- `host/test_protocol.py` - the behavioral contract (run
  `python -m unittest test_protocol` from `host/`).
- `docs/future-work.md` FW-0005 - the pending protocol change that motivated
  consolidating now.
