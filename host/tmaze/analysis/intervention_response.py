"""intervention_response.py - how the animal responds to intervention blocks.

Intervention blocks (side-bias debias / de-alternation) are part of the task and
often the instigator of a mode switch, so the analysis characterises them rather
than dropping their trials. Two products:

- ``block_table`` - one row per block with its response metrics: in-block accuracy,
  COMPLIANCE (fraction of block trials on which the animal chose the pushed/correct
  side), ESCAPE-VIA-CUE (the rise in cue-following inside the block vs a pre-onset
  baseline - the designed escape hatch), and trials-to-first-correct + length. These
  are computed for ALL blocks regardless of trigger.
- ``peri_transitions`` - the peri-intervention pre->post mode-transition summary,
  RESTRICTED TO AUTO blocks: an auto block fires from the animal's own drift, so its
  pre-onset window is a genuine baseline; a manual block is operator-chosen and would
  bias the "did the block cause the switch" reading, so manual blocks are surfaced
  only as timeline markers (``block_epochs`` in rolling_metrics), not here.

Pure pandas; no I/O. Headless tests: ``python -m unittest
test_analysis_intervention_response``.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from tmaze.analysis.choice_signals import EPOCH_FREE
from tmaze.core.phase3.phase3_task import CORRECT

DEFAULT_PRE_WINDOW = 15
DEFAULT_POST_WINDOW = 15


def _completed(signals: pd.DataFrame) -> pd.DataFrame:
    return signals[signals["completed"].fillna(False)].copy()


def block_table(
    signals: pd.DataFrame, *, pre_window: int = DEFAULT_PRE_WINDOW
) -> pd.DataFrame:
    """One row per intervention block with its response metrics.

    ``signals`` is a single animal's frame from :mod:`choice_signals`. Blocks are the
    maximal runs of in-block trials sharing a ``block_id`` within a session."""
    c = _completed(signals)
    inb = c[c["in_block"].fillna(False) & c["block_id"].astype(str).ne("")]
    rows: List[dict] = []
    for (sid, bid), grp in inb.groupby(["session_id", "block_id"], sort=False):
        grp = grp.sort_values("seq")
        start_seq = int(grp["seq"].min())
        end_seq = int(grp["seq"].max())
        n = len(grp)
        acc = float(grp["correct"].mean()) if n else float("nan")
        compliance = (
            float((grp["choice"] == grp["correct_arm"]).mean()) if n else float("nan")
        )
        in_cue = _mean_bool(grp["cue_followed"])
        # Pre-onset baseline: the last ``pre_window`` FREE completed trials before it.
        pre = c[
            (c["session_id"] == sid)
            & (c["seq"] < start_seq)
            & (c["epoch"] == EPOCH_FREE)
        ].tail(pre_window)
        pre_cue = _mean_bool(pre["cue_followed"])
        escape = (
            (in_cue - pre_cue)
            if (in_cue is not None and pre_cue is not None)
            else None
        )
        first_correct = _first_correct_position(grp)
        rows.append(
            {
                "session_id": sid,
                "block_id": bid,
                "kind": grp["block_kind"].replace("", "side_bias").iloc[0],
                "trigger": grp["block_trigger"].iloc[0] or "auto",
                "start_seq": start_seq,
                "end_seq": end_seq,
                "length": n,
                "in_block_accuracy": acc,
                "compliance": compliance,
                "pre_cue_follow": pre_cue,
                "in_cue_follow": in_cue,
                "escape_via_cue": escape,
                "trials_to_first_correct": first_correct,
            }
        )
    return pd.DataFrame(rows)


def _mean_bool(s: pd.Series) -> Optional[float]:
    s = s.dropna()
    if s.empty:
        return None
    return float(s.astype(bool).mean())


def _first_correct_position(grp: pd.DataFrame):
    """1-based index of the first CORRECT trial in the block, or NA if none."""
    outcomes = grp["outcome"].tolist()
    for i, o in enumerate(outcomes, start=1):
        if o == CORRECT:
            return i
    return pd.NA


def peri_transitions(
    labeled: pd.DataFrame,
    blocks: List[dict],
    *,
    pre_window: int = DEFAULT_PRE_WINDOW,
    post_window: int = DEFAULT_POST_WINDOW,
) -> pd.DataFrame:
    """Pre->post dominant-mode transition for each AUTO block.

    ``labeled`` is the per-trial mode frame from :mod:`mode_label` (needs ``seq``,
    ``session_id``, ``in_block``, ``mode``); ``blocks`` is ``rolling_metrics.
    block_epochs``. For each auto block, the dominant free-behaviour mode in the
    ``pre_window`` free trials before onset and the ``post_window`` free trials after
    offset. Returns one row per auto block: ``session_id, kind, pre_mode, post_mode,
    changed``."""
    if labeled.empty or not blocks:
        return pd.DataFrame(
            columns=["session_id", "kind", "pre_mode", "post_mode", "changed"]
        )
    free = labeled[~labeled["in_block"].fillna(False)]
    rows: List[dict] = []
    for b in blocks:
        if b.get("trigger") != "auto":
            continue
        sid = b["session_id"]
        pre = free[(free["session_id"] == sid) & (free["seq"] < b["start_seq"])].tail(
            pre_window
        )
        post = free[
            (free["session_id"] == sid) & (free["seq"] > b["end_seq"])
        ].head(post_window)
        pre_mode = _dominant(pre["mode"])
        post_mode = _dominant(post["mode"])
        if pre_mode is None or post_mode is None:
            continue
        rows.append(
            {
                "session_id": sid,
                "kind": b["kind"],
                "pre_mode": pre_mode,
                "post_mode": post_mode,
                "changed": pre_mode != post_mode,
            }
        )
    return pd.DataFrame(rows)


def _dominant(modes: pd.Series) -> Optional[str]:
    modes = modes.dropna()
    if modes.empty:
        return None
    return modes.value_counts().idxmax()


def transition_matrix(transitions: pd.DataFrame, kind: Optional[str] = None):
    """Pivot ``peri_transitions`` into a pre_mode x post_mode count matrix.

    ``kind`` filters to one block kind (else all). Returns an empty frame when there
    are no transitions."""
    if transitions.empty:
        return pd.DataFrame()
    t = transitions if kind is None else transitions[transitions["kind"] == kind]
    if t.empty:
        return pd.DataFrame()
    return (
        t.groupby(["pre_mode", "post_mode"]).size().unstack(fill_value=0)
    )
