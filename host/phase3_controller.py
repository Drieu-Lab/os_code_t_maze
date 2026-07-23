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
import queue
import random
import threading
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from tkinter import ttk
from typing import Optional

import protocol
from cue_gate import CHOICE, RETURN, CueGate, CueParams
from maze_state import CENTRAL_STEM, HUB
from phase3_log import Phase3Log
from phase3_task import CORRECT, INCORRECT, Phase3Config, Phase3Task
from rig_config import load_rig_config
from trial_sequence import LEFT, RIGHT, TrialConfig, TrialSequence

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
QUEUE_MAX = 512                 # bounded fast-frame hand-off (drop-newest on lag)
SEED_MASK = 0xFFFFFFFF          # the sequence PRNG is 32-bit (trial_sequence.lcg)

# Per-session data files live under host/data/ (same tree as rig_logger.py).
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Physical return arm ("left"/"right", from CueGate) -> trial_sequence side int,
# to look up that arm's planned return cue (see docs/decisions/0047).
_SIDE_OF_ARM = {"left": LEFT, "right": RIGHT}


def _now_iso() -> str:
    """Host wall-clock stamp for a logged event (millisecond resolution)."""
    return datetime.now().isoformat(timespec="milliseconds")


def _sanitize(name: str) -> str:
    """Filename-safe slug of an animal id (mirrors rig_logger._sanitize)."""
    slug = "".join(ch for ch in name.strip() if ch.isalnum() or ch in "-_")
    return slug or "unknown"


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


class Phase3ControlView:
    """Tk module: the Phase 3 tab - host-owned cued task, firmware as actuator."""

    def __init__(self, parent: tk.Misc, camera=None, pipeline=None, send=None):
        self._parent = parent
        self._camera = camera
        self._pipeline = pipeline
        self._send = send                 # callable(str) -> None (RigClient.send)
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
        self._active_cue: Optional[str] = None
        self._prev_zone: Optional[str] = None
        # The center-well lick channel (reward-collection lick), from the shared
        # rig-config pin map (ADR-0021/0022); None if unavailable -> no lick times.
        self._center_lick = load_rig_config().lick_index_by_role.get("center")
        # Live tallies (thread-written, Tk-read for status).
        self._correct = 0
        self._incorrect = 0
        self._reward_count = 0
        self._active_seed = 0
        self._drops = 0

        # --- actuation thread (ADR-0043 pattern) --------------------------
        self._queue: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._snapshot = _Phase3Snapshot()
        self._status_text = "phase 3: off"

        if pipeline is None or TaskEvents is None:
            reason = ("the maze-state pipeline (tracker) is not available"
                      if pipeline is None else "task_events is unavailable")
            ttk.Label(self.frame, padding=24, anchor="center", justify="center",
                      text=f"Phase 3 tab unavailable - {reason}.").pack(
                fill=tk.BOTH, expand=True)
            return

        self._build_ui()
        self._start_thread()
        self._schedule_poll()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, line: str) -> None:
        """Track the firmware mode (Tk) and reflect CUE echoes for status.

        Also snoop center-well lick edges (the reward-collection lick) and hand
        them to the actuation thread, which is the sole writer of the data log
        (this method runs on the serial-reader thread; ADR-0043/0047)."""
        mode = protocol.mode_name(line)
        if mode is not None:
            self._mode = mode.upper()
            return
        info = protocol.cue_info(line)
        if info is not None:
            state, kind, freq = info
            self._last_fw_cue = f"fw: {kind} @ {freq} Hz" if state == "ON" else "fw: off"
            return
        lick = protocol.lick_edge(line)
        if lick is not None and self._center_lick is not None:
            index, rising = lick
            if rising and index == self._center_lick:
                parsed = protocol.parse(line)
                fw_ms = parsed.ms if parsed is not None else None
                try:
                    self._queue.put_nowait(("lick", _now_iso(), fw_ms))
                except queue.Full:
                    self._drops += 1

    def close(self) -> None:
        self._closed = True
        self._stop.set()
        if self._pipeline is not None:
            try:
                self._pipeline.unsubscribe_fast(self._on_fast_state)
            except Exception:
                pass
        try:
            self._queue.put_nowait(None)   # wake the loop so it can exit
        except queue.Full:
            pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        # Best-effort: silence the tone + restore firmware autonomy.
        if self._gate_was_open:
            self._send_cmd("CUE_OFF")
            self._send_cmd("CUE,BEAM")

    # ----------------------------------------------- Session controller seams
    def arm(self, session_id: str = "") -> None:
        """Arm Phase 3 as part of a coordinated session (see docs/decisions/0048).

        Records the shared ``session_id`` (threaded into the data-file name +
        header so it correlates with the video + manifest) and flips the Arm
        checkbox. The task still starts only once the gate opens (Free Roam +
        tracking + a valid zone map), exactly as a manual arm. No-op if the tab
        is unavailable (no pipeline/task_events).
        """
        if not hasattr(self, "_enable_var"):
            return
        self._session_id = session_id or ""
        self._enable_var.set(True)

    def disarm(self) -> None:
        """Disarm Phase 3 (Session controller stop). No-op if tab unavailable."""
        if not hasattr(self, "_enable_var"):
            return
        self._enable_var.set(False)
        self._session_id = ""

    def current_log_path(self) -> Optional[str]:
        """Path of the in-progress session data file, or None until the gate
        opens and a session starts. Used by the Session controller's manifest."""
        return self._log_path

    # --------------------------------------------------------------- thread
    def _start_thread(self) -> None:
        self._pipeline.subscribe_fast(self._on_fast_state)
        self._thread = threading.Thread(
            target=self._loop, name="phase3-actuation", daemon=True)
        self._thread.start()

    def _on_fast_state(self, bundle) -> None:
        """Pipeline fast-view callback - runs INLINE on the tracking worker.

        Must be cheap + non-blocking: just enqueue for the actuation thread."""
        try:
            self._queue.put_nowait(("frame", bundle))
        except queue.Full:
            self._drops += 1

    def _loop(self) -> None:
        """The single actuation thread: drain frames and be the sole caller of
        CueGate.step / TaskEvents.update / Phase3Task.update / self._send."""
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:                 # close() sentinel
                continue
            try:
                if item[0] == "lick":
                    self._process_lick(item[1], item[2])
                else:
                    self._process_frame(item[1])
            except Exception:
                pass

    def _arm_session(self, snap: _Phase3Snapshot) -> None:
        """Rising-edge setup: fresh sequence + task from the seed; take the DAC."""
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
            base = f"phase3_{_sanitize(snap.session_id)}"
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            base = f"phase3_{_sanitize(animal)}_{stamp}"
        self._log = Phase3Log(self._seq, animal=animal, session_start_iso=_now_iso(),
                              reward_incorrect=snap.reward_incorrect,
                              session_id=snap.session_id)
        self._log_path = os.path.join(DATA_DIR, f"{base}.csv")
        self._write_log()
        self._send_cmd("CUE,VISION")         # host owns the DAC; suppress auto-tone

    def _process_frame(self, bundle) -> None:
        snap = self._snapshot                # atomic read of the immutable snapshot

        # Gate edges own the DAC-authority handshake + a clean session (re)start.
        if snap.gate_open and not self._gate_was_open:
            self._arm_session(snap)
        elif not snap.gate_open and self._gate_was_open:
            self._send_cmd("CUE_OFF")
            self._send_cmd("CUE,BEAM")       # restore firmware autonomy
            self._gate.reset()
            self._active_cue = None
        self._gate_was_open = snap.gate_open

        if not snap.gate_open or self._task is None or self._seq is None:
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
        self._gate.set_params(snap.params)
        d = self._gate.step(st.zone, st.direction, st.confidence, st.valid)
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
            if dec.reward:
                self._send_cmd(f"R{self._task.cfg.reward_port}1,"
                               f"{self._task.cfg.reward_pulse_ms}")
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
        self._publish_status(snap)

    def _process_lick(self, iso: str, fw_ms) -> None:
        """A center-well lick from the serial thread: attribute it to the trial
        awaiting its reward-collection lick and persist (ADR-0047)."""
        if self._log is not None and self._log.lick(iso, fw_ms):
            self._write_log()

    def _log_call(self, fn) -> None:
        """Run a log mutation only if a session log exists (defensive; the log is
        created on arm and the frame path already checks the task exists)."""
        if self._log is not None:
            fn()

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
        n = len(self._seq) if self._seq is not None else 0
        idx = self._task.index if self._task is not None else 0
        done = self._correct + self._incorrect
        acc = (100.0 * self._correct / done) if done else 0.0
        extra = f"  drops:{self._drops}" if self._drops else ""
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

        ttk.Label(root, text="Animal ID:").grid(row=6, column=0, sticky="w", **pad)
        self._animal_var = tk.StringVar(value="")
        animal = ttk.Entry(root, width=12, textvariable=self._animal_var)
        animal.grid(row=6, column=1, sticky="w", **pad)
        tip(animal, "phase3.animal")

        ttk.Label(root, text="Sequence seed:").grid(
            row=7, column=0, sticky="w", **pad)
        seed_row = ttk.Frame(root)
        seed_row.grid(row=7, column=1, sticky="w", **pad)
        self._seed_var = tk.StringVar(value=str(random.randint(1, SEED_MASK)))
        seed = ttk.Entry(seed_row, width=12, textvariable=self._seed_var)
        seed.pack(side=tk.LEFT)
        tip(seed, "phase3.seed")
        newb = ttk.Button(seed_row, text="New", width=5, command=self._new_seed)
        newb.pack(side=tk.LEFT, padx=(4, 0))
        tip(newb, "phase3.new_seed")
        ttk.Label(root, text="(applied when you arm)").grid(
            row=8, column=1, sticky="w", padx=6)

        ttk.Separator(root, orient="horizontal").grid(
            row=9, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self._status_var = tk.StringVar(value="phase 3: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=460, justify="left").grid(
            row=10, column=0, columnspan=2, sticky="w", **pad)

    # ------------------------------------------------------------- Tk poll
    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._publish_snapshot()
            self._status_var.set(self._status_text)
        except tk.TclError:
            return
        try:
            self.frame.after(POLL_INTERVAL_MS, self._schedule_poll)
        except tk.TclError:
            return

    def _publish_snapshot(self) -> None:
        """Tk poll: snapshot the gate + tunables the actuation thread needs, so
        the thread never reads Tk state (ADR-0043)."""
        self._snapshot = _Phase3Snapshot(
            gate_open=self._gate_open(),
            params=self._current_params(),
            seed=self._current_seed(),
            reward_incorrect=bool(self._reward_incorrect_var.get()),
            animal=self._animal_var.get().strip(),
            session_id=self._session_id)
