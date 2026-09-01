# ADR 0147: Fix the camera source-drop double-count, size the GigE transport, and record acquisition provenance

- **Status:** Accepted
- **Date:** 2026-09-01
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/camera_health.py` (new), `host/tmaze/ui/camera_view.py` (`_grab_loop`, `_configure_transport`, `_refresh_transport_stats`, `_Counters`, `_RecParams`, `_update_status`, `_status_tooltip_text`, `_write_camera_stats`, recording header), `host/tmaze/ui/tooltips.py` (`add_dynamic_tooltip`), `host/tmaze/ui/tooltip_text.py` (`camera.status`), `host/tests/test_camera_health.py` (new); recording sidecar header + a new companion `<id>_camera_stats.json`
- **Supersedes / Superseded by:** Extends ADR-0011 (free-run + grab/encode split), ADR-0012 (PyAV FFV1), ADR-0040 (tracking off the grab thread), ADR-0124 (self-anchoring bundle clocks); does not supersede any.

## Context

Investigating bug `#260810_CameraSrcDrops` (the camera's `src-drop` / incomplete
counter climbs steadily over a session). Two distinct problems, on a **Basler
acA1300-60gc GigE** camera:

1. **The `src-drop` counter double-counts incomplete frames.** In `_grab_loop`,
   an incomplete grab (`GrabSucceeded() == False`) did `incomplete += 1; continue`
   *before* reading the camera `BlockID`, so `last_block` was not advanced. The
   next good frame then showed a `BlockID` gap and was scored as a source drop.
   Every incomplete frame therefore manufactured exactly one phantom source drop,
   so `src-drop` mirrored `incomplete` one-for-one (94 == 94 in a captured status
   line). As instrumented there was really **one** phenomenon (incomplete GigE
   frames), counted twice — the health readout overstated severity ~2x and its
   comment ("frames the camera produced but we never received") misattributed the
   cause.

2. **The app was almost certainly not using the rig's jumbo NIC.** No `.pfs` is
   bundled or loaded by default (`self._pfs_var = "(camera defaults)"`), so the
   camera streamed at its power-up `GevSCPSPacketSize` (factory ~1500 B) even
   though the operator set the NIC to MTU 9014 months ago (which fixed severe
   drops **in pylon Viewer** — Viewer auto-negotiates the packet size; our app
   did not). At ~1500 B a 1.3 MP color frame is ~6x more packets than at ~8 KB,
   each an extra chance to drop a packet and mark the whole frame incomplete —
   consistent with the steady climb. No `MaxNumBuffer` was set either, so a brief
   encode/track CPU spike (ADR-0012/0040) could starve the receive path.

An incomplete frame is dropped before enqueue, so it is absent from the video +
sidecar; but the offline timeline keys on the monotonic `cam_block_id` +
`host_unix_s` (ADR-0124), so a gap stays explicit and cross-stream sync is
intact. At ~0.19% (94/50480) the data harm was minor; the misleading readout and
the un-tuned transport were the real issues.

## Decision

- **Pure accounting core.** New stdlib-only `tmaze.core.camera_health` with
  `account_block_gap(last_block, block_id) -> (new_last_block, src_drop_delta)`,
  headless-tested, so the counting rule leaves the Tk/pypylon shell (pure core +
  thin shell). `_grab_loop` now folds the `BlockID` of **every delivered buffer**
  (complete or incomplete) through it, so an incomplete grab advances `last_block`
  and no longer fabricates a source drop. `src-drop` now means *good-frame
  `BlockID` gaps not explained by a delivered incomplete buffer* = frames the
  host never received at all; `incomplete` counts partial/corrupt grabs
  separately.

- **Transport tuning at open (guarded), replicating Viewer's negotiation.**
  Before `StartGrabbing`, `_configure_transport` sets `MaxNumBuffer` =
  `GRAB_MAX_NUM_BUFFER` (32) and sets `GevSCPSPacketSize` to
  `min(TARGET_PACKET_SIZE=8192, node.GetMax())` — read the node maximum and clamp,
  so it can never exceed what the link supports (the NIC MTU is 9014). Optional
  `GevSCPD` inter-packet delay (`INTER_PACKET_DELAY`, default 0). Every write is
  wrapped so a non-GigE / read-only node can never break camera open.

- **Surface the driver's own transport stats (ground truth).**
  `_refresh_transport_stats` reads the stream-grabber statistics (~1/s, guarded):
  `Statistic_Failed_Buffer_Count`, `Statistic_Buffer_Underrun_Count`,
  `Statistic_Failed_Packet_Count`, `Statistic_Resend_Request_Count`,
  `Statistic_Total_Packet_Count`. These distinguish PC/buffer pressure
  (underruns) from link packet loss (resends). The status line grows only a tiny
  inline token `net: <resends>r/<underruns>u`; the full breakdown lives in the
  status hover tooltip (new `tooltips.add_dynamic_tooltip` for live text), because
  the status bar sits in the cramped Rig-view tab.

- **Bundle provenance.** The applied settings (negotiated packet size + node max,
  buffer count, inter-packet delay) are added to the recording sidecar CSV header
  at Start (additive keys, backward-compatible for readers; ADR-0124/0142). At
  recording close a small companion `<id>_camera_stats.json` records the final
  counters (`saved`/`src_drops`/`incomplete`/`queue_drops`) plus the transport
  stats, so each session bundle carries a self-contained record of *what was
  configured* and *what the link actually did*.

## Alternatives considered

- **Leave the metric, just tune the transport.** Rejected: the ~2x inflated,
  mis-labelled counter is itself a diagnosis hazard (it hid that the drops were
  all incomplete grabs); the fix is one small, tested pure function.
- **Hardcode a fixed jumbo packet size (9000).** Rejected as the default in
  favour of read-node-max-and-clamp, which adapts to the camera model's limit and
  can never exceed the link — same benefit, no way to silently break a stream if
  the environment changes. (The NIC being at 9014 makes even the fixed value safe,
  but the clamped form is strictly safer.)
- **Full transport stats inline on the status line.** Rejected: the Rig-view line
  is already dense and vertical space is tight; a tiny token + hover detail keeps
  the glanceable line clean while preserving the full numbers.
- **Write end-of-recording stats as a CSV footer row.** Rejected: a footer would
  confuse the sidecar's per-frame parsers; a companion JSON keeps the CSV clean.
- **Blank the track/video sidecar for incomplete frames' positions.** N/A here —
  incomplete frames are dropped before enqueue; the `BlockID` gap already makes
  the absence explicit offline.

## Consequences

- `src-drop` and `incomplete` are now independent and honestly labelled; a healthy
  session reads ~0 `src-drop`. The transport stats tell the operator whether a
  climb is link loss (resends) or PC/buffer pressure (underruns), pointing at the
  right lever.
- The packet-size change is expected to be the dominant reduction in incomplete
  frames (it brings the app in line with what Viewer negotiated); the new
  transport-stat readout is how we confirm this on-rig. The bug stays open pending
  that on-rig confirmation.
- One guarded pylon read per second on the grab thread (negligible) and a small
  JSON write per recording. All new camera access is defensive, so a non-GigE
  camera or missing statistics nodes degrade to `n/a`, never an error.
- If resends persist after jumbo, raise `INTER_PACKET_DELAY` (and, host-side, the
  NIC receive-buffer count); if underruns persist, raise `GRAB_MAX_NUM_BUFFER` or
  cap encode CPU (FW-0013).

## Evidence / links

- Code: `host/tmaze/core/camera_health.py`, `host/tmaze/ui/camera_view.py`
  (`_grab_loop`, `_configure_transport`, `_refresh_transport_stats`,
  `_write_camera_stats`, `_net_token`, `_status_tooltip_text`),
  `host/tmaze/ui/tooltips.py` (`add_dynamic_tooltip`),
  `host/tmaze/ui/tooltip_text.py` (`camera.status`).
- Tests: `host/tests/test_camera_health.py` (first-frame seed, consecutive,
  single/multi-frame gap, missing BlockID, duplicate/out-of-order no-negative,
  the incomplete-grab no-double-count walk, and a true drop around an incomplete
  grab). Full suite green (`python -m unittest discover -s tests -t .`, 928 tests).
- Symptom: captured status line `... saved: 50480  src-drop: 94  incomplete: 94 ...`
  (src-drop == incomplete). NIC jumbo frames at MTU 9014 (operator, set months
  earlier, fixed a severe drop issue in pylon Viewer).
- Bug: `docs/notes/Bugs.md` `#260810_CameraSrcDrops`. Related: ADR-0011, 0012,
  0040, 0124; FW-0013.
