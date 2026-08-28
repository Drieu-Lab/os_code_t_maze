"""
phase3_log.py - the host-owned Phase 3 session data table (pure core).

Builds and maintains the per-session data table for a host-owned Phase 3 run (see
docs/decisions/0046, 0047): the planned N-row cue matrix generated up front from
the seed, progressively filled with the animal's choices and event timestamps as
the session runs, and rendered to CSV text for the thin controller shell to write
to disk.

Layout (one row per trial ATTEMPT):
  - PLANNED at arm (from the seed-deterministic TrialSequence):
    ``correct_arm`` (LEFT/RIGHT) + the N x3 cue matrix ``choice_cue`` /
    ``return_cue_left`` / ``return_cue_right`` (each HIGH/LOW). ``correct_arm``
    makes the choice-cue -> side mapping explicit per row (the mapping is also in
    the header metadata).
  - FILLED live as the trial resolves: ``chosen_arm``, ``outcome``,
    ``rewarded``, ``return_arm_used``, ``return_cue_played`` and the event
    timestamps (all host wall-clock ISO except ``lick_fw_ms``).

Trial edits never erase behavior (docs/decisions/0119, bug #260810). The live
table stays one row per trial INDEX (so the ribbon/stats/readers key on it
unchanged), but a Repeat/Back on a trial that already recorded behavior does not
clear that row - it ARCHIVES the animal's real attempt (append-only) and starts a
fresh attempt in the live row. ``render_csv`` emits every attempt, so the file is
one row per (trial, attempt): the ``attempt`` column counts 1.. per trial, the
``superseded`` column marks a preserved (replaced) attempt, and the ``edit``
column names the operator action that created a later attempt (``repeat`` /
``back``). Readers that want only the trials that COUNT drop ``superseded`` rows.

Timestamp definitions (the corrected Phase 3 timeline):
  - ``trial_start`` = the first HUB->stem transition after the previous trial end.
  - ``choice_time`` = the choice was scored (rat entered a choice arm up the stem).
  - ``reward_cmd`` = the host fired the reward valve (rewarded trials only).
  - ``trial_end`` = the lap closed back at the hub (this also advances the trial).
  - ``lick_time`` = the FIRST center-well lick AFTER the reward command; because
    that lick lands after ``trial_end`` (index already advanced), it is attributed
    via a pending-reward pointer to the trial whose reward it collects. Blank on
    unrewarded trials or if no lick is seen. ``lick_fw_ms`` is the firmware
    hardware clock for that lick (host ISO times carry serial latency).

Pure and single-threaded (the Phase 3 actuation thread is the sole caller), like
the other cores: no Tk/OpenCV/serial, no file I/O (the controller writes the
rendered text), no locks. Headless tests: python -m unittest test_phase3_log.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any, Dict, List, Optional, Tuple

from tmaze.core.phase3.phase3_task import ABANDONED
from tmaze.core.phase3.trial_sequence import HIGH, LEFT, LOW, RIGHT, TrialSequence

# Row values use these arm labels (uppercased); the sequence uses int LEFT/RIGHT
# and task_events/cue_gate use lowercase "left"/"right", so normalize on the way in.
_ARM_LABEL = {LEFT: "LEFT", RIGHT: "RIGHT", "left": "LEFT", "right": "RIGHT"}

# CSV column order. The planned block is the "Nx3 cue matrix" (choice + the two
# return arms) plus the explicit correct_arm; the rest is filled live.
COLUMNS = (
    "trial", "correct_arm", "choice_cue", "return_cue_left", "return_cue_right",
    "chosen_arm", "outcome", "rewarded", "topups", "return_arm_used",
    "return_cue_played",
    "trial_start_iso", "choice_cue_on_iso", "choice_cue_off_iso",
    "choice_time_iso", "reward_cmd_iso", "return_cue_on_iso",
    "return_cue_off_iso", "trial_end_iso", "lick_time_iso", "lick_fw_ms",
    # Closed-loop intervention context (debias_blocks / intervention_blocks
    # generators; blank for balanced). sched_state = NORMAL/INTERVENTION, block_id =
    # 1-based within a block (blank in NORMAL), block_trigger = auto/manual, bias =
    # signed directional-error bias at this trial's decision, block_kind = which
    # intervention owns the block (side_bias/alternation), alt_rate = cue-aware
    # alternation rate at this trial's decision. See debias.py / alternation.py.
    "sched_state", "block_id", "block_trigger", "bias", "block_kind", "alt_rate",
    # Per-trial reward magnitude (docs/decisions/0110): the RAW prime pulse width
    # (ms) delivered on a rewarded trial, and the SUM of this trial's lick top-up
    # pulse widths (ms). Blank until the corresponding pulse fires. Raw ms only -
    # no calibration in the pure log; volume is computed downstream.
    "prime_ms", "topup_ms_total",
    # Unrewarded PROBE trial marker (docs/decisions/0115-probe): "1" on a probe
    # trial (cue + scoring normal, reward suppressed), blank otherwise. Orthogonal
    # to sched_state (a probe stays NORMAL - it is not an intervention block).
    # ``probe_trigger`` is "1" only on the correct-choice trial that STARTED the
    # block (docs/decisions/0134): it is always correct by construction, so
    # downstream accuracy readouts exclude it while still treating it as a probe.
    "probe", "probe_trigger",
    # Trial-edit lifecycle (docs/decisions/0119, bug #260810): with append-only
    # trial edits the file is one row per (trial, ATTEMPT). ``attempt`` is the
    # 1-based attempt number for that trial; ``superseded`` is "1" on a preserved
    # attempt that a later Repeat/Back replaced (blank on the current attempt);
    # ``edit`` names the operator action that created a >1 attempt ("repeat" /
    # "back"; blank on the natural first attempt). These are lifecycle metadata,
    # not trial data, so they live on the row here rather than in ``_Row``.
    "attempt", "superseded", "edit",
)


@dataclass
class _Row:
    """One trial's row. Planned fields set at construction; the rest fill live.

    Field names match COLUMNS (minus the derived ``trial`` index); ``None`` renders
    as an empty cell.
    """
    correct_arm: str
    choice_cue: str
    return_cue_left: str
    return_cue_right: str
    chosen_arm: Optional[str] = None
    outcome: Optional[str] = None
    rewarded: Optional[str] = None
    # Count of lick-triggered TOP-UP pulses delivered at the well AFTER the prime
    # (the credential prime is the ``rewarded`` 1; top-ups are extras). None until
    # the first top-up, then a stringified count. See docs/decisions/0069.
    topups: Optional[str] = None
    return_arm_used: Optional[str] = None
    return_cue_played: Optional[str] = None
    trial_start_iso: Optional[str] = None
    choice_cue_on_iso: Optional[str] = None
    choice_cue_off_iso: Optional[str] = None
    choice_time_iso: Optional[str] = None
    reward_cmd_iso: Optional[str] = None
    return_cue_on_iso: Optional[str] = None
    return_cue_off_iso: Optional[str] = None
    trial_end_iso: Optional[str] = None
    lick_time_iso: Optional[str] = None
    lick_fw_ms: Optional[str] = None
    # Closed-loop intervention context, set when the trial's side is decided (blank
    # for the balanced generator). See :meth:`Phase3Log.set_decision`.
    sched_state: Optional[str] = None
    block_id: Optional[str] = None
    block_trigger: Optional[str] = None
    bias: Optional[str] = None
    block_kind: Optional[str] = None
    alt_rate: Optional[str] = None
    # Per-trial reward magnitude (docs/decisions/0110): the prime pulse width (ms)
    # and the running SUM of this trial's top-up pulse widths (ms). None until the
    # matching pulse fires. See :meth:`reward_fired` / :meth:`topup_fired`.
    prime_ms: Optional[str] = None
    topup_ms_total: Optional[str] = None
    # Unrewarded probe-trial marker ("1" / None); set in :meth:`set_decision` from
    # the sequence's per-trial metadata (docs/decisions/0115-probe). ``probe_trigger``
    # is "1" only on the correct-choice trial that started the block (docs/decisions/
    # 0134), so it can be excluded from probe accuracy.
    probe: Optional[str] = None
    probe_trigger: Optional[str] = None


@dataclass(frozen=True)
class Phase3RowView:
    """Immutable, decoupled view of one trial row for cross-thread publishing.

    Mirrors :class:`_Row` plus a 1-based ``trial`` index; every field is an
    immutable str/None captured by value, so later mutation of the live ``_Row``
    never changes a published view (docs/decisions/0050)."""
    trial: int
    correct_arm: str
    choice_cue: str
    return_cue_left: str
    return_cue_right: str
    chosen_arm: Optional[str]
    outcome: Optional[str]
    rewarded: Optional[str]
    topups: Optional[str]
    return_arm_used: Optional[str]
    return_cue_played: Optional[str]
    trial_start_iso: Optional[str]
    choice_cue_on_iso: Optional[str]
    choice_cue_off_iso: Optional[str]
    choice_time_iso: Optional[str]
    reward_cmd_iso: Optional[str]
    return_cue_on_iso: Optional[str]
    return_cue_off_iso: Optional[str]
    trial_end_iso: Optional[str]
    lick_time_iso: Optional[str]
    lick_fw_ms: Optional[str]
    sched_state: Optional[str]
    block_id: Optional[str]
    block_trigger: Optional[str]
    bias: Optional[str]
    block_kind: Optional[str]
    alt_rate: Optional[str]
    prime_ms: Optional[str]
    topup_ms_total: Optional[str]
    probe: Optional[str]
    probe_trigger: Optional[str]


@dataclass(frozen=True)
class Phase3StateSnapshot:
    """Immutable freeze of the session table + run cursor, published by the
    controller for other tabs/panels to read off the Tk thread (docs/decisions/
    0050). ``current_index`` is the 0-based trial in progress; ``total`` equals
    ``len(rows)``; ``armed`` is False once a session's gate has closed (the rows
    persist as the last session's table)."""
    rows: Tuple[Phase3RowView, ...]
    current_index: int
    total: int
    armed: bool
    seed: int
    high_tone_is_left: bool
    high_tone_hz: int
    low_tone_hz: int
    # Trial-sequence generator + closed-loop debias context, so panels/stats can
    # render the debias state without re-reading the live sequence. For the
    # balanced generator: generator="balanced", state="NORMAL", bias 0.0, and the
    # threshold/exit/cap fields 0. ``debias_state`` / ``debias_bias`` are the
    # CURRENT scheduler readings at snapshot time.
    generator: str = "balanced"
    debias_state: str = "NORMAL"
    debias_bias: float = 0.0
    trigger_bias: float = 0.0
    exit_consecutive: int = 0
    max_block_len: int = 0
    # Whether the side-bias debias child is enabled this run (a child exists), its
    # auto-trigger evidence gate (``min_errors``) and how many directional errors
    # are CURRENTLY in its window - so a panel can show gate progress (errors N/min)
    # and explain why a high |bias| hasn't yet fired a block. See debias.py.
    debias_enabled: bool = False
    debias_min_errors: int = 0
    debias_window_errors: int = 0
    # Whether the reward-incorrect shaping aid is on this run: with it on every
    # completed trial is rewarded, so the reward rate pins at ~100% and stops
    # tracking accuracy - panels suppress that line rather than show a misleading
    # figure (docs/decisions/0094).
    reward_incorrect: bool = False
    # De-alternation intervention context (intervention_blocks generator with the
    # alternation child enabled; 0/"" otherwise). ``alt_rate`` is the CURRENT
    # cue-aware alternation rate at snapshot time; ``active_kind`` is which
    # intervention owns the running block ("side_bias"/"alternation"; "" in NORMAL);
    # ``alt_max_block_len`` is the alternation block's welfare cap. See
    # alternation.py / intervention_stack.py.
    alt_rate: float = 0.0
    active_kind: str = ""
    alt_max_block_len: int = 0
    # Whether the alternation child is enabled this run (a child exists), its
    # auto-trigger threshold (``trigger_rate``) + evidence gate (``min_samples``)
    # and how many repeat-cued samples are CURRENTLY in its window - so a panel can
    # show gate progress (samples N/min) and explain why a high alt_rate hasn't yet
    # fired a block. See alternation.py.
    alt_enabled: bool = False
    alt_trigger_rate: float = 0.0
    alt_min_samples: int = 0
    alt_window_samples: int = 0
    # Unrewarded probe-block scheduler context (probe_blocks.py). ``probe_enabled``
    # is whether probe blocks are available this run (the Rig-view on/off toggle is
    # live only then); ``probe_state`` is the CURRENT probe state at snapshot time
    # ("ACTIVE" while a probe block runs / "NORMAL"); ``probe_armed`` is whether the
    # correct-choice trigger is currently live (False after an operator mid-session
    # disable - docs/decisions/0137; drives the toggle label + the [PROBE OFF] tag).
    probe_enabled: bool = False
    probe_state: str = "NORMAL"
    probe_armed: bool = True
    # Whether the return-arm cue is played this run (CueParams.return_enabled). The
    # planned return-cue columns are always generated/recorded, so panels use this
    # to suppress the return-cue readout when it is off (nothing is actually cued).
    # See docs/decisions/0141.
    return_enabled: bool = True


@dataclass(frozen=True)
class SupersedeResult:
    """What :meth:`Phase3Log.supersede_trial` archived, so the shell can reconcile
    its live tallies (docs/decisions/0119). ``outcome`` is the archived attempt's
    scored outcome (CORRECT/INCORRECT/ABANDONED/None) and ``probe`` whether it was
    a probe trial that COUNTED toward the probe tally (i.e. a block trial, not the
    always-correct trigger, which is excluded from probe accuracy; docs/decisions/
    0134). Only returned when an attempt with recorded behavior was actually
    preserved; None means nothing was archived (an empty row)."""
    outcome: Optional[str]
    probe: bool


# The planned cue columns that a trial edit must KEEP (they belong to the trial's
# identity, not its live behavior); everything else on ``_Row`` is cleared for a
# fresh attempt. Named once so :meth:`Phase3Log._clear_live` and
# :meth:`Phase3Log._has_behavior` agree on what "behavior" means.
_PLANNED_FIELDS = ("correct_arm", "choice_cue", "return_cue_left",
                   "return_cue_right")


class Phase3Log:
    """The per-session Phase 3 data table (plan + live outcomes)."""

    def __init__(self, sequence: TrialSequence, *, animal: str,
                 session_start_iso: str, reward_incorrect: bool = False,
                 reward_pulse_ms: int = 500, max_topups: int = 0,
                 session_id: str = "",
                 session_t0: Optional[Dict[str, Any]] = None,
                 return_enabled: bool = True) -> None:
        self.animal = animal
        self.seed = sequence.seed
        self.cfg = sequence.cfg
        # Whether the return-arm cue is played this run (a display hint for panels;
        # the planned return-cue columns are recorded regardless). See snapshot().
        self.return_enabled = return_enabled
        # Kept so a snapshot can read the CURRENT closed-loop debias state (state +
        # bias) for panels; the snapshot copies those readings out by value.
        self._sequence = sequence
        # Gate-arm time (the gate rising edge that started the task; ADR-0060). May
        # precede the canonical session anchor below.
        self.session_start_iso = session_start_iso
        # Canonical session anchor: the manifest t0 (host wall+mono+iso captured at
        # Start Session), stamped in on rebind so this file is self-anchoring to the
        # one instant every stream shares (docs/decisions/0124). None on a manual
        # arm (no coordinated session) or before Start Session.
        self.session_t0 = session_t0
        self.reward_incorrect = reward_incorrect
        # Reward dispensing params used this run (ADR-0069), recorded in the header
        # for reproducibility (FW-0024): the correct-choice prime pulse width and
        # the max lick top-ups allowed at the well.
        self.reward_pulse_ms = reward_pulse_ms
        self.max_topups = max_topups
        # Shared session id when armed via the Session controller (docs/decisions/
        # 0048); links this file to the video + manifest. Empty on a manual arm.
        self.session_id = session_id
        # Build the planned rows from the seed-deterministic schedules.
        self._rows: List[_Row] = [
            _Row(
                correct_arm=_ARM_LABEL[sequence.correct_arm(i)],
                choice_cue=sequence.choice_cue(i) or "",
                return_cue_left=sequence.return_cue(LEFT, i) or "",
                return_cue_right=sequence.return_cue(RIGHT, i) or "",
            )
            for i in range(len(sequence))
        ]
        # The trial whose reward is still awaiting its collection lick (see module
        # docstring); the lick lands after that trial has already advanced.
        self._pending_lick_index: Optional[int] = None
        # Trial-edit lifecycle (docs/decisions/0119). Parallel to ``_rows``: the
        # current attempt number and the operator action that created it per trial
        # index. ``_archive`` holds preserved (superseded) attempts as
        # (index, attempt, edit, row-copy), append-only, rendered alongside the
        # live rows so a Repeat/Back never erases the animal's real behavior.
        self._attempt: List[int] = [1] * len(self._rows)
        self._edit: List[str] = [""] * len(self._rows)
        self._archive: List[Tuple[int, int, str, _Row]] = []

    # ------------------------------------------------------------- helpers
    def __len__(self) -> int:
        return len(self._rows)

    def _row(self, index: int) -> Optional[_Row]:
        return self._rows[index] if 0 <= index < len(self._rows) else None

    # --------------------------------------------------------- live events
    def trial_started(self, index: int, iso: str) -> None:
        """First HUB->stem transition of a trial (keep the first; later pokes ignored)."""
        r = self._row(index)
        if r is not None and r.trial_start_iso is None:
            r.trial_start_iso = iso

    def _apply_cue(self, index: int, r: _Row) -> None:
        """Point a row's correct-arm + choice-cue at the sequence's current reading
        for ``index`` (committed side for a decided trial, else the aligned
        provisional side; see TrialSequence). Display columns only - never the
        debias-context columns, which stay blank until a trial is actually decided.
        """
        arm = self._sequence.correct_arm(index)
        if arm is not None:
            r.correct_arm = _ARM_LABEL.get(arm, r.correct_arm)
        cue = self._sequence.choice_cue(index)
        if cue:
            r.choice_cue = cue

    def set_decision(self, index: int) -> None:
        """Refresh a trial's realized correct side + choice cue + debias context
        from the sequence, once its side has been committed (the closed-loop
        decision point). No-op for the balanced generator, whose planned columns
        are already final. Idempotent - safe to call on every trial start / re-run.
        """
        if not self._sequence.is_debias:
            return
        r = self._row(index)
        if r is None:
            return
        self._apply_cue(index, r)
        m = self._sequence.trial_meta(index)
        r.sched_state = m["sched_state"]
        r.block_id = None if not m["block_id"] else str(m["block_id"])
        r.block_trigger = m["block_trigger"] or None
        r.bias = f"{m['bias']:.4f}"
        r.block_kind = m.get("block_kind") or None
        alt = m.get("alt_rate")
        r.alt_rate = None if alt is None else f"{alt:.4f}"
        r.probe = "1" if m.get("probe") else None
        r.probe_trigger = "1" if m.get("probe_trigger") else None

    def refresh_provisional(self, current_index: int) -> None:
        """Re-align the DISPLAY cue columns of not-yet-scored trials from
        ``current_index`` onward to the sequence's current knowledge, so the ribbon
        and planned-trial list stop showing stale cues after a corrective block
        shifts the schedule (the ribbon display bug; docs/decisions/0096).

        A committed trial (the one in progress) gets its finalized side + debias
        context; each un-committed trial ahead gets the aligned provisional side
        only (its debias-context columns stay blank until it is actually decided).
        Already-scored rows are left untouched. No-op for the balanced generator.
        """
        if not self._sequence.is_debias:
            return
        for i in range(max(0, current_index), len(self._rows)):
            r = self._row(i)
            if r is None or r.outcome is not None:
                continue
            if self._sequence.is_committed(i):
                self.set_decision(i)
            else:
                self._apply_cue(i, r)

    def choice_cue_started(self, index: int, iso: str) -> None:
        r = self._row(index)
        if r is not None and r.choice_cue_on_iso is None:
            r.choice_cue_on_iso = iso

    def choice_cue_stopped(self, index: int, iso: str) -> None:
        r = self._row(index)
        if r is not None:
            r.choice_cue_off_iso = iso

    def choice_scored(self, index: int, chosen_arm: str, outcome: str,
                      iso: str) -> None:
        """Record the scored choice. Defaults ``rewarded`` to 0 (a later
        :meth:`reward_fired` promotes it to 1)."""
        r = self._row(index)
        if r is None:
            return
        r.chosen_arm = _ARM_LABEL.get(chosen_arm, chosen_arm)
        r.outcome = outcome
        r.choice_time_iso = iso
        if r.rewarded is None:
            r.rewarded = "0"

    def reward_fired(self, index: int, iso: str,
                     pulse_ms: Optional[int] = None) -> None:
        """Record the correct-choice prime fire. ``pulse_ms`` (when supplied by the
        shell) records the RAW prime pulse width for the per-trial reward magnitude
        (docs/decisions/0110); omitted -> the ``prime_ms`` cell stays blank (a
        pre-magnitude reader/caller)."""
        r = self._row(index)
        if r is None:
            return
        r.rewarded = "1"
        r.reward_cmd_iso = iso
        if pulse_ms is not None:
            r.prime_ms = str(int(pulse_ms))
        self._pending_lick_index = index      # this trial now awaits its lick

    def topup_fired(self, index: int, pulse_ms: Optional[int] = None) -> None:
        """Record one lick-triggered top-up pulse at the well for ``index`` (an
        extra beyond the prime; docs/decisions/0069). Increments the trial's
        running top-up count and, when ``pulse_ms`` is supplied, its running SUM of
        top-up pulse widths for the reward-magnitude readout (docs/decisions/0110)."""
        r = self._row(index)
        if r is None:
            return
        cur = int(r.topups) if r.topups is not None else 0
        r.topups = str(cur + 1)
        if pulse_ms is not None:
            prev = int(r.topup_ms_total) if r.topup_ms_total is not None else 0
            r.topup_ms_total = str(prev + int(pulse_ms))

    def return_cue_started(self, index: int, arm: str, cue: Optional[str],
                           iso: str) -> None:
        r = self._row(index)
        if r is None:
            return
        r.return_arm_used = _ARM_LABEL.get(arm, arm)
        r.return_cue_played = cue if cue is not None else "NONE"
        r.return_cue_on_iso = iso

    def return_cue_stopped(self, index: int, iso: str) -> None:
        r = self._row(index)
        if r is not None:
            r.return_cue_off_iso = iso

    def trial_ended(self, index: int, iso: str) -> None:
        r = self._row(index)
        if r is not None:
            r.trial_end_iso = iso

    def trial_abandoned(self, index: int, iso: str) -> None:
        """Mark a trial the operator abandoned mid-run (Skip / Repeat buttons;
        docs/decisions/0058). Records the distinct ``ABANDONED`` outcome and the
        end time, but only if the trial was not already scored - so a genuine
        CORRECT/INCORRECT is never overwritten by a stray abandon."""
        r = self._row(index)
        if r is None or r.outcome is not None:
            return
        r.outcome = ABANDONED
        if r.trial_end_iso is None:
            r.trial_end_iso = iso

    def _clear_live(self, r: _Row) -> None:
        """Reset a row's live (filled) fields to unrecorded, keeping its planned
        cue columns, so it can be re-run cleanly."""
        for f in fields(_Row):
            if f.name not in _PLANNED_FIELDS:
                setattr(r, f.name, None)

    def _has_behavior(self, r: _Row) -> bool:
        """True if a row recorded ANY live behavior (a scored outcome, an event
        timestamp, a chosen arm, ...), i.e. there is something worth preserving
        before a trial edit clears it."""
        return any(getattr(r, f.name) is not None
                   for f in fields(_Row) if f.name not in _PLANNED_FIELDS)

    def reset_trial(self, index: int) -> None:
        """Clear a trial's live (filled) fields back to unrecorded, keeping its
        planned cue columns, so it can be re-run cleanly. Also drops a pending
        reward-lick pointer aimed at it, so a stale lick can't attach to the reset
        row.

        This is the DESTRUCTIVE clear (it does not preserve the row). The Repeat /
        Back buttons now go through :meth:`supersede_trial`, which archives the
        real attempt first (docs/decisions/0119); this stays for callers/tests
        that want a plain clear."""
        r = self._row(index)
        if r is None:
            return
        self._clear_live(r)
        if self._pending_lick_index == index:
            self._pending_lick_index = None

    def supersede_trial(self, index: int, edit: str) -> Optional[SupersedeResult]:
        """Preserve the animal's real attempt for ``index`` and open a fresh one
        for a re-run, instead of erasing it (the Repeat / Back buttons; bug
        #260810, docs/decisions/0119).

        If the live row recorded any behavior, a copy is appended to the archive
        (marked superseded, tagged with the current attempt number and the edit
        that preceded it), the live row's attempt counter is bumped, and its
        ``edit`` is set to ``edit`` (the action that created the NEW attempt);
        then the live row is cleared for the re-run. A row with nothing recorded
        yet is just cleared (nothing to preserve). Returns a
        :class:`SupersedeResult` describing what was archived (so the shell can
        reconcile its accuracy tallies), or None when nothing was preserved.
        """
        r = self._row(index)
        if r is None:
            return None
        result: Optional[SupersedeResult] = None
        if self._has_behavior(r):
            self._archive.append(
                (index, self._attempt[index], self._edit[index], replace(r)))
            result = SupersedeResult(
                outcome=r.outcome,
                probe=(r.probe == "1" and r.probe_trigger != "1"))
            self._attempt[index] += 1
            self._edit[index] = edit
        self._clear_live(r)
        if self._pending_lick_index == index:
            self._pending_lick_index = None
        return result

    def extend_to(self, sequence: TrialSequence) -> int:
        """Append planned rows for trials added to ``sequence`` (append-only).

        Called after :meth:`TrialSequence.extend` when the rat runs past the
        planned count (docs/decisions/0059). Existing rows - including every
        already-recorded outcome and timestamp - are left untouched; only the new
        indices get fresh planned rows. Returns the number of rows appended."""
        start = len(self._rows)
        for i in range(start, len(sequence)):
            self._rows.append(_Row(
                correct_arm=_ARM_LABEL[sequence.correct_arm(i)],
                choice_cue=sequence.choice_cue(i) or "",
                return_cue_left=sequence.return_cue(LEFT, i) or "",
                return_cue_right=sequence.return_cue(RIGHT, i) or "",
            ))
            self._attempt.append(1)          # keep the edit-lifecycle lists aligned
            self._edit.append("")
        return len(self._rows) - start

    def lick(self, iso: str, fw_ms: Optional[int]) -> bool:
        """A center-well lick. Attributed to the trial awaiting its reward lick;
        ignored (no pending reward) otherwise, and only the first lick counts.
        Returns True iff it was recorded (so the shell can skip a needless write)."""
        idx = self._pending_lick_index
        r = self._row(idx) if idx is not None else None
        if r is None or r.lick_time_iso is not None:
            return False
        r.lick_time_iso = iso
        r.lick_fw_ms = "" if fw_ms is None else str(fw_ms)
        self._pending_lick_index = None
        return True

    # ----------------------------------------------------------- snapshot
    def snapshot(self, current_index: int, *, armed: bool) -> Phase3StateSnapshot:
        """Immutable freeze of the whole table for cross-thread publishing
        (docs/decisions/0050). Copies each row by value, so the returned snapshot
        is decoupled from later mutation of the live rows. ``current_index`` is
        the 0-based trial in progress; ``armed`` records whether a session's gate
        is still open."""
        names = [f.name for f in fields(_Row)]
        rows = tuple(
            Phase3RowView(trial=i + 1, **{n: getattr(r, n) for n in names})
            for i, r in enumerate(self._rows)
        )
        c = self.cfg
        db = getattr(c, "debias", None)
        al = getattr(c, "alternation", None)
        seq = self._sequence
        return Phase3StateSnapshot(
            rows=rows, current_index=current_index, total=len(self._rows),
            armed=armed, seed=self.seed, high_tone_is_left=c.high_tone_is_left,
            high_tone_hz=c.high_tone_hz, low_tone_hz=c.low_tone_hz,
            generator=c.generator, debias_state=seq.debias_state(),
            debias_bias=seq.debias_bias(),
            trigger_bias=(db.trigger_bias if db is not None else 0.0),
            exit_consecutive=(db.exit_consecutive if db is not None else 0),
            max_block_len=(db.max_block_len if db is not None else 0),
            debias_enabled=(db is not None),
            debias_min_errors=(db.min_errors if db is not None else 0),
            debias_window_errors=seq.debias_errors(),
            reward_incorrect=self.reward_incorrect,
            alt_rate=seq.alt_rate(), active_kind=seq.active_kind(),
            alt_max_block_len=(al.max_block_len if al is not None else 0),
            alt_enabled=(al is not None),
            alt_trigger_rate=(al.trigger_rate if al is not None else 0.0),
            alt_min_samples=(al.min_samples if al is not None else 0),
            alt_window_samples=seq.alt_samples(),
            probe_enabled=seq.probe_enabled, probe_state=seq.probe_state(),
            probe_armed=seq.probe_armed,
            return_enabled=self.return_enabled)

    # ------------------------------------------------------------- render
    def _header_comment(self) -> List[str]:
        c = self.cfg
        lines = [
            f"# phase3_session animal={self.animal} session_id={self.session_id} "
            f"seed={self.seed} proto=10",
            f"# high_tone_is_left={c.high_tone_is_left} "
            f"high_tone_hz={c.high_tone_hz} low_tone_hz={c.low_tone_hz}",
            f"# block_size={c.block_size} max_same_run={c.max_same_run} "
            f"max_alt_run={c.max_alt_run} "
            f"max_trials={c.max_trials} planned_trials={len(self._rows)} "
            f"reward_incorrect={self.reward_incorrect} "
            f"reward_pulse_ms={self.reward_pulse_ms} max_topups={self.max_topups}",
            f"# generator={c.generator}",
        ]
        # For a closed-loop debias session, record its params and the caveat that
        # the seed ALONE no longer reproduces the schedule (it also depends on the
        # animal's realized choices + any operator overrides). See debias.py.
        db = getattr(c, "debias", None)
        al = getattr(c, "alternation", None)
        if c.generator != "balanced" and (db is not None or al is not None):
            if db is not None:
                lines.append(
                    f"# debias_blocks window={db.window} "
                    f"min_errors={db.min_errors} trigger_bias={db.trigger_bias} "
                    f"exit_consecutive={db.exit_consecutive} "
                    f"max_block_len={db.max_block_len} signal={db.signal}")
            if al is not None:
                lines.append(
                    f"# alternation_blocks window={al.window} "
                    f"min_samples={al.min_samples} trigger_rate={al.trigger_rate} "
                    f"exit_repeats_per_side={al.exit_repeats_per_side} "
                    f"max_block_len={al.max_block_len} signal={al.signal}")
            lines.append(
                "# note: intervention schedule is closed-loop - seed alone does NOT "
                "reproduce it; it also depends on realized choices + overrides")
        # Unrewarded probe-block plan (probe_blocks.py; docs/decisions/0115/0134).
        # Recorded so the data file is self-describing about whether a probe was
        # planned, and - crucially - so a reader can tell a session the operator
        # DISABLED mid-session (docs/decisions/0137) apart from one where a window
        # simply never had a qualifying correct trial. The archived config alone
        # can't show that (it still says enabled). Precise on/off timing lives in
        # the session notes/summary; keys are ``probe_``-prefixed so the offline
        # header parser reads them unambiguously.
        pb = getattr(c, "probe", None)
        if pb is not None and pb.enabled:
            win = ",".join(f"{int(s)}-{int(e)}" for s, e in pb.windows) or "none"
            lines.append(
                f"# probe_blocks probe_enabled=True "
                f"probe_block_len={pb.block_len} probe_windows={win}")
            if self._sequence.probe_ever_disarmed:
                lines.append(
                    "# note: probe blocks were manually DISABLED by the operator "
                    "during this session - a planned window may not have fired; "
                    "see the session notes/summary for timing")
        if self.session_t0:
            lines.append(
                f"# session_t0_iso={self.session_t0.get('iso', '')} "
                f"session_t0_wall={self.session_t0.get('wall', '')} "
                f"session_t0_mono={self.session_t0.get('mono', '')}")
        lines += [
            f"# session_start_iso={self.session_start_iso}",
            "# note: session_t0_* is the canonical session anchor (Start Session; "
            "host wall+mono+iso) shared by every stream; session_start_iso is the "
            "gate-arm time (may precede t0). see docs/decisions/0124",
            "# note: *_iso are host actuation-decision times (serial latency ~ms); "
            "lick_fw_ms is the firmware hardware clock. see docs/decisions/0047",
        ]
        return lines

    def render_csv(self) -> str:
        """Render the whole table (header comment + column row + trials) as CSV
        text. Cheap enough to re-render on every update (a few hundred rows).

        One row per (trial, ATTEMPT): the current live attempt of each trial plus
        every preserved (superseded) attempt from the archive, ordered by
        (trial, attempt) so a reader sees a trial's attempts together with the
        current one last (docs/decisions/0119)."""
        data_fields = [f.name for f in fields(_Row)]
        # (trial_no, attempt, superseded, edit, row): the current live attempts...
        entries: List[Tuple[int, int, str, str, _Row]] = [
            (i + 1, self._attempt[i], "", self._edit[i], r)
            for i, r in enumerate(self._rows)
        ]
        # ...plus every preserved attempt (marked superseded).
        entries += [(idx + 1, attempt, "1", edit, r)
                    for (idx, attempt, edit, r) in self._archive]
        entries.sort(key=lambda e: (e[0], e[1]))

        lines = list(self._header_comment())
        lines.append(",".join(COLUMNS))
        for trial_no, attempt, superseded, edit, r in entries:
            cells = [str(trial_no)] + [
                ("" if getattr(r, name) is None else str(getattr(r, name)))
                for name in data_fields
            ]
            cells += [str(attempt), superseded, edit or ""]
            lines.append(",".join(cells))
        return "\n".join(lines) + "\n"
