# ADR 0093: Compute win-stay / lose-shift over truly-adjacent free-choice trials

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_stats.py`; test `test_phase3_stats.py`
- **Supersedes / Superseded by:** Refines the win-stay/lose-shift measure of
  [ADR-0052](0052-phase-aware-behavior-readout-and-phase3-stats.md) under the
  closed-loop debias partition of
  [ADR-0086](0086-phase3-debias-blocks.md) / [ADR-0090](0090-debias-bias-normalized-by-errors.md).

## Context

`phase3_stats` reports a naive-strategy readout - **win-stay** (P(same arm next |
this trial CORRECT)) and **lose-shift** (P(different arm next | this trial
INCORRECT)) - to flag a rat repeating/alternating on its own outcome instead of
following the cue. It is a *sequential* measure: it pairs each trial with the very
next one.

The debias work (ADR-0086) made most learning measures compute over **out-of-block
(`oob`)** trials only (the forced corrective-block trials removed). Win-stay/
lose-shift was passed that same `oob` list and paired *adjacent entries of the
filtered list*. That is wrong for a sequential measure: when a forced corrective
block sits between two out-of-block trials, those two are adjacent in the filtered
list but were **not** consecutive in time - a forced trial (or several) happened
between them. The measure then treated "the choice two-plus trials later, after a
coerced detour" as "the next choice", corrupting it. A forced trial could also
appear as the conditioning trial itself, where "stay/shift" isn't a strategy the
rat chose. Balanced sessions were unaffected (no in-block trials), so it only
mis-read once a debias session ran a block.

## Decision

Compute win-stay / lose-shift over **genuine trial-to-next-trial transitions of the
rat's own free choices.** Pass the full ordered `completed` list (the
CORRECT/INCORRECT prefix) and count a pair `(a, b)` only when:

- `b.trial == a.trial + 1` - they are consecutive trials. Any trial that
  intervened (a forced in-block trial, an operator-abandoned trial, or a
  not-yet-scored one) leaves a gap in the trial numbering and **breaks** the pair
  instead of being skipped over; and
- neither `a` nor `b` is an in-block (`INTERVENTION`) trial - a forced choice is
  not a strategy the rat adopted, and conditioning on a forced outcome (or asking
  whether it "stayed" into a forced next trial) is meaningless.

For a balanced session every completed trial is out-of-block and consecutively
numbered, so this reduces exactly to the previous adjacent-pair definition - the
readout is unchanged there.

## Alternatives considered

- **Keep pairing filtered-`oob` neighbours.** Rejected: that is the bug - it pairs
  trials separated by a forced block as if they were consecutive.
- **Pair over all completed trials, only excluding a forced trial as the
  *conditioning* (`a`) member.** Rejected: it would still count `(oob a, forced b)`
  where the "next" choice was coerced - not the rat's strategy - and would still
  span an abandoned-trial gap. Requiring both members out-of-block *and*
  trial-adjacent is the clean rule.
- **Also treat a not-yet-scored trial between two completed ones as "adjacent".**
  Rejected implicitly: the `b.trial == a.trial + 1` check already breaks such a
  pair, which is the correct, conservative behavior.

## Consequences

- Win-stay / lose-shift now measure only real, uncoerced trial-to-next-trial
  transitions, so they mean the same thing in a debias session as in a balanced
  one. They can read `None` more often in a heavily-blocked session (fewer eligible
  adjacent oob pairs), which is honest - there is simply less free-choice sequence
  to measure.
- No change to the dataclass or the JSON/text surfaces; only the internal pairing
  logic changed.
- **Revisit if:** a future analysis wants strategy conditioned across blocks (it
  would need a different, explicitly cross-block measure).

## Evidence / links

- Core: `host/tmaze/core/phase3/phase3_stats.py` (`_win_stay_lose_shift`, now over
  `completed` with the adjacency + out-of-block guards; called from `compute`).
- Tests: `host/tests/test_phase3_stats.py` (`TestWinStayAdjacency`: plain pair,
  in-block break, trial-number-gap break, and only-oob-pairs-count).
- Bug: `docs/notes/Bugs.md` `#260720_BehaviorStatError` (F3 of the review pass).
