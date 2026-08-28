"""
test_analysis_bundle_reader.py - headless tests for the bundle discovery/loader.

Covers ``bundle_reader``: the ``#``-header comment parser, animal/date filtering in
``discover_bundles``, and ``load_animals`` over a synthetic on-disk bundle (a
``task/`` CSV with a real ``#`` header + a ``session_<id>.json`` marker), including
the graceful empty ``excursions`` frame and the global ``seq`` ordering. Uses a temp
dir; no rig/Tk.

Run from host/tests:  python -m unittest test_analysis_bundle_reader
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from tmaze.analysis import bundle_reader as br
from tmaze.core.session import session_notes

_HEADER = (
    "# phase3_session animal=600 session_id={sid} seed=42 proto=7\n"
    "# high_tone_is_left=True high_tone_hz=8000 low_tone_hz=4000\n"
    "# generator=intervention_blocks\n"
    "# note: this line is free prose and ignored\n"
)
_COLS = (
    "trial,correct_arm,choice_cue,return_cue_left,return_cue_right,chosen_arm,"
    "outcome,rewarded,topups,return_arm_used,return_cue_played,trial_start_iso,"
    "choice_cue_on_iso,choice_time_iso,reward_cmd_iso,return_cue_on_iso,"
    "return_cue_off_iso,trial_end_iso,lick_time_iso,lick_fw_ms,sched_state,"
    "block_id,block_trigger,bias,block_kind,alt_rate\n"
)


def _row(trial, cue, choice, outcome, rewarded):
    ts = f"2026-01-01T12:00:{trial:02d}"
    return (
        f"{trial},LEFT,{cue},HIGH,LOW,{choice},{outcome},{rewarded},,{choice},HIGH,"
        f"{ts},{ts},{ts},,{ts},{ts},{ts},,,NORMAL,,,0.0,,0.0\n"
    )


def _blank_row(trial, cue):
    """A planned-but-unrun trial: the cue matrix is filled up front but there is no
    start stamp / chosen arm / outcome yet (phase3_log pre-writes the plan)."""
    vals = [str(trial), "LEFT", cue, "HIGH", "LOW"] + [""] * 15 + ["NORMAL"] + [""] * 5
    return ",".join(vals) + "\n"


def _abandoned_row(trial, cue):
    """An abandoned trial: STARTED (has a start stamp) and scored ABANDONED, but no
    chosen arm - it must be kept (it breaks own-choice pairs and counts as activity)."""
    ts = f"2026-01-01T12:00:{trial:02d}"
    vals = [str(trial), "LEFT", cue, "HIGH", "LOW", "", "ABANDONED", "0", "", "", "",
            ts, ts, "", "", "", "", ts, "", "", "NORMAL", "", "", "0.0", "", "0.0"]
    return ",".join(vals) + "\n"


def _make_bundle(root, sid, n=5):
    bundle = os.path.join(root, sid)
    os.makedirs(os.path.join(bundle, "task"))
    with open(os.path.join(bundle, f"session_{sid}.json"), "w") as fh:
        fh.write("{}")
    path = os.path.join(bundle, "task", f"phase3_{sid}.csv")
    with open(path, "w") as fh:
        fh.write(_HEADER.format(sid=sid))
        fh.write(_COLS)
        for t in range(1, n + 1):
            fh.write(_row(t, "HIGH", "LEFT", "CORRECT", 1))
    return bundle


def _make_bundle_with_unrun(root, sid, n_real, n_blank):
    """A bundle whose CSV has ``n_real`` attempted trials followed by ``n_blank``
    planned-but-unrun placeholder rows (the shape phase3_log writes)."""
    bundle = os.path.join(root, sid)
    os.makedirs(os.path.join(bundle, "task"))
    with open(os.path.join(bundle, f"session_{sid}.json"), "w") as fh:
        fh.write("{}")
    with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"), "w") as fh:
        fh.write(_HEADER.format(sid=sid))
        fh.write(_COLS)
        for t in range(1, n_real + 1):
            fh.write(_row(t, "HIGH", "LEFT", "CORRECT", 1))
        for t in range(n_real + 1, n_real + n_blank + 1):
            fh.write(_blank_row(t, "HIGH"))
    return bundle


def _make_probe_bundle(root, sid, *, planned=True, disabled=False, n=5):
    """A bundle carrying an optional probe_blocks header line and an optional
    summary/notes_<id>.jsonl with an AUTO_PROBE_DISABLED entry (docs/decisions/0137)."""
    bundle = os.path.join(root, sid)
    os.makedirs(os.path.join(bundle, "task"))
    os.makedirs(os.path.join(bundle, "summary"))
    with open(os.path.join(bundle, f"session_{sid}.json"), "w") as fh:
        fh.write("{}")
    header = _HEADER.format(sid=sid)
    if planned:
        header += ("# probe_blocks probe_enabled=True probe_block_len=10 "
                   "probe_windows=30-70\n")
    with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"), "w") as fh:
        fh.write(header)
        fh.write(_COLS)
        for t in range(1, n + 1):
            fh.write(_row(t, "HIGH", "LEFT", "CORRECT", 1))
    if disabled:
        with open(os.path.join(bundle, "summary", f"notes_{sid}.jsonl"),
                  "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": 0, "iso": "2026-01-01T12:00:03", "text": "probes disabled",
                "trial_index": 3, "tag": session_notes.AUTO_PROBE_DISABLED,
                "count": 1, "source": "auto"}) + "\n")
    return bundle


class TestHeaderParser(unittest.TestCase):
    def test_parses_key_values_skips_notes(self):
        hdr = br.parse_header_comments(_HEADER.format(sid="600_20260101_120000"))
        self.assertEqual(hdr["high_tone_is_left"], "True")
        self.assertEqual(hdr["generator"], "intervention_blocks")
        self.assertEqual(hdr["high_tone_hz"], "8000")
        self.assertNotIn("prose", hdr)


class TestDiscover(unittest.TestCase):
    def test_filters_by_animal_and_date(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(root, "600_20260101_120000")
            _make_bundle(root, "601_20260105_120000")
            _make_bundle(root, "000_20260101_120000")  # junk id
            got = br.discover_bundles(root, animals={"600", "601"})
            names = sorted(os.path.basename(p) for p in got)
            self.assertEqual(
                names, ["600_20260101_120000", "601_20260105_120000"]
            )
            # date bound excludes the later one
            got2 = br.discover_bundles(
                root, animals={"600", "601"}, end_date="2026-01-03"
            )
            self.assertEqual(len(got2), 1)


class TestLoadAnimals(unittest.TestCase):
    def test_loads_trials_and_meta(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(root, "600_20260101_120000", n=5)
            _make_bundle(root, "600_20260102_120000", n=3)
            data = br.load_animals(("600",), root=root)
            self.assertEqual(len(data.sessions), 2)
            self.assertEqual(len(data.trials), 8)
            self.assertTrue(data.excursions.empty)
            # global seq is contiguous and ordered by session start
            self.assertEqual(list(data.trials["seq"]), list(range(8)))
            self.assertEqual(data.sessions.iloc[0]["high_tone_is_left"], True)
            self.assertEqual(
                data.sessions.iloc[0]["generator"], "intervention_blocks"
            )

    def test_drops_superseded_trial_edit_rows(self):
        # A Repeat/Back preserves the animal's real attempt as a superseded row in
        # the CSV (ADR-0119); the tidy trials frame must count each trial once (its
        # current attempt), not the preserved one. Render via the real log so the
        # column set/order can't drift from the fixture.
        from tmaze.core.phase3.phase3_log import Phase3Log
        from tmaze.core.phase3.trial_sequence import TrialConfig, TrialSequence

        with tempfile.TemporaryDirectory() as root:
            sid = "600_20260101_120000"
            bundle = os.path.join(root, sid)
            os.makedirs(os.path.join(bundle, "task"))
            with open(os.path.join(bundle, f"session_{sid}.json"), "w") as fh:
                fh.write("{}")
            seq = TrialSequence(
                TrialConfig(max_trials=3, block_size=30, max_same_run=3), seed=7)
            log = Phase3Log(seq, animal="600",
                            session_start_iso="2026-01-01T12:00:00",
                            session_id=sid)
            for i in range(3):
                log.choice_scored(i, "left", "CORRECT", f"2026-01-01T12:00:0{i}")
                log.trial_ended(i, f"2026-01-01T12:00:1{i}")
            # Re-run trial index 1 (Repeat) as an incorrect RIGHT choice.
            log.supersede_trial(1, "repeat")
            log.choice_scored(1, "right", "INCORRECT", "2026-01-01T12:00:20")
            log.trial_ended(1, "2026-01-01T12:00:25")
            with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"),
                      "w", encoding="utf-8") as fh:
                fh.write(log.render_csv())

            data = br.load_animals(("600",), root=root)
            self.assertEqual(len(data.trials), 3)          # superseded row dropped
            self.assertEqual(data.sessions.iloc[0]["n_trials"], 3)
            row2 = data.trials[data.trials["trial"] == 2].iloc[0]
            self.assertEqual(row2["chosen_arm"], "RIGHT")  # the kept re-run attempt
            self.assertEqual(row2["outcome"], "INCORRECT")


class TestDropsUnrunPlaceholders(unittest.TestCase):
    """ADR-0131: phase3_log pre-writes the full planned cue matrix, so un-run trials
    sit as placeholder rows. The tidy frame keeps only ATTEMPTED trials, so ``seq``
    stays contiguous and ``n_trials`` reflects real activity."""

    def test_unrun_rows_dropped_seq_contiguous(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle_with_unrun(root, "600_20260101_120000", n_real=3, n_blank=17)
            _make_bundle_with_unrun(root, "600_20260102_120000", n_real=2, n_blank=18)
            data = br.load_animals(("600",), root=root)
            self.assertEqual(len(data.trials), 5)                 # attempted only
            self.assertEqual(list(data.trials["seq"]), list(range(5)))  # contiguous
            self.assertEqual(list(data.sessions["n_trials"]), [3, 2])

    def test_all_placeholder_session_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle_with_unrun(root, "600_20260101_120000", n_real=0, n_blank=20)
            _make_bundle(root, "600_20260102_120000", n=4)
            data = br.load_animals(("600",), root=root)
            self.assertEqual(len(data.sessions), 1)               # aborted start dropped
            self.assertEqual(data.sessions.iloc[0]["session_id"],
                             "600_20260102_120000")
            self.assertEqual(len(data.trials), 4)

    def test_abandoned_trial_kept(self):
        with tempfile.TemporaryDirectory() as root:
            sid = "600_20260101_120000"
            bundle = os.path.join(root, sid)
            os.makedirs(os.path.join(bundle, "task"))
            with open(os.path.join(bundle, f"session_{sid}.json"), "w") as fh:
                fh.write("{}")
            with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"), "w") as fh:
                fh.write(_HEADER.format(sid=sid))
                fh.write(_COLS)
                fh.write(_row(1, "HIGH", "LEFT", "CORRECT", 1))   # completed -> kept
                fh.write(_abandoned_row(2, "LOW"))                # started -> kept
                fh.write(_blank_row(3, "HIGH"))                   # never run -> dropped
            data = br.load_animals(("600",), root=root)
            self.assertEqual(sorted(data.trials["trial"].tolist()), [1, 2])
            self.assertEqual(data.sessions.iloc[0]["n_trials"], 2)


class TestSessionAnchor(unittest.TestCase):
    """ADR-0124: the sessions frame anchors to the canonical session t0 when the
    header carries it, and falls back to the gate-arm time for older bundles; the
    gate-arm time is always preserved in its own column so no anchor is lost."""

    _HDR_BOTH = (
        "# phase3_session animal=600 session_id={sid} seed=42 proto=9\n"
        "# high_tone_is_left=True high_tone_hz=8000 low_tone_hz=4000\n"
        "# generator=balanced\n"
        "# session_t0_iso=2026-01-01T12:00:05.250 session_t0_wall=1 session_t0_mono=2\n"
        "# session_start_iso=2026-01-01T12:00:00.000\n"
    )
    _HDR_OLD = (
        "# phase3_session animal=600 session_id={sid} seed=42 proto=8\n"
        "# high_tone_is_left=True high_tone_hz=8000 low_tone_hz=4000\n"
        "# generator=balanced\n"
        "# session_start_iso=2026-01-01T12:00:00.000\n"
    )

    def _bundle(self, root, sid, hdr):
        bundle = os.path.join(root, sid)
        os.makedirs(os.path.join(bundle, "task"))
        with open(os.path.join(bundle, f"session_{sid}.json"), "w") as fh:
            fh.write("{}")
        with open(os.path.join(bundle, "task", f"phase3_{sid}.csv"), "w") as fh:
            fh.write(hdr.format(sid=sid))
            fh.write(_COLS)
            fh.write(_row(1, "HIGH", "LEFT", "CORRECT", 1))
        return bundle

    def test_prefers_t0_and_keeps_gate_arm(self):
        with tempfile.TemporaryDirectory() as root:
            self._bundle(root, "600_20260101_120000", self._HDR_BOTH)
            s = br.load_animals(("600",), root=root).sessions.iloc[0]
            self.assertEqual(s["start_iso"], "2026-01-01T12:00:05.250")
            self.assertEqual(s["gate_arm_iso"], "2026-01-01T12:00:00.000")

    def test_falls_back_to_gate_arm_for_old_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            self._bundle(root, "600_20260102_120000", self._HDR_OLD)
            s = br.load_animals(("600",), root=root).sessions.iloc[0]
            self.assertEqual(s["start_iso"], "2026-01-01T12:00:00.000")
            self.assertEqual(s["gate_arm_iso"], "2026-01-01T12:00:00.000")


class TestMultiRoot(unittest.TestCase):
    """A history split across a primary + archive root (docs/decisions/0113):
    discovery/loading spans both and de-duplicates by session_id, first wins."""

    def test_discover_merges_two_roots(self):
        with tempfile.TemporaryDirectory() as primary, \
                tempfile.TemporaryDirectory() as archive:
            _make_bundle(primary, "600_20260201_120000")   # newest, on primary
            _make_bundle(archive, "600_20260101_120000")   # archived, older
            got = br.discover_bundles([primary, archive], animals={"600"})
            names = sorted(os.path.basename(p) for p in got)
            self.assertEqual(
                names, ["600_20260101_120000", "600_20260201_120000"])

    def test_discover_dedups_first_root_wins(self):
        with tempfile.TemporaryDirectory() as primary, \
                tempfile.TemporaryDirectory() as archive:
            sid = "600_20260101_120000"          # exists in BOTH (mid-copy)
            _make_bundle(primary, sid)
            _make_bundle(archive, sid)
            got = br.discover_bundles([primary, archive], animals={"600"})
            self.assertEqual(len(got), 1)
            # kept from the earliest-listed (primary/source) root
            self.assertTrue(os.path.dirname(got[0]) == primary)

    def test_load_animals_spans_two_roots(self):
        with tempfile.TemporaryDirectory() as primary, \
                tempfile.TemporaryDirectory() as archive:
            _make_bundle(primary, "600_20260201_120000", n=2)
            _make_bundle(archive, "600_20260101_120000", n=3)
            data = br.load_animals(("600",), root=[primary, archive])
            self.assertEqual(len(data.sessions), 2)
            self.assertEqual(len(data.trials), 5)
            # ordered by session start: the archived (older) session comes first
            self.assertEqual(list(data.trials["seq"]), list(range(5)))
            self.assertEqual(data.trials.iloc[0]["session_id"],
                             "600_20260101_120000")


class TestProbeNotesAndFlags(unittest.TestCase):
    """The notes frame + probe_planned/probe_disabled session flags
    (docs/decisions/0137)."""

    def test_probe_planned_but_not_disabled(self):
        with tempfile.TemporaryDirectory() as root:
            _make_probe_bundle(root, "600_20260101_120000",
                               planned=True, disabled=False)
            data = br.load_animals(("600",), root=root)
            s = data.sessions.iloc[0]
            self.assertTrue(bool(s["probe_planned"]))
            self.assertFalse(bool(s["probe_disabled"]))
            self.assertTrue(data.notes.empty)      # no notes file written

    def test_probe_disabled_flag_and_notes_frame(self):
        with tempfile.TemporaryDirectory() as root:
            _make_probe_bundle(root, "600_20260101_120000",
                               planned=True, disabled=True)
            data = br.load_animals(("600",), root=root)
            s = data.sessions.iloc[0]
            self.assertTrue(bool(s["probe_planned"]))
            self.assertTrue(bool(s["probe_disabled"]))
            self.assertFalse(data.notes.empty)
            self.assertIn(session_notes.AUTO_PROBE_DISABLED,
                          set(data.notes["tag"]))
            self.assertEqual(data.notes.iloc[0]["session_id"],
                             "600_20260101_120000")

    def test_no_notes_or_probe_plan_still_loads(self):
        with tempfile.TemporaryDirectory() as root:
            _make_bundle(root, "600_20260101_120000", n=3)  # plain bundle
            data = br.load_animals(("600",), root=root)
            s = data.sessions.iloc[0]
            self.assertFalse(bool(s["probe_planned"]))
            self.assertFalse(bool(s["probe_disabled"]))
            self.assertTrue(data.notes.empty)


if __name__ == "__main__":
    unittest.main()
