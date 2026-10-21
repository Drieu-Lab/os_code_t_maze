# ADR 0224: Preview DeepLabCut-Live head tracking from a helper process, on a crop around the rat

- **Status:** Accepted
- **Date:** 2026-10-20
- **Author / Agent:** Agent (Cursor) on behalf of Celine
- **Affects:** `host/tmaze/core/dlc_head.py`, `host/tmaze/core/dlc_head_protocol.py`,
  `host/tmaze/io/dlc_head_client.py`, `host/tmaze/tools/dlc_head_server.py`,
  `host/tmaze/tools/export_dlc_live.py`, `host/dlc_head.json`,
  `host/tmaze/vision/tracker.py` (`set_dlc_head`, `draw_dlc_head`),
  `host/tmaze/ui/camera_view.py` (track worker + recording),
  `host/tmaze/ui/tracker_view.py` (Tracking tab), `.gitignore`
- **Supersedes / Superseded by:** — (corrects the DLC-Live evidence in ADR-0219;
  see "Correction to ADR-0219" below)

## Context

The task's position is the tracker's centre of mass (CoM), which sits on the
body, not the head. ADR-0219 added a small learned network for the head, but
the lab's goal is [DeepLabCut-Live](../glossary.md#deeplabcut--deeplabcut-live)
(DLC-Live), and the next step is to trigger the cue from DLC's `head_center`
instead of the CoM. Before that, the lab wants to watch DLC-Live run live in
a real session, recorded, without it touching the task.

ADR-0219 had rejected DLC-Live as too slow (70 ms per full frame on the GPU)
and too inaccurate on smaller inputs. Re-testing found that most of the
inaccuracy was ours: DLC-Live 1.1.0 always swaps a 3-channel frame from
OpenCV's BGR colour order to RGB itself (`dlclive.py` overrides its own
`convert2rgb` switch), and the benchmark had already converted to RGB, so the
model saw swapped colours. With BGR frames DLC-Live matches DeepLabCut's own
runner to 0.01 px.

Two more facts shaped the design:

- `dlclive` 1.1.0 needs Python < 3.13 and numpy < 2. The rig env
  `drieu_t_maze` is Python 3.13 / numpy 2.4, so it cannot be installed there.
  The existing `dlclivegui` env (Python 3.12, numpy 1.26, CUDA torch 2.12,
  dlclive 1.1.0) already runs it.
- A fixed 448 x 448 px crop around the CoM keeps the full model's head point
  within ~1 px of the full-frame answer and cuts the time to ~17 ms
  (corrected speeds below).

## Decision

**Preview only.** DLC-Live adds a drawn head point and its own file. The CoM
stays the task's position and nothing in the task reads the DLC output.

**A [helper process](../glossary.md#helper-process) in the `dlclivegui` env.**
`tmaze.tools.dlc_head_server` loads the exported model on the GPU and serves one
rig connection on `127.0.0.1:5560`. The Tracking tab's "Track head (DLC-Live)"
box starts it through `tmaze.io.dlc_head_client.DlcHeadClient`, with its
settings in `host/dlc_head.json`. The client passes the env's DLL folders on
`PATH`, `PYTHONPATH` = this checkout's `host/`, and keeps the helper off
logical processors 10/11 (the parity-error core). Startup takes ~8 s and is
allowed up to 120 s.

**Messages** (`tmaze.core.dlc_head_protocol`): a 4-byte tag + a length, then
HELO (JSON: body parts, model file, SHA-256, dlclive/torch versions, device),
CROP (frame id, crop corner, size, raw BGR pixels), POSE (frame id, status,
model ms, every part's x/y/likelihood in full-frame px) or BYE_. The crop is
sent in BGR and handed to DLC-Live unchanged.

**Per frame**, on the camera's track worker, right after `tracker.process`:
the crop is 448 px around the tracker's raw detection (`TrackResult.centroid`),
shifted (never shrunk) to stay inside the frame. Only one crop is in flight.
A frame that arrives while the helper is busy is recorded as `skipped`, and a
frame with no detection as `no_blob`. `submit` never waits: it costs the track
worker ~0.1 ms. The helper keeps the most confident animal of DLC's
multi-animal output.

**Model:** the iteration-1 full model (shuffle 1, `snapshot-best-140`, 10 body
parts), exported by `tmaze.tools.export_dlc_live` to
`host/head_models/dlc/iteration1_shuffle1_snapshot-best-140_dlclive.pt`
(118 MB, git-ignored, rebuilt from the DLC project). It refuses a config whose
skeleton edges were pruned.

**Status per frame:** `ok` (head-centre [likelihood](../glossary.md#likelihood-pcutoff)
>= pcutoff 0.6), `low`, `error` (the helper failed on that crop), `skipped`,
`no_blob`.

**Recording:** a separate `video/<session>_dlc_head.csv`, opened and closed
with the video. It has `# key: value` header lines (model file + SHA-256,
snapshot, versions, device, crop size, pcutoff), then `cam_block_id,
dlc_status, dlc_latency_ms, crop_x0, crop_y0` and x/y/likelihood per body part.
Join it to the video sidecar on `cam_block_id`.
- Rows are not in frame order: an answer arrives after the next frame's
  `skipped` row.
- The crop that is in flight when recording stops has no row.

**Speed settings:**
- `cpu_threads: 2` caps torch's and OpenCV's threads in the helper. The
  library default competes with the tracker and was slower.
- The helper opts itself out of Windows 11 power throttling ("EcoQoS",
  `SetProcessInformation`). At the camera's 25 fps a windowless process that
  is idle ~half the time was being slowed: 48 ms per crop instead of ~20 ms.

**Failure handling:** a helper that won't start, exits, disconnects or leaves
a crop unanswered for 2 s turns the client to `error`. The tab unticks itself
and shows why (with the helper log's last lines from
`%TEMP%\tmaze_dlc_head_server.log`), and tracking carries on. The box is locked
while recording, like the learned-head box (ADR-0220), so a file covers one
setting. The helper exits when the rig disconnects or closes.

**Display:** a magenta dot on `head_center` (filled when `ok`, hollow when
`low`) with a nose-to-neck line, behind its own "Show on preview" switch.
Parts DLC could not place (NaN) are not drawn.

No bundle `format_version` bump: the bundle gains an optional extra file and
no existing file changes. The analysis repo does not read it yet.

## Alternatives considered

- **Install DLC-Live into the rig env (or a clone of it).** Rejected: dlclive
  1.1.0 does not support Python 3.13 / numpy 2, so it would mean
  downgrading the rig env or forcing an unsupported install. It would also
  put a GPU library that can crash the process inside the rig GUI. The helper
  keeps the two apart: a crash only turns the preview off.
- **Run the model on the full frame.** 71 ms per frame (14 fps), under the
  camera's 25 fps. **Rig-ROI crop:** 39 ms, also too slow to answer most
  frames.
- **The 3/4 or half-size model.** They are as accurate on the 13 clean test
  frames (mAP 87.8 / 88.1 vs 85.4) and faster (half: 12 ms on the 448 crop).
  Not chosen for the first live look, because the full model is the reference
  and fits the frame budget. A candidate for the trigger (FW-0085).
- **Wait for each answer (frame-synchronous).** Rejected: it would add the
  ~20 ms model time to every tracked frame and block the track worker
  (ADR-0040) whenever the GPU stalls (one 100+ ms spike seen in 200 frames).
- **Head columns in the video sidecar** (as ADR-0220 did for the learned
  head). Rejected for the preview: DLC answers a frame later and skips some,
  so a sidecar row would often be blank or stale. A separate file also leaves
  the sidecar format (and `format_version`) untouched.
- **A shared-memory or pipe transport.** Rejected: localhost TCP matches the
  rig's broker style and was fast enough (round trip ~1 ms over the model time).

## Consequences

- The rig PC needs the `dlclivegui` env and the exported model. On another PC,
  re-export the model and change `python` in `host/dlc_head.json`.
- At 25 fps the helper answers ~99% of frames (~20 ms each), and the tracker's
  per-frame time is unchanged within its run-to-run noise. Each answer is
  ~20-22 ms behind its frame, so a trigger built on it would carry that delay.
- When the GPU is lightly loaded it lowers its clock: DLC-Live alone takes
  ~20 ms at 25 fps against ~14 ms back to back.
- The analysis repo can't use the file until it gets a reader (FW-0085).
- Switching the trigger to `head_center` is a separate decision (FW-0085). It
  needs a policy for `low` / `skipped` / `no_blob` frames and the delay above.
- The same Windows throttling could slow the rig GUI itself when its window is
  in the background (FW-0086).

## Correction to ADR-0219

ADR-0219's "DLC-Live now" rejection said sizes that fit in 40 ms "lose the head
on 40-100% of frames". That was measured with RGB frames (see Context) and is
wrong. Its 70 ms full-frame time stands (71 ms here). Corrected on the
RTX 4060 Ti, FP32, 200 frames of `600_20260914_130417` from frame 20000:

| Model | Full frame | Rig-ROI crop | 448 crop at the CoM |
|---|---|---|---|
| Full (shuffle 1) | 71 ms, head 194/200 | 39 ms, 189/200 | 17 ms, 188/200 (1.2 px from full frame) |
| 3/4 (shuffle 2) | 40 ms, 192/200 | 25 ms, 192/200 | ~15 ms |
| Half (shuffle 3) | 20 ms, 183/200 | — | 12 ms, 186/200 |

The learned network of ADR-0219 remains the recorded head point. This ADR
only adds the DLC-Live preview beside it.

## Evidence / links

- Parity: the helper's `head_center` equals DLC-Live run directly on the same
  200 crops to 0.000 px. Model 17.8 ms, round trip 18.8 ms.
- Replay at 25 fps through the real tracker set up as session
  `600_20260914_130417` (600 frames without DLC, 1200 with, then the helper
  killed for 300):
  - Statuses: 1121 `ok`, 64 `low`, 4 `no_blob`, 10 `skipped`.
  - Helper 20.7 ms median (p95 21.8).
  - Every frame has exactly one row.
  - The tracker kept running at ~3 ms per frame after the kill, and the client
    reported `error`.
  - Before the power-throttling opt-out: 48 ms and 50% skipped.
  - At 60 fps with 2 threads: 13.9 ms and 6% skipped; with the default
    threads: 16-20 ms and 37-50%.
- Scripts and figures (outside the repos):
  `t-maze_recordings/_head_point_eval/`:
  - `helper_parity.py`, `helper_replay.py`, `pace_test.py`, `dlclive_bench.py`;
  - `replay_zoom.png` (close-ups drawn by `draw_dlc_head`).
- Tests: `host/tests/test_dlc_head.py`, `host/tests/test_dlc_head_client.py`
  (against the real `serve_one` with a fake model), and the overlay tests in
  `host/tests/test_tracker_view_options.py`.
- Test-suite note: the extra Tk variables on the Tracking tab moved a
  garbage collection onto a worker thread in `test_video_player` and aborted
  the run (`Tcl_AsyncDelete`). `test_tracker_view_options` now collects
  garbage in `tearDown`, on the main thread.
- DLC project: `Z:\Ravi\dlc\drieu_rat_model-Ravi-2026-10-07`, iteration 1.
  Test mAP after removing 3 mislabelled frames: full 85.4, 3/4 87.8,
  half 88.1.
