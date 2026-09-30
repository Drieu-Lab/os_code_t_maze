"""Headless tests for the in-bundle integrity audit (docs/decisions/0204).

Each check is exercised on a small synthetic session that is consistent, then
broken in exactly one way. The loader is run on the committed golden bundle.

Run from host/:  python -m unittest tests.test_bundle_audit
"""

import os
import unittest

from tmaze.core.session import bundle_audit as ba
from tmaze.core.session.bundle_audit_inputs import load_inputs

SID = "600_20260901_120000"
T0 = "2026-09-01T12:00:00.000-04:00"
T0_S = 1788278400.0            # T0 as unix seconds
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "600_20260101_120000")


def iso(sec: float) -> str:
    """T0 + ``sec`` seconds, in the trial CSV's ISO form."""
    import datetime as dt
    tz = dt.timezone(dt.timedelta(hours=-4))
    return dt.datetime.fromtimestamp(T0_S + sec, tz).isoformat(timespec="milliseconds")


def trial(n, start, *, rewarded=True, topups=0, outcome=None, superseded=False, lick=True):
    """A played trial starting ``start`` s after T0; firmware ms = 1000 * seconds."""
    return {"trial": str(n), "outcome": outcome or ("CORRECT" if rewarded else "INCORRECT"),
            "rewarded": "1" if rewarded else "0", "topups": str(topups),
            "superseded": "1" if superseded else "",
            "trial_start_iso": iso(start), "choice_cue_on_iso": iso(start + 1),
            "choice_time_iso": iso(start + 3), "trial_end_iso": iso(start + 6),
            "choice_cue_on_fw_ms": str((start + 1) * 1000),
            "reward_cmd_fw_ms": str((start + 4) * 1000) if rewarded else "",
            "lick_fw_ms": str((start + 5) * 1000) if rewarded and lick else ""}


def serial_for(rows, *, manual=0, extra=()):
    """The serial lines those trials would have produced, plus ``manual``
    hand-given pulses and a liveness STATUS/TXDROP every 10 s."""
    lines = [(0, "VERSION,9,unknown,Sep 21 2026")]
    for r in rows:
        s = int(r["choice_cue_on_fw_ms"]) // 1000 - 1
        lines.append((s * 1000 + 1000, "CUE,ON,CHOICE,8000,46"))
        if r["rewarded"] == "1":
            for k in range(1 + int(r["topups"])):
                lines.append((s * 1000 + 4000 + k * 300, "REWARD,2,MANUAL,300"))
            if r["lick_fw_ms"]:
                lines.append((s * 1000 + 5000, "L,2,1"))
    for k in range(manual):
        lines.append((500 + k, "REWARD,2,MANUAL,300"))
    last = max(ms for ms, _ in lines) + 10000
    for ms in range(0, last, 10000):
        lines += [(ms + 1, "STATUS,0,0,1"), (ms + 2, "TXDROP,0")]
    lines += list(extra)
    lines.sort()
    return [(i + 1, iso(ms / 1000), f"{ms},{body}") for i, (ms, body) in enumerate(lines)]


def good_bundle(n_trials=3, manual=1) -> ba.BundleInputs:
    rows = [trial(i + 1, 10 + 20 * i, topups=1 if i == 0 else 0) for i in range(n_trials)]
    end = 10 + 20 * n_trials + 10
    rewarded = n_trials
    b = ba.BundleInputs(folder_id=SID)
    b.manifest = {"session_id": SID, "animal": "600",
                  "t0": {"wall": T0_S, "iso": T0}, "t_end": {"wall": T0_S + end},
                  "firmware_resets": []}
    b.streams = [ba.StreamFile("phase3_task", "task/x.csv", "/b/task/x.csv", 10, True),
                 ba.StreamFile("serial_full", "serial/x.csv", "/b/serial/x.csv", 10, True)]
    b.trial_header = {"session_id": SID, "animal": "600"}
    b.trial_rows = rows
    b.serial_header = {"session_id": SID}
    b.serial_rows = serial_for(rows, manual=manual)
    b.firmware_proto = 9
    b.summary = {"schema_version": 8,
                 "estimated_water": {"pulses": rewarded + 1},
                 "manual_water": {"pulses": manual},
                 "cued_task": {"completed": n_trials, "correct": n_trials}}
    b.snapshot = {"files": [{"name": "phase3", "filename": "phase3.json",
                             "present": True, "sha256": "abc"}]}
    b.config_hashes = {"phase3.json": "abc"}
    b.animal_state = {"session_id": SID, "animal": "600"}
    b.sidecar_header = {"session_id": SID, "animal": "600"}
    b.sidecar_rows = 100
    b.sidecar_span_s = (T0_S + 1, T0_S + end - 1)
    b.camera_stats = {"counters": {"grabbed": 100, "saved": 100, "src_drops": 0,
                                   "incomplete": 0, "queue_drops": 0}}
    return b


def keys(audit):
    return {i.key.split(":", 2)[0] + ":" + i.key.split(":", 2)[2] for i in audit.issues}


class TestGoodBundle(unittest.TestCase):
    def test_a_consistent_session_is_ok(self):
        a = ba.audit_bundle(good_bundle())
        self.assertEqual(a.issues, [])
        self.assertEqual(a.status, "ok")
        self.assertEqual(a.not_checked, {})

    def test_issue_keys_name_the_session(self):
        b = good_bundle()
        b.trial_header["animal"] = "601"
        (issue,) = ba.audit_bundle(b).issues
        self.assertEqual(issue.key, f"identity:{SID}:animal:trial CSV")


class TestFilesAndConfig(unittest.TestCase):
    def test_missing_outside_and_empty_streams(self):
        b = good_bundle()
        b.streams = [ba.StreamFile("phase3_task", "t", None, None, True),
                     ba.StreamFile("serial_full", "/other/s.csv", "/other/s.csv", 5, False),
                     ba.StreamFile("camera_video", "v", "/b/v", 0, True)]
        self.assertEqual({"files:missing:phase3_task", "files:outside:serial_full",
                          "files:empty:camera_video"}, keys(ba.audit_bundle(b)))

    def test_either_serial_stream_counts_as_listed(self):
        b = good_bundle()
        b.streams[1] = ba.StreamFile("serial_sensors", "s", "/b/s", 5, True)
        self.assertEqual(set(), keys(ba.audit_bundle(b)))
        b.streams = b.streams[:1]
        self.assertEqual({"files:unlisted:serial"}, keys(ba.audit_bundle(b)))

    def test_config_changed_or_missing(self):
        b = good_bundle()
        b.config_hashes = {"phase3.json": "def"}
        self.assertEqual({"config:changed:phase3.json"}, keys(ba.audit_bundle(b)))
        b.config_hashes = {"phase3.json": None}
        self.assertEqual({"config:missing:phase3.json"}, keys(ba.audit_bundle(b)))


class TestIdentity(unittest.TestCase):
    def test_session_id_disagreement_is_an_error(self):
        b = good_bundle()
        b.serial_header["session_id"] = "601_20260901_120000"
        a = ba.audit_bundle(b)
        self.assertEqual({"identity:session_id:serial capture"}, keys(a))
        self.assertEqual(a.status, "errors")

    def test_sidecar_animal_is_a_warning_and_the_placeholder_is_skipped(self):
        b = good_bundle()
        b.sidecar_header["animal"] = "602"
        self.assertEqual(ba.audit_bundle(b).status, "warnings")
        b.sidecar_header["animal"] = ba.SIDECAR_ANIMAL_PLACEHOLDER
        a = ba.audit_bundle(b)
        self.assertEqual(a.issues, [])
        self.assertIn("identity.sidecar_animal", a.not_checked)


class TestTrials(unittest.TestCase):
    def test_gap_duplicate_and_orphan_attempt(self):
        b = good_bundle(4)
        b.trial_rows[1]["trial"] = "3"                  # 1, 3, 3, 4 -> 2 missing, 3 twice
        b.trial_rows.append(trial(9, 200, superseded=True))
        self.assertTrue({"trials:duplicate", "trials:gap", "trials:orphan_attempt"}
                        <= keys(ba.audit_bundle(b)))

    def test_superseded_attempts_are_not_duplicates(self):
        b = good_bundle(2)
        rerun = trial(2, 30, superseded=True)
        b.trial_rows.insert(1, rerun)
        b.serial_rows = serial_for(b.trial_rows, manual=1)
        b.summary["estimated_water"]["pulses"] += 1
        self.assertEqual(set(), keys(ba.audit_bundle(b)))

    def test_times_out_of_order_or_overlapping(self):
        b = good_bundle()
        b.trial_rows[0]["choice_time_iso"] = iso(9)     # before its cue at 11 s
        b.trial_rows[2]["trial_start_iso"] = iso(33)    # trial 2 ends at 36 s
        self.assertEqual({"trials:order:1", "trials:overlap:3"}, keys(ba.audit_bundle(b)))

    def test_unplayed_trial_before_a_played_one(self):
        b = good_bundle()
        b.trial_rows[1]["outcome"] = ""
        self.assertIn("trials:skipped", keys(ba.audit_bundle(b)))

    def test_trial_ending_after_stop_is_a_warning(self):
        b = good_bundle()
        b.trial_rows[-1]["trial_end_iso"] = iso(80 + ba.END_SLACK_S + 5)
        self.assertEqual({"trials:after_stop:3"}, keys(ba.audit_bundle(b)))


class TestRewards(unittest.TestCase):
    def test_every_pulse_accounted_for(self):
        self.assertEqual([], ba.audit_bundle(good_bundle(manual=3)).issues)

    def test_an_unexplained_extra_pulse_is_an_error(self):
        b = good_bundle()
        b.serial_rows = serial_for(b.trial_rows, manual=2)   # summary still says 1
        self.assertEqual({"rewards:serial_vs_expected"}, keys(ba.audit_bundle(b)))

    def test_a_rewarded_trial_without_its_pulse(self):
        b = good_bundle()
        b.trial_rows[1]["reward_cmd_fw_ms"] = "999999"
        self.assertEqual({"rewards:no_pulse:2"}, keys(ba.audit_bundle(b)))

    def test_summary_disagreeing_with_the_trial_csv(self):
        b = good_bundle()
        b.summary["estimated_water"]["pulses"] = 99
        self.assertEqual({"rewards:summary_vs_trials"}, keys(ba.audit_bundle(b)))

    def test_old_summaries_counted_every_pulse_as_manual(self):
        b = good_bundle(manual=1)
        b.summary["schema_version"] = 4
        b.summary["manual_water"]["pulses"] = 3 + 1 + 1     # all pulses, pre-ADR-0084
        self.assertEqual([], ba.audit_bundle(b).issues)

    def test_no_summary_checks_per_trial_pulses_only(self):
        b = good_bundle()
        b.summary = None
        a = ba.audit_bundle(b)
        self.assertEqual([], a.issues)
        self.assertIn("rewards", a.not_checked)


class TestCarriedOverTrials(unittest.TestCase):
    """A recording restarted mid-task (ADR-0198): the new bundle's trial CSV
    still holds the earlier trials, whose serial lines are in the old bundle."""

    def bundle(self):
        b = good_bundle(4)
        early = [trial(i + 1, -300 + 20 * i) for i in range(2)]
        for r in early:
            for c in ("choice_cue_on_fw_ms", "reward_cmd_fw_ms", "lick_fw_ms"):
                r[c] = str(int(r[c]) + 10_000_000)      # an earlier firmware boot
        for i, r in enumerate(b.trial_rows):
            r["trial"] = str(i + 3)
        b.trial_rows = early + b.trial_rows
        b.summary["estimated_water"]["pulses"] += 2
        b.summary["cued_task"] = {"completed": 6, "correct": 6}
        return b

    def test_earlier_trials_are_one_warning_not_many_errors(self):
        a = ba.audit_bundle(self.bundle())
        self.assertEqual({"trials:carried_over"}, keys(a))
        self.assertIn("trials 1-2", a.issues[0].text)


class TestStamps(unittest.TestCase):
    def test_cue_and_lick_stamps_must_be_capture_lines(self):
        b = good_bundle()
        b.trial_rows[0]["choice_cue_on_fw_ms"] = "12345"
        b.trial_rows[2]["lick_fw_ms"] = "54321"
        self.assertEqual({"cues:choice_cue_on_fw_ms", "licks:lick_fw_ms"},
                         keys(ba.audit_bundle(b)))

    def test_filtered_capture_skips_cues(self):
        b = good_bundle()
        b.serial_filtered = True
        b.trial_rows[0]["choice_cue_on_fw_ms"] = "12345"
        a = ba.audit_bundle(b)
        self.assertEqual([], a.issues)
        self.assertIn("cues", a.not_checked)


class TestSerial(unittest.TestCase):
    def test_txdrop_rising_during_the_session(self):
        b = good_bundle()
        b.serial_rows = serial_for(b.trial_rows, manual=1, extra=[(79000, "TXDROP,4")])
        a = ba.audit_bundle(b)
        self.assertEqual({"serial:txdrop"}, keys(a))
        self.assertIn("dropped 4", a.issues[0].text)

    def test_txdrop_already_nonzero_at_start_is_only_a_note(self):
        b = good_bundle()
        b.serial_rows = [(n, w, line.replace("TXDROP,0", "TXDROP,7"))
                         for n, w, line in b.serial_rows]
        a = ba.audit_bundle(b)
        self.assertEqual({"serial:txdrop_before"}, keys(a))
        self.assertEqual(a.status, "ok")

    def test_old_firmware_has_no_txdrop(self):
        b = good_bundle()
        b.firmware_proto = 8
        b.serial_rows = [r for r in b.serial_rows if "TXDROP" not in r[2]]
        a = ba.audit_bundle(b)
        self.assertEqual([], a.issues)
        self.assertIn("serial.txdrop", a.not_checked)
        b.firmware_proto = 9
        self.assertEqual({"serial:no_txdrop"}, keys(ba.audit_bundle(b)))

    def test_a_silent_link(self):
        b = good_bundle()
        rows = serial_for(b.trial_rows, manual=1, extra=[(75000, "PONG")])
        b.serial_rows = [r for r in rows if not 30 <= int(r[2].split(",")[0]) / 1000 < 70]
        b.trial_rows = b.trial_rows[:1]
        b.summary["estimated_water"]["pulses"] = 2
        b.summary["cued_task"] = {"completed": 1, "correct": 1}
        self.assertEqual({"serial:silence"}, keys(ba.audit_bundle(b)))

    def test_clock_jump_without_a_recorded_reset(self):
        b = good_bundle()
        b.serial_rows = serial_for(b.trial_rows, manual=1) + [(999, iso(90), "5,PONG")]
        self.assertEqual({"serial:clock_jump"}, keys(ba.audit_bundle(b)))
        b.manifest["firmware_resets"] = [{"at": "x"}]
        self.assertEqual(set(), keys(ba.audit_bundle(b)))


class TestSummaryAndVideo(unittest.TestCase):
    def test_summary_counts_must_match(self):
        b = good_bundle()
        b.summary["cued_task"]["correct"] = 2
        self.assertEqual({"summary:correct"}, keys(ba.audit_bundle(b)))

    def test_sidecar_rows_lost_frames_and_coverage(self):
        b = good_bundle()
        b.sidecar_rows = 90
        b.camera_stats["counters"]["incomplete"] = 5
        b.sidecar_span_s = (T0_S + 20, T0_S + 30)
        a = ba.audit_bundle(b)
        self.assertEqual({"video:sidecar_rows", "video:lost_frames", "video:starts_late",
                          "video:ends_early"}, keys(a))
        lost = next(i for i in a.issues if i.key.endswith("lost_frames"))
        self.assertEqual(lost.severity, ba.WARNING)          # 5% >= 1%


class TestAcross(unittest.TestCase):
    def test_overlapping_sessions(self):
        a1 = ba.BundleAudit("a", t0_s=0, t_end_s=100)
        a2 = ba.BundleAudit("b", t0_s=90, t_end_s=200)
        a3 = ba.BundleAudit("c", t0_s=300, t_end_s=400)
        (issue,) = ba.audit_across([a3, a1, a2])
        self.assertEqual(issue.key, "across:a:b")


class TestRebaseStream(unittest.TestCase):
    def test_a_moved_bundle_is_re_rooted(self):
        path, inside = ba.rebase_stream(
            rf"C:\rec\{SID}\task\phase3_{SID}.csv", os.path.join("Z:", "arch", SID), SID)
        self.assertTrue(inside)
        self.assertEqual(path, os.path.join("Z:", "arch", SID, "task", f"phase3_{SID}.csv"))

    def test_a_path_naming_another_folder(self):
        path, inside = ba.rebase_stream(r"C:\repo\host\data\phase3_x.csv", "/b", SID)
        self.assertFalse(inside)

    def test_relative_paths_are_inside(self):
        self.assertEqual(ba.rebase_stream("task/x.csv", "/b", SID),
                         (os.path.join("/b", "task/x.csv"), True))


class TestGoldenFixture(unittest.TestCase):
    def test_loader_reads_the_committed_bundle(self):
        b = load_inputs(FIXTURE)
        self.assertIsNotNone(b.manifest)
        self.assertTrue(b.trial_rows)
        a = ba.audit_bundle(b)
        # The fixture deliberately omits the video file; nothing else is wrong.
        self.assertEqual([i.key for i in a.issues if i.severity == ba.ERROR],
                         ["files:600_20260101_120000:missing:camera_video"])
        self.assertIn("serial", a.not_checked)


if __name__ == "__main__":
    unittest.main()
