# ADR 0020: Save the empty-rig background image alongside recordings

- **Status:** Accepted
- **Date:** 2026-06-30
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tracker.py` (`_reference_full`, `get_background_image`),
  `host/camera_view.py` (`_toggle_record` background write + sidecar header),
  `host/tracker_view.py` ("Save background..." button),
  `host/blob_tracker_prototype.py` (`--background` flag)
- **Supersedes / Superseded by:** — (extends ADR-0014; relates to ADR-0015)

## Context

The markerless tracker (ADR-0014) works by fixed reference-frame background
subtraction: it subtracts an empty-rig reference from each frame. During a live
session the operator records a video to tune the tracker afterwards, but a
recording of an occupied rig contains **no empty-rig frame** - so offline you
cannot press "grab background" on any frame of the video to reproduce the
session's reference. The tracker's in-memory reference (`_reference`) is also
unsuitable to ship as-is: it is grayscale, downscaled to `proc_max_w`, and
blurred, so it is tied to the current `proc_max_w`/`blur` and cannot be
re-derived at other settings.

We need the empty-rig background to travel with each recording so the tracker
can be re-tuned offline against the exact session geometry.

## Decision

Capture and save the **full-resolution colour empty-rig frame** that was current
when the reference was grabbed, not the derived gray proc reference.

- `RatTracker` retains `self._reference_full` (a copy of the full-res BGR frame)
  at the same moment it grabs the gray reference in `process()`, and exposes it
  via `get_background_image()`. The frame fed to `process()` is already
  undistorted + oriented (ADR-0015), so it lines up pixel-for-pixel with the
  recorded video and re-derives a reference at any `proc_max_w`/`blur`.
- **Auto-paired with recordings:** when a recording starts,
  `camera_view._toggle_record()` writes `<animal>_<stamp>_background.png` next to
  the video/CSV (lossless PNG) and records its name in the sidecar CSV header
  (`# background: ...`). No background grabbed -> the field is blank and nothing
  is written.
- **Manual save:** a "Save background..." button on the Tracking tab writes the
  same image to a chosen path for ad-hoc use.
- **Offline load:** `blob_tracker_prototype.py --background <png>` installs the
  saved frame as the reference (one `process()` pass), so a recorded video can be
  tuned with `--source clip.mkv --background clip_background.png`.

## Alternatives considered

- **Save the tracker's gray proc reference** (`_reference`) — rejected: it is
  grayscale, blurred, and locked to the registration `proc_max_w`; reloading it
  would prevent re-tuning `proc_max_w`/`blur` and would not match a full-res
  colour video for inspection.
- **Don't save anything; grab a background from the video offline** — rejected:
  a session recording has no empty-rig frame, so there is nothing to grab.
- **Persist the background inside the `RatModel` .npz** — rejected: the
  background is session/lighting-specific (ADR-0014 deliberately does not persist
  it with the model); pairing it with the recording keeps that separation and
  ties it to the right video.

## Consequences

- Each recording can be re-tuned offline against its true empty-rig reference;
  the PNG + video + sidecar travel together by name.
- A small extra artifact (one lossless PNG) and one full-frame copy held in the
  tracker; negligible next to the video.
- The sidecar header gains a `background` field; readers that ignore unknown
  header comment lines are unaffected.
- If tracking was never enabled / no background grabbed before recording, no
  PNG is produced - the operator must grab a background first (the live-tuning
  workflow already does).

## Evidence / links

- Code: `host/tracker.py` (`_reference_full`, `get_background_image()`),
  `host/camera_view.py` (`_toggle_record` write + `background` header),
  `host/tracker_view.py` (`_save_background`),
  `host/blob_tracker_prototype.py` (`--background`).
- Extends ADR-0014 (markerless tracking); the saved frame is already
  undistorted + oriented per ADR-0015.
