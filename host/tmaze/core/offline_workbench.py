"""
offline_workbench.py - pure decisions behind the offline tracker workbench.

The workbench window (``tmaze.ui.offline_workbench``, FW-0074) plays a recorded
session video with the tracker running on it. Everything here is the part that
needs no pixels or Tk, so it can be tested on its own:

- where to start tracking after a jump (warm-up), and frame stepping inside an
  A-B loop;
- the frames and frame rate of a presentation clip saved from the A-B range;
- trial markers: each trial's start / choice / reward / end time from the trial
  log, mapped onto video frame numbers through the sidecar's ``host_unix_s``;
- the settings table (which layer set each setting, or "you" for a change made
  in the window) and the ``--set`` values that reproduce the window's settings;
- the equivalent ``offline_track`` command line, so a result from the window can
  be repeated or batch-run;
- reading a recorded track (the live sidecar or an earlier offline run) for the
  comparison "ghosts", and the distance between two points.

Stdlib only. See docs/decisions/0211 (offline tracker), 0213 (workbench hosts the
Tracking tab), 0214 (settings, warm-up, command-line equivalence).
"""
from __future__ import annotations

import bisect
import csv
import glob
import math
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tmaze.core import offline_track as OT

DEFAULT_WARMUP_FRAMES = 150     # ~4-6 s of video: enough for lock-on + smoothing
# Playback speeds offered (multiples of the recorded frame rate); 0 = as fast as
# the tracker can go.
SPEEDS: Tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0, 0.0)
FLOAT_DECIMALS = 9              # settings equal to this many decimals are "the same"

# Extra file origins the workbench shows besides the offline_track SRC_* ones.
SRC_EDITED = "edited"           # drawn/changed in the window and not saved
SRC_NONE = "none"               # deliberately off (blocker model)

LAYER_YOU = "you"               # changed in the window
LAYER_MODEL = "model/default"   # the model's saved setting, or the default

# Seconds a trial event may fall outside the video and still get a frame.
_TIME_SLACK_S = 1.0


# ------------------------------------------------------------------ frames
def warmup_start(target: int, warmup: int) -> int:
    """First frame to track so ``target`` is shown with ``warmup`` frames of
    tracking history behind it."""
    return max(0, int(target) - max(0, int(warmup)))


def clamp_frame(frame: int, n_frames: int) -> int:
    return max(0, min(int(frame), max(0, n_frames - 1)))


@dataclass(frozen=True)
class Marks:
    """Frames marked on the scrub bar: an A-B range (loop playback, and the rat
    registration window) and single blocker-sample frames."""
    a: Optional[int] = None
    b: Optional[int] = None
    blocker_frames: Tuple[int, ...] = ()

    def ab(self) -> Optional[Tuple[int, int]]:
        """The A-B range in order, or None until both ends are set."""
        if self.a is None or self.b is None:
            return None
        return (min(self.a, self.b), max(self.a, self.b))

    def toggle_blocker(self, frame: int) -> "Marks":
        frames = set(self.blocker_frames)
        frames.symmetric_difference_update({int(frame)})
        return Marks(self.a, self.b, tuple(sorted(frames)))


def next_frame(current: int, step: int, n_frames: int, marks: Marks = Marks(),
               loop: bool = False) -> int:
    """``current + step``, kept inside the video; with ``loop`` and a full A-B
    range, wrapping from past B back to A (and from before A to B)."""
    target = int(current) + int(step)
    ab = marks.ab() if loop else None
    if ab is not None:
        lo, hi = ab
        if target > hi:
            target = lo
        elif target < lo:
            target = hi
    return clamp_frame(target, n_frames)


# ------------------------------------------------------------------- clips
# Clip speeds offered (multiples of the recorded frame rate). A clip faster than
# MAX_CLIP_FPS keeps every n-th frame instead: many players, PowerPoint among
# them, stutter or drop frames above 60 fps (see docs/decisions/0218).
CLIP_SPEEDS: Tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0)
MAX_CLIP_FPS = 60


@dataclass(frozen=True)
class ClipPlan:
    """Which frames a presentation clip tracks and writes, and at what rate."""
    first: int                 # first frame written (A)
    last: int                  # last frame that may be written (B)
    track_from: int            # first frame tracked (warm-up before A)
    step: int                  # write every step-th frame from A
    rate: Fraction             # frames per second of the saved file

    def written(self) -> range:
        return range(self.first, self.last + 1, self.step)


def clip_plan(a: int, b: int, *, fps: float, speed: float, warmup: int) -> ClipPlan:
    """Plan a clip of frames ``a..b`` played at ``speed`` x the recording's
    ``fps``, tracked from ``warmup`` frames before ``a`` so the overlay is
    already locked on at the first frame."""
    if fps <= 0:
        raise ValueError("the video's frame rate is unknown")
    if speed <= 0:
        raise ValueError("pick a clip speed (max is not a speed)")
    first, last = min(int(a), int(b)), max(int(a), int(b))
    rate = Fraction(fps).limit_denominator(1001) * Fraction(speed).limit_denominator(100)
    step = max(1, math.ceil(rate / MAX_CLIP_FPS))
    return ClipPlan(first, last, warmup_start(first, warmup), step, rate / step)


def clip_name(session_id: str, a: int, b: int, speed: float) -> str:
    """Suggested file name, e.g. ``600_20260714_131656_clip_1200-1650_0.5x.mp4``."""
    suffix = "" if speed == 1 else f"_{speed:g}x"
    return f"{session_id}_clip_{min(a, b)}-{max(a, b)}{suffix}.mp4"


# ------------------------------------------------------------------ trials
def iso_to_unix(value: Optional[str]) -> Optional[float]:
    """Trial-log ISO stamp -> POSIX seconds (matches the sidecar's ``host_unix_s``).

    Offset-aware stamps (format v4+, ADR-0182) are exact anywhere. Older naive
    stamps are read in this computer's time zone, which is right on the rig PC.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).strip()).timestamp()
    except ValueError:
        return None


def frame_at_time(frame_times: Sequence[float], t: Optional[float]) -> Optional[int]:
    """Index of the frame whose time is nearest ``t`` (``frame_times`` ascending),
    or None when ``t`` is missing or clearly outside the video."""
    if t is None or not frame_times:
        return None
    if t < frame_times[0] - _TIME_SLACK_S or t > frame_times[-1] + _TIME_SLACK_S:
        return None
    i = bisect.bisect_left(frame_times, t)
    if i <= 0:
        return 0
    if i >= len(frame_times):
        return len(frame_times) - 1
    return i if frame_times[i] - t < t - frame_times[i - 1] else i - 1


@dataclass(frozen=True)
class TrialMarker:
    trial: int
    start: int                       # frame of trial_start
    choice: Optional[int] = None
    reward: Optional[int] = None
    end: Optional[int] = None
    outcome: Optional[str] = None
    choice_cue: Optional[str] = None
    chosen_arm: Optional[str] = None


def trial_markers(rows: Sequence[Any], frame_times: Sequence[float]) -> List[TrialMarker]:
    """One marker per trial row (``Phase3RowView``-like: ``trial`` + ``*_iso``
    fields) whose start falls inside the video, in trial order."""
    out: List[TrialMarker] = []
    for r in rows:
        start = frame_at_time(frame_times, iso_to_unix(getattr(r, "trial_start_iso", None)))
        if start is None:
            continue

        def at(field: str) -> Optional[int]:
            return frame_at_time(frame_times, iso_to_unix(getattr(r, field, None)))

        out.append(TrialMarker(
            trial=int(r.trial), start=start, choice=at("choice_time_iso"),
            reward=at("reward_cmd_iso"), end=at("trial_end_iso"),
            outcome=getattr(r, "outcome", None) or None,
            choice_cue=getattr(r, "choice_cue", None) or None,
            chosen_arm=getattr(r, "chosen_arm", None) or None))
    return sorted(out, key=lambda m: m.trial)


def trial_at(markers: Sequence[TrialMarker], frame: int) -> Optional[TrialMarker]:
    """The trial in progress at ``frame`` (the last one started at or before it)."""
    starts = [m.start for m in markers]
    i = bisect.bisect_right(starts, frame) - 1
    return markers[i] if i >= 0 else None


def trial_frame(markers: Sequence[TrialMarker], trial: int) -> Optional[int]:
    for m in markers:
        if m.trial == int(trial):
            return m.start
    return None


# ---------------------------------------------------------------- settings
def _canon(v: Any) -> Any:
    return round(v, FLOAT_DECIMALS) if isinstance(v, float) else v


def format_value(v: Any) -> str:
    """A setting as ``--set`` text that parses back to the same value."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(_canon(v))
    return str(v)


def changed_settings(base: Mapping[str, Any], current: Mapping[str, Any]
                     ) -> Dict[str, Any]:
    """Settings in ``current`` that differ from ``base`` (floats compared to
    ``FLOAT_DECIMALS`` places, so slider rounding noise is not a change)."""
    return {k: _canon(v) for k, v in current.items()
            if k in base and _canon(v) != _canon(base[k])}


def set_args(base: Mapping[str, Any], current: Mapping[str, Any]) -> Tuple[str, ...]:
    """``("thresh=25", ...)`` - the ``--set`` values that turn ``base`` into
    ``current``, sorted by name."""
    return tuple(f"{k}={format_value(v)}"
                 for k, v in sorted(changed_settings(base, current).items()))


def settings_table(current: Mapping[str, Any], base: Mapping[str, Any],
                   sources: Mapping[str, str]) -> List[Tuple[str, Any, str]]:
    """``(name, value, layer)`` per setting, sorted by name. ``layer`` is
    ``"you"`` for a change made in the window, else the layer that set it
    (snapshot / params_file / set) or ``"model/default"``."""
    changed = changed_settings(base, current)
    return [(k, current[k], LAYER_YOU if k in changed else sources.get(k, LAYER_MODEL))
            for k in sorted(current)]


# ------------------------------------------------------------ command line
@dataclass(frozen=True)
class WorkbenchFiles:
    """The files the window is using now (``FileChoice.source`` may also be
    ``SRC_EDITED``; ``blocker_model`` None = off)."""
    model: Optional[OT.FileChoice]
    blocker_model: Optional[OT.FileChoice]
    roi: Optional[OT.FileChoice]
    landmarks: Optional[OT.FileChoice]
    background: OT.FileChoice


def _same_file(a: Optional[OT.FileChoice], b: Optional[OT.FileChoice]) -> bool:
    if a is None or b is None:
        return a is b
    return a.path is not None and a.path == b.path


def cli_args(defaults: OT.OfflineSettings, files: WorkbenchFiles,
             sets: Sequence[str], *, tag: Optional[str] = None,
             start_frame: int = 0, end_frame: Optional[int] = None,
             warmup_frames: int = 0,
             registration: Optional[OT.RegistrationRequest] = None
             ) -> Tuple[List[str], List[str]]:
    """``(argv, warnings)`` for ``python -m tmaze.tools.offline_track`` that
    reproduces the window's setup. ``defaults`` is what the command line picks
    with no flags. A file that only exists in memory cannot be passed: a model
    built from marked frames is rebuilt with ``registration``'s frames, anything
    else drawn and not saved is listed in ``warnings``."""
    argv: List[str] = [defaults.bundle]
    warnings: List[str] = []
    reg = registration or OT.RegistrationRequest()

    def file_flag(name: str, flag: str, now: Optional[OT.FileChoice],
                  default: Optional[OT.FileChoice]) -> None:
        if _same_file(now, default) or (now is None and default is None):
            return
        if now is None:
            warnings.append(f"{name}: cleared in the window; the command line "
                            f"would use {os.path.basename(default.path or '')}")
        elif now.path is None:
            warnings.append(f"{name}: changed in the window and not saved - save it "
                            "to include it")
        else:
            argv.extend([flag, now.path])

    # Rat model
    if files.model is not None and files.model.source == OT.SRC_REGISTERED \
            and files.model.path is None:
        if reg.rat_frames is not None:
            argv.extend(["--register-frames", str(reg.rat_frames[0]),
                         str(reg.rat_frames[1])])
        else:
            warnings.append("rat model: built in the window and not saved - save it "
                            "to include it")
    else:
        file_flag("rat model", "--model", files.model, defaults.model)

    # Blocker model
    if files.blocker_model is None:
        if defaults.blocker_model is not None:
            argv.append("--no-blocker")
    elif files.blocker_model.source == OT.SRC_REGISTERED and files.blocker_model.path is None:
        if reg.blocker_frames:
            argv.append("--blocker-frames")
            argv.extend(str(f) for f in reg.blocker_frames)
        else:
            warnings.append("blocker model: built in the window and not saved - save "
                            "it to include it")
    else:
        file_flag("blocker model", "--blocker-model", files.blocker_model,
                  defaults.blocker_model)

    file_flag("ROI", "--roi", files.roi, defaults.roi)
    file_flag("landmarks", "--landmarks", files.landmarks, defaults.landmarks)

    # Background
    bg, dbg = files.background, defaults.background
    if bg.source == OT.SRC_COMPUTED:
        if dbg.source != OT.SRC_COMPUTED:
            argv.append("--background-from-video")
    elif not _same_file(bg, dbg):
        if bg.path is None:
            warnings.append("background: changed in the window and not saved - save "
                            "it to include it")
        else:
            argv.extend(["--background", bg.path])

    for s in sets:
        argv.extend(["--set", s])
    if tag:
        argv.extend(["--tag", OT.validate_tag(tag)])
    OT.frame_range(start_frame, end_frame, warmup_frames)
    if start_frame:
        argv.extend(["--start-frame", str(start_frame)])
    if end_frame is not None:
        argv.extend(["--end-frame", str(end_frame)])
    if warmup_frames:
        argv.extend(["--warmup-frames", str(warmup_frames)])
    return argv, warnings


def format_command(argv: Sequence[str]) -> str:
    """The full command, quoted for a Windows prompt / PowerShell."""
    return subprocess.list2cmdline(["python", "-m", "tmaze.tools.offline_track", *argv])


def run_model_paths(out_dir: str, session_id: str, tag: Optional[str]) -> Tuple[str, str]:
    """Where a run started from the window saves models that only existed in
    memory, beside its outputs: ``<id>_offline_track[_<tag>]_model.npz`` and
    ``..._blockermodel.npz``."""
    csv_path, _ = OT.output_paths(out_dir, session_id, tag)
    stem = csv_path[:-len(".csv")]
    return stem + "_model.npz", stem + "_blockermodel.npz"


# ------------------------------------------------------------------ ghosts
@dataclass(frozen=True)
class TrackPoint:
    state: str
    x: Optional[float]               # smoothed centroid, full-frame px
    y: Optional[float]


def _float(s: Optional[str]) -> Optional[float]:
    try:
        v = float(s) if s not in (None, "") else None
    except ValueError:
        return None
    return v if v is not None and math.isfinite(v) else None


def read_track_points(path: str) -> Dict[int, TrackPoint]:
    """``frame_idx -> TrackPoint`` from a live sidecar or an offline track CSV
    (``# key: value`` header lines skipped)."""
    out: Dict[int, TrackPoint] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        for i, row in enumerate(rows):
            try:
                idx = int(row.get("frame_idx") or i)
            except ValueError:
                idx = i
            out[idx] = TrackPoint(state=row.get("track_state") or "",
                                  x=_float(row.get("track_smooth_cx")),
                                  y=_float(row.get("track_smooth_cy")))
    return out


def offline_runs(bundle: str) -> List[Optional[str]]:
    """Tags of the offline runs in a bundle (``None`` = untagged), untagged first."""
    sid = OT.session_id_of(bundle)
    prefix = f"{sid}_{OT.OUTPUT_STEM}"
    tags: List[Optional[str]] = []
    for p in glob.glob(os.path.join(bundle, "video", prefix + "*.csv")):
        rest = os.path.basename(p)[len(prefix):-len(".csv")]
        if rest == "":
            tags.append(None)
        elif rest.startswith("_"):
            tags.append(rest[1:])
    return sorted(tags, key=lambda t: (t is not None, t or ""))


def distance(a: Optional[TrackPoint], b: Optional[TrackPoint]) -> Optional[float]:
    """Pixel distance between two tracked points, None if either is missing."""
    if a is None or b is None or None in (a.x, a.y, b.x, b.y):
        return None
    return math.hypot(a.x - b.x, a.y - b.y)
