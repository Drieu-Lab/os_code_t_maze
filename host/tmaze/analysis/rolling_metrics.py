"""rolling_metrics.py - windowed strategy traces over an animal's trial stream.

Slides a trailing window over one animal's time-ordered COMPLETED trials (the frame
from :mod:`choice_signals`) and reduces each behavioural signal to a rate trace, so
the notebook can plot how a strategy waxes and wanes within and across sessions. It
also returns the session boundaries and the intervention-block epochs (for shading)
and an engagement proxy.

Every rate is a masked rolling ratio ``sum(numerator) / sum(denominator)`` over the
window, so conditional measures (win-stay is only defined after a correct trial,
etc.) use the correct denominator. Baseline-strategy rates use FREE trials only
(the animal's uncoerced behaviour); a block deliberately breaks counterbalancing, so
pooling it in would distort side bias / cue-follow (see the ADR on trial
partitioning).

Engagement: with no per-excursion log on disk yet (the feature is new), engagement
is a PROXY - the rolling median inter-trial gap in seconds from ``trial_start_iso``
(long gaps => disengagement). The excursion-log signals (trips/min, non-stem
fraction) auto-appear once those logs exist.

Pure pandas/numpy; no I/O. Headless tests: ``python -m unittest
test_analysis_rolling_metrics``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from tmaze.analysis.bundle_reader import LEFT_LABEL
from tmaze.analysis.choice_signals import EPOCH_FREE, bool_series

DEFAULT_WINDOW = 20
# A rate needs at least this many denominator samples in the window to be plotted,
# so an early/sparse window doesn't show a jumpy 0/1 artefact.
DEFAULT_MIN_DEN = 3


@dataclass
class TraceResult:
    """Traces + overlays for one animal."""

    traces: pd.DataFrame
    session_bounds: List[dict] = field(default_factory=list)
    blocks: List[dict] = field(default_factory=list)
    # Contiguous unrewarded probe-block runs (probe_blocks.py), each a dict with
    # ``session_id, start_seq, end_seq, length, n, accuracy`` - the offline twin of
    # phase3_traces.ProbeBlockSummary for the notebook. See docs/decisions/0115-probe.
    probe_blocks: List[dict] = field(default_factory=list)


def _masked_rate(
    num: pd.Series, den: pd.Series, window: int, min_den: int
) -> pd.Series:
    """Rolling ``sum(num)/sum(den)`` over a trailing ``window``; NaN below min_den."""
    n = num.astype(float).rolling(window, min_periods=1).sum()
    d = den.astype(float).rolling(window, min_periods=1).sum()
    rate = n / d.where(d > 0)
    return rate.where(d >= min_den)


def build_traces(
    signals: pd.DataFrame,
    *,
    window: int = DEFAULT_WINDOW,
    min_den: int = DEFAULT_MIN_DEN,
) -> TraceResult:
    """Build the rolling traces for ONE animal's signal frame.

    Assumes ``signals`` is a single animal (filter first for a multi-animal frame).
    The trace rows are the animal's completed trials in order; ``seq`` is the x-axis
    (the animal-wide order from the reader)."""
    if signals.empty:
        return TraceResult(traces=pd.DataFrame())

    df = signals.copy()
    # Engagement proxy needs all rows (gaps span abandoned trials too), computed
    # before we subset to completed trials.
    gap = _inter_trial_gap_s(df)
    df["_gap_s"] = gap

    c = df[bool_series(df["completed"])].copy()
    if c.empty:
        return TraceResult(traces=pd.DataFrame())

    free = c["epoch"].eq(EPOCH_FREE)
    own = bool_series(c["own_pair"])
    prev_correct = bool_series(c["prev_correct"])
    prev_incorrect = own & ~prev_correct

    traces = pd.DataFrame(
        {
            "seq": c["seq"].to_numpy(),
            "session_id": c["session_id"].to_numpy(),
            "date": c["date"].to_numpy(),
            # Per-trial context the mode labeler reads (epoch, which block owns an
            # in-block trial, and whether the animal complied with the pushed side).
            "epoch": c["epoch"].to_numpy(),
            "in_block": c["in_block"].fillna(False).to_numpy(),
            "probe": bool_series(c["probe"]).to_numpy() if "probe" in c
            else np.zeros(len(c), dtype=bool),
            "block_kind": c["block_kind"].replace("", pd.NA).to_numpy(),
            "complying": (c["choice"] == c["correct_arm"]).to_numpy(),
        }
    )
    traces["side_bias_left"] = _masked_rate(
        (c["choice"].eq(LEFT_LABEL) & free), (free & c["choice"].notna()),
        window, min_den,
    ).to_numpy()
    switch = bool_series(c["switch"])
    stay = bool_series(c["stay"])
    traces["switch_rate"] = _masked_rate(
        switch & own, own, window, min_den
    ).to_numpy()
    traces["win_stay"] = _masked_rate(
        stay & own & prev_correct, own & prev_correct, window, min_den
    ).to_numpy()
    traces["lose_shift"] = _masked_rate(
        ~stay & prev_incorrect, prev_incorrect, window, min_den
    ).to_numpy()
    traces["cue_follow"] = _masked_rate(
        bool_series(c["cue_followed"]) & free, free & c["cue_followed"].notna(),
        window, min_den,
    ).to_numpy()
    cue_switched = bool_series(c["cue_switched"])
    traces["cue_switch_tracks"] = _masked_rate(
        switch & own & cue_switched, own & cue_switched, window, min_den
    ).to_numpy()
    traces["reward_rate"] = _masked_rate(
        c["reward"].eq(1) & free, free, window, min_den
    ).to_numpy()
    traces["gap_s_med"] = (
        c["_gap_s"].rolling(window, min_periods=1).median().to_numpy()
    )

    return TraceResult(
        traces=traces,
        session_bounds=_session_bounds(c),
        blocks=block_epochs(c),
        probe_blocks=probe_epochs(c),
    )


def _inter_trial_gap_s(df: pd.DataFrame) -> pd.Series:
    """Seconds between successive ``trial_start_iso`` within a session/run (NaN at a
    session start). The engagement proxy until per-excursion logs exist."""
    if "trial_start_iso_dt" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    g = df.groupby(["session_id", "run_idx"], sort=False)["trial_start_iso_dt"]
    return g.diff().dt.total_seconds()


def _session_bounds(c: pd.DataFrame) -> List[dict]:
    """Start/end ``seq`` for each session, in order (for boundary markers)."""
    out: List[dict] = []
    for sid, grp in c.groupby("session_id", sort=False):
        out.append(
            {
                "session_id": sid,
                "date": grp["date"].iloc[0],
                "start_seq": int(grp["seq"].min()),
                "end_seq": int(grp["seq"].max()),
            }
        )
    return out


def block_epochs(c: pd.DataFrame) -> List[dict]:
    """Contiguous intervention-block runs as ``seq`` spans, with kind + trigger.

    A block is a maximal run of in-block trials sharing one ``block_id`` within a
    session. Returns one dict per block: ``session_id, kind, trigger, start_seq,
    end_seq, length`` - the spans the notebook shades and the peri-intervention
    analysis aligns to."""
    inb = c[c["in_block"].fillna(False) & c["block_id"].astype(str).ne("")]
    out: List[dict] = []
    for (sid, bid), grp in inb.groupby(["session_id", "block_id"], sort=False):
        grp = grp.sort_values("seq")
        kind = grp["block_kind"].replace("", "side_bias").iloc[0]
        trigger = grp["block_trigger"].iloc[0] or "auto"
        out.append(
            {
                "session_id": sid,
                "block_id": bid,
                "kind": kind,
                "trigger": trigger,
                "start_seq": int(grp["seq"].min()),
                "end_seq": int(grp["seq"].max()),
                "length": int(len(grp)),
            }
        )
    out.sort(key=lambda b: b["start_seq"])
    return out


def probe_epochs(c: pd.DataFrame) -> List[dict]:
    """Contiguous unrewarded probe-block runs as ``seq`` spans, with per-block
    accuracy - the offline twin of ``phase3_traces._probe_spans``.

    A probe block is a maximal run of ``probe``-flagged completed trials within a
    session/run (probe blocks are inserted at runtime and carry no block_id, so
    they are grouped by contiguity, not id). Returns one dict per block:
    ``session_id, start_seq, end_seq, length, n, accuracy`` - the spans the
    notebook shades and the per-block latent-knowledge accuracy it marks. See
    docs/decisions/0115-probe."""
    if "probe" not in c.columns:
        return []
    probe = bool_series(c["probe"])
    out: List[dict] = []
    run: List[int] = []

    def flush(sid) -> None:
        if not run:
            return
        grp = c.loc[run]
        n = len(grp)
        acc = (float(bool_series(grp["correct"]).sum()) / n) if n else None
        out.append({
            "session_id": sid,
            "start_seq": int(grp["seq"].min()),
            "end_seq": int(grp["seq"].max()),
            "length": n,
            "n": n,
            "accuracy": acc,
        })
        run.clear()

    for (sid, _run), grp in c.groupby(["session_id", "run_idx"], sort=False):
        for i in grp.index:
            if bool(probe.at[i]):
                run.append(i)
            elif run:
                flush(sid)
        flush(sid)
    out.sort(key=lambda b: b["start_seq"])
    return out


def has_excursion_data(excursions: Optional[pd.DataFrame]) -> bool:
    """Whether a non-empty per-excursion frame is available (else engagement is the
    inter-trial-gap proxy and the non-stem mode reads 'no data')."""
    return excursions is not None and not excursions.empty
