"""
trial_control_bar.py - operator trial-control buttons for the Rig view.

A small Tk widget: a vertical column of buttons the operator uses to steer a live
host-owned Phase 3 run (docs/decisions/0046) without leaving the Rig view - Skip,
Repeat, Back, +Trials, Restart, and Pause/Resume. It fills the empty right-hand
area beside the trial ribbon and the live-stats panel (docs/decisions/0058).

It owns no task state: each button calls a callback wired to the live Phase 3
controller (``Phase3ControlView``), which routes the action through its actuation
thread (the sole task-state mutator). The bar only REFLECTS state - it self-polls
the published ``Phase3StateSnapshot`` (like the ribbon's pull mode, docs/decisions/
0057) to enable/disable each button and to switch the Pause button to Resume.

Module contract (so the Rig view registers it like any other module):
    widget() -> the Tk widget to place
    apply(info) -> None   no-op (it reads the snapshot, not serial lines)
    close() -> None       cancel the self-poll
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Optional

from tmaze.core.phase3.phase3_log import Phase3StateSnapshot

try:
    from tmaze.ui.tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# Self-poll cadence for enable/disable + the Pause/Resume label; matches the
# ribbon / behavior panels so the whole right column refreshes in step.
POLL_INTERVAL_MS = 150

# Button spec: (key, label, tooltip_key). ``key`` indexes the callbacks dict and
# selects the enable rule in _refresh().
_BUTTONS = (
    ("skip", "Skip trial", "phase3.skip"),
    ("repeat", "Repeat trial", "phase3.repeat"),
    ("back", "Back", "phase3.back"),
    ("extend", "+Trials", "phase3.extend"),
    ("restart", "Restart", "phase3.restart"),
    ("pause", "Pause", "phase3.pause"),
    # Manual override of the closed-loop debias scheduler (debias_blocks /
    # intervention_blocks generators): toggles a side-bias corrective block on/off.
    # Label + enable are set in _refresh from the published scheduler state (like
    # Pause/Resume). See debias.py.
    ("debias", "Start debias block", "phase3.debias_toggle"),
    # Manual override of the de-alternation block (intervention_blocks generator
    # only): toggles a corrective block that forces the last choice. See
    # alternation.py / intervention_stack.py.
    ("alt", "Start alt block", "phase3.alt_toggle"),
    # Manual unrewarded probe block (probe_blocks.py). Unlike the two toggles
    # above this is live under ANY generator (probes are orthogonal to the
    # closed-loop stack); enable = armed and probe_enabled. Label + enable set in
    # _refresh from the published probe state.
    ("probe", "Start probe block", "phase3.probe_toggle"),
)


class TrialControlBar:
    """Tk widget: the vertical column of Phase 3 operator buttons."""

    def __init__(self, parent: tk.Misc, *,
                 callbacks: Dict[str, Callable[[], None]],
                 state_getter: Callable[[], Optional[Phase3StateSnapshot]],
                 paused_getter: Optional[Callable[[], bool]] = None,
                 poll_ms: int = POLL_INTERVAL_MS,
                 text: str = "Trial controls"):
        self._callbacks = callbacks
        self._state_getter = state_getter
        self._paused_getter = paused_getter
        self._poll_ms = poll_ms
        self._closed = False
        self._job: Optional[str] = None

        self._frame = ttk.LabelFrame(parent, text=text)
        tip(self._frame, "phase3.controls")
        self._buttons: Dict[str, ttk.Button] = {}
        for row, (key, label, tkey) in enumerate(_BUTTONS):
            b = ttk.Button(self._frame, text=label, width=12,
                           command=self._make_handler(key))
            b.grid(row=row, column=0, sticky="ew", padx=6,
                   pady=(6 if row == 0 else 3, 3))
            tip(b, tkey)
            self._buttons[key] = b
        self._frame.columnconfigure(0, weight=1)

        self._schedule_poll()

    # ------------------------------------------------------- module contract
    def widget(self) -> tk.Widget:
        return self._frame

    def apply(self, _info) -> None:
        return None

    def close(self) -> None:
        self._closed = True
        if self._job is not None:
            try:
                self._frame.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    # ------------------------------------------------------------ internals
    def _make_handler(self, key: str) -> Callable[[], None]:
        def handler() -> None:
            cb = self._callbacks.get(key)
            if cb is not None:
                try:
                    cb()
                except Exception:
                    pass
        return handler

    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._refresh()
        except tk.TclError:
            return
        try:
            self._job = self._frame.after(self._poll_ms, self._schedule_poll)
        except tk.TclError:
            self._job = None

    def _refresh(self) -> None:
        """Enable/disable each button from the published snapshot and switch the
        Pause label. Skip/Repeat/Back/Extend/Pause need a live (armed) session;
        Back additionally needs a trial to go back to; Restart only needs a
        session to have been armed at least once (its rows persist while disarmed).
        """
        try:
            state = self._state_getter() if self._state_getter else None
        except Exception:
            state = None
        has_state = state is not None
        armed = bool(has_state and state.armed)
        current = state.current_index if has_state else 0
        # The debias toggle is live under any closed-loop generator; the alt toggle
        # only under intervention_blocks (the snapshot carries the generator, the
        # current scheduler state, and which kind owns the active block).
        generator = getattr(state, "generator", "balanced") if has_state else "balanced"
        debias_on = bool(has_state and generator != "balanced")
        alt_on = bool(has_state and generator == "intervention_blocks")
        in_block = bool(has_state
                        and getattr(state, "debias_state", "NORMAL") == "INTERVENTION")
        active_kind = getattr(state, "active_kind", "") if has_state else ""
        # Probe is orthogonal to the generator: live whenever armed and probe blocks
        # are enabled this run (the snapshot carries both). See probe_blocks.py.
        probe_on = bool(has_state and getattr(state, "probe_enabled", False))
        probe_state = getattr(state, "probe_state", "NORMAL") if has_state else "NORMAL"
        enabled = {
            "skip": armed,
            "repeat": armed,
            "back": armed and current > 0,
            "extend": armed,
            "restart": has_state,
            "pause": armed,
            "debias": armed and debias_on,
            "alt": armed and alt_on,
            "probe": armed and probe_on,
        }
        for key, b in self._buttons.items():
            b.configure(state=("normal" if enabled.get(key) else "disabled"))
        if self._paused_getter is not None:
            try:
                paused = bool(self._paused_getter())
            except Exception:
                paused = False
            self._buttons["pause"].configure(
                text="Resume" if paused else "Pause")
        # Toggle each intervention button's label to match the running block's kind,
        # so only the button whose kind is active reads "End ... block".
        if "debias" in self._buttons:
            db_active = in_block and active_kind in ("side_bias", "")
            self._buttons["debias"].configure(
                text="End debias block" if db_active else "Start debias block")
        if "alt" in self._buttons:
            alt_active = in_block and active_kind == "alternation"
            self._buttons["alt"].configure(
                text="End alt block" if alt_active else "Start alt block")
        # The probe toggle reads "End probe block" while a probe is running OR still
        # pending (so a mistaken request can be cancelled before it starts).
        if "probe" in self._buttons:
            probe_active = probe_state in ("ACTIVE", "PENDING")
            self._buttons["probe"].configure(
                text="End probe block" if probe_active else "Start probe block")
