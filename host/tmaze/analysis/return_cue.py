"""return_cue.py - return-cue carryover summary for the animal-progress notebook.

Tests one hypothesis: does the return cue the animal heard on trial N-1 bias its
choice / switching on trial N? Two read-outs over the animal's OWN-CHOICE adjacent
pairs (``own_pair`` - the free-choice, cue-comparable adjacent trials of
``choice_signals`` / ADR-0093):

- ``P(switch at N)`` split by whether N-1's return cue was CONGRUENT with the
  upcoming cued side, each with a Wilson score confidence interval; and
- ``P(choice at N == the side N-1's return cue pointed to)``, also with a Wilson CI.

The return cue is partly ENDOGENOUS (which return cue plays depends on the animal's
own return path), so this is observational - a descriptive carryover read-out, not a
causal effect. Kept here, tested, so the notebook stays a thin front-end.

Pure pandas/numpy; no I/O. Headless tests:
``python -m unittest test_analysis_return_cue``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

import numpy as np
import pandas as pd

from tmaze.analysis.choice_signals import bool_series

# Standard-normal 97.5th percentile -> a two-sided 95% interval.
_Z = 1.96

_SWITCH_COLUMNS = ["congruent", "n", "k", "p", "lo", "hi"]


def wilson_interval(k: int, n: int, z: float = _Z) -> Tuple[float, float, float]:
    """Wilson score interval for a binomial proportion ``k / n``.

    Returns ``(p, lo, hi)`` where ``p = k / n`` is the point estimate and
    ``[lo, hi]`` is the two-sided score interval at confidence ``z`` (default ~95%).
    An empty sample (``n <= 0``) returns ``(nan, nan, nan)``. The score interval is
    preferred over the normal approximation because it stays inside ``[0, 1]`` and
    behaves at extreme proportions and small ``n`` - both common with these counts.
    """
    if n <= 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, centre - half, centre + half


@dataclass
class ReturnCueSummary:
    """Return-cue carryover read-outs for one animal (see the module docstring)."""

    # One row per congruency group: columns congruent(bool), n, k, p, lo, hi.
    switch: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=_SWITCH_COLUMNS)
    )
    # P(choice == N-1 return-cue side) over pairs with a known return-cue side.
    match_n: int = 0
    match_k: int = 0
    match_p: float = float("nan")
    match_lo: float = float("nan")
    match_hi: float = float("nan")


def return_cue_switch_summary(signals: pd.DataFrame) -> ReturnCueSummary:
    """Summarise return-cue carryover for ONE animal's signal frame.

    Restricts to own-choice adjacent pairs (``own_pair``) with a defined ``switch``
    and a known N-1 return-cue congruency. Computes, per congruency group,
    ``P(switch at N)`` with a Wilson CI, and overall ``P(choice == N-1 return-cue
    side)`` with a Wilson CI. Returns an empty-framed :class:`ReturnCueSummary` when
    there are no usable pairs.
    """
    empty = ReturnCueSummary()
    if signals.empty:
        return empty
    c = signals[bool_series(signals["own_pair"])].copy()
    c = c[c["prev_return_cue_congruent"].notna() & c["switch"].notna()]
    if c.empty:
        return empty

    rows = []
    for cong in (True, False):
        sub = c[c["prev_return_cue_congruent"] == cong]
        k = int(bool_series(sub["switch"]).sum())
        n = int(len(sub))
        p, lo, hi = wilson_interval(k, n)
        rows.append({"congruent": cong, "n": n, "k": k, "p": p, "lo": lo, "hi": hi})
    switch = pd.DataFrame(rows, columns=_SWITCH_COLUMNS)

    cc = c[c["prev_return_cue_side"].notna()]
    match = cc["choice"] == cc["prev_return_cue_side"]
    mk, mn = int(match.sum()), int(len(match))
    mp, mlo, mhi = wilson_interval(mk, mn)
    return ReturnCueSummary(
        switch=switch, match_n=mn, match_k=mk, match_p=mp, match_lo=mlo, match_hi=mhi,
    )
