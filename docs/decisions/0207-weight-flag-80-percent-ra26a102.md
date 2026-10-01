# ADR 0207: Lower the welfare weight flag from 90% to 80% of baseline (protocol RA26A102)

- **Status:** Accepted
- **Date:** 2026-10-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine Drieu
- **Affects:** `host/tmaze/core/records/dashboard.py` (`BELOW_THRESHOLD`, new `THRESHOLD_LABEL`), `host/tmaze/ui/records_manager.py` (flag labels, legend, dashboard text, chart line), `host/tests/test_dashboard.py`; resources repo `records/animal/build_daily_log.py` (Entry-sheet red flag, View-sheet threshold row + red cells)
- **Supersedes / Superseded by:** —

## Context

The Records Manager flags a rat in red when its weight drops below a fixed
fraction of its free-feeding baseline weight (`dashboard.BELOW_THRESHOLD`). That
fraction was 0.90, taken from the old ACUC protocol RA23A232 (PI Janak).

The experiments now run under a new protocol, **RA26A102** ("Brain-wide
mechanisms of memory formation and consolidation", PI Drieu, approved by the JHU
ACUC on 2026-08-17; PDF in the resources repo `animal_protocol/RA26A102.pdf`).
Section 14d, *Water restriction*, says rats are restricted "to achieve a
targeted weight reduction to 80-85% of the original weight before training
starts" and are then "maintained at 80-85% of their free-water/feeding weights
by adjusting their water ration after weighing."

Under the new protocol a rat at 85% of baseline is on target, but the old 90%
flag would show it in red every day. A flag that is always on stops being read,
so the old line was hiding real problems instead of showing them.

Before this change the 90% value was written in several places that didn't
share the constant: on-screen text in the Records Manager ("<90%!", "<90% FLAG",
"needs +x g to reach 90%", the table legend and a column tooltip) and three
literal `0.9`s in the daily-log Excel builder. If only the constant changed,
those would have kept saying 90% while the flag worked at 80%.

## Decision

- `dashboard.BELOW_THRESHOLD = 0.80`: a weight **below** 80% of baseline is
  flagged; exactly 80% is not (the protocol's range includes 80%).
- A new `dashboard.THRESHOLD_LABEL` ("80%") is built from the constant, and
  every user-facing label in the Records Manager uses it, so the text can't drift
  from the value again.
- The daily-log Excel builder (`build_daily_log.py`, resources repo) imports
  both from `tmaze.core.records.dashboard` (it already depends on the `tmaze`
  package; ADR-0162). Its Entry-sheet conditional format, the View sheet's
  "80% (g)" target-weight row and its red cells all follow the same value.

Only the **lower** bound (80%) is a flag. The protocol's upper bound (85%) is a
target the operator manages with the water ration, not a welfare alarm, so no
"above 85%" warning was added.

## Alternatives considered

- **Keep 90% as a stricter in-lab margin.** Rejected: the protocol's target
  range is 80-85%, so a 90% line would be red on every correctly restricted
  day, and an always-on flag gets ignored.
- **Flag at 85% (the top of the band).** Rejected: 85% is where the protocol
  wants the rats to be. Flagging there would mark on-target animals the same
  way as animals in trouble.
- **Per-animal threshold in `animals.json`.** Rejected for now: one protocol
  covers every rat on this rig, so a single value is simpler and can't be set
  wrong per rat. Revisit if the rig ever runs animals under different
  protocols or restriction types (food restriction under RA26A102 uses
  85-90% of an age-matched control weight, a different rule).
- **Change only the constant.** Rejected: the GUI text and the Excel builder
  had their own hardcoded 90s and would have contradicted the flag.

## Consequences

- Days between 80% and 90% of baseline that used to show red are no longer
  flagged. The history in `daily_log.csv` is unchanged; only how it is shown
  changes. The stored `pct_baseline` values are fractions and don't depend on
  the threshold.
- The legacy `build_water_restriction_log.py` / `water_restriction_log.xlsx`
  (the paper-form copy kept as a historical log) still shows 90%. It was left
  as is on purpose, because it records the old regime.
- `daily_log.xlsx` picks up the new red flag the next time it is rebuilt
  (`build_daily_log.py`, or the Records Manager's Excel action).
- FW-0060 (age-matched baseline) is still open: the fixed July baseline makes
  any percentage flag rarely fire for growing rats. A moving baseline would
  feed the same `BELOW_THRESHOLD`.

## Evidence / links

- Protocol: resources repo `animal_protocol/RA26A102.pdf`, section 14d (water
  restriction, 80-85%); approval memo dated 2026-08-19 (ACUC approval
  2026-08-17).
- Tests: `host/tests/test_dashboard.py`: the constant/label are pinned to
  0.80/"80%", 270/348 g (77.6%) is flagged with an 8.4 g deficit, 312/348 g
  (89.7%, flagged under the old rule) is not, and exactly 80% is not.
- Related: ADR-0087 (trend chart threshold line), ADR-0103 (Daily table red
  tint), ADR-0162 (builders import `tmaze`), FW-0060.
