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

import math
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

# Zone-name label styling. Labels are pushed OUTSIDE the rig outline (each
# zone's own outer edge / the maze bounding box) into the surrounding border so
# they never sit on top of the rat: the top zones (choices + junction) go above
# the maze, HUB to its left, REWARD below the well, and the two RETURN labels are
# rotated to run along the slanted side walls. CENTRAL_STEM is intentionally
# left near the corridor (its outward-from-centre anchor) per current request.
LABEL_FONT_SCALE = 0.28
LABEL_MARGIN = 0.02          # normalized gap pushed beyond the rig outline
LABEL_OUTWARD_FRAC = 0.6     # CENTRAL_STEM: centroid->outer-vertex fraction
_LABEL_OUTWARD_MIN = 1e-3    # below this the outward direction is degenerate

_TOP_ZONES = ("LEFT_CHOICE", "RIGHT_CHOICE", "JUNCTION")
_RETURN_ZONES = ("LEFT_RETURN", "RIGHT_RETURN")


def _hex_to_bgr(h: str):
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b, g, r)


def _text_at(disp, text, px, py, color, dw, dh,
             halign: str = "center", valign: str = "center") -> None:
    """Draw horizontal ``text`` anchored at (px, py) pixels with the given
    alignment, clamped so it stays on-frame. ``halign`` in {left, center,
    right}; ``valign`` in {top, center, bottom} where bottom puts the baseline
    at ``py`` (text sits above it)."""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                  LABEL_FONT_SCALE, 1)
    if halign == "center":
        tx = int(px - tw / 2)
    elif halign == "right":
        tx = int(px - tw)
    else:
        tx = int(px)
    if valign == "center":
        ty = int(py + th / 2)
    elif valign == "top":
        ty = int(py + th)
    else:  # bottom: baseline at py, glyphs above
        ty = int(py)
    tx = min(max(tx, 0), max(dw - tw, 0))
    ty = min(max(ty, th), dh - 1)
    cv2.putText(disp, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                LABEL_FONT_SCALE, color, 1, cv2.LINE_AA)


def _rotated_text_at(disp, text, cx, cy, angle_deg, color, dw, dh) -> None:
    """Draw ``text`` centred at (cx, cy) pixels, rotated by ``angle_deg`` (the
    on-screen slope of the wall it should run along), copying the glyph pixels
    onto ``disp``. Used for the RETURN labels on the slanted side walls."""
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX,
                                     LABEL_FONT_SCALE, 1)
    pad = 2
    pw, ph = tw + 2 * pad, th + base + 2 * pad
    patch = np.zeros((ph, pw, 3), np.uint8)
    cv2.putText(patch, text, (pad, pad + th), cv2.FONT_HERSHEY_SIMPLEX,
                LABEL_FONT_SCALE, color, 1, cv2.LINE_AA)
    # Rotate the little text patch. Negate the angle: the frame's y axis points
    # down, so a clockwise on-screen slope is a negative rotation for cv2.
    m = cv2.getRotationMatrix2D((pw / 2.0, ph / 2.0), -angle_deg, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(ph * sin + pw * cos), int(ph * cos + pw * sin)
    m[0, 2] += nw / 2.0 - pw / 2.0
    m[1, 2] += nh / 2.0 - ph / 2.0
    rot = cv2.warpAffine(patch, m, (nw, nh))
    x0, y0 = int(cx - nw / 2), int(cy - nh / 2)
    # Clip the rotated patch to the frame, then copy where a glyph was drawn.
    sx, sy = max(0, -x0), max(0, -y0)
    dx, dy = max(0, x0), max(0, y0)
    w = min(nw - sx, dw - dx)
    h = min(nh - sy, dh - dy)
    if w <= 0 or h <= 0:
        return
    sub = rot[sy:sy + h, sx:sx + w]
    mask = sub.any(axis=2)
    disp[dy:dy + h, dx:dx + w][mask] = sub[mask]


def _draw_outward_label(disp, zone, centroid, maze_center, color, dw, dh) -> None:
    """Draw a zone's name near its outer edge, centred on the anchor and clamped
    to the frame (the CENTRAL_STEM / fallback placement). ``centroid`` and
    ``maze_center`` are normalized (0..1)."""
    cx0, cy0 = centroid
    ox, oy = cx0 - maze_center[0], cy0 - maze_center[1]
    if (ox * ox + oy * oy) ** 0.5 < _LABEL_OUTWARD_MIN:
        ax, ay = cx0, cy0
    else:
        vx, vy = max(zone.polygon,
                     key=lambda p: (p[0] - cx0) * ox + (p[1] - cy0) * oy)
        ax = cx0 + (vx - cx0) * LABEL_OUTWARD_FRAC
        ay = cy0 + (vy - cy0) * LABEL_OUTWARD_FRAC
    _text_at(disp, zone.name, ax * dw, ay * dh, color, dw, dh,
             halign="center", valign="center")


def _draw_return_label(disp, zone, centroid, maze_center, color, dw, dh) -> None:
    """Draw a RETURN label rotated to run along that zone's outer (wall) edge,
    nudged just outside it."""
    ox, oy = centroid[0] - maze_center[0], centroid[1] - maze_center[1]
    norm = (ox * ox + oy * oy) ** 0.5 or 1.0
    ox, oy = ox / norm, oy / norm
    poly = zone.polygon
    # A return zone is a long, narrow quad: its two LONG edges are the walls and
    # its two short edges are the top/bottom. Restrict the wall search to the
    # long edges (length above the mean) so the outer WALL wins rather than a
    # short bottom edge that happens to poke slightly further out - otherwise
    # LEFT_RETURN latches onto its bottom edge and lies flat instead of hugging
    # the wall like RIGHT_RETURN.
    edges = []
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        length = math.hypot((b[0] - a[0]) * dw, (b[1] - a[1]) * dh)
        edges.append((a, b, length))
    mean_len = sum(e[2] for e in edges) / len(edges) if edges else 0.0
    long_edges = [e for e in edges if e[2] >= mean_len] or edges
    best_mid, best_edge, best_proj = centroid, None, -1e9
    for a, b, _ in long_edges:
        mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        proj = (mid[0] - centroid[0]) * ox + (mid[1] - centroid[1]) * oy
        if proj > best_proj:
            best_proj, best_mid, best_edge = proj, mid, (a, b)
    if best_edge is None:
        _draw_outward_label(disp, zone, centroid, maze_center, color, dw, dh)
        return
    (ax, ay), (bx, by) = best_edge
    angle = math.degrees(math.atan2((by - ay) * dh, (bx - ax) * dw))
    if angle > 90:
        angle -= 180
    elif angle <= -90:
        angle += 180
    cx = (best_mid[0] + ox * LABEL_MARGIN) * dw
    cy = (best_mid[1] + oy * LABEL_MARGIN) * dh
    _rotated_text_at(disp, zone.name, cx, cy, angle, color, dw, dh)


def _draw_zone_label(disp, zone, centroid, maze_center, color, dw, dh) -> None:
    """Dispatch a zone's label to the right peripheral placement."""
    name = zone.name
    if name in _TOP_ZONES:
        top_y = min(p[1] for p in zone.polygon)
        # Spread the top row: bias each CHOICE label out over its zone's outer
        # top corner so it clears the centred JUNCTION label.
        if name == "LEFT_CHOICE":
            cx = min(p[0] for p in zone.polygon)
        elif name == "RIGHT_CHOICE":
            cx = max(p[0] for p in zone.polygon)
        else:
            cx = centroid[0]
        _text_at(disp, name, cx * dw, (top_y - LABEL_MARGIN) * dh,
                 color, dw, dh, halign="center", valign="bottom")
    elif name == "HUB":
        left_x = min(p[0] for p in zone.polygon)
        _text_at(disp, name, (left_x - LABEL_MARGIN) * dw, centroid[1] * dh,
                 color, dw, dh, halign="right", valign="center")
    elif name in _RETURN_ZONES:
        _draw_return_label(disp, zone, centroid, maze_center, color, dw, dh)
    else:  # CENTRAL_STEM and any unrecognised zone
        _draw_outward_label(disp, zone, centroid, maze_center, color, dw, dh)


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
        # Maze centre (normalized): the mean of every zone's centroid. Labels are
        # pushed away from this point toward each zone's outer edge / periphery.
        drawn = [z for z in zone_map.zones if len(z.polygon) >= 3]
        centroids = {
            id(z): (sum(p[0] for p in z.polygon) / len(z.polygon),
                    sum(p[1] for p in z.polygon) / len(z.polygon))
            for z in drawn
        }
        if centroids:
            mcx = sum(c[0] for c in centroids.values()) / len(centroids)
            mcy = sum(c[1] for c in centroids.values()) / len(centroids)
        else:
            mcx = mcy = 0.5
        for z in drawn:
            pts = np.array([[int(x * dw), int(y * dh)] for x, y in z.polygon],
                           np.int32)
            color = _hex_to_bgr(ZONE_HEX.get(z.name, "#ffffff"))
            thick = 3 if z.name == current_zone else 1
            cv2.polylines(disp, [pts], True, color, thick)
            _draw_zone_label(disp, z, centroids[id(z)], (mcx, mcy),
                             color, dw, dh)
        if zone_map.well is not None and len(zone_map.well) >= 3:
            pts = np.array([[int(x * dw), int(y * dh)] for x, y in zone_map.well],
                           np.int32)
            well_color = _hex_to_bgr(WELL_HEX)
            cv2.polylines(disp, [pts], True, well_color, 3 if in_well else 1)
            # REWARD label below the whole rig (max y over zones + well).
            wcx = sum(p[0] for p in zone_map.well) / len(zone_map.well)
            bottom = max([p[1] for z in drawn for p in z.polygon]
                         + [p[1] for p in zone_map.well])
            _text_at(disp, "REWARD", wcx * dw, (bottom + LABEL_MARGIN) * dh,
                     well_color, dw, dh, halign="center", valign="top")
        if zone_map.stem_base is not None and zone_map.stem_tip is not None:
            a = (int(zone_map.stem_base[0] * dw), int(zone_map.stem_base[1] * dh))
            b = (int(zone_map.stem_tip[0] * dw), int(zone_map.stem_tip[1] * dh))
            cv2.arrowedLine(disp, a, b, _hex_to_bgr(AXIS_HEX), 2, tipLength=0.08)
    except Exception:
        pass
