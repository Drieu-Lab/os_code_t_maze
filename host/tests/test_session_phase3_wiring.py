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

Run from host/:  python -m unittest tests.test_session_phase3_wiring
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import tkinter as tk
import unittest

from tmaze.core.maze_state import (
    CENTRAL_STEM, HUB, IN_WELL, LEFT_CHOICE, RIGHT_CHOICE, UP_STEM, RatState)
from tmaze.core.phase3.trial_sequence import TrialConfig
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
        # This is a headless regression test with a REAL Tk display, so the
        # operator-facing "pre-session weight" modal is not skipped by its headless
        # guard: with no daily-table row for rat 600, _start_session() would pop
        # that dialog once per test and block forever waiting for input. Inject a
        # non-interactive ask_weight that "skips" (None) - the animal state then
        # falls back to a minimal record, which this folder-wiring test does not
        # exercise. (See docs/decisions/0083.)
        self.session = SessionControlView(
            self.root, camera=self.camera, phase3=self.phase3,
            send=lambda _cmd: None, recordings_root=self.recordings,
            animal_id=lambda: "600", ask_weight=lambda _animal: None)
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

    @staticmethod
    def _expected_task_files(csv_name):
        """The two files a Phase 3 session writes into task/: the trial CSV and its
        paired excursion-event log (ADR-0109), in os.listdir sorted order ('.'
        sorts before '_', so the trial CSV comes first)."""
        return sorted([csv_name, csv_name[:-4] + "_excursions.csv"])

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
                         self._expected_task_files(os.path.basename(expected)))
        self.assertEqual(self.session._manifest.streams["phase3_task"], expected)
        # The excursion-event log is registered as its own stream (ADR-0109).
        self.assertEqual(self.session._manifest.streams["phase3_excursions"],
                         expected[:-4] + "_excursions.csv")

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

    def test_start_stamps_canonical_session_t0_into_the_header(self):
        """ADR-0124: Start Session hands the manifest t0 to Phase 3, and the rebind
        stamps it into the header so the trial file self-anchors to the one shared
        instant every stream uses."""
        self._open_gate()
        self._run_a_trial()                        # arms (no t0 known yet)
        self._start_session()                      # rebind stamps t0

        t0_iso = self.session._manifest.t0["iso"]
        with open(self.phase3.current_log_path(), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn(f"session_t0_iso={t0_iso}", text)

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
        self.assertEqual(self._task_files(dir_b),
                         self._expected_task_files(os.path.basename(expected_b)))
        # Session A's folder keeps exactly its own files, untouched by session B.
        self.assertEqual(self._task_files(dir_a),
                         self._expected_task_files(
                             f"phase3_{os.path.basename(dir_a)}.csv"))

    def test_mid_session_reopen_resumes_and_preserves_data(self):
        """FW-0026 / ADR-0120: a mid-session tracking flicker (gate closes then
        reopens) must RESUME the same run - keep the same file, the recorded
        trials, and the trial index - not re-arm and overwrite the file with a
        blank plan at trial 1."""
        self._open_gate()
        self._run_a_trial()                        # arms this session
        self._start_session()                      # rebinds the file into task/
        self.session._resolve_paths(reschedule=False)

        # Hold the live log OBJECT: a RESUME keeps it; a re-arm (the bug) replaces
        # it with a fresh Phase3Log and rewrites the file as a blank plan.
        log_before = self.phase3._log
        path_before = self.phase3.current_log_path()
        rows_before = len(self.phase3.published_state().rows)
        index_before = self.phase3.published_state().current_index

        # The gate flickers: the operator toggles tracking off then on again.
        self.camera.tracking = False
        self._run_a_trial()                        # gate closes (disarm, table kept)
        self.camera.tracking = True
        self._run_a_trial()                        # gate reopens -> RESUME

        # Same log object + file + table + index: the run was resumed, not re-armed.
        self.assertIs(self.phase3._log, log_before)
        self.assertEqual(self.phase3.current_log_path(), path_before)
        state1 = self.phase3.published_state()
        self.assertEqual(len(state1.rows), rows_before)
        self.assertEqual(state1.current_index, index_before)
        # The reopen was surfaced to the operator as a resume, not a restart.
        self.assertGreaterEqual(self.phase3._reopen_resumes, 1)

    def test_disk_write_failure_is_surfaced_and_clears_on_recovery(self):
        """ADR-0121: a failed atomic write of the trial CSV is no longer swallowed
        - it is recorded per-file and surfaced loudly in the status line, then
        cleared once a later write succeeds."""
        import unittest.mock as mock

        self._open_gate()
        self._run_a_trial()                        # arms; a normal write succeeded
        self.phase3._publish_status(self.phase3._act_snapshot)
        self.assertNotIn("DISK WRITE FAILED", self.phase3._status_text)

        # The disk write fails: the failure is recorded, not swallowed.
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            self.phase3._write_log()
        self.assertIn("task", self.phase3._write_errors)
        self.phase3._publish_status(self.phase3._act_snapshot)
        self.assertIn("DISK WRITE FAILED", self.phase3._status_text)

        # A later successful write clears the warning.
        self.phase3._write_log()
        self.assertNotIn("task", self.phase3._write_errors)
        self.phase3._publish_status(self.phase3._act_snapshot)
        self.assertNotIn("DISK WRITE FAILED", self.phase3._status_text)

    # ---------------------------------------------- auto-extend (ADR-0150)
    def _feed(self, state):
        """Publish the gate snapshot then fold one synthetic frame, exactly as the
        Tk poll + actuation loop do."""
        self.phase3._publish_snapshot()
        self.phase3._process_act_item(("frame", _Bundle(state)),
                                      self.phase3._act_snapshot)

    def _run_full_trial(self, side):
        """Drive one COMPLETE trial through the real vision->task path: hub departure
        up the stem (trial start + choice cue), a choice-arm entry (scored on the
        shared choice-commit edge), and the return lap closing at the hub (advances
        the trial). ``side`` is "left"/"right"."""
        choice_zone = LEFT_CHOICE if side == "left" else RIGHT_CHOICE
        frames = (
            # Home at the well: re-arms the per-trip latches (excursion + stem).
            RatState(zone=HUB, substate=IN_WELL, valid=True, confidence=1.0),
            # Depart the hub UP the stem: trial-start edge + the CHOICE cue window.
            RatState(zone=CENTRAL_STEM, direction=UP_STEM, entered=CENTRAL_STEM,
                     exited=HUB, valid=True, confidence=1.0),
            # Enter the choice arm: the once-per-trip choice-commit edge -> scored.
            RatState(zone=choice_zone, entered=choice_zone, valid=True,
                     confidence=1.0),
            # Close the lap back at the hub: advances the trial index.
            RatState(zone=HUB, substate=IN_WELL, entered=HUB, exited=choice_zone,
                     loop_completed=True, loop_side=side, valid=True,
                     confidence=1.0),
        )
        for state in frames:
            self._feed(state)

    def test_plan_auto_extends_when_rat_outpaces_it(self):
        """ADR-0150: a rat that runs past the planned trial count keeps going - the
        plan auto-extends one balanced block ahead, trials past the original count
        are still scored, and NO identity-less (full-blast) cue is ever sent."""
        # A deliberately tiny plan so a handful of trials overruns it.
        self.phase3._current_trial_config = lambda: TrialConfig(
            max_trials=2, block_size=2, max_same_run=0)
        sends = []
        self.phase3._send = sends.append

        self._open_gate()
        for _ in range(4):                     # 4 > the 2 planned trials
            self._run_full_trial("left")

        # The plan grew past the initial max_trials (append-only extension).
        self.assertGreater(len(self.phase3._seq), 2)
        self.assertGreater(len(self.phase3.published_state().rows), 2)
        # Every one of the four trials was scored - none stranded in a "complete"
        # dead state past the original boundary.
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 4)

        # Cues fired with a real identity (hz + gain) throughout, and never as a
        # bare "CUE_ON,CHOICE"/"CUE_ON,RETURN" (the firmware full-volume low-tone
        # fallback the guard prevents).
        self.assertTrue(any(s.startswith("CUE_ON,CHOICE,") for s in sends),
                        "expected at least one identity-carrying choice cue")
        self.assertNotIn("CUE_ON,CHOICE", sends)
        self.assertNotIn("CUE_ON,RETURN", sends)

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

    # ------------------------------------ current-trial commit (ADR-0152)
    def test_running_block_current_trial_is_published_as_intervention(self):
        """docs/decisions/0152: the controller must commit the just-advanced CURRENT
        trial before it re-aligns + publishes, or a running corrective block's
        current trial shows on the ribbon as NORMAL (blank sched_state) for a frame.
        Drive a real advance inside a forced block and assert the published current
        row is already INTERVENTION."""
        from tmaze.core.phase3.debias import DebiasBlockConfig
        from tmaze.core.phase3.trial_sequence import GEN_DEBIAS_BLOCKS

        # A debias plan that never AUTO-triggers or exits on its own (min_errors and
        # the exit/length caps are set out of reach), so the only block is the one
        # the operator forces and it stays open across the advance we test.
        self.phase3._current_trial_config = lambda: TrialConfig(
            max_trials=40, block_size=20, max_same_run=4,
            generator=GEN_DEBIAS_BLOCKS,
            debias=DebiasBlockConfig(window=10, min_errors=100,
                                     exit_consecutive=100, max_block_len=100))

        self._open_gate()
        self._run_full_trial("left")           # trial 0 (NORMAL); now current == 1
        # Operator forces a corrective block; it applies on the next committed trial.
        self.phase3._process_act_item(("cmd", "debias_start"),
                                      self.phase3._act_snapshot)
        self._run_full_trial("left")           # trial 1 committed as the block opens
        self._run_full_trial("left")           # advance -> new current is in-block

        st = self.phase3.published_state()
        self.assertGreaterEqual(st.current_index, 2)
        cur = st.rows[st.current_index]
        self.assertEqual(cur.sched_state, "INTERVENTION")


if __name__ == "__main__":
    unittest.main()
