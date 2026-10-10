# ADR 0214: Workbench settings, warm-up, and command-line equivalence

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/offline_workbench.py`,
  `host/tests/test_offline_workbench.py`; later the workbench window and its
  video player (FW-0074)
- **Supersedes / Superseded by:** — (builds on ADR-0211, ADR-0213)

## Context

The workbench (ADR-0213) lets you change tracker settings and files while
watching a recording, then run the whole video. A result found in the window is
only useful if it can be repeated: rerun later, batch-run over other sessions,
or written in a methods note. Three details decide whether that holds:

- The live tab's sliders store some settings as `slider * factor`; "Accept"
  turns 0.35 into `35 * 0.01 = 0.35000000000000003`. Compared exactly, every
  untouched slider would look changed.
- The tracker's answer on a frame depends on earlier frames (smoothing,
  lock-on), so jumping to a frame and tracking it cold differs from the live run.
- Trial-log times must be placed on the video's frames to mark trials.

## Decision

- **One path for both.** The window describes its settings as the `--set`
  values that turn the command line's default settings for that bundle into the
  window's (`set_args`). "Run whole video" runs `offline_tracker.run` with those
  values; "Copy command line" prints the same values (`cli_args`,
  `format_command`). So a window run and its copied command go through the same
  `merge_params`, and produce the same CSV.
- **Floats are compared to 9 decimal places** (`FLOAT_DECIMALS`) and written
  rounded, so slider arithmetic is not a change and the written value parses
  back to itself.
- **Files:** each file the window uses becomes a flag only when it differs from
  the command line's default (`--model`, `--no-blocker`, `--background-from-video`,
  ...). A model built from marked frames and not saved becomes
  `--register-frames` / `--blocker-frames`; anything else drawn and not saved
  (ROI, landmarks) is listed as a warning to save it first. A run from the window
  saves such models next to its outputs (`<id>_offline_track_<tag>_model.npz`,
  `..._blockermodel.npz`).
- **Warm-up after a jump: 150 frames by default** (`DEFAULT_WARMUP_FRAMES`,
  about 4-6 s of video, adjustable in the window): the tracker re-runs from
  `target - 150` and shows `target`. Changing a setting while paused re-runs the
  same warm-up so the still frame reflects the new setting.
- **Trial markers** come from the trial log's ISO times mapped to the nearest
  sidecar frame time (`host_unix_s`); events more than 1 s outside the video get
  no frame. Older naive ISO stamps are read in the computer's own time zone,
  correct on the rig PC.

## Alternatives considered

- **Pass the window's full settings to the run directly.** Rejected: the copied
  command would take a different path and could differ.
- **Exact float comparison.** Rejected: untouched sliders would appear as
  changes, cluttering the settings table and command line.
- **Track a jumped-to frame cold (no warm-up).** Rejected: shows results the
  live run never produced; a short re-run is cheap (~0.5 s at ~270 frames/s).

## Consequences

- The workbench can promise "same command, same CSV"; its acceptance check runs
  the window's settings and the copied command over one range and compares.
- A model built in the window with settings that were changed afterwards is
  rebuilt by the command line with the final settings, so it can differ
  slightly; saving the model avoids that.

## Evidence / links

- `host/tests/test_offline_workbench.py` (`set_args` round-tripped through
  `merge_params`; the command line for each file choice).
- ADR-0211, ADR-0212, ADR-0213; glossary "Warm-up (tracking)".
