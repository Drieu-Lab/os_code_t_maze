"""
offline_track.py - pure decisions for re-tracking a recorded session video.

The offline tracker (``tmaze.vision.offline_tracker`` + the CLI
``tmaze.tools.offline_track``) re-runs the live ``RatTracker`` over a session's
recorded video, every frame, and writes a NEW track file beside the video. This
module holds the parts that need no OpenCV, so they are headless-tested
(``test_offline_track.py``):

  - which files a run uses (model, blocker model, ROI, landmarks, background) and
    where each came from - the bundle's ``config/`` snapshot, an explicit
    override, the current default model, or "compute from the video";
  - how the tracker settings are layered (model/defaults -> the session's
    snapshot -> a ``--params-file`` -> ``--set key=value``), with unknown keys
    rejected;
  - which frames feed the median background, the output file names, and the
    per-run summary counts.

By default a run REPLAYS the live setup: the session's own snapshot settings and
model, including model enrichment if it was on. See docs/decisions/0211.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tmaze.core.session.session_config import sha256_file

TOOL_VERSION = 1

# The CURRENT models, the only ones chosen automatically (older ones are being
# archived). One shared rat model covers rats 600/601/602; bundles never snapshot
# the blocker model, so it is always this default unless overridden. Both live in
# host/ratmodels/. Change these when the current models change.
FALLBACK_MODEL_NAME = "animal_ratmodel_messed_up_lamp_settings.npz"
FALLBACK_BLOCKER_MODEL_NAME = "animal_blockermodel.npz"

# Frames sampled (evenly across the video) for the per-pixel median background
# used when a bundle has no background PNG. The rat sits in any one spot for only
# a small share of them, so the median is the empty rig. See docs/decisions/0211.
BACKGROUND_SAMPLES = 50

OUTPUT_STEM = "offline_track"
_TAG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Where each chosen file came from.
SRC_BUNDLE = "bundle"
SRC_OVERRIDE = "override"
SRC_FALLBACK = "fallback"
SRC_COMPUTED = "computed"

# Where each tracker setting came from (keys not listed keep the model's saved
# setting, or the TrackerParams default when the model has none).
PSRC_SNAPSHOT = "snapshot"
PSRC_PARAMS_FILE = "params_file"
PSRC_SET = "set"


@dataclass(frozen=True)
class FileChoice:
    """One input file a run uses, with its origin and content hash."""
    path: Optional[str]          # None for a computed background
    source: str                  # SRC_*
    sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "source": self.source, "sha256": self.sha256}


@dataclass(frozen=True)
class Overrides:
    """Explicit choices from the command line (None = use the default rule)."""
    model: Optional[str] = None
    blocker_model: Optional[str] = None
    no_blocker: bool = False
    roi: Optional[str] = None
    landmarks: Optional[str] = None
    background: Optional[str] = None
    background_from_video: bool = False
    params_file: Optional[str] = None
    sets: Tuple[str, ...] = ()


@dataclass
class OfflineSettings:
    """Everything one offline run of one bundle uses."""
    bundle: str
    session_id: str
    video: str
    sidecar: Optional[str]
    model: Optional[FileChoice]
    blocker_model: Optional[FileChoice]
    roi: Optional[FileChoice]
    landmarks: Optional[FileChoice]
    background: FileChoice
    snapshot_params: Dict[str, Any] = field(default_factory=dict)
    file_params: Dict[str, Any] = field(default_factory=dict)
    set_params: Tuple[str, ...] = ()
    notes: List[str] = field(default_factory=list)


# ------------------------------------------------------------------ bundle files
def session_id_of(bundle: str) -> str:
    return os.path.basename(os.path.normpath(bundle))


def find_video(bundle: str, session_id: str) -> Optional[str]:
    for ext in (".mp4", ".mkv"):
        p = os.path.join(bundle, "video", session_id + ext)
        if os.path.isfile(p):
            return p
    return None


def is_bundle(path: str) -> bool:
    """A session bundle = a folder holding ``video/<id>.mp4|.mkv``."""
    return os.path.isdir(path) and find_video(path, session_id_of(path)) is not None


def find_bundles(root: str) -> List[str]:
    """Bundles directly under ``root`` (sorted), or ``[root]`` if it is one."""
    if is_bundle(root):
        return [root]
    if not os.path.isdir(root):
        return []
    return [os.path.join(root, d) for d in sorted(os.listdir(root))
            if is_bundle(os.path.join(root, d))]


def _choice(path: str, source: str) -> FileChoice:
    return FileChoice(os.path.abspath(path), source, sha256_file(path))


def _require_file(path: str, what: str) -> None:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{what} not found: {path}")


def _snapshot_model(config_dir: str) -> Optional[str]:
    """The rat model the session snapshotted (``config/model_<name>.npz``)."""
    if not os.path.isdir(config_dir):
        return None
    names = sorted(n for n in os.listdir(config_dir)
                   if n.startswith("model_") and n.endswith(".npz"))
    return os.path.join(config_dir, names[0]) if names else None


def read_sidecar_header(path: str) -> Dict[str, str]:
    """The leading ``# key: value`` lines of a video sidecar."""
    header: Dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            body = line[1:].strip()
            if ":" in body:
                k, v = body.split(":", 1)
                header[k.strip()] = v.strip()
    return header


def _snapshot_tracker_params(config_dir: str) -> Dict[str, Any]:
    path = os.path.join(config_dir, "runtime_params.json")
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    tracker = data.get("tracker") if isinstance(data, dict) else None
    return dict(tracker) if isinstance(tracker, dict) else {}


def load_params_file(path: str) -> Dict[str, Any]:
    """A JSON settings file: either a flat ``{key: value}`` dict or a copy of a
    bundle's ``runtime_params.json`` (its ``"tracker"`` section is used)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"params file must hold a JSON object: {path}")
    if isinstance(data.get("tracker"), dict):
        data = data["tracker"]
    return dict(data)


def resolve_settings(bundle: str, overrides: Overrides = Overrides(), *,
                     ratmodels_dir: str) -> OfflineSettings:
    """Pick every input file and settings layer for one bundle.

    Raises ``FileNotFoundError`` when the bundle has no video or an explicitly
    named file is missing; a missing DEFAULT (no snapshot model, no background
    PNG, ...) falls back by rule and is noted instead.
    """
    sid = session_id_of(bundle)
    video = find_video(bundle, sid)
    if video is None:
        raise FileNotFoundError(f"no video ({sid}.mp4/.mkv) under "
                                f"{os.path.join(bundle, 'video')}")
    config_dir = os.path.join(bundle, "config")
    sidecar = os.path.join(bundle, "video", sid + ".csv")
    sidecar = sidecar if os.path.isfile(sidecar) else None
    notes: List[str] = []
    if sidecar is None:
        notes.append("no video sidecar CSV: time columns will be blank")

    # Rat model: override -> session snapshot -> current default -> none.
    model: Optional[FileChoice]
    if overrides.model:
        _require_file(overrides.model, "--model")
        model = _choice(overrides.model, SRC_OVERRIDE)
    else:
        snap = _snapshot_model(config_dir)
        fallback = os.path.join(ratmodels_dir, FALLBACK_MODEL_NAME)
        if snap:
            model = _choice(snap, SRC_BUNDLE)
        elif os.path.isfile(fallback):
            model = _choice(fallback, SRC_FALLBACK)
            notes.append(f"no snapshot model: used current model {FALLBACK_MODEL_NAME}")
        else:
            model = None
            notes.append("no snapshot model and no current model file: "
                         "tracking without an appearance model")

    # Blocker model: never snapshotted, so the current default unless overridden.
    blocker: Optional[FileChoice] = None
    if overrides.no_blocker:
        notes.append("blocker model off (--no-blocker)")
    elif overrides.blocker_model:
        _require_file(overrides.blocker_model, "--blocker-model")
        blocker = _choice(overrides.blocker_model, SRC_OVERRIDE)
    else:
        fallback = os.path.join(ratmodels_dir, FALLBACK_BLOCKER_MODEL_NAME)
        if os.path.isfile(fallback):
            blocker = _choice(fallback, SRC_FALLBACK)

    def pick(override: Optional[str], flag: str, snap_name: str) -> Optional[FileChoice]:
        if override:
            _require_file(override, flag)
            return _choice(override, SRC_OVERRIDE)
        snap = os.path.join(config_dir, snap_name)
        return _choice(snap, SRC_BUNDLE) if os.path.isfile(snap) else None

    roi = pick(overrides.roi, "--roi", "roi_polygon.json")
    if roi is None:
        notes.append("no ROI polygon: tracking over the whole frame")
    landmarks = pick(overrides.landmarks, "--landmarks", "landmarks.json")

    # Background: override -> computed on request -> the session's PNG -> computed.
    if overrides.background:
        _require_file(overrides.background, "--background")
        background = _choice(overrides.background, SRC_OVERRIDE)
    elif overrides.background_from_video:
        background = FileChoice(None, SRC_COMPUTED)
    else:
        name = (read_sidecar_header(sidecar).get("background") if sidecar else None) \
            or f"{sid}_background.png"
        png = os.path.join(bundle, "video", name)
        if os.path.isfile(png):
            background = _choice(png, SRC_BUNDLE)
        else:
            background = FileChoice(None, SRC_COMPUTED)
            notes.append("no background PNG: computed a median background from the video")

    file_params: Dict[str, Any] = {}
    if overrides.params_file:
        _require_file(overrides.params_file, "--params-file")
        file_params = load_params_file(overrides.params_file)

    return OfflineSettings(
        bundle=os.path.abspath(bundle), session_id=sid, video=os.path.abspath(video),
        sidecar=os.path.abspath(sidecar) if sidecar else None,
        model=model, blocker_model=blocker, roi=roi, landmarks=landmarks,
        background=background,
        snapshot_params=_snapshot_tracker_params(config_dir),
        file_params=file_params, set_params=tuple(overrides.sets), notes=notes)


# ---------------------------------------------------------------- settings layers
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(key: str, raw: Any, like: Any) -> Any:
    """Convert ``raw`` to the type of the setting's current value ``like``."""
    if isinstance(like, bool):
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise ValueError(f"{key}: expected true/false, got {raw!r}")
    try:
        if isinstance(like, int):
            f = float(raw)
            if f != int(f):
                raise ValueError
            return int(f)
        if isinstance(like, float):
            return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key}: expected a {type(like).__name__}, got {raw!r}") from None
    return raw


def parse_set_args(items: Sequence[str]) -> Dict[str, str]:
    """``["thresh=25", "use_kalman=false"]`` -> ``{"thresh": "25", ...}``."""
    out: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--set expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def merge_params(base: Mapping[str, Any], settings: OfflineSettings
                 ) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Layer snapshot -> params file -> ``--set`` over ``base`` (the tracker's
    settings after its model is loaded). Returns ``(params, sources)`` where
    ``sources`` names the layer that last set each changed key.

    Unknown keys in the params file or ``--set`` raise ``ValueError`` listing
    the valid names; unknown snapshot keys (an older/newer rig) are ignored.
    """
    params = dict(base)
    sources: Dict[str, str] = {}
    valid = sorted(base)

    def apply(layer: Mapping[str, Any], name: str, strict: bool) -> None:
        unknown = sorted(k for k in layer if k not in params)
        if unknown and strict:
            raise ValueError(f"unknown tracker setting(s) {', '.join(unknown)}; "
                             f"valid: {', '.join(valid)}")
        for k, v in layer.items():
            if k in params:
                params[k] = _coerce(k, v, base[k])
                sources[k] = name

    apply(settings.snapshot_params, PSRC_SNAPSHOT, strict=False)
    apply(settings.file_params, PSRC_PARAMS_FILE, strict=True)
    apply(parse_set_args(settings.set_params), PSRC_SET, strict=True)
    return params, sources


# ----------------------------------------------------------- frames + outputs
def background_sample_indices(n_frames: int, n_samples: int = BACKGROUND_SAMPLES
                              ) -> List[int]:
    """``n_samples`` frame indices spread evenly over ``[0, n_frames)``, each
    the centre of its slice (so the very first/last frames are avoided)."""
    if n_frames <= 0 or n_samples <= 0:
        return []
    n = min(n_samples, n_frames)
    return sorted({int((i + 0.5) * n_frames / n) for i in range(n)})


def validate_tag(tag: Optional[str]) -> Optional[str]:
    if tag is None or tag == "":
        return None
    if not _TAG_RE.match(tag):
        raise ValueError(f"--tag may only contain letters, digits, '_' and '-': {tag!r}")
    return tag


def output_paths(out_dir: str, session_id: str, tag: Optional[str]
                 ) -> Tuple[str, str]:
    """``(<out_dir>/<id>_offline_track[_<tag>].csv, ... .json)``."""
    stem = f"{session_id}_{OUTPUT_STEM}" + (f"_{validate_tag(tag)}" if tag else "")
    return os.path.join(out_dir, stem + ".csv"), os.path.join(out_dir, stem + ".json")


def check_overwrite(paths: Sequence[str], overwrite: bool) -> None:
    existing = [p for p in paths if os.path.exists(p)]
    if existing and not overwrite:
        raise FileExistsError(
            f"offline run already exists ({os.path.basename(existing[0])}); "
            "pass --overwrite or choose a different --tag")


def frame_range(start: int, end: Optional[int], warmup: int
                ) -> Tuple[int, int, Optional[int]]:
    """``(first_processed, first_written, last_written_or_None)``.

    ``end`` is the INCLUSIVE last frame to write (None = to the end of the
    video). ``warmup`` frames before ``start`` are processed but not written, so
    the tracker is already locked on when the written range begins.
    """
    if start < 0 or warmup < 0:
        raise ValueError("--start-frame and --warmup-frames must be >= 0")
    if end is not None and end < start:
        raise ValueError("--end-frame must be >= --start-frame")
    return max(0, start - warmup), start, end


# ------------------------------------------------------------------- summary
class RunTally:
    """Running counts over the WRITTEN frames of one run."""

    def __init__(self) -> None:
        self.frames = 0
        self.detected = 0
        self.states: Dict[str, int] = {}

    def add(self, state: str, detected: bool) -> None:
        self.frames += 1
        self.detected += int(bool(detected))
        self.states[state] = self.states.get(state, 0) + 1

    def to_dict(self) -> Dict[str, Any]:
        n = self.frames
        return {
            "frames_written": n,
            "frames_detected": self.detected,
            "state_counts": dict(sorted(self.states.items())),
            "state_fractions": {k: round(v / n, 4) for k, v in sorted(self.states.items())}
            if n else {},
        }


def summary_dict(settings: OfflineSettings, *, tally: RunTally,
                 params: Mapping[str, Any], param_sources: Mapping[str, str],
                 sidecar_rows: Optional[int], frames_decoded: int,
                 first_written: int, last_written: Optional[int], warmup: int,
                 elapsed_s: float, created_iso: str, tag: Optional[str],
                 csv_path: str, notes: Sequence[str]) -> Dict[str, Any]:
    """The run's summary JSON: what was used, what range ran, and the counts."""
    def fc(c: Optional[FileChoice]) -> Optional[Dict[str, Any]]:
        return c.to_dict() if c else None

    return {
        "tool": "tmaze.tools.offline_track",
        "tool_version": TOOL_VERSION,
        "created_iso": created_iso,
        "session_id": settings.session_id,
        "tag": tag,
        "bundle": settings.bundle,
        "video": settings.video,
        "sidecar": settings.sidecar,
        "track_csv": os.path.abspath(csv_path),
        "files": {
            "model": fc(settings.model),
            "blocker_model": fc(settings.blocker_model),
            "roi": fc(settings.roi),
            "landmarks": fc(settings.landmarks),
            "background": fc(settings.background),
        },
        "params": dict(params),
        "param_sources": dict(sorted(param_sources.items())),
        "range": {"first_written": first_written, "last_written": last_written,
                  "warmup_frames": warmup},
        "sidecar_rows": sidecar_rows,
        "frames_decoded": frames_decoded,
        "elapsed_s": round(elapsed_s, 2),
        "frames_per_s": round(tally.frames / elapsed_s, 1) if elapsed_s > 0 else None,
        **tally.to_dict(),
        "notes": list(notes),
    }


def write_json(path: str, obj: Any) -> None:
    """Write ``obj`` as pretty JSON atomically (temp file + rename)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def csv_header_lines(settings: OfflineSettings, sidecar_header: Mapping[str, str],
                     *, created_iso: str, tag: Optional[str]) -> List[str]:
    """``# key: value`` lines for the track CSV: the sidecar's own header (so
    readers find fps/width/height/session_t0 as usual) then the offline keys."""
    lines = [f"# {k}: {v}" for k, v in sidecar_header.items()]

    def name(c: Optional[FileChoice]) -> str:
        if c is None:
            return "(none)"
        base = os.path.basename(c.path) if c.path else "(median of video frames)"
        return f"{base} [{c.source}]"

    lines += [
        f"# offline_track_tool_version: {TOOL_VERSION}",
        f"# offline_created: {created_iso}",
        f"# offline_tag: {tag or ''}",
        f"# offline_model: {name(settings.model)}",
        f"# offline_blocker_model: {name(settings.blocker_model)}",
        f"# offline_roi: {name(settings.roi)}",
        f"# offline_background: {name(settings.background)}",
        "# offline_note: every frame tracked on itself; full settings in the "
        "matching .json",
    ]
    return lines
