"""
timeutil.py - one home for host wall-clock ISO stamps.

Every host-side ISO 8601 timestamp the rig writes (manifest t0/t_end, trial +
excursion ``*_iso`` columns, serial receipt stamps, sidecar ``session_t0_iso``,
notes, the summary/config ``generated_iso``, and the records/registry stamps)
goes through ``now_iso`` so they are all **offset-aware** - they carry the host's
UTC offset, e.g. ``2026-09-19T13:12:00.123-04:00`` (review finding S9,
docs/decisions/0182).

Why it matters: the sidecar's ``host_unix_s`` is POSIX epoch seconds (an absolute
instant); the CSV ISO stamps are host wall time. To align an event with a video
frame, the analysis converts the ISO string to POSIX with ``.timestamp()`` - and
on a NAIVE datetime that conversion is interpreted in the READER machine's local
timezone. A bundle analysed on a machine in a different zone than the rig then
mis-aligns by the offset. An offset in the string makes the instant unambiguous,
so the reader's timezone no longer matters.

Pure stdlib; no Tk/IO. Headless tests: python -m unittest test_timeutil (host/).
"""

from __future__ import annotations

import datetime as _dt


def now_iso(timespec: str = "milliseconds") -> str:
    """Current host wall-clock time as an OFFSET-AWARE ISO 8601 string.

    ``datetime.now().astimezone()`` attaches the host's current UTC offset (incl.
    DST), so the instant is unambiguous regardless of where the bundle is later
    read. ``timespec`` controls sub-second precision (``"milliseconds"`` for the
    bundle streams; ``"seconds"`` for the records change log).
    """
    return _dt.datetime.now().astimezone().isoformat(timespec=timespec)
