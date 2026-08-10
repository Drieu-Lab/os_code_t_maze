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
import tempfile
import unittest

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
