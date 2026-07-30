"""
phase3_controller.py - "Phase 3" tab for experiment_control.py.

The thin host shell for the INITIAL host-owned Phase 3 (see docs/decisions/0046):
the whole cued two-alternative task runs on the host, with the firmware reduced
to a real-time actuator (tone synthesis, the reward valve, the Open Ephys TTLs).
It composes two pure cores over the shared maze-state pipeline's FAST view:

  - cue_gate.CueGate  - WHEN the cue sounds (positional, reused from the Cue tab).
  - phase3_task.Phase3Task over trial_sequence.TrialSequence - WHAT tone each
    trial uses, choice scoring, reward, and trial advancement, driven off the
    task-semantic TaskEventFrame stream (task_events.py), not raw beams.

and turns their decisions into firmware commands:
  - cue onset -> ``CUE_ON,CHOICE,<hz>`` / ``CUE_ON,RETURN,<hz>`` (host supplies the
    tone identity, since the firmware trial FSM is off; PROTOCOL v6), offset ->
    ``CUE_OFF``. The RETURN identity is now a balanced hi/lo schedule generated
    per physical arm from the seed and looked up by the arm the rat enters (not a
    random per-onset pick); see docs/decisions/0047.
  - a rewarded choice -> ``R<port>1,<ms>`` at the central well.

It also writes a per-session data file (host/data/phase3_<animal>_<ts>.csv): the
planned Nx3 cue matrix is written on arm and each trial's choice/outcome/reward
and event timestamps are filled in as it resolves. The pure ``phase3_log.Phase3Log``
core owns the table + CSV rendering; this shell only detects the events (from the
gate/task decisions it already computes, plus center-well lick lines snooped in
``apply()`` and routed to the actuation thread) and writes the file atomically.
See docs/decisions/0047.

Threading (mirrors the Cue/Rules tabs, ADR-0043): the pipeline's fast callback
runs inline on the tracking worker and only ENQUEUES the bundle; a dedicated
actuation thread drains the queue and is the SOLE caller of CueGate.step /
TaskEvents.update / Phase3Task.update / self._send, keeping the vision->tone/
reward path off the ~100 ms Tk poll. The Tk poll only publishes an atomic
snapshot (armed + firmware mode + tracking + tunables + seed) and renders status.

DAC authority handshake (as the Cue tab): on the gate's rising edge send
``CUE,VISION`` (host owns the DAC) and build a fresh sequence + task from the
seed; on the falling edge send ``CUE_OFF`` then ``CUE,BEAM``.

Module contract (like the other tabs):
    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None   handle each serial line (mode + CUE echo)
    close() -> None            stop the thread and silence the tone

Safety (this drives reward + the DAC): OFF by default behind an explicit "Arm"
checkbox; the gate additionally requires the firmware to be in Free Roam (the FSM
is off, so the host is the sole task authority) and tracking + a zone map. When
the gate closes the tone is cut and firmware autonomy is restored.
"""

from __future__ import annotations

import os
import random
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from tkinter import ttk
from typing import Callable, Optional

import protocol
from cue_gate import CHOICE, RETURN, CueGate, CueParams
from maze_state import CENTRAL_STEM, HUB
from phase3_log import Phase3Log, Phase3StateSnapshot
from phase3_task import ABANDONED, CORRECT, INCORRECT, Phase3Config, Phase3Task
from rig_config import load_rig_config
from trial_ribbon_view import (
    _ARM_SHORT, _CELL_CURRENT_BG, _CUE_SHORT, _UNSET, TrialRibbonView,
    progress_signature)
from trial_sequence import LEFT, RIGHT, TrialConfig, TrialSequence
from vision_actuator import VisionActuator

try:
    from task_events import TaskEvents
except Exception:  # pragma: no cover
    TaskEvents = None  # type: ignore

try:
    from tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# Free Roam is the firmware posture for host-owned Phase 3: the trial FSM is off,
# so the host is the sole task authority (see docs/decisions/0046). Same tokens
# the Rules tab actuates in.
FREEROAM_TOKENS = ("FREEROAM", "FREE")

POLL_INTERVAL_MS = 100          # snapshot publish + status refresh (Tk)
SEED_MASK = 0xFFFFFFFF          # the sequence PRNG is 32-bit (trial_sequence.lcg)

# Per-session data files live under host/data/ (same tree as rig_logger.py).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Physical return arm ("left"/"right", from CueGate) -> trial_sequence side int,
# to look up that arm's planned return cue (see docs/decisions/0047).
_SIDE_OF_ARM = {"left": LEFT, "right": RIGHT}

# In-tab progress visualization (docs/decisions/0051). The trial ribbon strip
# itself (cells + colours) now lives in trial_ribbon_view.TrialRibbonView so it
# can be shared with the Rig view (docs/decisions/0057); this tab keeps only the
# scrollable planned-trial list below it (which reuses the ribbon's shared
# short-labels / highlight colour, imported above).
SEQ_LIST_ROWS = 8               # visible rows of the scrollable planned-trial list


def _now_iso() -> str:
    """Host wall-clock stamp for a logged event (millisecond resolution)."""
    return datetime.now().isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class _Phase3Snapshot:
    """Atomic, immutable Tk->thread hand-off (read whole by the actuation thread).

    ``gate_open`` folds armed + firmware-mode + tracking readiness; the rest are
    the tunables the thread needs so it never touches Tk state.
    """
    gate_open: bool = False
    params: CueParams = field(default_factory=CueParams)
    seed: int = 0
    reward_incorrect: bool = False
    animal: str = ""
    session_id: str = ""
    session_out_dir: str = ""
    # Operator "pause cues" toggle (Rig-view button; docs/decisions/0058). When
    # set, the actuation thread silences any tone and freezes trial progress
    # without disarming (the gate stays open).
    paused: bool = False


class Phase3ControlView(VisionActuator):
    """Tk module: the Phase 3 tab - host-owned cued task, firmware as actuator.

    Mixes in ``VisionActuator`` for the shared ADR-0043 skeleton (fast-view
    subscription, bounded queue, dedicated actuation thread, atomic snapshot);
    this class adds the cue-gate / task policy, the DAC-authority handshake, and
    the per-session data log."""

    def __init__(self, parent: tk.Misc, camera=None, pipeline=None, send=None,
                 dispatcher=None,
                 animal_id: Optional[Callable[[], str]] = None):
        self._parent = parent
        self._camera = camera
        self._pipeline = pipeline
        self._send = send                 # callable(str) -> None (RigClient.send)
        # Shared reward seam (reward_dispatcher.RewardDispatcher): the ONE place a
        # reward is gated (global rate backstop), recorded (pulse/volume), and sent
        # (R<port>), shared with the Rules engine so a Phase 3 reward is handled
        # identically to a rules reward (docs/decisions/0054, FW-0019). None -> a
        # direct send fallback (no backstop) so the tab still works standalone.
        self._dispatcher = dispatcher
        # The single host-side animal-id source (the Task tab's shared field;
        # docs/decisions/0053). None -> no id (manual arms fall back to "unknown").
        self._animal_id = animal_id
        self.frame = ttk.Frame(parent)
        self._mode = ""                   # last firmware MODE token
        self._closed = False

        # State owned by the actuation thread.
        self._gate = CueGate()
        self._task: Optional[Phase3Task] = None
        self._seq: Optional[TrialSequence] = None
        self._task_events = TaskEvents() if TaskEvents is not None else None
        self._gate_was_open = False
        self._last_fw_cue = ""            # last firmware CUE echo (for status)
        # Session data log (plan matrix + live outcomes; ADR-0047), and the state
        # needed to fill it: which cue is sounding (to route the CUE_OFF edge), the
        # previous zone (to detect the HUB->stem trial-start edge), and the file
        # path. All touched only by the actuation thread.
        self._log: Optional[Phase3Log] = None
        self._log_path: Optional[str] = None
        # Shared session id when armed via the Session controller (docs/decisions/
        # 0048); "" on a manual arm. Set on the Tk thread, snapshotted to the thread.
        self._session_id = ""
        # Session output dir (the session's task/ folder; docs/decisions/0049); ""
        # -> the default host/data/ location for a manual arm.
        self._session_out_dir = ""
        self._active_cue: Optional[str] = None
        self._prev_zone: Optional[str] = None
        # Operator controls (Rig-view buttons; docs/decisions/0058). ``_paused`` is
        # a plain Tk-thread bool (button + poll both run on the Tk thread) folded
        # into the atomic snapshot for the actuation thread. ``_restart_count``
        # tags each restart's data file so a restart never clobbers the prior run.
        self._paused = False
        self._restart_count = 0
        # The center-well lick channel (reward-collection lick), from the shared
        # rig-config pin map (ADR-0021/0022); None if unavailable -> no lick times.
        self._center_lick = load_rig_config().lick_index_by_role.get("center")
        # Live tallies (thread-written, Tk-read for status).
        self._correct = 0
        self._incorrect = 0
        self._reward_count = 0
        self._active_seed = 0
        # Latest immutable table snapshot for cross-tab readers (the stats panel +
        # trial ribbon; docs/decisions/0050). Published by the actuation thread via
        # atomic reference assignment, read by published_state(); no lock needed
        # (mirrors the _snapshot Tk<->thread hand-off, ADR-0043). None until armed.
        self._state: Optional[Phase3StateSnapshot] = None

        # --- actuation thread (ADR-0043 skeleton, shared via VisionActuator) ---
        self._init_actuator(thread_name="phase3-actuation")
        self._act_snapshot = _Phase3Snapshot()
        self._status_text = "phase 3: off"
        # Change-signature of the last progress render (ribbon + planned-trial
        # list), so the Tk poll only rebuilds those widgets when the published
        # table actually changes, not every 100 ms (docs/decisions/0051). Starts
        # as a sentinel so the first poll always draws (None is a valid signature).
        self._progress_sig = _UNSET

        if pipeline is None or TaskEvents is None:
            reason = ("the maze-state pipeline (tracker) is not available"
                      if pipeline is None else "task_events is unavailable")
            ttk.Label(self.frame, padding=24, anchor="center", justify="center",
                      text=f"Phase 3 tab unavailable - {reason}.").pack(
                fill=tk.BOTH, expand=True)
            return

        self._build_ui()
        # Subscribe to the fast view + spawn the actuation thread (VisionActuator).
        self._start_actuation()
        self._schedule_poll()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, info) -> None:
        """Track the firmware mode (Tk) and reflect CUE echoes for status.

        Reads the pre-parsed protocol.LineInfo (docs/decisions/0056). Also snoop
        center-well lick edges (the reward-collection lick) and hand them to the
        actuation thread, which is the sole writer of the data log (this method
        runs on the serial-reader thread; ADR-0043/0047)."""
        if info.mode is not None:
            self._mode = info.mode.upper()
            return
        if info.cue is not None:
            state, kind, freq = info.cue
            self._last_fw_cue = f"fw: {kind} @ {freq} Hz" if state == "ON" else "fw: off"
            return
        lick = info.lick
        if lick is not None and self._center_lick is not None:
            index, rising = lick
            if rising and index == self._center_lick:
                fw_ms = info.parsed.ms if info.parsed is not None else None
                self._enqueue_act(("lick", _now_iso(), fw_ms))

    def close(self) -> None:
        self._closed = True
        # Stop the actuation thread + unsubscribe (VisionActuator) before the
        # best-effort DAC restore below.
        self._stop_actuation()
        # Best-effort: silence the tone + restore firmware autonomy.
        if self._gate_was_open:
            self._send_cmd("CUE_OFF")
            self._send_cmd("CUE,BEAM")

    # ----------------------------------------------- Session controller seams
    def is_armed(self) -> bool:
        """Whether the "Arm Phase 3" checkbox is on. The Session controller now
        REQUIRES Phase 3 to be armed before a session can start (it no longer arms
        Phase 3 itself); see docs/decisions/0060. False if the tab is unavailable
        (no ``_enable_var``)."""
        if not hasattr(self, "_enable_var"):
            return False
        try:
            return bool(self._enable_var.get())
        except tk.TclError:
            return False

    def set_session_context(self, session_id: str = "", out_dir: str = "") -> None:
        """Record the coordinated session's id + output folder WITHOUT arming.

        The Session controller calls this on Start once it has confirmed Phase 3 is
        already armed (docs/decisions/0060), and again with no arguments on Stop to
        RELEASE the context. The ``session_id`` is threaded into the data-file name
        + header so it correlates with the video + manifest, and ``out_dir`` is the
        session's ``task/`` folder the data file is written into (docs/decisions/
        0049). Unlike the old ``arm()`` this does NOT flip the Arm checkbox.

        Because the gate is already open by now, no rising edge will re-run
        ``_arm_session``, so this also asks the actuation thread to re-point an
        already-open data file at the new session (docs/decisions/0066). No-op if
        the tab is unavailable (no pipeline)."""
        if not hasattr(self, "_enable_var"):
            return
        self._session_id = session_id or ""
        self._session_out_dir = out_dir or ""
        # Publish the new context BEFORE asking for the rebind: the Tk poll would
        # otherwise be up to POLL_INTERVAL_MS behind, and the thread would rebind
        # using the PREVIOUS session's snapshot.
        try:
            self._publish_snapshot()
        except tk.TclError:
            return
        self._enqueue_act(("cmd", "rebind"))

    def arm(self, session_id: str = "", out_dir: str = "") -> None:
        """Arm Phase 3, recording an optional coordinated ``session_id`` / ``out_dir``
        (see docs/decisions/0048, 0060).

        Records the shared ``session_id`` (threaded into the data-file name +
        header so it correlates with the video + manifest) and, optionally, the
        session's ``out_dir`` (its ``task/`` folder, docs/decisions/0049) that the
        data file is written into; then flips the Arm checkbox. The task still
        starts only once the gate opens (Free Roam + tracking + a valid zone map),
        exactly as a manual arm. No-op if the tab is unavailable (no pipeline).

        Note: the Session controller no longer calls this - it requires Phase 3 to
        be armed first and only sets the session context (set_session_context);
        this remains for a direct/programmatic arm. See docs/decisions/0060.
        """
        if not hasattr(self, "_enable_var"):
            return
        self._session_id = session_id or ""
        self._session_out_dir = out_dir or ""
        self._enable_var.set(True)

    def disarm(self) -> None:
        """Disarm Phase 3. No-op if tab unavailable.

        The Session controller no longer calls this on Stop (Phase 3 keeps running
        so the task + stats persist; docs/decisions/0060) - this remains for a
        direct/programmatic disarm."""
        if not hasattr(self, "_enable_var"):
            return
        self._enable_var.set(False)
        self._session_id = ""
        self._session_out_dir = ""

    def current_log_path(self) -> Optional[str]:
        """Path of the in-progress session data file, or None until the gate
        opens and a session starts. Used by the Session controller's manifest."""
        return self._log_path

    def published_state(self) -> Optional[Phase3StateSnapshot]:
        """Latest immutable Phase 3 table snapshot for cross-tab readers (the
        stats panel + trial ribbon), or None until a session is armed. Thread-safe:
        the actuation thread publishes a fresh immutable snapshot by atomic
        reference assignment; this getter just returns the current reference
        (docs/decisions/0050; mirrors         the _snapshot hand-off, ADR-0043)."""
        return self._state

    # ------------------------------------------------ operator button seams
    # The Rig-view trial-control buttons (docs/decisions/0058) call these from the
    # Tk thread. Skip/Repeat/Back/Extend/Restart enqueue a command the actuation
    # thread applies (it is the sole task-state mutator); Pause is a Tk-thread bool
    # folded into the snapshot. All are no-ops when the tab is unavailable.
    def skip_current_trial(self) -> None:
        """Abandon the current trial, stop the cues, advance to the next (pending
        the rat's return to the hub)."""
        self._enqueue_act(("cmd", "skip"))

    def repeat_current_trial(self) -> None:
        """Abandon the current trial and re-run the SAME trial (don't advance)."""
        self._enqueue_act(("cmd", "repeat"))

    def step_back_trial(self) -> None:
        """Step back one trial (undo an accidental advance) and clear its row."""
        self._enqueue_act(("cmd", "back"))

    def extend_trials(self) -> None:
        """Append one balanced block of trials to the live plan (docs/decisions/0059)."""
        self._enqueue_act(("cmd", "extend"))

    def restart_session(self) -> None:
        """Regenerate the session from the seed at trial 1 into a fresh data file."""
        self._enqueue_act(("cmd", "restart"))

    def set_paused(self, paused: bool) -> None:
        """Mute/unmute the cues (freezes trial progress without disarming)."""
        self._paused = bool(paused)

    def toggle_paused(self) -> bool:
        """Flip the pause state; return the new value (for the toggle button)."""
        self._paused = not self._paused
        return self._paused

    def is_paused(self) -> bool:
        return self._paused

    # --------------------------------------------------------------- thread
    def _process_act_item(self, item, snap) -> None:
        """Sole caller of CueGate.step / TaskEvents.update / Phase3Task.update /
        self._send (ADR-0043). Called by the shared VisionActuator loop with the
        atomic snapshot; splits frames from routed center-well lick edges."""
        if item[0] == "lick":
            self._process_lick(item[1], item[2])
        elif item[0] == "cmd":
            self._process_command(item[1], snap)
        else:
            self._process_frame(item[1], snap)

    def _arm_session(self, snap: _Phase3Snapshot, *, restart_index: int = 0) -> None:
        """Rising-edge setup: fresh sequence + task from the seed; take the DAC.

        ``restart_index`` > 0 means this is an operator Restart (docs/decisions/
        0058), not a gate rising edge: it tags the data-file name (``_r<n>``) so the
        aborted run's file is preserved. A normal arm (index 0) resets the counter."""
        if restart_index == 0:
            self._restart_count = 0
        self._gate.reset()
        if self._task_events is not None:
            self._task_events.reset()
        self._seq = TrialSequence(TrialConfig(), seed=snap.seed)
        self._task = Phase3Task(
            self._seq, Phase3Config(reward_incorrect=snap.reward_incorrect))
        self._active_seed = snap.seed
        self._correct = self._incorrect = self._reward_count = 0
        self._active_cue = None
        self._prev_zone = None
        # Start a fresh session data file: the planned matrix is written now, then
        # each trial's outcome is filled in as it resolves (ADR-0047). When armed
        # via the Session controller the shared session_id names the file (so the
        # video + Phase 3 file share the stamp) and is stamped into the header;
        # otherwise fall back to <animal>_<stamp> (docs/decisions/0048).
        animal = snap.animal or "unknown"
        if snap.session_id:
            base = f"phase3_{protocol.sanitize_animal_id(snap.session_id)}"
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"phase3_{protocol.sanitize_animal_id(animal)}_{stamp}"
        if restart_index:
            base = f"{base}_r{restart_index}"
        self._log = Phase3Log(self._seq, animal=animal, session_start_iso=_now_iso(),
                              reward_incorrect=snap.reward_incorrect,
                              session_id=snap.session_id)
        # Write into the session's task/ folder when armed via a session, else the
        # default host/data/ tree (docs/decisions/0049). _write_log makes the dir.
        out_dir = snap.session_out_dir or DATA_DIR
        self._log_path = os.path.join(out_dir, f"{base}.csv")
        self._write_log()
        self._publish_state(armed=True)      # expose the fresh plan to other tabs
        self._send_cmd("CUE,VISION")         # host owns the DAC; suppress auto-tone

    # ------------------------------------------------- operator button commands
    def _process_command(self, name: str, snap: _Phase3Snapshot) -> None:
        """Apply one Rig-view trial-control command (docs/decisions/0058).

        Runs on the actuation thread like every other core call, so it is the sole
        mutator of the task state. Skip/Repeat/Back cut any sounding tone and clear
        the per-trip cue/gate state, so the (new) current trial is "pending start"
        - its choice cue only re-arms once the rat is back at the hub and heads up
        the stem again."""
        if name == "extend":
            self._extend_trials(snap)
            return
        if name == "restart":
            self._restart_session(snap)
            return
        if name == "rebind":
            self._rebind_session_output(snap)
            return
        if self._task is None or self._seq is None or self._log is None:
            return
        idx = self._task.index
        iso = _now_iso()
        if name == "skip":
            self._log_call(lambda: self._log.trial_abandoned(idx, iso))
            self._task.abandon_current()
        elif name == "repeat":
            self._log_call(lambda: self._log.reset_trial(idx))
            self._task.repeat_current()
        elif name == "back":
            new_idx = self._task.step_back()
            self._log_call(lambda: self._log.reset_trial(new_idx))
        else:
            return
        self._silence_and_rearm()
        self._write_log()
        self._publish_state(armed=snap.gate_open)

    def _silence_and_rearm(self) -> None:
        """Cut any sounding tone and reset the per-trip cue/gate state, so the next
        trial's cue waits for a fresh up-the-stem excursion from the hub."""
        self._send_cmd("CUE_OFF")
        self._active_cue = None
        self._prev_zone = None
        self._gate.reset()

    def _extend_trials(self, snap: _Phase3Snapshot) -> None:
        """Append one balanced block of trials to the live plan (docs/decisions/
        0059), for when the rat runs past the planned count. Append-only: the
        already-run trials are untouched, and a task that had completed resumes."""
        if self._seq is None or self._log is None:
            return
        self._seq.extend()                   # one block_size block
        self._log.extend_to(self._seq)
        self._write_log()
        self._publish_state(armed=snap.gate_open)

    def _rebind_session_output(self, snap: _Phase3Snapshot) -> None:
        """Re-point an already-open data file at the session that now owns it.

        ADR-0060 has the operator arm Phase 3 *before* Start Session, so the gate is
        already open when the session mints its folder and no rising edge fires to
        re-run ``_arm_session``. Without this, ``_log_path`` keeps whatever the last
        arm chose - the ``host/data/`` fallback on the first arm, or a previous
        (already stopped) session's ``task/`` folder - so the session's own ``task/``
        stayed empty and its manifest recorded another session's file. Start now
        re-points the path and the header's session id; since ``_write_log`` renders
        the WHOLE table, the session's file is complete from its first write,
        including any trials run between arming and Start. See docs/decisions/0066.

        No-op before the first arm (the gate then binds the right folder itself) and
        on the Stop release, which clears the context rather than moving the file.
        """
        if self._log is None or not snap.session_id or not snap.session_out_dir:
            return
        # The session's task/ folder starts empty, so this run takes the plain name
        # and the restart counter restarts with it: a later Restart appends _r1
        # without clobbering this file, exactly as ADR-0058 intends.
        new_path = os.path.join(
            snap.session_out_dir,
            f"phase3_{protocol.sanitize_animal_id(snap.session_id)}.csv")
        if new_path == self._log_path:
            return
        self._restart_count = 0
        self._log.session_id = snap.session_id   # the header's correlation id
        self._log_path = new_path
        self._write_log()
        self._publish_state(armed=snap.gate_open)

    def _restart_session(self, snap: _Phase3Snapshot) -> None:
        """Regenerate the session from the seed at trial 1 into a FRESH data file
        (the aborted run's file is preserved via the ``_r<n>`` tag; docs/decisions/
        0058). No-op unless a session has been armed at least once."""
        if self._log is None:
            return
        self._restart_count += 1
        self._silence_and_rearm()
        self._arm_session(snap, restart_index=self._restart_count)

    def _process_frame(self, bundle, snap) -> None:
        # Gate edges own the DAC-authority handshake + a clean session (re)start.
        if snap.gate_open and not self._gate_was_open:
            self._arm_session(snap)
        elif not snap.gate_open and self._gate_was_open:
            self._send_cmd("CUE_OFF")
            self._send_cmd("CUE,BEAM")       # restore firmware autonomy
            self._gate.reset()
            self._active_cue = None
            self._publish_state(armed=False)  # keep the last table, mark disarmed
        self._gate_was_open = snap.gate_open

        if not snap.gate_open or self._task is None or self._seq is None:
            self._publish_status(snap)
            return

        # Operator "pause cues" (docs/decisions/0058): silence any tone and freeze
        # trial progress without disarming. Resetting the gate/prev-zone each tick
        # means that on resume the next cue re-arms only from a fresh hub->stem run.
        if snap.paused:
            if self._active_cue is not None:
                self._send_cmd("CUE_OFF")
                self._active_cue = None
            self._gate.reset()
            self._prev_zone = None
            self._publish_status(snap)
            return

        st = bundle.state
        # Fold task-semantic context first (so a choice/return this tick is seen).
        task_frame = self._task_events.update(st) if self._task_events else None

        # The trial in progress this tick (captured BEFORE task.update may advance
        # it), and one wall-clock stamp shared by every event logged this tick.
        idx = self._task.index
        iso = _now_iso()
        changed = False

        # Trial start = the first HUB->stem transition of a trial (the log keeps
        # only the first; a poke-and-retreat re-entry is ignored). ADR-0047.
        if st.valid:
            if self._prev_zone == HUB and st.zone == CENTRAL_STEM:
                self._log_call(lambda: self._log.trial_started(idx, iso))
                changed = True
            self._prev_zone = st.zone

        # WHEN: the positional cue gate. Host supplies identity via ,<hz> (v6).
        # Each cue carries a shared task-event credential describing where in the
        # lap the animal is: RETURN needs a stem-out excursion so it marks only a
        # genuine inbound leg (docs/decisions/0061), and CHOICE needs this trip to
        # have DEPARTED the hub up the stem, so a lap run backwards is silent
        # crossing the stem on its way home (docs/decisions/0067).
        self._gate.set_params(snap.params)
        outbound = task_frame.outbound_used_stem if task_frame is not None else False
        on_leg = task_frame.left_hub_via_stem if task_frame is not None else False
        d = self._gate.step(st.zone, st.direction, st.confidence, st.valid,
                            outbound, on_leg)
        if d.started == CHOICE:
            freq = self._task.current_choice_freq()
            self._send_cmd(f"CUE_ON,CHOICE,{freq}" if freq is not None
                           else "CUE_ON,CHOICE")
            self._active_cue = CHOICE
            self._log_call(lambda: self._log.choice_cue_started(idx, iso))
            changed = True
        elif d.started == RETURN:
            # Identity now comes from this arm's planned return schedule, looked up
            # by the PHYSICAL return arm the rat entered (ADR-0047).
            side = _SIDE_OF_ARM.get(d.return_arm)
            freq = self._seq.return_freq(side, idx) if side is not None else None
            cue = self._seq.return_cue(side, idx) if side is not None else None
            self._send_cmd(f"CUE_ON,RETURN,{freq}" if freq is not None
                           else "CUE_ON,RETURN")
            self._active_cue = RETURN
            self._log_call(lambda: self._log.return_cue_started(
                idx, d.return_arm or "", cue, iso))
            changed = True
        elif d.stopped:
            self._send_cmd("CUE_OFF")
            if self._active_cue == CHOICE:
                self._log_call(lambda: self._log.choice_cue_stopped(idx, iso))
                changed = True
            elif self._active_cue == RETURN:
                self._log_call(lambda: self._log.return_cue_stopped(idx, iso))
                changed = True
            self._active_cue = None

        # WHAT/score: fold the task frame -> reward + advancement.
        if task_frame is not None:
            dec = self._task.update(task_frame)
            if dec.choice_side is not None and dec.outcome is not None:
                self._log_call(lambda: self._log.choice_scored(
                    dec.trial_index, dec.choice_side, dec.outcome, iso))
                changed = True
            if dec.reward and self._fire_reward():
                # Only count + log the reward when it actually fired: the shared
                # backstop may drop one too soon after the last (ADR-0054).
                self._reward_count += 1
                self._log_call(lambda: self._log.reward_fired(dec.trial_index, iso))
                changed = True
            if dec.advanced:
                self._log_call(lambda: self._log.trial_ended(dec.trial_index, iso))
                changed = True
            if dec.outcome == CORRECT:
                self._correct += 1
            elif dec.outcome == INCORRECT:
                self._incorrect += 1

        if changed:
            self._write_log()
            self._publish_state(armed=True)
        self._publish_status(snap)

    def _process_lick(self, iso: str, fw_ms) -> None:
        """A center-well lick from the serial thread: attribute it to the trial
        awaiting its reward-collection lick and persist (ADR-0047)."""
        if self._log is not None and self._log.lick(iso, fw_ms):
            self._write_log()
            self._publish_state(armed=True)

    def _log_call(self, fn) -> None:
        """Run a log mutation only if a session log exists (defensive; the log is
        created on arm and the frame path already checks the task exists)."""
        if self._log is not None:
            fn()

    def _publish_state(self, *, armed: bool) -> None:
        """Publish an immutable freeze of the table for cross-tab readers (P0,
        docs/decisions/0050). Called by the actuation thread whenever the table
        changes (and on arm/disarm), so readers on the Tk thread always see a
        consistent snapshot without touching the live rows."""
        log = self._log
        if log is None:
            self._state = None
            return
        idx = self._task.index if self._task is not None else 0
        self._state = log.snapshot(idx, armed=armed)

    def _write_log(self) -> None:
        """Atomically persist the whole table (temp file + os.replace), so a crash
        never leaves a half-written CSV. Cheap for a few hundred rows."""
        if self._log is None or self._log_path is None:
            return
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            tmp = self._log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(self._log.render_csv())
            os.replace(tmp, self._log_path)
        except OSError:
            pass

    def _publish_status(self, snap: _Phase3Snapshot) -> None:
        if not snap.gate_open:
            self._status_text = f"gated ({self._gate_reason()})"
            return
        if snap.paused:
            self._status_text = "PAUSED  (cues muted; still armed - press Resume)"
            return
        n = len(self._seq) if self._seq is not None else 0
        idx = self._task.index if self._task is not None else 0
        done = self._correct + self._incorrect
        acc = (100.0 * self._correct / done) if done else 0.0
        extra = f"  drops:{self._act_drops}" if self._act_drops else ""
        self._status_text = (
            f"ARMED  seed:{self._active_seed}  trial:{min(idx + 1, n)}/{n}  "
            f"correct:{self._correct}/{done} ({acc:.0f}%)  "
            f"rewards:{self._reward_count}  {self._last_fw_cue}{extra}")

    def _send_cmd(self, cmd: str) -> None:
        if self._send is None:
            return
        try:
            self._send(cmd)
        except Exception:
            pass

    def _fire_reward(self) -> bool:
        """Dispense the trial reward through the shared dispatcher; return whether
        it actually fired (the global backstop may suppress it). Falls back to a
        direct R<port> send (no backstop) if no dispatcher is wired. ADR-0054."""
        port = self._task.cfg.reward_port
        pulse = self._task.cfg.reward_pulse_ms
        if self._dispatcher is None:
            self._send_cmd(f"R{port}1,{pulse}")
            return True
        rec = self._dispatcher.fire(port, pulse, reason="phase3_reward",
                                    rule_id="phase3")
        return rec.fired

    # ---------------------------------------------------------------- gating
    def _tracking_running(self) -> bool:
        try:
            return self._camera is not None and self._camera.tracking_enabled()
        except Exception:
            return False

    def _mode_is_freeroam(self) -> bool:
        return self._mode in FREEROAM_TOKENS

    def _gate_open(self) -> bool:
        return bool(self._enable_var.get() and self._tracking_running()
                    and self._mode_is_freeroam()
                    and self._pipeline is not None
                    and self._pipeline.zones_valid())

    def _gate_reason(self) -> str:
        if not self._enable_var.get():
            return "not armed"
        if self._pipeline is None or not self._pipeline.zones_valid():
            return "no maze zone map (zones.json)"
        if not self._tracking_running():
            return "tracking is not running"
        if not self._mode_is_freeroam():
            return f"firmware not in Free Roam (mode={self._mode or '?'})"
        return "ready"

    def _current_params(self) -> CueParams:
        try:
            conf = float(self._conf_var.get())
        except (tk.TclError, ValueError):
            conf = 0.0
        return CueParams(
            choice_enabled=bool(self._choice_var.get()),
            return_enabled=bool(self._return_var.get()),
            min_confidence=conf,
            require_up_stem=bool(self._up_stem_var.get()))

    def _current_seed(self) -> int:
        try:
            return int(self._seed_var.get()) & SEED_MASK
        except (tk.TclError, ValueError):
            return 0

    def _new_seed(self) -> None:
        """Draw a fresh random seed into the field (takes effect on next arm)."""
        self._seed_var.set(str(random.randint(1, SEED_MASK)))

    # -------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 3}
        root = ttk.Frame(self.frame, padding=8)
        root.pack(fill=tk.BOTH, expand=True)

        self._enable_var = tk.BooleanVar(value=False)
        arm = ttk.Checkbutton(
            root, text="Arm Phase 3 (host-owned cued task; firmware = actuator)",
            variable=self._enable_var)
        arm.grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        tip(arm, "phase3.arm")

        self._choice_var = tk.BooleanVar(value=True)
        c = ttk.Checkbutton(root, text="Choice cue (central stem, up-stem)",
                            variable=self._choice_var)
        c.grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        tip(c, "phase3.choice")

        self._up_stem_var = tk.BooleanVar(value=True)
        u = ttk.Checkbutton(root, text="   require up-stem direction",
                            variable=self._up_stem_var)
        u.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        tip(u, "phase3.up_stem")

        self._return_var = tk.BooleanVar(value=True)
        r = ttk.Checkbutton(root, text="Return cue (return arm, fail-safe)",
                            variable=self._return_var)
        r.grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        tip(r, "phase3.return")

        self._reward_incorrect_var = tk.BooleanVar(value=False)
        ri = ttk.Checkbutton(root, text="Reward incorrect choices (shaping)",
                             variable=self._reward_incorrect_var)
        ri.grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        tip(ri, "phase3.reward_incorrect")

        ttk.Label(root, text="Min confidence:").grid(
            row=5, column=0, sticky="w", **pad)
        self._conf_var = tk.StringVar(value="0.0")
        conf = ttk.Spinbox(root, from_=0.0, to=1.0, increment=0.05, width=6,
                           textvariable=self._conf_var)
        conf.grid(row=5, column=1, sticky="w", **pad)
        tip(conf, "phase3.min_confidence")

        # NOTE: the animal id is no longer entered here - it comes from the Task
        # tab's single shared Animal ID field via the injected ``animal_id``
        # provider (docs/decisions/0053, FW-0005).
        ttk.Label(root, text="Sequence seed:").grid(
            row=6, column=0, sticky="w", **pad)
        seed_row = ttk.Frame(root)
        seed_row.grid(row=6, column=1, sticky="w", **pad)
        self._seed_var = tk.StringVar(value=str(random.randint(1, SEED_MASK)))
        seed = ttk.Entry(seed_row, width=12, textvariable=self._seed_var)
        seed.pack(side=tk.LEFT)
        tip(seed, "phase3.seed")
        newb = ttk.Button(seed_row, text="New", width=5, command=self._new_seed)
        newb.pack(side=tk.LEFT, padx=(4, 0))
        tip(newb, "phase3.new_seed")
        ttk.Label(root, text="(applied when you arm)").grid(
            row=7, column=1, sticky="w", padx=6)

        ttk.Separator(root, orient="horizontal").grid(
            row=8, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self._status_var = tk.StringVar(value="phase 3: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=460, justify="left").grid(
            row=9, column=0, columnspan=2, sticky="w", **pad)

        # In-tab progress visualization (docs/decisions/0051): a compact ribbon of
        # the current trial + neighbours, and a scrollable list of all N planned
        # trials with the current one highlighted. Both are read-only views of the
        # published table snapshot (self._state), refreshed on the Tk poll.
        self._build_progress_ui(root, row=10)

    def _build_progress_ui(self, root: ttk.Frame, *, row: int) -> None:
        """Build the trial ribbon + scrollable planned-trial list (ADR-0051).

        The ribbon is the shared TrialRibbonView widget (docs/decisions/0057);
        this tab drives it in push mode (its own Tk poll calls render()), so it
        also rebuilds the planned-trial list on the same change signature."""
        self._ribbon = TrialRibbonView(root)
        self._ribbon.widget().grid(row=row, column=0, columnspan=2, sticky="ew",
                                   padx=6, pady=(8, 3))

        seq = ttk.LabelFrame(root, text="Planned trials")
        seq.grid(row=row + 1, column=0, columnspan=2, sticky="nsew",
                 padx=6, pady=3)
        seq.rowconfigure(0, weight=1)
        seq.columnconfigure(0, weight=1)
        tip(seq, "phase3.sequence")
        self._seq_list = tk.Listbox(
            seq, height=SEQ_LIST_ROWS, activestyle="none", exportselection=False,
            font=("TkFixedFont", 9), borderwidth=0, highlightthickness=0)
        self._seq_list.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(seq, orient="vertical", command=self._seq_list.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self._seq_list.configure(yscrollcommand=bar.set)
        # Let the planned-trial list take the slack when the tab is tall.
        root.rowconfigure(row + 1, weight=1)
        root.columnconfigure(0, weight=1)

    # ------------------------------------------------------------- Tk poll
    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._publish_snapshot()
            self._status_var.set(self._status_text)
            self._refresh_progress()
        except tk.TclError:
            return
        try:
            self.frame.after(POLL_INTERVAL_MS, self._schedule_poll)
        except tk.TclError:
            return

    def _publish_snapshot(self) -> None:
        """Tk poll: snapshot the gate + tunables the actuation thread needs, so
        the thread never reads Tk state (ADR-0043)."""
        self._act_snapshot = _Phase3Snapshot(
            gate_open=self._gate_open(),
            params=self._current_params(),
            seed=self._current_seed(),
            reward_incorrect=bool(self._reward_incorrect_var.get()),
            animal=self._current_animal(),
            session_id=self._session_id,
            session_out_dir=self._session_out_dir,
            paused=self._paused)

    def _current_animal(self) -> str:
        """Current animal id from the Task tab's shared field (docs/decisions/
        0053); "" when no provider is wired."""
        if self._animal_id is None:
            return ""
        try:
            return self._animal_id().strip()
        except Exception:
            return ""

    # -------------------------------------------------- progress visualization
    def _refresh_progress(self) -> None:
        """Redraw the ribbon + planned-trial list from the published snapshot.

        Reads the immutable ``self._state`` (ADR-0050) on the Tk poll - never the
        actuation thread's live table - and guards ``None`` (before the first
        arm). Rebuilds only when the table actually changed (a cheap change
        signature), so a steady session doesn't churn widgets every 100 ms
        (docs/decisions/0051)."""
        state = self._state
        sig = progress_signature(state)
        if sig == self._progress_sig:
            return
        self._progress_sig = sig
        self._ribbon.render(state)
        self._rebuild_sequence(state)

    def _rebuild_sequence(self, state: Optional[Phase3StateSnapshot]) -> None:
        lb = self._seq_list
        lb.delete(0, tk.END)
        if state is None or state.total == 0:
            lb.insert(tk.END, "  (not armed)")
            return
        current = max(0, min(state.current_index, state.total - 1))
        for row in state.rows:
            lb.insert(tk.END, self._seq_line(row, current))
        if 0 <= current < lb.size():
            lb.itemconfig(current, background=_CELL_CURRENT_BG)
            lb.see(current)                  # keep the current trial in view

    @staticmethod
    def _seq_line(row, current: int) -> str:
        """One monospace planned-trial row for the scrollable list. ``row`` is a
        phase3_log.Phase3RowView; ``correct_arm`` is the arm the choice cue maps
        to (identity checked in test_phase3_ribbon)."""
        mark = ">" if (row.trial - 1) == current else " "
        arm = _ARM_SHORT.get(row.correct_arm, "?")
        left = _CUE_SHORT.get(row.return_cue_left, "?")
        right = _CUE_SHORT.get(row.return_cue_right, "?")
        if row.outcome == ABANDONED:
            result = "skipped"
        elif row.outcome is not None:
            chosen = _ARM_SHORT.get(row.chosen_arm, "?")
            result = f"chose {chosen} {'OK' if row.outcome == CORRECT else 'X'}"
        else:
            result = ""
        return (f"{mark}{row.trial:>4}  {row.choice_cue:<4}->{arm}  "
                f"ret L:{left} R:{right}   {result}")
