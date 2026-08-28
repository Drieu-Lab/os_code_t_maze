"""progression.py - per-session behavioural summary for the animal-progress notebook.

Reduces the derived per-trial signal frame (``choice_signals.add_signals``) to one
row per session with the headline behavioural numbers - accuracy and left-choice
bias over the session's OUT-OF-BLOCK trials (not an intervention block, not an
unrewarded probe). That baseline set matches the live GUI / ``phase3_stats`` and the
rolling traces (``rolling_metrics``); post-block recovery trials are included. See
docs/decisions/0132. Kept here, tested, so the notebook stays a thin front-end.

Pure pandas; no I/O. Headless tests: ``python -m unittest test_analysis_progression``.
"""
from __future__ import annotations

import pandas as pd

from tmaze.analysis.bundle_reader import LEFT_LABEL
from tmaze.analysis.choice_signals import bool_series

_COLUMNS = ["animal", "session_id", "date", "n", "accuracy", "side_bias_left"]


def per_session_summary(signals: pd.DataFrame) -> pd.DataFrame:
    """Per-session accuracy + left-choice bias over out-of-block trials.

    ``signals`` is the frame from ``choice_signals.add_signals`` (one or more
    animals, time-ordered). Returns one row per ``(animal, session_id, date)`` in
    session order with:

    - ``n``              - number of out-of-block completed trials in the session,
    - ``accuracy``       - fraction correct over those trials,
    - ``side_bias_left`` - fraction of those trials the animal chose LEFT.

    Out-of-block = completed AND not an intervention block AND not a probe (the same
    baseline set as ``rolling_metrics`` and the GUI; docs/decisions/0132). Sessions
    with no out-of-block trials are omitted; an empty input yields an empty frame.
    """
    if signals.empty:
        return pd.DataFrame(columns=_COLUMNS)
    c = signals[bool_series(signals["completed"])]
    oob = c[~bool_series(c["in_block"]) & ~bool_series(c["probe"])]
    if oob.empty:
        return pd.DataFrame(columns=_COLUMNS)
    return (
        oob.groupby(["animal", "session_id", "date"], sort=False)
        .agg(
            n=("correct", "size"),
            accuracy=("correct", "mean"),
            side_bias_left=("choice", lambda s: (s == LEFT_LABEL).mean()),
        )
        .reset_index()
    )
