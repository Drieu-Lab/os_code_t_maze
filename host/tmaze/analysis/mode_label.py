"""mode_label.py - rule-based behavioural-mode labelling (interpretable, no fitting).

Assigns each completed trial a single dominant behavioural MODE from the rolling
rates in :mod:`rolling_metrics`, using explicit, documented thresholds evaluated in
a fixed priority order. Nothing is fitted - every label traces back to one
arithmetic comparison a non-specialist can check - which is the point: it ships an
explainable decomposition now and is the validation baseline for a future latent-
state (GLM-HMM) model (deferred; see future-work).

Priority order (first match wins):
  disengaged -> non-stem -> side-bias -> alternating -> lose-shift/win-stay ->
  cue-following -> unclassified

Applied to ``free`` / ``post_block`` windows (the animal's own behaviour). An
``in_block`` trial is coerced by the schedule, so instead of a free-behaviour mode
it gets a RESPONSE label - ``complying`` (chose the pushed/correct side) or
``resisting`` - and the owning block kind is carried in ``mode_overlay`` for the
intervention lane. The escape-via-cue signal is a block-level contrast (did cue-
following rise vs baseline) and lives in :mod:`intervention_response`, because
in-block the cued side IS the pushed side, so it can't be told apart trial-by-trial.

The ``non-stem`` mode needs the per-excursion log; with none on disk yet it is
reported as unavailable (never assigned) rather than silently absent.

Pure pandas; no I/O. Headless tests: ``python -m unittest test_analysis_mode_label``.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MODE_DISENGAGED = "disengaged"
MODE_NONSTEM = "non-stem"
MODE_SIDE_BIAS = "side-bias"
MODE_ALTERNATING = "alternating"
MODE_LOSE_SHIFT = "lose-shift"
MODE_WIN_STAY = "win-stay"
MODE_CUE_FOLLOWING = "cue-following"
MODE_UNCLASSIFIED = "unclassified"
RESP_COMPLYING = "complying"
RESP_RESISTING = "resisting"

FREE_MODES = (
    MODE_DISENGAGED,
    MODE_NONSTEM,
    MODE_SIDE_BIAS,
    MODE_ALTERNATING,
    MODE_LOSE_SHIFT,
    MODE_WIN_STAY,
    MODE_CUE_FOLLOWING,
    MODE_UNCLASSIFIED,
)


@dataclass(frozen=True)
class ModeThresholds:
    """Tunable cut points for the rule-based modes (surfaced in the notebook config).

    Defaults are deliberate starting points for tuning against real data, not
    tuned values: a mode fires when its rolling rate crosses the cut. ``side_bias_frac``
    is one-sided (>= it, or <= its complement); the rest are lower bounds.
    """

    disengaged_gap_s: float = 45.0
    nonstem_frac: float = 0.5
    side_bias_frac: float = 0.75
    alternating_switch: float = 0.75
    lose_shift: float = 0.60
    win_stay: float = 0.60
    cue_following: float = 0.70


def _free_mode(row: pd.Series, thr: ModeThresholds, has_excursions: bool) -> str:
    gap = row.get("gap_s_med")
    if pd.notna(gap) and gap >= thr.disengaged_gap_s:
        return MODE_DISENGAGED
    if has_excursions:
        nonstem = row.get("nonstem_frac")
        if pd.notna(nonstem) and nonstem >= thr.nonstem_frac:
            return MODE_NONSTEM
    bias = row.get("side_bias_left")
    if pd.notna(bias) and (
        bias >= thr.side_bias_frac or bias <= (1.0 - thr.side_bias_frac)
    ):
        return MODE_SIDE_BIAS
    switch = row.get("switch_rate")
    if pd.notna(switch) and switch >= thr.alternating_switch:
        return MODE_ALTERNATING
    lose = row.get("lose_shift")
    win = row.get("win_stay")
    if pd.notna(lose) and lose >= thr.lose_shift:
        return MODE_LOSE_SHIFT
    if pd.notna(win) and win >= thr.win_stay:
        return MODE_WIN_STAY
    cue = row.get("cue_follow")
    if pd.notna(cue) and cue >= thr.cue_following:
        return MODE_CUE_FOLLOWING
    return MODE_UNCLASSIFIED


def label_modes(
    traces: pd.DataFrame,
    thresholds: ModeThresholds = ModeThresholds(),
    *,
    has_excursions: bool = False,
) -> pd.DataFrame:
    """Return ``traces`` with a ``mode`` column and a ``mode_overlay`` column.

    ``mode`` holds the free-behaviour mode for free/post-block trials and a RESPONSE
    label (``complying``/``resisting``) for in-block trials; ``mode_overlay`` carries
    the owning block kind on in-block trials (else NA) for the intervention lane."""
    if traces.empty:
        out = traces.copy()
        out["mode"] = pd.Series(dtype="object")
        out["mode_overlay"] = pd.Series(dtype="object")
        return out

    modes = []
    overlays = []
    for _, row in traces.iterrows():
        if bool(row.get("in_block")):
            modes.append(
                RESP_COMPLYING if bool(row.get("complying")) else RESP_RESISTING
            )
            overlays.append(row.get("block_kind"))
        else:
            modes.append(_free_mode(row, thresholds, has_excursions))
            overlays.append(pd.NA)
    out = traces.copy()
    out["mode"] = modes
    out["mode_overlay"] = overlays
    return out


def mode_occupancy(labeled: pd.DataFrame, by: str = "session_id") -> pd.DataFrame:
    """Fraction of trials in each free-behaviour mode, grouped by ``by`` (default
    per session), computed over free/post-block trials only (in-block responses are
    excluded so occupancy reflects the animal's own repertoire). Returns a table with
    one row per group value and one column per mode."""
    if labeled.empty:
        return pd.DataFrame()
    free = labeled[~labeled["in_block"].fillna(False)]
    if free.empty:
        return pd.DataFrame()
    tab = (
        free.groupby([by, "mode"]).size().unstack(fill_value=0)
    )
    frac = tab.div(tab.sum(axis=1), axis=0)
    # Stable column order (only the modes that occurred).
    cols = [m for m in FREE_MODES if m in frac.columns]
    return frac[cols]
