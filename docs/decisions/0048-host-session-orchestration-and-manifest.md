# ADR 0048: Host-only session orchestration + a shared session manifest with a single sync-fiducial seam

- **Status:** Accepted
- **Date:** 2026-07-23
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/session_log.py` (new pure core), `host/session_controller.py` (new "Session" tab), `host/camera_view.py` (recording seams), `host/phase3_controller.py` + `host/phase3_log.py` (arm/disarm seams + `session_id` in the header), `host/experiment_control.py` (tab wiring)
- **Supersedes / Superseded by:** —

## Context

A behavioral "session" today is spread across several files that start
independently and share no common id:

- the Phase 3 task record `host/data/phase3_<animal>_<ts>.csv` (seed, planned cue
  matrix, per-trial outcomes, `*_iso` + `lick_fw_ms`), written by
  `phase3_controller.py` via `phase3_log.py` (ADR-0047);
- the camera video `<name>.{mkv,mp4}` + its per-frame sidecar CSV
  (`host_unix_s`/`rig_ms`/`cam_block_id`/`cam_timestamp`), from `camera_view.py`
  (ADR-0011);
- the raw serial stream `host/data/<animal>_<ts>.csv`, written by the **separate**
  `rig_logger.py` process (ADR-0009).

Offline *alignment* already works: everything the host writes carries a host
wall clock, and the firmware `ms` clock maps to wall clock through the serial
log. What was missing is **coordination** (start the recorders together) and
**correlation** (know which files belong to one session). The operator had to
press several buttons in several tabs and later reconstruct, by eye and by
timestamp, which video went with which task file.

There is also a forward constraint. There is no neural-recording (Open Ephys)
system active yet, so the camera free-runs and the only cross-stream clock is the
host wall clock (tens-of-ms, plus serial latency). Once neural recording is
integrated the camera will be hardware-triggered or emit a per-frame TTL, and a
firmware `SYNC` pulse (Open Ephys TTL + a camera-visible LED barcode) will give
sub-ms, acquisition-grade alignment. Whatever we add now must be a *small*, not a
*throwaway*, step toward that.

Two standing constraints shaped the design: the broker is a transparent line
passthrough that does **not** echo a client's line back to the other clients
(ADR-0002), and GUI clients are broker-only (ADR-0003). So the GUI cannot inject
a marker directly into the serial log that `rig_logger.py` sees — a host marker
can only reach that log by looping through the firmware (which re-emits it).

## Decision

Add a **host-only** session orchestrator — a thin "Session" tab — that owns
coordination and correlation but no task logic, plus a shared `session_id` and a
JSON **manifest** that is the correlation index. No firmware change, no reflash.

- **`session_id = <animal>_<stamp>`**, minted once at Start and passed to the
  writers the host owns (camera + Phase 3) so their filenames and headers share
  it. The serial log (a separate process the host can't name; ADR-0002/0003) is
  correlated via the manifest — its directory + animal + "match by enclosing
  wall-clock time window" — not restructured.
- **`t0` captured at Start as `{wall, mono, iso}`** (`session_log.clock_snapshot`).
  Capturing the wall and monotonic clocks at the same instant is what lets the
  wall-clocked disk logs and the monotonic-clocked live vision/rules streams
  (ADR-0041) be placed on one timeline; `iso` is the human-readable form.
- **A single fiducial seam, `mark_session_start(t0)`.** Today it records the
  host-t0 anchor in the manifest and best-effort sends `PING` so a `PONG` lands
  in the serial log as a coarse shared marker (respecting ADR-0002 — it loops
  through the firmware). This is the *one* place a future firmware `SYNC` (TTL +
  LED barcode) attaches for sub-ms sync; nothing else changes when that lands.
- **The camera lifecycle is owned by the session**: Start begins recording if it
  isn't already, Stop ends what the session started. Phase 3 is *armed* (its
  existing Free-Roam/tracking/zone gating still applies; the task starts only
  once the gate opens), and its data file is named from the `session_id`.
- **The manifest `host/data/session_<id>.json`** holds `schema_version`,
  `session_id`, `animal`, `t0`/`t_end` (`{wall,mono,iso}`), a `streams` map
  (`phase3_task`, `camera_video`/`camera_sidecar`/`camera_codec`, `serial_log`
  descriptor), and a `fiducial` note (`method: host-t0`, `pending: firmware SYNC
  TTL + LED barcode`). It is written atomically (temp + `os.replace`) at Start
  and updated as each stream's path resolves and again on Stop — so a crash or a
  mid-session close still leaves a valid manifest.

Split of responsibilities (the repo's "pure core + thin shell" pattern):
`session_log.py` is a pure, headless-tested core (builds + renders the manifest,
no I/O); `session_controller.py` is the thin Tk shell that mints the id, drives
the recorders through their public seams, and writes the file.

## Alternatives considered

- **One-process "record everything" that also owns the serial log.** Rejected:
  it would fold the independent `rig_logger.py` under the GUI and push on
  ADR-0002/0003 (the logger runs headless and survives a GUI crash). Correlating
  the serial log via the manifest keeps that isolation while still tying it in.
- **Inject a host marker line directly into the serial log.** Impossible without
  breaking ADR-0002 (the broker won't echo client lines to other clients); the
  only compliant host-side marker is `PING`→`PONG` looped through the firmware,
  which is exactly the coarse anchor the seam sends today.
- **Rename all files to a single stamp, including the serial log.** Rejected for
  now (the serial log names itself in another process); deferred as a future item.
- **Do nothing / keep manual multi-button start.** Rejected: it is error-prone
  and leaves session membership implicit, exactly the correlation gap this closes.
- **Wait for the hardware `SYNC`/TTL + LED barcode and build the real fiducial
  now.** Rejected as premature (no Open Ephys yet); instead we leave a single,
  well-marked seam so the hardware path is a small drop-in (see FW-0018).

## Consequences

- One action starts a correlated session; the manifest makes "which files are
  this session?" explicit and machine-readable, and the shared `session_id`
  appears in the camera sidecar and Phase 3 headers.
- The host-t0 anchor is only as good as the host wall clock (tens of ms, plus
  serial latency) — deliberately coarse. Sub-ms alignment waits on the hardware
  fiducial, which attaches at the same seam with no other code change.
- The serial log stays a separate process; correlation is by time window, so a
  wildly wrong host clock would weaken it (acceptable — NTP-disciplined PC).
- Per-tab track/state/rule logs are out of scope for this first cut (they are
  per-tab toggles); they can be added to the `streams` map later.

## Evidence / links

- Builds on ADR-0011 (camera free-run + sidecar), ADR-0047 (Phase 3 session data
  file), ADR-0009 (serial logger), ADR-0041 (monotonic-clocked shared pipeline),
  ADR-0002/0003 (transparent broker-only transport).
- Deferred hardware upgrade: `docs/future-work.md` FW-0018 (firmware `SYNC` TTL +
  camera-visible LED barcode).
- Code: `host/session_log.py` (+ `host/test_session_log.py`),
  `host/session_controller.py`, the `start_recording`/`stop_recording`/
  `current_recording_path`/`is_recording` seams in `host/camera_view.py`, the
  `arm`/`disarm`/`current_log_path` seams + `session_id` header in
  `host/phase3_controller.py` / `host/phase3_log.py`, and the tab wiring in
  `host/experiment_control.py`.
