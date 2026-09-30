"""
bundle_audit.py - read-only integrity checks INSIDE each session bundle (pure core).

Bug ``#260720_DataLogging`` asked whether data logging has integrity issues:
completeness, correct per-session routing, no dropped or duplicated records.
``records_audit`` checks the records AROUND the sessions (husbandry rows,
annotations, the registry); this core checks what each bundle holds against
itself. The streams of one session record the same events more than once - a
reward is a trial-CSV row, a ``REWARD`` echo in the serial capture and a count
in the summary - so they can be reconciled (docs/decisions/0204):

- ``files``     every stream the manifest lists exists, inside its own bundle;
- ``config``    each snapshotted config file still matches its SHA-256;
- ``identity``  the session id / animal agree across manifest, trial CSV,
                serial capture, video sidecar and animal state;
- ``trials``    trial numbers 1..N without gaps or repeats, times in order and
                inside the session;
- ``rewards``   serial reward pulses == rewarded trials + top-ups + hand-given
                pulses, and each rewarded trial has its pulse;
- ``cues`` / ``licks``  each trial's firmware-clock stamps exist in the capture
                (they are copied from those lines, so the match is exact);
- ``serial``    no lines dropped by the firmware (``TXDROP``), no silences
                longer than the liveness probe allows, no firmware clock jumps;
- ``summary``   the summary's counts equal the trial CSV's;
- ``video``     frames lost by the camera, the sidecar covers the session.

``bundle_audit_inputs`` reads the files into ``BundleInputs``; nothing here
touches the disk, so a check that fails is testable from a dict. A check whose
inputs a bundle predates (e.g. ``TXDROP`` before firmware v9) is listed in
``BundleAudit.not_checked`` rather than reported as a problem.

Pure stdlib; headless tests: python -m unittest tests.test_bundle_audit
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

ERROR, WARNING, NOTE = "error", "warning", "note"

# Firmware-clock stamps in the trial CSV are copied from the serial echo lines
# (phase3_log: choice_cue_on_fw_ms / reward_cmd_fw_ms / lick_fw_ms), so a real
# stamp matches its line exactly; 2 ms only absorbs rounding.
FW_MATCH_TOL_MS = 2
# The broker probes a port after 10 s of silence and gives up at 25 s
# (serial_liveness, ADR-0062), so a healthy full capture never goes longer than
# this without a line; a longer gap is a link that stopped answering.
LINK_SILENCE_S = 30.0
# A firmware clock that runs backwards by more than this is a board reset.
FW_BACKWARDS_MS = 1000
# A trial ending this long after Stop was still being written after the session.
END_SLACK_S = 30.0
# A trial starting this long before Start belongs to an earlier recording of the
# same run (the recording was restarted mid-task, ADR-0198).
PRE_START_SLACK_S = 5.0
# The video sidecar's animal when the camera was never told the animal id.
SIDECAR_ANIMAL_PLACEHOLDER = "animal"
# From summary schema v5 ``manual_water`` counts operator pulses only; before,
# it counted every REWARD echo, task pulses included (ADR-0084).
MANUAL_OPERATOR_ONLY_FROM = 5
# Video starting later / ending earlier than the session by more than this.
VIDEO_COVER_SLACK_S = 5.0
# Frames the camera lost above this fraction are a warning; below, a note.
LOST_FRAMES_WARN = 0.01
# Streams whose file must be non-empty (notes / excursions may legitimately be).
MUST_HAVE_DATA = ("phase3_task", "serial_full", "camera_video", "camera_sidecar")
# TXDROP and the TTL/VERSION lines arrived with the v9 firmware (ADR-0193).
TXDROP_FROM_PROTO = 9


class Issue(NamedTuple):
    """One problem found in one bundle. ``key`` is stable across runs (so it can
    be acknowledged): ``<check>:<session id>:<detail>``."""
    check: str
    severity: str
    key: str
    text: str


@dataclass(frozen=True)
class StreamFile:
    """A manifest stream as the shell found it on disk."""
    name: str
    path: str               # as written in the manifest
    resolved: Optional[str]  # where it was found (None = not found)
    size: Optional[int]      # bytes (None = not found)
    inside: bool             # the path names this bundle's folder
    relocated: bool = False  # found only by re-rooting an absolute path


@dataclass
class BundleInputs:
    """Everything the checks read, already loaded by the shell. Any of it may be
    missing (None / empty) for an old or damaged bundle."""
    folder_id: str
    manifest: Optional[dict] = None
    streams: List[StreamFile] = field(default_factory=list)
    trial_header: Dict[str, str] = field(default_factory=dict)
    trial_rows: List[dict] = field(default_factory=list)
    serial_header: Dict[str, str] = field(default_factory=dict)
    serial_rows: List[Tuple[int, str, str]] = field(default_factory=list)   # (line_no, wall_iso, fw_line)
    # The pre-ADR-0178 ``sensors_<id>.csv`` keeps only beams + central-port
    # lines, so cue / liveness checks can't run on it.
    serial_filtered: bool = False
    summary: Optional[dict] = None
    snapshot: Optional[dict] = None
    config_hashes: Dict[str, Optional[str]] = field(default_factory=dict)   # filename -> actual sha256
    sidecar_header: Dict[str, str] = field(default_factory=dict)
    sidecar_rows: Optional[int] = None
    sidecar_span_s: Optional[Tuple[float, float]] = None                   # first/last host_unix_s
    camera_stats: Optional[dict] = None
    animal_state: Optional[dict] = None
    firmware_proto: Optional[int] = None


@dataclass
class BundleAudit:
    session_id: str
    issues: List[Issue] = field(default_factory=list)
    not_checked: Dict[str, str] = field(default_factory=dict)              # check -> why
    t0_s: Optional[float] = None
    t_end_s: Optional[float] = None

    @property
    def status(self) -> str:
        """``errors`` / ``warnings`` / ``ok`` (notes don't count)."""
        sev = {i.severity for i in self.issues}
        return "errors" if ERROR in sev else "warnings" if WARNING in sev else "ok"

    def count(self, severity: str) -> int:
        return sum(1 for i in self.issues if i.severity == severity)


# ----------------------------------------------------------------- helpers

def _iso_s(v) -> Optional[float]:
    if not v:
        return None
    try:
        return _dt.datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _wall(stamp) -> Optional[float]:
    if isinstance(stamp, dict):
        w = stamp.get("wall")
        return float(w) if isinstance(w, (int, float)) else _iso_s(stamp.get("iso"))
    return None


def _fw(line: str) -> Tuple[Optional[int], str, List[str]]:
    """``"123,SOL,2,1,300"`` -> (123, "SOL", ["2", "1", "300"])."""
    parts = line.split(",")
    ms = _int(parts[0]) if parts else None
    return ms, (parts[1] if len(parts) > 1 else ""), parts[2:]


def _near(sorted_ms: Sequence[int], x: int, tol: int) -> bool:
    import bisect
    i = bisect.bisect_left(sorted_ms, x - tol)
    return i < len(sorted_ms) and sorted_ms[i] <= x + tol


def live_rows(rows: Sequence[dict]) -> List[dict]:
    """The latest attempt of each trial (re-run attempts are ``superseded=1``)."""
    return [r for r in rows if str(r.get("superseded", "")).strip() != "1"]


def played(rows: Sequence[dict]) -> List[dict]:
    """Live rows that were actually run (unplayed planned rows have no outcome)."""
    return [r for r in live_rows(rows) if str(r.get("outcome", "")).strip()]


def _issue(check: str, sev: str, sid: str, detail: str, text: str) -> Issue:
    return Issue(check, sev, f"{check}:{sid}:{detail}", text)


# ----------------------------------------------------------------- checks

def check_files(b: BundleInputs, sid: str) -> List[Issue]:
    out = []
    for s in b.streams:
        if s.resolved is None:
            out.append(_issue("files", ERROR, sid, f"missing:{s.name}",
                              f"stream {s.name} is missing ({s.path})"))
            continue
        if not s.inside:
            out.append(_issue("files", ERROR, sid, f"outside:{s.name}",
                              f"stream {s.name} points outside its bundle ({s.path})"))
        if s.name in MUST_HAVE_DATA and not s.size:
            out.append(_issue("files", ERROR, sid, f"empty:{s.name}", f"stream {s.name} is empty"))
    listed = {s.name for s in b.streams}
    if b.manifest is not None:
        if "phase3_task" not in listed:
            out.append(_issue("files", WARNING, sid, "unlisted:phase3_task",
                              "the manifest lists no trial CSV (phase3_task)"))
        if not listed & {"serial_full", "serial_sensors"}:
            out.append(_issue("files", WARNING, sid, "unlisted:serial",
                              "the manifest lists no serial capture"))
    return out


def check_config(b: BundleInputs, sid: str) -> List[Issue]:
    out = []
    for f in (b.snapshot or {}).get("files", []):
        if not f.get("present") or not f.get("sha256"):
            continue
        name = f.get("filename") or f.get("name")
        actual = b.config_hashes.get(name)
        if actual is None:
            out.append(_issue("config", ERROR, sid, f"missing:{name}",
                              f"snapshotted config {name} is missing from config/"))
        elif actual != f["sha256"]:
            out.append(_issue("config", ERROR, sid, f"changed:{name}",
                              f"config/{name} no longer matches the SHA-256 recorded at Start "
                              "(edited or damaged after the session)"))
    return out


def check_identity(b: BundleInputs, sid: str) -> List[Issue]:
    out = []
    ids = {"manifest": (b.manifest or {}).get("session_id"),
           "trial CSV": b.trial_header.get("session_id"),
           "serial capture": b.serial_header.get("session_id"),
           "video sidecar": b.sidecar_header.get("session_id"),
           "animal state": (b.animal_state or {}).get("session_id")}
    for where, v in ids.items():
        if v and v != sid:
            out.append(_issue("identity", ERROR, sid, f"session_id:{where}",
                              f"{where} says session {v}, but the folder is {sid}"))
    animal = (b.manifest or {}).get("animal")
    others = {"trial CSV": b.trial_header.get("animal"),
              "animal state": (b.animal_state or {}).get("animal"),
              "video sidecar": b.sidecar_header.get("animal")}
    if others["video sidecar"] == SIDECAR_ANIMAL_PLACEHOLDER:
        del others["video sidecar"]
    for where, v in others.items():
        if animal and v and str(v) != str(animal):
            sev = WARNING if where == "video sidecar" else ERROR
            out.append(_issue("identity", sev, sid, f"animal:{where}",
                              f"{where} says animal {v!r}, the manifest says {animal!r}"))
    return out


# Pairs that must be in time order within a trial. Trial start vs cue onset is
# deliberately absent: July-August sessions stamped the start after the cue.
_TIME_ORDER = (("trial_start_iso", "trial_end_iso"),
               ("choice_cue_on_iso", "choice_time_iso"),
               ("choice_time_iso", "trial_end_iso"))


def carried_over(rows: Sequence[dict], t0_s: Optional[float]) -> List[dict]:
    """Trial rows (any attempt, played) that started before this recording did.

    When the operator stops and restarts the RECORDING mid-task, the task run and
    its trial CSV continue into the new bundle (ADR-0198), so the new bundle's CSV
    also holds the earlier trials; their serial lines and video frames are in the
    earlier bundle, and can't be checked against this one's."""
    if t0_s is None:
        return []
    return [r for r in rows if str(r.get("outcome", "")).strip()
            and (_iso_s(r.get("trial_start_iso")) or t0_s) < t0_s - PRE_START_SLACK_S]


def check_trials(b: BundleInputs, sid: str, t_end_s: Optional[float],
                 carried: Sequence[dict] = ()) -> List[Issue]:
    out: List[Issue] = []
    live = live_rows(b.trial_rows)
    nums = [_int(r.get("trial")) for r in live]
    if any(n is None for n in nums):
        out.append(_issue("trials", ERROR, sid, "bad_number", "a trial row has no trial number"))
    nums_ok = [n for n in nums if n is not None]
    dup = sorted({n for n in nums_ok if nums_ok.count(n) > 1})
    if dup:
        out.append(_issue("trials", ERROR, sid, "duplicate",
                          f"trial numbers appear twice among live rows: {dup[:10]}"))
    if nums_ok and sorted(set(nums_ok)) != list(range(1, max(nums_ok) + 1)):
        missing = sorted(set(range(1, max(nums_ok) + 1)) - set(nums_ok))
        out.append(_issue("trials", ERROR, sid, "gap", f"trial numbers missing: {missing[:10]}"))
    live_nums = set(nums_ok)
    orphans = sorted({_int(r.get("trial")) for r in b.trial_rows
                      if str(r.get("superseded", "")).strip() == "1"} - live_nums - {None})
    if orphans:
        out.append(_issue("trials", ERROR, sid, "orphan_attempt",
                          f"re-run attempts with no live trial: {orphans[:10]}"))

    played_nums = [_int(r.get("trial")) for r in played(b.trial_rows)]
    unplayed = [_int(r.get("trial")) for r in live if not str(r.get("outcome", "")).strip()]
    if played_nums and unplayed:
        skipped = sorted(n for n in unplayed if n is not None and n < max(played_nums))
        if skipped:
            out.append(_issue("trials", WARNING, sid, "skipped",
                              f"trials never played although later ones were: {skipped[:10]}"))

    prev_end, prev_n = None, None
    for r in sorted(played(b.trial_rows), key=lambda r: _int(r.get("trial")) or 0):
        n = r.get("trial")
        for c1, c2 in _TIME_ORDER:
            t1, t2 = _iso_s(r.get(c1)), _iso_s(r.get(c2))
            if t1 is not None and t2 is not None and t2 < t1:
                out.append(_issue("trials", ERROR, sid, f"order:{n}",
                                  f"trial {n}: {c2} is before {c1}"))
                break
        start = _iso_s(r.get("trial_start_iso"))
        if prev_end is not None and start is not None and start < prev_end - 0.001:
            out.append(_issue("trials", ERROR, sid, f"overlap:{n}",
                              f"trial {n} starts before trial {prev_n} ended"))
        end = _iso_s(r.get("trial_end_iso"))
        if t_end_s is not None and end is not None and end > t_end_s + END_SLACK_S:
            out.append(_issue("trials", WARNING, sid, f"after_stop:{n}",
                              f"trial {n} ends {end - t_end_s:.0f} s after the session stopped"))
        prev_end, prev_n = (end if end is not None else prev_end), n
    if carried:
        nums = sorted({_int(r.get("trial")) for r in carried} - {None})
        out.append(_issue("trials", WARNING, sid, "carried_over",
                          f"{len(nums)} trial(s) (trials {nums[0]}-{nums[-1]}) ran before this "
                          "recording started: the recording was restarted mid-task, so their "
                          "serial log and video are in the previous bundle (ADR-0198)"))
    return out


def _serial_index(rows: Sequence[Tuple[int, str, str]]):
    by_type: Dict[str, List[Tuple[int, List[str]]]] = {}
    for _n, _wall_iso, line in rows:
        ms, typ, payload = _fw(line)
        if ms is not None:
            by_type.setdefault(typ, []).append((ms, payload))
    return by_type


def _task_pulses(rows: Iterable[dict]) -> Tuple[int, int]:
    """(rewarded trials, top-up pulses) over played rows, re-run attempts
    included: a superseded attempt's water was still delivered."""
    rows = [r for r in rows if str(r.get("outcome", "")).strip()]
    return (sum(1 for r in rows if str(r.get("rewarded", "")).strip() == "1"),
            sum(_int(r.get("topups")) or 0 for r in rows))


def check_rewards(b: BundleInputs, sid: str, idx,
                  carried: Sequence[dict] = ()) -> Tuple[List[Issue], Optional[str]]:
    """Every valve pulse goes through the host (task rewards, top-ups and the
    operator's Solenoid-test button alike), so the serial ``REWARD`` count must
    equal the task pulses + the summary's hand-given pulses, exactly."""
    out: List[Issue] = []
    reward_ms = sorted(ms for ms, _p in idx.get("REWARD", []))
    carried_ids = {id(r) for r in carried}
    here = [r for r in b.trial_rows if id(r) not in carried_ids]
    for r in played(here) + [r for r in here if str(r.get("superseded", "")).strip() == "1"]:
        if str(r.get("rewarded", "")).strip() != "1":
            continue
        x = _int(r.get("reward_cmd_fw_ms"))
        if x is not None and not _near(reward_ms, x, FW_MATCH_TOL_MS):
            out.append(_issue("rewards", ERROR, sid, f"no_pulse:{r.get('trial')}",
                              f"trial {r.get('trial')} is rewarded but the capture has no reward "
                              f"pulse at firmware ms {x}"))
    rewarded, topups = _task_pulses(b.trial_rows)
    summ = b.summary or {}
    est = (summ.get("estimated_water") or {}).get("pulses")
    manual = (summ.get("manual_water") or {}).get("pulses")
    if isinstance(est, int) and est != rewarded + topups:
        out.append(_issue("rewards", ERROR, sid, "summary_vs_trials",
                          f"summary counts {est} task pulses, the trial CSV {rewarded} rewarded "
                          f"+ {topups} top-ups = {rewarded + topups}"))
    if not isinstance(manual, int):
        return out, ("no summary: hand-given pulses unknown, per-trial pulses checked only"
                     if b.summary is None else "summary predates manual_water (ADR-0078)")
    schema = _int(summ.get("schema_version")) or 0
    if schema < MANUAL_OPERATOR_ONLY_FROM:
        expect, how = manual, f"the summary's {manual} (all pulses, pre-ADR-0084 meaning)"
    else:
        r_here, t_here = _task_pulses(here)
        expect = r_here + t_here + manual
        how = f"{r_here} rewarded + {t_here} top-ups + {manual} hand-given = {expect}"
    if len(reward_ms) != expect:
        out.append(_issue("rewards", ERROR, sid, "serial_vs_expected",
                          f"the capture has {len(reward_ms)} reward pulses; expected {how}"))
    return out, None


def check_stamps(b: BundleInputs, sid: str, idx, check: str, column: str,
                 typ: str, want: Optional[Tuple[str, ...]] = None,
                 carried: Sequence[dict] = ()) -> List[Issue]:
    """Each played trial's ``column`` stamp must be a ``typ`` line in the capture
    (optionally with a payload prefix ``want``, e.g. ("ON", "CHOICE"))."""
    lines = [ms for ms, p in idx.get(typ, []) if want is None or tuple(p[:len(want)]) == want]
    lines.sort()
    bad = []
    carried_ids = {id(r) for r in carried}
    for r in played([r for r in b.trial_rows if id(r) not in carried_ids]):
        x = _int(r.get(column))
        if x is not None and not _near(lines, x, FW_MATCH_TOL_MS):
            bad.append(r.get("trial"))
    if not bad:
        return []
    return [_issue(check, ERROR, sid, column,
                   f"{len(bad)} trial(s) have a {column} with no matching {typ} line "
                   f"in the capture (trials {bad[:10]})")]


def check_serial(b: BundleInputs, sid: str, idx) -> Tuple[List[Issue], Dict[str, str]]:
    out: List[Issue] = []
    skipped: Dict[str, str] = {}
    drops = [_int(p[0]) for _ms, p in idx.get("TXDROP", []) if p]
    drops = [d for d in drops if d is not None]
    if drops:
        # The count is cumulative since the board booted, so only a rise during
        # the session means lines are missing from THIS capture.
        rose = max(drops) - drops[0]
        if rose > 0:
            out.append(_issue("serial", ERROR, sid, "txdrop",
                              f"the firmware dropped {rose} serial line(s) during this session "
                              f"(TXDROP {drops[0]} -> {max(drops)}): some beam/lick/event lines "
                              "are missing from the capture"))
        elif drops[0] > 0:
            out.append(_issue("serial", NOTE, sid, "txdrop_before",
                              f"TXDROP was already {drops[0]} at the first STATUS (lines dropped "
                              "before this session or before its first probe)"))
    elif (b.firmware_proto or 0) >= TXDROP_FROM_PROTO:
        out.append(_issue("serial", WARNING, sid, "no_txdrop",
                          "firmware v9+ but no TXDROP line: the dropped-line count is unknown"))
    else:
        skipped["serial.txdrop"] = f"firmware v{b.firmware_proto or '?'} predates TXDROP (v9)"

    if b.serial_filtered:
        skipped["serial.silence"] = "filtered sensor capture (pre-ADR-0178) has no probe replies"
    else:
        walls = [t for t in (_iso_s(w) for _n, w, _line in b.serial_rows) if t is not None]
        gaps = [c - a for a, c in zip(walls, walls[1:]) if c - a > LINK_SILENCE_S]
        if gaps:
            out.append(_issue("serial", WARNING, sid, "silence",
                              f"{len(gaps)} gap(s) of more than {LINK_SILENCE_S:.0f} s with no "
                              f"serial line at all (longest {max(gaps):.0f} s); the liveness "
                              "probe should answer within 25 s, so the link stopped answering"))

    last, jumps = None, 0
    for _n, _w, line in b.serial_rows:
        ms = _fw(line)[0]
        if ms is None:
            continue
        if last is not None and ms < last - FW_BACKWARDS_MS:
            jumps += 1
        last = ms
    resets = len((b.manifest or {}).get("firmware_resets") or [])
    if jumps and jumps != resets:
        out.append(_issue("serial", WARNING, sid, "clock_jump",
                          f"the firmware clock ran backwards {jumps} time(s) (a board reset?); "
                          f"the manifest records {resets} reset(s)"))
    return out, skipped


def check_summary(b: BundleInputs, sid: str) -> List[Issue]:
    cued = (b.summary or {}).get("cued_task") or {}
    rows = played(b.trial_rows)
    got = {"completed": sum(1 for r in rows if r.get("outcome") in ("CORRECT", "INCORRECT")),
           "correct": sum(1 for r in rows if r.get("outcome") == "CORRECT")}
    out = []
    for k, v in got.items():
        s = cued.get(k)
        if isinstance(s, int) and s != v:
            out.append(_issue("summary", ERROR, sid, k,
                              f"summary says {k} = {s}, the trial CSV has {v}"))
    return out


def check_video(b: BundleInputs, sid: str, t0_s, t_end_s) -> Tuple[List[Issue], Optional[str]]:
    if b.sidecar_rows is None and b.camera_stats is None:
        return [], "no video sidecar / camera stats"
    out = []
    c = (b.camera_stats or {}).get("counters_this_recording") or (b.camera_stats or {}).get("counters") or {}
    saved = c.get("saved")
    if isinstance(saved, int) and b.sidecar_rows is not None and saved != b.sidecar_rows:
        out.append(_issue("video", ERROR, sid, "sidecar_rows",
                          f"the camera saved {saved} frames but the sidecar lists {b.sidecar_rows}"))
    lost = sum(c.get(k) or 0 for k in ("src_drops", "incomplete", "queue_drops"))
    grabbed = c.get("grabbed")
    if lost and isinstance(grabbed, int) and grabbed:
        frac = lost / (grabbed + (c.get("src_drops") or 0))
        out.append(_issue("video", WARNING if frac >= LOST_FRAMES_WARN else NOTE, sid, "lost_frames",
                          f"{lost} frames lost by the camera ({frac:.2%}: "
                          + ", ".join(f"{k} {c.get(k) or 0}" for k in ("src_drops", "incomplete", "queue_drops"))
                          + ")"))
    if b.sidecar_span_s and t0_s is not None:
        first, last = b.sidecar_span_s
        if first > t0_s + VIDEO_COVER_SLACK_S:
            out.append(_issue("video", WARNING, sid, "starts_late",
                              f"the video starts {first - t0_s:.0f} s after the session"))
        if t_end_s is not None and last < t_end_s - VIDEO_COVER_SLACK_S:
            out.append(_issue("video", WARNING, sid, "ends_early",
                              f"the video ends {t_end_s - last:.0f} s before the session"))
    return out, None


# ----------------------------------------------------------------- entry points

def audit_bundle(b: BundleInputs) -> BundleAudit:
    """Run every check on one bundle."""
    sid = b.folder_id
    t0_s = _wall((b.manifest or {}).get("t0"))
    t_end_s = _wall((b.manifest or {}).get("t_end"))
    res = BundleAudit(sid, t0_s=t0_s, t_end_s=t_end_s)
    if b.manifest is None:
        res.issues.append(_issue("files", ERROR, sid, "manifest", "no readable session manifest"))
    res.issues += check_files(b, sid)
    res.issues += check_config(b, sid)
    if b.snapshot is None:
        res.not_checked["config"] = "no config snapshot index"
    res.issues += check_identity(b, sid)
    if b.sidecar_header.get("animal") == SIDECAR_ANIMAL_PLACEHOLDER:
        res.not_checked["identity.sidecar_animal"] = "the video sidecar has the placeholder 'animal'"
    carried = carried_over(b.trial_rows, t0_s)
    if b.trial_rows:
        res.issues += check_trials(b, sid, t_end_s, carried)
    else:
        res.not_checked["trials"] = "no trial CSV rows"
    idx = _serial_index(b.serial_rows)
    if b.serial_rows:
        issues, why = check_rewards(b, sid, idx, carried)
        res.issues += issues
        if why:
            res.not_checked["rewards"] = why
        if b.serial_filtered:
            res.not_checked["cues"] = "filtered sensor capture (pre-ADR-0178) has no CUE lines"
        else:
            res.issues += check_stamps(b, sid, idx, "cues", "choice_cue_on_fw_ms", "CUE",
                                       ("ON", "CHOICE"), carried)
        res.issues += check_stamps(b, sid, idx, "licks", "lick_fw_ms", "L", carried=carried)
        issues, skipped = check_serial(b, sid, idx)
        res.issues += issues
        res.not_checked.update(skipped)
    else:
        for c in ("rewards", "cues", "licks", "serial"):
            res.not_checked[c] = "no serial capture"
    if b.summary is not None and b.trial_rows:
        res.issues += check_summary(b, sid)
    else:
        res.not_checked["summary"] = "no summary or no trial rows"
    issues, why = check_video(b, sid, t0_s, t_end_s)
    res.issues += issues
    if why:
        res.not_checked["video"] = why
    return res


def audit_across(audits: Iterable[BundleAudit]) -> List[Issue]:
    """Checks between bundles: two sessions whose Start..Stop overlap (one rig
    runs one session at a time, so an overlap means misrouted or duplicated data)."""
    spans = sorted((a.t0_s, a.t_end_s, a.session_id) for a in audits
                   if a.t0_s is not None and a.t_end_s is not None)
    out = []
    for (s1, e1, id1), (s2, e2, id2) in zip(spans, spans[1:]):
        if s2 < e1:
            out.append(Issue("across", ERROR, f"across:{id1}:{id2}",
                             f"{id1} and {id2} overlap in time by {e1 - s2:.0f} s "
                             "(one rig runs one session at a time)"))
    return out


def rebase_stream(path: str, bundle_dir: str, session_id: str) -> Tuple[str, bool]:
    """Where a manifest stream path should be for this bundle, and whether it
    names this bundle at all. Stream paths are absolute (FW-0049), so a bundle
    moved to an archive drive still lists its old location: the part after the
    ``<session id>`` folder is re-rooted onto ``bundle_dir``."""
    if not os.path.isabs(path):
        return os.path.join(bundle_dir, path), True
    parts = path.replace("\\", "/").split("/")
    if session_id not in parts:
        return path, False
    last = len(parts) - 1 - parts[::-1].index(session_id)
    return os.path.join(bundle_dir, *parts[last + 1:]), True
