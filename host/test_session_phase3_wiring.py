"""
test_session_phase3_wiring.py - the session <-> Phase 3 output-folder contract.

A regression test for the 2026-07-27 data-integrity bug (docs/decisions/0066):
because ADR-0060 has the operator arm Phase 3 *before* Start Session, the cue
gate is already open when the session mints its folder, so no rising edge fires
to bind the Phase 3 data file to it. The session's ``task/`` folder stayed empty
and its manifest pointed at the previous session's CSV.

Unlike the other suites here this one drives the two Tk *shells*
(``phase3_controller`` + ``session_controller``) rather than a pure core, because
the bug lives in the wiring between them, not in either core. It needs no camera,
serial link or rat: the pipeline/camera are stubs and frames are synthetic
``RatState`` values. It does need a Tk display, and skips itself without one.

Run from host/:  python -m unittest test_session_phase3_wiring
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import tkinter as tk
import unittest

from tmaze.core.maze_state import CENTRAL_STEM, HUB, RatState
from tmaze.ui.phase3_controller import DATA_DIR, Phase3ControlView
from tmaze.ui.session_controller import SessionControlView

# How long to wait for the actuation thread to apply an enqueued command. The work
# is microseconds; this is a generous ceiling so a loaded machine can't flake.
SETTLE_TIMEOUT_S = 5.0

# session_id stamps have one-second resolution, so two sessions in one test must
# be separated in time to get distinct ids/folders.
STAMP_TICK_S = 1.2


class _FakePipeline:
    """The MazeStatePipeline seam VisionActuator needs (ADR-0043)."""

    def subscribe_fast(self, callback):
        pass

    def unsubscribe_fast(self, callback):
        pass

    def zones_valid(self):
        return True


class _FakeCamera:
    def __init__(self):
        self.tracking = False
        self._path = None

    def tracking_enabled(self):
        return self.tracking

    def start_recording(self, session_id, out_dir=None):
        self._path = os.path.join(out_dir, f"{session_id}.mkv")
        return True

    def current_recording_path(self):
        return self._path

    def stop_recording(self):
        pass


class _Bundle:
    """The pipeline's fast-view bundle (only ``.state`` is read here)."""

    def __init__(self, state):
        self.state = state


class SessionPhase3WiringTest(unittest.TestCase):
    """Each session's task data must live in - and be referenced from - its own
    folder, whatever order the operator armed and started things in."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:            # no display (headless CI)
            raise unittest.SkipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.recordings = tempfile.mkdtemp(prefix="test_session_wiring_")
        self.addCleanup(shutil.rmtree, self.recordings, ignore_errors=True)
        self.camera = _FakeCamera()
        self.phase3 = Phase3ControlView(
            self.root, camera=self.camera, pipeline=_FakePipeline(),
            send=lambda _cmd: None, animal_id=lambda: "600")
        self.session = SessionControlView(
            self.root, camera=self.camera, phase3=self.phase3,
            send=lambda _cmd: None, recordings_root=self.recordings,
            animal_id=lambda: "600")
        self.addCleanup(self.root.destroy)
        self.addCleanup(self.session.close)
        self.addCleanup(self.phase3.close)

    # ------------------------------------------------------------- helpers
    def _open_gate(self):
        """Put Phase 3's gate in the open state (armed + Free Roam + tracking)."""
        self.phase3._mode = "FREEROAM"
        self.camera.tracking = True
        self.phase3._enable_var.set(True)

    def _run_a_trial(self):
        """Two frames that make the rat leave the hub up the stem, which is the
        edge that marks a trial started and so persists the table."""
        for state in (RatState(zone=HUB, valid=True, confidence=1.0),
                      RatState(zone=CENTRAL_STEM, direction="up_stem",
                               valid=True, confidence=1.0)):
            self.phase3._publish_snapshot()          # what the Tk poll does
            self.phase3._process_act_item(("frame", _Bundle(state)),
                                          self.phase3._act_snapshot)

    def _settle(self):
        """Wait for the actuation thread to drain its queue (Start's rebind is
        enqueued, not applied inline)."""
        deadline = time.monotonic() + SETTLE_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.phase3._act_queue.empty():
                time.sleep(0.05)
                if self.phase3._act_queue.empty():
                    return
            time.sleep(0.01)
        self.fail("actuation thread did not drain")

    def _start_session(self):
        self.session._start_session()
        self._settle()
        return self.session._session_dir

    def _task_files(self, session_dir):
        task_dir = os.path.join(session_dir, "task")
        return sorted(os.listdir(task_dir)) if os.path.isdir(task_dir) else []

    # --------------------------------------------------------------- tests
    def test_arming_before_start_still_lands_data_in_the_session_folder(self):
        """The ADR-0060 order: arm first, so the gate opens before the session
        exists. The file must still end up in the session's task/ folder."""
        self._open_gate()
        self._run_a_trial()
        self.assertTrue(self.phase3.current_log_path().startswith(DATA_DIR),
                        "pre-session arm should use the host/data fallback")

        session_dir = self._start_session()
        self._run_a_trial()
        self.session._resolve_paths(reschedule=False)

        expected = os.path.join(session_dir, "task",
                                f"phase3_{os.path.basename(session_dir)}.csv")
        self.assertEqual(self.phase3.current_log_path(), expected)
        self.assertEqual(self._task_files(session_dir),
                         [os.path.basename(expected)])
        self.assertEqual(self.session._manifest.streams["phase3_task"], expected)

    def test_rebound_file_keeps_the_trials_run_before_start(self):
        """Rebinding re-points the path; it must not lose the table built while
        the operator was arming and placing the rat."""
        self._open_gate()
        self._run_a_trial()
        rows_before = len(self.phase3.published_state().rows)
        index_before = self.phase3.published_state().current_index

        session_dir = self._start_session()

        state = self.phase3.published_state()
        self.assertEqual(len(state.rows), rows_before)
        self.assertEqual(state.current_index, index_before)
        # The whole table is rendered on every write, so the session's file is
        # complete immediately - including the pre-Start trial's start stamp.
        with open(self.phase3.current_log_path(), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"session_id={os.path.basename(session_dir)}", text)

    def test_second_session_does_not_inherit_the_first_ones_file(self):
        """The observed 600_20260727_134317 failure: tracking is toggled between
        animals, so Phase 3 re-arms between sessions."""
        self._open_gate()
        self._run_a_trial()
        dir_a = self._start_session()
        self._run_a_trial()
        self.session._resolve_paths(reschedule=False)
        self.session._stop_session()
        time.sleep(STAMP_TICK_S)

        # Gate closes and reopens (the operator restarts tracking for the next rat).
        self.camera.tracking = False
        self._run_a_trial()
        self.camera.tracking = True
        self._run_a_trial()
        self.assertTrue(
            self.phase3.current_log_path().startswith(DATA_DIR),
            "a re-arm after Stop must not write into the closed session's folder")

        dir_b = self._start_session()
        self._run_a_trial()
        self.session._resolve_paths(reschedule=False)

        self.assertNotEqual(dir_a, dir_b)
        expected_b = os.path.join(dir_b, "task",
                                  f"phase3_{os.path.basename(dir_b)}.csv")
        self.assertEqual(self.session._manifest.streams["phase3_task"], expected_b)
        self.assertEqual(self._task_files(dir_b), [os.path.basename(expected_b)])
        # Session A's folder keeps exactly its own file, untouched by session B.
        self.assertEqual(self._task_files(dir_a),
                         [f"phase3_{os.path.basename(dir_a)}.csv"])

    def test_manifest_never_records_a_file_outside_the_session(self):
        """The backstop, independent of the rebind: a reported path from outside
        the session's tree is refused rather than recorded."""
        self._open_gate()
        self._run_a_trial()
        stale = self.phase3.current_log_path()
        # Pin the reported path so no rebind can take effect - which is what the
        # manifest sees if its resolve poll wins the race, or if some future change
        # reintroduces a stale binding.
        self.phase3.current_log_path = lambda: stale

        session_dir = self._start_session()
        self.session._resolve_paths(reschedule=False)

        self.assertFalse(stale.startswith(session_dir))
        self.assertNotIn("phase3_task", self.session._manifest.streams)


if __name__ == "__main__":
    unittest.main()
