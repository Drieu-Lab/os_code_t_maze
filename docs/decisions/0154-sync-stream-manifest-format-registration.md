# ADR 0154: Register the LED clock-fit as a bundle stream (manifest + format v2)

- **Status:** Accepted
- **Date:** 2026-09-11
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/phase3/bundle_format.py` (`FORMAT_VERSION` 1→2 + a
  `sync` block), `host/tmaze/core/session/session_config.py` (`HOST_CONFIG_FILES`),
  `host/tmaze/core/session/session_log.py` (`SessionManifest.from_dict`),
  `host/tmaze/tools/sync_fit.py` (manifest rewrite + `--overlay` fit plot),
  the golden fixture, and the analysis mirror
  (`drieu_lab_analysis` `phase3_format.FORMAT_VERSION` + its golden fixture)
- **Supersedes / Superseded by:** Builds on ADR-0153 (the LED fit itself) and the
  session-manifest/config-snapshot ADRs (0048/0072/0124); does not supersede one.

## Context

The offline board-LED sync tool (`sync_fit`, ADR-0153) fits
`cam_timestamp = offset[seg] + rate·firmware_ms` and writes two files into a
bundle's `sync/` folder: `led_events_<id>.csv` (one row per matched LED edge) and
`clock_fit_<id>.json` (the fit + residual stats + per-LED verdicts + board pose).
That fit **is the measured answer to `#260720_DataVideoSync`**: the current
host-stamp join (`host_unix_s` vs the serial line's wall stamp) carries a **+59 ms
systematic bias** (~1.5 frames) plus ~0.25-frame jitter, which the LED fit removes.

Until now those files were *dropped in the folder but not announced*. Nothing in
the bundle's self-describing contract pointed at them: the session manifest
(`session_<id>.json`) did not list them as streams, its time-anchor
`fiducial.method` still read `host-t0`, the format manifest (`config/format.json`)
did not describe the `sync/` stream's columns, and `led_map.json` — the map a
session registers against — was not snapshotted into the bundle like the other
rig config. So a reader that trusts only the manifest would not act on the fit.

The manifest is authored by the rig at Session Start; the fit is computed
**offline, afterwards**, so registering it means an offline tool editing a file
the rig wrote. And `format.json` is a cross-repo contract: the analysis package
(`drieu_lab_analysis`) refuses any bundle whose `format_version` exceeds what it
supports, so a format change must land in lockstep on both sides.

## Decision

**Announce the sync stream in the bundle contract, as a coordinated two-repo
change.**

- **Snapshot the map.** Add `led_map.json` to
  `session_config.HOST_CONFIG_FILES`, so it is copied into `config/led_map.json`
  at Session Start with a SHA-256 like `zones.json` (ADR-0072). Best-effort:
  absent until a mapping session exists (`present: false`).
- **Register the streams + flip the method.** On a normal (non-`--dry-run`) run,
  `sync_fit` reads `session_<id>.json` via the new pure builder
  `SessionManifest.from_dict`, records the `sync_led_events` / `sync_clock_fit`
  streams (bundle-relative paths), and sets the time-anchor `fiducial.method` to
  `led-fit`. It writes the file back **atomically** (temp file + `os.replace`),
  so the rig-authored manifest is never torn; the operation is idempotent. A
  `--dry-run` leaves the bundle and its manifest untouched.
- **Keep the `pending` note.** `fiducial.pending` still points at the deferred
  sub-ms hardware sync (firmware SYNC TTL + camera LED barcode, FW-0018): the
  LED fit is a ~9.5 ms software alignment (inside one 40 ms frame), a real
  improvement on `host-t0` but **not** the hardware-grade upgrade, so that work
  stays flagged.
- **Minimal manifest shape.** Only the `method` value changes and two stream
  keys are added; no new fiducial sub-block and no duplicated fit numbers (those
  live in `sync/clock_fit_<id>.json`). So `session_log.SCHEMA_VERSION` is left at
  2 — the manifest layout an offline reader branches on is unchanged.
- **Describe the stream in `format.json`, bump `FORMAT_VERSION` 1→2.**
  `bundle_format.format_manifest()` gains a `sync` block enumerating the
  `led_events` column dictionary and the `clock_fit` key fields (incl. the
  `board_poly` + fiducial registration from ADR-0153). The golden fixture is
  re-issued and the analysis mirror (`phase3_format.FORMAT_VERSION`) + its copy
  of the fixture bump to 2 **in the same coordinated change**.
- **Optional QA fit plot.** `--overlay` additionally writes
  `sync/clock_fit_<id>.png` (residuals vs firmware-ms + a median/MAD band) via a
  lazily-imported `matplotlib`; it is an *optional* rig dependency (the plot is
  skipped with a note when absent), and the PNG is an unregistered QA artifact
  like the ROI overlay.

## Alternatives considered

- **Sidecar-only (never edit the manifest).** Leave `session_<id>.json` exactly
  as the rig wrote it and let readers discover `sync/` by its conventional path.
  Rejected with the user: a reader that opens only the manifest should discover
  the fit; the atomic read-modify-write makes editing safe, and the raw recorded
  streams (trial CSV, serial log, video, sidecar) are still never mutated — only
  the small index file is.
- **Decoupled (skip the `format.json` bump).** Register the streams in
  `session.json` only and defer `format.json`/`FORMAT_VERSION` (no analysis-repo
  lockstep). Rejected with the user in favour of the full, complete contract now;
  the coordinated bump is done in one work session so no bundle is unreadable in
  between.
- **Rich `fiducial.led_fit` summary in the manifest** (rate, MAD, pose). Rejected:
  it duplicates numbers the `sync_clock_fit` stream already holds and would force
  a `SCHEMA_VERSION` bump for no reader that needs it. Kept the block minimal.
- **Register the QA plot / overlay as streams.** Rejected: they are debug aids,
  not data a reader consumes.
- **Make `matplotlib` a hard rig dependency.** Rejected: the rig thin shell
  avoids heavy deps; the plot degrades cleanly, mirroring the guarded
  `pyarrow`/`pynwb` optionals.

## Consequences

- The bundle is now fully self-describing about the sync stream: manifest streams
  + `fiducial.method: led-fit`, `format.json` v2 documenting the columns, and a
  snapshotted `config/led_map.json`. `#260720_DataVideoSync` is answered (the
  number lives in `sync/clock_fit_<id>.json`) and closed.
- **Cross-repo lockstep is mandatory:** a rig bundle at `format_version 2` is
  rejected by an analysis package still pinned to 1. The mirror bump + fixture
  copy in `drieu_lab_analysis` ship together with this change. A future format
  change repeats the drill (bump both, re-issue the fixture).
- The analysis package does not yet *read* the fit (it consumes only the trial
  CSV + sidecar today); a `clock_fit` reader that applies the correction is a
  follow-on, now that the stream is a first-class, versioned part of the contract.
- Re-running `sync_fit` is safe: it overwrites its own two `sync/` files and
  re-applies the same idempotent manifest edit.

## Evidence / links

- Code: `bundle_format.py` (`sync` block + `FORMAT_VERSION = 2`),
  `session_config.py` (`led_map.json` in `HOST_CONFIG_FILES`), `session_log.py`
  (`SessionManifest.from_dict`), `sync_fit.py` (`register_sync_in_manifest`,
  `_atomic_write`, `write_fit_plot`).
- Tests: `test_session_log.py` (from_dict round-trip), `test_session_config.py`
  (led_map snapshot), `test_golden_bundle.py` (format v2 byte-for-byte),
  `test_sync_fit.py` (manifest registration, dry-run leaves it byte-identical,
  `--overlay` fit plot).
- Measurement: design doc `docs/design/cross-stream-sync-design.md` §3.7 (+59 ms
  host-stamp bias, 9.5 ms fit MAD); ADR-0153 (the fit + fiducial ROI).
- Analysis mirror: `drieu_lab_analysis` `phase3_format.FORMAT_VERSION` 1→2 + its
  golden fixture copy.
- Backlog: FW-0045 (this feature, now done), FW-0018 (the deferred hardware sync).
