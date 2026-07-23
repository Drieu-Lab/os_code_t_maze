"""
cue_controller.py - "Cue" tab for experiment_control.py.

The thin GUI/host shell over the pure cue gate (cue_gate.py). It is the HOST-side
authority for the vision-triggered, spatially-gated audio cue: it reads the shared
maze-state pipeline's FAST view, folds each frame through a CueGate, and turns the
resulting ON/OFF edges into CUE_ON,<kind> / CUE_OFF commands to the firmware, which
owns tone synthesis + the Open Ephys TONE_ON/TONE_OFF TTLs (ADR-0044/0045).

Threading (mirrors the Rules tab, ADR-0043): the pipeline's fast callback runs
inline on the tracking worker and only ENQUEUES the bundle; a dedicated cue thread
drains the queue and is the sole caller of CueGate.step + self._send. This keeps
the vision->tone path off the ~100 ms Tk poll (the tight-onset-budget goal). The
Tk poll only publishes an atomic gate snapshot (armed + firmware mode + tracking +
tunables) and renders status.

DAC authority handshake: while the gate is open the host owns the DAC, so on the
gate's rising edge we send ``CUE,VISION`` (firmware suppresses its own beam-driven
auto-tone in Full task) and on the falling edge we send ``CUE_OFF`` then
``CUE,BEAM`` (restore firmware autonomy). Only one entity ever drives the DAC.

Module contract (like the other tabs):
    widget() -> a Tk widget to place in its tab
    apply(line: str) -> None   handle each serial line (mode + CUE echo)
    close() -> None            stop the cue thread and silence the tone

Safety (this drives an actuator): OFF by default behind an explicit "Arm cue"
checkbox; the gate additionally requires the firmware to be in Full task and
tracking to be running. When the gate closes the tone is cut and firmware autonomy
is restored.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Optional

import protocol
from cue_gate import CHOICE, RETURN, CueGate, CueParams

try:
    from tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# Firmware MODE tokens (0,MODE,<name>) in which the vision cue may actuate. The
# cue is a Full-task feature; in Free Roam the Rules tab is the actuator instead.
FULLTASK_TOKENS = ("FULLTASK", "FULL", "TASK")

POLL_INTERVAL_MS = 100          # gate-snapshot publish + status refresh (Tk)
# A few seconds of frames at 30-60 fps; the per-item work is tiny so it never
# backs up. Overflow drops the newest item (counted) rather than blocking the
# tracking worker (mirrors the Rules actuation queue).
CUE_QUEUE_MAX = 512


@dataclass(frozen=True)
class _GateSnapshot:
    """Atomic, immutable Tk->thread hand-off (read whole by the cue thread).

    ``gate_open`` folds armed + firmware-mode + tracking readiness; ``params`` are
    the current gate tunables. The thread never touches Tk state.
    """
    gate_open: bool = False
    params: CueParams = field(default_factory=CueParams)


class CueControlView:
    """Tk module: the Cue tab - controls + host-side actuator for the vision cue."""

    def __init__(self, parent: tk.Misc, camera=None, pipeline=None, send=None):
        self._parent = parent
        self._camera = camera
        self._pipeline = pipeline
        self._send = send                 # callable(str) -> None (RigClient.send)
        self.frame = ttk.Frame(parent)
        self._mode = ""                   # last firmware MODE token
        self._closed = False

        # Gate state owned by the cue thread.
        self._gate = CueGate()
        self._gate_was_open = False
        self._last_fw_cue = ""            # last firmware CUE echo (for status)

        # --- cue actuation thread (ADR-0043 pattern) ----------------------
        self._cue_queue: "queue.Queue" = queue.Queue(maxsize=CUE_QUEUE_MAX)
        self._cue_stop = threading.Event()
        self._cue_thread: Optional[threading.Thread] = None
        self._drops = 0                   # frames dropped on a full queue (lag)
        self._on_count = 0                # cue onsets sent (thread-written)
        self._skip_count = 0              # returns skipped by the fail-safe
        self._gate_snapshot = _GateSnapshot()
        # Plain status mirror: the thread writes it; the Tk poll copies it into
        # the StringVar (the thread must not touch Tk).
        self._status_text = "cue: off"

        if pipeline is None:
            ttk.Label(self.frame, padding=24, anchor="center", justify="center",
                      text="Cue tab unavailable - the maze-state pipeline "
                           "(tracker) is not available.").pack(
                fill=tk.BOTH, expand=True)
            return

        self._build_ui()
        self._start_cue_thread()
        self._schedule_poll()

    # ------------------------------------------------------------ module API
    def widget(self) -> tk.Widget:
        return self.frame

    def apply(self, line: str) -> None:
        """Track the firmware mode (Tk) and reflect CUE echoes for status."""
        mode = protocol.mode_name(line)
        if mode is not None:
            self._mode = mode.upper()
            return
        info = protocol.cue_info(line)
        if info is not None:
            state, kind, freq = info
            if state == "ON":
                self._last_fw_cue = f"fw: {kind} @ {freq} Hz"
            else:
                self._last_fw_cue = "fw: off"

    def close(self) -> None:
        self._closed = True
        self._cue_stop.set()
        if self._pipeline is not None:
            try:
                self._pipeline.unsubscribe_fast(self._on_fast_state)
            except Exception:
                pass
        try:
            self._cue_queue.put_nowait(None)   # wake the loop so it can exit
        except queue.Full:
            pass
        if self._cue_thread is not None and self._cue_thread.is_alive():
            self._cue_thread.join(timeout=2.0)
        # Best-effort: make sure the tone is off and firmware autonomy restored.
        if self._gate_was_open:
            self._send_cmd("CUE_OFF")
            self._send_cmd("CUE,BEAM")

    # --------------------------------------------------------------- thread
    def _start_cue_thread(self) -> None:
        self._pipeline.subscribe_fast(self._on_fast_state)
        self._cue_thread = threading.Thread(
            target=self._cue_loop, name="cue-actuation", daemon=True)
        self._cue_thread.start()

    def _on_fast_state(self, bundle) -> None:
        """Pipeline fast-view callback - runs INLINE on the tracking worker.

        Must be cheap + non-blocking: just enqueue for the cue thread (ADR-0042)."""
        try:
            self._cue_queue.put_nowait(("frame", bundle))
        except queue.Full:
            self._drops += 1

    def _cue_loop(self) -> None:
        """The single cue thread: drain frames and be the sole caller of
        CueGate.step + self._send."""
        while not self._cue_stop.is_set():
            try:
                item = self._cue_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:                 # close() sentinel
                continue
            try:
                self._process_frame(item[1])
            except Exception:
                pass

    def _process_frame(self, bundle) -> None:
        snap = self._gate_snapshot           # atomic read of the immutable snapshot

        # Gate edges own the DAC-authority handshake + a clean gate reset so an
        # arming session starts silent (see module docstring).
        if snap.gate_open and not self._gate_was_open:
            self._gate.reset()
            self._send_cmd("CUE,VISION")     # host takes the DAC; suppress auto-tone
        elif not snap.gate_open and self._gate_was_open:
            self._send_cmd("CUE_OFF")        # cut any playing tone
            self._send_cmd("CUE,BEAM")       # restore firmware autonomy
            self._gate.reset()
        self._gate_was_open = snap.gate_open

        if not snap.gate_open:
            self._publish_status(snap)
            return

        self._gate.set_params(snap.params)
        st = bundle.state
        d = self._gate.step(st.zone, st.direction, st.confidence, st.valid)
        if d.started is not None:
            self._on_count += 1
            self._send_cmd(f"CUE_ON,{d.started}")
        elif d.stopped:
            self._send_cmd("CUE_OFF")
        if d.skipped_return:
            self._skip_count += 1
        self._publish_status(snap, d.desired)

    def _publish_status(self, snap: _GateSnapshot, desired: Optional[str] = None) -> None:
        if not snap.gate_open:
            self._status_text = f"gated ({self._gate_reason()})"
            return
        cue = desired if desired is not None else (self._gate.active or "-")
        extra = f"  drops:{self._drops}" if self._drops else ""
        self._status_text = (f"ARMED  cue:{cue}  onsets:{self._on_count}  "
                             f"skipped:{self._skip_count}  {self._last_fw_cue}{extra}")

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

    def _mode_is_fulltask(self) -> bool:
        return self._mode in FULLTASK_TOKENS

    def _gate_open(self) -> bool:
        return bool(self._enable_var.get() and self._tracking_running()
                    and self._mode_is_fulltask()
                    and self._pipeline is not None
                    and self._pipeline.zones_valid())

    def _gate_reason(self) -> str:
        if not self._enable_var.get():
            return "not armed"
        if self._pipeline is None or not self._pipeline.zones_valid():
            return "no maze zone map (zones.json)"
        if not self._tracking_running():
            return "tracking is not running"
        if not self._mode_is_fulltask():
            return f"firmware not in Full task (mode={self._mode or '?'})"
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

    # -------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 3}
        root = ttk.Frame(self.frame, padding=8)
        root.pack(fill=tk.BOTH, expand=True)

        self._enable_var = tk.BooleanVar(value=False)
        arm = ttk.Checkbutton(root, text="Arm cue (host-driven vision tone)",
                              variable=self._enable_var)
        arm.grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        tip(arm, "cue.arm")

        self._choice_var = tk.BooleanVar(value=True)
        c = ttk.Checkbutton(root, text="Choice cue (central stem, up-stem)",
                            variable=self._choice_var)
        c.grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        tip(c, "cue.choice")

        self._up_stem_var = tk.BooleanVar(value=True)
        u = ttk.Checkbutton(root, text="   require up-stem direction",
                            variable=self._up_stem_var)
        u.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        tip(u, "cue.up_stem")

        self._return_var = tk.BooleanVar(value=True)
        r = ttk.Checkbutton(root, text="Return cue (return arm, fail-safe)",
                            variable=self._return_var)
        r.grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        tip(r, "cue.return")

        ttk.Label(root, text="Min confidence:").grid(
            row=4, column=0, sticky="w", **pad)
        self._conf_var = tk.StringVar(value="0.0")
        conf = ttk.Spinbox(root, from_=0.0, to=1.0, increment=0.05, width=6,
                           textvariable=self._conf_var)
        conf.grid(row=4, column=1, sticky="w", **pad)
        tip(conf, "cue.min_confidence")

        ttk.Separator(root, orient="horizontal").grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self._status_var = tk.StringVar(value="cue: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=420, justify="left").grid(
            row=6, column=0, columnspan=2, sticky="w", **pad)

    # ------------------------------------------------------------- Tk poll
    def _schedule_poll(self) -> None:
        if self._closed:
            return
        try:
            self._publish_gate_snapshot()
            self._status_var.set(self._status_text)
        except tk.TclError:
            return
        try:
            self.frame.after(POLL_INTERVAL_MS, self._schedule_poll)
        except tk.TclError:
            return

    def _publish_gate_snapshot(self) -> None:
        """Tk poll: snapshot the gate + tunables the cue thread needs, so the
        thread never reads Tk state (ADR-0043)."""
        self._gate_snapshot = _GateSnapshot(
            gate_open=self._gate_open(),
            params=self._current_params())
