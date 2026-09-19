# ADR 0184: Survive a mid-session firmware reset (host side)

- **Status:** Accepted
- **Date:** 2026-09-19
- **Author / Agent:** Agent (Cursor) on behalf of RJ
- **Affects:** `host/tmaze/core/protocol.py` (`is_boot_handshake`),
  `host/tmaze/ui/phase3_controller.py` (detect + re-assert),
  `host/tmaze/core/session/session_notes.py` (`AUTO_FIRMWARE_RESET`),
  `host/tmaze/ui/session_controller.py` (manifest recording),
  `host/tmaze/core/session/session_log.py` (`firmware_resets`, manifest
  `schema_version` 3 -> 4); tests `test_protocol`, `test_session_log`,
  `test_session_phase3_wiring`; golden fixture re-issued + re-copied to the
  analysis mirror
- **Supersedes / Superseded by:** —

## Context

The Arduino Due is on the USB **Programming** port. A mid-session reboot — a power
glitch, a watchdog, a stray reset, a knocked cable — is a real bench hazard, and
it is silent: the broker's serial connection stays open (the port did not close,
so the reopen path in ADR-0065/0120 never fires), but the firmware re-runs
`setup()` and comes up in its default state.

That default state is the problem for Phase 3. While the host drives the
positional cue it owns the DAC via `CUE,VISION` (ADR-0044/0045); a reboot clears
`cueSourceVision` back to firmware-driven **beam** tones. So after an unnoticed
reset the firmware would start sounding its own beam-triggered tones underneath
the host — exactly the double-drive the `CUE,VISION` handshake exists to prevent —
and the operator would have no record that the board restarted (its session clock
`millis() - startMs` also silently restarts at 0, so the firmware `*_fw_ms` twins
of ADR-0165/0166 would jump backwards with no marker).

There was no host-side detection of this at all. The reopen path (ADR-0120)
handles a *tracking* flicker, not a firmware reboot.

## Decision

Detect a mid-session reboot from the firmware's own startup marker and treat it as
a first-class, logged event that the run **survives**.

- **Pure detector.** New `protocol.is_boot_handshake(line)` returns True for a
  `PROTO` line. `0,PROTO,<n>` is printed once by `setup()` at every boot and
  nowhere else, so it is the unambiguous reboot signal. A `MODE` switch also
  restarts the firmware session clock to ~0 but does **not** re-emit `PROTO`, so
  keying on `PROTO` avoids a false positive on an operator mode change.

- **Detection (serial-reader thread).** `phase3_controller.apply()` tracks the
  last firmware ms seen on any line (`_last_fw_ms`). When it sees a boot handshake
  **and** the clock was already running (`_last_fw_ms > 0`), it hands a
  `("firmware_reset", last_fw_ms, iso)` item to the actuation thread and resets
  `_last_fw_ms` to 0. The `> 0` guard means the *initial* startup handshake (clock
  still 0) is never mistaken for a reset.

- **Recovery (actuation thread).** `_process_firmware_reset` re-asserts host DAC
  ownership when we were driving cues (gate open): `_silence_and_rearm()` +
  `CUE,VISION`, the same reclaim the reopen path does, **minus** the sequence
  rebuild — the task, trial index, and data file are untouched, so the run
  continues. If the gate was closed (firmware autonomy already in effect) nothing
  is re-asserted; the reset is still logged.

- **Always logged, two places.** An `AUTO_FIRMWARE_RESET` / `auto:firmware_reset`
  timeline note (carrying the host receipt time + last ms), a per-run status-line
  counter (`fw_reset:N`), **and** a manifest `firmware_resets: [{host_iso,
  last_fw_ms}]` entry so an offline reader can split the firmware ms timeline at
  the reboot (its clock restarts at 0). The manifest `schema_version` bumps 3 -> 4
  for the additive field; `_emit_intervention` gained an optional `extra` dict so
  the reset event can carry `last_fw_ms` to the session controller's drain.

## Alternatives considered

- **Key detection on a clock DROP (ms goes backwards), not `PROTO`.** Rejected: a
  `MODE` switch also drops the clock to 0 without a reboot, so this false-positives
  on a routine operator mode change; `PROTO` is emitted only at boot.
- **Treat it like the reopen path (rebuild the sequence).** Rejected: a reboot is
  not a new run — the trial index and data file are valid and must be preserved.
  Only DAC authority and stale per-trip cue/gate/well state need clearing.
- **Note only, no manifest entry.** Rejected: the reboot resets the firmware ms
  clock, so an analysis that reads `*_fw_ms` needs a machine-readable split point,
  not just prose.
- **No status-line counter.** Rejected: a survived reset should be visible live so
  a flaky cable is noticed on the bench, not only in post-hoc analysis.
- **Do nothing until the firmware carries a boot-count / VERSION.** Rejected: the
  hazard is live now; `PROTO` already exists. A firmware boot counter is a nice
  future addition (a reflash item) but not required for host-side survival.

## Consequences

- A clean session (no reboot) is byte-for-byte unchanged: `firmware_resets` is an
  empty list, no note, no status segment.
- The detector depends on the reboot emitting `PROTO`. On the Programming port with
  the host already attached, `setup()` proceeds immediately and prints it, so this
  holds. A truly idle rig (only startup lines seen, clock still 0 at the instant of
  reboot) would not trip the `> 0` guard — acceptable, since there is no active cue
  drive to protect and STATUS traffic advances the clock during a real session.
- No `format_version` bump: `firmware_resets` is manifest metadata
  (`schema_version` territory, ADR-0072/0180), not the on-disk task-data contract.
  The analysis mirror does not guard the manifest `schema_version` (only
  `format_version` + the summary), so it reads v3 and v4 alike.
- The firmware half — a boot counter / `VERSION` string and non-blocking prints —
  is deferred to the next reflash (Increments 5b/7e).

## Evidence / links

- Review finding **S8** (mid-session firmware reset unhandled on the host).
- `protocol.is_boot_handshake` + `test_protocol.test_is_boot_handshake`.
- `test_session_phase3_wiring.test_firmware_reboot_is_detected_and_reasserts_cue_vision`
  (ms-drop → one re-assert, one note) and `…_lands_in_the_manifest`.
- `test_session_log.test_firmware_resets_round_trip`.
- Builds on ADR-0044/0045 (host DAC ownership), ADR-0120/0065 (reopen vs reset),
  ADR-0165/0166 (firmware ms twins), ADR-0111 (intervention sink), ADR-0180
  (manifest schema v3 / provenance).
