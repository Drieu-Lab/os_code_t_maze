# ADR 0084: Attribute operator reward water at its source, not from the wire echo

- **Status:** Accepted
- **Date:** 2026-08-07
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/session_controller.py` (collect operator feeds via `note_operator_reward`, drop the wire-parse), `host/tmaze/ui/experiment_control.py` (Solenoid-test button seam), `host/tmaze/core/session/session_summary.py` (schema v4 -> v5, `manual_water` now operator-only), `host/tests/test_session_summary.py`, `docs/records-schema.md`, `host/tmaze/core/session/session_registry.py` (comment)
- **Supersedes / Superseded by:** Supersedes ADR-0078

## Context

The per-session summary reports reward water in two channels: `estimated_water`
(the automatic TASK reward - prime + top-ups, counted from the Phase 3 stats) and
`manual_water` (added by ADR-0078 to capture rare operator hand-feeds). They are
meant to be disjoint so a reader can reconcile the day's intake as their sum.

They were not disjoint. In host-owned Phase 3 the firmware trial FSM is off
(Free Roam), so the host dispenses *every* task reward itself, sending the bare
`R<port>1,<ms>` command through the shared `RewardDispatcher` (ADR-0054). The
operator's "Solenoid test" reward button sends the *identical* `R<port><sol>,<ms>`
command. The firmware echoes any reward-solenoid fire as
`REWARD,<port>,MANUAL,<dur_ms>` (ADR-0019) regardless of who asked for it, so on
the wire task and operator fires are indistinguishable.

ADR-0078 collected `manual_water` by counting every `REWARD,MANUAL` line off the
pre-parsed serial fan-out. Because task fires carry that same tag, `manual_water`
swept in the task reward too: `manual_water` became a superset of
`estimated_water` (man ⊇ est). The two channels overlapped, so summing them
double-counts and the summary implied a wrong day total.

## Decision

Attribute each dispense **at its source** on the host, instead of parsing the
ambiguous wire echo.

- **Task water** stays `estimated_water`, derived from the Phase 3 stats (the task
  is the only thing driving the `RewardDispatcher`), unchanged.
- **Operator water** is collected where the operator actually fires: the
  Solenoid-test panel. `build_solenoid_panel` gains an `on_reward_fire(ms)`
  callback, invoked only when the fired solenoid is the reward spout
  (`reward_sol_index`). `experiment_control` wires that to the Session panel's new
  `SessionControlView.note_operator_reward(ms)`, which appends the pulse width to
  `self._operator_pulses_ms` while a session is active. The pure
  `session_summary` core totals those widths into `manual_water` exactly as
  before (via the `reward_volume` calibration) - only the *feed* changed.
- The old wire-parse collection in `SessionControlView.apply()` is **removed**.
  It is the buggy path and, being ambiguous, cannot be salvaged as a cross-check
  that means anything.

`manual_water` keeps its key name and its always-definite-zero contract; its
meaning is now precisely "operator GUI-button reward," disjoint from and additive
with `estimated_water`. The summary schema bumps v4 -> v5 to flag the corrected
semantics for offline readers. The `RewardDispatcher` is deliberately left
untouched: task fires already funnel through it and the operator path is already
physically separate, so adding a `source` enum would be speculative generality
(CONTRIBUTING #6) - the source is implicit in which handler fires.

No firmware or wire-protocol change: the fix is entirely host-side, so
`PROTO_VERSION`, `firmware/PROTOCOL.md`, and `host/protocol.py` are unchanged.

## Alternatives considered

- **Make the firmware self-describing (distinct `AUTO` tag for controller-driven
  fires vs `MANUAL` for the bare operator command).** Cleanest on the wire, but a
  protocol change: bump `PROTO_VERSION`, update `firmware/PROTOCOL.md` and
  `host/protocol.py`, and reflash the Due. Rejected as disproportionate when the
  host already knows the source at the moment it fires; kept on record as the
  option to take if a non-GUI operator reward path ever appears.
- **Keep the wire path as a cross-check (log, don't sum).** Rejected: the wire
  cannot attribute the fire at all, so a cross-check would compare the source
  count against a number that means "task + operator," adding noise, not a check.
- **Subtract: operator = wire-manual-total - task-fires.** Rejected: that is
  arithmetic on two overlapping channels - exactly the fragility this ADR removes -
  and it breaks the moment the rate backstop suppresses a fire.
- **Rename `manual_water` -> `operator_water` (and the registry column).**
  Rejected for now to keep the diff small: the registry already reads
  `manual_water` into a separate `manual_reward_ml` column (never summed), and
  "manual reward" reads correctly for an operator hand-feed. Only the collection
  was wrong.

## Consequences

- For a Phase 3 session with no operator feeds, `manual_water` is a true
  `{"ul": 0.0, "pulses": 0}` and `estimated_water` ≈ its old value; the two
  channels are now disjoint, so `estimated_water + manual_water` is a correct day
  reward total. WS4's session registry (`task_reward_ml` + `manual_reward_ml`)
  becomes safely additive with no code change.
- Operator water is only counted when fired through the GUI Solenoid-test button.
  A reward typed as a raw `R21` on some other console would no longer be counted -
  an acceptable trade for correctness, since such a fire was already
  indistinguishable from task reward on the wire.
- Summary JSON schema is v5; a reader keyed to v4 sees the same `manual_water`
  shape but a corrected (smaller, operator-only) value.
- Manual-water volume inherits the same calibration caveats as the task estimate
  (single bench point, linear assumption; `reward_volume`).

## Evidence / links

- Supersedes ADR-0078 (`manual_water`, then wire-collected).
- Builds on: ADR-0019 (firmware logs each pulse's duration; host computes volume),
  ADR-0054 (shared `RewardDispatcher` as the single task fire choke point),
  ADR-0056 (pre-parsed `LineInfo` at the fan-out), ADR-0073 (`estimated_water`,
  task-only), ADR-0007 (reward at the central port).
- Tests: `host/tests/test_session_summary.py` (`TestManualWater` - zero case,
  totalling operator pulses at their widths, independence from the task estimate,
  and explicit task+operator additivity). Full suite:
  `python -m unittest discover -s tests -t .` from `host/`.
