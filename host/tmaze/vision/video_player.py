"""
video_player.py - play a recorded session video through the tracker, for the
offline workbench (FW-0074).

``VideoPlayer`` decodes the video on a worker thread and, in "track" mode, runs
``RatTracker.process()`` on every frame it shows, the way ``camera_view`` does on
the rig. It also answers the camera-like calls the live Tracking tab makes
(``get_latest_frame``, ``tracking_enabled``, ``enable_tracking``,
``get_output_dir``), so the workbench can host that tab unchanged (ADR-0213).

Jumps re-track a warm-up stretch before the target so the frame shows what a
continuous run would (ADR-0214); stepping forward a little just keeps tracking.
Everything that changes the tracker's frame history (jumps, re-tracks, model
building, a new background) runs on the worker, one at a time, so it never
races the frame loop.

``RunJob`` runs a whole-video (or range) offline track in the background with a
progress readout and Cancel, using the same settings path as the command line;
:func:`prepare_run` saves any model that exists only in memory beside the run's
outputs first. See docs/decisions/0215. Needs ``opencv-python`` + ``numpy``.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from tmaze.core import offline_track as OT
from tmaze.core import offline_workbench as WB
from tmaze.core.session.session_config import sha256_file
from tmaze.vision import offline_tracker as OTR
from tmaze.vision.tracker import RatTracker, cv2

MODE_TRACK = "track"
MODE_VIEW = "view"         # show the recorded video only, no tracking
_SEEK_FORWARD_MAX = 300    # a forward jump this short is decoded, not seeked
_IDLE_WAIT_S = 0.05


@dataclass(frozen=True)
class Shown:
    """The frame on screen and what the tracker made of it."""
    idx: int = -1
    frame: Any = None
    result: Any = None            # TrackResult, or None in view mode


class _Decoder:
    """``cv2.VideoCapture`` that knows which frame it will read next."""

    def __init__(self, video: str):
        self._cap = OTR._open(video)
        self.next_idx = 0
        self.n_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)

    def seek(self, idx: int) -> None:
        idx = max(0, int(idx))
        if idx == self.next_idx:
            return
        if self.next_idx < idx <= self.next_idx + _SEEK_FORWARD_MAX:
            while self.next_idx < idx and self._cap.grab():
                self.next_idx += 1
            return
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        self.next_idx = idx

    def read(self) -> Optional[Any]:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        self.next_idx += 1
        return frame

    def release(self) -> None:
        self._cap.release()


class VideoPlayer:
    """Threaded play / pause / seek / step over one video, feeding ``tracker``.

    All public methods are safe to call from the Tk thread; they only queue a
    request for the worker. Read :meth:`shown` (or the camera-like methods) to
    draw the current frame.
    """

    def __init__(self, video: str, tracker: RatTracker, *, n_frames: int = 0,
                 fps: float = 0.0, output_dir: Optional[str] = None,
                 warmup: int = WB.DEFAULT_WARMUP_FRAMES):
        self._dec = _Decoder(video)
        self.video = video
        self.tracker = tracker
        self.n_frames = int(n_frames or self._dec.n_frames)
        self.fps = float(fps or self._dec.fps or 30.0)
        self._output_dir = output_dir or os.path.dirname(os.path.abspath(video))
        self._cv = threading.Condition()
        self._shown = Shown()
        self._playing = False
        self._speed = 1.0
        self._mode = MODE_TRACK
        self._warmup = int(warmup)
        self._marks = WB.Marks()
        self._loop = False
        self._seek: Optional[int] = None
        self._step = 0
        self._retrack = False
        self._process_current = False
        self._jobs: List[Tuple[str, Callable[[], Any]]] = []
        self._busy: Optional[str] = None
        self._last_error: Optional[str] = None
        self._job_results: Dict[str, Any] = {}
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="video-player", daemon=True)
        self._thread.start()
        self.seek(0)

    # -------------------------------------------------------- camera-like API
    def get_latest_frame(self):
        return self._shown.frame

    def tracking_enabled(self) -> bool:
        return self._mode == MODE_TRACK

    def enable_tracking(self, on: bool) -> None:
        self.set_mode(MODE_TRACK if on else MODE_VIEW)

    def get_output_dir(self) -> str:
        return self._output_dir

    # ------------------------------------------------------------- state reads
    def shown(self) -> Shown:
        return self._shown

    def playing(self) -> bool:
        return self._playing

    def busy(self) -> Optional[str]:
        """What the worker is doing that blocks playback (e.g. "building rat
        model"), or None."""
        return self._busy

    def take_error(self) -> Optional[str]:
        with self._cv:
            err, self._last_error = self._last_error, None
        return err

    def take_job_result(self, name: str) -> Any:
        with self._cv:
            return self._job_results.pop(name, None)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def warmup(self) -> int:
        return self._warmup

    # ---------------------------------------------------------------- commands
    def _request(self, **kw) -> None:
        with self._cv:
            for k, v in kw.items():
                setattr(self, k, v)
            self._cv.notify_all()

    def play(self) -> None:
        self._request(_playing=True)

    def pause(self) -> None:
        self._request(_playing=False)

    def toggle_play(self) -> None:
        self._request(_playing=not self._playing)

    def seek(self, idx: int) -> None:
        """Show frame ``idx`` (re-tracking the warm-up before it)."""
        self._request(_seek=WB.clamp_frame(idx, self.n_frames or idx + 1), _step=0)

    def step(self, n: int) -> None:
        """Move ``n`` frames (pauses playback)."""
        with self._cv:
            self._playing = False
            self._step += int(n)
            self._cv.notify_all()

    def retrack(self) -> None:
        """Re-track the warm-up up to the shown frame (after a setting change)."""
        self._request(_retrack=True)

    def process_current(self) -> None:
        """Run the tracker once more on the shown frame (e.g. so a blocker
        sample is captured from it while paused)."""
        self._request(_process_current=True)

    def set_speed(self, speed: float) -> None:
        self._request(_speed=max(0.0, float(speed)))

    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_TRACK, MODE_VIEW):
            raise ValueError(f"mode must be {MODE_TRACK!r} or {MODE_VIEW!r}")
        self._request(_mode=mode, _retrack=True)

    def set_warmup(self, frames: int) -> None:
        self._request(_warmup=max(0, int(frames)))

    def set_loop(self, marks: WB.Marks, on: bool) -> None:
        self._request(_marks=marks, _loop=bool(on))

    def reset_tracker(self) -> None:
        """Drop the current lock (Kalman + trail), like the live Reset."""
        self.tracker.reset()

    def submit(self, name: str, fn: Callable[[], Any], *, retrack: bool = True) -> None:
        """Run ``fn`` on the worker between frames (it may use the tracker
        freely); its return value is kept for :meth:`take_job_result`. With
        ``retrack`` the shown frame is re-tracked afterwards."""
        def job():
            out = fn()
            if retrack:
                self._retrack = True
            return out
        with self._cv:
            self._jobs.append((name, job))
            self._cv.notify_all()

    def install_background(self, background_bgr) -> None:
        self.submit("background", lambda: OTR.install_background(self.tracker, background_bgr))

    def register(self, rat_range: Optional[Tuple[int, int]] = None,
                 blocker_frames: Tuple[int, ...] = ()) -> None:
        """Build models from marked frames (blocker first, then rat - ADR-0212);
        the result dict is available as job ``"register"``."""
        def job():
            info: Dict[str, Any] = {}
            if blocker_frames:
                info["blocker"] = OTR.register_blocker(self.tracker, self.video,
                                                       blocker_frames)
            if rat_range is not None:
                info["rat"] = OTR.register_model(self.tracker, self.video, *rat_range)
            self.tracker.reset()
            return info
        self.submit("register", job)

    def close(self) -> None:
        self._request(_stop=True)
        self._thread.join(timeout=2.0)
        self._dec.release()

    # ------------------------------------------------------------------ worker
    def _publish(self, idx: int, frame, result) -> None:
        self._shown = Shown(idx, frame, result)

    def _track(self, frame):
        return self.tracker.process(frame) if self._mode == MODE_TRACK else None

    def _show_from(self, start: int, target: int) -> bool:
        """Decode (and track) ``start..target``; show ``target``."""
        self._dec.seek(start)
        frame = result = None
        idx = start
        while idx <= target:
            frame = self._dec.read()
            if frame is None:
                if self.n_frames == 0 or idx < self.n_frames:
                    self.n_frames = idx
                break
            result = self._track(frame)
            idx += 1
        if frame is None and idx == start:
            return False
        self._publish(idx - 1, frame, result)
        return True

    def _jump(self, target: int) -> None:
        if self._mode == MODE_TRACK:
            self.tracker.reset()
            start = WB.warmup_start(target, self._warmup)
        else:
            start = target
        self._show_from(start, target)

    def _advance(self, n: int) -> bool:
        """Show ``n`` frames ahead by continuing to track (no warm-up)."""
        cur = self._shown.idx
        target = cur + n
        if self.n_frames:
            target = min(target, self.n_frames - 1)
        if target <= cur:
            return False
        return self._show_from(cur + 1, target)

    def _run(self) -> None:
        next_due = time.monotonic()
        while True:
            with self._cv:
                while not (self._stop or self._jobs or self._seek is not None
                           or self._step or self._retrack or self._process_current
                           or self._playing):
                    self._cv.wait(_IDLE_WAIT_S)
                if self._stop:
                    return
                job = self._jobs.pop(0) if self._jobs else None
                seek, self._seek = self._seek, None
                step, self._step = self._step, 0
                retrack, self._retrack = self._retrack, False
                once, self._process_current = self._process_current, False
                playing = self._playing
            try:
                if job is not None:
                    name, fn = job
                    self._busy = name
                    try:
                        out = fn()
                        with self._cv:
                            self._job_results[name] = out
                    finally:
                        self._busy = None
                    continue
                cur = self._shown.idx
                if seek is not None:
                    self._jump(seek)
                elif step:
                    target = WB.clamp_frame(cur + step, self.n_frames or cur + step + 1)
                    if 0 < target - cur <= self._warmup:
                        self._advance(target - cur)
                    elif target != cur:
                        self._jump(target)
                elif retrack:
                    self._jump(max(0, cur))
                elif once and self._shown.frame is not None:
                    self._publish(cur, self._shown.frame, self._track(self._shown.frame))
                elif playing:
                    wait = next_due - time.monotonic()
                    if wait > 0:
                        time.sleep(min(wait, _IDLE_WAIT_S))
                        continue
                    nxt = WB.next_frame(cur, 1, self.n_frames or cur + 2, self._marks,
                                        self._loop)
                    if nxt == cur + 1:
                        if not self._advance(1):
                            self._playing = False
                    elif nxt == cur:
                        self._playing = False          # end of the video
                    else:
                        self._jump(nxt)                # loop wrap-around
                    period = 0.0 if self._speed <= 0 else 1.0 / (self.fps * self._speed)
                    next_due = max(next_due + period, time.monotonic() - period)
                    continue
                next_due = time.monotonic()
            except Exception as e:  # keep the worker alive; the window shows it
                with self._cv:
                    self._last_error = f"{type(e).__name__}: {e}"
                    self._playing = False


# ------------------------------------------------------------- settings + runs
def current_params(tracker: RatTracker) -> Dict[str, Any]:
    return asdict(tracker.get_params())


def base_params(model_path: Optional[str], defaults: OT.OfflineSettings) -> Dict[str, Any]:
    """The settings the command line starts from for a run with this rat model:
    the model's saved settings (or the defaults), then the session snapshot. The
    window's changes are expressed relative to this (ADR-0214)."""
    t = RatTracker()
    if model_path:
        t.load_model(model_path, restore=True)
    params, _ = OT.merge_params(asdict(t.get_params()),
                                replace(defaults, file_params={}, set_params=()))
    return params


def _saved(tracker_save: Callable[[str], bool], path: str) -> OT.FileChoice:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if not tracker_save(path):
        raise OTR.OfflineTrackError(f"could not save model: {path}")
    return OT.FileChoice(os.path.abspath(path), OT.SRC_REGISTERED, sha256_file(path))


def prepare_run(defaults: OT.OfflineSettings, files: WB.WorkbenchFiles,
                tracker: RatTracker, *, out_dir: str, tag: Optional[str]
                ) -> Tuple[OT.OfflineSettings, Tuple[str, ...]]:
    """Settings for a run of the window's current setup, and its ``--set``
    values. Models that only exist in memory are saved beside the outputs
    first (``offline_workbench.run_model_paths``). Raises ``ValueError`` for an
    ROI / landmarks / background edited and not saved."""
    for name, choice in (("ROI", files.roi), ("landmarks", files.landmarks),
                         ("background", files.background)):
        if choice is not None and choice.path is None and choice.source == WB.SRC_EDITED:
            raise ValueError(f"{name} was changed in the window and not saved - save it first")
    model_path, blocker_path = WB.run_model_paths(out_dir, defaults.session_id, tag)
    model, blocker = files.model, files.blocker_model
    if model is not None and model.path is None:
        model = _saved(tracker.save_model, model_path)
    if blocker is not None and blocker.path is None:
        blocker = _saved(tracker.save_blocker_model, blocker_path)
    sets = WB.set_args(base_params(model.path if model else None, defaults),
                       current_params(tracker))
    settings = replace(defaults, model=model, blocker_model=blocker, roi=files.roi,
                       landmarks=files.landmarks, background=files.background,
                       file_params={}, set_params=sets)
    return settings, sets


class RunCancelled(Exception):
    pass


class RunJob:
    """One offline run on a background thread, with progress and Cancel.

    The run uses its own tracker (built from ``settings``), so the window keeps
    playing while it works. Read :attr:`progress`, :attr:`summary`,
    :attr:`error`; :meth:`done` is True when it has finished either way.
    """

    def __init__(self, settings: OT.OfflineSettings, *, csv_path: str, json_path: str,
                 tag: Optional[str], start_frame: int = 0,
                 end_frame: Optional[int] = None, warmup_frames: int = 0):
        self.csv_path, self.json_path = csv_path, json_path
        self.progress: Tuple[int, Optional[int]] = (0, end_frame)
        self.summary: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.cancelled = False
        self._cancel = threading.Event()
        self._args = dict(csv_path=csv_path, json_path=json_path, tag=tag,
                          start_frame=start_frame, end_frame=end_frame,
                          warmup_frames=warmup_frames)
        self._settings = settings
        self._thread = threading.Thread(target=self._run, name="offline-run", daemon=True)
        self._thread.start()

    def _on_progress(self, idx: int, last: Optional[int]) -> None:
        self.progress = (idx, last)
        if self._cancel.is_set():
            raise RunCancelled()

    def _run(self) -> None:
        try:
            self.summary = OTR.run(self._settings, progress=self._on_progress, **self._args)
        except RunCancelled:
            self.cancelled = True
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"

    def cancel(self) -> None:
        self._cancel.set()

    def done(self) -> bool:
        return not self._thread.is_alive()

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout)
