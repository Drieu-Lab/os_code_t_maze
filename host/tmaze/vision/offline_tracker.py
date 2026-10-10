"""
offline_tracker.py - run the live RatTracker over a recorded session video.

Thin OpenCV shell over the pure decisions in ``tmaze.core.offline_track``: open
the video, build a ``RatTracker`` from a resolved ``OfflineSettings``, then track
EVERY frame (the live tracker can skip frames when it falls behind; offline it
never does). Used by the CLI ``tmaze.tools.offline_track``; ``iter_track`` is the
seam the future offline workbench and snippet export reuse (FW-0074 / FW-0076).
It can also build new rat / blocker models from the video's own frames before
tracking, by driving the tracker's own registration calls (ADR-0212).

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
from dataclasses import asdict, replace
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


def frames_at(video: str, indices) -> Iterator[Tuple[int, Any]]:
    """Yield ``(frame_idx, frame_bgr)`` for the given frame numbers, in order.

    Decodes forward from the start (``grab`` skips without converting pixels),
    so the frame numbers are exact. Stops early at the end of the video.
    """
    cap = _open(video)
    idx = 0
    try:
        for target in sorted(set(indices)):
            while idx < target:
                if not cap.grab():
                    return
                idx += 1
            ok, frame = cap.read()
            if not ok or frame is None:
                return
            yield idx, frame
            idx += 1
    finally:
        cap.release()


def register_blocker(tracker: RatTracker, video: str, frames) -> Dict[str, Any]:
    """Build a blocker model from still frames, as the live tab's captures do:
    one sample per frame, finalized on the last. Replaces any loaded blocker."""
    got = list(frames_at(video, frames))
    if not got:
        raise OfflineTrackError(f"--blocker-frames: none of {list(frames)} is in the video")
    tracker.clear_blocker_model()
    for k, (_idx, frame) in enumerate(got):
        tracker.capture_blocker_sample()
        if k == len(got) - 1:
            tracker.finalize_blocker_model()
        tracker.process(frame)
    model = tracker.get_blocker_model()
    if model is None:
        raise OfflineTrackError("--blocker-frames: no blocker blob found in those frames")
    return {"frames": [i for i, _ in got], "samples": int(model.n),
            "orange_lo": list(model.orange_lo) if model.orange_lo else None,
            "orange_hi": list(model.orange_hi) if model.orange_hi else None}


def register_model(tracker: RatTracker, video: str, first: int, last: int
                   ) -> Dict[str, Any]:
    """Build a rat appearance model over frames ``first..last`` (inclusive), as
    the live tab's Register window does. Replaces the loaded model; the settings
    in force are stored with it (ADR-0027)."""
    got = list(frames_at(video, range(first, last + 1)))
    if not got:
        raise OfflineTrackError(f"--register-frames: frame {first} is past the end of the video")
    tracker.clear_model()
    tracker.start_registration()
    for k, (_idx, frame) in enumerate(got):
        if k == len(got) - 1:
            tracker.finalize_registration()
        tracker.process(frame)
    model = tracker.get_model()
    if model is None:
        raise OfflineTrackError("--register-frames: no rat blob found in those frames")
    return {"frames": [got[0][0], got[-1][0]], "frames_read": len(got),
            "samples": int(model.n), "target_samples": tracker.get_params().reg_min_frames,
            "coverage_ok": bool(model.coverage_ok)}


def _registered(tracker: RatTracker, settings: OT.OfflineSettings,
                req: OT.RegistrationRequest, notes: List[str]
                ) -> Tuple[OT.OfflineSettings, Dict[str, Any]]:
    """Run the requested registrations (blocker first, so the rat window
    already has the blocker subtracted), save them, and return the settings
    with the new models recorded as the files used."""
    info: Dict[str, Any] = {}
    if req.blocker_frames:
        info["blocker"] = register_blocker(tracker, settings.video, req.blocker_frames)
        if req.save_blocker_model:
            _ensure_dir(req.save_blocker_model)
            tracker.save_blocker_model(req.save_blocker_model)
        settings = replace(settings, blocker_model=OT.registered_choice(req.save_blocker_model))
    if req.rat_frames is not None:
        info["rat"] = register_model(tracker, settings.video, *req.rat_frames)
        if not info["rat"]["coverage_ok"]:
            notes.append(
                f"registered rat model has thin coverage ({info['rat']['samples']} samples, "
                f"target {info['rat']['target_samples']}): use a longer window with the rat "
                "running, turning and rearing")
        if req.save_model:
            _ensure_dir(req.save_model)
            tracker.save_model(req.save_model)
        settings = replace(settings, model=OT.registered_choice(req.save_model))
    tracker.reset()
    return settings, info


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)


def prepare(settings: OT.OfflineSettings, *, save_background: Optional[str] = None,
            registration: Optional[OT.RegistrationRequest] = None):
    """Everything before the frame loop: the tracker (settings, files,
    background) and any registration. Returns ``(tracker, settings, params,
    sources, notes, registration_info, sidecar_header, sidecar_frames)``."""
    sidecar_header, sidecar_frames = read_sidecar(settings.sidecar)
    notes = list(settings.notes)
    tracker, params, sources = build_tracker(settings)

    background = load_background(settings, len(sidecar_frames))
    if save_background:
        _ensure_dir(save_background)
        if not cv2.imwrite(save_background, background):
            raise OfflineTrackError(f"could not write background: {save_background}")
        notes.append(f"background saved to {os.path.abspath(save_background)}")
    install_background(tracker, background)

    reg_info: Optional[Dict[str, Any]] = None
    if registration is not None and registration.any:
        settings, reg_info = _registered(tracker, settings, registration, notes)
    return (tracker, settings, params, sources, notes, reg_info,
            sidecar_header, sidecar_frames)


def register_only(settings: OT.OfflineSettings, registration: OT.RegistrationRequest,
                  *, save_background: Optional[str] = None) -> Dict[str, Any]:
    """Build (and save) the requested models without tracking the video."""
    _, settings, _, _, notes, reg_info, _, _ = prepare(
        settings, save_background=save_background, registration=registration)
    return {"registration": reg_info, "notes": notes,
            "model": settings.model.to_dict() if settings.model else None,
            "blocker_model": (settings.blocker_model.to_dict()
                              if settings.blocker_model else None)}


def run(settings: OT.OfflineSettings, *, csv_path: str, json_path: str,
        tag: Optional[str] = None, start_frame: int = 0,
        end_frame: Optional[int] = None, warmup_frames: int = 0,
        save_background: Optional[str] = None,
        registration: Optional[OT.RegistrationRequest] = None,
        progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Track one bundle's video and write the track CSV + summary JSON.

    The CSV is written to a temporary name and renamed only when the run
    finishes, so an interrupted run never leaves a file that looks complete.
    Returns the summary dict.
    """
    first_processed, first_written, last_written = OT.frame_range(
        start_frame, end_frame, warmup_frames)
    (tracker, settings, params, sources, notes, reg_info,
     sidecar_header, sidecar_frames) = prepare(
        settings, save_background=save_background, registration=registration)

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
        notes=notes, registration=reg_info)
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
