"""
disk_space.py - free-disk-space display decision (pure core).

The Session panel shows how much room is left on the drive where the session
bundles are written, and flags it red when free space runs low (docs/decisions/
0151). The only logic that isn't Tk/OS wiring is turning a raw free-byte count +
the operator's threshold into (a) a short human label and (b) a yes/no "is this
low?" flag. That decision is pulled out here so it is stdlib-only and
headless-testable, like the other pure cores (session_timer.py, behavior_stats.py);
the Tk shell (session_controller.py) measures the disk via ``shutil.disk_usage``
and paints the label.

``free_bytes = None`` means the drive couldn't be measured (e.g. the folder's
path doesn't exist yet); we surface that as an explicit "unknown" rather than a
misleading 0 GB, and treat unknown as low so it draws attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

_BYTES_PER_GB = 1024 ** 3


@dataclass(frozen=True)
class DiskStatus:
    """The Session panel's free-space readout: the text to show, whether it is
    below the warning threshold (paint it red), and the measured free GB (``None``
    when the drive couldn't be read)."""
    text: str
    is_low: bool
    free_gb: Optional[float]


def disk_status(free_bytes: Optional[int],
                warn_gb: float) -> DiskStatus:
    """Turn a raw free-byte count and a warning threshold (GB) into the readout.

    ``free_bytes`` is ``None`` when the drive couldn't be measured; that shows as
    "Disk: unknown" and counts as low (so the operator notices). Otherwise the
    label is ``Disk: <N> GB free`` and ``is_low`` is true when the free space is
    at or below ``warn_gb``.
    """
    if free_bytes is None:
        return DiskStatus(text="Disk: unknown", is_low=True, free_gb=None)
    free_gb = free_bytes / _BYTES_PER_GB
    # Whole GB above ~10 GB (no false precision), one decimal when low so a
    # nearly-full drive reads e.g. "2.4 GB free" rather than a blunt "2 GB".
    shown = f"{free_gb:.0f}" if free_gb >= 10 else f"{free_gb:.1f}"
    return DiskStatus(text=f"Disk: {shown} GB free",
                      is_low=free_gb <= warn_gb,
                      free_gb=free_gb)
