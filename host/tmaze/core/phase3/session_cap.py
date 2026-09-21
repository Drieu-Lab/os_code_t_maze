"""
session_cap.py - the Phase 3 session cap and the daily-water reward rule (pure core).

Why a cap (docs/decisions/0190). Uncapped, a Phase 3 session runs until the rat
disengages (~1 h, 130-230 trials). At the trial rates the rats reach, an
alternating or side-biased strategy (50 % correct on average) earns the whole
daily water ration, so cue-following is never *needed*. The cap fixes the number
of OPPORTUNITIES per session - N trials or M minutes, whichever comes first - so
that the water earned becomes a function of strategy, and pairs it with a reward
volume sized to the animal's daily need (docs/decisions/0191):

    uL per correct trial = daily_water_ml * 1000 * water_at_chance_fraction
                           / (max_trials * CHANCE_ACCURACY)

With ``water_at_chance_fraction = 0.5`` a rat at chance over the full ``max_trials``
earns half its daily water and a perfect rat earns exactly all of it; the
shortfall (``target - earned``, zero at 100 %) is the home-cage supplement. An
explicit per-animal ``reward_ul`` overrides the derived volume (the operator
pinning one magnitude for every rat) while the daily target still drives the
earned / supplement readout.

This module is the arithmetic only: it decides whether a cap has been reached and
what the per-reward volume should be. The thin controller shell owns the clock,
the trial counting, muting the cues and the banner (docs/decisions/0190); the
pulse width for a target volume comes from ``reward_volume.pulse_ms_for_volume``.

Pure stdlib, single-threaded, headlessly testable:
python -m unittest test_session_cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Accuracy of a rat that ignores the cue (alternation / side bias). The reward
# rule is anchored here so "50 % of the session earns X" reads directly off the
# config (docs/decisions/0191).
CHANCE_ACCURACY = 0.5

# Cap-reached reasons (the ``CapStatus.reason`` vocabulary; also written to the
# session summary / auto-note so a bundle says WHICH limit ended it).
REASON_TRIALS = "trials"
REASON_TIME = "time"

# Where a resolved per-reward volume came from (docs/decisions/0191). Recorded
# next to the pulse width in the trial-CSV header / manifest / summary.
SOURCE_DAILY_WATER = "animals.json:daily_water_ml"
SOURCE_REWARD_UL = "animals.json:reward_ul"
SOURCE_CONFIG = "config"


@dataclass(frozen=True)
class SessionCapConfig:
    """The ``session_cap`` section of phase3.json (docs/decisions/0190).

    ``enabled`` False = today's uncapped behaviour. ``max_trials`` counts SCORED
    trials since Session Start; ``max_minutes`` is wall-clock minutes since
    Session Start. ``water_at_chance_fraction`` is the fraction of the daily water
    a rat at chance earns over ``max_trials`` (the reward-volume rule above).
    Defaults are the values agreed for the current cohort (2026-09-21).
    """
    enabled: bool = True
    max_trials: int = 120
    max_minutes: float = 45.0
    water_at_chance_fraction: float = 0.5


@dataclass(frozen=True)
class CapStatus:
    """The cap as of one evaluation.

    ``reached`` is True once EITHER limit is met; ``reason`` names the one that
    fired first (``"trials"`` / ``"time"``; None while running). ``trials_left``
    and ``seconds_left`` are for the operator countdown (never negative).
    """
    reached: bool = False
    reason: Optional[str] = None
    trials_left: int = 0
    seconds_left: float = 0.0


def evaluate(completed: int, elapsed_s: float, cfg: SessionCapConfig) -> CapStatus:
    """Has the cap been reached after ``completed`` scored trials and
    ``elapsed_s`` seconds since Session Start?

    Pure and idempotent - the shell calls it at trial boundaries (never mid-trial,
    so an in-flight trial always finishes; docs/decisions/0190). A disabled cap
    never triggers. Trials win the tie when both limits are met on the same call
    (the trial count is the scientific quantity; the clock is the backstop).
    """
    trials_left = max(0, cfg.max_trials - int(completed))
    seconds_left = max(0.0, cfg.max_minutes * 60.0 - float(elapsed_s))
    if not cfg.enabled:
        return CapStatus(False, None, trials_left, seconds_left)
    if trials_left == 0:
        return CapStatus(True, REASON_TRIALS, 0, seconds_left)
    if seconds_left == 0.0:
        return CapStatus(True, REASON_TIME, trials_left, 0.0)
    return CapStatus(False, None, trials_left, seconds_left)


def reward_ul_for_target(daily_water_ml: float, cfg: SessionCapConfig) -> float:
    """Per-correct-trial volume (uL) so that a rat at chance over ``max_trials``
    earns ``water_at_chance_fraction`` of ``daily_water_ml`` (docs/decisions/0191).

    Example: 8 mL/day, 120 trials, fraction 0.5 -> 8000 * 0.5 / (120 * 0.5) =
    66.7 uL. Raises ValueError on a non-positive daily target or trial count.
    """
    daily = float(daily_water_ml)
    if daily <= 0.0:
        raise ValueError("daily_water_ml must be > 0")
    if cfg.max_trials <= 0:
        raise ValueError("session_cap.max_trials must be > 0")
    return daily * 1000.0 * cfg.water_at_chance_fraction / (cfg.max_trials * CHANCE_ACCURACY)


def resolve_reward_ul(daily_water_ml: Optional[float], reward_ul: Optional[float],
                      cfg: SessionCapConfig):
    """The per-animal reward volume and its source, or ``(None, "config")``.

    Priority (docs/decisions/0191): an EXPLICIT per-animal ``reward_ul`` wins -
    it is the operator pinning the reward magnitude (e.g. one constant volume
    across rats so the per-trial reward signal is the same size for all); else a
    recorded ``daily_water_ml`` derives the volume via
    :func:`reward_ul_for_target`; else None, meaning the rig keeps the
    phase3.json / GUI pulse width. ``daily_water_ml`` stays the water TARGET for
    the earned / supplement readout whichever branch sizes the reward. A blank
    cell (None / "") is "unset", never zero.
    """
    if reward_ul not in (None, "") and float(reward_ul) > 0.0:
        return float(reward_ul), SOURCE_REWARD_UL
    if daily_water_ml not in (None, "") and float(daily_water_ml) > 0.0:
        return reward_ul_for_target(float(daily_water_ml), cfg), SOURCE_DAILY_WATER
    return None, SOURCE_CONFIG


def water_target_summary(daily_water_ml: Optional[float], earned_ul: float) -> Optional[dict]:
    """``earned X mL = Y % of the daily target, supplement Z mL`` as a dict for the
    session summary / cap banner (docs/decisions/0191), or None when the animal
    has no recorded daily target. ``supplement_ml`` is ``target - earned``, floored
    at 0 - it is zero on a perfect day, not a fixed daily top-up.
    """
    if daily_water_ml in (None, "") or float(daily_water_ml) <= 0.0:
        return None
    target_ml = float(daily_water_ml)
    earned_ml = max(0.0, float(earned_ul)) / 1000.0
    return {
        "daily_water_ml": round(target_ml, 2),
        "earned_ml": round(earned_ml, 2),
        "fraction": round(earned_ml / target_ml, 3),
        "supplement_ml": round(max(0.0, target_ml - earned_ml), 2),
    }


def format_water_target(summary: Optional[dict]) -> str:
    """One operator-facing line from :func:`water_target_summary` ("" if None)."""
    if not summary:
        return ""
    return (f"earned {summary['earned_ml']:.2f} mL = "
            f"{100.0 * summary['fraction']:.0f}% of {summary['daily_water_ml']:.1f} mL "
            f"target, supplement {summary['supplement_ml']:.2f} mL")


def mmss(sec: float) -> str:
    """``m:ss`` for a duration in seconds (floored at 0)."""
    s = max(0, int(sec))
    return f"{s // 60:d}:{s % 60:02d}"


def format_countdown(cfg: SessionCapConfig, completed: int, elapsed_s: float) -> str:
    """The live banner token, e.g. ``cap: 87/120 · 22:10/45:00``."""
    return (f"cap: {int(completed)}/{cfg.max_trials} \u00b7 "
            f"{mmss(elapsed_s)}/{mmss(cfg.max_minutes * 60.0)}")
