"""choice_model.py - per-session logistic model of choice (the cue-use signal).

Fits, per session, a logistic regression of the animal's CHOICE on the current cue
and its recent history, so the coefficient trajectory across sessions answers "is it
starting to use cue identity": the cue weight climbing above the perseveration /
win-stay-lose-shift weights is the emergence signature a single accuracy number
hides.

Model (all predictors signed +1 = LEFT, -1 = RIGHT; outcome P(choice = LEFT)):

    logit P(LEFT) = b0(bias)
                  + b_cue        * cue_side
                  + b_persev     * prev_choice
                  + b_winstay    * prev_choice * prev_reward_sign   (win-stay/lose-shift)
                  + b_returncue  * prev_return_cue_side             (return-cue carryover)

Fit on FREE trials only: an intervention block deliberately breaks counterbalancing
(the cue becomes correlated with the forced side), which would confound the cue
coefficient if pooled in. A separate optional pooled model adds an ``in_block``
indicator and a ``cue x in_block`` interaction to TEST whether cue-sensitivity
changes under intervention.

CAVEAT: the return-cue term is partly endogenous (``return_cue_played`` depends on
the animal's own return path), so its coefficient is associational, not causal.

Requires ``statsmodels`` (guarded: ``HAS_STATSMODELS`` is False if unavailable, and
the fit functions raise a clear ImportError). Headless tests: ``python -m unittest
test_analysis_choice_model``.
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

from tmaze.analysis.bundle_reader import LEFT_LABEL, RIGHT_LABEL
from tmaze.analysis.choice_signals import EPOCH_FREE

try:  # statsmodels is an analysis-only dependency.
    import statsmodels.api as sm

    HAS_STATSMODELS = True
    STATSMODELS_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 - a broken dep chain can raise non-ImportError
    # Catch broadly (not just ImportError): a version-mismatched numpy/scipy can make
    # ``import statsmodels`` fail with other errors. Keep the reason so the caller can
    # tell "absent" from "installed-but-broken" (and from a wrong-kernel interpreter).
    sm = None
    HAS_STATSMODELS = False
    STATSMODELS_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

# Predictor columns, in display order. ``bias`` is the intercept.
TERMS = ("cue", "persev", "winstay", "returncue")


def _signed(side: pd.Series) -> pd.Series:
    """LEFT -> +1.0, RIGHT -> -1.0, else NaN."""
    return side.map({LEFT_LABEL: 1.0, RIGHT_LABEL: -1.0})


def _design(free: pd.DataFrame) -> pd.DataFrame:
    """Build the signed design matrix + outcome from a session's free trials."""
    d = pd.DataFrame(index=free.index)
    d["y"] = _signed(free["choice"]).map({1.0: 1, -1.0: 0})  # P(LEFT)
    d["cue"] = _signed(free["cue_side"])
    prev_choice = _signed(free["prev_choice"])
    d["persev"] = prev_choice.where(free["own_pair"].fillna(False))
    rew_sign = free["prev_reward"].map({1: 1.0, 0: -1.0})
    d["winstay"] = (prev_choice * rew_sign).where(free["own_pair"].fillna(False))
    d["returncue"] = _signed(free["prev_return_cue_side"])
    return d


def fit_session(free: pd.DataFrame, *, min_trials: int = 25) -> dict:
    """Fit the choice model on one session's FREE trials; return a coeff dict.

    Drops any predictor that is all-NaN or constant in this session (e.g. no return
    cue recorded, or a one-sided session), so the fit never fails on a degenerate
    column. Returns a dict with ``n`` and, per surviving term, ``<term>``,
    ``<term>_lo``, ``<term>_hi`` (95% CI) and ``<term>_p``. On a fit failure every
    coefficient is NaN but ``n`` is still reported."""
    if not HAS_STATSMODELS:
        raise ImportError("choice_model.fit_session requires statsmodels")

    d = _design(free).dropna(subset=["y"])
    out: dict = {"n": int(len(d))}
    if len(d) < min_trials:
        return out

    usable = []
    for term in TERMS:
        col = d[term]
        if col.notna().sum() >= min_trials and col.dropna().nunique() > 1:
            usable.append(term)
    if not usable:
        return out

    d = d.dropna(subset=usable)
    if len(d) < min_trials or d["y"].nunique() < 2:
        out["n"] = int(len(d))
        return out

    x = sm.add_constant(d[usable].astype(float), has_constant="add")
    y = d["y"].astype(int)
    out["n"] = int(len(d))
    try:
        res = sm.Logit(y, x).fit(disp=0)
        # A non-converged fit is (quasi-)separation: the "coefficient" is a huge
        # artefact with a meaningless CI, so report it as missing rather than
        # misleading. ``converged`` is True for a healthy MLE.
        if not getattr(res, "mle_retvals", {}).get("converged", True):
            raise RuntimeError("logit did not converge (separation)")
        ci = res.conf_int()
        for term in usable:
            out[term] = float(res.params[term])
            out[f"{term}_lo"] = float(ci.loc[term, 0])
            out[f"{term}_hi"] = float(ci.loc[term, 1])
            out[f"{term}_p"] = float(res.pvalues[term])
        out["bias"] = float(res.params.get("const", np.nan))
        out["converged"] = True
    except Exception:  # noqa: BLE001 - separation / singular design -> NaN row
        out["converged"] = False
        for term in usable:
            out[term] = np.nan
    return out


def fit_by_session(
    signals: pd.DataFrame, *, min_trials: int = 25
) -> pd.DataFrame:
    """Fit :func:`fit_session` per session; return one row per session in order.

    ``signals`` is one animal's frame from :mod:`choice_signals`. Rows carry
    ``session_id``/``date`` plus the coefficient columns, so the notebook can plot
    the cue weight (and the return-cue weight) across sessions."""
    rows: List[dict] = []
    if signals.empty:
        return pd.DataFrame()
    completed_free = signals[
        signals["completed"].fillna(False) & (signals["epoch"] == EPOCH_FREE)
    ]
    for sid, grp in completed_free.groupby("session_id", sort=False):
        row = {"session_id": sid, "date": grp["date"].iloc[0]}
        row.update(fit_session(grp, min_trials=min_trials))
        rows.append(row)
    return pd.DataFrame(rows)


def fit_in_block_interaction(
    signals: pd.DataFrame, *, min_trials: int = 40
) -> Optional[dict]:
    """Optional pooled test: does cue-sensitivity differ inside intervention blocks?

    Pools all completed trials and fits ``y ~ cue + in_block + cue:in_block``; the
    interaction coefficient is the change in cue weight under intervention. Returns a
    coeff dict (same shape as :func:`fit_session`) or None when there aren't enough
    in-block trials to estimate it."""
    if not HAS_STATSMODELS:
        raise ImportError("choice_model.fit_in_block_interaction requires statsmodels")
    c = signals[signals["completed"].fillna(False)].copy()
    c = c[c["choice"].notna() & c["cue_side"].notna()]
    # Exclude unrewarded probe trials: they are neither free-reinforced nor
    # intervention trials, so they don't belong in the free-vs-block contrast.
    if "probe" in c.columns:
        c = c[~c["probe"].fillna(False)]
    if len(c) < min_trials or c["in_block"].fillna(False).sum() < 10:
        return None
    y = _signed(c["choice"]).map({1.0: 1, -1.0: 0}).astype(int)
    cue = _signed(c["cue_side"]).astype(float)
    inb = c["in_block"].fillna(False).astype(float)
    x = pd.DataFrame(
        {"cue": cue, "in_block": inb, "cue_x_in_block": cue * inb}, index=c.index
    )
    x = sm.add_constant(x, has_constant="add")
    try:
        res = sm.Logit(y, x).fit(disp=0)
    except Exception:  # noqa: BLE001
        return None
    ci = res.conf_int()
    out = {"n": int(len(c))}
    for term in ("cue", "in_block", "cue_x_in_block"):
        out[term] = float(res.params[term])
        out[f"{term}_lo"] = float(ci.loc[term, 0])
        out[f"{term}_hi"] = float(ci.loc[term, 1])
        out[f"{term}_p"] = float(res.pvalues[term])
    return out
