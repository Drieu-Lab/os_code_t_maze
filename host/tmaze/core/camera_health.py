"""camera_health.py - pure helpers for camera acquisition-health accounting.

Kept stdlib-only and headless-testable so the Tk/pypylon camera shell
(``tmaze.ui.camera_view``) stays thin (pure core + thin shell). See
docs/decisions/0147.
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional, Tuple


def recording_deltas(current: Mapping[str, int],
                     baseline: Mapping[str, int]) -> Dict[str, int]:
    """Per-recording counter deltas = current cumulative minus the snapshot taken
    when the recording opened (docs/decisions/0147, review finding L8).

    The camera-health counters (``grabbed`` / ``src_drops`` / ``incomplete`` /
    ``queue_drops`` / ``track_skips``) run cumulatively since app launch; only
    ``saved`` resets per recording. Subtracting the open-time baseline gives the
    count FOR THIS recording - what a multi-recording session bundle needs to
    answer "how many drops happened in THIS video" (bug #260810_CameraSrcDrops),
    which the lifetime totals alone can't. A key present in ``current`` but not in
    ``baseline`` is treated as baseline 0; each delta is clamped at 0 so a counter
    reset / rollover can never report a negative."""
    out: Dict[str, int] = {}
    for k, v in current.items():
        d = v - baseline.get(k, 0)
        out[k] = d if d >= 0 else 0
    return out


def account_block_gap(last_block: Optional[int],
                      block_id: Optional[int]) -> Tuple[Optional[int], int]:
    """Fold one delivered frame's camera BlockID into the source-drop tally.

    The camera stamps every buffer it delivers - complete OR incomplete - with a
    monotonically increasing BlockID. A jump larger than +1 between consecutive
    *delivered* buffers means the camera produced frames the host never received
    at all (true source drops). Returns ``(new_last_block, src_drop_delta)``.

    Call this on EVERY delivered buffer, including incomplete ones. Advancing
    ``last_block`` on an incomplete grab is what stops it from manufacturing a
    phantom gap (and thus a phantom source drop) for the next good frame - the
    pre-0147 double-count where ``src-drop`` mirrored ``incomplete`` one-for-one
    (bug #260810_CameraSrcDrops).

    Edge cases: a missing BlockID (``None``) can't be reasoned about, so the
    tally is untouched; the first delivered frame seeds ``last_block`` with no
    drop; a BlockID at or below ``last_block`` (a duplicate, out-of-order, or a
    counter wrap) keeps the running maximum and counts nothing, so the tally can
    never go negative.
    """
    if block_id is None:
        return last_block, 0
    if last_block is None:
        return block_id, 0
    if block_id > last_block:
        return block_id, block_id - last_block - 1
    return last_block, 0
