"""
test_bundle_format.py - the S/L sensor-polarity provenance in format.json.

Covers the ADR-0164 provenance: the run's firmware serial proto (seen on the
``0,PROTO`` handshake) is recorded into the bundle's ``config/format.json`` and
mapped to the S/L state convention (proto >= 8 corrected, else legacy inverted,
else unknown when the handshake was not seen). The golden-fixture byte-diff lives
in test_golden_bundle; this pins the pure derivation + the manifest fields.

Run from host/:  python -m unittest tests.test_bundle_format
"""
from __future__ import annotations

import unittest

from tmaze.core.phase3 import bundle_format as bf


class TestSensorPolarity(unittest.TestCase):
    def test_v8_and_above_is_corrected(self):
        self.assertEqual(bf.sensor_polarity_for(8), bf.POLARITY_CORRECTED)
        self.assertEqual(bf.sensor_polarity_for(9), bf.POLARITY_CORRECTED)

    def test_below_v8_is_legacy_inverted(self):
        self.assertEqual(bf.sensor_polarity_for(7), bf.POLARITY_LEGACY)
        self.assertEqual(bf.sensor_polarity_for(4), bf.POLARITY_LEGACY)

    def test_unseen_handshake_is_unknown(self):
        self.assertEqual(bf.sensor_polarity_for(None), bf.POLARITY_UNKNOWN)


class TestFormatManifestProvenance(unittest.TestCase):
    def test_records_firmware_proto_and_derived_polarity(self):
        man = bf.format_manifest(firmware_proto=8)
        self.assertEqual(man["format_version"], bf.FORMAT_VERSION)
        self.assertEqual(man["firmware_proto"], 8)
        self.assertEqual(man["sensor_polarity"], bf.POLARITY_CORRECTED)
        # The trial-CSV proto is a DIFFERENT number and must stay present.
        self.assertIn("proto", man)

    def test_missing_handshake_defaults_to_unknown(self):
        man = bf.format_manifest()
        self.assertIsNone(man["firmware_proto"])
        self.assertEqual(man["sensor_polarity"], bf.POLARITY_UNKNOWN)

    def test_legacy_firmware_marked_inverted(self):
        man = bf.format_manifest(firmware_proto=7)
        self.assertEqual(man["sensor_polarity"], bf.POLARITY_LEGACY)


class TestBackfillFirmwareProto(unittest.TestCase):
    """docs/decisions/0200: a version learned after Start fills an unset manifest."""

    def test_fills_an_unseen_proto_and_its_polarity(self):
        written = bf.format_manifest()
        out = bf.backfill_firmware_proto(written, 9)
        self.assertEqual(out["firmware_proto"], 9)
        self.assertEqual(out["sensor_polarity"], bf.POLARITY_CORRECTED)
        self.assertEqual(out, bf.format_manifest(firmware_proto=9))
        self.assertIsNone(written["firmware_proto"], "input must not be mutated")

    def test_never_overwrites_a_recorded_proto(self):
        self.assertIsNone(bf.backfill_firmware_proto(bf.format_manifest(7), 9))

    def test_nothing_learned_is_no_change(self):
        self.assertIsNone(bf.backfill_firmware_proto(bf.format_manifest(), None))


if __name__ == "__main__":
    unittest.main()
