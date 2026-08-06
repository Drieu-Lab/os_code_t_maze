# ADR 0078: Total in-task manual reward water as a separate channel in the session summary

- **Status:** Accepted
- **Date:** 2026-08-06
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_summary.py` (new `manual_water` figure, schema v3 -> v4), `host/session_controller.py` (collects manual pulse widths while a session is active), `host/test_session_summary.py`
- **Supersedes / Superseded by:** —

## Context

Reward water reaches the animal through more than one channel, but the session
summary only accounted for one. `estimated_water` (ADR-0073) totals the *task*
reward - the automatic prime + lick top-up pulses the Phase 3 stats core counts -
and by design excludes anything else (its text even reads "excl. manual"). But
the operator does, rarely, fire a **manual** reward during a session (a hand
dispense to re-motivate or guide the animal). The firmware already echoes those
on the wire as `REWARD,<port>,MANUAL,<dur_ms>` (ADR-0019); nothing totalled them,
so that water was invisible in the per-session record and could not be reconciled
against the day's husbandry water figure.

The coordinated-records upgrade wants each bundle's water accounting to be
complete and self-describing, without conflating the two channels (they mean
different things: task-earned vs operator-given).

## Decision

Add a **second, separate** water figure to the summary - `manual_water` - a
sibling to `estimated_water`, never merged into it.

- The thin shell (`session_controller`) collects the manual pulses. Its existing
  `apply(info)` fan-out already receives the pre-parsed `protocol.LineInfo`
  (ADR-0056), whose `.reward` field is `(manual, dur_ms)`. While a session is
  active, each `manual` pulse's width is appended to `self._manual_pulses_ms`
  (reset at Start). A pulse with no trailing duration (pre-v4 firmware) falls back
  to `reward_volume.DEFAULT_REWARD_PULSE_MS`, mirroring the task estimate's
  fallback so the two channels stay comparable. This runs on the Tk thread (the
  same sole-writer thread that filters the serial file), so no locking is added.
- The pure core (`session_summary`) does the calibration. `build_summary` takes a
  new `manual_pulses_ms` list; the `manual_water` property sums each width through
  the shared `reward_volume.reward_volume_ul` bench calibration (ADR-0019) and
  returns `{ul, pulses}`. Keeping the volume math in the pure core keeps it
  headlessly testable, exactly like `estimated_water`.

`manual_water` is **always a definite figure** - `{"ul": 0.0, "pulses": 0}` when
no manual dispense occurred - unlike `estimated_water`, which is `None` when it
cannot be computed. A definite zero lets an offline reader reconcile the day's
intake (task + manual + supplemental) without special-casing "no manual" as
missing data. The human text block shows a `Water (man):` line only when at least
one manual pulse fired (no clutter on the common zero case); the JSON always
carries the `manual_water` object.

Summary schema bumps v3 -> v4 (a new top-level `manual_water` key). WS4's session
registry will read this key directly rather than re-deriving it.

## Alternatives considered

- **Fold manual pulses into `estimated_water`.** Rejected: the two channels are
  semantically different (task-earned reward vs operator intervention) and mixing
  them would corrupt the behavioural reward-rate reading and hide interventions.
  ADR-0073 deliberately excluded manual; this keeps that boundary and just adds
  the missing channel next to it.
- **Sum the volume in the controller and pass a scalar.** Rejected: that puts the
  calibration lookup in the Tk shell, off the headless test path. Passing raw
  pulse widths keeps the shell a thin collector and the volume math in the tested
  pure core.
- **Parse the manual pulses back out of the filtered serial file at Stop.**
  Rejected: the live `apply` fan-out already has them pre-parsed; re-reading the
  file would re-implement parsing and depend on the file having been written.
- **Return `None` when there were no manual pulses (mirror `estimated_water`).**
  Rejected: manual water is always known (the session either saw pulses or it did
  not), so a definite `0` is more useful for reconciliation than "unknown".

## Consequences

- Every summary now carries complete reward-water accounting: task estimate and
  manual dispenses side by side, each labelled and never conflated.
- The day's intake can be reconciled downstream (WS4 registry / husbandry join)
  by reading `estimated_water` + `manual_water` from the bundle.
- Summary JSON schema is v4; an offline reader keyed to v3 gains one new optional
  object and is otherwise unaffected.
- Manual-water volume inherits the same calibration caveats as the task estimate
  (single bench point, linear assumption; `reward_volume`).

## Evidence / links

- Coordinated-records upgrade plan (WS5).
- Builds on: ADR-0019 (firmware logs each pulse's duration; host computes volume),
  ADR-0073 (`estimated_water`, task-only), ADR-0056 (pre-parsed `LineInfo` at the
  fan-out).
- Tests: `host/test_session_summary.py` (`TestManualWater`) - zero-case definite
  `0`, totalling two pulses at their own widths, and independence from the task
  estimate. Full suite: `python -m unittest test_session_summary`.
