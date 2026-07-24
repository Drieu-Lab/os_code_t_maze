"""
preview.py - shared Tk live-preview helpers for camera/tracking/rules tabs.

camera_view.py, tracker_view.py, and rules_view.py each duplicated the same
resize -> optional overlay draw -> BGR2RGB -> PhotoImage -> label.configure
pipeline, plus try/except tk.TclError around frame.after scheduling. This module
holds the common path; each tab passes a draw(disp, scale) callback for its
extras (tracking overlay, zone circles, mask view stays tab-local).
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from gui_deps import Image, ImageTk, cv2


def safe_after(widget: tk.Misc, ms: int, fn: Callable, *args) -> None:
    """Schedule fn on the Tk loop; ignore TclError if the widget is torn down."""
    try:
        widget.after(ms, fn, *args)
    except tk.TclError:
        pass


def render_bgr_to_label(
    label: tk.Widget,
    frame_bgr,
    max_w: int,
    draw: Optional[Callable[[object, float], None]] = None,
    *,
    copy_unscaled: bool = False,
) -> Optional[object]:
    """Downscale (if needed), optionally draw on disp, show on label.

    Returns the PhotoImage (caller must keep a reference) or None on failure.
    """
    if cv2 is None or Image is None or ImageTk is None:
        return None
    try:
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, max_w / float(w)) if w else 1.0
        if scale < 1.0:
            disp = cv2.resize(
                frame_bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            disp = frame_bgr.copy() if copy_unscaled else frame_bgr
        if draw is not None:
            draw(disp, scale)
        rgb = cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)
        photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        label.configure(image=photo)
        return photo
    except Exception:
        return None


class PreviewLoop:
    """Opt-in live-preview producer loop shared by the camera/tracking tabs.

    Each tab used to hand-copy the same outer loop around ``safe_after`` and
    ``render_bgr_to_label``: re-arm on the Tk loop, skip work while hidden, grab
    the newest frame, render it (scaled to ``max_w``) with a per-tab overlay, and
    tear down quietly once the widget is gone. This class owns that loop so a tab
    supplies only its frame ``source`` and overlay ``draw`` callback.

    Args:
        frame: widget used for ``after``-scheduling the next tick.
        label: target Label the rendered ``PhotoImage`` is shown on.
        interval_ms: re-arm interval (a tab's ``PREVIEW_INTERVAL_MS``).
        max_w: max display width (a tab's ``PREVIEW_MAX_W``).
        source: ``() -> newest BGR frame or None`` (returns None when there is
            nothing to show, e.g. no camera / no frame yet).
        draw: ``(disp, scale) -> None`` overlay drawn on the scaled frame.
        is_closed: optional ``() -> bool``; when it returns True the loop stops
            re-arming. Lets a tab share its existing ``self._closed`` flag rather
            than keep a second one. Defaults to never-closed.
    """

    def __init__(
        self,
        frame: tk.Misc,
        label: tk.Widget,
        interval_ms: int,
        max_w: int,
        source: Callable[[], Optional[object]],
        draw: Optional[Callable[[object, float], None]] = None,
        is_closed: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._frame = frame
        self._label = label
        self._interval_ms = interval_ms
        self._max_w = max_w
        self._source = source
        self._draw = draw
        self._is_closed = is_closed if is_closed is not None else (lambda: False)
        self._photo = None  # retain last good PhotoImage so Tk doesn't GC it

    def start(self) -> None:
        """Kick off the loop; it re-arms itself until ``is_closed`` is True."""
        self._schedule()

    def _schedule(self) -> None:
        if self._is_closed():
            return
        try:
            self._render()
        finally:
            safe_after(self._frame, self._interval_ms, self._schedule)

    def _render(self) -> None:
        try:
            if not self._label.winfo_viewable():
                return  # on another tab / hidden -> don't spend any work
        except tk.TclError:
            return
        frame = self._source()
        if frame is None:
            return
        photo = render_bgr_to_label(self._label, frame, self._max_w, draw=self._draw)
        if photo is not None:
            self._photo = photo
