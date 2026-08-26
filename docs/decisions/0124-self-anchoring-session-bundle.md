# ADR 0124: Make a session bundle self-anchoring on one canonical instant

- **Status:** Accepted
- **Date:** 2026-08-26
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/session/session_serial.py`, `host/tmaze/core/phase3/phase3_log.py`, `host/tmaze/ui/phase3_controller.py`, `host/tmaze/ui/camera_view.py`, `host/tmaze/ui/session_controller.py`, `host/tmaze/analysis/bundle_reader.py`, plus tests
- **Supersedes / Superseded by:** — (builds on ADR-0048/0060/0062; teed up for FW-0018)

## Context

A Phase 3 session is spread across independently-written files: the Phase 3
task CSV, the per-session serial (sensor) capture, the camera video + its
per-frame sidecar CSV, the notes log, and the session manifest
(`session_<id>.json`). Neural recording (Open Ephys) is about to be layered on,
and the one thing that can't be recovered after the fact is a mis-aligned
timeline. So a session bundle needs to be *self-anchoring*: each stream file must
carry the same session "zero" so an offline tool can put every event on one
timeline without guessing.

Today it isn't. Three different "start" instants are written, on three
occasions, and two offline readers disagree about which is the session start:

- the manifest `t0` (a host wall+monotonic+ISO triple) is captured at the
  **Start Session** button;
- the Phase 3 CSV header's `session_start_iso` (ISO only) is captured at the
  **gate rising edge** (arming), which by the ADR-0060 workflow is *before*
  Start Session;
- the camera sidecar header's `start_wall_clock` (only *second* resolution) is
  captured at **recording start**.

`bundle_reader.py` anchors analysis to the Phase 3 header's `session_start_iso`
(gate arm), while `session_registry.py` anchors to the summary's `t0.iso`
(Start Session). The rebind step (`_rebind_session_output`) re-points the Phase 3
file at the owning session but stamps no shared time into it. Separately, the
per-session serial capture deliberately *drops* the `PONG` line
(`session_serial.py`), so the sensor stream inside a bundle has no explicit
session-start marker and no in-stream firmware-ms↔host-wall tie point. And the
sidecar's `rig_ms` column is "the latest serial ms seen at frame-grab time" - it
lags and *jumps backwards* on a firmware MODE switch (the firmware session clock
resets), yet it looks like a usable clock.

## Decision

Adopt **one canonical session anchor - the manifest `t0`** (host wall+mono+ISO at
Start Session) - and make every stream self-anchoring to it. Specifically:

1. **Stamp `t0` into each stream's header.** The Phase 3 CSV header gains
   `session_t0_iso` / `session_t0_wall` / `session_t0_mono` (written at rebind,
   when the session that owns the file becomes known); the camera sidecar header
   gains `session_t0_iso` / `session_t0_wall` at millisecond resolution. The
   existing gate-arm `session_start_iso` and the camera record-start stay, but as
   clearly-labeled *secondary* events - not the zero.

2. **Reconcile the offline readers to `t0`.** `bundle_reader` prefers
   `session_t0_iso` from the Phase 3 header when present (falling back to
   `session_start_iso` for pre-ADR-0124 bundles), so it and the registry finally
   agree on one session start, and it also exposes the gate-arm time as a
   distinct field rather than conflating the two.

3. **Keep the `PONG` session-start fiducial in the per-session serial capture.**
   The host already sends one `PING` at Start Session (`_mark_session_start`);
   keeping the firmware's `PONG` reply in the per-session serial gives that
   stream an explicit, labeled session-start line carrying both the firmware ms
   (its own prefix) and the host wall time (the writer's per-line prefix) - a
   clean tie point for converting firmware-clock events onto the host timeline
   inside the bundle.

4. **Make `rig_ms` truthful.** Document (in the sidecar header and in code) that
   `rig_ms` is "the latest firmware session ms seen at grab time; it resets on a
   MODE switch and is NOT a monotonic clock", and clear the stale value on a MODE
   switch so a frame after a reset doesn't carry a pre-reset ms. Frame->time stays
   recoverable via the per-frame `cam_block_id` (BlockID gaps = source drops).

This is deliberately a **software-only** change. The acquisition-grade hardware
fiducial (a firmware `SYNC` TTL + a camera-visible LED barcode that Open Ephys,
the camera, and the serial log all see at one instant) remains **FW-0018** - it
needs the neural hardware and is the next phase. The header `t0` and the in-FOV
event LEDs (sensor/solenoid-triggered, visible in-frame) are separate future
avenues captured in future-work.

## Alternatives considered

- **Anchor everything to the gate-arm `session_start_iso` instead of `t0`.**
  Rejected: the gate arm is a task-subsystem event that can happen before the
  operator commits to the session (and can re-fire on a reopen); the manifest
  `t0` is the single, unambiguous "this session started" instant already used by
  the registry/summary. One canonical anchor should be the session's, not a
  subsystem's.
- **Rely on a single start fiducial for the whole session.** Insufficient by
  itself for long sessions: independent oscillators drift (tens of ppm ->
  ~100-300 ms over an hour). We do NOT try to fix drift here; instead we note
  that the per-line host-wall stamps already let an offline tool fit
  `firmware_ms -> host_time` per mode-segment (drift-corrected), and the fiducial
  just supplies a clean, labeled zero.
- **Add the firmware `SYNC` command / LED barcode now.** Deferred to FW-0018:
  it requires the Open Ephys TTL + an LED in the FOV and belongs with the neural
  integration, not this software groundwork.

## Consequences

A bundle becomes self-anchoring: any single stream file states the session's
canonical zero, and the sensor stream carries an in-stream fiducial tying the
firmware clock to host wall time. Offline tools converge on one start instant
(`bundle_reader` and the registry agree). Residual **drift** is correctable from
the existing per-line stamps (per mode-segment linear fit); residual **absolute
sub-ms latencies** (exposure->host, reward command->delivery) still need the
FW-0018 hardware fiducial. One visible behavior change: `bundle_reader`'s session
`start_iso` now reflects `t0` (Start Session) rather than the gate arm when the
newer header is present - more correct, and pre-0124 bundles fall back to the old
field. The per-session serial file now contains a `PONG` line at the top (one
extra, clearly-typed line); its "sensor + central-port" scope is broadened only
by that intentional fiducial.

## Evidence / links

- `host/tmaze/core/session/session_serial.py` - keep `PONG`.
- `host/tmaze/core/phase3/phase3_log.py`, `host/tmaze/ui/phase3_controller.py` -
  `session_t0_*` header fields stamped at rebind.
- `host/tmaze/ui/camera_view.py`, `host/tmaze/ui/session_controller.py` -
  sidecar `session_t0_*` header + `rig_ms` truthfulness.
- `host/tmaze/analysis/bundle_reader.py` - prefer `session_t0_iso`.
- Builds on ADR-0048 (session anchor), ADR-0060 (arm-before-start workflow),
  ADR-0062 (STATUS liveness keeps PONG as the session-start fiducial). Hardware
  fiducial: docs/future-work.md FW-0018.
