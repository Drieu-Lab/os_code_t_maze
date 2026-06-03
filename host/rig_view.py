"""
rig_view.py - live maze illustration module for experiment_control.py.

Draws a schematic of the Y/T maze and lights a marker at each body-sensor and
lick-port location as protocol-v2 edges arrive from the rig broker.

Design (functional core + thin Tk shell):
  - parse_line() and update() are pure: no Tk, no I/O. They turn a serial line
    into an event and fold it into an immutable RigState.
  - RigView is the only side-effecting part: it builds the canvas once and just
    recolors marker items when the state changes.

Marker positions are best-effort from the rig photos/sketch
(os_code_t_maze_resources/rig-hardware/circuit_pictures_small: IMG_1136,
IMG_1302_annotated, IMG_1303_annotated) and are isolated in the layout block
below, so they are easy to nudge after a first run.

Sensor markers are labeled with the cue-guided-task roles and ordered to match
the firmware SENSOR_PINS order (ENTRY, EXIT, L_CHOICE, R_CHOICE, L_RETURN,
R_RETURN), so "S,i" lights marker i.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, replace
from typing import Optional, Tuple, Union

# ============================= Pure: events =============================


@dataclass(frozen=True)
class SensorEdge:
    index: int
    active: bool


@dataclass(frozen=True)
class LickEdge:
    index: int
    active: bool


Event = Union[SensorEdge, LickEdge]


def parse_line(line: str) -> Optional[Event]:
    """Parse a protocol-v2 line "<ms>,S|L,<i>,<state>" into an Event.

    Returns None for any line that is not a sensor/lick edge. Pure.
    """
    parts = line.split(",")
    if len(parts) < 4 or not parts[0].isdigit():
        return None
    typ = parts[1]
    try:
        index = int(parts[2])
        state = int(parts[3])
    except ValueError:
        return None
    if typ == "S":
        return SensorEdge(index, state == 1)
    if typ == "L":
        return LickEdge(index, state == 1)
    return None


# ============================= Pure: state ==============================


@dataclass(frozen=True)
class RigState:
    sensors: Tuple[bool, ...]  # length 6, indexed by S,i
    licks: Tuple[bool, ...]    # length 3, indexed by L,i


def initial_state() -> RigState:
    return RigState(sensors=(False,) * 6, licks=(False,) * 3)


def _set(values: Tuple[bool, ...], index: int, active: bool) -> Tuple[bool, ...]:
    items = list(values)
    items[index] = active
    return tuple(items)


def update(state: RigState, event: Optional[Event]) -> RigState:
    """Fold an event into the state, returning a new RigState. Pure."""
    if isinstance(event, SensorEdge) and 0 <= event.index < len(state.sensors):
        return replace(state, sensors=_set(state.sensors, event.index, event.active))
    if isinstance(event, LickEdge) and 0 <= event.index < len(state.licks):
        return replace(state, licks=_set(state.licks, event.index, event.active))
    return state


# ====================== Pure: layout (editable) =========================
# Canvas is CANVAS_W x CANVAS_H. Geometry matches the real rig (see rig photos
# IMG_1136 sketch + IMG_1302_annotated): a funnel/trapezoid footprint - a narrow
# start box at bottom-center (Lick1), outer walls fanning up to the two top
# reward corners (Lick3 top-left, Lick2 top-right) - with two central divider
# wedges (islands) that separate the central stem from the side/return arms.
#
# All coordinates are isolated here so they are easy to nudge after a run.

CANVAS_W = 480
CANVAS_H = 540

MARKER_R = 12

COLOR_IDLE = "#cfcfcf"
COLOR_SENSOR = "#2ca02c"   # green
COLOR_LICK = "#1f77b4"     # blue
COLOR_FLOOR = "#f2f2f2"
COLOR_WEDGE = "#4a4a4a"
COLOR_WALL = "#333333"

# Maze footprint polygon (the walkable floor): a funnel - a wide top (the T-arm
# spanning the two reward ports), outer return walls slanting down to a narrow
# start box at the bottom.
#   top-left -> top-right -> right wall -> start box (R,bottom-R,bottom-L,L) -> left wall
MAZE_FOOTPRINT = (
    (60, 70), (420, 70),          # top wall == top T-arm (reward ports at its ends)
    (280, 460),                   # right return wall down to start box
    (280, 510), (200, 510),       # start box bottom
    (200, 460),                   # left side of start box up to left return wall
)

# Two WIDE central divider wedges (dark islands), each a triangle with apex
# pointing down toward the start. They separate the central stem (between them)
# from the left/right return arms (outside them), and their flat tops sit below
# the top T-arm so the choice beams read as being on the arm, not the stem.
LEFT_WEDGE = ((118, 128), (220, 128), (208, 430))
RIGHT_WEDGE = ((260, 128), (362, 128), (272, 430))
MAZE_WEDGES = (LEFT_WEDGE, RIGHT_WEDGE)


@dataclass(frozen=True)
class Marker:
    x: int
    y: int
    label: str


# Sensor markers: index 0..5 == S,i, in firmware SENSOR_PINS / Beam order
# (0 ENTRY, 1 EXIT, 2 L_CHOICE, 3 R_CHOICE, 4 L_RETURN, 5 R_RETURN). Placement
# follows the hand-drawn schematic (IMG_1136): ENTRY/EXIT on the central stem,
# the CHOICE beams up on the top T-arm toward the rewards, and the RETURN beams
# on the outer return arms - a different segment from the choices.
SENSOR_MARKERS = (
    Marker(240, 360, "ENTRY"),     # 0 - central stem, lower (Path2)
    Marker(240, 175, "EXIT"),      # 1 - central stem, upper / T-junction (Path5)
    Marker(158, 100, "L_CHOICE"),  # 2 - top T-arm, left toward left reward (Path6)
    Marker(322, 100, "R_CHOICE"),  # 3 - top T-arm, right toward right reward (Path4)
    Marker(156, 300, "L_RETURN"),  # 4 - left return arm (Path1)
    Marker(324, 300, "R_RETURN"),  # 5 - right return arm (Path3)
)

# Lick markers: index 0..2 == L,i.
# Firmware LICK_PINS = {35, 36, 37}. Mapping to physical ports (Lick3 left,
# Lick2 right, Lick1 start) is the working assumption - verify on the rig.
LICK_MARKERS = (
    Marker(80, 92, "L0 (Lick3)"),    # left reward port (left end of top T-arm)
    Marker(400, 92, "L1 (Lick2)"),   # right reward port (right end of top T-arm)
    Marker(240, 485, "L2 (Lick1)"),  # start port (bottom-center)
)


# ========================= Side-effecting view ==========================


class RigView:
    """Tk module: draws the maze and recolors markers on state changes.

    Module contract used by experiment_control.py: widget() and apply(line).
    """

    def __init__(self, parent: tk.Misc):
        self.state = initial_state()
        self.canvas = tk.Canvas(
            parent, width=CANVAS_W, height=CANVAS_H,
            bg="white", highlightthickness=0,
        )
        self._draw_maze()
        self._sensor_items = [self._make_marker(m) for m in SENSOR_MARKERS]
        self._lick_items = [self._make_marker(m) for m in LICK_MARKERS]
        self._draw_legend()

    # ---- module contract ----
    def widget(self) -> tk.Widget:
        return self.canvas

    def apply(self, line: str) -> None:
        new_state = update(self.state, parse_line(line))
        if new_state != self.state:
            self.state = new_state
            self._render()

    # ---- drawing ----
    def _draw_maze(self) -> None:
        self.canvas.create_polygon(
            self._flatten(MAZE_FOOTPRINT),
            fill=COLOR_FLOOR, outline=COLOR_WALL, width=3,
        )
        for wedge in MAZE_WEDGES:
            self.canvas.create_polygon(
                self._flatten(wedge), fill=COLOR_WEDGE, outline=COLOR_WEDGE,
            )

    @staticmethod
    def _flatten(points) -> list:
        return [c for pt in points for c in pt]

    def _make_marker(self, m: Marker) -> int:
        r = MARKER_R
        circle = self.canvas.create_oval(
            m.x - r, m.y - r, m.x + r, m.y + r,
            fill=COLOR_IDLE, outline="#333333", width=2,
        )
        # Label clear of the corridors: top T-arm markers label above; others
        # label outboard of center (left/right) or below for the central stem.
        cx = CANVAS_W / 2
        if m.y < 130:
            tx, ty, anchor = m.x, m.y - r - 4, "s"
        elif m.x < cx - 40:
            tx, ty, anchor = m.x - r - 4, m.y, "e"
        elif m.x > cx + 40:
            tx, ty, anchor = m.x + r + 4, m.y, "w"
        else:
            tx, ty, anchor = m.x, m.y + r + 9, "n"
        self.canvas.create_text(tx, ty, text=m.label, anchor=anchor,
                                font=("TkDefaultFont", 8))
        return circle

    def _draw_legend(self) -> None:
        x, y = 16, 16
        self.canvas.create_oval(x, y, x + 14, y + 14, fill=COLOR_SENSOR, outline="#333333")
        self.canvas.create_text(x + 22, y + 7, anchor="w", text="sensor active",
                                font=("TkDefaultFont", 8))
        self.canvas.create_oval(x, y + 20, x + 14, y + 34, fill=COLOR_LICK, outline="#333333")
        self.canvas.create_text(x + 22, y + 27, anchor="w", text="lick active",
                                font=("TkDefaultFont", 8))

    def _render(self) -> None:
        for i, item in enumerate(self._sensor_items):
            self.canvas.itemconfig(
                item, fill=COLOR_SENSOR if self.state.sensors[i] else COLOR_IDLE)
        for i, item in enumerate(self._lick_items):
            self.canvas.itemconfig(
                item, fill=COLOR_LICK if self.state.licks[i] else COLOR_IDLE)
