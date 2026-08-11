# ADR 0090: Normalize the debias bias signal by directional-error count, gated by a minimum-error threshold

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/debias.py`, `phase3_config.py`,
  `phase3_log.py`, `phase3_stats.py`;
  `host/tmaze/core/session/session_summary.py`;
  `host/tmaze/ui/phase3_controller.py`, `tooltip_text.py`; `host/phase3.json`;
  tests `test_debias.py`, `test_phase3_log.py`, `test_phase3_ribbon.py`,
  `test_phase3_stats.py`
- **Supersedes / Superseded by:** Supersedes the bias-signal normalization and the
  `min_samples` trigger gate of [ADR-0086](0086-phase3-debias-blocks.md) (the rest
  of ADR-0086 stands).

## Context

ADR-0086 defined the closed-loop debias signal as directional errors normalized by
the **trial count**: `b = (left_pull - right_pull) / len(window)`, and gated the
auto-trigger on `min_samples` (a minimum number of scored trials in the window).

On the bench the operator noticed a counter-intuitive artifact: a trial the rat
gets **correct on its preferred side** (a left-cued, chose-left trial for a
left-biased rat) lowered the measured bias `b`, even though it is not evidence
*against* a left bias. The cause is the trial-count denominator: a correct trial
contributes 0 to the numerator but +1 to the denominator, so it dilutes `b` while
the window is filling, and once the window is full it can *evict* an older pull
error, dropping `b` further. The signal we act on therefore under-reports a real
bias whenever the biased side also happens to be cued.

The intent of the metric is "of the mistakes the rat is making, how lopsided are
they?" — a property of the **errors**, not of all trials.

## Decision

Normalize the directional-error bias by the **directional-error count**, and gate
the auto-trigger on a **minimum number of errors** instead of a minimum number of
trials:

- **Bias signal.** `b = (left_pull - right_pull) / (left_pull + right_pull)` in
  `[-1, 1]`, and `b = 0.0` when there are no directional errors. In a two-choice
  task every error is exactly one of {left-pull, right-pull}, so the denominator is
  the total directional-error count. Correct trials no longer dilute `b`; they
  still occupy window slots, so they still age old errors out of the sliding
  window (recency is preserved) — they just don't count toward the ratio.
- **Trigger gate.** Replace `DebiasBlockConfig.min_samples` (trial-count gate) with
  `min_errors` (error-count gate, default `5`, validated `0 < min_errors <=
  window`). A block auto-triggers from NORMAL only once at least `min_errors`
  directional errors have accrued in the window **and** `|b| >= trigger_bias`. This
  is what stops a single early error — which now reads as `|b| = 1.0` under the
  errors-only ratio — from twitchily firing a block.
- **Session-level twin.** `phase3_stats._dir_bias` (the whole-session readout shown
  in the summary/panel) uses the same errors-only denominator, so the reported
  number matches the quantity the controller acts on.
- **Config surface.** `sequence.debias_blocks.min_samples` becomes `min_errors` in
  `phase3.json`, the GUI spinbox ("Min errors:"), the data-file header, the summary
  line, and the tooltip. This is a rename of a brand-new (ADR-0086, same-week) knob
  that has not shipped, so no migration path is provided: an old key is ignored and
  falls back to the default.

## Alternatives considered

- **Keep the trial-count denominator (ADR-0086 as-is).** Rejected: it produces the
  dilution/eviction artifact the operator flagged; a correct trial on the biased
  side is not evidence against the bias but was lowering the trigger signal.
- **Difference of per-cue error rates**
  (`P(left-pull | cued RIGHT) - P(right-pull | cued LEFT)`). More robust to a
  lopsided cue sequence, but needs both cue types sampled before it is defined and
  is harder to explain on the bench. Deferred; the errors-only ratio is simpler and
  directly matched the operator's mental model. Revisit if cue-sequence imbalance
  proves to distort the trigger.
- **Errors-only ratio with the trial-count `min_samples` kept as the only gate.**
  Rejected: under the errors-only ratio a lone error reads as full bias, so a
  trial-count gate no longer protects against a twitchy trigger; the guard has to be
  in error units. (Requiring *both* a trial-count and an error-count gate was
  considered and judged redundant — `min_errors` implies enough trials.)

## Consequences

- **The trigger fires on sustained error lopsidedness, sooner for a strongly biased
  rat.** A rat that pulls its preferred side on essentially every error reaches
  `|b| ~ 1.0` as soon as `min_errors` errors accrue, rather than waiting for a
  trial-count window to fill — which is the desired faster intervention. Correct
  trials on the biased side no longer delay it.
- **Default retune.** `min_errors` default is `5` (vs. the old `min_samples=10`);
  `host/phase3.json` is set to `min_errors=5`, `trigger_bias=0.4`, `window=20`.
  These remain GUI-tunable, so bench experience refines them without a code change.
- **No data-format break.** `proto` stays `7`; only the header token name
  (`min_samples=` -> `min_errors=`) and the `bias` column's meaning (now
  errors-normalized) change. Downstream consumers read stats fields by name and are
  unaffected.
- **Revisit if:** cue-sequence imbalance distorts the errors-only ratio (switch to
  the per-cue-rate variant above), or the `min_errors`/`trigger_bias` defaults prove
  too twitchy or too slow on the bench.

## Evidence / links

- Core: `host/tmaze/core/phase3/debias.py` (`_pull_counts`, `bias`, the trigger
  guard in `decide`), `phase3_stats._dir_bias` (matching session-level measure).
- Config/surfaces: `phase3_config.py` (`min_errors` validate + snapshot),
  `phase3_log.py` (header token), `session_summary.py` (summary line),
  `phase3_controller.py` + `tooltip_text.py` (GUI spinbox + tooltip),
  `host/phase3.json`.
- Tests: `host/tests/test_debias.py` (error-count gate + errors-only bias) and the
  `min_errors` updates in `test_phase3_log.py` / `test_phase3_ribbon.py` /
  `test_phase3_stats.py`.
- Supersedes the bias-signal + `min_samples` gate of
  [ADR-0086](0086-phase3-debias-blocks.md); the state machine, block lifecycle,
  GUI carve-out, logging, and stats partitioning of ADR-0086 are unchanged.
