# ADR 0094: Trim two misleading Phase 3 readouts - remove the wall-clock reaction time, hide reward rate under the shaping aid

- **Status:** Accepted
- **Date:** 2026-08-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_stats.py`, `phase3_log.py`;
  `host/tmaze/core/session/session_summary.py`;
  `host/tmaze/ui/behavior_view.py`, `tooltip_text.py`;
  tests `test_phase3_stats.py`
- **Supersedes / Superseded by:** Refines the readout set of
  [ADR-0052](0052-phase-aware-behavior-readout-and-phase3-stats.md); the
  reaction-time stamps themselves (ADR-0047) are unchanged in the CSV.

## Context

Two of the Phase 3 behavior-panel numbers were misleading enough on the bench
(bug `#260720_BehaviorStatError`) that showing them was worse than not:

1. **Choice reaction time** (`mean_rt_s`) was the mean of
   `choice_time_iso - choice_cue_on_iso`. Both are **host wall-clock** stamps at the
   host's actuation-decision points, so the interval carries uncharacterized serial
   and actuation latency (ADR-0047) plus the rat's travel time up the stem. Labeled
   "Choice RT", it reads as a hardware reaction time but is not one, and the
   latencies that dominate it have not yet been measured.
2. **Reward rate** = rewarded / completed. Under the `reward_incorrect` shaping aid
   (reward dispensed even on incorrect choices), *every* completed trial is
   rewarded, so the rate pins at ~100% by construction and carries no information -
   it silently stops tracking accuracy.

## Decision

- **Remove the reaction-time readout for now.** Drop `Phase3Stats.mean_rt_s` /
  `rt_n` and the `_mean_reaction_time` helper (and its `_parse_iso`); remove the
  "Choice RT" line from the live panel and the session summary and its tooltip. The
  raw `choice_cue_on_iso` / `choice_time_iso` stamps stay in the per-trial CSV, so
  the measure can be reinstated unchanged once the hardware latencies are
  characterized. This is a deliberate "not yet", not a permanent deletion.
- **Hide the reward-rate line under the shaping aid.** Thread the run's
  `reward_incorrect` flag through the published snapshot
  (`Phase3StateSnapshot.reward_incorrect`) into `Phase3Stats.reward_incorrect`, and
  when it is set, omit the reward-rate line entirely (live panel and summary) rather
  than print a misleading ~100%. `reward_rate` is still computed and still emitted
  in the JSON summary (for the logs); only the human-facing line is suppressed.

Note: `reward_incorrect` is **not** in current training use, so in practice the
reward line shows normally today; this is a defensive correction for when the aid
is turned on.

## Alternatives considered

- **Keep RT but relabel / robustify it** (median, an upper cutoff, an "approx"
  tag). Rejected for now: the dominant error is un-characterized latency, not
  outliers, so no presentation tweak makes the number trustworthy yet. Reinstate
  once latency is measured (the raw stamps are retained for exactly this).
- **Show reward rate as "n/a" under the aid instead of hiding it.** Rejected per
  the operator's preference to remove it entirely; a blank line is less visual
  clutter than an "n/a" placeholder for a rarely-used mode.
- **Compute `reward_incorrect` from the folded `params` in the summary only.** Done
  for the summary (it reads `params`), but the live panel has no params handle, so
  the flag is carried on the snapshot/stats - one source both surfaces read.

## Consequences

- The panel and summary drop one line each (RT), and the reward line disappears
  only in the (currently unused) shaping-aid mode. No arithmetic changes for the
  measures that remain.
- `Phase3Stats` loses two fields and gains one (`reward_incorrect`);
  `Phase3StateSnapshot` gains one bool. The JSON summary's `cued_task` block loses
  `mean_rt_s` / `rt_n` and gains `reward_incorrect` (an additive `vars()` dump, no
  `SCHEMA_VERSION` bump - consistent with ADR-0086/0090/0092).
- **Revisit when:** the serial/actuation and cue-onset latencies are characterized
  (reinstate a corrected reaction time from the retained CSV stamps).

## Evidence / links

- Core: `host/tmaze/core/phase3/phase3_stats.py` (RT fields/helpers removed;
  `reward_incorrect` field), `phase3_log.py` (`Phase3StateSnapshot.reward_incorrect`
  + `snapshot`).
- Surfaces: `host/tmaze/ui/behavior_view.py` (RT line removed; reward line gated),
  `host/tmaze/core/session/session_summary.py` (`_cued_lines`),
  `host/tmaze/ui/tooltip_text.py` (`behavior.phase3.rt` removed;
  `behavior.phase3.reward` updated).
- Tests: `host/tests/test_phase3_stats.py` (RT tests removed; empty-snapshot
  assertion updated).
- Bug: `docs/notes/Bugs.md` `#260720_BehaviorStatError` (F5 + F2 of the review pass).
