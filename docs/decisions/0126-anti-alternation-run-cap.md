# ADR 0126: Cap consecutive alternating trials in the Phase 3 sequence

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/trial_sequence.py`, `host/tmaze/core/phase3/phase3_config.py`, `host/tmaze/core/phase3/phase3_log.py`, `host/tmaze/core/session/session_summary.py`, `host/phase3.json`, `host/tests/test_trial_sequence.py`, `host/tests/test_phase3_config.py`, `host/tests/test_phase3_log.py`
- **Supersedes / Superseded by:** —

## Context

The Phase 3 schedule already discourages a **side** bias: `generateSequence`
(ported to `generate_balanced`) keeps exact per-block L/R balance but re-rolls any
block whose shuffle would create a same-side run longer than `max_same_run`
(ADR-0009). That cap does nothing about the *other* cheap strategy a rat can fall
into - **alternation**. A perfectly balanced, side-run-capped schedule can still
contain long stretches of `L R L R L R ...`, and a rat that simply alternates
every trial (ignoring the cue) will be correct on all of them. Long alternating
runs therefore let an animal score well without learning the cue->side
contingency, the same failure mode `max_same_run` guards against for side bias.

The task is host-owned in Phase 3 (ADR-0046), so the fix belongs in the host
Python generator; the firmware `task_core.h` / `rig_config.h` path is not used
for Phase 3 and is left untouched.

## Decision

Add a companion anti-bias cap **`max_alt_run`** that limits how many consecutive
*alternating* trials a generated block may contain, enforced by the exact same
re-roll mechanism as `max_same_run`:

- New pure validator `alt_run_ok(seq, n, max_alt)` in `trial_sequence.py`, the
  mirror of `max_run_ok` but counting a run of strictly alternating trials
  (`seq[i] != seq[i-1]`) **by trial**. `max_alt == 4` permits `L R L R` but
  rejects a 5th trial that would extend the run (`L R L R L`); `0` disables it.
- `generate_balanced` gains a trailing keyword `max_alt=0` and, in the same
  attempt loop, only accepts a block when **both** `max_run_ok` and `alt_run_ok`
  hold for the prefix (so a run spanning the block boundary is caught too). Added
  last/keyword so existing positional callers (e.g. the probe block's
  `generate_balanced(n, n, 0, rng)`) are unchanged.
- `TrialConfig.max_alt_run: int = 0` (default **disabled**), threaded through the
  choice schedule (`generate_sequence`), both return-arm schedules (`__init__`),
  and `extend` - exactly where `max_same_run` is applied, keeping all three
  schedules consistent. `extend` enforces it within each appended block only, like
  the existing cap (ADR-0059).
- Loaded from `sequence.max_alt_run` in `phase3.json` via `to_trial_config`
  (validated `>= 0`), included in `default_config`, recorded in the session-log
  header (`max_alt_run=...`) and shown on the session summary's Sequence line.
- `host/phase3.json` sets `max_alt_run: 4` (the live opt-in value; tunable).

Counting is **by trial**, matching how `max_same_run` counts (a same-side run of
4 = four identical trials), so the two caps read the same way.

## Alternatives considered

- **Count alternating *pairs* or *flips* instead of trials.** Rejected for
  consistency: `max_same_run` counts trials in a run, so counting alternation the
  same way keeps a single mental model. The value is just off-by-one between the
  framings; documented in the field/validator docstrings.
- **Default the cap on (non-zero) in the dataclass.** Rejected: a non-zero
  default would silently change the generated sequence for every existing seed and
  break byte-identical reproducibility of past configs. Default `0` (disabled)
  keeps old schedules identical; behaviour changes only where a config opts in.
- **Add it to the firmware too (parity with `max_same_run`).** Deferred: Phase 3
  sequence generation is host-owned (ADR-0046); the firmware path is unused here,
  so mirroring it would add C++ surface with no runtime benefit.
- **A softer transition-probability model (e.g. bias toward repeats).** Rejected
  as out of scope, the same reasoning as ADR-0009 deferring the 60/40 model: the
  hard, testable run-length cap is the high-value, low-risk part.

## Consequences

- Setting `max_alt_run` removes long alternating runs from the schedule, so a
  pure-alternation strategy no longer scores well - the intended effect.
- Adding a second constraint raises the per-block re-roll rate, but a balanced
  20-trial block rarely alternates for 5+ in a row, so `MAX_ATTEMPTS = 200` is
  ample; the last attempt is still accepted unconditionally (unchanged behaviour).
- Because the cap is enforced only within each block (not across the block join,
  like `max_same_run`), a single alternating pair can straddle a boundary; this
  matches the existing cap's semantics and is acceptable.
- The value lives in `phase3.json` and is hand-edited there (not the GUI Save
  path), consistent with the other `sequence` params (ADR-0070); it is captured in
  each session's config snapshot and log header for reproducibility.

## Evidence / links

- Host unit tests: `host/tests/test_trial_sequence.py`
  (`TestAltRunOk`, `TestAltRunSequence`, and the `generate_balanced` alt-cap /
  back-compat cases), `test_phase3_config.py` (round-trip + validation),
  `test_phase3_log.py` (`test_header_records_max_alt_run`) - all pass via
  `python -m unittest discover -s tests -t .` from `host/`.
- Builds on ADR-0009 (the `max_same_run` side-bias cap it mirrors), ADR-0046
  (host-owned Phase 3), ADR-0059 (append-only extension), ADR-0070 (`sequence`
  params hand-edited in `phase3.json`).
