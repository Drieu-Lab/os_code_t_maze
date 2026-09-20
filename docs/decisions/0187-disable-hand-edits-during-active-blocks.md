# ADR 0187: Disable hand Repeat/Back while an intervention or probe block is running

- **Status:** Accepted
- **Date:** 2026-09-20
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/ui/trial_control_bar.py` (enable rule),
  `host/tmaze/ui/tooltip_text.py`, `host/tests/test_trial_control_bar.py`;
  narrows [FW-0044](../future-work.md#fw-0044-closed-loop-scheduler-rollback-on-a-manual-scored-re-run)
- **Supersedes / Superseded by:** —

## Context

A *closed-loop* Phase 3 run (the debias / de-alternation / probe schedulers)
feeds each trial's outcome to its controller on the tick it is scored
(`TrialSequence.record_outcome` -> `controller.record(...)`). The operator's hand
**Repeat**/**Back** buttons (ADR-0119) preserve the animal's real attempt
append-only and open a fresh row for the re-run - but in a closed-loop run that
re-run feeds the scheduler a **second** outcome for the same trial index, so its
rolling windows and block counters double-count the edited trial. This is
[FW-0044](../future-work.md): the trial *log* supports superseding, but the
*scheduler* has no single-step undo, so the schedule is not reconciled. Until now
the only mitigation was a note ("auto-schedule may be affected") emitted from
`_supersede_trial` when a scored trial is re-run under a closed-loop generator.

Two facts shape the fix. First, `debias.py`'s `record()` "updates the bias window
**always**; inside a block also updates the consecutive-correct counter" - so a
duplicate has two very different consequences: in `NORMAL` it perturbs the
bias/alternation window by ~1/window (it can nudge *when* the next block fires),
but **inside an active block** it also corrupts the 3-correct exit counter, which
changes the block's **length** - a sensitive change to the experimental
manipulation the animal actually receives. Second, `intervention_blocks` is the
default generator, so "a closed-loop scheduler is selected" is true in nearly
every real session, but a block is *active* only for a minority of trials.

Operationally, the tracker is reliable and mis-scored trials are not observed in
practice, and the ideal is that the animal experiences no manual interventions
during learning - so removing the edit only when it would do the sensitive damage
(during an active block) costs the operator almost nothing.

## Decision

Prevent the sensitive case instead of building a rollback. In
`trial_control_bar._refresh`, **Repeat and Back are disabled whenever a block is
actually running** - an intervention block (`debias_state == "INTERVENTION"`,
covering side-bias and de-alternation) **or** a probe block
(`probe_state == "ACTIVE"`, orthogonal to the generator). In `NORMAL` (including
under a closed-loop generator with no block running) the edit stays **allowed**,
and any small bias-window drift is surfaced by the existing ADR-0119 note in
`_supersede_trial` (kept as the soft backstop; it also covers the ~150 ms
snapshot-poll race when a probe block starts). Skip / +Trials / Restart and the
block-override toggles stay live, so the operator still steers an active block.
`undo_outcome` is **not** built. Tooltips explain the disable; the balanced
generator is unaffected.

## Alternatives considered

- **Build the FW-0044 scheduler rollback (`undo_outcome` / replay)** — rejected
  for now: writing a correct inverse for each controller's window / block-length
  counter / forced-block + probe interactions is exactly the fragile, hard-to-
  review code the repo's comprehension-debt rules warn against, for a case that is
  rare and (post-guard) no longer corrupts block length. Kept as FW-0044 for a
  future revisit of whether hand edits should exist at all.
- **Disable edits whenever ANY closed-loop generator is selected (not just an
  active block)** — rejected: since `intervention_blocks` is the default, this
  removes hand Repeat/Back from ~every real session, including all the `NORMAL`
  time when the only cost of an edit is a bounded, already-warned window nudge.
  Too heavy for the marginal fidelity gain.
- **Hard-refuse on the actuation thread instead of a UI disable** — unnecessary
  as the sole mechanism: the `repeat`/`back` callbacks are wired ONLY into this
  bar (`experiment_control.py`), so disabling the buttons is the single entry
  point; the generator is fixed per session (no race) and the probe-start race is
  covered by the retained ADR-0119 note.
- **Grey-out with no escape / refuse-with-message** — the block-active scope makes
  an escape hatch moot: to edit, the operator simply ends the active block (an
  existing override), then Repeat/Back re-enable.
- **Do nothing but document the warn-and-allow as acceptable** — the block-length
  corruption is the one consequence worth preventing outright; a cheap disable is
  worth more than an ADR here.

## Consequences

- A re-scored trial can no longer change a running block's length; the
  experimental manipulation the animal receives during a block is faithful to its
  real choices. The `NORMAL`-state edit stays available (with the drift note), so
  the operator keeps the ability to correct data the great majority of the time.
- To edit during an active block, end the block first (the override re-enables the
  buttons). Balanced-generator sessions are unchanged.
- Whether hand edits should exist at all - and their implications for the research
  - is deferred to a future review (see FW-0044); this ADR narrows, not closes, it.
- Display-only + one test; no `format_version` / `schema_version` / proto change.

## Evidence / links

- `host/tmaze/ui/trial_control_bar.py` `_refresh` (enable rule + `editing_locked`),
  `host/tmaze/ui/tooltip_text.py` (`phase3.repeat` / `phase3.back`),
  `host/tests/test_trial_control_bar.py`.
- `host/tmaze/core/phase3/debias.py` `record()` ("updates the bias window always;
  inside a block also updates the consecutive-correct counter").
- Builds on ADR-0119 (append-only trial edits + the drift note), ADR-0058 (the
  trial-control bar), ADR-0086 / ADR-0107 (debias / alternation blocks),
  ADR-0134 / ADR-0137 (probe blocks); narrows FW-0044.
