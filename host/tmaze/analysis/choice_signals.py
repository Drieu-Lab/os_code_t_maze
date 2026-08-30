"""choice_signals.py - per-trial derived strategy features (pure core).

Turns the raw per-trial frame from :mod:`bundle_reader` into the behavioural
signals the traces / mode labels / choice model consume, adding columns rather
than reshaping. Definitions mirror the live :mod:`tmaze.core.phase3.phase3_stats`
so the offline analysis can't drift from the on-rig readout:

- ``choice`` / ``correct`` / ``reward`` - the animal's realised choice arm, whether
  it matched the cued side, and whether it was rewarded.
- ``cue_side`` / ``cue_followed`` - the side the CHOICE cue implied (via the
  session's ``high_tone_is_left`` mapping) and whether the animal went there.
- lag-1 own-choice features over TRULY-adjacent trials (``switch`` / ``stay`` /
  ``cue_switched``) - a pair counts only when the two trials are consecutive and
  both completed (a gap, an abandoned trial, or a not-yet-scored trial breaks it),
  exactly as phase3_stats' win-stay/lose-shift does (docs/decisions/0093).
- return-cue carryover (lag-1): ``prev_return_cue`` / ``prev_return_cue_side`` /
  ``prev_return_cue_congruent`` - the identity + implied side of trial N-1's PLAYED
  return cue, and whether it matched N-1's choice cue (same vs different tone). These
  test whether the trial N-1 return cue biases the choice / switch at trial N.
- ``epoch`` - ``free`` (NORMAL, counterbalanced) / ``in_block`` (INTERVENTION) /
  ``post_block`` (a NORMAL trial within ``post_window`` completed trials of a block
  exit). Blocks are KEPT, not dropped: they follow task rules and often instigate
  mode switches, so the analysis partitions rather than discards them.

CAVEAT (documented, propagated to the notebook): ``return_cue_played`` is set by
which arm the animal took back (``return_arm_used``), so the return-cue carryover is
partly endogenous - correlated with the animal's own N-1 behaviour, not an
independently randomised manipulation. The choice model controls for prev choice/
reward but this stays observational.

Pure pandas/numpy; no I/O. Headless tests: ``python -m unittest
test_analysis_choice_signals``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tmaze.analysis.bundle_reader import (
    COMPLETED_OUTCOMES,
    INTERVENTION_LABEL,
    LEFT_LABEL,
    RIGHT_LABEL,
)
from tmaze.analysis.phase3_format import CORRECT, HIGH, LOW

DEFAULT_POST_WINDOW = 10

EPOCH_FREE = "free"
EPOCH_IN_BLOCK = "in_block"
EPOCH_POST_BLOCK = "post_block"
# An unrewarded probe trial (probe_blocks.py): its own epoch so it is excluded
# from the reinforced FREE measures, like an in-block trial. See docs/decisions/
# 0115-probe.
EPOCH_PROBE = "probe"


def bool_series(s: pd.Series) -> pd.Series:
    """A possibly-object/NA boolean column as a clean ``bool`` Series (NA -> False).

    Routes through the nullable ``boolean`` dtype so filling NAs doesn't trigger the
    pandas object-downcast FutureWarning."""
    return s.astype("boolean").fillna(False).astype(bool)


def _implied_side(cue: pd.Series, high_tone_is_left: pd.Series) -> pd.Series:
    """Map a cue tone (HIGH/LOW) to the arm it implies, per the session mapping.

    ``high_tone_is_left`` True -> HIGH=LEFT / LOW=RIGHT; False -> the mirror. Returns
    ``LEFT``/``RIGHT`` strings, or NA where the tone is not HIGH/LOW or the mapping
    is unknown (an older session with no ``high_tone_is_left`` header)."""
    is_high = cue.eq(HIGH)
    is_low = cue.eq(LOW)
    valid_cue = is_high | is_low
    htl = high_tone_is_left.astype("boolean")
    known = htl.notna()
    # left when (HIGH and htl) or (LOW and not htl)
    left = (is_high & htl.fillna(False)) | (is_low & (~htl).fillna(False))
    left_np = left.astype("boolean").fillna(False).to_numpy(dtype=bool)
    side = pd.Series(np.where(left_np, LEFT_LABEL, RIGHT_LABEL), index=cue.index)
    return side.where(valid_cue & known)


def add_signals(
    trials: pd.DataFrame, *, post_window: int = DEFAULT_POST_WINDOW
) -> pd.DataFrame:
    """Return ``trials`` with the derived per-trial signal columns added.

    Lag features are computed WITHIN each ``(session_id, run_idx)`` group so they
    never cross a session or a restart boundary. Rows must already be sorted (the
    reader sorts by animal/session/run/trial)."""
    if trials.empty:
        return trials.copy()

    df = trials.copy()
    df["completed"] = df["outcome"].isin(COMPLETED_OUTCOMES)
    df["choice"] = df["chosen_arm"].where(
        df["chosen_arm"].isin([LEFT_LABEL, RIGHT_LABEL])
    )
    df["correct"] = df["outcome"].eq(CORRECT)
    df["reward"] = pd.to_numeric(df["rewarded"], errors="coerce").fillna(0).astype(int)
    df["in_block"] = df["sched_state"].eq(INTERVENTION_LABEL)
    df["auto_block"] = df["in_block"] & df["block_trigger"].eq("auto")
    # Unrewarded probe trial marker (string "1" in the CSV -> bool). Absent/"" in a
    # pre-probe session => all False. See docs/decisions/0115-probe.
    df["probe"] = df.get("probe", pd.Series("", index=df.index)).eq("1")
    # Correct-choice probe TRIGGER marker (docs/decisions/0134): the always-correct
    # trial that started a probe block. Excluded from the probe accuracy read-out
    # downstream (rolling_metrics). Absent/"" in an older session => all False.
    df["probe_trigger"] = df.get(
        "probe_trigger", pd.Series("", index=df.index)).eq("1")

    htl = df.get("high_tone_is_left")
    if htl is None:
        htl = pd.Series([pd.NA] * len(df), index=df.index)
    htl = htl.astype("object")
    df["cue_side"] = _implied_side(df["choice_cue"], htl)
    df["cue_followed"] = (df["choice"] == df["cue_side"]).where(
        df["choice"].notna() & df["cue_side"].notna()
    )
    df["return_cue_side"] = _implied_side(df["return_cue_played"], htl)
    df["return_cue_congruent"] = (
        df["return_cue_played"] == df["choice_cue"]
    ).where(df["return_cue_played"].isin([HIGH, LOW]))

    g = df.groupby(["session_id", "run_idx"], sort=False)
    df["prev_trial"] = g["trial"].shift(1)
    df["prev_choice"] = g["choice"].shift(1)
    df["prev_correct"] = g["correct"].shift(1)
    df["prev_reward"] = g["reward"].shift(1)
    df["prev_completed"] = g["completed"].shift(1)
    df["prev_in_block"] = g["in_block"].shift(1)
    df["prev_probe"] = g["probe"].shift(1)
    df["prev_cue_side"] = g["cue_side"].shift(1)
    # Return-cue carryover: N-1's PLAYED return cue + congruency, carried onto N.
    df["prev_return_cue"] = g["return_cue_played"].shift(1)
    df["prev_return_cue_side"] = g["return_cue_side"].shift(1)
    df["prev_return_cue_congruent"] = g["return_cue_congruent"].shift(1)

    consecutive = df["trial"] == (df["prev_trial"] + 1)
    pair_valid = (
        consecutive
        & bool_series(df["completed"])
        & bool_series(df["prev_completed"])
        & df["choice"].notna()
        & df["prev_choice"].notna()
    )
    df["pair_valid"] = pair_valid
    # A free-to-free own-choice transition: neither trial coerced by a block NOR an
    # unrewarded probe (a probe is not the rat's freely-reinforced strategy).
    df["own_pair"] = (
        pair_valid
        & ~df["in_block"] & ~bool_series(df["prev_in_block"])
        & ~df["probe"] & ~bool_series(df["prev_probe"])
    )
    df["switch"] = (df["choice"] != df["prev_choice"]).where(pair_valid)
    df["stay"] = (df["choice"] == df["prev_choice"]).where(pair_valid)
    df["cue_switched"] = (df["cue_side"] != df["prev_cue_side"]).where(
        pair_valid & df["cue_side"].notna() & df["prev_cue_side"].notna()
    )

    df["epoch"] = _epoch_series(df, post_window)
    return df


def _epoch_series(df: pd.DataFrame, post_window: int) -> pd.Series:
    """Per-trial ``free`` / ``in_block`` / ``post_block`` tag.

    ``post_block`` = a NORMAL trial within ``post_window`` COMPLETED trials after the
    most recent in-block trial in the same session/run. Computed per group so it
    never leaks across a session boundary."""
    out = pd.Series(EPOCH_FREE, index=df.index, dtype="object")
    for _, idx in df.groupby(["session_id", "run_idx"], sort=False).groups.items():
        since_block = None
        for i in idx:
            # An unrewarded probe trial is its own epoch (excluded from FREE); it
            # does not open/advance/close the post-block countdown.
            if bool(df.at[i, "probe"]):
                out.at[i] = EPOCH_PROBE
                continue
            if bool(df.at[i, "in_block"]):
                out.at[i] = EPOCH_IN_BLOCK
                since_block = 0
                continue
            if since_block is not None and since_block < post_window:
                out.at[i] = EPOCH_POST_BLOCK
            # Only completed NORMAL trials advance the post-block countdown.
            if since_block is not None and bool(df.at[i, "completed"]):
                since_block += 1
                if since_block >= post_window:
                    since_block = None
    return out
