"""
gui_deps.py - shared defensive imports for optional GUI dependencies.

camera_view.py, tracker_view.py, and rules_view.py each used to duplicate
~30 lines of try/except import guards plus an identical _missing_dependency()
method. This module centralizes those guards so importing experiment_control.py
never crashes when numpy/OpenCV/Pillow (or pypylon/av on the camera tab) are
absent; each tab surfaces the failure in its UI instead.

Usage:
    from tmaze.ui.gui_deps import np, cv2, Image, ImageTk, av, pylon, missing_dependency

    missing = missing_dependency("numpy", "opencv-python", "pillow")
"""

from __future__ import annotations

from typing import Optional

try:
    import numpy as np  # type: ignore
    _NUMPY_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    np = None  # type: ignore
    _NUMPY_ERR = f"{type(e).__name__}: {e}"

try:
    import cv2  # type: ignore
    _CV2_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    cv2 = None  # type: ignore
    _CV2_ERR = f"{type(e).__name__}: {e}"

try:
    from PIL import Image, ImageTk  # type: ignore
    _PIL_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    Image = None  # type: ignore
    ImageTk = None  # type: ignore
    _PIL_ERR = f"{type(e).__name__}: {e}"

try:
    from pypylon import pylon  # type: ignore
    _PYLON_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    pylon = None  # type: ignore
    _PYLON_ERR = f"{type(e).__name__}: {e}"

try:
    import av  # type: ignore
    _AV_ERR: Optional[str] = None
except Exception as e:  # pragma: no cover
    av = None  # type: ignore
    _AV_ERR = f"{type(e).__name__}: {e}"

# (module attr name, error string, human label for the tab message)
_DEP_CHECKS: dict[str, tuple[object, Optional[str], str]] = {
    "numpy": (np, _NUMPY_ERR, "numpy"),
    "opencv-python": (cv2, _CV2_ERR, "opencv-python"),
    "pillow": (Image, _PIL_ERR, "pillow"),
    "pypylon": (pylon, _PYLON_ERR, "pypylon"),
    "av": (av, _AV_ERR, "av"),
}


def missing_dependency(*names: str) -> Optional[str]:
    """Return a newline-separated problem list, or None if all named deps load."""
    problems = []
    for name in names:
        mod, err, label = _DEP_CHECKS.get(name, (None, "unknown", name))
        if mod is None:
            problems.append(f"{label} ({err})")
    return "\n".join(problems) if problems else None
