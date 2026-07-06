"""
rig_view.py - live maze illustration module for experiment_control.py.

Draws a schematic of the Y/T maze and lights a marker at each body-sensor,
lick-port, and reward-solenoid location as protocol-v2 lines arrive from the rig
broker. Sensor/lick markers track on/off edges (S/L); solenoid markers flash
momentarily on each SOL pulse (the ack tmaze_test emits for an R<port><sol>
command) and on each task REWARD line (cue-guided-task emits REWARD, not SOL).

Design (functional core + thin Tk shell):
  - parse_line() and update() are pure: no Tk, no I/O. They turn a serial line
    into an event and fold it into an immutable RigState.
  - RigView is the only side-effecting part: it builds the canvas once, recolors
    sensor/lick markers when the state changes, and flashes solenoid markers
    (which are momentary, so they live in the view, not in RigState).

Marker positions are best-effort from the rig photos/sketch
(os_code_t_maze_resources/rig-hardware/circuit_pictures_small: IMG_1136,
IMG_1302_annotated, IMG_1303_annotated) and are isolated in the layout block
below, so they are easy to nudge after a first run.

Sensor markers are labeled with the cue-guided-task roles and ordered to match
the firmware SENSOR_PINS order (ENTRY, EXIT, L_CHOICE, R_CHOICE, L_RETURN,
R_RETURN), so "S,i" lights marker i.

Solenoid markers are keyed by (port, sol) and labeled by port and function
(e.g. "L.R1", "C.drain"), so when a SOL ack arrives the matching marker flashes.
There are fifteen, five per port: sol 0-2 = reward (R1/R2/R3), sol 3 = drain,
sol 4 = rinse (the marker outline color also encodes the role). The fire command
for a marker is "R<port><sol>" (port L/R/C = 0/1/2), shown on the experiment_-
control buttons. The on-canvas position of each solenoid is a best-effort GUESS;
the pin -> physical-location mapping is TBD - fire each command and confirm which
valve actually clicks, then nudge the positions below.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, replace
from typing import Optional, Tuple, Union

import protocol
from rig_config import load_rig_config
from tooltips import canvas_tip

# firmware/common/rig_config.h is the single source of truth for the pin map;
# read it once so lick roles / the reward column can't drift from the firmware.
# Degrades gracefully (ok == False) if the header can't be parsed. NOTE: trusts
# flashed == source (see rig_config.py) - reflash the Due after editing it.
_RIG_CFG = load_rig_config()

# ============================= Pure: events =============================


@dataclass(frozen=True)
class SensorEdge:
    index: int
    active: bool


@dataclass(frozen=True)
class LickEdge:
    index: int
    active: bool


@dataclass(frozen=True)
class SolenoidPulse:
    """A momentary solenoid fire, from a "SOL,<port>,<sol>" ack."""
    port: int
    sol: int


Event = Union[SensorEdge, LickEdge, SolenoidPulse]

# Which solenoid column the task's REWARD line maps to (so the reward marker
# flashes even though the task emits REWARD, not SOL). Read from
# REWARD_SOLENOID_INDEX in firmware/common/rig_config.h (1 == R2), with a
# hard-coded fallback if the header can't be parsed.
REWARD_SOL_INDEX = _RIG_CFG.reward_sol_index if _RIG_CFG.ok and _RIG_CFG.reward_sol_index is not None else 1
_REWARD_PORT = {"LEFT": 0, "RIGHT": 1, "CENTER": 2, "START": 2}


def parse_line(line: str) -> Optional[Event]:
    """Parse a firmware line into an Event (via the shared protocol parser).

    Handles "<ms>,S|L,<i>,<state>" (sensor/lick edge), "<ms>,SOL,<port>,<sol>"
    (solenoid pulse), and "<ms>,REWARD,<LEFT|RIGHT|CENTER>" (task reward, mapped
    to the reward-column marker at that port). Returns None otherwise. Pure.
    """
    edge = protocol.sensor_edge(line)
    if edge is not None:
        return SensorEdge(*edge)
    edge = protocol.lick_edge(line)
    if edge is not None:
        return LickEdge(*edge)
    pulse = protocol.sol_pulse(line)
    if pulse is not None:
        return SolenoidPulse(*pulse)
    parsed = protocol.parse(line)
    if parsed is not None and parsed.type == "REWARD" and parsed.fields:
        port = _REWARD_PORT.get(parsed.fields[0].strip().upper())
        return SolenoidPulse(port, REWARD_SOL_INDEX) if port is not None else None
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
CANVAS_H = 560            # extra bottom margin for the center-port solenoids

MARKER_R = 12
SOL_MARKER_R = 7         # solenoids are smaller, three-to-a-cluster
SOL_FLASH_MS = 300       # how long a solenoid marker stays lit per pulse

# Legend anchors: in the empty triangles flanking the funnel's lower half.
LEGEND_LEFT_X, LEGEND_LEFT_Y = 16, 384     # bottom-left: marker states
LEGEND_RIGHT_X, LEGEND_RIGHT_Y = 316, 414  # bottom-right: solenoid role key

COLOR_IDLE = "#cfcfcf"
COLOR_SENSOR = "#2ca02c"   # green
COLOR_LICK = "#1f77b4"     # blue
COLOR_SOLENOID = "#ff7f0e" # orange flash (fill) on any SOL pulse
# Solenoid role shown by the marker OUTLINE (fill flashes orange when pulsed):
COLOR_SOL_REWARD = "#333333"  # sol 0-2 (R1/R2/R3)
COLOR_SOL_DRAIN = "#8c564b"   # sol 3   (brown)
COLOR_SOL_RINSE = "#9467bd"   # sol 4   (purple)
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

# Lick markers: index 0..2 == L,i. The (x, y) positions are the rig geometry
# verified on the bench (index 2 = bottom-center start port); the role text in
# each label is read from firmware/common/rig_config.h via rig_config.py, so it
# can't drift from the firmware. If the header's role aliases disagree with the
# geometry here, that mismatch is the bug to fix in the header (then reflash).
def _lick_marker_label(index: int) -> str:
    if _RIG_CFG.ok:
        role = _RIG_CFG.lick_role(index)
        if role:
            return f"L{index} ({role})"
    return f"L{index}"


LICK_MARKERS = (
    Marker(80, 92, _lick_marker_label(0)),    # left end of top T-arm
    Marker(400, 92, _lick_marker_label(1)),   # right end of top T-arm
    Marker(240, 485, _lick_marker_label(2)),  # bottom-center (start port)
)


@dataclass(frozen=True)
class SolenoidMarker:
    port: int
    sol: int
    x: int
    y: int
    label: str   # port-function role, e.g. "L\u00b7R1", "C\u00b7drain"


# Solenoids: fifteen total, five per port (sol 0-2 = reward R1/R2/R3, sol 3 =
# drain, sol 4 = rinse - per SOLENOID_TABLE and the green-circled valves in the
# rig photos). Keyed by (port, sol) so a "SOL,<port>,<sol>" ack flashes the
# matching marker. ports: 0=left, 1=right, 2=center/start. Role is shown by both
# the label (port-function, e.g. "L.R1") and the marker outline color; the fire
# command is "R<port><sol>".
#
# POSITIONS ARE SCHEMATIC GUESSES laid out per-port (left edge, right edge, and a
# bottom row), loosely following the photo (drain above / rinse below the reward
# cluster; center: rinse, drain, then the three reward). The command -> physical-
# valve mapping is unknown: fire one command at a time, confirm which valve
# clicks, then nudge x/y here.
SOLENOID_MARKERS = (
    # Left port: vertical column on the left edge (drain, R1-3, rinse).
    SolenoidMarker(0, 3, 40, 110, "L\u00b7drain"),
    SolenoidMarker(0, 0, 40, 136, "L\u00b7R1"),
    SolenoidMarker(0, 1, 40, 162, "L\u00b7R2"),
    SolenoidMarker(0, 2, 40, 188, "L\u00b7R3"),
    SolenoidMarker(0, 4, 40, 214, "L\u00b7rinse"),
    # Right port: vertical column on the right edge (drain, R1-3, rinse).
    SolenoidMarker(1, 3, 440, 110, "R\u00b7drain"),
    SolenoidMarker(1, 0, 440, 136, "R\u00b7R1"),
    SolenoidMarker(1, 1, 440, 162, "R\u00b7R2"),
    SolenoidMarker(1, 2, 440, 188, "R\u00b7R3"),
    SolenoidMarker(1, 4, 440, 214, "R\u00b7rinse"),
    # Center/start port: bottom row below the start box (rinse, drain, R1-3).
    SolenoidMarker(2, 4, 150, 532, "C\u00b7rinse"),
    SolenoidMarker(2, 3, 188, 532, "C\u00b7drain"),
    SolenoidMarker(2, 0, 236, 532, "C\u00b7R1"),
    SolenoidMarker(2, 1, 264, 532, "C\u00b7R2"),
    SolenoidMarker(2, 2, 292, 532, "C\u00b7R3"),
)


def _sol_outline(sol: int) -> str:
    if sol <= 2:
        return COLOR_SOL_REWARD
    if sol == 3:
        return COLOR_SOL_DRAIN
    return COLOR_SOL_RINSE


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
        # Solenoids: (port, sol) -> canvas item, plus a pending after-job per
        # marker so rapid repeats just restart the flash instead of stacking.
        self._sol_items = {
            (sm.port, sm.sol): self._make_sol_marker(sm) for sm in SOLENOID_MARKERS
        }
        self._sol_jobs: dict = {}
        self._draw_legend()
        self._add_marker_tooltips()

    def _add_marker_tooltips(self) -> None:
        """Attach hover tooltips (text from tooltip_text.py) to every marker."""
        for i, item in enumerate(self._sensor_items):
            canvas_tip(self.canvas, item, "marker.sensor", index=i)
        for i, item in enumerate(self._lick_items):
            canvas_tip(self.canvas, item, "marker.lick", index=i)
        for sm in SOLENOID_MARKERS:
            canvas_tip(self.canvas, self._sol_items[(sm.port, sm.sol)],
                       "marker.solenoid", port=sm.port, sol=sm.sol)

    # ---- module contract ----
    def widget(self) -> tk.Widget:
        return self.canvas

    def apply(self, line: str) -> None:
        event = parse_line(line)
        new_state = update(self.state, event)
        if new_state != self.state:
            self.state = new_state
            self._render()
        if isinstance(event, SolenoidPulse):
            self._flash_solenoid(event.port, event.sol)

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

    def _make_sol_marker(self, sm: SolenoidMarker) -> int:
        r = SOL_MARKER_R
        circle = self.canvas.create_oval(
            sm.x - r, sm.y - r, sm.x + r, sm.y + r,
            fill=COLOR_IDLE, outline=_sol_outline(sm.sol), width=2,
        )
        cx = CANVAS_W / 2
        # Side columns sit in narrow margins, so role labels read inward (over
        # the open floor) to avoid clipping off-canvas; bottom row labels below.
        if sm.x < cx - 120:        # left margin -> label inward (right)
            tx, ty, anchor = sm.x + r + 3, sm.y, "w"
        elif sm.x > cx + 120:      # right margin -> label inward (left)
            tx, ty, anchor = sm.x - r - 3, sm.y, "e"
        else:                      # bottom cluster -> label below
            tx, ty, anchor = sm.x, sm.y + r + 3, "n"
        self.canvas.create_text(tx, ty, text=sm.label, anchor=anchor,
                                font=("TkDefaultFont", 7))
        return circle

    def _flash_solenoid(self, port: int, sol: int) -> None:
        item = self._sol_items.get((port, sol))
        if item is None:
            return
        self.canvas.itemconfig(item, fill=COLOR_SOLENOID)
        job = self._sol_jobs.get((port, sol))
        if job is not None:
            try:
                self.canvas.after_cancel(job)
            except Exception:
                pass
        self._sol_jobs[(port, sol)] = self.canvas.after(
            SOL_FLASH_MS, lambda: self.canvas.itemconfig(item, fill=COLOR_IDLE))

    def _legend_row(self, x: int, y: int, fill: str, outline: str, text: str) -> int:
        swatch = self.canvas.create_oval(x, y, x + 14, y + 14, fill=fill,
                                         outline=outline, width=2)
        self.canvas.create_text(x + 22, y + 7, anchor="w", text=text,
                                font=("TkDefaultFont", 8))
        return swatch

    def _draw_legend(self) -> None:
        # The legend lives in the empty triangles flanking the funnel's lower
        # half, so it never collides with the top reward ports / lick markers.
        # Bottom-left: marker states (fill color). Bottom-right: solenoid role
        # (outline color, matching the idle markers).
        canvas_tip(self.canvas, self._legend_row(
            LEGEND_LEFT_X, LEGEND_LEFT_Y, COLOR_SENSOR, "#333333",
            "sensor active"), "legend.sensor")
        canvas_tip(self.canvas, self._legend_row(
            LEGEND_LEFT_X, LEGEND_LEFT_Y + 20, COLOR_LICK, "#333333",
            "lick active"), "legend.lick")
        canvas_tip(self.canvas, self._legend_row(
            LEGEND_LEFT_X, LEGEND_LEFT_Y + 40, COLOR_SOLENOID, "#333333",
            "solenoid pulsed"), "legend.solenoid")

        self.canvas.create_text(LEGEND_RIGHT_X, LEGEND_RIGHT_Y - 16, anchor="w",
                                text="solenoid role:", font=("TkDefaultFont", 8))
        canvas_tip(self.canvas, self._legend_row(
            LEGEND_RIGHT_X, LEGEND_RIGHT_Y, COLOR_IDLE, COLOR_SOL_REWARD,
            "reward"), "legend.role.reward")
        canvas_tip(self.canvas, self._legend_row(
            LEGEND_RIGHT_X, LEGEND_RIGHT_Y + 20, COLOR_IDLE, COLOR_SOL_DRAIN,
            "drain"), "legend.role.drain")
        canvas_tip(self.canvas, self._legend_row(
            LEGEND_RIGHT_X, LEGEND_RIGHT_Y + 40, COLOR_IDLE, COLOR_SOL_RINSE,
            "rinse"), "legend.role.rinse")

    def _render(self) -> None:
        for i, item in enumerate(self._sensor_items):
            self.canvas.itemconfig(
                item, fill=COLOR_SENSOR if self.state.sensors[i] else COLOR_IDLE)
        for i, item in enumerate(self._lick_items):
            self.canvas.itemconfig(
                item, fill=COLOR_LICK if self.state.licks[i] else COLOR_IDLE)
