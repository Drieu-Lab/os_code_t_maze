# ADR 0178: The per-session serial capture is the COMPLETE verbatim stream

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of Ravi
- **Affects:** `host/tmaze/ui/session_controller.py` (full capture in `apply()`, `serial_<id>.csv`, `serial_full` = real path, retired the filter + `serial_sensors` stream + `CENTER_SOL_PORT`/`_center_lick`), `host/tmaze/core/session/session_serial.py` (new `capture_filename`/`resolve_capture_path`; `is_sensor_or_central` kept as a reader helper), `host/tmaze/tools/sync_fit.py` + `host/tmaze/tools/led_sync_prototype.py` + `host/tmaze/core/sync/spans.py` (read `serial_<id>.csv`, fall back to `sensors_<id>.csv`), `host/tests/test_session_serial.py` + `host/tests/test_sync_fit.py`
- **Supersedes / Superseded by:** supersedes the *filtered per-session serial capture* portion of ADR-0049 (its per-session folder tree stands)

## Context

ADR-0049 gave each session its own inline serial file but, by the experimenter's
then-scope, wrote only a **filtered** subset - body-beam edges + the CENTRAL
well's lick/reward/solenoid + the `PONG` fiducial - to `serial/sensors_<id>.csv`.
The complete verbatim stream (cue echoes, MODE/handshake, other-port acks, the
`STATUS` heartbeat, left/right reward + rinse/drain valve acks) stayed only with
the separate `rig_logger.py` process, recorded in the manifest as a
find-it-by-time-window descriptor (`serial_full`).

That split has cost the analysis repeatedly:

- **The bundle is not self-contained.** Aligning clocks or reconstructing a
  session offline needs lines the filter dropped (e.g. the LED/valve acks a
  mapping session fires), so `sync_fit --map` had to be handed the external
  `rig_logger.py` CSV by hand. A bundle you copy/archive as a unit did not carry
  everything the run produced.
- **The dropped lines are exactly the interesting edge cases.** Cue echoes carry
  the firmware `CUE,ON` ms (the ADR-0165/0166 twins), the handshake carries
  `PROTO`/`MODE`, and the heartbeat is the liveness trace - all useful to an
  offline reader, none captured inline.
- **The saving is negligible.** Measured across 82 full-day `rig_logger`
  captures, the FULL stream adds about **7 KB/hour** over the filtered subset
  (~14 KB for a 2-hour session) - trivially small beside the video, and the
  writer is already crash-safe and fsynced (ADR-0174) and stamped at host
  receipt (ADR-0175).

The Session panel already sees *every* line via `apply()` (that is how the filter
ran), so capturing the whole stream is a one-line change on the same thread with
no new process and no change to `rig_logger.py` or the broker.

## Decision

**The per-session serial file is the complete verbatim stream.** While a session
is active, `apply()` appends EVERY firmware line it receives (any non-empty
`info.raw`) to `serial/serial_<id>.csv`, host-receipt-stamped (ADR-0175) in the
`rig_logger` column format `<wall_iso>,<ms>,<TYPE>,<payload...>`.

- **File name** is now `serial_<id>.csv` (was `sensors_<id>.csv`) - honest about
  holding the full stream. Minted via `session_serial.capture_filename`.
- **Manifest** records `serial_full` as the REAL in-bundle path (was a
  by-time-window pointer at the external `rig_logger.py` file). The
  `serial_sensors` stream is removed (nothing read it; tools build the path from
  the bundle + session id). This does not touch `config/format.json` or the
  golden fixture, neither of which enumerates the serial stream - so no
  `format_version` bump lands here (a later manifest `schema_version` tidy can
  fold in with the cluster-3 format work).
- **The old filter predicate is kept as a READER helper.**
  `session_serial.is_sensor_or_central` (and its `_line` wrapper) stay, unchanged
  and tested, so a consumer that wants the pre-0178 sensor/central VIEW of a full
  capture can reproduce it. The Session controller no longer imports it, and its
  now-unused `CENTER_SOL_PORT` constant + `_center_lick` lookup are removed.
- **Readers prefer the full file, fall back to the legacy one.** A new pure
  `session_serial.resolve_capture_path(serial_dir, id)` returns
  `serial_<id>.csv` when present, else `sensors_<id>.csv` (pre-0178 bundles),
  else the full name. `sync_fit`, `led_sync_prototype`, and the `spans` docs use
  it, so both old and new bundles read without a flag. `sync_fit --map` no longer
  needs `--serial <rig_logger.csv>` for a new bundle - the bundle's own capture
  now carries the left/right/rinse acks.

## Alternatives considered

- **Keep the filter, keep leaning on `rig_logger.py` for the rest** (status quo,
  ADR-0049). Rejected: the bundle stays non-self-contained for ~7 KB/hour of
  saving, and every offline tool needs a second, externally-named file.
- **Two files per session (filtered + full).** Rejected: pure redundancy - the
  reader helper reproduces the filtered view from the full file on demand.
- **Have `rig_logger.py` write into the session folder.** Rejected for the same
  reason as in ADR-0049: it is a separate, GUI-independent process that names its
  own file (ADR-0002/0003) and has no `session_id`. Capturing inside the panel
  (which already sees every line) keeps that isolation.
- **Bump `format_version` now.** Rejected as premature: the format manifest does
  not describe the serial stream, so nothing downstream needs the bump yet;
  bundling it with the cluster-3 format changes keeps schema churn to one step.

## Consequences

- A bundle is now genuinely self-contained: its `serial/serial_<id>.csv` holds
  everything the run put on the wire, host-receipt-stamped and fsynced.
- `sync_fit`/`led_sync_prototype` work off the in-bundle file for both mapping and
  ordinary sessions; the `--serial` override remains for pre-0178 (filtered)
  bundles or an external log.
- Old bundles keep working via the `sensors_<id>.csv` fallback; no migration.
- `rig_logger.py` still runs as the independent, always-on full logger; retiring
  its launch (now that the bundle self-captures) is a separate workflow step
  (Increment 4c) and is deliberately deferred here.
- Storage grows by ~14 KB per 2-hour session - immaterial next to the video.

## Evidence / links

- Supersedes the filtered-capture portion of `docs/decisions/0049`
  (per-session folder tree + filtered serial). The folder tree, `recordings_root`,
  and `out_dir` seams from 0049 are unchanged.
- Builds on `docs/decisions/0175` (host-receipt serial stamps), `0174` (durable +
  surfaced bundle writes), `0165`/`0166` (firmware-clock cue twins the echoes
  carry), `0124` (in-bundle `PONG` fiducial), `0002`/`0003` (transparent
  broker-only transport - unchanged), `0009` (`rig_logger.py`).
- Roadmap Increment 4a (`sensor_polarity_hand_check` plan): "full verbatim
  capture", after 4b (`docs/decisions/0177`, no orphans).
- Storage estimate: 82 full-day `rig_logger` captures - full vs filtered ≈ +7
  KB/hour, dominated by the `CUE` echoes and `STATUS` heartbeat.
- Code: `session_controller.apply` / serial open + manifest, `session_serial`
  (`capture_filename`/`resolve_capture_path`, reader helper), `sync_fit._read_events`,
  `led_sync_prototype.load_events`, `spans` docstring.
- Tests: `host/tests/test_session_serial.py` (resolver + reader helper),
  `host/tests/test_sync_fit.py` (bundle now writes `serial_<id>.csv`).
