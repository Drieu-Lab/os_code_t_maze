"""
records_audit_ack.py - the operator's acknowledged-integrity-findings store (core).

The read-only integrity report (``records_audit``, ADR-0097) surfaces
cross-record drift for the operator to fix. But some findings can never be
"fixed": a genuinely short real session, an old bundle whose animal id predates
``animals.json``, a husbandry row that legitimately has no matching session. Left
in the report, those permanent findings bury the new, actionable ones. So each
finding has a stable identity key (``records_audit.Finding.key``), and the
operator can ACKNOWLEDGE a key here to dismiss it from the active count; the audit
then partitions findings into active vs acknowledged rather than deleting
anything (reversible - un-acknowledging brings it back). This is the audit's
counterpart to ``session_ignore.py`` (which excludes whole sessions); here we
dismiss individual findings.

The store lives in ``host/records_audit_ack.json`` (tracked, the same ``host/``
JSON pattern as ``session_ignore.json``) so the acknowledgements are shared and
diffable. Shape:

    {"_schema": 1,
     "acknowledged": {"<finding key>": {"reason": "...", "date": "YYYY-MM-DD"}}}

Load is best-effort (a missing/malformed file acknowledges nothing). Writes are
atomic (temp file + ``os.replace``) so a crash can't leave a half-file.

Pure stdlib (``json``/``os``/``datetime``), headlessly testable.

Headless tests: python -m unittest test_records_audit_ack.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Dict, Optional, Set

from tmaze.paths import host_dir

# Bumped if the file's shape changes; written into a fresh file's ``_schema``.
SCHEMA_VERSION = 1


def default_ack_path() -> str:
    """Path to ``host/records_audit_ack.json`` in this (code) repo."""
    return os.path.join(str(host_dir()), "records_audit_ack.json")


def load_ack(path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """The ``{key: {reason, date}}`` acknowledgement map, read best-effort.

    A missing/malformed file, or a missing/non-dict ``acknowledged`` key, yields
    an empty dict so the audit dismisses nothing rather than failing. Each value
    is coerced to a ``{reason, date}`` dict (strings), tolerating older/looser
    entries (a bare string value is read as the reason).
    """
    p = path or default_ack_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    acked = raw.get("acknowledged")
    if not isinstance(acked, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for key, meta in acked.items():
        k = str(key).strip()
        if not k:
            continue
        if isinstance(meta, dict):
            out[k] = {"reason": str(meta.get("reason", "") or ""),
                      "date": str(meta.get("date", "") or "")}
        else:
            out[k] = {"reason": str(meta or ""), "date": ""}
    return out


def load_acked_keys(path: Optional[str] = None) -> Set[str]:
    """Just the set of acknowledged finding keys (convenience over ``load_ack``)."""
    return set(load_ack(path).keys())


def _write(acked: Dict[str, Dict[str, str]], path: str) -> None:
    """Serialise the acknowledgement map atomically (temp file + os.replace)."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    payload = {"_schema": SCHEMA_VERSION,
               "acknowledged": {k: acked[k] for k in sorted(acked)}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def acknowledge(key: str, reason: str = "",
                path: Optional[str] = None,
                today: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """Acknowledge (dismiss) one finding ``key`` with an optional ``reason``.

    Records today's date (ISO) unless ``today`` is given (tests inject a date).
    Merges into the existing store and returns the updated map. A blank key is a
    no-op."""
    p = path or default_ack_path()
    k = str(key).strip()
    acked = load_ack(p)
    if k:
        acked[k] = {"reason": str(reason or ""),
                    "date": today or _dt.date.today().isoformat()}
        _write(acked, p)
    return acked


def unacknowledge(key: str,
                  path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    """Remove one finding ``key`` from the store (bring it back into the active
    report). Returns the updated map; a missing key is a no-op."""
    p = path or default_ack_path()
    k = str(key).strip()
    acked = load_ack(p)
    if k in acked:
        del acked[k]
        _write(acked, p)
    return acked
