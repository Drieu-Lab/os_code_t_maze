# ADR 0128: Outbound-trajectory window and VTE dwell zones for the proposal figures

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/analysis/trajectories.py`,
  `host/tests/test_analysis_trajectories.py`,
  `host/notebooks/grant_figures/proposal_figures.ipynb`
- **Supersedes / Superseded by:** —

## Context

The K99/R00 proposal needs two behavioural figures the existing per-trial
analysis (`tmaze.analysis`, ADR-0112) cannot produce, because they need the
per-FRAME rat position, not just the trial table:

1. Cue-coloured **trajectory overlays** of ~20 trials at chance vs higher
   performance (does the path separate by cue as the rat learns?).
2. A **VTE** (vicarious-trial-and-error) proxy the proposal text references.

Building these forces two definitional choices a future reader will question,
because there is no single "obviously correct" answer:

- **Which slice of each trial's path is "the trajectory"?** A trial is a full
  loop (hub -> stem -> junction -> arm -> return arm -> hub). The proposal wants
  the decision run only, "excluding inter-trial trajectories" (the return/ITI
  leg).
- **Where is "deliberation" measured for VTE?** Classic VTE is scored at the
  choice point. Our zone vocabulary (`maze_state`, `host/zones.json`) names the
  T-intersection `JUNCTION`; the stem below it is `CENTRAL_STEM`; the regions
  past it are `LEFT_CHOICE`/`RIGHT_CHOICE` (the chosen arms, not the deliberation
  spot).

A second, invisible choice is the **time base**: the per-trial event stamps are
host ISO wall-clock (`datetime.now()`, tz-naive local), while the video sidecar
is `host_unix_s` (`time.time()`, POSIX). They must be joined correctly or the
whole figure is silently wrong.

## Decision

Pin all three in `tmaze.analysis.trajectories` (and reuse them in the notebook):

- **Outbound decision window = `[trial_start_iso, choice_time_iso]` + `end_pad_s`**
  (default 0.6 s). That is the cue->choice run - the stem climb, the pause at the
  split, the commit to an arm - and it EXCLUDES the reward/return/ITI leg. The
  small end pad lets the trace visibly enter the chosen arm (the raw
  `choice_time` stamp lands right at arm entry).
- **VTE dwell = seconds inside `CENTRAL_STEM` u `JUNCTION`** during that window
  (`trajectories.VTE_ZONES`). Chosen per the operator's guidance: the stem
  captures a pause at cue onset, the junction the head-swinging deliberation at
  the choice point. Zone membership reuses the LIVE classifier
  (`maze_state.ZoneMap.classify_point`) so offline zoning == on-rig zoning. The
  integral is a left-Riemann sum over frame timestamps (each inter-sample gap
  credited to its earlier sample's zone).
- **Time base = read the ISO stamp as LOCAL** (`datetime.fromisoformat(s)
  .timestamp()`, `trajectories.iso_to_unix`). Validated against a session's
  sidecar `start_wall_clock` header: the converted start matched the first
  `host_unix_s` to within one frame (0.38 s), and the per-trial windows land the
  path in `CENTRAL_STEM -> JUNCTION` as expected.

Low/high performance blocks are the lowest- and highest-accuracy consecutive
20-trial blocks (`select_blocks`), each kept WITHIN one session/run so a single
background image + zone map applies; the notebook prints each block's real
accuracy so a "chance" block that sits above 50% (likely on late-training days)
is not mislabelled.

## Alternatives considered

- **Whole-loop or start-to-start trajectory** — rejected: the proposal explicitly
  wants the decision run without the return/ITI leg; the return path would clutter
  the overlay and dominate the dwell metric.
- **VTE dwell in `JUNCTION` only** — reasonable (the textbook choice point), but
  rejected on operator guidance: this rat sometimes pauses on cue onset while
  still in the stem, so `CENTRAL_STEM u JUNCTION` captures the behaviour the
  proposal describes. The zone set is a single named constant, easy to narrow
  later.
- **A heading-based VTE index (zIdPhi)** — the classic quantitative VTE score.
  Deferred: it needs a smoothed heading/angular-velocity estimate and careful
  normalisation, disproportionate for a first proposal figure. Dwell time is
  transparent and needs no extra modelling. Left as a possible follow-up.
- **Interpreting the ISO stamp as UTC** (pandas `Timestamp.timestamp()` on a
  tz-naive value) — rejected: it is off by the local UTC offset (4 h here) and
  would put every window outside the recording.
- **Reading the pixel->cm homography for a metric axis** — not needed: the
  overlays are drawn over the pixel background image, and dwell is a time, not a
  distance. `rig_scale.json` `homography` is currently null anyway.

## Consequences

- The two definitions now have one home (`trajectories.py`) with headless tests,
  so the notebook cannot drift from them and a later change (e.g. narrowing the
  VTE zones or adding zIdPhi) is a one-line, testable edit.
- The analysis layer gains its first per-FRAME reader (`load_sidecar`); it stays
  pure pandas/numpy + `tmaze.core` (no Tk/OpenCV/matplotlib), so plotting remains
  in the notebook.
- The figures are only as clean as the session mix: on late-training days the
  "chance" block may be above 50%. That is surfaced (printed accuracy), not
  hidden; widening the date span to an earlier session yields a truer chance
  block when wanted.
- A tracking dropout through a run yields an empty window (no samples); those
  trials are reported (`n_samples == 0`) rather than silently contributing 0 s.

## Evidence / links

- Code: `host/tmaze/analysis/trajectories.py`,
  `host/tests/test_analysis_trajectories.py` (15 tests pass),
  `host/notebooks/grant_figures/proposal_figures.ipynb`.
- Alignment check: session `600_20260825_133212` sidecar
  `start_wall_clock 2026-08-25 13:32:12` -> first `host_unix_s` delta 0.38 s.
- Generated figures (rat 600): the 50%-correct block shows intermixed
  blue/green outbound paths, the 75% block shows cue-separated paths; VTE dwell
  carries the long-deliberation outliers in the chance block.
- Builds on ADR-0112 (analysis layer), ADR-0047 (trial log / event stamps),
  ADR-0015 (undistort + normalized zones), ADR-0014 (video sidecar),
  ADR-0124 (session t0 anchoring), ADR-0083 (paths / layering).
