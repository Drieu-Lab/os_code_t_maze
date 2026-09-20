"""Tests for the Rig-view trial-control bar's enable rules (tmaze.ui.trial_control_bar).

Focus: the Increment-6c guard (docs/decisions/0187) that DISABLES the hand
Repeat/Back buttons while an intervention or probe block is actually running - so
re-running a scored trial can't change the running block's length - while leaving
them enabled in NORMAL (including under a closed-loop generator, where any
bias-window drift is instead surfaced by the ADR-0119 note).

These drive the pure ``_refresh()`` against a fake published snapshot, so they
assert the button state without a live session. Skipped cleanly when Tk has no
display (headless CI)."""

from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import tkinter as tk

from tmaze.ui.trial_control_bar import TrialControlBar


def _state(**over):
    """A fake Phase3StateSnapshot with the fields _refresh reads (defaults =
    an armed, mid-run, balanced, no-block session)."""
    base = dict(
        armed=True,
        current_index=2,
        generator="balanced",
        debias_state="NORMAL",
        active_kind="",
        probe_enabled=False,
        probe_armed=True,
        probe_state="NORMAL",
    )
    base.update(over)
    return types.SimpleNamespace(**base)


class TestTrialControlBarEnable(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:            # no display (headless CI)
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self._snap = _state()
        # No-op callbacks; poll disabled (we call _refresh directly).
        self.bar = TrialControlBar(
            self.root,
            callbacks={k: (lambda: None) for k in
                       ("skip", "repeat", "back", "extend", "restart",
                        "pause", "debias", "alt", "probe")},
            state_getter=lambda: self._snap,
            paused_getter=lambda: False,
            poll_ms=10_000_000)
        self.addCleanup(self.bar.close)

    def _btn_state(self, key: str) -> str:
        return str(self.bar._buttons[key].cget("state"))

    def _refresh_with(self, **over) -> None:
        self._snap = _state(**over)
        self.bar._refresh()

    def test_normal_balanced_run_allows_repeat_and_back(self):
        self._refresh_with()  # armed, balanced, NORMAL
        self.assertEqual(self._btn_state("repeat"), "normal")
        self.assertEqual(self._btn_state("back"), "normal")
        self.assertEqual(self._btn_state("skip"), "normal")

    def test_closed_loop_generator_in_normal_still_allows_edits(self):
        # A closed-loop generator is SELECTED but no block is running: the edit is
        # still allowed (the ADR-0119 note covers the small bias-window drift).
        self._refresh_with(generator="intervention_blocks", debias_state="NORMAL")
        self.assertEqual(self._btn_state("repeat"), "normal")
        self.assertEqual(self._btn_state("back"), "normal")

    def test_active_intervention_block_disables_repeat_and_back(self):
        self._refresh_with(generator="intervention_blocks",
                           debias_state="INTERVENTION", active_kind="side_bias")
        self.assertEqual(self._btn_state("repeat"), "disabled")
        self.assertEqual(self._btn_state("back"), "disabled")
        # Skip / +Trials stay live so the operator can still steer the block.
        self.assertEqual(self._btn_state("skip"), "normal")
        self.assertEqual(self._btn_state("extend"), "normal")

    def test_active_probe_block_disables_repeat_and_back_under_any_generator(self):
        # Probe is orthogonal to the generator: an ACTIVE probe block locks edits
        # even under the balanced generator.
        self._refresh_with(generator="balanced", probe_enabled=True,
                           probe_state="ACTIVE")
        self.assertEqual(self._btn_state("repeat"), "disabled")
        self.assertEqual(self._btn_state("back"), "disabled")

    def test_back_still_needs_a_trial_to_go_back_to(self):
        self._refresh_with(current_index=0)
        self.assertEqual(self._btn_state("back"), "disabled")
        self.assertEqual(self._btn_state("repeat"), "normal")

    def test_disarmed_disables_edits_regardless(self):
        self._refresh_with(armed=False)
        self.assertEqual(self._btn_state("repeat"), "disabled")
        self.assertEqual(self._btn_state("back"), "disabled")


if __name__ == "__main__":
    unittest.main()
