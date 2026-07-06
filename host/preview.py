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
