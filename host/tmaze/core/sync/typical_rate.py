"""typical_rate.py - the rig's typical camera-vs-Arduino clock drift rate.

``host/sync_clock_rate.json`` holds the drift rate this rig's camera and Arduino
show across long sessions (they agree to ~0.4 ppm), so a short session whose
own rate is poorly determined can use it instead (``clockfit.fit_clock``;
ADR-0209). It is a property of the HARDWARE pair: replace the camera or the
Arduino and the value must be re-measured (``sync_fit`` flags a well-determined
session whose rate is far from it).

Schema (``schema_version`` 1)::

    {"schema_version": 1, "rate": <camera ticks per firmware ms>,
     "sd_ppm": <between-session spread>, "n_sessions": ..., "derived_from": "...",
     "date": "YYYY-MM-DD", "camera": "...", "arduino": "...", "_note": "..."}

Stdlib-only. A missing or unreadable file means "no typical rate": every
session then uses its own rate, as before.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from tmaze.core.sync.clockfit import RatePrior
from tmaze.paths import host_dir

SCHEMA_VERSION = 1
FILENAME = "sync_clock_rate.json"


def typical_rate_path() -> str:
    """Default location: ``host/sync_clock_rate.json`` beside ``led_map.json``."""
    return os.path.join(str(host_dir()), FILENAME)


def load(path: Optional[str] = None) -> Optional[RatePrior]:
    """The typical rate, or None when the file is absent, unreadable or invalid."""
    path = path or typical_rate_path()
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        rate, sd = float(d["rate"]), float(d["sd_ppm"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if rate <= 0 or sd < 0:
        return None
    return RatePrior(rate, sd, source=f"{os.path.basename(path)} ({d.get('derived_from', '')})")
