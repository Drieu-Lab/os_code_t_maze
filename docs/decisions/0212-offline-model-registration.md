# ADR 0212: Build rat and blocker models from recorded video in the offline tracker

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/offline_track.py`, `host/tmaze/vision/offline_tracker.py`,
  `host/tmaze/tools/offline_track.py`, `host/tests/test_offline_track.py`
- **Supersedes / Superseded by:** — (extends ADR-0211)

## Context

The offline tracker (ADR-0211) replays a session with an existing model. Building
tracking models is one of its purposes, and the live Tracking tab offers two ways
to build them: Register (a rat appearance model from a window of frames while the
rat moves) and the blocker captures (a blocker model from still frames). The
operator asked that every live tracking option be available offline. Recorded
video lets you pick exactly which frames to build from and try again, which the
live tab cannot do.

## Decision

Add registration flags to `python -m tmaze.tools.offline_track`, driving the same
`RatTracker` calls the live tab uses (no new tracker code):

1. `--register-frames FIRST LAST` builds a rat model over frames FIRST..LAST
   (inclusive) via `start_registration` / `finalize_registration`.
   `--blocker-frames F [F ...]` builds a blocker model, one sample per frame, via
   `capture_blocker_sample` / `finalize_blocker_model`. Each replaces the loaded
   model of its kind.
2. **Order:** after the full setup (model, settings, ROI/landmarks, background;
   ADR-0211), the blocker is built first, then the rat, so the rat model is built
   with the blocker already masked out, as on the rig. Then `reset()`, so
   tracking starts fresh rather than mid-lock-on.
3. **A built model is used for the run.** The summary records it as
   `source: registered` (with the saved path and sha256 when saved), plus a
   `registration` block (frames, sample count, coverage, blocker colour range).
   The rat model stores the settings in force when it was built, as live
   (ADR-0027).
4. **Saving is explicit:** `--save-model <npz>` / `--save-blocker-model <npz>`.
   Nothing goes to `host/ratmodels/` unless you name a path there. The save flags
   need their build flag and a single bundle.
5. `--register-only` builds (and saves) without tracking the video.
6. Thin coverage (fewer samples than `reg_min_frames`) is a note, not an error:
   the model works but may be weak. The note suggests a longer window with the rat
   running, turning, and rearing.
7. Checks that need no video (frame ordering, save without build, register-only
   with nothing to build) live in the pure `validate_registration` and run before
   any video is opened.

## Alternatives considered

- **Seek and register from a time range in seconds.** Rejected for now: frame
  numbers are exact and match the CSV's `frame_idx`. Time selection belongs with
  snippet export (FW-0076).
- **Auto-save into `host/ratmodels/`.** Rejected: that folder holds the models in
  use on the rig, and a trial build should not land there by accident.
- **Build the rat model before the blocker.** Rejected: the rat window would
  include the orange blocker, unlike the live procedure.
- **Make thin coverage an error.** Rejected: the live tab accepts it with a
  warning, and a quick test model is still useful.

## Consequences

- One command builds a model on a chosen stretch of a session and tracks with
  it. To compare against the session's own model, run twice with different
  `--tag`s.
- Smoke test on `602_20261009_132146`: a model built over frames 30000-30899
  (good coverage) tracked frames 40000-41499 94% TRACKING (2% ACQUIRING, 4%
  COASTING), against 100% with the session's own model. So one 30-second window is
  not enough on its own to match the model in use, which is what the comparison
  tool is for.
- Building a model reads frames forward from the start of the video (exact frame
  numbers), so a window late in a long session takes a minute or two to reach.

## Evidence / links

- Tests: `host/tests/test_offline_track.py` (`test_validate_registration`,
  `test_registered_choice`, `test_register_rat_model_then_track`,
  `test_register_blocker_model`, `test_register_only_cli`,
  `test_cli_rejects_save_model_without_frames`).
- Related: ADR-0211 (offline tracker), ADR-0027 (model stores its settings),
  ADR-0034 (blocker tracking), FW-0074 (workbench window).
