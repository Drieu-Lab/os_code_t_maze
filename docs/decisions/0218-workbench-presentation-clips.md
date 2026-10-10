# ADR 0218: Saving presentation clips from the offline workbench

- **Status:** Accepted
- **Date:** 2026-10-10
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/offline_workbench.py` (`clip_plan`, `clip_name`),
  `host/tmaze/vision/video_player.py` (`ClipWriter`, `VideoPlayer.save_clip`),
  `host/tmaze/ui/offline_workbench.py` (`Overlay`, "Save A-B as clip")
- **Supersedes / Superseded by:** — (builds on ADR-0214..0216)

## Context

The operator wants short excerpts of sessions, with the tracking drawn on, to
show in talks and to colleagues. Nothing in the rig wrote a video excerpt; the
A-B marks only fed loop playback and model building, and a range run writes a
track CSV, not video.

## Decision

- **What you see is what is saved.** "Save A-B as clip..." writes frames A..B
  with the overlay toggles as they are when the save starts (ROI, trail, zones,
  ghosts, mask inset...). The window's drawing moved into an `Overlay` object
  fixed at that moment, so the screen and the clip use the same code and the
  clip does not change if a toggle is flipped mid-save. It is drawn at the
  video's full resolution, not the on-screen scale.
- **Tracked like playback.** The save runs as a job on the player's worker
  thread with the window's own tracker (so a model built and not yet saved is
  used), tracking from the warm-up before A so the trail and rat marker are
  already locked on at the first frame. Playback pauses during the save; the
  shown frame is re-tracked afterwards. Progress and Cancel are on the
  transport bar; a cancelled or failed save deletes the partial file.
- **Format for presentation software.** H.264, yuv420p (frame size cropped to
  even if needed), CRF 18 (the rig's visually-lossless setting), the index at the
  front of the file (`faststart`) so it starts playing at once. PowerPoint,
  Teams and browsers play it without extra codecs. Our own `ClipWriter`
  rather than `camera_view`'s writer: that one rounds the rate to whole frames
  per second and lives in the UI layer, which the player cannot import.
- **Speed per export** (0.25x-4x, chosen next to the button). Slower clips keep
  every frame at a fractional rate (0.25x of 15 fps = 3.75 fps). A clip faster
  than 60 fps keeps every n-th frame instead (`MAX_CLIP_FPS`), because many
  players, PowerPoint among them, stutter or drop frames above 60 fps.
- **No caption** is burnt in (operator's choice).
- **Saved only through a Save-as dialog**, opening in the bundle's `video/`
  with a suggested name `<id>_clip_<A>-<B>[_<speed>x].mp4`.

## Alternatives considered

- **Screen-record the window.** Rejected: ties the clip to the screen's size
  and scaling, and captures the controls.
- **Clean video only, overlay left to the presenter.** Rejected; the overlay is
  usually the point. Turning every toggle off gives a clean clip anyway.
- **Run the save on its own tracker (like `RunJob`), keeping playback live.**
  Rejected: it would need models built in the window saved first, and could
  drift from what the window shows; a clip is seconds to a minute of work.
- **Lossless FFV1.** Rejected for presentations: large files most players can't
  open.

## Consequences

- The workbench needs PyAV (`av`, already a rig dependency) to save clips.
- Overlay line widths and label sizes are fixed in pixels, so on the full
  1280x1024 frame they look thinner than in the scaled-down window.
- A 4x clip of a 42 fps recording keeps every third frame.

## Evidence / links

- `host/tests/test_offline_workbench.py::TestClips`, `test_video_player.py`
  (warm-up before A, a draw call per written frame, file frame count and rate,
  cancel leaves no file), `test_offline_workbench_ui.py::test_save_clip_from_marks`.
- Smoke on `600_20260714_131656` (15 fps): a 10 s clip saved in ~3 s at 1x
  (150 frames, 1280x1024, 15.9 MB) and at 4x (60 fps, 4.8 MB).
