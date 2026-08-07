"""
blob_tracker_prototype.py - standalone tuning tool for the rat position tracker.

A throwaway-but-useful OpenCV-window front-end over the shared tracking core in
tracker.py (RatTracker). Use it to tune thresholds, draw the rig ROI polygon, and
sanity-check centroid tracking + intruder rejection before/independently of the
experiment GUI. The actual algorithm lives in tracker.py so the prototype and the
GUI never diverge.

Source can be the rig Basler camera (default), a video file, or a webcam, so you
can tune against a recorded clip on any machine. Camera identity and orientation
are imported from camera_view.py so the view matches the rig.

Run (from host/, env drieu_t_maze):
    python blob_tracker_prototype.py                 # rig Basler camera
    python blob_tracker_prototype.py --source path/to/clip.mkv
    python blob_tracker_prototype.py --source 0      # default webcam
    # Tune a recording against its paired empty-rig background (ADR-0020):
    python blob_tracker_prototype.py --source clip.mkv --background clip_background.png

Trackbars (window "tracker"): thresh, min_area, blur, gate (max jump/frame),
accept (appearance accept-score x100, model only), coast (COASTING->LOST frames).

Lens distortion: the live Basler source is undistorted with the same calibration
the GUI uses (host/camera_view.py CALIBRATION_PATH); toggle with the 'u' key.
File/webcam sources are left as-is.

Appearance model (Phase 3): after grabbing a background, put ONLY the animal in
the rig and press 't' to start a registration window; let it run/turn/rear/groom
for a few seconds (watch reg accepted=N climb) so the model captures the range of
colour, size and shape, then press 't' again to build it. The model is a pooled
H-S colour histogram plus a Gaussian over [log area, log elongation, solidity]
(size+shape, jointly). With a model, an intruder of the wrong colour/size is
rejected and the track is re-acquired after the stale lock times out (COASTING ->
LOST). Optionally press 'a' to keep ENRICHING the model while tracking (accumulate
more samples - widens the captured range; never forgets). Save/load the model and
the ROI to reuse them in the GUI.

Keys (focus an OpenCV window):
    b   grab current frame as the empty-rig reference (background)
    Left-click   add an ROI polygon vertex
    Right-click  close/commit the ROI polygon (needs >= 3 vertices)
    z   undo the last (uncommitted) ROI vertex
    x   clear the ROI polygon
    t   start / finalize the rat registration window (build appearance model)
    c   cancel an in-progress registration
    a   toggle online enrichment (accumulate model samples while tracking)
    m   clear the loaded appearance model
    w   save the appearance model to host/ratmodels/
    o   load the most recent model from host/ratmodels/
    e   save the ROI polygon to host/roi_polygon.json (shared with the GUI)
    u   toggle lens-distortion correction (Basler source)
    k   toggle Kalman smoothing
    p   clear the path trail
    l   start/stop logging centroids to a CSV
    s   save a snapshot PNG of the annotated view
    space  pause/resume
    q / Esc  quit
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import sys
import time
from typing import Optional

try:
    import cv2  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"OpenCV (cv2) is required: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

try:
    import numpy as np  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"numpy is required: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

from tmaze.vision.tracker import MODEL_REAPPLY_FIELDS, MODEL_SHAPING_FIELDS, RatTracker
from tmaze.vision.calibration import Undistorter, load_calibration
from tmaze.paths import host_dir

# Reuse the rig camera identity + orientation so this view matches camera_view.
try:
    from camera_view import (  # type: ignore
        CAMERA_IP, CAMERA_SERIAL, CAMERA_MAC, ORIENTATION,
        CALIBRATION_PATH, UNDISTORT_ALPHA,
    )
except Exception:
    CAMERA_IP, CAMERA_SERIAL, CAMERA_MAC, ORIENTATION = "192.168.3.3", "", "0030531AD4B0", "rot180"
    CALIBRATION_PATH, UNDISTORT_ALPHA = "", 0.0


# ================================== CONFIG ===================================
PROC_MAX_W = 720          # processing/display width
WIN_MAIN = "tracker"
WIN_MASK = "mask"

_HERE = str(host_dir())   # host/ - config/models live beside the package (ADR-0083)
ROI_PATH = os.path.join(_HERE, "roi_polygon.json")   # shared with the GUI
MODEL_DIR = os.path.join(_HERE, "ratmodels")          # appearance models (.npz)


def _orient(frame):
    """Match camera_view's orientation so the prototype shows the rig view."""
    if ORIENTATION == "rot180":
        return cv2.rotate(frame, cv2.ROTATE_180)
    if ORIENTATION == "mirror_h":
        return cv2.flip(frame, 1)
    if ORIENTATION == "flip_v":
        return cv2.flip(frame, 0)
    if ORIENTATION == "rot90_cw":
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if ORIENTATION == "rot90_ccw":
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


# ============================== frame sources ================================
class BaslerSource:
    """Free-running Basler grab, matching camera_view's device selection.

    Applies lens-distortion correction (if a matching calibration is supplied)
    in the same order as camera_view: debayer -> undistort -> orient.
    """

    def __init__(self, undistorter=None):
        from pypylon import pylon  # type: ignore
        self._pylon = pylon
        self.undistorter = undistorter
        self.undistort_enabled = True
        factory = pylon.TlFactory.GetInstance()
        devices = factory.EnumerateDevices()
        if not devices:
            raise RuntimeError("No Basler camera found (is Pylon Viewer holding it?)")
        device = self._select(devices)
        self.camera = pylon.InstantCamera(factory.CreateDevice(device))
        self.camera.Open()
        self.converter = pylon.ImageFormatConverter()
        self.converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        self.converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        self.camera.StartGrabbing(pylon.GrabStrategy_OneByOne)
        self.name = self._describe(device)

    @staticmethod
    def _attr(device, attr: str) -> str:
        try:
            return getattr(device, attr)()
        except Exception:
            return ""

    def _select(self, devices):
        for d in devices:
            if CAMERA_SERIAL and self._attr(d, "GetSerialNumber") == CAMERA_SERIAL:
                return d
        for d in devices:
            if CAMERA_IP and self._attr(d, "GetIpAddress") == CAMERA_IP:
                return d
        for d in devices:
            mac = (self._attr(d, "GetMacAddress") or "").replace(":", "").upper()
            if CAMERA_MAC and mac == CAMERA_MAC.upper():
                return d
        return devices[0]

    def _describe(self, device) -> str:
        model = self._attr(device, "GetModelName") or "Basler"
        ip = self._attr(device, "GetIpAddress")
        return f"{model}{(' @ ' + ip) if ip else ''}"

    def read(self):
        grab = self.camera.RetrieveResult(1000, self._pylon.TimeoutHandling_Return)
        if grab is None:
            return None
        try:
            if not grab.IsValid() or not grab.GrabSucceeded():
                return None
            arr = self.converter.Convert(grab).GetArray()
            if self.undistorter is not None and self.undistort_enabled:
                arr, _ = self.undistorter.apply(arr)
            return _orient(arr)
        finally:
            grab.Release()

    def close(self):
        try:
            self.camera.StopGrabbing()
        except Exception:
            pass
        try:
            self.camera.Close()
        except Exception:
            pass


class CaptureSource:
    """cv2.VideoCapture wrapper for a video file or a webcam index."""

    def __init__(self, spec):
        self.cap = cv2.VideoCapture(spec)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video source: {spec!r}")
        self.is_file = isinstance(spec, str)
        self.name = f"{spec}"

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            if self.is_file:  # loop the clip for continuous tuning
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
            if not ok:
                return None
        return _orient(frame)

    def close(self):
        try:
            self.cap.release()
        except Exception:
            pass


def open_source(spec: str, undistorter=None):
    if spec == "basler":
        return BaslerSource(undistorter=undistorter)
    if spec.isdigit():           # webcam index
        return CaptureSource(int(spec))
    return CaptureSource(spec)   # file path


# ============================== ROI polygon editor ===========================
class RoiEditor:
    """Collects polygon vertices via mouse clicks (display-pixel coords)."""

    def __init__(self):
        self.pts: list = []        # uncommitted vertices being drawn
        self.w = 1
        self.h = 1
        self.committed = False     # set briefly after a commit, for the caller

    def on_mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.pts.append((x, y))
        elif event == cv2.EVENT_RBUTTONDOWN:
            if len(self.pts) >= 3:
                self.committed = True   # caller normalizes + pushes to the tracker

    def take_committed(self):
        """If a polygon was just committed, return its normalized verts (once)."""
        if not self.committed:
            return None
        self.committed = False
        w, h = max(1, self.w), max(1, self.h)
        norm = [(px / w, py / h) for px, py in self.pts]
        self.pts = []
        return norm


# ================================ app / UI ===================================
def _scale_to(frame, max_w):
    h, w = frame.shape[:2]
    if w <= max_w:
        return frame
    s = max_w / float(w)
    return cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def _nothing(_):
    pass


def _to_disp(pt, w, h):
    """Normalized (0..1) -> display pixel coords."""
    return int(round(pt[0] * w)), int(round(pt[1] * h))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Classic-CV rat position tracker (prototype).")
    ap.add_argument("--source", default="basler",
                    help="'basler' (default), a video file path, or a webcam index (e.g. 0)")
    ap.add_argument("--max-width", type=int, default=PROC_MAX_W,
                    help=f"processing/display width (default {PROC_MAX_W})")
    ap.add_argument("--log-dir", default="recordings",
                    help="where centroid CSVs are written (default ./recordings)")
    ap.add_argument("--background", default=None,
                    help="empty-rig background PNG to install as the reference "
                         "(e.g. a recording's <name>_background.png; lets you "
                         "tune a recorded video that has no empty-rig frame)")
    args = ap.parse_args(argv)

    # Lens-distortion correction (same calibration the GUI uses). Applied only to
    # the live Basler source - recorded files are already processed and a webcam
    # won't match the rig calibration.
    undistorter = Undistorter(alpha=UNDISTORT_ALPHA)
    calib_msg = "no calib"
    if CALIBRATION_PATH and os.path.exists(CALIBRATION_PATH):
        try:
            cal = load_calibration(CALIBRATION_PATH)
            undistorter.set_calibration(cal)
            calib_msg = f"calib {cal['image_size'][0]}x{cal['image_size'][1]}"
        except Exception as e:
            calib_msg = f"calib load failed: {e}"
    print(f"Calibration: {calib_msg}")

    try:
        src = open_source(args.source, undistorter)
    except Exception as e:
        print(f"Failed to open source {args.source!r}: {type(e).__name__}: {e}\n"
              f"  Try --source basler (rig camera) or a video file path.",
              file=sys.stderr)
        return 1
    print(f"Source: {getattr(src, 'name', args.source)}")

    tracker = RatTracker()
    # Process at the display width so result coords line up with what we draw.
    tracker.update_params(proc_max_w=args.max_width)

    # Install a saved empty-rig background as the reference, if given. A recorded
    # session has no empty-rig frame to grab ('b'), so this loads the paired
    # <name>_background.png written alongside the video. See docs/decisions/0020.
    if args.background:
        bg = cv2.imread(args.background, cv2.IMREAD_COLOR)
        if bg is None:
            print(f"Background load failed: cannot read {args.background!r}",
                  file=sys.stderr)
            return 1
        bg = _scale_to(bg, args.max_width)
        tracker.grab_background()
        tracker.process(bg)   # one pass installs the reference from this frame
        print(f"Loaded background from {args.background}")

    cv2.namedWindow(WIN_MAIN, cv2.WINDOW_NORMAL)
    cv2.namedWindow(WIN_MASK, cv2.WINDOW_NORMAL)
    cv2.createTrackbar("thresh", WIN_MAIN, 30, 255, _nothing)
    cv2.createTrackbar("min_area", WIN_MAIN, 150, 8000, _nothing)
    cv2.createTrackbar("blur", WIN_MAIN, 5, 31, _nothing)
    cv2.createTrackbar("gate", WIN_MAIN, 120, 500, _nothing)
    cv2.createTrackbar("accept", WIN_MAIN, 50, 100, _nothing)   # accept_score x100
    cv2.createTrackbar("coast", WIN_MAIN, 30, 200, _nothing)    # COASTING->LOST frames

    # Reuse a previously drawn ROI (also written by/for the GUI), if present.
    if os.path.exists(ROI_PATH):
        try:
            if tracker.load_roi(ROI_PATH):
                print(f"Loaded ROI from {ROI_PATH}")
        except Exception as e:
            print(f"ROI load failed: {type(e).__name__}: {e}")

    editor = RoiEditor()
    cv2.setMouseCallback(WIN_MAIN, editor.on_mouse)

    paused = False
    use_kalman = True
    enrich = False
    last_frame = None
    csv_file = None
    csv_writer = None
    frame_idx = 0
    fps_t0, fps_n, fps = time.time(), 0, 0.0

    def stop_logging():
        nonlocal csv_file, csv_writer
        if csv_file is not None:
            path = csv_file.name
            csv_file.close()
            csv_file = csv_writer = None
            print(f"Stopped logging -> {path}")

    def start_logging():
        nonlocal csv_file, csv_writer
        os.makedirs(args.log_dir, exist_ok=True)
        path = os.path.join(args.log_dir, f"track_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        csv_file = open(path, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(
            ["frame_idx", "host_unix_s", "cx", "cy", "smoothed_cx", "smoothed_cy",
             "area", "elongation", "solidity", "confidence", "detected", "state"])
        print(f"Logging centroids -> {path}")

    try:
        while True:
            if not paused:
                frame = src.read()
                if frame is None:
                    cv2.waitKey(50)
                    if last_frame is None:
                        continue
                    frame = last_frame
                else:
                    frame = _scale_to(frame, args.max_width)
                    last_frame = frame
            else:
                frame = last_frame
                if frame is None:
                    if (cv2.waitKey(50) & 0xFF) in (ord("q"), 27):
                        break
                    continue

            h, w = frame.shape[:2]
            editor.w, editor.h = w, h

            # Commit a freshly drawn ROI polygon, if any.
            norm = editor.take_committed()
            if norm is not None:
                tracker.set_roi_polygon(norm)

            tracker.update_params(
                thresh=cv2.getTrackbarPos("thresh", WIN_MAIN),
                min_area=float(cv2.getTrackbarPos("min_area", WIN_MAIN)),
                blur=cv2.getTrackbarPos("blur", WIN_MAIN),
                gate_radius=float(cv2.getTrackbarPos("gate", WIN_MAIN)),
                accept_score=cv2.getTrackbarPos("accept", WIN_MAIN) / 100.0,
                coast_max_frames=max(1, cv2.getTrackbarPos("coast", WIN_MAIN)),
                adapt_enrich=enrich,
                use_kalman=use_kalman)

            result = tracker.process(frame)

            # ---- overlay ----
            view = frame.copy()
            roi = tracker.get_roi_polygon()
            if roi:
                pts = np.array([_to_disp(p, w, h) for p in roi], np.int32)
                cv2.polylines(view, [pts], True, (255, 180, 0), 1)
            # in-progress polygon vertices
            for i, p in enumerate(editor.pts):
                cv2.circle(view, p, 3, (0, 255, 255), -1)
                if i > 0:
                    cv2.line(view, editor.pts[i - 1], p, (0, 255, 255), 1)

            path = tracker.get_path()
            for i in range(1, len(path)):
                cv2.line(view, (int(path[i - 1][0]), int(path[i - 1][1])),
                         (int(path[i][0]), int(path[i][1])), (0, 200, 255), 2)
            for cand in result.candidates:           # rejected blobs (dim)
                cv2.circle(view, (int(cand[0]), int(cand[1])), 4, (120, 120, 120), 1)
            if result.centroid is not None:
                cv2.circle(view, (int(result.centroid[0]), int(result.centroid[1])),
                           5, (0, 0, 255), -1)        # raw (red)
            if use_kalman and result.smoothed is not None:
                cv2.circle(view, (int(result.smoothed[0]), int(result.smoothed[1])),
                           7, (0, 255, 0), 2)         # smoothed (green)

            if not paused:
                fps_n += 1
                if time.time() - fps_t0 >= 0.5:
                    fps = fps_n / (time.time() - fps_t0)
                    fps_t0, fps_n = time.time(), 0

            ref_txt = "ref:set" if tracker.has_reference() else "ref:NONE (press b)"
            if getattr(src, "undistorter", None) is None:
                undist_txt = "undist:n/a"
            elif not getattr(src, "undistort_enabled", False):
                undist_txt = "undist:off"
            elif undistorter.is_active():
                undist_txt = "undist:on"
            else:
                undist_txt = "undist:no-match"
            if result.state == "REGISTERING":
                model_txt = f"REGISTERING acc={result.reg_accepted}/{result.reg_target}"
            elif tracker.has_model():
                cov = "cov" if result.model_coverage else "thin"
                model_txt = f"model:set n={result.model_samples} {cov}"
            else:
                model_txt = "model:none (press t)"
            lines = [
                f"state={result.state}  conf={result.confidence:4.2f}  {ref_txt}",
                f"{model_txt}  enrich={'ON' if enrich else 'off'}  {undist_txt}",
                f"area={int(result.area)}  elong={result.elongation:4.2f}  "
                f"sol={result.solidity:4.2f}  fps={fps:4.1f}  "
                f"kalman={'on' if use_kalman else 'off'}  "
                f"log={'ON' if csv_file else 'off'}{'  PAUSED' if paused else ''}",
                "b bg | t register | a enrich | w/o model | e save ROI | x clear ROI | u undistort",
            ]
            for i, t in enumerate(lines):
                cv2.putText(view, t, (8, 20 + 20 * i), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (255, 255, 255), 1, cv2.LINE_AA)

            cv2.imshow(WIN_MAIN, view)
            if result.mask is not None:
                cv2.imshow(WIN_MASK, result.mask)

            if csv_writer is not None and not paused:
                cx, cy = (result.centroid if result.centroid is not None else ("", ""))
                sx, sy = (result.smoothed if result.smoothed is not None else ("", ""))
                csv_writer.writerow([frame_idx, f"{time.time():.6f}", cx, cy, sx, sy,
                                     int(result.area), f"{result.elongation:.3f}",
                                     f"{result.solidity:.3f}", f"{result.confidence:.3f}",
                                     int(result.detected), result.state])
            frame_idx += 1

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            elif key == ord("b"):
                tracker.grab_background()
                print("Reference grab requested.")
            elif key == ord("z"):
                if editor.pts:
                    editor.pts.pop()
            elif key == ord("x"):
                editor.pts = []
                tracker.clear_roi()
                print("ROI cleared.")
            elif key == ord("t"):
                if tracker.is_registering():
                    tracker.finalize_registration()
                    print("Registration: finalizing model...")
                elif not tracker.has_reference():
                    print("Grab a background (b) before registering.")
                else:
                    tracker.start_registration()
                    print("Registration: started (let the animal move, press t to finish).")
            elif key == ord("c"):
                if tracker.is_registering():
                    tracker.cancel_registration()
                    print("Registration cancelled.")
            elif key == ord("a"):
                enrich = not enrich
                print(f"Online enrichment: {'on' if enrich else 'off'}")
            elif key == ord("m"):
                tracker.clear_model()
                print("Appearance model cleared.")
            elif key == ord("w"):
                if tracker.has_model():
                    os.makedirs(MODEL_DIR, exist_ok=True)
                    mp = os.path.join(MODEL_DIR, f"ratmodel_{time.strftime('%Y%m%d_%H%M%S')}.npz")
                    tracker.save_model(mp)
                    print(f"Saved model -> {mp}")
                else:
                    print("No model to save (register one with t first).")
            elif key == ord("o"):
                models = sorted(glob.glob(os.path.join(MODEL_DIR, "*.npz")),
                                key=os.path.getmtime, reverse=True)
                if models:
                    try:
                        before = tracker.get_params()
                        tracker.load_model(models[0])
                        print(f"Loaded model <- {models[0]}")
                        m = tracker.get_model()
                        if getattr(m, "acq_params", None) is None:
                            print("  (no stored settings - current tuning kept)")
                        else:
                            after = tracker.get_params()
                            changed = [f"{f}:{getattr(before, f)}->{getattr(after, f)}"
                                       for f in MODEL_REAPPLY_FIELDS
                                       if getattr(before, f) != getattr(after, f)]
                            print("  restored settings"
                                  + (": " + ", ".join(changed) if changed
                                     else " (already matched)"))
                            if any(getattr(before, f) != getattr(after, f)
                                   for f in MODEL_SHAPING_FIELDS):
                                print("  detection settings changed - re-grab "
                                      "the background (b)")
                    except Exception as e:
                        print(f"Model load failed: {type(e).__name__}: {e}")
                else:
                    print(f"No models found in {MODEL_DIR}")
            elif key == ord("e"):
                if tracker.save_roi(ROI_PATH):
                    print(f"Saved ROI -> {ROI_PATH}")
                else:
                    print("No ROI to save (draw one first).")
            elif key == ord("u"):
                if hasattr(src, "undistort_enabled"):
                    src.undistort_enabled = not src.undistort_enabled
                    print(f"Undistort: {'on' if src.undistort_enabled else 'off'}")
            elif key == ord("k"):
                use_kalman = not use_kalman
                tracker.reset()
            elif key == ord("p"):
                tracker.reset()
            elif key == ord("l"):
                stop_logging() if csv_file else start_logging()
            elif key == ord("s"):
                snap = f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
                cv2.imwrite(snap, view)
                print(f"Saved {snap}")
            elif key == ord(" "):
                paused = not paused
    finally:
        stop_logging()
        src.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
