"""
still_frame_editor.py - shared modal still-frame editor scaffolding.

The Tracking tab (tracker_view.py: _RoiEditor / _LandmarkEditor) and the Maze
state tab (maze_state_view.py: _ZoneEditor) each pop up a modal window over a
captured still frame so the operator can click precise geometry (a polygon ROI,
single-point landmarks, or the multi-region zone map) without the moving rat
interfering. They all copied the same scaffolding: scale the frame to
EDITOR_MAX_W, convert BGR->RGB, open a grabbed Toplevel, draw the image on a
Canvas, and tear down safely on close. This module holds that common part; each
editor subclasses _StillFrameEditorBase and implements only its own placement /
commit semantics (see FW-0006, extracted under ADR-0053).

Heavy deps (OpenCV, Pillow) come from gui_deps so importing this module never
breaks experiment_control.py.
"""

from __future__ import annotations

import tkinter as tk

from gui_deps import Image, ImageTk, cv2

EDITOR_MAX_W = 960   # still-frame editor display width (shared by all editors)


class _StillFrameEditorBase:
    """Modal still-frame editor scaffolding shared by the ROI/landmark/zone editors.

    Subclasses call ``super().__init__(parent, frame_bgr, title)`` to get a
    grabbed ``self._top`` (Toplevel) holding the scaled still frame, then build
    their own controls and event bindings. Use ``self._make_canvas(parent)`` to
    place the image Canvas (packed straight in ``self._top`` for a bare editor,
    or inside a body frame beside a side list), and call ``self._close()`` on
    commit/cancel. The scaled display geometry is available as ``self._scale`` /
    ``self._dw`` / ``self._dh`` for normalizing clicked coordinates to 0..1.
    """

    def __init__(self, parent, frame_bgr, title, max_w=EDITOR_MAX_W):
        h, w = frame_bgr.shape[:2]
        self._scale = min(1.0, max_w / float(w)) if w else 1.0
        dw, dh = max(1, int(w * self._scale)), max(1, int(h * self._scale))
        disp = cv2.resize(frame_bgr, (dw, dh), interpolation=cv2.INTER_AREA) \
            if self._scale < 1.0 else frame_bgr.copy()
        self._rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        self._dw, self._dh = dw, dh
        self._photo = None   # kept as an attribute so Tk doesn't GC the image

        self._top = tk.Toplevel(parent)
        self._top.title(title)
        self._top.transient(parent.winfo_toplevel())

    def _make_canvas(self, parent) -> tk.Canvas:
        """Create the image Canvas in ``parent`` and draw the still frame on it."""
        self._photo = ImageTk.PhotoImage(Image.fromarray(self._rgb))
        canvas = tk.Canvas(parent, width=self._dw, height=self._dh,
                           highlightthickness=0)
        canvas.create_image(0, 0, anchor="nw", image=self._photo)
        return canvas

    def _close(self) -> None:
        try:
            self._top.grab_release()
        except tk.TclError:
            pass
        try:
            self._top.destroy()
        except tk.TclError:
            pass
