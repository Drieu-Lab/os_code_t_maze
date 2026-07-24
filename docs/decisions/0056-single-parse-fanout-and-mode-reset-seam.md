# ADR 0056: Parse each serial line once and route MODE switches through one reset seam

- **Status:** Accepted
- **Date:** 2026-07-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/protocol.py` (new `LineInfo` + `classify`, helpers split into
  `*_from(ParsedLine)` cores), `host/line_router.py` (new), `host/experiment_control.py`
  (`on_line`), and every registered module's `apply()`
  (`host/rig_view.py`, `host/camera_view.py`, `host/tracker_view.py`,
  `host/maze_state_view.py`, `host/task_view.py`, `host/rules_view.py`,
  `host/phase3_controller.py`, `host/behavior_view.py`, `host/session_controller.py`,
  `host/session_serial.py`); tests `host/test_protocol.py`,
  `host/test_line_router.py`, `host/test_session_serial.py`
- **Supersedes / Superseded by:** — (builds on
  [ADR-0024](0024-shared-host-protocol-parser.md))

## Context

`experiment_control.py`'s `on_line` is the single point where each firmware
serial line fans out to every GUI tab/module. Two smells had grown there
(captured as `docs/future-work.md` FW-0021 and FW-0022):

- **Redundant re-parsing.** `on_line` parsed each line for the bottom-strip
  tallies (`protocol.mode_name` / `lick_edge` / `reward_info`) and then handed
  the **raw string** to every module, each of which re-parsed it with the same
  `protocol` helpers (e.g. `phase3_controller` re-ran `mode_name`, `cue_info`,
  `lick_edge`, and `parse`). On a busy line rate that is N+1 parses of the same
  string, and the mode/lick/reward classification lived in two layers that had
  to stay in agreement.
- **Scattered MODE-switch resets.** A firmware `0,MODE,<name>` line means "new
  run, drop stale state," but each consumer noticed this on its own: `on_line`
  reset the reward/lick tallies while `behavior_view.apply` reset its stats on
  the same line. There was no single "mode boundary" signal, so adding a new
  stateful module meant remembering to wire in its own reset.

Both live at the same fan-out point, so they are fixed together.

## Decision

Parse each line **once** at the fan-out point and route both the parsed data and
the mode-boundary signal through one seam.

- **`protocol.classify(line) -> LineInfo`** (new). Calls `parse(line)` a single
  time and pre-computes every typed field (`mode`, `lick`, `sensor`, `sol`,
  `reward`, `animal`, `cue`) plus the raw text and the generic `ParsedLine`.
  `LineInfo` is a frozen dataclass. To avoid re-splitting, each per-type helper
  was refactored into a `*_from(ParsedLine)` core, and the existing public
  `line`-taking helpers (`lick_edge`, `mode_name`, ...) are now thin wrappers
  over those cores, so their ADR-0024 signatures and behaviour are unchanged for
  the standalone tools that still hold a raw string (`rig_logger`, `lick_tuner`,
  `maze_gui`, and the `rules_engine` actuation core).
- **`host/line_router.py` `LineRouter`** (new, pure stdlib). `dispatch(info)`
  fires a single **mode-changed** notification (with the mode name) to every
  subscriber when `info.mode is not None`, then hands the `LineInfo` to each
  module's `apply(info)`. A module opts into the reset seam by defining
  `on_mode_changed(mode)` (auto-subscribed when added, duck-typed like the
  optional `close()` hook); non-module subscribers (the reward/lick tallies, the
  mode label) register via `subscribe_mode()`.
- **`on_line`** now classifies once, adds to the lick/reward tallies from the
  pre-parsed fields, and calls `router.dispatch(info)`. The tally **resets** and
  the mode label move onto the router's mode seam, and `behavior_view` resets via
  `on_mode_changed` instead of sniffing the line in `apply`.

Every module's `apply(str)` became `apply(LineInfo)`, reading the pre-decoded
fields. The two actuation-thread consumers (Rules, Phase 3) still enqueue the
**raw** line (`info.raw`) so their off-thread `engine.event(line)` path is
unchanged.

## Alternatives considered

- **Keep `on_line` as-is (raw string to each module).** Rejected: this is the
  N+1-parse + split-reset status quo the two backlog items describe.
- **Reuse the existing `protocol.ParsedLine` as the fan-out object.** Rejected:
  `ParsedLine` is the generic `ms/type/fields` split; overloading it with the
  typed fields would blur the two layers. A separate `LineInfo` keeps the
  generic split and the typed decode distinct.
- **Inline the router logic in `on_line`.** Rejected: the acceptance test ("a
  new stateful module gets the reset without editing `on_line`") needs the
  dispatch/reset seam to be a headless, importable unit. A small pure
  `LineRouter` (stdlib only, no Tk) is testable and matches the repo's
  pure-core/thin-shell convention.
- **Blanket (no-argument) mode signal.** The mode seam passes the mode **name**
  instead. It costs nothing, current subscribers ignore it, and it leaves room
  for a future module that only resets on specific transitions (the FW-0022 open
  question).

## Consequences

- One parse per line on the Tk fan-out thread; the mode/lick/reward
  classification lives in exactly one place (`protocol.classify`), and modules
  read fields instead of re-deriving them.
- One reset seam: adding a stateful module means implementing
  `on_mode_changed(mode)` — no `on_line` edit, no per-module `if mode: reset()`.
- `apply()` now takes a `LineInfo`, not a `str`; any future module must accept
  the pre-parsed object (and can still reach the raw text via `info.raw`).
- No behaviour change: the tallies, the behaviour readout, and every module react
  exactly as before; the full `host/` unittest suite passes.

## Evidence / links

- `host/test_line_router.py` — the acceptance test (a new stateful module gets
  the reset purely by defining `on_mode_changed`) plus mode-seam ordering.
- `host/test_protocol.py` `TestClassify` — `classify` field-parity with the
  individual helpers, malformed-line handling, frozen.
- `host/test_session_serial.py` `TestInfoAndLineParity` — the pre-parsed and
  raw-line predicates agree.
- `docs/future-work.md` FW-0021 / FW-0022 — the backlog items implemented here.
- Builds on [ADR-0024](0024-shared-host-protocol-parser.md) (the single parser).
