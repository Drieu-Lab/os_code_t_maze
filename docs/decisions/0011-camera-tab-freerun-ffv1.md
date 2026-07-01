# ADR 0011: Add the Basler camera as an in-GUI tab (free-run, FFV1, decoupled grab/encode)

- **Status:** Accepted
- **Date:** 2026-06-24
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/camera_view.py`, `host/experiment_control.py`, `host/requirements.txt`
- **Supersedes / Superseded by:** —

## Context

The rig has a Basler `acA1300-60gc` GigE camera (color, 1.3 MP, <=60 fps) on a
dedicated, already-tuned NIC (`192.168.3.3`, jumbo frames, isolated subnet). We
want behavioral video integrated with the existing control GUI. Constraints and
facts that shaped the choice:

- `experiment_control.py` is a single-threaded Tk app; modules implement
  `widget()` + `apply(line)`. Blocking it freezes the whole GUI.
- Camera acquisition (steady) and lossless encoding/disk writes (bursty) run at
  different, variable rates. Doing them inline risks stalling acquisition (the
  camera's buffers overflow -> source-side frame loss) or freezing Tk.
- A single GigE link is ~117-125 MB/s. Debayered color at full res/60 fps
  (~236 MB/s) exceeds it; this first version targets a frame rate/ROI that fits.
- The camera input is hardware-triggerable, but that needs firmware + a drive
  circuit (deferred). A host-only free-run recorder needs none of that and is
  useful immediately.
- Alignment to behavior must be possible without a host behavior log (the broker
  is a transparent passthrough and writes nothing to disk; see ADR-0002).

## Decision

Add `CameraView` as a Camera **tab** in `experiment_control.py` (same module
contract, plus an optional `close()` hook the app now calls on exit). The module:

- runs the camera **free-running** and opens it by a **stable id** (serial / IP
  `192.168.3.3` / MAC), not "first device";
- uses a **background grab thread** + a **background encode thread** joined by a
  **bounded queue**, so encode/disk back-pressure becomes a controlled, counted
  frame drop rather than an acquisition stall or GUI freeze; Tk only previews
  (throttled) and drives controls;
- records **mathematically lossless FFV1** to `.mkv` via OpenCV `VideoWriter`
  (debayered color), with camera settings loaded from a Pylon **`.pfs`** profile;
- writes a per-frame **sidecar CSV** (`frame_idx, host_unix_s, rig_ms,
  cam_block_id, cam_timestamp`) - `rig_ms` (latest serial `<ms>` seen in
  `apply()`) is the bridge to behavior; `cam_block_id`/`cam_timestamp` are
  written now (future-proofed) for drop detection and precise timing later;
- imports all heavy deps (pypylon/OpenCV/numpy/Pillow) **defensively** and shows
  a message instead of crashing the GUI when a dep or the camera is missing/busy.

## Alternatives considered

- **Separate standalone capture process** - more robust isolation, but a heavier
  UX (extra window/process, cross-process control) than warranted for a first
  integration; the tab fits the existing module pattern. Can revisit if in-GUI
  capture proves too heavy.
- **Inline grab+encode (no queue)** - simplest, but a disk hiccup stalls the
  grabber and drops frames at the source, and encoding on the Tk thread freezes
  the GUI. Rejected for reliability.
- **Compressed MP4/H.264** - far smaller, but lossy; rejected for a behavioral
  record. FFV1 is lossless; raw-Bayer (smaller, sensor-lossless) is a later
  option for full frame rate over the GigE bandwidth ceiling.
- **Hardware-triggered acquisition now** - the gold standard for ephys
  alignment, but needs a free Due pin/timer, new firmware, and a 3.3 V -> opto
  drive circuit. Deferred to a later build-up; the sidecar schema and shared
  Open-Ephys TTL plan already accommodate it.

## Consequences

- A usable, lossless recorder ships with no hardware/firmware changes; the GUI
  stays responsive and never crashes if camera deps are absent.
- Frame-to-behavior alignment in free-run is only as precise as the serial
  update rate + host scheduling (tens of ms); sub-ms alignment awaits the
  hardware trigger + shared Open Ephys TTL.
- Full-res color at 60 fps won't fit one GigE link; users pick an fps/ROI that
  does, or (later) record raw Bayer and debayer offline.
- `pypylon` is pip-only (not on conda) and must be installed separately;
  `requirements.txt` notes this.

## Evidence / links

- Camera I/O / trigger facts: Basler `acA1300-60gc` product docs (opto-coupled
  Line 1, logical 1 >2.2 V at 5-15 mA).
- NIC/optimization: Pylon GigE Configurator log (9014 jumbo, RSS, etc.), 2026-06-24.
- Free Due timer headroom for a future frame clock: `firmware/cue-guided-task/sound.h`
  (DAC ISR uses TC0 ch0; 8 channels free).
- Related: ADR-0001 (broker), ADR-0002 (transparent passthrough).
