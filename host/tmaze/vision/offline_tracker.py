"""
offline_tracker.py - run the live RatTracker over a recorded session video.

Thin OpenCV shell over the pure decisions in ``tmaze.core.offline_track``: open
the video, build a ``RatTracker`` from a resolved ``OfflineSettings``, then track
EVERY frame (the live tracker can skip frames when it falls behind; offline it
never does). Used by the CLI ``tmaze.tools.offline_track``; ``iter_track`` is the
seam the future offline workbench and snippet export reuse (FW-0074 / FW-0076).

Setup order matters: loading a model re-applies the settings it was built with,
so the session's own settings are applied AFTER it; and changing ``proc_max_w``
discards the background, so the background is installed LAST. See
docs/decisions/0211.

The recorded video is already undistorted and oriented at the source (ADR-0015),
so frames go to the tracker exactly as decoded. Needs ``opencv-python`` + ``numpy``.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from tmaze.core import offline_track as OT
from tmaze.core.sync.spans import Frame, parse_sidecar_csv
from tmaze.core.timeutil import now_iso
from tmaze.vision.tracker import TRACK_COLUMNS, RatTracker, cv2, np, track_row

CSV_COLUMNS = ("frame_idx", "host_unix_s", "cam_timestamp") + TRACK_COLUMNS
PROGRESS_EVERY = 1000         # frames between progress callbacks while tracking
SKIP_PROGRESS_EVERY = 10000   # ... and while skipping to the start frame

Progress = Callable[[int, Optional[int]], None]


class OfflineTrackError(RuntimeError):
    pass


def _open(video: str):
    if cv2 is None or np is None:
        raise OfflineTrackError("offline tracking needs opencv-python + numpy")
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise OfflineTrackError(f"cannot open video: {video}")
    return cap


def read_sidecar(path: Optional[str]) -> Tuple[Dict[str, str], List[Frame]]:
    if not path:
        return {}, []
    with open(path, encoding="utf-8") as fh:
        return parse_sidecar_csv(fh)


def median_background(video: str, n_frames: int,
                      n_samples: int = OT.BACKGROUND_SAMPLES):
    """Per-pixel median of ``n_samples`` frames spread across the video.

    Seeks to each sample (approximate on compressed video, which is fine for a
    background). The rat occupies any one pixel in only a few samples, so the
    median is the empty rig.
    """
    cap = _open(video)
    try:
        if n_frames <= 0:
            n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames = []
        for idx in OT.background_sample_indices(n_frames, n_samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if ok and frame is not None:
                frames.append(frame)
    finally:
        cap.release()
    if not frames:
        raise OfflineTrackError(f"could not read any frames for a background: {video}")
    return np.median(np.stack(frames), axis=0).astype(np.uint8)


def build_tracker(settings: OT.OfflineSettings
                  ) -> Tuple[RatTracker, Dict[str, Any], Dict[str, str]]:
    """A tracker set up as the session had it (+ overrides), still without a
    background - install one with :func:`install_background` as the LAST step.

    Returns ``(tracker, final_params, param_sources)``. Raises ``ValueError`` on
    a bad ``--set`` / params-file entry.
    """
    tracker = RatTracker()
    if settings.model is not None:
        tracker.load_model(settings.model.path, restore=True)
    params, sources = OT.merge_params(asdict(tracker.get_params()), settings)
    tracker.update_params(**params)
    if settings.blocker_model is not None:
        tracker.load_blocker_model(settings.blocker_model.path)
    if settings.roi is not None and not tracker.load_roi(settings.roi.path):
        raise OfflineTrackError(f"ROI file has fewer than 3 points: {settings.roi.path}")
    if settings.landmarks is not None:
        tracker.load_landmarks(settings.landmarks.path)
    return tracker, params, sources


def install_background(tracker: RatTracker, background_bgr) -> None:
    tracker.grab_background()
    tracker.process(background_bgr)   # one pass installs the reference
    tracker.reset()


def load_background(settings: OT.OfflineSettings, n_frames: int):
    if settings.background.path:
        bg = cv2.imread(settings.background.path, cv2.IMREAD_COLOR)
        if bg is None:
            raise OfflineTrackError(f"cannot read background: {settings.background.path}")
        return bg
    return median_background(settings.video, n_frames)


def iter_track(cap, tracker: RatTracker, first_processed: int, first_written: int,
               last_written: Optional[int], progress: Optional[Progress] = None
               ) -> Iterator[Tuple[int, Any, Any]]:
    """Yield ``(frame_idx, frame_bgr, TrackResult)`` for every written frame.

    Frames before ``first_processed`` are skipped without decoding into pixels
    (``grab``), so frame numbers stay exact; frames from ``first_processed`` up
    to ``first_written`` are tracked but not yielded (warm-up).
    """
    idx = 0
    while idx < first_processed:
        if not cap.grab():
            return
        idx += 1
        if progress is not None and idx % SKIP_PROGRESS_EVERY == 0:
            progress(idx, last_written)
    while last_written is None or idx <= last_written:
        ok, frame = cap.read()
        if not ok or frame is None:
            return
        result = tracker.process(frame)
        if idx >= first_written:
            yield idx, frame, result
        idx += 1
        if progress is not None and idx % PROGRESS_EVERY == 0:
            progress(idx, last_written)


def run(settings: OT.OfflineSettings, *, csv_path: str, json_path: str,
        tag: Optional[str] = None, start_frame: int = 0,
        end_frame: Optional[int] = None, warmup_frames: int = 0,
        save_background: Optional[str] = None,
        progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Track one bundle's video and write the track CSV + summary JSON.

    The CSV is written to a temporary name and renamed only when the run
    finishes, so an interrupted run never leaves a file that looks complete.
    Returns the summary dict.
    """
    first_processed, first_written, last_written = OT.frame_range(
        start_frame, end_frame, warmup_frames)
    sidecar_header, sidecar_frames = read_sidecar(settings.sidecar)
    notes = list(settings.notes)
    tracker, params, sources = build_tracker(settings)

    background = load_background(settings, len(sidecar_frames))
    if save_background:
        os.makedirs(os.path.dirname(os.path.abspath(save_background)) or ".", exist_ok=True)
        if not cv2.imwrite(save_background, background):
            raise OfflineTrackError(f"could not write background: {save_background}")
        notes.append(f"background saved to {os.path.abspath(save_background)}")
    install_background(tracker, background)

    created = now_iso()
    tally = OT.RunTally()
    t0 = time.monotonic()
    tmp = csv_path + ".partial"
    os.makedirs(os.path.dirname(os.path.abspath(csv_path)) or ".", exist_ok=True)
    cap = _open(settings.video)
    try:
        _write_rows(tmp, cap, tracker, settings, sidecar_header, sidecar_frames,
                    created, tag, first_processed, first_written, last_written,
                    tally, progress)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        cap.release()
    os.replace(tmp, csv_path)
    elapsed = time.monotonic() - t0
    last_idx = first_written + tally.frames - 1

    frames_decoded = last_idx + 1
    if sidecar_frames and end_frame is None and frames_decoded != len(sidecar_frames):
        notes.append(f"frame count mismatch: video {frames_decoded} vs sidecar "
                     f"{len(sidecar_frames)} rows (time columns blank past the sidecar)")
    summary = OT.summary_dict(
        settings, tally=tally, params=params, param_sources=sources,
        sidecar_rows=len(sidecar_frames) if settings.sidecar else None,
        frames_decoded=frames_decoded, first_written=first_written,
        last_written=last_idx if tally.frames else None, warmup=warmup_frames,
        elapsed_s=elapsed, created_iso=created, tag=tag, csv_path=csv_path,
        notes=notes)
    OT.write_json(json_path, summary)
    return summary


def _write_rows(path: str, cap, tracker: RatTracker, settings: OT.OfflineSettings,
                sidecar_header: Dict[str, str], sidecar_frames: List[Frame],
                created: str, tag: Optional[str], first_processed: int,
                first_written: int, last_written: Optional[int],
                tally: OT.RunTally, progress: Optional[Progress]) -> None:
    """Write the CSV header + one row per written frame (rows are matched to the
    sidecar by position: decoded frame i is sidecar row i)."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in OT.csv_header_lines(settings, sidecar_header,
                                        created_iso=created, tag=tag):
            fh.write(line + "\n")
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for idx, _frame, result in iter_track(cap, tracker, first_processed,
                                              first_written, last_written, progress):
            if idx < len(sidecar_frames):
                sf = sidecar_frames[idx]
                times = (f"{sf.host_unix_s:.6f}", "" if sf.cam_ts is None else sf.cam_ts)
            else:
                times = ("", "")
            writer.writerow((idx,) + times + track_row(result))
            tally.add(result.state, result.detected)
