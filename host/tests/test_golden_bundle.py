"""
test_golden_bundle.py - the writer keeps the committed golden fixture honest.

``tools/make_golden_bundle.py`` writes a tiny, complete session bundle with the
REAL rig write path (``Phase3Log.render_csv`` + ``bundle_format.format_manifest``),
committed under ``tests/fixtures/`` and copied into the analysis repo
(``drieu_lab_analysis``) as the shared data-contract reference (FW-0048 / FW-0050).

This test regenerates that bundle into a temp dir and diffs it, byte-for-byte,
against the committed copy. A mismatch means the rig changed the on-disk FORMAT -
the reminder to bump ``bundle_format.FORMAT_VERSION`` and re-copy the fixture to
the analysis repo. Because the generator is fully hard-coded (id/seed/timestamps),
regeneration is deterministic, so this diff is stable.

Run from host/:  python -m unittest tests.test_golden_bundle
"""
from __future__ import annotations

import os
import tempfile
import unittest

from tools.make_golden_bundle import SID, build_bundle

_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _tree(root):
    """{relative path: bytes} for every file under ``root``.

    Line endings are normalised to ``\\n`` so a CRLF checkout (git ``autocrlf``
    on Windows) of the committed fixture still matches the LF the writer emits -
    the test guards the FORMAT, not the platform's newline convention.
    """
    out = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read().replace(b"\r\n", b"\n")
    return out


class TestGoldenBundleMatchesWriter(unittest.TestCase):
    def test_regenerates_committed_fixture_byte_for_byte(self):
        committed = os.path.join(_FIXTURES, SID)
        self.assertTrue(os.path.isdir(committed),
                        "golden fixture missing; run python -m tools.make_golden_bundle")
        with tempfile.TemporaryDirectory() as tmp:
            build_bundle(tmp)
            fresh = _tree(os.path.join(tmp, SID))
        have = _tree(committed)
        self.assertEqual(sorted(have), sorted(fresh), "fixture file set drifted")
        for rel in sorted(have):
            self.assertEqual(
                have[rel], fresh[rel],
                f"{rel} drifted from the writer - regenerate the fixture "
                "(python -m tools.make_golden_bundle) and bump FORMAT_VERSION if "
                "the on-disk format changed")


if __name__ == "__main__":
    unittest.main()
