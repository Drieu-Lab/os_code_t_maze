"""
spans.py - the inputs of the sync core, reduced to one shape: time SPANS.

A *span* is "channel X was in state Y from t0 to t1". Both streams we align
are expressed this way before anything is matched:

- **Serial spans** come from the per-session serial capture
  (``serial/serial_<id>.csv``, the full stream since docs/decisions/0178, or the
  pre-0178 ``sensors_<id>.csv``: one ``<host iso>,<firmware line>`` per row).
  A beam that reads ``S,2,0`` at t0 and ``S,2,1`` at t1 gives a span of channel
  ``S2=0``; the opposite pairing gives ``S2=1``. Channel names keep the WIRE
  state. Both states are emitted, but only the ACTIVE one (beam broken, tongue
  on the port) is matched (``active_spans``), and which wire state that is
  depends on the firmware that recorded the bundle: ``0`` on pre-v8 firmware,
  ``1`` from v8 on (docs/decisions/0164, 0199). The caller passes the bundle's
  ``sensor_polarity``; when the bundle does not know it, ``infer_polarity``
  reads it off the log (the active state is the brief one). A solenoid ack
  ``SOL,2,1,300`` is a span of channel ``SOL2.1`` lasting its pulse width.
  ``REWARD`` duplicates ``SOL`` and is skipped.
- **LED spans** come from the vision layer's per-frame brightness trace for one
  LED, turned into lit / dark spans here by ``hysteresis_edges`` +
  ``spans_from_edges``. Each edge carries the frame's host stamp and the
  camera's own clock (``cam_timestamp``), read from the video sidecar.

Every span carries a **host** start/end (the PC wall clock, the coarse shared
reference both streams have) and, when known, the stream's **own clock** at
each edge (firmware ms for serial spans, camera ticks for LED spans). The
host times are only used to decide which spans are *candidates* for each
other; the clock fit runs on the own-clock stamps (clockfit.py).

Firmware-clock **segments**: the firmware's session clock restarts on a MODE
switch (ADR-0064). Serial parsing numbers segments by watching for the ms
value to run backwards, so the fit can give each segment its own offset. In the
48 sessions recorded to date there is never a mid-session MODE switch, so this
is a safety net.

Stdlib-only; see docs/design/cross-stream-sync-design.md (Phase 1) and
tests/test_sync_core.py.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from tmaze.core import protocol
from tmaze.core.phase3.bundle_format import POLARITY_CORRECTED, POLARITY_LEGACY


# ---------------------------------------------------------------- the record

@dataclass(frozen=True)
class Span:
    """One "channel was in a state" interval.

    host_t0 / host_t1  - PC wall clock (POSIX seconds) at the two edges.
    clk0 / clk1        - the stream's own clock at the two edges (firmware ms for
                         serial spans, camera ticks for LED spans); None if unknown.
    clk_unit_s         - seconds per tick of that clock (1e-3 for firmware ms,
                         1e-9 for camera ns); lets durations and gaps be measured
                         on the steady own clock instead of the jittery host one.
    ref0 / ref1        - a traceability handle per edge: serial line number, or
                         video frame index. Never used for arithmetic.
    segment            - firmware-clock segment (serial spans; LED spans stay 0).
    """
    channel: str
    host_t0: float
    host_t1: float
    clk0: Optional[float] = None
    clk1: Optional[float] = None
    ref0: Optional[int] = None
    ref1: Optional[int] = None
    segment: int = 0
    clk_unit_s: Optional[float] = None

    @property
    def has_clock(self) -> bool:
        return self.clk0 is not None and self.clk1 is not None and self.clk_unit_s is not None

    @property
    def duration(self) -> float:
        """Seconds, on the span's own clock when known (host stamps of serial
        lines jitter by tens of ms when the PC reads several lines at once)."""
        if self.has_clock:
            return (self.clk1 - self.clk0) * self.clk_unit_s
        return self.host_t1 - self.host_t0


def gap_s(prev: Span, nxt: Span) -> float:
    """Seconds from the end of ``prev`` to the start of ``nxt``, on their own
    clock when both have the same one, else on the host clock."""
    if prev.has_clock and nxt.has_clock and prev.clk_unit_s == nxt.clk_unit_s \
            and prev.segment == nxt.segment:
        return (nxt.clk0 - prev.clk1) * prev.clk_unit_s
    return nxt.host_t0 - prev.host_t1


FIRMWARE_MS = 1e-3     # clk_unit_s of the Arduino's millis() clock

# The wire state in which a two-state sensor is ACTIVE (the event the camera
# can see: beam broken, tongue on the port), per bundle ``sensor_polarity``.
# Beams and licks flipped together in firmware v8 (docs/decisions/0164): pre-v8
# logs report the active state as 0, v8+ logs as 1. See docs/decisions/0199.
_ACTIVE_STATE = {POLARITY_LEGACY: "0", POLARITY_CORRECTED: "1"}


def active_state(polarity: str) -> str:
    """``"0"`` or ``"1"``: the wire state of an active beam/lick under
    ``polarity`` (``legacy_inverted`` or ``corrected``). ``unknown`` is refused
    - resolve it first (``infer_polarity``); guessing is how the sync matched
    the resting spans of v8+ bundles."""
    try:
        return _ACTIVE_STATE[polarity]
    except KeyError:
        raise ValueError(f"sensor polarity must be {POLARITY_CORRECTED!r} or "
                         f"{POLARITY_LEGACY!r}, got {polarity!r}") from None


def is_active_channel(channel: str, polarity: str) -> bool:
    """Is ``channel`` a sensor's active state under ``polarity``? Legacy:
    ``S2=0`` / ``L2=0`` -> True, ``S2=1`` / ``L2=1`` -> False; corrected: the
    reverse. Pulse channels (``SOL2.1``) are always active."""
    base, sep, state = channel.rpartition("=")
    if not sep or channel_kind(base) not in ("S", "L"):
        return True
    return state == active_state(polarity)


def active_spans(events: Dict[str, List[Span]], polarity: str) -> Dict[str, List[Span]]:
    """Keep only the channels an LED is matched against (see is_active_channel)."""
    return {ch: spans for ch, spans in events.items() if is_active_channel(ch, polarity)}


class PolarityGuess(NamedTuple):
    """What a serial log says about its own polarity (``infer_polarity``)."""
    polarity: Optional[str]     # None = the log does not say clearly
    n_agree: int                # sensors voting for ``polarity``
    n_votes: int                # sensors that voted at all


def infer_polarity(events: Dict[str, List[Span]], min_ratio: float = 5.0) -> PolarityGuess:
    """Read the S/L polarity off the log itself: the active state is the BRIEF
    one. A beam crossing or a tongue contact lasts well under two seconds; the
    resting state between events runs for tens of seconds or minutes. Sensor
    chatter splits the resting state into short pieces too, so each sensor
    compares its LONGEST span in each state, and votes only when one is at
    least ``min_ratio`` times the other. The answer is the unanimous vote;
    any dissent (or no vote) gives ``polarity=None``."""
    votes = {"0": 0, "1": 0}
    bases = {ch.rpartition("=")[0] for ch in events
             if "=" in ch and channel_kind(ch) in ("S", "L")}
    for base in bases:
        zero, one = events.get(f"{base}=0", []), events.get(f"{base}=1", [])
        if not zero or not one:
            continue
        long0 = max(s.duration for s in zero)
        long1 = max(s.duration for s in one)
        if long1 >= min_ratio * long0:
            votes["0"] += 1                 # 0 is brief -> active
        elif long0 >= min_ratio * long1:
            votes["1"] += 1
    n = votes["0"] + votes["1"]
    if n == 0 or (votes["0"] and votes["1"]):
        return PolarityGuess(None, max(votes.values()), n)
    state = "0" if votes["0"] else "1"
    pol = POLARITY_LEGACY if state == "0" else POLARITY_CORRECTED
    return PolarityGuess(pol, n, n)


def channel_kind(channel: str) -> str:
    """The letters a channel name starts with: ``S2=0`` -> ``S``, ``SOL2.1`` ->
    ``SOL``, ``L2=1`` -> ``L``. Used to pick which channels feed the fit."""
    out = []
    for ch in channel:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


# ------------------------------------------------------------ host timestamps

def iso_to_unix(iso: str) -> float:
    """Host ISO wall-clock stamp (naive, local time, as the loggers write it)
    -> POSIX seconds, the same axis as the sidecar's ``host_unix_s``."""
    return datetime.fromisoformat(iso).timestamp()


# ------------------------------------------------------------- serial spans

def parse_serial_csv(lines: Iterable[str]) -> List[Tuple[int, str, str]]:
    """Rows of a per-session serial capture -> [(line_no, host_iso, firmware_line)].

    Skips ``#`` comment rows and blanks. The file's own header documents the
    layout: ``wall_clock_iso,firmware_line (ms,TYPE,payload...)``.
    """
    out: List[Tuple[int, str, str]] = []
    for n, raw in enumerate(lines, 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.startswith("#"):
            continue
        iso, sep, fw = line.partition(",")
        if not sep:
            continue
        out.append((n, iso, fw))
    return out


def serial_spans(rows: Sequence[Tuple[int, str, str]], default_sol_ms: int = 300
                 ) -> Dict[str, List[Span]]:
    """Serial rows -> spans per channel (see module docstring for the channels).

    Beams and licks: consecutive opposite edges of the same sensor form a span of
    the state that was held between them (``S2=1`` for a 1->0 pair, ``S2=0`` for
    a 0->1 pair); both states are returned, filter with ``active_spans`` before
    matching. Solenoids: the ack line to ack + pulse width (the trailing
    ``<dur_ms>`` field when present, else ``default_sol_ms``).

    Malformed rows and unparsable host stamps are skipped, never raised: a
    session log with one garbled line must still align.
    """
    out: Dict[str, List[Span]] = {}
    last_edge: Dict[str, Tuple[float, int, bool, int, int]] = {}   # sensor -> (t, ms, active, line, seg)
    segment = 0
    prev_ms: Optional[int] = None
    for line_no, iso, fw in rows:
        p = protocol.parse(fw)
        if p is None:
            continue
        try:
            t = iso_to_unix(iso)
        except ValueError:
            continue
        if prev_ms is not None and p.ms < prev_ms - 1:
            segment += 1                       # firmware clock ran backwards: MODE reset
        prev_ms = p.ms

        if p.type in ("S", "L"):
            edge = protocol.classify(fw)
            e = edge.sensor if p.type == "S" else edge.lick
            if e is None:
                continue
            idx, active = e
            key = f"{p.type}{idx}"
            prev = last_edge.get(key)
            if prev is not None and prev[2] != active and prev[4] == segment:
                pt, pms, pactive, pline, _ = prev
                out.setdefault(f"{key}={'1' if pactive else '0'}", []).append(
                    Span(f"{key}={'1' if pactive else '0'}", pt, t, pms, p.ms,
                         pline, line_no, segment, FIRMWARE_MS))
            last_edge[key] = (t, p.ms, active, line_no, segment)
        elif p.type == "SOL":
            ps = protocol.sol_pulse_from(p)
            if ps is None:
                continue
            dur_field = p.fields[2].strip() if len(p.fields) > 2 else ""
            dur_ms = int(dur_field) if dur_field.isdigit() else default_sol_ms
            ch = f"SOL{ps[0]}.{ps[1]}"
            out.setdefault(ch, []).append(
                Span(ch, t, t + dur_ms / 1000.0, p.ms, p.ms + dur_ms, line_no, line_no, segment,
                     FIRMWARE_MS))
    for spans in out.values():
        spans.sort(key=lambda s: s.host_t0)
    return out


# ------------------------------------------------------------- video frames

@dataclass(frozen=True)
class Frame:
    """One video frame as the sidecar records it."""
    idx: int
    host_unix_s: float
    cam_ts: Optional[int]


def parse_sidecar_csv(lines: Iterable[str]) -> Tuple[Dict[str, str], List[Frame]]:
    """Video sidecar rows -> (header key/values, frames).

    The sidecar starts with ``# key: value`` rows (codec, fps, undistorted, ...)
    then a CSV header and one row per frame with at least ``frame_idx``,
    ``host_unix_s`` and ``cam_timestamp`` (blank when the driver gave none).
    """
    header: Dict[str, str] = {}
    frames: List[Frame] = []
    cols: Optional[Dict[str, int]] = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("#"):
            body = line[1:].strip()
            if ":" in body:
                k, v = body.split(":", 1)
                header[k.strip()] = v.strip()
            continue
        parts = line.split(",")
        if cols is None:
            cols = {name.strip(): i for i, name in enumerate(parts)}
            continue
        try:
            idx = int(parts[cols["frame_idx"]])
            host = float(parts[cols["host_unix_s"]])
        except (KeyError, IndexError, ValueError):
            continue
        ts_i = cols.get("cam_timestamp")
        ts_s = parts[ts_i].strip() if ts_i is not None and ts_i < len(parts) else ""
        frames.append(Frame(idx, host, int(ts_s) if ts_s.lstrip("-").isdigit() else None))
    return header, frames


# ------------------------------------------------------- trace -> LED spans

def _medfilt3(values: Sequence[float]) -> List[float]:
    """3-point running median: kills single-frame glitches, keeps real edges."""
    n = len(values)
    if n < 3:
        return list(values)
    out = [0.0] * n
    out[0], out[-1] = values[0], values[-1]
    for i in range(1, n - 1):
        a, b, c = values[i - 1], values[i], values[i + 1]
        out[i] = a + b + c - min(a, b, c) - max(a, b, c)
    return out


def hysteresis_edges(values: Sequence[float], on_frac: float = 0.6, off_frac: float = 0.4,
                     min_span: float = 15.0) -> Tuple[List[int], List[int], float]:
    """Per-frame brightness -> (rising edge indices, falling edge indices, span).

    Levels come from the MIN and MAX of the median-filtered trace rather than
    from percentiles: a reward LED is lit for ~1 % of frames and any percentile
    would miss it (design doc section 3.7). The LED is declared lit when the
    trace exceeds ``off + on_frac * span`` and dark when it drops below
    ``off + off_frac * span`` (hysteresis: two thresholds, so noise near one
    level cannot chatter). A span below ``min_span`` brightness units means the
    LED never changed - no edges. An edge is stamped on the FIRST frame in the
    new state. ``None`` values are treated as "no information" and skipped.
    """
    clean = [v for v in values if v is not None]
    if len(clean) < 10:
        return [], [], 0.0
    filled = list(values)
    fill = sorted(clean)[len(clean) // 2]
    filled = [fill if v is None else v for v in filled]
    sm = _medfilt3(filled)
    lo_lvl, hi_lvl = min(sm), max(sm)
    span = hi_lvl - lo_lvl
    if span < min_span:
        return [], [], span
    hi = lo_lvl + on_frac * span
    lo = lo_lvl + off_frac * span
    state = sm[0] > hi
    rising: List[int] = []
    falling: List[int] = []
    for k in range(1, len(sm)):
        x = sm[k]
        if not state and x > hi:
            state = True
            rising.append(k)
        elif state and x < lo:
            state = False
            falling.append(k)
    return rising, falling, span


def spans_from_edges(rising: Sequence[int], falling: Sequence[int], frames: Sequence[Frame],
                     channel: str, lit: bool, clk_unit_s: Optional[float] = None) -> List[Span]:
    """Edge indices (into ``frames``) -> spans of the LED being lit (rise->fall)
    or dark (fall->rise). Unterminated trailing states produce no span.
    ``clk_unit_s`` is the camera tick length in seconds (see ``cam_tick_s``)."""
    starts, ends = (rising, falling) if lit else (falling, rising)
    out: List[Span] = []
    j = 0
    for s in starts:
        while j < len(ends) and ends[j] <= s:
            j += 1
        if j >= len(ends):
            break
        e = ends[j]
        fs, fe = frames[s], frames[e]
        out.append(Span(channel, fs.host_unix_s, fe.host_unix_s,
                        None if fs.cam_ts is None else float(fs.cam_ts),
                        None if fe.cam_ts is None else float(fe.cam_ts),
                        fs.idx, fe.idx, 0, clk_unit_s))
    return out


def cam_tick_s(frames: Sequence[Frame]) -> Optional[float]:
    """Seconds per camera tick, measured from the sidecar itself: total ticks
    over total host seconds between the first and last stamped frames (the
    27 ppm drift is irrelevant at this precision). None if unmeasurable. The
    Basler acA1300 ticks at 1 GHz, i.e. 1e-9 (design doc section 3.7)."""
    stamped = [f for f in frames if f.cam_ts is not None]
    if len(stamped) < 2:
        return None
    a, b = stamped[0], stamped[-1]
    if b.cam_ts == a.cam_ts or b.host_unix_s <= a.host_unix_s:
        return None
    return (b.host_unix_s - a.host_unix_s) / (b.cam_ts - a.cam_ts)


def visible(spans: Sequence[Span], min_duration_s: float) -> List[Span]:
    """What a camera could have seen of these spans (same channel, sorted).

    Two rules, one threshold. A GAP shorter than ``min_duration_s`` between two
    spans is a bounce of the opposite state too brief for a frame to register
    (beam chatter: ``S,2,0 ... S,2,1 (+36 ms) S,2,0 ... S,2,1``): the video shows
    one continuous span, so the two are coalesced into one, keeping the first
    start and the last end. Then any span still shorter than ``min_duration_s``
    is dropped - a 40 ms frame cannot register a 15 ms flicker. Recall is judged
    against what remains, and the edges that remain are the electrical edges the
    camera actually saw, which is what the clock fit needs. Gaps and durations
    are measured on the spans' own clock when they have one (``gap_s``).
    """
    if not spans:
        return []
    out: List[Span] = []
    cur = spans[0]
    for s in spans[1:]:
        if gap_s(cur, s) < min_duration_s and s.segment == cur.segment:
            cur = replace(cur, host_t1=s.host_t1, clk1=s.clk1, ref1=s.ref1)
        else:
            out.append(cur)
            cur = s
    out.append(cur)
    return [s for s in out if s.duration >= min_duration_s]


def relabel(spans: Sequence[Span], channel: str) -> List[Span]:
    return [replace(s, channel=channel) for s in spans]
