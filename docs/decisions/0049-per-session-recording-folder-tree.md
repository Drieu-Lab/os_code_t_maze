# ADR 0049: Per-session recording folder tree + a filtered per-session serial capture (sensors + central port)

- **Status:** Accepted
- **Date:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_controller.py` (folder tree + serial capture + `recordings_root`), `host/session_serial.py` (new pure filter) + `host/test_session_serial.py`, `host/camera_view.py` (`start_recording` `out_dir`), `host/phase3_controller.py` (`arm` `out_dir`), `host/experiment_control.py` (passes `recordings_root`)
- **Supersedes / Superseded by:** — (refines ADR-0048)

## Context

ADR-0048 introduced the Session panel: one-click start/stop, a shared
`session_id`, and a JSON manifest as the correlation index. In that first cut the
files still landed in their old, scattered homes — the camera video under the
camera's Output folder, the Phase 3 file and manifest under `host/data/` — so a
session's outputs were tied together only by the manifest and by name, not
physically. The operator asked for the obvious next step: **one folder per
session**, created on Start, with the session's files sorted into subfolders, so
a session is a single directory you can copy/archive as a unit.

Two more decisions were needed to make that concrete:

1. **Where the session folders live.** The rig already writes behavior video to a
   fixed default recordings folder (`camera_view.DEFAULT_OUTPUT_DIR`,
   `C:\Users\Celine\Documents\t-maze_recordings`). Sessions should be a sibling of
   that, in one predictable place, regardless of what the camera's live "Output:"
   box happens to say.
2. **What serial data belongs in the session.** ADR-0048 left the *complete*
   verbatim serial stream to the separate `rig_logger.py` process (ADR-0002/0003),
   recorded in the manifest only as a "find it by time window" descriptor. The
   experimenter clarified that a session should also keep **its own** raw serial
   file, but only the **sensor + central-port** subset — the rat's body-beam
   position signal and the central reward well's activity — not the whole stream.

The Session panel is already a broker client that receives every serial line via
its `apply()` (it uses that to learn the animal id), so it can write that filtered
file itself, on the Tk thread, with no new process and no change to `rig_logger.py`
or the broker (so ADR-0002/0003 are untouched).

## Decision

**A per-session folder tree**, created on Start under a fixed recordings root, and
a **filtered per-session serial file** written by the Session panel.

- **Layout** — `<recordings_root>/<session_id>/`:

      session_<session_id>.json          the manifest (correlation index)
      video/   <session_id>.{mkv,mp4} + sidecar .csv (+ _background.png)
      task/    phase3_<session_id>.csv
      serial/  sensors_<session_id>.csv  (filtered raw serial)

  All three subfolders are created up front so every stream has a home even if a
  recorder never starts. The manifest moves from `host/data/` into the session
  folder root.
- **`recordings_root = <camera DEFAULT_OUTPUT_DIR>/phase3_recordings`**, wired in
  by `experiment_control.py`. Deriving it from the camera's *fixed default* (not
  the live Output box) means sessions always land in one place; the Output box is
  left untouched and still governs manual (non-session) Record-button captures.
- **Redirect seams instead of mutating shared state.** `camera_view.start_recording`
  and `phase3_controller.arm` each take an optional `out_dir` that overrides the
  destination for that call only. The Session panel passes `video/` and `task/`.
  Because nothing global is changed, a session recording and a manual recording
  can't stomp on each other's configured output.
- **Filtered per-session serial file.** While a session is active, the panel's
  `apply()` appends the SENSOR + CENTRAL-PORT lines to `serial/sensors_<id>.csv`,
  each stamped with the host wall clock in the exact column format `rig_logger.py`
  uses (`<wall_iso>,<ms>,<TYPE>,<payload...>`), so the two are trivially
  comparable. The subset is: every body-beam edge `S,<i>,<state>`; the central
  well's lick edges `L,<center>,...`; reward delivery `REWARD,...` (all reward is
  at the central port, ADR-0007); and the central solenoid ack `SOL,<center>,...`.
  Everything else stays with the full logger.
- **The subset rule is a pure function** (`session_serial.is_sensor_or_central_line`,
  protocol-only, headless-tested) — the repo's "pure core + thin shell" split, so
  "what counts as a session serial line" is readable and testable without a rig.
- **Manifest streams** gain `serial_sensors` (the path to this filtered file) and
  rename the ADR-0048 `serial_log` descriptor to `serial_full` (the complete
  `rig_logger.py` stream, still by-time-window). Camera/task/manifest paths now
  resolve inside the session folder.

## Alternatives considered

- **Capture the full serial stream per session.** Rejected per the experimenter's
  scope: the position + central-port signal is what a session needs inline; the
  exhaustive stream (cue echoes, handshakes, other-port acks, PONGs) stays with
  `rig_logger.py`, avoiding a second full copy.
- **Have `rig_logger.py` write into the session folder.** Rejected: it is a
  separate, GUI-independent process that names its own file (ADR-0002/0003) and
  has no `session_id`. Filtering inside the panel (which already sees every line)
  keeps that isolation.
- **Point `out_dir` at the session folder by mutating the camera's Output box /
  Phase 3's `DATA_DIR`.** Rejected: mutating shared UI/state is racy against manual
  use and needs a restore step. A per-call `out_dir` override is local and clean.
- **Base the root on the live Output box.** Rejected: the box is a user-editable
  scratch value; a fixed `phase3_recordings` root keeps sessions predictable.

## Consequences

- A session is now a single self-contained directory (manifest + video + task +
  filtered serial), easy to copy or archive as a unit.
- The session's inline serial file is a convenience subset, not the source of
  truth for the full stream — offline tools that need everything still read the
  `rig_logger.py` file (`serial_full` in the manifest).
- The filtered file is written on the Tk thread (sole writer, no locking); the
  captured line types are low-rate, so per-line flush is cheap and crash-safe.
- The center lick channel comes from the rig pin map (ADR-0022); if it can't be
  resolved, central *licks* are simply not captured (sensors/reward still are).
- `recordings_root` is injectable, so tests/other front-ends can redirect the tree
  without touching the camera default.

## Evidence / links

- Refines ADR-0048 (session orchestration + manifest + `mark_session_start` seam).
- Builds on ADR-0011 (camera sidecar), ADR-0047 (Phase 3 data file), ADR-0009
  (serial logger), ADR-0007 (reward at the central port), ADR-0022 (lick pin map),
  ADR-0002/0003 (transparent broker-only transport — unchanged here).
- Code: `host/session_serial.py` (+ `host/test_session_serial.py`), the folder
  tree + filtered serial capture + `recordings_root` in `host/session_controller.py`,
  the `out_dir` seams in `host/camera_view.py` and `host/phase3_controller.py`, and
  the `recordings_root` wiring in `host/experiment_control.py`.
