"""
task_view.py - the "Task" tab for experiment_control.py.

A thin container that groups the host-owned task controllers - Phase 3
(`phase3_controller.py`, the host-owned cued task) and Rules
(`rules_view.py`, the position-based reward-shaping engine) - under one
top-level tab with an exclusive controller selector, and owns the SINGLE
host-side Animal ID field (see docs/decisions/0053 phase 4, docs/future-work.md
FW-0005).

Why this exists:
  - Before, the Rules and Phase 3 controllers were two separate top-level tabs,
    and the animal id was entered in two unrelated places (the Phase 3 tab and
    the Session panel) with two copies of the same slug helper, which could even
    disagree. This tab collapses them: its Animal ID field is the one host-side
    id source, read by Phase 3 (its data-file name + log header) and by the
    Session panel (the session_id + manifest), and auto-filled from the firmware
    `ANIMAL` handshake.
  - The selector is EXCLUSIVE: only the chosen controller is shown, and
    switching disarms the other, so at most one controller can actuate reward.

This module holds no task logic; it composes the two child controllers (built
by dependency injection - the class objects are passed in by experiment_control,
keeping a single guarded-import site there) and forwards the module contract:

    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None   auto-fill the animal id + fan out to both children
    close() -> None            close both children

It also exposes:
    animal_id() -> str         the current shared animal-id text (callers slug it)
    .phase3                    the Phase 3 controller instance (or None), so the
                               Session panel + behavior readout can reach it as
                               before (docs/decisions/0048, 0050).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import List, Optional, Tuple

import protocol

try:
    from tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None


class TaskControlView:
    """Tk module: the Task tab - a shared Animal ID field + an exclusive
    controller selector over the Phase 3 and Rules controllers."""

    def __init__(self, parent: tk.Misc, *, tracker=None, camera=None,
                 pipeline=None, send=None, dispatcher=None, phase3_cls=None,
                 rules_cls=None):
        self.frame = ttk.Frame(parent)
        self._closed = False
        # (label, module) in selector order; the shared Animal ID var must exist
        # before the children are built so Phase 3 can read it via animal_id().
        self._panels: List[Tuple[str, object]] = []
        self._current_label = ""

        header = ttk.Frame(self.frame)
        header.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(6, 2))

        ttk.Label(header, text="Animal ID:").pack(side=tk.LEFT)
        self._animal_var = tk.StringVar(value="")
        animal = ttk.Entry(header, width=12, textvariable=self._animal_var)
        animal.pack(side=tk.LEFT, padx=(4, 12))
        tip(animal, "task.animal")

        self._body = ttk.Frame(self.frame)
        # The selector is built after the children so it can list only the ones
        # whose deps were available; pack the body first so it sits below it.
        self._body.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True,
                        padx=2, pady=2)

        # Build the children into the body (dependency-injected classes). Phase 3
        # gets the shared animal-id getter; Rules needs the tracker.
        self.phase3 = None
        self.rules = None
        if phase3_cls is not None:
            self.phase3 = phase3_cls(
                self._body, camera=camera, pipeline=pipeline, send=send,
                dispatcher=dispatcher, animal_id=self.animal_id)
            self._panels.append(("Phase 3", self.phase3))
        if rules_cls is not None:
            self.rules = rules_cls(
                self._body, tracker=tracker, camera=camera, pipeline=pipeline,
                send=send, dispatcher=dispatcher)
            self._panels.append(("Rules", self.rules))

        # Controller selector (only when there is a real choice to make).
        if len(self._panels) > 1:
            ttk.Label(header, text="Controller:").pack(side=tk.LEFT)
            self._selector_var = tk.StringVar()
            selector = ttk.Combobox(
                header, width=10, state="readonly",
                values=[label for label, _ in self._panels],
                textvariable=self._selector_var)
            selector.pack(side=tk.LEFT, padx=4)
            selector.bind("<<ComboboxSelected>>", self._on_select)
            tip(selector, "task.controller")

        if self._panels:
            # Default to the first controller (Phase 3 when present).
            first = self._panels[0][0]
            if len(self._panels) > 1:
                self._selector_var.set(first)
            self._show(first)
        else:
            ttk.Label(self.frame, padding=24, anchor="center",
                      justify="center",
                      text="Task tab unavailable - no task controllers loaded.").pack(
                fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, line: str) -> None:
        """Auto-fill the shared Animal ID from the firmware ANIMAL handshake when
        the field is empty (the field wins once the user types), then fan the line
        out to both controllers so each keeps its own firmware-mode / lick state
        regardless of which is currently shown."""
        animal = protocol.animal_id(line)
        if animal:
            try:
                if not self._animal_var.get().strip():
                    self._animal_var.set(animal)
            except tk.TclError:
                pass
        for _, module in self._panels:
            try:
                module.apply(line)
            except Exception:
                pass

    def close(self) -> None:
        self._closed = True
        for _, module in self._panels:
            closer = getattr(module, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    # ------------------------------------------------------ shared animal id
    def animal_id(self) -> str:
        """Current shared Animal ID text (unslugged; callers apply
        protocol.sanitize_animal_id). Empty string if the tab is unavailable."""
        try:
            return self._animal_var.get()
        except tk.TclError:
            return ""

    # --------------------------------------------------------- selector
    def _on_select(self, _evt=None) -> None:
        label = self._selector_var.get()
        self._show(label)

    def _show(self, label: str) -> None:
        """Show the selected controller and disarm every other one (exclusive:
        only the visible controller may actuate; docs/decisions/0053)."""
        if label == self._current_label:
            return
        self._current_label = label
        for name, module in self._panels:
            widget = module.widget()
            if name == label:
                widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                            padx=2, pady=2)
            else:
                widget.pack_forget()
                disarm = getattr(module, "disarm", None)
                if callable(disarm):
                    try:
                        disarm()
                    except Exception:
                        pass
