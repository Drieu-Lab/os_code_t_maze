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

import json
import os
import shutil
import tempfile
import time
import tkinter as tk
import unittest

from tmaze.core import protocol
from tmaze.core.session.session_notes import (
    AUTO_CAP_REACHED, AUTO_FIRMWARE_RESET, AUTO_SEND_FAILED, AUTO_WRITE_FAILED)
from tmaze.core.phase3.session_cap import SessionCapConfig
from tmaze.core.maze_state import (
    CENTRAL_STEM, HUB, IN_WELL, LEFT_CHOICE, RIGHT_CHOICE, UP_STEM, RatState)
from tmaze.core.records.animals import AnimalRegistry
from tmaze.core.phase3.trial_sequence import TrialConfig
from tmaze.ui.phase3_controller import Phase3ControlView
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
        # Neutralize the BLOCKING modal dialogs for the whole suite: this test
        # drives the real Tk shells with a real display, so any code path that
        # pops a messagebox (e.g. _start_session's "arm Phase 3 first" warning
        # when a test forgets to arm) would HANG an unattended run waiting for a
        # human click. Stub the display dialogs to no-ops (they return nothing the
        # code branches on); an un-armed Start then just falls through and the
        # test's own assertions catch the logic, instead of the suite blocking.
        # The real popups are unchanged on the rig. (Ask/return dialogs are left
        # alone; no test here depends on one, and the injected ask_weight already
        # answers the only interactive prompt in these flows.)
        import unittest.mock as mock
        for _name in ("showwarning", "showerror", "showinfo"):
            _p = mock.patch(f"tkinter.messagebox.{_name}")
            _p.start()
            self.addCleanup(_p.stop)
        self.recordings = tempfile.mkdtemp(prefix="test_session_wiring_")
        self.addCleanup(shutil.rmtree, self.recordings, ignore_errors=True)
        self.camera = _FakeCamera()
        self.phase3 = Phase3ControlView(
            self.root, camera=self.camera, pipeline=_FakePipeline(),
            # A send returns whether it reached the wire (RigClient.send -> bool,
            # docs/decisions/0173); the controller logs a cue only when it did, so
            # the double must report success or every cue reads as dropped.
            send=lambda _cmd: True, animal_id=lambda: "600")
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

    def _arm(self):
        """Open the gate AND fire the arm (gate rising edge) NOW, the way real usage
        does BEFORE Start Session: the camera streams frames continuously, so
        ``_arm_session`` has already run by the time the operator clicks Start
        (docs/decisions/0060). Without a frame here the harness would arm on the
        first trial frame - AFTER Start - which wipes the session-cap anchor the
        Start just took (docs/decisions/0198). Use this in cap tests that Start
        immediately (no pre-Start trials to trigger the edge)."""
        self._open_gate()
        self._feed(RatState(zone=HUB, valid=True, confidence=1.0))

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

    # ------------------------------ firmware-clock echoes (ADR-0165)
    def test_apply_routes_firmware_echoes_to_the_actuation_thread(self):
        """The serial-thread apply() must route a CHOICE cue-on echo and a
        SOL/REWARD echo to the actuation thread (the sole log writer), carrying the
        firmware ms; a RETURN cue echo must NOT feed the choice-cue stamp."""
        captured = []
        self.phase3._enqueue_act = captured.append
        self.phase3.apply(protocol.classify("12100,CUE,ON,CHOICE,4000"))
        self.phase3.apply(protocol.classify("12550,SOL,2,1"))
        self.phase3.apply(protocol.classify("13000,REWARD,CENTER,500"))
        self.phase3.apply(protocol.classify("13500,CUE,ON,RETURN,8000"))
        self.assertIn(("cue_ack", 12100), captured)
        self.assertIn(("reward_ack", 12550), captured)
        self.assertIn(("reward_ack", 13000), captured)
        # A RETURN cue echo feeds the return-cue stamp, not the choice one.
        self.assertIn(("return_cue_ack", 13500), captured)
        self.assertNotIn(("cue_ack", 13500), captured)

    def test_firmware_echoes_stamp_the_trial_fw_ms_cells(self):
        """End of the wiring: the actuation thread binds the echo ms to the pending
        trial's choice_cue_on_fw_ms / reward_cmd_fw_ms (docs/decisions/0165), and a
        later (top-up) reward echo does not overwrite the prime's firmware time."""
        self._open_gate()
        self._run_a_trial()                        # arms; a log now exists
        # The CHOICE cue, prime, and RETURN cue were commanded for trial 0.
        self.phase3._pending_cue_ack_index = 0
        self.phase3._pending_reward_ack_index = 0
        self.phase3._pending_return_cue_ack_index = 0
        self.phase3._process_act_item(("cue_ack", 12100), self.phase3._act_snapshot)
        self.phase3._process_act_item(("reward_ack", 12550),
                                      self.phase3._act_snapshot)
        self.phase3._process_act_item(("return_cue_ack", 12800),
                                      self.phase3._act_snapshot)
        row0 = self.phase3._log._rows[0]
        self.assertEqual(row0.choice_cue_on_fw_ms, "12100")
        self.assertEqual(row0.reward_cmd_fw_ms, "12550")
        self.assertEqual(row0.return_cue_on_fw_ms, "12800")
        # A stray later reward echo (a top-up) must not overwrite the prime's ms.
        self.phase3._pending_reward_ack_index = 0
        self.phase3._process_act_item(("reward_ack", 99999),
                                      self.phase3._act_snapshot)
        self.assertEqual(self.phase3._log._rows[0].reward_cmd_fw_ms, "12550")

    # -------------------------------- save-on-arm config provenance (ADR-0167)
    def test_start_persists_live_phase3_config_before_the_snapshot(self):
        """Save-on-arm: Start must write the tab's live GUI-editable config to
        phase3.json BEFORE the config snapshot, so the archived copy reflects what
        the session ARMED with even if the operator never pressed Save. Asserts
        both the persist->snapshot order and that the persisted file carries the
        live, un-Saved value (the snapshot then copies it verbatim, ADR-0072)."""
        # Redirect the tab's config file to a temp path so the test never touches
        # the real host/phase3.json.
        cfg_path = os.path.join(self.recordings, "phase3_live.json")
        self.phase3._config_path = cfg_path
        # A live, UN-SAVED edit: bump the reward pulse away from what was loaded.
        live_pulse = self.phase3._current_pulse_ms() + 123
        self.phase3._pulse_var.set(str(live_pulse))

        order = []
        real_persist = self.phase3.persist_live_config
        real_snapshot = self.session._write_config_snapshot

        def spy_persist():
            order.append("persist")
            return real_persist()

        def spy_snapshot(config_dir):
            order.append("snapshot")
            return real_snapshot(config_dir)

        self.phase3.persist_live_config = spy_persist
        self.session._write_config_snapshot = spy_snapshot

        self._open_gate()
        self._start_session()

        self.assertEqual(order[:2], ["persist", "snapshot"],
                         "phase3.json must be persisted before it is snapshotted")
        with open(cfg_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["reward"]["pulse_ms"], live_pulse)
        # The hand-edited-only sections survive the automatic write untouched.
        self.assertIn("_schema", saved)
        self.assertIn("max_trials", saved["sequence"])

    def test_persist_live_config_skips_write_on_invalid_edit(self):
        """Best-effort: an invalid live edit (min_errors > window) writes nothing
        and returns a note, so save-on-arm can never corrupt phase3.json or break
        Start."""
        cfg_path = os.path.join(self.recordings, "phase3_invalid.json")
        self.phase3._config_path = cfg_path
        self.phase3._db_window_var.set("2")
        self.phase3._db_min_errors_var.set("9")     # > window -> invalid
        note = self.phase3.persist_live_config()
        self.assertIsNotNone(note)
        self.assertFalse(os.path.exists(cfg_path))

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
        self.assertIsNone(self.phase3.current_log_path(),
                          "pre-session arm keeps the plan in memory - no orphan on "
                          "disk (Increment 4b)")

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

    def test_armed_without_session_writes_nothing_and_says_not_recording(self):
        """Increment 4b: arming before a session exists keeps the plan in memory -
        no trial/excursion file on disk, and the status line says NOT RECORDING so
        a run is never mistaken for one that is being recorded."""
        self._open_gate()
        self._run_a_trial()                        # armed, gate open, no session

        self.assertIsNone(self.phase3.current_log_path())
        self.assertIsNone(self.phase3.current_excursion_path())
        # A write attempt in this state is a no-op (guarded on _log_path is None),
        # so nothing lands and no write error is recorded either.
        self.phase3._write_log()
        self.phase3._write_excursion_log()
        self.assertEqual(self.phase3._write_errors, {})

        self.phase3._publish_status(self.phase3._act_snapshot)
        self.assertIn("NOT RECORDING", self.phase3._status_text)

        # Start binds a folder; the status then drops the warning and the file
        # appears, complete with the pre-Start trial.
        self._start_session()
        self.assertIsNotNone(self.phase3.current_log_path())
        self.phase3._publish_status(self.phase3._act_snapshot)
        self.assertNotIn("NOT RECORDING", self.phase3._status_text)

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
        self.assertIsNone(
            self.phase3.current_log_path(),
            "a re-arm after Stop keeps the plan in memory (no orphan, no write "
            "into the closed session's folder; Increment 4b)")

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
        self._run_a_trial()
        self._start_session()                      # binds the task/ log path
        self.phase3._write_log()                   # a normal write succeeded
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

    def test_trial_csv_is_fsynced_before_rename(self):
        """docs/decisions/0174 (L4): the trial CSV is flushed AND fsync'd to the
        physical disk BEFORE os.replace, so "crash-safe" means the bytes are
        durable, not merely un-torn in the OS write cache. We assert the fsync
        happens while the temp file still exists (i.e. before the rename)."""
        import unittest.mock as mock

        self._open_gate()
        self._run_a_trial()
        self._start_session()                      # binds the task/ log path

        order = []
        real_fsync, real_replace = os.fsync, os.replace

        def _rec_fsync(fd):
            order.append("fsync")
            return real_fsync(fd)

        def _rec_replace(src, dst):
            order.append("replace")
            return real_replace(src, dst)

        with mock.patch("os.fsync", _rec_fsync), \
                mock.patch("os.replace", _rec_replace):
            self.phase3._write_log()

        self.assertIn("fsync", order, "trial CSV must be fsync'd")
        self.assertLess(order.index("fsync"), order.index("replace"),
                        "fsync must precede the atomic rename")

    def test_filtered_serial_stamps_line_at_receipt_not_write_time(self):
        """docs/decisions/0175 (S3): the per-session serial capture stamps each
        line with the reader-thread RECEIPT wall clock passed through from the
        LineInfo (recv_wall), NOT datetime.now() at the moment the Tk callback
        got round to writing. We feed a fixed receipt epoch and confirm the row
        carries exactly that instant."""
        from datetime import datetime

        self._open_gate()                          # arm Phase 3 so Start proceeds
        self._start_session()                      # opens serial/ + the handle
        self.assertIsNotNone(self.session._serial_file)

        recv_wall = 1_600_000_000.5                # a fixed, unmistakable instant
        # Offset-aware receipt stamp (docs/decisions/0182): the host UTC offset is
        # attached so the instant is unambiguous off-rig.
        expected = datetime.fromtimestamp(recv_wall).astimezone().isoformat(
            timespec="milliseconds")
        self.session._write_serial("123,S,0,1", recv_wall=recv_wall)

        with open(self.session._serial_path, encoding="utf-8") as fh:
            rows = [ln for ln in fh.read().splitlines()
                    if ln and not ln.startswith("#")]
        self.assertEqual(rows[-1], f"{expected},123,S,0,1")

    def test_filtered_serial_falls_back_to_now_without_a_receipt_stamp(self):
        """A line that carried no receipt stamp (recv_wall=None) still gets a
        wall-clock stamp - the pre-0175 datetime.now() path - so the column is
        never blank."""
        self._open_gate()                          # arm Phase 3 so Start proceeds
        self._start_session()
        self.session._write_serial("456,L,0,1")    # no recv_wall

        with open(self.session._serial_path, encoding="utf-8") as fh:
            rows = [ln for ln in fh.read().splitlines()
                    if ln and not ln.startswith("#")]
        # <iso>,456,L,0,1 - a non-empty ISO stamp precedes the firmware line.
        stamp, _, rest = rows[-1].partition(",")
        self.assertEqual(rest, "456,L,0,1")
        self.assertTrue(stamp and "T" in stamp, f"expected an ISO stamp, got {stamp!r}")

    def test_session_write_failure_is_surfaced_and_session_still_closes(self):
        """docs/decisions/0174 (L3): a failed session-bundle write (here the
        end-of-session summary) is recorded per-slot, warned about loudly in the
        status line, and left as an in-memory auto:write_failed timeline note -
        yet Stop still closes the session cleanly (the data lives in memory)."""
        import unittest.mock as mock

        self._open_gate()
        self._run_a_trial()
        self._start_session()                      # binds manifest + session dir
        self.assertNotIn("DISK WRITE FAILED", self.session._status_var.get())

        # The bundle disk write fails: recorded, not swallowed, and noted.
        self.session._atomic_write = mock.Mock(side_effect=OSError("disk full"))
        self.session._write_session_summary()
        self.assertIn("summary", self.session._write_errors)
        self.assertTrue(
            any(e.tag == AUTO_WRITE_FAILED and e.is_auto()
                for e in self.session._notelog.entries()),
            "expected an in-memory auto:write_failed note")

        # Stop still closes the session, and repeats the warning by name.
        self.session._stop_session()
        self.assertFalse(self.session._active)
        status = self.session._status_var.get()
        self.assertIn("DISK WRITE FAILED", status)
        self.assertIn("session_summary", status)

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

        def _record_send(cmd):                 # send -> bool (docs/decisions/0173)
            sends.append(cmd)
            return True
        self.phase3._send = _record_send

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

    # ---------------------------------------------- session cap (ADR-0190)
    def _cap(self, **kw):
        """Pin the session cap the controller resolves (bypassing phase3.json)."""
        cfg = SessionCapConfig(**kw)
        self.phase3._current_session_cap = lambda: cfg
        return cfg

    def _record_sends(self):
        sends = []

        def _send(cmd):                        # send -> bool (docs/decisions/0173)
            sends.append(cmd)
            return True
        self.phase3._send = _send
        return sends

    def test_trial_cap_stops_cueing_and_scoring_and_is_recorded(self):
        """docs/decisions/0190: after ``max_trials`` SCORED trials since Session
        Start the task cues and scores nothing more (a soft stop), the auto-note
        fires once, the banner says CAP REACHED, and the cap is stamped into the
        trial-CSV header, the manifest provenance and the summary."""
        self._cap(enabled=True, max_trials=2, max_minutes=45.0)
        events = []
        self.phase3._on_intervention = events.append
        sends = self._record_sends()

        self._arm()
        self._start_session()
        for _ in range(4):                     # 4 attempts > the 2-trial cap
            self._run_full_trial("left")

        # Exactly two trials scored; the third and fourth trips were silent.
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 2)
        self.assertEqual(sum(1 for s in sends if s.startswith("CUE_ON,CHOICE")), 2)
        hit = self.phase3.cap_reached()
        self.assertIsNotNone(hit)
        self.assertEqual(hit["reason"], "trials")
        self.assertEqual(hit["completed"], 2)
        # One auto-note, carrying the cap detail.
        caps = [e for e in events if e["tag"] == AUTO_CAP_REACHED]
        self.assertEqual(len(caps), 1)
        self.assertIn("2 trials", caps[0]["text"])
        self.assertEqual(caps[0]["cap"]["reason"], "trials")
        # The banner is unmistakable.
        self.phase3._publish_status(self.phase3._act_snapshot)
        self.assertIn("CAP REACHED", self.phase3._status_text)
        # Provenance: header line, manifest, summary block.
        self.assertIn("session_cap_trials=2", self.phase3._log.render_csv())
        self.assertEqual(self.session._manifest.provenance.get("session_cap"),
                         {"enabled": True, "max_trials": 2, "max_minutes": 45.0})
        self.session._write_session_summary()
        path = os.path.join(self.session._session_dir, "summary",
                            f"session_summary_{self.session._session_id}.json")
        with open(path, encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["session_cap"]["max_trials"], 2)
        self.assertEqual(summary["session_cap"]["reached"]["reason"], "trials")
        self.assertEqual(summary["task_params"]["session_cap"]["enabled"], True)

    def test_time_cap_lets_the_in_flight_trial_finish(self):
        """docs/decisions/0190: the clock cap is only evaluated at a trial
        boundary, so a trial the rat is already running when time runs out is
        scored + rewarded as usual; the NEXT trip is the silent one."""
        self._cap(enabled=True, max_trials=120, max_minutes=45.0)
        elapsed = [0.0]
        self.phase3._session_elapsed_s = lambda snap: elapsed[0]
        sends = self._record_sends()

        self._arm()
        self._start_session()
        # Start a trial with time in hand: home, up the stem (cue), into the arm
        # (scored, awaiting the return lap)...
        self._feed(RatState(zone=HUB, substate=IN_WELL, valid=True, confidence=1.0))
        self._feed(RatState(zone=CENTRAL_STEM, direction=UP_STEM, entered=CENTRAL_STEM,
                            exited=HUB, valid=True, confidence=1.0))
        self._feed(RatState(zone=LEFT_CHOICE, entered=LEFT_CHOICE, valid=True,
                            confidence=1.0))
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 1)
        self.assertIsNone(self.phase3.cap_reached())      # mid-trial: not yet
        # ...then the clock runs out while the rat is still out.
        elapsed[0] = 45.0 * 60.0 + 1.0
        self._feed(RatState(zone=HUB, substate=IN_WELL, entered=HUB, exited=LEFT_CHOICE,
                            loop_completed=True, loop_side="left", valid=True,
                            confidence=1.0))
        # The in-flight trial advanced normally, and the cap fired at that boundary.
        self.assertEqual(self.phase3._task.index, 1)
        hit = self.phase3.cap_reached()
        self.assertEqual(hit["reason"], "time")
        self.assertEqual(hit["completed"], 1)
        # A further trip is silent and unscored.
        n_cues = sum(1 for s in sends if s.startswith("CUE_ON,CHOICE"))
        self._run_full_trial("right")
        self.assertEqual(sum(1 for s in sends if s.startswith("CUE_ON,CHOICE")), n_cues)
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 1)

    def test_cap_counts_from_session_start_and_needs_a_session(self):
        """Trials run while armed but BEFORE Start don't count toward the cap,
        and with no session at all (no Start anchor) the task is uncapped."""
        self._cap(enabled=True, max_trials=2, max_minutes=45.0)
        self._open_gate()
        for _ in range(3):                     # unrecorded: no cap
            self._run_full_trial("left")
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 3)
        self.assertIsNone(self.phase3.cap_reached())

        self._start_session()                  # the count restarts here
        self._run_full_trial("left")
        self.assertIsNone(self.phase3.cap_reached())
        self._run_full_trial("left")
        self.assertEqual(self.phase3.cap_reached()["completed"], 2)
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 5)

    def test_cap_banner_clears_on_stop_but_mute_persists(self):
        """Once the cap fires, the Rig-view banner (the PUBLISHED ``cap_reached``)
        must clear the instant Stop is pressed, even though the mute state persists
        so a rat still in the maze isn't re-cued (docs/decisions/0192, ADR-0060).
        Asserted with NO forced republish: the task is frozen after the cap, so
        Stop itself must drop the published field (a manual _publish_state would
        mask that)."""
        self._cap(enabled=True, max_trials=1, max_minutes=45.0)
        sends = self._record_sends()
        self._arm()
        self._start_session()
        self._run_full_trial("left")
        self.assertIsNotNone(self.phase3.cap_reached())
        self.assertIsNotNone(self.phase3.published_state().cap_reached)

        self.session._stop_session()
        self._settle()
        self.assertIsNotNone(self.phase3.cap_reached())               # mute persists
        self.assertIsNone(self.phase3.published_state().cap_reached)  # banner clears

        n_cues = sum(1 for s in sends if s.startswith("CUE_ON,CHOICE"))
        self._run_full_trial("left")           # still silent after Stop
        self.assertEqual(sum(1 for s in sends if s.startswith("CUE_ON,CHOICE")), n_cues)
        self.assertIsNotNone(self.phase3.cap_reached())

    def test_cap_survives_a_recording_restart_and_counts_continuously(self):
        """docs/decisions/0198: stopping and RESTARTING the recording mid-run (same
        arm, no re-arm - e.g. the lossless->H.264 switch on 600_20260923_141558)
        must NOT reset the cap. The trial + clock budget is anchored ONCE at the
        first Start and counts continuously across the restart; the old per-Start
        reset let the rat run well past the limit (the cap never fired)."""
        self._cap(enabled=True, max_trials=3, max_minutes=45.0)
        self._arm()
        self._start_session()                  # Start A: anchors base=0
        self._run_full_trial("left")
        self._run_full_trial("left")           # 2 scored, under the 3-trial cap
        self.assertIsNone(self.phase3.cap_reached())
        base_a = self.phase3._cap_base_done
        t0_a = self.phase3._cap_t0
        self.assertTrue(self.phase3._cap_anchored)

        self.session._stop_session()           # Stop A (codec fix)...
        self._settle()
        time.sleep(STAMP_TICK_S)
        self._start_session()                  # ...Start B = restart, no re-arm
        # The anchor is PRESERVED: base + clock are the first Start's, not re-taken.
        self.assertTrue(self.phase3._cap_anchored)
        self.assertEqual(self.phase3._cap_base_done, base_a)
        self.assertEqual(self.phase3._cap_t0, t0_a)

        # The 3rd scored trial (counted from Start A, across the restart) trips the
        # cap - proving continuity. The old code re-based at Start B, so it would
        # have needed 2 + 3 = 5 scored trials and never fired here.
        self._run_full_trial("left")
        hit = self.phase3.cap_reached()
        self.assertIsNotNone(hit)
        self.assertEqual(hit["reason"], "trials")
        self.assertEqual(hit["completed"], 3)

    def test_a_fresh_rearm_re_anchors_the_cap_budget(self):
        """The flip side of ADR-0198: a genuine re-arm (disarm then arm again, a new
        run for the next animal) DOES reset the cap anchor, so the next Start starts
        a fresh budget - only a recording restart WITHOUT a re-arm preserves it."""
        self._cap(enabled=True, max_trials=2, max_minutes=45.0)
        self._arm()
        self._start_session()
        self._run_full_trial("left")
        self.assertTrue(self.phase3._cap_anchored)

        self.session._stop_session()
        self._settle()
        self.phase3.disarm()                   # end the run for this animal
        self.assertFalse(self.phase3._cap_anchored)
        self.assertIsNone(self.phase3._cap_t0)

        self._arm()                            # re-arm for the next run
        time.sleep(STAMP_TICK_S)
        self._start_session()                  # anchors a FRESH budget (base re-taken)
        self.assertTrue(self.phase3._cap_anchored)
        self.assertIsNone(self.phase3.cap_reached())

    # ------------------------------------ per-animal reward volume (ADR-0191)
    def test_daily_water_target_sizes_the_reward_and_is_recorded(self):
        """animals.json v4 ``daily_water_ml`` wins: the armed pulse width is the
        calibration inverse of daily*1000*fraction/(max_trials*0.5), rounded to
        5 ms; the source lands in the header, the manifest and the summary's
        water_target block; the cap note carries the supplement."""
        from tmaze.core.reward import reward_volume as rv
        self._cap(enabled=True, max_trials=120, max_minutes=45.0,
                  water_at_chance_fraction=0.5)
        self.phase3._animal_registry = AnimalRegistry(
            [{"id": "600", "daily_water_ml": 8.0}])     # no explicit reward_ul
        self.phase3._pulse_var.set("470")               # the field is ignored

        pulse_ms, source, daily = self.phase3._resolve_reward_pulse()
        want = rv.round_pulse_ms(rv.pulse_ms_for_volume(8000.0 / 120.0, port=2))
        self.assertEqual(pulse_ms, want)
        self.assertNotEqual(pulse_ms, 470)
        self.assertEqual(source, "animals.json:daily_water_ml")
        self.assertEqual(daily, 8.0)

        self._open_gate()
        self._run_a_trial()                             # arms with the snapshot
        self.assertEqual(self.phase3._task.cfg.reward_pulse_ms, want)
        csv_text = self.phase3._log.render_csv()
        self.assertIn(f"reward_pulse_ms={want}", csv_text)
        self.assertIn("reward_pulse_source=animals.json:daily_water_ml "
                      "daily_water_ml=8", csv_text)
        self._start_session()
        self.assertEqual(self.session._manifest.provenance.get("reward_policy"),
                         {"pulse_ms": want, "source": "animals.json:daily_water_ml",
                          "daily_water_ml": 8.0, "reward_ul": None})
        # The summary's water_target reflects the run's earned volume.
        self.phase3._reward_ul = 5500.0
        target = self.phase3.water_target()
        self.assertEqual(target["daily_water_ml"], 8.0)
        self.assertEqual(target["supplement_ml"], 2.5)
        self.session._write_session_summary()
        path = os.path.join(self.session._session_dir, "summary",
                            f"session_summary_{self.session._session_id}.json")
        with open(path, encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["water_target"]["supplement_ml"], 2.5)

    def test_explicit_reward_ul_wins_and_config_is_the_last_resort(self):
        """An explicit per-animal ``reward_ul`` overrides the derived volume (the
        operator pinning one magnitude for every rat, ADR-0191) while the daily
        target still feeds the water-target readout; with neither set the field
        / phase3.json value is used."""
        from tmaze.core.reward import reward_volume as rv
        self._cap(enabled=True, max_trials=120, max_minutes=45.0)
        self.phase3._pulse_var.set("470")
        self.phase3._animal_registry = AnimalRegistry(
            [{"id": "600", "daily_water_ml": 8.0, "reward_ul": 70.0}])
        pulse_ms, source, daily = self.phase3._resolve_reward_pulse()
        self.assertEqual(pulse_ms, rv.round_pulse_ms(rv.pulse_ms_for_volume(70.0, port=2)))
        self.assertEqual(pulse_ms, 365)
        self.assertEqual(source, "animals.json:reward_ul")
        self.assertEqual(daily, 8.0)                    # target kept for the readout
        self.assertEqual(self.phase3.water_target()["daily_water_ml"], 8.0)
        # Explicit volume with no target: no water-target block.
        self.phase3._animal_registry = AnimalRegistry(
            [{"id": "600", "reward_ul": 60.0}])
        pulse_ms, source, daily = self.phase3._resolve_reward_pulse()
        self.assertEqual(pulse_ms, rv.round_pulse_ms(rv.pulse_ms_for_volume(60.0, port=2)))
        self.assertEqual(source, "animals.json:reward_ul")
        self.assertIsNone(daily)
        self.assertIsNone(self.phase3.water_target())   # no target -> no block
        # Neither set -> the field / phase3.json value, source "config".
        self.phase3._animal_registry = AnimalRegistry([{"id": "600"}])
        self.assertEqual(self.phase3._resolve_reward_pulse(),
                         (470, "config", None))

    def test_cap_note_carries_the_water_target(self):
        self._cap(enabled=True, max_trials=1, max_minutes=45.0)
        self.phase3._animal_registry = AnimalRegistry(
            [{"id": "600", "daily_water_ml": 8.0}])
        events = []
        self.phase3._on_intervention = events.append
        self._arm()
        self._start_session()
        self._run_full_trial("left")
        caps = [e for e in events if e["tag"] == AUTO_CAP_REACHED]
        self.assertEqual(len(caps), 1)
        self.assertIn("of 8.0 mL target, supplement", caps[0]["text"])
        self.assertEqual(caps[0]["water_target"]["daily_water_ml"], 8.0)

    def test_disabled_cap_never_fires(self):
        self._cap(enabled=False, max_trials=1, max_minutes=45.0)
        self._open_gate()
        self._start_session()
        for _ in range(3):
            self._run_full_trial("left")
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 3)
        self.assertIsNone(self.phase3.cap_reached())

    def test_uncued_trip_is_not_scored_and_reruns(self):
        """A genuine trip whose CHOICE cue never sounds (cue disabled) is UNCUED:
        no score, no reward, the trial index does NOT advance (the planned trial
        re-runs), and the uncued attempt is preserved + counted (docs/decisions/
        0171)."""
        self._open_gate()
        self.phase3._choice_var.set(False)      # choice cue OFF -> no cue this trip
        self._run_full_trial("left")

        self.assertEqual(self.phase3._uncued, 1)
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 0)
        self.assertEqual(self.phase3._reward_count, 0)
        self.assertEqual(self.phase3._task.index, 0)        # not advanced: re-run
        # The uncued attempt is preserved (archived) and the cumulative count rides
        # the published snapshot even though the live row was cleared for the re-run.
        self.assertEqual(self.phase3.published_state().uncued, 1)

    def test_cued_trip_still_scores_and_advances(self):
        """Control: with the cue enabled the SAME trip scores + advances and is not
        UNCUED - confirming it is the cue-on state that gates the classification."""
        self._open_gate()                       # choice cue enabled by default
        self._run_full_trial("left")
        self.assertEqual(self.phase3._uncued, 0)
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 1)
        self.assertEqual(self.phase3._task.index, 1)

    def test_failed_cue_send_is_not_logged_and_scores_uncued(self):
        """docs/decisions/0173 (L2b): when the CHOICE cue command does not reach the
        wire (the send reports failure), the rig records NO cue onset, counts the
        drop, emits an auto:send_failed note, and - because nothing was cued - the
        trip scores UNCUED (no score/reward, planned trial re-runs)."""
        events = []
        self.phase3._on_intervention = events.append
        self._open_gate()                       # choice cue enabled by default
        self.phase3._send = lambda _cmd: False  # every command fails to send
        self._run_full_trial("left")

        # The dropped cue is counted and noted, and no onset was written.
        self.assertEqual(self.phase3._send_fails, 1)
        self.assertIsNone(self.phase3._log._rows[0].choice_cue_on_iso)
        self.assertTrue(any(e["tag"] == AUTO_SEND_FAILED for e in events),
                        "expected an auto:send_failed note")
        # No tone played, so the commit is UNCUED: not scored, not rewarded, the
        # trial index does not advance.
        self.assertEqual(self.phase3._uncued, 1)
        self.assertEqual(self.phase3._correct + self.phase3._incorrect, 0)
        self.assertEqual(self.phase3._reward_count, 0)
        self.assertEqual(self.phase3._task.index, 0)

    # ---------------------------------------- firmware reset (S8, ADR-0184)
    def test_firmware_reboot_is_detected_and_reasserts_cue_vision(self):
        """docs/decisions/0184 (S8): a fresh PROTO handshake after the firmware
        clock was already running is a mid-session Due reboot. apply() (serial
        thread) routes it to the actuation thread ONCE, carrying the last ms seen;
        the handler re-takes the DAC (CUE,VISION) and surfaces an auto:firmware_reset
        note. The INITIAL handshake (clock still 0) is NOT treated as a reset."""
        events = []
        self.phase3._on_intervention = events.append
        sends = []

        def _record_send(cmd):                 # send -> bool (docs/decisions/0173)
            sends.append(cmd)
            return True
        self.phase3._send = _record_send
        self.phase3._gate_was_open = True       # we were driving Phase 3 cues

        # Intercept the hand-off so the reset is applied synchronously (no thread).
        enqueued = []
        self.phase3._enqueue_act = enqueued.append

        # A real running clock, then the reboot's PROTO line.
        self.phase3.apply(protocol.classify("183456,S,0,1"))   # clock now > 0
        self.phase3.apply(protocol.classify("0,PROTO,12"))     # <- reboot

        self.assertEqual(len(enqueued), 1, "exactly one firmware_reset hand-off")
        self.assertEqual(enqueued[0][0], "firmware_reset")
        self.assertEqual(enqueued[0][1], 183456)               # last ms before reset

        # Apply it on the actuation path.
        self.phase3._process_act_item(enqueued[0], self.phase3._act_snapshot)
        self.assertEqual(self.phase3._fw_resets, 1)
        self.assertIn("CUE,VISION", sends)                     # DAC re-asserted
        reset_notes = [e for e in events if e["tag"] == AUTO_FIRMWARE_RESET]
        self.assertEqual(len(reset_notes), 1)
        self.assertEqual(reset_notes[0]["last_fw_ms"], 183456)

        # A second PROTO right after (clock reset to 0) is NOT a fresh reset.
        enqueued.clear()
        self.phase3.apply(protocol.classify("0,PROTO,12"))
        self.assertEqual(enqueued, [])

    def test_firmware_reset_lands_in_the_manifest(self):
        """The session controller folds the auto:firmware_reset event into the note
        log AND records a firmware_resets entry in the manifest (docs/decisions/
        0184), so an offline reader can split the firmware ms timeline at the reboot."""
        self._open_gate()
        self._run_a_trial()
        self._start_session()                      # wires the intervention sink + manifest
        self.phase3._gate_was_open = True

        iso = "2026-01-01T12:03:00.000-05:00"
        # Run the handler directly (as the actuation thread would) -> the sink
        # enqueues to the session; the Tk-side drain folds it in.
        self.phase3._process_firmware_reset(183456, iso)
        self.session._drain_interventions()

        self.assertEqual(self.session._manifest.firmware_resets,
                         [{"host_iso": iso, "last_fw_ms": 183456}])
        self.assertTrue(
            any(e.tag == AUTO_FIRMWARE_RESET and e.is_auto()
                for e in self.session._notelog.entries()),
            "expected an auto:firmware_reset note in the log")

    # -------------------------------- per-animal cue->side mapping (ADR-0186)
    def _config_high_left(self):
        """The phase3.json cue->side default this test's tab loaded."""
        return bool(self.phase3._config["sequence"].get("high_tone_is_left", True))

    def test_per_animal_cue_mapping_overrides_config_and_is_recorded(self):
        """animals.json (schema v3) is authoritative: a per-animal high_tone_is_left
        overrides phase3.json, the arm builds the sequence with it, and the source
        is stamped into the trial log header + snapshot (docs/decisions/0186)."""
        per_animal = not self._config_high_left()      # opposite of the file default
        self.phase3._animal_registry = AnimalRegistry(
            [{"id": "600", "high_tone_is_left": per_animal}])

        value, source, overrides = self.phase3._resolve_cue_mapping()
        self.assertEqual(value, per_animal)
        self.assertEqual(source, "animals.json")
        self.assertTrue(overrides)                      # disagreed with phase3.json
        self.assertEqual(self.phase3._current_trial_config().high_tone_is_left,
                         per_animal)
        self.assertEqual(self.phase3.resolved_cue_mapping(),
                         {"high_tone_is_left": per_animal, "source": "animals.json"})

        self._open_gate()
        self._run_a_trial()                             # arms -> builds the log
        self.assertEqual(self.phase3._log.high_tone_is_left_source, "animals.json")
        self.assertEqual(self.phase3._log.cfg.high_tone_is_left, per_animal)
        snap = self.phase3._log.snapshot(0, armed=True)
        self.assertEqual(snap.high_tone_is_left_source, "animals.json")
        self.assertIn("high_tone_is_left_source=animals.json",
                      self.phase3._log.render_csv())

    def test_cue_mapping_falls_back_to_config_when_unset(self):
        """An animal with no per-animal mapping (unset) uses phase3.json - and None
        is NOT read as False (docs/decisions/0186)."""
        self.phase3._animal_registry = AnimalRegistry([{"id": "600"}])  # unset
        value, source, overrides = self.phase3._resolve_cue_mapping()
        self.assertEqual(value, self._config_high_left())
        self.assertEqual(source, "config")
        self.assertFalse(overrides)

    def test_cue_mapping_is_recorded_in_the_manifest_provenance(self):
        """Start folds the resolved mapping + source into the manifest provenance
        block, the single top-level provenance home (docs/decisions/0186/0180)."""
        per_animal = not self._config_high_left()
        self.phase3._animal_registry = AnimalRegistry(
            [{"id": "600", "high_tone_is_left": per_animal}])
        self._open_gate()
        self._run_a_trial()
        self._start_session()
        self.assertEqual(
            self.session._manifest.provenance.get("cue_mapping"),
            {"high_tone_is_left": per_animal, "source": "animals.json"})

    def test_manifest_never_records_a_file_outside_the_session(self):
        """The backstop, independent of the rebind: a reported path from outside
        the session's tree is refused rather than recorded."""
        self._open_gate()
        self._run_a_trial()
        # Pin the reported path to one OUTSIDE any session tree, so no rebind can
        # take effect - which is what the manifest sees if its resolve poll wins
        # the race, or if some future change reintroduces a stale binding. (4b no
        # longer has a host/data fallback to stand in as the out-of-session path,
        # so fabricate one explicitly.)
        stale = os.path.join(self.recordings, "not_a_session", "phase3_stale.csv")
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
