"""
test_line_router.py - headless unit tests for the serial fan-out + MODE-reset
seam (line_router.py). No hardware, no Tk. See docs/decisions/0056.

Run from host/:
    python -m unittest test_line_router
"""

from __future__ import annotations

import unittest

from tmaze.core import protocol
from tmaze.core.line_router import LineRouter


class _RecordingModule:
    """A module that records the LineInfo objects handed to its apply()."""

    def __init__(self) -> None:
        self.applied = []

    def apply(self, info) -> None:
        self.applied.append(info)


class _StatefulModule(_RecordingModule):
    """A stateful module that resets on a MODE switch via on_mode_changed()."""

    def __init__(self) -> None:
        super().__init__()
        self.resets = []

    def on_mode_changed(self, mode: str) -> None:
        self.resets.append(mode)


class TestLineRouter(unittest.TestCase):
    def test_apply_receives_parsed_lineinfo(self):
        router = LineRouter()
        mod = _RecordingModule()
        router.add_module(mod)
        info = protocol.classify("100,L,2,1")
        router.dispatch(info)
        self.assertEqual(mod.applied, [info])
        self.assertEqual(mod.applied[0].lick, (2, True))

    def test_new_stateful_module_reset_without_editing_dispatch(self):
        # The acceptance criterion (FW-0022): a newly-registered stateful module
        # gets the reset callback purely by defining on_mode_changed - no change
        # to the router/caller wiring.
        router = LineRouter()
        mod = _StatefulModule()
        router.add_module(mod)
        router.dispatch(protocol.classify("0,MODE,FREEROAM"))
        self.assertEqual(mod.resets, ["FREEROAM"])

    def test_mode_sub_fires_only_on_mode_lines_with_name(self):
        router = LineRouter()
        seen = []
        router.subscribe_mode(seen.append)
        router.dispatch(protocol.classify("0,MODE,FULLTASK"))
        router.dispatch(protocol.classify("100,L,2,1"))   # not a MODE line
        router.dispatch(protocol.classify("0,MODE,FREEROAM"))
        self.assertEqual(seen, ["FULLTASK", "FREEROAM"])

    def test_mode_subs_fire_before_module_apply(self):
        # Resets must land before modules process the MODE line, matching the
        # old on_line ordering (reset tallies, then module.apply).
        order = []
        router = LineRouter()
        router.subscribe_mode(lambda mode: order.append("reset"))

        class _Watcher:
            def apply(self, info):
                if info.mode is not None:
                    order.append("apply")

        router.add_module(_Watcher())
        router.dispatch(protocol.classify("0,MODE,FREEROAM"))
        self.assertEqual(order, ["reset", "apply"])

    def test_module_without_on_mode_changed_is_not_required_to_reset(self):
        router = LineRouter()
        mod = _RecordingModule()   # no on_mode_changed
        router.add_module(mod)
        router.dispatch(protocol.classify("0,MODE,FREEROAM"))  # must not raise
        self.assertEqual(len(mod.applied), 1)

    def test_all_modules_receive_every_line(self):
        router = LineRouter()
        a, b = _RecordingModule(), _StatefulModule()
        router.add_module(a)
        router.add_module(b)
        for line in ("0,MODE,FREEROAM", "100,L,2,1", "55,S,3,1"):
            router.dispatch(protocol.classify(line))
        self.assertEqual(len(a.applied), 3)
        self.assertEqual(len(b.applied), 3)
        self.assertEqual(b.resets, ["FREEROAM"])


if __name__ == "__main__":
    unittest.main()
