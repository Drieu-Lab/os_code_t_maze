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
import time
import tkinter as tk
from dataclasses import dataclass, field, replace
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Callable, Optional

from tmaze.core import protocol
from tmaze.core import cue_calibration
from tmaze.core.records import animals
from tmaze.core.timeutil import now_iso
from tmaze.core.cue_gate import CHOICE, RETURN, CueGate, CueParams
from tmaze.core.phase3.alternation import AlternationBlockConfig
from tmaze.core.phase3.debias import DebiasBlockConfig
from tmaze.core.phase3.phase3_config import (
    DEFAULT_MAX_TOPUPS, default_config, load_phase3_config, resolve_seed,
    save_phase3_config, to_session_cap_config, to_trial_config,
    validate_phase3_config)
from tmaze.core.phase3 import session_cap
from tmaze.core.phase3.session_cap import SessionCapConfig
from tmaze.core.phase3.trial_sequence import (
    GEN_BALANCED, GEN_DEBIAS_BLOCKS, GEN_INTERVENTION_BLOCKS)
from tmaze.core.phase3.excursion_log import (
    ExcursionLog, OUTCOME_DENIED, OUTCOME_SCORED, TYPE_NON_STEM, TYPE_UP_STEM)
from tmaze.core.phase3.phase3_log import Phase3Log, Phase3StateSnapshot
from tmaze.core.session.session_notes import (
    AUTO_ALT_END, AUTO_ALT_START, AUTO_BACK, AUTO_CAP_REACHED, AUTO_DEBIAS_END,
    AUTO_DEBIAS_START, AUTO_FIRMWARE_RESET, AUTO_PROBE_DISABLED,
    AUTO_PROBE_ENABLED, AUTO_PROBE_END, AUTO_PROBE_START, AUTO_REPEAT,
    AUTO_RESUME, AUTO_SEND_FAILED, AUTO_SKIP, AUTO_UNCUED)
from tmaze.core.phase3.phase3_task import (
    ABANDONED, CORRECT, INCORRECT, UNCUED, Phase3Config, Phase3Task)
from tmaze.core.reward.reward_dispenser import WellDispenser
from tmaze.core.reward.reward_volume import (
    format_volume, pulse_ms_for_volume, reward_volume_ul, round_pulse_ms)
from tmaze.core.rig_config import load_rig_config
from tmaze.paths import host_dir
from tmaze.ui.trial_ribbon_view import (
    _ARM_SHORT, _CELL_CURRENT_BG, _CUE_SHORT, _UNSET, TrialRibbonView,
    progress_signature)
from tmaze.core.phase3.trial_sequence import LEFT, RIGHT, TrialConfig, TrialSequence
from tmaze.io.vision_actuator import VisionActuator

try:
    from tmaze.core.task_events import TaskEvents
except Exception:  # pragma: no cover
    TaskEvents = None  # type: ignore

try:
    from tmaze.ui.tooltips import tip
except Exception:  # pragma: no cover
    def tip(*_a, **_k):  # type: ignore
        return None

# Free Roam is the firmware posture for host-owned Phase 3: the trial FSM is off,
# so the host is the sole task authority (see docs/decisions/0046). Same tokens
# the Rules tab actuates in.
FREEROAM_TOKENS = ("FREEROAM", "FREE")

POLL_INTERVAL_MS = 100          # snapshot publish + status refresh (Tk)
SEED_MASK = 0xFFFFFFFF          # normalise a pinned seed to a stable 32-bit range
                                # (the sequence PRNG accepts any int; see ADR-0140)

# Reward top-ups at the central well after the correct-choice prime (ADR-0069),
# delivered via the SAME shared WellDispenser the reward-shaping phases use
# (ADR-0068). The default top-up count is owned by phase3_config
# (DEFAULT_MAX_TOPUPS), the single source the config file + the tab start from.
# Within-visit lick spacing for a top-up (ms). 0 leans on the lick debounce + the
# shared dispatcher's global rate backstop (ADR-0054) to space fires; the debounce
# rejects electrical bounce / bursts on the lick line.
PHASE3_TOPUP_COOLDOWN_MS = 0
PHASE3_LICK_DEBOUNCE_MS = 150

# Versioned config file for the config-driven Phase 3 reward/cue/sequence params
# (FW-0024, ADR-0070); absent -> built-in defaults. Same host/ location + pattern
# as rules.json (docs/decisions/0031).
PHASE3_PATH = os.path.join(str(host_dir()), "phase3.json")

# Phase 3 writes its trial + excursion CSVs ONLY into a live session's task/
# folder (docs/decisions/0049); when armed WITHOUT a session it keeps the planned
# matrix in memory until Start binds a folder (Increment 4b, docs/decisions/0177)
# - so there is no host/data orphan and no such module-level fallback dir.

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
    """Host wall-clock stamp for a logged event (offset-aware, ms resolution;
    docs/decisions/0182)."""
    return now_iso()


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
    # Reward dispensing (ADR-0069): the correct-choice prime pulse width and the
    # number of lick-triggered top-ups allowed at the well after it. Applied on
    # arm (like the seed / reward_incorrect), so a mid-session edit takes effect on
    # the next arm.
    reward_pulse_ms: int = 500
    max_topups: int = DEFAULT_MAX_TOPUPS
    reward_port: int = 2
    # Where ``reward_pulse_ms`` came from (docs/decisions/0191): "config" (the
    # tab field / phase3.json), "animals.json:reward_ul" (an explicit per-animal
    # volume, which wins) or "animals.json:daily_water_ml" (derived from the
    # animal's daily target under the session cap). ``daily_water_ml`` is the
    # target itself (None when the animal has none) for the water-target readout,
    # whichever source sized the reward.
    reward_pulse_source: str = session_cap.SOURCE_CONFIG
    daily_water_ml: Optional[float] = None
    # Resolved schedule/tone config from phase3.json (FW-0024/ADR-0070); the
    # sequence is built from this on arm instead of the hardcoded default.
    trial_config: TrialConfig = field(default_factory=TrialConfig)
    # Where the resolved cue->side mapping came from (ADR-0186): "config"
    # (phase3.json) or "animals.json"; ``cue_mapping_overrides`` is True when a
    # per-animal value disagreed with phase3.json (surfaced to the operator).
    cue_mapping_source: str = "config"
    cue_mapping_overrides: bool = False
    animal: str = ""
    session_id: str = ""
    session_out_dir: str = ""
    # Canonical session anchor (manifest t0 = host wall+mono+iso at Start Session),
    # handed down from the Session controller so the Phase 3 header can self-anchor
    # to the one instant every stream shares (docs/decisions/0124). None before
    # Start Session / on a manual arm.
    session_t0: Optional[dict] = None
    # Operator "pause cues" toggle (Rig-view button; docs/decisions/0058). When
    # set, the actuation thread silences any tone and freezes trial progress
    # without disarming (the gate stays open).
    paused: bool = False
    # Session cap (docs/decisions/0190): N scored trials or M minutes since
    # Session Start, whichever first, then a soft stop. Resolved from phase3.json
    # on the Tk poll like the other tunables.
    session_cap: SessionCapConfig = field(default_factory=SessionCapConfig)
    # How many times the operator has unticked the Arm checkbox (counted on the Tk
    # side by a variable trace, so an untick while tracking is off and no frames
    # flow is still seen). A gate rising edge whose count differs from the one the
    # run was armed/resumed with is an operator re-arm, not a flicker
    # (docs/decisions/0210).
    disarm_count: int = 0
    # The cap's anchor (docs/decisions/0198): the manifest t0 captured at the
    # FIRST Start after an arm and PRESERVED across a stop->restart of the
    # recording, so an operationally-innocuous restart (e.g. a lossless->H.264
    # switch) doesn't reset the trial/time budget. Distinct from ``session_t0``
    # (which is per-recording); None before the first Start / on a manual arm.
    cap_t0: Optional[dict] = None


class Phase3ControlView(VisionActuator):
    """Tk module: the Phase 3 tab - host-owned cued task, firmware as actuator.

    Mixes in ``VisionActuator`` for the shared ADR-0043 skeleton (fast-view
    subscription, bounded queue, dedicated actuation thread, atomic snapshot);
    this class adds the cue-gate / task policy, the DAC-authority handshake, and
    the per-session data log."""

    def __init__(self, parent: tk.Misc, camera=None, pipeline=None, send=None,
                 dispatcher=None,
                 animal_id: Optional[Callable[[], str]] = None,
                 animal_registry=None):
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
        # Canonical animal registry, for the per-animal cue->side mapping (schema
        # v3, ADR-0186). Loaded lazily+cached on first resolve (best-effort: an
        # absent resources repo yields an empty registry, so the rig falls back to
        # phase3.json). A test may inject one; None means "load the real one".
        self._animal_registry = animal_registry
        # Auto-event sink (docs/decisions/0111): a thread-safe callable(event: dict)
        # the Session controller attaches (set_intervention_sink) to receive an
        # operator-intervention record whenever a trial-control override fires on
        # this actuation thread (debias / de-alternation edges, Back / Skip /
        # Repeat). Enqueue-only on the callee's side; None when unattached (a manual
        # arm, or a standalone tab) -> the events are simply not recorded.
        self._on_intervention: Optional[Callable[[dict], None]] = None
        self.frame = ttk.Frame(parent)
        self._mode = ""                   # last firmware MODE token
        # Last firmware session-clock ms seen on ANY line, tracked on the serial-
        # reader thread to detect a mid-session Due reboot: a fresh PROTO handshake
        # after we've seen a running clock (>0) is a firmware reset (S8,
        # docs/decisions/0184). None until the first timed line arrives.
        self._last_fw_ms: Optional[int] = None
        self._closed = False

        # State owned by the actuation thread.
        self._gate = CueGate()
        self._task: Optional[Phase3Task] = None
        self._seq: Optional[TrialSequence] = None
        self._task_events = TaskEvents() if TaskEvents is not None else None
        self._gate_was_open = False
        self._last_fw_cue = ""            # last firmware CUE echo (for status)
        # Trial index awaiting a firmware-clock echo (docs/decisions/0165): set on
        # the actuation thread when the CHOICE cue / prime reward is COMMANDED, then
        # bound to the trial's *_fw_ms cell when the firmware CUE,ON / SOL,REWARD
        # echo comes back (routed from the serial thread via _enqueue_act). First
        # echo wins, then cleared. Actuation-thread state; init here so an echo that
        # arrives before a session is armed is a harmless no-op.
        self._pending_cue_ack_index: Optional[int] = None
        self._pending_return_cue_ack_index: Optional[int] = None
        self._pending_reward_ack_index: Optional[int] = None
        # Session data log (plan matrix + live outcomes; ADR-0047), and the state
        # needed to fill it: which cue is sounding (to route the CUE_OFF edge) and
        # the file path. All touched only by the actuation thread. The trial-start
        # edge is no longer tracked here - it reads task_events.departed_hub_via_stem
        # (FW-0027), so the HUB->stem departure is computed once, in the pure core.
        self._log: Optional[Phase3Log] = None
        self._log_path: Optional[str] = None
        # Timestamped per-excursion event log (ADR-0109): one row per outbound trip
        # (stem + non-stem), written beside the trial CSV in the session task/
        # folder. Same actuation-thread-only ownership + atomic write as _log.
        self._excursion_log: Optional[ExcursionLog] = None
        self._excursion_path: Optional[str] = None
        # The choice-cue-on stamp of the CURRENT outbound trip (set when the choice
        # cue sounds, a prior frame), so a scored stem excursion can carry the
        # relative cue-zone dwell (choice_time - cue_on; ADR-0108). Per-trip: it is
        # cleared on each (re)arm / silence-and-rearm.
        self._choice_cue_on_iso: Optional[str] = None
        # Shared session id when armed via the Session controller (docs/decisions/
        # 0048); "" on a manual arm. Set on the Tk thread, snapshotted to the thread.
        self._session_id = ""
        # Session output dir (the session's task/ folder; docs/decisions/0049); ""
        # -> the default host/data/ location for a manual arm.
        self._session_out_dir = ""
        # Canonical session anchor (manifest t0) once Start Session hands it down
        # via set_session_context; stamped into the log header on rebind (ADR-0124).
        self._session_t0: Optional[dict] = None
        self._active_cue: Optional[str] = None
        # Operator controls (Rig-view buttons; docs/decisions/0058). ``_paused`` is
        # a plain Tk-thread bool (button + poll both run on the Tk thread) folded
        # into the atomic snapshot for the actuation thread. ``_restart_count``
        # tags each restart's data file so a restart never clobbers the prior run.
        self._paused = False
        self._restart_count = 0
        # Session cap state (docs/decisions/0190). ``_cap_reached`` is None while
        # running; once a limit is met it holds ``{reason, iso, trial_index,
        # completed, elapsed_s}`` and the actuation thread behaves as if paused
        # (no cue, no reward) until a fresh arm / Restart clears it - it
        # deliberately SURVIVES Stop, so a capped rat still in the maze is not cued
        # again by the still-armed task. ``_cap_base_done`` is the scored count when
        # the cap anchor was taken, so the trial limit counts from the FIRST Start
        # (like the clock), not from arm. The anchor (base + ``_cap_t0``) is taken
        # ONCE at that first Start and PRESERVED across a stop->restart of the
        # recording (docs/decisions/0198), so a codec-driven restart doesn't reset
        # the budget; ``_cap_anchored`` guards it and a fresh arm / Restart clears
        # it. Written on the actuation thread (set_session_context publishes before
        # the rebind), read by the Tk status poll.
        self._cap_reached: Optional[dict] = None
        # What the trial limit counts: NORMAL scored trials only (correct or
        # incorrect, ``not dec.probe``). The whole unrewarded probe block (trigger +
        # forced trials) runs ON TOP of the limit, so the rat always gets
        # ``max_trials`` chances at reward (docs/decisions/0205).
        self._cap_scored = 0
        self._cap_base_done = 0
        self._cap_anchored = False
        self._cap_t0: Optional[dict] = None
        # How many times a mid-session gate REOPEN was resumed rather than treated
        # as a fresh arm (docs/decisions/0120). Surfaced in the status line so the
        # operator knows a tracking flicker did not restart / overwrite the run;
        # reset on every real (re)arm.
        self._reopen_resumes = 0
        # Operator re-arm detection (docs/decisions/0210). ``_disarm_count`` is
        # Tk-written (the Arm checkbox trace); the other three are actuation-thread
        # state: the count and the plan settings the live run was armed (or last
        # resumed) with, and whether a resume left changed settings unapplied.
        self._disarm_count = 0
        self._armed_disarm_count = 0
        self._armed_settings: Optional[tuple] = None
        self._unapplied_settings = False
        # The center-well lick channel (reward-collection lick), from the shared
        # rig-config pin map (ADR-0021/0022); None if unavailable -> no lick times.
        self._center_lick = load_rig_config().lick_index_by_role.get("center")
        # Shared reward dispenser (ADR-0068): owns the correct-choice prime + the
        # lick top-up mechanics at the central well, driven with prime_on=
        # "credential" so the pulse fires the instant a choice is scored (out at
        # the choice arm, as today) and licks at the well top up. Built on arm from
        # the snapshot's reward knobs; None until then. Its FireReward intents are
        # routed through the shared dispatcher (rate backstop + volume record).
        self._well: Optional[WellDispenser] = None
        # The trial the current prime was logged against, so a later top-up lick
        # (which lands after the trial has advanced) is attributed to the right row.
        self._reward_trial_index: Optional[int] = None
        # Live tallies (thread-written, Tk-read for status).
        self._correct = 0
        self._incorrect = 0
        # UNCUED trips this run (a choice committed with no cue that trip;
        # docs/decisions/0171) - shown in the status line so the operator sees the
        # rig failing to cue. The authoritative cumulative count is the log
        # snapshot (live + archived); this mirrors it for the live readout.
        self._uncued = 0
        # Cue commands that did NOT reach the wire this run (docs/decisions/0173):
        # the send reported failure, so no cue onset was logged and (for CHOICE) the
        # trip scores UNCUED. Shown in the status line so a dropped cue is visible.
        self._send_fails = 0
        # Mid-session firmware reboots detected + survived this run (S8,
        # docs/decisions/0184); shown in the status line, counted from a real arm.
        self._fw_resets = 0
        # Unrewarded probe-trial tallies (probe_blocks.py): scored probe trials and
        # of those how many were correct, for the conditional status segment.
        self._probe_done = 0
        self._probe_correct = 0
        self._reward_count = 0
        self._reward_ul = 0.0            # session dispensed volume (prime + top-ups)
        self._active_seed = 0
        # Latest immutable table snapshot for cross-tab readers (the stats panel +
        # trial ribbon; docs/decisions/0050). Published by the actuation thread via
        # atomic reference assignment, read by published_state(); no lock needed
        # (mirrors the _snapshot Tk<->thread hand-off, ADR-0043). None until armed.
        self._state: Optional[Phase3StateSnapshot] = None

        # Config-driven reward/cue/sequence params from host/phase3.json (FW-0024,
        # ADR-0070): loaded once here to seed the tab, re-loadable via the Reload
        # button, and persisted (reward + cue) by the Save button. On a malformed
        # file we fall back to built-in defaults and surface the error in status.
        self._config_path = PHASE3_PATH
        self._config = default_config()
        self._config_error = ""
        # Last live-validation error of the GUI debias fields (invalid edits fall
        # back to the last valid config; surfaced in the sequence readout).
        self._debias_error = ""
        # Disk-write failures we could not persist (slot -> failing file name). A
        # failed atomic write of the trial CSV or the excursion log means data is
        # NOT on disk; rather than swallow it (the old silent `except OSError:
        # pass`), record it per-file and surface it loudly in the status line so
        # the operator notices, and clear a slot once its next write succeeds
        # (docs/decisions/0121). Actuation-thread-only, like the log itself.
        self._write_errors: dict = {}
        self._load_config()

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

    def set_intervention_sink(
            self, cb: Optional[Callable[[dict], None]]) -> None:
        """Attach (or clear) the auto-event sink the Session controller drains
        (docs/decisions/0111). ``cb`` MUST be thread-safe / enqueue-only: it is
        called on this tab's actuation thread. Set on the Tk thread before a run,
        mirroring the other injected seams (animal_id/dispatcher)."""
        self._on_intervention = cb

    def apply(self, info) -> None:
        """Track the firmware mode (Tk) and reflect CUE echoes for status.

        Reads the pre-parsed protocol.LineInfo (docs/decisions/0056). Also snoop
        center-well lick edges (the reward-collection lick) and hand them to the
        actuation thread, which is the sole writer of the data log (this method
        runs on the serial-reader thread; ADR-0043/0047)."""
        # A fresh PROTO handshake after we've already seen a running firmware clock
        # means the Due rebooted mid-session (S8, docs/decisions/0184). Hand it to
        # the actuation thread (sole actuator) to re-assert CUE,VISION + log it;
        # carry the last ms seen before the reset. Guard on _last_fw_ms > 0 so the
        # INITIAL startup handshake (clock still 0) is not mistaken for a reset.
        if protocol.is_boot_handshake(info.raw):
            if self._last_fw_ms is not None and self._last_fw_ms > 0:
                self._enqueue_act(("firmware_reset", self._last_fw_ms, _now_iso()))
            self._last_fw_ms = 0
            return
        if info.parsed is not None:
            self._last_fw_ms = info.parsed.ms
        if info.mode is not None:
            self._mode = info.mode.upper()
            return
        if info.cue is not None:
            state, kind, freq = info.cue
            self._last_fw_cue = f"fw: {kind} @ {freq} Hz" if state == "ON" else "fw: off"
            # The firmware's cue-on echo carries the cue onset on the firmware
            # clock; route its ms to the actuation thread (sole log writer) to
            # stamp the trial's choice_cue_on_fw_ms (docs/decisions/0165) or
            # return_cue_on_fw_ms (docs/decisions/0166).
            if state == "ON" and info.parsed is not None:
                if kind == CHOICE:
                    self._enqueue_act(("cue_ack", info.parsed.ms))
                elif kind == RETURN:
                    self._enqueue_act(("return_cue_ack", info.parsed.ms))
            return
        # A solenoid / reward echo carries the reward pulse on the firmware clock
        # (docs/decisions/0165); route its ms to stamp the trial's reward_cmd_fw_ms.
        if (info.reward is not None or info.sol is not None) \
                and info.parsed is not None:
            self._enqueue_act(("reward_ack", info.parsed.ms))
            return
        lick = info.lick
        if lick is not None and self._center_lick is not None:
            index, rising = lick
            if rising and index == self._center_lick:
                fw_ms = info.parsed.ms if info.parsed is not None else None
                # Carry a monotonic stamp (same clock as the fast-view frames,
                # ADR-0069) so the dispenser's lick freshness/top-up timing lines
                # up with visit_tick; the ISO string stays for the data log.
                self._enqueue_act(("lick", _now_iso(), fw_ms, time.monotonic()))

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

    def set_session_context(self, session_id: str = "", out_dir: str = "",
                            session_t0: Optional[dict] = None) -> None:
        """Record the coordinated session's id + output folder WITHOUT arming.

        The Session controller calls this on Start once it has confirmed Phase 3 is
        already armed (docs/decisions/0060), and again with no arguments on Stop to
        RELEASE the context. The ``session_id`` is threaded into the data-file name
        + header so it correlates with the video + manifest, and ``out_dir`` is the
        session's ``task/`` folder the data file is written into (docs/decisions/
        0049). ``session_t0`` is the manifest anchor (host wall+mono+iso at Start
        Session) stamped into the header on rebind so the file self-anchors to the
        one shared instant (docs/decisions/0124). Unlike the old ``arm()`` this does
        NOT flip the Arm checkbox.

        Because the gate is already open by now, no rising edge will re-run
        ``_arm_session``, so this also asks the actuation thread to re-point an
        already-open data file at the new session (docs/decisions/0066). No-op if
        the tab is unavailable (no pipeline)."""
        if not hasattr(self, "_enable_var"):
            return
        self._session_id = session_id or ""
        self._session_out_dir = out_dir or ""
        self._session_t0 = session_t0
        if session_id:
            # The cap anchor (trial base + clock t0) is taken ONCE, at the FIRST
            # Start after an arm, and PRESERVED across a stop->restart of the
            # recording so an operationally-innocuous restart (e.g. the lossless->
            # H.264 switch that motivated this) doesn't reset the 120-trial / 45-min
            # budget - the rat is counted continuously from the first Start
            # (docs/decisions/0198, extends 0190). A fresh arm / Restart clears
            # ``_cap_anchored`` so the next run re-anchors. Trials run between arm
            # and the first Start don't count (the base is the scored count now).
            if not self._cap_anchored:
                self._cap_base_done = self._cap_scored
                self._cap_t0 = session_t0
                self._cap_anchored = True
                self._cap_reached = None
        else:
            # Stop released the context (docs/decisions/0060): the task stays armed
            # and MUTED (``_cap_reached`` deliberately persists so a rat left in the
            # maze is not re-cued, ADR-0190), but the Rig-view "remove rat, Stop
            # session" banner is now stale. The actuation thread will NOT clear it
            # on its own - a capped task is frozen (no rows change, so no republish)
            # and the rebind below no-ops on an empty session_id - so drop the cap
            # field from the already-published readers' snapshot right here. The
            # session_id is cleared above, so any later republish also drops it; a
            # frozen dataclasses.replace keeps every other field of the final table
            # (docs/decisions/0192).
            st = self._state
            if st is not None and st.cap_reached is not None:
                self._state = replace(st, cap_reached=None)
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
        self._session_t0 = None
        # A direct disarm ends the run: drop the cap anchor so the next arm+Start
        # re-anchors a fresh budget (docs/decisions/0198).
        self._cap_anchored = False
        self._cap_t0 = None
        self._cap_reached = None

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

    def cap_reached(self) -> Optional[dict]:
        """The session-cap hit for the current run (docs/decisions/0190) as
        ``{reason, iso, trial_index, completed, elapsed_s}``, or None while the
        cap has not fired. Read on the Tk thread by the Session controller at Stop
        so the summary records WHICH limit ended the session."""
        hit = self._cap_reached
        return dict(hit) if hit else None

    # The debias corrective-block override (Rig-view / control-bar button). These
    # only do anything under the "debias_blocks" generator; they enqueue a command
    # the actuation thread applies (it owns the task), taking effect on the NEXT
    # committed trial (the current trial's side is already fixed). See debias.py.
    def start_debias_block(self) -> None:
        """Operator: force a corrective block to start (side inferred from bias)."""
        self._enqueue_act(("cmd", "debias_start"))

    def end_debias_block(self) -> None:
        """Operator: end the current corrective block early."""
        self._enqueue_act(("cmd", "debias_end"))

    def toggle_debias_block(self) -> None:
        """Flip between a side-bias corrective block and normal trials, based on the
        currently published scheduler state. Under "intervention_blocks" a block of
        a DIFFERENT kind (alternation) is left to its own toggle: end here only when
        a side-bias block is the one running."""
        st = self._state
        active = getattr(st, "active_kind", "") if st is not None else ""
        if (st is not None and st.debias_state == "INTERVENTION"
                and active in ("side_bias", "")):
            self.end_debias_block()
        else:
            self.start_debias_block()

    # The de-alternation corrective-block override (second Rig-view button). Only
    # meaningful under the "intervention_blocks" generator with the alternation
    # child enabled; enqueued like the debias override (applied on the next
    # committed trial). See alternation.py / intervention_stack.py.
    def start_alternation_block(self) -> None:
        """Operator: force a de-alternation block to start (forces the last choice)."""
        self._enqueue_act(("cmd", "alt_start"))

    def end_alternation_block(self) -> None:
        """Operator: end the current de-alternation block early."""
        self._enqueue_act(("cmd", "alt_end"))

    def toggle_alternation_block(self) -> None:
        """Flip a de-alternation block on/off from the published scheduler state."""
        st = self._state
        active = getattr(st, "active_kind", "") if st is not None else ""
        if active == "alternation":
            self.end_alternation_block()
        else:
            self.start_alternation_block()

    def debias_active(self) -> bool:
        """Whether a closed-loop generator is in effect this session (so the
        control bar can enable/disable its toggle)."""
        st = self._state
        return st is not None and st.generator != "balanced"

    # The unrewarded probe-block controls (Rig-view / control-bar button). There is
    # NO manual start: a probe fires automatically on the correct-choice window
    # trigger (probe_blocks.py / docs/decisions/0134). The operator can DISABLE
    # probes mid-session so no window fires (docs/decisions/0137) and re-enable them;
    # a disable also aborts any running block. This works under ANY generator (probes
    # are orthogonal to the closed-loop stack). Enqueued like the other overrides and
    # applied on the actuation thread.
    def end_probe_block(self) -> None:
        """Operator: end a running probe block early (abort)."""
        self._enqueue_act(("cmd", "probe_end"))

    def toggle_probe_armed(self) -> None:
        """Operator: flip probes on<->off mid-session (docs/decisions/0137). The
        control-bar button is a persistent On/Off toggle live whenever probes were
        built for the run. The flip is resolved on the actuation thread from the
        controller's current armed state, so it stays correct regardless of the
        published snapshot's age."""
        self._enqueue_act(("cmd", "probe_toggle"))

    # --------------------------------------------------------------- thread
    def _process_act_item(self, item, snap) -> None:
        """Sole caller of CueGate.step / TaskEvents.update / Phase3Task.update /
        self._send (ADR-0043). Called by the shared VisionActuator loop with the
        atomic snapshot; splits frames from routed center-well lick edges."""
        if item[0] == "lick":
            self._process_lick(item[1], item[2], item[3])
        elif item[0] == "cmd":
            self._process_command(item[1], snap)
        elif item[0] == "cue_ack":
            self._process_cue_ack(item[1])
        elif item[0] == "return_cue_ack":
            self._process_return_cue_ack(item[1])
        elif item[0] == "reward_ack":
            self._process_reward_ack(item[1])
        elif item[0] == "firmware_reset":
            self._process_firmware_reset(item[1], item[2])
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
        # Sequence + tones come from phase3.json (resolved into the snapshot;
        # FW-0024/ADR-0070) instead of the hardcoded default.
        self._seq = TrialSequence(snap.trial_config, seed=snap.seed)
        cfg = Phase3Config(reward_incorrect=snap.reward_incorrect,
                           reward_port=snap.reward_port,
                           reward_pulse_ms=snap.reward_pulse_ms)
        self._task = Phase3Task(self._seq, cfg)
        # Fresh dispenser for this run: prime fires at the scored choice
        # (prime_on="credential") and licks at the central well top up, up to
        # snap.max_topups (ADR-0068/0069). The port is the central reward port,
        # which is also the center-well lick index (ADR-0022), so on_lick matches.
        self._well = WellDispenser(
            {"port": cfg.reward_port, "pulse_ms": cfg.reward_pulse_ms,
             "max_topups": snap.max_topups,
             "lick_debounce_ms": PHASE3_LICK_DEBOUNCE_MS, "prime_on": "credential"},
            default_topup_cooldown_ms=PHASE3_TOPUP_COOLDOWN_MS, rule_id="phase3")
        self._reward_trial_index = None
        self._active_seed = snap.seed
        self._armed_disarm_count = snap.disarm_count
        self._armed_settings = self._plan_settings(snap)
        self._unapplied_settings = False
        self._correct = self._incorrect = self._reward_count = 0
        self._uncued = 0
        self._send_fails = 0
        self._probe_done = self._probe_correct = 0
        self._reopen_resumes = 0             # a fresh run: no reopens yet
        self._fw_resets = 0                  # ...nor firmware reboots (ADR-0184)
        self._reward_ul = 0.0
        self._active_cue = None
        self._pending_cue_ack_index = None
        self._pending_return_cue_ack_index = None
        self._pending_reward_ack_index = None
        # A fresh run starts uncapped; the cap anchor (trial base + clock) is taken
        # once at the first Session Start and preserved across a recording restart
        # (docs/decisions/0198), so re-anchor it here on a genuine (re)arm.
        self._cap_reached = None
        self._cap_scored = 0
        self._cap_base_done = 0
        self._cap_anchored = False
        self._cap_t0 = None
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
                              reward_pulse_ms=snap.reward_pulse_ms,
                              max_topups=snap.max_topups,
                              session_id=snap.session_id,
                              session_t0=snap.session_t0,
                              return_enabled=snap.params.return_enabled,
                              cue_calibration=self._resolve_cue_calibration(),
                              high_tone_is_left_source=snap.cue_mapping_source,
                              session_cap=snap.session_cap,
                              reward_pulse_source=snap.reward_pulse_source,
                              daily_water_ml=snap.daily_water_ml)
        # Bind + write into the session's task/ folder ONLY when armed via a
        # session (docs/decisions/0049). When armed WITHOUT one (the ADR-0060
        # arm-before-Start order, or a standalone tab), keep the planned matrix IN
        # MEMORY: _log_path stays None so _write_log is a no-op and no host/data
        # orphan is created. The Start rebind (_rebind_session_output) binds the
        # real path and does the first write, which renders the WHOLE table - so
        # any trials run between arm and Start are preserved (docs/decisions/0066,
        # 0177 Increment 4b).
        if snap.session_out_dir:
            self._log_path = os.path.join(snap.session_out_dir, f"{base}.csv")
            self._write_log()
        else:
            self._log_path = None            # in memory until a session exists
        # Fresh excursion-event log alongside the trial CSV (ADR-0109), named in
        # lockstep with the trial file (so a restart's _r<n> tag carries over).
        self._excursion_log = ExcursionLog(
            animal=animal, session_id=snap.session_id, session_start_iso=_now_iso(),
            session_t0=snap.session_t0)
        self._excursion_path = self._excursion_path_for(self._log_path)
        self._choice_cue_on_iso = None
        if self._excursion_path is not None:
            self._write_excursion_log()
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
        if name in ("debias_start", "debias_end", "alt_start", "alt_end"):
            # Operator override of the closed-loop scheduler (debias.py /
            # alternation.py). Latched on the task, applied on the next committed
            # trial; publish so the panel/control-bar reflect the request promptly.
            if name == "debias_start":
                self._task.force_debias_block(None)
                self._emit_intervention(AUTO_DEBIAS_START,
                                        "debias block started", idx, iso)
            elif name == "debias_end":
                self._task.end_debias_block()
                self._emit_intervention(AUTO_DEBIAS_END,
                                        "debias block ended", idx, iso)
            elif name == "alt_start":
                self._task.force_alternation_block()
                self._emit_intervention(AUTO_ALT_START,
                                        "de-alternation block started", idx, iso)
            else:
                self._task.end_alternation_block()
                self._emit_intervention(AUTO_ALT_END,
                                        "de-alternation block ended", idx, iso)
            self._publish_state(armed=snap.gate_open)
            return
        if name == "probe_end":
            # Operator aborts a running unrewarded probe block (probe_blocks.py).
            # There is no manual start (the trigger is automatic - docs/decisions/
            # 0134); publish so the control bar/readout drop the PROBE tag promptly.
            self._task.end_probe_block()
            self._emit_intervention(AUTO_PROBE_END, "probe block aborted", idx, iso)
            self._publish_state(armed=snap.gate_open)
            return
        if name == "probe_toggle":
            # Operator flips probes on<->off mid-session (docs/decisions/0137):
            # DISABLE stops any further correct-choice window from firing AND aborts a
            # running block; ENABLE re-arms the remaining windows. Resolve the flip
            # from the CURRENT armed state (not the possibly-stale snapshot), record
            # it on the timeline (so it is recoverable from the saved notes/summary -
            # the archived config can't show a mid-session disable), then publish so
            # the toggle label / [PROBE OFF] tag update promptly.
            was_armed = (bool(getattr(self._seq, "probe_armed", False))
                         if self._seq is not None else False)
            self._task.set_probe_armed(not was_armed)
            if was_armed:
                self._emit_intervention(
                    AUTO_PROBE_DISABLED,
                    "probe blocks disabled (no window will fire)", idx, iso)
            else:
                self._emit_intervention(
                    AUTO_PROBE_ENABLED, "probe blocks re-enabled", idx, iso)
            self._publish_state(armed=snap.gate_open)
            return
        if name == "skip":
            self._log_call(lambda: self._log.trial_abandoned(idx, iso))
            self._task.abandon_current()
            self._emit_intervention(AUTO_SKIP, "trial skipped (abandoned)",
                                    idx, iso)
        elif name == "repeat":
            self._supersede_trial(idx, "repeat", AUTO_REPEAT,
                                  "trial repeated", iso)
            self._task.repeat_current()
        elif name == "back":
            new_idx = self._task.step_back()
            self._supersede_trial(new_idx, "back", AUTO_BACK,
                                  "stepped back a trial", iso)
        else:
            return
        self._silence_and_rearm()
        self._write_log()
        self._publish_state(armed=snap.gate_open)

    def _emit_intervention(self, tag: str, text: str, trial_index: int,
                           iso: str, *, extra: Optional[dict] = None) -> None:
        """Hand one operator-intervention event to the attached sink (the Session
        controller's note-log drain; docs/decisions/0111). Runs on the actuation
        thread, so it only builds the event + calls the enqueue-only sink; a
        missing sink (manual arm / standalone) or a raising sink is swallowed so a
        note never disturbs the task. ``extra`` merges in event-specific fields the
        sink may act on beyond the note (e.g. firmware_reset's ``last_fw_ms`` that
        the Session controller records in the manifest; docs/decisions/0184)."""
        cb = self._on_intervention
        if cb is None:
            return
        event = {
            "tag": tag,
            "text": text,
            "iso": iso,
            "mono": time.monotonic(),
            "trial_index": trial_index,
        }
        if extra:
            event.update(extra)
        try:
            cb(event)
        except Exception:
            pass

    def _note_send_failed(self, kind: str, trial_index: int, iso: str) -> None:
        """Record that a ``kind`` ("CHOICE"/"RETURN") cue command did not reach the
        wire (docs/decisions/0173): bump the run counter for the status line and
        emit an ``auto:send_failed`` timeline note, so a dropped cue is visible and
        distinguishable from the animal simply not being cued by position."""
        self._send_fails += 1
        self._emit_intervention(
            AUTO_SEND_FAILED,
            f"{kind} cue send failed (not on the wire) - no cue this trip",
            trial_index, iso)

    def _supersede_trial(self, index: int, edit: str, tag: str, text: str,
                         iso: str) -> None:
        """Archive-not-erase a trial edit (Repeat / Back): preserve the animal's
        real attempt (docs/decisions/0119) and reconcile the live tallies.

        The pure log keeps the real attempt as a superseded row and opens a fresh
        one; here we un-count only the ACCURACY tallies for a preserved scored
        attempt (correct/incorrect + probe), never the reward count/volume - the
        prime + top-ups were physically dispensed and the rat drank them, so they
        stay in the session's water total. In a closed-loop run the scheduler has
        already consumed the outcome and has no single-step undo (FW-0044), so we
        flag on the operator note that re-running a scored trial may perturb the
        auto-schedule."""
        result = self._log.supersede_trial(index, edit) if self._log else None
        if result is not None:
            if result.outcome == CORRECT:
                self._correct = max(0, self._correct - 1)
            elif result.outcome == INCORRECT:
                self._incorrect = max(0, self._incorrect - 1)
            if not result.probe_block and result.outcome in (CORRECT, INCORRECT):
                self._cap_scored = max(0, self._cap_scored - 1)
            if result.probe and result.outcome in (CORRECT, INCORRECT):
                self._probe_done = max(0, self._probe_done - 1)
                if result.outcome == CORRECT:
                    self._probe_correct = max(0, self._probe_correct - 1)
            if (self._seq is not None and getattr(self._seq, "is_debias", False)
                    and result.outcome in (CORRECT, INCORRECT)):
                text += " - auto-schedule may be affected (scored trial re-run)"
        self._emit_intervention(tag, text, index, iso)

    def _silence_and_rearm(self) -> None:
        """Cut any sounding tone and reset the per-trip cue/gate state, so the next
        trial's cue waits for a fresh up-the-stem excursion from the hub."""
        self._send_cmd("CUE_OFF")
        self._active_cue = None
        self._choice_cue_on_iso = None
        self._pending_cue_ack_index = None
        self._pending_return_cue_ack_index = None
        self._gate.reset()
        # Drop any pending credential prime + open well visit, so an interrupted
        # trial can't carry a stale top-up window into the next one (ADR-0069).
        if self._well is not None:
            self._well.reset()
        self._reward_trial_index = None
        self._pending_reward_ack_index = None

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
            # Path already correct (unusual in the ADR-0060 flow). Still stamp the
            # canonical anchor if it only just arrived, then re-render (ADR-0124).
            if snap.session_t0 and self._log.session_t0 != snap.session_t0:
                self._log.session_t0 = snap.session_t0
                self._write_log()
            return
        self._restart_count = 0
        self._log.session_id = snap.session_id   # the header's correlation id
        # Stamp the canonical session anchor now that Start Session has captured it,
        # so the header self-anchors to the one shared instant (docs/decisions/0124).
        if snap.session_t0:
            self._log.session_t0 = snap.session_t0
        self._log_path = new_path
        self._write_log()
        # Re-point the excursion log at the session that now owns it too, keeping
        # any rows appended between arm and Start (ADR-0109; mirrors _log rebind).
        if self._excursion_log is not None:
            self._excursion_log.session_id = snap.session_id
            self._excursion_path = self._excursion_path_for(new_path)
            self._write_excursion_log()
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

    def _is_session_reopen(self, snap) -> bool:
        """Whether a gate RISING edge is a mid-session reopen (tracking toggled or
        a brief firmware mode change) rather than the first arm of a run.

        True when a session is already armed AND the snapshot's session context
        matches the one the live log was armed with, so the SAME session is still
        active (docs/decisions/0120). A different (or absent) ``session_id`` -
        e.g. a new coordinated session, or a re-arm after Stop cleared the context
        - is a genuinely new run and re-arms.

        An operator untick/retick of the Arm checkbox (``disarm_count`` moved since
        the run was armed/resumed) re-arms fresh when NOT recording, so a new seed
        or changed settings take effect; during a recording it still resumes, to
        protect the session's data file (docs/decisions/0210)."""
        if self._log is None or snap.session_id != self._log.session_id:
            return False
        operator_rearm = snap.disarm_count != self._armed_disarm_count
        return not (operator_rearm and not snap.session_id)

    @staticmethod
    def _plan_settings(snap: _Phase3Snapshot) -> tuple:
        """The settings a run applies only at arm (sequence + reward), compared on a
        resume to tell the operator when an edit was not applied (ADR-0210)."""
        return (snap.seed, snap.trial_config, snap.reward_incorrect,
                snap.reward_pulse_ms, snap.max_topups)

    def _resume_after_reopen(self, snap) -> None:
        """Resume the SAME session after a gate reopen instead of re-arming it
        (FW-0026, docs/decisions/0120): keep the task, sequence, trial index, and
        data file exactly as they were - only reclaim DAC authority and clear stale
        per-trip cue/gate/well state so no leftover tone sounds and the next trial
        waits for a fresh hub->stem run. Surfaced (status line + an ``auto:resume``
        note) so the operator knows the flicker was not treated as a new run."""
        if self._task_events is not None:
            self._task_events.reset()
        self._silence_and_rearm()            # CUE_OFF + per-trip cue/gate/well reset
        self._send_cmd("CUE,VISION")         # host re-owns the DAC (suppress auto-tone)
        self._reopen_resumes += 1
        self._armed_disarm_count = snap.disarm_count
        idx = self._task.index if self._task is not None else 0
        text = "tracking reopened mid-session - resumed (data preserved)"
        if self._plan_settings(snap) != self._armed_settings:
            # Seed / sequence / reward edits only apply at arm (ADR-0210).
            self._unapplied_settings = True
            text += ("; seed/sequence/reward settings changed while disarmed were "
                     "NOT applied - use Restart to apply them")
        self._emit_intervention(AUTO_RESUME, text, idx, _now_iso())
        self._publish_state(armed=True)

    def _process_firmware_reset(self, last_fw_ms, iso: str) -> None:
        """Survive a mid-session Due reboot (S8, docs/decisions/0184).

        The serial reader saw a fresh ``PROTO`` handshake after the firmware clock
        was already running, so the board rebooted (power glitch / watchdog / stray
        reset) and lost its state - including ``cueSourceVision``, which a reboot
        clears back to firmware-driven beam tones. If we were driving Phase 3 cues
        (gate open), re-take the DAC (``CUE,VISION``) and clear stale per-trip
        cue/gate/well state so no leftover tone sounds and the next trial waits for a
        fresh hub->stem run - the same reclaim the reopen path does, minus the
        sequence rebuild (the task, trial index, and data file are untouched, so the
        run continues). Always logged: a note on the timeline AND a manifest
        ``firmware_resets`` entry (carrying ``last_fw_ms`` so an offline reader can
        split the firmware ms timeline at the reboot, since its clock restarts at 0).
        """
        self._fw_resets += 1
        if self._gate_was_open:
            self._silence_and_rearm()        # CUE_OFF + per-trip cue/gate/well reset
            self._send_cmd("CUE,VISION")     # host re-owns the DAC (a reboot cleared it)
            note = ("firmware reset detected mid-session - re-asserted CUE,VISION "
                    "(run preserved)")
        else:
            note = ("firmware reset detected mid-session - no active cue drive "
                    "(firmware autonomy already in effect)")
        idx = self._task.index if self._task is not None else 0
        self._emit_intervention(AUTO_FIRMWARE_RESET, note, idx, iso,
                                extra={"last_fw_ms": last_fw_ms})
        self._publish_state(armed=self._gate_was_open)

    def _process_frame(self, bundle, snap) -> None:
        # Gate edges own the DAC-authority handshake + a clean session (re)start.
        if snap.gate_open and not self._gate_was_open:
            # A mid-session reopen resumes (no data loss); a first/new arm re-arms.
            if self._is_session_reopen(snap):
                self._resume_after_reopen(snap)
            else:
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

        # The cue gate reads the Return-cue checkbox live every frame, so the
        # ribbon / planned-trials flag (display only) must follow it too, or a
        # mid-run toggle shows return cues that never play (docs/decisions/0210).
        if self._log is not None \
                and self._log.return_enabled != snap.params.return_enabled:
            self._log.return_enabled = snap.params.return_enabled
            self._publish_state(armed=True)

        # Operator "pause cues" (docs/decisions/0058): silence any tone and freeze
        # trial progress without disarming. The early return skips folding frames
        # into task_events, so the next trial-start edge (departed_hub_via_stem)
        # and cue only fire from a fresh hub->stem run after resume; resetting the
        # gate keeps the cue re-arm consistent with that.
        # The session cap has fired (docs/decisions/0190): a SOFT stop - same
        # silence/freeze as pause, but not operator-clearable; it lifts only on a
        # fresh arm / Restart / the next Session Start. The operator removes the rat
        # and clicks Stop as usual (the recording is not auto-closed, ADR-0060).
        if snap.paused or self._cap_reached is not None:
            if self._active_cue is not None:
                self._send_cmd("CUE_OFF")
                self._active_cue = None
            self._gate.reset()
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

        # Trial start = the rat's first hub->stem departure of a trial, taken from
        # the shared task-event edge (task_events.departed_hub_via_stem) so the
        # HUB->CENTRAL_STEM departure is computed once, in the pure core (FW-0027).
        # The edge is true only on a valid tick, and the log keeps only the first
        # stamp per trial, so a poke-and-retreat re-entry is ignored. ADR-0047.
        if task_frame is not None and task_frame.departed_hub_via_stem:
            # The trial's side is committed by now (every frame's task.update commits
            # the current trial), so record its realized correct side + debias
            # context alongside the start stamp (no-op for the balanced generator).
            self._log_call(lambda: self._log.trial_started(idx, iso))
            self._log_call(lambda: self._log.set_decision(idx))
            # Scope the choice-cue-on stamp to THIS trip: clear it at the trial-start
            # edge (runs just before the cue block below, which re-sets it if the
            # cue fires this trip), so a commit with no cue this lap reads as UNCUED
            # (docs/decisions/0171) instead of inheriting an earlier trip's onset -
            # and the cue-zone dwell (ADR-0108) stays scoped to the current trip.
            self._choice_cue_on_iso = None
            changed = True

        # WHEN: the positional cue gate. Host supplies identity via ,<hz> (v6).
        # Each cue carries a shared task-event credential describing where in the
        # lap the animal is: RETURN needs a stem-out excursion so it marks only a
        # genuine inbound leg (docs/decisions/0061), and CHOICE needs this trip to
        # have DEPARTED the hub up the stem, so a lap run backwards is silent
        # crossing the stem on its way home (docs/decisions/0067).
        self._gate.set_params(snap.params)
        outbound = task_frame.outbound_used_stem if task_frame is not None else False
        # Feed the cue gate the SHARED trial credential (task_events.trial_trip;
        # docs/decisions/0168), the same one the scorer + reward read, so all three
        # agree on what a trial is. It equals left_hub_via_stem today.
        on_leg = task_frame.trial_trip if task_frame is not None else False
        # off_track (docs/decisions/0170) releases a tone the rat would otherwise
        # hold by perching in a polygon gap (the estimator keeps the last zone).
        d = self._gate.step(st.zone, st.direction, st.confidence, st.valid,
                            outbound, on_leg, st.off_track)
        if d.started == CHOICE:
            freq = self._task.current_choice_freq()
            # No valid trial identity (plan exhausted / index out of range) => start
            # NO cue. Auto-extend normally keeps identity valid, but this is the
            # belt-and-suspenders guard: a bare "CUE_ON,CHOICE" makes the firmware
            # play its full-volume LOW-tone fallback (cueFor(-1)) with no calibrated
            # gain, so a completed session would blast a wrong cue on every
            # excursion (docs/decisions/0150).
            if freq is not None:
                # Command the frequency-corrected tone so the speaker EMITS the
                # target, but key the volume balance to the TARGET freq (ADR-0148
                # extends 0125).
                cf = cue_calibration.commanded_hz_for(freq)
                sent = self._send_cmd(
                    f"CUE_ON,CHOICE,{cf},{cue_calibration.gain_pct_for(freq)}")
                # Record the cue as sounding ONLY when the command reached the wire
                # (docs/decisions/0173). A dropped send stamps nothing: no onset row,
                # no firmware-ack wait, and _choice_cue_on_iso stays None so the trip
                # scores UNCUED (docs/decisions/0171) - honest end to end. The gate
                # has latched "in cue", so it will not re-fire this trip; the tone
                # simply does not play.
                if sent:
                    self._active_cue = CHOICE
                    # Await the firmware CUE,ON echo to stamp this trial's cue onset
                    # on the firmware clock (docs/decisions/0165).
                    self._pending_cue_ack_index = idx
                    # Remember this trip's cue-on stamp so a scored excursion can
                    # carry the relative cue-zone dwell (ADR-0108/0109).
                    self._choice_cue_on_iso = iso
                    self._log_call(lambda: self._log.choice_cue_started(idx, iso))
                else:
                    self._note_send_failed("CHOICE", idx, iso)
                changed = True
        elif d.started == RETURN:
            # Identity now comes from this arm's planned return schedule, looked up
            # by the PHYSICAL return arm the rat entered (ADR-0047).
            side = _SIDE_OF_ARM.get(d.return_arm)
            freq = self._seq.return_freq(side, idx) if side is not None else None
            cue = self._seq.return_cue(side, idx) if side is not None else None
            # Same guard as CHOICE: no valid return identity => start no cue rather
            # than a full-volume bare "CUE_ON,RETURN" (docs/decisions/0150).
            if freq is not None:
                cf = cue_calibration.commanded_hz_for(freq)
                sent = self._send_cmd(
                    f"CUE_ON,RETURN,{cf},{cue_calibration.gain_pct_for(freq)}")
                # Same rule as CHOICE (docs/decisions/0173): log the return cue only
                # when it reached the wire; a dropped send records nothing and just
                # notes the failure.
                if sent:
                    self._active_cue = RETURN
                    # Await the firmware CUE,ON return echo to stamp this trial's
                    # return-cue onset on the firmware clock (docs/decisions/0166).
                    self._pending_return_cue_ack_index = idx
                    self._log_call(lambda: self._log.return_cue_started(
                        idx, d.return_arm or "", cue, iso))
                else:
                    self._note_send_failed("RETURN", idx, iso)
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
            # Whether the CHOICE cue actually SOUNDED this trip (the per-trip onset
            # stamp, cleared at the hub). A commit with no cue is scored UNCUED and
            # the planned trial re-runs, not consumed (docs/decisions/0171).
            dec = self._task.update(task_frame,
                                    self._choice_cue_on_iso is not None)
            if dec.choice_side is not None and dec.outcome is not None:
                self._log_call(lambda: self._log.choice_scored(
                    dec.trial_index, dec.choice_side, dec.outcome, iso))
                # Refresh the realized side + debias context for the scored trial
                # (its bias/state were fixed when it was committed; no-op balanced).
                self._log_call(lambda: self._log.set_decision(dec.trial_index))
                changed = True
            if dec.reward:
                # Prime the correct-choice pulse at the well via the shared
                # dispenser (fires here, out at the choice, as before; ADR-0068/
                # 0069). Only count + log when it actually fired: the shared
                # backstop may drop one too soon after the last (ADR-0054).
                fired = self._well.prime_at_credential(task_frame.now) \
                    if self._well is not None else None
                if fired is not None and self._dispatch_fire(fired):
                    self._reward_count += 1
                    self._reward_trial_index = dec.trial_index
                    # Await the firmware SOL/REWARD echo to stamp this trial's
                    # reward on the firmware clock (docs/decisions/0165).
                    self._pending_reward_ack_index = dec.trial_index
                    # Record the RAW prime width for the per-trial reward magnitude
                    # (ADR-0110); bind locals so the deferred call captures values.
                    ti, prime_ms = dec.trial_index, fired.ms
                    self._log_call(
                        lambda: self._log.reward_fired(ti, iso, prime_ms))
                    changed = True
            # Advance the well-visit lifecycle every tick so licks can top up on
            # arrival (credential mode fires nothing here; ADR-0068).
            if self._well is not None:
                self._well.visit_tick(task_frame, credit_pending=False)
            if dec.advanced:
                self._log_call(lambda: self._log.trial_ended(dec.trial_index, iso))
                changed = True
            if dec.uncued_cleared:
                # The uncued trip is spent but the trial did NOT advance: archive
                # the uncued attempt (preserved in the CSV via the ADR-0119 path)
                # and clear the live row so the SAME planned trial re-runs
                # (docs/decisions/0171); mark it on the timeline.
                self._log_call(lambda: self._log.supersede_trial(
                    dec.trial_index, "uncued"))
                self._emit_intervention(
                    AUTO_UNCUED, "uncued trip (no cue this trip) - trial re-run",
                    dec.trial_index, iso)
                changed = True
            if dec.outcome == CORRECT:
                self._correct += 1
            elif dec.outcome == INCORRECT:
                self._incorrect += 1
            elif dec.outcome == UNCUED:
                self._uncued += 1
            # The session cap counts NORMAL scored trials only; ``dec.probe`` is set
            # for the whole unrewarded probe block, trigger included (ADR-0205).
            if dec.outcome in (CORRECT, INCORRECT) and not dec.probe:
                self._cap_scored += 1
            # A correct-choice PROBE just fired on THIS trial (docs/decisions/0134):
            # note where it landed in the session log (the trigger is automatic, so
            # this is the only START marker). Gate on the SCORING edge
            # (``dec.outcome`` set) like the probe tally below: ``probe_trigger`` is a
            # LEVEL read from the trial's meta (it stays set for the trigger trial
            # until the return lap advances the index), so an ungated emit would post
            # an identical note on every tick between the choice and the rat's return.
            if dec.probe_trigger and dec.outcome in (CORRECT, INCORRECT):
                self._emit_intervention(AUTO_PROBE_START,
                                        "probe block triggered (correct choice)",
                                        idx, iso)
            # Tally scored PROBE trials separately for the status readout (they are
            # still counted in _correct/_incorrect above - the session accuracy is
            # over all scored trials; the probe segment is an extra breakdown). The
            # always-correct trigger trial is EXCLUDED so it can't inflate the latent
            # probe accuracy (docs/decisions/0134).
            if dec.probe and not dec.probe_trigger \
                    and dec.outcome in (CORRECT, INCORRECT):
                self._probe_done += 1
                if dec.outcome == CORRECT:
                    self._probe_correct += 1
            # One excursion-log row per outbound trip (stem + non-stem), on the
            # once-per-excursion choice_committed edge (ADR-0109). Done after the
            # score fold so a scored stem trip's choice_time (this frame's iso) is
            # available for the relative cue-zone dwell.
            self._append_excursion(task_frame, idx, iso)

        # Session cap (docs/decisions/0190): evaluated only at a TRIAL BOUNDARY -
        # no cue sounding, no scored choice awaiting its return lap, and the rat
        # not out on a genuine trial trip - so a trial in flight when the clock
        # runs out always finishes (the rat heard the cue; withholding its reward
        # would be an unearned punishment). The trial limit is naturally met on the
        # advance that completes the Nth trial. Only counts once a session has been
        # Started (needs the cap anchor, preserved across a recording restart;
        # docs/decisions/0198); a manual/unrecorded arm is uncapped.
        if self._cap_reached is None and snap.session_cap.enabled \
                and snap.cap_t0 and task_frame is not None \
                and self._active_cue is None \
                and not self._task.awaiting_return \
                and not task_frame.trial_trip:
            completed = self._cap_scored - self._cap_base_done
            elapsed_s = self._session_elapsed_s(snap)
            status = session_cap.evaluate(completed, elapsed_s, snap.session_cap)
            if status.reached:
                self._on_cap_reached(status, completed, elapsed_s, idx, iso, snap)
                changed = True

        # Auto-extend the plan one balanced block AHEAD so a rat that outpaces the
        # planned trial count keeps running with correct cue identity / calibrated
        # volume / scoring / reward, instead of hitting a degenerate "complete"
        # state (the plan runs out; docs/decisions/0150, extends 0059). One extend
        # per boundary: appending a block grows len(seq) by block_size, so this is
        # immediately false again and won't re-fire until the animal advances a
        # whole block later. Keeping the plan exactly one step ahead also caps the
        # trailing empty planned rows in the CSV at block_size - 1 (same footprint
        # as today's fixed plan).
        # Skipped once the cap has fired: no further trial will be cued.
        if self._seq is not None and self._cap_reached is None \
                and self._task.index >= len(self._seq) - 1:
            self._extend_trials(snap)

        if changed:
            # Finalize the CURRENT trial's decision before we align + publish the
            # display. On the frame a trial advances, task.update() commits the OLD
            # index and only then increments, so the new current trial is not yet
            # committed - refresh_provisional would then render it from its
            # pre-commit provisional reading (blank sched_state = looks NORMAL, and
            # the next cell walks the spine instead of the forced side), so the
            # ribbon showed the current intervention trial as NORMAL. This is the
            # same commit _ensure_committed would make one frame later, with no new
            # animal evidence in between (docs/decisions/0152; ADR-0096). Idempotent
            # and a no-op for the balanced generator.
            if self._seq is not None:
                self._seq.commit(self._task.index)
            # Re-align the display cues of the current + upcoming (not-yet-run)
            # trials to the sequence's current knowledge, so a corrective block that
            # shifts the counterbalanced spine doesn't leave the ribbon showing stale
            # cues for later trials (docs/decisions/0096). No-op for balanced.
            self._log_call(lambda: self._log.refresh_provisional(self._task.index))
            self._write_log()
            self._publish_state(armed=True)
        self._publish_status(snap)

    @staticmethod
    def _session_elapsed_s(snap: _Phase3Snapshot) -> float:
        """Seconds since the cap anchor - the FIRST Start after an arm - from that
        anchor's monotonic clock (docs/decisions/0124/0198); 0.0 with no anchor
        (unrecorded arm). Uses ``cap_t0`` (preserved across a recording restart),
        NOT the per-recording ``session_t0``, so the clock runs continuously."""
        t0 = snap.cap_t0 or {}
        mono = t0.get("mono")
        if mono is None:
            return 0.0
        try:
            return max(0.0, time.monotonic() - float(mono))
        except (TypeError, ValueError):
            return 0.0

    def _on_cap_reached(self, status, completed: int, elapsed_s: float,
                        trial_index: int, iso: str, snap: _Phase3Snapshot) -> None:
        """The session cap just fired (docs/decisions/0190): record it, silence
        the rig, and post the auto-note that tells the operator what the rat
        earned. From the next tick ``_process_frame`` takes the frozen branch."""
        cap = snap.session_cap
        self._cap_reached = {
            "reason": status.reason, "iso": iso, "trial_index": trial_index,
            "completed": int(completed), "elapsed_s": round(float(elapsed_s), 1),
            "max_trials": cap.max_trials, "max_minutes": cap.max_minutes,
        }
        if self._active_cue is not None:
            self._send_cmd("CUE_OFF")
            self._active_cue = None
        self._gate.reset()
        limit = (f"{cap.max_trials} normal trials"
                 if status.reason == session_cap.REASON_TRIALS
                 else f"{session_cap.mmss(cap.max_minutes * 60.0)} min")
        text = (f"session cap reached ({limit}): {completed} normal trials in "
                f"{session_cap.mmss(elapsed_s)} - earned "
                f"{format_volume(self._reward_ul)} - remove rat, Stop session")
        target = session_cap.water_target_summary(snap.daily_water_ml, self._reward_ul)
        if target:
            text += f" - {session_cap.format_water_target(target)}"
        self._emit_intervention(AUTO_CAP_REACHED, text, trial_index, iso,
                                extra={"cap": dict(self._cap_reached),
                                       "water_target": target})

    def _process_lick(self, iso: str, fw_ms, mono: float) -> None:
        """A center-well lick from the serial thread: record its time in the data
        log (ADR-0047) AND feed the shared dispenser so a lick while camping at
        the well tops up the reward, up to max_topups (ADR-0068/0069)."""
        changed = False
        if self._log is not None and self._log.lick(iso, fw_ms):
            changed = True
        if self._well is not None and self._center_lick is not None:
            res = self._well.on_lick(self._center_lick, mono, credit_pending=False)
            for fire in res.actions:
                if self._dispatch_fire(fire):
                    self._reward_count += 1
                    if self._reward_trial_index is not None:
                        # Record the RAW top-up width too (ADR-0110); bind locals so
                        # the deferred call captures this pulse's values.
                        ti, topup_ms = self._reward_trial_index, fire.ms
                        self._log_call(
                            lambda: self._log.topup_fired(ti, topup_ms))
                    changed = True
        if changed:
            self._write_log()
            self._publish_state(armed=True)

    def _process_cue_ack(self, fw_ms) -> None:
        """Bind a firmware CHOICE cue-on echo's ms to the pending trial's
        choice_cue_on_fw_ms (docs/decisions/0165). First echo wins (the pending
        index is cleared once bound); an echo with no pending cue is ignored."""
        idx = self._pending_cue_ack_index
        if idx is None:
            return
        self._pending_cue_ack_index = None
        if self._log is not None:
            self._log.cue_acked(idx, fw_ms)
            self._write_log()

    def _process_return_cue_ack(self, fw_ms) -> None:
        """Bind a firmware RETURN cue-on echo's ms to the pending trial's
        return_cue_on_fw_ms (docs/decisions/0166). First echo wins; an echo with no
        pending return cue is ignored (e.g. the return cue is disabled)."""
        idx = self._pending_return_cue_ack_index
        if idx is None:
            return
        self._pending_return_cue_ack_index = None
        if self._log is not None:
            self._log.return_cue_acked(idx, fw_ms)
            self._write_log()

    def _process_reward_ack(self, fw_ms) -> None:
        """Bind the first firmware SOL/REWARD echo after a prime to the pending
        trial's reward_cmd_fw_ms (docs/decisions/0165). First echo wins, so a
        later lick top-up echo does not overwrite the prime's firmware time; an
        echo with no pending prime is ignored."""
        idx = self._pending_reward_ack_index
        if idx is None:
            return
        self._pending_reward_ack_index = None
        if self._log is not None:
            self._log.reward_acked(idx, fw_ms)
            self._write_log()

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
        # Pass the ADR-0190 cap record (a plain dict, copied) so the Rig-view
        # banner can alert off the published snapshot (docs/decisions/0190/0192).
        # Gated on an ACTIVE recording session (``_session_id`` set): ``_cap_reached``
        # deliberately SURVIVES Stop to keep the still-armed task muted (ADR-0190),
        # but the "remove rat, Stop session" banner is stale once Stop was pressed,
        # so it is only published while the capped session is still recording. It
        # clears on the next Start (which also resets ``_cap_reached``).
        cap = (dict(self._cap_reached)
               if (self._cap_reached and self._session_id) else None)
        self._state = log.snapshot(idx, armed=armed, cap_reached=cap)

    def _record_write_result(self, slot: str, path: Optional[str],
                             exc: Optional[OSError]) -> None:
        """Track a persist attempt for a data-file ``slot`` ("task" / "excursions";
        docs/decisions/0121). A failure records the file name so the status line
        can warn that data is NOT on disk; a success clears that slot so a
        transient hiccup that recovers stops warning. Per-slot so a persistently
        failing file isn't masked by the other file writing fine."""
        if exc is None:
            self._write_errors.pop(slot, None)
        else:
            self._write_errors[slot] = os.path.basename(path) if path else slot

    def _resolve_cue_calibration(self) -> dict:
        """The tones AS PLAYED this run: commanded Hz + gain % for the HIGH and LOW
        tones, resolved from cue_calibration.json (P3, docs/decisions/0182). Sent
        into the trial-log header so the file records the played tones, not just
        the raw calibration snapshot. Falls back to identity/full-volume for an
        un-calibrated tone (the module's own defaults)."""
        cfg = self._seq.cfg
        high_hz, low_hz = cfg.high_tone_hz, cfg.low_tone_hz
        return {
            "high_cmd_hz": cue_calibration.commanded_hz_for(high_hz),
            "high_gain_pct": cue_calibration.gain_pct_for(high_hz),
            "low_cmd_hz": cue_calibration.commanded_hz_for(low_hz),
            "low_gain_pct": cue_calibration.gain_pct_for(low_hz),
        }

    def _write_log(self) -> None:
        """Atomically persist the whole table (temp file + os.replace), so a crash
        never leaves a half-written CSV. Cheap for a few hundred rows.

        The temp file is flushed AND fsync'd before the rename (docs/decisions/0174)
        so "crash-safe" means the bytes are on the physical disk, not merely
        un-torn in the OS cache. A write failure is NOT swallowed: it is recorded
        and surfaced in the status line (docs/decisions/0121), because a silently
        dropped write means the session's data is not on disk."""
        if self._log is None or self._log_path is None:
            return
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            tmp = self._log_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(self._log.render_csv())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._log_path)
        except OSError as exc:
            self._record_write_result("task", self._log_path, exc)
        else:
            self._record_write_result("task", self._log_path, None)

    # ------------------------------------------------ excursion event log (A/0109)
    @staticmethod
    def _excursion_path_for(log_path: Optional[str]) -> Optional[str]:
        """The excursion-log path that pairs with a trial-CSV path: the same name
        with an ``_excursions`` suffix, so a restart's ``_r<n>`` tag carries over."""
        if not log_path:
            return None
        base = log_path[:-4] if log_path.endswith(".csv") else log_path
        return f"{base}_excursions.csv"

    def current_excursion_path(self) -> Optional[str]:
        """Path of the in-progress excursion-event file, or None until a session
        starts. Used by the Session controller's manifest (ADR-0109)."""
        return self._excursion_path

    def _append_excursion(self, task_frame, idx: int, iso: str) -> None:
        """Append one outbound-excursion row on the choice_committed edge and
        persist the log (ADR-0109). ``type``/``outcome`` come from
        ``outbound_used_stem`` (a stem trip is a scored trial with a
        ``linked_trial``; a non-stem trip is a denied trip). A scored trip also
        carries the relative cue-zone dwell (choice_time - cue_on; ADR-0108)."""
        if self._excursion_log is None or task_frame is None:
            return
        side = task_frame.choice_committed
        if side is None:                 # not a commit edge this tick
            return
        stem = bool(task_frame.outbound_used_stem)
        linked = (idx + 1) if stem else None
        cue_dwell = self._cue_dwell_for(iso) if stem else None
        sched_state, bias = self._decision_context(idx)
        self._excursion_log.append(
            iso=iso, mono=getattr(task_frame, "now", None),
            type=(TYPE_UP_STEM if stem else TYPE_NON_STEM), side=side,
            outcome=(OUTCOME_SCORED if stem else OUTCOME_DENIED),
            linked_trial=linked, sched_state=sched_state, bias=bias,
            cue_dwell_s=cue_dwell)
        self._write_excursion_log()

    def _cue_dwell_for(self, choice_iso: str) -> Optional[float]:
        """This trip's relative cue-zone dwell = choice_time - choice_cue_on, in
        seconds, or None if either stamp is missing/unparseable or the interval is
        negative (a clock anomaly). Host-clock, not a reaction time (ADR-0108)."""
        on = self._parse_iso(self._choice_cue_on_iso)
        choice = self._parse_iso(choice_iso)
        if on is None or choice is None:
            return None
        dwell = (choice - on).total_seconds()
        return dwell if dwell >= 0.0 else None

    @staticmethod
    def _parse_iso(iso: Optional[str]):
        if not iso:
            return None
        try:
            return datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return None

    def _decision_context(self, idx: int):
        """The closed-loop scheduler state + directional bias at trial ``idx``
        (blank for the balanced generator), for the excursion row."""
        seq = self._seq
        if seq is None or not getattr(seq, "is_debias", False):
            return None, None
        try:
            m = seq.trial_meta(idx)
        except Exception:
            return None, None
        return m.get("sched_state"), m.get("bias")

    def _write_excursion_log(self) -> None:
        """Atomically persist the excursion event log (temp file + os.replace),
        the same crash-safe write as :meth:`_write_log` - flushed + fsync'd before
        the rename (docs/decisions/0174)."""
        if self._excursion_log is None or self._excursion_path is None:
            return
        try:
            os.makedirs(os.path.dirname(self._excursion_path), exist_ok=True)
            tmp = self._excursion_path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(self._excursion_log.render_csv())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._excursion_path)
        except OSError as exc:
            self._record_write_result("excursions", self._excursion_path, exc)
        else:
            self._record_write_result("excursions", self._excursion_path, None)

    def _write_warn(self) -> str:
        """A loud status-line prefix when a data file could not be written to disk
        (docs/decisions/0121), else "". Named files so the operator knows what is
        at risk; shown in every state since a failure may outlast the run."""
        if not self._write_errors:
            return ""
        files = ", ".join(sorted(set(self._write_errors.values())))
        return f"!! DISK WRITE FAILED: {files} - data not saved !!  "

    def _publish_status(self, snap: _Phase3Snapshot) -> None:
        warn = self._write_warn()
        if not snap.gate_open:
            self._status_text = warn + f"gated ({self._gate_reason()})"
            return
        # Armed but no session owns a folder yet: the task runs and the plan is
        # held in memory, but nothing is on disk until Start binds task/ (Increment
        # 4b, docs/decisions/0177). Say so loudly so a run isn't mistaken for one
        # that is being recorded.
        rec = "" if self._log_path is not None else "NOT RECORDING - start a session  "
        if snap.paused:
            self._status_text = (
                warn + rec + "PAUSED  (cues muted; still armed - press Resume)")
            return
        n = len(self._seq) if self._seq is not None else 0
        idx = self._task.index if self._task is not None else 0
        done = self._correct + self._incorrect
        acc = (100.0 * self._correct / done) if done else 0.0
        # Session cap hit (docs/decisions/0190): say so loudly and stop here - the
        # rat is no longer being cued; the operator removes it and clicks Stop.
        hit = self._cap_reached
        if hit is not None:
            limit = (f"{hit['max_trials']} normal trials"
                     if hit["reason"] == session_cap.REASON_TRIALS
                     else f"{session_cap.mmss(hit['max_minutes'] * 60.0)} min")
            self._status_text = (
                warn + rec +
                f"CAP REACHED ({limit}) - {hit['completed']} normal trials in "
                f"{session_cap.mmss(hit['elapsed_s'])}  "
                f"correct:{self._correct}/{done} ({acc:.0f}%)  "
                f"rewards:{self._reward_count} ({format_volume(self._reward_ul)})  "
                "- remove rat, Stop session")
            return
        extra = f"  drops:{self._act_drops}" if self._act_drops else ""
        # Live cap countdown once a session has been Started (docs/decisions/0190):
        # NORMAL scored trials since the (first) Start / limit (probe trials run on
        # top; ADR-0205), elapsed / limit.
        if snap.session_cap.enabled and snap.cap_t0:
            extra += "  " + session_cap.format_countdown(
                snap.session_cap, self._cap_scored - self._cap_base_done,
                self._session_elapsed_s(snap))
        # UNCUED trips (a commit with no cue this trip; docs/decisions/0171) never
        # scored/rewarded - show the count when any occurred so the operator sees
        # the rig failing to cue the animal (the trial re-runs, not advances).
        if self._uncued:
            extra += f"  uncued:{self._uncued}"
        # Cue sends that never reached the wire (docs/decisions/0173): a real fault
        # (broker/socket down) worth showing so a silent rig isn't mistaken for a
        # well-behaved one - the matching auto:send_failed notes carry the detail.
        if self._send_fails:
            extra += f"  send_fail:{self._send_fails}"
        # A mid-session tracking flicker was resumed, not restarted (ADR-0120):
        # show the count so the operator knows the run survived the reopen.
        if self._reopen_resumes:
            extra += f"  resumes:{self._reopen_resumes}"
        if self._unapplied_settings:
            extra += "  !! settings changed while disarmed NOT applied - Restart to apply"
        # Mid-session Due reboots the host caught and re-asserted through (ADR-0184):
        # show the count so a survived reset isn't invisible (the matching
        # auto:firmware_reset notes + manifest carry the detail).
        if self._fw_resets:
            extra += f"  fw_reset:{self._fw_resets}"
        # Per-animal cue->side mapping (ADR-0186): when animals.json set this
        # animal's mapping, show it - with a "!" when it DISAGREED with phase3.json
        # - so the operator sees which convention the rig is actually running under.
        if snap.cue_mapping_source == "animals.json":
            side = "L" if snap.trial_config.high_tone_is_left else "R"
            flag = "!" if snap.cue_mapping_overrides else ""
            extra += f"  cue:H->{side}(animals.json{flag})"
        # Per-animal reward volume (ADR-0191): show the width the run armed with
        # and that it came from animals.json, not the Pulse field.
        if snap.reward_pulse_source != session_cap.SOURCE_CONFIG:
            extra += f"  reward:{snap.reward_pulse_ms}ms(animals.json)"
        # Conditional probe segment: only after the first scored probe BLOCK trial
        # (so a normal session's status line is unchanged), plus a live PROBE tag
        # while a probe block is running and a [PROBE OFF] tag when the operator has
        # disabled probes mid-session (docs/decisions/0134/0137).
        probe = ""
        st = self._state
        probe_state = getattr(st, "probe_state", "NORMAL") if st is not None else "NORMAL"
        probe_enabled = bool(getattr(st, "probe_enabled", False)) if st is not None else False
        probe_armed = bool(getattr(st, "probe_armed", True)) if st is not None else True
        if self._probe_done:
            ppct = 100.0 * self._probe_correct / self._probe_done
            probe = f"  probe:{self._probe_correct}/{self._probe_done} ({ppct:.0f}%)"
        if probe_state == "ACTIVE":
            probe += "  [PROBE ACTIVE]"
        elif probe_enabled and not probe_armed:
            probe += "  [PROBE OFF]"
        self._status_text = (
            warn + rec +
            f"ARMED  seed:{self._active_seed}  trial:{min(idx + 1, n)}/{n}  "
            f"correct:{self._correct}/{done} ({acc:.0f}%)  "
            f"rewards:{self._reward_count} ({format_volume(self._reward_ul)}){probe}  "
            f"{self._last_fw_cue}{extra}")

    def _send_cmd(self, cmd: str) -> bool:
        """Send one command to the broker; return whether it reached the wire.

        False when no send is wired (``_send is None``), the injected send reported
        failure (``RigClient.send`` returns a bool; docs/decisions/0173), or it
        raised. Callers that log a command as done - the cue-onset stamps - gate
        that logging on this return so the trial CSV never records a cue that did
        not actually play."""
        if self._send is None:
            return False
        try:
            return bool(self._send(cmd))
        except Exception:
            return False

    def _dispatch_fire(self, fire) -> bool:
        """Route a dispenser ``FireReward`` (prime or top-up) through the shared
        dispatcher; return whether it actually fired (the global backstop may
        suppress it) and accumulate the dispensed volume. Falls back to a direct
        R<port> send (no backstop, assumed fired) if no dispatcher is wired.
        ADR-0054/0068."""
        if self._dispatcher is None:
            self._send_cmd(f"R{fire.port}1,{fire.ms}")
            self._reward_ul += reward_volume_ul(fire.ms)
            return True
        rec = self._dispatcher.fire(fire.port, fire.ms,
                                    reason=f"phase3_{fire.reason}", rule_id="phase3")
        if rec.fired:
            self._reward_ul += rec.volume_ul
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

    def _current_pulse_ms(self) -> int:
        """Reward prime pulse width (ms) from the field; falls back to the default
        on a blank/malformed entry, and is floored at 1 ms."""
        try:
            return max(1, int(float(self._pulse_var.get())))
        except (tk.TclError, ValueError):
            return Phase3Config().reward_pulse_ms

    def _current_max_topups(self) -> int:
        """Max lick top-ups from the field; 0 (no top-ups) on a blank/bad entry."""
        try:
            return max(0, int(float(self._topups_var.get())))
        except (tk.TclError, ValueError):
            return 0

    # ---------------------------------------------- debias (closed-loop) fields
    def _current_debias_dict(self) -> dict:
        """The debias-block params from the GUI fields (safe parse, defaults on a
        blank/bad entry). Range-checking is left to ``to_trial_config`` so a bad
        combo (e.g. min_errors > window) is reported, not silently clamped."""
        db = DebiasBlockConfig()

        def _i(var, default):
            try:
                return int(float(var.get()))
            except (tk.TclError, ValueError):
                return default

        def _f(var, default):
            try:
                return float(var.get())
            except (tk.TclError, ValueError):
                return default

        return {"enabled": bool(self._db_enabled_var.get()),
                "window": _i(self._db_window_var, db.window),
                "min_errors": _i(self._db_min_errors_var, db.min_errors),
                "trigger_bias": _f(self._db_trigger_var, db.trigger_bias),
                "exit_consecutive": _i(self._db_exit_var, db.exit_consecutive),
                "max_block_len": _i(self._db_cap_var, db.max_block_len),
                "alt_veto_rate": _f(self._db_alt_veto_var, db.alt_veto_rate)}

    def _current_alternation_dict(self) -> dict:
        """The de-alternation-block params from the GUI fields (safe parse; range
        checking is left to ``to_trial_config`` so a bad combo is reported)."""
        al = AlternationBlockConfig()

        def _i(var, default):
            try:
                return int(float(var.get()))
            except (tk.TclError, ValueError):
                return default

        def _f(var, default):
            try:
                return float(var.get())
            except (tk.TclError, ValueError):
                return default

        return {"enabled": bool(self._alt_enabled_var.get()),
                "window": _i(self._alt_window_var, al.window),
                "min_samples": _i(self._alt_min_samples_var, al.min_samples),
                "trigger_rate": _f(self._alt_trigger_var, al.trigger_rate),
                "exit_repeats_per_side": _i(self._alt_exit_repeats_var,
                                            al.exit_repeats_per_side),
                "max_block_len": _i(self._alt_cap_var, al.max_block_len)}

    def _current_trial_config(self) -> TrialConfig:
        """Resolve the schedule config for the NEXT arm, overlaying the GUI
        generator + intervention fields onto the file's ``sequence`` section. On an
        invalid edit it records the error and falls back to the last valid
        (loaded/saved) config, so the Tk poll never crashes on a half-typed field.
        """
        if not hasattr(self, "_gen_var"):
            return to_trial_config(self._config)
        seq = dict(self._config["sequence"])
        seq["generator"] = self._gen_var.get()
        seq["debias_blocks"] = {**seq.get("debias_blocks", {}),
                                **self._current_debias_dict()}
        seq["alternation_blocks"] = {**seq.get("alternation_blocks", {}),
                                     **self._current_alternation_dict()}
        # Per-animal cue->side mapping (ADR-0186): animals.json wins over the
        # phase3.json default when it sets one for this animal; otherwise this is
        # a no-op (resolved == the config value).
        resolved, _source, _overrides = self._resolve_cue_mapping()
        seq["high_tone_is_left"] = resolved
        try:
            tc = to_trial_config({"sequence": seq})
            self._debias_error = ""
            return tc
        except Exception as e:
            self._debias_error = f"{type(e).__name__}: {e}"
            return to_trial_config(self._config)

    # -------------------------------------------------- phase3.json config
    def _load_config(self) -> None:
        """(Re)load host/phase3.json into ``self._config``, validating it. A
        missing file yields built-in defaults; a malformed one falls back to
        defaults and records the error in ``self._config_error`` (surfaced in
        status) rather than crashing the tab. Pure read - the caller re-seeds the
        Tk vars (see :meth:`_apply_config_to_vars`)."""
        try:
            cfg = load_phase3_config(self._config_path)
            validate_phase3_config(cfg)
            self._config = cfg
            self._config_error = ""
        except Exception as e:                # malformed JSON or bad value
            self._config = default_config()
            self._config_error = f"{type(e).__name__}: {e}"

    def _apply_config_to_vars(self) -> None:
        """Push the loaded config into the tab's Tk vars (reward + cue + seed).

        Used on Reload. The seed follows the optional-seed policy: a pinned
        integer is shown; ``null`` draws a fresh random seed (as at launch)."""
        r, c = self._config["reward"], self._config["cue"]
        self._pulse_var.set(str(r["pulse_ms"]))
        self._topups_var.set(str(r["max_topups"]))
        self._reward_incorrect_var.set(bool(r["reward_incorrect"]))
        self._choice_var.set(bool(c["choice_enabled"]))
        self._return_var.set(bool(c["return_enabled"]))
        self._up_stem_var.set(bool(c["require_up_stem"]))
        self._conf_var.set(str(c["min_confidence"]))
        seed = resolve_seed(self._config)
        self._seed_var.set(str(seed) if seed is not None
                           else str(random.randint(1, SEED_MASK)))
        # Debias generator + block params (GUI-editable subset of ``sequence``;
        # ADR-0070 carve-out).
        s = self._config["sequence"]
        self._gen_var.set(s.get("generator", GEN_BALANCED))
        db = s.get("debias_blocks", {})
        dc = DebiasBlockConfig()
        self._db_enabled_var.set(bool(db.get("enabled", True)))
        self._db_window_var.set(str(db.get("window", dc.window)))
        self._db_min_errors_var.set(str(db.get("min_errors", dc.min_errors)))
        self._db_trigger_var.set(str(db.get("trigger_bias", dc.trigger_bias)))
        self._db_exit_var.set(str(db.get("exit_consecutive", dc.exit_consecutive)))
        self._db_cap_var.set(str(db.get("max_block_len", dc.max_block_len)))
        self._db_alt_veto_var.set(str(db.get("alt_veto_rate", dc.alt_veto_rate)))
        al = s.get("alternation_blocks", {})
        ac = AlternationBlockConfig()
        self._alt_enabled_var.set(bool(al.get("enabled", True)))
        self._alt_window_var.set(str(al.get("window", ac.window)))
        self._alt_min_samples_var.set(str(al.get("min_samples", ac.min_samples)))
        self._alt_trigger_var.set(str(al.get("trigger_rate", ac.trigger_rate)))
        self._alt_exit_repeats_var.set(
            str(al.get("exit_repeats_per_side", ac.exit_repeats_per_side)))
        self._alt_cap_var.set(str(al.get("max_block_len", ac.max_block_len)))
        self._sync_debias_enabled()

    def _collect_live_config(self) -> dict:
        """Fold the tab's live GUI-editable fields into ``self._config`` in place
        and return it - the single source of truth for both the Save button and the
        save-on-arm write (:meth:`persist_live_config`).

        Mutates only the operator-editable keys (reward + cue + the debias
        generator / intervention-block carve-out of ``sequence``; ADR-0070), so the
        scientific schedule params, the ``seed``, and ``_schema`` are preserved.
        Does NOT validate or write - callers do that."""
        cfg = self._config
        p = self._current_params()
        cfg["reward"]["pulse_ms"] = self._current_pulse_ms()
        cfg["reward"]["max_topups"] = self._current_max_topups()
        cfg["reward"]["reward_incorrect"] = bool(self._reward_incorrect_var.get())
        cfg["cue"]["choice_enabled"] = p.choice_enabled
        cfg["cue"]["return_enabled"] = p.return_enabled
        cfg["cue"]["require_up_stem"] = p.require_up_stem
        cfg["cue"]["min_confidence"] = p.min_confidence
        # Debias generator + block params are the GUI-editable slice of the
        # otherwise file-only ``sequence`` section (ADR-0070 carve-out); persist
        # them too, leaving the scientific schedule params untouched.
        cfg["sequence"]["generator"] = self._gen_var.get()
        cfg["sequence"].setdefault("debias_blocks", {}).update(
            self._current_debias_dict())
        cfg["sequence"].setdefault("alternation_blocks", {}).update(
            self._current_alternation_dict())
        return cfg

    def _save_settings(self) -> None:
        """Persist the tab's reward + cue fields to phase3.json (Save button).

        Folds the live fields in (:meth:`_collect_live_config`), validates the
        whole config, then writes it. Nothing is written if validation fails. The
        scientific ``sequence`` params are edited in the file, not here."""
        cfg = self._collect_live_config()
        try:
            validate_phase3_config(cfg)
        except Exception as e:
            messagebox.showerror("Save Phase 3 settings",
                                 f"Edits are invalid, nothing saved:\n"
                                 f"{type(e).__name__}: {e}")
            return
        try:
            save_phase3_config(self._config_path, cfg)
        except OSError as e:
            messagebox.showerror("Save Phase 3 settings", f"{type(e).__name__}: {e}")
            return
        self._config_error = ""
        self._refresh_seq_info()
        messagebox.showinfo(
            "Save Phase 3 settings",
            "Saved reward + cue + intervention (debias / de-alternation) settings "
            "to phase3.json. (The scientific schedule params - trials/block/tones/"
            "seed - are edited in the file.) New settings apply on the next arm.")

    def persist_live_config(self) -> Optional[str]:
        """Save-on-arm (P2; docs/decisions/0167): write the tab's live
        GUI-editable Phase 3 config to ``host/phase3.json`` so the per-session
        ``config/`` snapshot copied at Start reflects EXACTLY what this armed
        session will run with - not just what was last hand-Saved.

        Same fields as the Save button (:meth:`_collect_live_config`) but silent
        (no dialogs) and best-effort: on an invalid edit or a write failure it
        writes nothing and returns a short note for Start to log, so it can never
        break Start. Returns None on success. Called on the Tk thread from the
        Session controller's Start, just before the config snapshot.

        Note: this pins the archived config to the live values AS OF Start. A field
        changed mid-session is still applied live (the actuation snapshot reads the
        Tk vars each poll) but is not re-persisted - the same live-tuning caveat the
        data-file header carries."""
        if not hasattr(self, "_enable_var"):
            return None
        try:
            cfg = self._collect_live_config()
            validate_phase3_config(cfg)
        except Exception as e:
            return (f"Phase 3 config not persisted (invalid edit): "
                    f"{type(e).__name__}: {e}")
        try:
            save_phase3_config(self._config_path, cfg)
        except OSError as e:
            return f"Phase 3 config not persisted: {type(e).__name__}: {e}"
        self._config_error = ""
        try:
            self._refresh_seq_info()
        except tk.TclError:
            pass
        return None

    def _reload_settings(self) -> None:
        """Re-read phase3.json from disk into the tab (Reload button), e.g. after
        hand-editing the sequence/tones. Re-seeds the reward/cue/seed vars and the
        read-only sequence readout; applies on the next arm."""
        self._load_config()
        self._apply_config_to_vars()
        self._refresh_seq_info()
        if self._config_error:
            messagebox.showerror("Reload phase3.json", self._config_error)
        else:
            messagebox.showinfo(
                "Reload phase3.json",
                "Reloaded reward/cue/sequence from phase3.json. Applies on the "
                "next arm.")

    def _refresh_seq_info(self) -> None:
        """Update the read-only sequence/tones readout from the current config."""
        s = self._config["sequence"]
        left = "L" if s["high_tone_is_left"] else "R"
        seed = s.get("seed")
        seed_txt = "random each launch" if seed is None else str(seed)
        # Read-only probe-block summary (params are file-only per ADR-0070, like the
        # tones/seed; the only live control is the Rig-view abort button). Probes fire
        # automatically on a correct choice inside a context window (docs/decisions/
        # 0134), so summarise the windows. See probe_blocks.py / docs/decisions/0115.
        pb = s.get("probe_blocks", {})
        if pb.get("enabled", False):
            windows = pb.get("windows", []) or []
            win_txt = (",".join(f"{int(w[0])}-{int(w[1])}" for w in windows)
                       if windows else "no windows")
            probe_txt = f" \u00b7 probe {int(pb.get('block_len', 10))} @ {win_txt}"
        else:
            probe_txt = " \u00b7 probe off"
        # Read-only session cap (file-only like the schedule params; ADR-0190).
        cap = self._current_session_cap()
        if cap.enabled:
            cap_txt = (f" \u00b7 cap {cap.max_trials} normal trials / "
                       f"{cap.max_minutes:g} min")
        else:
            cap_txt = " \u00b7 cap off"
        self._seq_info_var.set(
            f"trials {s['max_trials']} \u00b7 block {s['block_size']} \u00b7 "
            f"max-run {s['max_same_run']} \u00b7 tones {s['high_tone_hz']}/"
            f"{s['low_tone_hz']} Hz (HIGH={left}) \u00b7 seed {seed_txt}{probe_txt}"
            f"{cap_txt}")

    def _sync_debias_enabled(self) -> None:
        """Enable each intervention's controls to match the generator + its enable
        checkbox (they are inert for the balanced schedule).

        The side-bias debias spinboxes are live under ``debias_blocks`` always, and
        under ``intervention_blocks`` only when its enable box is ticked; the
        alternation spinboxes are live only under ``intervention_blocks`` with its
        box ticked; the two enable checkboxes themselves are live only under
        ``intervention_blocks``. Also refreshes the reward-incorrect warning."""
        if not hasattr(self, "_debias_spinboxes"):
            return
        gen = self._gen_var.get()
        is_interv = gen == GEN_INTERVENTION_BLOCKS
        db_on = gen == GEN_DEBIAS_BLOCKS or (
            is_interv and bool(self._db_enabled_var.get()))
        alt_on = is_interv and bool(self._alt_enabled_var.get())

        def _set(widgets, on):
            for w in widgets:
                try:
                    w.configure(state=("normal" if on else "disabled"))
                except tk.TclError:
                    pass

        _set(self._debias_spinboxes, db_on)
        _set(getattr(self, "_alt_spinboxes", []), alt_on)
        _set(getattr(self, "_intervention_checks", []), is_interv)
        self._refresh_reward_incorrect_warning()

    def _refresh_reward_incorrect_warning(self) -> None:
        """Show a light caution when a closed-loop generator is selected while the
        reward-incorrect shaping aid is on: rewarding every choice defeats an
        intervention block (the animal earns reward whether or not it complies).
        Non-blocking - just a visible note (the operator confirmed they don't use
        the aid; FW-0036)."""
        if not hasattr(self, "_reward_incorrect_warn_var"):
            return
        closed_loop = self._gen_var.get() in (
            GEN_DEBIAS_BLOCKS, GEN_INTERVENTION_BLOCKS)
        on = closed_loop and bool(self._reward_incorrect_var.get())
        self._reward_incorrect_warn_var.set(
            "\u26a0 Reward-incorrect is ON: it rewards every choice, so an "
            "intervention block can't push the animal. Turn it off for the block "
            "to work." if on else "")

    # -------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 3}
        root = ttk.Frame(self.frame, padding=8)
        root.pack(fill=tk.BOTH, expand=True)

        self._enable_var = tk.BooleanVar(value=False)
        arm = ttk.Checkbutton(
            root, text="Arm Phase 3 (host-owned cued task; firmware = actuator)",
            variable=self._enable_var)
        self._enable_var.trace_add("write", self._on_enable_write)
        arm.grid(row=0, column=0, columnspan=2, sticky="w", **pad)
        tip(arm, "phase3.arm")

        self._choice_var = tk.BooleanVar(value=self._config["cue"]["choice_enabled"])
        c = ttk.Checkbutton(root, text="Choice cue (central stem, up-stem)",
                            variable=self._choice_var)
        c.grid(row=1, column=0, columnspan=2, sticky="w", **pad)
        tip(c, "phase3.choice")

        self._up_stem_var = tk.BooleanVar(
            value=self._config["cue"]["require_up_stem"])
        u = ttk.Checkbutton(root, text="   require up-stem direction",
                            variable=self._up_stem_var)
        u.grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        tip(u, "phase3.up_stem")

        self._return_var = tk.BooleanVar(value=self._config["cue"]["return_enabled"])
        r = ttk.Checkbutton(root, text="Return cue (return arm, fail-safe)",
                            variable=self._return_var)
        r.grid(row=3, column=0, columnspan=2, sticky="w", **pad)
        tip(r, "phase3.return")

        self._reward_incorrect_var = tk.BooleanVar(
            value=self._config["reward"]["reward_incorrect"])
        ri = ttk.Checkbutton(root, text="Reward incorrect choices (shaping)",
                             variable=self._reward_incorrect_var,
                             command=self._refresh_reward_incorrect_warning)
        ri.grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        tip(ri, "phase3.reward_incorrect")

        self._build_reward_ui(root, row=5)

        ttk.Label(root, text="Min confidence:").grid(
            row=6, column=0, sticky="w", **pad)
        self._conf_var = tk.StringVar(value=str(self._config["cue"]["min_confidence"]))
        conf = ttk.Spinbox(root, from_=0.0, to=1.0, increment=0.05, width=6,
                           textvariable=self._conf_var)
        conf.grid(row=6, column=1, sticky="w", **pad)
        tip(conf, "phase3.min_confidence")

        # NOTE: the animal id is no longer entered here - it comes from the Task
        # tab's single shared Animal ID field via the injected ``animal_id``
        # provider (docs/decisions/0053, FW-0005).
        ttk.Label(root, text="Sequence seed:").grid(
            row=7, column=0, sticky="w", **pad)
        seed_row = ttk.Frame(root)
        seed_row.grid(row=7, column=1, sticky="w", **pad)
        # Optional pinned seed from phase3.json; null -> a fresh random seed each
        # launch (FW-0024/ADR-0070). The "New" button still overrides it live.
        init_seed = resolve_seed(self._config)
        self._seed_var = tk.StringVar(
            value=str(init_seed) if init_seed is not None
            else str(random.randint(1, SEED_MASK)))
        seed = ttk.Entry(seed_row, width=12, textvariable=self._seed_var)
        seed.pack(side=tk.LEFT)
        tip(seed, "phase3.seed")
        newb = ttk.Button(seed_row, text="New", width=5, command=self._new_seed)
        newb.pack(side=tk.LEFT, padx=(4, 0))
        tip(newb, "phase3.new_seed")
        ttk.Label(root, text="(applied when you arm)").grid(
            row=8, column=1, sticky="w", padx=6)

        self._build_settings_ui(root, row=9)

        ttk.Separator(root, orient="horizontal").grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(8, 4))

        self._status_var = tk.StringVar(value="phase 3: off")
        ttk.Label(root, textvariable=self._status_var, anchor="w",
                  wraplength=460, justify="left").grid(
            row=12, column=0, columnspan=2, sticky="w", **pad)

        # In-tab progress visualization (docs/decisions/0051): a compact ribbon of
        # the current trial + neighbours, and a scrollable list of all N planned
        # trials with the current one highlighted. Both are read-only views of the
        # published table snapshot (self._state), refreshed on the Tk poll.
        self._build_progress_ui(root, row=13)

    def _build_settings_ui(self, root: ttk.Frame, *, row: int) -> None:
        """Read-only sequence/tones readout (edited by hand in phase3.json) plus
        Save/Reload buttons for the operator reward + cue settings (FW-0024,
        ADR-0070). Scientific schedule params stay file-only so they aren't
        fat-fingered mid-session."""
        box = ttk.LabelFrame(root, text="Sequence & tones (edit phase3.json)")
        box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=3)
        box.columnconfigure(0, weight=1)
        self._seq_info_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._seq_info_var, foreground="#555",
                  wraplength=440, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        tip(box, "phase3.sequence_config")
        self._refresh_seq_info()
        self._build_debias_ui(box, row=1)

        btns = ttk.Frame(root)
        btns.grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        save = ttk.Button(btns, text="Save settings", command=self._save_settings)
        save.pack(side=tk.LEFT)
        tip(save, "phase3.save_settings")
        reload_btn = ttk.Button(btns, text="Reload phase3.json",
                                command=self._reload_settings)
        reload_btn.pack(side=tk.LEFT, padx=(6, 0))
        tip(reload_btn, "phase3.reload_config")

    def _build_spin_grid(self, parent: ttk.Frame, spinspec) -> list:
        """Lay a list of ``(label, var, tooltip, lo, hi, step, is_float)`` knobs out
        in a compact 2-column spinbox grid; return the created Spinbox widgets (so
        the caller can enable/disable them as a group)."""
        boxes = []
        for i, (label, var, tkey, lo, hi, step, is_float) in enumerate(spinspec):
            r = i // 2
            c = (i % 2) * 2
            ttk.Label(parent, text=label).grid(
                row=r, column=c, sticky="w", padx=(0, 4), pady=2)
            sb = ttk.Spinbox(parent, from_=lo, to=hi, increment=step, width=7,
                             textvariable=var,
                             format=("%.2f" if is_float else "%.0f"))
            sb.grid(row=r, column=c + 1, sticky="w", padx=(0, 12), pady=2)
            tip(sb, tkey)
            boxes.append(sb)
        return boxes

    def _build_debias_ui(self, parent: ttk.Frame, *, row: int) -> None:
        """The GUI-editable slice of the otherwise file-only ``sequence`` section:
        the trial-sequence generator + the closed-loop intervention-block params
        (ADR-0070 carve-out; see debias.py / alternation.py). The generator dropdown
        picks ``balanced`` (counterbalanced, unchanged), ``debias_blocks``
        (counterbalanced + side-bias corrective blocks), or ``intervention_blocks``
        (counterbalanced + the two ENABLED interventions run concurrently, one active
        at a time). The two enable checkboxes + the per-block spinboxes are live only
        where they apply. All apply on the NEXT arm (like the seed) and are saved to
        phase3.json by Save settings."""
        s = self._config["sequence"]
        dc = DebiasBlockConfig()
        ac = AlternationBlockConfig()
        db = s.get("debias_blocks", {})
        al = s.get("alternation_blocks", {})

        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=(2, 4))
        frame.columnconfigure(0, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="w")
        ttk.Label(top, text="Generator:").grid(
            row=0, column=0, sticky="w", padx=(0, 4), pady=2)
        self._gen_var = tk.StringVar(value=s.get("generator", GEN_BALANCED))
        gen = ttk.Combobox(top, width=18, state="readonly",
                           textvariable=self._gen_var,
                           values=(GEN_BALANCED, GEN_DEBIAS_BLOCKS,
                                   GEN_INTERVENTION_BLOCKS))
        gen.grid(row=0, column=1, sticky="w", pady=2)
        gen.bind("<<ComboboxSelected>>", lambda _e: self._sync_debias_enabled())
        tip(gen, "phase3.generator")

        # The two intervention enable checkboxes (live only under
        # intervention_blocks; each maps 1:1 onto a stack child).
        self._db_enabled_var = tk.BooleanVar(value=bool(db.get("enabled", True)))
        self._alt_enabled_var = tk.BooleanVar(value=bool(al.get("enabled", True)))
        checks = ttk.Frame(frame)
        checks.grid(row=1, column=0, sticky="w", pady=(2, 0))
        dbc = ttk.Checkbutton(checks, text="Side-bias debias",
                              variable=self._db_enabled_var,
                              command=self._sync_debias_enabled)
        dbc.grid(row=0, column=0, sticky="w", padx=(0, 12))
        tip(dbc, "phase3.debias_enabled")
        altc = ttk.Checkbutton(checks, text="De-alternation",
                               variable=self._alt_enabled_var,
                               command=self._sync_debias_enabled)
        altc.grid(row=0, column=1, sticky="w")
        tip(altc, "phase3.alt_enabled")
        self._intervention_checks = [dbc, altc]

        # Side-bias debias knobs.
        self._db_window_var = tk.StringVar(value=str(db.get("window", dc.window)))
        self._db_min_errors_var = tk.StringVar(
            value=str(db.get("min_errors", dc.min_errors)))
        self._db_trigger_var = tk.StringVar(
            value=str(db.get("trigger_bias", dc.trigger_bias)))
        self._db_exit_var = tk.StringVar(
            value=str(db.get("exit_consecutive", dc.exit_consecutive)))
        self._db_cap_var = tk.StringVar(
            value=str(db.get("max_block_len", dc.max_block_len)))
        self._db_alt_veto_var = tk.StringVar(
            value=str(db.get("alt_veto_rate", dc.alt_veto_rate)))
        db_box = ttk.LabelFrame(frame, text="Side-bias debias block")
        db_box.grid(row=2, column=0, sticky="ew", pady=(4, 2))
        self._debias_spinboxes = self._build_spin_grid(db_box, (
            ("Window (trials):", self._db_window_var, "phase3.debias_window",
             1, 500, 1, False),
            ("Min errors:", self._db_min_errors_var, "phase3.debias_min_errors",
             1, 500, 1, False),
            ("Trigger |bias|:", self._db_trigger_var, "phase3.debias_trigger_bias",
             0.0, 1.0, 0.05, True),
            ("Exit (consec. correct):", self._db_exit_var,
             "phase3.debias_exit_consecutive", 1, 20, 1, False),
            ("Max block length:", self._db_cap_var, "phase3.debias_max_block_len",
             1, 200, 1, False),
            ("Alt veto rate:", self._db_alt_veto_var, "phase3.debias_alt_veto_rate",
             0.0, 1.0, 0.05, True),
        ))

        # De-alternation knobs.
        self._alt_window_var = tk.StringVar(value=str(al.get("window", ac.window)))
        self._alt_min_samples_var = tk.StringVar(
            value=str(al.get("min_samples", ac.min_samples)))
        self._alt_trigger_var = tk.StringVar(
            value=str(al.get("trigger_rate", ac.trigger_rate)))
        self._alt_exit_repeats_var = tk.StringVar(
            value=str(al.get("exit_repeats_per_side", ac.exit_repeats_per_side)))
        self._alt_cap_var = tk.StringVar(
            value=str(al.get("max_block_len", ac.max_block_len)))
        al_box = ttk.LabelFrame(frame, text="De-alternation block")
        al_box.grid(row=3, column=0, sticky="ew", pady=(4, 2))
        self._alt_spinboxes = self._build_spin_grid(al_box, (
            ("Window (trials):", self._alt_window_var, "phase3.alt_window",
             1, 500, 1, False),
            ("Min samples:", self._alt_min_samples_var, "phase3.alt_min_samples",
             1, 500, 1, False),
            ("Trigger rate:", self._alt_trigger_var, "phase3.alt_trigger_rate",
             0.0, 1.0, 0.05, True),
            ("Exit repeats/side:", self._alt_exit_repeats_var,
             "phase3.alt_exit_repeats_per_side", 1, 200, 1, False),
            ("Max block length:", self._alt_cap_var, "phase3.alt_max_block_len",
             1, 200, 1, False),
        ))

        # Non-blocking caution: the reward-incorrect shaping aid defeats any block.
        self._reward_incorrect_warn_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self._reward_incorrect_warn_var,
                  foreground="#b00", wraplength=440, justify="left").grid(
            row=4, column=0, sticky="w", pady=(2, 0))
        self._sync_debias_enabled()

    def _build_reward_ui(self, root: ttk.Frame, *, row: int) -> None:
        """Reward controls: the correct-choice prime pulse width, the number of
        lick top-ups at the well, and a live dispensed-volume estimate (ADR-0069).
        Pulse width + top-ups apply on the next arm (like the seed)."""
        box = ttk.LabelFrame(root, text="Reward (central well)")
        box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=6, pady=3)

        ttk.Label(box, text="Pulse (ms):").grid(
            row=0, column=0, sticky="w", padx=6, pady=3)
        self._pulse_var = tk.StringVar(value=str(self._config["reward"]["pulse_ms"]))
        pulse = ttk.Spinbox(box, from_=10, to=2000, increment=10, width=7,
                            textvariable=self._pulse_var,
                            command=self._refresh_reward_readout)
        pulse.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        tip(pulse, "phase3.reward_pulse_ms")

        ttk.Label(box, text="Max top-ups (licks):").grid(
            row=0, column=2, sticky="w", padx=6, pady=3)
        self._topups_var = tk.StringVar(value=str(self._config["reward"]["max_topups"]))
        topups = ttk.Spinbox(box, from_=0, to=20, increment=1, width=5,
                             textvariable=self._topups_var,
                             command=self._refresh_reward_readout)
        topups.grid(row=0, column=3, sticky="w", padx=6, pady=3)
        tip(topups, "phase3.max_topups")

        self._reward_vol_var = tk.StringVar(value="")
        ttk.Label(box, textvariable=self._reward_vol_var, foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 3))
        self._refresh_reward_readout()

    def _refresh_reward_readout(self) -> None:
        """Update the calibrated per-trial reward-volume estimate from the pulse +
        top-up fields (ADR-0019/0069). Runs on the Tk thread (spinbox command +
        poll), so it never touches the actuation thread's tallies."""
        pulse_ms, source, daily = self._resolve_reward_pulse()
        per = reward_volume_ul(pulse_ms)
        pulses = 1 + self._current_max_topups()
        text = (f"\u2248 {format_volume(per)}/pulse  \u00b7  up to {pulses} pulses/trial "
                f"= {format_volume(per * pulses)}")
        # A per-animal volume (docs/decisions/0191) overrides the field: say so,
        # with the width the rig will actually arm with.
        if source != session_cap.SOURCE_CONFIG:
            what = (f"daily {daily:g} mL" if source == session_cap.SOURCE_DAILY_WATER
                    else f"reward_ul {reward_volume_ul(pulse_ms):.0f} uL")
            text += (f"   [per-animal: {what} in animals.json -> {pulse_ms} ms; "
                     f"the Pulse field is ignored]")
        self._reward_vol_var.set(text)

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
            self._refresh_reward_readout()
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
        _cue_value, cue_source, cue_overrides = self._resolve_cue_mapping()
        pulse_ms, pulse_source, daily_water_ml = self._resolve_reward_pulse()
        self._act_snapshot = _Phase3Snapshot(
            gate_open=self._gate_open(),
            params=self._current_params(),
            seed=self._current_seed(),
            reward_incorrect=bool(self._reward_incorrect_var.get()),
            reward_pulse_ms=pulse_ms,
            reward_pulse_source=pulse_source,
            daily_water_ml=daily_water_ml,
            max_topups=self._current_max_topups(),
            reward_port=int(self._config["reward"]["port"]),
            trial_config=self._current_trial_config(),
            cue_mapping_source=cue_source,
            cue_mapping_overrides=cue_overrides,
            animal=self._current_animal(),
            session_id=self._session_id,
            session_out_dir=self._session_out_dir,
            session_t0=self._session_t0,
            cap_t0=self._cap_t0,
            paused=self._paused,
            session_cap=self._current_session_cap(),
            disarm_count=self._disarm_count)

    def _on_enable_write(self, *_args) -> None:
        """Arm-checkbox trace: count each untick (docs/decisions/0210)."""
        try:
            if not self._enable_var.get():
                self._disarm_count += 1
        except tk.TclError:
            pass

    def _current_session_cap(self) -> SessionCapConfig:
        """The session cap from the loaded phase3.json (docs/decisions/0190);
        the built-in default if the section is malformed (load already surfaced
        the error), so the poll never crashes."""
        try:
            return to_session_cap_config(self._config)
        except Exception:
            return SessionCapConfig()

    def _current_animal(self) -> str:
        """Current animal id from the Task tab's shared field (docs/decisions/
        0053); "" when no provider is wired."""
        if self._animal_id is None:
            return ""
        try:
            return self._animal_id().strip()
        except Exception:
            return ""

    def _cue_registry(self):
        """The canonical animal registry, loaded once and cached (best-effort;
        an absent/malformed resources repo yields an empty registry, ADR-0075).
        A registry injected at construction is used as-is (tests)."""
        if self._animal_registry is None:
            try:
                self._animal_registry = animals.load_registry()
            except Exception:
                self._animal_registry = animals.AnimalRegistry()
        return self._animal_registry

    def _resolve_cue_mapping(self):
        """Resolve the cue->side mapping for the CURRENT animal (ADR-0186).

        ``animals.json`` (schema v3) is authoritative when it records a per-animal
        ``high_tone_is_left``; otherwise the rig falls back to ``phase3.json``'s
        value. Returns ``(resolved_value, source, overrides_config)`` where
        ``source`` is ``"animals.json"`` or ``"config"`` and ``overrides_config``
        is True when a per-animal value DISAGREED with phase3.json (so the operator
        can be shown the override). None (unset) is NOT the same as False - only a
        real per-animal bool overrides the config default."""
        config_default = bool(self._config["sequence"].get("high_tone_is_left", True))
        reg = self._cue_registry()
        per_animal = (reg.high_tone_is_left(self._current_animal())
                      if reg is not None else None)
        if per_animal is None:
            return config_default, "config", False
        return per_animal, "animals.json", (per_animal != config_default)

    def resolved_cue_mapping(self) -> dict:
        """The resolved cue->side mapping + its source, for the session manifest's
        provenance block (ADR-0186/0180). Read on the Tk thread at Session Start."""
        value, source, _overrides = self._resolve_cue_mapping()
        return {"high_tone_is_left": value, "source": source}

    def resolved_session_cap(self) -> dict:
        """The session cap the run is armed under (docs/decisions/0190), for the
        manifest provenance + summary: ``{enabled, max_trials, max_minutes}``.
        Read on the Tk thread at Start / Stop."""
        cap = self._current_session_cap()
        return {"enabled": bool(cap.enabled), "max_trials": int(cap.max_trials),
                "max_minutes": float(cap.max_minutes)}

    def _resolve_reward_pulse(self):
        """Resolve the correct-choice prime pulse width for the CURRENT animal
        (docs/decisions/0191) -> ``(pulse_ms, source, daily_water_ml)``.

        Priority: the animal's explicit ``reward_ul`` in animals.json (v4),
        inverted through the calibration curve and rounded to ``PULSE_MS_STEP``;
        else its ``daily_water_ml`` -> a per-reward volume via
        ``session_cap.reward_ul_for_target`` under the session cap, likewise
        inverted; else the tab's Pulse field (phase3.json's value). The daily
        target is returned whichever branch sized the reward, for the water-target
        readout. A volume the curve can't invert (no calibration) falls back to
        the field too, so the rig always has a positive width. Runs on the Tk poll
        like the cue mapping."""
        field_ms = self._current_pulse_ms()
        reg = self._cue_registry()
        animal = self._current_animal()
        daily = reg.daily_water_ml(animal) if reg is not None else None
        explicit = reg.reward_ul(animal) if reg is not None else None
        try:
            ul, source = session_cap.resolve_reward_ul(
                daily, explicit, self._current_session_cap())
        except ValueError:
            return field_ms, session_cap.SOURCE_CONFIG, daily
        if ul is None:
            return field_ms, session_cap.SOURCE_CONFIG, daily
        ms = pulse_ms_for_volume(ul, port=int(self._config["reward"]["port"]))
        if ms <= 0.0:
            return field_ms, session_cap.SOURCE_CONFIG, daily
        return round_pulse_ms(ms), source, daily

    def resolved_reward_policy(self) -> dict:
        """The reward volume policy the run is armed under (docs/decisions/0191),
        for the manifest provenance: ``{pulse_ms, source, daily_water_ml,
        reward_ul}`` (the last two None when unset). Read on the Tk thread."""
        pulse_ms, source, daily = self._resolve_reward_pulse()
        reg = self._cue_registry()
        explicit = reg.reward_ul(self._current_animal()) if reg is not None else None
        return {"pulse_ms": int(pulse_ms), "source": source,
                "daily_water_ml": daily, "reward_ul": explicit}

    def water_target(self) -> Optional[dict]:
        """Earned task water (this run's prime + top-up pulses, since arm) vs the
        current animal's daily target (docs/decisions/0191), or None when it has
        no recorded target. Read on the Tk thread at Stop for the summary; the
        target is resolved fresh (not from the last snapshot) so it follows the
        animal id the way the reward width does."""
        _ms, _source, daily = self._resolve_reward_pulse()
        return session_cap.water_target_summary(daily, self._reward_ul)

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
            lb.insert(tk.END, self._seq_line(row, current, state.return_enabled))
        if 0 <= current < lb.size():
            lb.itemconfig(current, background=_CELL_CURRENT_BG)
            lb.see(current)                  # keep the current trial in view

    @staticmethod
    def _seq_line(row, current: int, return_enabled: bool = True) -> str:
        """One monospace planned-trial row for the scrollable list. ``row`` is a
        phase3_log.Phase3RowView; ``correct_arm`` is the arm the choice cue maps
        to (identity checked in test_phase3_ribbon). The return-cue segment is
        dropped when ``return_enabled`` is False (nothing is actually cued)."""
        mark = ">" if (row.trial - 1) == current else " "
        arm = _ARM_SHORT.get(row.correct_arm, "?")
        if row.outcome == ABANDONED:
            result = "skipped"
        elif row.outcome == UNCUED:
            result = "uncued"
        elif row.outcome is not None:
            chosen = _ARM_SHORT.get(row.chosen_arm, "?")
            result = f"chose {chosen} {'OK' if row.outcome == CORRECT else 'X'}"
        else:
            result = ""
        # Tag an unrewarded probe trial (probe_blocks.py); probes are inserted at
        # runtime, so they only appear once committed (docs/decisions/0115-probe).
        tag = " PROBE" if row.probe == "1" else ""
        if return_enabled:
            left = _CUE_SHORT.get(row.return_cue_left, "?")
            right = _CUE_SHORT.get(row.return_cue_right, "?")
            ret = f"ret L:{left} R:{right}"
        else:
            ret = ""
        return (f"{mark}{row.trial:>4}  {row.choice_cue:<4}->{arm}  "
                f"{ret}{tag}   {result}")
