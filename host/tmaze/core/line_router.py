"""
line_router.py - the single serial-line fan-out + MODE-switch reset seam.

The GUI (experiment_control.py) receives one firmware line at a time and must
hand it to every tab/module. Before this module, on_line parsed the line for the
bottom-strip tallies and then passed the RAW string to each module, which each
re-parsed it - N+1 parses per line - and every stateful consumer noticed a
firmware MODE switch ("new run, drop stale state") on its own, so adding a new
stateful module meant remembering to wire in its own reset (see
docs/future-work.md FW-0021 / FW-0022).

LineRouter is the one seam for both. The caller parses ONCE via
protocol.classify() and calls dispatch(info); the router:

  - fires a single mode-changed notification (with the mode name) to every
    subscriber whenever the line is a "<ms>,MODE,<name>" line, and
  - hands the pre-parsed protocol.LineInfo to each module's apply(info).

A module opts into the reset seam simply by defining on_mode_changed(mode); it
is auto-subscribed when added (duck-typed, like the optional close() hook). A
module that needs no reset just doesn't define it. Non-module subscribers (e.g.
the reward/lick tallies, the mode label) register via subscribe_mode().

Pure stdlib (no Tk/serial), so it is headlessly testable
(test_line_router.py). See docs/decisions/0056.
"""

from __future__ import annotations

from typing import Callable, List

from tmaze.core.protocol import LineInfo

ModeCallback = Callable[[str], None]


class LineRouter:
    """Fan one parsed serial line out to modules + a shared mode-reset seam."""

    def __init__(self) -> None:
        self._modules: List[object] = []
        self._mode_subs: List[ModeCallback] = []

    def add_module(self, module: object) -> None:
        """Register a module (anything with apply(info)). If it also defines
        on_mode_changed(mode), auto-subscribe that to the mode-reset seam."""
        self._modules.append(module)
        cb = getattr(module, "on_mode_changed", None)
        if callable(cb):
            self._mode_subs.append(cb)

    def subscribe_mode(self, callback: ModeCallback) -> None:
        """Register a non-module callback fired on every firmware MODE switch,
        receiving the new mode name (e.g. a tally reset, the mode label)."""
        self._mode_subs.append(callback)

    def dispatch(self, info: LineInfo) -> None:
        """Route one parsed line: fire the mode-reset seam on a MODE line
        (before modules see it), then hand ``info`` to every module."""
        if info.mode is not None:
            for cb in self._mode_subs:
                cb(info.mode)
        for module in self._modules:
            module.apply(info)  # type: ignore[attr-defined]
