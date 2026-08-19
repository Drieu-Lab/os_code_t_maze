# ADR 0108: Surface a RELATIVE cue-zone dwell readout (not a reaction time)

- **Status:** Accepted
- **Date:** 2026-08-18
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/phase3_stats.py`;
  `host/tmaze/core/session/session_summary.py`;
  `host/tmaze/ui/behavior_view.py`, `host/tmaze/ui/tooltip_text.py`;
  tests `host/tests/test_phase3_stats.py`
- **Supersedes / Superseded by:** Relates to (does NOT supersede)
  [ADR-0094](0094-trim-misleading-phase3-readouts.md), which removed the bare
  "Choice RT"; refines the readout set of
  [ADR-0052](0052-phase-aware-behavior-readout-and-phase3-stats.md).

## Context

A physical manipulation is being tested on rats 600/601: two short "speedbumps"
(hot-glue gluesticks) in the central stem flanking the choice-cue zone, meant to
slow the animal through the cue region so it actually samples the tone. The first
thing that should move if the manipulation works is the interval between the
choice cue turning on and the choice being scored — the animal dwelling longer in
the cue-on window. In a real 8/14 session this was already the decisive readout:
hand-computing it showed the median cue->choice time rise ~1.0 s -> ~1.3 s after a
taller bump, with a new tail of 2–9 s dwells absent before. Yet nothing in the
saved outputs surfaces it, so it has to be hand-derived from the raw CSV stamps
each time.

The two per-trial stamps needed (`choice_cue_on_iso`, `choice_time_iso`) are
already persisted per trial by `phase3_log.py`, so binning the interval offline is
already possible — the gap is a surfaced, honestly-labeled aggregate.

The tension: [ADR-0094](0094-trim-misleading-phase3-readouts.md) deliberately
REMOVED exactly this interval as a headline "Choice RT", because both stamps are
HOST wall-clock actuation-decision times carrying uncharacterized serial and
actuation latency (see the `phase3_log.py` header note), so presenting it as a
hardware reaction time is misleading. It called the removal "a deliberate 'not
yet'" pending latency characterization (still open as FW-0017).

## Decision

Add a per-trial cue-zone dwell = `choice_time_iso - choice_cue_on_iso` (seconds),
reduced in the pure core `phase3_stats.py` (NOT in `session_summary.py`, which may
not compute measures — ADR-0071) to `cue_dwell_mean_s`, `cue_dwell_median_s`, and
`cue_dwell_n` over the completed trials that carry both parseable stamps. A
negative interval (a clock anomaly) is dropped rather than folded in.

Frame it explicitly as a RELATIVE, within-animal signal, NOT an absolute reaction
time, everywhere it is surfaced (the live behavior panel line "Cue dwell (rel.)",
its tooltip, and the session-summary "Cue dwell" line labeled "relative,
host-clock"). This is the ADR-0094 concern, addressed by presentation rather than
by pretending the latency is gone: only the CHANGE of this number within an animal
(e.g. before vs after a speedbump) is interpretable. In the current rig the choice
cue fires as the animal enters the stem (`choice_cue_on_iso` == `trial_start_iso`),
so this is effectively its cue-zone / stem-traversal dwell.

Median is included alongside the mean precisely because the informative signal is a
lengthening tail (the 2–9 s dwells), which the median resists and the mean chases.

No `SCHEMA_VERSION` bump in `session_summary.py`: the JSON `cued_task` block is an
additive `vars()` dump of `Phase3Stats`, so the three new fields appear
automatically, consistent with how ADR-0094/0086/0090/0092 added `cued_task`
fields without a bump.

## Alternatives considered

- **Leave it removed (ADR-0094 as-is) and keep hand-deriving from the CSV.**
  Rejected: the manipulation is running now, and the readout is the decisive one;
  making an operator reprocess raw CSVs each session is exactly the analysis gap
  this work targets.
- **Reinstate a bare absolute "reaction time".** Rejected — that is precisely what
  ADR-0094 forbids until latencies are characterized. We keep the number but reframe
  it as relative and never call it a reaction time.
- **Mean only.** Rejected: the manipulation's signature is a new long-dwell tail,
  so the median (robust) and the mean (tail-sensitive) together are more honest than
  either alone; `n` is shown so a small sample is visible.
- **Compute it in `session_summary.py`.** Rejected: ADR-0071 keeps the summary a
  selector/labeler; measures live in a core. The live panel and the summary now read
  the same core fields.

## Consequences

- The live Phase 3 panel and the session summary each gain one line; the JSON
  summary gains three `cued_task` keys. Existing readers are unaffected (additive).
- `phase3_stats.py` regains a small `_parse_iso` (removed in ADR-0094) plus
  `_cue_dwells` / `_median` helpers; `Phase3Stats` gains three fields (appended
  last, defaulted, so every construction site stays valid).
- **Revisit when:** the serial/actuation + cue-onset latencies are characterized
  (FW-0017). At that point an ABSOLUTE, latency-corrected reaction time could be
  reinstated from the same retained CSV stamps, superseding the "relative only"
  framing here.

## Evidence / links

- Core: `host/tmaze/core/phase3/phase3_stats.py` (`_parse_iso`, `_cue_dwells`,
  `_median`, the three `cue_dwell_*` fields, wired in `compute`).
- Surfaces: `host/tmaze/ui/behavior_view.py` (`_dwell_text` + the "dwell" line),
  `host/tmaze/ui/tooltip_text.py` (`behavior.phase3.dwell`),
  `host/tmaze/core/session/session_summary.py` (`_dwell_str` in `_cued_lines`).
- Tests: `host/tests/test_phase3_stats.py` (`TestCueDwell`: mean/median/n,
  even-count median, missing-stamp exclusion, negative drop, empty).
- Prior art: the ~1.0 s -> ~1.3 s hand-computed shift in the 8/14 session that
  motivated it; ADR-0094 (why the bare RT was removed); FW-0017 (latency
  characterization that would unlock an absolute RT).
