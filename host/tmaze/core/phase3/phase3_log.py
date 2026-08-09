"""
phase3_log.py - the host-owned Phase 3 session data table (pure core).

Builds and maintains the per-session data table for a host-owned Phase 3 run (see
docs/decisions/0046, 0047): the planned N-row cue matrix generated up front from
the seed, progressively filled with the animal's choices and event timestamps as
the session runs, and rendered to CSV text for the thin controller shell to write
to disk.

Layout (one row per trial):
  - PLANNED at arm (from the seed-deterministic TrialSequence):
    ``correct_arm`` (LEFT/RIGHT) + the N x3 cue matrix ``choice_cue`` /
    ``return_cue_left`` / ``return_cue_right`` (each HIGH/LOW). ``correct_arm``
    makes the choice-cue -> side mapping explicit per row (the mapping is also in
    the header metadata).
  - FILLED live as the trial resolves: ``chosen_arm``, ``outcome``,
    ``rewarded``, ``return_arm_used``, ``return_cue_played`` and the event
    timestamps (all host wall-clock ISO except ``lick_fw_ms``).

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

from dataclasses import dataclass, field, fields
from typing import List, Optional, Tuple

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
    # Closed-loop debias context (debias_blocks generator; blank for balanced).
    # sched_state = NORMAL/INTERVENTION, block_id = 1-based within a block (blank
    # in NORMAL), block_trigger = auto/manual, bias = signed directional-error
    # bias at this trial's decision. See debias.py and the ADR.
    "sched_state", "block_id", "block_trigger", "bias",
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
    # Closed-loop debias context, set when the trial's side is decided (blank for
    # the balanced generator). See :meth:`Phase3Log.set_decision`.
    sched_state: Optional[str] = None
    block_id: Optional[str] = None
    block_trigger: Optional[str] = None
    bias: Optional[str] = None


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


class Phase3Log:
    """The per-session Phase 3 data table (plan + live outcomes)."""

    def __init__(self, sequence: TrialSequence, *, animal: str,
                 session_start_iso: str, reward_incorrect: bool = False,
                 reward_pulse_ms: int = 500, max_topups: int = 0,
                 session_id: str = "") -> None:
        self.animal = animal
        self.seed = sequence.seed
        self.cfg = sequence.cfg
        # Kept so a snapshot can read the CURRENT closed-loop debias state (state +
        # bias) for panels; the snapshot copies those readings out by value.
        self._sequence = sequence
        self.session_start_iso = session_start_iso
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
        arm = self._sequence.correct_arm(index)
        if arm is not None:
            r.correct_arm = _ARM_LABEL.get(arm, r.correct_arm)
        cue = self._sequence.choice_cue(index)
        if cue:
            r.choice_cue = cue
        m = self._sequence.trial_meta(index)
        r.sched_state = m["sched_state"]
        r.block_id = None if not m["block_id"] else str(m["block_id"])
        r.block_trigger = m["block_trigger"] or None
        r.bias = f"{m['bias']:.4f}"

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

    def reward_fired(self, index: int, iso: str) -> None:
        r = self._row(index)
        if r is None:
            return
        r.rewarded = "1"
        r.reward_cmd_iso = iso
        self._pending_lick_index = index      # this trial now awaits its lick

    def topup_fired(self, index: int) -> None:
        """Record one lick-triggered top-up pulse at the well for ``index`` (an
        extra beyond the prime; docs/decisions/0069). Increments the trial's
        running top-up count."""
        r = self._row(index)
        if r is None:
            return
        cur = int(r.topups) if r.topups is not None else 0
        r.topups = str(cur + 1)

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

    def reset_trial(self, index: int) -> None:
        """Clear a trial's live (filled) fields back to unrecorded, keeping its
        planned cue columns, so it can be re-run cleanly (Repeat / Back buttons;
        docs/decisions/0058). Also drops a pending reward-lick pointer aimed at it,
        so a stale lick can't attach to the reset row."""
        r = self._row(index)
        if r is None:
            return
        planned = ("correct_arm", "choice_cue", "return_cue_left",
                   "return_cue_right")
        for f in fields(_Row):
            if f.name not in planned:
                setattr(r, f.name, None)
        if self._pending_lick_index == index:
            self._pending_lick_index = None

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
        seq = self._sequence
        return Phase3StateSnapshot(
            rows=rows, current_index=current_index, total=len(self._rows),
            armed=armed, seed=self.seed, high_tone_is_left=c.high_tone_is_left,
            high_tone_hz=c.high_tone_hz, low_tone_hz=c.low_tone_hz,
            generator=c.generator, debias_state=seq.debias_state(),
            debias_bias=seq.debias_bias(),
            trigger_bias=(db.trigger_bias if db is not None else 0.0),
            exit_consecutive=(db.exit_consecutive if db is not None else 0),
            max_block_len=(db.max_block_len if db is not None else 0))

    # ------------------------------------------------------------- render
    def _header_comment(self) -> List[str]:
        c = self.cfg
        lines = [
            f"# phase3_session animal={self.animal} session_id={self.session_id} "
            f"seed={self.seed} proto=7",
            f"# high_tone_is_left={c.high_tone_is_left} "
            f"high_tone_hz={c.high_tone_hz} low_tone_hz={c.low_tone_hz}",
            f"# block_size={c.block_size} max_same_run={c.max_same_run} "
            f"max_trials={c.max_trials} planned_trials={len(self._rows)} "
            f"reward_incorrect={self.reward_incorrect} "
            f"reward_pulse_ms={self.reward_pulse_ms} max_topups={self.max_topups}",
            f"# generator={c.generator}",
        ]
        # For a closed-loop debias session, record its params and the caveat that
        # the seed ALONE no longer reproduces the schedule (it also depends on the
        # animal's realized choices + any operator overrides). See debias.py.
        db = getattr(c, "debias", None)
        if c.generator != "balanced" and db is not None:
            lines.append(
                f"# debias_blocks window={db.window} min_samples={db.min_samples} "
                f"trigger_bias={db.trigger_bias} "
                f"exit_consecutive={db.exit_consecutive} "
                f"max_block_len={db.max_block_len} signal={db.signal}")
            lines.append(
                "# note: debias schedule is closed-loop - seed alone does NOT "
                "reproduce it; it also depends on realized choices + overrides")
        lines += [
            f"# session_start_iso={self.session_start_iso}",
            "# note: *_iso are host actuation-decision times (serial latency ~ms); "
            "lick_fw_ms is the firmware hardware clock. see docs/decisions/0047",
        ]
        return lines

    def render_csv(self) -> str:
        """Render the whole table (header comment + column row + trials) as CSV
        text. Cheap enough to re-render on every update (a few hundred rows)."""
        data_fields = [f.name for f in fields(_Row)]
        lines = list(self._header_comment())
        lines.append(",".join(COLUMNS))
        for i, r in enumerate(self._rows, start=1):
            cells = [str(i)] + [
                ("" if getattr(r, name) is None else str(getattr(r, name)))
                for name in data_fields
            ]
            lines.append(",".join(cells))
        return "\n".join(lines) + "\n"
