"""
test_build_session_registry.py - the registry tool's scan/output paths.

Guards the ADR-0088 fix: ``build_session_registry.main`` scans the ``--root``
folder (finding real bundles there - the bug was scanning an empty default) and
writes ``session_registry.csv`` to ``--out-dir`` *independently* of the scan
root, so the CSV keeps landing where the dashboard + resources builder read it.

Run from host/tests:  python -m unittest test_build_session_registry
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import unittest

from tmaze.core.records import parse_commented_csv
from tmaze.core.session import session_registry
from tmaze.tools import build_session_registry as bsr


def _make_bundle(root: str, session_id: str) -> None:
    """A minimal session bundle: a dir carrying its manifest (that's all
    ``session_registry.is_bundle`` requires; docs/decisions/0049)."""
    d = os.path.join(root, session_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"session_{session_id}.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{}")


class TestScanAndOutputDecoupled(unittest.TestCase):
    def test_scans_root_and_writes_to_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan = os.path.join(tmp, "rig_recordings")
            out = os.path.join(tmp, "code_repo_out")
            os.makedirs(scan, exist_ok=True)
            _make_bundle(scan, "600_20260101_120000")
            _make_bundle(scan, "601_20260101_130000")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bsr.main(["--root", scan, "--out-dir", out])
            self.assertEqual(rc, 0)

            reg = os.path.join(out, bsr.REGISTRY_FILENAME)
            # Written to --out-dir, NOT next to the scanned bundles.
            self.assertTrue(os.path.isfile(reg))
            self.assertFalse(os.path.isfile(os.path.join(scan, bsr.REGISTRY_FILENAME)))

            with open(reg, "r", encoding="utf-8") as fh:
                data = [ln for ln in fh if not ln.startswith("#")]
            # header + 2 session rows
            self.assertEqual(len(data), 3)
            self.assertIn("600_20260101_120000", "".join(data))
            self.assertIn("601_20260101_130000", "".join(data))

    def test_merges_multiple_roots_and_dedups(self):
        # A split-across-drives history (docs/decisions/0113): --root is
        # repeatable; bundles from every root are merged and de-duplicated by
        # session_id (first-listed root wins).
        with tempfile.TemporaryDirectory() as tmp:
            primary = os.path.join(tmp, "rig_recordings")
            archive = os.path.join(tmp, "archive_recordings")
            out = os.path.join(tmp, "out")
            os.makedirs(primary, exist_ok=True)
            os.makedirs(archive, exist_ok=True)
            _make_bundle(primary, "600_20260201_120000")   # newest, on primary
            _make_bundle(archive, "600_20260101_120000")   # archived, older
            _make_bundle(archive, "600_20260201_120000")   # dup of the primary one

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bsr.main(["--root", primary, "--root", archive,
                               "--out-dir", out])
            self.assertEqual(rc, 0)

            reg = os.path.join(out, bsr.REGISTRY_FILENAME)
            with open(reg, "r", encoding="utf-8") as fh:
                data = [ln for ln in fh if not ln.startswith("#")]
            # header + 2 unique sessions (the duplicate id collapses to one)
            self.assertEqual(len(data), 3)
            joined = "".join(data)
            self.assertIn("600_20260101_120000", joined)
            self.assertIn("600_20260201_120000", joined)
            self.assertIn("2 sessions", buf.getvalue())

    def test_writes_provenance_header_and_root_relative_bundles(self):
        # Schema v2 (ADR-0157): the CSV opens with `# key=value` lines saying
        # when/from what it was built, and each row records (root, bundle).
        with tempfile.TemporaryDirectory() as tmp:
            scan = os.path.join(tmp, "rig_recordings")
            out = os.path.join(tmp, "out")
            os.makedirs(scan, exist_ok=True)
            _make_bundle(scan, "600_20260101_120000")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = bsr.main(["--root", scan, "--out-dir", out])
            self.assertEqual(rc, 0)
            with open(os.path.join(out, bsr.REGISTRY_FILENAME), "r",
                      encoding="utf-8") as fh:
                text = fh.read()
            version, meta = session_registry.registry_meta(text)
            self.assertEqual(version, session_registry.SCHEMA_VERSION)
            self.assertTrue(meta.built_at.startswith("20"))
            self.assertTrue(meta.code_commit)              # sha or "unknown"
            self.assertEqual(meta.roots, [os.path.abspath(scan)])
            self.assertEqual(set(meta.inputs),
                             {"daily_log", "note_tags", "session_ignore"})
            rows = parse_commented_csv(text).rows
            self.assertEqual(rows[0]["bundle"], "600_20260101_120000")
            self.assertEqual(rows[0]["root"], os.path.abspath(scan))
            self.assertTrue(os.path.isdir(
                session_registry.bundle_path(rows[0])))

    def test_refuses_to_shrink_unless_allowed(self):
        # ADR-0158: a rebuild that finds FEWER sessions than the current file
        # holds (archive unmounted, mistyped root) writes nothing and exits 2;
        # --allow-shrink accepts it.
        with tempfile.TemporaryDirectory() as tmp:
            scan = os.path.join(tmp, "rig_recordings")
            out = os.path.join(tmp, "out")
            os.makedirs(scan, exist_ok=True)
            _make_bundle(scan, "600_20260101_120000")
            _make_bundle(scan, "601_20260101_130000")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bsr.main(["--root", scan, "--out-dir", out]), 0)
            reg = os.path.join(out, bsr.REGISTRY_FILENAME)
            with open(reg, "r", encoding="utf-8") as fh:
                before = fh.read()

            # "Lose" one session (simulates an unreachable root).
            shutil.rmtree(os.path.join(scan, "601_20260101_130000"))

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bsr.main(["--root", scan, "--out-dir", out])
            self.assertEqual(rc, 2)
            self.assertIn("REFUSED", buf.getvalue())
            self.assertIn("601_20260101_130000", buf.getvalue())
            self.assertIn("--allow-shrink", buf.getvalue())
            with open(reg, "r", encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)      # untouched

            # Structured form for the GUI: same decision, as data.
            outcome = bsr.run_build([scan], out)
            self.assertFalse(outcome.ok)
            self.assertTrue(outcome.refused_shrink)
            self.assertEqual(outcome.vanished, ["601_20260101_130000"])
            self.assertEqual(outcome.previous_sessions, 2)
            self.assertEqual(outcome.sessions, 1)
            self.assertIn("REFUSED", outcome.summary())

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bsr.main(["--root", scan, "--out-dir", out,
                               "--allow-shrink"])
            self.assertEqual(rc, 0)
            self.assertIn("dropped 1 previously indexed", buf.getvalue())
            with open(reg, "r", encoding="utf-8") as fh:
                self.assertEqual(session_registry.registry_ids(fh.read()),
                                 ["600_20260101_120000"])

    def test_growth_and_first_build_never_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan = os.path.join(tmp, "rig_recordings")
            out = os.path.join(tmp, "out")
            os.makedirs(scan, exist_ok=True)
            _make_bundle(scan, "600_20260101_120000")
            first = bsr.run_build([scan], out)
            self.assertTrue(first.ok)
            self.assertIsNone(first.previous_sessions)   # no file to compare
            self.assertIsNone(first.shrink)
            _make_bundle(scan, "600_20260102_120000")
            second = bsr.run_build([scan], out)
            self.assertTrue(second.ok)
            self.assertEqual(second.previous_sessions, 1)
            self.assertEqual(second.sessions, 2)
            self.assertIsNone(second.shrink)
            self.assertEqual(second.vanished, [])

    def test_run_build_reports_errors_as_data(self):
        # An unwritable output location comes back as ok=False + error, not an
        # exception in the caller (the GUI's worker thread).
        with tempfile.TemporaryDirectory() as tmp:
            scan = os.path.join(tmp, "rig_recordings")
            os.makedirs(scan, exist_ok=True)
            blocker = os.path.join(tmp, "not_a_dir")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("x")
            outcome = bsr.run_build([scan], os.path.join(blocker, "out"))
            self.assertFalse(outcome.ok)
            self.assertFalse(outcome.refused_shrink)
            self.assertTrue(outcome.error)
            self.assertIn("FAILED", outcome.summary())

    def test_empty_root_writes_header_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan = os.path.join(tmp, "empty")
            out = os.path.join(tmp, "out")
            os.makedirs(scan, exist_ok=True)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = bsr.main(["--root", scan, "--out-dir", out])
            self.assertEqual(rc, 0)
            self.assertIn("0 sessions", buf.getvalue())

    def test_defaults_are_decoupled(self):
        # The scan-root default is the rig recordings folder; the output default
        # is host/data/phase3_recordings - they must be different locations.
        self.assertNotEqual(os.path.normpath(bsr.DEFAULT_SCAN_ROOT),
                            os.path.normpath(bsr.REGISTRY_OUTPUT_DIR))
        self.assertTrue(bsr.DEFAULT_SCAN_ROOT.endswith("phase3_recordings"))
        self.assertTrue(bsr.REGISTRY_OUTPUT_DIR.endswith(
            os.path.join("data", "phase3_recordings")))


if __name__ == "__main__":
    unittest.main()
