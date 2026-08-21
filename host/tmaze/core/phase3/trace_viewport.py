"""
trace_viewport.py - the Metrics tab's zoomable/scrollable time window (pure core).

The live traces panel (``phase3_traces_view``) plots session measures against
wall-clock minutes. Instead of the old fixed "last N minutes" clamp, the panel
now shows a VIEWPORT: a window of chosen WIDTH (the zoom) positioned by a scroll
OFFSET (the pan), optionally pinned to the newest data ("follow"). This tiny pure
helper turns that (width, start, follow) intent - plus the data's total extent -
into the two numbers the view needs: the matplotlib x-limits and the scrollbar
thumb fraction. Keeping it here (not in the Tk shell) makes the arithmetic
headlessly testable and keeps the follow-unless-zoomed rule in one place.

See docs/decisions/0116 (interactive viewport, follow-unless-zoomed). Pure
stdlib; headless tests: python -m unittest test_trace_viewport.
"""

from __future__ import annotations

from typing import Optional, Tuple

# When there is no data yet (total extent 0), fall back to a 1-minute unit window
# so the axis has a sane range - matching the panel's old xlim(0, 1) default.
_EMPTY_HI = 1.0


def compute_viewport(total_min: float,
                     width_min: Optional[float],
                     start_min: float,
                     follow: bool) -> Tuple[float, float, float, float]:
    """Resolve a scroll/zoom intent into x-limits + a scrollbar thumb fraction.

    Args:
      total_min: the data's full x-extent in minutes (the session runs [0, total]).
      width_min: the visible window WIDTH (zoom). ``None``/<=0 or any value >=
        ``total_min`` means "whole session" (fully zoomed out).
      start_min: the requested LEFT edge (pan), clamped to ``[0, total-width]``.
      follow: when True the window is pinned to the right edge (``xhi == total``),
        so a growing session keeps the newest trials in view; ``start_min`` is
        ignored. This is the follow-unless-zoomed default the panel resets to.

    Returns ``(xlo, xhi, scroll_lo, scroll_hi)`` - the axis limits in minutes and
    the scrollbar thumb span as fractions of the total in ``[0, 1]`` (0..1 = whole
    session, i.e. the thumb fills the bar).
    """
    total = max(0.0, float(total_min))
    if total <= 0.0:
        return (0.0, _EMPTY_HI, 0.0, 1.0)

    # Whole-session (fully zoomed out): no meaningful width, or a width that would
    # show everything anyway. Thumb fills the bar.
    if width_min is None or width_min <= 0.0 or float(width_min) >= total:
        return (0.0, total, 0.0, 1.0)

    width = float(width_min)
    max_start = max(0.0, total - width)
    start = max_start if follow else min(max(0.0, float(start_min)), max_start)
    xlo = start
    xhi = start + width
    return (xlo, xhi, xlo / total, xhi / total)
