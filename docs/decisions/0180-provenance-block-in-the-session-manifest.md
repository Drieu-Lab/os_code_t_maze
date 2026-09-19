# ADR 0180: Record a provenance block in the session manifest (schema 2->3)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/provenance.py` (new), `host/tmaze/core/session/session_log.py` (`SessionManifest`), `host/tmaze/ui/session_controller.py`, `host/tmaze/ui/camera_view.py`, `host/tmaze/ui/experiment_control.py`, `host/tmaze/tools/build_session_registry.py`, `host/tools/make_golden_bundle.py`, the golden fixture (rig + analysis mirror)
- **Supersedes / Superseded by:** — (builds on ADR-0072/0124/0164/0165/0166; folds in the manifest half of Increment 7d; picks up P5 from ADR-0015/0091)

## Context

A bundle recorded *what* happened but not *what produced it*. To reproduce or
audit a session a future reader needs the version facts the rig knew at Start and
never wrote down (review findings P1/P5/P6):

- **Which code ran** — the code repo's commit (the registry CSV already computed a
  `code_commit` for its header, but every bundle stood alone with none).
- **Which environment** — the Python version and the versions of the rig-critical
  packages (camera SDK, vision, video encode, arrays, serial link), so an analysis
  that hits a pypylon/opencv/av quirk can tell which build wrote the frames.
- **Which firmware** — the serial protocol the firmware speaks. ADR-0164/0165/0166
  made `PROTO_VERSION` (7->8->...->12) the exact record of the S/L polarity and the
  firmware-ms columns; a bundle should carry both what `rig_config.h` *declares*
  and what the live `0,PROTO` handshake actually *reported* this session, so a
  mismatch (a stale flash) is visible offline.
- **P5** — the config snapshot hashed the *default* `camera_calibration.json`
  constant, not the calibration a "Load calibration..." action had actually loaded
  into the undistorter, so the snapshot could disagree with the frames.

Separately, Increment 7d needs the manifest to carry an append-only log of edits an
OFFLINE tool makes after Stop (e.g. `sync_fit` registering the clock fit). Adding
that field is a manifest layout change too, so folding it into the SAME
`schema_version` bump keeps the offline reader branching on one version step, not
two.

## Decision

Add a `provenance` block and an append-only `post_hoc` list to `SessionManifest`
and bump its `SCHEMA_VERSION` 2 -> 3.

- **`tmaze.core.provenance`** (new, stdlib + `importlib.metadata` only). `collect(firmware_proto_seen)`
  returns `{code_commit, python_version, host_name, packages, firmware_proto_source,
  firmware_proto_seen}`. Every lookup is guarded so it can never block Start: git is
  shelled with a 10 s timeout and degrades to `"unknown"`; a package that isn't
  installed is omitted; `firmware_proto_source` reads `PROTO_VERSION` from
  `rig_config.h` (None if unreadable). `code_commit` (short sha `+dirty`) MOVES here
  from `build_session_registry`, which now imports it, so the manifest and the
  registry header share ONE definition.
- **`SessionManifest`** gains `provenance: Dict = {}` and `post_hoc: list = []`
  (both defaulting empty, so from_dict tolerates a v2 file), plus `set_provenance()`
  / `add_post_hoc()`. `session_controller` fills `provenance` once at Start with
  `provenance.collect(firmware_proto_seen=self._firmware_proto)`; the rig writes
  `post_hoc` empty (only post-hoc tools append — the wiring is 7d).
- **P5**: `camera_view.current_calibration_path()` returns the path currently loaded
  into the undistorter; `experiment_control.config_inputs()` hashes THAT (falling
  back to the default constant when none is loaded), so the snapshot records the
  calibration in use.
- **Firmware `VERSION` command** (a fuller build string than the proto int) is
  deferred to Reflash A; the host-side proto source/seen fields land now.
- The golden fixture uses a FIXED provenance stub (`code_commit: "0000000"`, fixed
  python/host/packages, proto source==seen==8) and empty `post_hoc`, never live
  values — the bundle must stay byte-reproducible. Re-issued in the rig repo and
  re-copied to the analysis mirror.

## Alternatives considered

- **Leave provenance out of the bundle (registry header only).** — The registry is
  a rebuildable index, not part of a bundle; a bundle copied off the rig would carry
  no code/firmware/env record at all. Rejected.
- **Two schema bumps (provenance now, post_hoc in 7d).** — Two layout steps for the
  reader to branch on when one covers both; `post_hoc` is a cheap empty default the
  rig already satisfies. Rejected — folded into one bump.
- **Collect live git/package/host values in the fixture.** — Makes the golden bundle
  non-reproducible and its round-trip test machine-dependent. Rejected (fixed stub).
- **Bump `format_version` instead.** — `format_version` describes the on-disk TASK
  data contract (tokens, zones, CSV columns) the analysis mirror guards;
  provenance/post_hoc are manifest metadata, which is exactly what the manifest's own
  `schema_version` is for (ADR-0072). Rejected.
- **A firmware `VERSION` string now.** — Needs a reflash (Reflash A) and a serial
  round-trip; the proto int the client already sees covers the version-critical
  facts today. Deferred.

## Consequences

Every new bundle carries a self-contained record of the code, environment, and
firmware that produced it, and the config snapshot hashes the calibration actually
used. `code_commit` has one definition shared by the manifest and the registry. The
manifest is ready for the 7d post-hoc edit log with no further schema step. Costs:
a few short git/metadata calls at Start (guarded, ~tens of ms, never fatal); a
manifest `schema_version` of 3 that offline readers tolerate (the analysis reader
does not guard the manifest `schema_version`, only `format_version` + the summary
schema, so it reads v2 and v3 alike). A clean session's non-manifest files are
byte-for-byte unchanged. Follow-up: wire the `post_hoc` appends in `sync_fit` (7d);
add the firmware `VERSION` string at Reflash A (P1/P6).

## Evidence / links

- Review findings P1/P5/P6 and Increment 7d in
  `sensor_polarity_hand_check_5b3798c1.plan.md`.
- Firmware-proto provenance chain: ADR-0164 (S/L polarity, proto 8), ADR-0165/0166
  (firmware-ms twins, proto 11/12). Calibration source: ADR-0015 (undistort),
  ADR-0091. Manifest schema precedent: ADR-0072 (config streams, schema 2),
  ADR-0124 (canonical t0).
- Fixture: `host/tools/make_golden_bundle.py` re-run; manifest schema round-trip in
  `host/tests/test_session_log.py`; provenance shape in `host/tests/test_provenance.py`.
