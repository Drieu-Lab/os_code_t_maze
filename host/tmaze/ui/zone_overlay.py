"""
zone_overlay.py - shared OpenCV drawing of the maze zone map on a preview.

A thin cv2 "shell" helper (per the pure-core + thin-shell convention) so the
zone polygons / well / stem axis are drawn identically wherever they appear.
Two callers today: the Setup -> Maze state preview (maze_state_view.py, which
also owns the zone editor) and the Rig view camera preview (camera_view.py).

Colours live here as the single source of truth: the hex strings double as Tk
canvas colours in the zone editor, and _hex_to_bgr converts them for OpenCV.
"""

from __future__ import annotations

from typing import Optional

from tmaze.ui.gui_deps import cv2, np

# Per-zone draw colours (hex for the Tk canvas editor; converted to BGR for the
# OpenCV overlay via _hex_to_bgr).
ZONE_HEX = {
    "HUB": "#ff4d4d",
    "CENTRAL_STEM": "#33cc33",
    "JUNCTION": "#e6c300",
    "LEFT_CHOICE": "#3399ff",
    "RIGHT_CHOICE": "#ff6699",
    "LEFT_RETURN": "#9966ff",
    "RIGHT_RETURN": "#ff9933",
}
WELL_HEX = "#00e5e5"
AXIS_HEX = "#ffffff"


def _hex_to_bgr(h: str):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def draw_zone_overlay(disp, zone_map, current_zone: Optional[str] = None,
                      in_well: bool = False) -> None:
    """Draw the zone polygons + well + stem axis on the (scaled) preview, in place.

    ``current_zone`` (the zone the rat is presently in, if known) is drawn with a
    thicker outline so the occupied zone stands out; pass None for a static draw.
    ``in_well`` bolds the well sub-region when the rat is in it (the well sits
    inside the HUB and is a HUB substate, not a separate zone, so this is what
    signals "in the well" visually).
    """
    if cv2 is None or np is None or disp is None:
        return
    try:
        dh, dw = disp.shape[:2]
        for z in zone_map.zones:
            if len(z.polygon) < 3:
                continue
            pts = np.array([[int(x * dw), int(y * dh)] for x, y in z.polygon],
                           np.int32)
            color = _hex_to_bgr(ZONE_HEX.get(z.name, "#ffffff"))
            thick = 3 if z.name == current_zone else 1
            cv2.polylines(disp, [pts], True, color, thick)
            cx = int(sum(p[0] for p in z.polygon) / len(z.polygon) * dw)
            cy = int(sum(p[1] for p in z.polygon) / len(z.polygon) * dh)
            cv2.putText(disp, z.name, (cx - 30, cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
        if zone_map.well is not None and len(zone_map.well) >= 3:
            pts = np.array([[int(x * dw), int(y * dh)] for x, y in zone_map.well],
                           np.int32)
            cv2.polylines(disp, [pts], True, _hex_to_bgr(WELL_HEX),
                          3 if in_well else 1)
        if zone_map.stem_base is not None and zone_map.stem_tip is not None:
            a = (int(zone_map.stem_base[0] * dw), int(zone_map.stem_base[1] * dh))
            b = (int(zone_map.stem_tip[0] * dw), int(zone_map.stem_tip[1] * dh))
            cv2.arrowedLine(disp, a, b, _hex_to_bgr(AXIS_HEX), 2, tipLength=0.08)
    except Exception:
        pass
