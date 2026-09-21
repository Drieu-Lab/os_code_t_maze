"""
test_provenance.py - headless tests for the session provenance helper.

Checks the shape + graceful degradation of tmaze.core.provenance (P1/P6,
docs/decisions/0180): the block always has the expected keys, a missing package
is omitted rather than raising, and the last-seen firmware proto is passed
through (else "unknown"). Pure stdlib; no rig.

Run from host/:  python -m unittest test_provenance
"""

from __future__ import annotations

import unittest

from tmaze.core import provenance


class TestCollect(unittest.TestCase):
    def test_block_has_all_expected_keys(self):
        block = provenance.collect()
        for key in ("code_commit", "python_version", "host_name", "packages",
                    "firmware_proto_source", "firmware_proto_seen",
                    "firmware_git_sha", "firmware_build"):
            self.assertIn(key, block, key)

    def test_code_commit_and_python_version_are_nonempty_strings(self):
        block = provenance.collect()
        self.assertIsInstance(block["code_commit"], str)
        self.assertTrue(block["code_commit"])          # sha or "unknown"
        self.assertIsInstance(block["python_version"], str)
        self.assertTrue(block["python_version"])

    def test_firmware_proto_seen_passthrough(self):
        self.assertEqual(provenance.collect(firmware_proto_seen=12)["firmware_proto_seen"], 12)

    def test_firmware_proto_seen_defaults_to_unknown(self):
        self.assertEqual(provenance.collect()["firmware_proto_seen"], "unknown")

    def test_firmware_build_identity_passthrough(self):
        # From the VERSION reply (proto v9, docs/decisions/0193).
        block = provenance.collect(firmware_git_sha="abc1234",
                                   firmware_build="Sep 21 2026 16:40:00")
        self.assertEqual(block["firmware_git_sha"], "abc1234")
        self.assertEqual(block["firmware_build"], "Sep 21 2026 16:40:00")

    def test_firmware_build_identity_defaults_to_unknown(self):
        block = provenance.collect()
        self.assertEqual(block["firmware_git_sha"], "unknown")
        self.assertEqual(block["firmware_build"], "unknown")


class TestPackageVersions(unittest.TestCase):
    def test_missing_package_is_omitted_not_raised(self):
        got = provenance.package_versions(("definitely-not-a-real-pkg-xyz",))
        self.assertEqual(got, {})

    def test_known_installed_package_is_reported(self):
        # this package is installed (pip install -e .), so it must appear
        got = provenance.package_versions(("tmaze",))
        self.assertIn("tmaze", got)
        self.assertIsInstance(got["tmaze"], str)


class TestCodeCommit(unittest.TestCase):
    def test_returns_a_string(self):
        self.assertIsInstance(provenance.code_commit(), str)

    def test_unknown_when_not_a_git_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            # A temp dir outside any repo -> git rev-parse fails -> "unknown".
            self.assertEqual(provenance.code_commit(cwd=d), "unknown")


if __name__ == "__main__":
    unittest.main()
