"""
probe_blocks.py - unrewarded "probe" blocks for host-owned Phase 3 (pure core).

A probe block is a short run of trials where the cue plays and the choice is
SCORED exactly as normal but NO reward is delivered (no prime, no lick top-ups,
``reward_incorrect`` ignored). Comparing the animal's accuracy under a probe
(what it KNOWS, latent) against its reinforced accuracy (what it EXPRESSES when
paid) reads out cue->action knowledge separately from reward-driven performance -
the acquisition/expression dissociation of Drieu et al. 2025 (see the ADRs).

Trigger: CORRECT-CHOICE within a context WINDOW (docs/decisions/0134, superseding
the manual boundary-start of ADR-0115). The config gives one or more trial-number
windows (e.g. trials 40-60). Inside an un-fired window, the FIRST correct choice on
a NORMAL trial (not an intervention trial, not already a probe) fires a probe:
  - that TRIGGER trial's reward is withheld (it is flagged ``probe_trigger``); it is
    the gating event and, being always-correct by construction, is NOT part of the
    measured block - stats/analysis exclude it from probe accuracy;
  - a fresh internally-counterbalanced block of ``block_len`` trials then FOLLOWS,
    all unrewarded (precedent: Current Biology S0960-9822(24)00457-3). So the
    unrewarded run is ``block_len + 1`` trials.
An incorrect choice does not trigger; a correct choice made DURING an intervention
block does not trigger (windows can be generous so interventions / a run of wrong
choices just mean the probe does not fire that session); each window fires at most
one probe.

Relationship to the debias / de-alternation interventions (debias.py,
alternation.py, intervention_stack.py): the ``block_len`` forced block INSERTS on
top of the counterbalanced spine the same way an intervention block does - it
occupies trial slots without consuming the seed-deterministic L/R schedule
(``TrialSequence``'s ``_spine_pos`` only advances on NORMAL trials). But a probe is
ORTHOGONAL to those closed-loop corrections: it is not a bias/alternation reaction,
it forces no particular side beyond staying internally counterbalanced, and it
works under ANY generator (including plain ``balanced``). It also takes PRIORITY
over the intervention stack and suspends it while active. The TRIGGER trial itself
is an ordinary spine trial the animal already chose - it consumes the spine and is
only reclassified as a probe trial at scoring time.

Because the trigger needs the trial's OUTCOME (was the choice correct?), it is
evaluated at SCORING time via :meth:`on_scored`, not at commit time. The following
block trials are then handed out at commit time via :meth:`advance`. There is no
manual START (the window trigger is automatic), but an operator can DISABLE probes
mid-session (:meth:`set_armed` - no further window fires, and any running block is
aborted) and re-enable them, as well as abort just a running probe (:meth:`end_block`);
see docs/decisions/0137. Placement is not reproducible from the seed alone (it
depends on when the animal happens to be correct); the CSV records where each probe
landed and flags a session the operator disabled.

Pure and single-threaded (the Phase 3 actuation thread), like the other cores: no
Tk/serial, no locks. Headless tests: python -m unittest test_phase3_probe_blocks.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

from tmaze.core.phase3.trial_sequence import (
    LEFT, RIGHT, generate_balanced,
)

# A distinct PRNG offset so the probe block's internal L/R plan is deterministic
# from the session seed yet independent of the three schedule streams (choice +
# the two return arms) TrialSequence threads off that seed. Placement is still not
# seed-reproducible (it is outcome-triggered); only the WITHIN-block 5/5 pattern
# for the k-th probe of a session is deterministic given the seed.
_PROBE_SALT = 0x9E3779B9


@dataclass(frozen=True)
class ProbeBlockConfig:
    """Parameters of the probe feature.

    ``enabled`` builds the controller at all (no probe machinery when False).
    ``block_len`` is the number of FORCED trials in the block that follows the
    trigger trial (default 10, matching the paper); it is internally L/R
    counterbalanced, so an even length gives an exact 5/5 balance. ``windows`` is
    the list of ``(start, end)`` inclusive 1-based trial-number ranges in which a
    correct choice arms a probe (empty = the feature never fires)."""
    enabled: bool = False
    block_len: int = 10
    windows: Tuple[Tuple[int, int], ...] = ()


@dataclass(frozen=True)
class ProbeDecision:
    """The controller's ruling for ONE upcoming block trial (from :meth:`advance`).

    ``active`` is True when this trial is a forced probe-block trial; ``side`` is
    then the forced (internally counterbalanced) correct side (LEFT/RIGHT), else
    None. ``block_id`` is a 1-based id within one probe; ``index_in_block`` is the
    1-based position within the forced block (the trigger trial is position 0 and is
    recorded separately)."""
    active: bool
    side: Optional[int]
    block_id: int
    index_in_block: int


@dataclass
class ProbeBlockController:
    """The window-triggered probe-block state machine (see the module docstring).

    ``max_same_run`` / ``max_alt_run`` are the session's anti-bias caps (the same
    ``sequence.max_same_run`` / ``max_alt_run`` from phase3.json that shape the main
    schedule): the forced probe block honours them too, so a probe can't hand the rat
    a longer single-side run or alternation than the rest of the session allows (0 =
    that cap disabled, as in the main schedule). They are threaded in by TrialSequence
    when it builds the controller; both default to 0 for a bare/tested controller."""

    cfg: ProbeBlockConfig = field(default_factory=ProbeBlockConfig)
    seed: int = 0
    max_same_run: int = 0
    max_alt_run: int = 0

    def __post_init__(self) -> None:
        self.seed &= 0xFFFFFFFF
        self.reset()

    def reset(self) -> None:
        """Back to no-probe with no windows fired (a fresh session / Restart).

        Also re-ARMS the feature: a Restart is a fresh session, so an operator
        mid-session disable (see :meth:`set_armed`) does not carry over."""
        self._active = False
        self._block_id = 0
        self._pos = 0                 # forced block trials issued so far
        self._plan: List[int] = []    # the current block's counterbalanced sides
        self._manual_end = False
        self._fired: Set[int] = set()  # indices of windows that already fired
        # Runtime arm gate (docs/decisions/0137), separate from cfg.enabled (the
        # load-time build switch): the operator can disable probes mid-session so no
        # further window ever fires. ``_ever_disarmed`` records that a disable
        # happened at some point this run so the CSV header can flag the session.
        self._armed = True
        self._ever_disarmed = False

    # ------------------------------------------------------------------ reads
    @property
    def active(self) -> bool:
        return self._active

    @property
    def block_id(self) -> int:
        return self._block_id

    @property
    def armed(self) -> bool:
        """Whether the correct-choice trigger is live this run. False after an
        operator mid-session disable (docs/decisions/0137); re-armed by
        :meth:`reset` (Restart)."""
        return self._armed

    @property
    def ever_disarmed(self) -> bool:
        """Whether the operator disabled probes at any point this run - so a planned
        window may never have fired even though the config armed one. Recorded in
        the CSV header so the data file is self-describing (docs/decisions/0137)."""
        return self._ever_disarmed

    def state(self) -> str:
        """A compact state token for the snapshot/readout: ``ACTIVE`` while a probe
        block runs, else ``NORMAL``."""
        return "ACTIVE" if self._active else "NORMAL"

    # -------------------------------------------------------- operator overrides
    def end_block(self) -> None:
        """End a running probe early (operator "End" button). No manual START - the
        window trigger is automatic."""
        if self._active:
            self._manual_end = True

    def set_armed(self, armed: bool) -> None:
        """Arm or disarm the correct-choice trigger mid-session (docs/decisions/0137).

        Turning it OFF stops any FUTURE window from firing and, per the single-toggle
        UI, also aborts a probe block that happens to be running right now (so "off"
        cleanly means "no probe"); it records ``_ever_disarmed`` for the CSV-header
        flag. Turning it back ON re-arms: already-passed windows stay fired, but any
        remaining un-fired window can trigger again. A no-op that just sets the flag
        when the state is unchanged."""
        armed = bool(armed)
        if not armed:
            self._ever_disarmed = True
            if self._active:
                self._manual_end = True
        self._armed = armed

    # ---------------------------------------------------------- trigger (scoring)
    def _window_of(self, trial_no: int) -> Optional[int]:
        """The index of the (first) window containing 1-based ``trial_no``, else None."""
        for i, (start, end) in enumerate(self.cfg.windows):
            if start <= trial_no <= end:
                return i
        return None

    def on_scored(self, *, trial_no: int, correct: bool,
                  intervention_active: bool) -> bool:
        """Evaluate the correct-choice trigger for a just-scored trial.

        ``trial_no`` is the 1-based overall trial number; ``correct`` whether the
        choice was correct; ``intervention_active`` whether this trial was an
        intervention-block trial. Fires (and returns True) iff the feature is
        enabled, ARMED (not disabled mid-session; docs/decisions/0137), no probe is
        already running, the choice was correct, this was NOT an intervention trial,
        and ``trial_no`` lands in a window that has not fired yet. On firing it arms
        the ``block_len`` forced block for the following trials and marks the window
        used."""
        if not self._active and self.cfg.enabled and self._armed and correct \
                and not intervention_active:
            wi = self._window_of(trial_no)
            if wi is not None and wi not in self._fired:
                self._fired.add(wi)
                self._active = True
                self._manual_end = False
                self._block_id += 1
                self._pos = 0
                self._plan = self._make_plan()
                return True
        return False

    # -------------------------------------------------------- per block trial
    def advance(self) -> ProbeDecision:
        """Rule the next upcoming trial while a probe block is running.

        Called once per trial at commit time (only when :attr:`active`). Hands out
        the next forced counterbalanced side; ends the block after ``block_len``
        trials or an operator "End". The trigger trial is handled separately at
        scoring (:meth:`on_scored`), so this only covers the following block."""
        # End a running block that is done (length reached) or aborted (operator).
        if self._manual_end or self._pos >= len(self._plan):
            self._active = False
            self._manual_end = False
            self._pos = 0
            self._plan = []
            return ProbeDecision(False, None, 0, 0)
        side = self._plan[self._pos]
        self._pos += 1
        return ProbeDecision(True, side, self._block_id, self._pos)

    # ------------------------------------------------------------- internals
    def _make_plan(self) -> List[int]:
        """A counterbalanced L/R plan for one probe block (LEFT=0/RIGHT=1).

        Reuses the same balanced generator as the choice schedule so the block is
        an exact per-block balance (5/5 for the default even length) AND honours the
        session's anti-bias caps ``max_same_run`` / ``max_alt_run`` (so the block is
        re-rolled if a shuffle would exceed them; 0 disables a cap). The whole block
        is one balancing unit (``block == n``), so the cap is enforced WITHIN the
        block - it does not consider the trigger trial before it or the trials after
        it. The PRNG is seeded off the session seed plus the salt and the block
        ordinal, so the k-th probe of a session is deterministic given the seed +
        caps (placement is not; see the module docstring). Uses stdlib
        ``random.Random`` since docs/decisions/0140."""
        n = max(1, int(self.cfg.block_len))
        # A distinct, deterministic generator per probe block: fold the session
        # seed, the salt, and the block ordinal into one integer seed.
        rng = random.Random((self.seed ^ _PROBE_SALT) + self._block_id * _PROBE_SALT)
        seq = generate_balanced(n, n, self.max_same_run, rng,
                                max_alt=self.max_alt_run)
        return [RIGHT if v else LEFT for v in seq]
